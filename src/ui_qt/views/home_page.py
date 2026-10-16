"""MCP助手首页视图 (方案 A: 温和智能助手)。

展示亲切的问候语、Hero 渐变状态大卡片、发光助手形象、真实随机已允许快捷应用卡片与底部提示条。
"""

from __future__ import annotations

import datetime
import random
import shiboken6

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui_qt.icon_provider import AppIconProvider
from src.ui_qt.presentation.apps import AppBucket, AppListItem
from src.ui_qt.state import ProductStatus, ProductStatusKind


class AssistantOrbWidget(QWidget):
    """绘制方案 A 设计稿中的温和助手 Orb 形象。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(110, 110)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # 1. 柔和发光背景圆球
        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, QColor("#5E75F0"))
        grad.setColorAt(0.6, QColor("#7A8BF4"))
        grad.setColorAt(1.0, QColor("#49C19A"))

        orb_rect = QRectF(10, 10, w - 20, h - 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawRoundedRect(orb_rect, 32, 32)

        # 2. 绘制灵动表情 (两只眼睛和一个微笑弧线)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        center_x = w / 2.0
        center_y = h / 2.0 - 2.0
        painter.drawEllipse(QPointF(center_x - 14, center_y - 6), 3.5, 3.5)
        painter.drawEllipse(QPointF(center_x + 14, center_y - 6), 3.5, 3.5)

        # 微笑弧线
        pen = QPen(QColor("#FFFFFF"), 3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(center_x - 12, center_y + 4)
        path.quadTo(center_x, center_y + 16, center_x + 12, center_y + 4)
        painter.drawPath(path)

        painter.end()


class AppShortcutCard(QFrame):
    """单个快捷应用卡片控件，展示真实原生图标、应用名称与语音提示。"""

    clicked = Signal()

    def __init__(self, item: AppListItem, parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item
        self.app_id = item.app_id
        self.setObjectName("appShortcutCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(64)

        card_layout = QHBoxLayout(self)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(12)

        # 1. 真实应用图标容器（中性浅灰底，边框，圆角，确保纯白/浅色原生图标清晰可见）
        self.icon_label = QLabel()
        self.icon_label.setObjectName("homeCardIconContainer")
        self.icon_label.setFixedSize(40, 40)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        provider = AppIconProvider.instance()
        dpr = round(float(self.devicePixelRatioF()), 2) if hasattr(self, "devicePixelRatioF") else 1.0
        self._icon_key = (item.icon_ref, item.launch_type, 32, dpr)
        cached = provider.get_cached_pixmap(item.icon_ref, item.launch_type, 32, dpr)
        if cached:
            self.set_icon_pixmap(cached)
        else:
            self.set_icon_pixmap(provider.get_default_fallback_pixmap(32, dpr))
            provider.load_icon_async(
                item.icon_ref,
                item.launch_type,
                32,
                self._on_icon_loaded,
                dpr=dpr,
            )

        # 2. 文本信息（主名称与语音提示）
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        app_name = QLabel(item.display_name)
        app_name.setObjectName("itemTitle")

        if item.voice_example:
            ex = item.voice_example.strip()
            detail_text = ex if ex.startswith(("可说", "可以说", "例如")) else f"可以说：{ex}"
        else:
            detail_text = "已允许小智打开和关闭"
        app_hint = QLabel(detail_text)
        app_hint.setObjectName("subText")

        text_box.addWidget(app_name)
        text_box.addWidget(app_hint)

        card_layout.addWidget(self.icon_label)
        card_layout.addLayout(text_box, 1)

    def set_icon_pixmap(self, pixmap: QPixmap) -> None:
        """设置图标 Pixmap，保持平滑缩放与宽高比。"""
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                32, 32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.icon_label.setPixmap(scaled)

    def _on_icon_loaded(self, pixmap: QPixmap) -> None:
        """异步提取完成后的安全回调。"""
        if not shiboken6.isValid(self):
            return
        if self.app_id != self.item.app_id:
            return
        self.set_icon_pixmap(pixmap)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()


class HomePage(QWidget):
    """MCP助手首页：以普通用户易懂的五种状态展示连接情况及核心动作。"""

    retry_requested = Signal()
    setup_requested = Signal()
    open_apps_requested = Signal()
    troubleshooting_requested = Signal()
    change_connection_code_requested = Signal()

    def __init__(self, random_generator: random.Random | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("pageRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._random = random_generator or random.Random()
        self._current_allowed_apps: list[AppListItem] = []
        self._selected_app_ids: list[str] | None = None
        self._cached_allowed_id_set: set[str] | None = None
        self._app_cards: list[AppShortcutCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(18)

        # 1. 顶部问候栏 (Main-Top)
        top_bar = QWidget()
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(0, 0, 0, 0)

        welcome_box = QVBoxLayout()
        welcome_box.setSpacing(4)
        self.greeting_label = QLabel(self._get_greeting())
        self.greeting_label.setObjectName("pageTitle")
        self.greeting_sub = QLabel("MCP助手会在后台保持连接，不需要手动启动服务。")
        self.greeting_sub.setObjectName("muted")
        welcome_box.addWidget(self.greeting_label)
        welcome_box.addWidget(self.greeting_sub)

        # 右侧本机指示胶囊
        self.avatar_badge = QLabel("本机")
        self.avatar_badge.setFixedSize(48, 28)
        self.avatar_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_badge.setStyleSheet(
            "border-radius: 14px; background-color: #E9ECFF; color: #4155D8; "
            "font-size: 11px; font-weight: 700;"
        )

        top_layout.addLayout(welcome_box, 1)
        top_layout.addWidget(self.avatar_badge)
        layout.addWidget(top_bar)

        # 2. 核心状态大卡片 (Hero Card)
        self.status_card = QFrame()
        self.status_card.setObjectName("heroCard")
        self.status_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hero_layout = QHBoxLayout(self.status_card)
        hero_layout.setContentsMargins(28, 24, 28, 24)
        hero_layout.setSpacing(20)

        left_hero_layout = QVBoxLayout()
        left_hero_layout.setSpacing(10)

        # 状态指示条（发光圆点 + 状态名）
        status_line = QWidget()
        status_line_layout = QHBoxLayout(status_line)
        status_line_layout.setContentsMargins(0, 0, 0, 0)
        status_line_layout.setSpacing(8)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(10, 10)
        self.status_dot.setStyleSheet("border-radius: 5px; background-color: #2DB174;")
        self.status_badge = QLabel("连接正常")
        self.status_badge.setStyleSheet("color: #16845B; font-size: 12px; font-weight: 700;")
        status_line_layout.addWidget(self.status_dot)
        status_line_layout.addWidget(self.status_badge)
        status_line_layout.addStretch(1)
        left_hero_layout.addWidget(status_line)

        # 大状态标题与说明（使用通用占位文案，由 set_product_status 统一更新）
        self.status_title = QLabel("MCP助手已准备好")
        self.status_title.setObjectName("heroTitle")
        self.status_detail = QLabel("MCP助手会在后台保持就绪，支持通过 MCP 直接控制已允许的应用。")
        self.status_detail.setObjectName("muted")
        self.status_detail.setWordWrap(True)
        left_hero_layout.addWidget(self.status_title)
        left_hero_layout.addWidget(self.status_detail)

        # 倒计时重试标签
        self.retry_label = QLabel("")
        self.retry_label.setObjectName("subText")
        left_hero_layout.addWidget(self.retry_label)

        # 操作按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self.primary_button = QPushButton("管理我的应用")
        self.primary_button.setObjectName("primary")
        self.primary_button.clicked.connect(self._emit_primary_action)

        self.change_code_button = QPushButton("更换连接码")
        self.change_code_button.setObjectName("secondary")
        self.change_code_button.clicked.connect(self.change_connection_code_requested)
        self.change_code_button.hide()

        self.help_button = QPushButton("查看使用帮助")
        self.help_button.setObjectName("secondary")
        self.help_button.clicked.connect(self.troubleshooting_requested)

        btn_row.addWidget(self.primary_button)
        btn_row.addWidget(self.change_code_button)
        btn_row.addWidget(self.help_button)
        btn_row.addStretch(1)
        left_hero_layout.addLayout(btn_row)

        hero_layout.addLayout(left_hero_layout, 1)

        # 右侧助手 Orb 形象
        self.orb_widget = AssistantOrbWidget()
        hero_layout.addWidget(self.orb_widget, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_card)

        # 3. 快捷应用区域
        section_row = QHBoxLayout()
        apps_section_title = QLabel("小智可以打开和关闭的应用")
        apps_section_title.setObjectName("sectionTitle")
        self.apps_count_hint = QLabel("点击卡片可快速管理")
        self.apps_count_hint.setObjectName("subText")
        section_row.addWidget(apps_section_title)
        section_row.addStretch(1)
        section_row.addWidget(self.apps_count_hint)
        layout.addLayout(section_row)

        self.shortcuts_container = QWidget()
        self.shortcuts_layout = QHBoxLayout(self.shortcuts_container)
        self.shortcuts_layout.setContentsMargins(0, 0, 0, 0)
        self.shortcuts_layout.setSpacing(12)
        layout.addWidget(self.shortcuts_container)

        # 初始呈现空状态
        self._render_empty_state()

        # 4. 底部提示条 (Bottom Strip)
        bottom_strip = QHBoxLayout()
        bottom_strip.setSpacing(12)

        tip1 = self._create_tip_card(
            "想添加其他软件？",
            "从电脑中选择一次即可",
            "添加应用 →",
            self.open_apps_requested,
        )
        tip2 = self._create_tip_card(
            "后台连接稳定",
            "MCP助手正在安全守护你的应用",
            "一切正常",
            None,
        )
        bottom_strip.addWidget(tip1, 1)
        bottom_strip.addWidget(tip2, 1)
        layout.addLayout(bottom_strip)

        layout.addStretch(1)

        # 默认初始状态
        self._status = ProductStatus(
            ProductStatusKind.CONNECTING,
            "正在连接 MCP 服务",
            "通常只需要几秒钟。",
        )
        self.set_product_status(self._status)

    def set_random_generator(self, rng: random.Random) -> None:
        """注入随机生成器（供测试或确定性环境使用）。"""
        self._random = rng

    def set_allowed_apps(self, apps: list[AppListItem]) -> None:
        """接收已允许应用列表并更新首页快捷卡片展示。

        规则：
        1. 仅候选当前有效且已允许的应用 (AppBucket.ALLOWED)；
        2. 按 app_id 去重；
        3. 仅在首次加载或候选集合发生实质变化时重新随机抽样；
        4. 同一会话内保持卡片稳定，切页/重绘不重新随机；
        5. 候选为 0 时呈现新手空状态，候选为 1~3 时仅显示实际数量。
        """
        seen_ids = set()
        unique_allowed: list[AppListItem] = []
        for app in apps:
            if getattr(app, "bucket", None) is AppBucket.ALLOWED and app.app_id not in seen_ids:
                seen_ids.add(app.app_id)
                unique_allowed.append(app)

        current_id_set = {app.app_id for app in unique_allowed}

        # 判断是否需要重新抽样
        if self._cached_allowed_id_set is None or current_id_set != self._cached_allowed_id_set:
            self._cached_allowed_id_set = current_id_set
            if len(unique_allowed) <= 3:
                self._selected_app_ids = [a.app_id for a in unique_allowed]
            else:
                chosen = self._random.sample(unique_allowed, 3)
                self._selected_app_ids = [a.app_id for a in chosen]

        # 根据选中的 ID 构建列表（保持抽样顺序）
        app_by_id = {a.app_id: a for a in unique_allowed}
        selected_apps = [app_by_id[aid] for aid in (self._selected_app_ids or []) if aid in app_by_id]
        self._current_allowed_apps = selected_apps

        # 渲染卡片
        self._render_shortcuts(selected_apps)

    def _render_shortcuts(self, selected_apps: list[AppListItem]) -> None:
        """根据当前选中的已允许应用列表渲染快捷卡片区域。"""
        while self.shortcuts_layout.count() > 0:
            child = self.shortcuts_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        self._app_cards.clear()

        if not selected_apps:
            self._render_empty_state()
            return

        for app in selected_apps:
            card = AppShortcutCard(app)
            card.clicked.connect(self.open_apps_requested)
            self._app_cards.append(card)
            if len(selected_apps) < 3:
                card.setFixedWidth(260)
                self.shortcuts_layout.addWidget(card)
            else:
                self.shortcuts_layout.addWidget(card, 1)

        if len(selected_apps) < 3:
            self.shortcuts_layout.addStretch(1)

    def _render_empty_state(self) -> None:
        """渲染无已允许应用时的完整新手空状态卡片。"""
        empty_card = QFrame()
        empty_card.setObjectName("homeEmptyCard")
        empty_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        empty_layout = QHBoxLayout(empty_card)
        empty_layout.setContentsMargins(20, 16, 20, 16)
        empty_layout.setSpacing(16)

        text_box = QVBoxLayout()
        text_box.setSpacing(4)
        title_lbl = QLabel("还没有允许小智打开和关闭的应用")
        title_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #20293A;")
        desc_lbl = QLabel("添加并允许应用后，就可以通过小智帮你打开和关闭。")
        desc_lbl.setObjectName("subText")
        text_box.addWidget(title_lbl)
        text_box.addWidget(desc_lbl)

        btn = QPushButton("去我的应用设置")
        btn.setObjectName("secondary")
        btn.setFixedHeight(34)
        btn.clicked.connect(self.open_apps_requested)

        empty_layout.addLayout(text_box, 1)
        empty_layout.addWidget(btn)

        self.shortcuts_layout.addWidget(empty_card, 1)

    def _get_greeting(self) -> str:
        """根据当前时间返回温馨中文问候语。"""
        hour = datetime.datetime.now().hour
        if 5 <= hour < 11:
            return "早上好，欢迎回来"
        elif 11 <= hour < 14:
            return "上午好，欢迎回来"
        elif 14 <= hour < 18:
            return "下午好，欢迎回来"
        else:
            return "晚上好，欢迎回来"

    def _create_tip_card(
        self, title: str, desc: str, action_text: str, signal: Signal | None
    ) -> QFrame:
        """创建底部小提示卡片。"""
        card = QFrame()
        card.setObjectName("tipCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #20293A;")
        desc_lbl = QLabel(desc)
        desc_lbl.setObjectName("subText")
        text_box.addWidget(title_lbl)
        text_box.addWidget(desc_lbl)

        action_lbl = QLabel(action_text)
        action_lbl.setStyleSheet("color: #4054D9; font-size: 11px; font-weight: 700;")

        layout.addLayout(text_box, 1)
        layout.addWidget(action_lbl)

        if signal is not None:
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.mousePressEvent = lambda e: signal.emit()

        return card

    def set_product_status(self, status: ProductStatus) -> None:
        """更新首页呈现的产品状态及主操作按钮。"""
        self._status = status
        badges = {
            ProductStatusKind.READY: ("连接正常", "#2DB174", "#16845B"),
            ProductStatusKind.CONNECTING: ("正在准备", "#5367E8", "#4054D9"),
            ProductStatusKind.RECOVERING: ("正在恢复", "#E5A024", "#A8650A"),
            ProductStatusKind.ACTION_REQUIRED: ("需要处理", "#D9383A", "#B34F4F"),
            ProductStatusKind.SETUP_REQUIRED: ("尚未连接", "#8A929F", "#667085"),
        }
        badge_text, dot_color, text_color = badges.get(
            status.kind, ("正在确认", "#8A929F", "#667085")
        )

        self.status_dot.setStyleSheet(f"border-radius: 5px; background-color: {dot_color};")
        self.status_badge.setText(badge_text)
        self.status_badge.setStyleSheet(f"color: {text_color}; font-size: 12px; font-weight: 700;")
        self.status_title.setText(status.title)
        self.status_detail.setText(status.detail)
        self.primary_button.setText(status.primary_action or "管理我的应用")
        self.primary_button.setVisible(bool(status.primary_action))
        self.retry_label.setText(
            f"将在 {status.retry_seconds} 秒后自动重试"
            if status.retry_seconds is not None
            else ""
        )

        # 根据五种状态控制按钮显隐
        if status.kind == ProductStatusKind.RECOVERING:
            self.change_code_button.setVisible(True)
            self.help_button.setVisible(False)
        elif status.kind == ProductStatusKind.ACTION_REQUIRED:
            self.change_code_button.setVisible(True)
            self.help_button.setVisible(False)
        elif status.kind == ProductStatusKind.SETUP_REQUIRED:
            self.change_code_button.setVisible(False)
            self.help_button.setVisible(False)
        elif status.kind == ProductStatusKind.READY:
            self.change_code_button.setVisible(False)
            self.help_button.setVisible(True)
        else:  # CONNECTING
            self.change_code_button.setVisible(False)
            self.help_button.setVisible(True)

    def _emit_primary_action(self) -> None:
        """根据当前产品状态分发点击主操作信号。"""
        actions = {
            ProductStatusKind.READY: self.open_apps_requested,
            ProductStatusKind.RECOVERING: self.retry_requested,
            ProductStatusKind.ACTION_REQUIRED: self.troubleshooting_requested,
            ProductStatusKind.SETUP_REQUIRED: self.setup_requested,
        }
        signal = actions.get(self._status.kind, self.open_apps_requested)
        if signal is not None:
            signal.emit()
