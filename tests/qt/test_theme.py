from src.ui_qt.theme import COLORS, METRICS, apply_theme


def test_design_tokens_match_approved_spec():
    assert COLORS["background"] == "#FBFAF7"
    assert COLORS["primary"] == "#5367E8"
    assert COLORS["success"] == "#16845B"
    assert COLORS["text"] == "#20293A"
    assert METRICS["control_height"] == 44
    assert METRICS["app_row_height"] == 64


def test_theme_contains_no_emoji_or_code_font(qapp):
    apply_theme(qapp)
    style = qapp.styleSheet()
    assert "font-family" not in style.lower()
    assert "Consolas" not in style
    assert "Segoe UI Emoji" not in style
