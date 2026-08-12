#!/usr/bin/env python3
"""Install sports-log service, nginx route and cron entry."""

import os
import shutil
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
PYTHON = "/root/.pyenv/versions/3.10.13/bin/python3"
WORKSPACE_CTL = os.path.join(WORKSPACE_ROOT, "bin", "workspace-ctl")
SERVICE_PATH = "/etc/systemd/system/sports-log-web.service"
NGINX_CONF = "/www/server/panel/vhost/nginx/zzzgry.top.conf"
CRON_LINE = (
    "0 23 * * *   %s refresh sports >> %s/logs/cron-refresh.log 2>&1"
    % (WORKSPACE_CTL, BASE_DIR)
)

SERVICE = """[Unit]
Description=Sports Log Web Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={base_dir}
Environment=BASE_PATH=/sport
Environment=PORT=18081
EnvironmentFile={workspace_root}/.env
EnvironmentFile={workspace_root}/.env.d/coros.env
EnvironmentFile={workspace_root}/.env.d/sports-log.env
ExecStart={python} {base_dir}/web_server.py
Restart=always
RestartSec=5
StandardOutput=append:{base_dir}/logs/web.log
StandardError=append:{base_dir}/logs/web.log

[Install]
WantedBy=multi-user.target
""".format(
    base_dir=BASE_DIR,
    workspace_root=WORKSPACE_ROOT,
    coros_mcp_root=COROS_MCP_ROOT,
    python=PYTHON,
)

NGINX_BLOCK = """    # -- sports-log reverse proxy ------------------------------------------
    location /sport/ {
        proxy_pass         http://127.0.0.1:18081/;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
    location = /sport {
        return 301 /sport/;
    }
"""


def backup(path):
    if os.path.exists(path):
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copy2(path, "%s.bak.%s" % (path, stamp))


def write_service():
    backup(SERVICE_PATH)
    with open(SERVICE_PATH, "w", encoding="utf-8") as f:
        f.write(SERVICE)


def patch_nginx():
    with open(NGINX_CONF, encoding="utf-8") as f:
        conf = f.read()
    if "location /sport/" in conf:
        return
    marker = "    # ── 以后新增服务在此追加 location 块"
    if marker not in conf:
        marker = "    #禁止访问的文件或目录"
    if marker not in conf:
        raise RuntimeError("nginx insertion marker not found")
    backup(NGINX_CONF)
    conf = conf.replace(marker, NGINX_BLOCK + "\n" + marker, 1)
    with open(NGINX_CONF, "w", encoding="utf-8") as f:
        f.write(conf)


def install_cron():
    try:
        current = subprocess.check_output(["crontab", "-l"], text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
    current = ""
    if CRON_LINE in current:
        return
    lines = [
        line
        for line in current.splitlines()
        if "sports-log/scripts/refresh_data.py" not in line and "workspace-ctl refresh sports" not in line
    ]
    lines.extend(["", "# sports-log 每天 23:00 更新当天 COROS 数据/汇总", CRON_LINE])
    data = "\n".join(lines).strip() + "\n"
    proc = subprocess.run(["crontab", "-"], input=data, text=True, check=True)
    return proc.returncode


def main():
    write_service()
    patch_nginx()
    install_cron()
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "sports-log-web.service"], check=True)
    subprocess.run(["nginx", "-t"], check=True)
    subprocess.run(["nginx", "-s", "reload"], check=True)
    print("sports-log deployed")


if __name__ == "__main__":
    main()
