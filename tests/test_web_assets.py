import re
import shutil

from fastapi.testclient import TestClient

from shu_grading.web import server


def test_entrypoints_version_assets_and_do_not_cache_html(tmp_path):
    app=server.create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        index=client.get('/')
        assert index.headers['cache-control']=='no-store'
        assert client.get('/index.html').text==index.text
        urls=re.findall(r'(?:src|href)="(/(?:app\.js|admin\.js|style\.css)\?v=[a-f0-9]+)"',index.text)
        assert len(urls)==3
        for url in urls:
            asset=client.get(url)
            assert asset.status_code==200 and asset.headers['cache-control']=='no-cache'
        assert client.get('/api/health').json()['web_version']==index.headers['x-shu-web-version']


def test_new_asset_content_changes_url_despite_unchanged_filename(tmp_path,monkeypatch):
    copied=tmp_path/'static'
    shutil.copytree(server.STATIC_DIR,copied)
    monkeypatch.setattr(server,'STATIC_DIR',copied)
    with TestClient(server.create_app(tmp_path/'data',start_worker=False)) as client:
        before=client.get('/').text
        old_version=client.get('/api/health').json()['web_version']
    script=copied/'app.js'
    script.write_text(script.read_text(encoding='utf-8')+'\n/* new release */\n',encoding='utf-8')
    with TestClient(server.create_app(tmp_path/'data',start_worker=False)) as client:
        assert client.get('/').text!=before
        assert client.get('/api/health').json()['web_version']!=old_version
