from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor


Color = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class UiTheme:
    key: str
    label: str
    panel_top: Color
    panel_mid: Color
    panel_bottom: Color
    surface: Color
    surface_soft: Color
    field_bg: Color
    field_focus: Color
    list_bg: Color
    border: Color
    border_hover: Color
    text: Color
    text_soft: Color
    muted: Color
    dim: Color
    accent: Color
    accent_hover: Color
    accent_text: Color
    accent_soft: Color
    positive: Color
    negative: Color
    warning: Color
    info: Color
    shadow: Color

    def color(self, name: str, alpha: int | None = None) -> QColor:
        raw = getattr(self, name)
        if alpha is None:
            return QColor(*raw)
        return QColor(raw[0], raw[1], raw[2], max(0, min(255, int(alpha))))


THEMES: dict[str, UiTheme] = {
    "default": UiTheme(
        key="default",
        label="原皮",
        panel_top=(18, 27, 27, 248),
        panel_mid=(8, 13, 15, 246),
        panel_bottom=(4, 7, 9, 252),
        surface=(18, 31, 32, 238),
        surface_soft=(14, 25, 24, 220),
        field_bg=(10, 17, 18, 245),
        field_focus=(14, 25, 24, 248),
        list_bg=(8, 12, 14, 222),
        border=(132, 151, 148, 72),
        border_hover=(82, 239, 164, 132),
        text=(236, 246, 242, 255),
        text_soft=(188, 202, 198, 255),
        muted=(132, 151, 148, 255),
        dim=(93, 106, 106, 255),
        accent=(64, 239, 153, 255),
        accent_hover=(82, 235, 165, 255),
        accent_text=(6, 18, 13, 255),
        accent_soft=(15, 82, 56, 142),
        positive=(48, 235, 137, 255),
        negative=(255, 82, 103, 255),
        warning=(255, 214, 102, 255),
        info=(80, 178, 255, 255),
        shadow=(0, 0, 0, 112),
    ),
    "okx": UiTheme(
        key="okx",
        label="OKX",
        panel_top=(18, 20, 22, 250),
        panel_mid=(6, 7, 9, 248),
        panel_bottom=(1, 2, 4, 252),
        surface=(22, 24, 27, 238),
        surface_soft=(28, 31, 35, 218),
        field_bg=(8, 9, 12, 246),
        field_focus=(24, 27, 32, 250),
        list_bg=(6, 7, 10, 228),
        border=(230, 236, 240, 54),
        border_hover=(245, 249, 252, 132),
        text=(244, 247, 249, 255),
        text_soft=(200, 209, 216, 255),
        muted=(136, 146, 154, 255),
        dim=(91, 99, 107, 255),
        accent=(242, 246, 248, 255),
        accent_hover=(255, 255, 255, 255),
        accent_text=(5, 6, 8, 255),
        accent_soft=(238, 246, 255, 34),
        positive=(0, 211, 149, 255),
        negative=(255, 77, 90, 255),
        warning=(255, 197, 69, 255),
        info=(58, 153, 255, 255),
        shadow=(0, 0, 0, 118),
    ),
    "binance": UiTheme(
        key="binance",
        label="Binance",
        panel_top=(30, 35, 41, 250),
        panel_mid=(11, 14, 17, 248),
        panel_bottom=(5, 7, 9, 252),
        surface=(30, 35, 41, 238),
        surface_soft=(43, 49, 57, 220),
        field_bg=(15, 18, 22, 246),
        field_focus=(30, 35, 41, 250),
        list_bg=(11, 14, 17, 228),
        border=(252, 213, 53, 62),
        border_hover=(252, 213, 53, 150),
        text=(234, 236, 239, 255),
        text_soft=(196, 203, 212, 255),
        muted=(112, 122, 138, 255),
        dim=(86, 94, 108, 255),
        accent=(252, 213, 53, 255),
        accent_hover=(240, 185, 11, 255),
        accent_text=(24, 26, 32, 255),
        accent_soft=(252, 213, 53, 42),
        positive=(14, 203, 129, 255),
        negative=(246, 70, 93, 255),
        warning=(252, 213, 53, 255),
        info=(59, 130, 246, 255),
        shadow=(0, 0, 0, 120),
    ),
    "gmgn": UiTheme(
        key="gmgn",
        label="GMGN",
        panel_top=(11, 23, 18, 250),
        panel_mid=(4, 12, 10, 248),
        panel_bottom=(1, 6, 6, 252),
        surface=(10, 32, 24, 238),
        surface_soft=(7, 48, 33, 216),
        field_bg=(5, 17, 14, 246),
        field_focus=(8, 32, 24, 250),
        list_bg=(3, 10, 10, 228),
        border=(78, 255, 166, 70),
        border_hover=(80, 255, 166, 160),
        text=(236, 255, 246, 255),
        text_soft=(184, 221, 207, 255),
        muted=(120, 154, 143, 255),
        dim=(74, 101, 94, 255),
        accent=(66, 255, 154, 255),
        accent_hover=(103, 255, 184, 255),
        accent_text=(4, 15, 10, 255),
        accent_soft=(41, 255, 145, 45),
        positive=(50, 255, 142, 255),
        negative=(255, 61, 94, 255),
        warning=(255, 224, 89, 255),
        info=(54, 214, 255, 255),
        shadow=(0, 0, 0, 112),
    ),
    "claude": UiTheme(
        key="claude",
        label="Claude",
        panel_top=(37, 35, 32, 250),
        panel_mid=(24, 23, 21, 248),
        panel_bottom=(15, 14, 13, 252),
        surface=(42, 38, 33, 238),
        surface_soft=(54, 48, 40, 218),
        field_bg=(28, 26, 23, 246),
        field_focus=(47, 42, 35, 250),
        list_bg=(22, 21, 19, 228),
        border=(204, 120, 92, 72),
        border_hover=(232, 165, 90, 150),
        text=(250, 249, 245, 255),
        text_soft=(214, 209, 199, 255),
        muted=(160, 157, 150, 255),
        dim=(108, 106, 100, 255),
        accent=(204, 120, 92, 255),
        accent_hover=(232, 165, 90, 255),
        accent_text=(255, 255, 255, 255),
        accent_soft=(204, 120, 92, 44),
        positive=(93, 184, 114, 255),
        negative=(198, 69, 69, 255),
        warning=(232, 165, 90, 255),
        info=(93, 184, 166, 255),
        shadow=(0, 0, 0, 106),
    ),
}

SKIN_ORDER = ("default", "okx", "binance", "gmgn", "claude")
_active_skin = "default"


def normalize_skin(name: object) -> str:
    value = str(name or "").lower().strip()
    aliases = {"origin": "default", "original": "default", "base": "default", "原皮": "default"}
    value = aliases.get(value, value)
    return value if value in THEMES else "default"


def get_theme(name: object = "") -> UiTheme:
    return THEMES[normalize_skin(name or _active_skin)]


def set_active_theme(name: object) -> UiTheme:
    global _active_skin
    _active_skin = normalize_skin(name)
    return get_theme(_active_skin)


def active_theme() -> UiTheme:
    return get_theme(_active_skin)


def rgba(color: Color, alpha: int | None = None) -> str:
    a = color[3] if alpha is None else max(0, min(255, int(alpha)))
    return f"rgba({color[0]},{color[1]},{color[2]},{a})"


def hex_rgb(color: Color) -> str:
    return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"


def menu_stylesheet(theme: UiTheme | None = None) -> str:
    theme = theme or active_theme()
    return f"""
        QMenu {{
            background: {rgba(theme.panel_mid, 250)};
            color: {hex_rgb(theme.text)};
            border: 1px solid {rgba(theme.border, 120)};
            padding: 6px;
            border-radius: 8px;
        }}
        QMenu::separator {{
            height: 1px;
            background: {rgba(theme.border, 64)};
            margin: 5px 8px;
        }}
        QMenu::item {{
            padding: 8px 28px 8px 22px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background: {rgba(theme.accent, 58)};
            color: {hex_rgb(theme.text)};
        }}
        QMenu::indicator:checked {{
            image: none;
            background: {hex_rgb(theme.accent)};
            border-radius: 5px;
            width: 10px;
            height: 10px;
        }}
    """


def app_stylesheet(theme: UiTheme | None = None) -> str:
    theme = theme or active_theme()
    return f"""
        QToolTip {{
            color: {hex_rgb(theme.text)};
            background: {rgba(theme.panel_mid, 248)};
            border: 1px solid {rgba(theme.border, 110)};
            border-radius: 6px;
            padding: 5px 8px;
        }}
    """
