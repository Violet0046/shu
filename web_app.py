"""源码目录中的便捷入口；安装后可直接运行 shu-grading-web。"""
import uvicorn  # 与包内启动器共用模块，兼容本地启动测试。
from shu_grading.cli import main

if __name__ == '__main__':
    main()
