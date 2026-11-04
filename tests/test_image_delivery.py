import io

from fastapi.testclient import TestClient
from PIL import Image

from test_web_workflow import submit, task, web


def test_preview_and_original_have_distinct_validated_caches(web):
    client,app,_=web
    tid=task(client);sid=submit(client,tid)
    url=f'/api/submissions/{sid}/image'
    original=client.get(url)
    preview=client.get(url+'?preview=true')
    row=app.state.store.one('SELECT image FROM submissions WHERE id=?',(sid,))
    assert app.state.services.files.path(row['image']).with_suffix('.reading.jpg').is_file()
    reading=client.get(url+'?reading=true')
    assert reading.status_code==200 and reading.headers['cache-control']=='private, no-cache'
    with Image.open(io.BytesIO(reading.content)) as image:
        assert max(image.size)<=3072
    assert client.get(url+'?reading=true',headers={'If-None-Match':reading.headers['etag']}).status_code==304
    # Historical submissions generate missing derivatives only once.
    app.state.services.files.path(row['image']).with_suffix('.reading.jpg').unlink()
    assert client.get(url+'?reading=true').status_code==200
    assert original.status_code==preview.status_code==200
    assert original.headers['cache-control']=='private, no-cache'
    assert preview.headers['cache-control']=='private, no-cache'
    with Image.open(io.BytesIO(preview.content)) as image:
        assert max(image.size)<=1600
    conditional=client.get(url,headers={'If-None-Match':original.headers['etag']})
    assert conditional.status_code==304 and not conditional.content
    assert 'content-length' not in conditional.headers
    assert client.get(url+'?preview=true',headers={'If-None-Match':preview.headers['etag']}).status_code==304
    assert client.get(url,headers={'If-None-Match':preview.headers['etag']}).status_code==200
    # Even a known ETag must not bypass another account's permissions.
    with TestClient(app) as stranger:
        app.state.services.auth.create_account('其他使用人','password-123')
        stranger.post('/api/login',json={'username':'其他使用人','password':'password-123'})
        assert stranger.get(url,headers={'If-None-Match':original.headers['etag']}).status_code==404
        assert stranger.get(url+'?preview=true',headers={'If-None-Match':preview.headers['etag']}).status_code==404
        assert stranger.get(url+'?reading=true').status_code==404
    assert client.get('/api/tasks/'+tid).headers['cache-control']=='no-store'
