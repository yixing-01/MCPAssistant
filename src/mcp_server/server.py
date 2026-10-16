"""MCP 独立子进程启动入口
由 PipeBridge 或独立命令行调用，以 FastMCP stdio 方式对外提供安全受控服务。
"""

import sys
import os

# 确保项目根路径进入 sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 确保 UTF-8 编码
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from mcp.server.fastmcp import FastMCP
from src.core.config import ConfigManager, AppPaths
from src.catalog.manager import CatalogManager
from src.core.logger import configure_for_mcp_service
from src.mcp_server.tools import register_mcp_tools


def create_server() -> FastMCP:
    """初始化并配置 FastMCP 服务器，严格保证 stdout 仅供 MCP 协议使用"""
    configure_for_mcp_service(log_dir=AppPaths.get_log_dir())
    config_mgr = ConfigManager()
    catalog_mgr = CatalogManager()

    # 保持原名称 "WindowsToos" 兼容小智识别
    mcp = FastMCP("WindowsToos")
    register_mcp_tools(mcp=mcp, config_mgr=config_mgr, catalog_mgr=catalog_mgr)
    return mcp


if __name__ == "__main__":
    mcp_app = create_server()
    mcp_app.run(transport="stdio")
