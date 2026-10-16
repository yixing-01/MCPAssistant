"""设置页面视图 (方案 A: 温和智能助手)。

为普通用户提供清晰的常规设置、连接与隐私开关行卡片，
采用现代化 ToggleSwitch 开关，默认折叠高级设置和技术详情入口，严禁出现专业技术名词。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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

from src.core.security import PathSandbox
from src.ui_qt.widgets.switch import ToggleSwitch


class SettingRow(QFrame):
    """单行设置项控件：包含标题、副标题说明与右侧 Toggle 开关。"""

    def __init__(
        self,
        title: str,
        description: str,
        switch: ToggleSwitch,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("settingRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(16)

        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("itemTitle")
        self.desc_label = QLabel(description)
        self.desc_label.setObjectName("subText")
        self.desc_label.setWordWrap(True)
        text_box.addWidget(self.title_label)
        text_box.addWidget(self.desc_label)

        layout.addLayout(text_box, 1)
        layout.addWidget(switch)


class SettingsPage(QWidget):
    """设置页面组件。"""

    retry_connection_requested = Signal()
    change_connection_code_requested = Signal()
    help_requested = Signal()
    stop_service_requested = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.setObjectName("pageRoot")
        self.controller = controller
        self.allowed_write_dirs: list[str] = []

        # 开关控件集合
        self.controls: dict[str, ToggleSwitch] = {
            "auto_start_with_windows": ToggleSwitch(),
            "minimize_to_tray_on_close": ToggleSwitch(),
            "allow_run_apps": ToggleSwitch(),
            "allow_open_url": ToggleSwitch(),
            "allow_file_write": ToggleSwitch(),
        }

        # 设置文案描述映射
        self.descriptions = {
            "auto_start_with_windows": ("开机后自动准备好", "电脑启动时MCP助手在后台就绪，无需手动启动"),
            "minimize_to_tray_on_close": ("关闭窗口后继续运行", "关闭主窗口后保持在系统托盘，继续在后台保持连接"),
            "allow_run_apps": ("允许小智控制已授权应用的启动与关闭", "严格按照你在“我的应用”中勾选的软件执行"),
            "allow_open_url": ("允许通过 MCP 打开安全网页", "仅允许通过系统默认浏览器打开标准安全网址"),
            "allow_file_write": ("允许通过 MCP 在指定文件夹创建文件", "限制通过 MCP 写入文件的范围，保护其他磁盘目录"),
        }

        # 设置辅助无障碍名称
        for key, switch in self.controls.items():
            title, _ = self.descriptions[key]
            switch.setAccessibleName(title)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 滚动区域以适应不同分辨率
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("pageRoot")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        # 页面标题
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title = QLabel("设置")
        title.setObjectName("pageTitle")
        title_sub = QLabel("管理MCP助手的运行方式与安全保护权限。")
        title_sub.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(title_sub)
        layout.addLayout(title_box)

        # 卡片分组构建
        sections = (
            ("常规", ("auto_start_with_windows", "minimize_to_tray_on_close")),
            ("连接与隐私", ("allow_run_apps", "allow_open_url", "allow_file_write")),
        )

        for heading, fields in sections:
            card = QFrame()
            card.setObjectName("card")
            card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 12, 0, 12)
            card_layout.setSpacing(0)

            # 分组小标头
            section_header = QLabel(heading)
            section_header.setObjectName("sectionTitle")
            section_header.setStyleSheet("padding: 0 16px 8px 16px;")
            card_layout.addWidget(section_header)

            # 在“连接与隐私”卡片靠前位置增加“MCP 连接”行
            if heading == "连接与隐私":
                conn_row = QFrame()
                conn_row.setObjectName("settingRow")
                conn_layout = QHBoxLayout(conn_row)
                conn_layout.setContentsMargins(16, 12, 16, 12)
                conn_layout.setSpacing(16)

                conn_text_box = QVBoxLayout()
                conn_text_box.setSpacing(3)
                conn_title = QLabel("MCP 连接")
                conn_title.setObjectName("itemTitle")
                self.connection_status_label = QLabel("连接码已安全保存")
                self.connection_status_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #16845B;")
                self.connection_endpoint_label = QLabel("wss://api.xiaozhi.me/mcp/?token=••••••")
                self.connection_endpoint_label.setObjectName("subText")

                conn_text_box.addWidget(conn_title)
                conn_text_box.addWidget(self.connection_status_label)
                conn_text_box.addWidget(self.connection_endpoint_label)
                conn_layout.addLayout(conn_text_box, 1)

                conn_btns = QHBoxLayout()
                conn_btns.setSpacing(8)
                self.retry_button = QPushButton("立即重试连接")
                self.retry_button.setObjectName("secondary")
                self.retry_button.setFixedHeight(34)
                self.retry_button.clicked.connect(self.retry_connection_requested)

                self.change_code_button = QPushButton("更换连接码")
                self.change_code_button.setObjectName("secondary")
                self.change_code_button.setFixedHeight(34)
                self.change_code_button.clicked.connect(self.change_connection_code_requested)

                conn_btns.addWidget(self.retry_button)
                conn_btns.addWidget(self.change_code_button)
                conn_layout.addLayout(conn_btns)

                card_layout.addWidget(conn_row)

            for field in fields:
                t, d = self.descriptions[field]
                row_widget = SettingRow(t, d, self.controls[field])
                card_layout.addWidget(row_widget)

                # 文件写入目录定制区域
                if field == "allow_file_write":
                    dir_box = QWidget()
                    dir_box_layout = QVBoxLayout(dir_box)
                    dir_box_layout.setContentsMargins(16, 4, 16, 8)
                    dir_box_layout.setSpacing(8)

                    dir_action_bar = QHBoxLayout()
                    dir_action_bar.setSpacing(8)
                    dir_hint = QLabel("受信任的保存文件夹：")
                    dir_hint.setObjectName("subText")
                    dir_action_bar.addWidget(dir_hint, 1)

                    self.add_dir_btn = QPushButton("添加文件夹")
                    self.add_dir_btn.setObjectName("secondary")
                    self.add_dir_btn.setFixedHeight(30)
                    self.add_dir_btn.clicked.connect(self._choose_write_dir)

                    self.clear_dirs_btn = QPushButton("清空全部")
                    self.clear_dirs_btn.setObjectName("secondary")
                    self.clear_dirs_btn.setFixedHeight(30)
                    self.clear_dirs_btn.clicked.connect(self._clear_write_dirs)

                    dir_action_bar.addWidget(self.add_dir_btn)
                    dir_action_bar.addWidget(self.clear_dirs_btn)
                    dir_box_layout.addLayout(dir_action_bar)

                    self.dir_list_container = QVBoxLayout()
                    self.dir_list_container.setSpacing(4)
                    dir_box_layout.addLayout(self.dir_list_container)

                    self.write_dir_label = QLabel("尚未选择保存文件夹")
                    self.write_dir_label.setObjectName("subText")
                    self.dir_list_container.addWidget(self.write_dir_label)

                    card_layout.addWidget(dir_box)

            layout.addWidget(card)

        # 底部操作栏：仅保留高级设置折叠与保存设置，不保留容易产生误解的重新连接按钮
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(12)

        self.advanced_button = QPushButton("高级设置  ›")
        self.advanced_button.setObjectName("secondary")
        self.advanced_button.clicked.connect(self._toggle_advanced)
        btn_bar.addWidget(self.advanced_button)
        btn_bar.addStretch(1)

        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("primary")
        self.save_button.setFixedWidth(120)
        self.save_button.clicked.connect(self._save)
        btn_bar.addWidget(self.save_button)
        layout.addLayout(btn_bar)

        # 高级设置折叠面板
        self.advanced_panel = QFrame()
        self.advanced_panel.setObjectName("card")
        self.advanced_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        advanced_layout = QVBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(16, 14, 16, 14)
        advanced_layout.setSpacing(12)

        adv_title = QLabel("高级设置")
        adv_title.setObjectName("sectionTitle")
        advanced_layout.addWidget(adv_title)

        detail = QLabel("连接信息、后台服务状态与故障排查技术详情。通常无需手动修改。")
        detail.setObjectName("muted")
        advanced_layout.addWidget(detail)

        adv_btns = QHBoxLayout()
        adv_btns.setSpacing(10)
        stop_button = QPushButton("停止后台连接")
        stop_button.setObjectName("secondary")
        stop_button.clicked.connect(self.stop_service_requested)
        adv_btns.addWidget(stop_button)

        help_button = QPushButton("打开技术详情")
        help_button.setObjectName("secondary")
        help_button.clicked.connect(self.help_requested)
        adv_btns.addWidget(help_button)
        adv_btns.addStretch(1)
        advanced_layout.addLayout(adv_btns)

        self.advanced_panel.hide()
        layout.addWidget(self.advanced_panel)

        layout.addStretch(1)
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        # 信号绑定
        controller.loaded.connect(self._load_values)
        controller.saved.connect(self._on_saved)
        controller.save_failed.connect(lambda text: QMessageBox.warning(self, "没有保存", text))
        controller.load()

    def _toggle_advanced(self) -> None:
        will_show = self.advanced_panel.isHidden()
        self.advanced_panel.setVisible(will_show)
        self.advanced_button.setText("高级设置  ⌄" if will_show else "高级设置  ›")

    def _update_dir_list(self) -> None:
        while self.dir_list_container.count():
            item = self.dir_list_container.takeAt(0)
            w = item.widget()
            if w and w is not self.write_dir_label:
                w.deleteLater()

        if not self.allowed_write_dirs:
            self.write_dir_label.setText("尚未选择保存文件夹")
            self.write_dir_label.show()
            self.dir_list_container.addWidget(self.write_dir_label)
            self.clear_dirs_btn.setEnabled(False)
        else:
            self.write_dir_label.setText("；".join(self.allowed_write_dirs))
            self.write_dir_label.hide()
            self.clear_dirs_btn.setEnabled(True)
            for d in self.allowed_write_dirs:
                row = QWidget()
                r_layout = QHBoxLayout(row)
                r_layout.setContentsMargins(8, 2, 8, 2)
                r_layout.setSpacing(8)
                lbl = QLabel(d)
                lbl.setStyleSheet("font-size: 12px; color: #344054;")
                lbl.setWordWrap(True)
                r_layout.addWidget(lbl, 1)

                rm_btn = QPushButton("移除")
                rm_btn.setObjectName("secondary")
                rm_btn.setFixedHeight(24)
                rm_btn.setFixedWidth(52)
                rm_btn.clicked.connect(lambda _, target=d: self._remove_write_dir(target))
                r_layout.addWidget(rm_btn)
                self.dir_list_container.addWidget(row)

    def _load_values(self, values: dict) -> None:
        for key, control in self.controls.items():
            control.setChecked(bool(values.get(key, False)))
        self.allowed_write_dirs = list(values.get("allowed_write_dirs", []))
        self._update_dir_list()
        has_valid = bool(values.get("has_valid_endpoint", False))
        masked = values.get("masked_endpoint", "")
        if has_valid:
            self.connection_status_label.setText("连接码已安全保存")
            self.connection_status_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #16845B;")
        else:
            self.connection_status_label.setText("尚未设置连接码")
            self.connection_status_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #D9383A;")
        if masked:
            self.connection_endpoint_label.setText(masked)

    def _on_saved(self, values: dict) -> None:
        self._load_values(values)
        QMessageBox.information(self, "设置已保存", "您的设置已成功保存。")

    def _choose_write_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择允许保存文件的文件夹")
        if not path:
            return
        is_safe, reason = PathSandbox.is_safe_write_directory(path)
        if not is_safe:
            QMessageBox.warning(self, "无法添加该文件夹", reason)
            return
        norm = os.path.realpath(os.path.abspath(path))
        if norm not in self.allowed_write_dirs:
            self.allowed_write_dirs.append(norm)
            self._update_dir_list()

    def _remove_write_dir(self, target: str) -> None:
        if target in self.allowed_write_dirs:
            self.allowed_write_dirs.remove(target)
            self._update_dir_list()

    def _clear_write_dirs(self) -> None:
        if not self.allowed_write_dirs:
            return
        reply = QMessageBox.question(
            self,
            "清空文件夹",
            "确定要清空所有已授权的保存文件夹吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.allowed_write_dirs.clear()
            self._update_dir_list()

    def _save(self) -> None:
        values = {key: control.isChecked() for key, control in self.controls.items()}
        values["allowed_write_dirs"] = list(self.allowed_write_dirs)
        self.controller.save(values)

    def visible_copy(self) -> str:
        """获取页面当前所有可见标签、按钮与选择框文字用于安全测试断言。"""
        labels = [label.text() for label in self.findChildren(QLabel)]
        buttons = [button.text() for button in self.findChildren(QPushButton)]
        checks = [check.text() for check in self.findChildren(QCheckBox)]
        switches = [s.accessibleName() for s in self.findChildren(ToggleSwitch)]
        return " ".join(labels + buttons + checks + switches)
