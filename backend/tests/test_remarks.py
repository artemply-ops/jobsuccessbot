"""П13: реплики сервиса по стилю общения."""

from app.services import remarks


def test_neutral_is_silent():
    assert remarks.package(None, {"after": 90, "before": 60}, {"req_total": 10, "req_covered": 9}) == ""
    assert remarks.score("neutral", 95) == ""


def test_ty_and_vy_coverage():
    ty = remarks.coverage("ty", 9, 10)
    vy = remarks.coverage("vy", 9, 10)
    assert "9 из 10" in ty and "9 из 10" in vy and ty != vy
    assert remarks.coverage("ty", 1, 10)  # низкое покрытие тоже комментируем, без грубости


def test_score_growth_and_stability():
    a = remarks.score("vy", 78, 60)
    assert "78 из 100" in a and "Плюс 18" in a
    assert remarks.score("vy", 78, 60) == a  # при обновлении страницы текст не меняется
    assert "Плюс" not in remarks.score("ty", 78, 76)


def test_no_long_dash_and_unknown_tone():
    for tone in ("ty", "vy"):
        for low, vs in remarks._COVERAGE[tone] + remarks._SCORE[tone]:
            assert all("—" not in v and "–" not in v for v in vs)
    assert remarks.norm("xx") == "neutral"
