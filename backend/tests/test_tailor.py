"""Этап 3: пакет на выбор (резюме, one pager, письмо). ТЕСТОВЫЕ ДАННЫЕ: люди, компании и цифры вымышленные.
LLM подменяется, в сеть тесты не ходят."""

import datetime
import json

import pytest

from app.schemas import CV, Highlight
from app.services import export, tailor

PROFILE_TEXT = "Руководил командой из 14 человек. Запустил продукт: 180 тыс. клиентов, конверсия +27%."


def test_normalize_items_any_subset_in_fixed_order():
    assert tailor.normalize_items(["letter", "cv"]) == ["cv", "letter"]
    assert tailor.normalize_items(["one_page", "cv", "letter", "x"]) == ["cv", "one_page", "letter"]
    with pytest.raises(tailor.PackageError):
        tailor.normalize_items([])


def test_highlights_only_with_numbers_from_profile():
    cv = CV(headline="h", summary="s", skills=[], experience=[], highlights=[
        Highlight(value="180 тыс.", label="клиентов"), Highlight(value="+27%", label="конверсия"),
        Highlight(value="+40%", label="выручка"), Highlight(value="MBA", label="без числа")])
    kept = [h.value for h in tailor.verified_highlights(cv, PROFILE_TEXT).highlights]
    assert kept == ["180 тыс.", "+27%"]


def _long_view() -> dict:
    exp = [{"period": f"{2024 - 2 * i} — {2026 - 2 * i}", "company": f"Тестовая компания {i}", "role": f"Роль {i}",
            "bullets": [f"Пункт {j} роли {i}: сделал заметный результат с цифрами и понятным эффектом для бизнеса"
                        for j in range(6)]} for i in range(9)]
    return {"name": "Анна Тестова", "contacts": "anna.test@example.com", "headline": "Руководитель продукта",
            "summary": "Тестовое описание. " * 6, "experience": exp, "skills": [f"Навык {i}" for i in range(14)],
            "highlights": [{"value": str(i), "label": "показатель"} for i in range(8)],
            "education": ["Тестовый университет"], "languages": ["Русский"]}


def test_fit_one_page_trims_least_important_first():
    from app.services.cv_render import render_cv

    v = _long_view()
    assert render_cv(v, "graphite", one_page=True)[1] > 1
    pdf, fitted = export.fit_one_page(v, "graphite")
    assert render_cv(fitted, "graphite", one_page=True)[1] == 1
    first = fitted["experience"][0]["bullets"]
    assert first[0].startswith("Пункт 0") and len(first) >= 2  # самые важные пункты свежей роли на месте
    assert len(v["experience"][0]["bullets"]) == 6  # исходные данные не испорчены


async def test_build_package_choice_flags_and_one_revision(session, monkeypatch):
    import httpx

    from app.config import get_settings
    from app.models import Contact, User
    from app.services import intake, llm
    from app.services.profile import save_basic

    user = User(tg_id=950, consent_at=datetime.datetime.now())
    session.add(user)
    await session.flush()
    await save_basic(session, user, "Руководитель продукта, Москва")
    session.add(Contact(user_id=user.id, full_name="Анна Тестова", email="anna.test@example.com", links=[], vault={}))
    target = await intake.add_target(session, user, text="Руководитель продукта в Тестовый Банк. Задачи: развитие "
                                     "мобильного приложения. Требования: управление продуктом от 5 лет, SQL.")
    with pytest.raises(llm.AINotConnected):
        await tailor.build(session, user, target, ["cv"])

    calls = []
    cv = {"headline": "Руководитель продукта", "summary": "[CANDIDATE] запускал продукты.",
          "highlights": [{"value": "999", "label": "выдумка"}],  # числа нет в профиле: должно отсеяться
          "skills": ["SQL"], "education": [], "languages": [],
          "experience": [{"company": "Тестовая компания", "role": "Продакт", "period": "2021 — н.в.",
                          "bullets": ["Запустил продукт", "Выдуманное достижение"]}]}

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        name = body["response_format"]["json_schema"]["name"]
        calls.append(name)
        out = {"TailoredBoth": {"cv": cv, "cover_letter": "Хочу к вам. [CANDIDATE]"},
               "TailoredLetter": {"cover_letter": "Только письмо."},
               "TailoredCV": {"cv": cv},
               "DossierData": {"business_area": "", "summary": "", "mission": None, "values": [], "products": [],
                               "culture_signals": [], "tone_of_voice": "", "sources": []},
               "FactCheck": {"flags": [{"text": "Выдуманное достижение", "where": "cv", "reason": "нет в профиле"},
                                       {"text": "Хочу к вам.", "where": "cover_letter", "reason": "тест"}]}}[name]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                      "message": {"content": json.dumps(out, ensure_ascii=False)}}]})

    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(llm, "_client", httpx.AsyncClient(base_url="https://or.test", transport=httpx.MockTransport(handler)))
    try:
        only_letter = await tailor.build(session, user, target, ["letter"], "classic")
        assert "TailoredLetter" in calls and "TailoredCV" not in calls and "TailoredBoth" not in calls
        assert only_letter.items == ["letter"] and only_letter.cv["headline"]

        pkg = await tailor.build(session, user, target, ["letter", "one_page", "cv"], "navy_rose")
        assert pkg.items == ["cv", "one_page", "letter"] and pkg.cv["highlights"] == []

        contact = await session.get(Contact, user.id)
        view = export.tailored_view(pkg, contact)
        assert view["name"] == "Анна Тестова" and view["summary"].startswith("Анна Тестова")
        assert view["experience"][0]["bullets"] == ["Запустил продукт"]
        for item in ("cv", "one_page"):
            assert export.render_item(pkg, contact, item)[:4] == b"%PDF"
        with pytest.raises(export.FlagsNotAccepted):
            export.render_item(pkg, contact, "letter")
        assert export.render_item(pkg, contact, "letter", accept_flags=True, company="Тестовый Банк")[:4] == b"%PDF"
        with pytest.raises(ValueError):
            export.render_item(only_letter, contact, "cv")

        rev = await tailor.build(session, user, target, ["cv"], instruction="Короче summary")
        assert rev.instruction and rev.version == pkg.version + 1
        # с 15.11.2026 число правок ограничивает биллинг (test_billing.py), без него вторая правка проходит
        rev2 = await tailor.build(session, user, target, ["cv"], instruction="Ещё правка")
        assert rev2.version == rev.version + 1
    finally:
        monkeypatch.setattr(llm, "_client", None)


def test_add_missing_columns_on_old_table(tmp_path):
    """Старая база без новых колонок: init_db досоздаёт их, данные не трогает."""
    import sqlalchemy as sa

    from app.db import _add_missing_columns

    eng = sa.create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE contacts (user_id INTEGER PRIMARY KEY, full_name VARCHAR(256))")
        c.exec_driver_sql("INSERT INTO contacts (user_id, full_name) VALUES (1, 'Анна Тестова')")
        _add_missing_columns(c)
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(contacts)")}
        assert "photo_path" in cols and "vault" not in cols  # обязательные колонки не досоздаются
        assert c.exec_driver_sql("SELECT full_name FROM contacts").scalar() == "Анна Тестова"


async def test_general_cv_without_vacancy(session, monkeypatch):
    """Быстрый путь: резюме из опыта без вакансии. Без письма, без досье и покрытия, свой промпт. ТЕСТОВЫЕ ДАННЫЕ."""
    import httpx

    from app.config import get_settings
    from app.models import User, Vacancy
    from app.services import intake, llm
    from app.services.profile import save_basic

    user = User(tg_id=951, consent_at=datetime.datetime.now())
    session.add(user)
    await session.flush()
    await save_basic(session, user, "Аналитик данных, Москва")
    target = await intake.add_general_target(session, user, "  Руководитель аналитики  ")
    assert intake.is_general(target) and target.parsed["role"] == "Руководитель аналитики"
    assert intake.view(target, await session.get(Vacancy, target.vacancy_id))["general"] is True
    leak = await intake.add_general_target(session, user, "Аналитик, пишите anna.test@example.com +7 900 000-00-00")
    assert "@" not in leak.parsed["role"] and "900" not in leak.parsed["role"]  # должность уходит в промпт
    other = await intake.add_general_target(session, user)  # каждый запуск отдельной целью
    assert other.id != target.id and (await session.get(Vacancy, other.vacancy_id)).title

    calls, systems = [], []
    cv = {"headline": "Аналитик данных", "summary": "s", "highlights": [], "skills": ["SQL"], "education": [],
          "languages": [], "experience": [{"company": "Т", "role": "Аналитик", "period": "2020", "bullets": ["Считал"]}]}

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        name = body["response_format"]["json_schema"]["name"]
        calls.append(name)
        systems.append(body["messages"][0]["content"])
        out = {"TailoredCV": {"cv": cv}, "FactCheck": {"flags": []}}[name]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                      "message": {"content": json.dumps(out, ensure_ascii=False)}}]})

    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(llm, "_client", httpx.AsyncClient(base_url="https://or.test", transport=httpx.MockTransport(handler)))
    try:
        pkg = await tailor.build(session, user, target, ["cv", "one_page", "letter"])
        with pytest.raises(tailor.PackageError):  # только письмо без вакансии не собрать
            await tailor.build(session, user, other, ["letter"])
    finally:
        monkeypatch.setattr(llm, "_client", None)
    assert pkg.items == ["cv", "one_page"] and pkg.coverage is None and pkg.cover_letter == ""
    assert calls == ["TailoredCV", "FactCheck"] and "без конкретной вакансии" in systems[0]


def test_normalize_items_new_texts():
    assert tailor.normalize_items(["linkedin", "message", "cv"]) == ["cv", "message", "linkedin"]


async def test_message_linkedin_docx_and_score(session, monkeypatch):
    """П3, П5, П7, П4. ТЕСТОВЫЕ ДАННЫЕ: человек, компания и цифры вымышленные."""
    import httpx

    from app.config import get_settings
    from app.models import Contact, Source, User
    from app.services import intake, llm, review
    from app.services.profile import save_basic

    user = User(tg_id=952, consent_at=datetime.datetime.now())
    session.add(user)
    await session.flush()
    await save_basic(session, user, "Руководитель продукта, Москва")
    session.add(Contact(user_id=user.id, full_name="Анна Тестова", email="anna.test@example.com", links=[], vault={}))
    session.add(Source(user_id=user.id, kind="text", status="parsed", text="Работала продактом. Делала продукт."))
    target = await intake.add_target(session, user, text="Руководитель продукта в Тестовый Банк. Задачи: развитие "
                                     "мобильного приложения. Требования: управление продуктом от 5 лет, SQL.")
    cv = {"headline": "Руководитель продукта", "summary": "[CANDIDATE] запускала продукты.", "highlights": [],
          "skills": ["SQL"], "education": ["Тестовый университет"], "languages": [],
          "experience": [{"company": "Тестовая компания", "role": "Продакт", "period": "2021 — 2026",
                          "bullets": ["Запустила мобильное приложение для клиентов банка, 14 человек в команде"]}]}
    li = {"headline": "Руководитель продукта · финтех", "about": "Я [CANDIDATE], делаю продукты.",
          "experience": [{"role": "Продакт", "company": "Тестовая компания", "period": "2021 — 2026", "text": "Запуск"}],
          "skills": ["SQL"]}
    calls, extras_n = [], [0]

    def handler(req: httpx.Request):
        name = json.loads(req.content)["response_format"]["json_schema"]["name"]
        calls.append(name)
        if name == "Extras":
            extras_n[0] += 1
            msg = "Здравствуйте! Выдуманный факт." if extras_n[0] == 1 else "Здравствуйте! Хочу поговорить о роли."
            out = {"recruiter_message": msg, "linkedin": li}
        elif name == "FactCheck":
            first = calls.count("FactCheck") == 1
            out = {"flags": [{"text": "Выдуманный факт.", "where": "recruiter_message", "reason": "нет в профиле"}]
                   if first else []}
        else:
            out = {"TailoredBoth": {"cv": cv, "cover_letter": "Хочу к вам."},
                   "DossierData": {"business_area": "", "summary": "", "mission": None, "values": [], "products": [],
                                   "culture_signals": [], "tone_of_voice": "", "sources": []},
                   "ReqCoverage": {"items": []}}[name]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                      "message": {"content": json.dumps(out, ensure_ascii=False)}}]})

    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(llm, "_client", httpx.AsyncClient(base_url="https://or.test", transport=httpx.MockTransport(handler)))
    try:
        pkg = await tailor.build(session, user, target, ["cv", "letter", "message", "linkedin"])
        only_texts = await tailor.build(session, user, target, ["linkedin"])
    finally:
        monkeypatch.setattr(llm, "_client", None)
    assert calls.count("Extras") >= 3  # первый вариант не прошёл фактчек: одна переделка
    assert pkg.recruiter_message == "Здравствуйте! Хочу поговорить о роли." and pkg.linkedin["skills"] == ["SQL"]
    assert not [f for f in pkg.fact_flags if f["where"] == "recruiter_message"]
    assert only_texts.items == ["linkedin"] and only_texts.cv["headline"]

    contact = await session.get(Contact, user.id)
    view = export.tailored_view(pkg, contact)
    assert view["linkedin"]["about"].startswith("Я Анна Тестова")  # маркер раскрыт только при выдаче
    assert "Заголовок (Headline)" in export.linkedin_text(view["linkedin"])
    assert set(export.file_names(pkg, contact, "Роль", "Тестовый Банк")) == {"cv", "letter"}
    assert export.letter_flags(pkg) == []  # флаги текстов П3/П5 письмо не блокируют
    for item in ("cv", "letter"):
        assert export.render_docx(pkg, contact, item, company="Тестовый Банк")[:2] == b"PK"  # DOCX это zip
    with pytest.raises(ValueError):
        export.render_docx(pkg, contact, "message")
    with pytest.raises(ValueError):
        export.render_item(pkg, contact, "linkedin")

    score = await review.score_before_after(session, pkg, target, view)  # П4: у покрытия нет требований -> качество
    assert score["kind"] == "quality" and score["after"] > score["before"]


def test_salary_summary_from_open_data():
    """П6: ориентир только по похожим вакансиям с зарплатой; мало данных -> честно без цифры. ТЕСТОВЫЕ ДАННЫЕ."""
    from app.services import salary
    from app.services.sources.base import RawVacancy

    def v(title, city, lo, hi):
        return RawVacancy(source="trudvsem", external_id=title + city + str(lo), title=title, company_name="Т",
                          url="", description="", city=city, salary_from=lo, salary_to=hi, currency="RUB")

    rows = [v("Руководитель отдела продаж", "г. Москва", 150_000 + i * 10_000, None) for i in range(6)]
    rows += [v("Руководитель отдела продаж B2B", "Тестовая область", 80_000, 120_000)] * 3
    rows += [v("Водитель погрузчика", "г. Москва", 60_000, 60_000)] * 10  # непохожие не считаются
    res = salary.summarize("Руководитель отдела продаж", "Москва", rows)
    assert res["scope"] == "Москва" and res["n"] == 6 and res["median"] == 175_000
    assert res["source"] and res["date"] and res["p25"] <= res["median"] <= res["p75"]
    whole = salary.summarize("Руководитель отдела продаж", "Казань", rows)  # по городу мало: по России
    assert whole["scope"] == "по России" and whole["n"] == 9
    none = salary.summarize("Главный технолог", None, rows)
    assert none["median"] is None and "меньше" in none["reason"]
    assert "175 000" in salary.text(res)
