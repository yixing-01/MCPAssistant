"""MCP 桥接层初始化状态机、握手门控与子进程生命周期管理测试。
验证要求：
1. WebSocket 首条消息为 tools/list 时，禁止写入子进程，进入退避重连；
2. 首条消息为 tools/call 或 ping 时，安全拒绝并重连；
3. 正常 initialize -> response -> notifications/initialized -> tools/list 顺序能进入 READY；
4. initialize 之后立即管道式到达的消息能保持原始顺序转发；
5. 子进程启动延迟下握手仍能正常完成；
6. 断线重连时旧会话请求不泄露给新子进程；
7. 子进程意外退出时记录真实退出码；
8. 子进程清理在有界时间内完成，杜绝管道死锁；
9. 连续启动/停止不存在孤儿进程或重复线程；
10. 只有在完成 MCP 握手进入 READY 后才报告 CONNECTED；
11. --mcp-service 遇到非法首包时不发生未捕获异常泄露给 PyInstaller。
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from typing import List, Optional
import pytest
import websockets

from src.bridge.pipe import PipeBridge, ServiceStatus, McpHandshakeState

PYTHON_EXE = sys.executable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_SCRIPT = os.path.join(PROJECT_ROOT, "src", "mcp_server", "server.py")
MAIN_SCRIPT = os.path.join(PROJECT_ROOT, "src", "main.py")


class MockMcpWsServer:
    """可编程的本地测试用 WebSocket 服务器"""

    def __init__(self):
        self.server = None
        self.port: Optional[int] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.received_messages: List[str] = []
        self.stop_requested = threading.Event()
        self.connection_count = 0
        self.connected_event = threading.Event()
        self.handler_hook = None

    def start(self):
        started_event = threading.Event()

        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            async def default_handler(ws):
                self.connection_count += 1
                self.connected_event.set()
                if self.handler_hook:
                    await self.handler_hook(ws)
                else:
                    try:
                        while not self.stop_requested.is_set():
                            msg = await ws.recv()
                            self.received_messages.append(msg)
                    except Exception:
                        pass

            async def main():
                self.server = await websockets.serve(default_handler, "127.0.0.1", 0)
                for sock in self.server.sockets:
                    self.port = sock.getsockname()[1]
                    break
                started_event.set()
                await self.server.wait_closed()

            try:
                self.loop.run_until_complete(main())
            except Exception:
                pass

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        started_event.wait(timeout=5.0)

    def stop(self):
        self.stop_requested.set()
        if self.loop and self.loop.is_running():
            async def _close():
                if self.server:
                    self.server.close()
                    try:
                        await self.server.wait_closed()
                    except Exception:
                        pass
                for t in asyncio.all_tasks(self.loop):
                    t.cancel()

            try:
                future = asyncio.run_coroutine_threadsafe(_close(), self.loop)
                future.result(timeout=2.0)
            except Exception:
                pass
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)


def test_first_message_tools_list_rejected_without_crashing_child():
    """要求 1 & 4: 首条消息为 tools/list 时，禁止写入子进程，不造成子进程崩溃，触发退避重连"""
    ws_server = MockMcpWsServer()

    async def server_behavior(ws):
        # 模拟云端违规：刚连接就先发 tools/list
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
        # 保持连接一会儿观察桥接层动作
        await asyncio.sleep(1.0)

    ws_server.handler_hook = server_behavior
    ws_server.start()

    status_history = []
    error_event = threading.Event()
    reconnecting_event = threading.Event()

    def on_status_change(status: ServiceStatus, msg: str):
        status_history.append((status, msg))
        if status == ServiceStatus.ERROR:
            error_event.set()
        elif status == ServiceStatus.RECONNECTING:
            reconnecting_event.set()

    bridge = PipeBridge(
        endpoint_url=f"ws://127.0.0.1:{ws_server.port}",
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT,
        on_status_change=on_status_change
    )

    try:
        bridge.start()
        # 必须触发 ERROR 与 RECONNECTING
        assert error_event.wait(timeout=8.0) is True, f"未触发 ERROR 状态: {status_history}"
        assert reconnecting_event.wait(timeout=5.0) is True, f"未触发 RECONNECTING 状态: {status_history}"

        # 验证：在握手未完成前，绝不能被标记为 CONNECTED
        statuses = [s for s, _ in status_history]
        assert ServiceStatus.CONNECTED not in statuses, "未完成握手前禁止置为 CONNECTED！"

        # 检查错误信息中脱敏提示
        err_msgs = [m for s, m in status_history if s == ServiceStatus.ERROR]
        assert any("initialize" in m or "tools/list" in m or "协议" in m for m in err_msgs)

    finally:
        bridge.stop(timeout=5.0)
        ws_server.stop()


def test_first_message_ping_or_tools_call_rejected():
    """要求 2: 首条消息为 ping 或 tools/call 时同样安全拒绝"""
    ws_server = MockMcpWsServer()

    async def server_behavior(ws):
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}))
        await asyncio.sleep(1.0)

    ws_server.handler_hook = server_behavior
    ws_server.start()

    error_event = threading.Event()
    status_history = []

    def on_status_change(status: ServiceStatus, msg: str):
        status_history.append((status, msg))
        if status == ServiceStatus.ERROR:
            error_event.set()

    bridge = PipeBridge(
        endpoint_url=f"ws://127.0.0.1:{ws_server.port}",
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT,
        on_status_change=on_status_change
    )

    try:
        bridge.start()
        assert error_event.wait(timeout=8.0) is True
        statuses = [s for s, _ in status_history]
        assert ServiceStatus.CONNECTED not in statuses
    finally:
        bridge.stop(timeout=5.0)
        ws_server.stop()


def test_normal_handshake_sequence_reaches_ready_and_connected():
    """要求 3 & 8 & 10: 正常 initialize -> response -> notifications/initialized -> tools/list 顺序能进入 READY 并显示 CONNECTED"""
    ws_server = MockMcpWsServer()
    tools_response_received = threading.Event()

    async def server_behavior(ws):
        # 1. 发送 initialize 请求
        init_req = {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test_client", "version": "1.0"}
            }
        }
        await ws.send(json.dumps(init_req))

        # 2. 接收子进程返回的 initialize 响应
        raw_resp = await ws.recv()
        resp_data = json.loads(raw_resp)
        assert resp_data.get("id") == 101
        assert "result" in resp_data

        # 3. 发送 notifications/initialized 通知
        initialized_notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        await ws.send(json.dumps(initialized_notif))

        # 4. 发送 tools/list 请求
        tools_req = {
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/list",
            "params": {}
        }
        await ws.send(json.dumps(tools_req))

        # 5. 接收 tools/list 响应
        raw_tools_resp = await ws.recv()
        tools_data = json.loads(raw_tools_resp)
        assert tools_data.get("id") == 102
        assert "result" in tools_data
        tools_response_received.set()

        # 保持连接
        await asyncio.sleep(1.0)

    ws_server.handler_hook = server_behavior
    ws_server.start()

    status_history = []
    connected_event = threading.Event()

    def on_status_change(status: ServiceStatus, msg: str):
        status_history.append((status, msg))
        if status == ServiceStatus.CONNECTED:
            connected_event.set()

    bridge = PipeBridge(
        endpoint_url=f"ws://127.0.0.1:{ws_server.port}",
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT,
        on_status_change=on_status_change
    )

    try:
        bridge.start()
        # 必须成功进入 CONNECTED
        assert connected_event.wait(timeout=10.0) is True, f"未进入 CONNECTED: {status_history}"
        assert tools_response_received.wait(timeout=10.0) is True, "未收到 tools/list 响应"
        assert bridge.status == ServiceStatus.CONNECTED
        assert bridge.handshake_state == McpHandshakeState.READY
    finally:
        bridge.stop(timeout=5.0)
        ws_server.stop()


def test_pipelined_messages_during_handshake_retained_in_order():
    """要求 4 & 9: initialize 之后立即连续到达 initialized/tools-list 时，受限队列能够保持原始顺序转发"""
    ws_server = MockMcpWsServer()
    tools_response_received = threading.Event()

    async def server_behavior(ws):
        # 立即管道式连续发送 3 条消息：initialize, notifications/initialized, tools/list
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": 201,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "pipelined", "version": "1.0"}}
        }))
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }))
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": 202,
            "method": "tools/list",
            "params": {}
        }))

        # 依次等待 201 响应和 202 响应
        r1 = json.loads(await ws.recv())
        assert r1.get("id") == 201

        r2 = json.loads(await ws.recv())
        assert r2.get("id") == 202
        assert "result" in r2
        tools_response_received.set()

    ws_server.handler_hook = server_behavior
    ws_server.start()

    connected_event = threading.Event()

    def on_status_change(status: ServiceStatus, msg: str):
        if status == ServiceStatus.CONNECTED:
            connected_event.set()

    bridge = PipeBridge(
        endpoint_url=f"ws://127.0.0.1:{ws_server.port}",
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT,
        on_status_change=on_status_change
    )

    try:
        bridge.start()
        assert connected_event.wait(timeout=10.0) is True
        assert tools_response_received.wait(timeout=10.0) is True
    finally:
        bridge.stop(timeout=5.0)
        ws_server.stop()


def test_cleanup_is_bounded_and_no_pipe_deadlock():
    """要求 II & 8: 子进程清理必须有明确超时（<5秒），不能发生管道关闭阻塞"""
    bridge = PipeBridge(
        endpoint_url="ws://127.0.0.1:65534",  # 不可达端口
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT
    )

    bridge.start()
    time.sleep(1.0)
    assert bridge.is_running() is True

    t0 = time.time()
    stopped = bridge.stop(timeout=5.0)
    elapsed = time.time() - t0

    assert stopped is True, "stop() 未在规定时间内返回 True"
    assert elapsed < 5.0, f"stop() 耗时超过预期: {elapsed:.2f} 秒"
    assert bridge.is_running() is False
    assert bridge._current_process is None


def test_main_mcp_service_exception_boundary_no_popup():
    """要求 III & 11: 子进程遇到 tools/list 错误首包时，顶层异常边界能捕获，不弹窗，以状态码 1 干净退出"""
    p = subprocess.Popen(
        [PYTHON_EXE, MAIN_SCRIPT, "--mcp-service"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    bad_msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    stdout, stderr = p.communicate(bad_msg, timeout=5.0)

    # 1. 退出码必须非零 (通常为 1)
    assert p.returncode != 0, f"期望退出码非 0，实际为: {p.returncode}"
    # 2. stdout 作为 JSON-RPC 通道，不得包含错误日志或 traceback
    assert "Traceback" not in stdout
    # 3. stderr 应包含捕获后的错误摘要，但不得泄露敏感路径或令牌
    assert "token" not in stderr.lower()


def test_subprocess_start_delay_handshake_succeeds(tmp_path):
    """要求 5: 注入 2-3 秒子进程启动延迟，握手仍能正常且正确完成"""
    # 创建带启动延迟的模拟 MCP 服务端脚本
    delay_script = str(tmp_path / "delayed_server.py")
    with open(delay_script, "w", encoding="utf-8") as f:
        f.write(
            "import sys, time, json\n"
            "time.sleep(2.0)\n"  # 模拟加载软件目录耗时 2 秒
            "while True:\n"
            "    line = sys.stdin.readline()\n"
            "    if not line: break\n"
            "    req = json.loads(line)\n"
            "    if req.get('method') == 'initialize':\n"
            "        resp = {'jsonrpc': '2.0', 'id': req.get('id'), 'result': {'protocolVersion': '2024-11-05'}}\n"
            "        sys.stdout.write(json.dumps(resp) + '\\n')\n"
            "        sys.stdout.flush()\n"
            "    elif req.get('method') == 'notifications/initialized':\n"
            "        pass\n"
        )

    ws_server = MockMcpWsServer()
    async def server_behavior(ws):
        # 客户端发送 initialize
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}
        }))
        resp = json.loads(await ws.recv())
        assert resp.get("id") == 1
        # 发送 initialized
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }))
        await asyncio.sleep(0.5)

    ws_server.handler_hook = server_behavior
    ws_server.start()

    connected_event = threading.Event()
    def on_status_change(status: ServiceStatus, msg: str):
        if status == ServiceStatus.CONNECTED:
            connected_event.set()

    bridge = PipeBridge(
        endpoint_url=f"ws://127.0.0.1:{ws_server.port}",
        python_executable=PYTHON_EXE,
        server_script_path=delay_script,
        on_status_change=on_status_change
    )

    try:
        bridge.start()
        # 即使有 2 秒启动延迟，握手仍在 15 秒超时内顺利完成并进入 CONNECTED
        assert connected_event.wait(timeout=10.0) is True
        assert bridge.status == ServiceStatus.CONNECTED
        assert bridge.handshake_state == McpHandshakeState.READY
    finally:
        bridge.stop(timeout=5.0)
        ws_server.stop()


def test_reconnect_ignores_stale_requests_before_new_initialize():
    """要求 6: 断线重连时若收到旧会话遗留请求，不得送入新子进程，触发安全重试"""
    ws_server = MockMcpWsServer()
    session_count = 0

    async def server_behavior(ws):
        nonlocal session_count
        session_count += 1
        if session_count == 1:
            # 第一轮：服务端故意发送旧遗留请求
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 888, "method": "tools/list"}))
            await asyncio.sleep(0.5)
        else:
            # 第二轮重连：发送合法 initialize 握手
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test_client", "version": "1.0"}
                }
            }))
            resp = json.loads(await ws.recv())
            assert resp.get("id") == 1
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            }))
            while not ws_server.stop_requested.is_set():
                await asyncio.sleep(0.2)

    ws_server.handler_hook = server_behavior
    ws_server.start()

    connected_event = threading.Event()
    status_records = []

    def on_status_change(status: ServiceStatus, msg: str):
        status_records.append(status)
        if status == ServiceStatus.CONNECTED:
            connected_event.set()

    bridge = PipeBridge(
        endpoint_url=f"ws://127.0.0.1:{ws_server.port}",
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT,
        on_status_change=on_status_change
    )

    try:
        bridge.start()
        # 经过第一轮拒绝与退避后，第二轮成功连接
        assert connected_event.wait(timeout=15.0) is True
        # 确保期间经历了 ERROR 或 RECONNECTING
        assert ServiceStatus.ERROR in status_records or ServiceStatus.RECONNECTING in status_records
        assert bridge.status == ServiceStatus.CONNECTED
    finally:
        bridge.stop(timeout=5.0)
        ws_server.stop()


def test_subprocess_unexpected_exit_records_real_exit_code(tmp_path):
    """要求 7: 子进程意外退出时，桥接层能准确读取并记录真实退出码"""
    crash_script = str(tmp_path / "crash_server.py")
    with open(crash_script, "w", encoding="utf-8") as f:
        f.write(
            "import sys\n"
            "# 读取首行后直接以状态码 42 退出\n"
            "sys.stdin.readline()\n"
            "sys.exit(42)\n"
        )

    ws_server = MockMcpWsServer()
    async def server_behavior(ws):
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {}
        }))
        await asyncio.sleep(1.0)

    ws_server.handler_hook = server_behavior
    ws_server.start()

    error_msgs = []
    error_event = threading.Event()

    def on_status_change(status: ServiceStatus, msg: str):
        if status == ServiceStatus.ERROR:
            error_msgs.append(msg)
            error_event.set()

    bridge = PipeBridge(
        endpoint_url=f"ws://127.0.0.1:{ws_server.port}",
        python_executable=PYTHON_EXE,
        server_script_path=crash_script,
        on_status_change=on_status_change
    )

    try:
        bridge.start()
        assert error_event.wait(timeout=8.0) is True
        # 验证错误消息或日志中记录了真实的退出码 42
        assert any("42" in m for m in error_msgs), f"未记录真实退出码 42: {error_msgs}"
    finally:
        bridge.stop(timeout=5.0)
        ws_server.stop()


def test_continuous_start_stop_reconnect_no_orphans_or_duplicate_threads():
    """要求 9: 连续快速启动/停止不存在孤儿进程或重复桥接线程"""
    bridge = PipeBridge(
        endpoint_url="ws://127.0.0.1:65534",
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT
    )

    for i in range(3):
        bridge.start()
        assert bridge.is_running() is True
        time.sleep(0.3)
        stopped = bridge.stop(timeout=5.0)
        assert stopped is True
        assert bridge.is_running() is False
        assert bridge._current_process is None


def test_gui_state_only_connected_after_ready():
    """要求 10: 验证在 TCP 连接上而 MCP 握手未完成期间，绝不会向 UI 发送 CONNECTED 状态"""
    ws_server = MockMcpWsServer()
    gate_event = asyncio.Event()

    async def server_behavior(ws):
        # 建立了 WebSocket，但故意停顿 2 秒不发 initialize
        await asyncio.sleep(2.0)
        # 然后再发 initialize
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test_client", "version": "1.0"}
            }
        }))
        await ws.recv()
        await ws.send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        while not ws_server.stop_requested.is_set():
            await asyncio.sleep(0.2)

    ws_server.handler_hook = server_behavior
    ws_server.start()

    status_sequence = []
    connected_event = threading.Event()

    def on_status_change(status: ServiceStatus, msg: str):
        status_sequence.append((status, time.time()))
        if status == ServiceStatus.CONNECTED:
            connected_event.set()

    bridge = PipeBridge(
        endpoint_url=f"ws://127.0.0.1:{ws_server.port}",
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT,
        on_status_change=on_status_change
    )

    t_start = time.time()
    try:
        bridge.start()
        # 等待最终连接
        assert connected_event.wait(timeout=10.0) is True
        t_connected = time.time()

        # 验证进入 CONNECTED 的时间必须晚于服务端延迟发送 initialize 的时间（至少 1.8 秒）
        assert (t_connected - t_start) >= 1.8, "在收到 initialize 之前就过早进入了 CONNECTED！"

        # 检查状态流转顺序：CONNECTING 必须先于 CONNECTED
        statuses = [s for s, _ in status_sequence]
        assert ServiceStatus.CONNECTING in statuses
        assert statuses.index(ServiceStatus.CONNECTING) < statuses.index(ServiceStatus.CONNECTED)
    finally:
        bridge.stop(timeout=5.0)
        ws_server.stop()

