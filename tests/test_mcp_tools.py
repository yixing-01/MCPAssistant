"""MCP 受控工具端到端调用与安全审计测试"""

import os
import tempfile
import pytest
from mcp.server.fastmcp import FastMCP
from src.catalog.manager import CatalogManager
from src.core.config import ConfigManager, AppConfig
from src.core.permission import permission_engine
from src.mcp_server.tools import register_mcp_tools


@pytest.fixture
def test_env():
    """搭建隔离的测试上下文"""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = os.path.join(temp_dir, "config.json")
        secrets_file = os.path.join(temp_dir, "secrets.dat")
        catalog_file = os.path.join(temp_dir, "catalog.json")

        cfg_mgr = ConfigManager()
        cfg_mgr.config_path = config_file
        cfg_mgr.secrets_path = secrets_file
        cfg_mgr.config = AppConfig(
            allow_calculator=True,
            allow_open_url=True,
            allow_run_apps=True,
            allow_cmd_commands=False,
            allow_file_write=False,
            allowed_write_dirs=[]
        )

        cat_mgr = CatalogManager(catalog_file_path=catalog_file)
        mcp = FastMCP("TestTools")
        register_mcp_tools(mcp=mcp, config_mgr=cfg_mgr, catalog_mgr=cat_mgr)

        yield {
            "mcp": mcp,
            "cfg_mgr": cfg_mgr,
            "cat_mgr": cat_mgr,
            "temp_dir": temp_dir
        }


def test_mcp_calculator(test_env):
    """测试 MCP 计算器工具"""
    mcp = test_env["mcp"]
    calc_func = None
    for tool in mcp._tool_manager.list_tools():
        if tool.name == "计算器":
            calc_func = tool.fn
            break
    assert calc_func is not None

    res_ok = calc_func(python_expression="2 + 3 * 4")
    assert res_ok["是否成功"] is True
    assert res_ok["结果"] == 14

    # 非法代码注入拒绝
    res_err = calc_func(python_expression="__import__('os').system('dir')")
    assert res_err["是否成功"] is False
    assert any(k in res_err["错误"] for k in ["未授权", "不允许", "禁止"])


def test_mcp_launch_app_security(test_env):
    """测试 MCP 软件启动工具的授权白名单保护"""
    mcp = test_env["mcp"]
    cat_mgr = test_env["cat_mgr"]
    temp_dir = test_env["temp_dir"]

    launch_func = None
    for tool in mcp._tool_manager.list_tools():
        if tool.name == "运行电脑端软件文件或程序":
            launch_func = tool.fn
            break
    assert launch_func is not None

    # 1. 尝试直接启动未授权的名称或任意路径
    res_unauth = launch_func(program_name=r"C:\Windows\System32\cmd.exe")
    assert res_unauth["是否成功"] is False
    assert "未找到" in res_unauth["错误请检查路径"]

    # 2. 手工添加并授权一个合法测试程序
    mock_exe = os.path.join(temp_dir, "myapp.exe")
    with open(mock_exe, "w") as f:
        f.write("mock")

    app_entry = cat_mgr.add_manual_app("我的应用", mock_exe, alias="myapp", authorized=True)

    # 模拟启动（对于非真实 exe，在 windows 下创建假 exe 会触发真实 subprocess，为安全我们验证已授权匹配）
    found = cat_mgr.find_authorized_app("myapp")
    assert found is not None
    assert found.display_name == "我的应用"

    # 3. 禁用后调用立即拒绝
    cat_mgr.set_app_enabled(app_entry.app_id, False)
    res_disabled = launch_func(program_name="myapp")
    assert res_disabled["是否成功"] is False
    assert "未找到" in res_disabled["错误请检查路径"] or "未被用户授权" in res_disabled["错误请检查路径"]


def test_mcp_open_url_security(test_env):
    """测试 MCP 打开网页安全限制"""
    mcp = test_env["mcp"]
    url_func = None
    for tool in mcp._tool_manager.list_tools():
        if tool.name == "在电脑上打开URL网址":
            url_func = tool.fn
            break
    assert url_func is not None

    # 拒绝 file 协议
    res_evil = url_func(url="file:///C:/Windows/win.ini")
    assert res_evil["是否成功"] is False
    assert "安全拒绝" in res_evil["错误"]


def test_mcp_file_write_security(test_env):
    """测试 MCP 文件写入受控沙箱"""
    mcp = test_env["mcp"]
    cfg_mgr = test_env["cfg_mgr"]
    temp_dir = test_env["temp_dir"]

    write_func = None
    for tool in mcp._tool_manager.list_tools():
        if tool.name == "在电脑上创建文件与写入内容":
            write_func = tool.fn
            break
    assert write_func is not None

    target_file = os.path.join(temp_dir, "test.txt")

    # 默认停用文件写入
    res_denied = write_func(file_path=target_file, content="hello")
    assert res_denied["是否成功"] is False
    assert "安全拒绝" in res_denied["错误"]

    # 开启文件写入并将 temp_dir 加入白名单
    cfg = cfg_mgr.load_config()
    cfg.allow_file_write = True
    cfg.allowed_write_dirs = [temp_dir]
    cfg_mgr.save_config(cfg)

    # 写入成功
    from unittest.mock import patch
    with patch("src.core.security.PathSandbox.is_sensitive_path", return_value=(False, "")), \
         patch("src.core.security.PathSandbox.is_safe_write_directory", return_value=(True, "")):
        res_ok = write_func(file_path=target_file, content="hello world")
        assert res_ok["是否成功"] is True
        assert os.path.exists(target_file)
        with open(target_file, "r", encoding="utf-8") as f:
            assert f.read() == "hello world"

        # 防止意外覆盖
        res_overwrite = write_func(file_path=target_file, content="overwrite")
        assert res_overwrite["是否成功"] is False
        assert "禁止自动覆盖" in res_overwrite["错误"]


def test_mcp_tools_enumeration(test_env):
    """测试 MCP 工具列表中包含启动和关闭两个对称工具"""
    mcp = test_env["mcp"]
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "运行电脑端软件文件或程序" in tool_names
    assert "关闭电脑端软件文件或程序" in tool_names


def test_mcp_close_app_security(test_env):
    """测试 MCP 软件关闭工具的安全白名单、总开关、授权控制与脱敏响应"""
    mcp = test_env["mcp"]
    cat_mgr = test_env["cat_mgr"]
    cfg_mgr = test_env["cfg_mgr"]
    temp_dir = test_env["temp_dir"]

    close_func = None
    for tool in mcp._tool_manager.list_tools():
        if tool.name == "关闭电脑端软件文件或程序":
            close_func = tool.fn
            break
    assert close_func is not None

    # 1. 尝试直接关闭未授权的软件
    res_unauth = close_func(program_name="未授权软件")
    assert res_unauth["是否成功"] is False
    assert res_unauth["关闭状态"] == "NOT_FOUND"
    assert "未找到" in res_unauth["错误请检查路径"]

    # 2. 手工添加软件并授权
    mock_exe = os.path.join(temp_dir, "myapp.exe")
    with open(mock_exe, "w") as f:
        f.write("mock")
    app_entry = cat_mgr.add_manual_app("我的测试软件", mock_exe, alias="testapp", authorized=True)

    # 注入 FakeWindowApi 到 cat_mgr.window_controller
    from tests.test_window_control import FakeWindowApi
    from src.catalog.window_control import WindowTarget
    fake_api = FakeWindowApi([
        WindowTarget(hwnd=999, pid=8888, process_path=mock_exe),
    ])
    cat_mgr.window_controller.api = fake_api

    # 3. 正常关闭已授权软件
    res_ok = close_func(program_name="testapp")
    assert res_ok["是否成功"] is True
    assert res_ok["关闭状态"] == "ALL_CLOSED"
    assert "已关闭该软件" in res_ok["结果"]
    assert fake_api.post_message_calls == [(999, 0x0010)]

    # 4. 软件处于托盘或后台（没有可见主窗口）
    fake_api.windows.clear()
    fake_api.closed_hwnds.clear()
    fake_api.post_message_calls.clear()
    res_no_win = close_func(program_name="testapp")
    assert res_no_win["是否成功"] is False
    assert res_no_win["关闭状态"] == "NO_WINDOWS"
    assert "当前没有可关闭的主窗口" in res_no_win["结果"]

    # 5. 用户取消授权后，关闭同样被拒绝
    cat_mgr.set_app_enabled(app_entry.app_id, False)
    res_disabled = close_func(program_name="testapp")
    assert res_disabled["是否成功"] is False
    assert res_disabled["关闭状态"] == "NOT_FOUND"
    assert "未找到" in res_disabled["错误请检查路径"] or "未被用户授权" in res_disabled["错误请检查路径"]

    # 6. 总开关关闭时拒绝
    cat_mgr.set_app_enabled(app_entry.app_id, True)
    cfg = cfg_mgr.load_config()
    cfg.allow_run_apps = False
    cfg_mgr.save_config(cfg)
    res_switch_off = close_func(program_name="testapp")
    assert res_switch_off["是否成功"] is False
    assert res_switch_off["关闭状态"] == "FEATURE_DISABLED"
    assert "安全拒绝" in res_switch_off["错误请检查路径"]

