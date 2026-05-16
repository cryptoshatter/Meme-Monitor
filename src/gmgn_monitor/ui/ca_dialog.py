from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QMouseEvent, QPainter, QPaintEvent, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton


class CaDialog(QDialog):
    def __init__(self, address: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CA")
        self.setModal(True)
        self.setFixedSize(430, 170)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.address_edit = QLineEdit(self)
        self.address_edit.setGeometry(28, 76, 374, 38)
        self.address_edit.setText(address)
        self.address_edit.setPlaceholderText("Paste token CA")
        self.address_edit.selectAll()
        self.address_edit.setStyleSheet(
            """
            QLineEdit {
                background: rgba(18, 25, 28, 238);
                color: #eef8f4;
                border: 1px solid rgba(255,255,255,42);
                border-radius: 12px;
                padding: 8px 11px;
                font: 600 12px "Consolas";
                selection-color: #f4fff9;
                selection-background-color: #1f7f59;
            }
            QLineEdit:focus {
                border: 1px solid rgba(86,244,166,168);
                background: rgba(22, 31, 34, 242);
            }
            """
        )

        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setGeometry(238, 124, 78, 32)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)

        self.ok_button = QPushButton("OK", self)
        self.ok_button.setGeometry(324, 124, 78, 32)
        self.ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_button.clicked.connect(self.accept)

        button_css = """
            QPushButton {
                border-radius: 11px;
                border: 1px solid rgba(255,255,255,28);
                color: #dce9e5;
                background: rgba(255,255,255,11);
                font: 700 12px "Segoe UI";
            }
            QPushButton:hover {
                background: rgba(255,255,255,18);
                color: #ffffff;
            }
        """
        self.cancel_button.setStyleSheet(button_css)
        self.ok_button.setStyleSheet(
            """
            QPushButton {
                border-radius: 11px;
                border: 1px solid rgba(103,255,184,92);
                color: #06120d;
                background: #39d88f;
                font: 800 12px "Segoe UI";
            }
            QPushButton:hover {
                background: #52eba5;
            }
            """
        )

        self._drag_origin = None

    @property
    def address(self) -> str:
        return self.address_edit.text().strip()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        rect = self.rect().adjusted(6, 6, -6, -6)
        shadow = QRect(rect).adjusted(0, 8, 0, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 96))
        painter.drawRoundedRect(shadow, 20, 20)

        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 20, 20)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(18, 25, 27, 248))
        grad.setColorAt(1.0, QColor(7, 10, 12, 250))
        painter.fillPath(path, grad)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 42), 1))
        painter.drawPath(path)

        painter.setPen(QColor(242, 250, 246))
        title_font = QFont("Segoe UI Variable Display", 18, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.drawText(28, 30, 180, 24, Qt.AlignmentFlag.AlignLeft, "CA")

        painter.setPen(QColor(128, 143, 141))
        sub_font = QFont("Segoe UI", 10, QFont.Weight.Medium)
        painter.setFont(sub_font)
        painter.drawText(28, 54, 370, 16, Qt.AlignmentFlag.AlignLeft, "Paste contract address. Chain is detected automatically.")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None
