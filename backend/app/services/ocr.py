"""Распознавание сканов и фото резюме. Обезличивание на нашем сервере, чтение текста моделью через OpenRouter.

Как это работает:
1. Локально (Tesseract) находим слова и их рамки. Наружу ничего не уходит.
2. Ищем ПДн: почта, телефон, ссылки, имя кандидата (крупная строка в шапке + NER), имена третьих лиц.
3. Собираем обезличенную копию страницы: белый лист, на нём только вырезки слов без ПДн; на месте ПДн
   серая плашка с меткой ([CANDIDATE], [EMAIL], [PHONE], [LINK_1], [PERSON_1]). Фото, подписи, печати
   и любая графика в копию не попадают: переносятся только найденные слова.
4. Копию читает vision-модель OpenRouter (LLM_MODEL_VISION, меняется в .env без правки кода).
5. Разметка «метка -> исходное значение» остаётся у нас: метки в ответе модели заменяем обратно,
   сырой текст хранится только в sources.text, как у обычного файла.
Без ключа OpenRouter работает только локальный Tesseract (качество ниже, но сценарий не стоит).
"""

import io
import re
from dataclasses import dataclass

from pydantic import BaseModel

from app.config import get_settings
from app.services import llm
from app.services.pii import EMAIL_RE, PHONE_RE, URL_RE, _person_spans

MAX_PAGES = 4  # резюме длиннее 4 страниц не бывает, а каждая страница это отдельный вызов модели
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
NO_TESSERACT = ("Это скан или фото, а распознавание на сервере пока не настроено. "
                "Пришлите резюме в DOCX, PDF с текстом или текстом.")

SYSTEM_OCR = """Перед тобой обезличенная копия страницы резюме: остались только слова, личные данные закрыты
серыми плашками с метками вида [CANDIDATE], [EMAIL], [PHONE], [LINK_1], [PERSON_1].
Перепиши текст страницы точно, в порядке чтения, сохраняя разбивку на строки, разделы и списки.
Метки переноси как есть. Ничего не добавляй, не исправляй и не додумывай закрытое."""


class OCRError(RuntimeError):
    pass


class OCRText(BaseModel):
    text: str


@dataclass
class Word:
    text: str
    x: int
    y: int
    w: int
    h: int
    line: tuple  # (block, par, line): слова одной строки


def available() -> bool:
    """Локальный Tesseract установлен (в Docker-образе ставится вместе с русским словарём)."""
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def pdf_pages_png(data: bytes, dpi: int = 200) -> list[bytes]:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return [p.get_pixmap(dpi=dpi).tobytes("png") for p in list(doc)[:MAX_PAGES]]
    finally:
        doc.close()


def image_to_png(data: bytes) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def find_words(png: bytes) -> list[Word]:
    import pytesseract
    from PIL import Image

    d = pytesseract.image_to_data(Image.open(io.BytesIO(png)), lang="rus+eng", output_type=pytesseract.Output.DICT)
    out = []
    for i, t in enumerate(d["text"]):
        if t and t.strip() and float(d["conf"][i]) > 30:
            out.append(Word(t.strip(), d["left"][i], d["top"][i], d["width"][i], d["height"][i],
                            (d["block_num"][i], d["par_num"][i], d["line_num"][i])))
    return out


def _lines(words: list[Word]) -> list[list[int]]:
    order: dict[tuple, list[int]] = {}
    for i, w in enumerate(words):
        order.setdefault(w.line, []).append(i)
    return list(order.values())


def _name_line(words: list[Word], page_h: int) -> list[int]:
    """Имя кандидата: самая крупная строка в верхней трети из 2-3 слов-букв (как правило, это ФИО)."""
    best, best_h = [], 0
    for idx in _lines(words):
        ws = [words[i] for i in idx]
        if not 2 <= len(ws) <= 3 or min(w.y for w in ws) > page_h / 3:
            continue
        if not all(re.fullmatch(r"[A-Za-zА-Яа-яЁё\-]{2,}", w.text) for w in ws):
            continue
        h = sum(w.h for w in ws) / len(ws)
        if h > best_h:
            best, best_h = idx, h
    return best


def find_pii(words: list[Word], page_h: int, known_name: str | None = None) -> dict[int, str]:
    """Индекс слова -> метка. Телефон и имя могут занимать несколько слов подряд."""
    labels: dict[int, str] = {}
    persons: dict[str, str] = {}
    links: dict[str, str] = {}
    for idx in _lines(words):
        text, spans = "", []
        for i in idx:
            start = len(text) + (1 if text else 0)
            text = f"{text} {words[i].text}" if text else words[i].text
            spans.append((start, len(text), i))

        def mark(a: int, b: int, label: str):
            for s, e, i in spans:
                if s < b and e > a:
                    labels[i] = label

        for m in EMAIL_RE.finditer(text):
            mark(m.start(), m.end(), "[EMAIL]")
        for m in PHONE_RE.finditer(text):
            mark(m.start(), m.end(), "[PHONE]")
        for m in URL_RE.finditer(text):
            if m.group(0).startswith("@") and EMAIL_RE.search(text[max(0, m.start() - 40):m.end()]):
                continue
            links.setdefault(m.group(0), f"[LINK_{len(links) + 1}]")
            mark(m.start(), m.end(), links[m.group(0)])
        for a, b in _person_spans(text):
            chunk = text[a:b]
            if known_name and any(p.lower() in chunk.lower() for p in known_name.split() if len(p) > 2):
                mark(a, b, "[CANDIDATE]")
            else:
                persons.setdefault(chunk, f"[PERSON_{len(persons) + 1}]")
                mark(a, b, persons[chunk])
    for i in _name_line(words, page_h):
        labels[i] = "[CANDIDATE]"
    # NER пометил имя кандидата как постороннее лицо: та же строка, что и крупное имя в шапке
    cand = {words[i].text.lower() for i, lb in labels.items() if lb == "[CANDIDATE]"}
    for i, lb in list(labels.items()):
        if lb.startswith("[PERSON_") and words[i].text.lower() in cand:
            labels[i] = "[CANDIDATE]"
    return labels


def vault_from(words: list[Word], labels: dict[int, str]) -> dict[str, str]:
    """Разметка «метка -> исходное значение». Хранится у нас, наружу не уходит."""
    out: dict[str, list[str]] = {}
    for i in sorted(labels):
        out.setdefault(labels[i], []).append(words[i].text)
    return {k: " ".join(dict.fromkeys(v)) for k, v in out.items()}


def masked_png(png: bytes, words: list[Word], labels: dict[int, str]) -> bytes:
    """Обезличенная копия: белый лист + вырезки слов без ПДн + плашки с метками на месте ПДн."""
    from PIL import Image, ImageDraw

    src = Image.open(io.BytesIO(png)).convert("RGB")
    out = Image.new("RGB", src.size, "white")
    draw = ImageDraw.Draw(out)
    done: set[tuple] = set()
    for i, w in enumerate(words):
        box = (w.x, w.y, w.x + w.w, w.y + w.h)
        if i not in labels:
            out.paste(src.crop(box), box[:2])
            continue
        key = (labels[i], w.line)
        draw.rectangle(box, fill=(200, 200, 200))
        if key not in done:  # метка один раз на строку, даже если ПДн из нескольких слов
            draw.text((w.x + 2, w.y + max(0, w.h // 2 - 6)), labels[i], fill=(0, 0, 0))
            done.add(key)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def restore_marks(text: str, vault: dict[str, str]) -> str:
    return re.sub(r"\[(?:CANDIDATE|EMAIL|PHONE|LINK_\d+|PERSON_\d+)\]", lambda m: vault.get(m.group(0), m.group(0)), text)


def _local_text(words: list[Word]) -> str:
    return "\n".join(" ".join(words[i].text for i in idx) for idx in _lines(words))


async def recognize_page(png: bytes, known_name: str | None = None) -> tuple[str, dict[str, str]]:
    from PIL import Image

    words = find_words(png)
    if not words:
        return "", {}
    labels = find_pii(words, Image.open(io.BytesIO(png)).height, known_name)
    vault = vault_from(words, labels)
    if not llm.available():  # без ИИ: локальный текст Tesseract
        return _local_text(words), vault
    res = await llm.structured(schema=OCRText, system=SYSTEM_OCR, model=get_settings().llm_model_vision,
                               effort="low", max_tokens=6000, user="Перепиши текст страницы.",
                               images=[masked_png(png, words, labels)])
    return restore_marks(res.text, vault), vault


async def recognize(filename: str, data: bytes, known_name: str | None = None) -> tuple[str, str | None]:
    """Скан PDF или фото -> (сырой текст, имя кандидата, если нашлось). Страницы склеиваются по порядку."""
    if not available():
        raise OCRError(NO_TESSERACT)
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == ".pdf":
        pages = pdf_pages_png(data)
    elif ext in IMAGE_EXT:
        pages = [image_to_png(data)]
    else:
        raise OCRError("Этот формат распознать нельзя")
    texts, name = [], known_name
    for p in pages:
        t, vault = await recognize_page(p, name)
        name = name or vault.get("[CANDIDATE]")
        texts.append(t)
    text = "\n".join(t for t in texts if t)
    if not text.strip():
        raise OCRError("На изображении не нашлось текста. Пришлите более чёткий скан или DOCX.")
    return text, name
