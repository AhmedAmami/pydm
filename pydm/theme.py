"""Look and feel: light/dark palettes, the stylesheet, and line icons drawn from
SVG so they follow the theme's colours and stay sharp at any scale."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent / "assets"

LIGHT = {
    "bg": "#f6f7f9", "surface": "#ffffff", "sidebar": "#eef1f5", "border": "#dfe3e8", "hover": "#eef3fb",
    "text": "#1d232b", "muted": "#6b7480", "accent": "#2563eb", "accent_hover": "#1d4ed8", "accent_text": "#ffffff",
    "selection": "#dbe7fd", "track": "#e6e9ee", "success": "#16a34a", "warning": "#d97706", "danger": "#dc2626",
    "merge": "#7c3aed",
}
DARK = {
    "bg": "#16181c", "surface": "#1e2126", "sidebar": "#191c20", "border": "#2e333a", "hover": "#262b33",
    "text": "#e6e9ee", "muted": "#8f98a3", "accent": "#3b82f6", "accent_hover": "#60a5fa", "accent_text": "#ffffff",
    "selection": "#1e3a66", "track": "#2c3139", "success": "#22c55e", "warning": "#f59e0b", "danger": "#ef4444",
    "merge": "#a78bfa",
}

colors = dict(LIGHT)  # the active palette; updated by apply()


def is_dark(mode: str) -> bool:
    if mode in ("light", "dark"):
        return mode == "dark"
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def app_icon() -> QIcon:
    icon = QIcon()
    for f in sorted(ASSETS.glob("icon-*.png")):
        icon.addFile(str(f))
    return icon


def apply(app, mode: str = "system") -> None:
    colors.clear()
    colors.update(DARK if is_dark(mode) else LIGHT)
    c = colors
    pal = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "surface"),
                      (QPalette.ColorRole.AlternateBase, "bg"), (QPalette.ColorRole.Button, "surface"),
                      (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.ToolTipBase, "surface"),
                      (QPalette.ColorRole.ToolTipText, "text"), (QPalette.ColorRole.Highlight, "accent"),
                      (QPalette.ColorRole.HighlightedText, "accent_text"), (QPalette.ColorRole.Mid, "border"),
                      (QPalette.ColorRole.PlaceholderText, "muted"), (QPalette.ColorRole.Link, "accent")):
        pal.setColor(role, QColor(c[key]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["muted"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["muted"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(c["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET.format(**c))
    icon.cache_clear()


STYLESHEET = """
* {{ font-family: "Segoe UI", system-ui, sans-serif; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {bg}; }}
QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 4px 6px; }}

#toolbar {{ background: {surface}; border-bottom: 1px solid {border}; }}
#toolbar QToolButton {{ border: none; border-radius: 6px; padding: 6px 9px; color: {text}; }}
#toolbar QToolButton:hover {{ background: {hover}; }}
#toolbar QToolButton:pressed {{ background: {selection}; }}
#toolbar QToolButton:disabled {{ color: {muted}; }}
QFrame#vline {{ background: {border}; max-width: 1px; margin: 8px 4px; }}

QPushButton {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 6px;
              padding: 6px 14px; }}
QPushButton:hover {{ background: {hover}; }}
QPushButton:disabled {{ color: {muted}; }}
QPushButton[primary="true"] {{ background: {accent}; color: {accent_text}; border: 1px solid {accent};
                               font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton[primary="true"]:disabled {{ background: {track}; border-color: {track}; color: {muted}; }}
QPushButton[danger="true"] {{ background: {danger}; color: #ffffff; border: 1px solid {danger}; font-weight: 600; }}
QPushButton[danger="true"]:hover {{ background: {danger}; border-color: {text}; }}
QPushButton[flat="true"] {{ border: none; background: transparent; color: {accent}; padding: 4px 6px; }}
QPushButton[flat="true"]:hover {{ text-decoration: underline; }}
QProgressBar {{ background: {border}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

QLineEdit, QComboBox {{ background: {surface}; color: {text}; border: 1px solid {border};
                                  border-radius: 6px; padding: 5px 8px; selection-background-color: {accent}; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {accent}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {muted}; background: {bg}; }}
QLineEdit#search {{ border-radius: 15px; padding-left: 10px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {surface}; border: 1px solid {border}; selection-background-color: {selection};
                               selection-color: {text}; outline: none; }}

QListWidget#sidebar {{ background: {sidebar}; border: none; border-right: 1px solid {border}; outline: none;
                       padding: 8px 6px; }}
QListWidget#sidebar::item {{ border-radius: 6px; padding: 7px 8px; margin: 1px 0; color: {text}; }}
QListWidget#sidebar::item:hover {{ background: {hover}; }}
QListWidget#sidebar::item:selected {{ background: {selection}; color: {text}; font-weight: 600; }}

QTableView {{ background: {surface}; border: none; outline: none; gridline-color: transparent;
              selection-background-color: {selection}; selection-color: {text}; }}
QTableView::item {{ border-bottom: 1px solid {border}; padding-left: 6px; }}
QTableView::item:hover {{ background: transparent; }}
QTableView::item:selected {{ background: {selection}; color: {text}; }}
QHeaderView::section {{ background: {surface}; color: {muted}; border: none; border-bottom: 1px solid {border};
                        padding: 7px 8px; font-weight: 600; font-size: 9pt; }}

#doneWindow {{ background: {surface}; border: 1px solid {border}; }}
#queueBar {{ background: {surface}; border-bottom: 1px solid {border}; }}
QToolButton#dayButton {{ border: 1px solid {border}; border-radius: 6px; padding: 4px 7px; background: {surface}; color: {text}; }}
QToolButton#dayButton:checked {{ background: {accent}; border-color: {accent}; color: {accent_text}; }}
QToolButton#dayButton:disabled {{ background: {bg}; color: {muted}; border-color: {border}; }}
QToolButton#dayButton:checked:disabled {{ background: {track}; color: {muted}; }}
#details {{ background: {surface}; border-top: 1px solid {border}; }}
#detailsTitle {{ font-size: 11.5pt; font-weight: 600; }}
QLabel[muted="true"] {{ color: {muted}; }}
QLabel#error {{ color: {danger}; }}
#emptyTitle {{ font-size: 14pt; font-weight: 600; }}
#previewTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#thumb {{ background: {track}; border-radius: 8px; }}
#dialogTitle {{ font-size: 13pt; font-weight: 600; }}
QFrame#card {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; }}

QSplitter::handle {{ background: {border}; }}
QStatusBar {{ background: {surface}; border-top: 1px solid {border}; color: {muted}; }}
QStatusBar::item {{ border: none; }}
QToolButton#themeButton {{ border: none; border-radius: 6px; padding: 6px 4px 6px 8px; }}
QToolButton#themeButton:hover {{ background: {hover}; }}
QToolButton#themeButton::menu-button {{ border: none; width: 14px; }}
QToolButton#limitButton {{ border: none; border-radius: 5px; padding: 2px 8px; color: {muted}; }}
QToolButton#limitButton:hover {{ background: {hover}; color: {text}; }}
QToolButton#limitButton::menu-indicator {{ image: none; }}
QMenu {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 22px 6px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {selection}; }}
QMenu::item:disabled {{ color: {muted}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}
QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; background: {surface}; top: -1px; }}
QTabBar::tab {{ padding: 7px 14px; color: {muted}; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ width: 0; height: 0;
                                                                                          background: none; }}
QProgressBar {{ background: {track}; border: none; border-radius: 4px; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
"""

# --- icons ----------------------------------------------------------------------
# Line icons on a 24x24 grid (in the style of Lucide, MIT).

PATHS = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>',
    "pause": '<rect x="6" y="4.5" width="4" height="15" rx="1"/><rect x="14" y="4.5" width="4" height="15" rx="1"/>',
    "play-all": '<circle cx="12" cy="12" r="9.5"/><path d="M10 8.5v7l5.5-3.5z"/>',
    "pause-all": '<circle cx="12" cy="12" r="9.5"/><path d="M10 9v6M14 9v6"/>',
    "trash": '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2'
             'M10 11v6M14 11v6"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4'
              'a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "open": '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "settings": '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3'
            'a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "copy": '<rect x="8" y="8" width="14" height="14" rx="2"/><path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10'
            'a2 2 0 0 1 2 2"/>',
    "retry": '<path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M8 16H3v5"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6'
             'l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    "check": '<circle cx="12" cy="12" r="9.5"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    "clock": '<circle cx="12" cy="12" r="9.5"/><path d="M12 7v5l3 2"/>',
    "alert": '<circle cx="12" cy="12" r="9.5"/><path d="M12 7.5v5M12 16.2v.1"/>',
    "globe": '<circle cx="12" cy="12" r="9.5"/><path d="M2.5 12h19M12 2.5a14.5 14.5 0 0 1 0 19'
             'a14.5 14.5 0 0 1 0-19z"/>',
    "video": '<rect x="2.5" y="4.5" width="19" height="15" rx="2.5"/><path d="m10 9 5 3-5 3z"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "archive": '<rect x="2.5" y="3" width="19" height="5" rx="1"/><path d="M4.5 8v11a2 2 0 0 0 2 2h11'
               'a2 2 0 0 0 2-2V8M10 12h4"/>',
    "program": '<rect x="3" y="3.5" width="18" height="17" rx="2.5"/><path d="M3 8.5h18M7 13l2.5 2L7 17M12 17h4"/>',
    "document": '<path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"/><path d="M14 2.5V8h5.5'
                'M8.5 13h7M8.5 17h7"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="9" cy="9" r="2"/>'
             '<path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    "file": '<path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"/><path d="M14 2.5V8h5.5"/>',
    "calendar": '<rect x="3" y="4.5" width="18" height="17" rx="2.5"/><path d="M8 2.5v4M16 2.5v4M3 10h18'
                '"/><path d="M8 14h2M14 14h2M8 17.5h2"/>',
    "up": '<path d="m6 15 6-6 6 6"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "more": '<circle cx="5" cy="12" r="1.6" fill="{color}"/><circle cx="12" cy="12" r="1.6" fill="{color}"/>'
            '<circle cx="19" cy="12" r="1.6" fill="{color}"/>',
    "columns": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M3 14.5h18M9 9.5v10.5"/>',
    "rows": '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/>',
    "gauge": '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41'
           'M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "theme": '<circle cx="12" cy="12" r="9.5"/><path d="M12 2.5a9.5 9.5 0 0 0 0 19z" fill="{color}"/>',
}


def _svg(name: str, color: str, stroke: float = 1.8) -> bytes:
    body = PATHS[name].replace("{color}", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def _render(svg: bytes, size: int, scale: float = 2.0) -> QPixmap:
    px = QPixmap(int(size * scale), int(size * scale))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg)).render(p, QRectF(0, 0, px.width(), px.height()))
    p.end()
    px.setDevicePixelRatio(scale)
    return px


@lru_cache(maxsize=None)
def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    """A theme-coloured line icon; disabled state drawn in the muted colour."""
    result = QIcon()
    result.addPixmap(_render(_svg(name, color or colors["text"]), size), QIcon.Mode.Normal)
    result.addPixmap(_render(_svg(name, colors["muted"]), size), QIcon.Mode.Disabled)
    return result


# --- file types -----------------------------------------------------------------

TYPES = {
    "video": ("Videos", "#8b5cf6", {"mp4", "mkv", "webm", "avi", "mov", "wmv", "flv", "m4v", "ts", "3gp", "mpg"}),
    "music": ("Music", "#ec4899", {"mp3", "flac", "wav", "aac", "ogg", "m4a", "opus", "wma"}),
    "document": ("Documents", "#3b82f6", {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "csv",
                                          "odt", "rtf", "md"}),
    "archive": ("Archives", "#f59e0b", {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "img", "dmg", "cab"}),
    "program": ("Programs", "#14b8a6", {"exe", "msi", "apk", "deb", "rpm", "appimage", "msix", "bat"}),
    "image": ("Images", "#22c55e", {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "tif", "tiff", "heic"}),
}


def file_type(filename: str, kind: str = "file") -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for key, (_, _, exts) in TYPES.items():
        if ext in exts:
            return key
    return "video" if kind in ("hls", "dash", "ytdl") else "file"


@lru_cache(maxsize=None)
def type_badge(kind: str, size: int = 28) -> QIcon:
    """A tinted rounded square with the file type's glyph, for the downloads list."""
    color = TYPES[kind][1] if kind in TYPES else "#64748b"
    scale = 2.0
    px = QPixmap(int(size * scale), int(size * scale))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    tint = QColor(color)
    tint.setAlpha(38)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(tint)
    p.drawRoundedRect(QRectF(0, 0, px.width(), px.height()), 7 * scale, 7 * scale)
    pad = px.width() * 0.22
    QSvgRenderer(QByteArray(_svg(kind if kind in PATHS else "file", color, 2.0))).render(
        p, QRectF(pad, pad, px.width() - 2 * pad, px.height() - 2 * pad))
    p.end()
    px.setDevicePixelRatio(scale)
    return QIcon(px)
