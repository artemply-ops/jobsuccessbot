"""П6. Ориентир по зарплате только из открытых источников, с датой и источником.

Источник: открытые данные «Работы России» (opendata.trudvsem.ru, тот же легальный источник, что в services/sources).
Берём до 200 вакансий по названию должности, оставляем похожие по названию и с указанной зарплатой в рублях,
считаем медиану и разброс (25-75 перцентиль). Город: если по нему набирается MIN_N вакансий, считаем по городу,
иначе по России и честно пишем это. Меньше MIN_N вакансий: ориентира нет, цифру не выдумываем.
Без ИИ и без данных пользователя: во внешний запрос уходит только название должности.
"""

import logging
import re
import statistics
import time
from datetime import date

import httpx

from app.services.coverage import _stem
from app.services.sources import trudvsem

log = logging.getLogger(__name__)

MIN_N = 5
SOURCE = "«Работа России», открытые данные (trudvsem.ru)"
CAVEAT = ("В базе «Работы России» много вакансий регионов и госсектора: в крупных частных компаниях и в IT "
          "зарплаты бывают выше.")
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
TTL = 12 * 3600


def _words(s: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-zа-яё0-9+#]{3,}", (s or "").lower())}


def similar(role: str, title: str) -> bool:
    """Похожая вакансия: в названии есть большинство значимых слов искомой должности."""
    need = _words(role)
    if not need:
        return False
    return len(need & _words(title)) >= max(1, round(len(need) * 0.6))


def _point(v) -> int | None:
    lo, hi = v.salary_from, v.salary_to
    if lo and hi:
        return (lo + hi) // 2
    return lo or hi


def _round(x: float) -> int:
    return int(round(x / 1000.0) * 1000)


def summarize(role: str, city: str | None, vacancies: list) -> dict:
    pts = [(v, _point(v)) for v in vacancies if similar(role, v.title) and (v.currency or "RUB") == "RUB"]
    pts = [(v, p) for v, p in pts if p and 10_000 <= p <= 5_000_000]  # отсекаем опечатки и почасовые ставки
    scope, use = "по России", pts
    c = (city or "").strip().lower().removeprefix("г.").strip()
    if c:
        local = [(v, p) for v, p in pts if c in (v.city or "").lower()]
        if len(local) >= MIN_N:
            scope, use = city.strip(), local
    base = {"role": role, "source": SOURCE, "date": date.today().isoformat(), "caveat": CAVEAT,
            "url": "https://trudvsem.ru/vacancy/search?_title=" + role.replace(" ", "+")}
    if len(use) < MIN_N:
        return {**base, "n": len(use), "scope": scope, "median": None,
                "reason": f"Похожих вакансий с зарплатой меньше {MIN_N}: ориентир был бы случайным."}
    vals = sorted(p for _, p in use)
    q = statistics.quantiles(vals, n=4) if len(vals) >= 4 else [vals[0], statistics.median(vals), vals[-1]]
    return {**base, "n": len(vals), "scope": scope, "median": _round(statistics.median(vals)),
            "p25": _round(q[0]), "p75": _round(q[2])}


async def estimate(role: str, city: str | None = None) -> dict:
    role = " ".join((role or "").split())[:120]
    if not role:
        return {"n": 0, "median": None, "reason": "Не знаю должность: укажите её, и я посчитаю ориентир."}
    key = (role.lower(), (city or "").lower())
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    found = None
    # API «Работы России» медленный и нестабильный (17.11.2026 с сервера: limit=5 за 15 с, limit=100 за 17 с,
    # limit=50 за 22 с, бывает 500). Размер порции на время почти не влияет, поэтому таймаут большой, повтор один
    async with httpx.AsyncClient(timeout=45) as client:
        for limit in (100, 50):
            try:
                found = await trudvsem.search(role, limit, client=client)
                break
            except (httpx.HTTPError, ValueError) as e:
                log.warning("salary: trudvsem limit=%s %s: %s", limit, type(e).__name__, e)
    if found is None:
        return {"n": 0, "median": None, "role": role, "source": SOURCE,
                "reason": "Источник «Работа России» сейчас не отвечает. Попробуйте позже."}
    res = summarize(role, city, found)
    _cache[key] = (time.monotonic(), res)
    return res


def role_and_city(target, vacancy, profile_data: dict | None) -> tuple[str, str | None]:
    """Под вакансию: её должность и город. Без вакансии: желаемая роль или заголовок профиля."""
    parsed = target.parsed or {}
    if parsed.get("general"):
        return (parsed.get("role") or (profile_data or {}).get("headline") or ""), (profile_data or {}).get("city")
    return (parsed.get("title") or vacancy.title or ""), parsed.get("city") or None


def text(res: dict) -> str:
    """Одной строкой для бота."""
    if not res.get("median"):
        return f"💰 Ориентир по зарплате: {res.get('reason', 'данных нет')}"
    return (f"💰 <b>Ориентир по зарплате «{res['role']}» {res['scope']}: медиана {res['median']:,} ₽</b>, "
            f"обычно {res['p25']:,}-{res['p75']:,} ₽ (по {res['n']} вакансиям).\n"
            f"<i>Источник: {res['source']}, {res['date']}. {res['caveat']}</i>").replace(",", " ")
