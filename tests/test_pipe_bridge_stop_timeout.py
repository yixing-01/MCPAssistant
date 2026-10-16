"""验证 PipeBridge 停止超时时线程引用保护与禁止重复启动测试"""

import threading
import time
import pytest
from src.bridge.pipe import PipeBridge, ServiceStatus


def test_pipe_bridge_stop_timeout_retains_thread_and_prevents_new_start():
    """测试场景：后台线程因外部原因阻塞，在 stop() 指定超时内未退出。
    验证：
    1. stop() 返回 False；
    2. _thread 引用被保留，绝不设为 None；
    3. is_running() 依然返回 True；
    4. 状态被设置为 ERROR 并带有清晰说明；
    5. 再次调用 start() 会被拒绝，不会创建新线程。
    """
    bridge = PipeBridge(
        endpoint_url="ws://127.0.0.1:9/dummy",
        python_executable="python",
        server_script_path="dummy.py"
    )

    # 模拟一个故意不退出的后台线程
    hang_event = threading.Event()

    def stubborn_worker():
        # 模拟线程被阻塞，即使收到停止请求也不退出，直到测试最后被显式释放
        hang_event.wait(timeout=10.0)

    stubborn_thread = threading.Thread(target=stubborn_worker, name="StubbornThread", daemon=True)
    stubborn_thread.start()

    # 将该线程赋给 bridge._thread，模拟正在运行的桥接线程
    bridge._thread = stubborn_thread
    bridge.status = ServiceStatus.CONNECTED
    assert bridge.is_running() is True

    try:
        # 调用 stop，使用极短的超时时间（0.1秒）触发超时
        result = bridge.stop(timeout=0.1)

        # 1. 验证返回值必须为 False
        assert result is False, "线程仍存活时 stop() 必须返回 False"

        # 2. 验证 _thread 引用被保留
        assert bridge._thread is stubborn_thread, "线程存活时禁止清空 _thread 引用"

        # 3. 验证 is_running() 依然为 True
        assert bridge.is_running() is True, "线程存活时 is_running() 必须返回 True"

        # 4. 验证状态进入 ERROR
        assert bridge.status == ServiceStatus.ERROR, f"状态应为 ERROR，当前为 {bridge.status}"

        # 5. 尝试再次调用 start()，必须被拒绝，不能创建新线程
        old_thread = bridge._thread
        bridge.start()
        assert bridge._thread is old_thread, "在旧线程存活时禁止启动新线程"

    finally:
        # 释放挂起线程，防止资源泄漏
        hang_event.set()
        stubborn_thread.join(timeout=1.0)


def test_pipe_bridge_stop_success_returns_true():
    """测试场景：后台线程在超时时间内正常退出。
    验证：
    1. stop() 返回 True；
    2. _thread 引用被安全置为 None；
    3. is_running() 返回 False；
    4. 状态为 STOPPED。
    """
    bridge = PipeBridge(
        endpoint_url="ws://127.0.0.1:9/dummy",
        python_executable="python",
        server_script_path="dummy.py"
    )

    def quick_worker():
        # 快速退出的工作线程
        time.sleep(0.05)

    quick_thread = threading.Thread(target=quick_worker, name="QuickThread", daemon=True)
    quick_thread.start()
    bridge._thread = quick_thread

    result = bridge.stop(timeout=1.0)
    assert result is True, "正常停止时 stop() 必须返回 True"
    assert bridge._thread is None
    assert bridge.is_running() is False
    assert bridge.status == ServiceStatus.STOPPED
