"""MCP助手单行应用展示行控件（ApplicationRow）。

展示应用图标、应用名称、别名语音提示、现代化 ToggleSwitch 授权开关及失效修复入口。
支持安全的增量数据更新 update_item，避免整行销毁重建。
"""

from __future__ import annotations

import weakref
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from src.ui_qt.icon_provider import AppIconProvider
from src.ui_qt.presentation.apps import AppBucket, AppListItem
from src.ui_qt.widgets.switch import ToggleSwitch


class ApplicationRow(QFrame):
    """单个软件行控件，至少 64 逻辑像素高，包含图标、应用名、语音示例、授权开关与修复按钮。"""

    enabled_changed = Signal(str, bool)
    repair_requested = Signal(str)

    def __init__(self, item: AppListItem, parent=None):
        super().__init__(parent)
        self.item = item
        self._app_id = item.app_id
        self.setObjectName("applicationRow")
        self.setMinimumHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(14)

        # 1. 软件图标展示 (42x42 容器，中性底色承托，清晰显示浅色/纯白原生图标)
        self.icon_label = QLabel()
        self.icon_label.setObjectName("appIcon")
        self.icon_label.setFixedSize(42, 42)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        provider = AppIconProvider.instance()
        dpr = round(float(self.devicePixelRatioF()), 2) if hasattr(self, "devicePixelRatioF") else 1.0
        self._icon_key = (item.icon_ref, item.launch_type, 36, dpr)
        cached = provider.get_cached_pixmap(item.icon_ref, item.launch_type, 36, dpr)
        if cached:
            self.set_icon_pixmap(cached)
        else:
            self.set_icon_pixmap(provider.get_default_fallback_pixmap(36, dpr))
            self._request_icon(item.icon_ref, item.launch_type, 36, dpr)

        # 2. 文本信息（主名称与语音提示）
        text = QVBoxLayout()
        text.setSpacing(3)
        self.name_label = QLabel(item.display_name)
        self.name_label.setObjectName("itemTitle")

        self.detail_label = QLabel(self._format_detail_text(item))
        self.detail_label.setObjectName("muted")
        text.addWidget(self.name_label)
        text.addWidget(self.detail_label)

        layout.addWidget(self.icon_label)
        layout.addLayout(text, 1)

        # 3. 失效修复按钮 (仅在需要处理时出现)
        btn_text = item.repair_action_label if getattr(item, "repair_action_label", None) else "重新选择"
        self.repair_button = QPushButton(btn_text)
        self.repair_button.setObjectName("secondary")
        self.repair_button.setFixedHeight(32)
        self.repair_button.setVisible(item.bucket is AppBucket.NEEDS_ATTENTION)
        self.repair_button.clicked.connect(lambda: self.repair_requested.emit(self._app_id))
        layout.addWidget(self.repair_button)

        # 4. 现代化 ToggleSwitch 授权开关
        self.enabled_toggle = ToggleSwitch()
        can_toggle = getattr(item, "can_toggle", True)
        self.enabled_toggle.setEnabled(can_toggle)
        self.enabled_toggle.setChecked(item.is_enabled)
        self.enabled_toggle.setAccessibleName(f"允许小智打开和关闭{item.display_name}")
        self.enabled_toggle.toggled.connect(self._on_toggle_toggled)
        layout.addWidget(self.enabled_toggle)

    @staticmethod
    def _format_detail_text(item: AppListItem) -> str:
        """根据条目属性格式化描述信息文本。"""
        if item.repair_message:
            return item.repair_message
        if item.voice_example:
            ex = item.voice_example.strip()
            return ex if ex.startswith(("可说", "可以说", "例如")) else f"可说：{ex}"
        return "已允许小智打开和关闭"

    def _on_toggle_toggled(self, checked: bool) -> None:
        """响应用户点击授权开关，待处理或已禁用的开关不会发射变更信号。"""
        can_toggle = getattr(self.item, "can_toggle", True)
        if not can_toggle:
            return
        self.enabled_changed.emit(self.item.app_id, checked)

    def update_item(self, item: AppListItem) -> None:
        """增量更新行控件展示数据，避免整行销毁重建。"""
        self.item = item
        self._app_id = item.app_id

        # 1. 更新名称与描述
        self.name_label.setText(item.display_name)
        self.detail_label.setText(self._format_detail_text(item))

        # 2. 更新修复按钮
        btn_text = item.repair_action_label if getattr(item, "repair_action_label", None) else "重新选择"
        self.repair_button.setText(btn_text)
        self.repair_button.setVisible(item.bucket is AppBucket.NEEDS_ATTENTION)

        # 3. 更新授权开关：阻止信号发射避免误触发业务事件
        can_toggle = getattr(item, "can_toggle", True)
        self.enabled_toggle.setEnabled(can_toggle)
        self.enabled_toggle.setAccessibleName(f"允许小智打开和关闭{item.display_name}")
        with QSignalBlocker(self.enabled_toggle):
            self.enabled_toggle.setChecked(item.is_enabled)

        # 4. 图标检查：仅在 key 变化时才重新提取
        dpr = round(float(self.devicePixelRatioF()), 2) if hasattr(self, "devicePixelRatioF") else 1.0
        new_icon_key = (item.icon_ref, item.launch_type, 36, dpr)
        if new_icon_key != self._icon_key:
            self._icon_key = new_icon_key
            provider = AppIconProvider.instance()
            cached = provider.get_cached_pixmap(item.icon_ref, item.launch_type, 36, dpr)
            if cached:
                self.set_icon_pixmap(cached)
            else:
                self.set_icon_pixmap(provider.get_default_fallback_pixmap(36, dpr))
                self._request_icon(item.icon_ref, item.launch_type, 36, dpr)

    def _request_icon(self, icon_ref: str, launch_type: str, size: int, dpr: float) -> None:
        """使用弱引用闭包安全发起异步图标加载，防止持久强引用已销毁的行对象。"""
        provider = AppIconProvider.instance()
        row_ref = weakref.ref(self)
        expected_id = self._app_id
        expected_key = self._icon_key

        def _cb(pixmap: QPixmap) -> None:
            row = row_ref()
            if row is None:
                return
            try:
                import shiboken6
                if not shiboken6.isValid(row):
                    return
            except Exception:
                pass
            try:
                if row._app_id == expected_id and row._icon_key == expected_key:
                    row.set_icon_pixmap(pixmap)
            except RuntimeError:
                pass

        provider.load_icon_async(icon_ref, launch_type, size, _cb, dpr=dpr)

    def set_icon_pixmap(self, pixmap: QPixmap) -> None:
        """设置图标 Pixmap，保持平滑缩放与宽高比。"""
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                36, 36,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.icon_label.setPixmap(scaled)

    def _on_icon_loaded(self, pixmap: QPixmap) -> None:
        """向后兼容原有回调入口，具备 C++ 对象存活性与应用行防错位保护。"""
        try:
            import shiboken6
            if not shiboken6.isValid(self):
                return
        except Exception:
            pass

        try:
            if self._app_id == self.item.app_id:
                self.set_icon_pixmap(pixmap)
        except RuntimeError:
            pass

