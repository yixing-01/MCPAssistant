"""本地 WebSocket 管道运行时异常传播与退避重试集成测试"""

import asyncio
import json
import os
import sys
import threading
import time
import pytest
import websockets
from src.bridge.pipe import PipeBridge, ServiceStatus

PYTHON_EXE = sys.executable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_SCRIPT = os.path.join(PROJECT_ROOT, "src", "mcp_server", "server.py")


class LocalWsServerController:
    """受控本地 WebSocket 服务器控制器"""

    def __init__(self):
        self.server = None
        self.port = None
        self.loop = None
        self.thread = None
        self.connected_event = threading.Event()
        self.second_connected_event = threading.Event()
        self.connection_times = []
        self.disconnect_time = None
        self.client_socket = None
        self.stop_requested = threading.Event()
        self.disconnect_first_client = threading.Event()

    def start(self):
        started_event = threading.Event()

        def run_server():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            async def handler(websocket):
                self.client_socket = websocket
                now = time.time()
                self.connection_times.append(now)
                is_first = len(self.connection_times) == 1
                if is_first:
                    try:
                        await websocket.send(json.dumps({
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1.0"}
                            }
                        }))
                        await websocket.recv()
                        await websocket.send(json.dumps({
                            "jsonrpc": "2.0",
                            "method": "notifications/initialized"
                        }))
                    except Exception:
                        pass
                    self.connected_event.set()
                elif len(self.connection_times) >= 2:
                    self.second_connected_event.set()

                # 保持连接，直到外部通知断开或服务器停止
                try:
                    while not self.stop_requested.is_set():
                        if is_first and self.disconnect_first_client.is_set():
                            break
                        await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    pass

            async def main():
                # 监听 127.0.0.1 的随机空闲端口
                self.server = await websockets.serve(handler, "127.0.0.1", 0)
                # 获取实际绑定的端口
                for sock in self.server.sockets:
                    self.port = sock.getsockname()[1]
                    break
                started_event.set()
                await self.server.wait_closed()

            try:
                self.loop.run_until_complete(main())
            except (asyncio.CancelledError, Exception):
                pass
            finally:
                try:
                    pending = [t for t in asyncio.all_tasks(self.loop) if t is not asyncio.current_task()]
                    for t in pending:
                        t.cancel()
                    if pending:
                        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                try:
                    self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                except Exception:
                    pass
                try:
                    self.loop.close()
                except Exception:
                    pass

        self.thread = threading.Thread(target=run_server, daemon=True)
        self.thread.start()
        started_event.wait(timeout=3.0)

    def close_client_connection(self):
        """主动断开与客户端的连接，模拟网络中断或服务端故障"""
        self.disconnect_time = time.time()
        self.disconnect_first_client.set()
        if self.client_socket and self.loop and self.loop.is_running():
            def _fail():
                try:
                    if hasattr(self.client_socket, "fail_connection"):
                        self.client_socket.fail_connection(1011, "Test server deliberate disconnect")
                    elif hasattr(self.client_socket, "transport") and self.client_socket.transport:
                        self.client_socket.transport.close()
                except Exception:
                    pass
            self.loop.call_soon_threadsafe(_fail)

    def stop(self):
        self.stop_requested.set()
        if self.loop and self.loop.is_running():
            async def _shutdown():
                if self.server:
                    self.server.close()
                    try:
                        await self.server.wait_closed()
                    except Exception:
                        pass
                pending = [t for t in asyncio.all_tasks(self.loop) if t is not asyncio.current_task()]
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            try:
                future = asyncio.run_coroutine_threadsafe(_shutdown(), self.loop)
                future.result(timeout=2.0)
            except Exception:
                pass
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)


def test_pipe_bridge_runtime_pump_error_propagation_and_backoff():
    """验证真正的 WebSocket 连接建立后，pump 异常能够正确传播到 _run_bridge 并执行退避"""
    ws_server = LocalWsServerController()
    ws_server.start()
    assert ws_server.port is not None, "本地 WebSocket 服务器启动失败"

    ws_url = f"ws://127.0.0.1:{ws_server.port}"
    status_records = []
    error_event = threading.Event()
    reconnecting_event = threading.Event()

    def on_status_change(status: ServiceStatus, msg: str):
        status_records.append((status, msg, time.time()))
        if status == ServiceStatus.ERROR:
            error_event.set()
        elif status == ServiceStatus.RECONNECTING:
            reconnecting_event.set()

    bridge = PipeBridge(
        endpoint_url=ws_url,
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT,
        on_status_change=on_status_change
    )

    try:
        # 1. 启动桥接器
        bridge.start()
        assert bridge.is_running() is True

        # 2. 等待成功建立第一次连接并进入 CONNECTED
        client_connected = ws_server.connected_event.wait(timeout=5.0)
        assert client_connected is True, "PipeBridge 未能在 5 秒内连接到本地 WebSocket 服务器"

        # 确认已进入 CONNECTED 状态
        for _ in range(30):
            if bridge.status == ServiceStatus.CONNECTED:
                break
            time.sleep(0.1)
        assert bridge.status == ServiceStatus.CONNECTED, f"期望状态为 CONNECTED，当前为: {bridge.status}"
        assert bridge._current_process is not None
        assert bridge._current_process.poll() is None, "MCP 子进程未正常运行"

        # 3. 由测试服务器主动断开连接，触发 pump 任务的异常传播
        ws_server.close_client_connection()

        # 4. 验证异常是否传播到 _run_bridge，并触发 ERROR 和 RECONNECTING
        got_error = error_event.wait(timeout=20.0)
        assert got_error is True, f"未能在连接断开后捕获到 ERROR 状态事件: records={[(s.value, m) for s, m, _ in status_records]}, is_set={error_event.is_set()}"

        got_reconnecting = reconnecting_event.wait(timeout=15.0)
        assert got_reconnecting is True, "未能在断开后触发 RECONNECTING 退避状态"

        # 验证退避生效：在退避等待期间，重连尝试计数已递增且退避倍数已更新
        assert bridge.reconnect_attempt >= 1
        assert bridge.backoff > bridge.INITIAL_BACKOFF

        # 检查进入 RECONNECTING 后的退避状态描述包含重试提示
        reconnecting_msgs = [msg for (s, msg, _) in status_records if s == ServiceStatus.RECONNECTING]
        assert any("重试" in m for m in reconnecting_msgs), f"退避消息未提示重试: {reconnecting_msgs}"

        # 5. 真正验证重连退避时间：等待 PipeBridge 发起第二次连接
        # 第二次连接必须等待至少 INITIAL_BACKOFF (1.0s) 的退避时间，设置合理误差下限 0.8s，上限 10.0s
        got_second = ws_server.second_connected_event.wait(timeout=10.0)
        assert got_second is True, "PipeBridge 未能在预期超时时间内发起第二次重连"

        assert len(ws_server.connection_times) >= 2, "未记录到两次连接时间戳"
        t1 = ws_server.connection_times[0]
        t2 = ws_server.connection_times[1]
        t_disc = ws_server.disconnect_time

        reconnect_delay = t2 - (t_disc if t_disc else t1)
        inter_connection_delay = t2 - t1

        assert reconnect_delay >= 0.8, (
            f"退避时间违规：断开到重连间隔仅 {reconnect_delay:.3f} 秒，小于合理的 0.8 秒下限"
        )
        assert reconnect_delay <= 30.0, (
            f"退避时间异常过长：断开到重连间隔达 {reconnect_delay:.3f} 秒"
        )
        assert inter_connection_delay >= 0.8, (
            f"两次连接间隔过短: {inter_connection_delay:.3f} 秒"
        )

    finally:
        # 6. 可靠清理所有资源，防止遗留后台进程或未关闭套接字
        bridge.stop(timeout=6.0)
        ws_server.stop()
        assert bridge.is_running() is False
        assert bridge._current_process is None
