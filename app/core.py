"""The unchanged upstream fb binary is the only source of base extraction.
No shell interpolation; every CLI operation and flag is explicitly allowlisted.
"""
import asyncio, json, os, re, uuid
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(os.getenv('FIP_DATA_ROOT', '/data'))
# All read operations from upstream docs /v1/* plus CLI-only capabilities.
OPS = {
    'page': 'Page/profile information and about tab',
    'profile': 'Alias of page',
    'feed': 'Profile/page timeline',
    'post': 'One post, attachments and available comments',
    'comments': 'Comments and comment authors, subject to visibility/pagination',
    'reactions': 'Reaction counts by type (not actor identities)',
    'photos': 'Photo tab / albums', 'photo': 'Photo, alt text, caption and optionally bytes',
    'videos': 'Video tab', 'video': 'Video, captions and optional transcript',
    'reel': 'Video/reel alias', 'playlists': 'Video playlists and shows', 'events': 'Profile events', 'event': 'Event details', 'event-suggested': 'Suggested events alongside an event',
    'group': 'Public group metadata', 'group-feed': 'Visible group discussion',
    'search': 'Search via signed-in account', 'discover': 'Pages directory',
    'edges': 'Relations claimed by a read', 'graph': 'Walk the claims graph',
    'rdf': 'Graph encoded as RDF', 'id': 'Classify a URL/id',
    'explain': 'Explain planned route and tier', 'surfaces': 'Available data surfaces',
    'tiers': 'Authentication tiers', 'routes': 'Routing matrix',
    'fields': 'Available record fields', 'crawl': 'Crawl into an isolated SQLite store',
    'db-stats': 'Stats for saved store', 'query': 'Read-only SQL on saved store',
    'export': 'Export store as RDF', 'archive': 'Archive one read for diagnostic inspection',
    'cache': 'Inspect cache', 'config': 'Show engine config', 'whoami': 'Check imported session',
}
NO_REF = {'surfaces','tiers','routes','fields','discover','db-stats','export','cache','config','whoami'}
# Only supported options; do not pass arbitrary flags from requests.
BOOL_FLAGS={'about','no-posts','transcript','playlists','suggested','resolve-opaque','no-provenance','questions','resolve','no-cache'}
VALUE_FLAGS={'limit','author','tab','album','depth','budget','format','fields','tier','letter','rate','retries','timeout','cache-ttl'}
# File paths, --download, --proxy, --db and --store are deliberately controlled by server.
SAFE_REF_HOSTS={'facebook.com','www.facebook.com','m.facebook.com','web.facebook.com','fb.watch','mbasic.facebook.com'}

class CLIError(Exception):
    def __init__(self, status, message, detail=''):
        self.status=status; self.message=message; self.detail=detail
        super().__init__(message)

def validate_ref(ref):
    if not isinstance(ref,str) or len(ref)>2048 or not ref.strip():
        raise ValueError('ref must be nonempty and <=2048 chars')
    if any(ord(c)<32 for c in ref): raise ValueError('Invalid control character in ref')
    # The upstream CLI accepts handles, ids, terms and facebook URLs; do not permit arbitrary URLs.
    u=urlparse(ref)
    if u.scheme or u.netloc:
        if u.scheme not in ('https','http') or u.hostname not in SAFE_REF_HOSTS:
            raise ValueError('Only Facebook URLs are allowed')
    return ref

def data_dir(account_id=None):
    if account_id:
        if not re.fullmatch(r'[a-f0-9-]{36}', account_id): raise ValueError('Invalid account id')
        return ROOT/'accounts'/account_id
    return ROOT/'guest'

def store_path(name='default'):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}', name): raise ValueError('Invalid store name')
    p=ROOT/'stores'; p.mkdir(parents=True,exist_ok=True)
    return p/(name+'.db')

def build_args(op,ref=None,flags=None,store='default'):
    if op not in OPS: raise ValueError('Unsupported operation')
    flags=flags or {}
    if not isinstance(flags,dict):raise ValueError('flags must be object')
    if len(flags)>20:raise ValueError('Too many flags')
    special={'group-feed':['group','feed'],'event-suggested':['event'],'playlists':['videos'],'db-stats':['db','stats'],'cache':['cache'],'config':['config']}
    args=['fb']+special.get(op,[op])
    if op=='cache': args += []
    if op=='config': args += ['show']
    if op=='playlists':args += ['--playlists']
    if op=='event-suggested':args += ['--suggested']
    if op in ('db-stats','export','query','crawl'): args += ['--store',str(store_path(store))]
    if op=='query':
        if not isinstance(ref,str) or not re.match(r'^\s*(SELECT|WITH|EXPLAIN)\b',ref,re.I) or ';' in ref:
            raise ValueError('Only one read-only SELECT/WITH/EXPLAIN statement is allowed')
        if re.search(r'\b(ATTACH|DETACH|PRAGMA|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|VACUUM)\b',ref,re.I):
            raise ValueError('Disallowed SQL keyword')
        args += [ref]
    elif op not in NO_REF:
        valid_ref = validate_ref(ref)
        if op == 'post' and urlparse(valid_ref).path.startswith('/reel/'):
            raise ValueError('Reel link detected: choose operation reel (or video), not post')
        args += [valid_ref]
    elif ref and op=='fields': args += [str(ref)[:100]]
    elif ref and op=='discover': args += []
    for k,v in flags.items():
        if k == 'about' and v and op not in ('page', 'profile'):
            raise ValueError('The about option is supported only for page/profile')
        if k in BOOL_FLAGS:
            if not isinstance(v,bool):raise ValueError(f'{k} must be boolean')
            if v: args.append('--'+k)
        elif k in VALUE_FLAGS:
            if isinstance(v,(dict,list,bool)) or len(str(v))>120:raise ValueError('Invalid flag value')
            if k in {'limit','depth','budget','retries'}:
                if not re.fullmatch(r'\d+',str(v)) or (k=='limit' and (int(v)<1 or int(v)>5000)) or (k!='limit' and int(v)>10000):raise ValueError('Invalid numeric flag')
            if k=='tier' and str(v) not in ['0','1','comet','graphql','og','embed','session']:raise ValueError('Invalid tier')
            if k=='format' and str(v) not in ['nt','turtle','jsonld']:raise ValueError('Invalid format')
            args += ['--'+k,str(v)]
        else:raise ValueError(f'Unsupported flag: {k}')
    if op not in ('rdf','export','archive'):
        args+=['--output','jsonl']
    return args

async def execute(op,ref=None,flags=None,account_id=None,store='default',timeout_seconds=180):
    cmd=build_args(op,ref,flags,store)
    path=data_dir(account_id);path.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'FB_DATA_DIR':str(path),'NO_COLOR':'1'}
    # Direct execution (never shell=True). Per-request isolated data dir selected by account.
    try:
        process=await asyncio.create_subprocess_exec(*cmd,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,cwd=str(ROOT))
    except FileNotFoundError:
        raise CLIError(503,'The original Facebook CLI executable is not installed')
    try:
        out,err=await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        process.kill();await process.communicate();raise CLIError(504,'Engine timed out')
    stderr=err.decode('utf8','replace')[-2400:]
    # Do not return anything potentially resembling a cookie or token in stderr.
    stderr=re.sub(r'(xs|c_user|cookie|token)[=: ]+\S+',r'\1=[REDACTED]',stderr,flags=re.I)
    raw=out.decode('utf8','replace')
    if process.returncode not in (0,3):
        raise CLIError(502,f'Facebook CLI exit code {process.returncode}',stderr)
    if op in ('rdf','export','archive'): return {'raw':raw[:4000000],'warning':stderr,'status':'ok' if process.returncode==0 else 'empty'}
    items=[]
    for line in raw.splitlines():
        if not line.strip():continue
        try:items.append(json.loads(line))
        except json.JSONDecodeError:items.append({'text':line})
    return {'records':items,'count':len(items),'warning':stderr,'status':'ok' if process.returncode==0 else 'empty'}

async def execute_file_job(kind,ref,account_id,job_id,limit=10):
    """Allow only upstream media/archive downloads to a server-owned job directory.
    Returns an artifact id, never a filesystem path or a Facebook cookie.
    """
    import zipfile
    if kind not in {'photo','photos','video','archive'}:
        raise ValueError('Download type must be photo, photos, video or archive')
    ref=validate_ref(ref)
    if not re.fullmatch(r'[a-f0-9-]{36}',job_id):raise ValueError('Invalid job id')
    folder=ROOT/'artifacts'/job_id
    folder.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'FB_DATA_DIR':str(data_dir(account_id))}
    data_dir(account_id).mkdir(parents=True,exist_ok=True)
    args=['fb',kind,ref]
    if kind=='archive':args+=['--dir',str(folder)]
    else:args+=['--download',str(folder),'--limit',str(max(1,min(limit,100)))]
    args+=['--output','jsonl']
    p=await asyncio.create_subprocess_exec(*args,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:out,err=await asyncio.wait_for(p.communicate(),timeout=600)
    except asyncio.TimeoutError:
        p.kill();await p.communicate();raise CLIError(504,'Media or archive download timed out')
    if p.returncode!=0:raise CLIError(502,'Original CLI failed to download media (exit '+str(p.returncode)+')')
    archive_path=ROOT/'artifacts'/(job_id+'.zip')
    # Avoid zipping unbounded media by accident.
    total=0;number=0
    with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED) as z:
        for path in folder.rglob('*'):
            if not path.is_file():continue
            total+=path.stat().st_size
            if total>1_000_000_000:raise CLIError(413,'Downloaded artifact exceeds 1 GB cap')
            z.write(path,path.relative_to(folder));number+=1
    return {'status':'ok','file_count':number,'size_bytes':total,'artifact_url':'/v1/jobs/'+job_id+'/artifact','notes':'Downloaded by unchanged original fb CLI; use X-API-Key to retrieve.'}