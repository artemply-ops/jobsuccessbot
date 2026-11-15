"""Web Push: ключи из gen_vapid подходят pywebpush, подписка, рассылка, удаление протухших подписок."""

import subprocess
import sys
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.main import app
from app.models import PushSubscription
from app.services import push

ROOT = Path(__file__).resolve().parents[2]


def _keys(tmp_path) -> dict:
    env = tmp_path / ".env"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_vapid.py"), str(env)], check=True, capture_output=True)
    pairs = dict(line.split("=", 1) for line in env.read_text().splitlines() if line)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_vapid.py"), str(env)], check=True, capture_output=True)
    assert env.read_text().count("VAPID_PRIVATE_KEY=") == 1  # повторный запуск ключи не меняет
    return pairs


def test_vapid_keys_work_with_pywebpush(tmp_path):
    from py_vapid import Vapid

    k = _keys(tmp_path)
    v = Vapid.from_string(k["VAPID_PRIVATE_KEY"])
    headers = v.sign({"sub": "mailto:test@example.com", "aud": "https://fcm.googleapis.com"})
    assert headers["Authorization"].startswith("vapid t=")


async def test_subscribe_send_and_drop_dead(tmp_path, monkeypatch):
    k = _keys(tmp_path)
    s = get_settings()
    monkeypatch.setattr(s, "vapid_public_key", k["VAPID_PUBLIC_KEY"])
    monkeypatch.setattr(s, "vapid_private_key", k["VAPID_PRIVATE_KEY"])
    await init_db()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/push/key")).json()["key"] == k["VAPID_PUBLIC_KEY"]
        tok = (await c.post("/api/auth/guest")).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        uid = (await c.get("/api/me", headers=h)).json()["id"]
        for n in (1, 2):
            r = await c.post("/api/push/subscribe", headers=h, json={
                "endpoint": f"https://push.example.com/{uid}/{n}", "keys": {"p256dh": "p" * 20, "auth": "a" * 10}})
            assert r.status_code == 200
        assert (await c.post("/api/push/subscribe", headers=h, json={"endpoint": "http://x", "keys": {}})).status_code == 400
    # первая подписка живая, вторая протухла (410)
    monkeypatch.setattr(push, "_send_one", lambda sub, payload: None if sub.endpoint.endswith("/1") else 410)
    async with SessionLocal() as session:
        assert await push.send(session, uid, "JobPilot", "Документы готовы") == 1
        left = (await session.execute(select(func.count()).select_from(PushSubscription)
                                      .where(PushSubscription.user_id == uid))).scalar()
        assert left == 1
