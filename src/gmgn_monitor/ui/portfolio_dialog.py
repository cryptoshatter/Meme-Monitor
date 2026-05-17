from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Any

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, Signal, QPropertyAnimation
from PySide6.QtGui import QFont, QFontMetrics, QLinearGradient, QMouseEvent, QPainter, QPaintEvent, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QAbstractItemView, QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QStyledItemDelegate, QStyle

from gmgn_monitor.gmgn_client import GmgnOpenApiClient, normalize_wallet_address, parse_wallet_activity_items, parse_wallet_holdings, possible_wallet_chains
from gmgn_monitor.ui.images import LogoLoader, native_icon, token_fallback_logo
from gmgn_monitor.ui.theme import active_theme, get_theme, hex_rgb, rgba
from gmgn_monitor.ui.token_dialog import EmbossCloseButton


class SmoothListWidget(QListWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scroll_animation: QPropertyAnimation | None = None
        self._scroll_target = 0

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        delta = event.pixelDelta().y() or event.angleDelta().y()
        if not delta:
            super().wheelEvent(event)
            return
        bar = self.verticalScrollBar()
        if self._scroll_animation and self._scroll_animation.state() == QAbstractAnimation.State.Running:
            base = self._scroll_target
            self._scroll_animation.stop()
        else:
            base = bar.value()
        step = int(delta * 0.46)
        if not event.pixelDelta().y():
            step = int(delta * 0.46)
        target = max(bar.minimum(), min(bar.maximum(), base - step))
        self._scroll_target = target
        self._scroll_animation = QPropertyAnimation(bar, b"value", self)
        self._scroll_animation.setStartValue(bar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.setDuration(185)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_animation.start()
        event.accept()


class PortfolioWorker(QThread):
    resolved = Signal(str, object)
    failed = Signal(str)

    def __init__(self, api_key: str, api_host: str, evm_address: str, sol_address: str) -> None:
        super().__init__()
        self.api_key = api_key
        self.api_host = api_host
        self.evm_address = normalize_wallet_address(evm_address)
        self.sol_address = normalize_wallet_address(sol_address)

    def run(self) -> None:
        if not self.api_key:
            self.failed.emit("缺少 GMGN API Key")
            return
        targets: list[tuple[str, str]] = []
        if self.evm_address:
            evm_chains = possible_wallet_chains(self.evm_address)
            if not self.evm_address.startswith("0x") or not evm_chains:
                self.failed.emit("EVM 钱包地址格式不正确")
                return
            targets.extend((chain, self.evm_address) for chain in evm_chains)
        if self.sol_address:
            sol_chains = possible_wallet_chains(self.sol_address)
            if sol_chains != ["sol"]:
                self.failed.emit("SOL 钱包地址格式不正确")
                return
            targets.append(("sol", self.sol_address))
        if not targets:
            self.failed.emit("输入 EVM 或 SOL 钱包地址")
            return

        errors: list[str] = []
        holdings: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            futures = {
                pool.submit(_fetch_holdings_for_chain, self.api_key, self.api_host, chain, wallet_address): (chain, wallet_address)
                for chain, wallet_address in targets
            }
            for future in as_completed(futures):
                chain, _wallet_address = futures[future]
                try:
                    holdings.extend(future.result())
                except Exception as exc:
                    errors.append(f"{chain.upper()}: {exc}")

        holdings.sort(key=lambda item: float(item.get("usd_value") or 0.0), reverse=True)
        if holdings:
            self.resolved.emit(_wallet_label(self.evm_address, self.sol_address), [_mark_row(item, "holding") for item in holdings[:20]])
            return

        activities: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            futures = {
                pool.submit(_fetch_recent_activity_for_chain, self.api_key, self.api_host, chain, wallet_address): (chain, wallet_address)
                for chain, wallet_address in targets
            }
            for future in as_completed(futures):
                chain, _wallet_address = futures[future]
                try:
                    activities.extend(future.result())
                except Exception as exc:
                    errors.append(f"{chain.upper()}: {exc}")
        activities.sort(key=lambda item: int(item.get("timestamp") or 0), reverse=True)
        if activities:
            self.resolved.emit(f"{_wallet_label(self.evm_address, self.sol_address)}  无持仓，显示最近交易", activities[:20])
            return
        self.failed.emit("未拿到持仓或交易数据")


def _fetch_holdings_for_chain(api_key: str, api_host: str, chain: str, wallet_address: str) -> list[dict[str, Any]]:
    client = GmgnOpenApiClient(api_key, api_host)
    data = client.get_wallet_holdings(chain, wallet_address, limit=20, order_by="usd_value", direction="desc")
    return [asdict(item) for item in parse_wallet_holdings(chain, wallet_address, data)]


def _fetch_recent_activity_for_chain(api_key: str, api_host: str, chain: str, wallet_address: str) -> list[dict[str, Any]]:
    client = GmgnOpenApiClient(api_key, api_host)
    data = client.get_wallet_activity(chain, wallet_address, limit=20, activity_types=["buy", "sell"])
    return parse_wallet_activity_items(chain, wallet_address, data)


def _mark_row(item: dict[str, Any], row_type: str) -> dict[str, Any]:
    row = dict(item)
    row["row_type"] = row_type
    return row


class TokenActivityWorker(QThread):
    resolved = Signal(object, object)
    failed = Signal(object, str)

    def __init__(self, api_key: str, api_host: str, row: dict[str, Any]) -> None:
        super().__init__()
        self.api_key = api_key
        self.api_host = api_host
        self.row = dict(row)

    def run(self) -> None:
        chain = str(self.row.get("chain") or "").lower().strip()
        wallet_address = normalize_wallet_address(str(self.row.get("wallet_address") or ""))
        token_address = str(self.row.get("token_address") or "").strip()
        if not self.api_key or not chain or not wallet_address or not token_address:
            self.failed.emit(self.row, "缺少交易查询参数")
            return
        try:
            client = GmgnOpenApiClient(self.api_key, self.api_host)
            data = client.get_wallet_activity(chain, wallet_address, limit=20, activity_types=["buy", "sell"], token_address=token_address)
            activities = parse_wallet_activity_items(chain, wallet_address, data)
        except Exception as exc:
            self.failed.emit(self.row, str(exc))
            return
        if not activities:
            self.failed.emit(self.row, "没有该币种交易记录")
            return
        token_lower = token_address.lower()
        filtered = [item for item in activities if str(item.get("token_address") or "").lower() == token_lower]
        self.resolved.emit(self.row, (filtered or activities)[:20])


class PortfolioItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        item = index.data(Qt.ItemDataRole.UserRole) or {}
        if isinstance(item, dict) and str(item.get("row_type") or "") == "trade_drawer":
            activities = item.get("activities") if isinstance(item.get("activities"), list) else []
            return QSize(1042, max(70, min(446, 40 + len(activities[:20]) * 20)))
        return QSize(1042, 70)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        item = index.data(Qt.ItemDataRole.UserRole) or {}
        row_type = str(item.get("row_type") or "holding") if isinstance(item, dict) else "holding"
        if row_type == "trade_drawer":
            self._paint_trade_drawer(painter, option, item)
            return

        rect = option.rect.adjusted(6, 5, -6, -5)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        theme = active_theme()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 13, 13)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, theme.color("surface_soft", 224 if selected else 196))
        grad.setColorAt(1.0, theme.color("panel_bottom", 236))
        painter.fillPath(path, grad)
        painter.setPen(QPen(theme.color("accent", 105) if selected else theme.color("border", 52), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        chain = str(item.get("chain") or "").lower()
        symbol = str(item.get("symbol") or "TOKEN")
        logo = item.get("_logo_pixmap")
        if not isinstance(logo, QPixmap) or logo.isNull():
            logo = token_fallback_logo(symbol, chain, 34)
        logo_rect = QRect(rect.left() + 14, rect.top() + 13, 36, 36)
        painter.drawPixmap(logo_rect, logo)
        painter.drawPixmap(QRect(logo_rect.right() - 10, logo_rect.bottom() - 10, 14, 14), native_icon(chain, 14))

        token_left = logo_rect.right() + 11
        token_width = 178
        title_font = QFont("Segoe UI Variable Text", 10, QFont.Weight.Black)
        sub_font = QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.setPen(theme.color("text"))
        painter.drawText(
            QRect(token_left, rect.top() + 12, token_width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(title_font).elidedText(symbol, Qt.TextElideMode.ElideRight, token_width),
        )
        painter.setFont(sub_font)
        painter.setPen(theme.color("text_soft"))
        sub_text = str(item.get("name") or symbol)
        if row_type == "activity":
            sub_text = f"{side_text(item.get('side'))} · {str(item.get('chain') or '').upper()}"
        painter.drawText(
            QRect(token_left, rect.top() + 34, token_width, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(sub_font).elidedText(sub_text, Qt.TextElideMode.ElideRight, token_width),
        )

        number_font = QFont("Cascadia Mono", 9, QFont.Weight.Black)
        small_font = QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold)
        if row_type == "activity":
            side_color = theme.color("positive") if str(item.get("side")) == "buy" else theme.color("negative")
            columns = (
                (rect.left() + 246, 68, side_text(item.get("side")), side_color, Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 322, 94, format_money(item.get("cost_usd"), signed=False), theme.color("text"), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 424, 88, format_amount(item.get("token_amount")), theme.color("text_soft"), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 524, 88, format_price(item.get("price_usd")), theme.color("text_soft"), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 625, 58, str(item.get("chain") or "").upper(), theme.color("text_soft"), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 748, 88, relative_time(item.get("timestamp")), theme.color("text_soft"), Qt.AlignmentFlag.AlignCenter),
            )
        else:
            columns = (
                (rect.left() + 238, 90, format_money(item.get("usd_value"), signed=False), theme.color("text"), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 334, 90, format_money(item.get("unrealized_profit")), pnl_color(item.get("unrealized_profit")), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 430, 90, format_money(item.get("realized_profit")), pnl_color(item.get("realized_profit")), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 526, 90, format_money(item.get("total_profit")), pnl_color(item.get("total_profit")), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 622, 150, format_avg_market_cap(item), theme.color("text_soft"), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 778, 80, format_duration(item.get("holding_duration_seconds"), with_hand=True), theme.color("text_soft"), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 864, 64, format_buy_sell_count(item.get("buy_count"), item.get("sell_count")), theme.color("text_soft"), Qt.AlignmentFlag.AlignCenter),
                (rect.left() + 934, 86, relative_time(item.get("last_active_timestamp")), theme.color("text_soft"), Qt.AlignmentFlag.AlignCenter),
            )
        for x, width, text, color, align in columns:
            painter.setFont(number_font if text.startswith(("$", "+", "-")) or text.isdigit() else small_font)
            painter.setPen(color)
            painter.drawText(QRect(x, rect.top() + 21, width, 20), align | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    def _paint_trade_drawer(self, painter: QPainter, option, item: dict[str, Any]) -> None:
        outer = option.rect.adjusted(48, 0, -48, -7)
        rect_width = min(758, outer.width())
        rect = QRect(outer.left() + (outer.width() - rect_width) // 2, outer.top(), rect_width, outer.height())
        theme = active_theme()
        activities = item.get("activities") if isinstance(item.get("activities"), list) else []
        status = str(item.get("status") or "").strip()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 14, 14)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, theme.color("surface", 214))
        grad.setColorAt(1.0, theme.color("surface_soft", 170))
        painter.fillPath(path, grad)
        painter.setPen(QPen(theme.color("border", 54), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        small_font = QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold)
        mono_font = QFont("Cascadia Mono", 8, QFont.Weight.Black)

        painter.setFont(small_font)
        painter.setPen(theme.color("muted"))
        table_width = 618
        table_left = rect.left() + (rect.width() - table_width) // 2
        columns = (
            ("方向", 0, 54, Qt.AlignmentFlag.AlignLeft),
            ("金额", 72, 90, Qt.AlignmentFlag.AlignRight),
            ("数量", 176, 92, Qt.AlignmentFlag.AlignRight),
            ("价格", 292, 96, Qt.AlignmentFlag.AlignRight),
            ("买/卖市值", 412, 104, Qt.AlignmentFlag.AlignRight),
            ("时间", 532, 82, Qt.AlignmentFlag.AlignRight),
        )
        for text, offset, width, align in columns:
            painter.drawText(QRect(table_left + offset, rect.top() + 12, width, 17), align | Qt.AlignmentFlag.AlignVCenter, text)

        if not activities:
            painter.setPen(theme.color("text_soft"))
            painter.drawText(rect.adjusted(16, 28, -16, 0), Qt.AlignmentFlag.AlignCenter, status or "后台更新中")
            painter.restore()
            return

        line_pen = QPen(theme.color("border", 22), 1)
        y = rect.top() + 34
        for index, activity in enumerate(activities[:20]):
            side = str(activity.get("side") or "")
            side_color = theme.color("positive") if side == "buy" else theme.color("negative")
            painter.setFont(small_font)
            painter.setPen(side_color)
            painter.drawText(QRect(table_left, y, 54, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, side_text(side))
            painter.setFont(mono_font)
            painter.setPen(theme.color("text"))
            painter.drawText(QRect(table_left + 72, y, 90, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, format_money(activity.get("cost_usd"), signed=False))
            painter.setPen(theme.color("text_soft"))
            painter.drawText(QRect(table_left + 176, y, 92, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, format_amount(activity.get("token_amount")))
            painter.drawText(QRect(table_left + 292, y, 96, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, format_price(activity.get("price_usd")))
            painter.drawText(QRect(table_left + 412, y, 104, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, format_money(activity_market_cap(activity), signed=False))
            painter.drawText(QRect(table_left + 532, y, 82, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, relative_time(activity.get("timestamp")))
            if index < min(len(activities), 20) - 1:
                painter.setPen(line_pen)
                painter.drawLine(table_left, y + 19, table_left + table_width, y + 19)
            y += 20
        painter.restore()


class PortfolioDialog(QDialog):
    wallet_addresses_changed = Signal(str, str)
    holdings_updated = Signal(object)

    def __init__(
        self,
        api_key: str = "",
        api_host: str = "https://openapi.gmgn.ai",
        evm_address: str = "",
        sol_address: str = "",
        cached_holdings: list[dict[str, Any]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("个人持仓")
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setFixedSize(1100, 590)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._api_key = api_key
        self._api_host = api_host
        self._theme = active_theme()
        self._drag_origin = None
        self._worker: PortfolioWorker | None = None
        self._token_activity_workers: list[TokenActivityWorker] = []
        self._expanded_token_key = ""
        self._drawer_status = ""
        self._drawer_activities: list[dict[str, Any]] = []
        self._drawer_source_row: dict[str, Any] = {}
        self._trade_cache: dict[str, list[dict[str, Any]]] = {}
        self._trade_cache_status: dict[str, str] = {}
        self._sort_key = "usd_value"
        self._sort_desc = True
        self._last_wallet_label = ""
        self._pending_query = False
        self._logo_loaders: dict[str, LogoLoader] = {}
        self._holdings: list[dict[str, Any]] = []
        self._query_timer = QTimer(self)
        self._query_timer.setSingleShot(True)
        self._query_timer.setInterval(520)
        self._query_timer.timeout.connect(self._query)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(6000)
        self._refresh_timer.timeout.connect(self._query)

        self.evm_address_edit = QLineEdit(self)
        self.evm_address_edit.setGeometry(26, 72, 1042, 36)
        self.evm_address_edit.setPlaceholderText("EVM 钱包地址（ETH / Base / BSC，可空）")
        self.evm_address_edit.setText(normalize_wallet_address(evm_address))
        self.evm_address_edit.textEdited.connect(self._addresses_edited)
        self.evm_address_edit.returnPressed.connect(self._schedule_query)

        self.sol_address_edit = QLineEdit(self)
        self.sol_address_edit.setGeometry(26, 116, 1042, 36)
        self.sol_address_edit.setPlaceholderText("SOL 钱包地址（可空）")
        self.sol_address_edit.setText(normalize_wallet_address(sol_address))
        self.sol_address_edit.textEdited.connect(self._addresses_edited)
        self.sol_address_edit.returnPressed.connect(self._schedule_query)

        self.close_button = EmbossCloseButton(self)
        self.close_button.setGeometry(1042, 20, 28, 28)
        self.close_button.clicked.connect(self.accept)

        self.status_label = QLabel("共 0 条", self)
        self.status_label.setGeometry(26, 542, 1042, 24)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.list_widget = SmoothListWidget(self)
        self.list_widget.setGeometry(24, 190, 1050, 342)
        self.list_widget.setItemDelegate(PortfolioItemDelegate(self.list_widget))
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_widget.setUniformItemSizes(False)
        self.list_widget.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.setAutoScroll(False)
        self.list_widget.verticalScrollBar().setSingleStep(18)
        self.list_widget.verticalScrollBar().setPageStep(132)
        self.list_widget.itemClicked.connect(self._toggle_token_drawer)

        self._apply_styles()
        self._holdings = clean_cached_holdings(cached_holdings)
        if self._holdings:
            self._preload_logos()
            self._refresh_list()
            self.status_label.setText(self._count_text())
        if self._current_addresses() != ("", ""):
            QTimer.singleShot(180, self._schedule_query)

    def set_theme(self, skin: str) -> None:
        self._theme = get_theme(skin)
        self._apply_styles()
        self.list_widget.viewport().update()
        self.update()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + (geo.height() - self.height()) // 2)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_background_work()
        super().closeEvent(event)

    def accept(self) -> None:  # type: ignore[override]
        self._stop_background_work()
        super().accept()

    def reject(self) -> None:  # type: ignore[override]
        self._stop_background_work()
        super().reject()

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
        grad.setColorAt(0.48, self._theme.color("panel_mid"))
        grad.setColorAt(1.0, self._theme.color("panel_bottom"))
        painter.fillPath(path, grad)
        painter.setPen(QPen(self._theme.color("border", 74), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        banner = QRect(20, 18, self.width() - 46, 42)
        banner_path = QPainterPath()
        banner_path.addRoundedRect(QRectF(banner), 15, 15)
        banner_grad = QLinearGradient(banner.topLeft(), banner.bottomRight())
        banner_grad.setColorAt(0, self._theme.color("surface_soft", 232))
        banner_grad.setColorAt(1, self._theme.color("surface", 238))
        painter.fillPath(banner_path, banner_grad)
        painter.setFont(QFont("Microsoft YaHei UI", 12, QFont.Weight.Black))
        painter.setPen(self._theme.color("text"))
        painter.drawText(banner.adjusted(14, 0, -54, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "个人持仓")

        header_y = 161
        header_font = QFont("Microsoft YaHei UI", 8, QFont.Weight.Black)
        painter.setFont(header_font)
        for text, key, x, width in self._header_specs():
            active = key == self._sort_key
            color = self._theme.color("text_soft") if active else self._theme.color("muted")
            align = Qt.AlignmentFlag.AlignLeft if key == "symbol" else Qt.AlignmentFlag.AlignCenter
            painter.setPen(color)
            painter.drawText(QRect(x, header_y, width, 18), align | Qt.AlignmentFlag.AlignVCenter, text)
            text_width = min(QFontMetrics(header_font).horizontalAdvance(text), max(10, width - 16))
            marker_x = x + text_width + 4 if key == "symbol" else x + (width + text_width) // 2 + 4
            self._draw_sort_marker(painter, marker_x, header_y + 4, active, self._sort_desc)
        painter.setPen(QPen(self._theme.color("border", 46), 1))
        painter.drawLine(24, 184, self.width() - 26, 184)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._handle_header_click(event.position().toPoint()):
                event.accept()
                return
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None

    def _header_specs(self) -> tuple[tuple[str, str, int, int], ...]:
        if self._is_activity_mode():
            return (
                ("币种 / 最后活跃", "symbol", 44, 190),
                ("方向", "side", 276, 56),
                ("交易金额", "cost_usd", 352, 80),
                ("数量", "token_amount", 454, 74),
                ("价格", "price_usd", 554, 74),
                ("链", "chain", 655, 46),
                ("时间", "timestamp", 778, 76),
            )
        return (
            ("币种 / 最后活跃", "symbol", 44, 190),
            ("持仓金额", "usd_value", 268, 90),
            ("未实现", "unrealized_profit", 364, 90),
            ("已实现", "realized_profit", 460, 90),
            ("总利润", "total_profit", 556, 90),
            ("平均买/卖市值", "avg_market_cap", 652, 150),
            ("持仓时长", "holding_duration_seconds", 808, 80),
            ("买/卖", "trade_count", 894, 64),
            ("时间", "last_active_timestamp", 964, 86),
        )

    def _handle_header_click(self, point) -> bool:
        if not 154 <= point.y() <= 184:
            return False
        for _text, key, x, width in self._header_specs():
            if QRect(x - 4, 154, width + 8, 30).contains(point):
                if self._sort_key == key:
                    self._sort_desc = not self._sort_desc
                else:
                    self._sort_key = key
                    self._sort_desc = key not in {"symbol", "side", "chain"}
                self._refresh_list()
                return True
        return False

    def _draw_sort_marker(self, painter: QPainter, x: int, y: int, active: bool, desc: bool) -> None:
        active_color = self._theme.color("text_soft", 210)
        idle_color = self._theme.color("muted", 95)
        dim_color = self._theme.color("muted", 50)
        up_color = active_color if active and not desc else idle_color if not active else dim_color
        down_color = active_color if active and desc else idle_color if not active else dim_color
        self._draw_triangle(painter, x, y, True, up_color)
        self._draw_triangle(painter, x, y + 8, False, down_color)

    def _draw_triangle(self, painter: QPainter, x: int, y: int, up: bool, color) -> None:
        path = QPainterPath()
        if up:
            path.moveTo(QPointF(x + 3.5, y))
            path.lineTo(QPointF(x + 7, y + 4))
            path.lineTo(QPointF(x, y + 4))
        else:
            path.moveTo(QPointF(x, y))
            path.lineTo(QPointF(x + 7, y))
            path.lineTo(QPointF(x + 3.5, y + 4))
        path.closeSubpath()
        painter.fillPath(path, color)

    def _sync_sort_mode(self) -> None:
        if self._is_activity_mode():
            valid = {"symbol", "side", "cost_usd", "token_amount", "price_usd", "chain", "timestamp"}
            if self._sort_key not in valid:
                self._sort_key = "timestamp"
                self._sort_desc = True
            return
        valid = {
            "symbol",
            "usd_value",
            "unrealized_profit",
            "realized_profit",
            "total_profit",
            "avg_market_cap",
            "holding_duration_seconds",
            "trade_count",
            "last_active_timestamp",
        }
        if self._sort_key not in valid:
            self._sort_key = "usd_value"
            self._sort_desc = True

    def _query(self) -> None:
        evm_address, sol_address = self._current_addresses()
        if not evm_address and not sol_address:
            self._refresh_timer.stop()
            self._holdings = []
            self._refresh_list()
            self.status_label.setText("共 0 条")
            return
        if self._worker and self._worker.isRunning():
            self._pending_query = True
            return
        if evm_address and (not evm_address.startswith("0x") or not possible_wallet_chains(evm_address)):
            self._refresh_timer.stop()
            self.status_label.setText("等待有效 EVM 地址")
            return
        if sol_address and possible_wallet_chains(sol_address) != ["sol"]:
            self._refresh_timer.stop()
            self.status_label.setText("等待有效 SOL 地址")
            return
        self.evm_address_edit.setText(evm_address)
        self.sol_address_edit.setText(sol_address)
        self.wallet_addresses_changed.emit(evm_address, sol_address)
        self._set_loading(True, "")
        worker = PortfolioWorker(self._api_key, self._api_host, evm_address, sol_address)
        worker.resolved.connect(self._on_resolved)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda worker=worker: self._clear_worker(worker))
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_resolved(self, wallet_label: str, holdings: object) -> None:
        if not isinstance(holdings, list):
            holdings = []
        self._holdings = [dict(item) for item in holdings if isinstance(item, dict)]
        self._last_wallet_label = wallet_label
        self._sync_sort_mode()
        self._preload_logos()
        self._refresh_list()
        self._set_loading(False, "")
        self.holdings_updated.emit(clean_cached_holdings(self._holdings))
        if self._expanded_token_key and self._drawer_source_row:
            self._start_token_activity_worker(self._drawer_source_row)
        if self._current_addresses() != ("", ""):
            self._refresh_timer.start()

    def _on_failed(self, message: str) -> None:
        if self._holdings:
            self._set_loading(False, "")
            return
        self._set_loading(False, message or "查询失败")

    def _clear_worker(self, worker: PortfolioWorker) -> None:
        if self._worker is worker:
            self._worker = None
        if self._pending_query:
            self._pending_query = False
            self._schedule_query()

    def _addresses_edited(self, _text: str = "") -> None:
        evm_address, sol_address = self._current_addresses()
        self._refresh_timer.stop()
        self.wallet_addresses_changed.emit(evm_address, sol_address)
        self._schedule_query()

    def _schedule_query(self) -> None:
        self._query_timer.start()

    def _current_addresses(self) -> tuple[str, str]:
        return (
            normalize_wallet_address(self.evm_address_edit.text()),
            normalize_wallet_address(self.sol_address_edit.text()),
        )

    def _set_loading(self, loading: bool, text: str) -> None:
        if loading and self._holdings:
            self.status_label.setText(self._count_text())
            return
        self.status_label.setText(text or self._count_text())

    def _refresh_list(self) -> None:
        self._sync_sort_mode()
        scroll_bar = self.list_widget.verticalScrollBar()
        scroll_value = scroll_bar.value()
        self.list_widget.clear()
        drawer_found = False
        for holding in self._sorted_holdings():
            self._add_list_row(holding)
            if self._expanded_token_key and self._token_key(holding) == self._expanded_token_key:
                drawer_found = True
                self._drawer_source_row = dict(holding)
                self._add_list_row(self._drawer_row())
        if self._expanded_token_key and not drawer_found:
            self._expanded_token_key = ""
            self._drawer_status = ""
            self._drawer_activities = []
            self._drawer_source_row = {}
        QTimer.singleShot(0, lambda value=scroll_value: self.list_widget.verticalScrollBar().setValue(value))
        self.status_label.setText(self._count_text())
        self.update()

    def _count_text(self) -> str:
        prefix = f"{self._last_wallet_label}  " if self._last_wallet_label else ""
        return f"{prefix}共 {len(self._holdings)} 条"

    def _sorted_holdings(self) -> list[dict[str, Any]]:
        rows = [dict(item) for item in self._holdings]
        key = self._sort_key
        if not rows:
            return rows
        if str(rows[0].get("row_type") or "") == "activity" and key not in {"symbol", "side", "cost_usd", "token_amount", "price_usd", "chain", "timestamp"}:
            key = "timestamp"
        elif str(rows[0].get("row_type") or "") != "activity" and key not in {
            "symbol",
            "usd_value",
            "unrealized_profit",
            "realized_profit",
            "total_profit",
            "holding_duration_seconds",
            "trade_count",
            "last_active_timestamp",
        }:
            key = "usd_value"
        return sorted(rows, key=lambda row: sort_value(row, key), reverse=self._sort_desc)

    def _is_activity_mode(self) -> bool:
        return bool(self._holdings and str(self._holdings[0].get("row_type") or "") == "activity")

    def _add_list_row(self, row: dict[str, Any]) -> None:
        item = QListWidgetItem()
        row_data = dict(row)
        item.setData(Qt.ItemDataRole.UserRole, row_data)
        item.setSizeHint(self._row_size(row_data))
        if str(row_data.get("row_type") or "") == "trade_drawer":
            item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list_widget.addItem(item)

    def _row_size(self, row: dict[str, Any]) -> QSize:
        if str(row.get("row_type") or "") == "trade_drawer":
            activities = row.get("activities") if isinstance(row.get("activities"), list) else []
            return QSize(1042, max(70, min(446, 40 + len(activities[:20]) * 20)))
        return QSize(1042, 70)

    def _toggle_token_drawer(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.ItemDataRole.UserRole) or {}
        if not isinstance(row, dict):
            return
        if str(row.get("row_type") or "") == "trade_drawer":
            return
        key = self._token_key(row)
        if not key:
            self.status_label.setText("缺少代币地址，无法查询交易")
            return
        if key == self._expanded_token_key:
            self._expanded_token_key = ""
            self._drawer_status = ""
            self._drawer_activities = []
            self._drawer_source_row = {}
            self._refresh_list()
            return
        self._expanded_token_key = key
        self._drawer_source_row = dict(row)
        cached = self._trade_cache.get(key, [])
        self._drawer_activities = [dict(item) for item in cached]
        self._drawer_status = self._trade_cache_status.get(key) or (f"最近 {len(cached)} 条交易" if cached else "暂无明细，后台更新")
        self._refresh_list()
        self._start_token_activity_worker(row)

    def _start_token_activity_worker(self, row: dict[str, Any]) -> None:
        key = self._token_key(row)
        if not key:
            return
        for worker in self._token_activity_workers:
            if worker.isRunning() and self._token_key(worker.row) == key:
                return
        worker = TokenActivityWorker(self._api_key, self._api_host, row)
        worker.resolved.connect(self._show_token_trades)
        worker.failed.connect(self._show_token_trade_error)
        worker.finished.connect(lambda worker=worker: self._clear_token_activity_worker(worker))
        worker.finished.connect(worker.deleteLater)
        self._token_activity_workers.append(worker)
        worker.start()

    def _show_token_trades(self, row: object, activities: object) -> None:
        if not isinstance(row, dict) or self._token_key(row) != self._expanded_token_key:
            return
        key = self._token_key(row)
        rows = [dict(item) for item in activities if isinstance(item, dict)] if isinstance(activities, list) else []
        self._trade_cache[key] = [dict(item) for item in rows]
        self._trade_cache_status[key] = f"最近 {len(rows)} 条交易" if rows else "没有该币种交易记录"
        self._drawer_activities = rows
        self._drawer_status = self._trade_cache_status[key]
        self._refresh_list()

    def _show_token_trade_error(self, row: object, message: str) -> None:
        if not isinstance(row, dict) or self._token_key(row) != self._expanded_token_key:
            return
        key = self._token_key(row)
        cached = self._trade_cache.get(key, [])
        if cached:
            self._drawer_activities = [dict(item) for item in cached]
            self._drawer_status = self._trade_cache_status.get(key) or f"最近 {len(cached)} 条交易"
            self._refresh_list()
            return
        if self._drawer_activities:
            self._drawer_status = f"最近 {len(self._drawer_activities)} 条交易"
            return
        self._drawer_status = message or "查询失败"
        self._refresh_list()

    def _clear_token_activity_worker(self, worker: TokenActivityWorker) -> None:
        if worker in self._token_activity_workers:
            self._token_activity_workers.remove(worker)

    def _drawer_row(self) -> dict[str, Any]:
        row = dict(self._drawer_source_row)
        row["row_type"] = "trade_drawer"
        row["status"] = self._drawer_status
        row["activities"] = [dict(item) for item in self._drawer_activities]
        return row

    def _token_key(self, row: dict[str, Any]) -> str:
        chain = str(row.get("chain") or "").lower().strip()
        wallet_address = normalize_wallet_address(str(row.get("wallet_address") or ""))
        token_address = str(row.get("token_address") or "").lower().strip()
        if not chain or not wallet_address or not token_address:
            return ""
        return f"{chain}:{wallet_address.lower()}:{token_address}"

    def _preload_logos(self) -> None:
        for holding in self._holdings:
            url = str(holding.get("logo_url") or "").strip()
            if not url or holding.get("_logo_pixmap") is not None or url in self._logo_loaders:
                continue
            loader = LogoLoader(url, 34)
            loader.loaded.connect(self._on_logo_loaded)
            loader.failed.connect(self._on_logo_failed)
            loader.finished.connect(loader.deleteLater)
            self._logo_loaders[url] = loader
            loader.start()

    def _on_logo_loaded(self, url: str, pixmap: object) -> None:
        self._logo_loaders.pop(url, None)
        for holding in self._holdings:
            if str(holding.get("logo_url") or "") == url:
                holding["_logo_pixmap"] = pixmap
        self._refresh_list()

    def _on_logo_failed(self, url: str) -> None:
        self._logo_loaders.pop(url, None)

    def _stop_background_work(self) -> None:
        self._query_timer.stop()
        self._refresh_timer.stop()
        if self._worker and self._worker.isRunning():
            try:
                self._worker.resolved.disconnect()
                self._worker.failed.disconnect()
            except RuntimeError:
                pass
            self._worker.wait(6000)
        self._worker = None
        for worker in list(self._token_activity_workers):
            try:
                worker.resolved.disconnect()
                worker.failed.disconnect()
            except RuntimeError:
                pass
            if worker.isRunning():
                worker.wait(5000)
        self._token_activity_workers.clear()
        for loader in list(self._logo_loaders.values()):
            try:
                if loader.isRunning():
                    loader.loaded.disconnect()
                    loader.failed.disconnect()
                    loader.wait(2500)
            except RuntimeError:
                pass
        self._logo_loaders.clear()

    def _apply_styles(self) -> None:
        theme = self._theme
        self.status_label.setStyleSheet(
            'color: {text}; font: 800 11px "Microsoft YaHei UI";'.format(text=hex_rgb(theme.text_soft))
        )
        field_css = """
            QLineEdit {{
                background: {field_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 12px;
                padding: 8px 12px;
                font: 800 12px "Cascadia Mono";
                selection-color: {text};
                selection-background-color: {selection};
            }}
            QLineEdit:focus {{
                border: 1px solid {focus_border};
                background: {field_focus};
            }}
            QLineEdit:disabled {{
                color: {dim};
            }}
        """.format(
            field_bg=rgba(theme.field_bg, 242),
            text=hex_rgb(theme.text),
            border=rgba(theme.border, 78),
            selection=rgba(theme.accent, 145),
            focus_border=rgba(theme.border_hover, 160),
            field_focus=rgba(theme.field_focus, 248),
            dim=hex_rgb(theme.dim),
        )
        self.evm_address_edit.setStyleSheet(field_css)
        self.sol_address_edit.setStyleSheet(field_css)
        self.list_widget.setStyleSheet(
            """
            QListWidget {{
                background: transparent;
                border: 0;
                outline: 0;
            }}
            QListWidget::item {{
                min-height: 60px;
                border: 0;
            }}
            QListWidget::item:selected {{
                background: transparent;
            }}
            QScrollBar:horizontal {{
                height: 0px;
                background: transparent;
            }}
            QScrollBar:vertical {{
                width: 8px;
                background: transparent;
                margin: 8px 2px 8px 0px;
            }}
            QScrollBar::handle:vertical {{
                min-height: 28px;
                border-radius: 4px;
                background: {scroll};
            }}
            QScrollBar::handle:vertical:hover {{
                background: {scroll_hover};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                height: 0px;
                background: transparent;
            }}
            """.format(scroll=rgba(theme.muted, 115), scroll_hover=rgba(theme.accent, 165))
        )


def pnl_color(value: object):
    number = to_float(value)
    theme = active_theme()
    if number is None:
        return theme.color("text_soft")
    if number > 0:
        return theme.color("positive")
    if number < 0:
        return theme.color("negative")
    return theme.color("text_soft")


def format_money(value: object, signed: bool = True) -> str:
    number = to_float(value)
    if number is None:
        return "--"
    prefix = ""
    if signed:
        prefix = "+" if number > 0 else "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000_000:
        text = f"{number / 1_000_000_000:.2f}B"
    elif number >= 1_000_000:
        text = f"{number / 1_000_000:.2f}M"
    elif number >= 1_000:
        text = f"{number / 1_000:.2f}K"
    elif number >= 10:
        text = f"{number:.2f}"
    else:
        text = f"{number:.4f}".rstrip("0").rstrip(".")
    return f"{prefix}${text}"


def format_price(value: object) -> str:
    number = to_float(value)
    if number is None or number <= 0:
        return "--"
    if number >= 1:
        return f"${number:.4f}".rstrip("0").rstrip(".")
    if number >= 0.000001:
        return f"${number:.8f}".rstrip("0").rstrip(".")
    return f"${number:.2e}"


def format_duration(value: object, with_hand: bool = False) -> str:
    seconds = to_float(value)
    if seconds is None:
        return "--"
    seconds = max(0, int(seconds))
    day = 86400
    hour = 3600
    minute = 60
    if seconds >= day:
        text = f"{seconds // day}天"
    elif seconds >= hour:
        text = f"{seconds // hour}h"
    elif seconds >= minute:
        text = f"{seconds // minute}m"
    else:
        text = f"{seconds}s"
    if with_hand:
        text = f"{text} {holding_hand_emoji(seconds)}"
    return text


def format_count(value: object) -> str:
    number = to_float(value)
    if number is None:
        return "--"
    return str(max(0, int(number)))


def format_buy_sell_count(buy_value: object, sell_value: object) -> str:
    buy = to_float(buy_value)
    sell = to_float(sell_value)
    if buy is None and sell is None:
        return "--"
    return f"{int(buy or 0)}/{int(sell or 0)}"


def format_avg_market_cap(item: dict[str, Any]) -> str:
    buy = format_money(item.get("avg_buy_market_cap"), signed=False)
    sell = format_money(item.get("avg_sell_market_cap"), signed=False)
    if buy == "--" and sell == "--":
        return "--"
    return f"{buy}/{sell}"


def format_amount(value: object) -> str:
    number = to_float(value)
    if number is None:
        return "--"
    number = abs(number)
    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"{number / 1_000:.2f}K"
    if number >= 1:
        return f"{number:.3f}".rstrip("0").rstrip(".")
    return f"{number:.6f}".rstrip("0").rstrip(".") or "0"


def activity_market_cap(activity: dict[str, Any]) -> object:
    side = str(activity.get("side") or "").lower()
    if side == "sell":
        return activity.get("sell_market_cap") or activity.get("trade_market_cap") or activity.get("market_cap")
    if side == "buy":
        return activity.get("buy_market_cap") or activity.get("trade_market_cap") or activity.get("market_cap")
    return activity.get("trade_market_cap") or activity.get("market_cap")


def side_text(value: object) -> str:
    side = str(value or "").lower().strip()
    if side == "buy":
        return "买入"
    if side == "sell":
        return "卖出"
    return side.upper() if side else "--"


def relative_time(value: object) -> str:
    timestamp = normalize_timestamp(value)
    if not timestamp:
        return "--"
    delta = max(0, int(time.time()) - timestamp)
    if delta < 60:
        return f"{delta}s前"
    if delta < 3600:
        return f"{delta // 60}m前"
    if delta < 86400:
        return f"{delta // 3600}h前"
    if delta < 2592000:
        return f"{delta // 86400}d前"
    if delta < 31536000:
        return f"{delta // 2592000}mo前"
    return f"{delta // 31536000}y前"


def normalize_timestamp(value: object) -> int:
    number = to_float(value)
    if number is None or number <= 0:
        return 0
    while number > 10_000_000_000:
        number /= 1000.0
    return int(number)


def short_tx(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "--"
    if len(text) <= 14:
        return text
    return f"{text[:6]}...{text[-6:]}"


def holding_hand_emoji(seconds: int) -> str:
    return "💎" if seconds >= 86400 else "📄"


def sort_value(row: dict[str, Any], key: str):
    if key == "symbol":
        return str(row.get("symbol") or row.get("name") or "").lower()
    if key == "side":
        return str(row.get("side") or "").lower()
    if key == "chain":
        return str(row.get("chain") or "").lower()
    if key == "trade_count":
        buy = to_float(row.get("buy_count")) or 0.0
        sell = to_float(row.get("sell_count")) or 0.0
        return buy + sell
    if key == "avg_market_cap":
        buy = to_float(row.get("avg_buy_market_cap"))
        sell = to_float(row.get("avg_sell_market_cap"))
        values = [value for value in (buy, sell) if value is not None]
        return sum(values) / len(values) if values else -1.0
    number = to_float(row.get(key))
    if number is not None:
        return number
    return -1.0


def to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def short_address(address: str) -> str:
    if len(address) <= 13:
        return address
    return f"{address[:6]}...{address[-4:]}"


def _wallet_label(evm_address: str, sol_address: str) -> str:
    labels = []
    if evm_address:
        labels.append(f"EVM {short_address(evm_address)}")
    if sol_address:
        labels.append(f"SOL {short_address(sol_address)}")
    return " / ".join(labels) if labels else "钱包"


def clean_cached_holdings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    clean: list[dict[str, Any]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row.pop("_logo_pixmap", None)
        row.pop("raw", None)
        clean.append(row)
    return clean
