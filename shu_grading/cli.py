"""本地一键启动网页和阅卷；服务器可使用 --api-only 拆分部署。"""
import argparse
import logging
import os
import uvicorn


def main(argv=None):
    parser = argparse.ArgumentParser(description='启动阅卷工作台，本地默认同时启动阅卷服务')
    parser.add_argument('--api-only', action='store_true', help='仅启动网页/API，阅卷使用独立 worker')
    args = parser.parse_args(argv)
    # 先加载项目 .env，再读取启动配置。
    from shu_grading.config import load_settings
    load_settings()
    if args.api_only:
        os.environ['SHU_EMBEDDED_WORKER'] = '0'
    else:
        os.environ.setdefault('SHU_EMBEDDED_WORKER', '1')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    uvicorn.run("shu_grading.web.server:create_app", factory=True,
                host=os.getenv("SHU_WEB_HOST", "127.0.0.1"),
                port=int(os.getenv("SHU_WEB_PORT", "8000")))


if __name__ == "__main__":
    main()
