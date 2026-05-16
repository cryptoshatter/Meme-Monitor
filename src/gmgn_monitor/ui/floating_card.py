from __future__ import annotations

import math
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, QTimer, QUrl, Qt, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QBrush,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from gmgn_monitor.gmgn_client import TokenSnapshot, WalletActivitySnapshot
from gmgn_monitor.config import app_data_dir
from gmgn_monitor.ui.images import LogoLoader, avatar_pixmap, native_icon, token_fallback_logo


MIN_W = 292
MAX_W = 540
COLLAPSED_SIDE_MIN_W = 66
COLLAPSED_SIDE_MAX_W = 118
COLLAPSED_SIDE_MIN_H = 118
COLLAPSED_BAR_MIN_W = 292
COLLAPSED_BAR_MAX_W = 560
COLLAPSED_BAR_H = 46
CARD_H = 158
CARD_ALERT_H = 184
MARGIN = 9
PAD_X = 18
PAD_Y = 15
MARKET_SIGNAL_W = 32
EDGE_SNAP_PX = 34
EDGE_OVERDRAG_PX = 96
WALLET_HISTORY_LIMIT = 5


@dataclass(slots=True)
class DisplayData:
    address: str = ""
    symbol: str = "GMGN"
    name: str = "GMGN Monitor"
    chain: str = "SOL"
    price: float | None = None
    market_cap: float | None = None
    change_percent: float | None = None
    volume_24h: float | None = None
    logo_url: str = ""
    status: str = "Waiting"


@dataclass(slots=True)
class WalletDisplay:
    remark: str = ""
    wallet_address: str = ""
    chain: str = ""
    side: str = ""
    native_amount: float | None = None
    native_symbol: str = ""
    token_symbol: str = ""
    token_address: str = ""
    token_logo_url: str = ""
    timestamp: int | None = None
    tx_hash: str = ""
    avatar_kind: str = "emoji"
    avatar_value: str = ""


@dataclass(slots=True)
class TokenAlertDisplay:
    symbol: str = ""
    chain: str = ""
    address: str = ""
    logo_url: str = ""
    delta_percent: float | None = None
    threshold_percent: float | None = None
    market_cap: float | None = None
    price: float | None = None
    received_at: float = 0.0


class WalletActivityPanel(QWidget):
    token_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self._activities: list[WalletDisplay] = []
        self._row_rects: list[QRect] = []
        self._hover_row = -1
        self._resonance: dict[str, int] = {}
        self._token_logo_provider = None
        self.resize(316, 56)

    def set_token_logo_provider(self, provider) -> None:
        self._token_logo_provider = provider

    def set_activities(self, activities: list[WalletDisplay]) -> None:
        self._activities = list(activities[:5])
        self._resonance = self._build_resonance(self._activities)
        row_count = max(1, len(self._activities))
        self.resize(316, 34 + row_count * 36 + 8)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        panel = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(panel), 18, 18)
        base = QLinearGradient(panel.topLeft(), panel.bottomRight())
        base.setColorAt(0.0, QColor(17, 27, 27, 246))
        base.setColorAt(1.0, QColor(4, 8, 10, 252))
        painter.fillPath(path, base)
        painter.setPen(QPen(QColor(132, 151, 148, 76), 1.05))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Black))
        painter.setPen(QColor(228, 242, 238))
        painter.drawText(QRect(panel.left() + 14, panel.top() + 10, 120, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "钱包动态")
        painter.setFont(QFont("Segoe UI Variable Text", 7, QFont.Weight.Bold))
        painter.setPen(QColor(116, 134, 132))
        painter.drawText(QRect(panel.right() - 92, panel.top() + 10, 78, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "LATEST 5")

        self._row_rects = []
        if not self._activities:
            painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
            painter.setPen(QColor(122, 138, 136))
            painter.drawText(panel.adjusted(14, 36, -14, -12), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "暂无钱包动态")
            return

        row_top = panel.top() + 36
        for index, activity in enumerate(self._activities):
            row = QRect(panel.left() + 8, row_top + index * 36, panel.width() - 16, 34)
            self._row_rects.append(row)
            self._draw_row(painter, row, activity, index)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = event.position().toPoint()
        hover = -1
        for index, rect in enumerate(self._row_rects):
            if rect.contains(point):
                hover = index
                break
        if hover != self._hover_row:
            self._hover_row = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position().toPoint()
        for index, rect in enumerate(self._row_rects):
            if rect.contains(point) and index < len(self._activities):
                activity = self._activities[index]
                if activity.token_address:
                    self.token_requested.emit(activity.chain, activity.token_address)
                    self.hide()
                return

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover_row = -1
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _draw_row(self, painter: QPainter, rect: QRect, activity: WalletDisplay, index: int) -> None:
        side_color = QColor(48, 235, 137) if activity.side == "buy" else QColor(255, 82, 103)
        if index == self._hover_row:
            hover = QColor(side_color)
            hover.setAlpha(22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover)
            painter.drawRoundedRect(rect, 12, 12)

        avatar_rect = QRect(rect.left() + 7, rect.top() + 8, 18, 18)
        painter.drawPixmap(avatar_rect, avatar_pixmap(activity.avatar_kind, activity.avatar_value, 18))

        time_text = format_relative_time(activity.timestamp)
        painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold))
        time_w = QFontMetrics(painter.font()).horizontalAdvance(time_text) + 6 if time_text else 0
        resonance_text = self._resonance_text(activity)
        resonance_w = 0
        if resonance_text:
            resonance_w = QFontMetrics(painter.font()).horizontalAdvance(resonance_text) + 12

        right_reserved = time_w + resonance_w + 8
        text_rect = QRect(rect.left() + 34, rect.top() + 1, max(40, rect.width() - 42 - right_reserved), 32)
        painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Black))
        painter.setPen(side_color)
        draw_wallet_activity_inline(
            painter,
            text_rect,
            activity,
            side_color,
            self._token_logo_provider,
            14,
            9,
        )

        x = rect.right() - time_w - resonance_w - 6
        if resonance_text:
            tag = QRect(x, rect.top() + 8, resonance_w - 6, 18)
            tag_color = QColor(35, 115, 83, 150)
            painter.setPen(QPen(QColor(73, 238, 158, 75), 1))
            painter.setBrush(tag_color)
            painter.drawRoundedRect(tag, 8, 8)
            painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.Black))
            painter.setPen(QColor(166, 255, 214))
            painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, resonance_text)
            x += resonance_w
        if time_text:
            painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold))
            painter.setPen(QColor(136, 153, 150))
            painter.drawText(QRect(rect.right() - time_w - 2, rect.top() + 8, time_w, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, time_text)

    def _resonance_text(self, activity: WalletDisplay) -> str:
        key = f"{activity.chain}:{activity.token_address}".lower()
        count = self._resonance.get(key, 0)
        if activity.side == "buy" and count >= 2:
            return f"共振 x{count}"
        return ""

    @staticmethod
    def _build_resonance(activities: list[WalletDisplay]) -> dict[str, int]:
        wallets_by_token: dict[str, set[str]] = {}
        for activity in activities:
            if activity.side != "buy" or not activity.token_address:
                continue
            key = f"{activity.chain}:{activity.token_address}".lower()
            wallets_by_token.setdefault(key, set()).add(activity.wallet_address or activity.remark)
        return {key: len(wallets) for key, wallets in wallets_by_token.items()}


def wallet_activity_full_text(activity: WalletDisplay) -> str:
    amount = format_native_amount(activity.native_amount, activity.native_symbol)
    side = "BUY" if activity.side == "buy" else "SELL"
    token = f" {activity.token_symbol}" if activity.token_symbol else ""
    return f"{activity.remark} {side}{token} {amount}".strip()


def wallet_display_text(activity: WalletDisplay, painter: QPainter, width: int) -> str:
    text = wallet_activity_full_text(activity)
    metrics = QFontMetrics(painter.font())
    if metrics.horizontalAdvance(text) <= width:
        return text
    amount = format_native_amount(activity.native_amount, activity.native_symbol)
    side = "BUY" if activity.side == "buy" else "SELL"
    token = f" {activity.token_symbol}" if activity.token_symbol else ""
    suffix = f"{side}{token} {amount}".strip()
    suffix_w = metrics.horizontalAdvance(suffix) + 5
    remark = metrics.elidedText(activity.remark, Qt.TextElideMode.ElideRight, max(0, width - suffix_w))
    return f"{remark} {suffix}".strip()


def wallet_activity_inline_width(activity: WalletDisplay, font: QFont, token_size: int = 14) -> int:
    metrics = QFontMetrics(font)
    amount = format_native_amount(activity.native_amount, activity.native_symbol)
    side = "BUY" if activity.side == "buy" else "SELL"
    token = (activity.token_symbol or "").strip()
    text = f"{activity.remark} {side} {token} {amount}".strip()
    icon_w = token_size + 5 if token else 0
    return metrics.horizontalAdvance(text) + icon_w + 8


def draw_wallet_activity_inline(
    painter: QPainter,
    rect: QRect,
    activity: WalletDisplay,
    color: QColor,
    token_logo_provider=None,
    token_size: int = 14,
    chain_size: int = 9,
) -> None:
    metrics = QFontMetrics(painter.font())
    amount = format_native_amount(activity.native_amount, activity.native_symbol)
    side = "BUY" if activity.side == "buy" else "SELL"
    token = (activity.token_symbol or "").strip()
    remark = (activity.remark or "Wallet").strip()
    gap = 4
    icon_w = token_size + 5 if token else 0
    fixed_w = metrics.horizontalAdvance(side) + gap + icon_w + metrics.horizontalAdvance(f"{token} {amount}".strip()) + 4
    remark_w = max(0, rect.width() - fixed_w - gap)
    remark_text = metrics.elidedText(remark, Qt.TextElideMode.ElideRight, remark_w)
    x = rect.left()
    y = rect.center().y()

    painter.setPen(color)
    if remark_text:
        painter.drawText(QRect(x, rect.top(), remark_w, rect.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, remark_text)
        x += metrics.horizontalAdvance(remark_text) + gap

    side_w = metrics.horizontalAdvance(side)
    painter.drawText(QRect(x, rect.top(), side_w + 2, rect.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, side)
    x += side_w + gap

    if token:
        logo_rect = QRect(x, y - token_size // 2, token_size, token_size)
        if token_logo_provider:
            pixmap = token_logo_provider(token, activity.chain, activity.token_logo_url, token_size)
        else:
            pixmap = token_fallback_logo(token, activity.chain, token_size)
        painter.drawPixmap(logo_rect, pixmap)
        painter.drawPixmap(
            QRect(logo_rect.right() - chain_size + 2, logo_rect.bottom() - chain_size + 2, chain_size, chain_size),
            native_icon(activity.chain, chain_size),
        )
        x = logo_rect.right() + 5

    tail = f"{token} {amount}".strip() if token else amount
    tail_rect = QRect(x, rect.top(), max(0, rect.right() - x + 1), rect.height())
    painter.drawText(tail_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elide_by_width(painter, tail, tail_rect.width()))


def wallet_history_path() -> Path:
    return app_data_dir() / "wallet_activity_history.json"


class FloatingCard(QWidget):
    position_changed = Signal(int, int)
    menu_requested = Signal(object)
    monitor_token_requested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GMGN Meme Monitor")
        self.setFixedSize(MIN_W, CARD_H)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)

        self._data = DisplayData()
        self._logo = token_fallback_logo(self._data.symbol, self._data.chain, 22)
        self._logo_url = ""
        self._logo_token_key = ""
        self._logo_loader: LogoLoader | None = None
        self._asset_logos: dict[str, object] = {}
        self._asset_logo_loaders: dict[str, LogoLoader] = {}
        self._drag_origin: QPoint | None = None
        self._locked = False
        self._hover = False
        self._flash = 0.0
        self._flip = 0.0
        self._direction = 0
        self._display_mc = 0.0
        self._target_mc = 0.0
        self._display_price = 0.0
        self._target_price = 0.0
        self._has_price = False
        self._old_mc_text = "--"
        self._new_mc_text = "--"
        self._old_price_text = "$--"
        self._new_price_text = "$--"
        self._wallet = WalletDisplay()
        self._wallet_history: list[WalletDisplay] = []
        self._token_alert = TokenAlertDisplay()
        self._token_alert_flash = 0.0
        self._wallet_flash = 0.0
        self._wallet_direction = 0
        self._live_pulse = 0.0
        self._wallet_rect = QRect()
        self._token_alert_rect = QRect()
        self._token_rect = QRect()
        self._hover_wallet = False
        self._hover_token_alert = False
        self._hover_token = False
        self._press_pos: QPoint | None = None
        self._dragging = False
        self._collapsed = False
        self._collapsed_edge = ""
        self._collapsed_anchor = QPoint()
        self._expanded_geometry = QRect(0, 0, MIN_W, CARD_H)
        self._expanded_width = MIN_W
        self._dock_anim_start = QRect()
        self._dock_anim_end = QRect()
        self._dock_target_collapsed = False
        self._dock_target_edge = ""
        self._dock_anim = QVariantAnimation(self)
        self._dock_anim.setDuration(260)
        self._dock_anim.setStartValue(0.0)
        self._dock_anim.setEndValue(1.0)
        self._dock_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._dock_anim.valueChanged.connect(self._on_dock_anim)
        self._dock_anim.finished.connect(self._on_dock_anim_finished)

        self._activity_panel = WalletActivityPanel()
        self._activity_panel.set_token_logo_provider(self._token_logo_pixmap)
        self._activity_panel.token_requested.connect(self._open_gmgn_token)
        self._load_wallet_history()

        self._mc_anim = QVariantAnimation(self)
        self._mc_anim.setDuration(380)
        self._mc_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._mc_anim.valueChanged.connect(self._on_mc_anim)

        self._price_anim = QVariantAnimation(self)
        self._price_anim.setDuration(340)
        self._price_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._price_anim.valueChanged.connect(self._on_price_anim)

        self._flip_anim = QVariantAnimation(self)
        self._flip_anim.setDuration(360)
        self._flip_anim.setStartValue(1.0)
        self._flip_anim.setEndValue(0.0)
        self._flip_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._flip_anim.valueChanged.connect(self._on_flip)

        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setDuration(420)
        self._flash_anim.setStartValue(0.95)
        self._flash_anim.setEndValue(0.0)
        self._flash_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._flash_anim.valueChanged.connect(self._on_flash)

        self._wallet_flash_anim = QVariantAnimation(self)
        self._wallet_flash_anim.setDuration(560)
        self._wallet_flash_anim.setStartValue(1.0)
        self._wallet_flash_anim.setEndValue(0.0)
        self._wallet_flash_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._wallet_flash_anim.valueChanged.connect(self._on_wallet_flash)

        self._token_alert_anim = QVariantAnimation(self)
        self._token_alert_anim.setDuration(2600)
        self._token_alert_anim.setStartValue(1.0)
        self._token_alert_anim.setEndValue(0.0)
        self._token_alert_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._token_alert_anim.valueChanged.connect(self._on_token_alert_flash)

        self._live_anim = QVariantAnimation(self)
        self._live_anim.setDuration(1550)
        self._live_anim.setStartValue(0.0)
        self._live_anim.setEndValue(1.0)
        self._live_anim.setLoopCount(-1)
        self._live_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._live_anim.valueChanged.connect(self._on_live_pulse)
        self._live_anim.start()

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._on_clock_tick)
        self._clock_timer.start()

    def set_locked(self, locked: bool) -> None:
        self._locked = locked

    def set_status(self, text: str) -> None:
        self._data.status = text
        self.update()

    def set_error(self, text: str) -> None:
        self._data.status = "Error"
        self.setToolTip(text)
        self.update()

    def update_snapshot(self, snap: TokenSnapshot) -> None:
        if snap.price is None and snap.market_cap is None and self._data.price is not None:
            self._data.status = "Live"
            self.update()
            return
        old_mc = self._target_mc
        old_price = self._target_price
        self._data = DisplayData(
            address=snap.address,
            symbol=snap.symbol,
            name=snap.name,
            chain=snap.chain.upper(),
            price=snap.price,
            market_cap=snap.market_cap,
            change_percent=snap.change_percent,
            volume_24h=snap.volume_24h,
            logo_url=snap.logo_url,
            status="Live",
        )
        token_key = f"{snap.chain}:{snap.address}:{snap.symbol}"
        if token_key != self._logo_token_key:
            self._logo_token_key = token_key
            self._logo = token_fallback_logo(snap.symbol, snap.chain, 22)
        if snap.logo_url != self._logo_url:
            self._logo_url = snap.logo_url
            self._start_logo_load(snap.logo_url)
        self._target_mc = snap.market_cap or 0.0
        self._target_price = snap.price or 0.0

        next_mc_text = format_market_cap(self._target_mc)
        next_price_text = format_price(self._target_price) if snap.price is not None else "$--"
        changed = False

        if old_mc <= 0 and self._target_mc > 0:
            self._display_mc = self._target_mc
            self._new_mc_text = next_mc_text
        elif self._target_mc != old_mc:
            changed = True
            self._direction = 1 if self._target_mc > old_mc else -1
            self._old_mc_text = self._new_mc_text
            self._new_mc_text = next_mc_text
            self._mc_anim.stop()
            self._mc_anim.setStartValue(float(self._display_mc))
            self._mc_anim.setEndValue(float(self._target_mc))
            self._mc_anim.start()

        if not self._has_price and self._target_price > 0:
            self._display_price = self._target_price
            self._has_price = True
            self._new_price_text = next_price_text
        elif self._target_price != old_price:
            changed = True
            self._old_price_text = self._new_price_text
            self._new_price_text = next_price_text
            if old_mc <= 0:
                self._direction = 1 if self._target_price > old_price else -1
            self._price_anim.stop()
            self._price_anim.setStartValue(float(self._display_price))
            self._price_anim.setEndValue(float(self._target_price))
            self._price_anim.start()

        self._resize_for_content()
        if changed:
            self._flip_anim.stop()
            self._flip_anim.start()
            self._flash_anim.stop()
            self._flash_anim.start()
        self.update()

    def update_wallet_activity(self, snap: WalletActivitySnapshot | None) -> None:
        if snap is None:
            self._wallet = WalletDisplay()
            self._activity_panel.hide()
            self._resize_for_content()
            self.update()
            return
        if not snap.side:
            return
        old_key = f"{self._wallet.tx_hash}:{self._wallet.side}:{self._wallet.native_amount}"
        new_key = f"{snap.tx_hash}:{snap.side}:{snap.native_amount}"
        self._wallet = WalletDisplay(
            remark=snap.remark or "Wallet",
            wallet_address=snap.wallet_address,
            chain=snap.chain,
            side=snap.side,
            native_amount=snap.native_amount,
            native_symbol=snap.native_symbol,
            token_symbol=snap.token_symbol,
            token_address=snap.token_address,
            token_logo_url=getattr(snap, "token_logo_url", ""),
            timestamp=snap.timestamp,
            tx_hash=snap.tx_hash,
            avatar_kind=snap.avatar_kind,
            avatar_value=snap.avatar_value,
        )
        self._ensure_asset_logo(self._wallet.token_logo_url)
        self._remember_wallet_activity(self._wallet)
        self._wallet_direction = 1 if snap.side == "buy" else -1
        self._resize_for_content()
        if new_key != old_key:
            self._wallet_flash_anim.stop()
            self._wallet_flash_anim.start()
        self.update()

    def update_token_alert(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        chain = str(payload.get("chain") or "").lower().strip()
        address = str(payload.get("address") or "").strip()
        if not chain or not address:
            return
        if f"{chain}:{address}".lower() == f"{self._data.chain}:{self._data.address}".lower():
            return
        self._token_alert = TokenAlertDisplay(
            symbol=str(payload.get("symbol") or "TOKEN").strip()[:18] or "TOKEN",
            chain=chain,
            address=address,
            logo_url=str(payload.get("logo_url") or "").strip(),
            delta_percent=to_float_or_none(payload.get("delta_percent")),
            threshold_percent=to_float_or_none(payload.get("threshold_percent")),
            market_cap=to_float_or_none(payload.get("market_cap")),
            price=to_float_or_none(payload.get("price")),
            received_at=float(payload.get("received_at") or time.time()),
        )
        self._ensure_asset_logo(self._token_alert.logo_url)
        self._resize_for_content()
        self._token_alert_anim.stop()
        self._token_alert_anim.start()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect().adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        self._draw_shadow(painter, rect)
        self._draw_card(painter, rect)
        if self._collapsed:
            self._draw_collapsed_content(painter, rect)
        else:
            self._draw_content(painter, rect)
        if self._has_active_token_alert() and self._token_alert_flash > 0:
            self._draw_card_warning_notice(painter, rect)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragging = False
            if not self._locked:
                self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None and not self._locked:
            if self._press_pos is not None:
                distance = (event.position().toPoint() - self._press_pos).manhattanLength()
                if distance < 4:
                    return
            self._dragging = True
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            return
        if self._collapsed:
            self._wallet_rect = QRect()
            self._token_alert_rect = QRect()
            self._token_rect = QRect()
            self._hover_wallet = False
            self._hover_token_alert = False
            self._hover_token = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.update()
            return
        hover_wallet = self._wallet_rect.contains(event.position().toPoint()) and bool(self._wallet.token_address)
        hover_alert = self._token_alert_rect.contains(event.position().toPoint()) and self._has_active_token_alert()
        hover_token = self._token_rect.contains(event.position().toPoint()) and bool(self._data.address)
        if hover_wallet != self._hover_wallet or hover_alert != self._hover_token_alert or hover_token != self._hover_token:
            self._hover_wallet = hover_wallet
            self._hover_token_alert = hover_alert
            self._hover_token = hover_token
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover_wallet or hover_alert or hover_token else Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            was_dragging = self._dragging
            had_drag_origin = self._drag_origin is not None
            self._drag_origin = None
            self._press_pos = None
            self._dragging = False
            if self._collapsed:
                if was_dragging:
                    if self._maybe_collapse_to_edge():
                        return
                    self._expand_from_current_position()
                    return
                self._expand_from_edge()
                return
            if not was_dragging and self._wallet_rect.contains(point) and self._wallet_history:
                self._show_wallet_activity_panel()
                return
            if not was_dragging and self._token_alert_rect.contains(point) and self._has_active_token_alert():
                self.monitor_token_requested.emit(self._token_alert.chain, self._token_alert.address)
                return
            if not was_dragging and self._token_rect.contains(point) and self._data.address:
                self._open_gmgn_token(self._data.chain, self._data.address)
                return
            if was_dragging:
                if self._maybe_collapse_to_edge():
                    return
                pos = self.pos()
                self.position_changed.emit(pos.x(), pos.y())
                return
            if had_drag_origin:
                pos = self.pos()
                self.position_changed.emit(pos.x(), pos.y())
                return
            return
        if self._drag_origin is not None:
            self._drag_origin = None
            self._press_pos = None
            self._dragging = False
            pos = self.pos()
            self.position_changed.emit(pos.x(), pos.y())

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover = False
        self._hover_wallet = False
        self._hover_token_alert = False
        self._hover_token = False
        self._press_pos = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _on_mc_anim(self, value) -> None:
        self._display_mc = float(value)
        self.update()

    def _on_price_anim(self, value) -> None:
        self._display_price = float(value)
        self.update()

    def _on_flash(self, value) -> None:
        self._flash = float(value)
        self.update()

    def _on_wallet_flash(self, value) -> None:
        self._wallet_flash = float(value)
        self.update()

    def _on_token_alert_flash(self, value) -> None:
        self._token_alert_flash = float(value)
        self.update()

    def _on_live_pulse(self, value) -> None:
        self._live_pulse = float(value)
        self.update()

    def _on_clock_tick(self) -> None:
        if self._wallet.timestamp or self._token_alert.address:
            self._resize_for_content()
            self.update()

    def _on_flip(self, value) -> None:
        self._flip = float(value)
        self.update()

    def _remember_wallet_activity(self, activity: WalletDisplay) -> None:
        key = self._wallet_activity_key(activity)
        self._wallet_history = [
            item for item in self._wallet_history if self._wallet_activity_key(item) != key
        ]
        self._wallet_history.insert(0, activity)
        self._wallet_history.sort(key=lambda item: int(item.timestamp or 0), reverse=True)
        self._wallet_history = self._wallet_history[:WALLET_HISTORY_LIMIT]
        self._save_wallet_history()
        if self._activity_panel.isVisible():
            self._activity_panel.set_activities(self._wallet_history)

    def _wallet_activity_key(self, activity: WalletDisplay) -> str:
        return "|".join(
            [
                activity.tx_hash,
                activity.wallet_address,
                activity.chain,
                activity.side,
                activity.token_address,
                str(activity.timestamp or ""),
            ]
        )

    def _load_wallet_history(self) -> None:
        path = wallet_history_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, list):
            return
        history: list[WalletDisplay] = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            activity = self._wallet_display_from_dict(raw)
            if activity.side and (activity.token_address or activity.tx_hash):
                history.append(activity)
        history.sort(key=lambda item: int(item.timestamp or 0), reverse=True)
        self._wallet_history = history[:WALLET_HISTORY_LIMIT]
        if self._wallet_history:
            self._wallet = self._wallet_history[0]
            self._ensure_asset_logo(self._wallet.token_logo_url)
            self._activity_panel.set_activities(self._wallet_history)
            self._resize_for_content()

    def _save_wallet_history(self) -> None:
        path = wallet_history_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps([asdict(item) for item in self._wallet_history[:WALLET_HISTORY_LIMIT]], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)
        except Exception:
            return

    def _wallet_display_from_dict(self, raw: dict) -> WalletDisplay:
        timestamp = raw.get("timestamp")
        try:
            timestamp = int(timestamp) if timestamp is not None else None
        except (TypeError, ValueError):
            timestamp = None
        native_amount = raw.get("native_amount")
        try:
            native_amount = float(native_amount) if native_amount is not None else None
        except (TypeError, ValueError):
            native_amount = None
        side = str(raw.get("side") or "").lower().strip()
        if side not in {"buy", "sell"}:
            side = ""
        avatar_kind = str(raw.get("avatar_kind") or "emoji").lower().strip()
        if avatar_kind not in {"emoji", "image"}:
            avatar_kind = "emoji"
        return WalletDisplay(
            remark=str(raw.get("remark") or "Wallet").strip() or "Wallet",
            wallet_address=str(raw.get("wallet_address") or "").strip(),
            chain=str(raw.get("chain") or "").lower().strip(),
            side=side,
            native_amount=native_amount,
            native_symbol=str(raw.get("native_symbol") or "").strip(),
            token_symbol=str(raw.get("token_symbol") or "").strip(),
            token_address=str(raw.get("token_address") or "").strip(),
            token_logo_url=str(raw.get("token_logo_url") or "").strip(),
            timestamp=timestamp,
            tx_hash=str(raw.get("tx_hash") or "").strip(),
            avatar_kind=avatar_kind,
            avatar_value=str(raw.get("avatar_value") or "").strip(),
        )

    def _show_wallet_activity_panel(self) -> None:
        if not self._wallet_history:
            return
        self._activity_panel.set_activities(self._wallet_history)
        panel_size = self._activity_panel.size()
        screen = self._screen_geometry_for_card()
        below = self.mapToGlobal(QPoint(0, self.height() + 8))
        above = self.mapToGlobal(QPoint(0, -panel_size.height() - 8))
        x = clamp_int(self.geometry().right() - panel_size.width() + 1, screen.left(), screen.right() - panel_size.width() + 1)
        y = below.y()
        if y + panel_size.height() > screen.bottom():
            y = above.y()
        y = clamp_int(y, screen.top(), screen.bottom() - panel_size.height() + 1)
        self._activity_panel.move(x, y)
        self._activity_panel.show()
        self._activity_panel.raise_()

    def _on_dock_anim(self, value) -> None:
        progress = max(0.0, min(1.0, float(value)))
        start = self._dock_anim_start
        end = self._dock_anim_end
        if not start.isValid() or not end.isValid():
            return
        rect = QRect(
            round(start.x() + (end.x() - start.x()) * progress),
            round(start.y() + (end.y() - start.y()) * progress),
            round(start.width() + (end.width() - start.width()) * progress),
            round(start.height() + (end.height() - start.height()) * progress),
        )
        self.setFixedSize(rect.width(), rect.height())
        self.move(rect.topLeft())
        self.update()

    def _on_dock_anim_finished(self) -> None:
        self._collapsed = self._dock_target_collapsed
        self._collapsed_edge = self._dock_target_edge if self._collapsed else ""
        self._collapsed_anchor = self.pos() if self._collapsed else QPoint()
        self._wallet_rect = QRect()
        self._token_rect = QRect()
        self._hover_wallet = False
        self._hover_token = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        pos = self.pos()
        self.position_changed.emit(pos.x(), pos.y())
        self.update()

    def _screen_geometry_for_card(self) -> QRect:
        center = self.frameGeometry().center()
        screen = QGuiApplication.screenAt(center) or self.screen() or QGuiApplication.primaryScreen()
        if screen:
            return screen.availableGeometry()
        return QRect(0, 0, 1920, 1080)

    def _nearest_snap_edge(self, card: QRect, screen: QRect) -> str:
        distances: dict[str, int] = {
            "left": card.left() - screen.left(),
            "right": screen.right() - card.right(),
            "top": card.top() - screen.top(),
            "bottom": screen.bottom() - card.bottom(),
        }
        candidates: dict[str, int] = {}
        for edge, distance in distances.items():
            if distance <= EDGE_SNAP_PX and distance >= -EDGE_OVERDRAG_PX:
                candidates[edge] = abs(distance)
            elif distance < -EDGE_OVERDRAG_PX:
                candidates[edge] = 0
        if not candidates:
            return ""
        edge, _ = min(candidates.items(), key=lambda item: item[1])
        return edge

    def _collapsed_size_for_edge(self, edge: str) -> QSize:
        mc_text = self._new_mc_text or format_market_cap(self._display_mc)
        change_text = format_change(self._data.change_percent)
        symbol_text = self._data.symbol or "--"
        mc_font = self._collapsed_side_market_font() if edge in {"left", "right"} else self._collapsed_market_font()
        change_font = self._collapsed_side_change_font() if edge in {"left", "right"} else self._collapsed_change_font()
        symbol_font = self._collapsed_side_symbol_font() if edge in {"left", "right"} else self._collapsed_symbol_font()
        mc_w = QFontMetrics(mc_font).horizontalAdvance(mc_text)
        change_w = QFontMetrics(change_font).horizontalAdvance(change_text)
        trend_w = 14 if edge in {"left", "right"} else 18
        symbol_w = QFontMetrics(self._collapsed_symbol_font()).horizontalAdvance(symbol_text)
        if edge in {"top", "bottom"}:
            content_w = MARGIN * 2 + 12 + 18 + 8 + min(120, symbol_w + 4) + 13 + mc_w + 8 + 18 + 7 + change_w + 18 + 12
            if self._has_active_token_alert():
                content_w += self._collapsed_alert_inline_width() + 8
            return QSize(clamp_int(content_w, COLLAPSED_BAR_MIN_W, COLLAPSED_BAR_MAX_W), COLLAPSED_BAR_H + MARGIN * 2)
        symbol_w = QFontMetrics(symbol_font).horizontalAdvance(symbol_text)
        content_w = MARGIN * 2 + max(mc_w, change_w + trend_w + 4, min(74, symbol_w), 18 + 9 + 8) + 16
        content_h = MARGIN * 2 + 9 + 18 + 7 + 16 + 7 + 22 + 5 + 17 + 9
        if self._has_active_token_alert():
            content_w = max(content_w, MARGIN * 2 + min(126, self._collapsed_alert_inline_width()) + 16)
            content_h += 38
        max_side_w = COLLAPSED_SIDE_MAX_W + (48 if self._has_active_token_alert() else 0)
        return QSize(clamp_int(content_w, COLLAPSED_SIDE_MIN_W, max_side_w), max(COLLAPSED_SIDE_MIN_H, content_h))

    def _collapsed_alert_inline_width(self) -> int:
        symbol = self._token_alert.symbol or "--"
        time_text = self._token_alert_time_text()
        symbol_w = QFontMetrics(self._collapsed_side_symbol_font()).horizontalAdvance(symbol)
        time_w = QFontMetrics(self._collapsed_side_time_font()).horizontalAdvance(time_text) if time_text else 0
        change_w = QFontMetrics(self._collapsed_change_font()).horizontalAdvance(format_change(self._token_alert.delta_percent))
        return clamp_int(24 + 16 + 6 + min(symbol_w, 92) + 5 + change_w + 17 + time_w + 8, 136, 210)

    def _collapsed_alert_size_for_edge(self, edge: str) -> QSize:
        value_text = self._token_alert_value_text()
        symbol_text = self._token_alert.symbol or "--"
        time_text = self._token_alert_time_text()
        if edge in {"top", "bottom"}:
            symbol_w = min(112, QFontMetrics(self._collapsed_symbol_font()).horizontalAdvance(symbol_text) + 4)
            value_w = QFontMetrics(self._collapsed_market_font()).horizontalAdvance(value_text)
            time_w = QFontMetrics(self._collapsed_time_font()).horizontalAdvance(time_text)
            content_w = MARGIN * 2 + 12 + 18 + 8 + symbol_w + 12 + value_w + 18 + max(30, time_w) + 12
            return QSize(clamp_int(content_w, COLLAPSED_BAR_MIN_W, COLLAPSED_BAR_MAX_W), COLLAPSED_BAR_H + MARGIN * 2)
        symbol_w = QFontMetrics(self._collapsed_side_symbol_font()).horizontalAdvance(symbol_text)
        value_w = QFontMetrics(self._collapsed_side_market_font()).horizontalAdvance(value_text)
        time_w = QFontMetrics(self._collapsed_side_time_font()).horizontalAdvance(time_text)
        row_1 = 18 + 7 + min(78, symbol_w)
        row_2 = value_w + 18 + (8 + time_w if time_text else 0)
        content_w = MARGIN * 2 + max(row_1, row_2) + 16
        content_h = MARGIN * 2 + 10 + 20 + 7 + 24 + 10
        return QSize(clamp_int(content_w, COLLAPSED_SIDE_MIN_W, COLLAPSED_SIDE_MAX_W + 36), max(74, content_h))

    def _collapsed_geometry(self, edge: str, screen: QRect, source: QRect | None = None) -> QRect:
        source = source or self.geometry()
        size = self._collapsed_size_for_edge(edge)
        width = size.width()
        height = size.height()
        x = source.x()
        y = source.y()
        if edge == "left":
            x = screen.left()
            y = clamp_int(source.center().y() - height // 2, screen.top(), screen.bottom() - height + 1)
        elif edge == "right":
            x = screen.right() - width + 1
            y = clamp_int(source.center().y() - height // 2, screen.top(), screen.bottom() - height + 1)
        elif edge == "top":
            y = screen.top()
            x = clamp_int(source.center().x() - width // 2, screen.left(), screen.right() - width + 1)
        elif edge == "bottom":
            y = screen.bottom() - height + 1
            x = clamp_int(source.center().x() - width // 2, screen.left(), screen.right() - width + 1)
        return QRect(x, y, width, height)

    def _collapsed_geometry_from_anchor(self, edge: str, screen: QRect, source: QRect | None = None) -> QRect:
        source = source or self.geometry()
        size = self._collapsed_size_for_edge(edge)
        width = size.width()
        height = size.height()
        anchor = self._collapsed_anchor if not self._collapsed_anchor.isNull() else source.topLeft()
        if edge == "left":
            x = screen.left()
            y = clamp_int(anchor.y(), screen.top(), screen.bottom() - height + 1)
        elif edge == "right":
            x = screen.right() - width + 1
            y = clamp_int(anchor.y(), screen.top(), screen.bottom() - height + 1)
        elif edge == "top":
            x = clamp_int(anchor.x(), screen.left(), screen.right() - width + 1)
            y = screen.top()
        elif edge == "bottom":
            x = clamp_int(anchor.x(), screen.left(), screen.right() - width + 1)
            y = screen.bottom() - height + 1
        else:
            x = source.x()
            y = source.y()
        return QRect(x, y, width, height)

    def _expanded_geometry_from_edge(self, edge: str, screen: QRect, source: QRect | None = None) -> QRect:
        source = source or self.geometry()
        width = max(MIN_W, int(self._expanded_width or MIN_W))
        target_h = self._target_card_height()
        if edge == "left":
            x = screen.left()
            y = clamp_int(source.center().y() - target_h // 2, screen.top(), screen.bottom() - target_h + 1)
        elif edge == "right":
            x = screen.right() - width + 1
            y = clamp_int(source.center().y() - target_h // 2, screen.top(), screen.bottom() - target_h + 1)
        elif edge == "top":
            x = clamp_int(source.center().x() - width // 2, screen.left(), screen.right() - width + 1)
            y = screen.top()
        elif edge == "bottom":
            x = clamp_int(source.center().x() - width // 2, screen.left(), screen.right() - width + 1)
            y = screen.bottom() - target_h + 1
        else:
            x = source.x()
            y = source.y()
        return QRect(x, y, width, target_h)

    def _expanded_geometry_from_current(self, source: QRect | None = None) -> QRect:
        source = source or self.geometry()
        width = max(MIN_W, int(self._expanded_width or MIN_W))
        target_h = self._target_card_height()
        if self._collapsed_edge == "right":
            x = source.right() - width + 1
        elif self._collapsed_edge in {"top", "bottom"}:
            x = source.center().x() - width // 2
        else:
            x = source.x()
        if self._collapsed_edge == "bottom":
            y = source.bottom() - target_h + 1
        elif self._collapsed_edge in {"left", "right"}:
            y = source.center().y() - target_h // 2
        else:
            y = source.y()
        return QRect(x, y, width, target_h)

    def _animate_geometry(self, start: QRect, end: QRect, collapsed: bool, edge: str) -> None:
        self._dock_anim.stop()
        self._dock_anim_start = QRect(start)
        self._dock_anim_end = QRect(end)
        self._dock_target_collapsed = collapsed
        self._dock_target_edge = edge
        if collapsed:
            self._collapsed = True
            self._collapsed_edge = edge
            self._wallet_rect = QRect()
            self._token_rect = QRect()
        else:
            self._collapsed = False
            self._collapsed_edge = ""
        self._dock_anim.start()

    def _maybe_collapse_to_edge(self) -> bool:
        screen = self._screen_geometry_for_card()
        geom = self.geometry()
        edge = self._nearest_snap_edge(geom, screen)
        if not edge:
            return False
        if not self._collapsed:
            self._expanded_width = max(MIN_W, self.width())
            self._expanded_geometry = QRect(geom)
        target = self._collapsed_geometry(edge, screen, geom)
        self._animate_geometry(geom, target, True, edge)
        return True

    def _expand_from_edge(self) -> None:
        screen = self._screen_geometry_for_card()
        edge = self._collapsed_edge or self._nearest_snap_edge(self.geometry(), screen)
        target = self._expanded_geometry_from_edge(edge, screen, self.geometry())
        self._animate_geometry(self.geometry(), target, False, "")

    def _expand_from_current_position(self) -> None:
        target = self._expanded_geometry_from_current(self.geometry())
        self._animate_geometry(self.geometry(), target, False, "")

    def _open_gmgn_token(self, chain: str, address: str) -> None:
        chain = gmgn_chain_slug(chain)
        address = str(address or "").strip()
        if not chain or not address:
            return
        QDesktopServices.openUrl(QUrl(f"https://gmgn.ai/{chain}/token/{address}"))

    def _has_active_token_alert(self) -> bool:
        return bool(self._token_alert.address)

    def _token_alert_text_full(self) -> str:
        delta = self._token_alert.delta_percent
        sign = "+" if delta is not None and delta > 0 else ""
        change = f"{sign}{delta:.2f}%" if delta is not None else "--"
        return f"{self._token_alert.symbol} {change}"

    def _token_alert_direction_text(self) -> str:
        return "\u4e0a\u6da8" if (self._token_alert.delta_percent or 0) >= 0 else "\u4e0b\u8dcc"

    def _token_alert_notice_text(self, compact: bool = False, tiny: bool = False) -> str:
        symbol = self._token_alert.symbol or "TOKEN"
        threshold = format_threshold_percent(self._token_alert.threshold_percent)
        if tiny:
            direction = "\u6da8" if (self._token_alert.delta_percent or 0) >= 0 else "\u8dcc"
            return f"{direction}{threshold}!"
        if compact:
            return f"{symbol} {self._token_alert_direction_text()} {threshold}!"
        return f"{symbol} {self._token_alert_direction_text()} {threshold} !!!"

    def _token_alert_value_text(self) -> str:
        return format_market_cap(self._token_alert.market_cap) if self._token_alert.market_cap else format_price(self._token_alert.price)

    def _token_alert_time_text(self) -> str:
        if not self._token_alert.received_at:
            return ""
        return format_relative_time(int(self._token_alert.received_at))

    def _collapsed_time_text(self) -> str:
        return self._token_alert_time_text() if self._has_active_token_alert() else ""

    def _start_logo_load(self, url: str) -> None:
        if not url:
            self._logo = token_fallback_logo(self._data.symbol, self._data.chain, 22)
            self.update()
            return
        if self._logo_loader:
            try:
                if self._logo_loader.isRunning():
                    self._logo_loader.loaded.disconnect()
                    self._logo_loader.failed.disconnect()
                    self._logo_loader.quit()
                    self._logo_loader.wait(50)
            except RuntimeError:
                pass
            self._logo_loader = None
        loader = LogoLoader(url, 22)
        loader.loaded.connect(self._on_logo_loaded)
        loader.failed.connect(self._on_logo_failed)
        loader.finished.connect(self._on_logo_loader_finished)
        loader.finished.connect(loader.deleteLater)
        self._logo_loader = loader
        loader.start()

    def _on_logo_loaded(self, url: str, pixmap) -> None:
        if url == self._logo_url:
            self._logo = pixmap
            self.update()

    def _on_logo_failed(self, url: str) -> None:
        if url == self._logo_url:
            self._logo = token_fallback_logo(self._data.symbol, self._data.chain, 22)
            self.update()

    def _on_logo_loader_finished(self) -> None:
        self._logo_loader = None

    def _asset_logo_key(self, url: str) -> str:
        return str(url or "").strip()

    def _ensure_asset_logo(self, url: str) -> None:
        key = self._asset_logo_key(url)
        if not key or key in self._asset_logos or key in self._asset_logo_loaders:
            return
        loader = LogoLoader(key, 16)
        loader.loaded.connect(self._on_asset_logo_loaded)
        loader.failed.connect(self._on_asset_logo_failed)
        loader.finished.connect(loader.deleteLater)
        self._asset_logo_loaders[key] = loader
        loader.start()

    def _on_asset_logo_loaded(self, url: str, pixmap) -> None:
        key = self._asset_logo_key(url)
        self._asset_logo_loaders.pop(key, None)
        self._asset_logos[key] = pixmap
        self.update()

    def _on_asset_logo_failed(self, url: str) -> None:
        self._asset_logo_loaders.pop(self._asset_logo_key(url), None)

    def _token_logo_pixmap(self, symbol: str, chain: str, url: str, size: int = 16):
        key = self._asset_logo_key(url)
        pixmap = self._asset_logos.get(key)
        if hasattr(pixmap, "isNull") and not pixmap.isNull():
            return pixmap
        if key:
            self._ensure_asset_logo(key)
        return token_fallback_logo(symbol, chain, size)

    def _resize_for_content(self) -> None:
        if self._collapsed:
            self._resize_collapsed_for_content()
            return
        if self._dock_anim.state() == QAbstractAnimation.State.Running:
            return
        mc_font = self._market_font()
        sub_font = self._sub_value_font()
        change_font = self._change_font()
        vol_font = self._volume_font()
        symbol_font = self._symbol_font()
        wallet_font = self._wallet_font()
        mc_w = QFontMetrics(mc_font).horizontalAdvance(self._new_mc_text)
        price_w = QFontMetrics(sub_font).horizontalAdvance(self._new_price_text)
        change_w = QFontMetrics(change_font).horizontalAdvance(format_change(self._data.change_percent))
        vol_w = max(
            QFontMetrics(vol_font).horizontalAdvance(format_volume(self._data.volume_24h)),
            QFontMetrics(self._label_font()).horizontalAdvance("VOL 24H"),
        )
        symbol_w = min(QFontMetrics(symbol_font).horizontalAdvance(self._data.symbol), 112)
        side_w = max(50, min(64, vol_w + 6))
        wallet_w = 0
        if self._wallet.side:
            time_w = QFontMetrics(self._wallet_time_font()).horizontalAdvance(format_relative_time(self._wallet.timestamp))
            wallet_w = wallet_activity_inline_width(self._wallet, wallet_font, 14) + 28 + max(0, time_w) + 10
        alert_w = 0
        needed = max(
            PAD_X * 2 + max(mc_w + 12 + MARKET_SIGNAL_W + 10 + side_w, price_w + 10 + change_w, wallet_w, alert_w),
            PAD_X * 2 + 22 + 8 + symbol_w + 8 + 38 + 40,
        )
        new_w = max(MIN_W, min(MAX_W, needed + MARGIN * 2))
        new_h = self._target_card_height()
        if abs(new_w - self.width()) >= 4 or new_h != self.height():
            old_pos = self.pos()
            self.setFixedSize(int(new_w), new_h)
            self._expanded_width = int(new_w)
            screen = self._screen_geometry_for_card()
            x = clamp_int(old_pos.x(), screen.left(), screen.right() - self.width() + 1)
            y = clamp_int(old_pos.y(), screen.top(), screen.bottom() - self.height() + 1)
            if x != old_pos.x() or y != old_pos.y():
                self.move(x, y)

    def _target_card_height(self) -> int:
        return CARD_ALERT_H if self._wallet.side and self._has_active_token_alert() else CARD_H

    def _resize_collapsed_for_content(self) -> None:
        if self._dock_anim.state() == QAbstractAnimation.State.Running:
            return
        edge = self._collapsed_edge
        if not edge:
            return
        current = self.geometry()
        screen = self._screen_geometry_for_card()
        target = self._collapsed_geometry_from_anchor(edge, screen, current)
        if target.size() == current.size() and target.topLeft() == current.topLeft():
            return
        self.setFixedSize(target.size())
        self.move(target.topLeft())

    def sizeHint(self) -> QSize:
        return QSize(self.width(), self.height())

    def _draw_shadow(self, painter: QPainter, rect: QRect) -> None:
        return

    def _draw_card(self, painter: QPainter, rect: QRect) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 23, 23)

        base = QLinearGradient(rect.topLeft(), rect.bottomRight())
        base.setColorAt(0.0, QColor(18, 27, 27, 248))
        base.setColorAt(0.48, QColor(8, 13, 15, 246))
        base.setColorAt(1.0, QColor(4, 7, 9, 252))
        painter.fillPath(path, base)

        glow = QRadialGradient(rect.left() + 34, rect.top() + 24, 112)
        glow.setColorAt(0.0, QColor(42, 226, 144, 16))
        glow.setColorAt(0.56, QColor(42, 226, 144, 5))
        glow.setColorAt(1.0, QColor(42, 226, 144, 0))
        painter.fillPath(path, glow)

        if self._has_active_token_alert() and self._token_alert_flash > 0:
            self._draw_card_warning_effect(painter, rect, path)

        border = QColor(132, 151, 148, 72)
        if self._hover:
            border = QColor(82, 239, 164, 132)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 1.05))
        painter.drawPath(path)

        if self._flash > 0:
            color = QColor(48, 241, 146, int(95 * self._flash))
            if self._direction < 0:
                color = QColor(255, 78, 101, int(95 * self._flash))
            painter.setPen(QPen(color, 2.0))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 22, 22)

    def _draw_card_warning_effect(self, painter: QPainter, rect: QRect, path: QPainterPath) -> None:
        pulse = max(0.0, min(1.0, self._token_alert_flash))
        progress = 1.0 - pulse
        flicker = 0.78 + 0.22 * abs(math.sin(progress * 42.0))
        strength = alert_train_opacity(progress) * flicker
        if strength <= 0.01:
            return

        painter.save()
        painter.setClipPath(path)
        is_up = (self._token_alert.delta_percent or 0) >= 0
        hot = QColor(48, 235, 137) if is_up else QColor(255, 82, 103)
        warm = QColor(90, 255, 185) if is_up else QColor(255, 193, 64)
        wash = QLinearGradient(rect.topLeft(), rect.topRight())
        wash.setColorAt(0.0, QColor(hot.red(), hot.green(), hot.blue(), int(48 * strength)))
        wash.setColorAt(0.48, QColor(14, 9, 10, int(18 * strength)))
        wash.setColorAt(1.0, QColor(hot.red(), hot.green(), hot.blue(), int(34 * strength)))
        painter.fillPath(path, wash)

        train_w = max(108, int(rect.width() * 0.64))
        train_x = int(alert_train_position(progress, rect.left() - train_w - 8, rect.center().x() - train_w // 2, rect.right() + 8))
        train_rect = QRect(train_x, rect.top(), train_w, rect.height())
        train_grad = QLinearGradient(train_rect.left(), 0, train_rect.right(), 0)
        train_grad.setColorAt(0.0, QColor(255, 82, 103, 0))
        train_grad.setColorAt(0.18, QColor(warm.red(), warm.green(), warm.blue(), int(52 * strength)))
        train_grad.setColorAt(0.52, QColor(hot.red(), hot.green(), hot.blue(), int(82 * strength)))
        train_grad.setColorAt(0.86, QColor(warm.red(), warm.green(), warm.blue(), int(44 * strength)))
        train_grad.setColorAt(1.0, QColor(255, 82, 103, 0))
        painter.fillRect(train_rect, train_grad)

        stripe = QColor(warm.red(), warm.green(), warm.blue(), int(38 + 78 * strength))
        painter.setPen(QPen(stripe, 2.4))
        shift = int(progress * 220) % 24
        for x in range(train_rect.left() - rect.height() + shift, train_rect.right() + rect.height(), 24):
            painter.drawLine(QPointF(x, rect.bottom()), QPointF(x + rect.height() * 0.85, rect.top()))

        scan_w = max(18, rect.width() // 7)
        scan_x = int(alert_train_position(progress, rect.left() - scan_w - 8, rect.center().x() - scan_w // 2, rect.right() + 8))
        scan = QRect(scan_x, rect.top(), scan_w, rect.height())
        scan_grad = QLinearGradient(scan.left(), 0, scan.right(), 0)
        scan_grad.setColorAt(0.0, QColor(255, 82, 103, 0))
        scan_grad.setColorAt(0.52, QColor(hot.red(), hot.green(), hot.blue(), int(70 * strength)))
        scan_grad.setColorAt(1.0, QColor(255, 82, 103, 0))
        painter.fillRect(scan, scan_grad)
        painter.restore()

        border = QColor(hot.red(), hot.green(), hot.blue(), int(120 + 115 * strength))
        painter.setPen(QPen(border, 1.8 + 1.2 * strength))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _draw_card_warning_notice(self, painter: QPainter, rect: QRect) -> None:
        pulse = max(0.0, min(1.0, self._token_alert_flash))
        if pulse <= 0.01:
            return
        progress = 1.0 - pulse
        opacity = alert_train_opacity(progress)
        if opacity <= 0.01:
            return
        is_up = (self._token_alert.delta_percent or 0) >= 0
        side_collapsed = self._collapsed and self._collapsed_edge in {"left", "right"}
        bar_collapsed = self._collapsed and self._collapsed_edge in {"top", "bottom"}
        if side_collapsed:
            notice_h = 22
            notice_w = max(52, rect.width() - 12)
        elif bar_collapsed:
            notice_h = 26
            notice_w = min(max(170, rect.width() // 2), rect.width() - 18)
        else:
            notice_h = 34
            notice_w = min(max(236, rect.width() - 16), max(250, rect.width() + 18))
        notice_x = int(alert_train_position(progress, rect.left() - notice_w - 10, rect.center().x() - notice_w // 2, rect.right() + 10))
        notice = QRect(notice_x, rect.center().y() - notice_h // 2, notice_w, notice_h)

        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(rect), 23, 23)
        painter.setClipPath(clip)

        hot = QColor(48, 235, 137) if is_up else QColor(255, 82, 103)
        warm = QColor(90, 255, 185) if is_up else QColor(255, 207, 86)
        grad = QLinearGradient(notice.left(), 0, notice.right(), 0)
        grad.setColorAt(0.0, QColor(94, 18, 24, 0))
        grad.setColorAt(0.14, QColor(hot.red(), hot.green(), hot.blue(), int(210 * opacity)))
        grad.setColorAt(0.50, QColor(25, 14, 12, int(238 * opacity)))
        grad.setColorAt(0.86, QColor(hot.red(), hot.green(), hot.blue(), int(210 * opacity)))
        grad.setColorAt(1.0, QColor(94, 18, 24, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(notice, 11, 11)

        stripe = QColor(warm.red(), warm.green(), warm.blue(), int(120 * opacity))
        painter.setPen(QPen(stripe, 1.4))
        shift = int(progress * 120) % 16
        for x in range(notice.left() - notice.height() + shift, notice.right() + notice.height(), 16):
            painter.drawLine(QPointF(x, notice.bottom()), QPointF(x + notice.height(), notice.top()))

        logo_size = 12 if side_collapsed else (16 if bar_collapsed else 22)
        logo_left = notice.left() + (6 if side_collapsed else 10)
        logo_rect = QRect(logo_left, notice.center().y() - logo_size // 2, logo_size, logo_size)
        painter.drawPixmap(logo_rect, self._token_logo_pixmap(self._token_alert.symbol, self._token_alert.chain, self._token_alert.logo_url, logo_size))
        chain_size = 7 if side_collapsed else (9 if bar_collapsed else 12)
        painter.drawPixmap(QRect(logo_rect.right() - chain_size + 2, logo_rect.bottom() - chain_size + 2, chain_size, chain_size), native_icon(self._token_alert.chain, chain_size))

        font_size = 7 if side_collapsed else (9 if bar_collapsed else 12)
        font = QFont("Microsoft YaHei UI", font_size, QFont.Weight.Black)
        painter.setFont(font)
        text_color = QColor(255, 226, 120) if is_up else QColor(255, 118, 135)
        text_color.setAlpha(int(255 * opacity))
        painter.setPen(text_color)
        text_rect = QRect(logo_rect.right() + 5, notice.top(), max(8, notice.right() - logo_rect.right() - 9), notice.height())
        notice_text = self._token_alert_notice_text(compact=bar_collapsed, tiny=side_collapsed)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elide_by_width(painter, notice_text, text_rect.width()))
        painter.restore()

    def _draw_collapsed_content(self, painter: QPainter, rect: QRect) -> None:
        self._wallet_rect = QRect()
        self._token_rect = QRect()
        if self._collapsed_edge in {"top", "bottom"}:
            self._draw_collapsed_bar_content(painter, rect)
            return
        self._draw_collapsed_side_content(painter, rect)

    def _draw_collapsed_side_content(self, painter: QPainter, rect: QRect) -> None:
        left = rect.left() + 8
        right = rect.right() - 8
        top = rect.top() + 9
        width = max(1, right - left)

        logo_rect = QRect(left, top, 18, 18)
        painter.drawPixmap(logo_rect, self._logo)
        painter.drawPixmap(QRect(logo_rect.right() - 7, logo_rect.bottom() - 7, 10, 10), native_icon(self._data.chain, 10))

        live_color = QColor(66, 232, 141) if self._data.status == "Live" else QColor(255, 188, 87)
        if self._data.status == "Error":
            live_color = QColor(255, 88, 108)
        pulse = self._live_pulse * 2.0 if self._live_pulse <= 0.5 else (1.0 - self._live_pulse) * 2.0
        pulse = max(0.0, min(1.0, pulse))
        dot_x = right - 4
        dot_y = top + 9
        if self._data.status == "Live":
            halo = QColor(live_color)
            halo.setAlpha(int(18 + 54 * pulse))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QRectF(dot_x - 5.0 - pulse, dot_y - 5.0 - pulse, 10.0 + pulse * 2.0, 10.0 + pulse * 2.0))
        dot_color = QColor(live_color)
        dot_color.setAlpha(int(130 + 125 * pulse) if self._data.status == "Live" else 225)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot_color)
        painter.drawEllipse(QRectF(dot_x - 2.7, dot_y - 2.7, 5.4, 5.4))

        symbol_font = self._collapsed_side_symbol_font()
        painter.setFont(symbol_font)
        painter.setPen(QColor(219, 232, 229))
        symbol_rect = QRect(left, logo_rect.bottom() + 7, width, 16)
        symbol = elide_by_width(painter, self._data.symbol, symbol_rect.width())
        painter.drawText(symbol_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, symbol)

        mc_text = self._new_mc_text or format_market_cap(self._display_mc)
        mc_font = fit_font_to_width(self._collapsed_side_market_font(), mc_text, width, 11)
        mc_color = QColor(245, 250, 247)
        if self._flash > 0:
            mc_color = QColor(67, 244, 153) if self._direction >= 0 else QColor(255, 84, 104)
        painter.setFont(mc_font)
        mc_rect = QRect(left, symbol_rect.bottom() + 7, width, 23)
        self._draw_flip_text(
            painter,
            mc_rect,
            self._old_mc_text,
            mc_text,
            mc_color,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            10,
        )

        change_text = format_change(self._data.change_percent)
        change_color = QColor(132, 144, 143)
        if self._data.change_percent is not None:
            change_color = QColor(55, 226, 130) if self._data.change_percent >= 0 else QColor(255, 82, 103)
        change_font = self._collapsed_side_change_font()
        painter.setFont(change_font)
        painter.setPen(change_color)
        change_w = QFontMetrics(change_font).horizontalAdvance(change_text)
        trend_w = 14
        change_rect = QRect(left, mc_rect.bottom() + 5, max(12, min(change_w + 2, width - trend_w - 4)), 17)
        painter.drawText(change_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, change_text)
        self._draw_compact_trend(painter, QRect(change_rect.right() + 3, change_rect.top() + 1, trend_w, 15), self._direction >= 0, 1.55)
        if self._has_active_token_alert():
            alert_rect = QRect(left, change_rect.bottom() + 5, width, 32)
            self._draw_collapsed_alert_inline(painter, alert_rect, compact=True)

    def _draw_collapsed_side_alert_content(self, painter: QPainter, rect: QRect) -> None:
        left = rect.left() + 10
        right = rect.right() - 10
        top = rect.top() + 11
        width = max(1, right - left)
        pulse = max(0.0, min(1.0, self._token_alert_flash))

        accent = QColor(255, 82, 103, 38 + int(55 * pulse))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), 16, 16)

        logo_rect = QRect(left, top, 18, 18)
        painter.drawPixmap(logo_rect, self._token_logo_pixmap(self._token_alert.symbol, self._token_alert.chain, self._token_alert.logo_url, 18))
        painter.drawPixmap(QRect(logo_rect.right() - 7, logo_rect.bottom() - 7, 10, 10), native_icon(self._token_alert.chain, 10))

        symbol_font = self._collapsed_side_symbol_font()
        painter.setFont(symbol_font)
        painter.setPen(QColor(219, 232, 229))
        symbol_rect = QRect(logo_rect.right() + 7, top, max(18, right - logo_rect.right() - 7), 19)
        painter.drawText(symbol_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elide_by_width(painter, self._token_alert.symbol, symbol_rect.width()))

        mc_text = self._token_alert_value_text()
        time_text = self._collapsed_time_text()
        time_font = self._collapsed_side_time_font()
        time_w = QFontMetrics(time_font).horizontalAdvance(time_text) + 4 if time_text else 0
        arrow_w = 15
        mc_rect = QRect(left, logo_rect.bottom() + 8, max(24, width - time_w - arrow_w - 9), 24)
        mc_font = fit_font_to_width(self._collapsed_side_market_font(), mc_text, mc_rect.width(), 11)
        mc_color = QColor(245, 250, 247)
        painter.setFont(mc_font)
        painter.setPen(mc_color)
        painter.drawText(mc_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, mc_text)

        arrow_rect = QRect(mc_rect.right() + 3, mc_rect.top() + 4, arrow_w, 15)
        self._draw_compact_trend(painter, arrow_rect, (self._token_alert.delta_percent or 0) >= 0, 1.45)

        if time_text:
            painter.setFont(time_font)
            painter.setPen(QColor(255, 214, 102))
            painter.drawText(QRect(right - time_w, mc_rect.top() + 2, time_w, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, time_text)

    def _draw_collapsed_bar_content(self, painter: QPainter, rect: QRect) -> None:
        alert_rect = QRect()
        if self._has_active_token_alert():
            alert_w = min(self._collapsed_alert_inline_width(), max(136, int(rect.width() * 0.40)))
            alert_rect = QRect(rect.right() - alert_w - 4, rect.top() + 14, alert_w, 20)
            rect = QRect(rect.left(), rect.top(), max(190, rect.width() - alert_w - 10), rect.height())

        left = rect.left() + 12
        right = rect.right() - 12
        center_y = rect.center().y()
        logo_rect = QRect(left, center_y - 9, 18, 18)
        painter.drawPixmap(logo_rect, self._logo)
        painter.drawPixmap(QRect(logo_rect.right() - 7, logo_rect.bottom() - 7, 10, 10), native_icon(self._data.chain, 10))

        symbol_font = self._collapsed_symbol_font()
        painter.setFont(symbol_font)
        symbol_limit = 86 if alert_rect.isValid() else 120
        symbol_w = min(symbol_limit, max(42, QFontMetrics(symbol_font).horizontalAdvance(self._data.symbol) + 4))
        symbol_rect = QRect(logo_rect.right() + 8, center_y - 10, symbol_w, 20)
        painter.setPen(QColor(218, 232, 229))
        painter.drawText(symbol_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elide_by_width(painter, self._data.symbol, symbol_rect.width()))

        live_color = QColor(66, 232, 141) if self._data.status == "Live" else QColor(255, 188, 87)
        if self._data.status == "Error":
            live_color = QColor(255, 88, 108)
        pulse = self._live_pulse * 2.0 if self._live_pulse <= 0.5 else (1.0 - self._live_pulse) * 2.0
        pulse = max(0.0, min(1.0, pulse))
        dot_x = right - 4
        dot_y = center_y
        if self._data.status == "Live":
            halo = QColor(live_color)
            halo.setAlpha(int(18 + 54 * pulse))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QRectF(dot_x - 5.0 - pulse, dot_y - 5.0 - pulse, 10.0 + pulse * 2.0, 10.0 + pulse * 2.0))
        dot_color = QColor(live_color)
        dot_color.setAlpha(int(130 + 125 * pulse) if self._data.status == "Live" else 225)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot_color)
        painter.drawEllipse(QRectF(dot_x - 2.8, dot_y - 2.8, 5.6, 5.6))

        mc_text = self._new_mc_text or format_market_cap(self._display_mc)
        change_text = format_change(self._data.change_percent)
        change_color = QColor(132, 144, 143)
        if self._data.change_percent is not None:
            change_color = QColor(55, 226, 130) if self._data.change_percent >= 0 else QColor(255, 82, 103)

        mc_left = symbol_rect.right() + 11
        change_font = self._collapsed_change_font()
        change_w = QFontMetrics(change_font).horizontalAdvance(change_text) + 4
        trend_w = 18
        mc_right = right - 12 - trend_w - 7 - change_w
        mc_rect = QRect(mc_left, center_y - 14, max(38, mc_right - mc_left), 28)
        mc_font = fit_font_to_width(self._collapsed_market_font(), mc_text, mc_rect.width(), 9)
        mc_color = QColor(245, 250, 247)
        if self._flash > 0:
            mc_color = QColor(67, 244, 153) if self._direction >= 0 else QColor(255, 84, 104)
        painter.setFont(mc_font)
        self._draw_flip_text(
            painter,
            mc_rect,
            self._old_mc_text,
            mc_text,
            mc_color,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            9,
        )

        trend_rect = QRect(mc_rect.right() + 5, center_y - 9, trend_w, 18)
        self._draw_compact_trend(painter, trend_rect, self._direction >= 0)

        painter.setFont(change_font)
        painter.setPen(change_color)
        painter.drawText(QRect(trend_rect.right() + 6, center_y - 10, change_w, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, change_text)
        if alert_rect.isValid():
            self._draw_collapsed_alert_inline(painter, alert_rect, compact=False)

    def _draw_collapsed_bar_alert_content(self, painter: QPainter, rect: QRect) -> None:
        left = rect.left() + 12
        right = rect.right() - 12
        center_y = rect.center().y()
        logo_rect = QRect(left, center_y - 9, 18, 18)
        painter.drawPixmap(logo_rect, self._token_logo_pixmap(self._token_alert.symbol, self._token_alert.chain, self._token_alert.logo_url, 18))
        painter.drawPixmap(QRect(logo_rect.right() - 7, logo_rect.bottom() - 7, 10, 10), native_icon(self._token_alert.chain, 10))

        symbol_font = self._collapsed_symbol_font()
        painter.setFont(symbol_font)
        symbol_w = min(112, max(42, QFontMetrics(symbol_font).horizontalAdvance(self._token_alert.symbol) + 4))
        symbol_rect = QRect(logo_rect.right() + 8, center_y - 10, symbol_w, 20)
        painter.setPen(QColor(238, 246, 242))
        painter.drawText(symbol_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elide_by_width(painter, self._token_alert.symbol, symbol_rect.width()))

        time_text = self._token_alert_time_text()
        time_font = self._collapsed_time_font()
        time_w = QFontMetrics(time_font).horizontalAdvance(time_text) + 4 if time_text else 0
        arrow_w = 18
        value_left = symbol_rect.right() + 12
        value_right = right - max(30, time_w) - arrow_w - 10
        value_text = self._token_alert_value_text()
        value_rect = QRect(value_left, center_y - 14, max(38, value_right - value_left), 28)
        value_font = fit_font_to_width(self._collapsed_market_font(), value_text, value_rect.width(), 13)
        painter.setFont(value_font)
        painter.setPen(QColor(245, 250, 247))
        painter.drawText(value_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value_text)

        arrow_rect = QRect(value_rect.right() + 5, center_y - 8, arrow_w, 17)
        self._draw_compact_trend(painter, arrow_rect, (self._token_alert.delta_percent or 0) >= 0, 1.8)

        if time_text:
            painter.setFont(time_font)
            painter.setPen(QColor(255, 214, 102))
            painter.drawText(QRect(right - time_w, center_y - 10, time_w, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, time_text)

    def _draw_collapsed_alert_inline(self, painter: QPainter, rect: QRect, compact: bool) -> None:
        pulse = max(0.0, min(1.0, self._token_alert_flash))
        is_up = (self._token_alert.delta_percent or 0) >= 0
        move_color = QColor(48, 235, 137) if is_up else QColor(255, 82, 103)
        bg = QColor(move_color)
        bg.setAlpha(46 + int(62 * pulse))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect.adjusted(-2, 0, 2, 0), 9, 9)

        logo_size = 14 if compact else 16
        logo_rect = QRect(rect.left() + 4, rect.center().y() - logo_size // 2, logo_size, logo_size)
        painter.drawPixmap(logo_rect, self._token_logo_pixmap(self._token_alert.symbol, self._token_alert.chain, self._token_alert.logo_url, logo_size))
        chain_size = 9 if compact else 10
        painter.drawPixmap(QRect(logo_rect.right() - chain_size + 2, logo_rect.bottom() - chain_size + 2, chain_size, chain_size), native_icon(self._token_alert.chain, chain_size))

        time_text = self._token_alert_time_text()
        time_font = self._collapsed_side_time_font() if compact else self._collapsed_time_font()
        time_w = QFontMetrics(time_font).horizontalAdvance(time_text) + 4 if time_text else 0
        arrow_w = 13 if compact else 15
        text_left = logo_rect.right() + 6
        text_right = rect.right() - time_w - arrow_w - 7
        text_color = QColor(211, 255, 231) if is_up else QColor(255, 177, 188)

        if compact:
            symbol_font = self._collapsed_side_symbol_font()
            change_font = self._collapsed_side_change_font()
            symbol_rect = QRect(text_left, rect.top() + 1, max(8, text_right - text_left), 14)
            painter.setFont(symbol_font)
            painter.setPen(QColor(236, 248, 243))
            painter.drawText(symbol_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elide_by_width(painter, self._token_alert.symbol, symbol_rect.width()))

            change_text = format_change(self._token_alert.delta_percent)
            change_rect = QRect(text_left, rect.top() + 15, max(8, text_right - text_left), 15)
            painter.setFont(change_font)
            painter.setPen(text_color)
            visible = elide_by_width(painter, change_text, change_rect.width())
            painter.drawText(change_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, visible)
            actual_w = min(QFontMetrics(change_font).horizontalAdvance(visible), change_rect.width())
            arrow_x = min(change_rect.left() + actual_w + 4, rect.right() - time_w - arrow_w - 4)
            arrow_rect = QRect(arrow_x, change_rect.center().y() - 7, arrow_w, 14)
        else:
            text_rect = QRect(text_left, rect.top(), max(8, text_right - text_left), rect.height())
            text = f"{self._token_alert.symbol} {format_change(self._token_alert.delta_percent)}"
            font = self._collapsed_change_font()
            painter.setFont(font)
            painter.setPen(text_color)
            visible = elide_by_width(painter, text, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, visible)
            actual_w = min(QFontMetrics(font).horizontalAdvance(visible), text_rect.width())
            arrow_x = min(text_rect.left() + actual_w + 4, rect.right() - time_w - arrow_w - 4)
            arrow_rect = QRect(arrow_x, rect.center().y() - 7, arrow_w, 14)

        self._draw_compact_trend(painter, arrow_rect, is_up, 1.35 if compact else 1.55)

        if time_text:
            painter.setFont(time_font)
            painter.setPen(QColor(255, 214, 102))
            painter.drawText(QRect(rect.right() - time_w, rect.top(), time_w, rect.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, time_text)

    def _collapsed_alert_text(self) -> str:
        time_text = self._token_alert_time_text()
        if time_text:
            return f"CA {time_text}"
        return "CA"

    def _draw_collapsed_alert_pill(self, painter: QPainter, rect: QRect, compact: bool) -> None:
        pulse = max(0.0, min(1.0, self._token_alert_flash))
        bg = QColor(94, 20, 28, 170 + int(58 * pulse))
        edge = QColor(255, 82, 103, 80 + int(96 * pulse))
        painter.setPen(QPen(edge, 0.9))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 8, 8)
        if pulse > 0:
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(rect), 8, 8)
            painter.setClipPath(clip)
            painter.setPen(QPen(QColor(255, 190, 63, int(35 + 65 * pulse)), 1.4))
            shift = int((1.0 - pulse) * 42) % 12
            for x in range(rect.left() - 16 + shift, rect.right() + 16, 12):
                painter.drawLine(QPointF(x, rect.bottom()), QPointF(x + 12, rect.top()))
            painter.restore()
        painter.setFont(QFont("Cascadia Mono", 6 if compact else 7, QFont.Weight.Black))
        painter.setPen(QColor(255, 215, 102))
        painter.drawText(rect.adjusted(3, 0, -3, 0), Qt.AlignmentFlag.AlignCenter, elide_by_width(painter, self._collapsed_alert_text(), rect.width() - 6))

    def _draw_compact_trend(self, painter: QPainter, rect: QRect, is_up: bool, stroke: float = 2.15) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        pulse = max(0.0, min(1.0, self._flash))
        color = QColor(58, 239, 154) if is_up else QColor(255, 83, 112)
        color.setAlpha(150 + int(80 * pulse))
        if is_up:
            start = QPointF(rect.left() + rect.width() * 0.18, rect.bottom() - rect.height() * 0.28)
            mid = QPointF(rect.left() + rect.width() * 0.52, rect.top() + rect.height() * 0.54)
            tip = QPointF(rect.right() - rect.width() * 0.16, rect.top() + rect.height() * 0.26)
        else:
            start = QPointF(rect.left() + rect.width() * 0.18, rect.top() + rect.height() * 0.28)
            mid = QPointF(rect.left() + rect.width() * 0.52, rect.bottom() - rect.height() * 0.54)
            tip = QPointF(rect.right() - rect.width() * 0.16, rect.bottom() - rect.height() * 0.26)
        path = QPainterPath()
        path.moveTo(start)
        path.quadTo(mid, tip)
        painter.setPen(QPen(color, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)
        angle = math.atan2(tip.y() - mid.y(), tip.x() - mid.x())
        head_len = max(3.7, stroke * 2.4)
        spread = 0.7
        back_1 = QPointF(tip.x() - math.cos(angle - spread) * head_len, tip.y() - math.sin(angle - spread) * head_len)
        back_2 = QPointF(tip.x() - math.cos(angle + spread) * head_len, tip.y() - math.sin(angle + spread) * head_len)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([tip, back_1, back_2]))

    def _draw_content(self, painter: QPainter, rect: QRect) -> None:
        left = rect.left() + PAD_X
        right = rect.right() - PAD_X
        top = rect.top() + 13

        logo_rect = QRect(left, top, 22, 22)
        symbol_x = logo_rect.right() + 8
        symbol_rect = QRect(symbol_x, top, 112, 22)
        symbol_w = min(QFontMetrics(self._symbol_font()).horizontalAdvance(self._data.symbol), symbol_rect.width())
        badge = QRect(symbol_x + symbol_w + 8, top + 3, 38, 16)
        self._token_rect = QRect(logo_rect.left(), logo_rect.top(), badge.right() - logo_rect.left() + 4, logo_rect.height())
        if self._hover_token:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(72, 238, 161, 18))
            painter.drawRoundedRect(self._token_rect.adjusted(-4, -2, 4, 2), 10, 10)

        painter.drawPixmap(logo_rect, self._logo)
        painter.drawPixmap(QRect(logo_rect.right() - 8, logo_rect.bottom() - 8, 11, 11), native_icon(self._data.chain, 11))
        painter.setFont(self._symbol_font())
        painter.setPen(QColor(235, 244, 241))
        painter.drawText(symbol_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elide_by_width(painter, self._data.symbol, symbol_rect.width()))
        painter.setPen(QPen(QColor(86, 231, 156, 80), 1))
        painter.setBrush(QColor(15, 82, 56, 142))
        painter.drawRoundedRect(badge, 8, 8)
        painter.setPen(QColor(177, 255, 218))
        painter.setFont(QFont("Segoe UI", 6, QFont.Weight.Bold))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, self._data.chain)

        live_color = QColor(66, 232, 141) if self._data.status == "Live" else QColor(255, 188, 87)
        if self._data.status == "Error":
            live_color = QColor(255, 88, 108)
        pulse = self._live_pulse * 2.0 if self._live_pulse <= 0.5 else (1.0 - self._live_pulse) * 2.0
        pulse = max(0.0, min(1.0, pulse))
        live_font = QFont("Segoe UI", 7, QFont.Weight.Black)
        painter.setFont(live_font)
        live_text = "LIVE"
        live_w = QFontMetrics(live_font).horizontalAdvance(live_text)
        live_x = right - live_w - 2
        dot_center_x = live_x - 8
        dot_center_y = top + 11
        live_alpha = int(120 + 135 * pulse) if self._data.status == "Live" else 230
        if self._data.status == "Live":
            halo = QColor(live_color)
            halo.setAlpha(int(18 + 62 * pulse))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QRectF(dot_center_x - 5.0 - pulse, dot_center_y - 5.0 - pulse, 10.0 + pulse * 2.0, 10.0 + pulse * 2.0))
        painter.setPen(Qt.PenStyle.NoPen)
        dot_color = QColor(live_color)
        dot_color.setAlpha(live_alpha)
        painter.setBrush(dot_color)
        painter.drawEllipse(QRectF(dot_center_x - 3.0, dot_center_y - 3.0, 6.0, 6.0))
        glow = QColor(live_color)
        glow.setAlpha(int(32 + 86 * pulse) if self._data.status == "Live" else 70)
        painter.setPen(QPen(glow, 2.2))
        painter.drawText(QRect(live_x, top + 2, live_w + 4, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, live_text)
        text_color = QColor(live_color)
        if self._data.status == "Live":
            text_color.setAlpha(live_alpha)
        painter.setPen(text_color)
        painter.drawText(QRect(live_x, top + 2, live_w + 4, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, live_text)

        vol_w = max(50, min(64, QFontMetrics(self._volume_font()).horizontalAdvance(format_volume(self._data.volume_24h)) + 8))
        vol_x = right - vol_w

        painter.setFont(self._market_font())
        mc_text_w = QFontMetrics(self._market_font()).horizontalAdvance(self._new_mc_text)
        signal_x = clamp_int(left + mc_text_w + 10, left + 76, vol_x - MARKET_SIGNAL_W - 12)
        market_signal_rect = QRect(signal_x, rect.top() + 40, MARKET_SIGNAL_W, 35)
        mc_rect = QRect(left, rect.top() + 39, market_signal_rect.left() - left - 7, 36)
        mc_color = QColor(245, 250, 247)
        if self._flash > 0:
            mc_color = QColor(67, 244, 153) if self._direction >= 0 else QColor(255, 84, 104)
        self._draw_flip_text(
            painter,
            mc_rect,
            self._old_mc_text,
            self._new_mc_text,
            mc_color,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            14,
        )
        self._draw_market_signal(painter, market_signal_rect)

        painter.setFont(self._label_font())
        painter.setPen(QColor(93, 106, 106))
        painter.drawText(QRect(vol_x, rect.top() + 41, vol_w, 12), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "VOL 24H")
        painter.setFont(self._volume_font())
        painter.setPen(QColor(178, 191, 188))
        painter.drawText(QRect(vol_x, rect.top() + 56, vol_w, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, format_volume(self._data.volume_24h))

        bottom_y = rect.top() + 88
        painter.setFont(self._sub_value_font())
        price_text = self._new_price_text if self._new_price_text != "$--" else format_price(self._data.price)
        price_w = QFontMetrics(self._sub_value_font()).horizontalAdvance(price_text)
        change_w = QFontMetrics(self._change_font()).horizontalAdvance(format_change(self._data.change_percent))
        available_market_w = max(92, right - left)
        price_rect = QRect(left, bottom_y, max(60, min(price_w + 6, available_market_w)), 18)
        self._draw_flip_text(
            painter,
            price_rect,
            self._old_price_text,
            self._new_price_text,
            QColor(188, 202, 198),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            7,
        )

        change = self._data.change_percent
        change_color = QColor(132, 144, 143)
        if change is not None:
            change_color = QColor(55, 226, 130) if change >= 0 else QColor(255, 82, 103)
        painter.setFont(self._change_font())
        painter.setPen(change_color)
        change_x = left + price_w + 12
        change_right = left + available_market_w
        change_rect_w = max(42, min(change_w + 6, change_right - change_x))
        if change_x + change_rect_w <= change_right:
            painter.drawText(QRect(change_x, bottom_y, change_rect_w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, format_change(change))

        if self._wallet.side:
            activity_y = bottom_y + 22
            self._draw_wallet_activity(painter, QRect(left, activity_y, right - left, 22))
            if self._has_active_token_alert():
                self._draw_token_alert(painter, QRect(left, activity_y + 24, right - left, 22))
            else:
                self._token_alert_rect = QRect()
        else:
            self._wallet_rect = QRect()
            if self._has_active_token_alert():
                self._draw_token_alert(painter, QRect(left, bottom_y + 22, right - left, 22))
            else:
                self._token_alert_rect = QRect()

    def _draw_flip_text(
        self,
        painter: QPainter,
        rect: QRect,
        old_text: str,
        new_text: str,
        color: QColor,
        alignment: Qt.AlignmentFlag,
        distance: int,
    ) -> None:
        if self._flip <= 0.01 or old_text == new_text:
            painter.setPen(color)
            painter.drawText(rect, alignment, new_text)
            return

        old_color = QColor(color)
        old_color.setAlpha(max(0, min(255, int(185 * self._flip))))
        old_rect = QRect(rect)
        old_rect.moveTop(rect.top() - int(distance * (1.0 - self._flip)))
        painter.setPen(old_color)
        painter.drawText(old_rect, alignment, old_text)

        new_color = QColor(color)
        new_color.setAlpha(max(0, min(255, int(255 - 120 * self._flip))))
        new_rect = QRect(rect)
        new_rect.moveTop(rect.top() + int(distance * self._flip))
        painter.setPen(new_color)
        painter.drawText(new_rect, alignment, new_text)

    def _draw_market_signal(self, painter: QPainter, rect: QRect) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return

        is_up = self._direction >= 0
        arrow_color = QColor(58, 239, 154) if is_up else QColor(255, 83, 112)
        soft_color = QColor(30, 184, 222) if is_up else QColor(255, 143, 94)
        pulse = max(0.0, min(1.0, self._flash))
        base_alpha = 130 if self._direction == 0 else 180
        active_alpha = min(255, base_alpha + int(75 * pulse))

        path, tip, neck, tail, angle = self._market_arrow_geometry(rect, is_up)

        halo = QColor(arrow_color)
        halo.setAlpha(int(18 + 58 * pulse))
        painter.setPen(QPen(halo, 8.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)

        shadow = QColor(0, 0, 0, 95)
        painter.setPen(QPen(shadow, 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path.translated(0.8, 1.1))

        grad = QLinearGradient(tail, neck)
        g0 = QColor(soft_color)
        g1 = QColor(arrow_color)
        g0.setAlpha(max(80, active_alpha - 55))
        g1.setAlpha(active_alpha)
        end_color = QColor(arrow_color)
        end_color.setAlpha(min(255, active_alpha + 16))
        grad.setColorAt(0.0, g0)
        grad.setColorAt(0.56, g1)
        grad.setColorAt(1.0, end_color)
        painter.setPen(QPen(QBrush(grad), 3.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)

        head = self._arrow_head_polygon(tip, neck, angle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawPolygon(head)

        highlight = QColor(255, 255, 255, int(42 + 64 * pulse))
        highlight_path = self._market_arrow_highlight(rect, is_up)
        painter.setPen(QPen(highlight, 1.05, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(highlight_path)

    def _market_arrow_geometry(self, rect: QRect, is_up: bool) -> tuple[QPainterPath, QPointF, QPointF, QPointF, float]:
        if is_up:
            start = QPointF(rect.left() + rect.width() * 0.13, rect.bottom() - rect.height() * 0.18)
            c1 = QPointF(rect.left() + rect.width() * 0.38, rect.bottom() - rect.height() * 0.18)
            c2 = QPointF(rect.left() + rect.width() * 0.58, rect.top() + rect.height() * 0.46)
            tip = QPointF(rect.left() + rect.width() * 0.65, rect.top() + rect.height() * 0.13)
        else:
            start = QPointF(rect.left() + rect.width() * 0.15, rect.top() + rect.height() * 0.15)
            c1 = QPointF(rect.left() + rect.width() * 0.27, rect.top() + rect.height() * 0.38)
            c2 = QPointF(rect.left() + rect.width() * 0.54, rect.bottom() - rect.height() * 0.34)
            tip = QPointF(rect.left() + rect.width() * 0.68, rect.bottom() - rect.height() * 0.13)
        dx = tip.x() - c2.x()
        dy = tip.y() - c2.y()
        angle = math.atan2(dy, dx)
        neck_distance = 7.2
        neck = QPointF(
            tip.x() - math.cos(angle) * neck_distance,
            tip.y() - math.sin(angle) * neck_distance,
        )
        path = QPainterPath()
        path.moveTo(start)
        path.cubicTo(c1, c2, neck)
        return path, tip, neck, start, angle

    def _market_arrow_highlight(self, rect: QRect, is_up: bool) -> QPainterPath:
        inset = rect.adjusted(3, 3, -3, -3)
        path, _, _, _, _ = self._market_arrow_geometry(inset, is_up)
        return path

    def _arrow_head_polygon(self, tip: QPointF, neck: QPointF, angle: float) -> QPolygonF:
        length = 8.8
        spread = 0.66
        back_1 = QPointF(
            tip.x() - math.cos(angle - spread) * length,
            tip.y() - math.sin(angle - spread) * length,
        )
        back_2 = QPointF(
            tip.x() - math.cos(angle + spread) * length,
            tip.y() - math.sin(angle + spread) * length,
        )
        inner = QPointF(
            neck.x(),
            neck.y(),
        )
        return QPolygonF([tip, back_1, inner, back_2])

    def _draw_wallet_activity(self, painter: QPainter, rect: QRect) -> None:
        self._wallet_rect = QRect(rect)
        side_color = QColor(48, 235, 137) if self._wallet.side == "buy" else QColor(255, 82, 103)
        if self._hover_wallet and self._wallet.token_address:
            hover = QColor(side_color)
            hover.setAlpha(26)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover)
            painter.drawRoundedRect(rect.adjusted(-3, -1, 3, 1), 10, 10)
        if self._wallet_flash > 0:
            glow = QColor(side_color)
            glow.setAlpha(int(42 * self._wallet_flash))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawRoundedRect(rect.adjusted(-3, 0, 3, 1), 10, 10)

        painter.drawPixmap(QRect(rect.left(), rect.top() + 3, 16, 16), avatar_pixmap(self._wallet.avatar_kind, self._wallet.avatar_value, 16))
        painter.setFont(self._wallet_font())
        painter.setPen(side_color)
        time_text = format_relative_time(self._wallet.timestamp)
        time_w = 0
        if time_text:
            time_w = QFontMetrics(self._wallet_time_font()).horizontalAdvance(time_text) + 4
        text_rect = QRect(rect.left() + 23, rect.top(), max(20, rect.width() - 23 - time_w - 8), rect.height())
        draw_wallet_activity_inline(
            painter,
            text_rect,
            self._wallet,
            side_color,
            self._token_logo_pixmap,
            14,
            9,
        )
        if time_text:
            painter.setFont(self._wallet_time_font())
            painter.setPen(QColor(139, 154, 151))
            painter.drawText(
                QRect(rect.right() - time_w, rect.top(), time_w, rect.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                time_text,
            )

    def _wallet_area_width(self, painter: QPainter, rect: QRect) -> int:
        if not self._wallet.side:
            return 0
        painter.setFont(self._wallet_font())
        full_w = wallet_activity_inline_width(self._wallet, self._wallet_font(), 14) + 24
        return max(70, min(full_w, max(70, rect.width() - PAD_X * 2 - 110)))

    def _draw_token_alert(self, painter: QPainter, rect: QRect) -> None:
        self._token_alert_rect = QRect(rect)
        is_up = (self._token_alert.delta_percent or 0) >= 0
        move_color = QColor(48, 235, 137) if is_up else QColor(255, 82, 103)
        warn_color = move_color
        stripe_color = QColor(90, 255, 185) if is_up else QColor(255, 185, 67)
        pulse = max(0.0, min(1.0, self._token_alert_flash))
        bg_alpha = 42 + int(18 * self._hover_token_alert) + int(58 * pulse)
        bg = QColor(warn_color)
        bg.setAlpha(bg_alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        bg_rect = rect.adjusted(-3, 0, 3, 1)
        painter.drawRoundedRect(bg_rect, 10, 10)
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(bg_rect), 10, 10)
        painter.setClipPath(clip)
        stripe = QColor(stripe_color.red(), stripe_color.green(), stripe_color.blue(), 45 + int(78 * pulse))
        painter.setPen(QPen(stripe, 2.0))
        shift = int((1.0 - pulse) * 120) % 18
        for x in range(bg_rect.left() - 24 + shift, bg_rect.right() + 24, 18):
            painter.drawLine(QPointF(x, bg_rect.bottom()), QPointF(x + 20, bg_rect.top()))
        painter.restore()

        logo_rect = QRect(rect.left() + 4, rect.top() + 3, 16, 16)
        painter.drawPixmap(logo_rect, self._token_logo_pixmap(self._token_alert.symbol, self._token_alert.chain, self._token_alert.logo_url, 16))
        painter.drawPixmap(QRect(logo_rect.right() - 7, logo_rect.bottom() - 7, 10, 10), native_icon(self._token_alert.chain, 10))

        time_text = self._token_alert_time_text()
        time_w = QFontMetrics(self._wallet_time_font()).horizontalAdvance(time_text) + 5 if time_text else 0
        arrow_w = 17
        text_left = logo_rect.right() + 7
        text_rect = QRect(text_left, rect.top(), max(12, rect.width() - (text_left - rect.left()) - time_w - arrow_w - 12), rect.height())
        text_value = self._token_alert_text_full()
        alert_font = fit_font_to_width(self._wallet_font(), text_value, text_rect.width(), 7)
        painter.setFont(alert_font)
        painter.setPen(move_color)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elide_by_width(painter, text_value, text_rect.width()),
        )
        visible = elide_by_width(painter, text_value, text_rect.width())
        actual_w = min(QFontMetrics(alert_font).horizontalAdvance(visible), text_rect.width())
        arrow_x = min(text_rect.left() + actual_w + 4, rect.right() - time_w - arrow_w - 4)
        arrow_rect = QRect(arrow_x, rect.top() + 4, arrow_w, 15)
        self._draw_compact_trend(painter, arrow_rect, is_up, 1.5)
        if time_text:
            painter.setFont(self._wallet_time_font())
            painter.setPen(QColor(164, 180, 176))
            painter.drawText(QRect(rect.right() - time_w, rect.top(), time_w, rect.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, time_text)

    def _wallet_text_for_width(self, painter: QPainter, width: int) -> str:
        return wallet_display_text(self._wallet, painter, width)

    def _wallet_text_full(self) -> str:
        return wallet_activity_full_text(self._wallet)

    def _market_font(self) -> QFont:
        return QFont("Segoe UI Variable Display", 27, QFont.Weight.Black)

    def _sub_value_font(self) -> QFont:
        return QFont("Segoe UI Variable Text", 9, QFont.Weight.Bold)

    def _change_font(self) -> QFont:
        return QFont("Segoe UI Variable Text", 9, QFont.Weight.Bold)

    def _volume_font(self) -> QFont:
        return QFont("Segoe UI Variable Text", 10, QFont.Weight.Bold)

    def _wallet_time_font(self) -> QFont:
        return QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold)

    def _wallet_font(self) -> QFont:
        return QFont("Microsoft YaHei UI", 8, QFont.Weight.Black)

    def _label_font(self) -> QFont:
        return QFont("Segoe UI", 6, QFont.Weight.Bold)

    def _symbol_font(self) -> QFont:
        return QFont("Segoe UI Variable Text", 10, QFont.Weight.Bold)

    def _collapsed_symbol_font(self) -> QFont:
        return QFont("Segoe UI Variable Text", 8, QFont.Weight.Black)

    def _collapsed_market_font(self) -> QFont:
        return QFont("Segoe UI Variable Display", 20, QFont.Weight.Black)

    def _collapsed_side_symbol_font(self) -> QFont:
        return QFont("Segoe UI Variable Text", 7, QFont.Weight.Bold)

    def _collapsed_side_market_font(self) -> QFont:
        return QFont("Segoe UI Variable Display", 16, QFont.Weight.Black)

    def _collapsed_side_change_font(self) -> QFont:
        return QFont("Segoe UI Variable Text", 7, QFont.Weight.Black)

    def _collapsed_price_font(self) -> QFont:
        return QFont("Segoe UI Variable Text", 8, QFont.Weight.Bold)

    def _collapsed_change_font(self) -> QFont:
        return QFont("Segoe UI Variable Text", 8, QFont.Weight.Black)

    def _collapsed_time_font(self) -> QFont:
        return QFont("Microsoft YaHei UI", 7, QFont.Weight.Black)

    def _collapsed_side_time_font(self) -> QFont:
        return QFont("Microsoft YaHei UI", 7, QFont.Weight.Black)


def elide_by_width(painter: QPainter, value: str, width: int) -> str:
    metrics = QFontMetrics(painter.font())
    if metrics.horizontalAdvance(value) <= width:
        return value
    return metrics.elidedText(value, Qt.TextElideMode.ElideRight, width)


def fit_font_to_width(font: QFont, text: str, width: int, min_size: int = 7) -> QFont:
    fitted = QFont(font)
    while fitted.pointSize() > min_size and QFontMetrics(fitted).horizontalAdvance(text) > width:
        fitted.setPointSize(fitted.pointSize() - 1)
    return fitted


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    if maximum < minimum:
        return minimum
    return max(minimum, min(value, maximum))


def ease_in_out_cubic(value: float) -> float:
    t = max(0.0, min(1.0, value))
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - pow(-2.0 * t + 2.0, 3.0) / 2.0


def ease_in_cubic(value: float) -> float:
    t = max(0.0, min(1.0, value))
    return t * t * t


def ease_out_cubic(value: float) -> float:
    t = max(0.0, min(1.0, value))
    return 1.0 - pow(1.0 - t, 3.0)


def alert_train_position(progress: float, left_x: float, center_x: float, right_x: float) -> float:
    t = max(0.0, min(1.0, progress))
    in_end = 0.115
    out_start = 0.885
    if t < in_end:
        k = ease_out_cubic(t / in_end)
        return left_x + (center_x - left_x) * k
    if t <= out_start:
        return center_x
    k = ease_in_cubic((t - out_start) / (1.0 - out_start))
    return center_x + (right_x - center_x) * k


def alert_train_opacity(progress: float) -> float:
    t = max(0.0, min(1.0, progress))
    in_end = 0.115
    out_start = 0.885
    if t < in_end:
        return ease_out_cubic(t / in_end)
    if t <= out_start:
        return 1.0
    return 1.0 - ease_in_cubic((t - out_start) / (1.0 - out_start))


def to_float_or_none(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def format_price(value: float | None) -> str:
    if value is None:
        return "$--"
    if value == 0:
        return "$0"
    if abs(value) < 0.000001:
        return f"${value:.10f}".rstrip("0")
    if abs(value) < 0.01:
        return f"${value:.8f}".rstrip("0")
    if abs(value) < 1:
        return f"${value:.6f}".rstrip("0")
    return f"${value:,.4f}".rstrip("0").rstrip(".")


def format_market_cap(value: float | None) -> str:
    if value is None or value <= 0:
        return "--"
    abs_v = abs(value)
    if abs_v >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B".rstrip("0").rstrip(".")
    if abs_v >= 1_000_000:
        return f"{value / 1_000_000:.2f}M".rstrip("0").rstrip(".")
    if abs_v >= 1_000:
        return f"{value / 1_000:.2f}K".rstrip("0").rstrip(".")
    return f"{value:.0f}"


def format_volume(value: float | None) -> str:
    if value is None or value <= 0:
        return "--"
    abs_v = abs(value)
    if abs_v >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if abs_v >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_v >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def format_change(value: float | None) -> str:
    if value is None:
        return "--"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def format_threshold_percent(value: float | None) -> str:
    if value is None:
        return "--%"
    text = f"{abs(value):.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def format_native_amount(value: float | None, symbol: str) -> str:
    ticker = symbol or ""
    if value is None:
        return f"--{ticker}"
    if abs(value) >= 100:
        amount = f"{value:,.0f}"
    elif abs(value) >= 10:
        amount = f"{value:,.1f}".rstrip("0").rstrip(".")
    elif abs(value) >= 1:
        amount = f"{value:,.2f}".rstrip("0").rstrip(".")
    elif abs(value) >= 0.01:
        amount = f"{value:.3f}".rstrip("0").rstrip(".")
    else:
        amount = f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{amount}{ticker}"


def format_relative_time(timestamp: int | None) -> str:
    if not timestamp:
        return ""
    delta = max(0, int(time.time()) - int(timestamp))
    if delta < 60:
        return f"{max(1, delta)}s前"
    minutes = delta // 60
    if minutes < 60:
        return f"{minutes}m前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h前"
    return f"{hours // 24}d前"


def gmgn_chain_slug(chain: str) -> str:
    value = (chain or "").lower().strip()
    if value in {"sol", "solana"}:
        return "sol"
    if value in {"eth", "ethereum"}:
        return "eth"
    if value == "base":
        return "base"
    if value in {"bsc", "bnb", "bnbchain", "bnb chain"}:
        return "bsc"
    return value
