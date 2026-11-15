#!/usr/bin/env bash
# Переключить сервис на свой домен. Запуск из корня проекта: bash scripts/set_domain.sh job-pilot.ru
# Перед запуском у регистратора должны быть A-записи домена и www на 201.24.55.219 (проверка ниже).
# Старый адрес sslip.io остаётся рабочим. После запуска: /setdomain у @BotFather на этот домен (вход на сайте).
set -euo pipefail
D="${1:?укажите домен, например job-pilot.ru}"
IP=201.24.55.219
ip_of() { nslookup "$1" 8.8.8.8 2>/dev/null | awk '/^Address/ {a=$2} END {print a}'; }
got=$(ip_of "$D")
[ "$got" = "$IP" ] || { echo "$D указывает на '${got:-ничего}', нужно $IP. Проверьте DNS и повторите позже"; exit 1; }
HOSTS="$D"
got=$(ip_of "www.$D")
if [ "$got" = "$IP" ]; then HOSTS="$D, www.$D"
else echo "www.$D пока указывает на '${got:-ничего}': подключаю без www. Исправьте запись и запустите скрипт ещё раз"; fi
SSH_KEY="$HOME/.ssh/id_ed25519_tihoeslovo_deploy"
for i in $(seq 1 20); do
  set +e
  ssh -o ConnectTimeout=15 -i "$SSH_KEY" deploy@$IP "bash -s" <<EOS
set -e
cd /opt/jobsuccessbot
setv() { if grep -q "^\$1=" .env; then sed -i "s|^\$1=.*|\$1=\$2|" .env; else echo "\$1=\$2" >> .env; fi; }
setv DOMAIN '"$HOSTS, 201-24-55-219.sslip.io"'
setv PUBLIC_URL "https://$D"
C="docker compose -f docker-compose.server.yml -p jobsuccess"
\$C up -d caddy app
echo "готово: DOMAIN и PUBLIC_URL обновлены, caddy и app перезапущены"
EOS
  rc=$?; set -e
  [ $rc -ne 255 ] && exit $rc
  sleep 8
done
