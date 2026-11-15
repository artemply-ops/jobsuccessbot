"""Схема резюме в цветах компании: цвета с сайта, палитра, рендер; терпимость к ссылке при наличии текста."""

from app.services import brand, cv_render

HTML = """<html><head><meta name="theme-color" content="#E30613">
<style>:root{--brand-primary:#E30613;--accent-color:#1D3C8F} body{color:#222;background:#fff}
.btn{background:#E30613}.link{color:#1D3C8F}.muted{color:#999999}</style></head><body></body></html>"""


def test_brand_colors_from_page():
    colors = brand.pick(brand.collect(HTML, []))
    assert colors[0] == "e30613"  # theme-color и переменная --brand
    assert colors[1] == "1d3c8f"  # второй фирменный оттенок
    assert all(brand.is_brand(c) for c in colors)  # серые и белый отброшены


def test_company_scheme_key_roundtrip_and_render():
    key = cv_render.company_key(brand.palette(["e30613", "1d3c8f"]))
    assert cv_render.valid(key) and len(key) <= 32
    s = cv_render.scheme_of(key)
    assert s.signal == "#e30613" and s.title == "Цвета компании"
    assert not cv_render.valid("company-zzz") and cv_render.valid("graphite")
    sample = {"name": "Тест", "headline": "Роль", "summary": "Текст", "highlights": [], "experience": [],
              "skills": [], "education": [], "languages": [], "contacts": ""}
    pdf, _ = cv_render.render_cv(sample, key, one_page=True)
    assert pdf[:4] == b"%PDF"


def test_light_brand_color_gets_darker_signal():
    p = brand.palette(["ffd400"])  # светло-жёлтый бренд
    assert p["head"] != p["signal"] and p["head"].startswith("#")
