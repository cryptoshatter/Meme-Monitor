from __future__ import annotations

import io
import logging
import sys
import time
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import QEventLoop, QPointF, QRectF, QTimer, QUrl, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtSvg import QSvgRenderer

LOG = logging.getLogger(__name__)
_FAILED_LOGOS: dict[tuple[str, int], float] = {}
_CHAIN_SVG_FILES = {
    "sol": "sol.svg",
    "solana": "sol.svg",
    "eth": "eth.svg",
    "ethereum": "eth.svg",
    "base": "eth.svg",
    "bnb": "bnb.svg",
    "bsc": "bnb.svg",
    "bnbchain": "bnb.svg",
    "bnb chain": "bnb.svg",
}


class LogoLoader(QThread):
    loaded = Signal(str, object)
    failed = Signal(str)

    def __init__(self, url: str, size: int) -> None:
        super().__init__()
        self.url = url
        self.size = size

    def run(self) -> None:
        pix = pixmap_from_url(self.url, self.size)
        if not pix.isNull():
            self.loaded.emit(self.url, pix)
        else:
            self.failed.emit(self.url)


def pixmap_from_url(url: str, size: int = 54) -> QPixmap:
    if not url:
        return QPixmap()
    failed_until = _FAILED_LOGOS.get((url, size), 0.0)
    if failed_until > time.monotonic():
        return QPixmap()
    try:
        return _pixmap_from_url_cached(url, size)
    except Exception as exc:
        LOG.info("logo download failed: %s", exc)
        _FAILED_LOGOS[(url, size)] = time.monotonic() + 300.0
        return QPixmap()


@lru_cache(maxsize=64)
def _pixmap_from_url_cached(url: str, size: int) -> QPixmap:
    content = _download_image_with_qt(url)
    image = Image.open(io.BytesIO(content)).convert("RGBA")
    return _pil_to_pixmap(_circle_image(image, size))


def _download_image_with_qt(url: str) -> bytes:
    manager = QNetworkAccessManager()
    request = QNetworkRequest(QUrl(url))
    request.setTransferTimeout(9000)
    request.setRawHeader(
        b"User-Agent",
        (
            b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            b"AppleWebKit/537.36 (KHTML, like Gecko) "
            b"Chrome/125.0.0.0 Safari/537.36"
        ),
    )
    request.setRawHeader(b"Accept", b"image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8")
    request.setRawHeader(b"Referer", b"https://gmgn.ai/")

    loop = QEventLoop()
    reply = manager.get(request)
    reply.finished.connect(loop.quit)
    QTimer.singleShot(9500, loop.quit)
    loop.exec()

    status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    err = reply.error()
    content_type = reply.rawHeader("content-type").data().decode("latin1", "ignore")
    data = bytes(reply.readAll())
    reply.deleteLater()
    manager.deleteLater()

    if err != QNetworkReply.NetworkError.NoError:
        raise ValueError(f"logo network error: {err}")
    if status and int(status) >= 400:
        raise ValueError(f"logo HTTP {status}")
    if "image" not in content_type.lower():
        raise ValueError(f"logo response is not an image: {content_type}")
    if not data:
        raise ValueError("logo response is empty")
    return data


@lru_cache(maxsize=64)
def _pixmap_from_local_image_cached(path: str, size: int) -> QPixmap:
    image = Image.open(path).convert("RGBA")
    return _pil_to_pixmap(_circle_image(image, size))


def _circle_image(image: Image.Image, size: int) -> Image.Image:
    scale = 4
    work_size = size * scale
    source = image.copy()
    source.thumbnail((work_size, work_size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
    x = (work_size - source.width) // 2
    y = (work_size - source.height) // 2
    canvas.alpha_composite(source, (x, y))
    mask = Image.new("L", (work_size, work_size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, work_size - 1, work_size - 1), fill=255)
    canvas.putalpha(mask)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def _pil_to_pixmap(image: Image.Image) -> QPixmap:
    raw = image.tobytes("raw", "RGBA")
    qimage = QImage(raw, image.width, image.height, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimage)


def fallback_logo(size: int = 54) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor(74, 255, 174))
    grad.setColorAt(1.0, QColor(20, 146, 98))
    painter.setBrush(grad)
    painter.drawEllipse(0, 0, size, size)
    painter.setBrush(QColor(10, 16, 15))
    painter.drawEllipse(size * 0.24, size * 0.24, size * 0.52, size * 0.52)
    painter.setBrush(QColor(238, 255, 248))
    painter.drawEllipse(size * 0.39, size * 0.39, size * 0.22, size * 0.22)
    painter.end()
    return pix


def token_fallback_logo(symbol: str = "", chain: str = "", size: int = 54) -> QPixmap:
    seed = sum(ord(char) for char in (symbol or chain or "GMGN"))
    palettes = (
        (QColor(74, 255, 174), QColor(20, 146, 98)),
        (QColor(101, 151, 255), QColor(69, 84, 255)),
        (QColor(255, 208, 91), QColor(220, 111, 44)),
        (QColor(248, 98, 148), QColor(128, 88, 255)),
        (QColor(71, 234, 255), QColor(52, 128, 224)),
    )
    c1, c2 = palettes[seed % len(palettes)]
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, c1)
    grad.setColorAt(1.0, c2)
    painter.setBrush(grad)
    painter.drawEllipse(0, 0, size, size)
    painter.setBrush(QColor(8, 13, 14, 210))
    painter.drawEllipse(size * 0.24, size * 0.24, size * 0.52, size * 0.52)
    painter.setBrush(QColor(255, 255, 255, 235))
    painter.drawEllipse(size * 0.40, size * 0.40, size * 0.20, size * 0.20)
    painter.end()
    return pix


def avatar_pixmap(kind: str, value: str, size: int = 18) -> QPixmap:
    kind = (kind or "emoji").lower().strip()
    value = (value or "\U0001f9e9").strip()
    if kind == "image" and value:
        try:
            return _pixmap_from_local_image_cached(value, size)
        except Exception as exc:
            LOG.info("avatar image load failed: %s", exc)
    return emoji_avatar(value, size)


@lru_cache(maxsize=64)
def emoji_avatar(value: str, size: int = 18) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setBrush(QColor(22, 30, 33))
    painter.setPen(QPen(QColor(255, 255, 255, 52), 0.8))
    painter.drawEllipse(0.6, 0.6, size - 1.2, size - 1.2)

    text = clean_avatar_text(value)
    painter.setPen(QColor(245, 252, 248))
    family = "Segoe UI" if is_plain_avatar_text(text) else "Segoe UI Emoji"
    point_size = max(7, int(size * (0.54 if is_plain_avatar_text(text) else 0.64)))
    painter.setFont(QFont(family, point_size, QFont.Weight.Black))
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return pix


def clean_avatar_text(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "*"
    chars: list[str] = []
    include_next = False
    for char in text:
        if not chars and char.isspace():
            continue
        code = ord(char)
        if not chars:
            chars.append(char.upper() if 0x20 <= code <= 0x7E and char.isalnum() else char)
            continue
        if code in {0x200D, 0xFE0E, 0xFE0F} or 0x1F3FB <= code <= 0x1F3FF or include_next:
            chars.append(char)
            include_next = code == 0x200D
            continue
        break
    return "".join(chars) or "*"


def is_plain_avatar_text(value: str) -> bool:
    return len(value) == 1 and (value.isascii() and value.isalnum() or "\u4e00" <= value <= "\u9fff")


@lru_cache(maxsize=16)
def native_icon(chain: str, size: int = 15) -> QPixmap:
    chain = (chain or "").lower().strip()
    svg_icon = _chain_svg_icon(chain, size)
    if not svg_icon.isNull():
        return svg_icon

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    if chain == "sol":
        painter.setPen(Qt.PenStyle.NoPen)
        bar_h = max(2.0, size * 0.16)
        radius = bar_h / 2
        specs = (
            (0.18, QColor(139, 92, 246), QColor(72, 219, 188), 0.03),
            (0.43, QColor(60, 220, 185), QColor(76, 181, 255), -0.03),
            (0.68, QColor(230, 72, 255), QColor(139, 92, 246), 0.03),
        )
        for y_ratio, c1, c2, offset in specs:
            y = size * y_ratio
            x1 = size * (0.18 + offset)
            x2 = size * (0.82 + offset)
            grad = QLinearGradient(x1, y, x2, y + bar_h)
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            painter.setBrush(grad)
            painter.drawRoundedRect(QRectF(x1, y, x2 - x1, bar_h), radius, radius)
    elif chain in {"eth", "base"}:
        bg = QColor(36, 83, 255) if chain == "base" else QColor(84, 108, 255)
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(255, 255, 255, 44), 0.8))
        painter.drawEllipse(0.6, 0.6, size - 1.2, size - 1.2)
        painter.setBrush(QColor(232, 240, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        cx = size / 2
        painter.drawPolygon(QPolygonF([
            QPointF(cx, size * 0.18),
            QPointF(size * 0.31, size * 0.51),
            QPointF(cx, size * 0.42),
            QPointF(size * 0.69, size * 0.51),
        ]))
        painter.setBrush(QColor(176, 193, 255))
        painter.drawPolygon(QPolygonF([
            QPointF(cx, size * 0.46),
            QPointF(size * 0.31, size * 0.55),
            QPointF(cx, size * 0.82),
            QPointF(size * 0.69, size * 0.55),
        ]))
    elif chain == "bsc":
        painter.setBrush(QColor(242, 186, 47))
        painter.setPen(QPen(QColor(255, 245, 180, 110), 0.8))
        painter.drawEllipse(0.6, 0.6, size - 1.2, size - 1.2)
        painter.setBrush(QColor(12, 14, 14))
        painter.setPen(Qt.PenStyle.NoPen)
        d = max(1, int(size * 0.13))
        for x, y in ((0.5, 0.30), (0.5, 0.70), (0.30, 0.50), (0.70, 0.50), (0.5, 0.50)):
            painter.drawRect(int(size * x - d / 2), int(size * y - d / 2), d, d)
    else:
        painter.setBrush(QColor(49, 220, 141))
        painter.setPen(QPen(QColor(255, 255, 255, 42), 0.8))
        painter.drawEllipse(0.6, 0.6, size - 1.2, size - 1.2)
        painter.setPen(QColor(8, 15, 12))
        painter.setFont(QFont("Segoe UI", max(6, int(size * 0.46)), QFont.Weight.Black))
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, (chain[:1] or "?").upper())

    painter.end()
    return pix


@lru_cache(maxsize=32)
def _chain_svg_icon(chain: str, size: int) -> QPixmap:
    path = _chain_svg_path(chain)
    if path is None:
        return QPixmap()
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        LOG.info("invalid chain svg: %s", path)
        return QPixmap()

    dpr = 4.0
    pixel_size = max(16, int(round(size * dpr)))
    pix = QPixmap(pixel_size, pixel_size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    view = renderer.viewBoxF()
    if view.isEmpty():
        target = QRectF(0, 0, pixel_size, pixel_size)
    else:
        source_ratio = view.width() / view.height()
        target = QRectF(0, 0, pixel_size, pixel_size)
        if source_ratio > 1:
            target.setHeight(pixel_size / source_ratio)
            target.moveTop((pixel_size - target.height()) / 2)
        else:
            target.setWidth(pixel_size * source_ratio)
            target.moveLeft((pixel_size - target.width()) / 2)
    renderer.render(painter, target)
    painter.end()
    pix.setDevicePixelRatio(dpr)
    return pix


def clear_icon_caches() -> None:
    native_icon.cache_clear()
    _chain_svg_icon.cache_clear()


def _chain_svg_path(chain: str) -> Path | None:
    file_name = _CHAIN_SVG_FILES.get(chain)
    if not file_name:
        return None
    for root in _asset_roots():
        path = root / "chains" / file_name
        if path.exists():
            return path
    return None


def _asset_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.append(Path(frozen_root) / "gmgn_monitor" / "assets")
    roots.append(Path(__file__).resolve().parents[1] / "assets")
    return tuple(roots)


def tray_icon() -> QIcon:
    icon_path = _app_icon_path()
    if icon_path is not None:
        return QIcon(str(icon_path))
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QColor(10, 16, 20))
    painter.setPen(QColor(56, 255, 157, 180))
    painter.drawRoundedRect(4, 4, 56, 56, 16, 16)
    painter.setPen(QColor(235, 255, 246))
    font = painter.font()
    font.setBold(True)
    font.setPointSize(18)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "G")
    painter.end()
    return QIcon(pix)


def _app_icon_path() -> Path | None:
    for root in _asset_roots():
        path = root / "app.ico"
        if path.exists():
            return path
    return None
