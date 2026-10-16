from __future__ import annotations

import os
import sys
import threading
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from src.bridge.pipe import PipeBridge, ServiceStatus
from src.core.endpoint import validate_mcp_endpoint
from src.ui_qt.state import ProductStatus, map_service_status


class ServiceController(QObject):
    """MCP助手服务生命周期控制器，管理唯一的 PipeBridge 实例并将底层状态转换为产品状态。"""

    product_status_changed = Signal(object)
    user_error = Signal(str)
    busy_changed = Signal(bool)
    stop_finished = Signal(bool)
    restart_finished = Signal(bool)
    shutdown_finished = Signal(bool)
    _raw_status = Signal(object, str)
    _async_stop_done = Signal(bool)
    _async_restart_done = Signal(bool)
    _async_shutdown_done = Signal(bool)

    def __init__(
        self,
        config_manager,
        bridge_factory: Callable[..., PipeBridge] = PipeBridge,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.bridge_factory = bridge_factory
        self.bridge: PipeBridge | None = None
        self._is_busy = False
        self._shutdown_requested = False
        self._active_worker_count = 0
        self._op_lock = threading.Lock()
        # 使用队列连接确保跨线程回调安全发送到 Qt 事件循环中
        self._raw_status.connect(self._accept_status, Qt.ConnectionType.QueuedConnection)
        self._async_stop_done.connect(self._on_async_stop_done, Qt.ConnectionType.QueuedConnection)
        self._async_restart_done.connect(self._on_async_restart_done, Qt.ConnectionType.QueuedConnection)
        self._async_shutdown_done.connect(self._on_async_shutdown_done, Qt.ConnectionType.QueuedConnection)

    def is_busy(self) -> bool:
        """检查控制器当前是否正在执行后台异步停止或重启操作。"""
        return self._is_busy

    def _server_command(self) -> tuple[str, str]:
        """确定 MCP 服务的 Python 解释器与脚本/参数入口。"""
        if getattr(sys, "frozen", False):
            return sys.executable, "--mcp-service"
        ui_dir = os.path.dirname(os.path.abspath(__file__))
        src_dir = os.path.dirname(os.path.dirname(ui_dir))
        return sys.executable, os.path.join(src_dir, "mcp_server", "server.py")

    @Slot()
    def start(self) -> None:
        """启动后台服务连接。验证连接码有效性，并防止重复启动。"""
        config = self.config_manager.load_config()
        validation = validate_mcp_endpoint(config.mcp_endpoint or "")
        if not validation.is_valid:
            self.user_error.emit("连接码无效，请重新配置 MCP 连接。")
            self.product_status_changed.emit(
                map_service_status(
                    ServiceStatus.STOPPED,
                    "",
                    has_valid_endpoint=False,
                )
            )
            return
        if self.bridge is not None and self.bridge.is_running():
            return
        python_executable, server_script = self._server_command()
        self.bridge = self.bridge_factory(
            endpoint_url=validation.normalized_url,
            python_executable=python_executable,
            server_script_path=server_script,
            on_status_change=lambda status, message: self._raw_status.emit(status, message),
        )
        self.bridge.start()

    @Slot()
    def stop(self, emit_error: bool = True) -> bool:
        """停止后台服务连接。若停止超时则返回 False 并提示用户。"""
        if self.bridge is None:
            return True
        if not self.bridge.stop():
            if emit_error and not self._shutdown_requested:
                self.user_error.emit("MCP 服务仍在停止中，请稍后再试。")
            return False
        self.bridge = None
        self._accept_status(ServiceStatus.STOPPED, "用户已停止")
        return True

    @Slot()
    def restart(self) -> bool:
        """立即重试连接：先停止旧 Bridge，停止成功后使用当前已保存连接码重新启动。"""
        if self.bridge is not None:
            if not self.stop():
                return False
        self.start()
        return True

    @property
    def is_busy(self):
        """当前是否正在执行后台异步停止或重启操作。支持属性与方法两种访问风格。"""
        with self._op_lock:
            val = self._is_busy

        class _BusyFlag:
            def __call__(self) -> bool:
                return val

            def __bool__(self) -> bool:
                return val

            def __eq__(self, other):
                return val == other

            def __repr__(self):
                return repr(val)

        return _BusyFlag()

    @Slot()
    def stop_async(self) -> bool:
        """在后台工作线程中异步执行停止操作，避免阻塞 Qt 界面主线程。"""
        with self._op_lock:
            if self._is_busy:
                return False
            if self.bridge is None:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.stop_finished.emit(True))
                return True
            self._is_busy = True
            self._active_worker_count += 1
            self.busy_changed.emit(True)

        def _worker():
            success = False
            try:
                success = self.stop()
            finally:
                with self._op_lock:
                    self._active_worker_count -= 1
                    is_shutdown = self._shutdown_requested
                if is_shutdown:
                    self._async_shutdown_done.emit(self.bridge is None and success)
                else:
                    self._async_stop_done.emit(success)

        t = threading.Thread(target=_worker, name="ServiceControllerStopThread", daemon=True)
        t.start()
        return True

    @Slot(bool)
    def _on_async_stop_done(self, success: bool) -> None:
        with self._op_lock:
            self._is_busy = False
        self.busy_changed.emit(False)
        self.stop_finished.emit(success)

    @Slot()
    def restart_async(self) -> bool:
        """在后台工作线程中异步执行重启操作，避免阻塞 Qt 界面主线程。"""
        with self._op_lock:
            if self._is_busy or self._shutdown_requested:
                return False
            self._is_busy = True
            self._active_worker_count += 1
            self.busy_changed.emit(True)

        def _worker():
            success = False
            try:
                # 1. 先安全停止旧 Bridge
                stop_ok = True
                if self.bridge is not None:
                    stop_ok = self.stop()

                # 2. 检查在此期间是否收到了关闭退出请求，并原子消除竞态窗口
                with self._op_lock:
                    if self._shutdown_requested:
                        # 收到关闭请求，绝不再创建和启动新 Bridge
                        return
                    if not stop_ok:
                        self._async_restart_done.emit(False)
                        return
                    self.start()
                    success = True
                self._async_restart_done.emit(success)
            finally:
                with self._op_lock:
                    self._active_worker_count -= 1
                    is_shutdown = self._shutdown_requested
                    has_bridge = (self.bridge is not None)

                if is_shutdown:
                    if has_bridge:
                        self.stop(emit_error=False)
                    self._async_shutdown_done.emit(self.bridge is None)

        t = threading.Thread(target=_worker, name="ServiceControllerRestartThread", daemon=True)
        t.start()
        return True

    @Slot(bool)
    def _on_async_restart_done(self, success: bool) -> None:
        with self._op_lock:
            self._is_busy = False
        self.busy_changed.emit(False)
        self.restart_finished.emit(success)

    @Slot()
    def shutdown_async(self) -> bool:
        """异步退出协调：即使在重启 busy 期间也绝不丢失关闭请求，确保清理所有 Bridge 并通知退出。"""
        with self._op_lock:
            self._shutdown_requested = True
            if self.bridge is None and not self._is_busy and self._active_worker_count == 0:
                from PySide6.QtCore import QTimer
                def _notify():
                    self.stop_finished.emit(True)
                    self.shutdown_finished.emit(True)
                QTimer.singleShot(0, _notify)
                return True

            if not self._is_busy:
                self._is_busy = True
                self._active_worker_count += 1
                self.busy_changed.emit(True)

                def _shutdown_worker():
                    try:
                        ok = True
                        if self.bridge is not None:
                            ok = self.stop(emit_error=False)
                    finally:
                        with self._op_lock:
                            self._active_worker_count -= 1
                        self._async_shutdown_done.emit(self.bridge is None and ok)

                t = threading.Thread(target=_shutdown_worker, name="ServiceControllerShutdownThread", daemon=True)
                t.start()
            return True

    @Slot(bool)
    def _on_async_shutdown_done(self, success: bool) -> None:
        with self._op_lock:
            self._is_busy = False
        self.busy_changed.emit(False)
        self.stop_finished.emit(success)
        self.shutdown_finished.emit(success)

    @Slot(object, str)
    def _accept_status(self, status: ServiceStatus, message: str) -> None:
        """接收底层 ServiceStatus 并映射为面向用户的 ProductStatus 发射信号。"""
        config = self.config_manager.load_config()
        valid = validate_mcp_endpoint(config.mcp_endpoint or "").is_valid
        attempts = self.bridge.reconnect_attempt if self.bridge is not None else 0
        product = map_service_status(
            status,
            message,
            has_valid_endpoint=valid,
            reconnect_attempt=attempts,
        )
        self.product_status_changed.emit(product)
