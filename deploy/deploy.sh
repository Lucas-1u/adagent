#!/usr/bin/env bash
# ============================================================
# AdAgent —— 服务器一键部署
# 支持 Ubuntu/Debian（apt）与 CentOS/RHEL（yum）
#
# 用法（在服务器上，root 或 sudo）：
#   bash deploy.sh /tmp/ai-marketing-20261018-2200.tar.gz
#
# 可选环境变量：
#   APP_DIR=/opt/ai-marketing   安装目录
#   PORT=8890                   监听端口
#   BIND=0.0.0.0                监听地址（只给 Nginx 用时可改 127.0.0.1）
# ============================================================
set -euo pipefail

PKG="${1:-}"
APP_DIR="${APP_DIR:-/opt/ai-marketing}"
PORT="${PORT:-8890}"
BIND="${BIND:-0.0.0.0}"
SERVICE_NAME="ai-marketing"
PY="${PY:-python3}"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[警告] %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 或 sudo 执行：sudo bash $0 <部署包.tar.gz>"
  exit 1
fi
if [ -z "$PKG" ] || [ ! -f "$PKG" ]; then
  echo "用法：bash $0 <部署包.tar.gz>"
  exit 1
fi

# ---------- 1. 系统依赖 ----------
log "安装系统依赖（python3 / venv）"
if [ "${SKIP_BROWSER:-0}" = "1" ]; then
  echo "SKIP_BROWSER=1：跳过浏览器安装（可稍后用 playwright 方案安装）"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip curl
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip curl
  fi
elif command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip curl
  BROWSER_OK=0
  for pkg in chromium chromium-browser google-chrome-stable; do
    if apt-get install -y "$pkg" >/dev/null 2>&1; then
      echo "已安装浏览器：$pkg"
      BROWSER_OK=1
      break
    fi
  done
  [ "$BROWSER_OK" -eq 1 ] || warn "浏览器安装失败（海报出图会降级为占位图，其余功能正常）"
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 python3-pip curl
  if yum install -y chromium >/dev/null 2>&1; then
    echo "已安装浏览器：chromium"
  else
    warn "chromium 安装失败（可先启用 EPEL：yum install -y epel-release；海报出图会降级）"
  fi
else
  warn "未识别的包管理器，请手动安装 python3 / pip / venv / chromium"
fi

# ---------- 2. 解包 ----------
log "解包到 ${APP_DIR}"
mkdir -p "$APP_DIR"
tar -xzf "$PKG" -C "$APP_DIR"
mkdir -p "$APP_DIR/data" "$APP_DIR/outputs"

if [ ! -f "$APP_DIR/.env" ]; then
  warn "未找到 .env —— 请在 ${APP_DIR}/.env 中配置 DEEPSEEK_API_KEY 后重启服务"
else
  chmod 600 "$APP_DIR/.env" || true
fi

# ---------- 3. Python 环境 ----------
log "创建虚拟环境并安装依赖（首次约 1-2 分钟）"
cd "$APP_DIR"
if [ ! -x .venv/bin/python ]; then
  $PY -m venv .venv
fi
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

# ---------- 4. systemd 服务 ----------
log "写入 systemd 服务：${SERVICE_NAME}"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=AdAgent 工作台
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app.server:app --host ${BIND} --port ${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
systemctl restart "$SERVICE_NAME"

# ---------- 5. 健康检查 ----------
log "等待服务就绪"
READY=0
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    READY=1
    echo "服务已就绪：http://127.0.0.1:${PORT}"
    break
  fi
  sleep 1
done
if [ "$READY" -eq 0 ]; then
  warn "服务未就绪，请查看日志：journalctl -u ${SERVICE_NAME} -n 80 --no-pager"
fi

# ---------- 6. 海报出图能力自检 ----------
log "检查海报出图能力"
if .venv/bin/python -c "from app.agents.poster_agent import find_browser; import sys; sys.exit(0 if find_browser() else 1)" 2>/dev/null; then
  echo "浏览器可用 —— 海报可正常出图"
else
  warn "未找到 Chromium 内核浏览器 —— 海报环节会降级为占位图（其余功能正常）"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

============================================================
 部署完成
   访问：http://${IP:-<服务器IP>}:${PORT}
   服务：systemctl status  ${SERVICE_NAME}
   日志：journalctl -u ${SERVICE_NAME} -f
   重启：systemctl restart ${SERVICE_NAME}
   配置：${APP_DIR}/.env（改完记得 restart）
============================================================
 提示：若外网访问不了，检查云厂商安全组是否放行 ${PORT} 端口
============================================================
EOF
