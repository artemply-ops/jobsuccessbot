"""Один кабинет на сайт, Mini App и бота: гость, вход по почте, привязка Telegram, объединение кабинетов."""

import hashlib
import hmac
import time

import httpx
from sqlalchemy import func, select

from app.db import SessionLocal, init_db
from app.main import app
from app.models import Grant, Profile, Source, User
from app.services import accounts

TOKEN = "123456:TEST"
TEXT = "Работал Python-разработчиком 5 лет в компании Ромашка, делал API на FastAPI и PostgreSQL. " * 2


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _widget(tg_id: int) -> dict:
    data = {"id": str(tg_id), "first_name": "Тест", "auth_date": str(int(time.time()))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(hashlib.sha256(TOKEN.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return data


async def _email_login(c, email: str, tok: str | None = None) -> str:
    r = await c.post("/api/auth/email/start", json={"email": email})
    assert r.status_code == 200, r.text
    r = await c.post("/api/auth/email/verify", json={"email": email, "code": r.json()["dev_code"]},
                     headers=_h(tok) if tok else {})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _sources(user_id: int) -> int:
    async with SessionLocal() as s:
        return (await s.execute(select(func.count()).select_from(Source).where(Source.user_id == user_id))).scalar()


async def _guest_with_material(c) -> tuple[str, int]:
    tok = (await c.post("/api/auth/guest")).json()["token"]
    await c.post("/api/me/consent", headers=_h(tok))
    assert (await c.post("/api/sources/text", json={"text": TEXT}, headers=_h(tok))).status_code == 200
    me = (await c.get("/api/me", headers=_h(tok))).json()
    assert me["guest"] is True
    return tok, me["id"]


async def test_guest_works_then_logs_in_by_email_and_keeps_materials():
    await init_db()
    async with _client() as c:
        tok, gid = await _guest_with_material(c)
        r = await c.post("/api/targets/1/package", json={"items": ["cv"]}, headers=_h(tok))
        assert r.status_code == 403 and r.json()["detail"]["need"] == "login"  # документы только после входа
        tok2 = await _email_login(c, "New.User@Example.com", tok)
        me = (await c.get("/api/me", headers=_h(tok2))).json()
        assert me["id"] == gid and me["guest"] is False  # гость стал полноценным кабинетом
        m = (await c.get("/api/auth/methods", headers=_h(tok2))).json()
        assert m["email"] == "new.user@example.com" and m["telegram"] is False
        assert await _sources(gid) == 1


async def test_guest_logs_into_existing_account_and_data_merges(monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr(accounts, "CODE_RESEND", timedelta(0))  # второй вход той же почтой в пределах минуты
    await init_db()
    async with _client() as c:
        old = await _email_login(c, "old@example.com")
        old_id = (await c.get("/api/me", headers=_h(old))).json()["id"]
        await c.post("/api/me/consent", headers=_h(old))
        await c.post("/api/sources/text", json={"text": TEXT}, headers=_h(old))
        await c.get("/api/billing", headers=_h(old))  # бесплатный грант у старого кабинета
        tok, gid = await _guest_with_material(c)
        await c.get("/api/billing", headers=_h(tok))  # и у гостя
        tok2 = await _email_login(c, "old@example.com", tok)
        assert (await c.get("/api/me", headers=_h(tok2))).json()["id"] == old_id
        assert await _sources(old_id) == 2  # материалы гостя переехали
        async with SessionLocal() as s:
            assert await s.get(User, gid) is None
            free = (await s.execute(select(func.count()).select_from(Grant)
                                    .where(Grant.user_id == old_id, Grant.source == "free"))).scalar()
            assert free == 1  # бесплатный старт один на человека


async def test_telegram_linked_to_email_account_merges_bot_cabinet():
    await init_db()
    async with _client() as c:
        # Кабинет из бота: Telegram 7001 с материалом
        bot_tok = (await c.post("/api/auth/telegram-login", json=_widget(7001))).json()["token"]
        bot_id = (await c.get("/api/me", headers=_h(bot_tok))).json()["id"]
        await c.post("/api/me/consent", headers=_h(bot_tok))
        await c.post("/api/sources/text", json={"text": TEXT}, headers=_h(bot_tok))
        # Кабинет на сайте по почте, затем «Подключить Telegram»
        web = await _email_login(c, "web@example.com")
        web_id = (await c.get("/api/me", headers=_h(web))).json()["id"]
        r = await c.post("/api/auth/telegram-login", json=_widget(7001), headers=_h(web))
        assert r.status_code == 200
        me = (await c.get("/api/me", headers=_h(r.json()["token"]))).json()
        assert me["id"] == web_id and me["telegram"] is True
        assert await _sources(web_id) == 1
        # Теперь вход через Telegram ведёт в тот же кабинет
        again = (await c.post("/api/auth/telegram-login", json=_widget(7001))).json()["token"]
        assert (await c.get("/api/me", headers=_h(again))).json()["id"] == web_id
        assert (await c.get("/api/me", headers=_h(bot_tok))).status_code == 401  # старый кабинет удалён
        assert bot_id != web_id


async def test_bot_link_token_opens_same_cabinet_once():
    await init_db()
    async with SessionLocal() as s:
        u = User(tg_id=7002)
        s.add(u)
        await s.flush()
        token = await accounts.issue_link_token(s, u)
        await s.commit()
    async with _client() as c:
        r = await c.post("/api/auth/token", json={"token": token})
        assert r.status_code == 200
        assert (await c.get("/api/me", headers=_h(r.json()["token"]))).json()["id"] == u.id
        assert (await c.post("/api/auth/token", json={"token": token})).status_code == 400  # одноразовая


async def test_wrong_code_limits():
    await init_db()
    async with _client() as c:
        await c.post("/api/auth/email/start", json={"email": "x@example.com"})
        assert (await c.post("/api/auth/email/start", json={"email": "x@example.com"})).status_code == 400  # раз в минуту
        for _ in range(5):
            r = await c.post("/api/auth/email/verify", json={"email": "x@example.com", "code": "000000"})
            assert r.status_code == 400
        assert "устарел" in r.json()["detail"] or "Неверный" in r.json()["detail"]
        r = await c.post("/api/auth/email/verify", json={"email": "x@example.com", "code": "000000"})
        assert "устарел" in r.json()["detail"]
        assert (await c.post("/api/auth/email/start", json={"email": "не почта"})).status_code == 400


async def test_merge_keeps_both_profile_histories(session):
    from datetime import datetime, timedelta, timezone

    a, b = User(tg_id=7003), User(guest=True)
    session.add_all([a, b])
    await session.flush()
    t = datetime.now(timezone.utc)
    session.add_all([Profile(user_id=a.id, version=1, data={"v": "a1"}, created_at=t - timedelta(hours=3)),
                     Profile(user_id=b.id, version=1, data={"v": "b1"}, created_at=t - timedelta(hours=2)),
                     Profile(user_id=a.id, version=2, data={"v": "a2"}, created_at=t - timedelta(hours=1))])
    await session.flush()
    await accounts.merge(session, keep=a, drop=b)
    rows = (await session.execute(select(Profile).where(Profile.user_id == a.id).order_by(Profile.version)))\
        .scalars().all()
    assert [(p.version, p.data["v"]) for p in rows] == [(1, "a1"), (2, "b1"), (3, "a2")]  # свежий последним



async def test_merge_keeps_tone_from_dropped_account(session):
    a, b = User(tg_id=7004), User(guest=True, tone="ty")
    session.add_all([a, b])
    await session.flush()
    await accounts.merge(session, keep=a, drop=b)
    assert a.tone == "ty"
    c, d = User(tg_id=7005, tone="vy"), User(guest=True, tone="ty")
    session.add_all([c, d])
    await session.flush()
    await accounts.merge(session, keep=c, drop=d)
    assert c.tone == "vy"  # свой выбор у оставшегося кабинета важнее

async def test_admin_stats_only_for_admin_and_counts():
    await init_db()
    async with _client() as c:
        r = await c.get("/api/admin/stats")
        assert r.status_code == 401
        tok = (await c.post("/api/auth/dev")).json()["token"]  # в dev админка открыта (как /admin/promo)
        r = await c.get("/api/admin/stats", headers=_h(tok))
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["series"]) == 30 and d["kpi"]["users_total"] >= 1
        assert d["funnel"][0]["step"] == "Новые пользователи"
        assert (await c.get("/api/admin/stats?date_from=2026-11-10&date_to=2026-11-01", headers=_h(tok))).status_code == 400


async def test_site_texts_edit_history_restore():
    await init_db()
    async with _client() as c:
        pub = (await c.get("/api/site/texts")).json()
        assert pub["hero"]["eyebrow"] == "Резюме за 2 минуты"
        assert (await c.put("/api/admin/texts", json={"data": {}})).status_code == 401
        h = _h((await c.post("/api/auth/dev")).json()["token"])
        cur = (await c.get("/api/admin/texts", headers=h)).json()
        data = cur["data"]
        old_title = data["hero"]["title"]
        data["hero"]["title"] = "Новый слоган — тест"
        r = await c.put("/api/admin/texts", headers=h, json={"data": data, "version": cur["version"]})
        assert r.status_code == 200 and any("тире" in w for w in r.json()["warnings"])
        assert (await c.get("/api/site/texts")).json()["hero"]["title"] == "Новый слоган — тест"
        # устаревшая версия не затирает свежую правку
        assert (await c.put("/api/admin/texts", headers=h, json={"data": data, "version": cur["version"]})).status_code == 409
        hist = (await c.get("/api/admin/texts", headers=h)).json()["history"]
        assert len(hist) >= 2  # первая версия в истории это исходные тексты
        assert (await c.post(f"/api/admin/texts/restore/{hist[1]['id']}", headers=h)).status_code == 200
        assert (await c.get("/api/site/texts")).json()["hero"]["title"] == old_title
