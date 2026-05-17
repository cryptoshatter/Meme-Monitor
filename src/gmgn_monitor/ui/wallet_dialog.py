from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from PySide6.QtCore import QTimer, QRect, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QMouseEvent, QPainter, QPaintEvent, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QStyledItemDelegate, QStyle

from gmgn_monitor.config import app_data_dir
from gmgn_monitor.gmgn_client import GmgnOpenApiClient, possible_wallet_chains
from gmgn_monitor.ui.images import avatar_pixmap

from gmgn_monitor.ui.emoji_picker import EmojiPicker, first_emoji

DEFAULT_EMOJI = "\U0001f9e9"
KOL_CHAINS = ("sol", "eth", "base", "bsc")
_KOL_CACHE_TTL = 90.0
_KOL_CACHE_LOCK = threading.Lock()
_KOL_CACHE: dict[tuple[str, str, str, str], tuple[float, list[dict[str, Any]]]] = {}


class KolSearchWorker(QThread):
    finished_search = Signal(int, str, object)

    def __init__(self, request_id: int, query: str, api_key: str, host: str) -> None:
        super().__init__()
        self.request_id = request_id
        self.query = query
        self.api_key = api_key
        self.host = host

    def run(self) -> None:
        query = self.query.strip()
        if not self.api_key or not query:
            self.finished_search.emit(self.request_id, query, [])
            return
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        chains = _kol_search_chains(query)
        tasks = [(chain, source) for chain in chains for source in ("kol", "smartmoney")]
        with ThreadPoolExecutor(max_workers=min(6, len(tasks))) as pool:
            futures = [pool.submit(_cached_kol_wallets, self.api_key, self.host, chain, source) for chain, source in tasks]
            for future in as_completed(futures):
                try:
                    wallets = future.result()
                except Exception:
                    continue
                for wallet in wallets:
                    if not _kol_matches(query, wallet):
                        continue
                    key = f"{wallet.get('chain', '')}:{str(wallet.get('address') or '').lower()}"
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(_kol_display_wallet(wallet))
        items.sort(key=lambda item: _kol_match_score(query, item), reverse=True)
        self.finished_search.emit(self.request_id, query, items[:12])


def _cached_kol_wallets(api_key: str, host: str, chain: str, source: str = "kol") -> list[dict[str, Any]]:
    key = (api_key[-10:], host.rstrip("/"), chain, source)
    now = time.monotonic()
    with _KOL_CACHE_LOCK:
        cached = _KOL_CACHE.get(key)
        if cached and cached[0] > now:
            return [dict(item) for item in cached[1]]
    client = GmgnOpenApiClient(api_key, host)
    wallets = client.get_smartmoney_wallets(chain, limit=500) if source == "smartmoney" else client.get_kol_wallets(chain, limit=500)
    with _KOL_CACHE_LOCK:
        _KOL_CACHE[key] = (now + _KOL_CACHE_TTL, [dict(item) for item in wallets])
    return wallets


def _kol_search_chains(query: str) -> tuple[str, ...]:
    query = query.strip()
    if query.startswith(("0x", "0X")):
        return ("eth", "base", "bsc")
    return KOL_CHAINS


def _kol_matches(query: str, wallet: dict[str, Any]) -> bool:
    query = query.lower().strip()
    haystack = " ".join(
        [
            str(wallet.get("remark") or ""),
            str(wallet.get("twitter_username") or ""),
            str(wallet.get("twitter_name") or ""),
            " ".join(str(tag) for tag in wallet.get("tags", []) if str(tag).strip()),
            str(wallet.get("address") or ""),
        ]
    ).lower()
    return query in haystack


def _kol_match_score(query: str, wallet: dict[str, Any]) -> int:
    query = query.lower().strip()
    name = str(wallet.get("remark") or "").lower()
    twitter = str(wallet.get("twitter_username") or "").lower().lstrip("@")
    address = str(wallet.get("address") or "").lower()
    if query == name or query == twitter:
        return 100
    if name.startswith(query) or twitter.startswith(query):
        return 80
    if query in name or query in twitter:
        return 60
    if query in address:
        return 30
    return 0


def _kol_display_wallet(wallet: dict[str, Any]) -> dict[str, Any]:
    item = dict(wallet)
    url = str(item.get("avatar_value") or "").strip()
    if str(item.get("avatar_kind") or "") == "image" and url.startswith(("http://", "https://")):
        cached = _cached_avatar_path(url)
        item["avatar_url"] = url
        if cached:
            item["avatar_value"] = cached
        else:
            item["avatar_kind"] = "emoji"
            item["avatar_value"] = DEFAULT_EMOJI
    return item


def _cached_avatar_path(url: str) -> str:
    cache_dir = app_data_dir() / "avatar_cache"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    path = cache_dir / f"{digest}.img"
    return str(path) if path.exists() and path.stat().st_size > 0 else ""


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
        if wallet.get("_message"):
            painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
            painter.setPen(QColor(166, 188, 181))
            painter.drawText(rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(wallet["_message"]))
            painter.restore()
            return
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


class KolResultDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        return QSize(360, 42)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        wallet = index.data(Qt.ItemDataRole.UserRole) or {}
        rect = option.rect.adjusted(4, 3, -4, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(53, 218, 143, 62) if selected else QColor(255, 255, 255, 10))
        painter.drawRoundedRect(rect, 9, 9)

        avatar_rect = QRect(rect.left() + 8, rect.top() + 7, 26, 26)
        painter.drawPixmap(avatar_rect, avatar_pixmap(wallet.get("avatar_kind", "emoji"), wallet.get("avatar_value", DEFAULT_EMOJI), 26))
        name = str(wallet.get("remark") or "KOL")
        chain = str(wallet.get("chain") or "").upper()
        address = str(wallet.get("address") or "")
        text_left = avatar_rect.right() + 9
        text_width = max(20, rect.right() - text_left - 8)

        name_font = QFont("Microsoft YaHei UI", 8, QFont.Weight.Black)
        sub_font = QFont("Cascadia Mono", 7, QFont.Weight.Bold)
        painter.setFont(name_font)
        painter.setPen(QColor(241, 255, 248))
        painter.drawText(QRect(text_left, rect.top() + 5, text_width, 15), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, QFontMetrics(name_font).elidedText(name, Qt.TextElideMode.ElideRight, text_width))
        painter.setFont(sub_font)
        painter.setPen(QColor(132, 234, 181))
        painter.drawText(QRect(text_left, rect.top() + 22, text_width, 13), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, QFontMetrics(sub_font).elidedText(f"{chain} {short_address(address)}", Qt.TextElideMode.ElideRight, text_width))
        painter.restore()


class WalletDialog(QDialog):
    wallets_changed = Signal(object)

    def __init__(self, wallets: list[dict[str, Any]], api_key: str = "", api_host: str = "https://openapi.gmgn.ai", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("钱包监控")
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
        self._api_key = api_key
        self._api_host = api_host
        self._search_request_id = 0
        self._kol_search_worker: KolSearchWorker | None = None
        self._kol_workers: list[KolSearchWorker] = []
        self._kol_search_timer = QTimer(self)
        self._kol_search_timer.setSingleShot(True)
        self._kol_search_timer.setInterval(180)
        self._kol_search_timer.timeout.connect(self._search_kol_wallets)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(420)
        self._autosave_timer.timeout.connect(self._emit_wallets_changed)

        self.list_widget = QListWidget(self)
        self.list_widget.setGeometry(28, 146, 274, 158)
        self.list_widget.setItemDelegate(WalletItemDelegate(self.list_widget))
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_widget.currentRowChanged.connect(self._select_wallet)

        self.kol_search_edit = QLineEdit(self)
        self.kol_search_edit.setGeometry(28, 76, 544, 38)
        self.kol_search_edit.setPlaceholderText("搜索 KOL 名字（API限制只能找到最近有交易行为的100个钱包）")
        self.kol_search_edit.textEdited.connect(self._schedule_kol_search)

        self.kol_result_list = QListWidget(self)
        self.kol_result_list.setGeometry(28, 118, 544, 118)
        self.kol_result_list.setItemDelegate(KolResultDelegate(self.kol_result_list))
        self.kol_result_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.kol_result_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.kol_result_list.setUniformItemSizes(True)
        self.kol_result_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.kol_result_list.itemClicked.connect(self._add_kol_result)
        self.kol_result_list.hide()

        self.add_wallet_button = QPushButton("+", self)
        self.add_wallet_button.setGeometry(28, 316, 130, 30)
        self.add_wallet_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_wallet_button.clicked.connect(self._new_wallet)

        self.remove_wallet_button = QPushButton("-", self)
        self.remove_wallet_button.setGeometry(172, 316, 130, 30)
        self.remove_wallet_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_wallet_button.clicked.connect(self._delete_wallet)

        self.remark_edit = QLineEdit(self)
        self.remark_edit.setGeometry(326, 146, 246, 35)
        self.remark_edit.setPlaceholderText("钱包备注")
        self.remark_edit.textEdited.connect(self._on_wallet_field_edited)

        self.address_edit = QLineEdit(self)
        self.address_edit.setGeometry(326, 191, 246, 38)
        self.address_edit.setPlaceholderText("钱包地址")
        self.address_edit.setCursorPosition(0)
        self.address_edit.textEdited.connect(self._on_wallet_field_edited)

        self._avatar_kind = "emoji"
        self._avatar_value = DEFAULT_EMOJI

        self.avatar_button = QPushButton("钱包图标", self)
        self.avatar_button.setText("钱包图标")
        self.avatar_button.setGeometry(326, 252, 246, 34)
        self.avatar_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.avatar_button.clicked.connect(self._open_avatar_picker)

        self.cancel_button = QPushButton("关闭", self)
        self.cancel_button.setGeometry(492, 342, 80, 31)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.accept)

        self.ok_button = QPushButton("", self)
        self.ok_button.hide()

        self._apply_styles()
        self._refresh_list()

    @property
    def wallets(self) -> list[dict[str, Any]]:
        self._sync_current_wallet_preview()
        return [dict(wallet) for wallet in self._wallets]

    def _accept_with_save(self) -> None:
        self._sync_current_wallet_preview()
        self._emit_wallets_changed()
        self.accept()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + (geo.height() - self.height()) // 2)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._emit_wallets_changed()
        self._stop_kol_worker()
        super().closeEvent(event)

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
        painter.drawText(28, 30, 250, 24, Qt.AlignmentFlag.AlignLeft, "钱包监控")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None

    def _stop_kol_worker(self) -> None:
        self._kol_search_timer.stop()
        for worker in list(self._kol_workers):
            if worker.isRunning():
                worker.quit()
                worker.wait(400)

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
        self._queue_wallets_changed()

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
        self._emit_wallets_changed()

    def _open_avatar_picker(self) -> None:
        dialog = EmojiPicker(self._avatar_value, self._avatar_kind, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._set_avatar(dialog.selected_kind, dialog.selected_image if dialog.selected_kind == "image" else dialog.selected_emoji)
        self._sync_current_wallet_preview()
        self._emit_wallets_changed()

    def _on_wallet_field_edited(self, _text: str = "") -> None:
        self._sync_current_wallet_preview()
        self._queue_wallets_changed()

    def _sync_current_wallet_preview(self) -> None:
        if self._loading_wallet:
            return
        row = self._selected_index
        if row < 0 or row >= len(self._wallets):
            return
        wallet = self._wallets[row]
        old_address = str(wallet.get("address") or "").strip()
        new_address = self.address_edit.text().strip()
        if new_address.startswith(("0x", "0X")):
            new_address = new_address.lower()
        wallet["remark"] = self.remark_edit.text().strip() or "Wallet"
        wallet["address"] = new_address
        if new_address != old_address:
            chains = possible_wallet_chains(new_address)
            wallet["chains"] = chains
            wallet["chain"] = chains[0] if chains else ""
        wallet["avatar_kind"] = self._avatar_kind
        wallet["avatar_value"] = self._current_avatar_value()
        item = self.list_widget.item(row)
        if item is not None:
            item.setData(Qt.ItemDataRole.UserRole, dict(wallet))

    def _queue_wallets_changed(self) -> None:
        if self._loading_wallet:
            return
        self._autosave_timer.start()

    def _emit_wallets_changed(self) -> None:
        self._autosave_timer.stop()
        self._sync_current_wallet_preview()
        self.wallets_changed.emit(self.wallets)

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

    def _schedule_kol_search(self, text: str) -> None:
        query = text.strip()
        self.kol_result_list.clear()
        if len(query) < 1 or not self._api_key:
            self.kol_result_list.hide()
            self._kol_search_timer.stop()
            return
        self._kol_search_timer.start()

    def _search_kol_wallets(self) -> None:
        query = self.kol_search_edit.text().strip()
        if not query or not self._api_key:
            self.kol_result_list.hide()
            return
        self._search_request_id += 1
        worker = KolSearchWorker(self._search_request_id, query, self._api_key, self._api_host)
        worker.finished_search.connect(self._show_kol_results)
        worker.finished.connect(lambda worker=worker: self._kol_workers.remove(worker) if worker in self._kol_workers else None)
        worker.finished.connect(worker.deleteLater)
        self._kol_search_worker = worker
        self._kol_workers.append(worker)
        worker.start()

    def _show_kol_results(self, request_id: int, query: str, results: object) -> None:
        if request_id != self._search_request_id or query != self.kol_search_edit.text().strip():
            return
        self.kol_result_list.clear()
        if not isinstance(results, list) or not results:
            self.kol_result_list.hide()
            return
        for wallet in results:
            if not isinstance(wallet, dict):
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, dict(wallet))
            self.kol_result_list.addItem(item)
        self.kol_result_list.setVisible(self.kol_result_list.count() > 0)
        self.kol_result_list.raise_()

    def _add_kol_result(self, item: QListWidgetItem) -> None:
        wallet = item.data(Qt.ItemDataRole.UserRole) or {}
        if not isinstance(wallet, dict):
            return
        address = str(wallet.get("address") or "").strip()
        if not address:
            return
        key = f"{str(wallet.get('chain') or '').lower()}:{address.lower()}"
        for idx, existing in enumerate(self._wallets):
            existing_key = f"{str(existing.get('chain') or '').lower()}:{str(existing.get('address') or '').lower()}"
            if existing_key == key or str(existing.get("address") or "").lower() == address.lower():
                self._refresh_list()
                self.list_widget.setCurrentRow(idx)
                self.kol_result_list.hide()
                self._emit_wallets_changed()
                return
        avatar_kind, avatar_value = self._wallet_avatar(wallet)
        self._wallets.append(
            {
                "remark": str(wallet.get("remark") or "KOL").strip() or "KOL",
                "address": address,
                "chain": str(wallet.get("chain") or "").lower().strip(),
                "chains": [str(wallet.get("chain") or "").lower().strip()] if wallet.get("chain") else [],
                "avatar_kind": avatar_kind,
                "avatar_value": avatar_value,
            }
        )
        self._refresh_list()
        self.list_widget.setCurrentRow(len(self._wallets) - 1)
        self.kol_result_list.hide()
        self.kol_search_edit.clear()
        self._emit_wallets_changed()

    def _wallet_avatar(self, wallet: dict[str, Any]) -> tuple[str, str]:
        kind = str(wallet.get("avatar_kind") or "emoji").lower().strip()
        value = str(wallet.get("avatar_value") or DEFAULT_EMOJI)
        if kind == "image" and value and not value.startswith(("http://", "https://")):
            return "image", value
        url = str(wallet.get("avatar_url") or "").strip()
        if url:
            cached = _cached_avatar_path(url)
            if cached:
                return "image", cached
        return "emoji", DEFAULT_EMOJI

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
        self.kol_search_edit.setStyleSheet(
            """
            QLineEdit {
                background: rgba(11, 22, 18, 244);
                color: #effff7;
                border: 1px solid rgba(71, 238, 153, 92);
                border-radius: 11px;
                padding: 7px 11px;
                selection-color: #f4fff9;
                selection-background-color: #1f7f59;
                font: 900 12px "Microsoft YaHei UI";
            }
            QLineEdit:focus {
                border: 1px solid rgba(83,255,173,190);
                background: rgba(14, 30, 24, 250);
            }
            """
        )
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
            QScrollBar:horizontal {
                height: 0px;
                background: transparent;
            }
            QScrollBar:vertical {
                width: 7px;
                background: transparent;
                margin: 8px 2px 8px 0px;
            }
            QScrollBar::handle:vertical {
                min-height: 26px;
                border-radius: 3px;
                background: rgba(179, 206, 196, 115);
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(77, 238, 156, 165);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                height: 0px;
                background: transparent;
            }
            """
        )
        self.kol_result_list.setStyleSheet(
            """
            QListWidget {
                background: rgba(5, 10, 11, 246);
                color: #edf7f3;
                border: 1px solid rgba(76,238,157,110);
                border-radius: 12px;
                padding: 5px;
                font: 700 10px "Microsoft YaHei UI";
            }
            QListWidget::item {
                min-height: 36px;
                padding: 4px 6px;
                border-radius: 8px;
            }
            QListWidget::item:selected {
                background: rgba(53, 218, 143, 55);
            }
            QScrollBar:horizontal {
                height: 0px;
                background: transparent;
            }
            QScrollBar:vertical {
                width: 7px;
                background: transparent;
                margin: 8px 2px 8px 0px;
            }
            QScrollBar::handle:vertical {
                min-height: 26px;
                border-radius: 3px;
                background: rgba(179, 206, 196, 115);
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(77, 238, 156, 165);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                height: 0px;
                background: transparent;
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
