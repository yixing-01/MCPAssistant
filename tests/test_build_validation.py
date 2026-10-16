"""测试构建脚本对内嵌 Python 运行时 DLL 的强制校验规则"""

import os
import re
import pytest


def validate_embedded_python_dlls(internal_dir: str, py_major: int, py_minor: int) -> dict:
    """模拟并测试 build.ps1 中的内嵌 Python DLL 强校验逻辑"""
    expected_dll = f"python{py_major}{py_minor}.dll"
    expected_dll_path = os.path.join(internal_dir, expected_dll)

    # 1. 检查是否存在所有 python*.dll
    all_dlls = [
        f for f in os.listdir(internal_dir)
        if f.lower().startswith("python") and f.lower().endswith(".dll")
    ]

    # 2. 必须存在预期主运行时 DLL
    if not os.path.exists(expected_dll_path):
        raise ValueError(f"未在 _internal 目录下找到预期的主运行时 DLL: {expected_dll}")

    # 3. 检查是否有不匹配的 python3xx.dll
    pattern = re.compile(r"^python3\d+\.dll$", re.IGNORECASE)
    mismatched = [
        dll for dll in all_dlls
        if pattern.match(dll) and dll.lower() != expected_dll.lower()
    ]
    if mismatched:
        raise ValueError(f"发现不匹配的 Python 主运行时 DLL: {', '.join(mismatched)}")

    return {
        "status": "passed",
        "expected_dll": expected_dll,
        "all_dlls": all_dlls,
    }


def test_build_validation_accepts_correct_dll(tmp_path):
    """验证当存在正确的 DLL 且无冲突时校验通过"""
    internal_dir = tmp_path / "_internal"
    internal_dir.mkdir()
    (internal_dir / "python313.dll").write_bytes(b"dummy")
    # 允许包含稳定的 ABI 辅助 DLL 如 python3.dll
    (internal_dir / "python3.dll").write_bytes(b"dummy")

    result = validate_embedded_python_dlls(str(internal_dir), 3, 13)
    assert result["status"] == "passed"
    assert result["expected_dll"] == "python313.dll"


def test_build_validation_rejects_missing_dll(tmp_path):
    """验证当缺少预期 DLL 时报错"""
    internal_dir = tmp_path / "_internal"
    internal_dir.mkdir()

    with pytest.raises(ValueError, match="未在 _internal 目录下找到预期的主运行时 DLL: python313.dll"):
        validate_embedded_python_dlls(str(internal_dir), 3, 13)


def test_build_validation_rejects_mismatched_runtime_dll(tmp_path):
    """验证当存在其他版本的运行时 DLL (例如 python310.dll) 时报错拒绝"""
    internal_dir = tmp_path / "_internal"
    internal_dir.mkdir()
    (internal_dir / "python313.dll").write_bytes(b"dummy")
    (internal_dir / "python310.dll").write_bytes(b"dummy")

    with pytest.raises(ValueError, match="发现不匹配的 Python 主运行时 DLL: python310.dll"):
        validate_embedded_python_dlls(str(internal_dir), 3, 13)


def test_spec_includes_required_com_hidden_imports():
    """验证 PyInstaller spec 文件显式包含了 Windows COM 依赖"""
    spec_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "MCPAssistant.spec",
    )
    assert os.path.exists(spec_path), f"Spec 文件不存在: {spec_path}"
    with open(spec_path, "r", encoding="utf-8") as f:
        spec_content = f.read()

    required_hidden_imports = {
        "pythoncom",
        "pywintypes",
        "win32api",
        "win32com",
        "win32com.client",
    }
    for mod in required_hidden_imports:
        assert (
            f"'{mod}'" in spec_content or f'"{mod}"' in spec_content
        ), f"Spec 缺少必需的 hiddenimport: {mod}"


def test_spec_includes_required_qt_hidden_imports_and_manifest():
    """验证 Spec 文件显式声明了 Qt 核心组件和 DPI manifest 清单文件。"""
    spec_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "MCPAssistant.spec",
    )
    with open(spec_path, "r", encoding="utf-8") as f:
        spec_content = f.read()

    for mod in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtSvg"):
        assert f"'{mod}'" in spec_content or f'"{mod}"' in spec_content, f"Spec 缺少 Qt hiddenimport: {mod}"

    assert "MCPAssistant.manifest" in spec_content, "Spec 缺少 manifest 属性"


def test_manifest_file_exists_and_declares_per_monitor_v2():
    """验证 installer/MCPAssistant.manifest 文件存在并声明了 PerMonitorV2。"""
    manifest_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "installer",
        "MCPAssistant.manifest",
    )
    assert os.path.exists(manifest_path), f"Manifest 文件不存在: {manifest_path}"
    with open(manifest_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "PerMonitorV2" in content, "Manifest 未声明 PerMonitorV2 DPI awareness"


def test_build_script_invokes_packaged_qt_smoke():
    """验证 build.ps1 构建脚本包含了针对打包产物的 Qt 烟测调用。"""
    build_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "build.ps1",
    )
    with open(build_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "test_packaged_qt.py" in content, "build.ps1 未调用 tests/qt/test_packaged_qt.py 烟测"
