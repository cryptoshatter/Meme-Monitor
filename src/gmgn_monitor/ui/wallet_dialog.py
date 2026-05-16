from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QMouseEvent, QPainter, QPaintEvent, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QStyledItemDelegate, QStyle

from gmgn_monitor.ui.images import avatar_pixmap

from gmgn_monitor.ui.emoji_picker import EmojiPicker, first_emoji

DEFAULT_EMOJI = "\U0001f9e9"


class WalletItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        return QSize(196, 56)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        wallet = index.data(Qt.ItemDataRole.UserRole) or {}
        rect = option.rect.adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(53, 218, 143, 48) if selected else QColor(255, 255, 255, 0))
        painter.drawRoundedRect(rect, 8, 8)

        avatar_rect = QRect(rect.left() + 8, rect.top() + 7, 38, 38)
        painter.drawPixmap(avatar_rect, avatar_pixmap(wallet.get("avatar_kind", "emoji"), wallet.get("avatar_value", DEFAULT_EMOJI), 38))

        text_left = avatar_rect.right() + 10
        text_width = max(20, rect.right() - text_left - 6)
        remark = wallet.get("remark") or "Wallet"
        chain = str(wallet.get("chain") or "").upper()
        address = wallet.get("address") or ""
        sub = f"{chain} {short_address(address)}".strip()

        remark_font = QFont("Microsoft YaHei UI", 9, QFont.Weight.Black)
        sub_font = QFont("Cascadia Mono", 7, QFont.Weight.Bold)
        painter.setFont(remark_font)
        painter.setPen(QColor(245, 255, 250))
        painter.drawText(
            QRect(text_left, rect.top() + 8, text_width, 17),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(remark_font).elidedText(remark, Qt.TextElideMode.ElideRight, text_width),
        )
        painter.setFont(sub_font)
        painter.setPen(QColor(194, 213, 205))
        painter.drawText(
            QRect(text_left, rect.top() + 28, text_width, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(sub_font).elidedText(sub, Qt.TextElideMode.ElideRight, text_width),
        )
        painter.restore()


class WalletDialog(QDialog):
    def __init__(self, wallets: list[dict[str, Any]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Wallet Monitor")
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setFixedSize(600, 396)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._wallets = [dict(wallet) for wallet in wallets]
        self._selected_index = -1
        self._drag_origin = None
        self._loading_wallet = False

        self.list_widget = QListWidget(self)
        self.list_widget.setGeometry(28, 78, 214, 226)
        self.list_widget.setItemDelegate(WalletItemDelegate(self.list_widget))
        self.list_widget.currentRowChanged.connect(self._select_wallet)

        self.add_wallet_button = QPushButton("+", self)
        self.add_wallet_button.setGeometry(28, 316, 100, 30)
        self.add_wallet_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_wallet_button.clicked.connect(self._new_wallet)

        self.remove_wallet_button = QPushButton("-", self)
        self.remove_wallet_button.setGeometry(142, 316, 100, 30)
        self.remove_wallet_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_wallet_button.clicked.connect(self._delete_wallet)

        self.remark_edit = QLineEdit(self)
        self.remark_edit.setGeometry(266, 78, 306, 35)
        self.remark_edit.setPlaceholderText("Wallet remark")
        self.remark_edit.textEdited.connect(self._sync_current_wallet_preview)

        self.address_edit = QLineEdit(self)
        self.address_edit.setGeometry(266, 123, 306, 38)
        self.address_edit.setPlaceholderText("Wallet address")
        self.address_edit.setCursorPosition(0)
        self.address_edit.textEdited.connect(self._sync_current_wallet_preview)

        self._avatar_kind = "emoji"
        self._avatar_value = DEFAULT_EMOJI

        self.avatar_button = QPushButton("钱包图标", self)
        self.avatar_button.setText("钱包图标")
        self.avatar_button.setGeometry(266, 174, 306, 34)
        self.avatar_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.avatar_button.clicked.connect(self._open_avatar_picker)

        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setGeometry(402, 342, 80, 31)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)

        self.ok_button = QPushButton("OK", self)
        self.ok_button.setGeometry(492, 342, 80, 31)
        self.ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_button.clicked.connect(self._accept_with_save)

        self._apply_styles()
        self._refresh_list()

    @property
    def wallets(self) -> list[dict[str, Any]]:
        self._sync_current_wallet_preview()
        return [dict(wallet) for wallet in self._wallets]

    def _accept_with_save(self) -> None:
        self._sync_current_wallet_preview()
        self.accept()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + (geo.height() - self.height()) // 2)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        rect = self.rect().adjusted(7, 7, -7, -7)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 95))
        painter.drawRoundedRect(rect.adjusted(0, 9, 0, 9), 20, 20)

        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 20, 20)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(19, 29, 29, 250))
        grad.setColorAt(0.55, QColor(8, 13, 15, 248))
        grad.setColorAt(1.0, QColor(4, 7, 9, 252))
        painter.fillPath(path, grad)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 38), 1))
        painter.drawPath(path)

        painter.setPen(QColor(239, 249, 245))
        painter.setFont(QFont("Segoe UI Variable Display", 17, QFont.Weight.Black))
        painter.drawText(28, 30, 250, 24, Qt.AlignmentFlag.AlignLeft, "Wallet Monitor")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for wallet in self._wallets:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, dict(wallet))
            self.list_widget.addItem(item)
        if self._wallets and self.list_widget.currentRow() < 0:
            self.list_widget.setCurrentRow(0)

    def _select_wallet(self, row: int) -> None:
        self._loading_wallet = True
        self._selected_index = row
        if row < 0 or row >= len(self._wallets):
            self.remark_edit.clear()
            self.address_edit.clear()
            self._set_avatar("emoji", DEFAULT_EMOJI)
            self._loading_wallet = False
            return
        wallet = self._wallets[row]
        self.remark_edit.setText(wallet.get("remark", ""))
        self.address_edit.setText(wallet.get("address", ""))
        self.address_edit.setCursorPosition(0)
        kind = wallet.get("avatar_kind", "emoji")
        value = wallet.get("avatar_value") or DEFAULT_EMOJI
        self._set_avatar("image" if kind == "image" else "emoji", value)
        self._loading_wallet = False

    def _new_wallet(self) -> None:
        wallet = {
            "remark": "Wallet",
            "address": "",
            "chain": "",
            "chains": [],
            "avatar_kind": "emoji",
            "avatar_value": DEFAULT_EMOJI,
        }
        self._wallets.append(wallet)
        self._refresh_list()
        self.list_widget.setCurrentRow(len(self._wallets) - 1)
        self.remark_edit.selectAll()

    def _delete_wallet(self) -> None:
        row = self._selected_index
        if row < 0 or row >= len(self._wallets):
            return
        del self._wallets[row]
        self._selected_index = -1
        self._refresh_list()
        if self._wallets:
            self.list_widget.setCurrentRow(min(row, len(self._wallets) - 1))
        else:
            self._select_wallet(-1)

    def _open_avatar_picker(self) -> None:
        dialog = EmojiPicker(self._avatar_value, self._avatar_kind, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._set_avatar(dialog.selected_kind, dialog.selected_image if dialog.selected_kind == "image" else dialog.selected_emoji)
        self._sync_current_wallet_preview()

    def _sync_current_wallet_preview(self) -> None:
        if self._loading_wallet:
            return
        row = self._selected_index
        if row < 0 or row >= len(self._wallets):
            return
        wallet = self._wallets[row]
        old_address = str(wallet.get("address") or "").strip()
        new_address = self.address_edit.text().strip()
        wallet["remark"] = self.remark_edit.text().strip() or "Wallet"
        wallet["address"] = new_address
        if new_address != old_address:
            wallet["chain"] = ""
            wallet["chains"] = []
        wallet["avatar_kind"] = self._avatar_kind
        wallet["avatar_value"] = self._current_avatar_value()
        item = self.list_widget.item(row)
        if item is not None:
            item.setData(Qt.ItemDataRole.UserRole, dict(wallet))

    def _current_avatar_value(self) -> str:
        if self._avatar_kind == "image":
            return self._avatar_value
        return first_emoji(self._avatar_value) or DEFAULT_EMOJI

    def _set_avatar(self, kind: str, value: str) -> None:
        self._avatar_kind = "image" if kind == "image" else "emoji"
        if self._avatar_kind == "image":
            self._avatar_value = value.strip()
            self.avatar_button.setText("钱包图标")
        else:
            self._avatar_value = first_emoji(value) or DEFAULT_EMOJI
            self.avatar_button.setText("钱包图标")

    def _apply_styles(self) -> None:
        field_css = """
            QLineEdit {
                background: rgba(11, 17, 19, 238);
                color: #eef8f4;
                border: 1px solid rgba(255,255,255,34);
                border-radius: 10px;
                padding: 7px 10px;
                selection-color: #f4fff9;
                selection-background-color: #1f7f59;
                font: 700 12px "Segoe UI Variable Text";
            }
            QLineEdit:focus {
                border: 1px solid rgba(76,238,157,178);
                background: rgba(17, 25, 27, 244);
            }
        """
        self.remark_edit.setStyleSheet(field_css)
        self.address_edit.setStyleSheet(field_css + 'QLineEdit { font: 600 9px "Cascadia Mono"; }')
        self.avatar_button.setStyleSheet(
            """
            QPushButton {
                background: rgba(11, 17, 19, 238);
                color: #eef8f4;
                border: 1px solid rgba(255,255,255,34);
                border-radius: 10px;
                padding: 0px;
                font: 900 13px "Microsoft YaHei UI";
            }
            QPushButton:hover, QPushButton:focus {
                border: 1px solid rgba(76,238,157,178);
                background: rgba(17, 25, 27, 244);
            }
            """
        )
        self.list_widget.setStyleSheet(
            """
            QListWidget {
                background: rgba(8, 12, 14, 222);
                color: #edf7f3;
                border: 1px solid rgba(255,255,255,30);
                border-radius: 12px;
                padding: 6px;
                font: 700 11px "Segoe UI Variable Text";
            }
            QListWidget::item {
                min-height: 42px;
                padding: 7px 8px;
                border-radius: 8px;
                color: #d8e7e2;
            }
            QListWidget::item:selected {
                background: rgba(53, 218, 143, 42);
                color: #ffffff;
            }
            """
        )
        neutral_button = """
            QPushButton {
                border-radius: 10px;
                border: 1px solid rgba(255,255,255,28);
                color: #dce9e5;
                background: rgba(255,255,255,10);
                font: 800 12px "Segoe UI Variable Text";
            }
            QPushButton:hover { background: rgba(255,255,255,18); color: #ffffff; }
            QPushButton:disabled { color: #65716f; background: rgba(255,255,255,5); }
        """
        accent_button = """
            QPushButton {
                border-radius: 10px;
                border: 1px solid rgba(103,255,184,94);
                color: #06120d;
                background: #39d88f;
                font: 900 12px "Segoe UI Variable Text";
            }
            QPushButton:hover { background: #52eba5; }
        """
        self.add_wallet_button.setStyleSheet(neutral_button)
        self.remove_wallet_button.setStyleSheet(neutral_button)
        self.cancel_button.setStyleSheet(neutral_button)
        self.ok_button.setStyleSheet(accent_button)


def short_address(address: str) -> str:
    if len(address) <= 12:
        return address
    return f"{address[:6]}...{address[-4:]}"
