# Linux 服务器直接部署

以 Ubuntu/Debian + systemd 为例，项目目录固定为 `/opt/shu`。Python 需 3.10+，依赖和程序由 `pyproject.toml` 统一安装，无需 Docker。以下命令在目标服务器执行。

## 安装环境和项目

```bash
sudo apt-get update
sudo apt-get install python3 python3-venv git
sudo useradd --system --user-group --home-dir /var/lib/shu --create-home --shell /usr/sbin/nologin shu
sudo git clone https://github.com/Violet0046/shu.git /opt/shu
sudo python3 -m venv /opt/shu/.venv
sudo /opt/shu/.venv/bin/python -m pip install --upgrade pip
sudo /opt/shu/.venv/bin/python -m pip install /opt/shu
```

最后一条命令自动安装项目及全部运行依赖，并打包网页、提示词和默认评分标准。安装后有三个命令：`shu-grading-web`、`shu-grading-worker`、`shu-grading-admin`。

## 配置与账号

```bash
sudo install -d -m 750 -o root -g shu /etc/shu
sudo install -m 640 -o root -g shu /opt/shu/.env.example /etc/shu/shu.env
sudo install -d -m 750 -o shu -g shu /var/lib/shu
sudoedit /etc/shu/shu.env
```

填写模型密钥、接口地址和正式域名。保留 `SHU_WEB_DATA_DIR=/var/lib/shu`，web、worker、账号管理共用此持久目录。正式 HTTPS 使用 `SHU_COOKIE_SECURE=1`；本地 HTTP 测试设为 `0`。示例中的服务器数据目录不适合普通用户本地测试，本地可删除该项使用默认目录。

从当前工作目录 `.env` 读取配置，或通过 `SHU_ENV_FILE` 指定文件绝对路径；系统环境变量优先。服务文件使用 `/etc/shu/shu.env`，真实配置不得提交 Git。

```bash
sudo -u shu env SHU_ENV_FILE=/etc/shu/shu.env /opt/shu/.venv/bin/shu-grading-admin create-user teacher01
```

密码在终端交互输入。其他账号命令参见上级目录的使用说明。

## 常驻服务

```bash
sudo install -m 644 /opt/shu/deploy/shu-web.service /etc/systemd/system/shu-web.service
sudo install -m 644 /opt/shu/deploy/shu-worker.service /etc/systemd/system/shu-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now shu-web shu-worker
sudo systemctl status shu-web shu-worker
curl http://127.0.0.1:8000/api/health
```

工作进程启动后，健康检查的 `worker_available` 应为 `true`。日志使用 `journalctl -u shu-web -u shu-worker -f` 查看。

网页只监听回环地址。在已经配置 HTTPS 证书的域名 Nginx `server` 块中加入 `nginx.conf.example` 的内容，检查配置后重新加载 Nginx。手机扫码依赖正确的 `SHU_PUBLIC_URL`。24 MB 请求上限容纳 15 MB 图片的 base64 JSON 请求。

临时验证也可以在两个终端分别执行以下命令；这不提供退出终端后的常驻管理：

```bash
SHU_ENV_FILE=/etc/shu/shu.env /opt/shu/.venv/bin/shu-grading-web --api-only
SHU_ENV_FILE=/etc/shu/shu.env /opt/shu/.venv/bin/shu-grading-worker
```

## 更新与备份

升级前停止两个服务，并备份完整 `/var/lib/shu`（数据库和图片）：

```bash
sudo systemctl stop shu-web shu-worker
sudo cp -a /var/lib/shu /var/lib/shu-backup-$(date +%Y%m%d-%H%M%S)
sudo git -C /opt/shu pull --ff-only
sudo /opt/shu/.venv/bin/python -m pip install /opt/shu
sudo systemctl start shu-web shu-worker
```

普通安装后，仅更新源码不会更新安装包，必须重新运行安装命令。若修改服务文件，应重新安装对应文件，并执行 `systemctl daemon-reload` 后启动。

以上为部署模板，尚未在目标 Linux 服务器执行；域名、证书、公网访问及真实模型调用需要上线时验证。
