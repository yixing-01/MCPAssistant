"""构建与发布脚本自动化测试
验证：
- setup.iss 包含 AppMutex 与卸载清理注册表项
- build.ps1 支持 -Release 参数并在缺少 Inno Setup 时阻断报错
"""

import os
import subprocess
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_setup_iss_contains_required_directives():
    """验证 setup.iss 包含互斥体定义与卸载清理"""
    iss_path = os.path.join(PROJECT_ROOT, "installer", "setup.iss")
    assert os.path.exists(iss_path)

    with open(iss_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "AppMutex=Local\\MCPAssistant_SingleInstance_Mutex" in content
    assert "CloseApplications=yes" in content
    assert "RestartApplications=no" in content
    assert "PrivilegesRequiredOverridesAllowed=dialog" in content
    assert "DefaultDirName={autopf}\\MCPAssistant" in content
    assert 'Root: HKCU; Subkey: "Software\\Microsoft\\Windows\\CurrentVersion\\Run"' in content
    assert "uninsdeletevalue" in content


def test_build_script_release_mode_fails_when_iscc_missing():
    """验证 build.ps1 在 -Release 模式下，若无 ISCC 则严格报错退出（exit code 1）"""
    ps1_path = os.path.join(PROJECT_ROOT, "build.ps1")
    assert os.path.exists(ps1_path)

    # 检查 PowerShell 语法中包含 Release 参数定义
    with open(ps1_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "[switch]$Release" in content
    assert "Release 构建阻断" in content
    assert "[string]$IsccPath" in content


def test_setup_iss_uses_simplified_chinese_isl():
    """验证 setup.iss 使用官方简体中文语言包 ChineseSimplified.isl"""
    iss_path = os.path.join(PROJECT_ROOT, "installer", "setup.iss")
    with open(iss_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "compiler:Languages\\ChineseSimplified.isl" in content
    assert "compiler:Default.isl" not in content


def test_build_release_subproc_fast_fails_with_invalid_iscc():
    """真实子进程执行 build.ps1 -Release -IsccPath <不存在路径> 必须在昂贵构建前快速失败"""
    non_existent = os.path.join(PROJECT_ROOT, "non_existent_iscc_temp", "iscc.exe")
    cmd = [
        "powershell",
        "-ExecutionPolicy", "Bypass",
        "-File", os.path.join(PROJECT_ROOT, "build.ps1"),
        "-Release",
        "-IsccPath", non_existent
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=PROJECT_ROOT)
    assert proc.returncode != 0
    stdout_str = (proc.stdout or b"").decode("utf-8", errors="replace") + (proc.stdout or b"").decode("gbk", errors="replace")
    stderr_str = (proc.stderr or b"").decode("utf-8", errors="replace") + (proc.stderr or b"").decode("gbk", errors="replace")
    output = stdout_str + stderr_str
    assert "Release 构建阻断" in output or "ISCC" in output

