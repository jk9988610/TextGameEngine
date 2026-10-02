"""
Gunicorn 生产配置 —— Ubuntu 服务器用
启动命令：gunicorn -c gunicorn_config.py app:app
"""
import multiprocessing
import os

# 绑定本地 8000 端口（Nginx 反代到这里）
bind = "127.0.0.1:8000"

# Worker 数量：轻量应用，CPU核数 * 2 + 1，阿里云 1-2 核设 4 就够
workers = 4

# Worker 类型：同步就行（Flask 处理 API 请求，不做长连接）
worker_class = "sync"

# 每个 worker 最多处理 1000 请求后重启（防内存泄漏）
max_requests = 1000
max_request_jitter = 50

# 超时
timeout = 30
graceful_timeout = 10
keepalive = 2

# 进程名
proc_name = "textgame"

# 日志（放到项目下的 logs/ 目录）
project_dir = os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(project_dir, "logs")
os.makedirs(log_dir, exist_ok=True)

accesslog = os.path.join(log_dir, "gunicorn_access.log")
errorlog = os.path.join(log_dir, "gunicorn_error.log")
loglevel = "info"

# Daemon mode 关掉，交给 systemd 管理
daemon = False

# 环境变量（生产 secret_key，必须在服务器上设置）
raw_env = [
    "FLASK_DEBUG=false",
]
