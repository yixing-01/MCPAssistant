from __future__ import annotations

import threading
from typing import Callable, Iterable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from src.core.endpoint import test_mcp_connection, validate_mcp_endpoint


class OnboardingController(QObject):
    """首次配置与重新连接控制器，负责异步连接码验证、应用安全扫描及配置落盘事务。"""

    validation_started = Signal()
    validation_succeeded = Signal()
    validation_failed = Signal(str)
    scan_started = Signal()
    new_apps_discovered = Signal(list)
    scan_failed = Signal(str)
    save_failed = Signal(str)
    completed = Signal()
    _verification_finished = Signal(int, bool, str, str)
    _scan_finished = Signal(int, object)

    def __init__(
        self,
        config_manager,
        catalog_manager,
        tester: Callable[[str], tuple[bool, str]] = test_mcp_connection,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.catalog_manager = catalog_manager
        self.tester = tester
        self.pending_endpoint = ""
        self._request_id = 0
        self._scan_request_id = 0

        # 通过队列连接将后台工作线程结果安全派发到 Qt 线程
        self._verification_finished.connect(
            self._accept_verification,
            Qt.ConnectionType.QueuedConnection,
        )
        self._scan_finished.connect(
            self._accept_scan,
            Qt.ConnectionType.QueuedConnection,
        )

    @Slot(str)
    def verify(self, raw_endpoint: str) -> None:
        """异步校验连接码，不冻结界面，使用请求序列号防止旧结果覆盖。"""
        validation = validate_mcp_endpoint(raw_endpoint)
        if not validation.is_valid:
            self.validation_failed.emit(validation.error_message)
            return
        self._request_id += 1
        request_id = self._request_id
        normalized = validation.normalized_url
        self.validation_started.emit()

        def work() -> None:
            try:
                ok, message = self.tester(normalized)
            except Exception:
                ok, message = False, "连接失败，请检查网络后重试。"
            safe_message = "连接成功" if ok else "连接失败，请检查网络后重试。"
            self._verification_finished.emit(
                request_id, ok, safe_message, normalized
            )

        threading.Thread(target=work, name="EndpointVerify", daemon=True).start()

    @Slot(int, bool, str, str)
    def _accept_verification(
        self, request_id: int, ok: bool, message: str, normalized: str
    ) -> None:
        """接收验证结果；若请求 ID 已过期则丢弃。"""
        if request_id != self._request_id:
            return
        if not ok:
            self.validation_failed.emit(message)
            return
        self.pending_endpoint = normalized
        self.validation_succeeded.emit()

    @Slot()
    def discover_apps(self) -> None:
        """在用户明确同意后，异步扫描本机可启动应用。"""
        self._scan_request_id += 1
        request_id = self._scan_request_id
        self.scan_started.emit()

        def work() -> None:
            try:
                result = self.catalog_manager.refresh_scan(user_agreed=True)
            except Exception:
                result = None
            self._scan_finished.emit(request_id, result)

        threading.Thread(target=work, name="OnboardingCatalogScan", daemon=True).start()

    @Slot(int, object)
    def _accept_scan(self, request_id: int, result) -> None:
        """接收应用扫描结果，只提取本次新增且有效的应用条目。"""
        if request_id != self._scan_request_id:
            return
        if result is None:
            self.scan_failed.emit("应用列表暂时无法读取，请重试。")
            return
        new_ids = set(result.new_ids)
        new_apps = [app for app in result.all_apps if app.app_id in new_ids and app.is_valid]
        self.new_apps_discovered.emit(new_apps)

    def invalidate_pending_requests(self) -> None:
        """废弃当前所有正在进行的异步验证与扫描请求。"""
        self._request_id += 1
        self._scan_request_id += 1

    @Slot(list)
    def complete(self, selected_new_ids: Iterable[str]) -> None:
        """完成向导：安全保存配置（DPAPI），并仅对本次显式勾选的新增应用授权。"""
        config = self.config_manager.load_config().model_copy(deep=True)
        config.mcp_endpoint = self.pending_endpoint
        config.scan_agreed = True
        try:
            self.config_manager.save_config(config)
        except Exception:
            self.save_failed.emit("连接信息未能安全保存，请重试。")
            return
        selected = list(selected_new_ids)
        if selected:
            try:
                self.catalog_manager.batch_set_enabled(True, app_ids=selected)
            except Exception:
                self.save_failed.emit("应用允许范围没有保存，请重试。")
                return
        self.completed.emit()
