"""SuperJob: официальный API https://api.superjob.ru/2.0/vacancies/

Включается ключом SUPERJOB_APP_ID. Формат полей сверить на живом ключе: без ключа не проверялся.
"""

from datetime import datetime, timezone

import httpx

from app.config import get_settings
from app.services.sources.base import RawVacancy, guess_remote, strip_html

URL = "https://api.superjob.ru/2.0/vacancies/"


def parse(v: dict) -> RawVacancy:
    ts = v.get("date_published")
    return RawVacancy(
        source="superjob",
        external_id=str(v["id"]),
        title=v.get("profession") or "",
        company_name=v.get("firm_name") or "",
        url=v.get("link") or "",
        description=strip_html(v.get("vacancyRichText") or v.get("candidat") or ""),
        city=(v.get("town") or {}).get("title"),
        remote=guess_remote((v.get("place_of_work") or {}).get("title")),
        salary_from=v.get("payment_from") or None,
        salary_to=v.get("payment_to") or None,
        currency=(v.get("currency") or "rub").upper(),
        published_at=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
    )


def enabled() -> bool:
    return bool(get_settings().superjob_app_id)


async def search(text: str, limit: int = 100) -> list[RawVacancy]:
    if not enabled():
        return []
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(URL, params={"keyword": text, "count": min(limit, 100)},
                        headers={"X-Api-App-Id": get_settings().superjob_app_id})
        r.raise_for_status()
        return [parse(v) for v in r.json().get("objects", [])]
