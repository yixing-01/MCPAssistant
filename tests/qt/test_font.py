"""Windows 系统默认 UI 字体与样式表字体规则测试。"""

from pathlib import Path
from PySide6.QtWidgets import QApplication

from src.ui_qt.app import configure_application


def test_app_configuration_preserves_system_font(qtbot):
    """1. 配置应用后保留 Windows 平台主题提供的系统默认字体家族，不被硬编码覆盖。"""
    app = QApplication.instance() or QApplication([])
    original_font_family = app.font().family()

    configure_application(app)

    # 验证 configure_application 未强制覆盖系统默认字体家族为硬编码字体
    configured_font_family = app.font().family()
    assert configured_font_family == original_font_family
    # 验证未硬编码写死某个字体
    assert configured_font_family != "HardcodedBogusFont"


def test_theme_qss_contains_no_hardcoded_font_family():
    """2. 验证 theme.qss 中彻底移除了所有的 font-family 声明，完全交给系统和 Qt 字体回退机制。"""
    qss_path = Path(__file__).parents[2] / "src" / "ui_qt" / "resources" / "theme.qss"
    assert qss_path.exists()
    content = qss_path.read_text(encoding="utf-8")

    assert "font-family" not in content, "theme.qss 中仍存在硬编码的 font-family 规则！"
    assert "Microsoft YaHei" not in content, "theme.qss 中仍存在硬编码的 Microsoft YaHei！"
    assert "Segoe UI" not in content, "theme.qss 中仍存在硬编码的 Segoe UI！"


def test_ui_qt_source_contains_no_hardcoded_font_declarations():
    """3. 验证 src/ui_qt 目录下所有 Python 源码均未硬编码全局字体家族。"""
    ui_qt_dir = Path(__file__).parents[2] / "src" / "ui_qt"
    py_files = list(ui_qt_dir.rglob("*.py"))
    assert len(py_files) > 0

    for py_file in py_files:
        code = py_file.read_text(encoding="utf-8")
        assert "Microsoft YaHei" not in code, f"{py_file.name} 中仍包含硬编码字体 'Microsoft YaHei'"
        assert 'setFont(QFont(' not in code, f"{py_file.name} 中仍包含硬编码 setFont(QFont(...))"
