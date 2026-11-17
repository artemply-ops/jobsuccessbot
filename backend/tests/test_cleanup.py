"""Т1: прерванные перезапуском задачи. Т4: чистка брошенных гостей."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import jobs
from app.config import get_settings
from app.db import SessionLocal
from app.models import (BillingUsage, Contact, Grant, Job, LlmUsage, Payment, Profile, Source, User, Vacancy)
from app.services import cleanup

OLD = datetime.now(timezone.utc) - timedelta(days=45)


async def _guest(s, *, created=OLD, **kw) -> User:
    u = User(guest=True, created_at=created, **kw)
    s.add(u)
    await s.flush()
    return u


async def test_fail_interrupted_marks_queued_and_running(session):
    a, b, c = Job(kind="profile", status="running"), Job(kind="package", status="queued"), Job(kind="x", status="done")
    session.add_all([a, b, c])
    await session.commit()
    n = await jobs.fail_interrupted()
    assert n >= 2
    async with SessionLocal() as s:
        got = {j.id: j for j in (await s.execute(select(Job).where(Job.id.in_([a.id, b.id, c.id])))).scalars()}
    assert got[a.id].status == got[b.id].status == "error"
    assert got[a.id].note == jobs.INTERRUPTED and got[a.id].finished_at is not None
    assert got[c.id].status == "done"
    assert await jobs.fail_interrupted() == 0  # повторно нечего закрывать


async def test_purge_stale_guest_with_everything(session):
    files = get_settings().files_dir
    files.mkdir(parents=True, exist_ok=True)
    g = await _guest(session)
    src_file, photo = files / f"{g.id}_cv.pdf", files / f"{g.id}_photo_x.img"
    other = files / f"{g.id}9_cv.pdf"  # чужой файл с похожим префиксом не трогаем
    for f in (src_file, photo, other):
        f.write_bytes(b"x")
    session.add_all([
        Source(user_id=g.id, kind="file", path=str(src_file), text="резюме", created_at=OLD),
        Contact(user_id=g.id, full_name="Тестовый Гость", photo_path=str(photo)),
        Profile(user_id=g.id, version=1, data={}, created_at=OLD),
        Job(user_id=g.id, kind="profile", status="done", created_at=OLD),
        Vacancy(fingerprint=f"general:{g.id}:abc", company_name="", title="t", url=f"general:{g.id}",
                description=""),
        LlmUsage(user_id=g.id, step="s", model="m", cost_usd=0.01, created_at=OLD),
    ])
    gr = Grant(user_id=g.id, sku="free", quota={"package": 1}, source="free", created_at=OLD)
    session.add(gr)
    await session.flush()
    session.add(BillingUsage(user_id=g.id, grant_id=gr.id, action="package", created_at=OLD))
    await session.commit()
    gid = g.id

    assert gid in await cleanup.stale_guest_ids(session)
    await cleanup.purge_stale_guests(session)

    async with SessionLocal() as s:
        assert await s.get(User, gid) is None
        assert await s.get(Contact, gid) is None
        for model in (Source, Profile, Job, Grant, BillingUsage):
            assert not (await s.execute(select(model).where(model.user_id == gid))).first()
        assert not (await s.execute(select(Vacancy).where(Vacancy.fingerprint == f"general:{gid}:abc"))).first()
        usage = (await s.execute(select(LlmUsage).where(LlmUsage.cost_usd == 0.01))).scalars().all()
        assert usage and all(u.user_id is None for u in usage)  # расход для экономики остался, без привязки
    assert not src_file.exists() and not photo.exists() and other.exists()
    other.unlink()


async def test_keep_active_paid_signed_in_and_fresh(session):
    fresh = await _guest(session, created=datetime.now(timezone.utc))
    active = await _guest(session)
    session.add(Source(user_id=active.id, kind="text", text="t"))  # активность сегодня
    paid = await _guest(session)
    session.add(Payment(user_id=paid.id, provider="yookassa", sku="week", amount=249, currency="RUB"))
    tg = User(guest=None, tg_id=777000111, created_at=OLD)
    session.add(tg)
    await session.commit()
    ids = {fresh.id, active.id, paid.id, tg.id}

    stale = set(await cleanup.stale_guest_ids(session))
    assert not ids & stale
    await cleanup.purge_stale_guests(session)
    async with SessionLocal() as s:
        assert len((await s.execute(select(User.id).where(User.id.in_(ids)))).all()) == 4
