"""WebSocket 管道桥接模块
保留小智原版 MCP_Pipe 协议行为，实现与云端 WebSocket 服务器的双向 JSON-RPC 通信泵、
指数退避自动重连、MCP 初始化状态机握手门控以及子进程严格生命周期管理。
"""

import asyncio
import json
import os
import random
import re
import subprocess
import sys
import threading
from enum import Enum
from typing import Callable, Optional, List
import websockets
from src.core.logger import app_logger, sanitize_text


class ServiceStatus(str, Enum):
    """服务状态枚举"""
    STOPPED = "已停止"
    CONNECTING = "正在连接"
    CONNECTED = "已连接"
    RECONNECTING = "正在重连"
    ERROR = "异常错误"


class McpHandshakeState(str, Enum):
    """MCP 初始化握手状态机"""
    WAIT_INITIALIZE = "WAIT_INITIALIZE"                          # 等待客户端发送首条合法 initialize 请求
    WAIT_INITIALIZE_RESPONSE = "WAIT_INITIALIZE_RESPONSE"        # 已转发 initialize 给子进程，等待子进程返回结果
    WAIT_INITIALIZED_NOTIFICATION = "WAIT_INITIALIZED_NOTIFICATION"  # 已返回 initialize 响应，等待客户端发送 initialized 通知
    READY = "READY"                                              # 握手完成，进入全双工通信状态


def _safe_rpc_method(data: dict) -> str:
    """提取脱敏 JSON-RPC method，只保留合法字符，杜绝泄露任何参数或敏感信息"""
    if not isinstance(data, dict):
        return "invalid"
    method = data.get("method")
    if isinstance(method, str) and method:
        clean = re.sub(r"[^a-zA-Z0-9._$/-]", "", method)[:60]
        return clean or "unknown"
    return "none"


def _safe_rpc_id(data: dict) -> str:
    """提取脱敏 JSON-RPC request id"""
    if not isinstance(data, dict):
        return "none"
    req_id = data.get("id")
    if req_id is None:
        return "none"
    if isinstance(req_id, (int, float)):
        return str(req_id)
    if isinstance(req_id, str):
        return sanitize_text(req_id[:32])
    return "unknown"


class PipeBridge:
    """WebSocket 与 MCP 服务端子进程桥接器"""

    INITIAL_BACKOFF = 1.0  # 初始重连退避时间（秒）
    MAX_BACKOFF = 60.0    # 最大重连退避时间（秒）
    MAX_PENDING_MSGS = 50  # 握手期间排队消息最大条数
    MAX_PENDING_BYTES = 256 * 1024  # 握手期间排队消息最大总字节数 (256KB)
    HANDSHAKE_TIMEOUT = 15.0  # 握手各阶段总超时上限（秒）

    def __init__(
        self,
        endpoint_url: str,
        python_executable: str,
        server_script_path: str,
        on_status_change: Optional[Callable[[ServiceStatus, str], None]] = None
    ):
        self.endpoint_url = endpoint_url
        self.python_executable = python_executable
        self.server_script_path = server_script_path
        self.on_status_change = on_status_change

        self.status = ServiceStatus.STOPPED
        self.handshake_state = McpHandshakeState.WAIT_INITIALIZE
        self._thread_lock = threading.Lock()
        self._proc_cleanup_lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._loop_stop_event: Optional[asyncio.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._current_process: Optional[subprocess.Popen] = None
        self.reconnect_attempt = 0
        self.backoff = self.INITIAL_BACKOFF

    def is_running(self) -> bool:
        """检查后台桥接线程是否存活"""
        return self._thread is not None and self._thread.is_alive()

    def set_status(self, status: ServiceStatus, message: str = ""):
        """更新服务状态并触发回调"""
        self.status = status
        app_logger.info(f"[状态更新] {status.value}: {message}")
        if self.on_status_change:
            try:
                self.on_status_change(status, message)
            except Exception as e:
                app_logger.warning(f"状态回调执行异常: {e}")

    def start(self):
        """在独立后台线程中启动异步事件循环，确保同一时间只有一个线程在运行"""
        with self._thread_lock:
            if self.is_running():
                app_logger.warning("服务后台线程已在运行中，忽略重复启动请求")
                return

            self._stop_requested.clear()
            self.reconnect_attempt = 0
            self.backoff = self.INITIAL_BACKOFF
            self.handshake_state = McpHandshakeState.WAIT_INITIALIZE

            def run_loop():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                # 为新事件循环创建独立的 stop_event，避免跨 loop 复用引发 RuntimeError
                self._loop_stop_event = asyncio.Event()
                try:
                    self._loop.run_until_complete(self._run_bridge())
                except Exception as e:
                    app_logger.error(f"管道运行主循环异常退出: {e}")
                finally:
                    try:
                        pending = asyncio.all_tasks(self._loop)
                        for task in pending:
                            task.cancel()
                        if pending:
                            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                        self._loop.close()
                    except Exception:
                        pass
                    self._loop = None
                    self._loop_stop_event = None
                    self.set_status(ServiceStatus.STOPPED, "服务已停止")

            self._thread = threading.Thread(target=run_loop, name="PipeBridgeThread", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> bool:
        """停止管道服务并彻底回收所有子进程与异步任务。
        成功完全退出返回 True；超时未完全退出则保留线程引用并返回 False。
        """
        with self._thread_lock:
            app_logger.info("收到停止指令，正在关闭管道服务...")
            self.set_status(ServiceStatus.STOPPED, "正在停止...")
            self._stop_requested.set()

            # 触发当前事件循环的停止事件
            if self._loop and self._loop.is_running() and self._loop_stop_event:
                try:
                    self._loop.call_soon_threadsafe(self._loop_stop_event.set)
                except Exception:
                    pass

            # 线程安全终止并清理当前运行的子进程
            self._cleanup_process(timeout=2.0)

            # 等待后台线程退出
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=timeout)
                # 检查线程是否真正退出
                if self._thread.is_alive():
                    app_logger.error("停止服务超时：后台工作线程未能退出，保留引用以防并发冲突")
                    self.set_status(ServiceStatus.ERROR, "停止服务超时，后台线程未能及时退出")
                    return False
                self._thread = None

            self.set_status(ServiceStatus.STOPPED, "服务已停止")
            return True

    def _terminate_process_instance(self, proc: subprocess.Popen, timeout: float = 3.0) -> Optional[int]:
        """线程安全地终止指定子进程实例并关闭流。
        先 terminate / kill，等待进程结束，然后关闭流，彻底杜绝管道关闭死锁。
        """
        pid = proc.pid
        exit_code = proc.poll()
        if exit_code is None:
            app_logger.info(f"正在结束 MCP 子进程 (PID: {pid})...")
            # 1. 先尝试正常 terminate
            try:
                proc.terminate()
            except Exception:
                pass

            # 等待进程退出
            try:
                exit_code = proc.wait(timeout=min(timeout, 1.5))
            except (subprocess.TimeoutExpired, Exception):
                exit_code = None

            # 2. 若超时仍未退出，升级为 kill
            if exit_code is None:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    exit_code = proc.wait(timeout=1.5)
                except (subprocess.TimeoutExpired, Exception):
                    exit_code = proc.poll()

        # 3. 必须在子进程完全退出后，才关闭管道流，避免后台 readline 阻塞死锁
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass

        app_logger.info(f"MCP 子进程 (PID: {pid}) 已成功回收，退出码: {exit_code}")
        return exit_code

    def _cleanup_process(self, proc: Optional[subprocess.Popen] = None, timeout: float = 3.0) -> Optional[int]:
        """线程安全、幂等地终止并清理当前或指定子进程"""
        with self._proc_cleanup_lock:
            target = proc or self._current_process
            if self._current_process is target:
                self._current_process = None
            if not target:
                return None
            return self._terminate_process_instance(target, timeout=timeout)

    def _kill_process(self):
        """兼容保留的子进程回收方法"""
        self._cleanup_process()

    def _is_stopped(self) -> bool:
        """检查停止信号是否被触发"""
        if self._stop_requested.is_set():
            return True
        if self._loop_stop_event and self._loop_stop_event.is_set():
            return True
        return False

    async def _run_bridge(self):
        """异步连接主循环，带指数退避重连机制"""
        while not self._is_stopped():
            try:
                self.set_status(ServiceStatus.CONNECTING, "正在连接到 WebSocket 服务端...")
                await self._connect_and_pump()

            except Exception as e:
                if self._is_stopped():
                    break
                self.reconnect_attempt += 1
                clean_err = sanitize_text(str(e))
                self.set_status(ServiceStatus.ERROR, f"连接异常: {clean_err}")

                # 计算退避时间（指数退避 + 0-10% 随机抖动），最大不超过 MAX_BACKOFF
                wait_time = self.backoff * (1 + random.random() * 0.1)
                self.backoff = min(self.backoff * 2, self.MAX_BACKOFF)

                self.set_status(
                    ServiceStatus.RECONNECTING,
                    f"将在 {wait_time:.1f} 秒后重试 (第 {self.reconnect_attempt} 次)..."
                )

                # 必须执行合理的退避延迟，禁止无延迟快速重试
                try:
                    if self._loop_stop_event:
                        await asyncio.wait_for(self._loop_stop_event.wait(), timeout=wait_time)
                        if self._is_stopped():
                            break
                    else:
                        await asyncio.sleep(wait_time)
                except asyncio.TimeoutError:
                    pass

    async def _connect_and_pump(self):
        """建立 WebSocket 连接并与子进程 stdio 进行双向通信"""
        if not self.endpoint_url:
            raise ValueError("未配置有效的 MCP WebSocket 服务端 Endpoint")

        # 启动 MCP 子进程
        # 重要：使用二进制模式 text=False 保证跨平台中文字符与二进制数据完整性
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            [self.python_executable, self.server_script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=env
        )
        with self._proc_cleanup_lock:
            self._current_process = proc
        app_logger.info(f"已启动 MCP 服务子进程 (PID: {proc.pid})")

        session_tasks: set[asyncio.Task] = set()

        def _spawn_task(coro) -> asyncio.Task:
            t = asyncio.create_task(coro)
            session_tasks.add(t)
            t.add_done_callback(session_tasks.discard)
            return t

        try:
            # 连接 WebSocket 服务端
            async with websockets.connect(self.endpoint_url) as ws:
                # 握手未完成前绝不标记为 CONNECTED，保持 CONNECTING
                app_logger.info("WebSocket 连接已建立，等待客户端 MCP initialize 握手...")

                # 启动 stderr 转发日志任务（无论握手是否成功，stderr 均能记录子进程日志）
                t_stderr = _spawn_task(self._pump_stderr_to_log(proc))

                # 启动握手流程与全局停止信号监听
                handshake_task = _spawn_task(self._perform_mcp_handshake(ws, proc))
                stop_task = _spawn_task(self._loop_stop_event.wait())

                done_hs, pending_hs = await asyncio.wait(
                    [handshake_task, stop_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                if stop_task in done_hs:
                    return

                # 若握手任务抛出异常，立即传播以触发重连
                pending_messages = handshake_task.result()

                # 握手成功并进入 READY 状态，此时才将界面状态更新为 CONNECTED！
                self.reconnect_attempt = 0
                self.backoff = self.INITIAL_BACKOFF
                self.set_status(ServiceStatus.CONNECTED, "已成功连接至 MCP 服务")

                # 将握手期间合法排队的后续消息（如 tools/list）按原始顺序写入子进程 stdin
                for raw_data in pending_messages:
                    if proc.stdin and not proc.stdin.closed:
                        proc.stdin.write(raw_data + b"\n")
                        proc.stdin.flush()

                # 创建全双工消息转发任务
                t1 = _spawn_task(self._pump_ws_to_stdin(ws, proc))
                t2 = _spawn_task(self._pump_stdout_to_ws(proc, ws))

                done, pending = await asyncio.wait(
                    [t1, t2, t_stderr, stop_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                # 读取已完成异步任务的结果，使连接异常能够传播到外层重连逻辑
                for task in done:
                    if task is not stop_task and not task.cancelled():
                        exc = task.exception()
                        if exc:
                            raise exc
        finally:
            # 彻底清理本会话中创建的所有未完成异步任务，杜绝任务泄漏
            for task in list(session_tasks):
                if not task.done():
                    task.cancel()
            if session_tasks:
                await asyncio.gather(*session_tasks, return_exceptions=True)
            session_tasks.clear()

            # 检查是否有正在抛出的原始异常
            exc_in_flight = sys.exc_info()[0] is not None
            # 线程安全终止并清理子进程，获取真实退出状态
            exit_code = self._cleanup_process(proc, timeout=3.0)
            if not exc_in_flight and exit_code is not None and exit_code != 0 and not self._is_stopped():
                app_logger.warning(f"MCP 子进程异常退出 (退出码: {exit_code})")
                raise RuntimeError(f"MCP 子进程异常退出 (退出码: {exit_code})")

    async def _perform_mcp_handshake(self, ws, proc: subprocess.Popen) -> List[bytes]:
        """执行严格的 MCP 初始化状态机握手。"""
        self.handshake_state = McpHandshakeState.WAIT_INITIALIZE
        return await asyncio.wait_for(
            self._handshake_steps(ws, proc),
            timeout=self.HANDSHAKE_TIMEOUT
        )

    async def _handshake_steps(self, ws, proc: subprocess.Popen) -> List[bytes]:
        loop = asyncio.get_running_loop()

        # --- 步骤 1: 等待首包消息，必须为合法 initialize 请求 ---
        raw_first = await ws.recv()
        text_first = raw_first if isinstance(raw_first, str) else raw_first.decode("utf-8", errors="replace")
        try:
            first_json = json.loads(text_first)
        except Exception:
            app_logger.warning("[MCP握手] 收到无法解析为 JSON 的首包数据，拒绝并重连")
            raise RuntimeError("Invalid JSON received as first message")

        if not isinstance(first_json, dict):
            app_logger.warning("[MCP握手] 首包数据非 JSON 对象，拒绝并重连")
            raise RuntimeError("First message must be a JSON object")

        first_method = first_json.get("method")
        if first_method != "initialize":
            safe_m = _safe_rpc_method(first_json)
            app_logger.warning(
                f"[MCP握手拒绝] 首包请求非 initialize (method={safe_m})，"
                f"安全阻断写入子进程，退出当前会话并执行退避重连"
            )
            # 关键：绝不写入子进程 stdin，不伪造 initialize，直接抛出异常退出本次连接
            raise RuntimeError(f"Protocol violation: first message was '{safe_m}', expected 'initialize'")

        init_req_id = first_json.get("id")
        safe_id = _safe_rpc_id(first_json)
        app_logger.info(f"[MCP握手] 收到合法 initialize 请求 (id={safe_id})，转发给子进程...")

        # 转发 initialize 到子进程 stdin
        bytes_first = raw_first.encode("utf-8") if isinstance(raw_first, str) else raw_first
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write(bytes_first + b"\n")
            proc.stdin.flush()

        self.handshake_state = McpHandshakeState.WAIT_INITIALIZE_RESPONSE

        # --- 步骤 2: 等待子进程 initialize 响应，并缓冲此期间 WS 发送的消息 ---
        pending_queue: List[bytes] = []
        pending_bytes = 0

        async def _read_stdout_init_resp() -> str:
            while True:
                line = await loop.run_in_executor(None, proc.stdout.readline)
                if not line:
                    try:
                        code = proc.wait(timeout=1.0)
                    except Exception:
                        code = proc.poll()
                    raise RuntimeError(f"MCP 子进程在返回 initialize 响应前退出 (退出码: {code})")
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    data = json.loads(text)
                except Exception:
                    continue
                if (
                    isinstance(data, dict)
                    and data.get("id") == init_req_id
                    and ("result" in data or "error" in data)
                ):
                    return text
                else:
                    # 子进程输出的非匹配响应（如初始化期间的通知）先行转发给 WS
                    try:
                        await ws.send(text)
                    except Exception:
                        pass

        async def _buffer_incoming_ws():
            nonlocal pending_bytes
            while True:
                msg = await ws.recv()
                raw = msg.encode("utf-8") if isinstance(msg, str) else msg
                if len(pending_queue) >= self.MAX_PENDING_MSGS:
                    raise RuntimeError("握手待处理队列消息数超出上限 (MAX_PENDING_MSGS)")
                pending_bytes += len(raw)
                if pending_bytes > self.MAX_PENDING_BYTES:
                    raise RuntimeError("握手待处理队列字节总数超出上限 (MAX_PENDING_BYTES)")
                pending_queue.append(raw)

        read_task = asyncio.create_task(_read_stdout_init_resp())
        buffer_task = asyncio.create_task(_buffer_incoming_ws())
        try:
            done, pending = await asyncio.wait(
                [read_task, buffer_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            # 若 buffer_task 率先完成（发生异常，如队列溢出或断开连接）
            if buffer_task in done:
                for t in pending:
                    t.cancel()
                exc = buffer_task.exception()
                if exc:
                    raise exc
                raise RuntimeError("握手期间 WebSocket 缓冲任务意外终止")

            # 否则 read_task 完成，检查其是否有异常
            exc = read_task.exception()
            if exc:
                raise exc
            init_resp_text = read_task.result()
        finally:
            for t in (read_task, buffer_task):
                if not t.done():
                    t.cancel()
            await asyncio.gather(read_task, buffer_task, return_exceptions=True)

        # 将 initialize 响应发回给 WebSocket
        await ws.send(init_resp_text)
        app_logger.info(f"[MCP握手] 已将子进程 initialize 响应转发给客户端，等待 initialized 通知...")

        self.handshake_state = McpHandshakeState.WAIT_INITIALIZED_NOTIFICATION

        # --- 步骤 3: 提取并校验 notifications/initialized ---
        if pending_queue:
            notif_raw = pending_queue.pop(0)
        else:
            notif_msg = await ws.recv()
            notif_raw = notif_msg.encode("utf-8") if isinstance(notif_msg, str) else notif_msg

        notif_text = notif_raw.decode("utf-8", errors="replace")
        try:
            notif_json = json.loads(notif_text)
        except Exception:
            raise RuntimeError("收到非法非 JSON 数据，期望 notifications/initialized")

        if not isinstance(notif_json, dict) or notif_json.get("method") != "notifications/initialized":
            safe_m = _safe_rpc_method(notif_json if isinstance(notif_json, dict) else {})
            app_logger.warning(f"[MCP握手拒绝] 期望 notifications/initialized，实际收到: {safe_m}")
            raise RuntimeError(f"Protocol violation: expected 'notifications/initialized', got '{safe_m}'")

        app_logger.info("[MCP握手] 收到 notifications/initialized 通知，写入子进程...")
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write(notif_raw + b"\n")
            proc.stdin.flush()

        # --- 步骤 4: 握手成功，进入 READY ---
        self.handshake_state = McpHandshakeState.READY
        app_logger.info(f"[MCP握手成功] 进入 READY 状态，握手期间队列中后续消息数: {len(pending_queue)}")
        return pending_queue

    async def _pump_ws_to_stdin(self, ws, process: subprocess.Popen):
        """将 WebSocket 收到的 JSON-RPC 消息写入子进程 stdin"""
        try:
            while not self._is_stopped():
                message = await ws.recv()
                if isinstance(message, str):
                    data = message.encode("utf-8")
                else:
                    data = message
                if process.stdin and not process.stdin.closed:
                    process.stdin.write(data + b"\n")
                    process.stdin.flush()
        except Exception as e:
            if not self._is_stopped():
                app_logger.warning(f"WebSocket 到子进程管道异常: {sanitize_text(str(e))}")
                raise

    async def _pump_stdout_to_ws(self, process: subprocess.Popen, ws):
        """从子进程 stdout 逐行读取消息并发送至 WebSocket"""
        loop = asyncio.get_running_loop()
        try:
            while not self._is_stopped():
                line = await loop.run_in_executor(None, process.stdout.readline)
                if not line:
                    app_logger.info("子进程输出流已结束")
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    await ws.send(text)
        except Exception as e:
            if not self._is_stopped():
                app_logger.warning(f"子进程到 WebSocket 管道异常: {sanitize_text(str(e))}")
                raise

    async def _pump_stderr_to_log(self, process: subprocess.Popen):
        """将子进程 stderr 转发记录到脱敏日志中"""
        loop = asyncio.get_running_loop()
        try:
            while not self._is_stopped():
                line = await loop.run_in_executor(None, process.stderr.readline)
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    app_logger.info(f"[MCP子进程日志] {sanitize_text(text)}")
        except Exception:
            pass
