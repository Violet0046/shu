"""网页/API 入口；阅卷另行启动 python -m shu_grading.backend.worker。"""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("shu_grading.web.server:create_app", factory=True,
                host=os.getenv("SHU_WEB_HOST", "127.0.0.1"),
                port=int(os.getenv("SHU_WEB_PORT", "8000")))
