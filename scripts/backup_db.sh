#!/usr/bin/env bash
# Ежедневная резервная копия JobPilot на сервере (Т5). Запускается cron пользователя deploy:
#   15 3 * * * /opt/jobsuccessbot/scripts/backup_db.sh >> /opt/jobsuccessbot/backups/backup.log 2>&1
# Что копируется: база Postgres (pg_dump -Fc) и том files (загруженные резюме, фото; без _logs).
# Хранится BACKUP_KEEP_DAYS дней (по умолчанию 14) в /opt/jobsuccessbot/backups, доступ только у deploy (700).
# В копиях ПДн пользователей: наружу их не отправлять. Копия на этом же сервере не спасёт при потере сервера,
# вынос за пределы сервера (хранилище в РФ) решает владелец.
# Восстановление базы:
#   docker compose -f docker-compose.server.yml -p jobsuccess exec -T db \
#     pg_restore -U jobpilot -d jobpilot --clean --if-exists < backups/db_ДАТА.dump
set -euo pipefail
DIR=/opt/jobsuccessbot
OUT="$DIR/backups"
KEEP="${BACKUP_KEEP_DAYS:-14}"
C="docker compose -f $DIR/docker-compose.server.yml -p jobsuccess"
TS=$(date -u +%Y%m%d_%H%M)

umask 077
mkdir -p "$OUT"
chmod 700 "$OUT"
cd "$DIR"

$C exec -T db pg_dump -U jobpilot -d jobpilot -Fc > "$OUT/db_$TS.dump.part"
# Проверка, что дамп читается целиком: оглавление архива без ошибок
$C exec -T db pg_restore -l < "$OUT/db_$TS.dump.part" > /dev/null
mv "$OUT/db_$TS.dump.part" "$OUT/db_$TS.dump"

$C exec -T app tar -C /data/files --exclude=./_logs -czf - . > "$OUT/files_$TS.tgz.part"
gzip -t "$OUT/files_$TS.tgz.part"
mv "$OUT/files_$TS.tgz.part" "$OUT/files_$TS.tgz"

find "$OUT" -maxdepth 1 \( -name 'db_*.dump' -o -name 'files_*.tgz' -o -name '*.part' \) -mtime +"$KEEP" -delete
echo "$(date -u +%FT%TZ) ok db=$(du -h "$OUT/db_$TS.dump" | cut -f1) files=$(du -h "$OUT/files_$TS.tgz" | cut -f1) total=$(du -sh "$OUT" | cut -f1)"
