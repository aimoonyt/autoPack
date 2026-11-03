#!/usr/bin/env bash
# После того как закинул новые папки app и webapp:
#   sudo bash deploy/update.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
# если ставили через git clone — подтягиваем свежий код
if [ -d .git ]; then
    git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
    git pull --ff-only
fi
sed -i 's/\r$//' .env
.venv/bin/pip install -q -r requirements.txt
chown -R autopack:autopack "$APP_DIR"
systemctl restart autopack
sleep 3
systemctl is-active --quiet autopack && echo "Обновлено, бот работает." || { journalctl -u autopack -n 40 --no-pager; exit 1; }
