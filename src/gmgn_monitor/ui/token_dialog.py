from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QFontMetrics, QLinearGradient, QMouseEvent, QPainter, QPaintEvent, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QStyledItemDelegate, QStyle, QWidget

from gmgn_monitor.gmgn_client import GmgnOpenApiClient
from gmgn_monitor.ui.images import LogoLoader, native_icon, token_fallback_logo

PANEL_BG_TOP = QColor(18, 27, 27, 248)
PANEL_BG_MID = QColor(8, 13, 15, 246)
PANEL_BG_BOTTOM = QColor(4, 7, 9, 252)
ACCENT = QColor(64, 239, 153)
TEXT = QColor(236, 246, 242)
MUTED = QColor(132, 151, 148)


class TokenInfoWorker(QThread):
    resolved = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, api_key: str, api_host: str, address: str, preferred_chain: str = "") -> None:
        super().__init__()
        self.api_key = api_key
        self.api_host = api_host
        self.address = normalize_address(address)
        self.preferred_chain = preferred_chain.lower().strip()

    def run(self) -> None:
        if not self.api_key:
            self.failed.emit(self.address.lower(), "缺少 GMGN API Key")
            return
        chains = candidate_chains(self.address, self.preferred_chain)
        client = GmgnOpenApiClient(self.api_key, self.api_host)
        last_error = ""
        for chain in chains:
            try:
                snap = client.get_token_info(chain, self.address)
            except Exception as exc:
                last_error = str(exc)
                continue
            if snap.symbol and snap.symbol != "TOKEN":
                self.resolved.emit(
                    self.address.lower(),
                    {
                        "chain": snap.chain,
                        "address": normalize_address(snap.address or self.address),
                        "symbol": snap.symbol,
                        "name": snap.name,
                        "logo_url": snap.logo_url,
                    },
                )
                return
        self.failed.emit(self.address.lower(), last_error or "GMGN 未返回代币信息")


class TokenItemDelegate(QStyledItemDelegate):
    delete_requested = Signal(int)

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        return QSize(286, 68)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        token = index.data(Qt.ItemDataRole.UserRole) or {}
        rect = option.rect.adjusted(6, 5, -6, -5)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        enabled = bool(token.get("enabled", True))
        pinned = bool(token.get("pinned", False))

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        row_path = QPainterPath()
        row_path.addRoundedRect(QRectF(rect), 12, 12)
        bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if pinned:
            bg.setColorAt(0.0, QColor(20, 63, 45, 236))
            bg.setColorAt(1.0, QColor(8, 28, 24, 238))
        else:
            bg.setColorAt(0.0, QColor(14, 21, 22, 214))
            bg.setColorAt(1.0, QColor(7, 11, 13, 224))
        painter.fillPath(row_path, bg)

        side = QColor(61, 237, 151) if pinned else QColor(13, 88, 55)
        side.setAlpha(245 if pinned else 150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(side)
        painter.drawRoundedRect(QRect(rect.left() + 9, rect.top() + 11, 4, rect.height() - 22), 2, 2)
        border = QColor(82, 239, 164, 98) if pinned else QColor(132, 151, 148, 42)
        painter.setPen(QPen(border, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(row_path)

        symbol = str(token.get("symbol") or "TOKEN")
        chain = str(token.get("chain") or "").lower()
        address = str(token.get("address") or "")
        logo = token.get("_logo_pixmap")
        if not hasattr(logo, "isNull"):
            logo = QPixmapNull()
        if logo.isNull():
            logo = token_fallback_logo(symbol, chain, 28)
        logo_rect = QRect(rect.left() + 24, rect.top() + 14, 30, 30)
        painter.drawPixmap(logo_rect, logo)
        painter.drawPixmap(QRect(logo_rect.right() - 9, logo_rect.bottom() - 9, 13, 13), native_icon(chain, 13))

        text_left = logo_rect.right() + 11
        action_w = 88 if not pinned else 38
        text_w = max(40, rect.right() - text_left - action_w - 8)
        title_font = QFont("Segoe UI Variable Text", 10, QFont.Weight.Black)
        sub_font = QFont("Cascadia Mono", 7, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.setPen(TEXT if enabled else QColor(135, 147, 145))
        painter.drawText(QRect(text_left, rect.top() + 11, text_w, 19), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, QFontMetrics(title_font).elidedText(symbol, Qt.TextElideMode.ElideRight, text_w))
        painter.setFont(sub_font)
        painter.setPen(QColor(159, 181, 176) if enabled else QColor(93, 106, 104))
        sub = short_address(address)
        painter.drawText(QRect(text_left, rect.top() + 34, text_w, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, QFontMetrics(sub_font).elidedText(sub, Qt.TextElideMode.ElideRight, text_w))

        if not pinned:
            threshold_rect = self.threshold_rect(rect)
            self._draw_threshold_shell(painter, threshold_rect)
        del_rect = self.action_rect(rect)
        self._draw_action(painter, del_rect, "-", QColor(255, 91, 111))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # type: ignore[override]
        if event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            rect = option.rect.adjusted(5, 5, -5, -5)
            del_rect = self.action_rect(rect)
            point = event.position().toPoint()
            if del_rect.contains(point):
                self.delete_requested.emit(index.row())
                return True
        return False

    def action_rect(self, rect: QRect) -> QRect:
        return QRect(rect.right() - 36, rect.top() + 16, 24, 24)

    def threshold_rect(self, rect: QRect) -> QRect:
        return QRect(rect.right() - 84, rect.top() + 17, 42, 22)

    def _draw_action(self, painter: QPainter, rect: QRect, text: str, color: QColor) -> None:
        bg = QColor(color)
        bg.setAlpha(12)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 72), 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 7, 7)
        painter.setFont(QFont("Segoe UI Variable Text", 13, QFont.Weight.Black))
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_threshold_shell(self, painter: QPainter, rect: QRect) -> None:
        painter.setPen(QPen(QColor(82, 239, 164, 74), 1))
        painter.setBrush(QColor(82, 239, 164, 12))
        painter.drawRoundedRect(rect, 8, 8)


class ChainThresholdBar(QWidget):
    value_changed = Signal(float)

    def __init__(self, value: float = 1.0, parent=None) -> None:
        super().__init__(parent)
        self._value = clamp_threshold(value)
        self._dragging = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def value(self) -> float:
        return self._value

    def set_value(self, value: float, emit: bool = False) -> None:
        value = clamp_threshold(value)
        if abs(value - self._value) < 0.001:
            return
        self._value = value
        self.update()
        if emit:
            self.value_changed.emit(value)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = self.rect().adjusted(0, 1, 0, -1)

        shell = QPainterPath()
        shell.addRoundedRect(QRectF(rect), 13, 13)
        shell_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        shell_grad.setColorAt(0.0, QColor(7, 13, 14, 242))
        shell_grad.setColorAt(0.55, QColor(12, 24, 23, 236))
        shell_grad.setColorAt(1.0, QColor(5, 9, 11, 246))
        painter.fillPath(shell, shell_grad)
        painter.setPen(QPen(QColor(82, 239, 164, 72), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(shell)

        track = QRectF(rect.left() + 48, rect.top() + 12, rect.width() - 96, 6)
        center_x = track.center().x()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(18, 31, 32, 235))
        painter.drawRoundedRect(track, 3, 3)

        for i in range(15):
            t = i / 14
            x = track.left() + t * track.width()
            h = 10 if i in {0, 7, 14} else 6
            c = QColor(82, 239, 164, 80 if i in {0, 7, 14} else 42)
            painter.setPen(QPen(c, 1))
            painter.drawLine(QPointF(x, track.center().y() - h / 2), QPointF(x, track.center().y() + h / 2))

        span = track.width() * min(100.0, self._value) / 100.0 / 2
        left_active = QRectF(center_x - span, track.top(), span, track.height())
        right_active = QRectF(center_x, track.top(), span, track.height())
        red = QLinearGradient(left_active.topLeft(), left_active.topRight())
        red.setColorAt(0, QColor(255, 82, 103, 35))
        red.setColorAt(1, QColor(255, 82, 103, 150))
        green = QLinearGradient(right_active.topLeft(), right_active.topRight())
        green.setColorAt(0, QColor(48, 235, 137, 150))
        green.setColorAt(1, QColor(48, 235, 137, 35))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(red)
        painter.drawRoundedRect(left_active, 3, 3)
        painter.setBrush(green)
        painter.drawRoundedRect(right_active, 3, 3)

        self._draw_handle(painter, center_x - span, track.center().y(), QColor(255, 82, 103))
        self._draw_handle(painter, center_x + span, track.center().y(), QColor(48, 235, 137))

        painter.setPen(QPen(QColor(236, 246, 242, 215), 1.2))
        painter.drawLine(QPointF(center_x, rect.top() + 6), QPointF(center_x, rect.bottom() - 6))
        painter.setFont(QFont("Cascadia Mono", 7, QFont.Weight.Black))
        painter.setPen(QColor(222, 239, 235))
        painter.drawText(QRectF(center_x - 18, rect.top() + 1, 36, 11), Qt.AlignmentFlag.AlignCenter, "0")
        painter.setPen(QColor(255, 120, 136, 170))
        painter.drawText(QRectF(rect.left() + 9, rect.top() + 7, 36, 12), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "-100")
        painter.setPen(QColor(104, 245, 170, 170))
        painter.drawText(QRectF(rect.right() - 45, rect.top() + 7, 36, 12), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "+100")
        painter.setFont(QFont("Cascadia Mono", 8, QFont.Weight.Black))
        painter.setPen(QColor(127, 250, 184))
        painter.drawText(QRectF(center_x - 32, rect.bottom() - 12, 64, 11), Qt.AlignmentFlag.AlignCenter, f"±{format_threshold_value(self._value)}%")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_from_x(event.position().x(), emit=False)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._update_from_x(event.position().x(), emit=False)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._update_from_x(event.position().x(), emit=True)

    def _update_from_x(self, x: float, emit: bool) -> None:
        track = self.rect().adjusted(48, 0, -48, 0)
        center = track.center().x()
        half = max(1.0, track.width() / 2)
        value = min(100.0, max(0.1, abs(x - center) / half * 100.0))
        self.set_value(value, emit=emit)

    def _draw_handle(self, painter: QPainter, x: float, y: float, color: QColor) -> None:
        glow = QColor(color)
        glow.setAlpha(52)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(x, y), 7.4, 7.4)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 225))
        painter.drawEllipse(QPointF(x, y), 3.4, 3.4)


class EmbossCloseButton(QPushButton):
    def __init__(self, parent=None) -> None:
        super().__init__("", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: 0; }")

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = self.rect()
        hover = self.underMouse()
        font = QFont("Segoe UI Variable Display", 17, QFont.Weight.Black)
        painter.setFont(font)
        painter.setPen(QColor(0, 0, 0, 170))
        painter.drawText(rect.adjusted(1, 2, 1, 2), Qt.AlignmentFlag.AlignCenter, "×")
        painter.setPen(QColor(255, 255, 255, 54))
        painter.drawText(rect.adjusted(-1, -1, -1, -1), Qt.AlignmentFlag.AlignCenter, "×")
        painter.setPen(QColor(255, 124, 142) if hover else QColor(225, 241, 235))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "×")


class TokenDialog(QDialog):
    main_token_requested = Signal(str, str)
    tokens_changed = Signal(object)

    def __init__(self, tokens: list[dict[str, Any]], api_key: str = "", api_host: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CA 监控")
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setFixedSize(330, 456)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._tokens = [dict(token) for token in tokens]
        self._api_key = api_key
        self._api_host = api_host
        self._drag_origin = None
        self._logo_loaders: dict[str, LogoLoader] = {}
        self._info_workers: dict[str, TokenInfoWorker] = {}
        self._threshold_edits: list[QLineEdit] = []

        self.list_widget = QListWidget(self)
        self.list_widget.setGeometry(12, 124, 306, 268)
        self.delegate = TokenItemDelegate(self.list_widget)
        self.delegate.delete_requested.connect(self._delete_row)
        self.list_widget.setItemDelegate(self.delegate)
        self.list_widget.itemClicked.connect(self._item_clicked)

        self.threshold_bar = ChainThresholdBar(self._default_threshold(), self)
        self.threshold_bar.setGeometry(14, 54, 302, 28)
        self.sync_button = QPushButton("全局同步", self)
        self.sync_button.setGeometry(14, 87, 302, 28)
        self.sync_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sync_button.setAutoDefault(False)
        self.sync_button.setDefault(False)
        self.sync_button.clicked.connect(self._sync_global_threshold)

        self.close_button = EmbossCloseButton(self)
        self.close_button.setGeometry(288, 16, 25, 25)
        self.close_button.clicked.connect(self.accept)

        self.address_edit = QLineEdit(self)
        self.address_edit.setGeometry(14, 406, 222, 38)
        self.address_edit.setPlaceholderText("粘贴 CA")
        self.address_edit.returnPressed.connect(self._add_current_input)

        self.add_button = QPushButton("添加", self)
        self.add_button.setGeometry(244, 406, 72, 38)
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.setAutoDefault(False)
        self.add_button.setDefault(False)
        self.add_button.clicked.connect(self._add_current_input)

        self._apply_styles()
        self._preload_logos()
        self._refresh_list()

    @property
    def tokens(self) -> list[dict[str, Any]]:
        clean: list[dict[str, Any]] = []
        for token in self._tokens:
            if not str(token.get("address") or "").strip():
                continue
            item = dict(token)
            item.pop("_logo_pixmap", None)
            clean.append(item)
        return clean

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + (geo.height() - self.height()) // 2)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        rect = self.rect().adjusted(6, 6, -6, -6)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 20, 20)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, PANEL_BG_TOP)
        grad.setColorAt(0.5, PANEL_BG_MID)
        grad.setColorAt(1.0, PANEL_BG_BOTTOM)
        painter.fillPath(path, grad)
        painter.setPen(QPen(QColor(132, 151, 148, 72), 1.05))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        banner = QRect(14, 14, 302, 34)
        banner_path = QPainterPath()
        banner_path.addRoundedRect(QRectF(banner), 13, 13)
        banner_grad = QLinearGradient(banner.topLeft(), banner.bottomRight())
        banner_grad.setColorAt(0, QColor(18, 36, 32, 232))
        banner_grad.setColorAt(1, QColor(9, 18, 20, 238))
        painter.fillPath(banner_path, banner_grad)
        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Black))
        painter.setPen(TEXT)
        painter.drawText(banner.adjusted(12, 0, -46, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "CA 监控")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._position_threshold_edits()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_background_workers()
        super().closeEvent(event)

    def accept(self) -> None:  # type: ignore[override]
        self._stop_background_workers()
        super().accept()

    def reject(self) -> None:  # type: ignore[override]
        self._stop_background_workers()
        super().reject()

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        self._clear_threshold_edits()
        for token in self._tokens:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, dict(token))
            self.list_widget.addItem(item)
        self._install_threshold_edits()

    def _install_threshold_edits(self) -> None:
        for row, token in enumerate(self._tokens):
            if token.get("pinned"):
                continue
            item = self.list_widget.item(row)
            if item is None:
                continue
            edit = QLineEdit(self.list_widget.viewport())
            edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            edit.setValidator(QDoubleValidator(0.1, 100.0, 3, edit))
            edit.setText(format_threshold_value(token_threshold_value(token)))
            edit.setPlaceholderText("%")
            edit.setProperty("token_row", row)
            edit.setToolTip("提醒阈值 %")
            edit.editingFinished.connect(lambda edit=edit: self._threshold_edit_finished(edit))
            edit.returnPressed.connect(lambda edit=edit: edit.clearFocus())
            edit.setStyleSheet(self._threshold_edit_css())
            self._threshold_edits.append(edit)
        self._position_threshold_edits()

    def _clear_threshold_edits(self) -> None:
        for edit in self._threshold_edits:
            edit.deleteLater()
        self._threshold_edits = []

    def _position_threshold_edits(self) -> None:
        for edit in self._threshold_edits:
            row = edit.property("token_row")
            if not isinstance(row, int):
                continue
            item = self.list_widget.item(row)
            if item is None:
                continue
            rect = self.list_widget.visualItemRect(item).adjusted(6, 5, -6, -5)
            edit.setGeometry(self.delegate.threshold_rect(rect))
            edit.show()

    def _preload_logos(self) -> None:
        for token in self._tokens:
            url = str(token.get("logo_url") or "").strip()
            if not url or token.get("_logo_pixmap") is not None or url in self._logo_loaders:
                continue
            loader = LogoLoader(url, 28)
            loader.loaded.connect(self._on_logo_loaded)
            loader.failed.connect(self._on_logo_failed)
            loader.finished.connect(loader.deleteLater)
            self._logo_loaders[url] = loader
            loader.start()

    def _on_logo_loaded(self, url: str, pixmap: object) -> None:
        self._logo_loaders.pop(url, None)
        for token in self._tokens:
            if str(token.get("logo_url") or "") == url:
                token["_logo_pixmap"] = pixmap
        self._refresh_list()

    def _on_logo_failed(self, url: str) -> None:
        self._logo_loaders.pop(url, None)

    def _stop_background_workers(self) -> None:
        for loader in list(self._logo_loaders.values()):
            try:
                if loader.isRunning():
                    loader.loaded.disconnect()
                    loader.failed.disconnect()
                    loader.wait(2500)
            except RuntimeError:
                pass
        self._logo_loaders.clear()
        for worker in list(self._info_workers.values()):
            try:
                if worker.isRunning():
                    worker.resolved.disconnect()
                    worker.failed.disconnect()
                    worker.wait(8000)
            except RuntimeError:
                pass
        self._info_workers.clear()

    def _add_current_input(self) -> None:
        address = self.address_edit.text().strip()
        if not address:
            return
        if address.startswith(("0x", "0X")):
            address = address.lower()
        if any(str(token.get("address") or "").lower() == address.lower() for token in self._tokens):
            self.address_edit.clear()
            return
        self._tokens.append(
            {
                "chain": "",
                "address": address,
                "symbol": "识别中",
                "name": "",
                "logo_url": "",
                "alert_threshold_percent": None,
                "enabled": True,
                "pinned": not any(t.get("pinned") for t in self._tokens),
            }
        )
        self.address_edit.clear()
        self.tokens_changed.emit(self.tokens)
        self._refresh_list()
        self._resolve_token(address)

    def _delete_row(self, row: int) -> None:
        if row < 0 or row >= len(self._tokens):
            return
        was_pinned = bool(self._tokens[row].get("pinned"))
        del self._tokens[row]
        if was_pinned and self._tokens:
            self._tokens[0]["pinned"] = True
        self.tokens_changed.emit(self.tokens)
        self._refresh_list()

    def _item_clicked(self, item: QListWidgetItem) -> None:
        self._pin_row(self.list_widget.row(item))

    def _pin_row(self, row: int) -> None:
        if row < 0 or row >= len(self._tokens):
            return
        old_pinned_row = next((index for index, token in enumerate(self._tokens) if token.get("pinned")), -1)
        for index, token in enumerate(self._tokens):
            token["pinned"] = index == row
            if index == row:
                token["enabled"] = True
            elif index == old_pinned_row:
                token["alert_threshold_percent"] = None
        self._refresh_list()
        token = self._tokens[row]
        chain = str(token.get("chain") or "").strip()
        address = str(token.get("address") or "").strip()
        if chain and address:
            self.main_token_requested.emit(chain, address)
        self.tokens_changed.emit(self.tokens)

    def _resolve_token(self, address: str) -> None:
        address = normalize_address(address)
        key = address.lower()
        if key in self._info_workers:
            return
        worker = TokenInfoWorker(self._api_key, self._api_host, address)
        worker.resolved.connect(self._on_token_resolved)
        worker.failed.connect(self._on_token_failed)
        worker.finished.connect(lambda key=key: self._info_workers.pop(key, None))
        worker.finished.connect(worker.deleteLater)
        self._info_workers[key] = worker
        worker.start()

    def _on_token_resolved(self, address_key: str, info: object) -> None:
        if not isinstance(info, dict):
            return
        updated = False
        for token in self._tokens:
            if str(token.get("address") or "").lower() != address_key:
                continue
            token.update(info)
            updated = True
        if not updated:
            return
        self._preload_logos()
        self._refresh_list()
        self.tokens_changed.emit(self.tokens)
        pinned = next((token for token in self._tokens if token.get("pinned") and str(token.get("address") or "").lower() == address_key), None)
        if pinned and pinned.get("chain"):
            self.main_token_requested.emit(str(pinned["chain"]), str(pinned["address"]))

    def _on_token_failed(self, address_key: str, message: str) -> None:
        for token in self._tokens:
            if str(token.get("address") or "").lower() == address_key:
                token["symbol"] = "未识别"
        self._refresh_list()

    def _threshold_edit_finished(self, edit: QLineEdit) -> None:
        row = edit.property("token_row")
        if not isinstance(row, int) or row < 0 or row >= len(self._tokens) or self._tokens[row].get("pinned"):
            return
        raw = edit.text().strip()
        old_value = token_threshold_value(self._tokens[row])
        if not raw:
            edit.setText("")
            if old_value is None:
                return
            self._tokens[row]["alert_threshold_percent"] = None
            self.tokens_changed.emit(self.tokens)
            return
        value = clamp_threshold(raw)
        edit.setText(format_threshold_value(value))
        if old_value == value:
            return
        self._tokens[row]["alert_threshold_percent"] = value
        self.tokens_changed.emit(self.tokens)

    def _sync_global_threshold(self) -> None:
        value = clamp_threshold(self.threshold_bar.value)
        changed = False
        for token in self._tokens:
            if token.get("pinned"):
                continue
            if token_threshold_value(token) != value:
                token["alert_threshold_percent"] = value
                changed = True
        if changed:
            self._refresh_list()
            self.tokens_changed.emit(self.tokens)

    def _default_threshold(self) -> float:
        for token in self._tokens:
            if token.get("pinned"):
                continue
            value = token_threshold_value(token)
            if value is not None:
                return value
        return 1.0

    def _apply_styles(self) -> None:
        self.list_widget.setStyleSheet("""
            QListWidget { background: transparent; border: 0; outline: 0; }
            QListWidget::item { min-height: 56px; border: 0; }
            QListWidget::item:selected { background: transparent; }
        """)
        self.address_edit.setStyleSheet("""
            QLineEdit {
                background: rgba(8, 13, 15, 242);
                color: #effbf6;
                border: 1px solid rgba(132,151,148,76);
                border-radius: 12px;
                padding: 8px 10px;
                font: 700 12px "Cascadia Mono";
                selection-background-color: #1f7f59;
            }
            QLineEdit:focus {
                border: 1px solid rgba(82,239,164,150);
                background: rgba(12, 19, 20, 246);
            }
        """)
        self.add_button.setStyleSheet("""
            QPushButton {
                background: #39d88f;
                color: #06120d;
                border: 1px solid rgba(103,255,184,94);
                border-radius: 12px;
                font: 900 13px "Microsoft YaHei UI";
            }
            QPushButton:hover { background: #52eba5; }
        """)
        self.sync_button.setStyleSheet("""
            QPushButton {
                background: rgba(18, 31, 32, 238);
                color: #bdf7dc;
                border: 1px solid rgba(82,239,164,76);
                border-radius: 12px;
                font: 900 12px "Microsoft YaHei UI";
            }
            QPushButton:hover {
                background: rgba(31, 127, 89, 185);
                color: #effbf6;
                border: 1px solid rgba(103,255,184,130);
            }
            QPushButton:pressed {
                background: rgba(17, 95, 66, 210);
                padding-top: 1px;
            }
        """)

    def _threshold_edit_css(self) -> str:
        return """
            QLineEdit {
                background: rgba(10, 17, 18, 245);
                color: #78f5b0;
                border: 1px solid rgba(82,239,164,105);
                border-radius: 7px;
                padding: 1px 3px;
                font: 800 8px "Cascadia Mono";
                selection-background-color: #1f7f59;
            }
            QLineEdit:focus {
                background: rgba(14, 25, 24, 248);
                border: 1px solid rgba(103,255,184,165);
            }
        """


def short_address(address: str) -> str:
    if len(address) <= 13:
        return address
    return f"{address[:6]}...{address[-4:]}"


def normalize_address(address: str) -> str:
    value = str(address or "").strip()
    if value.startswith(("0x", "0X")):
        return value.lower()
    return value


def candidate_chains(address: str, preferred_chain: str = "") -> list[str]:
    preferred_chain = preferred_chain.lower().strip()
    if address.startswith(("0x", "0X")):
        chains = ["eth", "base", "bsc"]
    else:
        chains = ["sol", "bsc", "base", "eth"]
    if preferred_chain in chains:
        chains = [preferred_chain] + [chain for chain in chains if chain != preferred_chain]
    return chains


def clamp_threshold(value: object) -> float:
    try:
        number = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        number = 1.0
    return max(0.1, min(number, 100.0))


def token_threshold_value(token: dict[str, Any]) -> float | None:
    value = token.get("alert_threshold_percent")
    if value is None:
        return None
    raw = str(value).strip().rstrip("%")
    if not raw:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.1, min(number, 100.0))


def format_threshold_value(value: object) -> str:
    if isinstance(value, dict):
        number = token_threshold_value(value)
    else:
        number = token_threshold_value({"alert_threshold_percent": value})
    if number is None:
        return ""
    text = f"{number:.3f}".rstrip("0").rstrip(".")
    return text or "0.1"


def QPixmapNull():
    from PySide6.QtGui import QPixmap
    return QPixmap()
