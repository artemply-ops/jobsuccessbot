"""Блок 1. Профиль кандидата: источники -> обезличенный текст -> структурированный профиль."""

import json
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Contact, Profile, Source, User
from app.schemas import ProfileData, ProfileExtraction
from app.services import llm
from app.services.extract import ExtractError, extract_text
from app.services.pii import EMAIL_RE, PHONE_RE, Vault, _person_spans, depersonalize

SYSTEM_PROFILE = """Ты карьерный аналитик. Из материалов кандидата собери структурированный профиль.
Правила:
- Только факты из материалов. Ничего не додумывай: нет данных, оставь поле пустым.
- У каждого навыка и достижения укажи source_ids: номера источников, где это сказано.
- Достижения формулируй с цифрами, если цифры есть в источнике.
- Маркеры вида [CANDIDATE], [PERSON_1], [LINK_1] оставляй как есть, это обезличенные данные.
- writing_style: опиши, как человек пишет о себе (тон, длина фраз, лексика), по его собственным текстам.
- Вопросы: 5-7 штук, по самым важным пробелам для поиска работы (зарплата, город и формат,
  стоп-факторы, цифры в достижениях, целевая роль). Не спрашивай то, что уже есть.
Пиши по-русски."""

SYSTEM_REFINE = """Ты карьерный аналитик. Есть текущий профиль кандидата (JSON) и новые материалы.
Обнови профиль: добавь новые факты, уточни существующие, сохрани source_ids.
Если новые данные противоречат старым, приоритет у более свежих ответов кандидата.
Ничего не додумывай. Маркеры вида [CANDIDATE], [PERSON_1] оставляй как есть.
Верни обновлённый профиль и оставшиеся вопросы по пробелам (0-5 штук). Пиши по-русски."""


async def get_contact(session: AsyncSession, user: User) -> Contact:
    c = await session.get(Contact, user.id)
    if c is None:
        c = Contact(user_id=user.id, links=[], vault={})
        session.add(c)
        await session.flush()
    return c


async def latest_profile(session: AsyncSession, user_id: int) -> Profile | None:
    q = select(Profile).where(Profile.user_id == user_id).order_by(Profile.version.desc()).limit(1)
    return (await session.execute(q)).scalar_one_or_none()


async def add_file_source(session: AsyncSession, user: User, filename: str, data: bytes) -> Source:
    s = get_settings()
    path = s.files_dir / f"{user.id}_{uuid.uuid4().hex}_{re.sub(r'[^\w.-]', '_', filename)}"
    path.write_bytes(data)
    src = Source(user_id=user.id, kind="file", filename=filename, path=str(path))
    try:
        src.text = extract_text(filename, data)
        src.status = "parsed"
    except ExtractError as e:
        src.status, src.error = "error", str(e)
    except Exception as e:  # битый файл
        src.status, src.error = "error", f"Не удалось прочитать файл: {type(e).__name__}"
    session.add(src)
    await session.flush()
    await _harvest_contacts(session, user, src)
    return src


async def add_text_source(session: AsyncSession, user: User, text: str, kind: str = "text") -> Source:
    src = Source(user_id=user.id, kind=kind, text=text.strip()[:20_000], status="parsed")
    session.add(src)
    await session.flush()
    await _harvest_contacts(session, user, src)
    return src


async def _harvest_contacts(session: AsyncSession, user: User, src: Source) -> None:
    """Контакты из резюме сохраняем к себе, чтобы подставить в итоговое CV."""
    if not src.text:
        return
    c = await get_contact(session, user)
    if not c.email and (m := EMAIL_RE.search(src.text)):
        c.email = m.group(0)
    if not c.phone and (m := PHONE_RE.search(src.text)):
        c.phone = m.group(0)
    if not c.full_name and src.kind == "file":
        spans = _person_spans(src.text[:300])
        if spans:
            a, b = spans[0]
            c.full_name = src.text[a:b]


async def _sources_block(session: AsyncSession, user: User, sources: list[Source]) -> tuple[str, Vault]:
    c = await get_contact(session, user)
    vault = Vault(dict(c.vault or {}))
    parts = []
    for src in sources:
        text = depersonalize(src.text or "", c.full_name, vault)
        label = {"file": f"файл «{src.filename}»", "voice": "голосовое", "text": "сообщение"}.get(src.kind, src.kind)
        parts.append(f"<source id=\"{src.id}\" type=\"{label}\">\n{text}\n</source>")
    c.vault = vault.items
    return "\n\n".join(parts), vault


async def build_profile(session: AsyncSession, user: User, new_sources: list[Source] | None = None) -> Profile:
    """Собирает профиль с нуля или дополняет существующий новыми источниками."""
    current = await latest_profile(session, user.id)
    if new_sources is None or current is None:
        q = select(Source).where(Source.user_id == user.id, Source.status == "parsed").order_by(Source.id)
        new_sources = list((await session.execute(q)).scalars())
    if not new_sources:
        raise ValueError("Нет материалов для профиля. Пришлите резюме, голосовое или текст о себе.")

    block, _ = await _sources_block(session, user, new_sources)
    if current is None:
        res = await llm.structured(schema=ProfileExtraction, system=SYSTEM_PROFILE,
                                   user=f"Материалы кандидата:\n\n{block}", effort="high")
    else:
        res = await llm.structured(
            schema=ProfileExtraction, system=SYSTEM_REFINE, effort="medium",
            user=f"Текущий профиль:\n{json.dumps(current.data, ensure_ascii=False)}\n\nНовые материалы:\n\n{block}",
        )
    return await _save_version(session, user.id, res.profile, [q.model_dump() for q in res.questions])


async def save_edited(session: AsyncSession, user: User, data: ProfileData, confirm: bool) -> Profile:
    """Правка профиля пользователем. Контакты в профиль не пишем: вычищаем на всякий случай."""
    c = await get_contact(session, user)
    vault = Vault(dict(c.vault or {}))
    raw = json.dumps(data.model_dump(), ensure_ascii=False)
    clean = ProfileData.model_validate(json.loads(depersonalize(raw, c.full_name, vault, use_ner=False)))
    c.vault = vault.items
    current = await latest_profile(session, user.id)
    p = await _save_version(session, user.id, clean, current.questions if current else [])
    p.confirmed = confirm
    return p


async def _save_version(session: AsyncSession, user_id: int, data: ProfileData, questions: list) -> Profile:
    current = await latest_profile(session, user_id)
    p = Profile(user_id=user_id, version=(current.version + 1) if current else 1,
                data=data.model_dump(), questions=questions, confirmed=False)
    session.add(p)
    await session.flush()
    return p


def profile_to_text(data: dict) -> str:
    """Компактное текстовое представление профиля для промптов."""
    p = ProfileData.model_validate(data)
    lines = [f"Заголовок: {p.headline}", f"Целевые роли: {', '.join(p.target_roles)}", f"Уровень: {p.seniority}",
             f"Кратко: {p.summary}",
             "Навыки: " + ", ".join(f"{s.name} ({s.level}{', ' + str(s.years) + ' г.' if s.years else ''})" for s in p.skills),
             f"Отрасли: {', '.join(p.industries)}"]
    for e in p.experience:
        lines.append(f"- {e.role}, {e.company} ({e.period}){' / ' + e.industry if e.industry else ''}")
        lines += [f"    • {a.text}" for a in e.achievements]
    ex = p.expectations
    lines += [f"Образование: {'; '.join(p.education)}", f"Языки: {', '.join(p.languages)}",
              f"Ожидания: от {ex.salary_min or '?'} {ex.currency}, города: {', '.join(ex.cities) or '?'}, "
              f"формат: {', '.join(ex.formats) or '?'}, релокация: {ex.relocation}",
              f"Ценности: {', '.join(p.values)}", f"Стоп-факторы: {', '.join(p.stop_factors)}"]
    return "\n".join(lines)
