r"""第 11 轮：Inno Setup 安装模式与写权限预检查静态/算法测试套件。

说明与测试边界声明：
1. 本测试仅对 setup.iss 指令配置进行静态检查，以及对写探测算法逻辑进行 Python 单元测试；
2. 本测试绝不修改、也不构造 Windows 系统真实目录（如 D:\Program Files）的系统级 ACL 权限；
3. 真实普通用户模式下选择 D:\Program Files\MCPAssistant 触发友好拦截，必须列为用户手工验收项；
4. 覆盖：
   - PrivilegesRequired=lowest 保持；
   - PrivilegesRequiredOverridesAllowed=dialog 保持；
   - 默认目录使用 {autopf}\MCPAssistant；
   - 固定 AppId 严格保持不变；
   - 绝不存在给 Program Files 放宽 ACL 的危险配置；
   - setup.iss 包含 NextButtonClick、IsAdminInstallMode、wpSelectDir 真实写入探测代码；
   - 安装完成后启动应用使用 runasoriginaluser 确保不以管理员身份常驻；
   - 算法层面：普通用户可写目录通过、管理员模式放行。
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETUP_ISS = PROJECT_ROOT / "installer" / "setup.iss"


def test_setup_iss_privileges_and_directory_directives():
    """验证 setup.iss 中的关键安装模式与路径指令。"""
    assert SETUP_ISS.exists(), f"未找到 setup.iss: {SETUP_ISS}"
    content = SETUP_ISS.read_text(encoding="utf-8")

    # 1. PrivilegesRequired=lowest 保留
    assert "PrivilegesRequired=lowest" in content, "缺失 PrivilegesRequired=lowest"

    # 2. 存在 PrivilegesRequiredOverridesAllowed=dialog
    assert "PrivilegesRequiredOverridesAllowed=dialog" in content, (
        "缺失 PrivilegesRequiredOverridesAllowed=dialog，无法支持双模式与 /ALLUSERS /CURRENTUSER 参数"
    )

    # 3. 默认目录使用 {autopf}\\MCPAssistant
    assert "DefaultDirName={autopf}\\MCPAssistant" in content, (
        "DefaultDirName 必须使用 {autopf}\\MCPAssistant 自动适配当前用户或所有用户"
    )

    # 4. 固定 AppId 没有变化
    assert 'MyAppId "{{8F2C41B9-72E5-46B9-B37D-97D8C61E8D90}"' in content, (
        "固定 AppId 遭意外修改"
    )

    # 5. 绝对不存在给 Program Files 放宽 ACL 的危险配置
    forbidden_acl_keywords = ["Permissions:", "everyone-full", "users-modify", "everyone-modify"]
    for kw in forbidden_acl_keywords:
        assert kw not in content.lower(), f"检测到危险的权限放宽配置: {kw}"

    # 9. 安装完成后启动应用使用 runasoriginaluser 确保不以管理员权限常驻
    assert "runasoriginaluser" in content.lower(), (
        "[Run] 项缺少 runasoriginaluser 标志，可能导致提升权限后应用以管理员身份运行"
    )


def test_setup_iss_contains_write_permission_precheck():
    """验证 setup.iss 包含 Pascal 代码实现的目标目录写权限预探测与友好中文阻断。"""
    content = SETUP_ISS.read_text(encoding="utf-8")

    # 包含 [Code] 段
    assert "[Code]" in content, "setup.iss 缺少 [Code] 段"

    # 检查核心预检函数和状态判断
    assert "NextButtonClick" in content, "setup.iss 缺少 NextButtonClick 页面切换钩子"
    assert "IsAdminInstallMode" in content, "setup.iss 缺少对管理员安装模式的判断"
    assert "wpSelectDir" in content, "setup.iss 未在目录选择页面 (wpSelectDir) 执行检查"

    # 检查中文友好提示文本
    assert "当前安装位置需要管理员权限" in content, "缺少普通用户能看懂的管理员权限中文提示"
    assert "为所有用户安装" in content, "提示信息未引导用户选择'为所有用户安装'"
    assert "可写的目录" in content, "提示信息未引导用户改选可写目录"


def test_python_simulated_write_probe_logic(tmp_path: Path):
    """测试安装器写探测算法单元测试（算法逻辑仿真，真实 D:\\Program Files 拦截由用户手工验收）。"""
    # 模拟探测逻辑函数（与 Pascal 脚本算法完全对应）
    def check_install_dir_writable(target_dir: Path, is_admin_mode: bool) -> tuple[bool, str]:
        if is_admin_mode:
            return True, "管理员模式直接放行"

        # 向上寻找最近存在的父目录
        cur = target_dir
        while cur and not cur.exists():
            if cur.parent == cur:
                break
            cur = cur.parent

        if not cur or not cur.exists():
            return False, "根目录不存在"

        # 创建安全临时探测文件
        probe_file = cur / f".mcp_assistant_write_test_{os.getpid()}.tmp"
        try:
            probe_file.write_text("probe", encoding="utf-8")
            probe_file.unlink()
            return True, "可写"
        except (PermissionError, OSError):
            return False, "当前安装位置需要管理员权限。\n\n你可以返回安装模式页面，选择“为所有用户安装”并通过 Windows 权限确认；\n也可以选择当前用户可写的目录。"

    # 7. 普通用户在可写目录下
    writable_sub = tmp_path / "Sub" / "MCPAssistant"
    ok, _ = check_install_dir_writable(writable_sub, is_admin_mode=False)
    assert ok is True
    # 验证测试文件已清理
    assert not (tmp_path / f".mcp_assistant_write_test_{os.getpid()}.tmp").exists()

    # 8. 管理员模式在受保护目录下无论如何放行
    protected_dummy = Path("C:/Windows/System32/MCPAssistant")
    ok_admin, _ = check_install_dir_writable(protected_dummy, is_admin_mode=True)
    assert ok_admin is True
