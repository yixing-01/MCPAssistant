"""更换连接码独立对话框。

为用户提供专用的连接码更新窗口，默认不显示且不预填保存的 Token，
支持遮罩切换与内联异步验证提示，绝不触发完整向导或应用扫描。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.endpoint import validate_mcp_endpoint
from src.ui_qt.controllers.connection_code_controller import ConnectionCodeController


class ConnectionCodeDialog(QDialog):
    """更换连接码对话框。"""

    def __init__(
        self,
        controller: ConnectionCodeController,
        config_manager,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.controller = controller
        self.config_manager = config_manager
        self.setWindowTitle("更换连接码")
        self.setObjectName("connectionCodeDialog")
        self.setFixedSize(540, 360)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(18)

        # 1. 顶部标题与说明
        title_box = QVBoxLayout()
        title_box.setSpacing(6)
        self.title_label = QLabel("更换连接码")
        self.title_label.setObjectName("pageTitle")
        self.desc_label = QLabel("粘贴新的连接码，验证成功后会安全保存并重新连接。")
        self.desc_label.setObjectName("muted")
        self.desc_label.setWordWrap(True)
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.desc_label)
        layout.addLayout(title_box)

        # 2. 当前保存状态展示卡片（固定脱敏，绝不显示明文 Token）
        status_card = QFrame()
        status_card.setObjectName("card")
        status_card_layout = QHBoxLayout(status_card)
        status_card_layout.setContentsMargins(16, 12, 16, 12)
        status_card_layout.setSpacing(8)

        current_config = self.config_manager.load_config()
        endpoint_val = validate_mcp_endpoint(current_config.mcp_endpoint or "")
        if endpoint_val.is_valid:
            status_text = "当前保存的连接：wss://api.xiaozhi.me/mcp/?token=••••••"
        else:
            status_text = "当前状态：尚未设置有效连接码"

        self.current_status_label = QLabel(status_text)
        self.current_status_label.setObjectName("subText")
        self.current_status_label.setStyleSheet("color: #455064; font-size: 12px;")
        status_card_layout.addWidget(self.current_status_label, 1)
        layout.addWidget(status_card)

        # 3. 新连接码输入区域（默认完全为空）
        input_box = QVBoxLayout()
        input_box.setSpacing(6)

        input_header = QHBoxLayout()
        input_label = QLabel("新连接码")
        input_label.setObjectName("itemTitle")
        input_header.addWidget(input_label)
        input_header.addStretch(1)

        self.toggle_echo_btn = QPushButton("显示明文")
        self.toggle_echo_btn.setFlat(True)
        self.toggle_echo_btn.setStyleSheet(
            "QPushButton { border: none; color: #5367E8; font-size: 11px; font-weight: 600; min-height: 20px; } "
            "QPushButton:hover { color: #4054D9; }"
        )
        self.toggle_echo_btn.clicked.connect(self._toggle_echo)
        input_header.addWidget(self.toggle_echo_btn)
        input_box.addLayout(input_header)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("在此粘贴以 wss:// 开头的新连接码")
        self.input_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_edit.setClearButtonEnabled(True)
        input_box.addWidget(self.input_edit)

        # 内联状态/错误提示
        self.inline_msg = QLabel("")
        self.inline_msg.setObjectName("subText")
        self.inline_msg.setWordWrap(True)
        self.inline_msg.hide()
        input_box.addWidget(self.inline_msg)
        layout.addLayout(input_box)

        layout.addStretch(1)

        # 4. 底部操作按钮
        btn_box = QHBoxLayout()
        btn_box.setSpacing(12)
        btn_box.addStretch(1)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("secondary")
        self.cancel_button.setFixedWidth(90)
        self.cancel_button.clicked.connect(self._on_cancel)
        btn_box.addWidget(self.cancel_button)

        self.save_button = QPushButton("验证并保存")
        self.save_button.setObjectName("primary")
        self.save_button.setFixedWidth(120)
        self.save_button.clicked.connect(self._on_save)
        btn_box.addWidget(self.save_button)
        layout.addLayout(btn_box)

        # 信号绑定
        controller.validating.connect(self._on_validating)
        controller.validation_failed.connect(self._on_validation_failed)
        controller.code_saved.connect(self._on_code_saved)
        controller.save_failed.connect(self._on_save_failed)

    def _toggle_echo(self) -> None:
        if self.input_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.input_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_echo_btn.setText("隐藏内容")
        else:
            self.input_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_echo_btn.setText("显示明文")

    def _on_validating(self) -> None:
        self.save_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.input_edit.setEnabled(False)
        self.save_button.setText("正在验证...")
        self.inline_msg.setStyleSheet("color: #5367E8; font-size: 12px;")
        self.inline_msg.setText("正在连接 MCP 服务器进行验证，请稍候...")
        self.inline_msg.show()

    def _on_validation_failed(self, error: str) -> None:
        self.save_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.input_edit.setEnabled(True)
        self.save_button.setText("验证并保存")
        self.inline_msg.setStyleSheet("color: #D9383A; font-size: 12px;")
        self.inline_msg.setText(error)
        self.inline_msg.show()

    def _on_save_failed(self, error: str) -> None:
        self.save_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.input_edit.setEnabled(True)
        self.save_button.setText("验证并保存")
        self.inline_msg.setStyleSheet("color: #D9383A; font-size: 12px;")
        self.inline_msg.setText(error)
        self.inline_msg.show()

    def _on_code_saved(self) -> None:
        self.accept()

    def _on_save(self) -> None:
        text = self.input_edit.text()
        self.controller.validate_and_save(text)

    def _on_cancel(self) -> None:
        self.controller.cancel()
        self.reject()

    def closeEvent(self, event) -> None:
        self.controller.cancel()
        super().closeEvent(event)
