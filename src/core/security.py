"""安全与沙箱引擎模块
实现 AST 数学表达式安全解析器、URL 白名单校验、受控文件写入沙箱以及命令白名单防注入。
"""

import ast
import ipaddress
import math
import os
import re
import socket
import threading
import urllib.parse
from typing import Any, Dict, List, Set, Tuple, Union


# -------------------------------------------------------------------------------------------------
# 1. AST 安全数学计算器（彻底消除 eval 远程代码执行风险）
# -------------------------------------------------------------------------------------------------
def _validate_calc_result(val: Any) -> Union[int, float]:
    """验证计算结果类型与范围，确保仅返回可安全序列化的 int 或有限 float。"""
    if isinstance(val, bool):
        raise ValueError("计算结果不能为布尔值")
    if isinstance(val, complex):
        raise ValueError("计算结果不能为复数")
    if isinstance(val, int):
        if abs(val) > 10**40:
            raise ValueError("计算结果过大，超出安全计算上限")
        return val
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            raise ValueError("计算结果不能为 NaN 或 Infinity")
        return val
    raise ValueError(f"不允许的计算结果类型: {type(val).__name__}")


def _safe_pow(base: Any, exp: Any) -> Union[int, float]:
    """安全的幂运算函数，对指数和预估结果施加统一上限。"""
    if not isinstance(base, (int, float)) or isinstance(base, bool):
        raise ValueError("幂运算底数必须为数值类型")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        raise ValueError("幂运算指数必须为数值类型")

    if abs(exp) > 100:
        raise ValueError("幂指数过大 (上限 100)，防止拒绝服务计算")

    # 负底数的非整数指数会产生复数，在运算前立即拒绝
    if base < 0:
        if isinstance(exp, float) and not exp.is_integer():
            raise ValueError("不支持负数的非整数次幂（结果为复数）")

    # 在创建巨大数值前预检查预计结果大小
    if exp > 0 and abs(base) > 1:
        try:
            est_digits = exp * math.log10(abs(base))
            if est_digits > 40:
                raise ValueError("幂运算结果过大，超出安全计算上限 (10^40)")
        except (OverflowError, ValueError):
            raise ValueError("幂运算结果过大，超出安全计算上限")

    try:
        res = base ** exp
    except OverflowError:
        raise ValueError("数值过大，超出计算上限")

    return _validate_calc_result(res)


def _safe_sum(seq: Any) -> Union[int, float]:
    """安全的求和函数，严格限制序列长度与数值类型。"""
    if not isinstance(seq, (list, tuple)):
        raise ValueError("sum 参数必须为数值列表或元组")
    if len(seq) > 20:
        raise ValueError("列表或元组长度超过安全上限 (最多 20 个元素)")
    for x in seq:
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            raise ValueError("序列元素必须为纯数值类型")
    try:
        res = sum(seq)
    except OverflowError:
        raise ValueError("数值累加溢出")
    return res


def _safe_min(*args: Any) -> Union[int, float]:
    """安全的取最小值函数。"""
    items = args[0] if (len(args) == 1 and isinstance(args[0], (list, tuple))) else args
    if len(items) > 20:
        raise ValueError("min 参数数量超过安全上限 (最多 20 个元素)")
    for x in items:
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            raise ValueError("参数必须为纯数值类型")
    return min(items)


def _safe_max(*args: Any) -> Union[int, float]:
    """安全的取最大值函数。"""
    items = args[0] if (len(args) == 1 and isinstance(args[0], (list, tuple))) else args
    if len(items) > 20:
        raise ValueError("max 参数数量超过安全上限 (最多 20 个元素)")
    for x in items:
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            raise ValueError("参数必须为纯数值类型")
    return max(items)


class SafeCalculator:
    """基于 AST 白名单的安全数学计算器，仅允许数字、运算符与批准的数学函数"""

    MAX_EXPR_LENGTH = 200
    MAX_NODES = 40
    MAX_RECURSION_DEPTH = 10
    MAX_INT_VAL = 10**40

    # 允许的安全数学函数与常量白名单
    ALLOWED_NAMES: Dict[str, Any] = {
        # 基础内建函数（经过安全包装）
        "abs": abs,
        "round": round,
        "min": _safe_min,
        "max": _safe_max,
        "pow": _safe_pow,
        "sum": _safe_sum,
        # math 模块常用数学常量与函数
        "pi": math.pi,
        "e": math.e,
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "asin": math.asin,
        "acos": math.acos,
        "atan": math.atan,
        "log": math.log,
        "log10": math.log10,
        "log2": math.log2,
        "exp": math.exp,
        "ceil": math.ceil,
        "floor": math.floor,
        "degrees": math.degrees,
        "radians": math.radians,
    }

    # 允许访问的数学模块名（如允许 math.sqrt(4) 语法）
    ALLOWED_MODULES = {"math"}

    @classmethod
    def evaluate(cls, expression: str) -> Union[int, float]:
        """安全计算数学表达式字符串"""
        if not expression or not isinstance(expression, str):
            raise ValueError("表达式不能为空且必须为字符串")

        expression = expression.strip()
        if len(expression) > cls.MAX_EXPR_LENGTH:
            raise ValueError(f"表达式长度超出安全限制（最多{cls.MAX_EXPR_LENGTH}字符）")

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"数学表达式语法错误: {e}")

        # AST 节点总数上限检查
        all_nodes = list(ast.walk(tree))
        if len(all_nodes) > cls.MAX_NODES:
            raise ValueError(f"表达式过于复杂 (AST 节点数超出上限 {cls.MAX_NODES})")

        try:
            res = cls._eval_node(tree.body, depth=0)
        except ZeroDivisionError:
            raise ZeroDivisionError("除数不能为零")
        except OverflowError:
            raise OverflowError("计算结果溢出，超出数值上限")
        except MemoryError:
            raise MemoryError("计算所需内存过大，已终止计算")

        return _validate_calc_result(res)

    @classmethod
    def _eval_node(cls, node: ast.AST, depth: int = 0) -> Any:
        if depth > cls.MAX_RECURSION_DEPTH:
            raise ValueError("表达式嵌套层级过深，超出安全递归上限")

        # 常量数值
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                raise ValueError("不支持布尔值常量")
            if isinstance(node.value, (int, float)):
                return _validate_calc_result(node.value)
            raise ValueError(f"不允许的常量类型: {type(node.value).__name__}")

        # 二元运算符（加减乘除、幂、取模、整除）
        elif isinstance(node, ast.BinOp):
            left = cls._eval_node(node.left, depth + 1)
            right = cls._eval_node(node.right, depth + 1)

            # 严格限制二元运算仅支持纯数值类型，坚决阻止序列复制或字符串拼接
            if not isinstance(left, (int, float)) or isinstance(left, bool):
                raise ValueError("操作数必须为纯数值类型，禁止非数值操作")
            if not isinstance(right, (int, float)) or isinstance(right, bool):
                raise ValueError("操作数必须为纯数值类型，禁止非数值操作")

            if isinstance(node.op, ast.Add):
                res = left + right
            elif isinstance(node.op, ast.Sub):
                res = left - right
            elif isinstance(node.op, ast.Mult):
                # 乘法预检查防巨大分配
                if isinstance(left, int) and isinstance(right, int):
                    if left != 0 and right != 0:
                        est_digits = math.log10(abs(left)) + math.log10(abs(right))
                        if est_digits > 40:
                            raise ValueError("乘法运算结果过大，超出安全计算上限")
                res = left * right
            elif isinstance(node.op, ast.Div):
                if right == 0:
                    raise ZeroDivisionError("除数不能为零")
                res = left / right
            elif isinstance(node.op, ast.FloorDiv):
                if right == 0:
                    raise ZeroDivisionError("除数不能为零")
                res = left // right
            elif isinstance(node.op, ast.Mod):
                if right == 0:
                    raise ZeroDivisionError("除数不能为零")
                res = left % right
            elif isinstance(node.op, ast.Pow):
                res = _safe_pow(left, right)
            else:
                raise ValueError(f"不支持的二元操作符: {type(node.op).__name__}")

            return _validate_calc_result(res)

        # 一元操作符（正负号）
        elif isinstance(node, ast.UnaryOp):
            operand = cls._eval_node(node.operand, depth + 1)
            if not isinstance(operand, (int, float)) or isinstance(operand, bool):
                raise ValueError("一元操作数必须为纯数值类型")
            if isinstance(node.op, ast.UAdd):
                return _validate_calc_result(+operand)
            elif isinstance(node.op, ast.USub):
                return _validate_calc_result(-operand)
            else:
                raise ValueError(f"不支持的一元操作符: {type(node.op).__name__}")

        # 标识符（如 pi, e）
        elif isinstance(node, ast.Name):
            if node.id in cls.ALLOWED_NAMES:
                val = cls.ALLOWED_NAMES[node.id]
                return val
            raise ValueError(f"未授权的变量或标识符: '{node.id}'")

        # 属性访问（仅允许 math.xxx 语法）
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in cls.ALLOWED_MODULES:
                if node.attr in cls.ALLOWED_NAMES:
                    return cls.ALLOWED_NAMES[node.attr]
                raise ValueError(f"未授权的 math 属性或函数: '{node.attr}'")
            raise ValueError("禁止非法的属性访问")

        # 函数调用
        elif isinstance(node, ast.Call):
            func = cls._eval_node(node.func, depth + 1)
            if not callable(func):
                raise ValueError("尝试调用的对象不可执行")
            args = [cls._eval_node(arg, depth + 1) for arg in node.args]
            if node.keywords:
                raise ValueError("函数调用不支持关键字参数")
            res = func(*args)
            return _validate_calc_result(res)

        # 列表/元组参数（仅作为参数，严格限长 20 且必须全为数值）
        elif isinstance(node, (ast.List, ast.Tuple)):
            if len(node.elts) > 20:
                raise ValueError("列表或元组元素数量超出安全上限 (最多 20 个)")
            elements = [cls._eval_node(el, depth + 1) for el in node.elts]
            for el in elements:
                if not isinstance(el, (int, float)) or isinstance(el, bool):
                    raise ValueError("序列中仅允许包含纯数值类型")
            return elements

        else:
            raise ValueError(f"表达式中包含未授权的代码节点: {type(node).__name__}")


# -------------------------------------------------------------------------------------------------
# 2. 安全 URL 校验器（只允许 http 与 https，严禁危险协议、内网回环与 SSRF）
# -------------------------------------------------------------------------------------------------
class UrlValidator:
    """URL 安全校验器（严格限制公网 HTTP/HTTPS，防御 SSRF 与私网探测）"""

    ALLOWED_SCHEMES = {"http", "https"}
    ALLOWED_PORTS = {80, 443, 8080, 8443}
    MAX_URL_LENGTH = 2048
    DNS_RESOLVE_TIMEOUT_SECONDS = 2.0
    MAX_CONCURRENT_DNS_RESOLUTIONS = 4

    _dns_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_DNS_RESOLUTIONS)

    @classmethod
    def validate_url(cls, url: str) -> str:
        """检查并规范化 URL，拦截非 http/https、userinfo、私网/回环/组播/保留地址及异常端口"""
        if not url or not isinstance(url, str):
            raise ValueError("URL 不能为空")

        clean_url = url.strip()
        if len(clean_url) > cls.MAX_URL_LENGTH:
            raise ValueError(f"URL 长度超出安全限制 (最多 {cls.MAX_URL_LENGTH} 字符)")

        parsed = urllib.parse.urlsplit(clean_url)

        scheme = parsed.scheme.lower()
        if scheme not in cls.ALLOWED_SCHEMES:
            raise ValueError(f"安全拒绝：仅允许 http:// 与 https:// 协议，禁止协议: '{scheme or '未知'}'")

        if not parsed.netloc:
            raise ValueError("URL 格式无效，缺少有效的主机名")

        # 拒绝 userinfo (例如 http://user:pass@host)
        if parsed.username or parsed.password or "@" in parsed.netloc:
            raise ValueError("安全拒绝：禁止包含用户名或密码的 URL (userinfo)")

        hostname = parsed.hostname
        if not hostname:
            raise ValueError("URL 格式无效，缺少有效的主机名")

        # 端口检查
        if parsed.port is not None and parsed.port not in cls.ALLOWED_PORTS:
            raise ValueError(f"安全拒绝：禁止访问非标准 Web 端口 ({parsed.port})")

        host_clean = hostname.strip("[]").lower()

        # 检查是否为 localhost 及其别名
        if host_clean in ("localhost", "localhost.localdomain") or host_clean.endswith(".localhost"):
            raise ValueError("安全拒绝：禁止访问本地回环网络 (localhost)")

        # 检查是否直接为 IP 地址
        try:
            ip = ipaddress.ip_address(host_clean)
            if ip.is_loopback:
                raise ValueError("安全拒绝：禁止访问本地回环地址 (127.0.0.1 / ::1)")
            if ip.is_private:
                raise ValueError("安全拒绝：禁止访问私有内网地址 (RFC 1918)")
            if ip.is_link_local:
                raise ValueError("安全拒绝：禁止访问链路本地地址 (Link-local)")
            if ip.is_multicast:
                raise ValueError("安全拒绝：禁止访问组播地址")
            if ip.is_unspecified:
                raise ValueError("安全拒绝：禁止访问未指定地址 (0.0.0.0 / ::)")
            if ip.is_reserved:
                raise ValueError("安全拒绝：禁止访问保留网络地址")
        except ValueError as e:
            if "安全拒绝" in str(e):
                raise
            # 如果不是 IP 字面量，说明是普通域名，进行有界 DNS 解析检查防 SSRF
            # 并发控制：占满时快速拒绝以防拒绝服务攻击
            acquired = cls._dns_semaphore.acquire(blocking=False)
            if not acquired:
                raise ValueError("安全拒绝：当前 DNS 解析并发已达上限，已拒绝请求以防网络拥塞")

            result_container: List[Any] = []
            exc_container: List[Exception] = []
            finished_event = threading.Event()

            def _dns_worker():
                try:
                    res = socket.getaddrinfo(host_clean, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                    result_container.append(res)
                except Exception as ex:
                    exc_container.append(ex)
                finally:
                    finished_event.set()
                    try:
                        cls._dns_semaphore.release()
                    except ValueError:
                        pass

            worker = threading.Thread(
                target=_dns_worker,
                name=f"DnsResolve-{host_clean[:16]}",
                daemon=True,
            )
            worker.start()

            timeout = cls.DNS_RESOLVE_TIMEOUT_SECONDS
            completed = finished_event.wait(timeout=timeout)
            if not completed:
                raise ValueError(f"安全拒绝：域名解析超时（超过 {timeout} 秒），无法确认目标地址安全性")

            if exc_container:
                raise ValueError("安全拒绝：域名解析失败，无法确认目标地址安全性")

            if not result_container or not result_container[0]:
                raise ValueError("安全拒绝：域名未解析到有效 IP 地址")

            addrs = result_container[0]
            for family, socktype, proto, canonname, sockaddr in addrs:
                ip_str = sockaddr[0]
                resolved_ip = ipaddress.ip_address(ip_str)
                if (
                    resolved_ip.is_loopback
                    or resolved_ip.is_private
                    or resolved_ip.is_link_local
                    or resolved_ip.is_multicast
                    or resolved_ip.is_unspecified
                    or resolved_ip.is_reserved
                ):
                    raise ValueError("安全拒绝：域名解析到了受保护的本地、私网或保留网络地址")

        return clean_url


# -------------------------------------------------------------------------------------------------
# 3. 文件路径沙箱（防止路径穿越、禁止系统/用户敏感目录、禁止可执行与持久化脚本文件）
# -------------------------------------------------------------------------------------------------
class PathSandbox:
    """文件路径写入安全沙箱"""

    # 严禁通过文本写入工具创建的可执行与持久化扩展名
    PROHIBITED_EXTENSIONS = {
        ".exe", ".com", ".bat", ".cmd", ".ps1", ".vbs",
        ".js", ".jse", ".wsf", ".wsh", ".hta", ".scr",
        ".dll", ".lnk", ".url", ".reg", ".pif", ".msc", ".cpl",
    }

    @classmethod
    def is_sensitive_path(cls, path: str) -> Tuple[bool, str]:
        """检查指定物理路径是否落在系统核心目录、敏感用户目录或系统卷中。"""
        if not path:
            return True, "路径不能为空"

        real_p = os.path.realpath(os.path.abspath(path))
        drive, rest = os.path.splitdrive(real_p)

        # 1. 阻止任何磁盘根目录（如 C:\, D:\）
        if rest.strip("\\/") == "":
            return True, "安全拒绝：禁止直接在磁盘根目录操作"

        norm_p = real_p.lower()

        # 2. 跨磁盘动态拦截敏感系统路径片段（统一以反斜杠首尾包裹进行路径组件匹配）
        norm_wrapped = f"\\{norm_p.strip(os.sep)}\\"
        sensitive_fragments = [
            "\\windows\\",
            "\\program files\\",
            "\\program files (x86)\\",
            "\\programdata\\",
            "\\appdata\\",
            "\\startup\\",
            "\\$recycle.bin\\",
            "\\recycler\\",
            "\\system volume information\\",
            "\\recovery\\",
        ]
        for frag in sensitive_fragments:
            if frag in norm_wrapped:
                frag_name = frag.strip("\\")
                return True, f"安全拒绝：禁止操作受保护的系统或敏感目录 ({frag_name})"

        # 3. 拦截系统与用户临时目录
        temp_dirs = [
            os.environ.get("TEMP", ""),
            os.environ.get("TMP", ""),
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp"),
        ]
        for td in temp_dirs:
            if td and os.path.exists(td):
                real_td = os.path.realpath(os.path.abspath(td)).lower()
                try:
                    if os.path.commonpath([norm_p, real_td]) == real_td:
                        return True, "安全拒绝：禁止操作临时文件夹 (Temp)"
                except Exception:
                    pass

        # 4. 阻止用户主目录根（例如 C:\Users\Username 本身，但允许其子目录如 Documents）
        user_home = os.path.realpath(os.path.expanduser("~")).lower()
        if norm_p == user_home:
            return True, "安全拒绝：禁止将用户主目录根设为保存位置，请选择文档或其他子文件夹"

        return False, ""

    @classmethod
    def is_safe_write_directory(cls, dir_path: str) -> Tuple[bool, str]:
        """验证指定文件夹是否可以作为受信任保存目录加入配置。"""
        if not dir_path or not isinstance(dir_path, str):
            return False, "目录路径不能为空"

        clean_d = dir_path.strip().strip('"').strip("'")
        if not os.path.exists(clean_d):
            return False, "指定的文件夹不存在"

        if not os.path.isdir(clean_d):
            return False, "指定路径不是文件夹目录"

        real_d = os.path.realpath(os.path.abspath(clean_d))
        is_sens, reason = cls.is_sensitive_path(real_d)
        if is_sens:
            return False, reason

        return True, ""

    @classmethod
    def validate_write_path(cls, file_path: str, allowed_dirs: List[str]) -> str:
        """
        验证待写入文件路径的安全性。
        1. 必须位于用户授权的有效安全写入目录之下；
        2. 防目录穿越、符号链接与 Junction 逃逸；
        3. 拒绝 UNC 网络路径与磁盘根目录；
        4. 拒绝系统敏感目录与可执行/脚本扩展名。
        """
        if not file_path or not isinstance(file_path, str):
            raise ValueError("文件路径不能为空")

        clean_path = file_path.strip().strip('"').strip("'")
        if not clean_path:
            raise ValueError("文件路径不能为空")

        # 检查是否为 UNC 远程网络路径（例如 \\server\share）
        if clean_path.startswith("\\\\") or clean_path.startswith("//"):
            raise ValueError("安全拒绝：禁止向 UNC 远程网络路径写入文件")

        # 检查扩展名是否在危险可执行/持久化黑名单中
        ext = os.path.splitext(clean_path)[1].lower()
        if ext in cls.PROHIBITED_EXTENSIONS:
            raise ValueError(f"安全拒绝：禁止创建或写入可执行程序、脚本与系统配置文件 ({ext})")

        # 规范化绝对路径并解析上级符号链接与 Junction
        abs_path = os.path.abspath(clean_path)
        parent_dir = os.path.dirname(abs_path)
        real_parent = os.path.realpath(parent_dir)
        real_file_path = os.path.join(real_parent, os.path.basename(abs_path))

        # 检查目标路径本身或解析后的上级目录是否落在敏感目录中
        is_sens, sens_reason = cls.is_sensitive_path(real_file_path)
        if is_sens:
            raise ValueError(sens_reason)

        # 必须至少配置一个用户授权的写入目录
        if not allowed_dirs:
            raise ValueError("安全拒绝：当前未配置允许写入的受信任目录。请在应用设置中添加授权目录。")

        # 检查目标路径是否位于任一安全有效的允许目录之下
        is_allowed = False
        for allowed_dir in allowed_dirs:
            safe_d, _ = cls.is_safe_write_directory(allowed_dir)
            if not safe_d:
                continue
            real_allowed = os.path.realpath(os.path.abspath(allowed_dir))
            try:
                common = os.path.commonpath([real_file_path, real_allowed])
                if common.lower() == real_allowed.lower() and real_file_path.lower() != real_allowed.lower():
                    is_allowed = True
                    break
            except Exception:
                continue

        if not is_allowed:
            raise ValueError("安全拒绝：文件路径不在任何用户授权的安全目录之内")

        return real_file_path


# -------------------------------------------------------------------------------------------------
# 4. CMD 命令白名单验证器
# -------------------------------------------------------------------------------------------------
class CommandValidator:
    """命令白名单验证器"""

    @classmethod
    def resolve_command(cls, command_name_or_text: str, whitelist: Dict[str, str], allow_cmd: bool) -> str:
        """根据命令别名或精确匹配白名单获取执行命令"""
        if not allow_cmd:
            raise PermissionError("安全拒绝：命令执行功能已被用户关闭。")

        target = command_name_or_text.strip()

        # 1. 尝试按别名查找
        if target in whitelist:
            return whitelist[target]

        # 2. 尝试按完整固定命令值查找
        for alias, cmd in whitelist.items():
            if target.lower() == cmd.strip().lower():
                return cmd

        raise PermissionError(f"安全拒绝：'{command_name_or_text}' 不在命令白名单中。禁止任意执行。")
