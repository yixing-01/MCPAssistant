"""MCP助手平滑滑动开关控件（ToggleSwitch）。

绘制带有圆形白色滑块（Thumb）和圆角轨道（Track）的滑动开关，
提供比原生 QCheckBox 更明确、更具现代感的开关状态表达。
"""

from __future__ import annotations

from PySide6.QtCore import Property, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QWidget


class ToggleSwitch(QAbstractButton):
    """带圆形滑块的现代化滑动开关。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 24)

        # 颜色令牌
        self._track_checked_color = QColor("#5367E8")
        self._track_unchecked_color = QColor("#D0D5DD")
        self._thumb_color = QColor("#FFFFFF")
        self._shadow_color = QColor(0, 0, 0, 45)
        self.toggled.connect(lambda _: self.update())

    def sizeHint(self) -> QSize:
        return QSize(44, 24)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        radius = h / 2.0
        thumb_diameter = h - 6.0  # 上下各留 3px 边距

        # 1. 绘制轨道 (Track)
        track_rect = QRectF(0, 0, w, h)
        track_color = (
            self._track_checked_color if self.isChecked() else self._track_unchecked_color
        )
        if not self.isEnabled():
            track_color = QColor("#E4E7EC")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(track_color))
        painter.drawRoundedRect(track_rect, radius, radius)

        # 2. 计算滑块位置
        if self.isChecked():
            thumb_x = w - 3.0 - thumb_diameter
        else:
            thumb_x = 3.0
        thumb_y = 3.0
        thumb_rect = QRectF(thumb_x, thumb_y, thumb_diameter, thumb_diameter)

        # 3. 绘制滑块轻微阴影
        shadow_rect = QRectF(thumb_x, thumb_y + 1.0, thumb_diameter, thumb_diameter)
        painter.setBrush(QBrush(self._shadow_color))
        painter.drawEllipse(shadow_rect)

        # 4. 绘制纯白滑块 (Thumb)
        painter.setBrush(QBrush(self._thumb_color))
        painter.drawEllipse(thumb_rect)

        painter.end()
