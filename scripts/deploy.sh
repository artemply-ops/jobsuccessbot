#!/usr/bin/env bash
# Деплой на общий сервер (2 ГБ RAM). Запуск из корня проекта: bash scripts/deploy.sh
# 1) фронтенд собирается локально, 2) код уходит в GitHub, 3) готовый web/dist копируется на сервер,
# 4) на сервере git pull и сборка Python-образа С ОСТАНОВЛЕННЫМ app, чтобы сборка не вызвала OOM
#    (16.10.2026 сборка с работающим app исчерпала память, ядро убило uvicorn).
set -euo pipefail
cd "$(dirname "$0")/.."

SSH_KEY="$HOME/.ssh/id_ed25519_tihoeslovo_deploy"
HOST="deploy@201.24.55.219"
DIR=/opt/jobsuccessbot
SSH_OPTS=(-o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=6 -i "$SSH_KEY")

retry() {  # повтор только при обрыве SSH (код 255)
  local rc
  for i in $(seq 1 40); do
    set +e; "$@"; rc=$?; set -e
    [ $rc -ne 255 ] && return $rc
    echo "  SSH оборвался, повтор $i..." >&2; sleep 8
  done
  return $rc
}

remote() {  # скрипт на сервер заново при каждой попытке: heredoc в retry читался один раз,
  local script="$1"  # и после обрыва повтор запускал пустой скрипт с кодом 0 (20.11.2026)
  retry ssh "${SSH_OPTS[@]}" "$HOST" "bash -s" <<<"$script"
}

echo "1/5 сборка фронтенда"
(cd web && npm run build --silent >/dev/null)

echo "2/5 push в GitHub"
if [ -n "$(git status --porcelain)" ]; then echo "Есть незакоммиченные изменения, сначала commit"; exit 1; fi
for i in 1 2 3 4 5; do git push -q origin main && break; sleep 8; done
REV=$(git rev-parse --short HEAD)

echo "3/5 web/dist на сервер"
tar -C web -czf /tmp/jsb-dist.tgz dist
retry scp -q "${SSH_OPTS[@]}" /tmp/jsb-dist.tgz "$HOST:/tmp/jsb-dist.tgz"
rm -f /tmp/jsb-dist.tgz

echo "4/5 git pull и сборка образа (app работает, пока хватает памяти)"
remote "$(cat <<EOS
set -e
cd $DIR
# Повтор после обрыва SSH: сборка уже запущена прошлой попыткой, второй раз не запускаем
if pgrep -f "jobsuccess build -q app" >/dev/null && [ "\$(git rev-parse --short HEAD)" = "$REV" ]; then
  echo "  сборка уже идёт, коммит $REV"; exit 0
fi
git fetch -q origin main && git reset -q --hard origin/main
test "\$(git rev-parse --short HEAD)" = "$REV" || { echo "на сервере не тот коммит"; exit 1; }
rm -rf web/dist && tar -C web -xzf /tmp/jsb-dist.tgz && rm -f /tmp/jsb-dist.tgz
# Сборка при работающем app: сайт и бот не лежат, даже если pip качает долго. Фронтенд здесь не собирается
# (причина OOM 16.10.2026 была в npm). Если свободной памяти меньше 1500 МБ, app на время сборки останавливается.
# При неудачной сборке app поднимается на прежнем образе: бот не остаётся выключенным
nohup sh -c 'C="docker compose -f docker-compose.server.yml -p jobsuccess"; AV=\$(grep MemAvailable /proc/meminfo | tr -dc 0-9); echo "памяти свободно \$AV кБ"; [ "\$AV" -lt 1536000 ] && \$C stop app; if \$C build -q app; then \$C up -d app; echo EXIT=0; else \$C up -d --no-build app; echo "EXIT=1 (сборка упала, app поднят на прежнем образе)"; fi' > /tmp/jsb-build.log 2>&1 </dev/null &
echo "  сборка запущена на сервере, коммит $REV"
EOS
)"

echo "5/5 жду старт"
remote "$(cat <<'EOS'
for i in $(seq 1 360); do grep -q "EXIT=" /tmp/jsb-build.log && break; sleep 5; done
grep -a "EXIT=" /tmp/jsb-build.log || { echo "сборка не завершилась за 30 минут, app работает на прежнем образе"; tail -5 /tmp/jsb-build.log; exit 1; }
cd /opt/jobsuccessbot
for i in $(seq 1 40); do
  docker compose -f docker-compose.server.yml -p jobsuccess logs --since 5m app 2>&1 </dev/null | grep -q "Run polling for bot" && break; sleep 3
done
docker compose -f docker-compose.server.yml -p jobsuccess logs --since 5m app 2>&1 </dev/null | grep -aoE "Run polling for bot @[A-Za-z0-9_]+|Traceback|ERROR.*" | head -3
docker ps --format '{{.Names}} {{.Status}}' | grep jobsuccess
free -m | sed -n 2p
EOS
)"
