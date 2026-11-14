"""Проверка резюме без вакансии: оценка 0-100 и конкретные правки.

Проверки в коде (по сырому тексту, у нас): цифры в достижениях, объём, даты, контакты, разделы.
Советы модели (по обезличенному тексту): 3-5 конкретных правок с примером. Контакты в модель не уходят.
"""

import re

from pydantic import BaseModel, Field

from app.config import get_settings
from app.services import llm
from app.services.pii import EMAIL_RE, PHONE_RE, Vault, depersonalize, scrub_contacts


class Advice(BaseModel):
    problem: str = Field(description="что не так, конкретно, со ссылкой на место в резюме")
    fix: str = Field(description="как исправить, с примером формулировки на основе фактов резюме")


class Review(BaseModel):
    advice: list[Advice] = Field(description="3-5 самых полезных правок, от важной к менее важной")


SYSTEM_REVIEW = """Ты сильный рекрутер. Проверь резюме и дай 3-5 самых полезных правок.
Смотри на: результаты с цифрами вместо обязанностей, глаголы действия, понятность масштаба (команда, бюджет,
клиенты), лишнее и устаревшее, длину, единый формат дат, ясный заголовок и краткое описание о себе.
Пример формулировки строй только на фактах резюме; если цифры нет, напиши «добавьте цифру: сколько…»,
не выдумывай её. Маркеры вида [CANDIDATE], [EMAIL] не раскрывай. Пиши по-русски, на «вы»."""

SECTIONS = {"опыт": r"опыт|experience|места работы", "образование": r"образован|education|университет|вуз",
            "навыки": r"навык|skills|компетенц|инструмент"}


def checks(text: str) -> list[dict]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    bullets = [ln for ln in lines if re.match(r"^[-•*–·▪]\s*|^\d+[.)]\s+", ln) or len(ln.split()) >= 6]
    with_num = [b for b in bullets if re.search(r"\d", re.sub(r"(19|20)\d{2}", "", b))]
    words = len(re.findall(r"\w+", text or ""))
    dates = re.findall(r"(19|20)\d{2}", text or "")
    low = (text or "").lower()
    out = [
        {"key": "numbers", "title": "Результаты с цифрами",
         "ok": bool(bullets) and len(with_num) / max(1, len(bullets)) >= 0.4,
         "note": f"цифры есть в {len(with_num)} из {len(bullets)} пунктов" if bullets else "пунктов опыта не нашёл"},
        {"key": "length", "title": "Объём", "ok": 150 <= words <= 900,
         "note": f"{words} слов" + (": мало, добавьте результаты" if words < 150 else ": много, сократите до сути" if words > 900 else "")},
        {"key": "dates", "title": "Периоды работы", "ok": len(dates) >= 2,
         "note": "периоды указаны" if len(dates) >= 2 else "не вижу годов работы"},
        {"key": "contacts", "title": "Контакты", "ok": bool(EMAIL_RE.search(text or "") or PHONE_RE.search(text or "")),
         "note": "почта или телефон есть" if (EMAIL_RE.search(text or "") or PHONE_RE.search(text or "")) else "нет почты и телефона"},
    ]
    for name, pat in SECTIONS.items():
        ok = bool(re.search(pat, low))
        out.append({"key": f"section_{name}", "title": f"Раздел «{name}»", "ok": ok, "note": "есть" if ok else "не нашёл"})
    return out


def check_score(items: list[dict]) -> int:
    weights = {"numbers": 30, "length": 15, "dates": 15, "contacts": 10}
    total = sum(weights.get(i["key"], 10) for i in items)
    got = sum(weights.get(i["key"], 10) for i in items if i["ok"])
    return round(100 * got / total) if total else 0


async def review(text: str, full_name: str | None = None) -> dict:
    items = checks(text)
    advice: list[dict] = []
    if llm.available() and text.strip():
        clean = depersonalize(scrub_contacts(text[:12000]), full_name, Vault())
        res = await llm.structured(schema=Review, system=SYSTEM_REVIEW, model=get_settings().llm_model_scoring,
                                   effort="medium", max_tokens=3000, user=f"РЕЗЮМЕ:\n{clean}")
        advice = [a.model_dump() for a in res.advice[:5]]
    return {"score": check_score(items), "checks": items, "advice": advice}
