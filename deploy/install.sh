#!/usr/bin/env bash
# Установка бота на VPS (Ubuntu/Debian) одной командой:
#   sudo bash deploy/install.sh
# Свой домен вместо бесплатного <ip>.sslip.io:
#   sudo DOMAIN=bot.example.com bash deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

say() { echo -e "\n\033[1;32m==> $*\033[0m"; }
die() { echo -e "\n\033[1;31mОшибка: $*\033[0m"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "запусти через sudo (или под root)"
[ -f .env ] || die "нет файла .env рядом с папкой app — закинь его на сервер"
[ -f requirements.txt ] || die "не вижу requirements.txt — запускай из папки бота"

# .env с Windows приходит с \r в конце строк
sed -i 's/\r$//' .env

say "Ставлю пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip curl ca-certificates gnupg
if ! apt-get install -y caddy; then
    say "Caddy нет в стандартных репозиториях, подключаю официальный"
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    apt-get install -y caddy
fi

if [ -z "${DOMAIN:-}" ]; then
    IP="$(curl -4 -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"
    [ -n "$IP" ] || die "не смог узнать IP сервера, задай DOMAIN вручную"
    DOMAIN="$(echo "$IP" | tr . -).sslip.io"
fi
say "Адрес мини-апки: https://$DOMAIN/"

say "Ставлю зависимости бота"
rm -rf .venv  # вдруг закинули виндовую .venv
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

set_env() {
    if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
set_env WEBAPP_URL "https://$DOMAIN/"
set_env HOST 127.0.0.1
set_env PORT 8080
set_env DB_PATH data/bot.db
mkdir -p data
rm -f data/cloudflared*.log

say "Создаю системного пользователя и сервис"
id -u autopack >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin autopack
chown -R autopack:autopack "$APP_DIR"

cat > /etc/systemd/system/autopack.service <<UNIT
[Unit]
Description=autoPack Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=autopack
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m app.main
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:8080
}
CADDY

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    say "Открываю порты 80 и 443 в ufw"
    ufw allow 80/tcp && ufw allow 443/tcp
fi

systemctl daemon-reload
systemctl enable --now autopack
systemctl restart autopack
systemctl enable caddy
systemctl restart caddy

sleep 5
if systemctl is-active --quiet autopack; then
    say "Готово! Бот работает."
    echo "Мини-апка: https://$DOMAIN/"
    echo "Логи бота:      journalctl -u autopack -f"
    echo "Перезапуск:     systemctl restart autopack"
    echo "Обновление:     sudo bash $APP_DIR/deploy/update.sh"
    echo
    echo "Не забудь выключить бота на компе — два запущенных бота мешают друг другу."
else
    journalctl -u autopack -n 40 --no-pager
    die "бот не запустился, логи выше"
fi
