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

from gmgn_monitor.ui.theme import active_theme, get_theme, hex_rgb, rgba


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
        self._theme = active_theme()

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
        self._apply_styles()

    def set_theme(self, skin: str) -> None:
        self._theme = get_theme(skin)
        self._apply_styles()
        self.update()

    def _apply_styles(self) -> None:
        theme = self._theme
        self.setStyleSheet("""
            QDialog {{
                background: transparent;
                border-radius: 18px;
            }}
            QLabel#title {{
                color: {text};
                font: 800 20px "Microsoft YaHei UI";
            }}
            QLabel#hint {{
                color: {muted};
                font: 600 11px "Microsoft YaHei UI";
                line-height: 150%;
            }}
            QLineEdit {{
                background: {field_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 12px;
                padding: 9px 11px;
                font: 700 12px "Cascadia Mono";
                selection-color: {accent_text};
                selection-background-color: {accent};
            }}
            QLineEdit:focus {{
                border: 1px solid {focus_border};
                background: {field_focus};
            }}
            QCheckBox {{
                color: {muted};
                font: 700 10px "Microsoft YaHei UI";
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 1px solid {border};
                background: {surface};
            }}
            QCheckBox::indicator:checked {{
                background: {accent};
                border: 1px solid {accent};
            }}
            QPushButton {{
                border-radius: 11px;
                border: 1px solid {button_border};
                color: {text_soft};
                background: {button_bg};
                font: 800 12px "Microsoft YaHei UI";
                padding: 8px 13px;
            }}
            QPushButton:hover {{
                background: {button_hover};
                color: {text};
            }}
            QPushButton#primary {{
                border: 1px solid {focus_border};
                color: {accent_text};
                background: {accent};
            }}
            QPushButton#primary:hover {{
                background: {accent_hover};
            }}
            QPushButton#link {{
                border: 1px solid {info_border};
                color: {info};
                background: {info_bg};
            }}
            QPushButton#link:hover {{
                background: {info_hover};
            }}
        """.format(
            text=hex_rgb(theme.text),
            muted=hex_rgb(theme.muted),
            field_bg=rgba(theme.field_bg, 245),
            border=rgba(theme.border, 86),
            focus_border=rgba(theme.border_hover, 180),
            field_focus=rgba(theme.field_focus, 250),
            accent_text=hex_rgb(theme.accent_text),
            accent=hex_rgb(theme.accent),
            surface=rgba(theme.surface, 130),
            button_border=rgba(theme.border, 58),
            text_soft=hex_rgb(theme.text_soft),
            button_bg=rgba(theme.surface, 165),
            button_hover=rgba(theme.surface_soft, 220),
            accent_hover=hex_rgb(theme.accent_hover),
            info_border=rgba(theme.info, 88),
            info=hex_rgb(theme.info),
            info_bg=rgba(theme.info, 38),
            info_hover=rgba(theme.info, 65),
        ))

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
        painter.setBrush(self._theme.color("shadow", 112))
        painter.drawRoundedRect(shadow, 22, 22)

        path = QPainterPath()
        path.addRoundedRect(rect, 22, 22)
        base = QLinearGradient(rect.topLeft(), rect.bottomRight())
        base.setColorAt(0.0, self._theme.color("panel_top"))
        base.setColorAt(0.50, self._theme.color("panel_mid"))
        base.setColorAt(1.0, self._theme.color("panel_bottom"))
        painter.fillPath(path, base)

        glow = QLinearGradient(rect.topLeft(), rect.topRight())
        glow.setColorAt(0.0, self._theme.color("accent", 28))
        glow.setColorAt(0.36, self._theme.color("accent", 8))
        glow.setColorAt(1.0, self._theme.color("accent", 0))
        painter.fillPath(path, glow)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._theme.color("border", 82), 1.05))
        painter.drawPath(path)
