"""更换连接码控制器。

专用于用户输入新连接码时的格式校验、异步连接测试与事务式安全保存。
绝不触碰或修改已有应用授权、scan_agreed、扫描结果或普通设置。
"""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from src.core.endpoint import test_mcp_connection, validate_mcp_endpoint


class ConnectionCodeController(QObject):
    """更换连接码控制器，独立负责新连接码的校验、测试与保存。"""

    validating = Signal()
    validation_failed = Signal(str)
    code_saved = Signal()
    save_failed = Signal(str)

    # 内部跨线程信号
    _test_completed = Signal(int, bool, str, str)

    def __init__(
        self,
        config_manager,
        tester: Callable[[str], tuple[bool, str]] = test_mcp_connection,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.tester = tester
        self._request_seq = 0
        self._is_active = True
        self._test_completed.connect(
            self._on_test_completed, Qt.ConnectionType.QueuedConnection
        )

    def cancel(self) -> None:
        """取消当前操作并废弃所有正在运行的后台请求结果。"""
        self._request_seq += 1
        self._is_active = False

    @Slot(str)
    def validate_and_save(self, raw_code: str) -> None:
        """先进行本地格式校验，随后启动异步测试；测试成功后才保存新 endpoint。"""
        self._request_seq += 1
        seq = self._request_seq
        self._is_active = True

        code_text = (raw_code or "").strip()
        validation = validate_mcp_endpoint(code_text)
        if not validation.is_valid:
            # 严格脱敏：绝不回显用户输入的内容或 Token
            msg = validation.error_message or "连接码格式不正确，请检查后重新粘贴。"
            self.validation_failed.emit(msg)
            return

        self.validating.emit()
        normalized_url = validation.normalized_url

        def _worker(target_seq: int, endpoint: str):
            try:
                ok, err = self.tester(endpoint)
            except Exception:
                ok, err = False, "网络连接测试出现异常，请稍后重试。"
            self._test_completed.emit(target_seq, ok, err, endpoint)

        threading.Thread(
            target=_worker,
            args=(seq, normalized_url),
            daemon=True,
        ).start()

    @Slot(int, bool, str, str)
    def _on_test_completed(
        self, seq: int, success: bool, error_msg: str, endpoint: str
    ) -> None:
        """在 Qt 主线程接收测试结果并执行单项字段事务保存。"""
        # 丢弃已废弃或过期请求的结果
        if seq != self._request_seq or not self._is_active:
            return

        if not success:
            # 保证错误提示不泄漏真实 endpoint 与 token
            friendly_err = error_msg or "无法连接到 MCP 服务，请检查网络或连接码。"
            self.validation_failed.emit(friendly_err)
            return

        # 仅修改 mcp_endpoint，深拷贝配置以保护其他字段
        try:
            current = self.config_manager.load_config()
            candidate = current.model_copy(deep=True)
            candidate.mcp_endpoint = endpoint
            self.config_manager.save_config(candidate)
            self.code_saved.emit()
        except Exception as e:
            self.save_failed.emit("保存新连接码失败，请重试。")
