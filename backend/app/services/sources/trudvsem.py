"""«Работа России»: открытые данные, http://opendata.trudvsem.ru/api/v1/vacancies

Контакты рекрутера (contact_person, contact_list, email компании) не сохраняем: это ПДн, нам не нужны.
"""

from datetime import datetime

import httpx

from app.services.sources.base import RawVacancy, guess_remote

URL = "http://opendata.trudvsem.ru/api/v1/vacancies"


def _to_int(x) -> int | None:
    try:
        v = int(float(x))
        return v or None
    except (TypeError, ValueError):
        return None


def parse(item: dict) -> RawVacancy:
    v = item["vacancy"]
    company = v.get("company") or {}
    req = v.get("requirement") or {}
    desc_parts = [v.get("duty"), v.get("requirements"),
                  f"Опыт от {req['experience']} лет" if req.get("experience") else None,
                  req.get("education"), v.get("conditions")]
    skills = v.get("skills") or []
    if skills:
        desc_parts.append("Навыки: " + ", ".join(map(str, skills)))
    try:
        published = datetime.fromisoformat(v.get("date_modify") or v.get("creation-date"))
    except (TypeError, ValueError):
        published = None
    site = company.get("site") or ""
    return RawVacancy(
        source="trudvsem",
        external_id=str(v["id"]),
        title=v.get("job-name") or "",
        company_name=company.get("name") or "",
        url=v.get("vac_url") or "",
        description="\n".join(p for p in desc_parts if p),
        city=(v.get("region") or {}).get("name"),
        remote=guess_remote(v.get("schedule"), v.get("employment")),
        salary_from=_to_int(v.get("salary_min")),
        salary_to=_to_int(v.get("salary_max")),
        currency="RUB",
        published_at=published,
        company_domain=site.split("//")[-1].split("/")[0].removeprefix("www.") or None,
    )


async def search(text: str, limit: int = 100, client: httpx.AsyncClient | None = None) -> list[RawVacancy]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        r = await client.get(URL, params={"text": text, "limit": min(limit, 100), "offset": 0})
        r.raise_for_status()
        items = (r.json().get("results") or {}).get("vacancies") or []
        return [parse(i) for i in items]
    finally:
        if own:
            await client.aclose()
