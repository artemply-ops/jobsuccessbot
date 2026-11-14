"""Этап 4 пивота. Сценарий бота «резюме под вакансию»:

материалы (файл, фото, голос, текст) -> вакансия (ссылка или текст) -> 2-3 уточнения -> выбор документов
(резюме, one pager, письмо: любой набор) -> цветовая схема -> фото (необязательно) -> файлы -> одна правка.
Состояния в памяти процесса (MemoryStorage): после перезапуска пользователь продолжает с шага вакансии.
"""

import html
import io
import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message)

from app.config import get_settings
from app.db import SessionLocal
from app.jobs import build_profile_job, improve_job, tailor_job
from app.models import Contact, Package, Target, User, Vacancy
from app.schemas import PACKAGE_ITEMS
from app.services import cv_render, export, intake, llm, profile

log = logging.getLogger("bot.cv")
router = Router(name="cv_flow")

ITEM_TITLES = {"cv": "Резюме", "one_page": "Резюме на 1 страницу", "letter": "Сопроводительное письмо"}
URL_RE = re.compile(r"^\s*(https?://|www\.)\S+\s*$|^\s*[\w-]+(\.[\w-]+)+/\S*\s*$", re.IGNORECASE)


class CV(StatesGroup):
    materials = State()
    vacancy = State()
    company = State()
    answers = State()
    items = State()
    scheme = State()
    photo = State()
    revise = State()
    name = State()
    facts = State()


def is_url(text: str) -> bool:
    return bool(URL_RE.match(text or ""))


def items_kb(selected: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=("☑ " if i in selected else "☐ ") + ITEM_TITLES[i], callback_data=f"cvi:{i}")]
            for i in PACKAGE_ITEMS]
    rows.append([InlineKeyboardButton(text="Все три", callback_data="cvi:all"),
                 InlineKeyboardButton(text="Дальше →", callback_data="cvi:next")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def toggle(selected: list[str], item: str) -> list[str]:
    if item == "all":
        return list(PACKAGE_ITEMS)
    if item not in PACKAGE_ITEMS:
        return selected
    out = [i for i in selected if i != item] if item in selected else selected + [item]
    return [i for i in PACKAGE_ITEMS if i in out]


def scheme_kb(prefix: str = "cvs") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=s.title.split(" (")[0],
                                                                       callback_data=f"{prefix}:{s.key}")]
                                                 for s in cv_render.SCHEMES.values()])


SAMPLE = {  # ТЕСТОВЫЕ ДАННЫЕ для образца схем: человек и цифры вымышленные
    "name": "Образец Резюме", "contacts": "mail@example.com · Москва", "headline": "Руководитель продукта",
    "summary": "Так будет выглядеть ваше резюме в этой цветовой схеме.",
    "highlights": [{"value": "12", "label": "образец"}, {"value": "3", "label": "образец"},
                   {"value": "40%", "label": "образец"}, {"value": "7", "label": "образец"}],
    "experience": [{"period": "2021 — н.в.", "company": "Компания", "role": "Должность",
                    "bullets": ["Пункт опыта", "Ещё один пункт опыта"]}],
    "skills": ["Навык", "Навык", "Навык"], "education": [], "languages": []}
_preview_cache: bytes | None = None


def schemes_preview() -> bytes:
    """Одна картинка с образцами всех схем (верх листа), считается один раз на процесс."""
    global _preview_cache
    if _preview_cache is None:
        from PIL import Image

        ims = []
        for key in cv_render.SCHEMES:
            png = cv_render.preview_png(cv_render.render_cv(SAMPLE, key, one_page=True)[0], dpi=60)
            im = Image.open(io.BytesIO(png))
            ims.append(im.crop((0, 0, im.width, int(im.height * 0.42))))
        gap = 16
        out = Image.new("RGB", (sum(i.width for i in ims) + gap * (len(ims) + 1), ims[0].height + 2 * gap),
                        (232, 232, 236))
        x = gap
        for im in ims:
            out.paste(im, (x, gap))
            x += im.width + gap
        buf = io.BytesIO()
        out.save(buf, "PNG")
        _preview_cache = buf.getvalue()
    return _preview_cache


async def _user(tg) -> User:
    from app.auth import upsert_tg_user

    async with SessionLocal() as s:
        u = await upsert_tg_user(s, tg.id, tg.username, tg.first_name)
        await s.commit()
        return u


async def _error(message: Message, e: Exception, where: str):
    """Ожидаемые ошибки показываем как есть, остальные пишем в лог с трассировкой."""
    if isinstance(e, (ValueError, llm.LLMError)):
        log.warning("%s: user=%s %s: %s", where, message.chat.id, type(e).__name__, e)
        await message.answer(f"⚠️ {html.escape(str(e))}")
    else:
        log.exception("%s failed: user=%s", where, message.chat.id)
        await message.answer("⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже.")


# ---------- Шаг 1. Материалы ----------

START_TEXT = ("Сделаю резюме и сопроводительное письмо под конкретную вакансию.\n\n"
              "<b>Шаг 1.</b> Пришлите ваше резюме: файл (PDF, DOCX), скан или фото. Можно добавить рассказ о себе "
              "текстом или голосовыми: чем занимались, какие результаты с цифрами.\n\n"
              "Когда всё пришлёте, нажмите «Готово».")


def done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Готово, дальше →", callback_data="cv:done")]])


async def start_flow(message: Message, state: FSMContext):
    await state.set_state(CV.materials)
    await message.answer(START_TEXT, reply_markup=done_kb())


@router.message(Command("new"))
async def new_vacancy(message: Message, state: FSMContext):
    """Новая вакансия с тем же профилем."""
    user = await _user(message.from_user)
    async with SessionLocal() as s:
        has_profile = await profile.latest_profile(s, user.id) is not None
    if not has_profile:
        await start_flow(message, state)
        return
    await ask_vacancy(message, state)


@router.callback_query(F.data == "cv:done")
async def on_materials_done(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    user = await _user(cb.from_user)
    async with SessionLocal() as s:
        from sqlalchemy import func, select

        from app.models import Source

        n = (await s.execute(select(func.count()).where(Source.user_id == user.id, Source.status == "parsed"))).scalar()
    if not n:
        await cb.message.answer("Пока нет ни одного материала. Пришлите резюме файлом или расскажите о себе.")
        return
    if llm.available():
        try:
            async with working(cb.message, STAGES_PROFILE):
                await _run(build_profile_job(user.id))  # по всем материалам: новый файл мог заменить старое резюме
        except Exception as e:
            await _error(cb.message, e, "build_profile")
            return
    await ask_name(cb.message, state, user.id)


# ---------- Ожидание ответа модели: анимированный статус ----------

STAGES_PROFILE = ["Читаю ваши материалы", "Выделяю опыт и достижения", "Собираю профиль"]
STAGES_VACANCY = ["Читаю вакансию", "Выделяю требования", "Смотрю сайт компании"]
STAGES_CLARIFY = ["Сравниваю профиль с вакансией", "Ищу пробелы", "Формулирую вопросы"]
STAGES_ANSWERS = ["Добавляю ответы в профиль", "Обновляю профиль"]
STAGES_TAILOR = ["Изучаю компанию", "Пишу резюме под вакансию", "Пишу письмо", "Проверяю каждый факт по профилю",
                 "Собираю PDF"]
SPIN = "◐◓◑◒"


class working:
    """Статус-сообщение, которое обновляется, пока ждём модель: этап, анимация, секунды. Потом удаляется.

    Плюс «печатает…» в шапке чата. Ошибки обновления (лимиты Telegram) не мешают основной работе.
    """

    def __init__(self, message: Message, stages: list[str], every: float = 3.0):
        self.message, self.stages, self.every = message, stages, every
        self.msg = self.task = None

    def _text(self, i: int, sec: int) -> str:
        stage = self.stages[min(i // 2, len(self.stages) - 1)]
        done = "".join("●" if k <= min(i // 2, len(self.stages) - 1) else "○" for k in range(len(self.stages)))
        return f"{SPIN[i % 4]} <b>{stage}…</b>\n{done}  <i>{sec} с</i>"

    async def _loop(self):
        import asyncio
        import time

        t0, i = time.monotonic(), 0
        while True:
            await asyncio.sleep(self.every)
            i += 1
            try:
                await self.message.bot.send_chat_action(self.message.chat.id, "typing")
                await self.msg.edit_text(self._text(i, int(time.monotonic() - t0)))
            except Exception:
                pass

    async def __aenter__(self):
        import asyncio

        try:
            self.msg = await self.message.answer(self._text(0, 0))
            self.task = asyncio.create_task(self._loop())
        except Exception:
            log.warning("progress message failed", exc_info=True)
        return self

    async def __aexit__(self, *exc):
        if self.task:
            self.task.cancel()
        if self.msg:
            try:
                await self.msg.delete()
            except Exception:
                pass
        return False


# ---------- Имя в резюме ----------

async def ask_name(message: Message, state: FSMContext, user_id: int):
    """Имя показываем на подтверждение: его угадывают по резюме, и угадывание может ошибиться."""
    from app.services.profile import plausible_name

    async with SessionLocal() as s:
        c = await s.get(Contact, user_id)
    name = c.full_name if c else None
    if name and plausible_name(name):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Верно", callback_data="cv:name_ok"),
                                                    InlineKeyboardButton(text="Изменить", callback_data="cv:name_edit")]])
        await message.answer(f"В шапке резюме будет имя: <b>{html.escape(name)}</b>", reply_markup=kb)
        return
    await state.set_state(CV.name)
    await message.answer("Как вас указать в резюме? Напишите имя и фамилию.")


@router.callback_query(F.data.in_({"cv:name_ok", "cv:name_edit"}))
async def on_name(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    if cb.data == "cv:name_ok":
        await ask_vacancy(cb.message, state)
        return
    await state.set_state(CV.name)
    await cb.message.answer("Напишите имя и фамилию так, как они должны стоять в резюме.")


async def take_name(message: Message, state: FSMContext, text: str):
    name = " ".join(text.split())
    if not 2 <= len(name.split()) <= 4 or len(name) > 80 or any(ch.isdigit() for ch in name):
        await message.answer("Напишите имя и фамилию, например: <code>Анна Иванова</code>")
        return
    user = await _user(message.from_user)
    async with SessionLocal() as s:
        c = await profile.get_contact(s, await s.get(User, user.id))
        c.full_name = name
        await s.commit()
    await message.answer(f"Записал: <b>{html.escape(name)}</b>")
    await ask_vacancy(message, state)


async def _run(fn) -> dict:
    async with SessionLocal() as s:
        try:
            res = await fn(s)
            await s.commit()
            return res
        except Exception:
            await s.rollback()
            raise


# ---------- Шаг 2. Вакансия ----------

async def ask_vacancy(message: Message, state: FSMContext):
    await state.set_state(CV.vacancy)
    await message.answer("<b>Шаг 2.</b> Пришлите ссылку на вакансию на сайте компании или вставьте её текст.\n"
                         "<i>Ссылки hh.ru я не открываю: оттуда скопируйте текст.</i>")


async def take_vacancy(message: Message, state: FSMContext, text: str):
    user = await _user(message.from_user)
    if is_url(text):  # отдельный шаг: сначала проверяем, читается ли страница, и честно говорим, если нет
        async with working(message, ["Проверяю, читается ли страница"]):
            chk = await intake.check_url(text.strip())
        if not chk["ok"]:
            await message.answer(f"⚠️ Не получилось прочитать страницу: {html.escape(chk['reason'])}\n\n"
                                 "<b>Скопируйте текст вакансии со страницы и пришлите его сообщением.</b>")
            return
        await message.answer("✅ Страница читается" + (f": «{html.escape(chk['title'][:120])}»" if chk.get("title") else ""))
    try:
        async with working(message, STAGES_VACANCY):
            async with SessionLocal() as s:
                u = await s.get(User, user.id)
                t = await (intake.add_target(s, u, url=text.strip()) if is_url(text) else intake.add_target(s, u, text=text))
                v = await s.get(Vacancy, t.vacancy_id)
                await s.commit()
    except Exception as e:
        await _error(message, e, "add_target")
        return
    await state.update_data(target_id=t.id, items=list(PACKAGE_ITEMS))
    p = t.parsed
    reqs = "\n".join(f"• {html.escape(r)}" for r in (p.get("requirements") or [])[:6])
    await message.answer(f"<b>{html.escape(v.title)}</b> · {html.escape(v.company_name)}"
                         + (f"\n\nГлавные требования:\n{reqs}" if reqs else ""))
    if not t.company_domain:
        await state.set_state(CV.company)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="cv:nosite")]])
        await message.answer("Пришлите адрес сайта компании: возьму оттуда миссию и ценности для письма. "
                             "Или нажмите «Пропустить».", reply_markup=kb)
        return
    await ask_questions(message, state)


async def take_company(message: Message, state: FSMContext, text: str):
    data = await state.get_data()
    domain = intake._domain(text)
    if not domain:
        await message.answer("Не похоже на адрес сайта. Пришлите, например, <code>company.ru</code> или нажмите «Пропустить».")
        return
    async with SessionLocal() as s:
        t = await s.get(Target, data["target_id"])
        t.company_domain = domain
        await s.commit()
    await ask_questions(message, state)


@router.callback_query(F.data == "cv:nosite")
async def on_no_site(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await ask_questions(cb.message, state)


# ---------- Шаг 3. Уточнения ----------

async def ask_questions(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.chat.id
    try:
        async with working(message, STAGES_CLARIFY):
            async with SessionLocal() as s:
                from sqlalchemy import select

                u = (await s.execute(select(User).where(User.tg_id == user_id))).scalar_one()
                t = await s.get(Target, data["target_id"])
                qs = await intake.clarify(s, u, t)
                await s.commit()
                before = t.coverage
    except Exception as e:
        await _error(message, e, "clarify")
        qs, before = [], None
    if before and before.get("req_total"):  # «своё резюме против вакансии» до генерации
        miss = [r["requirement"] for r in before["requirements"] if r["status"] == "missing"]
        await message.answer(f"📊 Ваше резюме сейчас закрывает <b>{before['req_covered']} из {before['req_total']}</b> "
                             "требований вакансии." + ("\nНе видно: " + html.escape("; ".join(miss[:4])) if miss else "")
                             + "\nПосле генерации покажу, сколько стало.")
    if not qs:
        await ask_items(message, state)
        return
    await state.set_state(CV.answers)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="cv:noanswers")]])
    await message.answer("<b>Шаг 3.</b> Несколько уточнений, чтобы резюме попало точнее. Ответьте одним сообщением "
                         "или голосом:\n\n" + "\n".join(f"{i}. {html.escape(q['question'])}" for i, q in enumerate(qs, 1)),
                         reply_markup=kb)


async def take_answers(message: Message, state: FSMContext, text: str):
    data = await state.get_data()
    user = await _user(message.from_user)
    try:
        async with working(message, STAGES_ANSWERS):
            async with SessionLocal() as s:
                await intake.answer(s, await s.get(User, user.id), await s.get(Target, data["target_id"]), text)
                await s.commit()
    except Exception as e:
        await _error(message, e, "answers")
        return
    await ask_items(message, state)


@router.callback_query(F.data == "cv:noanswers")
async def on_no_answers(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await ask_items(cb.message, state)


# ---------- Шаг 4. Состав пакета ----------

async def ask_items(message: Message, state: FSMContext):
    await state.set_state(CV.items)
    data = await state.get_data()
    await message.answer("<b>Шаг 4.</b> Что подготовить? Отметьте нужное.", reply_markup=items_kb(data.get("items", [])))


@router.callback_query(F.data.startswith("cvi:"))
async def on_item(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    item = cb.data.split(":", 1)[1]
    if item == "next":
        if not data.get("items"):
            await cb.answer("Отметьте хотя бы один документ", show_alert=True)
            return
        await cb.answer()
        await ask_scheme(cb.message, state)
        return
    items = toggle(data.get("items", []), item)
    await state.update_data(items=items)
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=items_kb(items))


# ---------- Шаг 5. Схема и фото ----------

async def ask_scheme(message: Message, state: FSMContext):
    await state.set_state(CV.scheme)
    await message.answer_photo(BufferedInputFile(schemes_preview(), "schemes.png"),
                               caption="<b>Шаг 5.</b> Выберите цветовую схему (слева направо, как в списке).",
                               reply_markup=scheme_kb())


@router.callback_query(F.data.startswith("cvs:"))
async def on_scheme(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":", 1)[1]
    if key not in cv_render.SCHEMES:
        await cb.answer()
        return
    await state.update_data(scheme=key)
    await cb.answer(cv_render.SCHEMES[key].title)
    data = await state.get_data()
    if not ({"cv", "one_page"} & set(data.get("items", []))):  # только письмо: фото не нужно
        await generate(cb.message, state, cb.from_user)
        return
    await state.set_state(CV.photo)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Без фото", callback_data="cv:nophoto")],
                                               [InlineKeyboardButton(text="Оставить прежнее фото", callback_data="cv:keepphoto")]])
    await cb.message.answer("<b>Шаг 6.</b> Пришлите фото для резюме (необязательно). В ИИ оно не отправляется.",
                            reply_markup=kb)


@router.message(CV.photo, F.photo)
async def on_photo(message: Message, state: FSMContext, bot: Bot):
    import uuid

    user = await _user(message.from_user)
    buf = await bot.download(message.photo[-1])
    async with SessionLocal() as s:
        c = await profile.get_contact(s, await s.get(User, user.id))
        path = get_settings().files_dir / f"{user.id}_photo_{uuid.uuid4().hex}.jpg"
        path.write_bytes(buf.read())
        c.photo_path = str(path)
        await s.commit()
    await message.reply("📸 Фото принял: оно будет в левом верхнем углу резюме. В ИИ фото не отправляется.")
    await generate(message, state, message.from_user)


@router.callback_query(F.data.in_({"cv:nophoto", "cv:keepphoto"}))
async def on_no_photo(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    user = await _user(cb.from_user)
    async with SessionLocal() as s:
        c = await profile.get_contact(s, await s.get(User, user.id))
        if cb.data == "cv:nophoto":
            c.photo_path = None
            await s.commit()
        photo = _photo(c)
    if cb.data == "cv:keepphoto":
        if photo:  # показываем, какое именно фото останется
            await cb.message.answer_photo(BufferedInputFile(photo, "photo.jpg"), caption="Оставляю это фото.")
        else:
            await cb.message.answer("Прежнего фото нет, резюме будет без фото.")
    else:
        await cb.message.answer("Резюме будет без фото.")
    await generate(cb.message, state, cb.from_user)


# ---------- Шаг 6. Генерация, файлы, правка ----------

async def generate(message: Message, state: FSMContext, tg_user, instruction: str | None = None):
    data = await state.get_data()
    if not llm.available():
        await message.answer("🤖 <i>Генерация резюме работает на ИИ, а ИИ пока не подключён.</i>")
        return
    user = await _user(tg_user)
    try:
        async with working(message, STAGES_TAILOR, every=4.0):
            res = await _run(tailor_job(user.id, data["target_id"], data.get("items") or list(PACKAGE_ITEMS),
                                        data.get("scheme", "graphite"), instruction))
    except Exception as e:
        await _error(message, e, "tailor")
        return
    await state.update_data(package_id=res["package_id"])
    await state.set_state(None)
    await send_files(message, res["package_id"])


def _photo(contact: Contact | None) -> bytes | None:
    from pathlib import Path

    p = Path(contact.photo_path) if contact and contact.photo_path else None
    return p.read_bytes() if p and p.exists() else None


async def send_files(message: Message, package_id: int, accept_flags: bool = False):
    async with SessionLocal() as s:
        pkg = await s.get(Package, package_id)
        contact = await s.get(Contact, pkg.user_id)
        vacancy = await s.get(Vacancy, pkg.vacancy_id)
    photo = _photo(contact)
    cv_flags = [f for f in pkg.fact_flags or [] if f.get("where") == "cv"]
    letter_flags = [f for f in pkg.fact_flags or [] if f.get("where") != "cv"]
    if cv_flags and not accept_flags:
        await message.answer("ℹ️ Убрал из резюме то, что не подтверждается вашим профилем:\n" + "\n".join(
            f"• {html.escape(f['text'])}" for f in cv_flags))
    for item in pkg.items or []:
        if item == "letter" and letter_flags and not accept_flags:
            continue
        try:
            data = export.render_item(pkg, contact, item, photo, accept_flags=accept_flags,
                                      company=vacancy.company_name, position=vacancy.title)
        except Exception as e:
            await _error(message, e, f"render {item}")
            continue
        name = {"cv": "Резюме", "one_page": "Резюме_1_страница", "letter": "Сопроводительное_письмо"}[item]
        await message.answer_document(BufferedInputFile(data, f"{name}_v{pkg.version}.pdf"))
    kb = []
    if "letter" in (pkg.items or []) and letter_flags and not accept_flags:
        await message.answer("⚠️ В письме есть утверждения, которых нет в вашем профиле:\n" + "\n".join(
            f"• {html.escape(f['text'])} — {html.escape(f['reason'])}" for f in letter_flags)
            + "\n\nЕсли это правда, подтвердите, и я пришлю письмо. Если нет, внесите правку.")
        kb.append([InlineKeyboardButton(text="Это правда, прислать письмо", callback_data=f"cva:{pkg.id}")])
    from app.services.tailor import revisions

    async with SessionLocal() as s:
        target = await s.get(Target, pkg.target_id) if pkg.target_id else None
        manual_used, improve_left = await revisions(s, pkg.user_id, pkg.target_id) if pkg.target_id else (True, 0)
    if pkg.coverage:  # проверка после генерации и предложение перегенерировать
        await message.answer(coverage_text(pkg.coverage, target.coverage if target else None))
        if improve_left > 0:
            kb.append([InlineKeyboardButton(text="🔁 Перегенерировать с учётом пробелов", callback_data=f"cvi2:{pkg.id}")])
            if any(r["status"] != "covered" and not r["in_profile"] for r in pkg.coverage.get("requirements", [])):
                kb.append([InlineKeyboardButton(text="✍️ Добавить факты и перегенерировать", callback_data=f"cvf:{pkg.id}")])
    if not manual_used:
        kb.append([InlineKeyboardButton(text="✏️ Внести правку (одна)", callback_data=f"cvr:{pkg.id}")])
    kb.append([InlineKeyboardButton(text="🎨 Другая цветовая схема", callback_data=f"cvc:{pkg.id}")])
    kb.append([InlineKeyboardButton(text="Новая вакансия", callback_data="cv:new")])
    await message.answer("Готово. Что дальше?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


def coverage_text(cov: dict, before: dict | None) -> str:
    """Отчёт о покрытии: сколько требований закрыто, было/стало, что не покрыто и почему."""
    total = cov.get("req_total") or 0
    lines = []
    if total:
        was = (f" (в исходном резюме было {before['req_covered']} из {before['req_total']})"
               if before and before.get("req_total") else "")
        part = f", частично ещё {cov['req_partial']}" if cov.get("req_partial") else ""
        lines.append(f"📊 <b>Покрытие требований: {cov['req_covered']} из {total}</b>{part}{was}")
    if cov.get("kw_total"):
        lines.append(f"Ключевые слова вакансии: {len(cov['keywords_covered'])} из {cov['kw_total']}")
    gaps = [r for r in cov.get("requirements", []) if r["status"] != "covered"]
    if gaps:
        lines.append("\n<b>Не покрыто или покрыто частично:</b>")
        for r in gaps[:6]:
            why = ("есть в вашем профиле, но в резюме отражено слабо: можно перегенерировать" if r["in_profile"]
                   else "в вашем профиле этого нет" + (f". {r['question']}" if r.get("question") else ""))
            mark = "◐" if r["status"] == "partial" else "○"
            lines.append(f"{mark} {html.escape(r['requirement'])} — <i>{html.escape(why)}</i>")
    elif total:
        lines.append("Все требования вакансии подтверждены фактами из профиля.")
    if cov.get("keywords_missing"):
        lines.append("\nНе встретились слова: " + html.escape(", ".join(cov["keywords_missing"][:8])))
    lines.append("\nМожно перегенерировать: выдумывать не буду, только точнее подам ваш опыт.")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("cvi2:"))
async def on_improve(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    pkg = await _own(cb, int(cb.data.split(":")[1]))
    if pkg:
        await run_improve(cb.message, state, cb.from_user, pkg.id, None)


@router.callback_query(F.data.startswith("cvf:"))
async def on_facts(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    pkg = await _own(cb, int(cb.data.split(":")[1]))
    if not pkg:
        return
    gaps = [r for r in (pkg.coverage or {}).get("requirements", []) if r["status"] != "covered" and not r["in_profile"]]
    await state.update_data(package_id=pkg.id)
    await state.set_state(CV.facts)
    await cb.message.answer("Расскажите, какой опыт у вас есть по этим требованиям. Только правду: я проверю каждый "
                            "факт и добавлю в профиль.\n\n" + "\n".join(
                                f"• {html.escape(r['question'] or r['requirement'])}" for r in gaps[:6]))


async def take_facts(message: Message, state: FSMContext, text: str):
    data = await state.get_data()
    await run_improve(message, state, message.from_user, data["package_id"], text)


async def run_improve(message: Message, state: FSMContext, tg_user, package_id: int, facts: str | None):
    if not llm.available():
        await message.answer("🤖 <i>Перегенерация работает на ИИ, а ИИ пока не подключён.</i>")
        return
    user = await _user(tg_user)
    stages = (["Добавляю ваши факты в профиль"] if facts else []) + ["Закрываю пробелы", "Проверяю каждый факт",
                                                                     "Считаю покрытие", "Собираю PDF"]
    try:
        async with working(message, stages, every=4.0):
            res = await _run(improve_job(user.id, package_id, facts))
    except Exception as e:
        await _error(message, e, "improve")
        return
    await state.update_data(package_id=res["package_id"])
    await state.set_state(None)
    await send_files(message, res["package_id"])


@router.message(Command("check"))
async def check_resume(message: Message):
    """Проверка резюме без вакансии: оценка и конкретные правки."""
    from sqlalchemy import select

    from app.models import Source
    from app.services import review

    user = await _user(message.from_user)
    async with SessionLocal() as s:
        rows = (await s.execute(select(Source).where(Source.user_id == user.id, Source.status == "parsed")
                                .order_by(Source.id.desc()))).scalars().all()
        c = await s.get(Contact, user.id)
    files = [x for x in rows if x.kind == "file"]
    text = files[0].text if files else "\n\n".join(x.text or "" for x in rows)
    if not (text or "").strip():
        await message.answer("Сначала пришлите резюме файлом, сканом или фото, потом /check.")
        return
    try:
        async with working(message, ["Читаю резюме", "Проверяю цифры, даты и структуру", "Готовлю советы"]):
            r = await review.review(text, c.full_name if c else None)
    except Exception as e:
        await _error(message, e, "review")
        return
    lines = [f"📝 <b>Оценка резюме: {r['score']} из 100</b>"]
    lines += [f"{'✅' if x['ok'] else '⚠️'} {html.escape(x['title'])}: {html.escape(x['note'])}" for x in r["checks"]]
    if r["advice"]:
        lines.append("\n<b>Что поправить:</b>")
        lines += [f"{i}. {html.escape(a['problem'])}\n   → {html.escape(a['fix'])}" for i, a in enumerate(r["advice"], 1)]
    lines.append("\nЧтобы сделать резюме под конкретную вакансию: /new")
    await message.answer("\n".join(lines))


async def _own(cb: CallbackQuery, package_id: int) -> Package | None:
    user = await _user(cb.from_user)
    async with SessionLocal() as s:
        pkg = await s.get(Package, package_id)
    return pkg if pkg and pkg.user_id == user.id else None


@router.callback_query(F.data.startswith("cva:"))
async def on_accept(cb: CallbackQuery):
    await cb.answer()
    pkg = await _own(cb, int(cb.data.split(":")[1]))
    if pkg:
        async with SessionLocal() as s:
            contact = await s.get(Contact, pkg.user_id)
            vacancy = await s.get(Vacancy, pkg.vacancy_id)
        data = export.render_item(pkg, contact, "letter", accept_flags=True, company=vacancy.company_name,
                                  position=vacancy.title)
        await cb.message.answer_document(BufferedInputFile(data, f"Сопроводительное_письмо_v{pkg.version}.pdf"))


@router.callback_query(F.data.startswith("cvr:"))
async def on_revise(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    pkg = await _own(cb, int(cb.data.split(":")[1]))
    if not pkg:
        return
    await state.update_data(package_id=pkg.id, target_id=pkg.target_id, items=pkg.items, scheme=pkg.scheme)
    await state.set_state(CV.revise)
    await cb.message.answer("Напишите или наговорите, что поправить. Правка одна, поэтому лучше всё сразу. "
                            "Например: «короче summary, акцент на B2B, письмо менее формально».")


async def take_revision(message: Message, state: FSMContext, text: str):
    await generate(message, state, message.from_user, instruction=text.strip())


@router.callback_query(F.data.startswith("cvc:"))
async def on_change_scheme(cb: CallbackQuery):
    await cb.answer()
    pkg = await _own(cb, int(cb.data.split(":")[1]))
    if pkg:
        await cb.message.answer_photo(BufferedInputFile(schemes_preview(), "schemes.png"), caption="Выберите схему:",
                                      reply_markup=scheme_kb(f"cvx:{pkg.id}"))


@router.callback_query(F.data.startswith("cvx:"))
async def on_rerender(cb: CallbackQuery):
    """Смена схемы без ИИ: перерисовываем те же документы."""
    _, pid, key = cb.data.split(":")
    pkg = await _own(cb, int(pid))
    if not pkg or key not in cv_render.SCHEMES:
        await cb.answer()
        return
    await cb.answer(cv_render.SCHEMES[key].title)
    async with SessionLocal() as s:
        p = await s.get(Package, pkg.id)
        p.scheme = key
        await s.commit()
    await send_files(cb.message, pkg.id)


@router.callback_query(F.data == "cv:new")
async def on_new(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await ask_vacancy(cb.message, state)


# ---------- Текст и голос по шагам ----------

async def handle_text(message: Message, state: FSMContext, text: str, kind: str = "text") -> bool:
    """Текст или распознанный голос в зависимости от шага. False: шаг не ждёт текста, пусть решает вызывающий."""
    st = await state.get_state()
    if st == CV.vacancy.state:
        await take_vacancy(message, state, text)
    elif st == CV.company.state:
        await take_company(message, state, text)
    elif st == CV.answers.state:
        await take_answers(message, state, text)
    elif st == CV.revise.state:
        await take_revision(message, state, text)
    elif st == CV.name.state:
        await take_name(message, state, text)
    elif st == CV.facts.state:
        await take_facts(message, state, text)
    elif st in (CV.items.state, CV.scheme.state, CV.photo.state):
        await message.answer("Выберите вариант кнопками выше.")
    else:
        return False
    return True
