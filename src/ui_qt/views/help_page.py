"""帮助与故障排查视图。

引导用户执行常用自愈操作（重试、网络检查、重连），
技术详情默认折叠，提供安全的脱敏诊断摘要与日志导出。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class HelpPage(QWidget):
    """帮助与故障排查页面。"""

    retry_connection_requested = Signal()
    retry_requested = retry_connection_requested
    network_settings_requested = Signal()
    change_connection_code_requested = Signal()
    back_requested = Signal()

    def __init__(self, diagnostics_controller, parent=None):
        super().__init__(parent)
        self.diagnostics_controller = diagnostics_controller

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        # 顶栏包含返回按钮和页面标题
        header = QHBoxLayout()
        self.back_button = QPushButton("‹  返回")
        self.back_button.setObjectName("secondary")
        self.back_button.setFixedWidth(90)
        self.back_button.clicked.connect(self.back_requested)
        header.addWidget(self.back_button)

        title = QLabel("帮助与故障排查")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        # 优先展示普通恢复操作
        actions_card = QFrame()
        actions_card.setObjectName("card")
        actions_layout = QVBoxLayout(actions_card)
        actions_layout.setSpacing(12)
        actions_label = QLabel("常用恢复操作")
        actions_label.setObjectName("sectionTitle")
        actions_layout.addWidget(actions_label)

        self.retry_button = QPushButton("立即重试连接")
        self.retry_button.setObjectName("primary")
        self.retry_button.clicked.connect(self._on_retry)
        actions_layout.addWidget(self.retry_button)

        self.network_button = QPushButton("检查网络")
        self.network_button.clicked.connect(self.network_settings_requested)
        actions_layout.addWidget(self.network_button)

        self.change_code_button = QPushButton("更换连接码")
        self.change_code_button.setObjectName("secondary")
        self.change_code_button.clicked.connect(self.change_connection_code_requested)
        actions_layout.addWidget(self.change_code_button)

        layout.addWidget(actions_card)

        # 技术详情折叠区域
        self.technical_button = QPushButton("技术详情  ›")
        self.technical_button.clicked.connect(self._toggle_technical)
        layout.addWidget(self.technical_button)

        self.technical_panel = QFrame()
        self.technical_panel.setObjectName("card")
        panel_layout = QVBoxLayout(self.technical_panel)
        panel_layout.setSpacing(12)

        tech_title = QLabel("系统运行与诊断信息")
        tech_title.setObjectName("sectionTitle")
        panel_layout.addWidget(tech_title)

        self.summary = QLabel("")
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary.setWordWrap(True)
        panel_layout.addWidget(self.summary)

        self.export_button = QPushButton("导出脱敏日志")
        self.export_button.clicked.connect(self._export)
        panel_layout.addWidget(self.export_button)

        self.technical_panel.hide()
        layout.addWidget(self.technical_panel)

        layout.addStretch(1)
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    def _on_retry(self) -> None:
        """用户点击立即重试连接。"""
        self.retry_connection_requested.emit()

    def set_summary(self, text: str) -> None:
        """更新技术详情展示内容。"""
        self.summary.setText(text)

    def _toggle_technical(self) -> None:
        """折叠/展开技术详情面板。"""
        will_show = self.technical_panel.isHidden()
        self.technical_panel.setVisible(will_show)
        self.technical_button.setText("技术详情  ⌄" if will_show else "技术详情  ›")

    def _export(self) -> None:
        """弹出文件保存对话框并调用脱敏日志导出。"""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出脱敏日志",
            "mcp-assistant-diagnostics.txt",
            "文本文件 (*.txt)",
        )
        if path:
            self.diagnostics_controller.export_logs(path)
            QMessageBox.information(self, "导出成功", "脱敏日志已成功保存。")

    def visible_copy(self) -> str:
        """获取可见文案字符串用于自动化安全检查。"""
        labels = [label.text() for label in self.findChildren(QLabel)]
        buttons = [button.text() for button in self.findChildren(QPushButton)]
        return " ".join(labels + buttons)
