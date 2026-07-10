#!/usr/bin/env bash
# SEU-Monitor 运行脚本（供 cron / systemd 调用）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

# 加载 .env（如果存在）
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# 日志目录
mkdir -p logs
LOG_FILE="logs/monitor_$(date +%Y%m%d).log"

# Python 环境
PYTHON="${VENV_DIR:-.venv}/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] SEU-Monitor 开始运行" >> "$LOG_FILE"
exec "$PYTHON" monitor.py >> "$LOG_FILE" 2>&1
