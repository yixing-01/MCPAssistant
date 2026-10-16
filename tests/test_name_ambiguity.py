"""软件名称匹配歧义防御与 MCP 提示测试"""

import os
from unittest.mock import patch
import pytest

from src.catalog.models import AppEntry
from src.catalog.manager import CatalogManager, LookupStatus, AppLookupResult
from src.core.config import ConfigManager, AppConfig
from src.mcp_server.tools import register_mcp_tools
from mcp.server.fastmcp import FastMCP


def test_unique_exact_alias_hits(tmp_path):
    """唯一精确别名正确命中"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    exe1 = tmp_path / "app1.exe"
    exe1.write_text("mock")
    exe2 = tmp_path / "app2.exe"
    exe2.write_text("mock")

    manager.add_manual_app("代码编辑器", str(exe1), alias="code", authorized=True)
    manager.add_manual_app("代码格式化器", str(exe2), alias="formatter", authorized=True)

    res = manager.find_authorized_app("code")
    assert res.status == LookupStatus.FOUND
    assert res.app is not None
    assert res.app.display_name == "代码编辑器"


def test_unique_fuzzy_match_hits(tmp_path):
    """唯一模糊匹配正确命中"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    exe1 = tmp_path / "wechat.exe"
    exe1.write_text("mock")
    exe2 = tmp_path / "qq.exe"
    exe2.write_text("mock")

    manager.add_manual_app("微信电脑版", str(exe1), alias="weixin", authorized=True)
    manager.add_manual_app("腾讯QQ", str(exe2), alias="qq", authorized=True)

    res = manager.find_authorized_app("微信")
    assert res.status == LookupStatus.FOUND
    assert res.app is not None
    assert res.app.display_name == "微信电脑版"


def test_multiple_fuzzy_matches_return_ambiguous(tmp_path):
    """当模糊匹配到多个候选项时，绝不能返回第一个，必须返回歧义状态并包含所有候选项"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    exe1 = tmp_path / "vscode.exe"
    exe1.write_text("mock")
    exe2 = tmp_path / "vsinstaller.exe"
    exe2.write_text("mock")

    manager.add_manual_app("Visual Studio Code", str(exe1), alias="vscode", authorized=True)
    manager.add_manual_app("Visual Studio Installer", str(exe2), alias="installer", authorized=True)

    res = manager.find_authorized_app("Visual Studio")
    assert res.status == LookupStatus.AMBIGUOUS
    assert res.app is None
    assert len(res.candidates) == 2
    names = [c.display_name for c in res.candidates]
    assert "Visual Studio Code" in names
    assert "Visual Studio Installer" in names


def test_duplicate_display_names_return_ambiguous_unless_unique_alias(tmp_path):
    """当存在重复显示名称时，按显示名称查找返回歧义；但若使用唯一别名则可精确选择"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    exe1 = tmp_path / "app_v1.exe"
    exe1.write_text("mock")
    exe2 = tmp_path / "app_v2.exe"
    exe2.write_text("mock")

    manager.add_manual_app("记事本", str(exe1), alias="老记事本", authorized=True)
    manager.add_manual_app("记事本", str(exe2), alias="新记事本", authorized=True)

    # 1. 查找名称 "记事本" 产生歧义
    res_name = manager.find_authorized_app("记事本")
    assert res_name.status == LookupStatus.AMBIGUOUS
    assert len(res_name.candidates) == 2

    # 2. 通过唯一别名精确查找
    res_alias = manager.find_authorized_app("新记事本")
    assert res_alias.status == LookupStatus.FOUND
    assert res_alias.app.alias == "新记事本"


def test_disabled_or_invalid_apps_excluded_from_candidates(tmp_path):
    """已禁用、未授权或路径失效的软件不得计入歧义候选项"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    exe1 = tmp_path / "tool1.exe"
    exe1.write_text("mock")
    exe2 = tmp_path / "tool2.exe"
    exe2.write_text("mock")

    # 软件 1 已授权有效，软件 2 未授权
    manager.add_manual_app("调试工具 专业版", str(exe1), alias="tool_pro", authorized=True)
    manager.add_manual_app("调试工具 社区版", str(exe2), alias="tool_comm", authorized=False)

    res = manager.find_authorized_app("调试工具")
    assert res.status == LookupStatus.FOUND
    assert res.app.display_name == "调试工具 专业版"


def test_mcp_tool_handles_ambiguity_safely_without_leaking_paths(tmp_path):
    """MCP 工具遇到歧义时：不启动任何程序，返回安全中文提示，最多展示 5 个候选，绝不泄漏物理路径"""
    cat_file = tmp_path / "catalog.json"
    cfg_file = tmp_path / "config.json"
    secrets_file = tmp_path / "secrets.dat"

    cfg_mgr = ConfigManager()
    cfg_mgr.config_path = str(cfg_file)
    cfg_mgr.secrets_path = str(secrets_file)
    cfg_mgr.config = AppConfig(allow_run_apps=True)

    cat_mgr = CatalogManager(catalog_file_path=str(cat_file))
    mcp = FastMCP("AmbiguityTest")
    register_mcp_tools(mcp=mcp, config_mgr=cfg_mgr, catalog_mgr=cat_mgr)

    launch_tool = None
    for t in mcp._tool_manager.list_tools():
        if t.name == "运行电脑端软件文件或程序":
            launch_tool = t.fn
            break
    assert launch_tool is not None

    exe1 = tmp_path / "vs1.exe"
    exe1.write_text("mock")
    exe2 = tmp_path / "vs2.exe"
    exe2.write_text("mock")

    cat_mgr.add_manual_app("Visual Studio Code", str(exe1), alias="vscode", authorized=True)
    cat_mgr.add_manual_app("Visual Studio 2022", str(exe2), alias="vs2022", authorized=True)

    with patch.object(cat_mgr, "launch_app") as mock_launch:
        resp = launch_tool(program_name="Visual Studio")

        # 绝不调用 launch_app
        mock_launch.assert_not_called()

        assert resp["是否成功"] is False
        err_msg = resp["错误请检查路径"]
        assert "找到多个" in err_msg or "歧义" in err_msg
        assert "Visual Studio Code" in err_msg
        assert "Visual Studio 2022" in err_msg
        # 绝不能暴露本地路径
        assert str(tmp_path) not in err_msg
        assert "vs1.exe" not in err_msg
        assert "vs2.exe" not in err_msg


def test_mcp_close_tool_ambiguous_returns_candidate_list_without_paths(tmp_path):
    """验证关闭工具在遇到歧义匹配时拒绝执行关闭，并返回脱敏的候选项列表，绝不暴露物理路径"""
    from unittest.mock import patch
    cfg_mgr = ConfigManager()
    cfg_mgr.config_path = str(tmp_path / "config.json")
    cfg_mgr.secrets_path = str(tmp_path / "secrets.dat")
    cat_mgr = CatalogManager(catalog_file_path=str(tmp_path / "catalog.json"))
    mcp = FastMCP("TestAmbiguityClose")
    register_mcp_tools(mcp=mcp, config_mgr=cfg_mgr, catalog_mgr=cat_mgr)

    close_tool = None
    for t in mcp._tool_manager.list_tools():
        if t.name == "关闭电脑端软件文件或程序":
            close_tool = t.fn
            break
    assert close_tool is not None

    exe1 = tmp_path / "app1.exe"
    exe1.write_text("mock")
    exe2 = tmp_path / "app2.exe"
    exe2.write_text("mock")

    cat_mgr.add_manual_app("Office Word", str(exe1), alias="word", authorized=True)
    cat_mgr.add_manual_app("Office Excel", str(exe2), alias="excel", authorized=True)

    with patch.object(cat_mgr, "close_app") as mock_close:
        resp = close_tool(program_name="Office")

        # 绝不调用 close_app
        mock_close.assert_not_called()

        assert resp["是否成功"] is False
        err_msg = resp["错误请检查路径"]
        assert "找到多个" in err_msg or "歧义" in err_msg
        assert "Office Word" in err_msg
        assert "Office Excel" in err_msg
        # 绝不能暴露本地路径
        assert str(tmp_path) not in err_msg
        assert "app1.exe" not in err_msg
        assert "app2.exe" not in err_msg



def test_store_app_matches_every_explicit_codex_alias(tmp_path):
    manager = CatalogManager(catalog_file_path=str(tmp_path / "catalog.json"))
    app = AppEntry(
        app_id="chatgpt",
        display_name="ChatGPT",
        aliases=["ChatGPT", "Codex", "OpenAI Codex", "codeex"],
        launch_type="store_aumid",
        launch_target="OpenAI.Codex_2p2nqsd0c76g0!App",
        stable_identity="store:openai.codex_2p2nqsd0c76g0!app",
        source="AppsFolder",
        is_enabled=True,
        is_valid=True,
    )
    manager.apps[app.app_id] = app
    for query in ("ChatGPT", "Codex", "OpenAI Codex", "codeex"):
        result = manager.find_authorized_app(query)
        assert result.status == LookupStatus.FOUND
        assert result.app.app_id == "chatgpt"


def test_normalization_does_not_override_real_ambiguity(tmp_path):
    manager = CatalogManager(catalog_file_path=str(tmp_path / "catalog.json"))
    manager.apps = {
        "one": AppEntry(
            app_id="one", display_name="A B", aliases=["A-B"],
            launch_type="store_aumid", launch_target="Vendor.One!App",
            stable_identity="store:vendor.one!app", source="AppsFolder",
            is_enabled=True, is_valid=True,
        ),
        "two": AppEntry(
            app_id="two", display_name="AB", aliases=["A_B"],
            launch_type="store_aumid", launch_target="Vendor.Two!App",
            stable_identity="store:vendor.two!app", source="AppsFolder",
            is_enabled=True, is_valid=True,
        ),
    }
    assert manager.find_authorized_app("ab").status == LookupStatus.AMBIGUOUS
