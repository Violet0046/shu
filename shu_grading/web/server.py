"""HTTP 应用组装入口；默认仅提供 API，不在网页进程中执行阅卷。"""
import asyncio
import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..backend.errors import ServiceError
from ..backend.services import build_services
from ..backend.worker import GradingWorker
from .routes import admin, auth, capture, resources, submissions, tasks

STATIC_DIR = Path(__file__).parent/'static'


def create_app(data_dir=None, provider_factory=None, start_worker=None, registry=None):
    index = (STATIC_DIR/'index.html').read_text(encoding='utf-8')
    versions = {name:hashlib.sha256((STATIC_DIR/name).read_bytes()).hexdigest()[:16]
                for name in ('app.js','admin.js','viewer.js','style.css')}
    web_version = hashlib.sha256(''.join(versions.values()).encode()).hexdigest()[:16]
    for name, version in versions.items():
        index = index.replace(f'"/{name}"',f'"/{name}?v={version}"')
    services = build_services(data_dir, provider_factory, registry)
    worker = GradingWorker(services)
    embedded = os.getenv('SHU_EMBEDDED_WORKER')=='1' if start_worker is None else start_worker
    concurrency = int(os.getenv('SHU_WORKER_CONCURRENCY','2'))
    if embedded and not 1 <= concurrency <= 16:
        raise ValueError('SHU_WORKER_CONCURRENCY 必须为 1～16')

    @asynccontextmanager
    async def lifespan(app):
        logger = logging.getLogger('uvicorn.error')
        runner = asyncio.create_task(worker.run(concurrency)) if embedded else None
        if embedded:
            logger.info('阅卷服务已随网页启动，并发数 %s；数据目录：%s', concurrency, services.store.root)
        else:
            logger.info('当前仅启动网页/API。请另行启动 python -m shu_grading.backend.worker；否则答卷只会排队等待。')
        yield
        if runner:
            runner.cancel()
            with suppress(asyncio.CancelledError):
                await runner

    app = FastAPI(title='书 · 阅卷服务', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services
    # 兼容本地业务测试入口，实际使用独立 worker 命令。
    app.state.store = services.store
    app.state.process_one = worker.run_once

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({'detail': exc.message}, status_code=exc.status)

    @app.middleware('http')
    async def protect_origin(request: Request, call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin = request.headers.get('origin')
            if (origin and urlsplit(origin).netloc != request.headers.get('host')) or request.headers.get('sec-fetch-site')=='cross-site':
                return JSONResponse({'detail':'请从应用页面提交请求'}, status_code=403)
            if not request.headers.get('content-type','').startswith('application/json'):
                return JSONResponse({'detail':'需要 JSON 请求'}, status_code=415)
            try:
                size = int(request.headers.get('content-length','0') or 0)
            except ValueError:
                return JSONResponse({'detail':'请求长度无效'}, status_code=400)
            if size > 22_000_000:
                return JSONResponse({'detail':'图片超过上传大小限制'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        if request.url.path.startswith('/api') and 'Cache-Control' not in response.headers:
            response.headers['Cache-Control'] = 'no-store'
        if request.url.path in ('/app.js','/admin.js','/viewer.js','/style.css'):
            response.headers['Cache-Control'] = 'no-cache'
        return response

    for module in (auth, resources, tasks, submissions, capture, admin):
        app.include_router(module.router(services))

    @app.get('/api/health')
    def health():
        services.store.one('SELECT 1 AS ready')
        recent = services.store.one('SELECT MAX(heartbeat) AS last FROM workers')['last']
        return {'database':'ok', 'worker_available': bool(recent and recent > time.time()-services.queue.lease_seconds),
                'embedded_worker': bool(embedded), 'web_version':web_version}

    @app.api_route('/api/{unknown:path}', methods=['GET','POST','PUT','DELETE','PATCH'])
    def unknown_api(unknown: str):
        return JSONResponse({'detail':'接口不存在'}, status_code=404)

    @app.get('/', response_class=HTMLResponse)
    @app.get('/index.html', response_class=HTMLResponse)
    def index_page():
        return HTMLResponse(index,headers={'Cache-Control':'no-store','X-Shu-Web-Version':web_version})

    app.mount('/', StaticFiles(directory=STATIC_DIR, html=True), name='web')
    return app
