"""Блок 3. Досье компании и пакет под вакансию: CV, сопроводительное, сообщение рекрутеру + проверка фактов."""

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Company, Dossier, Package, Vacancy
from app.schemas import DossierData, FactCheck, PackageDraft
from app.services import llm
from app.services.pii import Vault, depersonalize, scrub_contacts
from app.services.profile import latest_profile, profile_to_text
from app.services.sources.base import norm
from app.services.sources.careers import allowed

DOSSIER_TTL = timedelta(days=30)
ABOUT_PATHS = ["", "/about", "/company", "/o-kompanii", "/about-us", "/mission", "/values", "/culture"]
ABOUT_WORDS = ("о компании", "о нас", "кто мы", "миссия", "ценност", "культур", "about", "mission", "values",
               "culture", "who we are", "our story")
REVIEWS_NOTE = "Отзывы сотрудников: источник не подключён, ждёт юридической проверки условий площадок."

SYSTEM_DOSSIER = """Ты аналитик. По текстам с сайта компании и её вакансии составь краткое досье для кандидата:
сфера бизнеса, чем компания занимается, миссия и ценности (только если явно заявлены), продукты,
сигналы о культуре, tone of voice (как компания пишет о себе). Только факты из текста, без домыслов.
Если данных мало, так и напиши в summary. Пиши по-русски."""

SYSTEM_PACKAGE = """Ты сильный карьерный консультант и редактор. Готовишь пакет под конкретную вакансию:
1) CV: переставь акценты под требования вакансии, говори языком вакансии, самые релевантные достижения
   выше. Используй ТОЛЬКО факты из профиля. Нельзя добавлять навыки, опыт, цифры, которых нет в профиле.
   Можно переформулировать и опускать нерелевантное.
2) Сопроводительное письмо: 150-250 слов, под миссию, ценности и продукт компании из досье, в стиле
   кандидата (writing style) и с учётом tone of voice компании. Без штампов и лести. Конкретика: почему
   эта компания, чем кандидат полезен именно здесь, 1-2 доказательства из опыта.
3) Сообщение рекрутеру: до 500 знаков, для мессенджера.
Маркер [CANDIDATE] означает имя кандидата: используй его для подписи, не придумывай имя.
Пиши по-русски, если вакансия на русском, иначе на языке вакансии."""

SYSTEM_FACTCHECK = """Ты проверяющий. Сравни каждое утверждение о кандидате в CV, письме и сообщении с профилем.
Отметь только утверждения, которые НЕ подтверждаются профилем: выдуманные навыки, компании, роли, сроки,
цифры, сертификаты, преувеличения уровня. Перефразирование подтверждённого факта это не ошибка.
Если всё подтверждается, верни пустой список."""


def about_links(html: str, base_url: str) -> list[str]:
    """Ссылки на разделы о компании с главной: у многих сайтов они не лежат по стандартным путям."""
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(base_url).netloc
    out = []
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
        url = urljoin(base_url, a["href"]).split("#")[0]
        if urlparse(url).netloc == host and any(w in label for w in ABOUT_WORDS):
            out.append(url)
    return list(dict.fromkeys(out))[:4]


async def _site_text(domain: str) -> tuple[str, list[str]]:
    """Тексты о компании с её сайта. Контакты и имена людей вычищаются до отправки в LLM."""
    s = get_settings()
    texts, used, queue, seen = [], [], [urljoin(f"https://{domain}", p) for p in ABOUT_PATHS], set()
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": s.http_user_agent}) as c:
        while queue and sum(map(len, texts)) < 15000 and len(seen) < 12:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                if not await allowed(c, url):
                    continue
                r = await c.get(url)
            except httpx.HTTPError:
                continue
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
            if not used:  # главная: сначала идём по её ссылкам «о компании»
                queue = about_links(r.text, str(r.url)) + queue
            soup = BeautifulSoup(r.text, "html.parser")
            for t in soup(["script", "style", "noscript", "nav", "footer", "form"]):
                t.decompose()
            txt = soup.get_text("\n", strip=True)[:6000]
            if txt and txt not in texts:
                texts.append(txt)
                used.append(str(r.url))
    text = scrub_contacts("\n\n---\n\n".join(texts))
    return depersonalize(text, None, Vault()), used


async def get_dossier(session: AsyncSession, vacancy: Vacancy, domain: str | None = None) -> dict:
    """domain: сайт компании, если он известен не из каталога (например, пользователь дал ссылку)."""
    company = await session.get(Company, vacancy.company_id) if vacancy.company_id else None
    domain = domain or (company.domain if company else None)
    key = domain or f"name:{norm(vacancy.company_name)}"
    d = await session.get(Dossier, key)
    if d and d.updated_at.replace(tzinfo=d.updated_at.tzinfo or timezone.utc) > datetime.now(timezone.utc) - DOSSIER_TTL:
        return d.data

    site, used = await _site_text(domain) if domain else ("", [])
    res = await llm.structured(
        schema=DossierData, system=SYSTEM_DOSSIER, effort="low", max_tokens=4000,
        user=f"Компания: {vacancy.company_name}\n\nТексты с сайта:\n{site or '(сайт недоступен или неизвестен)'}"
             f"\n\nВакансия:\n{vacancy.title}\n{vacancy.description[:5000]}",
    )
    data = res.model_dump()
    data["sources"] = used + [vacancy.url]
    data["reviews_note"] = REVIEWS_NOTE
    if d is None:
        session.add(Dossier(company_key=key, data=data))
    else:
        d.data, d.updated_at = data, datetime.now(timezone.utc)
    await session.flush()
    return data


async def build_package(session: AsyncSession, user_id: int, vacancy_id: int,
                        instruction: str | None = None) -> Package:
    prof = await latest_profile(session, user_id)
    if prof is None:
        raise ValueError("Сначала соберите профиль")
    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise ValueError("Вакансия не найдена")
    dossier = await get_dossier(session, vacancy)
    prev = (await session.execute(select(Package).where(Package.user_id == user_id, Package.vacancy_id == vacancy_id)
                                  .order_by(Package.version.desc()).limit(1))).scalar_one_or_none()

    profile_text = profile_to_text(prof.data)
    style = prof.data.get("writing_style") or "не определён"
    user_msg = (f"ПРОФИЛЬ КАНДИДАТА:\n{profile_text}\nСтиль письма кандидата: {style}\n\n"
                f"ВАКАНСИЯ: {vacancy.title} — {vacancy.company_name}\n{vacancy.description[:8000]}\n\n"
                f"ДОСЬЕ КОМПАНИИ:\n{json.dumps(dossier, ensure_ascii=False)}")
    if prev and instruction:
        user_msg += (f"\n\nПРЕДЫДУЩАЯ ВЕРСИЯ ПАКЕТА:\n{json.dumps({'cv': prev.cv, 'cover_letter': prev.cover_letter, 'recruiter_message': prev.recruiter_message}, ensure_ascii=False)}"
                     f"\n\nПРАВКА ОТ КАНДИДАТА (примени к предыдущей версии): {instruction}")

    draft = await llm.structured(schema=PackageDraft, system=SYSTEM_PACKAGE, user=user_msg, effort="high")
    check = await llm.structured(
        schema=FactCheck, system=SYSTEM_FACTCHECK, effort="medium", max_tokens=4000,
        user=f"ПРОФИЛЬ:\n{profile_text}\n\nТЕКСТЫ ДЛЯ ПРОВЕРКИ:\n{draft.model_dump_json()}",
    )
    pkg = Package(user_id=user_id, vacancy_id=vacancy_id, version=(prev.version + 1) if prev else 1,
                  cv=draft.cv.model_dump(), cover_letter=draft.cover_letter,
                  recruiter_message=draft.recruiter_message[:500],
                  fact_flags=[f.model_dump() for f in check.flags], instruction=instruction)
    session.add(pkg)
    await session.flush()
    return pkg
