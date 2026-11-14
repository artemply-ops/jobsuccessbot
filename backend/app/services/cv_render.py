"""Вёрстка CV и сопроводительного письма в PDF. Один шаблон, цветовые схемы.

Макет: тёмная шапка на всю ширину с сигнальной полосой, фото в углу, блок показателей,
таймлайн опыта с колонкой дат, светлая плашка с навыками и образованием.
Текст остаётся настоящим, чтобы его читали системы отбора резюме.
Шрифты лежат в репозитории, поэтому PDF выглядит одинаково на любой машине.
"""

import io
from dataclasses import dataclass
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageOps

FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"
Color = tuple[int, int, int]
STRIPE = 1.8  # сигнальная полоса сверху, мм


def _rgb(h: str) -> Color:
    h = h.lstrip("#")
    return int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@dataclass(frozen=True)
class Scheme:
    key: str
    title: str
    head: str  # тёмный базовый: шапка, названия должностей
    highlight: str  # яркий на тёмном: должность в шапке, цифры показателей
    signal: str  # акцент: полоса сверху, линия таймлайна
    paper: str = "#FFFFFF"  # фон листа
    ink: str = "#22262E"  # основной текст


# Номер в названии: палитра статьи Visme «50 цветовых схем», цвета сняты с её картинок
SCHEMES = {s.key: s for s in [
    Scheme("graphite", "Графит, золото, красный", "#222632", "#FEDA5A", "#FC441C"),
    Scheme("navy_rose", "Navy, персик, малиновый (Visme 15)", "#123D6A", "#EDC8B8", "#AB3B61"),
    Scheme("classic", "Классика: чёрный и белый", "#111111", "#FFFFFF", "#6B7280"),
]}


@dataclass(frozen=True)
class Density:
    body: float
    line: float
    name: float
    section_gap: float
    item_gap: float
    margin: float
    photo_w: float
    photo_h: float


FULL = Density(body=10.0, line=5.0, name=27, section_gap=6.0, item_gap=4.0, margin=14, photo_w=40, photo_h=37)
ONE_PAGE = Density(body=8.7, line=4.2, name=24, section_gap=3.6, item_gap=2.4, margin=10.6, photo_w=36, photo_h=33.3)


def _mix(a: Color, b: Color, t: float) -> Color:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


class _Doc(FPDF):
    def __init__(self, scheme: Scheme):
        super().__init__(format="A4")
        self.paper, self.ink = _rgb(scheme.paper), _rgb(scheme.ink)
        self.head, self.highlight, self.signal = _rgb(scheme.head), _rgb(scheme.highlight), _rgb(scheme.signal)
        self.muted = _mix(self.ink, self.paper, 0.42)
        self.on_head = _mix((255, 255, 255), self.head, 0.18)
        self.on_head_dim = _mix((255, 255, 255), self.head, 0.3)
        self.tint = _mix(self.paper, self.head, 0.07)
        self.rail = _mix(self.paper, self.signal, 0.34)
        self.add_font("Sans", "", FONTS / "PT_Sans-Web-Regular.ttf")
        self.add_font("Sans", "B", FONTS / "PT_Sans-Web-Bold.ttf")
        self.c_margin = 0

    def header(self):  # фон и сигнальная полоса на каждой странице
        self.set_fill_color(*self.paper)
        self.rect(0, 0, self.w, self.h, "F")
        self.set_fill_color(*self.signal)
        self.rect(0, 0, self.w, STRIPE, "F")

    def text_block(self, text: str, size: float, line: float, color: Color, style: str = "",
                   x: float | None = None, w: float | None = None, align: str = "L"):
        self.set_font("Sans", style, size)
        self.set_text_color(*color)
        if x is not None:
            self.set_x(x)
        self.multi_cell(w if w is not None else self.w - self.get_x() - self.r_margin, line, text,
                        align=align, new_x="LMARGIN", new_y="NEXT")

    def block_height(self, text: str, size: float, line: float, w: float, style: str = "") -> float:
        self.set_font("Sans", style, size)
        return len(self.multi_cell(w, line, text, align="L", dry_run=True, output="LINES")) * line


def _photo_jpeg(data: bytes, w: float, h: float) -> io.BytesIO:
    """Кроп под окно шапки с учётом EXIF-поворота; фото в модель не отправляется, только в итоговый файл."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img = ImageOps.fit(img, (640, round(640 * h / w)), Image.Resampling.LANCZOS, centering=(0.5, 0.35))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    buf.seek(0)
    return buf


def _head(pdf: _Doc, d: Density, v: dict, photo: bytes | None):
    """Шапка на всю ширину: фото, имя, контакты, резюме в двух строках, сетка показателей."""
    name, headline = v.get("name") or "", v.get("headline") or ""
    title, sub = (name, headline) if name else (headline, "")
    contacts = [c.strip() for c in (v.get("contacts") or "").split("·") if c.strip()]
    summary, highlights = v.get("summary") or "", (v.get("highlights") or [])[:8]
    pad = d.margin * 0.8
    left = d.photo_w + 6 if photo else pdf.l_margin
    contacts_w = 58 if contacts else 0
    title_w = pdf.w - pdf.r_margin - left - (contacts_w + 5 if contacts else 0)
    name_line, sub_line, small = d.name * 0.46, d.line + 0.8, d.body - 1.1
    col_w = pdf.epw / 4

    y0 = STRIPE + pad
    h_title = pdf.block_height(title, d.name, name_line, title_w, "B")
    h_sub = pdf.block_height(sub, d.body + 1.6, sub_line, title_w, "B") + 1.6 if sub else 0
    h_contacts = sum(pdf.block_height(c, small, d.line - 0.3, contacts_w) for c in contacts)
    top_end = max(y0 + h_title + h_sub, y0 + h_contacts, STRIPE + d.photo_h if photo else 0)
    summary_y = top_end + d.section_gap * 0.7
    h_summary = pdf.block_height(summary, d.body, d.line, pdf.epw) if summary else 0
    grid_y = summary_y + h_summary + (d.section_gap if highlights else 0)
    rows = [highlights[i:i + 4] for i in range(0, len(highlights), 4)]
    value_size, value_line = d.body + 6, d.line + 2.2
    row_h = [value_line + max(pdf.block_height(c.get("label") or "", small, d.line - 0.4, col_w - 4) for c in r) + 2.2
             for r in rows]
    band = grid_y + sum(row_h) + pad * (0.6 if highlights else 1)

    pdf.set_fill_color(*pdf.head)
    pdf.rect(0, STRIPE, pdf.w, band - STRIPE, "F")
    pdf.set_fill_color(*_mix(pdf.head, (255, 255, 255), 0.28))
    pdf.rect(0, band, pdf.w, 1.2, "F")
    if photo:
        pdf.image(_photo_jpeg(photo, d.photo_w, d.photo_h), x=0, y=STRIPE, w=d.photo_w, h=d.photo_h)
    pdf.set_y(y0)
    pdf.text_block(title, d.name, name_line, (255, 255, 255), "B", x=left, w=title_w)
    if sub:
        pdf.ln(1.6)
        pdf.text_block(sub, d.body + 1.6, sub_line, pdf.highlight, "B", x=left, w=title_w)
    pdf.set_y(y0)
    for c in contacts:
        pdf.text_block(c, small, d.line - 0.3, pdf.on_head, x=pdf.w - pdf.r_margin - contacts_w, w=contacts_w, align="R")
    if summary:
        pdf.set_y(summary_y)
        pdf.text_block(summary, d.body, d.line, pdf.on_head)
    y = grid_y
    for r, h in zip(rows, row_h):
        for i, c in enumerate(r):
            x = pdf.l_margin + i * col_w
            pdf.set_y(y)
            pdf.text_block(c.get("value") or "", value_size, value_line, pdf.highlight, "B", x=x, w=col_w - 3)
            pdf.text_block(c.get("label") or "", small, d.line - 0.4, pdf.on_head_dim, x=x, w=col_w - 4)
        y += h
    pdf.set_y(band + 1.2)


def _section(pdf: _Doc, d: Density, title: str):
    pdf.ln(d.section_gap)
    pdf.text_block(title, d.body + 1.2, d.line + 0.6, pdf.head, "B")
    pdf.ln(d.item_gap * 0.5)


def _role(pdf: _Doc, d: Density, e: dict):
    """Строка таймлайна: слева период и компания, линия, справа должность и достижения."""
    rail_x = pdf.l_margin + 23.5
    x, w = rail_x + 2.8, pdf.w - pdf.r_margin - rail_x - 2.8
    left_w = 22.5
    company, period, role = e.get("company") or "", e.get("period") or "", e.get("role") or ""
    bullets = e.get("bullets") or []
    title_size, title_line = d.body + 0.8, d.line + 0.3
    small = d.body - 0.9
    h_left = pdf.block_height(period, small, d.line - 0.2, left_w, "B") + pdf.block_height(company, small, d.line - 0.2, left_w)
    h_right = pdf.block_height(role, title_size, title_line, w, "B") + 0.6 + sum(
        pdf.block_height(b, d.body, d.line, w - 3.2) + 0.4 for b in bullets)
    h = max(h_left, h_right)
    if pdf.get_y() + h > pdf.page_break_trigger and h < pdf.eph:
        pdf.add_page()
        pdf.set_y(STRIPE + d.margin * 0.7)
    y0 = pdf.get_y()
    pdf.text_block(period, small, d.line - 0.2, pdf.ink, "B", w=left_w)
    pdf.text_block(company, small, d.line - 0.2, pdf.muted, w=left_w)
    pdf.set_y(y0)
    pdf.text_block(role, title_size, title_line, pdf.head, "B", x=x, w=w)
    pdf.ln(0.6)
    for b in bullets:
        y = pdf.get_y()
        pdf.set_fill_color(*pdf.head)
        pdf.rect(x + 0.2, y + d.line * 0.5 - 0.5, 1.0, 1.0, "F")
        pdf.text_block(b, d.body, d.line, pdf.ink, x=x + 3.2, w=w - 3.2)
        pdf.ln(0.4)
    pdf.set_draw_color(*pdf.rail)
    pdf.set_line_width(0.7)
    pdf.line(rail_x, y0, rail_x, y0 + h)
    pdf.set_y(y0 + h + d.item_gap)


def _footer_box(pdf: _Doc, d: Density, rows: list[tuple[str, str]]):
    """Светлая плашка внизу: навыки, образование, языки."""
    if not rows:
        return
    pad, label_w, line = 3.2, 27, d.line
    w = pdf.epw - pad * 2 - label_w
    heights = [pdf.block_height(t, d.body, line, w) + 0.8 for _, t in rows]
    total = sum(heights) + pad * 2 - 0.8
    if pdf.get_y() + total + 2 > pdf.page_break_trigger:
        pdf.add_page()
        pdf.set_y(STRIPE + d.margin * 0.7)
    pdf.ln(d.section_gap * 0.4)
    y = pdf.get_y()
    pdf.set_fill_color(*pdf.tint)
    pdf.rect(pdf.l_margin, y, pdf.epw, total, "F")
    y += pad
    for (label, text), h in zip(rows, heights):
        pdf.set_y(y)
        pdf.text_block(label, d.body, line, pdf.head, "B", x=pdf.l_margin + pad, w=label_w - 2)
        pdf.set_y(y)
        pdf.text_block(text, d.body, line, pdf.ink, x=pdf.l_margin + pad + label_w, w=w)
        y += h
    pdf.set_y(y + pad)


def render_cv(v: dict, scheme: str = "graphite", one_page: bool = False, photo: bytes | None = None) -> tuple[bytes, int]:
    """v: name, contacts, headline, summary, highlights[{value,label}], experience, skills, education, languages.

    Возвращает (pdf, страниц).
    """
    d = ONE_PAGE if one_page else FULL
    pdf = _Doc(SCHEMES[scheme])
    pdf.set_margins(d.margin, 0, d.margin)
    pdf.set_auto_page_break(True, d.margin * 0.7)
    pdf.add_page()
    _head(pdf, d, v, photo)
    if v.get("experience"):
        _section(pdf, d, "Опыт работы")
        for e in v["experience"]:
            _role(pdf, d, e)
    rows = []
    if v.get("skills"):
        rows.append(("Навыки", "  ·  ".join(v["skills"])))
    if v.get("education"):
        rows.append(("Образование", "; ".join(v["education"])))
    if v.get("languages"):
        rows.append(("Языки", "  ·  ".join(v["languages"])))
    _footer_box(pdf, d, rows)
    return bytes(pdf.output()), pdf.page_no()


def render_letter(v: dict, scheme: str = "graphite", company: str = "", position: str = "") -> bytes:
    """Письмо в одном стиле с CV: та же шапка, без фото и показателей, спокойная ширина строки."""
    d = FULL
    pdf = _Doc(SCHEMES[scheme])
    pdf.set_margins(24, 0, 24)
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()
    _head(pdf, d, {k: v.get(k) for k in ("name", "headline", "contacts")}, None)
    pdf.ln(10)
    if company or position:
        pdf.text_block(" · ".join(x for x in [company, position] if x), d.body, d.line, pdf.head, "B")
        pdf.ln(5)
    for para in (v.get("cover_letter") or "").split("\n"):
        if para.strip():
            pdf.text_block(para.strip(), d.body + 0.5, d.line + 1.0, pdf.ink)
            pdf.ln(3.2)
    return bytes(pdf.output())


def preview_png(pdf_bytes: bytes, page: int = 0, dpi: int = 110) -> bytes:
    """Картинка страницы для превью в боте и PWA."""
    import pymupdf

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return doc[page].get_pixmap(dpi=dpi).tobytes("png")
