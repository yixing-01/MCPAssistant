"""MCP助手新手引导三步进度条控件（StepProgressWidget）。

提供清晰的步骤序号、状态徽标与过渡连接线，对齐已确认的设计方案。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


class StepNode(QWidget):
    """单个步骤节点：包含圆形状态徽章与文字说明。"""

    def __init__(self, step_number: int, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.step_number = step_number
        self.title = title

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.badge = QLabel(str(step_number))
        self.badge.setFixedSize(24, 24)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label = QLabel(title)

        layout.addWidget(self.badge)
        layout.addWidget(self.text_label)

    def set_state(self, state: str) -> None:
        """更新节点呈现状态：done(已完成), active(进行中), upcoming(未进行)。"""
        if state == "done":
            self.badge.setText("✓")
            self.badge.setStyleSheet(
                "border-radius: 12px; background: #E7F6EF; color: #16845B; "
                "font-size: 11px; font-weight: 800; border: 1px solid #BDE5D2;"
            )
            self.text_label.setStyleSheet("color: #3E765F; font-size: 12px; font-weight: 700;")
        elif state == "active":
            self.badge.setText(str(self.step_number))
            self.badge.setStyleSheet(
                "border-radius: 12px; background: #5367E8; color: #FFFFFF; "
                "font-size: 11px; font-weight: 800; border: 2px solid #5367E8;"
            )
            self.text_label.setStyleSheet("color: #4054D9; font-size: 12px; font-weight: 700;")
        else:
            self.badge.setText(str(self.step_number))
            self.badge.setStyleSheet(
                "border-radius: 12px; background: #FFFFFF; color: #8A929F; "
                "font-size: 11px; font-weight: 700; border: 1px solid #CFD3DA;"
            )
            self.text_label.setStyleSheet("color: #9AA2AF; font-size: 12px; font-weight: 500;")


class StepProgressWidget(QWidget):
    """向导进度指示条。"""

    def __init__(self, titles: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.nodes: list[StepNode] = []
        self.lines: list[QFrame] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        for i, title in enumerate(titles):
            node = StepNode(i + 1, title)
            self.nodes.append(node)
            layout.addWidget(node)

            if i < len(titles) - 1:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setFixedHeight(1)
                line.setStyleSheet("background-color: #D9DCE2; border: none;")
                self.lines.append(line)
                layout.addWidget(line, 1)

        self.set_current_step(0)

    def set_current_step(self, step_index: int) -> None:
        """设置当前处于第几步（0 索引）。"""
        for i, node in enumerate(self.nodes):
            if i < step_index:
                node.set_state("done")
            elif i == step_index:
                node.set_state("active")
            else:
                node.set_state("upcoming")
