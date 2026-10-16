"""Тесты тестовыми данными: все ФИО, телефоны и почты ниже вымышленные."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.auth import AuthError, verify_login_widget, verify_webapp_init_data
from app.models import Package, Vacancy
from app.schemas import Expectations, ProfileData, Skill
from app.services import export
from app.services.collect import upsert
from app.services.pii import Vault, depersonalize, has_pii, restore
from app.services.scoring import hard_filter, prefilter_score
from app.services.sources import trudvsem
from app.services.sources.base import RawVacancy, fingerprint

TOKEN = "123456:TEST"


# ---------- Обезличивание ----------

def test_depersonalize_contacts_and_name():
    text = ("Иванов Пётр Сергеевич, тел. +7 (912) 345-67-89, почта petr.ivanov@example.com, "
            "github.com/pivanov. Иванова рекомендуют. СНИЛС 123-456-789 01.")
    v = Vault()
    out = depersonalize(text, "Иванов Пётр Сергеевич", v, use_ner=False)
    assert "Иванов" not in out and "Пётр" not in out
    assert "+7" not in out and "example.com" not in out and "123-456-789" not in out
    assert "[CANDIDATE]" in out and "[PHONE]" in out and "[EMAIL]" in out
    assert not has_pii(out)
    back = restore(out, "Иванов Пётр Сергеевич", "petr.ivanov@example.com", "+79123456789", v)
    assert "petr.ivanov@example.com" in back


def test_depersonalize_keeps_business_facts():
    text = "Руководил командой из 12 человек в компании Ромашка, Москва, рост выручки на 30%."
    out = depersonalize(text, "Петров Иван", Vault(), use_ner=False)
    assert "12 человек" in out and "Москва" in out and "30%" in out


def test_ner_replaces_third_party_names():
    v = Vault()
    out = depersonalize("Рекомендацию написала Мария Кузнецова, руководитель отдела.", None, v)
    assert "Кузнецова" not in out
    assert any(k.startswith("[PERSON_") for k in v.items)


# ---------- Telegram ----------

def _sign_webapp(fields: dict) -> str:
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


def test_webapp_init_data_ok_and_tampered():
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 42, "first_name": "Тест"})}
    init = _sign_webapp(fields)
    assert verify_webapp_init_data(init, TOKEN)["id"] == 42
    with pytest.raises(AuthError):
        verify_webapp_init_data(init.replace("42", "43"), TOKEN)


def test_login_widget():
    data = {"id": "42", "first_name": "Тест", "auth_date": str(int(time.time()))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    h = hmac.new(hashlib.sha256(TOKEN.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    assert verify_login_widget({**data, "hash": h}, TOKEN)["id"] == "42"
    with pytest.raises(AuthError):
        verify_login_widget({**data, "hash": "0" * 64}, TOKEN)


# ---------- Источники и склейка ----------

TRUDVSEM_ITEM = {"vacancy": {
    "id": "abc", "job-name": "Senior Python-разработчик", "company": {"name": "ООО Ромашка", "site": "https://www.romashka.ru/"},
    "region": {"name": "г. Москва"}, "salary_min": 250000, "salary_max": 0, "vac_url": "https://trudvsem.ru/vacancy/card/1/abc",
    "duty": "Разработка сервисов на Python, FastAPI. Звонить +7 999 111-22-33", "schedule": "Удаленная работа",
    "requirement": {"experience": 3}, "contact_person": "Смирнова Анна", "date_modify": "2026-09-22T13:00:24+0300"}}


def test_trudvsem_parse_skips_recruiter_contacts():
    r = trudvsem.parse(TRUDVSEM_ITEM)
    assert r.title == "Senior Python-разработчик" and r.salary_from == 250000 and r.salary_to is None
    assert r.remote is True and r.company_domain == "romashka.ru"
    assert "Смирнова" not in r.description


def test_fingerprint_merges_legal_forms():
    a = RawVacancy("trudvsem", "1", "Python-разработчик", "ООО «Ромашка»", "u1", city="Москва")
    b = RawVacancy("superjob", "2", "python разработчик", "Ромашка", "u2", city="москва")
    assert fingerprint(a) == fingerprint(b)


async def test_upsert_dedup_and_clean(session):
    raws = [trudvsem.parse(TRUDVSEM_ITEM),
            RawVacancy("superjob", "9", "Senior Python-разработчик", "Ромашка", "https://sj/9", city="г. Москва",
                       description="длинное описание " * 20)]
    created, updated = await upsert(session, raws)
    assert (created, updated) == (1, 1)
    v = (await session.execute(__import__("sqlalchemy").select(Vacancy))).scalars().one()
    assert len(v.sources) == 2 and "+7 999" not in v.description


# ---------- Скоринг ----------

def _profile(**kw) -> ProfileData:
    return ProfileData(target_roles=["Python-разработчик"], headline="Backend Python",
                       skills=[Skill(name="Python", level="expert"), Skill(name="FastAPI", level="confident")],
                       expectations=Expectations(salary_min=300000, cities=["Москва"], formats=["hybrid", "remote"]),
                       **kw)


def _vac(**kw) -> Vacancy:
    base = dict(title="Python-разработчик", company_name="X", description="Python, FastAPI, PostgreSQL",
                city="Москва", remote=None, salary_to=None)
    return Vacancy(**{**base, **kw})


def test_hard_filter():
    p = _profile(stop_factors=["гемблинг"])
    assert hard_filter(p, _vac()) is None
    assert hard_filter(p, _vac(salary_to=150000)) == "зарплата ниже ожиданий"
    assert hard_filter(p, _vac(description="проект в сфере гемблинг")) == "стоп-фактор: гемблинг"


def test_prefilter_ranks_relevant_higher():
    p = _profile()
    good = prefilter_score(p, _vac())
    bad = prefilter_score(p, _vac(title="Бухгалтер", description="1С, отчётность"))
    assert good > bad and bad < 0.1


# ---------- Выгрузка ----------

def _pkg(flags=None) -> Package:
    return Package(user_id=1, vacancy_id=1, version=1, instruction=None,
                   cv={"headline": "Backend-разработчик", "summary": "[CANDIDATE]: 6 лет в Python.",
                       "skills": ["Python", "FastAPI"],
                       "experience": [{"company": "Ромашка", "role": "Разработчик", "period": "2020 — н.в.",
                                       "bullets": ["Ускорил API в 3 раза", "Руководил 50 людьми"]}],
                       "education": ["МГУ"], "languages": ["Английский B2"]},
                   cover_letter="Здравствуйте!\nХочу к вам.\n[CANDIDATE]", recruiter_message="Привет",
                   fact_flags=flags or [])


def test_export_drops_flagged_cv_bullets_and_restores_name():
    from app.models import Contact

    c = Contact(user_id=1, full_name="Петров Иван", email="ivan@example.com", phone=None, city="Москва", vault={})
    pkg = _pkg([{"text": "Руководил 50 людьми", "where": "cv", "reason": "нет в профиле"}])
    view = export.cv_view(pkg, c)
    assert view["experience"][0]["bullets"] == ["Ускорил API в 3 раза"]
    assert "Петров Иван" in view["cover_letter"]
    assert export.to_docx(pkg, c)[:2] == b"PK"
    assert export.to_pdf(pkg, c)[:4] == b"%PDF"


def test_export_blocks_unaccepted_letter_flags():
    pkg = _pkg([{"text": "Я эксперт Kubernetes", "where": "cover_letter", "reason": "нет в профиле"}])
    with pytest.raises(export.FlagsNotAccepted):
        export.to_docx(pkg, None)
    assert export.to_docx(pkg, None, accept_flags=True)[:2] == b"PK"


# ---------- Режим без ИИ ----------

def test_parse_basic_profile():
    from app.services.profile import parse_basic

    p = parse_basic("Python-разработчик, Москва, 250 тыс, удалёнка")
    assert p.target_roles == ["Python-разработчик"] and p.expectations.cities == ["Москва"]
    assert p.expectations.salary_min == 250000 and p.expectations.formats == ["remote"]
    with pytest.raises(ValueError):
        parse_basic("  ")


async def test_flow_without_ai(session):
    """Без ключа ИИ: базовый профиль -> предварительная оценка -> заглушки на шагах с ИИ."""
    import httpx
    from sqlalchemy import select

    from app.main import app
    from app.models import User, VacancyScore
    from app.services import llm, scoring
    from app.services.profile import save_basic

    assert not llm.available()
    user = User(tg_id=777, consent_at=__import__("datetime").datetime.now())
    session.add(user)
    await session.flush()
    await save_basic(session, user, "Python-разработчик, Москва")
    await upsert(session, [RawVacancy("trudvsem", "a1", "Python-разработчик", "Ромашка", "https://x/1", city="Москва"),
                           RawVacancy("trudvsem", "a2", "Бухгалтер", "Лютик", "https://x/2", city="Москва")])
    res = await scoring.score_user(session, user.id)
    assert res["method"] == "basic" and res["scored"] == 1
    sc = (await session.execute(select(VacancyScore).where(VacancyScore.user_id == user.id))).scalars().one()
    assert sc.method == "basic" and sc.score > 0
    assert (await scoring.score_companies(session, user.id))["scored"] == 0
    with pytest.raises(llm.AINotConnected):
        await llm.structured(schema=type(p := None) if False else __import__("app.schemas", fromlist=["FactCheck"]).FactCheck,
                             system="s", user="u")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        tok = (await c.post("/api/auth/dev")).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert (await c.get("/api/config")).json()["ai"] is False
        await c.post("/api/me/consent", headers=h)
        assert (await c.post("/api/profile/build", json={}, headers=h)).json()["stub"] is True
        assert (await c.post("/api/vacancies/1/package", json={}, headers=h)).json()["stub"] is True
        r = await c.post("/api/sources/voice", files={"file": ("v.ogg", b"x")}, headers=h)
        assert r.status_code == 422 and "не подключено" in r.json()["detail"]


# ---------- OpenRouter ----------

def test_strict_schema_all_required_no_extra():
    from app.schemas import ProfileExtraction
    from app.services.llm import strict_schema

    sch = strict_schema(ProfileExtraction)

    def walk(n):
        if isinstance(n, dict):
            assert "default" not in n
            if n.get("type") == "object" and "properties" in n:
                assert n["additionalProperties"] is False and set(n["required"]) == set(n["properties"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(sch)
    assert "$defs" in sch and "Expectations" in sch["$defs"]


async def test_structured_parses_openrouter_response(monkeypatch):
    """Ответ OpenRouter разбирается в схему; нарушение схемы -> повтор -> понятная ошибка."""
    import httpx

    from app.config import get_settings
    from app.schemas import FactCheck
    from app.services import llm

    calls = []

    def handler(req: httpx.Request):
        body = __import__("json").loads(req.content)
        calls.append(body)
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["provider"]["require_parameters"] is True
        content = '{"flags": []}' if len(calls) == 1 else "not json"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(llm, "_client", httpx.AsyncClient(base_url="https://or.test", transport=httpx.MockTransport(handler)))
    res = await llm.structured(schema=FactCheck, system="s", user="текст без контактов")
    assert res.flags == [] and calls[0]["model"] == get_settings().llm_model_main
    with pytest.raises(llm.LLMError, match="не по схеме"):
        await llm.structured(schema=FactCheck, system="s", user="ещё")
    with pytest.raises(llm.PIILeakError):
        await llm.structured(schema=FactCheck, system="s", user="пишите на a@b.ru")
    monkeypatch.setattr(llm, "_client", None)
