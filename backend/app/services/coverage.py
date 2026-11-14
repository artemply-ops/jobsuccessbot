"""Покрытие требований вакансии: сколько требований и ключевых слов попало в резюме и почему не попало остальное.

Две точки замера:
- before: профиль кандидата (его исходное резюме и рассказ) против вакансии, до генерации;
- after: готовое резюме против вакансии, после генерации.
Требования проверяет дешёвая модель (смысловое совпадение: «опыт от 5 лет» не найти поиском слов), ключевые слова
считаются в коде. По каждому непокрытому требованию: есть ли подтверждение в профиле (тогда его можно добавить
перегенерацией) или нет (тогда вопрос кандидату). Выдумывать недостающее нельзя, это правило всего сервиса.
"""

import json
import re

from pydantic import BaseModel, Field

from app.config import get_settings
from app.schemas import VacancyParsed
from app.services import llm


class ReqStatus(BaseModel):
    requirement: str
    status: str = Field(description="covered | partial | missing")
    evidence: str = Field(description="где в тексте это подтверждено, до 12 слов; пусто, если missing")
    in_profile: bool = Field(description="есть ли подтверждение в ПРОФИЛЕ кандидата (для missing/partial)")
    question: str = Field(description="если в профиле нет: короткий вопрос кандидату про этот опыт; иначе пусто")


class ReqCoverage(BaseModel):
    items: list[ReqStatus]


SYSTEM_COVERAGE = """Ты рекрутер. Для каждого требования вакансии определи, подтверждает ли его ПРОВЕРЯЕМЫЙ ТЕКСТ.
covered: требование явно подтверждено фактом в тексте; partial: подтверждено частично (меньше лет, смежный опыт);
missing: в тексте нет. Отдельно для partial и missing проверь ПРОФИЛЬ кандидата: in_profile = true, если там есть
факт, которым требование можно закрыть. Если в профиле нет, сформулируй короткий вопрос кандидату на «вы».
Требования верни в том же порядке и той же формулировкой. Только факты из текстов, без домыслов.
Маркеры вида [CANDIDATE] не раскрывай. Пиши по-русски."""


def _stem(w: str) -> str:
    w = w.lower().replace("ё", "е").strip("./-")  # «SQL.» в конце предложения это «SQL»
    return w[:-2] if len(w) > 6 else w[:-1] if len(w) > 4 else w


def _tokens(text: str) -> set[str]:
    return {_stem(t) for t in re.findall(r"[a-zа-яё0-9+#./-]{2,}", (text or "").lower())}


def keyword_coverage(keywords: list[str], text: str) -> tuple[list[str], list[str]]:
    """Ключевое слово покрыто, если все его значимые слова (по основе) встречаются в тексте."""
    have = _tokens(text)
    covered, missing = [], []
    for kw in dict.fromkeys(k.strip() for k in keywords if k and k.strip()):
        parts = [_stem(p) for p in re.findall(r"[a-zа-яё0-9+#./-]{2,}", kw.lower())]
        (covered if parts and all(p in have for p in parts) else missing).append(kw)
    return covered, missing


def cv_text(cv: dict) -> str:
    """Готовое резюме одним текстом (обезличенное, как хранится в пакете)."""
    parts = [cv.get("headline", ""), cv.get("summary", "")]
    parts += [f"{h.get('value', '')} {h.get('label', '')}" for h in cv.get("highlights", [])]
    for e in cv.get("experience", []):
        parts.append(f"{e.get('role', '')} {e.get('company', '')} {e.get('period', '')}")
        parts += e.get("bullets", [])
    parts += cv.get("skills", []) + cv.get("education", []) + cv.get("languages", [])
    return "\n".join(p for p in parts if p)


def score(reqs: list[dict], kw_cov: int, kw_total: int) -> int:
    """0-100: требования весят 80% (partial = половина), ключевые слова 20%."""
    if not reqs and not kw_total:
        return 0
    r = (sum(1 if x["status"] == "covered" else 0.5 if x["status"] == "partial" else 0 for x in reqs) / len(reqs)
         if reqs else 1.0)
    k = kw_cov / kw_total if kw_total else 1.0
    return round(100 * (0.8 * r + 0.2 * k))


async def assess(text: str, profile_text: str, parsed: dict) -> dict:
    """Отчёт о покрытии. Без ИИ: только ключевые слова."""
    p = VacancyParsed.model_validate(parsed)
    reqs_in = [r for r in p.requirements if r.strip()][:12]
    kw_cov, kw_miss = keyword_coverage(p.keywords, text)
    reqs: list[dict] = []
    if reqs_in and llm.available():
        res = await llm.structured(
            schema=ReqCoverage, system=SYSTEM_COVERAGE, model=get_settings().llm_model_scoring, effort="low",
            max_tokens=3000,
            user=f"ТРЕБОВАНИЯ ВАКАНСИИ:\n{json.dumps(reqs_in, ensure_ascii=False)}\n\nПРОВЕРЯЕМЫЙ ТЕКСТ:\n{text}"
                 f"\n\nПРОФИЛЬ КАНДИДАТА:\n{profile_text}")
        by_req = {r.requirement.strip(): r for r in res.items}
        for i, req in enumerate(reqs_in):  # порядок и формулировки как в вакансии, даже если модель их поменяла
            r = by_req.get(req.strip()) or (res.items[i] if i < len(res.items) else None)
            st = r.status if r and r.status in ("covered", "partial", "missing") else "missing"
            reqs.append({"requirement": req, "status": st, "evidence": r.evidence if r else "",
                         "in_profile": bool(r and r.in_profile), "question": (r.question if r else "") or ""})
    covered = sum(1 for r in reqs if r["status"] == "covered")
    return {"requirements": reqs, "req_covered": covered,
            "req_partial": sum(1 for r in reqs if r["status"] == "partial"), "req_total": len(reqs),
            "keywords_covered": kw_cov, "keywords_missing": kw_miss, "kw_total": len(kw_cov) + len(kw_miss),
            "score": score(reqs, len(kw_cov), len(kw_cov) + len(kw_miss))}


def improve_instruction(cov: dict) -> str:
    """Указание для перегенерации: закрыть пробелы ТОЛЬКО фактами профиля."""
    fixable = [r["requirement"] for r in cov.get("requirements", []) if r["status"] != "covered" and r["in_profile"]]
    kws = cov.get("keywords_missing", [])[:10]
    lines = ["Улучши покрытие требований вакансии, не нарушая правил: только факты профиля."]
    if fixable:
        lines.append("В профиле есть подтверждение, но в резюме это слабо или не отражено: " + "; ".join(fixable))
    if kws:
        lines.append("Ключевые слова вакансии, которые стоит использовать, если они подтверждаются профилем: "
                     + ", ".join(kws))
    lines.append("Требование, которого нет в профиле, не добавляй.")
    return "\n".join(lines)
