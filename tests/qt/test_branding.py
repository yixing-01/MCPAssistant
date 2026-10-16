"""品牌资源与本软件全链路图标替换专项测试。

验证：
1. 品牌资源文件存在且能够被 Qt 正常加载。
2. ICO 包含规定的多尺寸图层 (16, 20, 24, 32, 40, 48, 64, 128, 256)。
3. configure_application() 后全局窗口图标非空。
4. MainWindow 窗口图标非空。
5. 侧边栏品牌标签：有真实 pixmap、不再显示“智”、尺寸符合要求 (44x44)。
6. 系统托盘使用品牌图标且图标非空。
7. PyInstaller spec 设置了正确 ICO。
8. Inno Setup 设置了 SetupIconFile。
9. Windows AppUserModelID 调用有平台保护和异常保护。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from PIL import Image
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from src.ui_qt.app import configure_application
from src.ui_qt.branding import (
    get_brand_full_path,
    get_brand_full_pixmap,
    get_brand_ico_path,
    get_brand_master_path,
    get_brand_symbol_path,
    get_brand_tray_icon,
    get_brand_window_icon,
    set_windows_app_user_model_id,
)
from src.ui_qt.tray import QtTrayManager
from src.ui_qt.views.main_window import MainWindow


def test_branding_assets_exist_and_loadable(qapp):
    """1. 品牌资源文件均存在且能被 PIL 与 Qt 正常加载。"""
    master_p = get_brand_master_path()
    full_p = get_brand_full_path()
    sym_p = get_brand_symbol_path()
    ico_p = get_brand_ico_path()

    for p in (master_p, full_p, sym_p, ico_p):
        assert p.exists(), f"品牌资源文件不存在: {p}"
        assert p.stat().st_size > 0, f"品牌资源文件为空: {p}"

    # Qt 加载验证
    icon = get_brand_window_icon()
    assert not icon.isNull(), "Qt 加载窗口品牌图标失败"
    pix = get_brand_full_pixmap(44)
    assert not pix.isNull(), "Qt 加载完整版品牌 Pixmap 失败"


def test_ico_contains_specified_multi_sizes():
    """2. 校验 Windows ICO 文件真实包含 16, 20, 24, 32, 40, 48, 64, 128, 256 全部尺寸图层。"""
    ico_p = get_brand_ico_path()
    img = Image.open(ico_p)
    assert img.format == "ICO"
    sizes = img.info.get("sizes", set())
    expected_sizes = {(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)}
    assert expected_sizes.issubset(sizes), f"ICO 未包含完整多尺寸图层: 当前包含 {sizes}"


def test_configure_application_sets_global_window_icon(qapp):
    """3. configure_application 后全局窗口图标非空。"""
    configure_application(qapp)
    icon = qapp.windowIcon()
    assert not icon.isNull(), "QApplication.windowIcon() 未能成功设置品牌图标"


def test_mainwindow_has_valid_window_icon(qtbot):
    """4. MainWindow 具有有效的品牌窗口图标。"""
    win = MainWindow(skip_startup=True, enable_tray=False)
    qtbot.addWidget(win)
    icon = win.windowIcon()
    assert not icon.isNull(), "MainWindow 未设置有效的品牌窗口图标"


def test_sidebar_brand_mark_displays_full_brand_pixmap_no_character(qtbot):
    """5. 侧边栏品牌区域展示真实完整版图片，尺寸为 44x44，绝不显示'智'字。"""
    win = MainWindow(skip_startup=True, enable_tray=False)
    qtbot.addWidget(win)

    mark = win.brand_mark
    assert mark.width() == 44 and mark.height() == 44
    assert mark.text() == "", "品牌徽标标签仍残留了文字！"
    pix = mark.pixmap()
    assert not pix.isNull(), "品牌徽标未成功设置 Pixmap！"
    assert pix.width() >= 44 and pix.height() >= 44
    assert mark.accessibleName() == "MCP助手品牌徽标"


def test_system_tray_uses_brand_symbol_icon(qapp):
    """6. 系统托盘使用小尺寸品牌符号图标且非空，绝不使用旧机器人。"""
    tray_icon = get_brand_tray_icon()
    assert not tray_icon.isNull()

    from unittest.mock import MagicMock
    window_mock = MagicMock()
    service_mock = MagicMock()
    manager = QtTrayManager(window_mock, service_mock, available_check=lambda: True)
    assert manager.start() is True
    assert manager.icon is not None
    assert not manager.icon.icon().isNull()


def test_pyinstaller_spec_configures_brand_ico():
    """7. 校验 PyInstaller spec 包含正确的品牌 ICO 配置。"""
    spec_path = Path("MCPAssistant.spec")
    assert spec_path.exists()
    content = spec_path.read_text(encoding="utf-8")
    assert "mcp_assistant.ico" in content, "MCPAssistant.spec 未配置 mcp_assistant.ico"
    assert "icon=" in content


def test_inno_setup_script_configures_brand_ico():
    """8. 校验 Inno Setup 脚本配置了 SetupIconFile。"""
    iss_path = Path("installer") / "setup.iss"
    assert iss_path.exists()
    content = iss_path.read_text(encoding="utf-8")
    assert "SetupIconFile=" in content
    assert "mcp_assistant.ico" in content


def test_windows_app_user_model_id_is_safe_and_protected():
    """9. Windows AppUserModelID 调用具备平台安全检查与异常保护。"""
    # 在 Windows 环境下应返回 True 或不抛出任何异常
    res = set_windows_app_user_model_id("MCPAssistant.TestId")
    if sys.platform == "win32":
        assert isinstance(res, bool)
    else:
        assert res is False
