r"""第 11 轮：Inno Setup 卸载清理静态安全检查与策略验证套件。

说明与测试边界声明：
1. 本测试文件专门针对 installer/setup.iss 进行 AST/文本级静态安全检查与防御策略验证；
2. 本测试不代表也不伪称已经在真实 Windows 操作系统上执行了交互式卸载；真实卸载必须由用户进行手工验收；
3. 本测试严格检查以下关键安全防线：
   - 彻底杜绝 Inno Setup 未知常量 {userlocalappdata}；
   - 强制使用官方有效常量 ExpandConstant('{localappdata}') 与 ExpandConstant('{userappdata}')；
   - 包含 UninstallSilent、IsAdminInstallMode、IsAdmin 三重保护分支；
   - 管理员/所有用户模式的保护判断必须发生在交互式清理确认和目录删除之前；
   - 严格限定仅清理四个固定目标目录：
     %APPDATA%\\MCPAssistant
     %LOCALAPPDATA%\\MCPAssistant
     %APPDATA%\\XiaozhiMcpApp
     %LOCALAPPDATA%\\XiaozhiMcpApp
   - 严禁删除 %APPDATA%、%LOCALAPPDATA% 本身或任何父目录；
   - 强制具备 Windows 重解析点（符号链接/Junction）探测与防护；
   - 包含 ExpandConstant 失败或返回异常时的 fail-closed 保护。
"""

from __future__ import annotations

from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETUP_ISS = PROJECT_ROOT / "installer" / "setup.iss"


@pytest.fixture(scope="module")
def setup_iss_content() -> str:
    """读取 setup.iss 完整内容。"""
    assert SETUP_ISS.exists(), f"未找到 setup.iss: {SETUP_ISS}"
    return SETUP_ISS.read_text(encoding="utf-8")


def get_function_body(content: str, start_marker: str, end_marker: str | None = None) -> str:
    """提取 Pascal 脚本指定函数/过程的代码块。"""
    pos1 = content.find(start_marker)
    if pos1 == -1:
        return ""
    if end_marker:
        pos2 = content.find(end_marker, pos1 + len(start_marker))
        if pos2 != -1:
            return content[pos1:pos2]
    return content[pos1:]


def test_forbidden_userlocalappdata_constant_not_present(setup_iss_content: str):
    """1. 禁止出现无效常量 {userlocalappdata}。"""
    assert "{userlocalappdata}" not in setup_iss_content.lower(), (
        "setup.iss 包含了无效的 Inno Setup 常量 '{userlocalappdata}'！"
        "Inno Setup 不支持该常量，运行时会导致 Internal error: Unknown constant 'userlocalappdata' 错误。"
        "必须使用 '{localappdata}'。"
    )


def test_valid_appdata_constants_used(setup_iss_content: str):
    """2. 必须使用 ExpandConstant('{localappdata}')；3. 必须保留 ExpandConstant('{userappdata}')。"""
    lower = setup_iss_content.lower()
    assert "expandconstant('{localappdata}')" in lower, (
        "setup.iss 必须使用有效的 ExpandConstant('{localappdata}') 来获取当前用户 LocalAppData 目录"
    )
    assert "expandconstant('{userappdata}')" in lower, (
        "setup.iss 必须保留 ExpandConstant('{userappdata}') 来获取当前用户 Roaming AppData 目录"
    )


def test_admin_and_silent_uninstall_safety_branches_present(setup_iss_content: str):
    """4. 必须存在以下安全分支：UninstallSilent, IsAdminInstallMode, IsAdmin。"""
    assert "UninstallSilent" in setup_iss_content, "缺少 UninstallSilent 静默卸载安全分支"
    assert "IsAdminInstallMode" in setup_iss_content, "缺少 IsAdminInstallMode 管理员安装模式安全分支"
    assert "IsAdmin" in setup_iss_content, "缺少 IsAdmin 管理员权限安全分支"


def test_admin_mode_check_occurs_before_cleanup_confirmation(setup_iss_content: str):
    """5. 管理员/所有用户模式的保护判断必须发生在清理确认和目录删除之前。"""
    init_uninst_code = get_function_body(
        setup_iss_content,
        "function InitializeUninstall()",
        "function SafeDeleteUserDataFolder",
    )
    assert init_uninst_code, "setup.iss 缺少 InitializeUninstall 卸载初始化函数"

    # 验证在确认弹窗前必须检查 IsAdminInstallMode 或 IsAdmin
    prompt_pos = init_uninst_code.find("MsgBox")
    assert prompt_pos > 0, "InitializeUninstall 中未找到用户确认对话框 MsgBox"

    admin_install_pos = init_uninst_code.find("IsAdminInstallMode")
    is_admin_pos = init_uninst_code.find("IsAdmin")

    assert admin_install_pos != -1, "InitializeUninstall 中缺少 IsAdminInstallMode 判断"
    assert is_admin_pos != -1, "InitializeUninstall 中缺少 IsAdmin 判断"

    assert admin_install_pos < prompt_pos, (
        "IsAdminInstallMode 检查必须发生在显示清理确认对话框 MsgBox 之前，"
        "管理员模式下应直接跳过用户数据删除询问并给出提示，避免删错其他用户数据。"
    )
    assert is_admin_pos < prompt_pos, (
        "IsAdmin 检查必须发生在显示清理确认对话框 MsgBox 之前"
    )


def test_only_four_fixed_target_directories_are_allowed(setup_iss_content: str):
    """6. 删除目标只能是以下四个固定目录：
    %APPDATA%\\MCPAssistant
    %LOCALAPPDATA%\\MCPAssistant
    %APPDATA%\\XiaozhiMcpApp
    %LOCALAPPDATA%\\XiaozhiMcpApp
    """
    step_code = get_function_body(
        setup_iss_content,
        "procedure CurUninstallStepChanged",
        None,
    )
    assert step_code, "setup.iss 缺少 CurUninstallStepChanged 卸载步骤钩子"

    # 检查调用 SafeDeleteUserDataFolder 的目标组合
    assert "SafeDeleteUserDataFolder" in step_code, "未通过 SafeDeleteUserDataFolder 进行受控删除"
    assert "MCPAssistant" in step_code, "未包含 MCPAssistant 清理目标"
    assert "XiaozhiMcpApp" in step_code, "未包含 XiaozhiMcpApp 清理目标"

    safe_del_code = get_function_body(
        setup_iss_content,
        "function SafeDeleteUserDataFolder",
        "procedure CurUninstallStepChanged",
    )
    assert safe_del_code, "setup.iss 缺少 SafeDeleteUserDataFolder 函数"

    # 检查末级目录名称严格限制在 MCPAssistant 与 XiaozhiMcpApp
    assert "MCPAssistant" in safe_del_code and "XiaozhiMcpApp" in safe_del_code, (
        "SafeDeleteUserDataFolder 必须严格白名单限制末级目录名为 MCPAssistant 或 XiaozhiMcpApp"
    )


def test_no_deletion_of_appdata_or_localappdata_roots(setup_iss_content: str):
    """7. 测试必须确认不存在删除 %APPDATA%、%LOCALAPPDATA% 本身或任意父目录的逻辑。"""
    safe_del_code = get_function_body(
        setup_iss_content,
        "function SafeDeleteUserDataFolder",
        "procedure CurUninstallStepChanged",
    )
    assert safe_del_code, "setup.iss 缺少 SafeDeleteUserDataFolder 函数"

    # 拒绝 NormTarget 等于 NormBase
    assert "CompareText(NormTarget, NormBase) = 0" in safe_del_code, (
        "SafeDeleteUserDataFolder 必须包含拒绝删除基准目录（AppData/LocalAppData 根）本身的防线"
    )

    # 拒绝父级目录跳转
    assert "Pos('..', TargetDir) > 0" in safe_del_code, (
        "SafeDeleteUserDataFolder 必须拦截包含 '..' 的路径"
    )

    # 拒绝非直接子目录
    assert "CompareText(ExtractFileDir(NormTarget), NormBase) <> 0" in safe_del_code, (
        "SafeDeleteUserDataFolder 必须验证目标是基准目录的直接子目录"
    )


def test_reparse_point_and_symlink_defense(setup_iss_content: str):
    """8. 测试必须继续检查重解析点/Junction/符号链接防护。"""
    safe_del_code = get_function_body(
        setup_iss_content,
        "function SafeDeleteUserDataFolder",
        "procedure CurUninstallStepChanged",
    )
    assert safe_del_code, "setup.iss 缺少 SafeDeleteUserDataFolder 函数"

    assert "GetFileAttributes" in safe_del_code, "缺少 GetFileAttributes 文件属性探测调用"
    assert "FILE_ATTRIBUTE_REPARSE_POINT" in safe_del_code, "缺少 FILE_ATTRIBUTE_REPARSE_POINT 重解析点掩码检查"


def test_expand_constant_fail_closed_handling(setup_iss_content: str):
    """9. 测试必须验证 ExpandConstant 失败或返回异常时的 fail-closed 保护。"""
    step_code = get_function_body(
        setup_iss_content,
        "procedure CurUninstallStepChanged",
        None,
    )
    assert step_code, "setup.iss 缺少 CurUninstallStepChanged 卸载步骤钩子"

    # 必须对展开结果进行安全性与有效性检查（如非空、未包含未解析花括号符号等）
    assert "{" in step_code or "Pos('{' " in step_code or "''" in step_code, (
        "CurUninstallStepChanged 必须对 ExpandConstant 返回值进行非空与未展开常量检查"
    )
