import asyncio, hashlib, json, os, re, secrets, time, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Form, Query, Body
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from starlette.middleware.sessions import SessionMiddleware
from .db import Base, engine, SessionLocal, Account, Key, Job, now
from .core import OPS, NO_REF, execute, execute_file_job, CLIError, data_dir
from .browser import browser

APP_SECRET=os.environ.get('APP_SECRET','')
ADMIN_PASSWORD=os.environ.get('ADMIN_PASSWORD','')
if not APP_SECRET or not ADMIN_PASSWORD:
    # Strict only outside explicit test mode, never silently boot a production server with default credentials.
    if os.getenv('FIP_TEST')!='1':raise RuntimeError('APP_SECRET and ADMIN_PASSWORD must be set')
    APP_SECRET=APP_SECRET or 'test-secret-only-do-not-deploy'
    ADMIN_PASSWORD=ADMIN_PASSWORD or 'test-password'
ENCRYPTION_KEY=hashlib.sha256(APP_SECRET.encode()).digest()
import base64
fernet=Fernet(base64.urlsafe_b64encode(ENCRYPTION_KEY))

def encrypt(v):return fernet.encrypt(json.dumps(v).encode()).decode()
def decrypt(v):return json.loads(fernet.decrypt(v.encode())) if v else None

def serialize_account(a):return {'id':a.id,'label':a.label,'c_user':a.c_user,'primary':a.is_primary,'enabled':a.enabled,'status':a.status,'last_error':a.last_error,'has_session':bool(a.token_enc or a.storage_enc)}

def session_db():
    with SessionLocal() as db:yield db

def session_expired(exc):
    # An explicit expired/invalid login is not a rate limit or Facebook access refusal.
    # Never rotate accounts when a post is restricted, a checkpoint appears, or rate-limited.
    val=(getattr(exc,'detail','')+' '+getattr(exc,'message','')).lower()
    markers=('session expired','expired cookie','invalid session','session no longer valid',
             'authentication expired','not authenticated')
    return any(m in val for m in markers)

async def execute_with_optional_fallback(operation, ref, flags, account_id, store, db):
    try:
        return await execute(operation,ref,flags,account_id,store)
    except CLIError as exc:
        if not account_id or not session_expired(exc):raise
        current=db.get(Account,account_id)
        if current:
            current.status='expired';current.last_error='Session expired';db.commit()
        # Only a previously saved secondary account, and only once for session expiry.
        backup=db.scalar(select(Account).where(Account.id!=account_id,
                   Account.is_primary==False,Account.enabled==True,
                   Account.status.in_(['verified','saved'])).order_by(Account.created_at))
        if backup is None:raise
        result=await execute(operation,ref,flags,backup.id,store)
        result['fallback_account_id']=backup.id
        result['fallback_reason']='primary_session_expired'
        return result

def require_admin(req:Request):
    if req.session.get('admin')!=hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest():
        raise HTTPException(401,'Administrator login required')
    return True

def get_key(key:str=Depends(APIKeyHeader(name='X-API-Key',auto_error=False)), db=Depends(session_db)):
    if not key:raise HTTPException(401,'Supply X-API-Key (use Authorize in /docs)')
    k=db.scalar(select(Key).where(Key.secret_hash==hashlib.sha256(key.encode()).hexdigest(),Key.revoked==False))
    if k is None:raise HTTPException(403,'Invalid or revoked API key')
    k.last_used=now();db.commit();return k

class ReadRequest(BaseModel):
    ref: Optional[str]=Field(None,examples=['https://www.facebook.com/NASA/'])
    flags:dict[str,Any]=Field(default_factory=dict,examples=[{'limit':50}])
    account_id:Optional[str]=None
    store:str='default'

class JobRequest(ReadRequest):
    operation:str=Field(examples=['comments'])

class AccountInput(BaseModel):
    label:str=Field(min_length=1,max_length=120)
    c_user:Optional[str]=Field(None,description='Numeric Facebook account ID')
    xs:Optional[str]=Field(None,description='Private session cookie, encrypted at rest')
    is_primary:bool=False

class KeyInput(BaseModel):
    label:str=Field(min_length=1,max_length=120)

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for j in db.scalars(select(Job).where(Job.status=='running')).all():j.status='queued'
        db.commit()
    stop=asyncio.Event()
    worker=asyncio.create_task(worker_loop(stop))
    yield
    stop.set();worker.cancel()
    try:await worker
    except asyncio.CancelledError:pass
    await browser.shutdown()

app=FastAPI(title='Facebook Intelligence Platform API',version='1.0.0',description='Full upstream Facebook CLI access, keys, jobs, sessions, and best-effort reaction identities. The base engine remains unchanged. **Authorize** with the X-API-Key created in the administrator dashboard.',docs_url=None,redoc_url=None,lifespan=lifespan)
app.add_middleware(SessionMiddleware,secret_key=APP_SECRET,max_age=43200,same_site='lax',https_only=os.getenv('COOKIE_SECURE','true').lower()=='true')

@app.get('/healthz',tags=['System'])
def healthz():
    try:
        with engine.connect() as c:c.exec_driver_sql('SELECT 1')
        db_state='ok'
    except Exception:db_state='error'
    fb_exists=Path('/usr/local/bin/fb').is_file() or os.getenv('FIP_TEST')=='1'
    return {'status':'ok' if db_state=='ok' and fb_exists else 'degraded','database':db_state,'base_engine':'unchanged-upstream-0.3.0','engine_binary':'ready' if fb_exists else 'missing'}

@app.get('/docs',include_in_schema=False)
def swagger():return get_swagger_ui_html(openapi_url='/openapi.json',title='Facebook Intelligence API • Try it out',swagger_ui_parameters={'persistAuthorization':True,'displayRequestDuration':True,'filter':True,'docExpansion':'list'})

@app.get('/redoc',include_in_schema=False)
def redoc():return get_redoc_html(openapi_url='/openapi.json',title='API Reference')

@app.get('/v1/capabilities',tags=['Discover'],dependencies=[Depends(get_key)])
def capabilities():return {'upstream_version':'0.3.0','operations':OPS,'native_http_reads':['page','feed','photos','photo','post','comments','reactions','videos','video','playlists','events','event','event/suggested','group','group/feed','graph','edges','discover'],'cli_only':['profile','reel','search','rdf','crawl','query','export','archive','id','explain','surfaces','tiers','routes','fields','cache','config','whoami','db-stats'],'extra':['reaction-identities','multi-account','admin','api-keys','queued-jobs','media-downloads','archives'],'important':'Reaction actors cannot be guaranteed complete; visibility and access restrictions apply'}

async def run_op(req:ReadRequest,operation:str,db=None):
    if operation not in OPS:raise HTTPException(404,'Unknown operation')
    if operation=='archive':
        j=Job(id=str(uuid.uuid4()),operation='archive',ref=req.ref,account_id=req.account_id,options={'flags':{},'store':req.store},status='queued')
        db.add(j);db.commit()
        return {'status':'queued','id':j.id,'status_url':'/v1/jobs/'+j.id}
    account_id=req.account_id
    if account_id:
        a=db.get(Account,account_id)
        if not a or not a.enabled:raise HTTPException(400,'Account missing/disabled')
    else:
        a=db.scalar(select(Account).where(Account.is_primary==True,Account.enabled==True))
        if a:account_id=a.id
    try:return await execute_with_optional_fallback(operation,req.ref,req.flags,account_id,req.store,db)
    except ValueError as e:raise HTTPException(400,str(e))
    except CLIError as e:raise HTTPException(e.status,{'message':e.message,'details':e.detail})

@app.post('/v1/run/{operation}',tags=['Extraction'],summary='Run ANY upstream operation directly',dependencies=[Depends(get_key)])
async def run_any(operation:str,req:ReadRequest,db=Depends(session_db)):
    """Universal read endpoint: page, feed, post, comments, reactions, media, events, group-feed, search, graphs, RDF, crawl, SQL read-only query, archive and all metadata commands. Read /v1/capabilities for allowed operation names."""
    return await run_op(req,operation,db)

@app.get('/v1/read/{operation}',tags=['Extraction'],summary='Convenient GET for original read commands',dependencies=[Depends(get_key)])
async def read_get(operation:str,ref:Optional[str]=None,limit:int=Query(50,ge=1,le=5000),account_id:Optional[str]=None,db=Depends(session_db)):
    if operation not in OPS:raise HTTPException(404)
    return await run_op(ReadRequest(ref=ref,flags={'limit':limit} if operation not in {'id','explain','surfaces','tiers','routes','fields','cache','config','whoami','db-stats','export'} else {},account_id=account_id),operation,db)

@app.post('/v1/jobs',tags=['Jobs'],status_code=202,dependencies=[Depends(get_key)])
def submit(req:JobRequest,db=Depends(session_db)):
    if req.operation not in list(OPS)+['reaction-identities','download-media']:
        raise HTTPException(400,'Unsupported operation')
    if req.account_id and not db.get(Account,req.account_id):raise HTTPException(400,'Unknown account')
    j=Job(id=str(uuid.uuid4()),operation=req.operation,ref=req.ref,account_id=req.account_id,options={'flags':req.flags,'store':req.store},status='queued')
    db.add(j);db.commit()
    return {'id':j.id,'status':'queued','status_url':'/v1/jobs/'+j.id}

@app.get('/v1/jobs',tags=['Jobs'],dependencies=[Depends(get_key)])
def list_jobs(limit:int=Query(20,ge=1,le=100),db=Depends(session_db)):
    return [{'id':j.id,'operation':j.operation,'status':j.status,'created_at':str(j.created_at)} for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit))]

@app.get('/v1/jobs/{job_id}/artifact',tags=['Jobs'],dependencies=[Depends(get_key)],summary='Download a finished media/archive ZIP')
def get_artifact(job_id:str,db=Depends(session_db)):
    if not re.fullmatch(r'[a-f0-9-]{36}',job_id):raise HTTPException(400,'Invalid job id')
    j=db.get(Job,job_id)
    if not j or j.status!='done' or j.operation not in ('download-media','archive'):
        raise HTTPException(404,'Finished downloadable artifact does not exist')
    path=Path('/data/artifacts')/(job_id+'.zip')
    if not path.is_file():raise HTTPException(404,'Artifact has expired or is missing')
    return FileResponse(path,filename='facebook-'+job_id+'.zip',media_type='application/zip')

@app.get('/v1/jobs/{job_id}',tags=['Jobs'],dependencies=[Depends(get_key)])
def get_job(job_id:str,db=Depends(session_db)):
    j=db.get(Job,job_id)
    if not j:raise HTTPException(404,'Unknown job')
    return {'id':j.id,'operation':j.operation,'status':j.status,'result':j.result,'error':j.error,'created_at':j.created_at,'updated_at':j.updated_at}

async def worker_loop(stop):
    while not stop.is_set():
        try:
            with SessionLocal() as db:
                job=db.scalar(select(Job).where(Job.status=='queued').order_by(Job.created_at).limit(1))
                if job:job.status='running';job.updated_at=now();db.commit();jid=job.id
                else:jid=None
            if not jid:
                await asyncio.sleep(1.5);continue
            with SessionLocal() as db:
                job=db.get(Job,jid)
                try:
                    aid=job.account_id
                    if not aid:
                        a=db.scalar(select(Account).where(Account.is_primary==True,Account.enabled==True));aid=a.id if a else None
                    if job.operation=='download-media':
                        opt=job.options.get('flags',{})
                        result=await execute_file_job(opt.get('media_type','photo'),job.ref,aid,job.id, int(opt.get('limit',10)))
                    elif job.operation=='archive':
                        result=await execute_file_job('archive',job.ref,aid,job.id)
                    elif job.operation=='reaction-identities':
                        if not aid:raise ValueError('A saved Facebook account is required to view reaction identities')
                        a=db.get(Account,aid)
                        state=decrypt(a.storage_enc) if a.storage_enc else None
                        if not state and a.token_enc:
                            xs=decrypt(a.token_enc)
                            state={'cookies':[{'name':n,'value':v,'domain':'.facebook.com','path':'/'} for n,v in [('c_user',a.c_user),('xs',xs)]],'origins':[]}
                        if not state:raise ValueError('Account session is missing')
                        result=await browser.collect_reactors(job.ref,state,min(int(job.options.get('flags',{}).get('limit',500)),2000))
                    else:
                        result=await execute_with_optional_fallback(job.operation,job.ref,job.options.get('flags',{}),aid,job.options.get('store','default'),db)
                    job.result=result;job.status='done'
                except CLIError as e:
                    job.status='failed'
                    # The original engine's sanitized stderr helps diagnose usage failures.
                    # The upstream engine's source code is unchanged.
                    job.error=(e.message + (': ' + e.detail.strip() if e.detail.strip() else ''))[:800]
                except Exception as e:
                    job.status='failed';job.error=str(e)[:400]
                job.updated_at=now();db.commit()
        except asyncio.CancelledError:raise
        except Exception:
            await asyncio.sleep(3)

# Admin HTML and JSON APIs: browser-only, admin session required, no API-key here.
@app.get('/',include_in_schema=False)
def root():return RedirectResponse('/admin')
@app.get('/admin',response_class=HTMLResponse,include_in_schema=False)
def admin(req:Request):
    html=Path(__file__).with_name('ui.html').read_text(encoding='utf-8')
    if req.session.get('admin')!=hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest():
        return HTMLResponse(Path(__file__).with_name('login.html').read_text(encoding='utf-8'))
    return HTMLResponse(html)

@app.post('/admin/login',include_in_schema=False)
async def login(req:Request,password:str=Form(...)):
    if not secrets.compare_digest(password,ADMIN_PASSWORD):return HTMLResponse('Invalid password. <a href="/admin">Try again</a>',status_code=401)
    req.session['admin']=hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()
    return RedirectResponse('/admin',status_code=303)

@app.post('/admin/logout',include_in_schema=False)
def logout(req:Request):req.session.clear();return RedirectResponse('/admin',status_code=303)

@app.get('/admin/api/status',dependencies=[Depends(require_admin)],include_in_schema=False)
def admin_status(db=Depends(session_db)):
    import shutil
    total,used,free=shutil.disk_usage('/tmp')
    mem={}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith(('MemTotal:', 'MemAvailable:')):
                k,v=line.split(':',1);mem[k]=int(v.strip().split()[0])*1024
    except Exception:pass
    system={'memory':mem,'disk_free_bytes':free,'load_1m':round(os.getloadavg()[0],2) if hasattr(os,'getloadavg') else None}
    return {'health':healthz(),'system':system,'accounts':[serialize_account(a) for a in db.scalars(select(Account).order_by(Account.created_at)).all()], 'keys':[{'id':k.id,'label':k.label,'prefix':k.prefix,'revoked':k.revoked,'created_at':str(k.created_at)} for k in db.scalars(select(Key).order_by(Key.created_at.desc())).all()], 'jobs':[{'id':j.id,'operation':j.operation,'status':j.status,'error':j.error,'created_at':str(j.created_at)} for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(30)).all()]}

@app.post('/admin/api/keys',dependencies=[Depends(require_admin)],include_in_schema=False)
def add_key(req:KeyInput,db=Depends(session_db)):
    raw='fip_'+secrets.token_urlsafe(36)
    key=Key(id=str(uuid.uuid4()),label=req.label,prefix=raw[:14],secret_hash=hashlib.sha256(raw.encode()).hexdigest())
    db.add(key);db.commit();return {'id':key.id,'api_key':raw,'note':'Shown only once. Save securely.'}

@app.delete('/admin/api/keys/{id}',dependencies=[Depends(require_admin)],include_in_schema=False)
def revoke_key(id:str,db=Depends(session_db)):
    k=db.get(Key,id)
    if not k:raise HTTPException(404)
    k.revoked=True;db.commit();return {'revoked':True}

@app.post('/admin/api/accounts',dependencies=[Depends(require_admin)],include_in_schema=False)
async def add_account(req:AccountInput,db=Depends(session_db)):
    if (req.c_user and not req.xs) or (req.xs and not req.c_user):raise HTTPException(400,'Provide both c_user and xs, or neither for browser login')
    if req.c_user and not re.fullmatch(r'\d{3,30}',req.c_user):raise HTTPException(400,'c_user must be numeric')
    if req.is_primary:
        for a in db.scalars(select(Account).where(Account.is_primary==True)):a.is_primary=False
    a=Account(id=str(uuid.uuid4()),label=req.label,c_user=req.c_user,token_enc=encrypt(req.xs) if req.xs else None,is_primary=req.is_primary, status='saved' if req.xs else 'awaiting_login')
    db.add(a);db.commit()
    if req.xs:
        try:
            await import_session(a.id,req.c_user,req.xs)
        except Exception as e:
            a.status='import_failed';a.last_error=str(e)[:200];db.commit()
    return serialize_account(a)

async def import_session(account_id,c_user,xs):
    path=data_dir(account_id);path.mkdir(parents=True,exist_ok=True)
    proc=await asyncio.create_subprocess_exec('fb','auth','import','--c-user',c_user,'--xs',xs,env={**os.environ,'FB_DATA_DIR':str(path)},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    out,err=await asyncio.wait_for(proc.communicate(),20)
    if proc.returncode!=0:raise ValueError('Original engine session import failed')

@app.post('/admin/api/accounts/{id}/primary',dependencies=[Depends(require_admin)],include_in_schema=False)
def primary_account(id:str,db=Depends(session_db)):
    a=db.get(Account,id)
    if not a:raise HTTPException(404)
    for item in db.scalars(select(Account)):item.is_primary=item.id==id
    db.commit();return {'primary':id}

@app.delete('/admin/api/accounts/{id}',dependencies=[Depends(require_admin)],include_in_schema=False)
async def delete_account(id:str,db=Depends(session_db)):
    a=db.get(Account,id)
    if not a:raise HTTPException(404)
    await browser.close(id)
    # Do not cascade/delete historical job records; erase sensitive session and disable.
    a.token_enc=None;a.storage_enc=None;a.enabled=False;a.status='removed';db.commit()
    path=data_dir(id)
    import shutil
    if path.exists():shutil.rmtree(path)
    return {'removed':True}

@app.post('/admin/api/accounts/{id}/check',dependencies=[Depends(require_admin)],include_in_schema=False)
async def check_account(id:str,db=Depends(session_db)):
    a=db.get(Account,id)
    if not a:raise HTTPException(404)
    try:
        res=await execute('whoami',account_id=id,timeout_seconds=45)
        a.status='verified';a.last_error=None;db.commit()
        return {'status':a.status,'result':res}
    except Exception as e:
        a.status='needs_attention';a.last_error=str(e)[:140];db.commit()
        return {'status':a.status,'error':a.last_error}

@app.post('/admin/api/browser/{id}/open',dependencies=[Depends(require_admin)],include_in_schema=False)
async def open_browser(id:str,db=Depends(session_db)):
    a=db.get(Account,id)
    if not a or not a.enabled:raise HTTPException(404)
    state=decrypt(a.storage_enc) if a.storage_enc else None
    try:await browser.open(id,state);return {'opened':True,'viewport':{'width':1100,'height':780}}
    except Exception as e:raise HTTPException(503,'Cannot start browser: '+str(e)[:140])

@app.get('/admin/api/browser/{id}/screenshot',dependencies=[Depends(require_admin)],include_in_schema=False)
async def browser_screenshot(id:str):
    try:return Response(content=await browser.screenshot(id),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    except ValueError as e:raise HTTPException(404,str(e))

@app.post('/admin/api/browser/{id}/action',dependencies=[Depends(require_admin)],include_in_schema=False)
async def browser_action(id:str,body:dict=Body(...)):
    try:await browser.action(id,body.get('kind'),**{k:v for k,v in body.items() if k!='kind'});return {'ok':True}
    except Exception as e:raise HTTPException(400,str(e)[:150])

@app.post('/admin/api/browser/{id}/save',dependencies=[Depends(require_admin)],include_in_schema=False)
async def browser_save(id:str,db=Depends(session_db)):
    a=db.get(Account,id)
    if not a:raise HTTPException(404)
    try:state=await browser.snapshot(id)
    except ValueError as e:raise HTTPException(400,str(e))
    if not state.get('c_user') or not state.get('xs'):
        raise HTTPException(409,'Facebook login not complete. No c_user/xs session cookies found.')
    a.c_user=state['c_user'];a.token_enc=encrypt(state['xs']);a.storage_enc=encrypt(state['storage_state']);a.status='saved';db.commit()
    await import_session(id,a.c_user,state['xs'])
    await browser.close(id)
    return serialize_account(a)

@app.post('/admin/api/maintenance/cache-clear',dependencies=[Depends(require_admin)],include_in_schema=False)
async def clear_cache(body:dict=Body(default={}),db=Depends(session_db)):
    account_id=body.get('account_id')
    if account_id and not db.get(Account,account_id):raise HTTPException(404,'Account missing')
    folder=data_dir(account_id)
    folder.mkdir(parents=True,exist_ok=True)
    p=await asyncio.create_subprocess_exec('fb','cache','clear',env={**os.environ,'FB_DATA_DIR':str(folder)},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    await p.communicate()
    return {'cleared':p.returncode==0}

@app.post('/admin/api/jobs',dependencies=[Depends(require_admin)],include_in_schema=False)
def admin_submit(req:JobRequest,db=Depends(session_db)):
    if req.operation not in list(OPS)+['reaction-identities','download-media']:raise HTTPException(400,'Unsupported operation')
    j=Job(id=str(uuid.uuid4()),operation=req.operation,ref=req.ref,account_id=req.account_id,options={'flags':req.flags,'store':req.store},status='queued');db.add(j);db.commit()
    return {'id':j.id,'status':j.status}

@app.get('/admin/api/jobs/{id}',dependencies=[Depends(require_admin)],include_in_schema=False)
def admin_job(id:str,db=Depends(session_db)):
    j=db.get(Job,id)
    if not j:raise HTTPException(404)
    return {'id':j.id,'status':j.status,'result':j.result,'error':j.error}

# Document each upstream capability independently in Swagger's Try it out UI.
# The route shares the universal runner, so documentation cannot diverge from execution.
for _op, _description in OPS.items():
    def make_handler(operation):
        async def handler(req:ReadRequest,db=Depends(session_db)):
            return await run_op(req,operation,db)
        handler.__name__='operation_'+operation.replace('-','_')
        handler.__doc__=_description+'\n\nUses the unmodified upstream Facebook CLI binary. Fields returned by Facebook may be incomplete.'
        return handler
    app.add_api_route('/v1/operations/'+_op,make_handler(_op),methods=['POST'],tags=['Original engine — '+('Read' if _op in ['page','profile','feed','post','comments','reactions','photos','photo','videos','video','reel','playlists','events','event-suggested','event','group','group-feed','search','discover'] else 'Graph and tools')],summary=_description,dependencies=[Depends(get_key)])