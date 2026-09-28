# JobPilot: правила проекта

Личный проект Артёма. Сервис поиска работы с ИИ: Telegram-бот + PWA (она же Telegram Mini App).
MVP: блок 1 (профиль кандидата), блок 2 (поиск и скоринг вакансий + каталог компаний), блок 3 (пакет под вакансию).

## Первым делом
Прочитать хвост `PROJECT_MEMORY.md`: раздел «В процессе», «Следующие шаги», «Открытые вопросы».

## Структура
- `backend/app/services/` вся логика: `profile.py` (блок 1), `collect.py` + `sources/` + `scoring.py` (блок 2), `package.py` + `export.py` (блок 3), `pii.py` (обезличивание), `llm.py` (Claude)
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

## Команды (Windows, из корня проекта)
- Тесты: `cd backend; $env:PYTHONUTF8=1; .venv\Scripts\python -m pytest -q`
- API + PWA: `backend\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000`
- Бот: `cd backend; .venv\Scripts\python -m app.bot`
- Фронт dev: `cd web; npm run dev` (прокси /api на :8000), сборка `npm run build`
- Каталог: POST `/api/admin/seed`, затем `/api/admin/discover`
