from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QMouseEvent, QPainter, QPaintEvent, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QListWidgetItem, QStyledItemDelegate, QStyle

from gmgn_monitor.event_store import load_events
from gmgn_monitor.ui.images import LogoLoader, native_icon, token_fallback_logo
from gmgn_monitor.ui.theme import active_theme, get_theme
from gmgn_monitor.ui.token_dialog import EmbossCloseButton


class TimelineDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        return QSize(504, 54)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        event = index.data(Qt.ItemDataRole.UserRole) or {}
        theme = active_theme()
        rect = option.rect.adjusted(6, 4, -6, -4)
        selected = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        bg = theme.color("surface")
        bg.setAlpha(48 if selected else 26)
        painter.setPen(QPen(theme.color("border_hover" if selected else "border", 74 if selected else 38), 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 12, 12)

        chain = str(event.get("chain") or "").lower()
        side = str(event.get("side") or "").lower()
        kind = str(event.get("kind") or "")
        color = theme.color("positive") if side in {"buy", "up"} else theme.color("negative") if side in {"sell", "down"} else theme.color("accent")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRect(rect.left() + 10, rect.top() + 11, 4, rect.height() - 22), 2, 2)

        icon_rect = QRect(rect.left() + 22, rect.top() + 13, 24, 24)
        logo = event.get("_logo_pixmap")
        if hasattr(logo, "isNull") and not logo.isNull():
            painter.drawPixmap(icon_rect, logo)
        else:
            painter.drawPixmap(icon_rect, token_fallback_logo(str(event.get("title") or kind), chain, 24))
        if chain:
            painter.drawPixmap(QRect(icon_rect.right() - 8, icon_rect.bottom() - 8, 12, 12), native_icon(chain, 12))

        text_left = icon_rect.right() + 12
        time_text = relative_time(event.get("timestamp") or event.get("received_at"))
        time_w = QFontMetrics(QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold)).horizontalAdvance(time_text) + 8 if time_text else 0
        value = str(event.get("value") or "")
        value_w = QFontMetrics(QFont("Cascadia Mono", 8, QFont.Weight.Bold)).horizontalAdvance(value) + 8 if value else 0
        right_reserved = time_w + value_w + 8

        title = str(event.get("title") or "事件")
        subtitle = str(event.get("subtitle") or "--")
        title_font = QFont("Microsoft YaHei UI", 9, QFont.Weight.Black)
        sub_font = QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold)
        title_rect = QRect(text_left, rect.top() + 9, max(24, rect.right() - text_left - right_reserved), 17)
        painter.setFont(title_font)
        painter.setPen(theme.color("text"))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, QFontMetrics(title_font).elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width()))
        sub_rect = QRect(text_left, rect.top() + 29, title_rect.width(), 14)
        painter.setFont(sub_font)
        painter.setPen(theme.color("text_soft"))
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, QFontMetrics(sub_font).elidedText(subtitle, Qt.TextElideMode.ElideRight, sub_rect.width()))

        x = rect.right() - time_w - 8
        if value:
            value_rect = QRect(x - value_w, rect.top() + 17, value_w, 18)
            painter.setFont(QFont("Cascadia Mono", 8, QFont.Weight.Black))
            painter.setPen(color)
            painter.drawText(value_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, value)
        if time_text:
            painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold))
            painter.setPen(theme.color("muted"))
            painter.drawText(QRect(rect.right() - time_w - 2, rect.top() + 17, time_w, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, time_text)
        painter.restore()


class TimelineDialog(QDialog):
    token_requested = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("事件时间线")
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setFixedSize(548, 468)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._theme = active_theme()
        self._drag_origin = None
        self._logos: dict[str, object] = {}
        self._logo_loaders: dict[str, LogoLoader] = {}
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self.list_timestamp_update)

        self.close_button = EmbossCloseButton(self)
        self.close_button.setGeometry(500, 19, 28, 28)
        self.close_button.clicked.connect(self.accept)

        self.list_widget = QListWidget(self)
        self.list_widget.setGeometry(22, 72, 504, 370)
        self.list_widget.setItemDelegate(TimelineDelegate(self.list_widget))
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_widget.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_widget.itemClicked.connect(self._item_clicked)
        self._apply_styles()
        self.reload()

    def set_theme(self, skin: str) -> None:
        self._theme = get_theme(skin)
        self._apply_styles()
        self.update()

    def reload(self) -> None:
        self.list_widget.clear()
        for event in load_events():
            url = str(event.get("logo_url") or "").strip()
            if url and url in self._logos:
                event["_logo_pixmap"] = self._logos[url]
            elif url:
                self._ensure_logo(url)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, dict(event))
            self.list_widget.addItem(item)

    def list_timestamp_update(self) -> None:
        self.list_widget.viewport().update()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.reload()
        self._clock_timer.start()
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + (geo.height() - self.height()) // 2)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._clock_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        rect = self.rect().adjusted(6, 6, -6, -6)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 22, 22)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, self._theme.color("panel_top"))
        grad.setColorAt(0.5, self._theme.color("panel_mid"))
        grad.setColorAt(1.0, self._theme.color("panel_bottom"))
        painter.fillPath(path, grad)
        painter.setPen(QPen(self._theme.color("border", 74), 1))
        painter.drawPath(path)

        painter.setFont(QFont("Microsoft YaHei UI", 13, QFont.Weight.Black))
        painter.setPen(self._theme.color("text"))
        painter.drawText(QRect(24, 23, 280, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "事件时间线")
        painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
        painter.setPen(self._theme.color("muted"))
        painter.drawText(QRect(340, 24, 150, 22), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "最近 50 条")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None

    def _item_clicked(self, item: QListWidgetItem) -> None:
        event = item.data(Qt.ItemDataRole.UserRole) or {}
        chain = str(event.get("chain") or "").strip()
        address = str(event.get("address") or "").strip()
        if chain and address:
            self.token_requested.emit(chain, address)

    def _apply_styles(self) -> None:
        self.list_widget.setStyleSheet(
            """
            QListWidget {
                background: transparent;
                border: 0;
                outline: 0;
            }
            QListWidget::item {
                min-height: 52px;
                border: 0;
            }
            QListWidget::item:selected {
                background: transparent;
            }
            QScrollBar:horizontal {
                height: 0px;
                background: transparent;
            }
            QScrollBar:vertical {
                width: 8px;
                background: transparent;
                margin: 8px 2px 8px 0px;
            }
            QScrollBar::handle:vertical {
                min-height: 28px;
                border-radius: 4px;
                background: rgba(132,151,148,115);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                height: 0px;
                background: transparent;
            }
            """
        )

    def _ensure_logo(self, url: str) -> None:
        if not url or url in self._logos or url in self._logo_loaders:
            return
        loader = LogoLoader(url, 24)
        loader.loaded.connect(self._on_logo_loaded)
        loader.failed.connect(self._on_logo_failed)
        loader.finished.connect(loader.deleteLater)
        self._logo_loaders[url] = loader
        loader.start()

    def _on_logo_loaded(self, url: str, pixmap: object) -> None:
        self._logo_loaders.pop(url, None)
        self._logos[url] = pixmap
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            event = item.data(Qt.ItemDataRole.UserRole) or {}
            if str(event.get("logo_url") or "").strip() == url:
                event["_logo_pixmap"] = pixmap
                item.setData(Qt.ItemDataRole.UserRole, event)
        self.list_widget.viewport().update()

    def _on_logo_failed(self, url: str) -> None:
        self._logo_loaders.pop(url, None)


def relative_time(value: object) -> str:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return ""
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    delta = max(0, int(time.time() - timestamp))
    if delta < 60:
        return f"{max(1, delta)}s前"
    minutes = delta // 60
    if minutes < 60:
        return f"{minutes}m前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h前"
    return f"{hours // 24}d前"
