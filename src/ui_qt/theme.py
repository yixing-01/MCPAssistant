from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

# 统一设计色彩令牌
COLORS = {
    "background": "#FBFAF7",
    "sidebar": "#F0EFE9",
    "surface": "#FFFFFF",
    "text": "#20293A",
    "muted": "#667085",
    "primary": "#5367E8",
    "primary_dark": "#4054D9",
    "success": "#16845B",
    "warning": "#87530C",
    "danger": "#B34F4F",
    "border": "#E1E2DF",
}

# 统一设计尺寸与度量令牌
METRICS = {
    "control_height": 44,
    "app_row_height": 64,
    "sidebar_width": 220,
    "card_radius": 16,
    "control_radius": 10,
}


THEME_QSS_PATH = Path(__file__).with_name("resources") / "theme.qss"


def load_stylesheet() -> str:
    """读取并返回全局 QSS 样式表文本。"""
    return THEME_QSS_PATH.read_text(encoding="utf-8")


def apply_theme(app: QApplication) -> None:
    """为应用程序应用 Fusion 基础样式与定制 QSS。"""
    app.setStyle("Fusion")
    app.setStyleSheet(load_stylesheet())
