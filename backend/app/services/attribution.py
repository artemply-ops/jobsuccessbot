"""Откуда пришёл пользователь: метка первого касания, одна на кабинет.

Формат метки «канал_кампания», например tg_posev1, blog_ivanov, friend_petrov, seo, catalog_botlist.
Канал (часть до первого «_») группирует отчёт в админке. Ссылки:
- бот: https://t.me/<бот>?start=<метка> (Telegram пускает только A-Z a-z 0-9 _ -, до 64 символов)
- Mini App: https://t.me/<бот>/<app>?startapp=<метка>
- сайт: https://job-pilot.ru/?src=<метка> или стандартные utm_source / utm_campaign

Метка ставится только новому кабинету и не перезаписывается. При объединении кабинетов остаётся более ранняя.
Ни имён, ни контактов в метке нет: это код ссылки, а не данные человека.
"""

import re

MAX_LEN = 64
_BAD = re.compile(r"[^a-z0-9_.\-]+")


def clean(raw: str | None) -> str | None:
    """Нормализует метку: нижний регистр, только латиница, цифры, «_», «.», «-». Пустое и мусор дают None."""
    if not raw:
        return None
    s = _BAD.sub("", raw.strip().lower().replace(" ", "_"))[:MAX_LEN].strip("_.-")
    return s or None


def channel(source: str | None) -> str:
    """Канал для группировки: часть метки до первого «_». Без метки «direct» (пришёл сам)."""
    if not source:
        return "direct"
    return source.split("_", 1)[0]


def assign(user, raw: str | None) -> None:
    """Ставит метку, если её ещё нет (первое касание)."""
    if getattr(user, "source", None):
        return
    s = clean(raw)
    if s:
        user.source = s
