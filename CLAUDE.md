# JobPilot: правила проекта

Личный проект Артёма. Сервис поиска работы с ИИ: Telegram-бот + PWA (она же Telegram Mini App).
MVP: блок 1 (профиль кандидата), блок 2 (поиск и скоринг вакансий + каталог компаний), блок 3 (пакет под вакансию).

## Первым делом
Прочитать хвост `PROJECT_MEMORY.md`: раздел «В процессе», «Следующие шаги», «Открытые вопросы».

## Структура
- `backend/app/services/` вся логика: `profile.py` (блок 1), `collect.py` + `sources/` + `scoring.py` (блок 2), `package.py` + `tailor.py` + `export.py` (блок 3: резюме, одностраничник, письмо, сообщение менеджеру, LinkedIn; PDF и Word), `salary.py` (ориентир по зарплате), `pii.py` (обезличивание), `llm.py` (LLM через OpenRouter)
- `backend/app/api/routes.py` HTTP API, `backend/app/bot/__main__.py` бот, `backend/app/jobs.py` фоновые задачи
- `web/` PWA (Vite + React + TS), собирается в `web/dist`, раздаётся бэкендом
- `data/companies_seed.yaml` посевной каталог компаний
- `services/billing.py` цены, лимиты, промокоды (единственное место цен), `services/payments.py` ЮKassa и Stars,
  `api/billing_routes.py`, `bot/billing_flow.py`, `web/src/pages/Billing.tsx`, лендинг `Landing.tsx`, оферта `Legal.tsx`
- `services/accounts.py` один кабинет на сайт, Mini App и бота: гость, код на почту, Яндекс ID (выключен), объединение
  кабинетов; `api/auth_routes.py`, `web/src/pages/Auth.tsx`. Палитра сайта «зелёный + золото» в `styles.css`
- `docs/BACKLOG.md` блокеры, техдолг и продуктовый бэклог (новые идеи владельца записывать сюда)
- `docs/ARCHITECTURE.md` где что менять, `docs/ECONOMICS.md` цены, себестоимость, маржа (цель выше 60%)

## Жёсткие правила
- Во внешнюю LLM только обезличенный текст. Любой новый вызов LLM идёт через `services/llm.structured`, где стоит страховка `has_pii`.
- Контакты человека живут только в таблице `contacts`, в `profiles` их нет. Реальные данные подставляются только в `export.py`.
- Голос это ПДн: распознавание только через Yandex SpeechKit (РФ).
- Источники вакансий только легальные. Реестр в `services/sources/base.py`. hh.ru не парсим. robots.txt соблюдаем.
- Контакты рекрутеров из вакансий не храним (см. `collect._clean`, `trudvsem.parse`).
- Платные действия списываются только в `services/tailor.build` через `billing.require` / `billing.consume`; каналы
  заранее вызывают `billing.require` и показывают оплату. Внутри Telegram цифровые товары только за Stars.
- Учёт токенов: `llm.track(user_id, action)` в точке входа нового сценария, иначе расход не привяжется к действию.
- В CV нельзя выдуманных фактов: проверка фактов (`SYSTEM_FACTCHECK`) обязательна для каждого пакета.

## Сервер
Timeweb, Москва, `201.24.55.219`, общий с «Тихим словом» и другими ботами (4 ГБ RAM + swap 2 ГБ, память беречь).
SSH: `ssh -i ~/.ssh/id_ed25519_tihoeslovo_deploy deploy@201.24.55.219`. Соединение часто рвётся:
каждую команду в цикл повтора `while :; do ssh ...; rc=$?; [ $rc -ne 255 ] && break; sleep 8; done`, в фоне.
- Код: `/opt/jobsuccessbot`, секреты в `/opt/jobsuccessbot/.env` (только на сервере, chmod 600)
- Стек: `docker compose -f docker-compose.server.yml -p jobsuccess ...` (db, app, caddy)
- app = API + PWA + бот в одном процессе (RUN_BOT_IN_API=true), планировщик внутри (RUN_SCHEDULER=true)
- HTTPS: Caddy, домен `201-24-55-219.sslip.io`; свой домен job-pilot.ru (15.11.2026 владелец покупает).
  Переключение после A-записей: `bash scripts/set_domain.sh job-pilot.ru`, затем `/setdomain` у BotFather
- Telegram и OpenRouter из РФ через прокси `PROXY_URL` / `LLM_PROXY_URL` (тот же, что у «Тихого слова», выход DE).
  OpenRouter с российского IP отдаёт 403. Прокси нужен с DNS на его стороне (socks5h), адаптер переводит сам
- Бот: @job_success_bot. Админ: tg 52622104
- Секреты: владелец вводит сам `.\scripts\set_secret.cmd КЛЮЧ` (PowerShell, префикс `.\` обязателен; .ps1 запрещены политикой) или `bash scripts/set_secret.sh КЛЮЧ` (Git Bash). Ввод скрыт, значение идёт через stdin SSH, app перезапускается. Секреты в чат не просим
- Деплой ТОЛЬКО `bash scripts/deploy.sh`: фронтенд собирается локально, на сервере образ из `Dockerfile.server`.
  С 16.11.2026 образ собирается при работающем app (сайт не лежит), если свободно больше 1,5 ГБ, иначе app
  останавливается на время сборки. apt и pip в локальном базовом образе `Dockerfile.base` (тег по хэшу
  requirements.txt), он пересобирается только при смене библиотек; сборка на сервере в `scripts/server_build.sh`. OOM 16.10.2026 был от npm ci на сервере:
  фронтенд на сервере не собирать никогда
- Тексты лендинга владелец правит в админке (/admin/texts, `services/site_texts.py`), без выкатки

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
- Миграция БД (любое изменение моделей): `cd backend; $env:PYTHONUTF8=1; $env:PYTHONPATH='.'; .venv\Scripts\python -m alembic revision --autogenerate -m "что меняем"`,
  проверить файл глазами; на сервере применяется сама при старте app (`db.init_db`)
