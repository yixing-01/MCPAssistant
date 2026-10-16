from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlaceholderPage(QWidget):
    """阶段一临时占位页面，展示标题与简要说明。"""

    def __init__(self, title: str, description: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        body = QLabel(description)
        body.setObjectName("muted")
        body.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(body)
        layout.addStretch(1)
