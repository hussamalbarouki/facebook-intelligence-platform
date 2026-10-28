import os
os.environ['FIP_TEST']='1'
os.environ['DATABASE_URL']='sqlite:////tmp/fip-test-suite.db'
from app.core import OPS,build_args,validate_ref
import pytest

def test_operation_catalog():
    for op in ['page','comments','reactions','photos','video','event','group-feed','search','rdf','crawl','query','archive','whoami']:
        assert op in OPS

def test_cli_safe_args():
    assert build_args('comments','https://www.facebook.com/nasa/posts/123',{'limit':50}) == ['fb','comments','https://www.facebook.com/nasa/posts/123','--limit','50','--output','jsonl']
    assert build_args('group-feed','123')[:3] == ['fb','group','feed']

def test_block_arbitrary_exe_and_hosts():
    with pytest.raises(ValueError):build_args('cmd','nasa')
    with pytest.raises(ValueError):build_args('comments','http://127.0.0.1/private')
    with pytest.raises(ValueError):build_args('comments','nasa',{'evil':'yes'})
    with pytest.raises(ValueError):build_args('query','DELETE FROM nodes')

from fastapi.testclient import TestClient
from app.main import app

def test_auth_dashboard_and_docs():
    with TestClient(app,base_url="https://testserver") as c:
        assert c.get('/healthz').status_code==200
        assert c.get('/v1/capabilities').status_code==401
        assert c.get('/docs').status_code==200
        assert c.get('/admin/api/status').status_code==401
        assert c.post('/admin/login',data={'password':'test-password'},follow_redirects=False).status_code==303
        assert c.get('/admin/api/status').status_code==200
        k=c.post('/admin/api/keys',json={'label':'test'}).json()['api_key']
        h={'X-API-Key':k}
        assert c.get('/v1/capabilities',headers=h).status_code==200
        assert c.get('/v1/operations/page',headers=h).status_code==405
        assert '/v1/operations/page' in c.get('/openapi.json').json()['paths']
        ids=c.get('/admin/api/status').json()['keys']; uid=ids[0]['id']
        assert c.delete('/admin/api/keys/'+uid).status_code==200
        assert c.get('/v1/capabilities',headers=h).status_code==403


def test_reel_links_use_reel_command():
    ref='https://www.facebook.com/reel/3181543935372005/'
    args=build_args('reel',ref,{'limit':1000})
    assert args[:3]==['fb','reel',ref]
    assert args[-4:]==['--limit','1000','--output','jsonl']
    with pytest.raises(ValueError,match='reel'):
        build_args('post',ref,{'limit':1000})


def test_about_option_only_applies_to_pages():
    assert '--about' in build_args('page','nasa',{'about':True})
    with pytest.raises(ValueError,match='about'):
        build_args('reel','https://www.facebook.com/reel/3181543935372005/',{'about':True})