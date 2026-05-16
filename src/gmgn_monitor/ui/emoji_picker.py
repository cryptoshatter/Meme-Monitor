from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class EmojiEntry:
    emoji: str
    name: str
    group: str
    subgroup: str


GROUP_LABELS = {
    "Smileys & Emotion": "Smileys",
    "People & Body": "People",
    "Component": "Component",
    "Animals & Nature": "Nature",
    "Food & Drink": "Food",
    "Travel & Places": "Travel",
    "Activities": "Activity",
    "Objects": "Objects",
    "Symbols": "Symbols",
    "Flags": "Flags",
}

GROUP_ICONS = {
    "Smileys & Emotion": "\U0001f600",
    "People & Body": "\U0001faf6",
    "Component": "\U0001f9e9",
    "Animals & Nature": "\U0001f331",
    "Food & Drink": "\U0001f354",
    "Travel & Places": "\U0001f697",
    "Activities": "\u26bd",
    "Objects": "\U0001f4a1",
    "Symbols": "\u2665\ufe0f",
    "Flags": "\U0001f3c1",
}


class EmojiGrid(QWidget):
    selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.entries: list[EmojiEntry] = []
        self.columns = 8
        self.cell = 40
        self.hover_index = -1
        self.selected_emoji = ""
        self._font = QFont("Segoe UI Emoji", 20)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_entries(self, entries: list[EmojiEntry], selected_emoji: str = "") -> None:
        self.entries = entries
        self.selected_emoji = selected_emoji
        self.hover_index = -1
        rows = max(1, (len(entries) + self.columns - 1) // self.columns)
        self.setMinimumHeight(rows * self.cell)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        rows = max(1, (len(self.entries) + self.columns - 1) // self.columns)
        return QSize(self.columns * self.cell, rows * self.cell)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self._font)
        metrics = QFontMetrics(self._font)
        first_row = max(0, event.rect().top() // self.cell)
        last_row = min((len(self.entries) + self.columns - 1) // self.columns, event.rect().bottom() // self.cell + 1)
        for row in range(first_row, last_row):
            for col in range(self.columns):
                index = row * self.columns + col
                if index >= len(self.entries):
                    break
                rect = self._cell_rect(index)
                emoji = self.entries[index].emoji
                if emoji == self.selected_emoji or index == self.hover_index:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(255, 255, 255, 30 if emoji == self.selected_emoji else 18))
                    painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 9, 9)
                painter.setPen(QColor(255, 255, 255))
                y = rect.top() + (rect.height() + metrics.ascent() - metrics.descent()) // 2
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, emoji)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        index = self._index_at(event.position().toPoint().x(), event.position().toPoint().y())
        if index != self.hover_index:
            old = self.hover_index
            self.hover_index = index
            if old >= 0:
                self.update(self._cell_rect(old))
            if index >= 0:
                self.update(self._cell_rect(index))

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self.hover_index >= 0:
            old = self.hover_index
            self.hover_index = -1
            self.update(self._cell_rect(old))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        index = self._index_at(event.position().toPoint().x(), event.position().toPoint().y())
        if index < 0 or index >= len(self.entries):
            return
        emoji = self.entries[index].emoji
        self.selected_emoji = emoji
        self.update()
        self.selected.emit(emoji)

    def _index_at(self, x: int, y: int) -> int:
        col = x // self.cell
        row = y // self.cell
        if col < 0 or col >= self.columns or row < 0:
            return -1
        index = row * self.columns + col
        return index if index < len(self.entries) else -1

    def _cell_rect(self, index: int):
        row = index // self.columns
        col = index % self.columns
        return self.rect().adjusted(0, 0, 0, 0).__class__(col * self.cell, row * self.cell, self.cell, self.cell)


class EmojiPicker(QDialog):
    emoji_selected = Signal(str)
    avatar_selected = Signal(str, str)

    def __init__(self, current: str = "", avatar_kind: str = "emoji", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Avatar")
        self.setModal(True)
        self.setFixedSize(392, 556)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.selected_kind = "image" if avatar_kind == "image" else "emoji"
        self.selected_emoji = first_emoji(current) if self.selected_kind == "emoji" else "\U0001f9e9"
        self.selected_emoji = self.selected_emoji or "\U0001f9e9"
        self.selected_image = current if self.selected_kind == "image" else ""
        self._active_group = "Smileys & Emotion"
        self._drag_origin = None

        self.root = QWidget(self)
        self.root.setObjectName("root")
        self.root.setGeometry(7, 7, self.width() - 14, self.height() - 14)

        layout = QVBoxLayout(self.root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.emoji_tab = QPushButton("Emoji")
        self.emoji_tab.setCheckable(True)
        self.emoji_tab.clicked.connect(lambda: self._set_tab("emoji"))
        self.image_tab = QPushButton("自定义图片")
        self.image_tab.setCheckable(True)
        self.image_tab.clicked.connect(lambda: self._set_tab("image"))
        tabs.addWidget(self.emoji_tab)
        tabs.addWidget(self.image_tab)
        layout.addLayout(tabs)

        self.emoji_page = QWidget()
        emoji_layout = QVBoxLayout(self.emoji_page)
        emoji_layout.setContentsMargins(0, 0, 0, 0)
        emoji_layout.setSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.textChanged.connect(self._render_grid)
        emoji_layout.addWidget(self.search)

        group_bar = QHBoxLayout()
        group_bar.setSpacing(5)
        self.group_buttons: list[QPushButton] = []
        for group in ordered_groups():
            button = QPushButton(GROUP_ICONS.get(group, "?"))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setCheckable(True)
            button.setToolTip(GROUP_LABELS.get(group, group))
            button.clicked.connect(lambda _checked=False, value=group: self._select_group(value))
            self.group_buttons.append(button)
            group_bar.addWidget(button)
        emoji_layout.addLayout(group_bar)

        self.group_title = QLabel()
        emoji_layout.addWidget(self.group_title)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.grid = EmojiGrid()
        self.grid.selected.connect(self._choose)
        self.scroll.setWidget(self.grid)
        emoji_layout.addWidget(self.scroll, 1)

        self.image_page = QWidget()
        image_layout = QVBoxLayout(self.image_page)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(0)
        image_layout.addStretch(1)
        self.upload_button = QPushButton("+\n上传自定义头像\n推荐正方形图片，支持 PNG/JPG")
        self.upload_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upload_button.setFixedSize(220, 220)
        self.upload_button.clicked.connect(self._choose_image)
        center_row = QHBoxLayout()
        center_row.addStretch(1)
        center_row.addWidget(self.upload_button)
        center_row.addStretch(1)
        image_layout.addLayout(center_row)
        self.image_path_label = QLabel()
        self.image_path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_layout.addWidget(self.image_path_label)
        image_layout.addStretch(1)

        layout.addWidget(self.emoji_page, 1)
        layout.addWidget(self.image_page, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.selected_label = QLabel(self.selected_emoji)
        self.selected_label.setFixedSize(56, 56)
        self.selected_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selected_label.setFont(QFont("Segoe UI Emoji", 30))
        footer.addWidget(self.selected_label)
        footer.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self._accept_current)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.ok_button)
        layout.addLayout(footer)

        self._apply_styles()
        self._select_group(self._active_group)
        self._set_tab(self.selected_kind)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 90))
        painter.drawRoundedRect(self.rect().adjusted(7, 12, -7, -4), 18, 18)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._drag_origin = None

    def _select_group(self, group: str) -> None:
        self._active_group = group
        for button in self.group_buttons:
            button.setChecked(button.toolTip() == GROUP_LABELS.get(group, group))
        self._render_grid()

    def _render_grid(self) -> None:
        query = self.search.text().strip()
        entries = filter_entries(self._active_group, query)
        self.group_title.setText("Search Results" if query else GROUP_LABELS.get(self._active_group, self._active_group))
        self.grid.set_entries(entries, self.selected_emoji)
        self.scroll.verticalScrollBar().setValue(0)

    def _choose(self, emoji: str) -> None:
        self.selected_emoji = emoji
        self.selected_label.setText(emoji)
        self.grid.selected_emoji = emoji
        self.grid.update()

    def _accept_current(self) -> None:
        if self.selected_kind == "image" and self.selected_image:
            self.avatar_selected.emit("image", self.selected_image)
        else:
            self.selected_kind = "emoji"
            self.avatar_selected.emit("emoji", self.selected_emoji)
            self.emoji_selected.emit(self.selected_emoji)
        self.accept()

    def _set_tab(self, tab: str) -> None:
        self.selected_kind = "image" if tab == "image" else "emoji"
        self.emoji_tab.setChecked(self.selected_kind == "emoji")
        self.image_tab.setChecked(self.selected_kind == "image")
        self.emoji_page.setVisible(self.selected_kind == "emoji")
        self.image_page.setVisible(self.selected_kind == "image")
        self.ok_button.setText("应用" if self.selected_kind == "image" else "OK")
        self.ok_button.setEnabled(self.selected_kind == "emoji" or bool(self.selected_image))
        if self.selected_kind == "image":
            self.selected_label.setVisible(False)
            self._update_image_path_label()
        else:
            self.selected_label.setVisible(True)
            self.selected_label.setText(self.selected_emoji)

    def _choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Avatar",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not path:
            return
        self.selected_image = path
        self._update_image_path_label()
        self.ok_button.setEnabled(True)

    def _update_image_path_label(self) -> None:
        if self.selected_image:
            self.image_path_label.setText(Path(self.selected_image).name)
            self.selected_label.setText("\U0001f5bc")
        else:
            self.image_path_label.setText("")
            self.selected_label.setText("\U0001f5bc")

    def _apply_styles(self) -> None:
        self.root.setStyleSheet(
            """
            QWidget#root {
                background: #1d1f23;
                border: 1px solid rgba(255,255,255,32);
                border-radius: 12px;
            }
            QLineEdit {
                min-height: 34px;
                border: 1px solid rgba(255,255,255,18);
                border-radius: 8px;
                padding: 8px 12px;
                color: #f1f3f5;
                background: #37393d;
                selection-color: #ffffff;
                selection-background-color: #287857;
                font: 600 12px "Segoe UI Variable Text";
            }
            QLabel {
                color: #d9dddf;
                font: 800 13px "Segoe UI Variable Text";
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                width: 10px;
                background: #292b2f;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #85898d;
                border-radius: 5px;
                min-height: 32px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QPushButton {
                border: none;
                color: #d7dcdf;
                background: transparent;
                border-radius: 8px;
                font: 800 12px "Segoe UI Variable Text";
            }
            QPushButton:hover {
                background: rgba(255,255,255,22);
                color: #ffffff;
            }
            """
        )
        tab_css = """
            QPushButton {
                min-height: 30px;
                border: 1px solid rgba(255,255,255,28);
                border-radius: 7px;
                background: #14161a;
                color: #9da3a8;
                font: 800 12px "Segoe UI Variable Text";
            }
            QPushButton:checked {
                background: #282a2f;
                color: #f2f4f5;
            }
        """
        self.emoji_tab.setStyleSheet(tab_css)
        self.image_tab.setStyleSheet(tab_css)
        group_css = """
            QPushButton {
                min-width: 28px;
                min-height: 28px;
                border-radius: 7px;
                border: 1px solid transparent;
                background: transparent;
                color: #a7abb0;
                font: 700 17px "Segoe UI Emoji";
            }
            QPushButton:checked, QPushButton:hover {
                background: rgba(255,255,255,18);
                border-color: rgba(255,255,255,24);
                color: #ffffff;
            }
        """
        for button in self.group_buttons:
            button.setStyleSheet(group_css)
        self.cancel_button.setStyleSheet(
            """
            QPushButton {
                min-width: 72px;
                min-height: 32px;
                border: 1px solid rgba(255,255,255,26);
                border-radius: 9px;
                background: rgba(255,255,255,8);
                color: #dfe5e7;
                font: 800 12px "Segoe UI Variable Text";
            }
            QPushButton:hover { background: rgba(255,255,255,16); }
            """
        )
        self.ok_button.setStyleSheet(
            """
            QPushButton {
                min-width: 72px;
                min-height: 32px;
                border: 1px solid rgba(103,255,184,94);
                border-radius: 9px;
                background: #39d88f;
                color: #05140d;
                font: 900 12px "Segoe UI Variable Text";
            }
            QPushButton:hover { background: #52eba5; }
            """
        )
        self.selected_label.setStyleSheet("background: #15171a; border-radius: 28px;")
        self.grid.setStyleSheet("background: transparent;")
        self.upload_button.setStyleSheet(
            """
            QPushButton {
                border: 1px dashed rgba(154, 162, 170, 80);
                border-radius: 8px;
                background: transparent;
                color: #dfe5e7;
                font: 700 13px "Segoe UI Variable Text";
                line-height: 1.45;
            }
            QPushButton:hover {
                border-color: rgba(103,255,184,115);
                background: rgba(255,255,255,6);
                color: #ffffff;
            }
            """
        )
        self.image_path_label.setStyleSheet('color: #8b9299; font: 700 11px "Segoe UI Variable Text";')

    def sizeHint(self) -> QSize:
        return QSize(392, 556)


def filter_entries(group: str, query: str) -> list[EmojiEntry]:
    if not query:
        return [entry for entry in load_emoji_entries() if entry.group == group]
    entries = list(load_emoji_entries())
    words = query.lower().split()
    return [entry for entry in entries if all(word in entry.name for word in words)]


def ordered_groups() -> list[str]:
    seen: list[str] = []
    for entry in load_emoji_entries():
        if entry.group not in seen:
            seen.append(entry.group)
    return seen


@lru_cache(maxsize=1)
def load_emoji_entries() -> tuple[EmojiEntry, ...]:
    path = emoji_data_path()
    if not path.exists():
        return fallback_entries()
    entries: list[EmojiEntry] = []
    group = "Smileys & Emotion"
    subgroup = ""
    pattern = re.compile(r"^([0-9A-F ]+)\s*;\s*fully-qualified\s*#\s*(\S+)\s+E[\d.]+\s+(.+)$")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("# group:"):
            group = line.split(":", 1)[1].strip()
            continue
        if line.startswith("# subgroup:"):
            subgroup = line.split(":", 1)[1].strip()
            continue
        match = pattern.match(line)
        if not match:
            continue
        codes, emoji, name = match.groups()
        entries.append(EmojiEntry(emoji=emoji, name=name.lower(), group=group, subgroup=subgroup or codes))
    return tuple(entries) if entries else fallback_entries()


def emoji_data_path() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidate = Path(frozen_root) / "gmgn_monitor" / "assets" / "emoji" / "emoji-test.txt"
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parents[1] / "assets" / "emoji" / "emoji-test.txt"


def fallback_entries() -> tuple[EmojiEntry, ...]:
    names = [
        ("\U0001f600", "grinning face"),
        ("\U0001f525", "fire"),
        ("\u26a1", "high voltage"),
        ("\U0001f48e", "gem stone"),
        ("\U0001f3af", "bullseye"),
        ("\U0001f680", "rocket"),
        ("\U0001f4b0", "money bag"),
        ("\U0001f9e9", "puzzle piece"),
    ]
    return tuple(EmojiEntry(emoji=emoji, name=name, group="Smileys & Emotion", subgroup="fallback") for emoji, name in names)


def first_emoji(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    chars: list[str] = []
    include_next = False
    for char in text:
        if not chars and char.isspace():
            continue
        code = ord(char)
        if not chars:
            chars.append(char)
            continue
        if code in {0x200D, 0xFE0E, 0xFE0F} or 0x1F3FB <= code <= 0x1F3FF or include_next:
            chars.append(char)
            include_next = code == 0x200D
            continue
        break
    return "".join(chars)
