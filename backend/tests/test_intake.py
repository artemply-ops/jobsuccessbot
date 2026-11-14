"""Этап 2: вакансия от пользователя, досье, уточнения, распознавание сканов. ТЕСТОВЫЕ ДАННЫЕ: компании,
люди и контакты ниже вымышленные. Тесты не ходят в сеть: страницы подаются строками, LLM подменяется."""

import datetime
import json

import pytest

from app.services import intake, ocr
from app.services.package import about_links
from app.services.pii import has_pii, scrub_contacts

VACANCY_TEXT = """Руководитель цифровых продуктов
Компания: Тестовый Банк
Задачи: развитие мобильного приложения, управление командой из 12 человек, unit-экономика продукта.
Требования: опыт управления продуктом от 5 лет, SQL, A/B-тесты.
Будет плюсом: опыт в медтехе.
Резюме присылайте на hr.test@example.com или звоните 8 (900) 000-00-00, спросить Марию Тестовую."""


def test_scrub_contacts_catches_tel_links():
    """Причина PIILeakError в разборе карьерных страниц: телефоны в ссылках tel: не вычищались."""
    links = "Позвонить -> tel:+79000000000\nНаписать -> mailto:hr.test@example.com\nВакансии -> https://x.test/jobs"
    out = scrub_contacts(links)
    assert not has_pii(out) and "https://x.test/jobs" in out and "[CONTACT]" in out


def test_hh_links_refused():
    for url in ("https://hh.ru/vacancy/1", "spb.hh.ru/vacancy/2", "https://www.headhunter.kz/vacancy/3"):
        assert intake.is_hh(intake.normalize_url(url))
    assert not intake.is_hh("https://career.example.com/job/1")


def test_company_domain_from_url():
    assert intake.company_domain_from("https://career.example.ru/vacancy/1") == "example.ru"
    assert intake.company_domain_from("https://www.example.com/jobs/1") == "example.com"
    assert intake.company_domain_from("https://jobs.lever.co/example/1") is None
    assert intake.company_domain_from("https://hh.ru/vacancy/1") is None


def test_page_from_html_prefers_jobposting():
    html = """<html><head><title>Вакансии</title><script type="application/ld+json">""" + json.dumps({
        "@context": "https://schema.org", "@type": "JobPosting", "title": "Продакт-менеджер",
        "description": "<p>Развивать продукт.</p><p>Опыт от 3 лет.</p>",
        "hiringOrganization": {"@type": "Organization", "name": "Тестовая компания", "sameAs": "https://example.ru"},
    }) + """</script></head><body><nav>Меню</nav><main>Другое</main></body></html>"""
    page = intake.page_from_html(html, "https://career.example.ru/job/1")
    assert page.title == "Продакт-менеджер" and page.company_name == "Тестовая компания"
    assert "Опыт от 3 лет" in page.text and page.company_url == "https://example.ru"


def test_page_from_html_main_text_without_menu():
    html = "<html><body><nav>Меню сайта</nav><main><h1>Аналитик</h1><p>Задачи и требования</p></main>" \
           "<footer>Подвал</footer></body></html>"
    page = intake.page_from_html(html, "https://example.ru/job")
    assert "Аналитик" in page.text and "Меню" not in page.text and "Подвал" not in page.text


def test_about_links_same_site_only():
    html = '<a href="/o-kompanii">О компании</a><a href="/values">Наши ценности</a>' \
           '<a href="https://other.test/about">О партнёре</a><a href="/shop">Магазин</a>'
    links = about_links(html, "https://example.ru/")
    assert links == ["https://example.ru/o-kompanii", "https://example.ru/values"]


async def test_fetch_page_refuses_hh_before_network():
    with pytest.raises(intake.IntakeError, match="hh.ru"):
        await intake.fetch_page("https://hh.ru/vacancy/123")


async def test_add_target_without_ai_stores_clean_vacancy(session):
    """Без ИИ: вакансия принимается, контакты рекрутера не сохраняются, уточнений нет."""
    from app.models import User, Vacancy

    user = User(tg_id=901, consent_at=datetime.datetime.now())
    session.add(user)
    await session.flush()
    t = await intake.add_target(session, user, text=VACANCY_TEXT, company_url="https://career.example.ru/")
    v = await session.get(Vacancy, t.vacancy_id)
    assert t.parsed["title"] == "Руководитель цифровых продуктов" and t.company_domain == "example.ru"
    assert not has_pii(v.description) and "hr.test@example.com" not in v.description
    assert await intake.clarify(session, user, t) == [] and await intake.dossier(session, t) is None
    with pytest.raises(intake.IntakeError):
        await intake.add_target(session, user, text="коротко")
    with pytest.raises(intake.IntakeError, match="hh.ru"):
        await intake.add_target(session, user, url="https://hh.ru/vacancy/1")


async def test_clarify_and_answer_with_mocked_llm(session, monkeypatch):
    """С ИИ: имя рекрутера обезличено до LLM, вопросов не больше трёх, ответы попадают в профиль."""
    import httpx

    from app.config import get_settings
    from app.models import Source, User
    from app.services import llm
    from app.services.profile import save_basic

    sent = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        sent.append(body["messages"][1]["content"])
        name = body["response_format"]["json_schema"]["name"]
        if name == "VacancyParsed":
            out = {"title": "Руководитель цифровых продуктов", "company_name": "Тестовый Банк", "company_site": "",
                   "city": "", "remote": None, "salary_from": None, "salary_to": None, "currency": "",
                   "seniority": "head", "responsibilities": ["Развитие приложения"], "language": "ru",
                   "requirements": ["Управление продуктом от 5 лет", "SQL"], "nice_to_have": [], "keywords": ["SQL"]}
        elif name == "ClarifyQuestions":
            out = {"questions": [{"field": f"req{i}", "question": f"Вопрос {i}?"} for i in range(4)]}
        else:  # ProfileExtraction при обновлении профиля ответами
            out = {"profile": {"headline": "Продакт", "target_roles": [], "seniority": "", "summary": "", "skills": [],
                               "experience": [], "industries": [], "education": [], "languages": [],
                               "expectations": {"salary_min": None, "currency": "RUB", "cities": [], "formats": [],
                                                "relocation": False}, "values": [], "stop_factors": [],
                               "writing_style": ""}, "questions": []}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                      "message": {"content": json.dumps(out, ensure_ascii=False)}}]})

    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(llm, "_client", httpx.AsyncClient(base_url="https://or.test", transport=httpx.MockTransport(handler)))
    try:
        user = User(tg_id=902, consent_at=datetime.datetime.now())
        session.add(user)
        await session.flush()
        await save_basic(session, user, "Руководитель продукта, Москва")
        t = await intake.add_target(session, user, text=VACANCY_TEXT)
        assert "Мари" not in sent[0] and not has_pii(sent[0])
        qs = await intake.clarify(session, user, t)
        assert len(qs) == 3
        await intake.answer(session, user, t, "Да, вёл SQL-аналитику сам, команда 12 человек.")
        assert t.answered
        src = (await session.execute(__import__("sqlalchemy").select(Source).where(Source.user_id == user.id))).scalars().all()
        assert any("команда 12 человек" in (s.text or "") for s in src)
    finally:
        monkeypatch.setattr(llm, "_client", None)


def _scan():
    """Тестовый скан: тёмное «фото» в углу, крупное имя, контакты и строка опыта. Всё вымышленное."""
    import io

    from PIL import Image, ImageDraw

    im = Image.new("RGB", (800, 1100), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((20, 20, 180, 200), fill=(60, 60, 60))  # фото
    for x, y in [(220, 40), (330, 40), (220, 120), (420, 120), (220, 300), (330, 300)]:
        d.rectangle((x, y, x + 90, y + 30), fill=(10, 10, 10))  # «слова»
    buf = io.BytesIO()
    im.save(buf, "PNG")
    words = [ocr.Word("Анна", 220, 40, 90, 40, (1, 1, 1)), ocr.Word("Тестова", 330, 40, 90, 40, (1, 1, 1)),
             ocr.Word("anna.test@example.com", 220, 120, 180, 14, (1, 1, 2)),
             ocr.Word("+7", 420, 120, 20, 14, (1, 1, 2)), ocr.Word("900", 445, 120, 30, 14, (1, 1, 2)),
             ocr.Word("000-00-00", 480, 120, 70, 14, (1, 1, 2)),
             ocr.Word("Руководитель", 220, 300, 100, 14, (2, 1, 1)), ocr.Word("продукта", 330, 300, 90, 14, (2, 1, 1))]
    return buf.getvalue(), words


def test_ocr_masking_hides_pii_and_photo():
    import io

    from PIL import Image

    png, words = _scan()
    labels = ocr.find_pii(words, 1100)
    assert {labels[0], labels[1]} == {"[CANDIDATE]"} and labels[2] == "[EMAIL]"
    assert {labels[3], labels[4], labels[5]} == {"[PHONE]"} and 6 not in labels and 7 not in labels
    vault = ocr.vault_from(words, labels)
    assert vault["[CANDIDATE]"] == "Анна Тестова" and vault["[EMAIL]"] == "anna.test@example.com"
    masked = Image.open(io.BytesIO(ocr.masked_png(png, words, labels)))
    assert masked.getpixel((100, 100)) == (255, 255, 255)  # фото не перенесено
    assert masked.getpixel((260, 50)) == (200, 200, 200)  # имя под плашкой
    assert masked.getpixel((250, 310)) == (10, 10, 10)  # обычное слово перенесено
    assert ocr.restore_marks("[CANDIDATE], [EMAIL]", vault) == "Анна Тестова, anna.test@example.com"


async def test_ocr_sends_only_masked_copy_to_openrouter(monkeypatch):
    import base64
    import io

    import httpx
    from PIL import Image

    from app.config import get_settings
    from app.services import llm

    png, words = _scan()
    sent = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        sent.append(body)
        text = "[CANDIDATE]\n[EMAIL] [PHONE]\nРуководитель продукта"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                      "message": {"content": json.dumps({"text": text})}}]})

    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(llm, "_client", httpx.AsyncClient(base_url="https://or.test", transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ocr, "find_words", lambda _: words)
    try:
        text, vault = await ocr.recognize_page(png)
    finally:
        monkeypatch.setattr(llm, "_client", None)
    content = sent[0]["messages"][1]["content"]
    assert sent[0]["model"] == get_settings().llm_model_vision
    assert "Анна" not in json.dumps(content, ensure_ascii=False)
    img = Image.open(io.BytesIO(base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])))
    assert img.getpixel((100, 100)) == (255, 255, 255)
    assert text.startswith("Анна Тестова\nanna.test@example.com +7 900 000-00-00")


async def test_scan_upload_gives_clear_error_without_tesseract(session, monkeypatch):
    """Tesseract не установлен: источник с понятной ошибкой, а не падение."""
    import pymupdf

    from app.models import User
    from app.services.profile import add_file_source

    monkeypatch.setattr(ocr, "available", lambda: False)
    doc = pymupdf.open()
    doc.new_page()  # пустая страница = PDF без текстового слоя
    user = User(tg_id=903, consent_at=datetime.datetime.now())
    session.add(user)
    await session.flush()
    src = await add_file_source(session, user, "scan.pdf", doc.tobytes())
    assert src.status == "error" and "распознавание" in src.error


async def test_ocr_limits_pages(monkeypatch):
    import pymupdf

    doc = pymupdf.open()
    for _ in range(6):
        doc.new_page()
    seen = []

    async def fake(png: bytes, name=None):
        assert png[1:4] == b"PNG"
        seen.append(1)
        return f"стр {len(seen)}", {"[CANDIDATE]": "Анна Тестова"}

    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "recognize_page", fake)
    text, name = await ocr.recognize("cv.pdf", doc.tobytes())
    assert text == "стр 1\nстр 2\nстр 3\nстр 4" and name == "Анна Тестова"  # не больше MAX_PAGES


def test_strict_schema_keeps_field_named_title():
    """Регрессия 14.11: поле title (название вакансии) вычищалось вместе со служебными title, модель его не видела."""
    from app.schemas import VacancyParsed
    from app.services.llm import strict_schema
    from app.services.sources.careers import _HtmlVacancies

    s = strict_schema(VacancyParsed)
    assert "title" in s["properties"] and "title" in s["required"] and "title" not in s
    assert s["properties"]["title"]["type"] == "string" and "title" not in s["properties"]["title"]
    h = strict_schema(_HtmlVacancies)
    item = h["$defs"]["_HtmlVacancy"]
    assert "title" in item["properties"] and "title" in item["required"]
