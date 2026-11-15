"""Учёт источников: метка первого касания на сайте, в боте и Mini App, перенос при объединении, отчёт в админке."""

import httpx

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.main import app
from app.models import User
from app.services import accounts, attribution


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_clean_and_channel():
    assert attribution.clean(" Friend_Ivan ") == "friend_ivan"
    assert attribution.clean("Блогер Иван") is None  # кириллицу не храним: метка это код ссылки
    assert attribution.clean("tg posev 1") == "tg_posev_1"
    assert attribution.clean("x" * 100) == "x" * 64
    assert attribution.clean("") is None and attribution.clean(None) is None
    assert attribution.channel("friend_ivan") == "friend"
    assert attribution.channel("seo") == "seo"
    assert attribution.channel(None) == "direct"


def test_assign_is_first_touch_only():
    u = User()
    attribution.assign(u, "blog_anna")
    attribution.assign(u, "tg_posev1")
    assert u.source == "blog_anna"


async def test_guest_gets_source_from_header_and_report_groups_by_channel(monkeypatch):
    await init_db()
    accounts._guest_hits.clear()
    monkeypatch.setattr(get_settings(), "admin_tg_ids", [1])
    async with _client() as c:
        ids = []
        for src in ("friend_ivan", "friend_olga", None):
            r = await c.post("/api/auth/guest", headers={"X-JP-Source": src} if src else {})
            tok = r.json()["token"]
            ids.append((await c.get("/api/me", headers={"Authorization": f"Bearer {tok}"})).json()["id"])
        async with SessionLocal() as s:
            got = [(await s.get(User, i)).source for i in ids]
        assert got == ["friend_ivan", "friend_olga", None]

        admin = (await c.post("/api/auth/dev")).json()["token"]
        st = (await c.get("/api/admin/stats", headers={"Authorization": f"Bearer {admin}"})).json()
        rows = {r["source"]: r for r in st["sources"]}
        assert rows["friend_ivan"]["channel"] == "friend" and rows["friend_ivan"]["users"] >= 1
        assert rows["direct"]["users"] >= 1
        assert {"profile", "documents", "paid", "revenue_rub", "paid_rate"} <= set(rows["friend_olga"])


async def test_merge_keeps_earlier_source():
    await init_db()
    async with SessionLocal() as s:
        old = User(source="blog_anna", tg_id=990001)
        s.add(old)
        await s.flush()
        guest = User(guest=True, source="tg_posev1")
        s.add(guest)
        await s.flush()
        await accounts.merge(s, keep=guest, drop=old)
        assert guest.source == "blog_anna"
        await s.rollback()
