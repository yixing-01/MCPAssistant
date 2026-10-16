from importlib.metadata import version


def test_locked_qt_runtime_versions():
    assert version("PySide6") == "6.12.0"
    assert version("pytest-qt") == "4.5.0"
    assert version("pyinstaller") == "6.22.3"


def test_qapplication_uses_expected_identity(qapp):
    assert qapp.applicationName() == "MCP助手"
    assert qapp.organizationName() == "MCPAssistant"

