"""Блок 2. Скоринг: жёсткие фильтры + лексический предфильтр -> LLM-оценка топ-N пачками.

Эмбеддинги в MVP не используем: у Claude нет эмбеддингов, а лексический предфильтр
по ролям и навыкам на объёмах MVP (тысячи вакансий) достаточен и бесплатен.
"""

import json
import re

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Company, CompanyScore, Profile, Vacancy, VacancyScore
from app.schemas import CompanyFits, ProfileData, VacancyAssessments
from app.services import llm
from app.services.profile import profile_to_text

WORD_RE = re.compile(r"[a-zа-яё0-9+#.]{2,}", re.IGNORECASE)
STOP = {"и", "в", "на", "с", "по", "для", "от", "до", "the", "and", "of", "or", "работа", "опыт", "лет", "год"}
BATCH = 10


def _stems(text: str) -> set[str]:
    out = set()
    for w in WORD_RE.findall((text or "").lower().replace("ё", "е")):
        w = w.strip(".")
        if w in STOP or len(w) < 2:
            continue
        out.add(w[:6] if re.match(r"[а-я]", w) and len(w) > 6 else w)  # грубый стемминг для русского
    return out


def hard_filter(p: ProfileData, v: Vacancy) -> str | None:
    """Причина отсева или None."""
    text = f"{v.title}\n{v.description}".lower()
    for sf in p.stop_factors:
        sf = sf.lower().strip()
        if 3 <= len(sf) <= 40 and sf in text:
            return f"стоп-фактор: {sf}"
    ex = p.expectations
    if ex.salary_min and v.salary_to and v.salary_to < ex.salary_min * 0.8:
        return "зарплата ниже ожиданий"
    only_remote = ex.formats == ["remote"]
    if only_remote and v.remote is False:
        return "не удалёнка"
    if ex.cities and not ex.relocation and v.city and v.remote is not True and "remote" not in ex.formats:
        if not any(c.lower() in v.city.lower() for c in ex.cities):
            return "другой город"
    return None


def prefilter_score(p: ProfileData, v: Vacancy) -> float:
    role_terms = _stems(" ".join(p.target_roles) + " " + p.headline)
    skill_terms = _stems(" ".join(s.name for s in p.skills))
    title, desc = _stems(v.title), _stems(v.description)
    if not (role_terms or skill_terms):
        return 0.0
    role_hit = len(role_terms & title) / max(len(role_terms), 1)
    skill_hit = len(skill_terms & (desc | title)) / max(min(len(skill_terms), 15), 1)
    return round(0.6 * role_hit + 0.4 * min(skill_hit, 1.0), 4)


SYSTEM_SCORE = """Ты опытный рекрутер. Оцени, насколько каждая вакансия подходит кандидату, 0-100.
Разбивка (каждый пункт 0-100): skills (навыки), experience (релевантность опыта), level (уровень роли),
industry (отрасль), money (деньги относительно ожиданий; нет данных = 50), format (город/формат).
Итоговый score не среднее арифметическое: навыки и опыт весят больше всего, явный провал по деньгам
или формату сильно снижает итог. pros и gaps: по 1-3 коротких пункта, конкретно, без воды.
Верни оценку для каждой вакансии с её vacancy_id. Пиши по-русски."""


BASIC_GAP = "ИИ-оценка пока не подключена: это предварительная оценка по совпадению ключевых слов"


async def score_user(session: AsyncSession, user_id: int, vacancy_ids: list[int] | None = None) -> dict:
    s = get_settings()
    prof = (await session.execute(select(Profile).where(Profile.user_id == user_id)
                                  .order_by(Profile.version.desc()).limit(1))).scalar_one_or_none()
    if prof is None:
        raise ValueError("Сначала соберите профиль")
    p = ProfileData.model_validate(prof.data)
    use_ai = llm.available()
    if use_ai:  # предварительные оценки без ИИ заменяем полноценными
        await session.execute(delete(VacancyScore).where(
            VacancyScore.user_id == user_id, VacancyScore.profile_version == prof.version,
            VacancyScore.method == "basic"))

    q = select(Vacancy).where(Vacancy.active.is_(True))
    if vacancy_ids:
        q = q.where(Vacancy.id.in_(vacancy_ids))
    vacancies = (await session.execute(q)).scalars().all()
    done = set((await session.execute(select(VacancyScore.vacancy_id).where(
        VacancyScore.user_id == user_id, VacancyScore.profile_version == prof.version))).scalars())

    candidates, filtered = [], 0
    for v in vacancies:
        if v.id in done:
            continue
        if hard_filter(p, v):
            filtered += 1
            continue
        candidates.append((prefilter_score(p, v), v))
    candidates.sort(key=lambda x: x[0], reverse=True)
    top = [(ps, v) for ps, v in candidates[: s.prefilter_top_n] if ps > 0]

    if not use_ai:
        basic = [(ps, v) for ps, v in candidates[:100] if ps > 0]
        for ps, v in basic:
            session.add(VacancyScore(user_id=user_id, vacancy_id=v.id, profile_version=prof.version, prefilter=ps,
                                     method="basic", score=round(ps * 100), breakdown={},
                                     pros=["Совпадает с целевой ролью"] if ps >= 0.3 else [], gaps=[BASIC_GAP]))
        await session.commit()
        return {"vacancies": len(vacancies), "filtered": filtered, "candidates": len(candidates),
                "scored": len(basic), "method": "basic"}

    profile_text = profile_to_text(prof.data)
    scored = 0
    for i in range(0, len(top), BATCH):
        chunk = top[i:i + BATCH]
        vac_text = "\n\n".join(
            f"<vacancy id=\"{v.id}\">\n{v.title} — {v.company_name}\nГород: {v.city or '?'}; удалёнка: "
            f"{'да' if v.remote else 'неизвестно' if v.remote is None else 'нет'}; зарплата: "
            f"{v.salary_from or '?'}–{v.salary_to or '?'} {v.currency or ''}\n{(v.description or '')[:3500]}\n</vacancy>"
            for _, v in chunk)
        res = await llm.structured(schema=VacancyAssessments, system=SYSTEM_SCORE, model=s.llm_model_scoring,
                                   effort="low", max_tokens=16000,
                                   user=f"Профиль кандидата:\n{profile_text}\n\nВакансии:\n{vac_text}")
        by_id = {a.vacancy_id: a for a in res.items}
        for ps, v in chunk:
            a = by_id.get(v.id)
            session.add(VacancyScore(
                user_id=user_id, vacancy_id=v.id, profile_version=prof.version, prefilter=ps,
                score=max(0, min(100, a.score)) if a else None,
                breakdown=a.breakdown.model_dump() if a else {}, pros=a.pros if a else [], gaps=a.gaps if a else []))
            scored += 1
        await session.commit()
    return {"vacancies": len(vacancies), "filtered": filtered, "candidates": len(candidates), "scored": scored}


SYSTEM_COMPANY = """Ты карьерный консультант. Оцени, насколько каждая компания подходит кандидату как
работодатель, 0-100: отрасль и продукт, масштаб, культура и ценности кандидата, вероятная потребность
в его роли. reasons: 1-3 коротких пункта. Верни оценку для каждой компании с её company_id. Пиши по-русски."""


async def score_companies(session: AsyncSession, user_id: int) -> dict:
    s = get_settings()
    if not llm.available():
        return {"scored": 0, "skipped": "ИИ-оценка компаний пока не подключена"}
    prof = (await session.execute(select(Profile).where(Profile.user_id == user_id)
                                  .order_by(Profile.version.desc()).limit(1))).scalar_one_or_none()
    if prof is None:
        raise ValueError("Сначала соберите профиль")
    done = set((await session.execute(select(CompanyScore.company_id).where(
        CompanyScore.user_id == user_id, CompanyScore.profile_version == prof.version))).scalars())
    comps = [c for c in (await session.execute(select(Company).where(Company.moderated.is_(True)))).scalars()
             if c.id not in done and c.status != "error"]
    profile_text = profile_to_text(prof.data)
    for i in range(0, len(comps), 40):
        chunk = comps[i:i + 40]
        lst = json.dumps([{"company_id": c.id, "name": c.name, "segments": c.segments, "about": c.description}
                          for c in chunk], ensure_ascii=False)
        res = await llm.structured(schema=CompanyFits, system=SYSTEM_COMPANY, model=s.llm_model_scoring,
                                   effort="low", max_tokens=16000,
                                   user=f"Профиль кандидата:\n{profile_text}\n\nКомпании:\n{lst}")
        ids = {c.id for c in chunk}
        for f in res.items:
            if f.company_id in ids:
                session.add(CompanyScore(user_id=user_id, company_id=f.company_id, profile_version=prof.version,
                                         score=max(0, min(100, f.score)), reasons=f.reasons))
        await session.commit()
    return {"scored": len(comps)}
