"""MCP 接入地址统一规范化、校验与脱敏模块（兼容 api.xiaozhi.me 安全接入地址）
严格保障：
1. 仅支持 mcp:wss:// 与 wss:// 前缀；
2. 内部统一转换为标准的 wss:// 协议，禁止将 mcp: 直接送入网络层；
3. 严格限制官方主域名 api.xiaozhi.me 与 /mcp/ 路径；
4. 阻断一切不安全协议、重定向、用户信息、控制字符与重复参数；
5. 界面脱敏统一显示为 wss://api.xiaozhi.me/mcp/?token=••••••，绝不泄露首尾字符；
6. 错误信息固定分类，绝不回显用户输入。
"""

import urllib.parse
from dataclasses import dataclass
from typing import Optional, Set

DEFAULT_OFFICIAL_HOST = "api.xiaozhi.me"
DEFAULT_MCP_PATH = "/mcp/"


@dataclass
class EndpointValidationResult:
    """接入地址校验结果"""
    is_valid: bool
    normalized_url: str
    error_message: str


def validate_mcp_endpoint(raw_input: str) -> EndpointValidationResult:
    """
    全面校验并规范化用户输入的 MCP 接入地址。
    严格仅允许官方 api.xiaozhi.me 与安全的 WSS 协议。
    :param raw_input: 用户原始输入文本
    """
    if not raw_input or not isinstance(raw_input, str):
        return EndpointValidationResult(False, "", "接入地址不能为空")

    # 1. 严格拦截换行符、回车符与 NUL 字符
    if "\r" in raw_input or "\n" in raw_input or "\0" in raw_input:
        return EndpointValidationResult(False, "", "接入地址格式不正确")

    # 仅去除首尾普通空格与制表符
    cleaned = raw_input.strip(" \t")
    if not cleaned:
        return EndpointValidationResult(False, "", "接入地址不能为空")

    # 检查其他不可打印控制字符
    for ch in cleaned:
        if ord(ch) < 32 or ord(ch) == 127:
            return EndpointValidationResult(False, "", "接入地址格式不正确")

    # 2. 前缀清洗：支持且仅支持以 mcp:wss:// 开头或以 wss:// 开头
    url_to_parse = cleaned
    if url_to_parse.lower().startswith("mcp:"):
        url_to_parse = url_to_parse[4:]  # 仅去除最前面的 mcp:

    # 3. 结构化 URL 解析与安全属性访问
    try:
        split = urllib.parse.urlsplit(url_to_parse)
        scheme = split.scheme.lower()
        has_user_info = bool(split.username or split.password or "@" in split.netloc)
        has_fragment = bool(split.fragment)
        raw_hostname = split.hostname
        port = split.port
        raw_path = split.path or "/"
        raw_query = split.query
    except Exception:
        return EndpointValidationResult(False, "", "接入地址格式不正确")

    # 4. 协议安全性校验：严格仅允许 wss
    if scheme != "wss":
        return EndpointValidationResult(False, "", "只允许安全的 WSS 地址")

    # 5. 禁止携带用户信息 (user:pass@) 和 fragment (#...)
    if has_user_info or has_fragment:
        return EndpointValidationResult(False, "", "接入地址格式不正确")

    # 6. 主机与端口校验：严格锁定官方主机
    hostname = (raw_hostname or "").lower()
    if not hostname or hostname != DEFAULT_OFFICIAL_HOST:
        return EndpointValidationResult(False, "", "当前版本仅支持 api.xiaozhi.me 的安全 MCP 接入地址")

    if port is not None:
        if port <= 0 or port > 65535 or port != 443:
            return EndpointValidationResult(False, "", "接入地址格式不正确")

    # 7. 路径规范化校验：限定为 /mcp 或 /mcp/
    if raw_path in ["/mcp", "/mcp/"]:
        norm_path = DEFAULT_MCP_PATH
    else:
        return EndpointValidationResult(False, "", "接入地址格式不正确")

    # 8. 查询参数与 Token 严格校验
    try:
        query_items = urllib.parse.parse_qsl(raw_query, keep_blank_values=True)
    except Exception:
        return EndpointValidationResult(False, "", "接入地址格式不正确")

    token_values = [v for k, v in query_items if k.lower() == "token"]
    if len(token_values) == 0:
        return EndpointValidationResult(False, "", "接入地址缺少 Token")
    elif len(token_values) > 1:
        return EndpointValidationResult(False, "", "接入地址格式不正确")

    # 直接拒绝包含未知 query 参数的地址，避免静默丢弃语义
    if any(k.lower() != "token" for k, _ in query_items):
        return EndpointValidationResult(False, "", "接入地址格式不正确")

    raw_token = token_values[0]
    if not raw_token:
        return EndpointValidationResult(False, "", "接入地址缺少 Token")

    # 严格禁止首尾空白字符，不得静默 strip
    if raw_token != raw_token.strip():
        return EndpointValidationResult(False, "", "接入地址格式不正确")

    token = raw_token

    for ch in token:
        if ord(ch) < 32 or ord(ch) == 127:
            return EndpointValidationResult(False, "", "接入地址格式不正确")

    # 9. 构造最终规范化 URL（使用标准 quote 重新编码 token query）
    netloc_str = hostname
    if port and port != 443:
        netloc_str = f"{hostname}:{port}"

    canonical_query = urllib.parse.urlencode([("token", token)], quote_via=urllib.parse.quote)
    normalized_url = urllib.parse.urlunsplit((scheme, netloc_str, norm_path, canonical_query, ""))

    return EndpointValidationResult(
        is_valid=True,
        normalized_url=normalized_url,
        error_message=""
    )


def normalize_mcp_endpoint(raw_input: str) -> str:
    """
    规范化用户输入的 MCP 接入地址。若不合法则抛出 ValueError（附带固定分类错误提示）。
    """
    res = validate_mcp_endpoint(raw_input)
    if not res.is_valid:
        raise ValueError(res.error_message)
    return res.normalized_url


def mask_mcp_endpoint(normalized_url: str) -> str:
    """
    对已规范化的接入地址进行安全脱敏显示。
    仅显示格式为 wss://api.xiaozhi.me/mcp/?token=••••••，绝不泄露首尾字符或 Token 长度。
    """
    if not normalized_url or not isinstance(normalized_url, str):
        return ""

    try:
        split = urllib.parse.urlsplit(normalized_url)
        query_items = urllib.parse.parse_qsl(split.query, keep_blank_values=True)
        if any(k.lower() == "token" for k, _ in query_items):
            return urllib.parse.urlunsplit((split.scheme, split.netloc, split.path, "token=••••••", ""))
    except Exception:
        pass

    return "wss://api.xiaozhi.me/mcp/?token=••••••"


def test_mcp_connection(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """
    测试指定 MCP WebSocket 接入点的连通性。
    支持超时控制、近即时关闭帧/鉴权拒绝探测与脱敏错误分类，返回 (success, message)。
    """
    import asyncio
    import ssl
    import websockets
    from websockets.exceptions import (
        ConnectionClosed,
        ConnectionClosedError,
        InvalidURI,
        InvalidStatusCode,
    )

    async def _test():
        try:
            val = validate_mcp_endpoint(url)
            if not val.is_valid:
                return False, val.error_message

            connect_url = val.normalized_url

            import json

            async with websockets.connect(connect_url, open_timeout=timeout, close_timeout=1.0) as ws:
                # 建立连接后，在总超时范围内读取并解析首条消息，确认是否为合法的 MCP initialize 请求
                try:
                    raw_msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    return False, "连接超时：未收到服务端的有效初始化响应"
                except ConnectionClosed as e:
                    code = getattr(e, "code", None)
                    if code in (4001, 4003, 1008, 4401, 4403):
                        return False, "连接已被服务端拒绝，连接码可能无效或已过期"
                    return False, f"连接被服务端关闭 (状态码: {code})"

                if not raw_msg or not isinstance(raw_msg, str):
                    return False, "服务端返回的初始化消息格式无效"

                try:
                    parsed = json.loads(raw_msg)
                except Exception:
                    return False, "协议格式无效：服务端返回非 JSON 响应"

                if not isinstance(parsed, dict):
                    return False, "服务端返回的初始化消息格式无效 (非 JSON 对象)"

                # 检查服务端返回的顶层 error 响应
                if "error" in parsed:
                    err_obj = parsed.get("error")
                    err_code = err_obj.get("code") if isinstance(err_obj, dict) else None
                    if err_code in (401, 403, 4001, 4003):
                        return False, "认证失败，连接码无效或已被服务端拒绝"
                    return False, "连接已被服务端拒绝：收到服务端错误响应"

                # 严格校验是否为合法的 MCP initialize 请求
                if parsed.get("jsonrpc") != "2.0":
                    return False, "协议不匹配：非 JSON-RPC 2.0 消息"

                method = parsed.get("method")
                if method != "initialize":
                    return False, f"协议不匹配：收到非预期请求 ({method or '未知'})"

                if parsed.get("id") is None:
                    return False, "协议不匹配：initialize 请求缺少消息 ID"

                params = parsed.get("params")
                if not isinstance(params, dict):
                    return False, "协议不匹配：initialize 请求缺少有效 params 参数"

                return True, "连接成功"
        except InvalidStatusCode as e:
            code = getattr(e, "status_code", None)
            if code in (401, 403):
                return False, "认证失败，连接码无效或已过期"
            if code == 404:
                return False, "未找到 MCP 服务接入端点"
            return False, f"服务端响应异常 (HTTP {code})"
        except (ConnectionClosed, ConnectionClosedError) as e:
            code = getattr(e, "code", None)
            if code in (4001, 4003, 1008, 4401, 4403):
                return False, "连接已被服务端拒绝，连接码可能无效或已过期"
            return False, "连接被服务端关闭，请检查网络或连接码"
        except (InvalidURI, ValueError):
            return False, "接入地址格式不正确"
        except (ssl.SSLError, ssl.CertificateError):
            return False, "SSL/TLS 证书校验失败，无法建立安全连接"
        except (OSError, TimeoutError) as e:
            err_str = str(e).lower()
            if "name or service not known" in err_str or "getaddrinfo" in err_str or "nodename" in err_str:
                return False, "DNS 域名解析失败，请检查网络连接"
            if "proxy" in err_str:
                return False, "代理服务器连接失败，请检查网络代理设置"
            return False, "连接超时，无法连接至该接入点"
        except asyncio.TimeoutError:
            return False, "连接超时，无法连接至该接入点"
        except Exception:
            return False, "连接失败，无法连接至该接入点"

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(_test(), timeout=timeout + 1.5))
    except asyncio.TimeoutError:
        return False, "连接超时，无法连接至该接入点"
    except Exception:
        return False, "连接失败，无法连接至该接入点"
    finally:
        loop.close()
