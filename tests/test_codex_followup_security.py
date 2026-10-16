"""Codex 验收发现问题专项安全回归测试套件
覆盖：
A. PowerShell 命令注入防御（彻底移除生产环境 PowerShell 解析，不得启动外部进程）
B. 真实 Windows .lnk 解析与策略校验（安全项放行、高危/参数/损坏项拒绝）
C. CatalogManager.load_catalog() 旧目录加载时自动迁移与重新校验
D. MCP 工具对外错误脱敏（绝不泄漏本地路径、快捷方式参数或原始异常）
E. 路径真实规范化、Junction/符号链接与 UNC 根目录深度隐私防护
"""

import os
import sys
import json
import tempfile
import subprocess
import ctypes
from unittest.mock import patch, MagicMock
import pytest

from src.catalog.models import AppEntry
from src.catalog.shortcut_resolver import (
    ShortcutResolver,
    ShortcutInfo,
    ShortcutValidationResult,
    DANGEROUS_TARGET_NAMES,
)
from src.catalog.manager import CatalogManager, LookupStatus
from src.catalog.scanner import is_path_within_protected_root
from src.core.config import ConfigManager, AppConfig
from src.core.permission import permission_engine
from src.mcp_server.tools import register_mcp_tools
from mcp.server.fastmcp import FastMCP


# =================================================================================================
# A. PowerShell 命令注入测试
# =================================================================================================
def test_powershell_injection_payload_in_lnk_path_does_not_execute(tmp_path):
    """
    当快捷方式路径包含单引号与 PowerShell 注入指令（如 bad'; Write-Output INJECTED; #.lnk），
    且原生 IShellLinkW 解析失败时：
    1. ShortcutResolver.resolve() 必须安全返回失败；
    2. 绝不得调用 subprocess.run / subprocess.Popen；
    3. 绝不得启动 powershell.exe / pwsh.exe / cmd.exe / wscript.exe；
    4. 生产代码中彻底不得存在 _resolve_via_wscript_shell 方法。
    """
    # 验证生产代码中彻底移除了 _resolve_via_wscript_shell
    assert not hasattr(ShortcutResolver, "_resolve_via_wscript_shell"), (
        "ShortcutResolver 生产代码中仍保留了危险的 _resolve_via_wscript_shell 备用方法！"
    )

    malicious_lnk = tmp_path / "bad'; Write-Output INJECTED; #.lnk"
    malicious_lnk.write_text("mock")

    # 模拟原生 IShellLinkW 解析失败
    with patch.object(ShortcutResolver, "_resolve_via_ishell_link", side_effect=RuntimeError("COM Failed")), \
         patch("subprocess.run") as mock_subproc_run, \
         patch("subprocess.Popen") as mock_subproc_popen:

        res = ShortcutResolver.resolve(str(malicious_lnk))
        assert res.is_resolved is False
        assert "无法解析" in res.error_message or "失败" in res.error_message

        # 核心断言：绝对没有启动任何外部进程
        mock_subproc_run.assert_not_called()
        mock_subproc_popen.assert_not_called()


# =================================================================================================
# B. 真实 Windows .lnk 集成测试
# =================================================================================================
@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 平台上执行真实 .lnk 集成测试")
def test_real_windows_lnk_resolution_and_validation(tmp_path):
    """
    在 Windows 上使用系统 COM 接口创建真实 .lnk 文件，测试原生 IShellLinkW 的解析与策略校验：
    - 安全无参数 EXE 快捷方式 -> 解析成功并通过
    - 指向 cmd.exe 的快捷方式 -> 拦截
    - 指向 powershell.exe 的快捷方式 -> 拦截
    - 含有启动参数的普通快捷方式 -> 拦截
    - 损坏无效的 .lnk 文件 -> 安全失败，不崩溃
    """
    # 定义测试辅助函数：使用纯进程内 Windows COM/ctypes 创建真实快捷方式（严禁调用 PowerShell/cmd 等外部进程）
    def create_shortcut(lnk_dest: str, target: str, args: str = ""):
        from ctypes import wintypes
        class GUID(ctypes.Structure):
            _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD), ('Data4', wintypes.BYTE * 8)]

        CLSID_ShellLink = GUID(0x00021401, 0, 0, (wintypes.BYTE * 8)(0xC0, 0, 0, 0, 0, 0, 0, 0x46))
        IID_IShellLinkW = GUID(0x000214F9, 0, 0, (wintypes.BYTE * 8)(0xC0, 0, 0, 0, 0, 0, 0, 0x46))
        IID_IPersistFile = GUID(0x0000010B, 0, 0, (wintypes.BYTE * 8)(0xC0, 0, 0, 0, 0, 0, 0, 0x46))

        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        p_sl = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(CLSID_ShellLink), None, 1, ctypes.byref(IID_IShellLinkW), ctypes.byref(p_sl))
        vtable = ctypes.cast(p_sl, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        set_path_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR)
        set_path_type(vtable[20])(p_sl, target)

        if args:
            set_args_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR)
            set_args_type(vtable[11])(p_sl, args)

        p_pf = ctypes.c_void_p()
        qi_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        qi_type(vtable[0])(p_sl, ctypes.byref(IID_IPersistFile), ctypes.byref(p_pf))
        pf_vtable = ctypes.cast(p_pf, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        save_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.BOOL)
        hr_save = save_type(pf_vtable[6])(p_pf, str(lnk_dest), True)

        rel_type = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        rel_type(pf_vtable[2])(p_pf)
        rel_type(vtable[2])(p_sl)
        ole32.CoUninitialize()
        if hr_save != 0:
            raise RuntimeError(f"IPersistFile::Save 失败: {hex(hr_save)}")

    with patch("subprocess.run") as mock_sub_run, patch("subprocess.Popen") as mock_sub_popen:
        real_notepad = os.path.expandvars(r"%SystemRoot%\System32\notepad.exe")
        real_cmd = os.path.expandvars(r"%SystemRoot%\System32\cmd.exe")
        real_powershell = os.path.expandvars(r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")

        # 1. 真实普通安全快捷方式
        safe_lnk = tmp_path / "safe_notepad.lnk"
        create_shortcut(str(safe_lnk), real_notepad)
        res_safe = ShortcutResolver.resolve(str(safe_lnk))
        assert res_safe.is_resolved is True
        assert "notepad.exe" in res_safe.target_path.lower()
        val_safe = ShortcutResolver.validate(str(safe_lnk), res_safe)
        assert val_safe.is_allowed is True

        # 2. 真实 cmd.exe 快捷方式
        cmd_lnk = tmp_path / "cmd_target.lnk"
        create_shortcut(str(cmd_lnk), real_cmd)
        res_cmd = ShortcutResolver.resolve(str(cmd_lnk))
        val_cmd = ShortcutResolver.validate(str(cmd_lnk), res_cmd)
        assert val_cmd.is_allowed is False
        assert "受限制" in val_cmd.block_reason or "高危" in val_cmd.block_reason

        # 3. 真实 powershell.exe 快捷方式
        ps_lnk = tmp_path / "ps_target.lnk"
        create_shortcut(str(ps_lnk), real_powershell)
        res_ps = ShortcutResolver.resolve(str(ps_lnk))
        val_ps = ShortcutResolver.validate(str(ps_lnk), res_ps)
        assert val_ps.is_allowed is False
        assert "受限制" in val_ps.block_reason or "高危" in val_ps.block_reason

        # 4. 真实带参数快捷方式
        args_lnk = tmp_path / "notepad_args.lnk"
        create_shortcut(str(args_lnk), real_notepad, args="/p confidential.txt")
        res_args = ShortcutResolver.resolve(str(args_lnk))
        val_args = ShortcutResolver.validate(str(args_lnk), res_args)
        assert val_args.is_allowed is False
        assert "启动参数" in val_args.block_reason
        # 核心安全：对外错误原因严禁泄露原始参数内容
        assert "confidential.txt" not in val_args.block_reason
        assert "/p" not in val_args.block_reason

        # 5. 损坏的伪造 .lnk
        corrupt_lnk = tmp_path / "corrupt.lnk"
        corrupt_lnk.write_bytes(b"\x00\x01\x02\x03CORRUPTED_DATA")
        res_corrupt = ShortcutResolver.resolve(str(corrupt_lnk))
        assert res_corrupt.is_resolved is False

        # 断言整个快捷方式创建与解析生命周期绝对没有启动外部脚本/进程
        mock_sub_run.assert_not_called()
        mock_sub_popen.assert_not_called()


# =================================================================================================
# C. 旧目录自动迁移与重新校验测试
# =================================================================================================
def test_catalog_load_automatically_migrates_and_revalidates_shortcuts(tmp_path):
    """
    旧版 catalog.json 中保存了已授权的危险快捷方式（例如指向 cmd.exe）或已失效快捷方式。
    仅仅实例化 CatalogManager(catalog_file_path=...)，在加载阶段就必须自动执行迁移核验：
    - 危险快捷方式自动被置为 is_enabled=False, is_valid=False，并填充 block_reason；
    - 安全快捷方式保持原有授权状态不变；
    - 整个过程不触发全盘扫描，重新读取磁盘保存的文件也是安全状态。
    """
    cat_file = tmp_path / "catalog.json"
    fake_cmd_lnk = tmp_path / "danger_cmd.lnk"
    fake_cmd_lnk.write_text("mock")
    fake_safe_lnk = tmp_path / "safe_app.lnk"
    fake_safe_lnk.write_text("mock")
    safe_exe = tmp_path / "safe.exe"
    safe_exe.write_text("mock")

    legacy_data = [
        {
            "app_id": "app_legacy_danger",
            "display_name": "系统终端",
            "alias": "cmd",
            "exe_path": str(fake_cmd_lnk),
            "source": "Manual",
            "is_enabled": True,  # 旧状态为已授权
            "is_valid": True,
            "is_restricted": False,
            "resolved_target": "",
            "shortcut_arguments": "",
            "authorization_block_reason": ""
        },
        {
            "app_id": "app_legacy_safe",
            "display_name": "安全程序",
            "alias": "safe",
            "exe_path": str(fake_safe_lnk),
            "source": "Manual",
            "is_enabled": True,  # 旧状态为已授权
            "is_valid": True,
            "is_restricted": False,
            "resolved_target": "",
            "shortcut_arguments": "",
            "authorization_block_reason": ""
        }
    ]
    with open(cat_file, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    def mock_resolve(path):
        if str(fake_cmd_lnk) in path:
            return ShortcutInfo(
                shortcut_path=str(fake_cmd_lnk),
                target_path="C:\\Windows\\System32\\cmd.exe",
                arguments="",
                is_resolved=True
            )
        else:
            return ShortcutInfo(
                shortcut_path=str(fake_safe_lnk),
                target_path=str(safe_exe),
                arguments="",
                is_resolved=True
            )

    with patch.object(ShortcutResolver, "resolve", side_effect=mock_resolve):
        # 仅仅实例化，不调用 revalidate_all()
        manager = CatalogManager(catalog_file_path=str(cat_file))

        danger_app = manager.apps["app_legacy_danger"]
        assert danger_app.is_enabled is False, "加载旧目录时危险快捷方式必须被自动撤销授权！"
        assert danger_app.is_valid is False
        assert "受限制" in danger_app.authorization_block_reason or "高危" in danger_app.authorization_block_reason

        safe_app = manager.apps["app_legacy_safe"]
        assert safe_app.is_enabled is True, "加载旧目录时安全快捷方式的原有授权状态必须保留！"
        assert safe_app.is_valid is True
        assert safe_app.resolved_target == str(safe_exe)

        # 检查磁盘文件也已自动更新保存为安全状态
        with open(cat_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        disk_danger = next(d for d in disk_data if d["app_id"] == "app_legacy_danger")
        assert disk_danger["is_enabled"] is False
        assert disk_danger["is_valid"] is False


# =================================================================================================
# D. MCP 路径泄漏与参数泄露测试
# =================================================================================================
def test_mcp_launch_tool_never_leaks_local_paths_or_arguments(tmp_path):
    """
    当软件启动发生异常（如底层抛出包含用户完整个人路径、文档名或快捷方式参数的异常）：
    1. MCP 返回值绝不得包含 'C:\\Users', 'Documents', 'secret.txt' 或原始异常堆栈；
    2. 仅返回固定类别的中文安全错误信息；
    3. 审计记录与日志中绝不记录用户完整物理路径和快捷方式原始参数。
    """
    cat_file = tmp_path / "catalog.json"
    cfg_file = tmp_path / "config.json"
    secrets_file = tmp_path / "secrets.dat"

    cfg_mgr = ConfigManager()
    cfg_mgr.config_path = str(cfg_file)
    cfg_mgr.secrets_path = str(secrets_file)
    cfg_mgr.config = AppConfig(allow_run_apps=True)

    cat_mgr = CatalogManager(catalog_file_path=str(cat_file))
    fake_exe = tmp_path / "test_app.exe"
    fake_exe.write_text("mock")

    entry = cat_mgr.add_manual_app("测试应用", str(fake_exe), alias="testapp", authorized=True)

    mcp = FastMCP("LeakTest")
    register_mcp_tools(mcp=mcp, config_mgr=cfg_mgr, catalog_mgr=cat_mgr)

    launch_tool = next(t.fn for t in mcp._tool_manager.list_tools() if t.name == "运行电脑端软件文件或程序")

    sensitive_path_error = (
        r"WindowsError 0x2: Failed to start C:\Users\Alice\Documents\Private\secret.txt "
        r"with args --token=SECRET_PASS_123"
    )

    with patch.object(cat_mgr, "launch_app", side_effect=RuntimeError(sensitive_path_error)), \
         patch.object(permission_engine, "log_audit") as mock_audit:

        resp = launch_tool(program_name="testapp")

        assert resp["是否成功"] is False
        err_msg = resp["错误请检查路径"]

        # 核心脱敏断言：绝不泄露路径、文件名、参数
        assert "C:\\Users" not in err_msg
        assert "Alice" not in err_msg
        assert "Documents" not in err_msg
        assert "secret.txt" not in err_msg
        assert "SECRET_PASS_123" not in err_msg
        assert "WindowsError" not in err_msg
        assert "软件启动失败" in err_msg or "路径失效" in err_msg or "安全校验" in err_msg

        # 检查审计日志脱敏：审计目标与结果中不得包含敏感路径与密码参数
        mock_audit.assert_called_once()
        call_args = mock_audit.call_args[0]
        # call_args: (tool_name, action, target, success, detail)
        audit_target = call_args[2]
        audit_detail = call_args[4]
        assert "C:\\Users\\Alice" not in audit_target
        assert "SECRET_PASS_123" not in audit_detail
        assert "secret.txt" not in audit_detail


# =================================================================================================
# E. GetArguments 失败 fail-closed 专项测试
# =================================================================================================
@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上测试 IShellLinkW")
def test_get_arguments_failure_fails_closed(tmp_path):
    """
    当 IShellLinkW::GetArguments 返回失败 HRESULT 时：
    1. 解析结果必须为失败 (is_resolved=False)；
    2. 绝不得把读取失败当成无参数并放行；
    3. validate_shortcut_target() 返回 is_allowed=False；
    4. 绝不启动任何外部进程；
    5. 错误提示固定且不包含路径或参数。
    """
    from ctypes import wintypes
    class GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD), ('Data4', wintypes.BYTE * 8)]

    CLSID_ShellLink = GUID(0x00021401, 0, 0, (wintypes.BYTE * 8)(0xC0, 0, 0, 0, 0, 0, 0, 0x46))
    IID_IShellLinkW = GUID(0x000214F9, 0, 0, (wintypes.BYTE * 8)(0xC0, 0, 0, 0, 0, 0, 0, 0x46))
    IID_IPersistFile = GUID(0x0000010B, 0, 0, (wintypes.BYTE * 8)(0xC0, 0, 0, 0, 0, 0, 0, 0x46))

    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    p_sl = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(CLSID_ShellLink), None, 1, ctypes.byref(IID_IShellLinkW), ctypes.byref(p_sl))
    vtable = ctypes.cast(p_sl, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    set_path_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR)
    real_notepad = os.path.expandvars(r"%SystemRoot%\System32\notepad.exe")
    set_path_type(vtable[20])(p_sl, real_notepad)
    p_pf = ctypes.c_void_p()
    qi_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
    qi_type(vtable[0])(p_sl, ctypes.byref(IID_IPersistFile), ctypes.byref(p_pf))
    pf_vtable = ctypes.cast(p_pf, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    save_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.BOOL)
    lnk_file = tmp_path / "getargs_fail.lnk"
    save_type(pf_vtable[6])(p_pf, str(lnk_file), True)
    rel_type = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    rel_type(pf_vtable[2])(p_pf)
    rel_type(vtable[2])(p_sl)
    ole32.CoUninitialize()

    # 注入 GetArguments 失败（非 0 HRESULT）
    with patch.object(ShortcutResolver, "_get_shell_link_arguments", return_value=-2147467259), \
         patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen:
        res = ShortcutResolver.resolve(str(lnk_file))
        assert res.is_resolved is False, "GetArguments 失败时快捷方式绝不得被标记为解析成功！"
        assert "无法解析" in res.error_message
        val = ShortcutResolver.validate(str(lnk_file))
        assert val.is_allowed is False
        assert val.block_reason == "快捷方式无法解析"
        mock_run.assert_not_called()
        mock_popen.assert_not_called()


# =================================================================================================
# F. 路径规范化、Junction 与 UNC 隐私边界深度测试
# =================================================================================================
def test_canonical_path_and_junction_privacy_boundary(tmp_path):
    """
    测试 is_path_within_protected_root() 集中规范化与深度隐私防护：
    - 必须放行：未展开原始 %LOCALAPPDATA%\\Programs 及多层合法子路径；
    - 必须拦截：未展开的 %USERPROFILE%\\Documents、%UNKNOWN_VAR%\\App；
    - 必须拦截：UNC/网络根目录、盘符根目录、Documents/Downloads 多层后代；
    - 必须拦截：通过 Junction 或符号链接最终解析到 Documents/Downloads 的路径；
    - 遇到兼容盘符 commonpath 比较异常时，必须 fail-closed 默认阻断；
    - 遇到跨盘比较时正常放行，绝不误阻断。
    """
    docs = os.path.expandvars(r"%USERPROFILE%\Documents")
    downloads = os.path.expandvars(r"%USERPROFILE%\Downloads")

    # 1. 原始未展开的环境变量路径：
    # 合法的 %LOCALAPPDATA%\Programs 必须放行（返回 False）！
    assert is_path_within_protected_root(r"%LOCALAPPDATA%\Programs") is False
    assert is_path_within_protected_root(r"%LOCALAPPDATA%\Programs\Microsoft VS Code") is False

    # 未展开的敏感目录 %USERPROFILE%\Documents\App 必须拒绝（返回 True）！
    assert is_path_within_protected_root(r"%USERPROFILE%\Documents\App") is True
    assert is_path_within_protected_root(r"%USERPROFILE%\Downloads\App") is True

    # 残留未知环境变量的 %UNKNOWN_VAR%\App 必须拒绝（返回 True）！
    assert is_path_within_protected_root(r"%UNKNOWN_VAR%\App") is True

    # 2. UNC 与网络根目录 -> 必须安全拦截
    assert is_path_within_protected_root(r"\\server\share") is True
    assert is_path_within_protected_root("\\\\?\\C:\\") is True

    # 3. 盘符根目录 -> 必须拦截
    assert is_path_within_protected_root("C:") is True
    assert is_path_within_protected_root("C:\\") is True
    assert is_path_within_protected_root("D:/") is True

    # 4. Documents / Downloads 及其深层子目录 -> 必须拦截
    assert is_path_within_protected_root(os.path.join(docs, "Deep", "Nested", "App")) is True
    assert is_path_within_protected_root(os.path.join(downloads, "SubDir")) is True

    # 5. Junction / 符号链接解析测试：
    # 模拟外部路径 D:\MyJunction，其真实物理路径 (realpath) 指向 %USERPROFILE%\Documents\Target
    with patch("os.path.realpath", return_value=os.path.join(docs, "Target")):
        assert is_path_within_protected_root(r"D:\MyJunction") is True

    # 6. 类似前缀但非子目录 -> 必须放行
    doc_backup = os.path.expandvars(r"%USERPROFILE%\DocumentsBackup")
    dl_old = os.path.expandvars(r"%USERPROFILE%\Downloads_Old")
    assert is_path_within_protected_root(doc_backup) is False
    assert is_path_within_protected_root(dl_old) is False

    # 7. 跨盘路径与无公共路径比较 -> 正常放行，不因跨盘把其他盘软件拒绝
    assert is_path_within_protected_root(r"E:\Games\MyGame") is False
    assert is_path_within_protected_root(r"X:\Tools\Utility") is False

    # 8. 同盘兼容路径在 commonpath 比较发生不可预期异常时 -> 必须 fail-closed 默认阻断（返回 True）！
    with patch("os.path.commonpath", side_effect=ValueError("Corrupted comparison")):
        assert is_path_within_protected_root(r"C:\SafeDir\App") is True


# =================================================================================================
# G. 本地日志与异常链防泄漏专项测试
# =================================================================================================
def test_local_logs_and_exception_chains_never_leak_sensitive_paths_or_args(tmp_path, caplog):
    """
    在软件启动和快捷方式安全校验路径中：
    - 不得把物理 path 写入本地日志；
    - 不得把快捷方式参数写入本地日志；
    - 不得通过异常或异常链（__cause__ / __context__）泄露原始路径与敏感文本；
    - 审计日志中不得包含物理路径或参数；
    - display_name 与 app_id 必须安全保留。
    """
    import logging

    sensitive_user = "SecretUser123"
    sensitive_dir = "TopSecretFolder"
    sensitive_file = "sensitive_app.exe"
    sensitive_token = "TOKEN_XYZ_789"
    sensitive_full_path = f"C:\\Users\\{sensitive_user}\\Documents\\{sensitive_dir}\\{sensitive_file}"

    cat_file = tmp_path / "catalog.json"
    cat_mgr = CatalogManager(catalog_file_path=str(cat_file))

    app_id = "app_sensitive_test"
    entry = AppEntry(
        app_id=app_id,
        display_name="保密测试工具",
        alias="sectool",
        exe_path=sensitive_full_path,
        source="Manual",
        is_enabled=True,
        is_valid=True,
        resolved_target="",
        shortcut_arguments=f"--token={sensitive_token}"
    )
    cat_mgr.apps[app_id] = entry

    raw_os_error = f"[WinError 2] The system cannot find the file specified: '{sensitive_full_path}' with args {sensitive_token}"

    with caplog.at_level(logging.DEBUG), \
         patch("os.path.exists", return_value=True), \
         patch("subprocess.Popen", side_effect=FileNotFoundError(raw_os_error)):

        with pytest.raises(RuntimeError) as exc_info:
            cat_mgr.launch_app(entry)

        raised_err = str(exc_info.value)

        # 断言异常文本中绝无敏感标记
        assert sensitive_user not in raised_err
        assert sensitive_dir not in raised_err
        assert sensitive_file not in raised_err
        assert sensitive_token not in raised_err
        assert "WinError" not in raised_err

        # 断言异常链 __cause__ 与 __context__ 均无敏感泄露
        cause = exc_info.value.__cause__
        context = exc_info.value.__context__
        assert cause is None or sensitive_user not in str(cause)
        assert context is None or sensitive_user not in str(context)

        # 断言捕获的所有本地日志中均无敏感标记
        captured_logs = caplog.text
        assert sensitive_user not in captured_logs
        assert sensitive_dir not in captured_logs
        assert sensitive_file not in captured_logs
        assert sensitive_token not in captured_logs
        assert "WinError" not in captured_logs

        # 断言安全的 display_name 和 app_id 正常保留在日志中
        assert "保密测试工具" in captured_logs
        assert app_id in captured_logs
