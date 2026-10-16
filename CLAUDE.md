# JobPilot: правила проекта

Личный проект Артёма. Сервис поиска работы с ИИ: Telegram-бот + PWA (она же Telegram Mini App).
MVP: блок 1 (профиль кандидата), блок 2 (поиск и скоринг вакансий + каталог компаний), блок 3 (пакет под вакансию).

## Первым делом
Прочитать хвост `PROJECT_MEMORY.md`: раздел «В процессе», «Следующие шаги», «Открытые вопросы».

## Структура
- `backend/app/services/` вся логика: `profile.py` (блок 1), `collect.py` + `sources/` + `scoring.py` (блок 2), `package.py` + `export.py` (блок 3), `pii.py` (обезличивание), `llm.py` (LLM через OpenRouter)
- `backend/app/api/routes.py` HTTP API, `backend/app/bot/__main__.py` бот, `backend/app/jobs.py` фоновые задачи
- `web/` PWA (Vite + React + TS), собирается в `web/dist`, раздаётся бэкендом
- `data/companies_seed.yaml` посевной каталог компаний

## Жёсткие правила
- Во внешнюю LLM только обезличенный текст. Любой новый вызов LLM идёт через `services/llm.structured`, где стоит страховка `has_pii`.
- Контакты человека живут только в таблице `contacts`, в `profiles` их нет. Реальные данные подставляются только в `export.py`.
- Голос это ПДн: распознавание только через Yandex SpeechKit (РФ).
- Источники вакансий только легальные. Реестр в `services/sources/base.py`. hh.ru не парсим. robots.txt соблюдаем.
- Контакты рекрутеров из вакансий не храним (см. `collect._clean`, `trudvsem.parse`).
- В CV нельзя выдуманных фактов: проверка фактов (`SYSTEM_FACTCHECK`) обязательна для каждого пакета.

## Сервер
Timeweb, Москва, `201.24.55.219`, общий с «Тихим словом» и другими ботами (2 ГБ RAM, память беречь).
SSH: `ssh -i ~/.ssh/id_ed25519_tihoeslovo_deploy deploy@201.24.55.219`. Соединение часто рвётся:
каждую команду в цикл повтора `while :; do ssh ...; rc=$?; [ $rc -ne 255 ] && break; sleep 8; done`, в фоне.
- Код: `/opt/jobsuccessbot`, секреты в `/opt/jobsuccessbot/.env` (только на сервере, chmod 600)
- Стек: `docker compose -f docker-compose.server.yml -p jobsuccess ...` (db, app, caddy)
- app = API + PWA + бот в одном процессе (RUN_BOT_IN_API=true), планировщик внутри (RUN_SCHEDULER=true)
- HTTPS: Caddy, домен `201-24-55-219.sslip.io` (временный, заменить на свой)
- Telegram и OpenRouter из РФ через прокси `PROXY_URL` / `LLM_PROXY_URL` (тот же, что у «Тихого слова», выход DE).
  OpenRouter с российского IP отдаёт 403. Прокси нужен с DNS на его стороне (socks5h), адаптер переводит сам
- Бот: @job_success_bot. Админ: tg 52622104
- Секреты: владелец вводит сам `scripts\set_secret.cmd КЛЮЧ` (PowerShell или cmd; .ps1 запрещены политикой) или `bash scripts/set_secret.sh КЛЮЧ` (Git Bash). Ввод скрыт, значение идёт через stdin SSH, app перезапускается. Секреты в чат не просим
- Деплой: push в GitHub `artemply-ops/jobsuccessbot` → на сервере `git pull` → `up -d --build app`

## LLM
OpenRouter, OpenAI-совместимый API, ответ строго по JSON-схеме. Модели в `.env`: `LLM_MODEL_MAIN`
(по умолчанию anthropic/claude-sonnet-5.5), `LLM_MODEL_SCORING` (anthropic/claude-haiku-5.5). Смена модели без правки кода.

## Режим без ИИ
Пока нет LLM_API_KEY (ключ OpenRouter), `llm.available()` = False: шаги с ИИ отвечают заглушкой и ведут дальше.
Профиль: базовый из строки «должность, город, зарплата, формат». Скоринг: `method=basic` по ключевым словам,
после подключения ИИ пересчитывается полноценно. Голос: заглушка, пока нет YANDEX_API_KEY.

## Команды (Windows, из корня проекта)
- Тесты: `cd backend; $env:PYTHONUTF8=1; .venv\Scripts\python -m pytest -q`
- API + PWA: `backend\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000`
- Бот: `cd backend; .venv\Scripts\python -m app.bot`
- Фронт dev: `cd web; npm run dev` (прокси /api на :8000), сборка `npm run build`
- Каталог: POST `/api/admin/seed`, затем `/api/admin/discover`
