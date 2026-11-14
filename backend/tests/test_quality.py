"""Регрессии по итогам живого прогона 14.11.2026. ТЕСТОВЫЕ ДАННЫЕ: люди и компании вымышленные."""

from app.schemas import CV, CVExperience, FactFlag
from app.services.pii import Vault, depersonalize
from app.services.tailor import apply_cv_flags


def test_name_masking_keeps_similar_words():
    """«Тестова» не должна превращать «Тестовый университет» в [CANDIDATE] (вуз пропадал из резюме)."""
    out = depersonalize("Светлана Тестовая окончила Тестовый федеральный университет. Письмо Светлане Тестовой.",
                        "Светлана Тестовая", Vault(), use_ner=False)
    assert "Тестовый федеральный университет" in out and "Светлан" not in out and "Тестовой" not in out
    out = depersonalize("Ольга Белова, Белорусский рынок. Ольгу Белову, Беловой.", "Ольга Белова", Vault(), use_ner=False)
    assert "Белорусский" in out and "Белов" not in out and "Ольг" not in out
    out = depersonalize("ИГОРЬ ТЕСТОВ, Игоря Тестова", "Игорь Тестов", Vault(), use_ner=False)
    assert "ИГОРЬ" not in out and "Тестова" not in out


def test_flagged_headline_and_skills_do_not_reach_file():
    cv = CV(headline="Коммерческий директор (B2B-продажи)", summary="s", skills=["CRM", "Удержание сотрудников"],
            experience=[CVExperience(company="Тестсофт", role="Руководитель отдела продаж", period="2019 — н.в.",
                                     bullets=["b"])])
    flags = [FactFlag(text="Коммерческий директор (B2B-продажи)", where="cv", reason="должности не было"),
             FactFlag(text="Удержание сотрудников", where="cv", reason="нет в профиле"),
             FactFlag(text="что-то в письме", where="cover_letter", reason="r")]
    out = apply_cv_flags(cv, flags, "Руководитель отдела продаж B2B")
    assert out.headline == "Руководитель отдела продаж B2B" and out.skills == ["CRM"]
    untouched = apply_cv_flags(CV(headline="Продакт", summary="", skills=["SQL"], experience=[]), [], "x")
    assert untouched.headline == "Продакт" and untouched.skills == ["SQL"]


def test_low_severity_is_default_high_for_old_flags():
    assert FactFlag(text="t", where="cv", reason="r").severity == "high"
