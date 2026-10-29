"""PyQt6 front end. The engine runs on its own threads; the window just polls
task snapshots on a timer, so no Qt objects are touched off the GUI thread."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QEvent, QPointF, QRectF, QTime, QSettings, QSize, QStandardPaths, Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPushButton, QSpinBox, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QTabWidget, QTimeEdit, QToolButton, QVBoxLayout, QWidget,
)

from PyQt6.QtNetwork import QLocalServer

from . import __version__, buildcheck, install_browser, ipc, scan, streams, theme, updates, ytdl
from . import log as pydm_log
from .log import log
from .engine import DownloadManager, DownloadTask, Status, probe_size
from .queues import AFTER_ACTIONS, DAY_NAMES, MAIN_QUEUE, QueueManager, power_action
from .format import human_size, human_time

APP_NAME = "PyDM"
COLUMNS = ["", "File name", "Size", "Progress", "Speed", "Time left", "Status", "Added"]
COL_CHECK, COL_NAME, COL_SIZE, COL_PROGRESS, COL_SPEED, COL_ETA, COL_STATUS, COL_ADDED = range(len(COLUMNS))
VIDEO_KINDS = ("hls", "dash", "ytdl")
STATUS_COLOR_KEYS = {
    "Threat found": "danger",
    Status.COMPLETED: "success", Status.ERROR: "danger", Status.PAUSED: "warning", Status.CANCELLED: "muted",
    Status.DOWNLOADING: "accent", Status.PROBING: "accent", Status.MERGING: "merge", Status.QUEUED: "muted",
}
# Sidebar: (key, label, icon). None draws a section header.
CATEGORIES = [
    ("all", "All downloads", "inbox"), ("active", "Downloading", "download"),
    ("completed", "Completed", "check"), ("unfinished", "Unfinished", "clock"),
    None,
    *[(key, label, key) for key, (label, _, _) in theme.TYPES.items()],
]
DATA = Qt.ItemDataRole.UserRole
HEADER = Qt.ItemDataRole.UserRole + 1   # sidebar section headings ("types", "queues")
# Copied links with these extensions (or video pages) open the Add dialog.
CAPTURE_EXTENSIONS = {
    "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "img", "dmg", "exe", "msi", "apk", "deb", "rpm",
    "appimage", "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mp3", "flac", "wav", "aac", "ogg",
    "m4a", "pdf", "epub", "torrent", "bin", "m3u8", "mpd",
}
# "Sort downloads into folders by type": subfolder of the download folder per type.
TYPE_FOLDERS = {"video": "Video", "music": "Music", "document": "Documents", "archive": "Compressed",
                "program": "Programs", "image": "Images"}
SPEED_PRESETS_KB = [0, 256, 512, 1024, 2048, 5120, 10240]


def speed_text(kb: int) -> str:
    if not kb:
        return "Unlimited"
    return f"{kb / 1024:g} MB/s" if kb >= 1024 else f"{kb} KB/s"


def url_key(url: str) -> str:
    """What makes two links "the same download": host, path and query; for
    YouTube, the video id (watch?v=, youtu.be/, shorts/ all match)."""
    url = url.strip()
    if ytdl.is_youtube(url):
        m = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]{11})", url)
        if m:
            return "youtube:" + m.group(1)
    u = QUrl(url)
    return f"{u.host().lower()}{u.path()}?{u.query()}"


def default_download_dir() -> str:
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) \
        or str(Path.home() / "Downloads")


def in_test_mode() -> bool:
    """QStandardPaths test mode (used by the tests). PyQt has no isTestModeEnabled(),
    but test mode puts data under a "qttest" folder."""
    path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return "qttest" in path.replace("\\", "/").lower().split("/")


def force_foreground(widget) -> None:
    """Bring a window in front of the browser. Windows normally only flashes the
    taskbar button of a background app that asks for this; the browser's native
    host allows it first (AllowSetForegroundWindow), and if that wasn't enough the
    usual workaround is to share input with the current foreground window briefly."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        hwnd = int(widget.winId())
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if user32.GetForegroundWindow() == hwnd or user32.SetForegroundWindow(hwnd):
            return
        foreground = user32.GetForegroundWindow()
        their_thread = user32.GetWindowThreadProcessId(foreground, None)
        our_thread = kernel32.GetCurrentThreadId()
        if their_thread and their_thread != our_thread and user32.AttachThreadInput(their_thread, our_thread, True):
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(their_thread, our_thread, False)
    except Exception as e:  # noqa: BLE001 - only cosmetic
        log.warning("Couldn't bring the window to the front: %s", e)


def looks_like_url(text: str) -> bool:
    return text.strip().lower().startswith(("http://", "https://"))


def reveal_in_explorer(path: str) -> None:
    if sys.platform == "win32" and os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


def status_color(status: str) -> QColor:
    return QColor(theme.colors[STATUS_COLOR_KEYS.get(status, "muted")])


def friendly_date(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    if d.date() == today:
        return d.strftime("Today %H:%M")
    if (today - d.date()).days == 1:
        return d.strftime("Yesterday %H:%M")
    return d.strftime("%d %b %Y") if d.year != today.year else d.strftime("%d %b %H:%M")


def dot(color: QColor, size: int = 10) -> QPixmap:
    px = QPixmap(size * 2, size * 2)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QRectF(size * 0.5, size * 0.5, size, size))
    p.end()
    px.setDevicePixelRatio(2)
    return px


def task_category(task, s: dict) -> set[str]:
    status = s["status"]
    cats = {"all", theme.file_type(s["filename"] or "", s["kind"])}
    if status in Status.ACTIVE or status == Status.QUEUED:
        cats.add("active")
    elif status == Status.COMPLETED:
        cats.add("completed")
    else:
        cats.add("unfinished")
    return cats


def primary(button: QPushButton) -> QPushButton:
    button.setProperty("primary", True)
    return button


# --- widgets -----------------------------------------------------------------

class SegmentBar(QWidget):
    """Draws each connection's byte range and how much of it is done."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(14)
        self.setMaximumHeight(14)
        self._total = 0
        self._segments: list[tuple] = []

    def set_data(self, total: int | None, segments: list[tuple]) -> None:
        self._total, self._segments = total or 0, segments
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        c = theme.colors
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["track"]))
        p.drawRoundedRect(rect, 4, 4)
        if self._total <= 0:
            return
        p.setClipRect(rect)
        w = rect.width()
        done_color, active_color = QColor(c["accent"]), QColor(c["accent_hover"])
        pending = QColor(c["accent"])
        pending.setAlpha(70)
        for start, end, done, active in self._segments:
            x = start / self._total * w
            if done:
                p.fillRect(QRectF(x, 0, max(1.0, done / self._total * w), rect.height()),
                           active_color if active else done_color)
            elif active:  # a video segment being fetched
                p.fillRect(QRectF(x, 0, max(1.0, w / self._total), rect.height()), pending)
            if len(self._segments) <= 64 and x > 0:  # dividers would just be noise for long videos
                p.fillRect(QRectF(x, 0, 1, rect.height()), QColor(c["surface"]))


def draw_checkbox(p: QPainter, rect, state: int) -> None:
    """state: 0 unchecked, 1 partly (some rows selected), 2 checked."""
    c = theme.colors
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    box = QRectF(rect.center().x() - 8, rect.center().y() - 8, 16, 16)
    if state:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["accent"]))
        p.drawRoundedRect(box, 4, 4)
        pen = QPen(QColor(c["accent_text"]), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if state == 2:
            p.drawPolyline([QPointF(box.left() + 4, box.center().y()), QPointF(box.left() + 7, box.bottom() - 4.5),
                            QPointF(box.right() - 3.5, box.top() + 4.5)])
        else:
            p.drawLine(QPointF(box.left() + 4, box.center().y()), QPointF(box.right() - 4, box.center().y()))
    else:
        p.setPen(QPen(QColor(c["muted"]), 1.3))
        p.setBrush(QColor(c["surface"]))
        p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
    p.restore()


class CheckDelegate(QStyledItemDelegate):
    """The row's checkbox mirrors whether the row is selected."""

    def paint(self, p: QPainter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, p, opt.widget)
        draw_checkbox(p, option.rect, 2 if option.state & QStyle.StateFlag.State_Selected else 0)


class CheckHeader(QHeaderView):
    """Header with a select-all checkbox over the checkbox column."""

    def __init__(self, table: QTableWidget):
        super().__init__(Qt.Orientation.Horizontal, table)
        self.table = table
        self.setSectionsClickable(True)

    def paintSection(self, p: QPainter, rect, index: int):
        p.save()
        super().paintSection(p, rect, index)
        p.restore()
        if index == COL_CHECK and self.table.rowCount():
            selected = len(self.table.selectionModel().selectedRows())
            draw_checkbox(p, rect, 2 if selected == self.table.rowCount() else 1 if selected else 0)


class ProgressDelegate(QStyledItemDelegate):
    """Slim rounded progress bar with the percentage beside it."""

    def paint(self, p: QPainter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, p, opt.widget)  # hover/selection
        data = index.data(DATA)
        if not data:
            return
        progress, status, text = data
        c = theme.colors
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(8, 0, -8, 0)
        text_w = 52
        bar = QRectF(r.left(), r.center().y() - 2.5, max(10, r.width() - text_w), 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["track"]))
        p.drawRoundedRect(bar, 3, 3)
        color = status_color(status)
        if progress is None and status in Status.ACTIVE:
            # indeterminate: a block sliding along the track
            phase = (time.monotonic() * 0.7) % 1.0
            chunk = bar.width() * 0.3
            x = bar.left() - chunk + phase * (bar.width() + chunk)
            p.setClipRect(bar)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, bar.top(), chunk, bar.height()), 3, 3)
            p.setClipping(False)
        elif progress:
            p.setBrush(color)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(6.0, bar.width() * progress), bar.height()), 3, 3)
        p.setPen(QColor(c["muted"]) if status != Status.COMPLETED else QColor(c["text"]))
        p.drawText(QRectF(bar.right() + 8, r.top(), text_w - 8, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        p.restore()


# --- dialogs -------------------------------------------------------------------

def dialog_header(title: str, subtitle: str = "") -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 6)
    icon = QLabel()
    icon.setPixmap(theme.app_icon().pixmap(36, 36))
    row.addWidget(icon)
    col = QVBoxLayout()
    col.setSpacing(0)
    t = QLabel(title)
    t.setObjectName("dialogTitle")
    col.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setProperty("muted", True)
        col.addWidget(s)
    row.addLayout(col, 1)
    return w


class AddDownloadDialog(QDialog):
    def __init__(self, settings: QSettings, url: str = "", parent=None, filename: str = "",
                 referer: str = "", cookies: str = "", extra_headers: dict | None = None,
                 source: str = "", kind: str = "", queues: list | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add download")
        self.setMinimumWidth(600)
        self._extra_headers = extra_headers or {}
        # The browser tells us when a URL is a stream; otherwise we go by its extension.
        self._given = (url, kind) if kind in VIDEO_KINDS else ("", "")
        self.kind = "file"
        self._probe_error: Exception | None = None
        self._probe_token = 0
        self._probed_url = None
        self._probe_results: list = []   # filled by a worker thread, drained by a timer
        self._probe_timer = QTimer(self)
        self._probe_timer.timeout.connect(self._drain_probe)

        if not url:
            clip = QGuiApplication.clipboard().text().strip()
            url = clip if looks_like_url(clip) else ""
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("Paste a link: https://example.com/file.zip, a YouTube or .m3u8 video…")
        self.url.setClearButtonEnabled(True)
        self.filename = QLineEdit(filename)
        self.filename.setPlaceholderText("Auto (from server)")
        self.base_folder = settings.value("download_dir", default_download_dir())
        self.sort_by_type = settings.value("sort_by_type", True, type=bool)
        self.folder = QLineEdit(self.base_folder)
        self._folder_auto = True   # follows the file type until the user picks a folder
        self.folder.textEdited.connect(lambda _: setattr(self, "_folder_auto", False))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        folder_row.addWidget(self.folder)
        folder_row.addWidget(browse)
        self.connections = QSpinBox()
        self.connections.setRange(1, 32)
        self.connections.setValue(int(settings.value("connections", 8)))
        self.referer = QLineEdit(referer)
        self.referer.setPlaceholderText("Optional")
        self.cookies = QLineEdit(cookies)
        self.cookies.setPlaceholderText("Optional, e.g. session=abc123; other=xyz")
        self.cookies.setEchoMode(QLineEdit.EchoMode.Password if cookies else QLineEdit.EchoMode.Normal)
        # When to download: now, later by hand, or as part of a queue (which may be scheduled).
        self.when = QComboBox()
        self.when.addItem("Start now", "now")
        self.when.addItem("Add paused", "paused")
        for queue_id, name in queues or []:
            self.when.addItem(f"Add to {name}", f"queue:{queue_id}")
        self.quality = QComboBox()
        self.stream_note = QLabel()
        self.stream_note.setWordWrap(True)
        self.stream_note.setProperty("muted", True)

        self.form = form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.addRow("URL", self.url)
        form.addRow("Quality", self.quality)
        form.addRow("", self.stream_note)
        form.addRow("Save as", self.filename)
        form.addRow("Folder", folder_row)

        # Rarely needed settings stay folded away.
        self.advanced = QWidget()
        adv = QFormLayout(self.advanced)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setVerticalSpacing(10)
        adv.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        adv.addRow("Connections", self.connections)
        adv.addRow("Referer", self.referer)
        adv.addRow("Cookies", self.cookies)
        self.advanced.setVisible(False)
        self.toggle_advanced = QPushButton("Advanced options ▸")
        self.toggle_advanced.setProperty("flat", True)
        self.toggle_advanced.clicked.connect(self._toggle_advanced)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 12)
        card_layout.addLayout(form)
        adv_row = QHBoxLayout()
        adv_row.addWidget(self.toggle_advanced)
        adv_row.addStretch()
        if cookies or referer:
            hint = QLabel("Browser cookies attached")
            hint.setProperty("muted", True)
            adv_row.addWidget(hint)
        card_layout.addLayout(adv_row)
        card_layout.addWidget(self.advanced)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = primary(buttons.button(QDialogButtonBox.StandardButton.Ok))
        ok.setText("Download")
        ok.setIcon(theme.icon("download", theme.colors["accent_text"]))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        when_label = QLabel("When")
        when_label.setProperty("muted", True)
        bottom.addWidget(when_label)
        bottom.addWidget(self.when)
        bottom.addStretch()
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(dialog_header("Add download", source or "Files, videos and YouTube links"))
        layout.addWidget(card)
        layout.addLayout(bottom)

        self.url.editingFinished.connect(self._url_changed)
        self.filename.textChanged.connect(lambda _: self._update_folder())
        self.quality.currentIndexChanged.connect(lambda _: self._update_folder())
        self._url_changed()

    def _update_folder(self):
        """Put the download in <folder>/Video, /Music, ... by its type."""
        if not self._folder_auto:
            return
        folder = self.base_folder
        if self.sort_by_type:
            name = self.filename.text().strip() or QUrl(self.url.text().strip()).fileName()
            kind = "music" if self.variant() == "audio" else theme.file_type(name, self.kind)
            if kind in TYPE_FOLDERS:
                folder = str(Path(self.base_folder) / TYPE_FOLDERS[kind])
        self.folder.setText(folder)

    def showEvent(self, event):
        super().showEvent(event)
        # Usually opened because the browser sent a download: come to the front of it.
        QTimer.singleShot(0, lambda: (self.raise_(), self.activateWindow(), force_foreground(self)))

    def _toggle_advanced(self):
        shown = not self.advanced.isVisible()
        self.advanced.setVisible(shown)
        self.toggle_advanced.setText("Advanced options ▾" if shown else "Advanced options ▸")
        self.adjustSize()

    # --- stream detection & quality list ---

    def _url_changed(self):
        url = self.url.text().strip()
        kind = self._given[1] if url == self._given[0] else streams.guess_kind(url)
        self.kind = kind or "file"
        is_stream = self.kind != "file"
        self.filename.setPlaceholderText("Auto (video title)" if self.kind == "ytdl" else "Auto (from server)")
        for widget in (self.quality, self.stream_note):
            self.form.setRowVisible(widget, is_stream)
        if is_stream and url != self._probed_url:
            self._start_probe(url)
        elif not is_stream:
            self._probed_url, self._probe_error = None, None
        self._update_folder()

    def _start_probe(self, url: str):
        self._probed_url, self._probe_error = url, None
        self._probe_token += 1
        token, kind, headers = self._probe_token, self.kind, self.headers()
        self.quality.clear()
        self.quality.addItem("Loading qualities…")
        self.quality.setEnabled(False)
        self.stream_note.setText("")

        def work():
            try:
                self._probe_results.append((token, streams.list_variants(url, headers, kind), None))
            except Exception as e:  # noqa: BLE001 - shown to the user
                self._probe_results.append((token, None, e))
        threading.Thread(target=work, daemon=True).start()
        self._probe_timer.start(100)

    def _drain_probe(self):
        while self._probe_results:
            token, result, error = self._probe_results.pop(0)
            if token != self._probe_token:
                continue  # an older URL's answer
            self._probe_timer.stop()
            self.quality.clear()
            if error:
                self._probe_error = error
                self.quality.addItem("—")
                self.stream_note.setText(f"<span style='color:{theme.colors['danger']}'>{error}</span>")
                return
            self.kind, variants = result
            for v in variants:
                self.quality.addItem(v.label, v.id)
            if not variants:
                self.quality.addItem("Default", None)
            self.quality.setEnabled(len(variants) > 1)
            n = len(variants)
            name = (ytdl.site_for(self._probed_url) or "Online") if self.kind == "ytdl" else self.kind.upper()
            self.stream_note.setText(f"{name} video" + (f" · {n} qualities" if n > 1 else ""))
            self._update_folder()

    def variant(self) -> str | None:
        return self.quality.currentData() if self.kind != "file" else None

    def start_now(self) -> bool:
        return self.when.currentData() == "now"

    def queue_id(self) -> str | None:
        data = self.when.currentData() or ""
        return data.split(":", 1)[1] if data.startswith("queue:") else None

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save to", self.folder.text())
        if d:
            self._folder_auto = False
            self.folder.setText(d)

    def _accept(self):
        if not looks_like_url(self.url.text()):
            QMessageBox.warning(self, APP_NAME, "Please enter an http:// or https:// URL.")
            return
        if not self.folder.text().strip():
            QMessageBox.warning(self, APP_NAME, "Please choose a folder.")
            return
        self._url_changed()  # in case the URL was edited without leaving the field
        if isinstance(self._probe_error, (streams.DrmError, streams.LiveStreamError)):
            QMessageBox.warning(self, APP_NAME, str(self._probe_error))
            return
        if self._probe_error and QMessageBox.question(
                self, APP_NAME, f"Couldn't read this video:\n{self._probe_error}\n\nAdd it anyway?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.accept()

    def headers(self) -> dict:
        h = dict(self._extra_headers)
        if self.referer.text().strip():
            h["Referer"] = self.referer.text().strip()
        if self.cookies.text().strip():
            h["Cookie"] = self.cookies.text().strip()
        return h


class DeleteDialog(QDialog):
    """Confirm deleting downloads from the list, optionally with their files."""

    def __init__(self, tasks: list, with_files: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete downloads")
        self.setMinimumWidth(460)
        n = len(tasks)
        finished = sum(t.status == Status.COMPLETED for t in tasks)
        unfinished = n - finished
        names = [t.filename or t.url.rsplit("/", 1)[-1] for t in tasks[:5]]
        listing = "<br>".join(f"• {name}" for name in names) + (f"<br>… and {n - 5} more" if n > 5 else "")
        text = QLabel(f"Delete {n} download{'s' * (n > 1)} from PyDM?<br><br>"
                      f"<span style='color:{theme.colors['muted']}'>{listing}</span>")
        text.setWordWrap(True)
        self.with_files = QCheckBox(f"Also delete the downloaded file{'s' * (finished > 1)} from disk")
        self.with_files.setChecked(with_files and finished > 0)
        self.with_files.setEnabled(finished > 0)
        if not finished:
            self.with_files.setText("Also delete the downloaded files from disk (none are finished yet)")
        note = QLabel(f"Unfinished downloads always lose their partial data ({unfinished} selected).")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        note.setVisible(bool(unfinished))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setProperty("danger", True)
        self.with_files.toggled.connect(self._label)
        self._label()
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(dialog_header("Delete downloads"))
        layout.addWidget(text)
        layout.addWidget(self.with_files)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _label(self):
        self.ok.setText("Delete with files" if self.with_files.isChecked() else "Delete from list")


class DoneWindow(QWidget):
    """Small window in the corner when a download finishes: Open / Open folder,
    and the virus scan result. Doesn't take the focus from what you're doing; a
    second download finishing just updates it."""

    def __init__(self, window):
        # No parent: it must also appear while the main window is hidden in the tray.
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.window_ = window
        self.task_id = None
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Download complete")
        self.setFixedWidth(400)
        self.setObjectName("doneWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.icon = QLabel()
        self.title = QLabel("Download complete")
        self.title.setObjectName("detailsTitle")
        self.name = QLabel()   # single lines, shortened with "…" (wrapping labels in a
        self.info = QLabel()   # fixed-width window can overlap)
        self.info.setProperty("muted", True)
        self.scan_line = QLabel()
        self.scan_line.setWordWrap(True)
        self.more = QLabel()
        self.more.setProperty("muted", True)
        self.open_btn = primary(QPushButton("Open"))
        self.open_btn.clicked.connect(self._open)
        self.folder_btn = QPushButton("Open folder")
        self.folder_btn.clicked.connect(self._folder)
        close = QPushButton("Close")
        close.clicked.connect(self.hide)
        self.never = QCheckBox("Don't show this window again")
        self.never.toggled.connect(lambda v: window.settings.setValue("done_window", not v))
        head = QHBoxLayout()
        head.addWidget(self.icon)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(self.title)
        texts.addWidget(self.name)
        texts.addWidget(self.info)
        head.addLayout(texts, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.folder_btn)
        buttons.addStretch()
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.addLayout(head)
        layout.addWidget(self.scan_line)
        layout.addWidget(self.more)
        layout.addLayout(buttons)
        layout.addWidget(self.never)

    def show_task(self, task, others: int = 0):
        self.task_id = task.id
        s = task.snapshot()
        name = s["filename"] or task.url
        self.icon.setPixmap(theme.type_badge(theme.file_type(name, s["kind"])).pixmap(40, 40))
        bold = self.name.font()
        bold.setBold(True)
        self.name.setFont(bold)
        self.name.setText(self.name.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, 300))
        self.name.setToolTip(name)
        folder = task.final_path.parent if task.final_path else None
        where = f"in {folder.parent.name}{os.sep}{folder.name}" if folder and folder.parent.name else (
            f"in {folder}" if folder else "")
        info = " · ".join(x for x in (human_size(s["total_size"]) if s["total_size"] else "", where) if x)
        self.info.setText(self.info.fontMetrics().elidedText(info, Qt.TextElideMode.ElideMiddle, 300))
        self.info.setToolTip(str(folder or ""))
        self.more.setText(f"{others} more download{'s' * (others > 1)} finished too." if others else "")
        self.more.setVisible(bool(others))
        self.update_scan()
        self.never.setChecked(False)
        self._place()
        self.show()
        self.raise_()

    def update_scan(self):
        state = self.window_.scan_state(self.task_id) if self.task_id else None
        exists = bool(self.task_id and (t := self.window_.manager.get(self.task_id)) and t.final_path
                      and t.final_path.exists())
        c = theme.colors
        if state is None:
            text = ""
        elif state == "scanning":
            text = f"<span style='color:{c['muted']}'>Scanning with Microsoft Defender…</span>"
        elif state.status == "threat":
            text = f"<span style='color:{c['danger']}'>⚠ {state.text()}.</span>"
            self.title.setText("Threat found")
        elif state.status == "clean":
            text = f"<span style='color:{c['success']}'>✓ {state.text()}.</span>"
        else:
            text = f"<span style='color:{c['muted']}'>{state.text()}</span>"
        if state is None or state == "scanning" or getattr(state, "status", "") != "threat":
            self.title.setText("Download complete")
        self.scan_line.setText(text)
        self.scan_line.setVisible(bool(text))
        self.open_btn.setEnabled(exists and state != "scanning" and getattr(state, "status", "") != "threat")
        self.folder_btn.setEnabled(bool(self.task_id))

    def _place(self):
        self.adjustSize()
        screen = (self.window_.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        self.move(screen.right() - self.width() - 16, screen.bottom() - self.height() - 16)

    def _task(self):
        return self.window_.manager.get(self.task_id) if self.task_id else None

    def _open(self):
        t = self._task()
        if t and t.final_path and t.final_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(t.final_path)))
            self.hide()

    def _folder(self):
        t = self._task()
        if t and t.final_path:
            reveal_in_explorer(str(t.final_path if t.final_path.exists() else t.dest_dir))
            self.hide()


class RefreshLinkDialog(QDialog):
    """Give a download whose link stopped working a fresh one. Stays open (not
    modal) while you get the link in the browser: the next download the browser
    or the clipboard hands to PyDM is used here instead of being added as new."""

    def __init__(self, window, task):
        super().__init__(window)
        self.window_, self.task = window, task
        self.setWindowTitle("Refresh link")
        self.setMinimumWidth(580)
        name = task.filename or task.url.rsplit("/", 1)[-1]
        intro = QLabel("Download links from file hosts and video sites stop working after a while. "
                       "Give PyDM a fresh link to the same file and it continues where it stopped.")
        intro.setWordWrap(True)
        referer = task.headers.get("Referer", "")
        step1 = QLabel("<b>1.</b> Open the page you downloaded it from.")
        self.open_page = QPushButton(theme.icon("globe"), "Open the download page")
        self.open_page.setEnabled(looks_like_url(referer))
        self.open_page.setToolTip(referer or "PyDM doesn't know which page this came from")
        self.open_page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(referer)))
        row1 = QHBoxLayout()
        row1.addWidget(step1, 1)
        row1.addWidget(self.open_page)
        step2 = QLabel("<b>2.</b> Start the download again there, or copy its link. PyDM catches it and uses "
                       "it for this download instead of adding a new one.")
        step2.setWordWrap(True)
        self.url = QLineEdit()
        self.url.setPlaceholderText("…or paste the new link here")
        self.url.returnPressed.connect(lambda: self.use(self.url.text().strip()))
        use = primary(QPushButton("Use this link"))
        use.clicked.connect(lambda: self.use(self.url.text().strip()))
        row2 = QHBoxLayout()
        row2.addWidget(self.url, 1)
        row2.addWidget(use)
        self.status = QLabel("Waiting for the new link…")
        self.status.setProperty("muted", True)
        self.status.setWordWrap(True)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(dialog_header("Refresh link", name))
        layout.addWidget(intro)
        layout.addLayout(row1)
        layout.addWidget(step2)
        layout.addLayout(row2)
        layout.addLayout(bottom)
        self.finished.connect(lambda _: window._end_refresh(self))

    def use(self, url: str, headers: dict | None = None) -> bool:
        if not looks_like_url(url):
            self._say("Paste an http:// or https:// link.", error=True)
            return False
        self.url.setText(url)
        self._say("Checking the new link…")
        QApplication.processEvents()
        ok, message = self.window_.apply_new_link(self.task, url, headers or {})
        if ok:
            self.accept()
        else:
            self._say(message, error=True)
        return ok

    def _say(self, text: str, error: bool = False):
        color = theme.colors["danger"] if error else theme.colors["muted"]
        self.status.setText(f"<span style='color:{color}'>{text}</span>")


class SchedulerDialog(QDialog):
    """Queues: their downloads and order, how many run at once, start/stop times,
    and what to do when everything has finished. Changes apply immediately."""

    def __init__(self, window, queue_id: str = MAIN_QUEUE):
        super().__init__(window)
        self.window_ = window
        self.queues = window.queues
        self.setWindowTitle("Scheduler")
        self.setMinimumSize(780, 520)
        self._loading = False

        self.list = QListWidget()
        self.list.setObjectName("sidebar")
        self.list.setFixedWidth(190)
        self.list.currentRowChanged.connect(lambda _: self._show())
        new_btn = QPushButton(theme.icon("plus"), "New queue")
        new_btn.clicked.connect(self._new)
        self.delete_btn = QPushButton(theme.icon("trash"), "Delete")
        self.delete_btn.clicked.connect(self._delete)
        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        left.addWidget(new_btn)
        left.addWidget(self.delete_btn)

        self.name = QLineEdit()
        self.name.textEdited.connect(lambda t: self._set("name", t.strip() or "Queue"))
        self.max_active = QSpinBox()
        self.max_active.setRange(1, 10)
        self.max_active.setSuffix(" at a time")
        self.max_active.setFixedWidth(130)
        self.max_active.valueChanged.connect(lambda v: self._set("max_active", v))
        self.start_on = QCheckBox("Start at")
        self.start_on.toggled.connect(lambda v: self._set("start_enabled", v))
        self.start_time = QTimeEdit()
        self.start_time.setDisplayFormat("HH:mm")
        self.start_time.timeChanged.connect(lambda t: self._set("start_time", t.toString("HH:mm")))
        self.day_boxes = []
        days_row = QHBoxLayout()
        days_row.setSpacing(4)
        for i, day in enumerate(DAY_NAMES):
            b = QToolButton()
            b.setText(day)
            b.setCheckable(True)
            b.setObjectName("dayButton")
            b.toggled.connect(lambda _: self._set("days", [i for i, x in enumerate(self.day_boxes) if x.isChecked()]))
            self.day_boxes.append(b)
            days_row.addWidget(b)
        days_row.addStretch()
        start_row = QHBoxLayout()
        start_row.addWidget(self.start_on)
        start_row.addWidget(self.start_time)
        start_row.addStretch()
        self.stop_on = QCheckBox("Stop at")
        self.stop_on.toggled.connect(lambda v: self._set("stop_enabled", v))
        self.stop_time = QTimeEdit()
        self.stop_time.setDisplayFormat("HH:mm")
        self.stop_time.timeChanged.connect(lambda t: self._set("stop_time", t.toString("HH:mm")))
        stop_row = QHBoxLayout()
        stop_row.addWidget(self.stop_on)
        stop_row.addWidget(self.stop_time)
        stop_row.addStretch()
        self.after = QComboBox()
        for key, label in AFTER_ACTIONS.items():
            self.after.addItem(label, key)
        self.after.currentIndexChanged.connect(lambda _: self._set("after", self.after.currentData()))
        self.next_label = QLabel()
        self.next_label.setProperty("muted", True)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow("Name", self.name)
        form.addRow("Download", self.max_active)
        form.addRow("Schedule", start_row)
        form.addRow("", days_row)
        form.addRow("", stop_row)
        form.addRow("When done", self.after)
        form.addRow("", self.next_label)

        self.files = QListWidget()
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        up = QPushButton("Move up")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Move down")
        down.clicked.connect(lambda: self._move(1))
        out = QPushButton("Remove from queue")
        out.clicked.connect(self._remove_files)
        file_buttons = QHBoxLayout()
        for b in (up, down, out):
            file_buttons.addWidget(b)
        file_buttons.addStretch()
        files_title = QLabel("<b>Downloads in this queue</b> (top first)")

        self.start_btn = primary(QPushButton(theme.icon("play", theme.colors["accent_text"]), "Start now"))
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton(theme.icon("pause"), "Stop")
        self.stop_btn.clicked.connect(self._stop)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.start_btn)
        bottom.addWidget(self.stop_btn)
        bottom.addStretch()
        bottom.addWidget(close)

        right = QVBoxLayout()
        right.addLayout(form)
        right.addWidget(files_title)
        right.addWidget(self.files, 1)
        right.addLayout(file_buttons)
        card = QFrame()
        card.setObjectName("card")
        card.setLayout(right)
        body = QHBoxLayout()
        body.addLayout(left)
        body.addWidget(card, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.addWidget(dialog_header("Scheduler", "Queues download one after another, by hand or at set times"))
        layout.addLayout(body, 1)
        layout.addLayout(bottom)
        self._fill_list(queue_id)

    def queue(self):
        item = self.list.currentItem()
        return self.queues.get(item.data(DATA)) if item else None

    def _fill_list(self, select: str | None = None):
        self.list.clear()
        for q in self.queues.queues:
            item = QListWidgetItem(theme.icon("calendar"), q.name)
            item.setData(DATA, q.id)
            self.list.addItem(item)
            if q.id == select:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)

    def _show(self):
        q = self.queue()
        if not q:
            return
        self._loading = True
        self.name.setText(q.name)
        self.max_active.setValue(q.max_active)
        self.start_on.setChecked(q.start_enabled)
        self.start_time.setTime(QTime.fromString(q.start_time, "HH:mm"))
        for i, b in enumerate(self.day_boxes):
            b.setChecked(i in q.days)
        self.stop_on.setChecked(q.stop_enabled)
        self.stop_time.setTime(QTime.fromString(q.stop_time, "HH:mm"))
        self.after.setCurrentIndex(max(0, self.after.findData(q.after)))
        self.delete_btn.setEnabled(q.id != MAIN_QUEUE)
        self._loading = False
        self._refresh()

    def _enable_times(self):
        for w in (self.start_time, *self.day_boxes):
            w.setEnabled(self.start_on.isChecked())
        self.stop_time.setEnabled(self.stop_on.isChecked())

    def _refresh(self):
        q = self.queue()
        if not q:
            return
        selected = {i.data(DATA) for i in self.files.selectedItems()}
        self.files.clear()
        for t in self.queues.tasks(q.id):
            name = t.filename or t.url.rsplit("/", 1)[-1]
            item = QListWidgetItem(theme.type_badge(theme.file_type(name, getattr(t, "kind", "file"))),
                                   f"{name}    —  {t.status}")
            item.setData(DATA, t.id)
            self.files.addItem(item)
            item.setSelected(t.id in selected)
        self._enable_times()
        nxt = self.queues.next_start(q)
        state = "Running now. " if q.running else ""
        self.next_label.setText(state + (f"Next start: {nxt:%a %d %b, %H:%M}" if nxt else q.schedule_text()))
        self.start_btn.setEnabled(not q.running)
        self.stop_btn.setEnabled(q.running)
        self.list.currentItem().setText(q.name)

    def _set(self, attr, value):
        q = self.queue()
        if self._loading or not q:
            return
        setattr(q, attr, value)
        self.queues.save()
        self._refresh()

    def _new(self):
        q = self.queues.create(f"Queue {len(self.queues.queues) + 1}")
        self._fill_list(q.id)
        self.name.setFocus()
        self.name.selectAll()

    def _delete(self):
        q = self.queue()
        if q and q.id != MAIN_QUEUE and QMessageBox.question(
                self, APP_NAME, f"Delete the queue “{q.name}”? Its downloads stay in your list.") \
                == QMessageBox.StandardButton.Yes:
            self.queues.delete(q.id)
            self._fill_list(MAIN_QUEUE)

    def _move(self, delta):
        q = self.queue()
        ids = [i.data(DATA) for i in self.files.selectedItems()]
        if q and ids:
            self.queues.move(q.id, ids, delta)
            self._refresh()

    def _remove_files(self):
        for item in self.files.selectedItems():
            self.queues.remove_task(item.data(DATA))
        self._refresh()

    def _start(self):
        q = self.queue()
        if q:
            self.queues.start(q.id)
            self.window_.refresh()
            self._refresh()

    def _stop(self):
        q = self.queue()
        if q:
            self.queues.stop(q.id)
            self.window_.refresh()
            self._refresh()


class SettingsDialog(QDialog):
    THEMES = [("system", "Match Windows"), ("light", "Light"), ("dark", "Dark")]

    def __init__(self, settings: QSettings, parent=None, tab: str = "general"):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)

        # General
        self.folder = QLineEdit(settings.value("download_dir", default_download_dir()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self.folder.setText(
            QFileDialog.getExistingDirectory(self, "Default folder", self.folder.text()) or self.folder.text()))
        row = QHBoxLayout()
        row.addWidget(self.folder)
        row.addWidget(browse)
        self.theme = QComboBox()
        for key, label in self.THEMES:
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(0, [k for k, _ in self.THEMES].index(settings.value("theme", "system"))))
        self.tray = QCheckBox("Keep running in the notification area when the window is closed")
        self.tray.setChecked(settings.value("close_to_tray", True, type=bool))
        self.notify = QCheckBox("Notify me when a download finishes or fails")
        self.notify.setChecked(settings.value("notify", True, type=bool))
        self.sort_by_type = QCheckBox("Sort into subfolders by type (Video, Music, Documents, Compressed…)")
        self.sort_by_type.setChecked(settings.value("sort_by_type", True, type=bool))
        self.clipboard = QCheckBox("Offer to download links I copy (files and video pages)")
        self.clipboard.setChecked(settings.value("watch_clipboard", True, type=bool))
        self.done_window = QCheckBox("Show a window with Open / Open folder when a download finishes")
        self.done_window.setChecked(settings.value("done_window", True, type=bool))
        self.virus_scan = QComboBox()
        for key, label in scan.MODES.items():
            self.virus_scan.addItem(label, key)
        self.virus_scan.setCurrentIndex(max(0, self.virus_scan.findData(settings.value("virus_scan", "risky"))))
        if not scan.scanner():
            self.virus_scan.setEnabled(False)
            self.virus_scan.setToolTip("Microsoft Defender isn't available on this PC")
        general = QWidget()
        g = QFormLayout(general)
        g.setVerticalSpacing(12)
        g.addRow("Save downloads to", row)
        g.addRow("", self.sort_by_type)
        g.addRow("Appearance", self.theme)
        g.addRow("", self.clipboard)
        g.addRow("", self.done_window)
        g.addRow("Virus scan", self.virus_scan)
        scan_note = QLabel("Finished downloads are checked with Microsoft Defender; anything it finds is "
                           "removed as usual. “Programs and archives”: .exe, .msi, .zip, .rar, .apk, …")
        scan_note.setWordWrap(True)
        scan_note.setProperty("muted", True)
        g.addRow("", scan_note)
        g.addRow("", self.tray)
        g.addRow("", self.notify)

        # Downloads
        self.connections = QSpinBox()
        self.connections.setRange(1, 32)
        self.connections.setValue(int(settings.value("connections", 8)))
        self.max_active = QSpinBox()
        self.max_active.setRange(1, 20)
        self.max_active.setValue(int(settings.value("max_active", 3)))
        self.speed_limit = QSpinBox()
        self.speed_limit.setRange(0, 10_000_000)
        self.speed_limit.setSingleStep(128)
        self.speed_limit.setSuffix(" KB/s")
        self.speed_limit.setSpecialValueText("Unlimited")
        self.speed_limit.setValue(int(settings.value("speed_limit_kb", 0)))
        downloads = QWidget()
        d = QFormLayout(downloads)
        d.setVerticalSpacing(12)
        d.addRow("Connections per download", self.connections)
        d.addRow("Downloads at the same time", self.max_active)
        d.addRow("Speed limit (all downloads)", self.speed_limit)
        self.resume_queues = QCheckBox("Continue queues that were running when PyDM closed or the PC restarted")
        self.resume_queues.setChecked(settings.value("resume_queues", True, type=bool))
        d.addRow("", self.resume_queues)
        note = QLabel("More connections can speed up downloads from servers that limit each connection.")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        d.addRow("", note)

        self.tabs = QTabWidget()
        self.tabs.addTab(general, "General")
        self.tabs.addTab(downloads, "Downloads")
        self.tabs.addTab(BrowserPage(), "Browser")
        self.tabs.addTab(AboutPage(parent if isinstance(parent, MainWindow) else None), "About")
        self.tabs.setCurrentIndex({"general": 0, "downloads": 1, "browser": 2, "about": 3}.get(tab, 0))
        if tab == "downloads":
            self.speed_limit.setFocus()
        for i in range(self.tabs.count()):
            self.tabs.widget(i).setContentsMargins(14, 14, 14, 14)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        primary(buttons.button(QDialogButtonBox.StandardButton.Ok)).setText("Save")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(dialog_header("Settings"))
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)

    def theme_mode(self) -> str:
        return self.theme.currentData()


class AboutPage(QWidget):
    """Version and the bundled tools; filled in the background (running ffmpeg/Deno takes a moment)."""

    def __init__(self, window=None):
        super().__init__()
        self.window_ = window
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel(f"<b>PyDM {__version__}</b> — a fast, resumable download manager")
        layout.addWidget(title)
        self.table = QLabel("Checking components…")
        self.table.setProperty("muted", True)
        self.table.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.table)
        updates_box = QFrame()
        updates_box.setObjectName("card")
        ub = QVBoxLayout(updates_box)
        ub.setContentsMargins(12, 10, 12, 10)
        heading = QLabel("<b>yt-dlp updates</b>")
        note = QLabel("YouTube and other video sites change often; when they do, downloads from them fail "
                      "until yt-dlp is updated. Updates are checked and verified before PyDM uses them.")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        self.auto = QCheckBox("Keep yt-dlp up to date automatically (checks once a day)")
        self.auto.setChecked(window.settings.value("auto_update_ytdlp", True, type=bool) if window else True)
        self.auto.toggled.connect(lambda v: window and window.settings.setValue("auto_update_ytdlp", v))
        self.check_btn = QPushButton(theme.icon("retry"), "Check for updates")
        self.check_btn.clicked.connect(self._check)
        self.restart_btn = primary(QPushButton("Restart PyDM to use it"))
        self.restart_btn.clicked.connect(lambda: window and window.restart_app())
        self.update_status = QLabel()
        self.update_status.setWordWrap(True)
        logs = QPushButton(theme.icon("folder"), "Open log folder")
        logs.setToolTip(str(pydm_log.folder()))
        logs.clicked.connect(lambda: (pydm_log.folder().mkdir(parents=True, exist_ok=True),
                                      QDesktopServices.openUrl(QUrl.fromLocalFile(str(pydm_log.folder())))))
        layout.addWidget(logs)
        row = QHBoxLayout()
        row.addWidget(self.check_btn)
        row.addWidget(self.restart_btn)
        row.addWidget(self.update_status, 1)
        for w in (heading, note, self.auto):
            ub.addWidget(w)
        ub.addLayout(row)
        layout.addWidget(updates_box)
        layout.addStretch()
        self._show_update_state()
        self._result: list = []
        threading.Thread(target=lambda: self._result.append(buildcheck.components()), daemon=True).start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._show)
        self._timer.start(150)

    def _check(self):
        if not self.window_:
            return
        self.check_btn.setEnabled(False)
        self.update_status.setText("Checking…")
        self.window_.check_ytdlp_update(auto=False, on_done=lambda _: self._show_update_state())

    def _show_update_state(self):
        w = self.window_
        ready = w and w._update_ready
        self.restart_btn.setVisible(bool(ready))
        self.check_btn.setEnabled(not (w and w._update_busy))
        if w and w._update_busy:
            text = "Checking…"
        elif ready:
            text = f"yt-dlp {ready} is downloaded and verified."
        elif w and w._update_last:
            kind, detail = w._update_last
            text = {"current": f"You have the latest yt-dlp ({detail}).",
                    "error": f"Couldn't check: {detail}"}.get(kind, detail)
        else:
            text = f"Using yt-dlp {updates.active_version()}."
        self.update_status.setText(text)

    def _show(self):
        if not self._result:
            return
        self._timer.stop()
        rows = "".join(f"<tr><td style='padding-right:18px'>{k}</td><td>{v}</td></tr>"
                       for k, v in self._result[0].items())
        self.table.setText(f"<table>{rows}</table>")


class BrowserPage(QWidget):
    """Status of the browser connection, with the steps to load the extension."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self.status = QLabel()
        layout.addWidget(self.status)
        steps = QLabel(
            "<b>Add the PyDM extension to Brave, Chrome or Edge</b>"
            "<ol style='margin-left:-20px'>"
            "<li>Open <b>brave://extensions</b> (or chrome://extensions, edge://extensions).</li>"
            "<li>Turn on <b>Developer mode</b>.</li>"
            "<li>Click <b>Load unpacked</b> and choose the extension folder below.</li>"
            "<li>Pin the PyDM icon, then use <i>Settings → Test connection</i> in its popup.</li></ol>")
        steps.setWordWrap(True)
        layout.addWidget(steps)
        self.folder = QLineEdit(str(install_browser.extension_dir()))
        self.folder.setReadOnly(True)
        layout.addWidget(self.folder)
        row = QHBoxLayout()
        open_btn = QPushButton(theme.icon("folder"), "Open extension folder")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder.text())))
        copy_btn = QPushButton(theme.icon("copy"), "Copy path")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.folder.text()))
        self.register = QPushButton(theme.icon("retry"), "Repair connection")
        self.register.clicked.connect(self._register)
        row.addWidget(open_btn)
        row.addWidget(copy_btn)
        row.addStretch()
        row.addWidget(self.register)
        layout.addLayout(row)
        layout.addStretch()
        self._show_status()

    def _show_status(self):
        ok = install_browser.is_installed()
        color = theme.colors["success" if ok else "warning"]
        text = ("Connected: PyDM is registered with Brave, Chrome and Edge." if ok
                else "Not registered yet. Click <b>Repair connection</b>.")
        self.status.setText(f"<span style='color:{color}'>●</span>&nbsp; {text}")

    def _register(self):
        try:
            install_browser.install(quiet=True)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Couldn't register PyDM with the browsers:\n{e}")
        self._show_status()


# --- main window -------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        app = QCoreApplication.instance()
        self.settings = QSettings(app.organizationName() or APP_NAME, app.applicationName() or APP_NAME)
        data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        self.manager = DownloadManager(data_dir / "downloads.json",
                                       max_active=int(self.settings.value("max_active", 3)))
        self.queues = QueueManager(self.manager, data_dir / "queues.json")
        self.power = power_action  # replaced in tests
        self._row_ids: list[str] = []
        self._category = "all"
        self._last_status: dict[str, str] = {}   # for "finished" notifications
        self._notified_path: str | None = None
        self._quitting = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.resize(1120, 680)
        self.setMinimumSize(860, 520)
        self.setAcceptDrops(True)
        self._build_actions()
        self._build_ui()
        self._build_tray()
        self.apply_icons()
        self.set_speed_limit(self.speed_limit_kb())
        self._clipboard_seen: list[str] = []
        QGuiApplication.clipboard().dataChanged.connect(self._clipboard_changed)
        self._errors: list[str] = []         # unexpected errors (any thread), shown by refresh()
        self._error_reported = False
        pydm_log.on_error(self._errors.append)
        self._refresh_target = None          # the open Refresh link dialog, if any
        self._update_results: list = []      # filled by the updater thread, read by refresh()
        self._update_listeners: list = []
        self._update_busy = False
        self._update_ready: str | None = None   # a verified yt-dlp waiting for a restart
        self._update_last = None
        QTimer.singleShot(20_000, lambda: self.check_ytdlp_update(auto=True))
        QTimer.singleShot(0, self._resume_after_restart)
        self._scans: dict = {}               # task id -> "scanning" or scan.ScanResult (this session)
        self._scan_results: list = []        # filled by the scan thread
        self._scan_queue: list = []
        self._scan_lock = threading.Lock()
        self._done_window = None
        self._recent_done: list[str] = []    # finished while the completion window was up
        QTimer.singleShot(0, self._resume_queues)

        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(300)
        self.refresh()

        self._incoming: list[dict] = []   # downloads sent by the browser, shown one dialog at a time
        self._dialog_open = False
        self.ipc_server = QLocalServer(self)
        self.ipc_server.newConnection.connect(self._ipc_connection)
        if not self.ipc_server.listen(ipc.SERVER_NAME):
            QLocalServer.removeServer(ipc.SERVER_NAME)  # stale pipe left by a crash
            self.ipc_server.listen(ipc.SERVER_NAME)

    # --- construction ---

    def _action(self, text, icon_name, slot, shortcut=None, tip="") -> QAction:
        a = QAction(text, self)
        a.setData(icon_name)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setToolTip(f"{tip or text}" + (f"  ({QKeySequence(shortcut).toString()})" if shortcut else ""))
        self.addAction(a)
        return a

    def _build_actions(self):
        self.act_add = self._action("Add URL", "plus", self.add_download, "Ctrl+N", "Add a download")
        self.act_resume = self._action("Resume", "play", self.resume_selected, "Ctrl+R")
        self.act_pause = self._action("Pause", "pause", self.pause_selected, "Ctrl+P")
        self.act_remove = self._action("Delete", "trash", self.remove_selected, "Del",
                                       "Delete the selected downloads (optionally their files too)")
        self.act_delete = self._action("Delete with files", "trash", lambda: self.remove_selected(True), "Shift+Del",
                                       "Delete the selected downloads and their files from disk")
        self.act_select_all = self._action("Select all", "check", self.table_select_all, "Ctrl+A")
        self.act_open = self._action("Open", "open", self.open_selected, "Return")
        self.act_folder = self._action("Show in folder", "folder", self.open_selected_folder, "Ctrl+E")
        self.act_copy = self._action("Copy link", "link", self.copy_url, "Ctrl+Shift+C")
        self.act_resume_all = self._action("Resume all", "play-all", self.resume_all)
        self.act_pause_all = self._action("Pause all", "pause-all", self.pause_all)
        self.act_settings = self._action("Settings", "settings", self.open_settings, "Ctrl+,")
        self.act_quit = self._action("Quit PyDM", "x", self.quit_app, "Ctrl+Q")
        self.act_refresh = self._action("Refresh link…", "retry", lambda: self.refresh_link(), "Ctrl+L",
                                        "Give this download a fresh link when the old one stopped working")
        self.act_scheduler = self._action("Scheduler", "calendar", lambda: self.open_scheduler(), "Ctrl+J",
                                          "Queues and scheduled downloads")
        self.act_move_up = self._action("Move up", "up", lambda: self.move_in_queue(-1), "Ctrl+Up")
        self.act_move_down = self._action("Move down", "down", lambda: self.move_in_queue(1), "Ctrl+Down")
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: (self.search.setFocus(), self.search.selectAll()))
        QShortcut(QKeySequence("Space"), self, self.toggle_selected)

    def _build_ui(self):
        # Toolbar
        bar = QWidget()
        bar.setObjectName("toolbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(12, 8, 12, 8)
        tb.setSpacing(4)
        self.add_button = primary(QPushButton("Add URL"))
        self.add_button.setToolTip(self.act_add.toolTip())
        self.add_button.clicked.connect(self.add_download)
        tb.addWidget(self.add_button)
        tb.addSpacing(8)
        self._tool_buttons = []
        for a in (self.act_resume, self.act_pause, self.act_remove, None, self.act_resume_all, self.act_pause_all,
                  None, self.act_scheduler):
            if a is None:
                line = QFrame()
                line.setObjectName("vline")
                line.setFixedWidth(1)
                tb.addWidget(line)
                continue
            b = QToolButton()
            b.setDefaultAction(a)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setIconSize(QSize(18, 18))
            tb.addWidget(b)
            self._tool_buttons.append(b)
        tb.addStretch()
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search downloads")
        self.search.setClearButtonEnabled(True)
        self._search_action = self.search.addAction(theme.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(lambda _: self.refresh())
        tb.addWidget(self.search)
        settings_button = QToolButton()
        settings_button.setDefaultAction(self.act_settings)
        settings_button.setIconSize(QSize(18, 18))
        tb.addWidget(settings_button)
        self._tool_buttons.append(settings_button)

        # Sidebar
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(200)
        self.sidebar.setIconSize(QSize(18, 18))
        for entry in CATEGORIES + [None]:
            if entry is None:
                heading = "types" if self.sidebar.count() < len(CATEGORIES) else "queues"
                item = QListWidgetItem(heading.upper())
                item.setData(HEADER, heading)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                f = item.font()
                f.setPointSizeF(8)
                f.setBold(True)
                item.setFont(f)
                item.setSizeHint(QSize(0, 34))
                item.setTextAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
            else:
                item = QListWidgetItem(entry[1])
                item.setData(DATA, entry[0])
            self.sidebar.addItem(item)
        self._sync_queue_items()
        self.sidebar.setCurrentRow(0)
        self.sidebar.currentItemChanged.connect(self._category_changed)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._sidebar_menu)

        # Downloads table
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeader(CheckHeader(self.table))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Click selects one, Ctrl+click adds, Shift+click selects a range, dragging selects rows;
        # the checkboxes toggle a row without touching the others.
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setItemDelegateForColumn(COL_CHECK, CheckDelegate(self.table))
        self.table.viewport().installEventFilter(self)
        self._checkbox_press = False
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(28, 28))
        self.table.setMouseTracking(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setItemDelegateForColumn(COL_PROGRESS, ProgressDelegate(self.table))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemSelectionChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(lambda: self.table.horizontalHeader().viewport().update())
        self.table.cellDoubleClicked.connect(self._double_clicked)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_CHECK, 40)
        header.setToolTip("Tick rows to select several downloads; the box here selects all")
        header.sectionClicked.connect(self._header_clicked)
        for col, width in ((COL_SIZE, 85), (COL_PROGRESS, 170), (COL_SPEED, 90),
                           (COL_ETA, 75), (COL_STATUS, 125), (COL_ADDED, 115)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col, width)

        # Shown instead of the table when there's nothing to list.
        self.empty = QWidget()
        e = QVBoxLayout(self.empty)
        e.addStretch()
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = QLabel()
        self.empty_title.setObjectName("emptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text = QLabel()
        self.empty_text.setProperty("muted", True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text.setWordWrap(True)
        self.empty_buttons = QWidget()
        eb = QHBoxLayout(self.empty_buttons)
        eb.addStretch()
        add2 = primary(QPushButton("Add URL"))
        add2.clicked.connect(self.add_download)
        browser = QPushButton("Connect your browser")
        browser.clicked.connect(lambda: self.open_settings("browser"))
        eb.addWidget(add2)
        eb.addWidget(browser)
        eb.addStretch()
        for w in (self.empty_icon, self.empty_title, self.empty_text, self.empty_buttons):
            e.addWidget(w)
        e.addStretch()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty)

        # Shown above the list while a queue is selected in the sidebar.
        self.queue_bar = QWidget()
        self.queue_bar.setObjectName("queueBar")
        self.queue_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.queue_title = QLabel()
        self.queue_title.setObjectName("detailsTitle")
        self.queue_info = QLabel()
        self.queue_info.setProperty("muted", True)
        self.queue_start = primary(QPushButton("Start queue"))
        self.queue_start.clicked.connect(lambda: self._queue_action("start"))
        self.queue_stop = QPushButton("Stop queue")
        self.queue_stop.clicked.connect(lambda: self._queue_action("stop"))
        self.queue_edit = QPushButton("Schedule…")
        self.queue_edit.clicked.connect(lambda: self.open_scheduler(self._category.split(":", 1)[1]))
        qb = QHBoxLayout(self.queue_bar)
        qb.setContentsMargins(16, 10, 16, 10)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(self.queue_title)
        titles.addWidget(self.queue_info)
        qb.addLayout(titles, 1)
        qb.addWidget(self.queue_start)
        qb.addWidget(self.queue_stop)
        qb.addWidget(self.queue_edit)
        self.queue_bar.setVisible(False)
        list_area = QWidget()
        la = QVBoxLayout(list_area)
        la.setContentsMargins(0, 0, 0, 0)
        la.setSpacing(0)
        la.addWidget(self.queue_bar)
        la.addWidget(self.stack, 1)

        # Details pane for the selected download.
        self.details = QWidget()
        self.details.setObjectName("details")
        self.details.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.details_icon = QLabel()
        self.details_title = QLabel()
        self.details_title.setObjectName("detailsTitle")
        self.details_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_open = QPushButton("Open")
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_folder = QPushButton("Show in folder")
        self.btn_folder.clicked.connect(self.open_selected_folder)
        self.btn_refresh = QPushButton("Refresh link…")
        self.btn_refresh.clicked.connect(lambda: self.refresh_link())
        self.conn_spin = QSpinBox()
        self.conn_spin.setRange(1, 32)
        self.conn_spin.setPrefix("Connections: ")
        self.conn_spin.setToolTip("Change how many connections this download uses (also while it runs)")
        self.conn_spin.valueChanged.connect(self._connections_changed)
        head = QHBoxLayout()
        head.addWidget(self.details_icon)
        head.addWidget(self.details_title, 1)
        head.addWidget(self.conn_spin)
        head.addWidget(self.btn_refresh)
        head.addWidget(self.btn_open)
        head.addWidget(self.btn_folder)
        self.lbl_url = QLabel()
        self.lbl_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_path = QLabel()
        self.lbl_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_info = QLabel()
        self.lbl_error = QLabel()
        self.lbl_error.setObjectName("error")
        self.lbl_error.setWordWrap(True)
        self.segment_bar = SegmentBar()
        form = QFormLayout()
        form.setVerticalSpacing(6)
        for label, widget in (("Link", self.lbl_url), ("Saved to", self.lbl_path), ("Info", self.lbl_info),
                              ("Progress", self.segment_bar)):
            caption = QLabel(label)
            caption.setProperty("muted", True)
            form.addRow(caption, widget)
        form.addRow("", self.lbl_error)
        self.details_hint = QLabel("Select a download to see its details.")
        self.details_hint.setProperty("muted", True)
        self.details_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.details_body = QWidget()
        body = QVBoxLayout(self.details_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.addLayout(head)
        body.addLayout(form)
        dl = QVBoxLayout(self.details)
        dl.setContentsMargins(16, 12, 16, 12)
        dl.addWidget(self.details_hint)
        dl.addWidget(self.details_body)

        right = QSplitter(Qt.Orientation.Vertical)
        right.setHandleWidth(1)
        right.addWidget(list_area)
        right.addWidget(self.details)
        right.setStretchFactor(0, 4)
        right.setStretchFactor(1, 1)
        right.setCollapsible(0, False)

        body_split = QWidget()
        bs = QHBoxLayout(body_split)
        bs.setContentsMargins(0, 0, 0, 0)
        bs.setSpacing(0)
        bs.addWidget(self.sidebar)
        bs.addWidget(right, 1)

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(bar)
        cl.addWidget(body_split, 1)
        self.setCentralWidget(central)

        self.speed_label = QLabel()
        self.statusBar().addWidget(self.speed_label)
        self.limit_button = QToolButton()
        self.limit_button.setObjectName("limitButton")
        self.limit_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.limit_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.limit_button.setToolTip("Limit the total download speed, so browsing stays fast")
        self.limit_menu = self._speed_menu()
        self.limit_button.setMenu(self.limit_menu)
        self.statusBar().addWidget(self.limit_button)
        self.update_button = QPushButton()
        self.update_button.setProperty("flat", True)
        self.update_button.setToolTip("A newer yt-dlp (for YouTube and other video sites) is ready")
        self.update_button.clicked.connect(self.restart_app)
        self.update_button.setVisible(False)
        self.statusBar().addWidget(self.update_button)
        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label)
        self.statusBar().setSizeGripEnabled(False)

    def _speed_menu(self) -> QMenu:
        menu = QMenu("Speed limit", self)
        for kb in SPEED_PRESETS_KB:
            a = menu.addAction(speed_text(kb))
            a.setCheckable(True)
            a.setData(kb)
            a.triggered.connect(lambda _, kb=kb: self.set_speed_limit(kb))
        menu.addSeparator()
        menu.addAction("Custom…").triggered.connect(lambda: self.open_settings("downloads"))
        menu.aboutToShow.connect(lambda: [a.setChecked(a.data() == self.speed_limit_kb())
                                          for a in menu.actions() if a.isCheckable()])
        return menu

    def speed_limit_kb(self) -> int:
        return int(self.settings.value("speed_limit_kb", 0))

    def set_speed_limit(self, kb: int):
        self.settings.setValue("speed_limit_kb", int(kb))
        self.manager.set_speed_limit(int(kb) * 1024)
        self.limit_button.setText(f"Speed limit: {speed_text(kb)}")
        self.limit_button.setIcon(theme.icon("gauge", theme.colors["warning"] if kb else theme.colors["muted"]))

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(theme.app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        show = menu.addAction("Show PyDM")
        show.triggered.connect(self.bring_to_front)
        menu.addAction(self.act_add)
        menu.addSeparator()
        menu.addAction(self.act_resume_all)
        menu.addAction(self.act_pause_all)
        menu.addMenu(self._speed_menu())
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.bring_to_front() if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                               QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.messageClicked.connect(
            lambda: self._notified_path and reveal_in_explorer(self._notified_path))
        self.tray.show()

    def apply_icons(self):
        """(Re)draw icons in the current theme's colours."""
        for a in self.actions():
            if a.data():
                a.setIcon(theme.icon(a.data()))
        self.add_button.setIcon(theme.icon("plus", theme.colors["accent_text"]))
        self._search_action.setIcon(theme.icon("search", theme.colors["muted"]))
        for i in range(self.sidebar.count()):
            item = self.sidebar.item(i)
            key = item.data(DATA)
            if key and not key.startswith("queue:"):
                color = theme.TYPES[key][1] if key in theme.TYPES else None
                item.setIcon(theme.icon(dict((c[0], c[2]) for c in CATEGORIES if c)[key], color))
            elif not key:
                item.setForeground(QColor(theme.colors["muted"]))
        self.btn_open.setIcon(theme.icon("open"))
        self.btn_refresh.setIcon(theme.icon("retry"))
        self.btn_folder.setIcon(theme.icon("folder"))
        self.empty_icon.setPixmap(theme.app_icon().pixmap(72, 72))
        if hasattr(self, "limit_button"):
            self.set_speed_limit(self.speed_limit_kb())  # recolour its icon
        theme.type_badge.cache_clear()
        self._row_ids = []  # rebuild rows so badges and dots pick up the new colours

    def apply_theme(self):
        theme.apply(QApplication.instance(), self.settings.value("theme", "system"))
        self.apply_icons()
        self.refresh()

    # --- refresh loop ---

    def refresh(self):
        # Rebuilding rows changes the selection, whose signal calls refresh() again;
        # re-entering a half-done rebuild makes Qt free the same rows twice (a crash).
        if getattr(self, "_refreshing", False):
            return
        self._refreshing = True
        try:
            self._refresh()
        finally:
            self._refreshing = False

    def _refresh(self):
        self.manager.pump()
        tasks = list(self.manager.tasks)
        snaps = {t.id: t.snapshot() for t in tasks}
        self._notify_changes(tasks, snaps)

        self._handle_queue_events(self.queues.tick())
        if self._errors:
            self._errors.clear()
            if not self._error_reported:  # once per session; details are in the log
                self._error_reported = True
                self.statusBar().showMessage("PyDM ran into a problem but kept running. Details: Settings → About "
                                             "→ Open log folder.", 15000)
                if self.tray:
                    self.tray.showMessage("PyDM ran into a problem", "It kept running. The details are in the log "
                                          "(Settings → About → Open log folder).",
                                          QSystemTrayIcon.MessageIcon.Warning, 8000)
        while self._update_results:
            self._update_done(self._update_results.pop(0))
        while self._scan_results:
            self._scan_done(*self._scan_results.pop(0))

        needle = self.search.text().strip().lower()
        counts: dict[str, int] = {}
        visible = []

        def matches(s):
            return not needle or needle in (s["filename"] or "").lower() or needle in s["url"].lower()

        for t in tasks:
            s = snaps[t.id]
            cats = task_category(t, s)
            for c in cats:
                counts[c] = counts.get(c, 0) + 1
            if self._category in cats and matches(s):
                visible.append(t.id)
        if self._category.startswith("queue:"):  # a queue lists its downloads in queue order
            visible = [t.id for t in self.queues.tasks(self._category[6:]) if t.id in snaps and matches(snaps[t.id])]
        self._update_queue_bar()
        if visible != self._row_ids:
            self._rebuild_rows(visible, snaps)
        for row, task_id in enumerate(visible):
            self._update_row(row, snaps[task_id])
        self.table.viewport().update()  # animate indeterminate bars
        self._update_sidebar(counts)
        self._update_empty(len(tasks), bool(needle))

        total_speed = sum(s["speed"] for s in snaps.values())
        active = sum(s["status"] in Status.ACTIVE for s in snaps.values())
        queued = sum(s["status"] == Status.QUEUED for s in snaps.values())
        parts = [f"{active} downloading"] + ([f"{queued} queued"] if queued else [])
        self.speed_label.setText(f"  ↓ {human_size(total_speed)}/s" if active else "  Idle")
        self.status_label.setText("  ·  ".join(parts + [f"{len(tasks)} total"]) + "  ")
        if self.tray:
            self.tray.setToolTip(f"{APP_NAME} · {active} downloading · {human_size(total_speed)}/s"
                                 if active else APP_NAME)

        selected = self.selected_tasks()
        if len(selected) > 1:
            self.status_label.setText(f"{len(selected)} selected  ·  " + self.status_label.text())
        self.details_hint.setText(f"{len(selected)} downloads selected. Press Delete to remove them."
                                  if len(selected) > 1 else "Select a download to see its details.")
        self._update_details(snaps.get(selected[0].id) if len(selected) == 1 else None)
        self._update_actions(selected)

    def _notify_changes(self, tasks, snaps):
        notify = self.tray is not None and self.settings.value("notify", True, type=bool)
        for t in tasks:
            s = snaps[t.id]
            before = self._last_status.get(t.id)
            if before and before != s["status"] and s["status"] in (Status.COMPLETED, Status.ERROR):
                log.info("%s: %s (%s)%s", s["status"], s["filename"], s["kind"],
                         f" - {s['error']}" if s["error"] else "")
            if before and before != s["status"] and s["status"] == Status.COMPLETED:
                self._on_completed(t, s, notify)
            elif notify and before and before != s["status"]:
                if s["status"] == Status.ERROR:
                    if s["kind"] == "ytdl":  # often fixed by a newer yt-dlp
                        self.check_ytdlp_update(auto=True, max_age=3600)
                    self._notified_path = None
                    self.tray.showMessage("Download failed", f"{s['filename']}\n{s['error'] or ''}",
                                          QSystemTrayIcon.MessageIcon.Warning, 7000)
            self._last_status[t.id] = s["status"]

    def _rebuild_rows(self, ids: list[str], snaps: dict):
        selected = {t.id for t in self.selected_tasks()}
        self.table.blockSignals(True)  # selection churn while rebuilding isn't a user action
        try:
            self._fill_rows(ids, snaps, selected)
        finally:
            self.table.blockSignals(False)

    def _fill_rows(self, ids: list[str], snaps: dict, selected: set):
        self.table.setRowCount(len(ids))
        for row, task_id in enumerate(ids):
            for col in range(len(COLUMNS)):
                item = QTableWidgetItem()
                if col in (COL_SIZE, COL_SPEED, COL_ETA):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, col, item)
            s = snaps[task_id]
            name_item = self.table.item(row, COL_NAME)
            name_item.setData(DATA, task_id)
            name_item.setIcon(theme.type_badge(theme.file_type(s["filename"] or "", s["kind"])))
            self.table.item(row, COL_ADDED).setForeground(QColor(theme.colors["muted"]))
            self.table.item(row, COL_ADDED).setText(friendly_date(self.manager.get(task_id).added_at))
        self._row_ids = ids
        self.table.clearSelection()
        for row, task_id in enumerate(ids):
            if task_id in selected:
                self.table.selectRow(row)

    def _update_row(self, row: int, s: dict):
        name = s["filename"] or s["url"].rsplit("/", 1)[-1] or s["url"]
        name_item = self.table.item(row, COL_NAME)
        if name_item.text() != name:
            name_item.setText(name)
            name_item.setIcon(theme.type_badge(theme.file_type(name, s["kind"])))
        name_item.setToolTip(f"{name}\n{s['url']}")
        size = human_size(s["total_size"]) if s["total_size"] else "?"
        self.table.item(row, COL_SIZE).setText(("~" + size) if s["size_estimated"] else size)

        status, progress = s["status"], s["progress"]
        if status == Status.COMPLETED:
            progress, text = 1.0, "100%"
        elif status == Status.MERGING:
            progress, text = None, "Merging"
        elif progress is not None:
            text = f"{100 * progress:.1f}%" if progress < 1 else "100%"
        else:
            text = human_size(s["downloaded"]) if s["downloaded"] else ""
        self.table.item(row, COL_PROGRESS).setData(DATA, (progress, status, text))

        active = status == Status.DOWNLOADING
        self.table.item(row, COL_SPEED).setText(f"{human_size(s['speed'])}/s" if active else "")
        self.table.item(row, COL_ETA).setText(human_time(s["eta"]) if active else "")
        status_item = self.table.item(row, COL_STATUS)
        q = self.queues.queue_of(s["id"])
        if q and status == Status.PAUSED and s["id"] not in q.tried:
            status = "In queue"  # shown only; the task itself is simply paused
        scanned = self._scans.get(s["id"]) or (self._saved_scan(s["id"]) if status == Status.COMPLETED else None)
        if getattr(scanned, "status", "") == "threat":
            status = "Threat found"
        if status_item.text() != status:
            status_item.setText(status)
            status_item.setIcon(QIcon(dot(status_color(status))))
            status_item.setForeground(status_color(status) if status in (Status.ERROR, Status.COMPLETED)
                                      else QColor(theme.colors["danger"]) if status == "Threat found"
                                      else QColor(theme.colors["text"]))
        status_item.setToolTip(s["error"] or "")

    def _update_sidebar(self, counts: dict[str, int]):
        any_type = any(counts.get(k) for k in theme.TYPES) or self._category in theme.TYPES
        for i in range(self.sidebar.count()):
            item = self.sidebar.item(i)
            key = item.data(DATA)
            if not key:
                if item.data(HEADER) == "types":
                    item.setHidden(not any_type)
                continue
            if key.startswith("queue:"):
                q = self.queues.get(key[6:])
                if q:
                    n = len(self.queues.tasks(q.id))
                    text = f"{q.name}   {n}" if n else q.name
                    if item.text() != text:
                        item.setText(text)
                    if item.data(HEADER) != q.running:  # running queues get a green play icon
                        item.setData(HEADER, q.running)
                        item.setIcon(theme.icon("play", theme.colors["success"]) if q.running
                                     else theme.icon("calendar"))
                continue
            label = next(c[1] for c in CATEGORIES if c and c[0] == key)
            n = counts.get(key, 0)
            text = f"{label}   {n}" if n else label
            if item.text() != text:
                item.setText(text)
            if key in theme.TYPES:
                item.setHidden(n == 0 and key != self._category)

    def _update_empty(self, total: int, searching: bool):
        empty = not self._row_ids
        self.stack.setCurrentWidget(self.empty if empty else self.table)
        self.details.setVisible(total > 0)
        if not empty:
            return
        if total == 0:
            title, text, buttons = ("No downloads yet",
                                    "Click Add URL, paste a link with Ctrl+V or drag one here.\n"
                                    "With the browser extension, downloads and videos come here by themselves.", True)
        elif searching:
            title, text, buttons = "No matches", f"Nothing matches “{self.search.text().strip()}”.", False
        else:
            title, text, buttons = "Nothing here", "No downloads in this list.", False
        self.empty_title.setText(title)
        self.empty_text.setText(text)
        self.empty_buttons.setVisible(buttons)

    def _update_details(self, s: dict | None):
        self.details_body.setVisible(s is not None)
        self.details_hint.setVisible(s is None)
        if s is None:
            for lbl in (self.lbl_url, self.lbl_path, self.lbl_info, self.lbl_error):
                lbl.clear()
            self.segment_bar.set_data(0, [])
            return
        self.details_title.setText(s["filename"] or s["url"])
        self.details_icon.setPixmap(theme.type_badge(theme.file_type(s["filename"] or "", s["kind"])).pixmap(32, 32))
        metrics = self.lbl_url.fontMetrics()
        width = max(200, self.details.width() - 160)
        self.lbl_url.setText(metrics.elidedText(s["url"], Qt.TextElideMode.ElideMiddle, width))
        self.lbl_url.setToolTip(s["url"])
        self.lbl_path.setText(s["path"] or "(chosen when the download starts)")
        resumable = "yes" if s["supports_ranges"] else ("no" if s["total_size"] is not None else "?")
        of = ("~" if s["size_estimated"] else "") + human_size(s["total_size"])
        parts = [f"{human_size(s['downloaded'])} of {of}"]
        if s["info"]:
            parts.append(s["info"])
        else:
            parts.append(f"{len(s['segments'])} segment(s)")
        parts += [f"{s['active_connections']} active connection(s)", f"resumable: {resumable}"]
        self.lbl_info.setText("  ·  ".join(parts))
        if s["kind"] in ("hls", "dash") and s["segments"]:
            total, segments = len(s["segments"]), s["segments"]  # one unit per video segment
        else:
            total = s["total_size"] or (s["downloaded"] if s["status"] == Status.COMPLETED else 0)
            segments = s["segments"]
        if not segments and s["status"] == Status.COMPLETED:
            total, segments = 1, [(0, 0, 1, False)]
        self.segment_bar.set_data(total, segments)
        error = s["error"] or ""
        if error and s["kind"] != "ytdl" and re.search(r"\b(401|403|404|410)\b|expired|forbidden|gone", error, re.I):
            error += "\nThe link has probably expired. Click Refresh link… to continue with a fresh one."
        elif error and s["kind"] == "ytdl" and "Sign in" not in error:
            error += "\nIf this keeps happening, check for a yt-dlp update in Settings → About."
        self.lbl_error.setText(error)
        self.lbl_error.setVisible(bool(error))
        self.btn_refresh.setVisible(s["status"] != Status.COMPLETED and s["kind"] != "ytdl")
        task = self.manager.get(s["id"])
        self.conn_spin.setVisible(task is not None and s["status"] != Status.COMPLETED and s["kind"] != "ytdl"
                                  and s["supports_ranges"] is not False)
        if task is not None and not self.conn_spin.hasFocus() and self.conn_spin.value() != task.connections:
            self.conn_spin.blockSignals(True)
            self.conn_spin.setValue(task.connections)
            self.conn_spin.blockSignals(False)
        result = self._scans.get(s["id"]) or self._saved_scan(s["id"])
        if result == "scanning":
            self.lbl_info.setText(self.lbl_info.text() + "  ·  scanning with Microsoft Defender…")
        elif result is not None:
            self.lbl_info.setText(self.lbl_info.text() + "  ·  " + result.text())

    def _update_actions(self, selected):
        statuses = {t.status for t in selected}
        self.act_resume.setEnabled(bool(statuses & {*Status.RESUMABLE, Status.CANCELLED}))
        self.act_pause.setEnabled(bool(statuses & {*Status.ACTIVE, Status.QUEUED}))
        self.act_remove.setEnabled(bool(selected))
        self.act_delete.setEnabled(bool(selected))
        self.act_select_all.setEnabled(self.table.rowCount() > 0)
        one_done = len(selected) == 1 and selected[0].status == Status.COMPLETED
        self.act_open.setEnabled(one_done)
        self.act_folder.setEnabled(len(selected) == 1 and selected[0].final_path is not None)
        self.act_copy.setEnabled(bool(selected))
        self.act_refresh.setEnabled(len(selected) == 1 and selected[0].status != Status.COMPLETED)
        self.btn_open.setEnabled(one_done)
        self.btn_folder.setEnabled(self.act_folder.isEnabled())
        tasks = self.manager.tasks
        self.act_resume_all.setEnabled(any(t.status in (*Status.RESUMABLE, Status.CANCELLED) for t in tasks))
        self.act_pause_all.setEnabled(any(t.status in (*Status.ACTIVE, Status.QUEUED) for t in tasks))

    def _category_changed(self, current, _previous):
        if current and current.data(DATA):
            self._category = current.data(DATA)
            self.table.clearSelection()
            self.refresh()

    # --- selection helpers ---

    def selected_tasks(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [t for t in (self.manager.get(self._row_ids[r]) for r in rows if r < len(self._row_ids)) if t]

    # --- actions ---

    def add_download(self, url: str = "", **prefill):
        url = url if isinstance(url, str) else ""
        self._dialog_open = True
        try:
            # Already in the list? Ask before showing the Add dialog (like a browser capture would).
            if url and self._handle_duplicate(url):
                return
            dlg = AddDownloadDialog(self.settings, url, self, queues=[(q.id, q.name) for q in self.queues.queues],
                                    **prefill)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            final_url = dlg.url.text().strip()
            if final_url != url and self._handle_duplicate(final_url):
                return
        finally:
            self._dialog_open = False
            QTimer.singleShot(0, self._process_incoming)
        folder = dlg.folder.text().strip()
        if not dlg.sort_by_type:
            self.settings.setValue("download_dir", folder)  # remember the last folder used
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Couldn't create the folder {folder}:\n{e}")
            return
        log.info("Added %s (%s, quality %s)", final_url, dlg.kind, dlg.variant())
        task = self.manager.add(final_url, folder, dlg.connections.value(),
                                dlg.filename.text().strip() or None, dlg.headers(), start=dlg.start_now(),
                                kind=dlg.kind, variant=dlg.variant())
        if dlg.queue_id():
            self.queues.add_tasks(dlg.queue_id(), [task.id])
        self.refresh()
        if task.id not in self._row_ids:  # filtered out: show it
            self.search.clear()
            self.sidebar.setCurrentRow(0)
        if task.id in self._row_ids:
            row = self._row_ids.index(task.id)
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, COL_NAME))

    # --- finished downloads: completion window and virus scan ---

    def _on_completed(self, task, s: dict, notify: bool):
        if s["path"] and scan.scanner() and scan.should_scan(s["path"], self.settings.value("virus_scan", "risky")):
            self._start_scan(task.id, s["path"])
        if self.settings.value("done_window", True, type=bool):
            if self._done_window is None:
                self._done_window = DoneWindow(self)
            showing = self._done_window.isVisible() and self._done_window.task_id != task.id
            self._recent_done = (self._recent_done if showing else []) + [task.id]
            self._done_window.show_task(task, others=len(self._recent_done) - 1)
        elif notify:
            self._notified_path = s["path"]
            self.tray.showMessage("Download complete", s["filename"], theme.app_icon(), 5000)

    def scan_state(self, task_id: str):
        return self._scans.get(task_id) or self._saved_scan(task_id)

    def _saved_scan(self, task_id: str):
        value = self.settings.value(f"scans/{task_id}")
        if not value:
            return None
        status, _, detail = str(value).partition("|")
        return scan.ScanResult(status, detail)

    def _start_scan(self, task_id: str, path: str):
        self._scans[task_id] = "scanning"
        with self._scan_lock:
            self._scan_queue.append((task_id, path))
            if len(self._scan_queue) > 1:
                return  # the running worker will get to it

        def work():  # one scan at a time, in order
            while True:
                with self._scan_lock:
                    if not self._scan_queue:
                        return
                    tid, p = self._scan_queue[0]
                result = scan.scan(p)
                self._scan_results.append((tid, result))
                with self._scan_lock:
                    self._scan_queue.pop(0)
        threading.Thread(target=work, daemon=True, name="virus-scan").start()

    def _scan_done(self, task_id: str, result):
        log.info("Virus scan of %s: %s %s", task_id, result.status, result.detail)
        self._scans[task_id] = result
        self.settings.setValue(f"scans/{task_id}", f"{result.status}|{result.detail}")
        task = self.manager.get(task_id)
        if self._done_window is not None and self._done_window.task_id == task_id:
            self._done_window.update_scan()
        if result.status == "threat":
            name = task.filename if task else "A download"
            if self._done_window is not None and self._done_window.task_id != task_id and task:
                self._done_window.show_task(task)  # make sure a threat is seen
            if self.tray:
                self.tray.showMessage("Threat found", f"{name}\n{result.text()}.",
                                      QSystemTrayIcon.MessageIcon.Critical, 10000)

    def _connections_changed(self, n: int):
        selected = self.selected_tasks()
        if len(selected) == 1:
            self.manager.set_connections(selected[0], n)

    def _resume_queues(self):
        if not self.settings.value("resume_queues", True, type=bool):
            for q in self.queues.queues:
                q.interrupted = False
            return
        resumed = self.queues.resume_interrupted()
        if resumed and self.tray and self.settings.value("notify", True, type=bool):
            names = ", ".join(q.name for q in resumed)
            self.tray.showMessage("Queue continued", f"{names} was running when PyDM closed, so it continues.",
                                  theme.app_icon(), 5000)
        self.refresh()

    # --- refreshing an expired link ---

    def refresh_link(self, task=None):
        if task is None:
            selected = self.selected_tasks()
            task = selected[0] if len(selected) == 1 else None
        if task is None or task.status == Status.COMPLETED:
            return
        if self._refresh_target is not None:
            self._refresh_target.reject()
        dlg = RefreshLinkDialog(self, task)
        self._refresh_target = dlg
        dlg.show()
        dlg.raise_()
        return dlg

    def _end_refresh(self, dlg):
        if self._refresh_target is dlg:
            self._refresh_target = None

    def apply_new_link(self, task, url: str, headers: dict) -> tuple[bool, str]:
        """Point `task` at `url` and resume it. Returns (ok, message for the user)."""
        if task.is_running:
            self.manager.pause(task)
            task.join(15)
        try:
            if isinstance(task, DownloadTask):
                size = probe_size(url, {**task.headers, **headers})
                if task.total_size and size and size != task.total_size:
                    answer = QMessageBox.question(
                        self, APP_NAME, f"This link is for a different file ({human_size(size)} instead of "
                        f"{human_size(task.total_size)}).\n\nDownload it from the start instead?")
                    if answer != QMessageBox.StandardButton.Yes:
                        return False, "Not used: that link is for a different file."
                task.set_url(url, headers)
            elif not task.set_url(url, headers):
                self.statusBar().showMessage("The new link offers a different version of this video, "
                                             "so it starts over.", 10000)
        except Exception as e:  # noqa: BLE001 - shown in the dialog
            return False, f"That link doesn't work either: {e}"
        self.manager.resume(task)
        self.refresh()
        self.statusBar().showMessage(f"Continuing {task.filename or 'the download'} with the new link.", 6000)
        return True, ""

    # --- yt-dlp updates ---

    def check_ytdlp_update(self, auto: bool = False, max_age: float = 20 * 3600, on_done=None):
        """Check PyPI for a newer yt-dlp and install it (verified) in the background.
        Automatic checks happen at most every `max_age` seconds and only if enabled."""
        if on_done:
            self._update_listeners.append(on_done)
        if self._update_busy or self._update_ready and auto:
            return
        if auto:
            if in_test_mode():
                return  # tests never reach out to PyPI on their own
            last = float(self.settings.value("ytdlp_last_check", 0))
            if not self.settings.value("auto_update_ytdlp", True, type=bool) or time.time() - last < max_age:
                return
        self._update_busy = True
        self.settings.setValue("ytdlp_last_check", time.time())

        def work():
            try:
                release = updates.check()
                if release["newer"]:
                    self._update_results.append(("updated", updates.install(release)))
                else:
                    self._update_results.append(("current", release["current"]))
            except Exception as e:  # noqa: BLE001 - offline, PyPI down, a refused release...
                self._update_results.append(("error", str(e) or e.__class__.__name__))
        threading.Thread(target=work, daemon=True, name="ytdlp-update").start()

    def _update_done(self, result):
        log.info("yt-dlp update check: %s %s", *result)
        self._update_busy = False
        self._update_last = result
        kind, detail = result
        if kind == "updated":
            self._update_ready = detail
            self.update_button.setText(f"yt-dlp {detail} ready · Restart PyDM")
            self.update_button.setVisible(True)
            if self.tray:
                self.tray.showMessage("yt-dlp updated", f"Version {detail} is ready for YouTube and other video "
                                      "sites. Restart PyDM to use it.", theme.app_icon(), 6000)
        listeners, self._update_listeners = self._update_listeners, []
        for f in listeners:
            try:
                f(result)
            except RuntimeError:
                pass  # its dialog was closed meanwhile

    def restart_app(self):
        """Restart PyDM (to load a new yt-dlp). Running downloads pause and carry on afterwards."""
        running = [t.id for t in self.manager.tasks if t.status in (*Status.ACTIVE, Status.QUEUED)]
        if running and QMessageBox.question(
                self, APP_NAME, f"{len(running)} download{'s' * (len(running) > 1)} will pause for a moment "
                "and continue after the restart. Restart now?") != QMessageBox.StandardButton.Yes:
            return
        self.settings.setValue("resume_after_restart", running)
        self.timer.stop()
        self.ipc_server.close()  # so the new PyDM doesn't hand over to this one
        self.manager.shutdown()
        self.queues.save()
        self.settings.sync()
        ipc.launch_gui()
        self.quit_app()

    def _resume_after_restart(self):
        ids = self.settings.value("resume_after_restart", [])
        ids = [ids] if isinstance(ids, str) else list(ids or [])
        self.settings.remove("resume_after_restart")
        for t in self.manager.tasks:
            if t.id in ids:
                self.manager.resume(t)

    # --- duplicates ---

    def find_duplicate(self, url: str):
        key = url_key(url)
        return next((t for t in self.manager.tasks if url_key(t.url) == key), None)

    def _handle_duplicate(self, url: str) -> bool:
        """True if the link is already in the list and the user chose not to add it again."""
        task = self.find_duplicate(url)
        if task is None:
            return False
        choice = self.ask_duplicate(task)
        if choice == "again":
            return False
        if choice == "open":
            self._select_task(task)
            self.open_selected()
        elif choice == "resume":
            self.manager.resume(task)
            self._select_task(task)
        return True

    def ask_duplicate(self, task) -> str:
        """Returns "again", "open", "resume" or "cancel"."""
        done = task.status == Status.COMPLETED
        exists = bool(task.final_path and task.final_path.exists())
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Information)
        name = task.filename or task.url
        box.setText(f"<b>This link is already in your downloads.</b><br><br>{name}<br>"
                    f"<span style='color:{theme.colors['muted']}'>{task.status}</span>")
        buttons = {}
        if done and exists:
            buttons["open"] = box.addButton("Open file", QMessageBox.ButtonRole.AcceptRole)
        elif not done:
            buttons["resume"] = box.addButton("Resume it", QMessageBox.ButtonRole.AcceptRole)
        buttons["again"] = box.addButton("Download again", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(next(iter(buttons.values())))
        box.exec()
        return next((k for k, b in buttons.items() if box.clickedButton() is b), "cancel")

    def _select_task(self, task):
        self.search.clear()
        self.sidebar.setCurrentRow(0)
        self.refresh()
        if task.id in self._row_ids:
            row = self._row_ids.index(task.id)
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, COL_NAME))
        self.bring_to_front()

    # --- clipboard ---

    def _clipboard_changed(self):
        if not self.settings.value("watch_clipboard", True, type=bool):
            return
        if QApplication.activeWindow() is not None:
            return  # copied inside PyDM itself
        text = QGuiApplication.clipboard().text().strip()
        if not text or len(text) > 2000 or any(c.isspace() for c in text) or not looks_like_url(text):
            return
        if text in self._clipboard_seen:
            return  # offer each link once
        self._clipboard_seen = (self._clipboard_seen + [text])[-50:]
        if self._refresh_target is not None:  # any link will do: it's for the Refresh link dialog
            self._incoming.append({"type": "download", "url": text, "via": "clipboard"})
            QTimer.singleShot(0, self._process_incoming)
            return
        ext = QUrl(text).fileName().rsplit(".", 1)[-1].lower() if "." in QUrl(text).fileName() else ""
        if ext in CAPTURE_EXTENSIONS or streams.guess_kind(text):
            self._incoming.append({"type": "download", "url": text, "via": "clipboard"})
            QTimer.singleShot(0, self._process_incoming)

    # --- messages from other processes (browser host, second launch) ---

    def _ipc_connection(self):
        while self.ipc_server.hasPendingConnections():
            sock = self.ipc_server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._ipc_read(s))
            sock.disconnected.connect(sock.deleteLater)

    def _ipc_read(self, sock):
        sock.setProperty("buf", (sock.property("buf") or b"") + bytes(sock.readAll()))
        buf = sock.property("buf")
        if not buf.endswith(b"\n"):
            return
        try:
            msg = json.loads(buf.decode("utf-8"))
        except ValueError:
            sock.disconnectFromServer()
            return
        sock.write(b"ok\n")
        sock.flush()
        kind = msg.get("type")
        if kind == "show":
            self.bring_to_front()
        elif kind == "download" and looks_like_url(str(msg.get("url", ""))):
            self._incoming.append(msg)
            QTimer.singleShot(0, self._process_incoming)

    def bring_to_front(self):
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        force_foreground(self)

    def _process_incoming(self):
        if self._dialog_open or not self._incoming:
            return
        msg = self._incoming.pop(0)
        if self._refresh_target is not None:  # the user is getting a fresh link for a download
            headers = {k: v for k, v in (("User-Agent", msg.get("userAgent")), ("Referer", msg.get("referrer")),
                                         ("Cookie", msg.get("cookies"))) if v}
            dlg = self._refresh_target
            dlg.raise_()
            dlg.activateWindow()
            dlg.use(msg["url"], headers)
            QTimer.singleShot(0, self._process_incoming)
            return
        self.bring_to_front()
        extra = {"User-Agent": msg["userAgent"]} if msg.get("userAgent") else {}
        size = msg.get("fileSize")
        kind = msg.get("kind") if msg.get("kind") in VIDEO_KINDS else ""
        source = ("Video captured from browser" if kind else "Captured from browser") \
            + (f" · {human_size(size)}" if size and size > 0 else "")
        if msg.get("via") == "clipboard":
            source = "Link copied to the clipboard"
        # For videos the page title makes a far better name than "master.m3u8".
        # (yt-dlp names YouTube videos itself, from the real video title.)
        filename = msg.get("filename") or (msg.get("title") if kind in ("hls", "dash") else "") or ""
        self.add_download(msg["url"], filename=filename,
                          referer=msg.get("referrer") or "", cookies=msg.get("cookies") or "",
                          extra_headers=extra, source=source, kind=kind)

    def resume_selected(self):
        for t in self.selected_tasks():
            self.manager.resume(t)
        self.refresh()

    def pause_selected(self):
        for t in self.selected_tasks():
            self.manager.pause(t)
        self.refresh()

    def toggle_selected(self):
        for t in self.selected_tasks():
            if t.status in (*Status.ACTIVE, Status.QUEUED):
                self.manager.pause(t)
            elif t.status != Status.COMPLETED:
                self.manager.resume(t)
        self.refresh()

    def resume_all(self):
        for t in self.manager.tasks:
            self.manager.resume(t)

    def pause_all(self):
        for t in self.manager.tasks:
            self.manager.pause(t)

    def remove_selected(self, delete_files: bool = False):
        tasks = self.selected_tasks()
        if not tasks:
            return
        dlg = DeleteDialog(tasks, delete_files or self.settings.value("delete_with_files", False, type=bool), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        with_files = dlg.with_files.isChecked()
        if dlg.with_files.isEnabled():
            self.settings.setValue("delete_with_files", with_files)  # remembered for next time
        self.delete_tasks(tasks, with_files)

    def delete_tasks(self, tasks: list, with_files: bool) -> list[str]:
        """Remove downloads from the list (and their files if asked). Returns the
        names whose file couldn't be deleted, e.g. because it's open in a player."""
        failed = []
        for t in tasks:
            self.queues.remove_task(t.id)
            try:
                self.manager.remove(t, with_files)
            except OSError:
                failed.append(t.filename or t.url)
                self.manager.remove(t, False)  # still take it off the list
        self.refresh()
        if failed:
            QMessageBox.warning(self, APP_NAME, "These files couldn't be deleted, maybe because they're open in "
                                "another program. They were removed from the list but are still on disk:\n\n"
                                + "\n".join(failed[:10]))
        return failed

    def table_select_all(self):
        if QApplication.focusWidget() is self.search:
            self.search.selectAll()
        else:
            self.table.selectAll()

    def _header_clicked(self, section: int):
        if section != COL_CHECK:
            return
        if len(self.table.selectionModel().selectedRows()) == self.table.rowCount():
            self.table.clearSelection()
        else:
            self.table.selectAll()

    def eventFilter(self, obj, event):
        # A click on a row's checkbox toggles just that row, like Ctrl+click.
        mouse = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                 QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease)
        if obj is self.table.viewport() and event.type() in mouse:
            if event.type() in mouse[:2]:
                index = self.table.indexAt(event.position().toPoint())
                if index.isValid() and index.column() == COL_CHECK and event.button() == Qt.MouseButton.LeftButton:
                    self._checkbox_press = True
                    if event.type() == QEvent.Type.MouseButtonPress:
                        sm = self.table.selectionModel()
                        sm.select(index, sm.SelectionFlag.Toggle | sm.SelectionFlag.Rows)
                        sm.setCurrentIndex(index, sm.SelectionFlag.NoUpdate)
                    return True
                self._checkbox_press = False
            elif self._checkbox_press:
                if event.type() == QEvent.Type.MouseButtonRelease:
                    self._checkbox_press = False
                return True
        return super().eventFilter(obj, event)

    def open_selected(self):
        tasks = self.selected_tasks()
        if tasks and tasks[0].final_path and tasks[0].final_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(tasks[0].final_path)))

    def open_selected_folder(self):
        tasks = self.selected_tasks()
        if not tasks or not tasks[0].final_path:
            return
        path = tasks[0].final_path
        reveal_in_explorer(str(path if path.exists() else tasks[0].dest_dir))

    def copy_url(self):
        text = "\n".join(t.url for t in self.selected_tasks())
        self._clipboard_seen.append(text)
        QGuiApplication.clipboard().setText(text)

    def open_settings(self, tab: str = "general"):
        dlg = SettingsDialog(self.settings, self, tab if isinstance(tab, str) else "general")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings.setValue("download_dir", dlg.folder.text().strip())
        self.settings.setValue("connections", dlg.connections.value())
        self.settings.setValue("max_active", dlg.max_active.value())
        self.settings.setValue("close_to_tray", dlg.tray.isChecked())
        self.settings.setValue("notify", dlg.notify.isChecked())
        self.settings.setValue("sort_by_type", dlg.sort_by_type.isChecked())
        self.settings.setValue("watch_clipboard", dlg.clipboard.isChecked())
        self.settings.setValue("done_window", dlg.done_window.isChecked())
        self.settings.setValue("virus_scan", dlg.virus_scan.currentData())
        self.settings.setValue("resume_queues", dlg.resume_queues.isChecked())
        self.set_speed_limit(dlg.speed_limit.value())
        self.manager.max_active = dlg.max_active.value()
        if dlg.theme_mode() != self.settings.value("theme", "system"):
            self.settings.setValue("theme", dlg.theme_mode())
            self.apply_theme()

    def _double_clicked(self, row: int, _col: int):
        task = self.manager.get(self._row_ids[row])
        if not task:
            return
        if task.status == Status.COMPLETED:
            self.open_selected()
        elif task.status in Status.ACTIVE:
            self.manager.pause(task)
        else:
            self.manager.resume(task)

    def _context_menu(self, pos):
        if not self.selected_tasks():
            return
        menu = QMenu(self)
        for a in (self.act_open, self.act_folder, None, self.act_resume, self.act_pause, self.act_refresh, None):
            menu.addSeparator() if a is None else menu.addAction(a)
        conn_tasks = [t for t in self.selected_tasks() if t.status != Status.COMPLETED
                      and t.snapshot()["kind"] != "ytdl"]
        if conn_tasks:
            conns = menu.addMenu(theme.icon("link"), "Connections")
            for n in (1, 2, 4, 8, 16, 32):
                a = conns.addAction(str(n))
                a.setCheckable(True)
                a.setChecked(all(t.connections == n for t in conn_tasks))
                a.triggered.connect(lambda _, n=n: [self.manager.set_connections(t, n) for t in conn_tasks])
        add_to = menu.addMenu(theme.icon("calendar"), "Add to queue")
        for q in self.queues.queues:
            add_to.addAction(q.name).triggered.connect(lambda _, qid=q.id: self.add_selected_to_queue(qid))
        add_to.addSeparator()
        add_to.addAction("New queue…").triggered.connect(lambda: self.add_selected_to_queue(None))
        ids = [t.id for t in self.selected_tasks()]
        if any(self.queues.queue_of(i) for i in ids):
            menu.addAction("Remove from queue").triggered.connect(self.remove_selected_from_queue)
        if self._category.startswith("queue:"):
            menu.addAction(self.act_move_up)
            menu.addAction(self.act_move_down)
        menu.addSeparator()
        for a in (self.act_copy, self.act_select_all, None, self.act_remove, self.act_delete):
            menu.addSeparator() if a is None else menu.addAction(a)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # --- queues ---

    def _sync_queue_items(self):
        """Rebuild the sidebar's queue entries (after a queue is added, renamed or deleted)."""
        current = self._category if hasattr(self, "_category") else "all"
        for i in reversed(range(self.sidebar.count())):
            if (self.sidebar.item(i).data(DATA) or "").startswith("queue:"):
                self.sidebar.takeItem(i)
        for q in self.queues.queues:
            item = QListWidgetItem(theme.icon("calendar"), q.name)
            item.setData(DATA, f"queue:{q.id}")
            self.sidebar.addItem(item)
            if f"queue:{q.id}" == current:
                self.sidebar.setCurrentItem(item)
        if current.startswith("queue:") and not self.queues.get(current[6:]):
            self.sidebar.setCurrentRow(0)

    def _update_queue_bar(self):
        q = self.queues.get(self._category[6:]) if self._category.startswith("queue:") else None
        self.queue_bar.setVisible(q is not None)
        if not q:
            return
        n = len(self.queues.tasks(q.id))
        nxt = self.queues.next_start(q)
        parts = [f"{n} download{'s' * (n != 1)}", f"{q.max_active} at a time",
                 "running" if q.running else (f"next start {nxt:%a %H:%M}" if nxt else q.schedule_text().lower())]
        if q.after != "nothing":
            parts.append(f"then: {AFTER_ACTIONS[q.after].lower()}")
        self.queue_title.setText(q.name)
        self.queue_info.setText("  ·  ".join(parts))
        if self.queue_start.isHidden() != q.running:
            self.queue_start.setVisible(not q.running)
            self.queue_stop.setVisible(q.running)
            self.queue_bar.layout().invalidate()

    def _queue_action(self, action: str):
        qid = self._category[6:]
        if action == "start":
            self.queues.start(qid)
        else:
            self.queues.stop(qid)
        self.refresh()

    def _sidebar_menu(self, pos):
        item = self.sidebar.itemAt(pos)
        key = (item.data(DATA) or "") if item else ""
        menu = QMenu(self)
        if key.startswith("queue:"):
            q = self.queues.get(key[6:])
            if q:
                if q.running:
                    menu.addAction(theme.icon("pause"), "Stop queue").triggered.connect(
                        lambda: (self.queues.stop(q.id), self.refresh()))
                else:
                    menu.addAction(theme.icon("play"), "Start queue").triggered.connect(
                        lambda: (self.queues.start(q.id), self.refresh()))
                menu.addAction(theme.icon("calendar"), "Schedule…").triggered.connect(
                    lambda: self.open_scheduler(q.id))
                menu.addSeparator()
        menu.addAction(theme.icon("plus"), "New queue…").triggered.connect(lambda: self.open_scheduler(new=True))
        menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    def open_scheduler(self, queue_id: str | None = None, new: bool = False):
        if queue_id is None and self._category.startswith("queue:"):
            queue_id = self._category[6:]
        dlg = SchedulerDialog(self, queue_id or MAIN_QUEUE)
        if new:
            dlg._new()
        timer = QTimer(dlg)  # keep statuses in the file list current
        timer.timeout.connect(dlg._refresh)
        timer.start(1000)
        dlg.exec()
        self.queues.save()
        self._sync_queue_items()
        self.refresh()

    def add_selected_to_queue(self, queue_id: str | None):
        ids = [t.id for t in self.selected_tasks()]
        if queue_id is None:
            q = self.queues.create(f"Queue {len(self.queues.queues) + 1}")
            queue_id = q.id
            self._sync_queue_items()
        self.queues.add_tasks(queue_id, ids)
        self.refresh()

    def remove_selected_from_queue(self):
        for t in self.selected_tasks():
            self.queues.remove_task(t.id)
        self.refresh()

    def move_in_queue(self, delta: int):
        if not self._category.startswith("queue:"):
            return
        ids = [t.id for t in self.selected_tasks()]
        self.queues.move(self._category[6:], ids, delta)
        self.refresh()
        self.table.clearSelection()
        for task_id in ids:
            if task_id in self._row_ids:
                self.table.selectRow(self._row_ids.index(task_id))

    def _handle_queue_events(self, events):
        for event, q in events:
            if self.tray and self.settings.value("notify", True, type=bool):
                text = {"started": "started", "stopped": "stopped", "finished": "finished"}[event]
                self.tray.showMessage(f"{q.name} {text}", q.schedule_text() if event == "started" else "",
                                      theme.app_icon(), 4000)
            if event == "finished" and q.after != "nothing":
                QTimer.singleShot(0, lambda q=q: self.after_queue(q))

    def after_queue(self, q):
        """Exit, sleep or shut down after a queue finished; sleep and shutdown get
        a 60-second countdown that can be cancelled."""
        if q.after == "exit":
            self.quit_app()
            return
        action = q.after
        verb = "shut down" if action == "shutdown" else "go to sleep"
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Warning)
        now_btn = box.addButton("Shut down now" if action == "shutdown" else "Sleep now",
                                QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        remaining = [60]

        def tick():
            box.setText(f"<b>“{q.name}” has finished.</b><br><br>"
                        f"The computer will {verb} in {remaining[0]} seconds.")
            if remaining[0] <= 0:
                box.done(0)
                go()
            remaining[0] -= 1

        def go():
            timer.stop()
            self.manager.save()
            self.queues.save()
            self.power(action)

        timer = QTimer(box)
        timer.timeout.connect(tick)
        tick()
        timer.start(1000)
        box.buttonClicked.connect(lambda b: go() if b is now_btn else timer.stop())
        self.bring_to_front()
        box.open()
        self._countdown = box

    # --- paste / drag & drop a link onto the window ---

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.StandardKey.Paste) and not isinstance(QApplication.focusWidget(), QLineEdit):
            text = QGuiApplication.clipboard().text().strip()
            if looks_like_url(text):
                self.add_download(text)
                return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        url = md.urls()[0].toString() if md.hasUrls() else md.text().strip()
        if looks_like_url(url):
            self.add_download(url)

    # --- closing ---

    def quit_app(self):
        self._quitting = True
        self.close()
        QApplication.quit()

    def closeEvent(self, e):
        if self.tray and not self._quitting and self.settings.value("close_to_tray", True, type=bool):
            e.ignore()
            self.hide()
            if not self.settings.value("tray_hint_shown", False, type=bool):
                self.tray.showMessage(APP_NAME, "PyDM keeps running here so downloads can finish. "
                                      "Right-click this icon to quit.", theme.app_icon(), 6000)
                self.settings.setValue("tray_hint_shown", True)
            return
        self.timer.stop()
        self.ipc_server.close()
        self.settings.setValue("geometry", self.saveGeometry())
        self.queues.save()       # running queues continue next time (if enabled)
        self.manager.shutdown()  # pauses everything and saves progress
        super().closeEvent(e)
        if self.tray:
            self.tray.hide()
            QApplication.quit()  # quitOnLastWindowClosed is off while the tray icon exists


def main():
    pydm_log.setup()
    if "--check-ytdlp" in sys.argv:  # verifies a downloaded yt-dlp before it's used (updates.self_test)
        i = sys.argv.index("--check-ytdlp")
        updates.run_self_test(sys.argv[i + 1], sys.argv[i + 2])
        return
    if "--build-check" in sys.argv:  # verifies a packaged build; see README "Building the installer"
        from . import buildcheck
        buildcheck.run(sys.argv[sys.argv.index("--build-check") + 1])
        return
    if "--install-browser" in sys.argv or "--uninstall-browser" in sys.argv:  # used by the installer
        install_browser.install(quiet=True) if "--install-browser" in sys.argv else install_browser.uninstall(True)
        return
    # Single instance: hand off to an already-running window if there is one.
    args = [a for a in sys.argv[1:] if looks_like_url(a)]
    message = {"type": "download", "url": args[0]} if args else {"type": "show"}
    if "--minimized" in sys.argv and not args:
        message = {"type": "ping"}  # already running: leave it where it is
    if ipc.send(message, 500):
        return

    if sys.platform == "win32":
        try:  # own taskbar entry and icon instead of Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PyDM.DownloadManager")
        except Exception:  # noqa: BLE001
            pass
    if getattr(sys, "frozen", False) and not os.environ.get("PYDM_NO_REGISTER"):
        try:  # keep the browser pointed at this copy of PyDM
            install_browser.install(quiet=True)
        except OSError:
            pass

    log.info("PyDM %s starting (%s, Python %s)", __version__, "installed" if getattr(sys, "frozen", False)
             else "from source", sys.version.split()[0])
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setWindowIcon(theme.app_icon())
    win = MainWindow()
    win.apply_theme()
    app.styleHints().colorSchemeChanged.connect(
        lambda _: win.settings.value("theme", "system") == "system" and win.apply_theme())
    app.setQuitOnLastWindowClosed(win.tray is None)
    if not ("--minimized" in sys.argv and win.tray):  # started with Windows: stay in the tray
        win.show()
    if args:
        win._incoming.append(message)
        QTimer.singleShot(0, win._process_incoming)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
