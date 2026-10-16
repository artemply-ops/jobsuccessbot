#!/usr/bin/env bash
# Запись секрета в /opt/jobsuccessbot/.env на сервере без попадания в чат и историю команд.
#   bash scripts/set_secret.sh TELEGRAM_BOT_TOKEN
# Значение вводится скрыто, уходит на сервер через stdin SSH (не в аргументах), после записи
# приложение перезапускается и печатается только статус. Значение нигде не выводится.
set -u

ALLOWED="TELEGRAM_BOT_TOKEN YANDEX_API_KEY YANDEX_FOLDER_ID SUPERJOB_APP_ID LLM_API_KEY"
KEY="${1:-}"
if [ -z "$KEY" ] || ! grep -qw -- "$KEY" <<<"$ALLOWED"; then
  echo "Использование: bash scripts/set_secret.sh КЛЮЧ"
  echo "Разрешённые ключи: $ALLOWED"
  exit 1
fi

SSH_KEY="$HOME/.ssh/id_ed25519_tihoeslovo_deploy"
HOST="deploy@201.24.55.219"

printf "Введите значение %s (ввод скрыт): " "$KEY"
IFS= read -rs VALUE
echo
if [ -z "$VALUE" ]; then echo "Пустое значение, ничего не меняю."; exit 1; fi
printf "Повторите для проверки: "
IFS= read -rs VALUE2
echo
if [ "$VALUE" != "$VALUE2" ]; then echo "Значения не совпали, ничего не меняю."; exit 1; fi
unset VALUE2

REMOTE=$(cat <<'EOS'
set -e
K="$1"
IFS= read -r V
export V
cd /opt/jobsuccessbot
umask 077
awk -v k="$K" 'BEGIN{d=0} index($0, k"=")==1 {print k"="ENVIRON["V"]; d=1; next} {print} END{if(!d) print k"="ENVIRON["V"]}' .env > .env.tmp
mv .env.tmp .env && chmod 600 .env
unset V
echo "записано в .env"
docker compose -f docker-compose.server.yml -p jobsuccess up -d --force-recreate --no-deps app </dev/null >/dev/null 2>&1
echo "приложение перезапущено, жду старт..."
for i in $(seq 1 30); do
  sleep 3
  L=$(docker compose -f docker-compose.server.yml -p jobsuccess logs --since 2m app 2>&1 </dev/null)
  if echo "$L" | grep -q "Run polling for bot"; then echo "$L" | grep -o "Run polling for bot @[A-Za-z0-9_]*" | tail -1; exit 0; fi
  if echo "$L" | grep -qE "Unauthorized|TokenValidationError|Traceback"; then echo "ОШИБКА при старте:"; echo "$L" | grep -E "Unauthorized|TokenValidationError|Error" | tail -3; exit 2; fi
done
echo "Бот не подтвердил старт за 90 с, посмотрите логи: docker compose -f docker-compose.server.yml -p jobsuccess logs app"
exit 3
EOS
)

for i in $(seq 1 40); do
  printf '%s\n' "$VALUE" | ssh -o ConnectTimeout=15 -i "$SSH_KEY" "$HOST" "bash -c $(printf '%q' "$REMOTE") _ $KEY"
  rc=$?
  [ $rc -ne 255 ] && break
  echo "SSH оборвался, повтор $i..."
  sleep 8
done
unset VALUE
exit $rc
