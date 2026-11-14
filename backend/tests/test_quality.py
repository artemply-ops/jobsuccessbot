"""Регрессии по итогам живого прогона 14.11.2026. ТЕСТОВЫЕ ДАННЫЕ: люди и компании вымышленные."""

from app.schemas import CV, CVExperience, FactFlag
from app.services.pii import Vault, depersonalize
from app.services.tailor import apply_cv_flags


def test_name_masking_keeps_similar_words():
    """«Тестова» не должна превращать «Тестовый университет» в [CANDIDATE] (вуз пропадал из резюме)."""
    out = depersonalize("Светлана Тестовая окончила Тестовый федеральный университет. Письмо Светлане Тестовой.",
                        "Светлана Тестовая", Vault(), use_ner=False)
    assert "Тестовый федеральный университет" in out and "Светлан" not in out and "Тестовой" not in out
    out = depersonalize("Ольга Белова, Белорусский рынок. Ольгу Белову, Беловой.", "Ольга Белова", Vault(), use_ner=False)
    assert "Белорусский" in out and "Белов" not in out and "Ольг" not in out
    out = depersonalize("ИГОРЬ ТЕСТОВ, Игоря Тестова", "Игорь Тестов", Vault(), use_ner=False)
    assert "ИГОРЬ" not in out and "Тестова" not in out


def test_flagged_headline_and_skills_do_not_reach_file():
    cv = CV(headline="Коммерческий директор (B2B-продажи)", summary="s", skills=["CRM", "Удержание сотрудников"],
            experience=[CVExperience(company="Тестсофт", role="Руководитель отдела продаж", period="2019 — н.в.",
                                     bullets=["b"])])
    flags = [FactFlag(text="Коммерческий директор (B2B-продажи)", where="cv", reason="должности не было"),
             FactFlag(text="Удержание сотрудников", where="cv", reason="нет в профиле"),
             FactFlag(text="что-то в письме", where="cover_letter", reason="r")]
    out = apply_cv_flags(cv, flags, "Руководитель отдела продаж B2B")
    assert out.headline == "Руководитель отдела продаж B2B" and out.skills == ["CRM"]
    untouched = apply_cv_flags(CV(headline="Продакт", summary="", skills=["SQL"], experience=[]), [], "x")
    assert untouched.headline == "Продакт" and untouched.skills == ["SQL"]


def test_low_severity_is_default_high_for_old_flags():
    assert FactFlag(text="t", where="cv", reason="r").severity == "high"


def test_guess_name_skips_dates_and_headers():
    """Регрессия 14.11: в шапку резюме попадало «Nov» из даты вместо имени."""
    from app.services.profile import guess_name, plausible_name

    assert guess_name("Nov 2023 Resume\nИван Тестов\nМенеджер") == "Иван Тестов"
    assert guess_name("ТЕСТОВ ИВАН - CRO, CEO") == "Тестов Иван"
    assert guess_name("Наталья Тестюк — главный бухгалтер") == "Наталья Тестюк"
    assert guess_name("Резюме\nОпыт работы\n2020 Nov") is None
    assert not plausible_name("Nov Тестов") and not plausible_name("Иван") and plausible_name("Иван Тестов")


async def test_check_url_explains_without_network():
    from app.services import intake

    r = await intake.check_url("https://hh.ru/vacancy/1")
    assert r["ok"] is False and r["code"] == "hh" and "hh.ru" in r["reason"]
    r = await intake.check_url("не ссылка")
    assert r["ok"] is False and r["code"] == "bad_url"


async def test_send_package_to_telegram_and_contact_patch(session, monkeypatch):
    """Кнопка «Прислать в Telegram»: файлы уходят в чат пользователя; правка имени не стирает почту."""
    import datetime

    import httpx

    from app.auth import issue_token
    from app.main import app
    from app.models import Contact, Package, User, Vacancy
    from app.services import notify

    u = User(tg_id=777001, consent_at=datetime.datetime.now())
    session.add(u)
    await session.flush()
    v = Vacancy(fingerprint="fp-send-test", company_name="Тестовая компания", title="Тестовая роль", url="user:1")
    session.add(v)
    session.add(Contact(user_id=u.id, full_name="Nov Тестов", email="anna.test@example.com", links=[], vault={}))
    await session.flush()
    p = Package(user_id=u.id, vacancy_id=v.id, version=1, items=["cv", "letter"], scheme="classic", recruiter_message="",
                cv={"headline": "Роль", "summary": "s", "skills": [], "experience": [], "highlights": []},
                cover_letter="Письмо.", fact_flags=[{"text": "Письмо.", "where": "cover_letter", "reason": "r"}])
    session.add(p)
    await session.commit()
    sent = []

    async def fake_send(chat_id, files, text=None):
        sent.append((chat_id, [n for n, _ in files]))
        return True

    monkeypatch.setattr(notify, "send_documents", fake_send)
    h = {"Authorization": f"Bearer {issue_token(u)}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = (await c.post(f"/api/packages/{p.id}/send", json={}, headers=h)).json()
        assert r["sent"] == 1 and r["skipped"] == ["letter"]  # письмо с замечаниями ждёт подтверждения
        assert sent[0][0] == 777001 and sent[0][1][0].startswith("Резюме")
        r = (await c.post(f"/api/packages/{p.id}/send", json={"accept_flags": True}, headers=h)).json()
        assert r["sent"] == 2
        assert (await c.put("/api/contact", json={"full_name": "Анна Тестова"}, headers=h)).status_code == 200
        me = (await c.get("/api/me", headers=h)).json()["contact"]
        assert me["full_name"] == "Анна Тестова" and me["email"] == "anna.test@example.com"
        assert (await c.get("/api/contact/photo", headers=h)).status_code == 404
