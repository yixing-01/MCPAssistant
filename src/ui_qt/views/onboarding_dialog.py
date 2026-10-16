"""MCP助手首次配置三步引导对话框 (方案 A 视觉规范)。

左侧为品牌信任侧栏，右侧包含三步指示器 StepProgressWidget、
紧凑卡片、连接码安全输入与应用勾选。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.ui_qt.widgets.stepper import StepProgressWidget


class OnboardingDialog(QDialog):
    """首次配置三步引导对话框：欢迎、连接 MCP 服务、选择应用。"""

    step_titles = ["欢迎", "连接 MCP 服务", "选择应用"]

    def __init__(self, controller, new_apps=None, parent=None):
        super().__init__(parent)
        self.setObjectName("onboardingDialog")
        self.controller = controller
        self.new_apps = list(new_apps or [])
        self.app_checks: dict[str, QCheckBox] = {}
        self.setWindowTitle("MCP助手 · 首次设置")
        self.setModal(True)
        self.resize(820, 560)
        self.setFixedSize(820, 560)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. 左侧欢迎侧栏 (Welcome-Side)
        welcome_side = QFrame()
        welcome_side.setObjectName("welcomeSide")
        welcome_side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        welcome_side.setFixedWidth(270)
        welcome_side.setStyleSheet(
            "QFrame#welcomeSide { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            "stop:0 #4F63E5, stop:0.55 #6478EC, stop:1 #6888E8); border: none; }"
        )
        side_layout = QVBoxLayout(welcome_side)
        side_layout.setContentsMargins(28, 36, 28, 32)
        side_layout.setSpacing(14)

        big_mark = QLabel("M")
        big_mark.setFixedSize(48, 48)
        big_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        big_mark.setStyleSheet(
            "border-radius: 14px; background: #FFFFFF; color: #4F63E5; "
            "font-size: 20px; font-weight: 900; border: none;"
        )
        side_layout.addWidget(big_mark)

        side_title = QLabel("很高兴见到你")
        side_title.setStyleSheet("font-size: 22px; font-weight: 700; color: #FFFFFF; background: transparent; border: none;")
        side_desc = QLabel("完成一次简单连接后，就可以通过小智帮你打开和关闭电脑上的应用。")
        side_desc.setStyleSheet("font-size: 12px; color: #EDF0FF; line-height: 1.6; background: transparent; border: none;")
        side_desc.setWordWrap(True)
        side_layout.addWidget(side_title)
        side_layout.addWidget(side_desc)
        side_layout.addStretch(1)

        # 底部加密信任卡片
        trust_card = QFrame()
        trust_card.setObjectName("trustCard")
        trust_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        trust_card.setStyleSheet(
            "QFrame#trustCard { background-color: rgba(255, 255, 255, 0.18); "
            "border: 1px solid rgba(255, 255, 255, 0.3); border-radius: 12px; }"
        )
        trust_layout = QVBoxLayout(trust_card)
        trust_layout.setContentsMargins(14, 12, 14, 12)
        trust_layout.setSpacing(4)
        trust_title = QLabel("连接信息会被安全保存")
        trust_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #FFFFFF; background: transparent; border: none;")
        trust_desc = QLabel("只保存在这台电脑上。我们不会在界面、日志或帮助信息中显示完整内容。")
        trust_desc.setStyleSheet("font-size: 10px; color: #F0F2FF; line-height: 1.5; background: transparent; border: none;")
        trust_desc.setWordWrap(True)
        trust_layout.addWidget(trust_title)
        trust_layout.addWidget(trust_desc)
        side_layout.addWidget(trust_card)

        main_layout.addWidget(welcome_side)

        # 2. 右侧主体向导内容 (Wizard Main)
        right_widget = QWidget()
        right_widget.setObjectName("pageRoot")
        right_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(36, 28, 36, 28)
        right_layout.setSpacing(20)

        # 顶部三步指示器
        self.stepper = StepProgressWidget(self.step_titles)
        right_layout.addWidget(self.stepper)

        # 保持与旧测试兼容的 step_label (隐蔽但可检索)
        self.step_label = QLabel("")
        self.step_label.setVisible(False)
        right_layout.addWidget(self.step_label)

        # 步骤卡片栈
        self.stack = QStackedWidget()
        self.stack.addWidget(self._welcome_page())
        self.stack.addWidget(self._connection_page())
        self.stack.addWidget(self._apps_page())
        right_layout.addWidget(self.stack, 1)

        # 底部操作栏
        actions = QHBoxLayout()
        self.back_button = QPushButton("‹ 返回")
        self.back_button.setObjectName("secondary")
        self.back_button.clicked.connect(self.go_back)

        self.next_button = QPushButton("下一步")
        self.next_button.setObjectName("primary")
        self.next_button.clicked.connect(self.go_next)

        actions.addWidget(self.back_button)
        actions.addStretch(1)
        actions.addWidget(self.next_button)
        right_layout.addLayout(actions)

        main_layout.addWidget(right_widget, 1)

        # 关联控制器信号
        controller.validation_started.connect(self._on_validation_started)
        controller.validation_succeeded.connect(lambda: self.show_step(2))
        controller.validation_failed.connect(self._show_error)
        controller.scan_started.connect(self._on_scan_started)
        controller.new_apps_discovered.connect(self.set_new_apps)
        controller.scan_failed.connect(self._show_scan_error)
        controller.save_failed.connect(self._show_error)
        controller.completed.connect(self.accept)

        self.show_step(0)
        if self.new_apps:
            self.set_new_apps(self.new_apps)

    def _welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        heading = QLabel("欢迎使用 MCP助手")
        heading.setObjectName("pageTitle")
        desc = QLabel(
            "只需两分钟，配置你的 MCP 接入地址，轻松通过大模型打开和控制电脑上的应用。\n"
            "所有连接凭据只保存在本机，受到 Windows 数据保护机制严格保护。"
        )
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(desc)

        # 三格功能卡片
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        features = [
            ("安全直连", "不经过多余中转，直接与 MCP 终端安全通信"),
            ("权限自主", "只有你勾选允许的软件，小智才有权打开和关闭"),
            ("随开随用", "后台静默守护，无需任何复杂命令行操作"),
        ]
        for title, text in features:
            fcard = QFrame()
            fcard.setObjectName("card")
            fl = QVBoxLayout(fcard)
            fl.setContentsMargins(12, 12, 12, 12)
            fl.setSpacing(4)
            ft = QLabel(title)
            ft.setObjectName("itemTitle")
            fd = QLabel(text)
            fd.setObjectName("subText")
            fd.setWordWrap(True)
            fl.addWidget(ft)
            fl.addWidget(fd)
            cards_row.addWidget(fcard, 1)

        layout.addLayout(cards_row)
        layout.addStretch(1)
        return page

    def _connection_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        heading = QLabel("粘贴你的连接码")
        heading.setObjectName("pageTitle")
        desc = QLabel(
            "从兼容服务的管理页面复制完整连接码，再粘贴到下面。程序会先确认它是否可用，不会改变其他设置。"
        )
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(desc)

        # 三步指引小步骤条
        steps_box = QHBoxLayout()
        steps_box.setSpacing(8)
        how_steps = [
            ("1", "打开服务管理页面", "进入服务的接入配置"),
            ("2", "复制完整连接码", "不要手动修改内容"),
            ("3", "回到这里粘贴", "验证成功后进入下一步"),
        ]
        for num, t, d in how_steps:
            box = QFrame()
            box.setObjectName("card")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(10, 8, 10, 8)
            bl.setSpacing(2)
            bt = QLabel(f"{num}. {t}")
            bt.setStyleSheet("font-size: 11px; font-weight: 700; color: #344054;")
            bd = QLabel(d)
            bd.setObjectName("subText")
            bl.addWidget(bt)
            bl.addWidget(bd)
            steps_box.addWidget(box, 1)
        layout.addLayout(steps_box)

        # 连接码输入卡片
        input_card = QFrame()
        input_card.setObjectName("card")
        ic_layout = QVBoxLayout(input_card)
        ic_layout.setContentsMargins(14, 12, 14, 12)
        ic_layout.setSpacing(8)

        field_head = QLabel("连接码")
        field_head.setStyleSheet("font-size: 12px; font-weight: 700; color: #20293A;")
        ic_layout.addWidget(field_head)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.endpoint_edit = QLineEdit()
        self.endpoint_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.endpoint_edit.setPlaceholderText("粘贴形如 wss://... 的连接码")
        self.endpoint_edit.setAccessibleName("连接码")
        self.reveal_button = QPushButton("显示")
        self.reveal_button.setObjectName("secondary")
        self.reveal_button.setFixedWidth(64)
        self.reveal_button.clicked.connect(self._toggle_reveal)
        row.addWidget(self.endpoint_edit, 1)
        row.addWidget(self.reveal_button)
        ic_layout.addLayout(row)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #D9383A; font-size: 11px;")
        ic_layout.addWidget(self.error_label)

        layout.addWidget(input_card)
        layout.addStretch(1)
        return page

    def _apps_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        heading = QLabel("选择小智可以打开和关闭的应用")
        heading.setObjectName("pageTitle")
        desc = QLabel("只选择你愿意允许小智打开和关闭的应用，设置完成后随时可以在“我的应用”中更改。")
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(desc)

        action_row = QHBoxLayout()
        self.scan_button = QPushButton("查找此电脑上的应用")
        self.scan_button.setObjectName("secondary")
        self.scan_button.clicked.connect(self.controller.discover_apps)
        action_row.addWidget(self.scan_button)
        self.scan_status = QLabel("")
        self.scan_status.setObjectName("subText")
        action_row.addWidget(self.scan_status)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        # 应用多选滚动容器
        scroll_card = QFrame()
        scroll_card.setObjectName("card")
        sc_layout = QVBoxLayout(scroll_card)
        sc_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.apps_host = QWidget()
        self.apps_layout = QVBoxLayout(self.apps_host)
        self.apps_layout.setContentsMargins(12, 10, 12, 10)
        self.apps_layout.setSpacing(8)
        self.apps_layout.addStretch(1)
        scroll.setWidget(self.apps_host)
        sc_layout.addWidget(scroll)

        layout.addWidget(scroll_card, 1)
        return page

    def set_new_apps(self, apps) -> None:
        """呈现本次安全扫描新发现的应用列表，默认全不勾选。"""
        while self.apps_layout.count() > 1:
            item = self.apps_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.app_checks.clear()

        for app in apps:
            check = QCheckBox(app.display_name)
            check.setStyleSheet("font-size: 13px; font-weight: 500; color: #20293A;")
            check.setAccessibleName(f"允许{app.display_name}")
            self.app_checks[app.app_id] = check
            self.apps_layout.insertWidget(self.apps_layout.count() - 1, check)

        self.scan_status.setText(f"已找到 {len(apps)} 个应用")
        self.scan_button.setEnabled(True)
        self.scan_button.setText("重新查找")

    def show_step(self, step_index: int) -> None:
        """切换至指定步骤并更新顶部三步进度指示器与底部按钮文案。"""
        self.stepper.set_current_step(step_index)
        self.stack.setCurrentIndex(step_index)
        self.step_label.setText(f"第 {step_index + 1} 步，共 3 步")

        self.back_button.setVisible(step_index > 0)
        if step_index == 1:
            self.next_button.setText("验证并继续")
        elif step_index == 2:
            self.next_button.setText("完成设置")
        else:
            self.next_button.setText("下一步")

    def go_back(self) -> None:
        """返回上一步。"""
        idx = self.stack.currentIndex()
        if idx > 0:
            self.show_step(idx - 1)

    def go_next(self) -> None:
        """处理当前步骤的下一步或完成操作。"""
        idx = self.stack.currentIndex()
        if idx == 0:
            self.show_step(1)
        elif idx == 1:
            raw_text = self.endpoint_edit.text().strip()
            self.error_label.setText("")
            self.controller.verify(raw_text)
        elif idx == 2:
            selected_ids = [aid for aid, cb in self.app_checks.items() if cb.isChecked()]
            self.controller.complete(selected_ids)

    def visible_copy(self) -> str:
        """获取向导当前可见文本以供安全测试断言。"""
        labels = [lbl.text() for lbl in self.findChildren(QLabel)]
        buttons = [btn.text() for btn in self.findChildren(QPushButton)]
        checks = [cb.text() for cb in self.findChildren(QCheckBox)]
        return " ".join(labels + buttons + checks)

    def _toggle_reveal(self) -> None:
        """切换连接码密码/明文回显。"""
        if self.endpoint_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.endpoint_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.reveal_button.setText("隐藏")
        else:
            self.endpoint_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.reveal_button.setText("显示")

    def _on_validation_started(self) -> None:
        self.next_button.setEnabled(False)
        self.next_button.setText("正在验证...")
        self.error_label.setText("正在确认连接码可用性...")
        self.error_label.setStyleSheet("color: #4054D9; font-size: 11px;")

    def _show_error(self, message: str) -> None:
        self.next_button.setEnabled(True)
        self.next_button.setText("验证并继续")
        self.error_label.setText(message)
        self.error_label.setStyleSheet("color: #D9383A; font-size: 11px;")

    def _on_scan_started(self) -> None:
        self.scan_button.setEnabled(False)
        self.scan_button.setText("正在查找应用...")
        self.scan_status.setText("正在安全检索电脑中的常用应用...")

    def _show_scan_error(self, message: str) -> None:
        self.scan_button.setEnabled(True)
        self.scan_button.setText("重新查找")
        self.scan_status.setText(f"查找未能完成：{message}")
