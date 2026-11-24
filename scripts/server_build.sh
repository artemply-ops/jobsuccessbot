#!/usr/bin/env bash
# Сборка и перезапуск app НА СЕРВЕРЕ. Вызывает scripts/deploy.sh (в фоне, лог в /tmp/jsb-build.log).
# 1) базовый образ (apt, pip) собирается, только если его нет для текущего хэша requirements.txt и Dockerfile.base;
# 2) образ app поверх него: только код, секунды;
# 3) при неудачной сборке app поднимается на прежнем образе, бот не остаётся выключенным.
# Сборка идёт при работающем app; если свободной памяти меньше 1500 МБ, app на время сборки останавливается.
cd /opt/jobsuccessbot
C="docker compose -f docker-compose.server.yml -p jobsuccess"
AV=$(grep MemAvailable /proc/meminfo | tr -dc 0-9)
echo "памяти свободно $AV кБ"
[ "$AV" -lt 1536000 ] && $C stop app

H=$(cat backend/requirements.txt Dockerfile.base | sha256sum | cut -c1-12)
export APP_BASE="jobsuccess-base:$H"
ok=1
if docker image inspect "$APP_BASE" >/dev/null 2>&1; then
  echo "базовый образ $APP_BASE есть"
else
  echo "базовый образ $APP_BASE собирается (apt и pip)"
  DOCKER_BUILDKIT=1 docker build -q -f Dockerfile.base -t "$APP_BASE" -t jobsuccess-base:latest . || ok=0
fi
if [ $ok = 1 ] && $C build -q app; then
  $C up -d app
  # старые базовые образы (другие хэши) больше не нужны; текущий и latest остаются
  docker images jobsuccess-base --format '{{.Tag}}' | grep -vxE "$H|latest" | sed 's/^/jobsuccess-base:/' | xargs -r docker rmi >/dev/null 2>&1
  echo EXIT=0
else
  $C up -d --no-build app
  echo "EXIT=1 (сборка упала, app поднят на прежнем образе)"
fi
