"""MCP助手统一品牌资源与 Windows 应用身份模块。

统一管理软件自身的品牌图标（原图、完整版、小尺寸符号版、多尺寸 Windows ICO），
并提供 Windows AppUserModelID 初始化，确保任务栏分组、标题栏与托盘图标统一。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QIcon, QPixmap


def get_branding_dir() -> Path:
    """获取品牌资源目录（兼容源码运行与 PyInstaller 打包运行）。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # PyInstaller 运行环境
        base = Path(sys._MEIPASS)
        candidate = base / "src" / "ui_qt" / "resources" / "branding"
        if candidate.exists():
            return candidate
        candidate2 = base / "resources" / "branding"
        if candidate2.exists():
            return candidate2
    return Path(__file__).resolve().parent / "resources" / "branding"


def get_brand_master_path() -> Path:
    return get_branding_dir() / "mcp_brand_master.png"


def get_brand_full_path() -> Path:
    return get_branding_dir() / "mcp_brand_full.png"


def get_brand_symbol_path() -> Path:
    return get_branding_dir() / "mcp_brand_symbol.png"


def get_brand_ico_path() -> Path:
    return get_branding_dir() / "mcp_assistant.ico"


def get_brand_window_icon() -> QIcon:
    """获取用于主窗口、全局 Application 和任务栏的品牌图标。优先使用多尺寸 ICO。"""
    ico_path = get_brand_ico_path()
    if ico_path.exists():
        icon = QIcon(str(ico_path))
        if not icon.isNull():
            return icon

    # 回退到符号版 PNG
    sym_path = get_brand_symbol_path()
    if sym_path.exists():
        icon = QIcon(str(sym_path))
        if not icon.isNull():
            return icon

    return QIcon()


def get_brand_tray_icon() -> QIcon:
    """获取系统托盘图标（使用小尺寸符号版）。"""
    return get_brand_window_icon()


def get_brand_full_pixmap(size: int = 44, dpr: float = 1.0) -> QPixmap:
    """获取界面品牌位完整版 Pixmap（支持平滑缩放与高 DPI 适配）。"""
    full_path = get_brand_full_path()
    if not full_path.exists():
        return QPixmap()

    pix = QPixmap(str(full_path))
    if pix.isNull():
        return QPixmap()

    # 按物理像素高分辨率缩放后设置 devicePixelRatio
    physical_size = max(16, int(round(size * dpr)))
    from PySide6.QtCore import Qt
    scaled = pix.scaled(
        physical_size,
        physical_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    return scaled


def set_windows_app_user_model_id(app_id: str = "MCPAssistant.Desktop") -> bool:
    """在 Windows 环境下设置 AppUserModelID，保证任务栏和快捷方式身份统一。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        shell32 = ctypes.windll.shell32
        hr = shell32.SetCurrentProcessExplicitAppUserModelID(wintypes.LPCWSTR(app_id))
        return hr == 0
    except Exception:
        return False
