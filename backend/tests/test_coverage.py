"""Покрытие требований вакансии и проверка резюме. ТЕСТОВЫЕ ДАННЫЕ: люди и компании вымышленные."""

import datetime
import json

import pytest

from app.services import coverage, review

PARSED = {"title": "Head of Product", "company_name": "Тест", "company_site": "", "city": "", "remote": None,
          "salary_from": None, "salary_to": None, "currency": "", "seniority": "", "responsibilities": [],
          "requirements": ["Управление продуктом от 5 лет", "SQL", "Опыт в кредитовании"], "nice_to_have": [],
          "keywords": ["SQL", "A/B-тесты", "unit-экономика", "Kafka"], "language": "ru"}


def test_keyword_coverage_by_stems():
    text = "Запускала A/B-тесты, считала unit-экономику, писала запросы на SQL."
    cov, miss = coverage.keyword_coverage(PARSED["keywords"], text)
    assert cov == ["SQL", "A/B-тесты", "unit-экономика"] and miss == ["Kafka"]


def test_score_weights():
    reqs = [{"status": "covered"}, {"status": "partial"}, {"status": "missing"}]
    assert coverage.score(reqs, 3, 4) == round(100 * (0.8 * 0.5 + 0.2 * 0.75))
    assert coverage.score([], 0, 0) == 0


def test_improve_instruction_only_profile_facts():
    cov = {"requirements": [
        {"requirement": "SQL", "status": "missing", "in_profile": True},
        {"requirement": "Опыт в кредитовании", "status": "missing", "in_profile": False},
        {"requirement": "Управление продуктом от 5 лет", "status": "covered", "in_profile": True}],
        "keywords_missing": ["Kafka"]}
    text = coverage.improve_instruction(cov)
    assert "SQL" in text and "кредитовании" not in text and "не добавляй" in text and "Kafka" in text


async def test_assess_with_mocked_llm(monkeypatch):
    import httpx

    from app.config import get_settings
    from app.services import llm

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        assert body["model"] == get_settings().llm_model_scoring
        out = {"items": [
            {"requirement": "Управление продуктом от 5 лет", "status": "covered", "evidence": "с 2018", "in_profile": True, "question": ""},
            {"requirement": "SQL", "status": "partial", "evidence": "SQL", "in_profile": True, "question": ""},
            {"requirement": "Другая формулировка", "status": "missing", "evidence": "", "in_profile": False,
             "question": "Был ли опыт в кредитовании?"}]}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                      "message": {"content": json.dumps(out, ensure_ascii=False)}}]})

    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(llm, "_client", httpx.AsyncClient(base_url="https://or.test", transport=httpx.MockTransport(handler)))
    try:
        r = await coverage.assess("Руководитель продукта с 2018, SQL, A/B-тесты", "профиль", PARSED)
    finally:
        monkeypatch.setattr(llm, "_client", None)
    assert (r["req_covered"], r["req_partial"], r["req_total"]) == (1, 1, 3)
    assert r["requirements"][2]["requirement"] == "Опыт в кредитовании"  # формулировка вакансии, не модели
    assert r["requirements"][2]["question"].startswith("Был ли")
    assert r["keywords_missing"] == ["unit-экономика", "Kafka"] and 0 < r["score"] < 100


async def test_assess_without_ai_counts_keywords_only():
    r = await coverage.assess("SQL и A/B-тесты", "", PARSED)
    assert r["req_total"] == 0 and len(r["keywords_covered"]) == 2


def test_cv_text_flattens_package():
    cv = {"headline": "Продакт", "summary": "s", "highlights": [{"value": "14", "label": "человек"}],
          "experience": [{"role": "Роль", "company": "Компания", "period": "2020", "bullets": ["Внедрила SQL-отчёты"]}],
          "skills": ["Kafka"], "education": [], "languages": []}
    t = coverage.cv_text(cv)
    assert "14 человек" in t and "SQL" in t and "Kafka" in t


async def test_revisions_auto_does_not_spend_manual(session):
    from app.models import Package, User, Vacancy
    from app.services import tailor

    u = User(tg_id=880001, consent_at=datetime.datetime.now())
    session.add(u)
    await session.flush()
    v = Vacancy(fingerprint="fp-rev-test", company_name="Т", title="Р", url="user:2")
    session.add(v)
    await session.flush()
    base = dict(user_id=u.id, vacancy_id=v.id, target_id=None, items=["cv"], cv={}, cover_letter="",
                recruiter_message="", fact_flags=[])
    session.add(Package(version=1, instruction=None, **base))
    session.add(Package(version=2, instruction=tailor.AUTO + "закрой пробелы", **base))
    await session.flush()
    manual, left = await tailor.revisions(session, u.id, None)
    assert manual is False and left == tailor.MAX_IMPROVE - 1
    session.add(Package(version=3, instruction="короче", **base))
    await session.flush()
    manual, left = await tailor.revisions(session, u.id, None)
    assert manual is True and left == tailor.MAX_IMPROVE - 1


def test_review_checks_without_ai():
    good = """Анна Тестова, anna.test@example.com
Опыт работы
2020 — н.в. Тестовая компания, руководитель продукта
- Запустила приложение: 180 тыс. клиентов за год
- Сократила время ответа с 2 дней до 15 минут
- Собрала команду из 9 человек
2017 — 2020 Другая компания, аналитик
- Построила отчётность для 40 менеджеров
Образование: Тестовый университет, 2016
Навыки: SQL, A/B-тесты""" + " Дополнительный текст о проектах и результатах." * 15
    items = {c["key"]: c["ok"] for c in review.checks(good)}
    assert items["numbers"] and items["dates"] and items["contacts"] and items["section_опыт"]
    bad = {c["key"]: c["ok"] for c in review.checks("Ответственный, коммуникабельный, стрессоустойчивый.")}
    assert not bad["numbers"] and not bad["dates"] and not bad["contacts"]
    assert review.check_score(review.checks(good)) > review.check_score(review.checks("коротко"))


@pytest.mark.parametrize("text", ["", "   "])
async def test_review_empty_text(text):
    r = await review.review(text)
    assert r["advice"] == [] and r["score"] < 50
