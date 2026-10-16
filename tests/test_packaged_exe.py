"""打包后独立 EXE (MCPAssistant.exe) 的真实子进程 MCP 协议交互测试"""

import json
import os
import queue
import subprocess
import threading
import time
import pytest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_EXE = os.path.join(PROJECT_ROOT, "dist", "MCPAssistant", "MCPAssistant.exe")


def _read_line_with_timeout(pipe, timeout: float = 8.0) -> str:
    """带超时保护的管道行读取，防止子进程挂起导致测试永久阻塞"""
    result_queue = queue.Queue()

    def _reader():
        try:
            line = pipe.readline()
            result_queue.put(("line", line))
        except Exception as e:
            result_queue.put(("error", e))

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    try:
        kind, val = result_queue.get(timeout=timeout)
        if kind == "error":
            raise val
        if isinstance(val, bytes):
            return val.decode("utf-8", errors="replace").strip()
        return str(val).strip()
    except queue.Empty:
        raise TimeoutError(f"读取子进程 stdout 超时（超过 {timeout} 秒未返回有效响应）")


def test_packaged_exe_mcp_service_initialize_and_tools_list(tmp_path):
    """真实运行打包产物 MCPAssistant.exe --mcp-service：
    验证：
    1. 打包后的独立 EXE 存在；
    2. 能以子进程启动并保持运行；
    3. stdout 没有日志污染；
    4. initialize 协议握手成功；
    5. tools/list 工具枚举成功，包含安全受控工具。
    """
    assert os.path.exists(DIST_EXE), f"打包产物不存在: {DIST_EXE}"

    # 打包烟测必须隔离用户真实配置、目录与日志，禁止读写真实 APPDATA/LOCALAPPDATA。
    isolated_appdata = tmp_path / "AppData" / "Roaming"
    isolated_localappdata = tmp_path / "AppData" / "Local"
    isolated_appdata.mkdir(parents=True)
    isolated_localappdata.mkdir(parents=True)
    child_env = os.environ.copy()
    child_env["APPDATA"] = str(isolated_appdata)
    child_env["LOCALAPPDATA"] = str(isolated_localappdata)

    # 启动打包后的 EXE
    process = subprocess.Popen(
        [DIST_EXE, "--mcp-service"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        env=child_env,
    )

    try:
        # 给打包程序 1.5 秒完成依赖加载与启动
        time.sleep(1.5)

        # 验证进程没有闪退
        assert process.poll() is None, f"打包 EXE 提前退出，退出码: {process.poll()}"

        # 1. 发送 initialize 请求
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "packaged-exe-tester",
                    "version": "1.0.0"
                }
            }
        }
        process.stdin.write(json.dumps(init_req).encode("utf-8") + b"\n")
        process.stdin.flush()

        # 读取响应（带超时保护）
        first_line = _read_line_with_timeout(process.stdout, timeout=8.0)
        assert len(first_line) > 0, "打包 EXE 没有向 stdout 输出响应"

        # 验证 stdout 纯净无日志
        assert "[INFO]" not in first_line, f"stdout 含有普通日志: {first_line}"
        assert "[DEBUG]" not in first_line, f"stdout 含有普通日志: {first_line}"

        init_resp = json.loads(first_line)
        assert init_resp.get("jsonrpc") == "2.0"
        assert init_resp.get("id") == 1
        assert "result" in init_resp
        assert "serverInfo" in init_resp["result"]

        # 2. 发送 notifications/initialized
        notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        process.stdin.write(json.dumps(notif).encode("utf-8") + b"\n")
        process.stdin.flush()

        # 3. 发送 tools/list
        tools_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        process.stdin.write(json.dumps(tools_req).encode("utf-8") + b"\n")
        process.stdin.flush()

        tools_line = _read_line_with_timeout(process.stdout, timeout=8.0)
        tools_resp = json.loads(tools_line)
        assert tools_resp.get("id") == 2
        assert "result" in tools_resp
        tools = tools_resp["result"].get("tools", [])
        tool_names = [t.get("name") for t in tools]

        assert "计算器" in tool_names
        assert "运行电脑端软件文件或程序" in tool_names
        assert "关闭电脑端软件文件或程序" in tool_names
        assert "在电脑上打开URL网址" in tool_names

    finally:
        try:
            if process.stdin:
                process.stdin.close()
        except Exception:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except Exception:
                    pass
        try:
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
        except Exception:
            pass


def test_packaged_exe_store_app_provider_smoke(tmp_path):
    """验证打包 EXE 能够正常加载 pywin32 COM 组件并执行 AppsFolder 枚举"""
    assert os.path.exists(DIST_EXE), f"打包产物不存在: {DIST_EXE}"
    isolated_appdata = tmp_path / "AppData" / "Roaming"
    isolated_localappdata = tmp_path / "AppData" / "Local"
    isolated_appdata.mkdir(parents=True)
    isolated_localappdata.mkdir(parents=True)
    child_env = os.environ.copy()
    child_env["APPDATA"] = str(isolated_appdata)
    child_env["LOCALAPPDATA"] = str(isolated_localappdata)

    res = subprocess.run(
        [DIST_EXE, "--smoke-store-enum"],
        capture_output=True,
        text=True,
        env=child_env,
        timeout=15,
    )
    assert res.returncode == 0, f"烟测执行失败: stderr={res.stderr}"
    assert "STORE_ENUM_OK" in res.stdout

