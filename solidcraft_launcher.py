#!/usr/bin/env python3
"""SolidCraft Launcher (PySide6).

Manage SolidCraft AppImages (stable and nightly), test GitHub pull requests by building
them with cargo, and open local projects with a chosen version.

Modelled on FreeCAD Smart Launcher by Deltahedra
(https://github.com/deltahedra3d/freecad-launcher, MIT): same layout and features, and
the colour theme is adapted from it. Unofficial; not affiliated with the SolidCraft project.
"""

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTabWidget,
    QTextBrowser, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

APP_NAME = "SolidCraft Launcher"
APP_VERSION = "0.1.0"
REPO = "tangency-labs/solidcraft"
REPO_URL = f"https://github.com/{REPO}"
API = f"https://api.github.com/repos/{REPO}"
USER_AGENT = f"SolidCraft-Launcher/{APP_VERSION}"

HOME = os.path.expanduser("~")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INSTALL_DIR = os.path.join(HOME, "Applications", "SolidCraft")
DEFAULT_SCAN_DIR = os.path.join(HOME, "CAD")
CACHE_DIR = os.path.join(HOME, ".cache", "solidcraft-launcher")
LOCK_FILE = os.path.join(HOME, ".solidcraft_launcher.lock")
BUILD_LOG_FILE = os.path.join(HOME, ".solidcraft_launcher_build.log")
APPS_DIR = os.path.join(HOME, ".local", "share", "applications")
SOLIDCRAFT_PREFS = os.path.join(HOME, ".local", "share", "solidcraft", "app.ron")

# Versioned AppImage names only ("solidcraft-linux-x86_64.AppImage" is the moving "latest" alias).
APPIMAGE_RE = re.compile(r"^solidcraft-(?P<ver>\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)-linux-x86_64\.AppImage$")
PROJECT_EXTENSIONS = {".solidcraft", ".step", ".stp", ".iges", ".igs", ".stl", ".3mf", ".obj"}
F3D_DIRECT = {".step", ".stp", ".iges", ".igs", ".stl", ".3mf", ".obj"}
# Tells the AppImage's AppRun that a launcher owns the menu entries, so it installs none itself.
LAUNCH_ENV = {"DESKTOPINTEGRATION": "solidcraft-launcher"}
SESSION_MIN_SEC = 5

_SSL = ssl.create_default_context()
_GH_TOKEN = None


def github_token():
    """The GitHub CLI's token when logged in (higher API rate limit), else None."""
    global _GH_TOKEN
    if _GH_TOKEN is None:
        _GH_TOKEN = ""
        if os.environ.get("GITHUB_TOKEN"):
            _GH_TOKEN = os.environ["GITHUB_TOKEN"]
        elif shutil.which("gh"):
            try:
                out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
                if out.returncode == 0:
                    _GH_TOKEN = out.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
    return _GH_TOKEN or None


def github_get(url, timeout=15):
    """GET JSON from api.github.com (the token, if any, is only ever sent there)."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if url.startswith("https://api.github.com/") and github_token():
        headers["Authorization"] = f"Bearer {github_token()}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
        return json.loads(resp.read().decode())


def fetch_bytes(url, timeout=15, limit=8 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
        return resp.read(limit)


# ---------- Versions ----------

def version_kind(ver):
    if "nightly" in ver:
        return "nightly"
    return "rc" if "-" in ver else "stable"


def version_key(ver):
    base, _, pre = ver.partition("-")
    nums = tuple(int(x) for x in base.split(".")[:3])
    return nums, tuple(int(x) for x in re.findall(r"\d+", pre))


def display_name(ver):
    m = re.search(r"nightly\.(\d{4})(\d{2})(\d{2})", ver)
    if m:
        return f"Nightly {m.group(1)}-{m.group(2)}-{m.group(3)} ({ver.split('-')[0]})"
    return ver


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def fmt_duration(sec):
    return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"


# ---------- Theme (adapted from FreeCAD Smart Launcher, MIT) ----------

DARK = {
    "bg": "#121316", "card": "#1C1D21", "input": "#16171A", "accent": "#3B82F6",
    "success": "#22C55E", "danger": "#EF4444", "warning": "#EAB308", "neutral": "#2A2B30",
    "neutral_dk": "#3A3B41", "text": "#E8E8EC", "muted": "#8B8B96", "link": "#6BA3F7",
    "error": "#F87171", "footer": "#6B6B76", "border": "#2E2F35", "hover": "#32333A",
    "text_area_bg": "#0E0F12", "text_area_fg": "#D4D4D8",
}
LIGHT = {
    "bg": "#F0F1F3", "card": "#FFFFFF", "input": "#F7F8F9", "accent": "#2563EB",
    "success": "#16A34A", "danger": "#DC2626", "warning": "#CA8A04", "neutral": "#E8E9EC",
    "neutral_dk": "#D5D6DB", "text": "#1A1B1E", "muted": "#6B6B76", "link": "#2563EB",
    "error": "#DC2626", "footer": "#6B6B76", "border": "#D8D9DE", "hover": "#ECEDEF",
    "text_area_bg": "#FAFBFC", "text_area_fg": "#1A1B1E",
}


def theme(dark):
    return DARK if dark else LIGHT


def chevron_icon():
    path = os.path.join(CACHE_DIR, "chevron_down.svg")
    if not os.path.exists(path):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write('<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8" viewBox="0 0 12 8">'
                        '<path d="M1 1.5 L6 6.5 L11 1.5" fill="none" stroke="#C4C4C8" stroke-width="1.8" '
                        'stroke-linecap="round" stroke-linejoin="round"/></svg>')
        except OSError:
            return ""
    return path


def make_qss(dark):
    t = theme(dark)
    chev = chevron_icon()
    arrow = f'image: url("{chev}");' if chev else "width: 0; height: 0;"
    btn = {  # (bg, fg, border, hover bg, hover border) per role
        "accent": ("#3A5F8A", "#FFFFFF", "#2E4D70", "#4A6F9A", "#6BA3F7") if dark else ("#C5D4E8", "#1A2A3A", "#9AB0C8", "#B0C4DC", "#3B82F6"),
        "success": ("#3D6B4F", "#FFFFFF", "#325A42", "#4A7A5C", "#4ADE80") if dark else ("#C5D9C8", "#1A2A1A", "#9AB89A", "#B0C8B4", "#16A34A"),
        "danger": ("#8A4545", "#FFFFFF", "#703838", "#9A5555", "#F87171") if dark else ("#E8C8C8", "#3A1A1A", "#C8A0A0", "#D8B0B0", "#DC2626"),
        "warning": ("#8A7040", "#F5F0E0", "#705830", "#9A8050", "#FBBF24") if dark else ("#E8DCC0", "#2A2418", "#C8B890", "#D8CCB0", "#CA8A04"),
    }
    roles = "".join(
        f"QPushButton#{r} {{ background-color: {bg}; color: {fg}; border: 1px solid {bd}; }}"
        f"QPushButton#{r}:hover {{ background-color: {hbg}; border: 1px solid {hbd}; }}"
        for r, (bg, fg, bd, hbg, hbd) in btn.items()
    )
    return f"""
    * {{ font-family: "Inter", "Noto Sans", sans-serif; }}
    QMainWindow, QDialog {{ background-color: {t['bg']}; color: {t['text']}; font-size: 12.5px; }}
    QWidget {{ color: {t['text']}; font-size: 12.5px; }}
    QFrame#card {{ background-color: {t['card']}; border-radius: 8px; border: 1px solid {t['border']}; }}
    QLabel {{ color: {t['text']}; background: transparent; }}
    QLabel#sectionTitle {{ font-size: 10.5px; font-weight: 700; color: {t['muted']}; letter-spacing: 1.1px; }}
    QLabel#muted {{ color: {t['muted']}; font-size: 11.5px; }}
    QLabel#statusOk {{ color: {t['success']}; font-size: 11px; font-weight: 600; }}
    QLabel#statusWarn {{ color: {t['warning']}; font-size: 11px; font-weight: 600; }}
    QLabel#footer {{ color: {t['footer']}; font-style: italic; font-size: 11.5px; }}
    QPushButton {{ background-color: {t['neutral']}; color: {t['text']}; border: 1px solid {t['border']};
        border-radius: 5px; padding: 4px 11px; font-weight: 600; font-size: 11.5px; min-height: 22px; max-height: 28px; }}
    QPushButton:hover {{ background-color: {t['hover']}; border-color: {t['muted']}; }}
    QPushButton:pressed {{ background-color: {t['neutral_dk']}; }}
    QPushButton:disabled {{ background-color: {t['bg']}; color: {t['muted']}; border-color: {t['border']}; }}
    {roles}
    QLineEdit, QComboBox {{ background-color: {t['input']}; color: {t['text']}; border: 1px solid {t['border']};
        border-radius: 5px; padding: 4px 8px; min-height: 24px; selection-background-color: {t['accent']}; }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {t['accent']}; }}
    QComboBox:hover {{ border-color: {t['muted']}; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 26px; border: none;
        border-left: 1px solid {t['border']}; background-color: {t['neutral']};
        border-top-right-radius: 5px; border-bottom-right-radius: 5px; }}
    QComboBox::down-arrow {{ {arrow} width: 11px; height: 7px; margin-right: 2px; }}
    QComboBox QAbstractItemView {{ background-color: {t['card']}; color: {t['text']};
        selection-background-color: {t['accent']}; border: 1px solid {t['border']}; outline: none; padding: 3px; }}
    QListWidget, QTreeWidget {{ background-color: {t['input']}; color: {t['text']}; border: 1px solid {t['border']};
        border-radius: 5px; outline: none; padding: 3px; }}
    QListWidget::item {{ padding: 5px 8px; border-radius: 4px; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{ background-color: {t['accent']}; color: white; }}
    QListWidget::item:hover {{ background-color: {t['hover']}; }}
    QCheckBox {{ color: {t['text']}; spacing: 8px; background: transparent; font-size: 12px; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 3px; border: 1px solid {t['border']}; background: {t['input']}; }}
    QCheckBox::indicator:checked {{ background-color: {t['accent']}; border-color: {t['accent']}; }}
    QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 6px; background: {t['card']}; top: -1px; }}
    QTabBar::tab {{ background: transparent; color: {t['muted']}; padding: 6px 14px; margin-right: 1px;
        border-top-left-radius: 5px; border-top-right-radius: 5px; font-weight: 600; font-size: 11.5px; border: 1px solid transparent; }}
    QTabBar::tab:selected {{ background: {t['card']}; color: {t['text']}; border: 1px solid {t['border']}; border-bottom-color: {t['card']}; }}
    QTabBar::tab:hover:!selected {{ color: {t['text']}; background: {t['hover']}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QAbstractScrollArea > QWidget#qt_scrollarea_viewport, QWidget#scrollInner {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 4px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QHeaderView::section {{ background-color: {t['card']}; color: {t['muted']}; padding: 6px; border: none;
        font-weight: 700; font-size: 10.5px; }}
    QTextEdit, QTextBrowser {{ background-color: {t['text_area_bg']}; color: {t['text_area_fg']};
        border: 1px solid {t['border']}; border-radius: 5px; }}
    QTextEdit {{ font-family: "JetBrains Mono", "Noto Sans Mono", monospace; font-size: 12px; }}
    QTextBrowser {{ font-family: "Inter", "Noto Sans", sans-serif; font-size: 12.5px; }}
    QProgressBar {{ border: 1px solid {t['border']}; border-radius: 5px; background: {t['input']}; text-align: center;
        color: {t['text']}; font-size: 11px; font-weight: 600; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
    """


# ---------- Small widgets ----------

class ElidedLabel(QLabel):
    """A label that elides long lines with "…" instead of widening its column."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(10)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def text(self):
        return self._full

    def setText(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        self._elide()

    def _elide(self):
        fm = self.fontMetrics()
        w = max(10, self.width())
        super().setText("\n".join(fm.elidedText(line, Qt.ElideRight, w) for line in self._full.split("\n")))

    def resizeEvent(self, e):
        self._elide()
        super().resizeEvent(e)


def card():
    f = QFrame()
    f.setObjectName("card")
    return f


def section_title(text):
    lbl = QLabel(text)
    lbl.setObjectName("sectionTitle")
    return lbl


def button(text, role=None, slot=None, tip=None):
    b = QPushButton(text)
    if role:
        b.setObjectName(role)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    return b


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


class GitHubBrowser(QTextBrowser):
    """Shows GitHub Markdown with its images (prefetched off the GUI thread)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.images = {}
        self.setOpenExternalLinks(True)

    def loadResource(self, kind, url):
        if kind == QTextDocument.ImageResource and url.toString() in self.images:
            return self.images[url.toString()]
        return super().loadResource(kind, url)


# ---------- Dialogs ----------

class StatsDialog(QDialog):
    def __init__(self, parent, stats, dark, on_reset):
        super().__init__(parent)
        self.setWindowTitle("Statistics")
        self.resize(520, 400)
        self.setStyleSheet(make_qss(dark))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addWidget(section_title("SOLIDCRAFT USAGE STATISTICS"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Version / build", "Time", "Launches"])
        self.tree.setColumnWidth(0, 280)
        self.tree.setRootIsDecorated(False)
        lay.addWidget(self.tree, 1)
        for name, d in sorted(stats.items(), key=lambda kv: -kv[1].get("time_sec", 0)):
            self.tree.addTopLevelItem(QTreeWidgetItem([name, fmt_duration(d.get("time_sec", 0)), str(d.get("launches", 0))]))
        total = sum(d.get("time_sec", 0) for d in stats.values())
        launches = sum(d.get("launches", 0) for d in stats.values())
        row = QHBoxLayout()
        self.lbl_total = QLabel(f"Total: {fmt_duration(total)} ({launches} launches)")
        row.addWidget(self.lbl_total)
        row.addStretch()

        def reset():
            if QMessageBox.question(self, "Reset statistics", "Clear all usage times and launch counts?\nThis cannot be undone.") == QMessageBox.Yes:
                on_reset()
                self.tree.clear()
                self.lbl_total.setText("Total: 0h 00m (0 launches)")

        row.addWidget(button("Reset", "danger", reset))
        row.addWidget(button("OK", "accent", self.accept))
        lay.addLayout(row)


class InfoDialog(QDialog):
    def __init__(self, parent, dark, install_dir):
        super().__init__(parent)
        self.setWindowTitle("About / Infos")
        self.resize(540, 460)
        self.setStyleSheet(make_qss(dark))
        t = theme(dark)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        text = QLabel(
            f"<b>{APP_NAME} {APP_VERSION}</b><br><br>"
            "• Downloads, updates and launches SolidCraft <b>stable</b> and <b>nightly</b> AppImages from "
            f'<a style="color:{t["link"]}" href="{REPO_URL}/releases">the official GitHub releases</a>.<br>'
            "• Builds GitHub pull requests (or <i>main</i>) with cargo and launches the result. A Rust toolchain "
            "is found automatically, or installed into the install folder on request (no root).<br>"
            "• Project library: preview pictures come from each version's own <tt>solidcraft-cli</tt>; "
            f'3D view opens the file in <a style="color:{t["link"]}" href="https://f3d.app/">F3D</a> if it is installed '
            "(SolidCraft designs are exported to STL first).<br>"
            "• Versions it launches don't add their own menu entries; use <i>Desktop entry</i> for that.<br>"
            "• Each SolidCraft it starts gets a private control port, so <i>Open in running instance</i> can "
            "hand it a file.<br><br>"
            f"Install folder: <tt>{install_dir}</tt><br>"
            f"Build log: <tt>{BUILD_LOG_FILE}</tt><br><br>"
            "Unofficial; not affiliated with the SolidCraft project. Modelled on "
            f'<a style="color:{t["link"]}" href="https://github.com/deltahedra3d/freecad-launcher">FreeCAD Smart '
            "Launcher</a> by Deltahedra (MIT)."
        )
        text.setWordWrap(True)
        text.setOpenExternalLinks(True)
        text.setTextFormat(Qt.RichText)
        lay.addWidget(text)
        lay.addStretch()
        lay.addWidget(button("OK", "accent", self.accept), 0, Qt.AlignCenter)


class CopyableErrorDialog(QDialog):
    def __init__(self, parent, title, message, dark):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 460)
        self.setStyleSheet(make_qss(dark))
        lay = QVBoxLayout(self)
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {theme(dark)['error']}; font-weight: 700; font-size: 14px;")
        lay.addWidget(lbl)
        text = QTextEdit()
        text.setPlainText(message)
        lay.addWidget(text, 1)
        row = QHBoxLayout()
        row.addWidget(button("Copy to clipboard", "accent", lambda: QApplication.clipboard().setText(text.toPlainText())))
        row.addStretch()
        row.addWidget(button("Close", None, self.accept))
        lay.addLayout(row)


class DownloadDialog(QDialog):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle("Download SolidCraft Versions")
        self.setFixedSize(560, 360)
        self.setStyleSheet(make_qss(app.dark))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Install folder:"))
        row = QHBoxLayout()
        self.edit_dir = QLineEdit(app.install_dir)
        self.edit_dir.setReadOnly(True)
        row.addWidget(self.edit_dir, 1)
        row.addWidget(button("Browse...", None, self.change_dir))
        lay.addLayout(row)
        self.combos = {}
        self.buttons = []
        for kind, label in (("stable", "Stable version:"), ("nightly", "Nightly version:")):
            lay.addWidget(QLabel(label))
            r = QHBoxLayout()
            combo = QComboBox()
            for rel in app.remote[kind]:
                combo.addItem(display_name(rel["ver"]), rel["filename"])
            self.combos[kind] = combo
            r.addWidget(combo, 1)
            b = button("Download", "accent", lambda _=False, k=kind: self.start(k))
            self.buttons.append(b)
            r.addWidget(b)
            lay.addLayout(r)
        lay.addStretch()
        self.lbl = QLabel(app.lbl_status.text())
        self.lbl.setObjectName("muted")
        lay.addWidget(self.lbl)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        lay.addWidget(self.progress)
        r = QHBoxLayout()
        r.addStretch()
        self.btn_cancel = button("Cancel download", "danger", app.cancel_download)
        self.btn_cancel.setEnabled(bool(app.downloading))
        r.addWidget(self.btn_cancel)
        lay.addLayout(r)
        app.sig.download_status.connect(self.on_status)
        self.finished.connect(lambda _r: app.sig.download_status.disconnect(self.on_status))

    def change_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Select install folder", self.app.install_dir)
        if folder:
            self.app.set_install_dir(folder)
            self.edit_dir.setText(folder)

    def start(self, kind):
        fn = self.combos[kind].currentData()
        if fn:
            self.progress.setValue(0)
            self.app.download_version(fn)

    def on_status(self, text, pct, busy):
        self.lbl.setText(text)
        if pct >= 0:
            self.progress.setValue(pct)
        self.btn_cancel.setEnabled(busy)
        for b in self.buttons:
            b.setEnabled(not busy)
        if text.startswith("Download complete"):
            QTimer.singleShot(500, self.accept)


class UpdateDialog(QDialog):
    """First-run welcome or "new version available" prompt."""

    def __init__(self, parent, updates, dark, welcome):
        super().__init__(parent)
        self.choice = None
        self.setMinimumWidth(520)
        self.setStyleSheet(make_qss(dark))
        self.setWindowTitle("Welcome" if welcome else "Updates available")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        lay.addWidget(section_title("WELCOME TO SOLIDCRAFT LAUNCHER" if welcome else "NEW SOLIDCRAFT VERSION(S) AVAILABLE"))
        if welcome:
            body = QLabel("No SolidCraft version was found in the install folder.\n\nStart by downloading a stable version, a nightly build, or both.")
            body.setWordWrap(True)
            lay.addWidget(body)
        lines = QLabel("\n".join(f"• {u['kind'].capitalize()}: {display_name(u['ver'])}" for u in updates))
        lines.setObjectName("muted" if welcome else "")
        lay.addWidget(lines)
        hint = QLabel("Choose what to download, or Close to dismiss this window.")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(button("Close", None, self.reject))
        row.addStretch()
        kinds = {u["kind"] for u in updates}
        verb = "Download" if welcome else "Update"
        if "stable" in kinds:
            row.addWidget(button(f"{verb} stable", "accent", lambda: self.pick("stable")))
        if "nightly" in kinds:
            row.addWidget(button(f"{verb} nightly", "warning", lambda: self.pick("nightly")))
        if len(kinds) == 2:
            row.addWidget(button(f"{verb} both", "success", lambda: self.pick("both")))
        lay.addLayout(row)

    def pick(self, choice):
        self.choice = choice
        self.accept()


class PRConversationDialog(QDialog):
    sig_loaded = Signal(str, dict)

    def __init__(self, parent, pr, dark):
        super().__init__(parent)
        self.pr = pr
        num = pr.get("number")
        self.setWindowTitle(f"PR #{num} — Conversation")
        self.resize(800, 660)
        self.setStyleSheet(make_qss(dark))
        lay = QVBoxLayout(self)
        hdr = QLabel(f"#{num}  —  {pr.get('title', '')}")
        hdr.setWordWrap(True)
        hdr.setStyleSheet("font-weight: 700; font-size: 14px;")
        lay.addWidget(hdr)
        meta = QLabel(f"@{(pr.get('user') or {}).get('login', '?')}  ·  {(pr.get('created_at') or '')[:10]}")
        meta.setObjectName("muted")
        lay.addWidget(meta)
        self.body = GitHubBrowser()
        self.body.setMarkdown("*Loading conversation…*")
        lay.addWidget(self.body, 1)
        row = QHBoxLayout()
        row.addWidget(button("Open on GitHub", None, lambda: webbrowser.open(f"{REPO_URL}/pull/{num}")))
        row.addStretch()
        row.addWidget(button("Close", "accent", self.accept))
        lay.addLayout(row)
        self.sig_loaded.connect(self.apply)
        threading.Thread(target=self.load, daemon=True).start()

    def load(self):
        num = self.pr.get("number")
        images = {}
        try:
            pr = github_get(f"{API}/pulls/{num}")
            comments = github_get(f"{API}/issues/{num}/comments?per_page=100")
            parts = [f"### Description — @{pr['user']['login']} · {pr['created_at'][:10]}\n\n{pr.get('body') or '*(no description)*'}"]
            for c in comments:
                parts.append(f"### @{c['user']['login']} · {c['created_at'][:16].replace('T', ' ')}\n\n{c.get('body') or ''}")
            md = "\n\n---\n\n".join(parts)
            # Raw <img> tags become Markdown images so the browser shows them.
            md = re.sub(r'<img[^>]*src="([^"]+)"[^>]*>', r"![](\1)", md)
            for url in list(dict.fromkeys(re.findall(r"!\[[^\]]*\]\((https?://[^)\s]+)", md)))[:30]:
                try:
                    img = QImage.fromData(fetch_bytes(url))
                    if not img.isNull():
                        images[url] = img.scaledToWidth(min(img.width(), 720), Qt.SmoothTransformation)
                except (OSError, ValueError):
                    pass
        except Exception as e:  # network or API error: show it in the dialog
            md = f"**Could not load the conversation:** {e}"
        self.sig_loaded.emit(md, images)

    def apply(self, md, images):
        self.body.images = images
        self.body.setMarkdown(md)


class SourceBuildDialog(QDialog):
    """Guided clone + build of SolidCraft main, with a confirmation before each stage."""

    sig_log = Signal(str)
    sig_progress = Signal(int)
    sig_status = Signal(str)
    sig_done = Signal(bool, str)
    sig_confirm = Signal(str, str)

    def __init__(self, parent, dark):
        super().__init__(parent)
        self.setWindowTitle("Build SolidCraft from source")
        self.resize(720, 460)
        self.setStyleSheet(make_qss(dark))
        self.cancelled = False
        self.proc = None
        self._answer = False
        self._event = threading.Event()
        lay = QVBoxLayout(self)
        self.lbl = QLabel("Preparing…")
        self.lbl.setObjectName("muted")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        lay.addWidget(self.progress)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QTextEdit.NoWrap)
        lay.addWidget(self.log, 1)
        row = QHBoxLayout()
        row.addStretch()
        self.btn_cancel = button("Cancel", "danger", self.cancel)
        self.btn_close = button("Close", None, self.accept)
        self.btn_close.setEnabled(False)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        self.sig_log.connect(self._append)
        self.sig_progress.connect(self.progress.setValue)
        self.sig_status.connect(self.lbl.setText)
        self.sig_done.connect(self._done)
        self.sig_confirm.connect(self._ask)

    def confirm(self, title, message):
        """Ask on the GUI thread from a worker thread; blocks the worker until answered."""
        self._event.clear()
        self.sig_confirm.emit(title, message)
        self._event.wait()
        return self._answer

    def _ask(self, title, message):
        self._answer = QMessageBox.question(self, title, message) == QMessageBox.Yes
        self._event.set()

    def _append(self, text):
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def cancel(self):
        self.cancelled = True
        self.lbl.setText("Cancelling…")
        kill_group(self.proc)

    def _done(self, ok, message):
        self.btn_cancel.setEnabled(False)
        self.btn_close.setEnabled(True)
        self.lbl.setText(message)
        if ok:
            self.progress.setValue(100)

    def reject(self):
        if self.btn_close.isEnabled():
            super().reject()
        else:
            self.cancel()


def kill_group(proc):
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            try:
                proc.terminate()
            except OSError:
                pass


class BuildCancelled(Exception):
    pass


# ---------- Signals hub (worker threads -> GUI) ----------

class Signals(QObject):
    releases = Signal(dict)
    releases_error = Signal(str)
    milestones = Signal(list)
    download_status = Signal(str, int, bool)  # text, percent (-1 unknown), busy
    download_done = Signal(str)
    refresh_versions = Signal()
    footer = Signal()
    status = Signal(str)
    error = Signal(str, str)
    prs_recent = Signal(list)
    prs_recent_error = Signal(str)
    prs_search = Signal(list)
    prs_search_error = Signal(str)
    pr_meta = Signal(dict)
    pr_build_status = Signal(str, int)
    pr_build_done = Signal(bool, str)
    preview = Signal(str, str, str)  # project path, png path ("" = none), message
    projects = Signal(list)


# ---------- Main window ----------

class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sig = Signals()
        self.config = {}
        self.install_dir = DEFAULT_INSTALL_DIR
        self.load_config()
        self.stats = self.load_stats()
        self.dark = self.config.get("dark_mode", True)
        self.remote = {"stable": [], "nightly": []}
        self.remote_by_file = {}
        self.local = []
        self.selected_kind = self.config.get("selected_kind", "stable")
        self.downloading = None
        self._cancel_download = False
        self.instances = []  # SolidCraft processes this launcher started: dict(proc, port, token, name)
        self.pr_favorites = self.config.get("pr_favorites", [])
        self.current_pr = None
        self.build_proc = None
        self.build_cancel = False
        self.projects = {}
        self._preview_for = None
        self._update_prompted = False

        self.setWindowTitle(APP_NAME)
        icon = self.icon_path()
        if icon:
            self.setWindowIcon(QIcon(icon))
        self.resize(1300, 840)
        self.setStyleSheet(make_qss(self.dark))
        self.build_ui()
        self.connect_signals()
        self.refresh_local_versions()
        self.refresh_projects()
        self.update_footer()
        self._update_pr_panel()
        threading.Thread(target=self.fetch_releases, daemon=True).start()
        threading.Thread(target=self.fetch_milestones, daemon=True).start()
        threading.Thread(target=self.fetch_recent_prs, daemon=True).start()
        self.instance_timer = QTimer(self)
        self.instance_timer.timeout.connect(self._reap_instances)
        self.instance_timer.start(3000)

    # ----- config and stats -----

    def config_file(self):
        return os.path.join(self.install_dir, "launcher_config.json")

    def stats_file(self):
        return os.path.join(self.install_dir, "time_tracker.json")

    def load_config(self):
        pointer = os.path.join(HOME, ".config", "solidcraft-launcher", "install_dir")
        try:
            with open(pointer, encoding="utf-8") as f:
                d = f.read().strip()
                if d:
                    self.install_dir = d
        except OSError:
            pass
        os.makedirs(self.install_dir, exist_ok=True)
        try:
            with open(self.config_file(), encoding="utf-8") as f:
                self.config = json.load(f)
        except (OSError, ValueError):
            self.config = {}

    def save_config(self):
        try:
            os.makedirs(self.install_dir, exist_ok=True)
            tmp = self.config_file() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            os.replace(tmp, self.config_file())
        except OSError as e:
            self.sig.status.emit(f"Could not save settings: {e}")

    def load_stats(self):
        try:
            with open(self.stats_file(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_stats(self):
        try:
            tmp = self.stats_file() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2)
            os.replace(tmp, self.stats_file())
        except OSError:
            pass

    def set_install_dir(self, folder):
        self.install_dir = folder
        os.makedirs(folder, exist_ok=True)
        pointer_dir = os.path.join(HOME, ".config", "solidcraft-launcher")
        os.makedirs(pointer_dir, exist_ok=True)
        with open(os.path.join(pointer_dir, "install_dir"), "w", encoding="utf-8") as f:
            f.write(folder)
        self.save_config()
        self.stats = self.load_stats() or self.stats
        self.refresh_local_versions()

    def icon_path(self):
        for p in (os.path.join(SCRIPT_DIR, "icon.png"), os.path.join(getattr(sys, "_MEIPASS", SCRIPT_DIR), "icon.png")):
            if os.path.exists(p):
                return p
        return None

    # ----- UI -----

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(16, 12, 16, 8)
        outer.setSpacing(8)

        header = QHBoxLayout()
        icon = self.icon_path()
        if icon:
            lbl_icon = QLabel()
            lbl_icon.setPixmap(QPixmap(icon).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            header.addWidget(lbl_icon)
        title = QLabel("SolidCraft Launcher")
        title.setStyleSheet("font-size: 22px; font-weight: 700; letter-spacing: 0.2px;")
        header.addWidget(title)
        header.addStretch()
        outer.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(12)
        outer.addLayout(content, 1)

        # Left column: versions, community, options
        left_inner = QWidget()
        left_inner.setObjectName("scrollInner")
        left = QVBoxLayout(left_inner)
        left.setContentsMargins(0, 0, 4, 0)
        left.setSpacing(10)
        left.setAlignment(Qt.AlignTop)

        b1 = card()
        l1 = QVBoxLayout(b1)
        l1.setContentsMargins(14, 12, 14, 12)
        l1.setSpacing(8)
        self.version_combos = {}
        self.version_status = {}
        for kind, title_text in (("stable", "STABLE"), ("nightly", "NIGHTLY"), ("other", "OTHER BUILDS")):
            hdr = QHBoxLayout()
            hdr.addWidget(section_title(title_text))
            hdr.addStretch()
            st = QLabel("Checking…" if kind != "other" else "")
            st.setObjectName("muted")
            hdr.addWidget(st)
            combo = QComboBox()
            combo.setFixedHeight(32)
            combo.activated.connect(lambda _i, k=kind: self.select_kind(k))
            self.version_combos[kind] = combo
            self.version_status[kind] = st
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 4 if kind != "stable" else 0, 0, 0)
            wl.setSpacing(6)
            wl.addLayout(hdr)
            wl.addWidget(combo)
            l1.addWidget(wrap)
            if kind == "other":
                self.other_wrap = wrap
                combo.setToolTip("Your installed SolidCraft, your own builds, and pull requests you built")
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(button("Download", "accent", self.open_download_dialog))
        row.addWidget(button("Desktop entry", "accent", self.create_desktop_entry, "Add the selected version to the application menu"))
        row.addStretch()
        self.btn_delete = button("Delete", "danger", self.delete_local)
        row.addWidget(self.btn_delete)
        row.addWidget(button("Launch", "success", self.launch_selected))
        l1.addLayout(row)
        left.addWidget(b1)

        b3 = card()
        l3 = QVBoxLayout(b3)
        l3.setContentsMargins(14, 12, 14, 12)
        l3.setSpacing(8)
        l3.addWidget(section_title("SOLIDCRAFT COMMUNITY"))
        links = QHBoxLayout()
        for text, url in (("🌐 Website", "https://solidcraft.io"), ("📖 Docs", "https://docs.solidcraft.io"),
                          ("💬 Discord", "https://discord.gg/FtU4NQNVDH"), ("💻 GitHub", REPO_URL)):
            links.addWidget(button(text, None, lambda _=False, u=url: webbrowser.open(u)))
        l3.addLayout(links)
        self.milestone_box = QVBoxLayout()
        self.milestone_box.setSpacing(4)
        ph = QLabel("Milestones: loading…")
        ph.setObjectName("muted")
        self.milestone_box.addWidget(ph)
        l3.addLayout(self.milestone_box)
        left.addWidget(b3)

        b4 = card()
        l4 = QVBoxLayout(b4)
        l4.setContentsMargins(14, 12, 14, 12)
        l4.setSpacing(4)
        l4.addWidget(section_title("LAUNCHER OPTIONS"))
        self.chk_autoclose = QCheckBox("Close launcher on launch")
        self.chk_autoclose.setChecked(self.config.get("autoclose", False))
        self.chk_autoclose.toggled.connect(lambda v: self.set_option("autoclose", v))
        l4.addWidget(self.chk_autoclose)
        self.chk_dark = QCheckBox("Dark mode")
        self.chk_dark.setChecked(self.dark)
        self.chk_dark.toggled.connect(self.toggle_dark)
        l4.addWidget(self.chk_dark)
        self.chk_reminder = QCheckBox("Turn off update reminder")
        self.chk_reminder.setChecked(self.config.get("disable_update_reminder", False))
        self.chk_reminder.setToolTip("Don't show the startup window when a newer SolidCraft is available")
        self.chk_reminder.toggled.connect(lambda v: self.set_option("disable_update_reminder", v))
        l4.addWidget(self.chk_reminder)
        left.addWidget(b4)
        left.addStretch()

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_inner)
        left_scroll.setMinimumWidth(420)
        left_scroll.setMaximumWidth(540)
        content.addWidget(left_scroll, 0)

        # Centre: pull requests
        pr_panel = card()
        pl = QVBoxLayout(pr_panel)
        pl.setContentsMargins(12, 12, 12, 12)
        pl.setSpacing(8)
        hdr = QHBoxLayout()
        hdr.addWidget(section_title("PULL REQUESTS"))
        hdr.addStretch()
        btn_refresh = button("↻", None, lambda: threading.Thread(target=self.fetch_recent_prs, daemon=True).start(), "Refresh pull requests")
        btn_refresh.setFixedSize(32, 28)
        hdr.addWidget(btn_refresh)
        pl.addLayout(hdr)
        self.pr_tabs = QTabWidget()
        self.pr_recent_layout = self._scroll_tab("Recent")
        search_page = QWidget()
        sl = QVBoxLayout(search_page)
        sl.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.pr_search_edit = QLineEdit()
        self.pr_search_edit.setPlaceholderText("PR title, author, or PR number…")
        self.pr_search_edit.returnPressed.connect(self.search_prs)
        bar.addWidget(self.pr_search_edit, 1)
        bar.addWidget(button("Search", "accent", self.search_prs))
        sl.addLayout(bar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("scrollInner")
        self.pr_search_layout = QVBoxLayout(inner)
        self.pr_search_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(inner)
        sl.addWidget(scroll, 1)
        self.pr_tabs.addTab(search_page, "Search")
        self.pr_fav_layout = self._scroll_tab("Favorites")
        pl.addWidget(self.pr_tabs, 1)
        pl.addWidget(button("VIEW MORE ON GITHUB", "accent", lambda: webbrowser.open(f"{REPO_URL}/pulls")))

        b2 = card()
        b2.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        l2 = QVBoxLayout(b2)
        l2.setContentsMargins(14, 12, 14, 12)
        l2.setSpacing(8)
        l2.addWidget(section_title("TEST A GITHUB PULL REQUEST"))
        self.pr_stack = QStackedWidget()
        onboard = QWidget()
        ol = QVBoxLayout(onboard)
        ol.setContentsMargins(0, 4, 0, 0)
        msg = QLabel("Want to try a PR?\nYou need a SolidCraft source folder (a git clone) first.")
        msg.setObjectName("muted")
        msg.setWordWrap(True)
        ol.addWidget(msg)
        ob = QHBoxLayout()
        ob.addWidget(button("Use an existing clone", "accent", self.pick_source, "Select a folder with a clone of tangency-labs/solidcraft"))
        ob.addWidget(button("Clone && build SolidCraft…", "warning", self.build_from_source, "Clone SolidCraft and build it with cargo"))
        ol.addLayout(ob)
        self.pr_stack.addWidget(onboard)

        ready = QWidget()
        rl = QVBoxLayout(ready)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        src = QHBoxLayout()
        src.addWidget(QLabel("Source:"))
        self.src_edit = QLineEdit(self.config.get("src_folder", ""))
        self.src_edit.editingFinished.connect(self._src_edited)
        src.addWidget(self.src_edit, 1)
        src.addWidget(button("Browse...", None, self.pick_source))
        rl.addLayout(src)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("PR #:"))
        self.combo_pr = QComboBox()
        self.combo_pr.setEditable(True)
        self.combo_pr.addItems([str(x) for x in self.config.get("pr_history", [])])
        self.combo_pr.setCurrentText(str(self.config.get("last_pr", "")))
        self.combo_pr.setFixedWidth(110)
        self.combo_pr.currentTextChanged.connect(self.on_pr_changed)
        prow.addWidget(self.combo_pr)
        prow.addWidget(button("GitHub", None, self.open_pr_on_github))
        prow.addWidget(button("Conversation", None, self.open_pr_conversation))
        prow.addStretch()
        rl.addLayout(prow)
        brow = QHBoxLayout()
        self.btn_build = button("Build", "warning", self.build_pr)
        self.btn_stop = button("Stop", "danger", self.stop_build)
        self.btn_stop.setEnabled(False)
        brow.addWidget(self.btn_build, 1)
        brow.addWidget(self.btn_stop, 1)
        brow.addWidget(button("Launch", "success", self.launch_pr), 1)
        rl.addLayout(brow)
        self.pr_progress = QProgressBar()
        self.pr_progress.setRange(0, 100)
        self.pr_progress.setFixedHeight(14)
        self.pr_progress.setTextVisible(False)
        self.pr_progress.hide()
        rl.addWidget(self.pr_progress)
        xrow = QHBoxLayout()
        xrow.addWidget(button("Build SolidCraft main…", "warning", self.build_from_source, "Update the source folder to main and build it"))
        xrow.addWidget(button("Free build space", None, self.free_build_space, "Delete the source folder's cargo build files (target/)"))
        xrow.addStretch()
        rl.addLayout(xrow)
        self.lbl_pr_info = ElidedLabel("Title: -\nAuthor: -")
        self.lbl_pr_info.setObjectName("muted")
        rl.addWidget(self.lbl_pr_info)
        self.lbl_pr_status = ElidedLabel("Ready.")
        self.lbl_pr_status.setObjectName("muted")
        rl.addWidget(self.lbl_pr_status)
        self.pr_stack.addWidget(ready)
        l2.addWidget(self.pr_stack)
        pl.addWidget(b2)
        content.addWidget(pr_panel, 1)

        # Right: project library
        right = card()
        rr = QVBoxLayout(right)
        rr.setContentsMargins(12, 12, 12, 12)
        rr.setSpacing(8)
        rr.addWidget(section_title("PROJECT LIBRARY"))
        self.thumb = QLabel("No project selected")
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setFixedHeight(170)
        self.thumb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.thumb.setStyleSheet(f"background: {theme(self.dark)['input']}; border-radius: 6px;")
        rr.addWidget(self.thumb)
        vrow = QHBoxLayout()
        vrow.addStretch()
        self.btn_3d = button("3D view", "accent", self.open_3d_view, "Open the selected file in F3D (designs are exported to STL first)")
        self.btn_3d.setEnabled(False)
        vrow.addWidget(self.btn_3d)
        rr.addLayout(vrow)
        self.lbl_file = ElidedLabel("No project selected")
        self.lbl_file.setObjectName("muted")
        rr.addWidget(self.lbl_file)
        self.project_list = QListWidget()
        self.project_list.itemSelectionChanged.connect(self.on_project_select)
        self.project_list.itemDoubleClicked.connect(lambda _i: self.open_3d_view())
        rr.addWidget(self.project_list, 1)
        srow = QHBoxLayout()
        srow.addWidget(button("Scan Folder...", None, self.change_scan_folder))
        srow.addWidget(button("Refresh", None, self.refresh_projects))
        rr.addLayout(srow)
        rr.addWidget(button("LAUNCH PROJECT", "success", self.launch_project))
        rr.addWidget(button("OPEN IN RUNNING INSTANCE", "accent", self.open_in_running))
        rr.addWidget(button("LAUNCH PROJECT WITH PR", "warning", self.launch_project_with_pr))
        content.addWidget(right, 1)

        # Footer
        self.lbl_status = ElidedLabel("Checking GitHub for releases…")
        self.lbl_status.setObjectName("muted")
        outer.addWidget(self.lbl_status)
        foot = QHBoxLayout()
        self.lbl_footer = QLabel("")
        self.lbl_footer.setObjectName("footer")
        foot.addWidget(self.lbl_footer)
        foot.addStretch()
        credit = QLabel(f'<a href="https://github.com/deltahedra3d/freecad-launcher" style="color:{theme(self.dark)["muted"]};">'
                        "based on FreeCAD Smart Launcher by Deltahedra</a>")
        credit.setOpenExternalLinks(True)
        credit.setStyleSheet("font-size: 10.5px;")
        foot.addWidget(credit)
        foot.addWidget(button("Infos", None, lambda: InfoDialog(self, self.dark, self.install_dir).exec()))
        foot.addWidget(button("Statistics", None, self.show_stats))
        foot.addWidget(button("Close", None, self.close))
        outer.addLayout(foot)

        for layout, text in ((self.pr_recent_layout, "Loading recent PRs…"),
                             (self.pr_search_layout, "Type a keyword or PR number\nand press Enter or Search.")):
            ph = QLabel(text)
            ph.setObjectName("muted")
            ph.setAlignment(Qt.AlignCenter)
            layout.addWidget(ph)
        self.render_favorites()

    def _scroll_tab(self, name):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("scrollInner")
        layout = QVBoxLayout(inner)
        layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(inner)
        self.pr_tabs.addTab(scroll, name)
        return layout

    def connect_signals(self):
        s = self.sig
        s.releases.connect(self.on_releases)
        s.releases_error.connect(lambda e: self.lbl_status.setText(f"Could not reach GitHub: {e}"))
        s.milestones.connect(self.render_milestones)
        s.download_status.connect(self.on_download_status)
        s.download_done.connect(self.on_download_done)
        s.refresh_versions.connect(self.refresh_local_versions)
        s.footer.connect(self.update_footer)
        s.status.connect(self.lbl_status.setText)
        s.error.connect(lambda title, msg: CopyableErrorDialog(self, title, msg, self.dark).exec())
        s.prs_recent.connect(lambda prs: self.render_prs(self.pr_recent_layout, prs, "No open pull requests."))
        s.prs_recent_error.connect(lambda e: self.render_error(self.pr_recent_layout, e))
        s.prs_search.connect(lambda prs: self.render_prs(self.pr_search_layout, prs, "No matching pull requests."))
        s.prs_search_error.connect(lambda e: self.render_error(self.pr_search_layout, e))
        s.pr_meta.connect(self.on_pr_meta)
        s.pr_build_status.connect(self.on_build_status)
        s.pr_build_done.connect(self.on_build_done)
        s.preview.connect(self.on_preview)

    def set_option(self, key, value):
        self.config[key] = value
        self.save_config()

    def toggle_dark(self, value):
        self.dark = value
        self.set_option("dark_mode", value)
        self.setStyleSheet(make_qss(value))
        self.thumb.setStyleSheet(f"background: {theme(value)['input']}; border-radius: 6px;")
        self.render_favorites()
        self.render_milestones(getattr(self, "_milestones", []))
        threading.Thread(target=self.fetch_recent_prs, daemon=True).start()

    # ----- versions -----

    def refresh_local_versions(self):
        found = []
        try:
            names = os.listdir(self.install_dir)
        except OSError:
            names = []
        for name in names:
            m = APPIMAGE_RE.match(name)
            if not m:
                continue
            ver = m.group("ver")
            kind = version_kind(ver)
            found.append({"name": name, "path": os.path.join(self.install_dir, name), "ver": ver,
                          "kind": "stable" if kind == "rc" else kind, "display": display_name(ver), "managed": True})
        others = []
        if os.path.exists("/usr/bin/solidcraft"):
            ver = "system"
            try:
                out = subprocess.run(["rpm", "-q", "--qf", "%{VERSION}", "solidcraft"], capture_output=True, text=True, timeout=5)
                if out.returncode == 0:
                    ver = out.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
            others.append({"name": "system", "path": "/usr/bin/solidcraft", "ver": ver, "kind": "other",
                           "display": f"Installed system-wide ({ver})", "managed": False})
        for b in self.config.get("builds", []):
            if os.path.exists(b.get("path", "")):
                others.append({"name": b["id"], "path": b["path"], "ver": b["id"], "kind": "other",
                               "display": b.get("display", b["id"]), "managed": True, "build": True})
        for extra in self.config.get("extra_versions", [{"display": "Fork (~/solidcraft-fork)", "path": os.path.join(HOME, "solidcraft-fork", "bin", "solidcraft")}]):
            if os.path.exists(extra["path"]):
                others.append({"name": extra["display"], "path": extra["path"], "ver": extra["display"], "kind": "other",
                               "display": extra["display"], "managed": False})
        self.local = found + others
        for kind in ("stable", "nightly", "other"):
            combo = self.version_combos[kind]
            combo.blockSignals(True)
            combo.clear()
            items = [v for v in self.local if v["kind"] == kind]
            items.sort(key=lambda v: version_key(v["ver"]) if kind != "other" else (0,), reverse=True)
            for v in items:
                combo.addItem(v["display"], v["path"])
            if not items:
                combo.addItem("Not installed" if kind != "other" else "None", None)
            last = self.config.get(f"last_{kind}")
            idx = combo.findData(last) if last else -1
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)
        self.other_wrap.setVisible(any(v["kind"] == "other" for v in self.local))
        if not any(v["kind"] == self.selected_kind for v in self.local):
            self.selected_kind = next((k for k in ("stable", "nightly", "other") if any(v["kind"] == k for v in self.local)), "stable")
        self.update_version_status()
        self._highlight_selected()

    def select_kind(self, kind):
        self.selected_kind = kind
        path = self.version_combos[kind].currentData()
        self.config["selected_kind"] = kind
        if path:
            self.config[f"last_{kind}"] = path
        self.save_config()
        self._highlight_selected()

    def _highlight_selected(self):
        t = theme(self.dark)
        for kind, combo in self.version_combos.items():
            combo.setStyleSheet(f"QComboBox {{ border: 2px solid {t['accent']}; }}" if kind == self.selected_kind else "")
        sel = self.selected_version()
        self.btn_delete.setEnabled(bool(sel and sel["managed"]))

    def selected_version(self):
        path = self.version_combos[self.selected_kind].currentData()
        return next((v for v in self.local if v["path"] == path), None)

    def update_version_status(self):
        for kind in ("stable", "nightly"):
            lbl = self.version_status[kind]
            latest = self.remote[kind][0] if self.remote[kind] else None
            have = [v for v in self.local if v["kind"] == kind]
            if latest is None:
                lbl.setText("Not installed" if not have else f"{len(have)} installed")
                lbl.setObjectName("muted")
            elif any(v["name"] == latest["filename"] for v in have):
                lbl.setText("Up to date")
                lbl.setObjectName("statusOk")
            else:
                lbl.setText(f"New: {display_name(latest['ver'])}" if have else "Not installed")
                lbl.setObjectName("statusWarn" if have else "muted")
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def fetch_releases(self):
        try:
            rels = github_get(f"{API}/releases?per_page=60")
            remote = {"stable": [], "nightly": []}
            for r in rels:
                if r.get("draft"):
                    continue
                ver = r["tag_name"].lstrip("v")
                kind = version_kind(ver)
                if kind == "rc" or (kind == "stable" and r.get("prerelease")):
                    continue
                fn = f"solidcraft-{ver}-linux-x86_64.AppImage"
                asset = next((a for a in r.get("assets", []) if a["name"] == fn), None)
                if asset:
                    remote[kind].append({"ver": ver, "kind": kind, "filename": fn, "url": asset["browser_download_url"],
                                         "size": asset["size"], "published": r.get("published_at", "")})
            for kind in remote:
                remote[kind].sort(key=lambda x: version_key(x["ver"]), reverse=True)
                remote[kind] = remote[kind][:12]
            self.sig.releases.emit(remote)
        except Exception as e:  # network, HTTP or JSON errors all end up in the status line
            self.sig.releases_error.emit(str(e))

    def on_releases(self, remote):
        self.remote = remote
        self.remote_by_file = {r["filename"]: r for k in remote for r in remote[k]}
        s = remote["stable"][0]["ver"] if remote["stable"] else "-"
        n = display_name(remote["nightly"][0]["ver"]).replace("Nightly ", "") if remote["nightly"] else "-"
        self.lbl_status.setText(f"Latest on GitHub: stable {s}, nightly {n}.")
        self.update_version_status()
        self.maybe_prompt_updates()

    def available_updates(self):
        ups = []
        for kind in ("stable", "nightly"):
            if self.remote[kind]:
                latest = self.remote[kind][0]
                if not any(v["name"] == latest["filename"] for v in self.local):
                    ups.append(latest)
        return ups

    def maybe_prompt_updates(self):
        if self._update_prompted:
            return
        self._update_prompted = True
        welcome = not any(v["managed"] and v["kind"] in ("stable", "nightly") for v in self.local)
        if self.config.get("disable_update_reminder") and not welcome:
            return
        ups = self.available_updates()
        if not welcome:
            # Only offer channels the user already uses.
            ups = [u for u in ups if any(v["kind"] == u["kind"] for v in self.local)]
        if not ups:
            return
        dlg = UpdateDialog(self, ups, self.dark, welcome)
        if dlg.exec() and dlg.choice:
            chosen = [u for u in ups if dlg.choice in ("both", u["kind"])]
            self._queue = [u["filename"] for u in chosen]
            self._next_queued()

    def _next_queued(self):
        if getattr(self, "_queue", None) and not self.downloading:
            self.download_version(self._queue.pop(0))

    def open_download_dialog(self):
        if not self.remote["stable"] and not self.remote["nightly"]:
            QMessageBox.information(self, "Download", "The release list hasn't loaded from GitHub yet. Try again in a moment.")
            return
        DownloadDialog(self).exec()

    def download_version(self, filename):
        rel = self.remote_by_file.get(filename)
        if not rel:
            return
        target = os.path.join(self.install_dir, filename)
        if os.path.exists(target):
            QMessageBox.information(self, "Download", "This version is already installed.")
            return
        if self.downloading:
            QMessageBox.information(self, "Download", "Another download is still running.")
            return
        self.downloading = filename
        self._cancel_download = False
        threading.Thread(target=self._download, args=(rel, target), daemon=True).start()

    def _download(self, rel, target):
        tmp = target + ".part"
        name = display_name(rel["ver"])
        try:
            req = urllib.request.Request(rel["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60, context=_SSL) as resp, open(tmp, "wb") as out:
                total = int(resp.headers.get("Content-Length") or rel["size"] or 0)
                done, last = 0, -1
                while True:
                    if self._cancel_download:
                        raise BuildCancelled()
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    pct = int(done * 100 / total) if total else -1
                    if pct != last:
                        last = pct
                        self.sig.download_status.emit(f"Downloading {name}… {pct}% ({human_size(done)})", pct, True)
            if rel["size"] and os.path.getsize(tmp) != rel["size"]:
                raise OSError(f"Incomplete download: {os.path.getsize(tmp)} of {rel['size']} bytes")
            os.chmod(tmp, 0o755)
            os.replace(tmp, target)
            self.sig.download_status.emit(f"Download complete: {name}.", 100, False)
            self.sig.download_done.emit(rel["filename"])
        except BuildCancelled:
            self.sig.download_status.emit("Download cancelled.", 0, False)
        except Exception as e:  # network/disk errors: report and clean up
            self.sig.download_status.emit("Download error.", 0, False)
            self.sig.error.emit("Download failed", f"{rel['url']}\n\n{e}")
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self.downloading = None

    def cancel_download(self):
        self._cancel_download = True

    def on_download_status(self, text, _pct, _busy):
        self.lbl_status.setText(text)

    def on_download_done(self, filename):
        rel = self.remote_by_file.get(filename, {})
        kind = rel.get("kind", "stable")
        self.config[f"last_{kind}"] = os.path.join(self.install_dir, filename)
        self.selected_kind = kind
        self.save_config()
        self.refresh_local_versions()
        self.migrate_desktop_entry(kind, os.path.join(self.install_dir, filename), display_name(rel.get("ver", "")))
        QTimer.singleShot(200, self._next_queued)

    def delete_local(self):
        v = self.selected_version()
        if not v or not v["managed"]:
            return
        if QMessageBox.question(self, "Delete", f"Delete {v['display']}?\n\n{v['path']}") != QMessageBox.Yes:
            return
        try:
            os.remove(v["path"])
            entry = self.desktop_entries().pop(v["path"], None)
            if entry and os.path.exists(entry):
                os.remove(entry)
            if v.get("build"):
                self.config["builds"] = [b for b in self.config.get("builds", []) if b.get("path") != v["path"]]
            shutil.rmtree(os.path.join(CACHE_DIR, "cli", v["ver"]), ignore_errors=True)
            self.save_config()
            self.refresh_local_versions()
        except OSError as e:
            QMessageBox.critical(self, "Delete", str(e))

    # ----- milestones -----

    def fetch_milestones(self):
        try:
            ms = github_get(f"{API}/milestones?state=open&per_page=30")
            self.sig.milestones.emit([m for m in ms if m.get("open_issues", 0) + m.get("closed_issues", 0) > 0])
        except Exception:  # optional decoration: say nothing on failure
            self.sig.milestones.emit([])

    def render_milestones(self, ms):
        self._milestones = ms
        clear_layout(self.milestone_box)
        if not ms:
            lbl = QLabel("No milestones loaded.")
            lbl.setObjectName("muted")
            self.milestone_box.addWidget(lbl)
            return
        ms.sort(key=lambda m: (m.get("due_on") or "9999", -m.get("open_issues", 0)))
        for m in ms[:3]:
            closed, opened = m.get("closed_issues", 0), m.get("open_issues", 0)
            pct = int(closed * 100 / max(1, closed + opened))
            lbl = QLabel(f'<a href="{m["html_url"]}" style="color:{theme(self.dark)["text"]}; text-decoration:none;">{m["title"]}</a>'
                         f'  <span style="color:{theme(self.dark)["muted"]};">{closed}/{closed + opened} done</span>')
            lbl.setOpenExternalLinks(True)
            self.milestone_box.addWidget(lbl)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setFixedHeight(14)
            bar.setFormat("%p%")
            self.milestone_box.addWidget(bar)

    # ----- launching -----

    def launch(self, path, name, project=None):
        """Start SolidCraft with a private control port; time the session."""
        port = free_port()
        token = os.urandom(24).hex()
        env = dict(os.environ, **LAUNCH_ENV, SOLIDCRAFT_CONTROL_TOKEN=token)
        cmd = [path, "--control", str(port)] + ([project] if project else [])
        try:
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            QMessageBox.critical(self, "Launch", f"Could not start {name}:\n{e}")
            return
        inst = {"proc": proc, "port": port, "token": token, "name": name, "start": time.time()}
        self.instances.append(inst)
        self.lbl_status.setText(f"Started {name}" + (f" with {os.path.basename(project)}" if project else "") + ".")
        threading.Thread(target=self._watch, args=(inst, path, project, env), daemon=True).start()
        if self.config.get("autoclose"):
            self.hide()

    def _watch(self, inst, path, project, env):
        proc = inst["proc"]
        fuse = False
        start = time.time()
        try:
            for line in iter(proc.stderr.readline, b""):
                if time.time() - start < 3 and (b"fuse" in line.lower() or b"dlopen" in line.lower()):
                    fuse = True
        except (OSError, ValueError):
            pass
        proc.wait()
        if proc.returncode != 0 and fuse and path.endswith(".AppImage"):
            # No FUSE: run the AppImage extracted instead (not timed, as in FreeCAD Launcher).
            subprocess.Popen([path, "--appimage-extract-and-run"] + ([project] if project else []), env=env, start_new_session=True).wait()
            return
        if proc.returncode not in (0, None) and time.time() - start < 3:
            self.sig.error.emit("Launch failed", f"{inst['name']} exited with code {proc.returncode} right after starting.\n\n{path}")
        self.record_session(inst["name"], inst["start"])
        inst["done"] = True

    def record_session(self, name, start):
        duration = int(time.time() - start)
        if duration <= SESSION_MIN_SEC:
            return
        e = self.stats.setdefault(name, {"time_sec": 0, "launches": 0})
        e["time_sec"] += duration
        e["launches"] += 1
        self.save_stats()
        self.sig.footer.emit()

    def _reap_instances(self):
        self.instances = [i for i in self.instances if not i.get("done")]
        if not self.isVisible() and not self.instances:
            QApplication.quit()

    def launch_selected(self):
        v = self.selected_version()
        if not v:
            QMessageBox.warning(self, "Launch", "Download or select a SolidCraft version first.")
            return
        self.launch(v["path"], v["display"])

    def update_footer(self):
        total = sum(d.get("time_sec", 0) for d in self.stats.values())
        launches = sum(d.get("launches", 0) for d in self.stats.values())
        self.lbl_footer.setText(f"Time in SolidCraft: {fmt_duration(total)} over {launches} sessions")

    def show_stats(self):
        def reset():
            self.stats = {}
            self.save_stats()
            self.update_footer()
        StatsDialog(self, self.stats, self.dark, reset).exec()

    # ----- desktop entries -----

    def desktop_entries(self):
        return self.config.setdefault("desktop_entries", {})

    def solidcraft_icon(self, appimage):
        for base in ("/usr/share/icons/hicolor", os.path.join(HOME, ".local", "share", "icons", "hicolor")):
            for size in ("256x256", "128x128", "scalable"):
                d = os.path.join(base, size, "apps")
                if any(f.startswith("ai.storyteller.solidcraft.") for f in (os.listdir(d) if os.path.isdir(d) else [])):
                    return "ai.storyteller.solidcraft"
        icon = os.path.join(CACHE_DIR, "solidcraft.png")
        if not os.path.exists(icon) and appimage.endswith(".AppImage"):
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run([appimage, "--appimage-extract", "ai.storyteller.solidcraft.png"], cwd=tmp,
                               capture_output=True, timeout=60)
                src = os.path.join(tmp, "squashfs-root", "ai.storyteller.solidcraft.png")
                if os.path.exists(src):
                    os.makedirs(CACHE_DIR, exist_ok=True)
                    shutil.copy(src, icon)
        return icon if os.path.exists(icon) else "applications-engineering"

    def write_desktop_entry(self, path, name, appimage):
        os.makedirs(APPS_DIR, exist_ok=True)
        slug = re.sub(r"[^a-z0-9.]+", "-", name.lower()).strip("-")
        desk = os.path.join(APPS_DIR, f"solidcraft-launcher-{slug}.desktop")
        with open(desk, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\n"
                    f"Name=SolidCraft {name}\nGenericName=3D CAD\nComment=Started from SolidCraft Launcher\n"
                    f'Exec=env DESKTOPINTEGRATION=solidcraft-launcher "{appimage}" %f\n'
                    f"TryExec={appimage}\nIcon={self.solidcraft_icon(appimage)}\nTerminal=false\n"
                    "StartupNotify=true\nStartupWMClass=ai.storyteller.solidcraft\n"
                    "Categories=Graphics;3DGraphics;Engineering;\n")
        self.desktop_entries()[path] = desk
        self.save_config()
        return desk

    def create_desktop_entry(self):
        v = self.selected_version()
        if not v:
            QMessageBox.warning(self, "Desktop entry", "Select a SolidCraft version first.")
            return
        existing = self.desktop_entries().get(v["path"])
        if existing and os.path.exists(existing) and QMessageBox.question(
                self, "Desktop entry", f"A menu entry for SolidCraft {v['display']} already exists.\nOverwrite it?") != QMessageBox.Yes:
            return
        try:
            desk = self.write_desktop_entry(v["path"], v["display"], v["path"])
            QMessageBox.information(self, "Desktop entry", f"Menu entry created:\nSolidCraft {v['display']}\n\n{desk}")
        except OSError as e:
            QMessageBox.critical(self, "Desktop entry", str(e))

    def migrate_desktop_entry(self, kind, new_path, new_name):
        """After an update, move the channel's menu entry from the previous version to the new one."""
        entries = self.desktop_entries()
        old = [p for p in list(entries) if p != new_path and any(v["path"] == p and v["kind"] == kind for v in self.local)]
        for p in old:
            desk = entries.pop(p)
            if os.path.exists(desk):
                os.remove(desk)
        if old:
            self.write_desktop_entry(new_path, new_name, new_path)

    # ----- project library -----

    def scan_dir(self):
        return self.config.get("scan_folder", DEFAULT_SCAN_DIR)

    def change_scan_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Folder with your designs", self.scan_dir())
        if folder:
            self.set_option("scan_folder", folder)
            self.refresh_projects()

    def solidcraft_recent(self):
        """SolidCraft's own recent-files list (from its preferences)."""
        try:
            with open(SOLIDCRAFT_PREFS, encoding="utf-8") as f:
                text = f.read()
            m = re.search(r'"recent\\?":\s*\[(.*?)\]', text.replace('\\"', '"'))
            return [p for p in re.findall(r'"([^"]+)"', m.group(1)) if os.path.exists(p)] if m else []
        except OSError:
            return []

    def refresh_projects(self):
        root = self.scan_dir()
        found = {}
        for p in self.solidcraft_recent():
            found[p] = os.path.getmtime(p)
        count = 0
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if os.path.splitext(f)[1].lower() in PROJECT_EXTENSIONS and not f.endswith(".bak"):
                    p = os.path.join(dirpath, f)
                    try:
                        found[p] = os.path.getmtime(p)
                    except OSError:
                        pass
                    count += 1
            if count > 2000:
                break
        self.project_list.clear()
        self.projects = {}
        names = {}
        for p, _m in sorted(found.items(), key=lambda kv: -kv[1]):
            label = os.path.basename(p)
            names[label] = names.get(label, 0) + 1
            if names[label] > 1:
                label = f"{label}  ({os.path.basename(os.path.dirname(p))})"
            item = QListWidgetItem(label)
            item.setToolTip(p)
            item.setData(Qt.UserRole, p)
            self.project_list.addItem(item)
            self.projects[label] = p
        self.lbl_file.setText(f"{len(self.projects)} files in {root} and SolidCraft's recent files")

    def selected_project(self):
        items = self.project_list.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def on_project_select(self):
        p = self.selected_project()
        if not p:
            return
        ext = os.path.splitext(p)[1].lower()
        try:
            st = os.stat(p)
            self.lbl_file.setText(f"{p}\n{human_size(st.st_size)} · modified {time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}")
        except OSError:
            self.lbl_file.setText(p)
        self.btn_3d.setEnabled(ext in PROJECT_EXTENSIONS)
        self._preview_for = p
        self.thumb.setPixmap(QPixmap())
        v = self.selected_version()
        first = bool(v and v["path"].endswith(".AppImage") and not os.path.exists(os.path.join(CACHE_DIR, "cli", v["ver"])))
        self.thumb.setText("Unpacking the preview tool from the AppImage (first time only)…" if first else "Rendering preview…")
        threading.Thread(target=self._render_preview, args=(p, self.selected_version()), daemon=True).start()

    def cli_for(self, version):
        """A solidcraft-cli matching `version` where possible: the AppImage's own, else any we have."""
        if version and version["path"].endswith(".AppImage"):
            dest = os.path.join(CACHE_DIR, "cli", version["ver"])
            cli = os.path.join(dest, "squashfs-root", "usr", "bin", "solidcraft-cli")
            if not os.path.exists(cli):
                os.makedirs(dest, exist_ok=True)
                subprocess.run([version["path"], "--appimage-extract", "usr/bin/solidcraft-cli"], cwd=dest,
                               capture_output=True, timeout=120)
            if os.path.exists(cli):
                return cli
        if version:
            sibling = os.path.join(os.path.dirname(version["path"]), "solidcraft-cli")
            if os.path.exists(sibling):
                return sibling
        for v in sorted((v for v in self.local if v["path"].endswith(".AppImage")), key=lambda v: version_key(v["ver"]), reverse=True):
            if version is None or v["path"] != version["path"]:
                return self.cli_for(v)
        return shutil.which("solidcraft-cli")

    def _render_preview(self, path, version):
        out = os.path.join(CACHE_DIR, "thumbs")
        os.makedirs(out, exist_ok=True)
        try:
            st = os.stat(path)
        except OSError:
            self.sig.preview.emit(path, "", "File not found")
            return
        key = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
        png = os.path.join(out, f"{key}.png")
        if not os.path.exists(png):
            cli = self.cli_for(version)
            if not cli:
                self.sig.preview.emit(path, "", "No solidcraft-cli available for previews.\nDownload a SolidCraft version first.")
                return
            try:
                res = subprocess.run([cli, "snapshot", "--in", path, "--out", png, "--width", "640", "--height", "340",
                                      "--view", "iso", "--no-grid", "--no-axes"], capture_output=True, text=True, timeout=120)
                if res.returncode != 0 or not os.path.exists(png):
                    self.sig.preview.emit(path, "", "No preview: " + (res.stderr.strip().splitlines() or ["unknown error"])[-1][:120])
                    return
            except subprocess.TimeoutExpired:
                self.sig.preview.emit(path, "", "Preview timed out (large file).")
                return
        self.sig.preview.emit(path, png, "")

    def on_preview(self, path, png, message):
        if path != self._preview_for:
            return
        if png:
            pix = QPixmap(png)
            self.thumb.setPixmap(pix.scaled(self.thumb.width(), self.thumb.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.thumb.setText(message)

    def open_3d_view(self):
        p = self.selected_project()
        if not p:
            return
        f3d = shutil.which("f3d")
        if not f3d:
            QMessageBox.information(self, "3D view", "The 3D view uses F3D, which isn't installed.\n\nInstall it with:\n  sudo dnf install f3d")
            return
        ext = os.path.splitext(p)[1].lower()
        if ext in F3D_DIRECT:
            subprocess.Popen([f3d, p], start_new_session=True)
            return
        self.lbl_status.setText(f"Exporting {os.path.basename(p)} to STL for the 3D view…")
        version = self.selected_version()

        def export():
            cli = self.cli_for(version)
            stl = os.path.join(CACHE_DIR, "view.stl")
            try:
                res = subprocess.run([cli, "run", "--in", p, "--out", stl, "--quiet"], capture_output=True, text=True, timeout=300)
                if res.returncode == 0 and os.path.exists(stl):
                    subprocess.Popen([f3d, stl], start_new_session=True)
                    self.sig.status.emit("Opened in F3D.")
                else:
                    self.sig.error.emit("3D view", res.stderr or "Export failed.")
            except (OSError, subprocess.SubprocessError, TypeError) as e:
                self.sig.error.emit("3D view", str(e))

        threading.Thread(target=export, daemon=True).start()

    def launch_project(self):
        v = self.selected_version()
        p = self.selected_project()
        if not v or not p:
            QMessageBox.warning(self, "Launch project", "Select a version on the left and a project on the right.")
            return
        self.launch(v["path"], v["display"], p)

    def open_in_running(self):
        p = self.selected_project()
        if not p:
            QMessageBox.warning(self, "Open", "Select a project on the right.")
            return
        alive = [i for i in self.instances if i["proc"].poll() is None]
        if not alive:
            QMessageBox.information(self, "Open in running instance",
                                    "No SolidCraft started from this launcher is running.\n"
                                    "(Only those can be reached; use LAUNCH PROJECT instead.)")
            return
        inst = alive[-1]
        self.lbl_status.setText(f"Opening {os.path.basename(p)} in {inst['name']}…")

        def send():
            try:
                # Opening can take a while: a design saved by another version rebuilds its features.
                with socket.create_connection(("127.0.0.1", inst["port"]), timeout=600) as s:
                    f = s.makefile("rw")
                    for i, req in enumerate(({"method": "ui.documents", "params": {"action": "new"}},
                                             {"method": "engine.execute", "params": {"command": "doc.open", "params": {"path": p}}}), 1):
                        req["id"] = i
                        if i == 1:
                            req["token"] = inst["token"]
                        f.write(json.dumps(req) + "\n")
                        f.flush()
                        reply = json.loads(f.readline() or "{}")
                        if not reply.get("ok"):
                            if "still running" in str(reply.get("error", "")):
                                self.sig.status.emit(f"{inst['name']} is still opening {os.path.basename(p)} (large or older designs rebuild first)…")
                                return
                            raise RuntimeError(reply.get("error", "no answer"))
                self.sig.status.emit(f"Opened {os.path.basename(p)} in {inst['name']}.")
            except (OSError, ValueError, RuntimeError) as e:
                self.sig.error.emit("Open in running instance", f"{inst['name']} didn't take the file:\n{e}")

        threading.Thread(target=send, daemon=True).start()

    def launch_project_with_pr(self):
        p = self.selected_project()
        b = self.current_build()
        if not p:
            QMessageBox.warning(self, "Launch project", "Select a project on the right.")
            return
        if not b:
            QMessageBox.warning(self, "Launch project", "No build for this PR yet. Build it first.")
            return
        self.launch(b["path"], b["display"], p)

    # ----- pull requests -----

    def fetch_recent_prs(self):
        try:
            self.sig.prs_recent.emit(github_get(f"{API}/pulls?state=open&sort=created&direction=desc&per_page=30"))
        except Exception as e:  # shown in the list
            self.sig.prs_recent_error.emit(str(e))

    def search_prs(self):
        q = self.pr_search_edit.text().strip()
        if not q:
            return
        clear_layout(self.pr_search_layout)
        ph = QLabel("Searching…")
        ph.setObjectName("muted")
        self.pr_search_layout.addWidget(ph)

        def run():
            try:
                if q.lstrip("#").isdigit():
                    self.sig.prs_search.emit([github_get(f"{API}/pulls/{q.lstrip('#')}")])
                    return
                author = q[1:] if q.startswith("@") else None
                query = f"repo:{REPO} is:pr " + (f"author:{author}" if author else q)
                res = github_get(f"https://api.github.com/search/issues?q={urllib.parse.quote(query)}&per_page=30")
                self.sig.prs_search.emit([dict(i, merged_at=(i.get("pull_request") or {}).get("merged_at")) for i in res.get("items", [])])
            except Exception as e:  # shown in the list
                self.sig.prs_search_error.emit(str(e))

        threading.Thread(target=run, daemon=True).start()

    def pr_status(self, pr):
        t = theme(self.dark)
        if pr.get("merged_at"):
            return "merged", "#A78BFA"
        if pr.get("state") == "closed":
            return "closed", t["danger"]
        if pr.get("draft"):
            return "draft", t["muted"]
        return "open", t["success"]

    def render_error(self, layout, err):
        clear_layout(layout)
        lbl = QLabel(f"Could not load pull requests:\n{err}")
        lbl.setObjectName("muted")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

    def render_prs(self, layout, prs, empty):
        clear_layout(layout)
        if not prs:
            lbl = QLabel(empty)
            lbl.setObjectName("muted")
            layout.addWidget(lbl)
        for pr in prs:
            layout.addWidget(self.pr_row(pr))

    def render_favorites(self):
        self.render_prs(self.pr_fav_layout, self.pr_favorites, "No favorites yet. Star a PR with ☆.")

    def pr_row(self, pr):
        t = theme(self.dark)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 4)
        lay.setSpacing(3)
        top = QHBoxLayout()
        num = pr.get("number")
        lbl = QLabel(f"#{num}")
        lbl.setStyleSheet(f"color: {t['accent']}; font-weight: 700;")
        top.addWidget(lbl)
        date = QLabel((pr.get("created_at") or "")[:10])
        date.setStyleSheet("font-size: 11px; font-weight: 600;")
        top.addWidget(date)
        top.addStretch()
        st, color = self.pr_status(pr)
        sl = QLabel(st)
        sl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")
        top.addWidget(sl)
        lay.addLayout(top)
        title = QLabel(pr.get("title", ""))
        title.setWordWrap(True)
        lay.addWidget(title)
        bottom = QHBoxLayout()
        author = QLabel(f"@{(pr.get('user') or {}).get('login', '?')}")
        author.setStyleSheet(f"color: {t['muted']}; font-size: 11px;")
        bottom.addWidget(author)
        bottom.addStretch()
        fav = any(f.get("number") == num for f in self.pr_favorites)
        icon_css = (f"QPushButton {{ background: {t['neutral']}; border: 1px solid {t['border']}; border-radius: 4px; "
                    f"font-size: 13px; font-weight: 700; padding: 0; color: %s; }} QPushButton:hover {{ background: {t['hover']}; }}")
        star = QPushButton("★" if fav else "☆")
        star.setFixedSize(26, 22)
        star.setToolTip("Add/remove favorite")
        star.setStyleSheet(icon_css % ("#FFD700" if fav else t["muted"]))
        star.clicked.connect(lambda: self.toggle_favorite(pr))
        bottom.addWidget(star)
        talk = QPushButton("💬")
        talk.setFixedSize(26, 22)
        talk.setToolTip("Read the conversation")
        talk.setStyleSheet(icon_css % t["link"])
        talk.clicked.connect(lambda: PRConversationDialog(self, pr, self.dark).exec())
        bottom.addWidget(talk)
        web = QPushButton("↗")
        web.setFixedSize(26, 22)
        web.setToolTip("Open on GitHub")
        web.setStyleSheet(icon_css % t["link"])
        web.clicked.connect(lambda: webbrowser.open(f"{REPO_URL}/pull/{num}"))
        bottom.addWidget(web)
        test = button("Test", "warning", lambda: self.use_pr(pr))
        test.setFixedHeight(22)
        bottom.addWidget(test)
        lay.addLayout(bottom)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {t['border']}; border: none; max-height: 1px;")
        lay.addWidget(sep)
        return w

    def toggle_favorite(self, pr):
        num = pr.get("number")
        if any(f.get("number") == num for f in self.pr_favorites):
            self.pr_favorites = [f for f in self.pr_favorites if f.get("number") != num]
        else:
            keep = ("number", "title", "user", "created_at", "state", "draft", "merged_at", "html_url")
            self.pr_favorites.insert(0, {k: pr.get(k) for k in keep})
        self.set_option("pr_favorites", self.pr_favorites)
        self.render_favorites()
        threading.Thread(target=self.fetch_recent_prs, daemon=True).start()

    def use_pr(self, pr):
        self.current_pr = pr
        self.combo_pr.setCurrentText(str(pr.get("number")))
        if not self.source_dir():
            QMessageBox.information(self, "Test a PR", "Set up a SolidCraft source folder first (Use an existing clone, or Clone & build).")

    def pr_number(self):
        m = re.search(r"\d+", self.combo_pr.currentText())
        return int(m.group()) if m else None

    def on_pr_changed(self, _text):
        num = self.pr_number()
        if not num:
            self.lbl_pr_info.setText("Title: -\nAuthor: -")
            return
        if self.current_pr and self.current_pr.get("number") == num:
            self.on_pr_meta(self.current_pr)
            return

        def fetch():
            try:
                self.sig.pr_meta.emit(github_get(f"{API}/pulls/{num}"))
            except Exception:  # unknown PR: the info line says so
                self.sig.pr_meta.emit({"number": num, "title": "(not found)", "user": {"login": "-"}})

        threading.Thread(target=fetch, daemon=True).start()

    def on_pr_meta(self, pr):
        if pr.get("number") != self.pr_number():
            return
        self.current_pr = pr
        built = self.current_build()
        st, _c = self.pr_status(pr) if pr.get("state") else ("?", "")
        self.lbl_pr_info.setText(f"Title: {pr.get('title', '-')}\nAuthor: @{(pr.get('user') or {}).get('login', '-')} · {st}"
                                 + (f" · built {built['built']}" if built else ""))

    def open_pr_on_github(self):
        num = self.pr_number()
        webbrowser.open(f"{REPO_URL}/pull/{num}" if num else f"{REPO_URL}/pulls")

    def open_pr_conversation(self):
        num = self.pr_number()
        if num:
            PRConversationDialog(self, self.current_pr if self.current_pr and self.current_pr.get("number") == num else {"number": num}, self.dark).exec()

    def source_dir(self):
        d = self.config.get("src_folder", "")
        return d if d and os.path.exists(os.path.join(d, "Cargo.toml")) and os.path.isdir(os.path.join(d, ".git")) else ""

    def _update_pr_panel(self):
        index = 1 if self.source_dir() else 0
        self.pr_stack.setCurrentIndex(index)
        # A stack is as tall as its tallest page: let only the shown page count.
        for i in range(self.pr_stack.count()):
            self.pr_stack.widget(i).setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred if i == index else QSizePolicy.Ignored)
        self.pr_stack.adjustSize()
        self.src_edit.setText(self.config.get("src_folder", ""))

    def _src_edited(self):
        self.set_source(self.src_edit.text().strip())

    def set_source(self, folder):
        if folder and not (os.path.exists(os.path.join(folder, "Cargo.toml")) and os.path.exists(os.path.join(folder, "apps", "solidcraft"))):
            QMessageBox.warning(self, "Source folder", f"{folder}\n\ndoesn't look like a SolidCraft clone (no Cargo.toml / apps/solidcraft).")
            return
        self.set_option("src_folder", folder)
        self._update_pr_panel()

    def pick_source(self):
        folder = QFileDialog.getExistingDirectory(self, "SolidCraft source folder (git clone)", self.config.get("src_folder") or HOME)
        if folder:
            self.set_source(folder)

    def current_build(self):
        num = self.pr_number()
        return next((b for b in self.config.get("builds", []) if b.get("id") == f"pr-{num}" and os.path.exists(b["path"])), None)

    # Toolchain

    def find_cargo(self):
        """(cargo path, extra env) for the first Rust toolchain found, or (None, {})."""
        homes = [self.config.get("cargo_home")] if self.config.get("cargo_home") else []
        homes += [os.path.join(self.install_dir, "toolchain", "cargo"), os.path.join(HOME, ".cargo"),
                  os.path.join(HOME, "solidcraft-fork", "cargo")]
        for home in homes:
            cargo = os.path.join(home, "bin", "cargo")
            if os.path.exists(cargo):
                env = {"CARGO_HOME": home}
                rustup = os.path.join(os.path.dirname(home), "rustup")
                if os.path.isdir(rustup):
                    env["RUSTUP_HOME"] = rustup
                env["PATH"] = os.path.join(home, "bin") + os.pathsep + os.environ.get("PATH", "")
                return cargo, env
        cargo = shutil.which("cargo")
        return (cargo, {}) if cargo else (None, {})

    def install_toolchain(self, run, log):
        base = os.path.join(self.install_dir, "toolchain")
        os.makedirs(base, exist_ok=True)
        script = os.path.join(base, "rustup-init.sh")
        log("Downloading rustup installer from https://sh.rustup.rs …\n")
        with open(script, "wb") as f:
            f.write(fetch_bytes("https://sh.rustup.rs", limit=4 * 1024 * 1024))
        env = {"RUSTUP_HOME": os.path.join(base, "rustup"), "CARGO_HOME": os.path.join(base, "cargo")}
        run(["sh", script, "-y", "--no-modify-path", "--profile", "minimal", "--default-toolchain", "stable"], base, env)
        os.remove(script)

    def _run_logged(self, cmd, cwd, env, log, is_cancelled, set_proc, on_line=None):
        full_env = dict(os.environ, **(env or {}))
        log(f"\n$ {' '.join(cmd)}\n")
        proc = subprocess.Popen(cmd, cwd=cwd, env=full_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", start_new_session=True)
        set_proc(proc)
        for line in proc.stdout:
            log(line)
            if on_line:
                on_line(line)
            if is_cancelled():
                kill_group(proc)
        proc.wait()
        set_proc(None)
        if is_cancelled():
            raise BuildCancelled()
        if proc.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd[:3])} failed (exit {proc.returncode})")

    def _build(self, src, ref, build_id, display, log, status, is_cancelled, set_proc, confirm=None):
        """Fetch `ref` (a PR number or "main"), check it out, cargo build, keep the binary."""
        run = lambda cmd, cwd, env=None, on_line=None: self._run_logged(cmd, cwd, env, log, is_cancelled, set_proc, on_line)
        if not shutil.which("git"):
            raise RuntimeError("git is not installed (sudo dnf install git).")
        cargo, cenv = self.find_cargo()
        if not cargo:
            if confirm and not confirm("Rust toolchain", f"No Rust toolchain was found.\n\nInstall one into\n{self.install_dir}/toolchain\n(about 1 GB, no root needed)?"):
                raise BuildCancelled()
            if not confirm:
                raise RuntimeError("No Rust toolchain found. Use \"Build SolidCraft main…\" once to install one.")
            status("Installing the Rust toolchain…", 2)
            self.install_toolchain(run, log)
            cargo, cenv = self.find_cargo()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=src, capture_output=True, text=True).stdout.strip()
        if dirty:
            status("Stashing your local changes (git stash)…", 4)
            run(["git", "stash", "push", "-u", "-m", f"solidcraft-launcher: before {build_id}"], src)
        branch = f"solidcraft-launcher/{build_id}"
        refspec = f"pull/{ref}/head" if ref != "main" else "main"
        status(f"Fetching {refspec} from {REPO}…", 6)
        run(["git", "fetch", "--force", f"{REPO_URL}.git", refspec], src)
        run(["git", "checkout", "--force", "-B", branch, "FETCH_HEAD"], src)
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
        try:
            with open(os.path.join(src, "Cargo.lock"), encoding="utf-8") as f:
                total = max(1, f.read().count("[[package]]"))
        except OSError:
            total = 600
        compiled = [0]

        def progress(line):
            if line.lstrip().startswith(("Compiling", "Checking")):
                compiled[0] += 1
                status(f"Building {display}: {line.strip()[:80]}", 10 + min(85, compiled[0] * 85 // total))

        status(f"Building {display} with cargo (several minutes)…", 10)
        run([cargo, "build", "--release", "-p", "solidcraft"], src, cenv, progress)
        target = os.environ.get("CARGO_TARGET_DIR") or os.path.join(src, "target")
        built = os.path.join(target, "release", "solidcraft")
        dest_dir = os.path.join(self.install_dir, "builds")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, f"solidcraft-{build_id}")
        status("Keeping the built app…", 97)
        if shutil.which("strip"):
            subprocess.run(["strip", "-o", dest, built], check=True)
        else:
            shutil.copy2(built, dest)
        builds = [b for b in self.config.get("builds", []) if b.get("id") != build_id]
        builds.insert(0, {"id": build_id, "path": dest, "display": display, "commit": commit,
                          "built": time.strftime("%Y-%m-%d %H:%M")})
        self.config["builds"] = builds
        self.save_config()
        return dest

    def build_pr(self):
        src = self.source_dir()
        num = self.pr_number()
        if not src or not num:
            QMessageBox.warning(self, "Build", "Set a source folder and a PR number first.")
            return
        cargo, _ = self.find_cargo()
        if not cargo:
            QMessageBox.information(self, "Build", "No Rust toolchain found.\nUse \"Build SolidCraft main…\" once: it can install one for you.")
            return
        history = [str(num)] + [h for h in self.config.get("pr_history", []) if h != str(num)]
        self.config["pr_history"] = history[:15]
        self.config["last_pr"] = num
        self.save_config()
        title = (self.current_pr or {}).get("title", "")
        display = f"PR #{num}" + (f" – {title[:40]}" if title else "")
        self.build_cancel = False
        self.btn_build.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.pr_progress.setValue(0)
        self.pr_progress.show()
        open(BUILD_LOG_FILE, "w").close()

        def log(text):
            with open(BUILD_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(text)

        def work():
            try:
                self._build(src, str(num), f"pr-{num}", display, log, lambda s, p: self.sig.pr_build_status.emit(s, p),
                            lambda: self.build_cancel, lambda p: setattr(self, "build_proc", p))
                self.sig.pr_build_done.emit(True, f"Built {display}. Launch it, or find it under Other builds.")
            except BuildCancelled:
                self.sig.pr_build_done.emit(False, "Build stopped.")
            except Exception as e:  # build failure: point at the log
                self.sig.pr_build_done.emit(False, f"Build failed: {e}  (log: {BUILD_LOG_FILE})")

        threading.Thread(target=work, daemon=True).start()

    def stop_build(self):
        self.build_cancel = True
        kill_group(self.build_proc)
        self.lbl_pr_status.setText("Stopping…")

    def on_build_status(self, text, pct):
        self.lbl_pr_status.setText(text)
        self.pr_progress.setValue(pct)

    def on_build_done(self, ok, message):
        self.btn_build.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.pr_progress.setVisible(ok)
        self.lbl_pr_status.setText(message)
        self.refresh_local_versions()
        if self.current_pr:
            self.on_pr_meta(self.current_pr)
        if not ok and "failed" in message:
            try:
                with open(BUILD_LOG_FILE, encoding="utf-8") as f:
                    tail = f.read()[-6000:]
            except OSError:
                tail = ""
            CopyableErrorDialog(self, "Build failed", tail or message, self.dark).exec()

    def launch_pr(self):
        b = self.current_build()
        if not b:
            QMessageBox.warning(self, "Launch", "No build for this PR yet. Build it first.")
            return
        self.launch(b["path"], b["display"])

    def free_build_space(self):
        src = self.source_dir()
        target = os.path.join(src, "target") if src else ""
        if not target or not os.path.isdir(target):
            QMessageBox.information(self, "Free build space", "There are no build files to delete.")
            return
        size = dir_size(target)
        if QMessageBox.question(self, "Free build space",
                                f"Delete {target}?\n\nThat frees {human_size(size)}. Built apps you kept are not affected;\n"
                                "the next build starts from scratch (slower).") == QMessageBox.Yes:
            shutil.rmtree(target, ignore_errors=True)
            self.lbl_pr_status.setText(f"Freed {human_size(size)}.")

    def build_from_source(self):
        """Guided: pick or clone the source, then build main, confirming each stage."""
        src = self.source_dir()
        dlg = SourceBuildDialog(self, self.dark)
        default_src = os.path.join(self.install_dir, "source", "solidcraft")

        def log(text):
            dlg.sig_log.emit(text)
            with open(BUILD_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(text)

        def status(text, pct):
            dlg.sig_status.emit(text)
            dlg.sig_progress.emit(pct)

        def work():
            nonlocal src
            try:
                open(BUILD_LOG_FILE, "w").close()
                if not src:
                    if os.path.isdir(os.path.join(default_src, ".git")):
                        src = default_src
                    else:
                        if not dlg.confirm("Clone SolidCraft", f"Clone {REPO_URL}\ninto {default_src}?\n\n(about 200 MB)"):
                            raise BuildCancelled()
                        os.makedirs(os.path.dirname(default_src), exist_ok=True)
                        status("Cloning SolidCraft…", 1)
                        self._run_logged(["git", "clone", "--depth", "50", f"{REPO_URL}.git", default_src], os.path.dirname(default_src),
                                         None, log, lambda: dlg.cancelled, lambda p: setattr(dlg, "proc", p))
                        src = default_src
                    self.config["src_folder"] = src
                    self.save_config()
                if not dlg.confirm("Build SolidCraft main",
                                   f"Update {src} to the latest main and build it?\n\nThis takes several minutes and up to ~7 GB of "
                                   "build files (\"Free build space\" deletes them afterwards). Uncommitted changes are stashed."):
                    raise BuildCancelled()
                self._build(src, "main", "main", "main (built from source)", log, status, lambda: dlg.cancelled,
                            lambda p: setattr(dlg, "proc", p), confirm=dlg.confirm)
                dlg.sig_done.emit(True, "Done. \"main (built from source)\" is under Other builds.")
            except BuildCancelled:
                dlg.sig_done.emit(False, "Cancelled.")
            except Exception as e:  # shown in the dialog with the log above it
                dlg.sig_done.emit(False, f"Failed: {e}")

        threading.Thread(target=work, daemon=True).start()
        dlg.exec()
        self._update_pr_panel()
        self.refresh_local_versions()

    def closeEvent(self, e):
        if self.build_proc is not None:
            if QMessageBox.question(self, "Close", "A build is running. Stop it and close?") != QMessageBox.Yes:
                e.ignore()
                return
            self.stop_build()
        if self.instances:
            # SolidCraft is still running: hide, and quit once it exits so its time is still recorded.
            e.ignore()
            self.hide()
            return
        e.accept()
        QApplication.quit()


# ---------- Single instance ----------

def acquire_lock():
    try:
        with open(LOCK_FILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
        if pid != os.getpid() and os.path.exists(f"/proc/{pid}"):
            return False
    except (OSError, ValueError):
        pass
    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_lock():
    try:
        with open(LOCK_FILE, encoding="utf-8") as f:
            if int(f.read().strip()) == os.getpid():
                os.remove(LOCK_FILE)
    except (OSError, ValueError):
        pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("solidcraft-launcher")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    if not acquire_lock():
        QMessageBox.warning(None, APP_NAME, "The launcher is already open in another window.")
        return 0
    try:
        win = Launcher()
        win.show()
        shot = os.environ.get("SCL_SCREENSHOT")  # testing: save a picture of the window, then quit
        if shot:
            QTimer.singleShot(int(os.environ.get("SCL_SCREENSHOT_DELAY", "6000")),
                              lambda: (win.grab().save(shot), QApplication.quit()))
        return app.exec()
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
