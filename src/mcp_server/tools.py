"""MCP 工具实现与注册模块
向 FastMCP 注册符合小智原有函数名与参数签名的工具，但内部实施强制安全防御与审计。
"""

import os
import subprocess
import tempfile
import webbrowser
from typing import Dict, Any
from urllib.parse import urlsplit
from mcp.server.fastmcp import FastMCP

from src.catalog.manager import CatalogManager, LookupStatus
from src.catalog.window_control import CloseStatus
from src.core.config import ConfigManager
from src.core.logger import app_logger, sanitize_text
from src.core.permission import permission_engine
from src.core.security import (
    SafeCalculator,
    UrlValidator,
    PathSandbox,
    CommandValidator,
)


def register_mcp_tools(mcp: FastMCP, config_mgr: ConfigManager, catalog_mgr: CatalogManager):
    """向 FastMCP 实例注册全部受控工具"""

    # ---------------------------------------------------------------------------------------------
    # 1. 计算器工具（基于 AST 安全解析器，彻底禁止 eval）
    # ---------------------------------------------------------------------------------------------
    @mcp.tool()
    def 计算器(python_expression: str) -> dict:
        """
        用于数学计算时，请始终使用此工具来计算数学表达式的结果。
        支持常用加减乘除、乘方与数学函数（如 sqrt, sin, cos, log 等）。
        """
        expr_len = len(python_expression) if isinstance(python_expression, str) else 0
        target_summary = f"表达式[长度={expr_len}]"

        cfg = config_mgr.load_config()
        if not cfg.allow_calculator:
            err = "安全拒绝：计算器功能已被管理员在权限设置中停用"
            app_logger.warning(f"计算器功能已停用，拒绝执行: {target_summary}")
            permission_engine.log_audit("计算器", "数学计算", target_summary, False, err)
            return {"是否成功": False, "错误": err}

        try:
            result = SafeCalculator.evaluate(python_expression)
            import json
            try:
                json.dumps(result)
            except Exception:
                err_msg = "计算失败：计算结果类型无法序列化"
                app_logger.warning(f"安全计算结果序列化失败 ({target_summary})")
                permission_engine.log_audit("计算器", "数学计算", target_summary, False, err_msg)
                return {"是否成功": False, "错误": err_msg}

            app_logger.info(f"安全计算成功 ({target_summary}, 类型={type(result).__name__})")
            permission_engine.log_audit("计算器", "数学计算", target_summary, True, f"计算完成 (类型={type(result).__name__})")
            return {"是否成功": True, "结果": result}
        except ZeroDivisionError:
            err_msg = "计算失败：除数不能为零"
            app_logger.warning(f"计算受阻 ({target_summary}): 除数不能为零")
            permission_engine.log_audit("计算器", "数学计算", target_summary, False, err_msg)
            return {"是否成功": False, "错误": err_msg}
        except OverflowError:
            err_msg = "计算失败：数值过大，超出计算上限"
            app_logger.warning(f"计算受阻 ({target_summary}): 数值溢出")
            permission_engine.log_audit("计算器", "数学计算", target_summary, False, err_msg)
            return {"是否成功": False, "错误": err_msg}
        except MemoryError:
            err_msg = "计算失败：计算所需内存过大，已终止计算"
            app_logger.warning(f"计算受阻 ({target_summary}): 内存超出")
            permission_engine.log_audit("计算器", "数学计算", target_summary, False, err_msg)
            return {"是否成功": False, "错误": err_msg}
        except ValueError as e:
            raw_e = str(e)
            if "语法错误" in raw_e:
                category = "表达式语法错误"
            elif any(w in raw_e for w in ("未授权", "不允许", "禁止", "非法")):
                category = "包含未授权变量或非法标识符"
            elif "复数" in raw_e or "类型" in raw_e or "布尔" in raw_e:
                category = "包含不支持的数值类型"
            elif "超出" in raw_e or "过大" in raw_e or "上限" in raw_e or "递归" in raw_e or "层级" in raw_e:
                category = "表达式复杂度超出安全上限"
            elif "NaN" in raw_e or "Infinity" in raw_e:
                category = "结果不能为 NaN 或 Infinity"
            else:
                category = "无效的数学表达式"
            err_msg = f"计算失败：{category}"
            app_logger.warning(f"计算受阻 ({target_summary}): {category}")
            permission_engine.log_audit("计算器", "数学计算", target_summary, False, err_msg)
            return {"是否成功": False, "错误": err_msg}
        except Exception:
            err_msg = "计算失败：表达式计算异常"
            app_logger.warning(f"计算受阻 ({target_summary}): 未知异常")
            permission_engine.log_audit("计算器", "数学计算", target_summary, False, err_msg)
            return {"是否成功": False, "错误": err_msg}

    # ---------------------------------------------------------------------------------------------
    # 2. 运行电脑端程序（白名单管控，禁止任意路径启动）
    # ---------------------------------------------------------------------------------------------
    @mcp.tool()
    def 运行电脑端软件文件或程序(program_name: str) -> dict:
        """
        运行用户已在软件目录中授权的电脑程序
        参数： program_name: 程序名称或别名，例如 "记事本" 或 "Photoshop"
        """
        cfg = config_mgr.load_config()
        if not cfg.allow_run_apps:
            err = "安全拒绝：软件启动功能已被管理员在权限设置中停用"
            permission_engine.log_audit("运行电脑端软件文件或程序", "软件启动", program_name, False, err)
            return {"是否成功": False, "错误请检查路径": err}

        # 重新加载最新目录缓存
        catalog_mgr.load_catalog()
        lookup_res = catalog_mgr.find_authorized_app(program_name)

        if lookup_res.status == LookupStatus.AMBIGUOUS:
            candidate_names = []
            for c in lookup_res.candidates[:5]:
                aliases = [alias for alias in c.aliases if alias.casefold() != c.display_name.casefold()]
                label = c.display_name
                if aliases:
                    label += f"（可说：{'、'.join(aliases[:3])}）"
                candidate_names.append(label)
            candidate_list_str = "、".join(candidate_names)
            err = f"找到多个匹配的软件条目存在歧义：[{candidate_list_str}]。为保障安全，请指定完整的软件名称或为其设置专属别名后再试。"
            app_logger.warning(f"软件启动命中歧义: {program_name}, 候选项: {candidate_list_str}")
            permission_engine.log_audit("运行电脑端软件文件或程序", "软件启动", program_name, False, err)
            return {"是否成功": False, "错误请检查路径": err}

        if lookup_res.status != LookupStatus.FOUND or not lookup_res.app:
            err = f"未找到名为 '{program_name}' 的已授权软件。如需使用，请先在 APP 软件目录中搜索并启用授权，或手动添加该便携软件。"
            app_logger.warning(f"尝试启动未授权软件: {program_name}")
            permission_engine.log_audit("运行电脑端软件文件或程序", "软件启动", program_name, False, err)
            return {"是否成功": False, "错误请检查路径": err}

        app_entry = lookup_res.app

        try:
            launch_msg = catalog_mgr.launch_app(app_entry)
            permission_engine.log_audit(
                "运行电脑端软件文件或程序",
                "软件启动",
                f"{app_entry.display_name} [{app_entry.app_id}]",
                True,
                "软件已成功启动"
            )
            return {"是否成功": True, "结果": launch_msg}
        except PermissionError as pe:
            app_logger.warning(f"启动软件被安全策略拒绝: {app_entry.display_name} [{app_entry.app_id}]")
            safe_err = "安全拒绝：该软件尚未被用户授权启动，或未通过安全校验，禁止启动"
            permission_engine.log_audit(
                "运行电脑端软件文件或程序",
                "软件启动",
                f"{app_entry.display_name} [{app_entry.app_id}]",
                False,
                "未通过安全校验或未授权启动"
            )
            return {"是否成功": False, "错误请检查路径": safe_err}
        except FileNotFoundError:
            app_logger.warning(f"启动软件路径失效: {app_entry.display_name} [{app_entry.app_id}]")
            safe_err = "启动失败：软件路径已经失效或文件已被移除，请在客户端重新验证"
            permission_engine.log_audit(
                "运行电脑端软件文件或程序",
                "软件启动",
                f"{app_entry.display_name} [{app_entry.app_id}]",
                False,
                "软件路径失效或文件不存在"
            )
            return {"是否成功": False, "错误请检查路径": safe_err}
        except Exception as e:
            app_logger.error(f"启动软件失败: {app_entry.display_name} [{app_entry.app_id}]")
            safe_err = "软件启动失败，请在软件目录中重新验证该程序"
            permission_engine.log_audit(
                "运行电脑端软件文件或程序",
                "软件启动",
                f"{app_entry.display_name} [{app_entry.app_id}]",
                False,
                "软件启动过程发生异常"
            )
            return {"是否成功": False, "错误请检查路径": safe_err}

    # ---------------------------------------------------------------------------------------------
    # 2.1 关闭电脑端程序（白名单管控，只能关闭用户已授权软件的可见主窗口）
    # ---------------------------------------------------------------------------------------------
    @mcp.tool()
    def 关闭电脑端软件文件或程序(program_name: str) -> dict:
        """
        关闭用户已在软件目录中授权的电脑程序的可见主窗口
        参数： program_name: 程序名称或别名，例如 "记事本" 或 "网易云音乐"
        """
        cfg = config_mgr.load_config()
        if not cfg.allow_run_apps:
            err = "安全拒绝：软件控制功能已被管理员在权限设置中停用"
            permission_engine.log_audit("关闭电脑端软件文件或程序", "软件关闭", program_name, False, err)
            return {"是否成功": False, "关闭状态": "FEATURE_DISABLED", "错误请检查路径": err}

        # 重新加载最新目录缓存
        catalog_mgr.load_catalog()
        lookup_res = catalog_mgr.find_authorized_app(program_name)

        if lookup_res.status == LookupStatus.AMBIGUOUS:
            candidate_names = []
            for c in lookup_res.candidates[:5]:
                aliases = [alias for alias in c.aliases if alias.casefold() != c.display_name.casefold()]
                label = c.display_name
                if aliases:
                    label += f"（可说：{'、'.join(aliases[:3])}）"
                candidate_names.append(label)
            candidate_list_str = "、".join(candidate_names)
            err = f"找到多个匹配的软件条目存在歧义：[{candidate_list_str}]。为保障安全，请指定完整的软件名称或为其设置专属别名后再试。"
            app_logger.warning(f"软件关闭命中歧义: {program_name}, 候选项: {candidate_list_str}")
            permission_engine.log_audit("关闭电脑端软件文件或程序", "软件关闭", program_name, False, err)
            return {"是否成功": False, "关闭状态": "AMBIGUOUS", "错误请检查路径": err}

        if lookup_res.status != LookupStatus.FOUND or not lookup_res.app:
            err = f"未找到名为 '{program_name}' 的已授权软件。如需使用，请先在 APP 软件目录中搜索并启用授权，或手动添加该便携软件。"
            app_logger.warning(f"尝试关闭未授权软件: {program_name}")
            permission_engine.log_audit("关闭电脑端软件文件或程序", "软件关闭", program_name, False, err)
            return {"是否成功": False, "关闭状态": "NOT_FOUND", "错误请检查路径": err}

        app_entry = lookup_res.app

        try:
            close_res = catalog_mgr.close_app(app_entry)
            is_success = (close_res.status == CloseStatus.ALL_CLOSED)
            permission_engine.log_audit(
                "关闭电脑端软件文件或程序",
                "软件关闭",
                f"{app_entry.display_name} [{app_entry.app_id}]",
                is_success,
                close_res.user_message
            )
            resp = {
                "是否成功": is_success,
                "关闭状态": close_res.status.value,
            }
            if is_success:
                resp["结果"] = close_res.user_message
            elif close_res.status in (CloseStatus.NO_WINDOWS, CloseStatus.SOME_REMAINING, CloseStatus.ALL_REMAINING):
                resp["结果"] = close_res.user_message
            else:
                resp["错误请检查路径"] = close_res.user_message
            return resp
        except PermissionError:
            app_logger.warning(f"关闭软件被安全策略拒绝: {app_entry.display_name} [{app_entry.app_id}]")
            safe_err = "安全拒绝：该软件尚未被用户授权控制，或未通过安全校验，禁止关闭"
            permission_engine.log_audit(
                "关闭电脑端软件文件或程序",
                "软件关闭",
                f"{app_entry.display_name} [{app_entry.app_id}]",
                False,
                "未通过安全校验或未授权控制"
            )
            return {"是否成功": False, "关闭状态": "PERMISSION_DENIED", "错误请检查路径": safe_err}
        except FileNotFoundError:
            app_logger.warning(f"关闭软件路径失效: {app_entry.display_name} [{app_entry.app_id}]")
            safe_err = "关闭失败：软件路径已经失效或文件已被移除，请在客户端重新验证"
            permission_engine.log_audit(
                "关闭电脑端软件文件或程序",
                "软件关闭",
                f"{app_entry.display_name} [{app_entry.app_id}]",
                False,
                "软件路径失效或文件不存在"
            )
            return {"是否成功": False, "关闭状态": "FILE_NOT_FOUND", "错误请检查路径": safe_err}
        except Exception:
            app_logger.error(f"关闭软件失败: {app_entry.display_name} [{app_entry.app_id}]")
            safe_err = "关闭软件主窗口失败，请在软件目录中重新验证该程序"
            permission_engine.log_audit(
                "关闭电脑端软件文件或程序",
                "软件关闭",
                f"{app_entry.display_name} [{app_entry.app_id}]",
                False,
                "关闭主窗口过程发生异常"
            )
            return {"是否成功": False, "关闭状态": "ERROR", "错误请检查路径": safe_err}

    # ---------------------------------------------------------------------------------------------
    # 3. 打开 URL 网址（仅允许 http/https）
    # ---------------------------------------------------------------------------------------------
    @mcp.tool()
    def 在电脑上打开URL网址(url: str) -> dict:
        """
        在电脑浏览器上打开指定 URL 的网页
        参数： url: 网页 URL，例如 "https://www.baidu.com"
        """
        cfg = config_mgr.load_config()
        url_summary = "未知网址"
        try:
            parts = urlsplit(url)
            if parts.scheme and parts.netloc:
                url_summary = f"{parts.scheme}://{parts.netloc}/*"
        except Exception:
            pass

        if not cfg.allow_open_url:
            err = "安全拒绝：网页打开功能已被停用"
            permission_engine.log_audit("在电脑上打开URL网址", "打开网页", url_summary, False, err)
            return {"是否成功": False, "错误": err}

        try:
            clean_url = UrlValidator.validate_url(url)
            parts = urlsplit(clean_url)
            url_summary = f"{parts.scheme}://{parts.netloc}/*"
            opened = webbrowser.open(clean_url)
            if not opened:
                err = "打开网页失败：系统未能启动默认浏览器"
                app_logger.warning(f"打开网页失败: {url_summary}, 原因: {err}")
                permission_engine.log_audit("在电脑上打开URL网址", "打开网页", url_summary, False, err)
                return {"是否成功": False, "错误": err}

            app_logger.info(f"成功打开网页: {url_summary}")
            permission_engine.log_audit("在电脑上打开URL网址", "打开网页", url_summary, True, "网页已在浏览器中打开")
            return {"是否成功": True, "结果": f"网页已打开：{clean_url}"}
        except Exception as e:
            err_msg = str(e)
            app_logger.warning(f"打开网页失败: {url_summary}, 原因: {err_msg}")
            permission_engine.log_audit("在电脑上打开URL网址", "打开网页", url_summary, False, err_msg)
            return {"是否成功": False, "错误": err_msg}

    # ---------------------------------------------------------------------------------------------
    # 4. 在电脑上运行 CMD 命令（严格白名单控制，禁止任意 Shell 注入）
    # ---------------------------------------------------------------------------------------------
    @mcp.tool()
    def 在电脑上运行CMD命令(command_name: str) -> dict:
        """
        运行预设命令白名单中的系统命令（默认关闭）
        参数： command_name: CMD指令名称，例如 "IP配置" 或 "系统信息"
        """
        cfg = config_mgr.load_config()

        try:
            resolved_cmd = CommandValidator.resolve_command(
                command_name_or_text=command_name,
                whitelist=cfg.cmd_whitelist,
                allow_cmd=cfg.allow_cmd_commands
            )
            # 使用列表传参以避免 shell=True 代码拼接注入风险
            output = subprocess.check_output(
                resolved_cmd,
                shell=True,  # 某些内部命令如 ipconfig/netstat 需要通过 cmd 解析，但输入来自严格白名单
                stderr=subprocess.STDOUT,
                text=True,
                timeout=15
            )
            app_logger.info(f"执行受信任白名单命令: {resolved_cmd}")
            permission_engine.log_audit("在电脑上运行CMD命令", "系统命令", resolved_cmd, True, "执行成功")
            return {"是否成功": True, "结果": f"命令执行成功：\n{output}"}
        except Exception as e:
            err_msg = str(e)
            app_logger.warning(f"执行命令受阻: {command_name}, 原因: {err_msg}")
            permission_engine.log_audit("在电脑上运行CMD命令", "系统命令", command_name, False, err_msg)
            return {"是否成功": False, "错误": err_msg}

    # ---------------------------------------------------------------------------------------------
    # 5. 在电脑上创建文件与写入内容（受控目录沙箱，防止目录穿越与敏感覆盖）
    # ---------------------------------------------------------------------------------------------
    @mcp.tool()
    def 在电脑上创建文件与写入内容(file_path: str, content: str) -> dict:
        """
        在用户授权的受信任目录中创建文件并写入内容
        参数： file_path: 文件路径
        参数： content: 要写入的内容
        """
        cfg = config_mgr.load_config()
        target_summary = os.path.basename(file_path.strip().strip('"').strip("'")) if isinstance(file_path, str) else "未知目标"
        if not target_summary:
            target_summary = "未知目标"

        if not cfg.allow_file_write:
            err = "安全拒绝：文件写入功能已被停用。如需启用，请在设置中开启并添加允许写入的目录。"
            permission_engine.log_audit("在电脑上创建文件与写入内容", "文件写入", target_summary, False, err)
            return {"是否成功": False, "错误": err}

        try:
            safe_path = PathSandbox.validate_write_path(
                file_path=file_path,
                allowed_dirs=cfg.allowed_write_dirs
            )
            target_summary = os.path.basename(safe_path)

            # 若文件已存在，阻止意外覆盖
            if os.path.exists(safe_path):
                err = f"安全保护：目标文件已存在 ({target_summary})，为避免重要数据丢失，禁止自动覆盖已有文件。"
                permission_engine.log_audit("在电脑上创建文件与写入内容", "文件写入", target_summary, False, err)
                return {"是否成功": False, "错误": err}

            target_dir = os.path.dirname(safe_path)
            os.makedirs(target_dir, exist_ok=True)

            # 原子写入临时文件再移动替换，发生异常时清理临时文件
            temp_file = tempfile.NamedTemporaryFile(
                mode="w",
                dir=target_dir,
                prefix=".tmp_write_",
                delete=False,
                encoding="utf-8"
            )
            temp_path = temp_file.name
            try:
                temp_file.write(content)
                temp_file.flush()
                temp_file.close()

                if os.path.exists(safe_path):
                    raise FileExistsError(f"目标文件已存在 ({target_summary})，禁止自动覆盖已有文件")

                os.replace(temp_path, safe_path)
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

            msg = f"文件已创建并写入成功：{safe_path}"
            app_logger.info(f"文件已创建并写入成功: {target_summary}")
            permission_engine.log_audit("在电脑上创建文件与写入内容", "文件写入", target_summary, True, "创建并写入成功")
            return {"是否成功": True, "结果": msg}
        except Exception as e:
            err_msg = str(e)
            app_logger.error(f"写入文件失败: {target_summary}, 原因: {err_msg}")
            permission_engine.log_audit("在电脑上创建文件与写入内容", "文件写入", target_summary, False, err_msg)
            return {"是否成功": False, "错误": err_msg}
