"""MCP 子服务真实进程启动、stdio 协议纯净度及 PipeBridge 生命周期回归测试"""

import asyncio
import json
import os
import subprocess
import sys
import time
import pytest
from src.bridge.pipe import PipeBridge, ServiceStatus


PYTHON_EXE = sys.executable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_SCRIPT = os.path.join(PROJECT_ROOT, "src", "main.py")
SERVER_SCRIPT = os.path.join(PROJECT_ROOT, "src", "mcp_server", "server.py")


def test_mcp_service_stdio_clean_and_protocol_rpc():
    """真实子进程验证：
    1. 启动 src/main.py --mcp-service 保持运行；
    2. stdout 绝对不含任何日期、INFO、DEBUG 等普通日志；
    3. MCP initialize 成功返回合法的 JSON-RPC 响应；
    4. MCP tools/list 成功返回包含受控工具的响应。
    """
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = PROJECT_ROOT

    # 启动真实子进程
    process = subprocess.Popen(
        [PYTHON_EXE, MAIN_SCRIPT, "--mcp-service"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        env=env
    )

    try:
        # 给子进程 0.5 秒完成初始化
        time.sleep(0.5)

        # 检查子进程是否健在（没有因 import 错误而崩溃退出）
        assert process.poll() is None, f"MCP 子服务未能保持运行，提前退出码: {process.poll()}"

        # 构造并发送 initialize 请求
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "regression-tester",
                    "version": "1.0.0"
                }
            }
        }
        init_payload = json.dumps(init_req).encode("utf-8") + b"\n"
        process.stdin.write(init_payload)
        process.stdin.flush()

        # 读取第一行输出
        first_line = process.stdout.readline().decode("utf-8", errors="replace").strip()
        assert len(first_line) > 0, "MCP 服务没有任何响应"

        # 验证 stdout 第一行绝对不能是普通日志文本（不能包含日志标签或日期）
        assert "[INFO]" not in first_line, f"stdout 受到日志污染: {first_line}"
        assert "[DEBUG]" not in first_line, f"stdout 受到日志污染: {first_line}"
        assert "MCPAssistant" not in first_line or "jsonrpc" in first_line
        assert "XiaozhiMcpApp" not in first_line or "jsonrpc" in first_line

        # 验证必须能够解析为合法的 JSON-RPC 响应
        init_resp = json.loads(first_line)
        assert init_resp.get("jsonrpc") == "2.0"
        assert init_resp.get("id") == 1
        assert "result" in init_resp
        assert "protocolVersion" in init_resp["result"]
        assert "serverInfo" in init_resp["result"]

        # 发送 notifications/initialized
        notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        process.stdin.write(json.dumps(notif).encode("utf-8") + b"\n")
        process.stdin.flush()

        # 发送 tools/list 请求
        tools_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        process.stdin.write(json.dumps(tools_req).encode("utf-8") + b"\n")
        process.stdin.flush()

        # 读取 tools/list 响应
        tools_line = process.stdout.readline().decode("utf-8", errors="replace").strip()
        tools_resp = json.loads(tools_line)
        assert tools_resp.get("jsonrpc") == "2.0"
        assert tools_resp.get("id") == 2
        assert "result" in tools_resp
        tools_list = tools_resp["result"].get("tools", [])
        tool_names = [t.get("name") for t in tools_list]

        # 验证受控工具已正确就绪
        assert "计算器" in tool_names
        assert "运行电脑端软件文件或程序" in tool_names
        assert "关闭电脑端软件文件或程序" in tool_names
        assert "在电脑上打开URL网址" in tool_names

    finally:
        # 清理子进程
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()


def test_server_py_entrypoint_directly():
    """验证直接运行 src/mcp_server/server.py 也能正常启动并响应协议"""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = PROJECT_ROOT

    proc = subprocess.Popen(
        [PYTHON_EXE, SERVER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        env=env
    )

    try:
        time.sleep(0.5)
        assert proc.poll() is None, f"server.py 启动异常，退出码: {proc.poll()}"

        init_req = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "server-test", "version": "1.0"}
            }
        }
        proc.stdin.write(json.dumps(init_req).encode("utf-8") + b"\n")
        proc.stdin.flush()

        line = proc.stdout.readline().decode("utf-8", errors="replace").strip()
        assert not line.startswith("202"), f"输出不应是日志: {line}"
        resp = json.loads(line)
        assert resp["id"] == 10
        assert "result" in resp

    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_pipe_bridge_lifecycle_restart_and_no_duplicate_threads():
    """验证 PipeBridge 完整生命周期：
    1. 可以正常 start -> stop -> start（无事件循环冲突）；
    2. 重复调用 start() 不会创建多个后台线程；
    """
    statuses = []

    def on_status(s, msg):
        statuses.append((s, msg))

    bridge = PipeBridge(
        endpoint_url="ws://127.0.0.1:59999/non_existent",  # 故意使用不可达端口
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT,
        on_status_change=on_status
    )

    # 1. 首次 start
    bridge.start()
    assert bridge.is_running() is True
    first_thread = bridge._thread

    # 2. 重复调用 start()，应被安全忽略，不产生新线程
    bridge.start()
    assert bridge._thread is first_thread

    # 等待一小会儿确保产生连接尝试
    time.sleep(0.8)

    # 3. 停止服务
    bridge.stop()
    assert bridge.is_running() is False
    assert bridge.status == ServiceStatus.STOPPED

    # 4. 第二次 start，验证旧 loop 与新 loop 切换无 RuntimeError
    bridge.start()
    assert bridge.is_running() is True
    second_thread = bridge._thread
    assert second_thread is not first_thread

    # 再次安全关闭
    time.sleep(0.5)
    bridge.stop()
    assert bridge.is_running() is False


def test_pipe_bridge_reconnect_backoff():
    """验证 PipeBridge 在连接失败后能够进行退避，且 backoff 会翻倍递增"""
    backoff_logs = []

    def on_status(s, msg):
        if s == ServiceStatus.RECONNECTING:
            backoff_logs.append(msg)

    bridge = PipeBridge(
        endpoint_url="ws://127.0.0.1:59998/non_existent",
        python_executable=PYTHON_EXE,
        server_script_path=SERVER_SCRIPT,
        on_status_change=on_status
    )

    bridge.start()
    try:
        # 等待产生连接失败和至少一次退避状态
        time.sleep(2.5)
        # 验证退避尝试次数和重连等待
        assert bridge.reconnect_attempt >= 1
        assert bridge.backoff > bridge.INITIAL_BACKOFF
        assert any("重试" in msg for msg in backoff_logs)
    finally:
        bridge.stop()
        assert bridge.is_running() is False
