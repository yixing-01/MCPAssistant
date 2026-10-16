"""MCP助手 - 程序主入口"""

import sys
import os

# 确保项目根路径进入 sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 确保标准输出与标准错误 UTF-8 编码
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.core.config import AppPaths


def main():
    # 优先检查是否以独立子进程 MCP 服务模式启动
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp-service":
        from src.core.logger import configure_for_mcp_service, app_logger, sanitize_text
        # 为 MCP 服务配置日志，严格只输出到 stderr 和文件，严禁向 stdout 输出任何日志
        configure_for_mcp_service(log_dir=AppPaths.get_log_dir())
        try:
            from src.mcp_server.server import create_server
            server = create_server()
            server.run(transport="stdio")
            sys.exit(0)
        except BaseException as e:
            # 捕获包括 ExceptionGroup, SystemExit 等在内的顶级异常
            if isinstance(e, SystemExit):
                if e.code in (0, None):
                    sys.exit(0)
                sys.exit(e.code)

            # 提取简短脱敏错误摘要
            err_type = type(e).__name__
            err_msg = sanitize_text(str(e))
            if hasattr(e, "exceptions"):
                sub_types = [type(sub).__name__ for sub in e.exceptions]
                summary = f"{err_type}: [{', '.join(sub_types)}] {err_msg}"
            else:
                summary = f"{err_type}: {err_msg}"

            # 严格只向 stderr 和日志记录，严禁向 stdout 写入任何内容
            try:
                app_logger.error(f"[MCP子进程异常退出] {summary}")
                sys.stderr.write(f"MCP service error: {summary}\n")
                sys.stderr.flush()
            except Exception:
                pass

            # 以非零状态码干净退出，杜绝异常泄露给 PyInstaller bootloader 导致模态弹窗
            sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "--smoke-store-enum":
        from src.catalog.store_apps import StoreAppProvider
        provider = StoreAppProvider()
        apps = provider.enumerate_apps()
        # 仅输出是否成功及是否包含验证过的 ChatGPT AUMID，绝不输出完整软件清单
        has_chatgpt = any(
            a.aumid.casefold() == "openai.codex_2p2nqsd0c76g0!app" for a in apps
        )
        print(f"STORE_ENUM_OK: count={len(apps)}, has_chatgpt={has_chatgpt}")
        return

    if "--qt-smoke" in sys.argv:
        from src.ui_qt.app import run_qt_smoke
        raise SystemExit(run_qt_smoke(sys.argv))

    # 默认模式：启动现代图形界面
    from src.core.logger import setup_logger, app_logger
    setup_logger(log_dir=AppPaths.get_log_dir())

    # 获取全局单实例互斥体锁，防止重复启动多个 GUI
    from src.core.single_instance import SingleInstanceLock
    lock = SingleInstanceLock()
    if not lock.acquire():
        app_logger.info("检测到已存在运行中的应用实例，本进程将直接退出")
        return
    try:
        start_in_bg = "--background" in sys.argv
        app_logger.info(
            f"正在启动MCP助手图形界面 (后台模式={start_in_bg})..."
        )
        from src.ui_qt.app import run_qt
        run_qt(sys.argv, start_in_background=start_in_bg)
    finally:
        lock.release()


if __name__ == "__main__":
    main()

