"""П6. Ориентир по зарплате только из открытых источников, с датой и источником.

Источник: открытые данные «Работы России» (opendata.trudvsem.ru, тот же легальный источник, что в services/sources).
Берём до 200 вакансий по названию должности, оставляем похожие по названию и с указанной зарплатой в рублях,
считаем медиану и разброс (25-75 перцентиль). Город: если по нему набирается MIN_N вакансий, считаем по городу,
иначе по России и честно пишем это. Меньше MIN_N вакансий: ориентира нет, цифру не выдумываем.
Без ИИ и без данных пользователя: во внешний запрос уходит только название должности.

Т17: API «Работы России» отвечает 15-22 с и бывает недоступен. Поэтому посчитанные ориентиры хранятся в файле
(FILES_DIR/_cache/salary.json, только должность, город и цифры, без данных людей) и переживают перезапуск.
Раз в сутки планировщик пересчитывает должности, которые спрашивали за последние 30 дней (refresh_popular):
повторный запрос отвечает сразу. Если источник лежит, отдаём последний расчёт не старше 7 дней с его датой.
"""

import logging
import re
import statistics
import time
from datetime import date

import asyncio
import json

import httpx

from app.config import get_settings
from app.services.coverage import _stem
from app.services.sources import trudvsem

log = logging.getLogger(__name__)

MIN_N = 5
SOURCE = "«Работа России», открытые данные (trudvsem.ru)"
CAVEAT = ("В базе «Работы России» много вакансий регионов и госсектора: в крупных частных компаниях и в IT "
          "зарплаты бывают выше.")
FRESH = 36 * 3600  # свежий расчёт: отвечаем без запроса к источнику
STALE = 7 * 86400  # источник не отвечает: отдаём расчёт не старше недели (дата в ответе своя)
ASKED = 30 * 86400  # ночной пересчёт только для должностей, которые спрашивали за 30 дней
_store: dict[str, dict] | None = None  # "должность|город" -> {"at": время расчёта, "asked": время запроса, "res": ...}


def _path():
    return get_settings().files_dir / "_cache" / "salary.json"


def _load() -> dict[str, dict]:
    global _store
    if _store is None:
        try:
            _store = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _store = {}
    return _store


def _save() -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_load(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        log.warning("salary: cache not saved: %s", e)

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


async def _fetch(role: str, city: str | None) -> dict | None:
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
    return None if found is None else summarize(role, city, found)


async def estimate(role: str, city: str | None = None) -> dict:
    role = " ".join((role or "").split())[:120]
    if not role:
        return {"n": 0, "median": None, "reason": "Не знаю должность: укажите её, и я посчитаю ориентир."}
    key = f"{role.lower()}|{(city or '').strip().lower()}"
    store, now = _load(), time.time()
    hit = store.get(key)
    if hit:
        hit["asked"] = now
        if now - hit["at"] < FRESH:
            _save()
            return hit["res"]
    res = await _fetch(role, city)
    if res is None:
        if hit and now - hit["at"] < STALE:
            _save()
            return hit["res"]
        return {"n": 0, "median": None, "role": role, "source": SOURCE,
                "reason": "Источник «Работа России» сейчас не отвечает. Попробуйте позже."}
    store[key] = {"at": now, "asked": now, "role": role, "city": city, "res": res}
    _save()
    return res


async def refresh_popular(limit: int = 30) -> dict:
    """Т17, раз в сутки из планировщика: пересчитать должности, которые спрашивали за 30 дней, и забыть старые."""
    store, now = _load(), time.time()
    for k in [k for k, v in store.items() if now - v.get("asked", 0) > ASKED]:
        del store[k]
    todo = sorted(store.items(), key=lambda kv: -kv[1].get("asked", 0))[:limit]
    done = failed = 0
    for key, item in todo:
        if now - item["at"] < 12 * 3600:  # уже свежий
            continue
        res = await _fetch(item["role"], item.get("city"))
        if res is None:
            failed += 1
        else:
            store[key] = {**item, "at": time.time(), "res": res}
            done += 1
        await asyncio.sleep(2)  # не долбим медленный источник подряд
    _save()
    return {"kept": len(store), "refreshed": done, "failed": failed}

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
