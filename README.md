# 书 · 阅卷工作台

面向教师的网页 AI 辅助阅卷工具。当前支持作文，后端通过统一的单份阅卷接口扩展其他题型。

- 逐份拍照、选择图片或粘贴截图，支持手机扫码录入。
- 老师指定学生或使用匿名编号，模型只负责正文转写和评分。
- 通过网页表单管理个人评分模板及班级名单。
- 对照原图复核分数、评语和卷面扣分，导出已确认的 Excel 成绩。
- 管理员分配账号；网页/API 与独立阅卷工作进程共享持久队列。

## 开始使用

需要 Python 3.10 或更新版本。

```powershell
python -m pip install -e .
python -m shu_grading.backend.admin create-user teacher01
```

分别在两个终端运行：

```powershell
python web_app.py
```

```powershell
python -m shu_grading.backend.worker --concurrency 2
```

打开 <http://127.0.0.1:8000>，使用管理员分配的账号登录。模型接口与密钥配置在服务器环境变量中。

详细的配置、手机上传、账号管理和部署步骤见 [使用与部署说明](WEB_README.md)。

## 项目结构

```text
shu_grading/
  backend/          账户、资源、任务、答卷、文件存储和持久队列
    grading/        题型接口、注册表和作文实现
  web/
    routes/         HTTP API
    static/         教师网页界面
  graders/          作文模型调用与响应解析
  models/           模型服务适配
  prompts/          评分提示词
  utils/            成绩报告导出
tests/              业务、架构与浏览器验证
```

数据库及图片默认保存在 `~/.shu-grading/web/`，可通过 `SHU_WEB_DATA_DIR` 调整。后端架构、队列语义和题型扩展方式见 [后端说明](BACKEND.md)。

## 验证

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
node --check shu_grading/web/static/app.js
```

自动测试使用模拟模型，不消耗模型额度。浏览器验收脚本为 `tests/browser_smoke.py`，需另外安装 `.[browser]` 与可用浏览器。

## 部署

提供 `Dockerfile` 和 `compose.yaml`，包含独立 web、worker 服务及持久数据卷。先根据 `.env.server.example` 创建服务器配置，再按 [部署说明](WEB_README.md) 启动。

当前采用单服务器 SQLite 与本地图片存储。Docker 镜像和真实模型效果需在目标环境验证。
