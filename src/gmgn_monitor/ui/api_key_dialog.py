from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor, QDesktopServices, QLinearGradient, QPainter, QPaintEvent, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


GMGN_API_KEY_URL = "https://gmgn.ai/ai"


class ApiKeyDialog(QDialog):
    def __init__(self, api_key: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GMGN API Key")
        self.setModal(True)
        self.setFixedSize(470, 270)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(
            """
            QDialog {
                background: transparent;
                border-radius: 18px;
            }
            QLabel#title {
                color: #f2fbf7;
                font: 800 20px "Microsoft YaHei UI";
            }
            QLabel#hint {
                color: #9badab;
                font: 600 11px "Microsoft YaHei UI";
                line-height: 150%;
            }
            QLineEdit {
                background: rgba(14, 22, 24, 245);
                color: #eef8f4;
                border: 1px solid rgba(92, 255, 181, 86);
                border-radius: 12px;
                padding: 9px 11px;
                font: 700 12px "Cascadia Mono";
                selection-color: #07100d;
                selection-background-color: #55e99f;
            }
            QLineEdit:focus {
                border: 1px solid rgba(92, 255, 181, 180);
                background: rgba(21, 32, 34, 250);
            }
            QCheckBox {
                color: #9fb2ad;
                font: 700 10px "Microsoft YaHei UI";
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 1px solid rgba(255,255,255,56);
                background: rgba(255,255,255,10);
            }
            QCheckBox::indicator:checked {
                background: #42e395;
                border: 1px solid #42e395;
            }
            QPushButton {
                border-radius: 11px;
                border: 1px solid rgba(255,255,255,28);
                color: #dce9e5;
                background: rgba(255,255,255,13);
                font: 800 12px "Microsoft YaHei UI";
                padding: 8px 13px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,18);
                color: #ffffff;
            }
            QPushButton#primary {
                border: 1px solid rgba(103,255,184,92);
                color: #06120d;
                background: #39d88f;
            }
            QPushButton#primary:hover {
                background: #52eba5;
            }
            QPushButton#link {
                border: 1px solid rgba(80, 178, 255, 80);
                color: #bde4ff;
                background: rgba(32, 113, 183, 36);
            }
            QPushButton#link:hover {
                background: rgba(32, 113, 183, 62);
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 30, 32, 28)
        layout.setSpacing(12)

        title = QLabel("填写 GMGN API Key")
        title.setObjectName("title")
        layout.addWidget(title)

        hint = QLabel("本工具只需要 API Key，不需要公钥、私钥或助记词。没有 Key 可以先打开 GMGN 页面生成。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("粘贴 GMGN OpenAPI Key")
        self.key_edit.setText(api_key)
        self.key_edit.selectAll()
        layout.addWidget(self.key_edit)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.show_key = QCheckBox("显示")
        self.show_key.toggled.connect(self._toggle_visible)
        row.addWidget(self.show_key)
        row.addStretch(1)
        self.open_button = QPushButton("打开 GMGN 申请页")
        self.open_button.setObjectName("link")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.clicked.connect(self.open_gmgn_page)
        row.addWidget(self.open_button)
        layout.addLayout(row)

        guide = QLabel("导航：打开页面后按 GMGN 提示创建 API Key，再复制回这里保存。")
        guide.setObjectName("hint")
        guide.setWordWrap(True)
        layout.addWidget(guide)

        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = QPushButton("保存并启动")
        self.save_button.setObjectName("primary")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.clicked.connect(self._accept_if_valid)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

    @property
    def api_key(self) -> str:
        return self.key_edit.text().strip()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )
        self.key_edit.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def open_gmgn_page(self) -> None:
        QDesktopServices.openUrl(QUrl(GMGN_API_KEY_URL))

    def _toggle_visible(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.key_edit.setEchoMode(mode)

    def _accept_if_valid(self) -> None:
        if not self.api_key:
            self.key_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.accept()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        rect = self.rect().adjusted(8, 8, -8, -8)
        shadow = rect.adjusted(0, 10, 0, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 112))
        painter.drawRoundedRect(shadow, 22, 22)

        path = QPainterPath()
        path.addRoundedRect(rect, 22, 22)
        base = QLinearGradient(rect.topLeft(), rect.bottomRight())
        base.setColorAt(0.0, QColor(18, 27, 27, 250))
        base.setColorAt(0.50, QColor(8, 13, 15, 248))
        base.setColorAt(1.0, QColor(4, 7, 9, 252))
        painter.fillPath(path, base)

        glow = QLinearGradient(rect.topLeft(), rect.topRight())
        glow.setColorAt(0.0, QColor(42, 226, 144, 28))
        glow.setColorAt(0.36, QColor(42, 226, 144, 8))
        glow.setColorAt(1.0, QColor(42, 226, 144, 0))
        painter.fillPath(path, glow)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(132, 151, 148, 82), 1.05))
        painter.drawPath(path)
