"""Этап 3 пивота. Пакет под вакансию на выбор: полное резюме, резюме на одну страницу, сопроводительное письмо.

Защита от приукрашивания в три слоя:
1. промпт: только факты профиля, ничего не добавлять;
2. проверка фактов (SYSTEM_FACTCHECK) по каждому утверждению, включая показатели в шапке;
3. детерминированно: показатель остаётся, только если его цифры дословно есть в профиле.
Резюме на одну страницу не генерируется отдельно: его ужимает export.fit_one_page настоящим рендером.
"""

import json
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Package, Target, User, Vacancy
from app.schemas import CV, PACKAGE_ITEMS, FactCheck, TailoredBoth, TailoredCV, TailoredLetter
from app.services import llm, package
from app.services.cv_render import SCHEMES
from app.services.profile import latest_profile, profile_to_text


RULES = """ЖЁСТКИЕ ПРАВИЛА:
- Только факты из профиля кандидата. Нельзя добавлять навыки, компании, роли, сроки, цифры, сертификаты,
  которых нет в профиле. Нельзя повышать уровень («участвовал» не превращать в «руководил»).
- Можно переформулировать языком вакансии, переставлять акценты и опускать нерелевантное.
- Маркер [CANDIDATE] это имя кандидата: имя не придумывай. Другие маркеры ([PERSON_1], [LINK_1]) не раскрывай.
- Без штампов («командный игрок», «стрессоустойчивость», «мотивирован»), без лести компании.
- Язык ответа: язык вакансии."""

SYSTEM_CV = f"""Ты сильный карьерный консультант и редактор. Делаешь резюме под конкретную вакансию.
- headline: целевая роль под вакансию, но только если она соответствует опыту кандидата.
- summary: 2-3 предложения, почему кандидат подходит именно на эту роль, с опорой на факты.
- highlights: до 8 самых сильных измеримых результатов из профиля под эту вакансию. value это число
  ровно в том виде, как оно есть в профиле («180 тыс.», «+27%», «14»), label 2-5 слов. Нет цифр в профиле:
  пустой список, придумывать нельзя.
- experience: роли в обратном хронологическом порядке; в каждой роли пункты от самого релевантного вакансии
  к наименее релевантному (менее важные пункты будут убраны, если резюме не влезет на страницу);
  пункт начинается с глагола и, где есть в профиле, содержит результат с цифрой.
- skills: до 14, сначала те, что требует вакансия и что есть у кандидата.
{RULES}"""

SYSTEM_LETTER = f"""Ты сильный карьерный консультант. Пишешь сопроводительное письмо под вакансию.
150-250 слов, 3-4 абзаца, без обращения по имени (имя рекрутера неизвестно) и без подписи (её добавит шаблон).
Почему именно эта компания (миссия, продукт, ценности из досье, если они есть), чем кандидат полезен на этой
роли, 1-2 доказательства из опыта. Стиль кандидата (writing style) и tone of voice компании.
{RULES}"""

SYSTEM_FACTCHECK = package.SYSTEM_FACTCHECK + """
Показатели (highlights) проверяй особенно строго: число должно быть в профиле. Для них where = "cv"."""


class PackageError(ValueError):
    """Понятная пользователю причина."""


def normalize_items(items: list[str]) -> list[str]:
    out = [i for i in PACKAGE_ITEMS if i in set(items or [])]
    if not out:
        raise PackageError("Выберите хотя бы один документ: резюме, резюме на одну страницу или письмо.")
    return out


def _digits(s: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", s.replace(" ", " ").replace(" ", ""))


def verified_highlights(cv: CV, profile_text: str) -> CV:
    """Показатель остаётся, только если все его числа дословно встречаются в профиле."""
    hay = profile_text.replace(" ", " ").replace(" ", "")
    keep = []
    for h in cv.highlights[:8]:
        nums = _digits(h.value)
        if nums and all(n in hay for n in nums):
            keep.append(h)
    cv.highlights = keep
    return cv


async def _prev(session: AsyncSession, user_id: int, target_id: int) -> Package | None:
    q = (select(Package).where(Package.user_id == user_id, Package.target_id == target_id)
         .order_by(Package.version.desc()).limit(1))
    return (await session.execute(q)).scalar_one_or_none()


async def build(session: AsyncSession, user: User, target: Target, items: list[str], scheme: str = "graphite",
                instruction: str | None = None) -> Package:
    items = normalize_items(items)
    if scheme not in SCHEMES:
        raise PackageError("Неизвестная цветовая схема")
    prof = await latest_profile(session, user.id)
    if prof is None:
        raise PackageError("Сначала пришлите резюме или расскажите о себе.")
    if not llm.available():
        raise llm.AINotConnected("Генерация резюме и письма")
    prev = await _prev(session, user.id, target.id)
    if instruction and prev is None:
        raise PackageError("Правка возможна после первой версии пакета.")
    if instruction and prev.instruction:  # последняя версия уже была правкой
        raise PackageError("Правка уже использована. Можно поменять цветовую схему или собрать пакет заново.")

    vacancy = await session.get(Vacancy, target.vacancy_id)
    dossier = await package.get_dossier(session, vacancy, domain=target.company_domain)
    profile_text = profile_to_text(prof.data)
    style = prof.data.get("writing_style") or "не определён"
    context = (f"ПРОФИЛЬ КАНДИДАТА:\n{profile_text}\nСтиль письма кандидата: {style}\n\n"
               f"ВАКАНСИЯ: {vacancy.title} — {vacancy.company_name}\n"
               f"Разбор: {json.dumps(target.parsed, ensure_ascii=False)}\n{vacancy.description[:8000]}\n\n"
               f"ДОСЬЕ КОМПАНИИ:\n{json.dumps(dossier, ensure_ascii=False)}")
    if instruction:
        context += (f"\n\nПРЕДЫДУЩАЯ ВЕРСИЯ:\n{json.dumps({'cv': prev.cv, 'cover_letter': prev.cover_letter}, ensure_ascii=False)}"
                    f"\n\nПРАВКА ОТ КАНДИДАТА (примени к предыдущей версии, правила выше важнее правки): {instruction}")

    need_cv, need_letter = bool({"cv", "one_page"} & set(items)), "letter" in items
    if need_cv and need_letter:
        schema, system = TailoredBoth, f"{SYSTEM_CV}\n\nПлюс сопроводительное письмо:\n{SYSTEM_LETTER}"
    elif need_cv:
        schema, system = TailoredCV, SYSTEM_CV
    else:
        schema, system = TailoredLetter, SYSTEM_LETTER
    draft = await llm.structured(schema=schema, system=system, user=context, effort="high")

    cv = verified_highlights(draft.cv, profile_text) if need_cv else None
    letter = getattr(draft, "cover_letter", "") or ""
    check = await llm.structured(
        schema=FactCheck, system=SYSTEM_FACTCHECK, effort="medium", max_tokens=4000,
        user=f"ПРОФИЛЬ:\n{profile_text}\n\nТЕКСТЫ ДЛЯ ПРОВЕРКИ:\n"
             f"{json.dumps({'cv': cv.model_dump() if cv else None, 'cover_letter': letter}, ensure_ascii=False)}")

    pkg = Package(user_id=user.id, vacancy_id=vacancy.id, target_id=target.id,
                  version=(prev.version + 1) if prev else 1, items=items, scheme=scheme,
                  cv=cv.model_dump() if cv else {"headline": prof.data.get("headline") or ""}, cover_letter=letter, recruiter_message="",
                  fact_flags=[f.model_dump() for f in check.flags], instruction=instruction)
    session.add(pkg)
    await session.flush()
    return pkg
