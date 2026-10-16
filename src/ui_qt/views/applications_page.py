"""MCP助手“我的应用”管理页面视图 (方案 A)。

包含应用搜索、分段药丸分组标签（已允许/未允许/需要处理）、
单项 Toggle 开关、安全添加与失效修复入口。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ui_qt.presentation.apps import AppBucket
from src.ui_qt.widgets.application_row import ApplicationRow


class ApplicationsPage(QWidget):
    """“我的应用”管理页面，包含搜索、三分组标签页、单项开关与安全添加/修复。"""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.setObjectName("pageRoot")
        self.controller = controller
        self.rows: list[ApplicationRow] = []
        self._rows_by_id: dict[str, ApplicationRow] = {}
        self._pending_scroll_anchor: dict | None = None
        self._is_busy: bool = False

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        # 1. 顶部操作栏
        header = QHBoxLayout()
        header.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title = QLabel("我的应用")
        title.setObjectName("pageTitle")
        title_sub = QLabel("决定小智可以打开和关闭哪些软件，随时可以更改。")
        title_sub.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(title_sub)
        header.addLayout(title_box, 1)

        self.refresh_button = QPushButton("更新应用列表")
        self.refresh_button.setObjectName("secondary")

        self.add_button = QPushButton("＋ 添加应用")
        self.add_button.setObjectName("primary")

        # 次要管理操作（折叠在更多下拉菜单中）
        self.more_button = QToolButton()
        self.more_button.setObjectName("applicationsMoreButton")
        self.more_button.setText("···")
        self.more_button.setAccessibleName("更多应用管理操作")
        self.more_button.setToolTip("只影响当前列表中显示的应用")

        menu = QMenu(self.more_button)
        menu.setObjectName("applicationsMoreMenu")
        self.allow_visible_action = menu.addAction("允许这些应用")
        self.allow_visible_action.setToolTip("只影响当前列表中显示的应用")
        self.revoke_visible_action = menu.addAction("取消允许")
        self.revoke_visible_action.setToolTip("只影响当前列表中显示的应用")
        self.allow_visible_action.triggered.connect(lambda: self._confirm_batch(True))
        self.revoke_visible_action.triggered.connect(lambda: self._confirm_batch(False))
        self.more_button.setMenu(menu)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        header.addWidget(self.refresh_button)
        header.addWidget(self.add_button)
        header.addWidget(self.more_button)
        root.addLayout(header)

        # 2. 温和安全提示横条 (Notice Bar)：通过专属 ID 配合 QSS 限定选择器渲染
        self.notice_frame = QFrame()
        self.notice_frame.setObjectName("applicationsNoticeBar")
        self.notice_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        notice_layout = QHBoxLayout(self.notice_frame)
        notice_layout.setContentsMargins(16, 10, 16, 10)
        notice_layout.setSpacing(12)

        notice_text_box = QVBoxLayout()
        notice_text_box.setSpacing(2)
        notice_strong = QLabel("只显示能够安全控制的应用")
        notice_strong.setStyleSheet("font-size: 12px; font-weight: 700; color: #344054; background: transparent; border: none;")
        notice_span = QLabel("危险系统工具会自动隐藏；可以修复的应用会显示在“需要处理”中。")
        notice_span.setObjectName("subText")
        notice_span.setStyleSheet("background: transparent; border: none;")
        notice_text_box.addWidget(notice_strong)
        notice_text_box.addWidget(notice_span)

        notice_layout.addLayout(notice_text_box, 1)
        root.addWidget(self.notice_frame)

        # 3. 搜索栏与分段药丸切换器
        tool_bar = QHBoxLayout()
        tool_bar.setSpacing(16)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索应用，例如：微信")
        self.search.setMinimumWidth(300)
        tool_bar.addWidget(self.search, 1)

        # 分段药丸 Tabs (Segmented Control)
        tabs_container = QFrame()
        tabs_container.setStyleSheet(
            "background-color: #ECECE8; border-radius: 10px; border: none;"
        )
        tabs_layout = QHBoxLayout(tabs_container)
        tabs_layout.setContentsMargins(4, 4, 4, 4)
        tabs_layout.setSpacing(4)

        self.bucket_buttons: list[QPushButton] = []
        tab_btn_qss = (
            "QPushButton { border: none; border-radius: 8px; padding: 6px 14px; "
            "background-color: transparent; color: #667085; font-size: 12px; font-weight: 600; min-height: 28px; } "
            "QPushButton:checked { background-color: #FFFFFF; color: #4054D9; font-weight: 700; } "
            "QPushButton:hover:!checked { color: #20293A; }"
        )
        for label, bucket in (
            ("已允许 0", AppBucket.ALLOWED),
            ("未允许 0", AppBucket.NOT_ALLOWED),
            ("需要处理 0", AppBucket.NEEDS_ATTENTION),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(tab_btn_qss)
            btn.clicked.connect(lambda checked=False, value=bucket: self._select_bucket(value))
            tabs_layout.addWidget(btn)
            self.bucket_buttons.append(btn)

        tool_bar.addWidget(tabs_container)
        root.addLayout(tool_bar)

        # 4. 应用列表白底卡片容器：外层提供 16px 圆角与微边框，内部透明以杜绝覆盖父级圆角
        list_card = QFrame()
        list_card.setObjectName("appListCard")
        list_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        list_card_layout = QVBoxLayout(list_card)
        list_card_layout.setContentsMargins(4, 6, 4, 6)
        list_card_layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("appListScrollArea")
        self.scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        if self.scroll.viewport() is not None:
            self.scroll.viewport().setObjectName("qt_scrollarea_viewport")
            self.scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.list_host = QWidget()
        self.list_host.setObjectName("appListHost")
        self.list_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(6, 4, 6, 4)
        self.list_layout.setSpacing(0)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        list_card_layout.addWidget(self.scroll)
        root.addWidget(list_card, 1)

        # 信号绑定
        self.search.textChanged.connect(self._on_search_text_changed)
        self.refresh_button.clicked.connect(controller.refresh_scan)
        self.add_button.clicked.connect(self._add_manual)
        controller.items_changed.connect(self.set_items)
        controller.counts_changed.connect(self.set_counts)
        controller.busy_changed.connect(self._set_busy)
        controller.user_error.connect(lambda text: QMessageBox.warning(self, "需要处理", text))

        self._select_bucket(AppBucket.ALLOWED)

    def _on_search_text_changed(self, text: str) -> None:
        """搜索关键字变更时，清除锚点并将滚动条复位到顶部。"""
        self._pending_scroll_anchor = None
        self.controller.set_query(text)
        vbar = self.scroll.verticalScrollBar()
        if vbar:
            vbar.setValue(0)

    def _select_bucket(self, bucket: AppBucket) -> None:
        """选择分组并高亮对应按钮，同时重置滚动条到顶部。"""
        self._pending_scroll_anchor = None
        buckets = [AppBucket.ALLOWED, AppBucket.NOT_ALLOWED, AppBucket.NEEDS_ATTENTION]
        for index, b in enumerate(buckets):
            self.bucket_buttons[index].setChecked(b is bucket)
        self.controller.set_bucket(bucket)
        vbar = self.scroll.verticalScrollBar()
        if vbar:
            vbar.setValue(0)

    def _on_row_enabled_changed(self, app_id: str, enabled: bool) -> None:
        """单项授权状态改变时保存滚动锚点，防止 Qt 因焦点丢失导致跳到底部。"""
        # 待处理或不可切换条目忽略授权开关变更
        row = self._rows_by_id.get(app_id)
        if row and not getattr(row.item, "can_toggle", True):
            return

        # 清除列表内部可能获取焦点的控件焦点，阻断 Qt 默认向后寻找新焦点的行为
        focused = QApplication.focusWidget()
        if focused and self.scroll.isAncestorOf(focused):
            focused.clearFocus()

        vbar = self.scroll.verticalScrollBar()
        current_scroll = vbar.value() if vbar else 0

        self._pending_scroll_anchor = {
            "scroll_value": current_scroll,
        }
        self.controller.set_enabled(app_id, enabled)

    def _update_more_button_state(self) -> None:
        """根据当前忙碌状态与可见的可切换应用数更新更多按钮及菜单项状态。"""
        if self._is_busy:
            self.more_button.setEnabled(False)
            if hasattr(self, "allow_visible_action"):
                self.allow_visible_action.setEnabled(False)
            if hasattr(self, "revoke_visible_action"):
                self.revoke_visible_action.setEnabled(False)
            return

        toggleable_count = sum(1 for row in self.rows if getattr(row.item, "can_toggle", True))
        has_toggleable = toggleable_count > 0
        self.more_button.setEnabled(has_toggleable)
        if hasattr(self, "allow_visible_action"):
            self.allow_visible_action.setEnabled(has_toggleable)
        if hasattr(self, "revoke_visible_action"):
            self.revoke_visible_action.setEnabled(has_toggleable)

    def set_items(self, items) -> None:
        """增量更新应用列表中的行组件，复用已有行对象并保持视口滚动位置。"""
        anchor = self._pending_scroll_anchor
        self._pending_scroll_anchor = None

        new_items_by_id = {item.app_id: item for item in items}
        visible_ids = set(new_items_by_id.keys())

        # 获取全量已知条目的 ID 集合；只有真正不在全量目录中的条目才进行 deleteLater 清理
        catalog_ids = None
        if hasattr(self.controller, "all_catalog_ids"):
            catalog_ids = set(self.controller.all_catalog_ids)
        elif hasattr(self.controller, "all_app_ids"):
            catalog_ids = set(self.controller.all_app_ids)
        elif hasattr(self.controller, "_items") and self.controller._items:
            catalog_ids = {it.app_id for it in self.controller._items}

        # 原子绘制：锁定容器更新，杜绝中间态闪烁与逐行增删
        self.list_host.setUpdatesEnabled(False)
        try:
            # 1. 彻底从目录消失的条目：从缓存中移除并销毁
            if catalog_ids is not None:
                for app_id in list(self._rows_by_id.keys()):
                    if app_id not in catalog_ids and app_id not in visible_ids:
                        row = self._rows_by_id.pop(app_id)
                        if self.list_layout.indexOf(row) != -1:
                            self.list_layout.removeWidget(row)
                        row.hide()
                        row.deleteLater()

            # 2. 当前在缓存中但当前不在此次可见列表中的行：仅移出布局并隐藏，保留在缓存池中以备复用
            for app_id, row in self._rows_by_id.items():
                if app_id not in visible_ids:
                    if self.list_layout.indexOf(row) != -1:
                        self.list_layout.removeWidget(row)
                    row.hide()

            # 3. 对此次可见的条目：若已在缓存中则复用并增量更新，若不在则惰性创建（显式指定 self.list_host 父对象）
            for app_id, item in new_items_by_id.items():
                if app_id in self._rows_by_id:
                    row = self._rows_by_id[app_id]
                    row.update_item(item)
                else:
                    row = ApplicationRow(item, self.list_host)
                    row.enabled_changed.connect(self._on_row_enabled_changed)
                    row.repair_requested.connect(self._repair_manual)
                    self._rows_by_id[app_id] = row

            # 4. 根据 items 顺序排布可见行
            for i, item in enumerate(items):
                row = self._rows_by_id[item.app_id]
                self.list_layout.insertWidget(i, row)
                row.show()

            self.rows = [self._rows_by_id[item.app_id] for item in items]

            # 5. 若页面处于 busy 状态，同步设置各控件禁用
            if self._is_busy:
                for row in self.rows:
                    if hasattr(row, "enabled_toggle"):
                        row.enabled_toggle.setEnabled(False)
                    if hasattr(row, "repair_button"):
                        row.repair_button.setEnabled(False)

        finally:
            self.list_host.setUpdatesEnabled(True)
            if self.scroll.viewport() is not None:
                self.scroll.viewport().update()

        # 更新更多按钮状态
        self._update_more_button_state()

        if anchor is not None:
            target_scroll = anchor.get("scroll_value", 0)

            def _restore_scroll():
                vbar = self.scroll.verticalScrollBar()
                if vbar:
                    vbar.setValue(min(vbar.maximum(), max(0, target_scroll)))

            _restore_scroll()
            QTimer.singleShot(0, _restore_scroll)

    def set_counts(self, counts: dict) -> None:
        """更新三个分组按钮上的应用数量徽章。"""
        labels = (("已允许", "allowed"), ("未允许", "not_allowed"), ("需要处理", "needs_attention"))
        for button, (label, key) in zip(self.bucket_buttons, labels):
            button.setText(f"{label} {counts.get(key, 0)}")

    def _set_busy(self, busy: bool) -> None:
        """根据后台扫描或批量操作状态更新界面控件可用性与文案。"""
        self._is_busy = busy
        self.refresh_button.setEnabled(not busy)
        self.refresh_button.setText("正在刷新..." if busy else "更新应用列表")
        self.add_button.setEnabled(not busy)
        self._update_more_button_state()
        for row in self._rows_by_id.values():
            if hasattr(row, "enabled_toggle"):
                can_toggle = getattr(row.item, "can_toggle", True) if not busy else False
                row.enabled_toggle.setEnabled(can_toggle)
            if hasattr(row, "repair_button"):
                row.repair_button.setEnabled(not busy)

    def _confirm_batch(self, enabled: bool) -> None:
        """批量授权当前筛选的应用。"""
        title = "允许这些应用？" if enabled else "取消允许这些应用？"
        action = "允许" if enabled else "取消允许"
        toggleable_rows = [row for row in self.rows if getattr(row.item, "can_toggle", True)]
        count = len(toggleable_rows)
        if count == 0:
            QMessageBox.information(self, "提示", "当前筛选条件下没有可操作的应用。")
            return
        reply = QMessageBox.question(
            self,
            title,
            f"将为当前看到的 {count} 个应用统一设置为【{action}】。\n确定要继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.controller.batch_set_visible(enabled)

    def _add_manual(self) -> None:
        """打开文件选择器安全添加便携软件。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择要允许的应用程序",
            "",
            "可执行文件 (*.exe);;所有文件 (*.*)",
        )
        if file_path:
            self.controller.add_manual(file_path)

    def _repair_manual(self, app_id: str) -> None:
        """重新选择可执行文件修复失效的手动便携应用。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "重新选择程序文件",
            "",
            "可执行文件 (*.exe);;所有文件 (*.*)",
        )
        if file_path:
            self.controller.repair_manual(app_id, file_path)

    def visible_copy(self) -> str:
        """获取应用页面当前可见的所有文本以供测试断言。"""
        labels = [lbl.text() for lbl in self.findChildren(QLabel)]
        buttons = [btn.text() for btn in self.findChildren(QPushButton)]
        return " ".join(labels + buttons)
