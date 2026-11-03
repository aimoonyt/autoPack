#!/usr/bin/env bash
# Первая установка на VPS прямо из консоли хостера, без копирования файлов с компа:
#   apt update && apt install -y git
#   git clone -b claude/fervent-faraday-fx1pqg https://github.com/aimoonyt/autoPack /opt/autopack
#   bash /opt/autopack/deploy/bootstrap.sh
# Скрипт спросит токен бота, создаст .env и запустит deploy/install.sh.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

[ "$(id -u)" -eq 0 ] || { echo "Запусти от root (или через sudo)"; exit 1; }

if [ ! -f .env ]; then
    echo
    echo "=== Настройка бота ==="
    token=""
    while ! [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]]; do
        read -r -p "Вставь токен бота от @BotFather (вида 123456:ABC...): " token < /dev/tty
        token="$(echo "$token" | tr -d '[:space:]')"
        [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] || echo "Не похоже на токен, попробуй ещё раз."
    done
    read -r -p "Telegram ID админа [8030878478]: " admin < /dev/tty
    admin="$(echo "${admin:-8030878478}" | tr -d '[:space:]')"
    cat > .env <<ENV
BOT_TOKEN=$token
ADMIN_IDS=$admin
TIMEZONE=Europe/Moscow
DEFAULT_EMOJI=🙂
ENV
    chmod 600 .env
    echo ".env создан."
fi

exec bash "$APP_DIR/deploy/install.sh"
