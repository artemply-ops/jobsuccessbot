"""Этап 4: сценарий бота без Telegram и без ИИ. ТЕСТОВЫЕ ДАННЫЕ: люди и компании вымышленные."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import cv_flow


def test_toggle_items_any_subset():
    assert cv_flow.toggle([], "letter") == ["letter"]
    assert cv_flow.toggle(["cv", "letter"], "cv") == ["letter"]
    assert cv_flow.toggle(["letter"], "cv") == ["cv", "letter"]
    assert cv_flow.toggle(["cv"], "all") == ["cv", "one_page", "letter"]
    kb = cv_flow.items_kb(["cv"])
    assert kb.inline_keyboard[0][0].text.startswith("☑") and kb.inline_keyboard[2][0].text.startswith("☐")


def test_is_url():
    assert cv_flow.is_url("https://career.example.ru/job/1") and cv_flow.is_url("example.ru/jobs/2")
    assert not cv_flow.is_url("Руководитель продукта. Требования: SQL, опыт от 5 лет")


def test_schemes_preview_is_png_with_all_schemes():
    png = cv_flow.schemes_preview()
    assert png[1:4] == b"PNG" and cv_flow.schemes_preview() is png  # считается один раз


def _ctx(chat_id: int) -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))


def _msg(chat_id: int):
    return SimpleNamespace(from_user=SimpleNamespace(id=chat_id, username="test", first_name="Анна"),
                           chat=SimpleNamespace(id=chat_id), answer=AsyncMock(), answer_photo=AsyncMock())


async def test_vacancy_step_without_ai_goes_to_company_then_items(session):
    """Текст вакансии -> разбор -> вопрос про сайт -> (без ИИ уточнений нет) -> выбор документов."""
    from app.db import init_db

    await init_db()
    state, msg = _ctx(5001), _msg(5001)
    await state.set_state(cv_flow.CV.vacancy)
    text = ("Руководитель продукта\nКомпания: Тестовый Банк\nЗадачи: развитие приложения.\n"
            "Требования: управление продуктом от 5 лет, SQL. Пишите hr.test@example.com")
    assert await cv_flow.handle_text(msg, state, text)
    assert await state.get_state() == cv_flow.CV.company.state
    assert "Руководитель продукта" in msg.answer.await_args_list[1].args[0]
    assert await cv_flow.handle_text(msg, state, "testbank.example.ru")
    assert await state.get_state() == cv_flow.CV.items.state
    data = await state.get_data()
    assert data["items"] == ["cv", "one_page", "letter"] and data["target_id"]


async def test_hh_link_in_vacancy_step_explains(session):
    from app.db import init_db

    await init_db()
    state, msg = _ctx(5002), _msg(5002)
    await state.set_state(cv_flow.CV.vacancy)
    await cv_flow.handle_text(msg, state, "https://hh.ru/vacancy/123")
    assert "hh.ru" in msg.answer.await_args_list[-1].args[0]
    assert await state.get_state() == cv_flow.CV.vacancy.state  # остаёмся на шаге вакансии


async def test_text_outside_flow_not_handled():
    state, msg = _ctx(5003), _msg(5003)
    assert not await cv_flow.handle_text(msg, state, "просто текст")
    await state.set_state(cv_flow.CV.scheme)
    assert await cv_flow.handle_text(msg, state, "текст")
    assert "кнопками" in msg.answer.await_args.args[0]


def test_errors_go_to_rotating_file(tmp_path, monkeypatch):
    from app import logs
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "log_dir", tmp_path)
    monkeypatch.setattr(logs, "_done", False)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logs.setup_logging()
        logging.getLogger("test").info("info не пишется в файл")
        logging.getLogger("test").error("ошибка пишется")
        for h in root.handlers:
            h.flush()
        text = (tmp_path / "errors.log").read_text(encoding="utf-8")
        assert "ошибка пишется" in text and "info не пишется" not in text
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
