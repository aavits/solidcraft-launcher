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

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QImage, QPixmap, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTabBar, QTabWidget,
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
LOCK_FILE = os.path.join(CACHE_DIR, "launcher.lock")
BUILD_LOG_FILE = os.path.join(CACHE_DIR, "build.log")
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


# ---------- Theme: SolidCraft's own palette, fonts and icons ----------
# Colours are SolidCraft's design tokens (assets/styles/ui/palettes.rs and sampled from the app);
# fonts are the IBM Plex faces it bundles; icons are its line icons plus a few drawn to match.

DARK = {
    "app_bar": "#1c1f26", "app_bar_text": "#e6eaf2", "app_bar_sep": "#3f4552", "app_bar_hover": "#2b2f37",
    "bg": "#1f232b", "card": "#272b34", "header": "#2f343e", "border": "#3f4552", "rule": "#353b47",
    "text": "#dee2e8", "dim": "#949ba8", "accent": "#3a88ee", "primary": "#3a88ee", "primary_hover": "#5098f0",
    "accent_soft": "#2a466e", "selected_ink": "#9cc7f5", "control": "#1c1f26", "control_border": "#4a5262",
    "field": "#1e2229", "hover": "#363d4a", "error": "#f06460", "warning": "#ecaa46",
    "ok_bg": "#2c4a33", "ok_ink": "#8fd19e", "warn_bg": "#43331c", "warn_ink": "#f5cc89",
    "pill_bg": "#2f343e", "merged": "#b39bee", "card_hi": "#1b3048", "card_hi_border": "#2a5c9b",
}
LIGHT = {
    "app_bar": "#343840", "app_bar_text": "#e6eaf2", "app_bar_sep": "#4f5562", "app_bar_hover": "#40454e",
    "bg": "#f7f9fb", "card": "#ffffff", "header": "#eceef2", "border": "#e0e5ec", "rule": "#e6eaf0",
    "text": "#262a32", "dim": "#6e7480", "accent": "#2678da", "primary": "#236ead", "primary_hover": "#2b78b8",
    "accent_soft": "#e8f1fa", "selected_ink": "#1a507f", "control": "#ffffff", "control_border": "#c3cbd6",
    "field": "#ffffff", "hover": "#e2eaf6", "error": "#d63636", "warning": "#de961e",
    "ok_bg": "#e5f2e6", "ok_ink": "#276b2c", "warn_bg": "#fff6e5", "warn_ink": "#7a4a05",
    "pill_bg": "#eceef2", "merged": "#7349cd", "card_hi": "#e3f0fb", "card_hi_border": "#328bd3",
}

SANS = "IBM Plex Sans"
MONO = "IBM Plex Mono"


def theme(dark):
    return DARK if dark else LIGHT


def asset(*parts):
    """A bundled file: next to the script, or inside the packaged app."""
    for base in (SCRIPT_DIR, getattr(sys, "_MEIPASS", SCRIPT_DIR)):
        p = os.path.join(base, "assets", *parts)
        if os.path.exists(p):
            return p
    return None


def load_fonts():
    """Register SolidCraft's IBM Plex faces; fall back to the system sans if they're missing."""
    global SANS, MONO
    from PySide6.QtGui import QFontDatabase
    for name, var in (("IBMPlexSans-Variable.ttf", "SANS"), ("IBMPlexMono-Regular.ttf", "MONO")):
        p = asset("fonts", name)
        fid = QFontDatabase.addApplicationFont(p) if p else -1
        families = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
        if families:
            globals()[var] = families[0]


# Real interface weights from the variable font (Qt ignores font-weight for it; the wght axis works).
WEIGHTS = {"appTitle": 600, "strong": 600, "heading": 600, "prNumber": 600, "sectionTitle": 600, "primary": 600,
           "pillOk": 600, "pillWarn": 600, "pillMuted": 600, "pillMerged": 600, "pillError": 600}


class WeightFilter(QObject):
    """Gives each widget SolidCraft's weight for its role as it is polished (styled)."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Polish and isinstance(obj, QWidget):
            weight = WEIGHTS.get(obj.objectName())
            if weight is None and isinstance(obj, (QPushButton, QTabBar)):
                weight = 500
            if weight is not None:
                f = obj.font()
                f.setVariableAxis(QFont.Tag("wght"), weight)
                obj.setFont(f)
        return False


def tinted_svg(name, color):
    """SolidCraft icons draw in currentColor: return a copy of the SVG in `color` (cached file path)."""
    src = asset("icons", f"{name}.svg")
    if not src:
        return ""
    out = os.path.join(CACHE_DIR, "icons", f"{name}-{color.lstrip('#')}.svg")
    if not os.path.exists(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(src, encoding="utf-8") as f, open(out, "w", encoding="utf-8") as g:
            g.write(f.read().replace("currentColor", color))
    return out


def icon(name, color):
    p = tinted_svg(name, color)
    return QIcon(p) if p else QIcon()


def make_qss(dark):
    t = theme(dark)
    chev = tinted_svg("chevron", t["dim"])
    tick = tinted_svg("check", "#ffffff")
    return f"""
    * {{ font-family: "{SANS}"; }}
    QMainWindow, QDialog {{ background-color: {t['bg']}; color: {t['text']}; }}
    QDialog {{ background-color: {t['card']}; }}
    QWidget {{ color: {t['text']}; font-size: 12.5px; }}
    QToolTip {{ background-color: {t['card']}; color: {t['text']}; border: 1px solid {t['border']}; padding: 4px 6px; }}

    QWidget#appBar {{ background-color: {t['app_bar']}; }}
    QWidget#appBar QLabel {{ color: {t['app_bar_text']}; }}
    QLabel#appTitle {{ font-size: 13px; font-weight: 600; }}
    QLabel#appVersion {{ color: {t['dim']}; font-size: 11.5px; }}
    QFrame#appSep {{ background-color: {t['app_bar_sep']}; }}
    QPushButton#appBarButton {{ background: transparent; color: {t['app_bar_text']}; border: 1px solid {t['app_bar_sep']};
        border-radius: 4px; padding: 2px 10px; min-height: 22px; font-weight: 500; }}
    QPushButton#appBarButton:hover {{ background-color: {t['app_bar_hover']}; }}

    QFrame#card {{ background-color: {t['card']}; border: 1px solid {t['border']}; border-radius: 8px; }}
    QFrame#rule {{ background-color: {t['rule']}; border: none; }}
    QFrame#field {{ background-color: {t['field']}; border: 1px solid {t['border']}; border-radius: 8px; }}
    QLabel {{ background: transparent; }}
    QLabel#sectionTitle {{ color: {t['dim']}; font-size: 10.5px; font-weight: 600; letter-spacing: 0.4px; }}
    QLabel#heading {{ font-size: 15px; font-weight: 600; }}
    QLabel#strong {{ font-weight: 600; }}
    QLabel#muted {{ color: {t['dim']}; font-size: 11.5px; }}
    QLabel#mono {{ color: {t['dim']}; font-family: "{MONO}"; font-size: 11.5px; }}
    QLabel#prNumber {{ color: {t['accent']}; font-weight: 600; }}
    QLabel#footer {{ color: {t['dim']}; font-size: 11.5px; }}
    QLabel#pillOk, QLabel#pillWarn, QLabel#pillMuted, QLabel#pillMerged, QLabel#pillError {{
        border-radius: 9px; padding: 1px 8px; font-size: 11px; font-weight: 600; }}
    QLabel#pillOk {{ background-color: {t['ok_bg']}; color: {t['ok_ink']}; }}
    QLabel#pillWarn {{ background-color: {t['warn_bg']}; color: {t['warn_ink']}; }}
    QLabel#pillMuted {{ background-color: {t['pill_bg']}; color: {t['dim']}; }}
    QLabel#pillMerged {{ background-color: {t['pill_bg']}; color: {t['merged']}; }}
    QLabel#pillError {{ background-color: {t['pill_bg']}; color: {t['error']}; }}

    QPushButton {{ background-color: {t['control']}; color: {t['text']}; border: 1px solid {t['control_border']};
        border-radius: 6px; padding: 0 12px; min-height: 26px; max-height: 26px; font-size: 12.5px; font-weight: 500; }}
    QPushButton:hover {{ background-color: {t['hover']}; }}
    QPushButton:pressed {{ background-color: {t['accent_soft']}; }}
    QPushButton:disabled {{ color: {t['dim']}; border-color: {t['rule']}; background-color: {t['card']}; }}
    QPushButton#primary {{ background-color: {t['primary']}; border-color: {t['primary']}; color: #ffffff; font-weight: 600; }}
    QPushButton#primary:hover {{ background-color: {t['primary_hover']}; border-color: {t['primary_hover']}; }}
    QPushButton#primary:disabled {{ background-color: {t['accent_soft']}; border-color: {t['accent_soft']}; color: {t['dim']}; }}
    QPushButton#danger {{ color: {t['error']}; }}
    QPushButton#ghost {{ background: transparent; border: 1px solid transparent; padding: 0 6px; }}
    QPushButton#ghost:hover {{ background-color: {t['hover']}; }}
    QPushButton#small {{ min-height: 22px; max-height: 22px; padding: 0 9px; font-size: 11.5px; }}

    QLineEdit, QComboBox {{ background-color: {t['control']}; color: {t['text']}; border: 1px solid {t['control_border']};
        border-radius: 6px; padding: 0 8px; min-height: 26px; selection-background-color: {t['accent_soft']};
        selection-color: {t['selected_ink']}; }}
    QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-color: {t['accent']}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{ image: url("{chev}"); width: 14px; height: 14px; }}
    QComboBox QAbstractItemView {{ background-color: {t['card']}; color: {t['text']}; border: 1px solid {t['border']};
        selection-background-color: {t['accent_soft']}; selection-color: {t['selected_ink']}; outline: none; padding: 4px; }}

    QListWidget {{ background-color: {t['card']}; border: 1px solid {t['border']}; border-radius: 8px; outline: none; padding: 4px; }}
    QListWidget::item {{ border-radius: 6px; padding: 0; }}
    QListWidget::item:hover {{ background-color: {t['hover']}; }}
    QListWidget::item:selected {{ background-color: {t['accent_soft']}; color: {t['selected_ink']}; }}
    QTreeWidget {{ background-color: {t['card']}; border: 1px solid {t['border']}; border-radius: 8px; outline: none; }}
    QTreeWidget::item {{ padding: 4px 2px; }}
    QTreeWidget::item:selected {{ background-color: {t['accent_soft']}; color: {t['selected_ink']}; }}
    QHeaderView::section {{ background-color: {t['header']}; color: {t['dim']}; padding: 6px 8px; border: none;
        font-size: 10.5px; font-weight: 600; }}

    QCheckBox {{ spacing: 8px; background: transparent; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px; border: 1px solid {t['control_border']};
        background: {t['control']}; }}
    QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
    QCheckBox::indicator:checked {{ background-color: {t['accent']}; border-color: {t['accent']}; image: url("{tick}"); }}

    QTabWidget::pane {{ border: none; background: transparent; top: 2px; }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{ background: transparent; color: {t['dim']}; padding: 4px 12px; margin-right: 4px; border-radius: 6px;
        font-weight: 500; }}
    QTabBar::tab:selected {{ background-color: {t['accent_soft']}; color: {t['selected_ink']}; font-weight: 600; }}
    QTabBar::tab:hover:!selected {{ background-color: {t['hover']}; color: {t['text']}; }}

    QScrollArea {{ border: none; background: transparent; }}
    QAbstractScrollArea > QWidget#qt_scrollarea_viewport, QWidget#scrollInner {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['control_border']}; border-radius: 3px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['dim']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; background: none; }}

    QTextEdit, QTextBrowser {{ background-color: {t['field']}; color: {t['text']}; border: 1px solid {t['border']}; border-radius: 8px; }}
    QTextEdit {{ font-family: "{MONO}"; font-size: 11.5px; }}
    QTextBrowser {{ font-family: "{SANS}"; font-size: 12.5px; }}
    QProgressBar {{ background-color: {t['control']}; border: 1px solid {t['border']}; border-radius: 4px; color: {t['text']};
        text-align: center; font-size: 10.5px; font-weight: 600; }}
    QProgressBar::chunk {{ background-color: {t['accent']}; border-radius: 3px; }}
    QProgressBar#thin {{ max-height: 6px; min-height: 6px; border: none; border-radius: 3px; background-color: {t['header']}; }}
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


def card(title=None, extra=None):
    """A SolidCraft panel: rounded surface, optional uppercase title over a thin rule.
    Returns (frame, body layout)."""
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 14)
    lay.setSpacing(8)
    if title:
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(section_title(title))
        head.addStretch()
        if extra is not None:
            head.addWidget(extra)
        lay.addLayout(head)
        lay.addWidget(rule())
    return f, lay


def rule():
    line = QFrame()
    line.setObjectName("rule")
    line.setFixedHeight(1)
    return line


def section_title(text):
    lbl = QLabel(text.upper())
    lbl.setObjectName("sectionTitle")
    return lbl


def pill(text, kind="Muted"):
    lbl = QLabel(text)
    lbl.setObjectName(f"pill{kind}")
    return lbl


# Buttons whose icon colour follows the theme: (button, icon name, role)
_ICON_BUTTONS = []


def icon_color(role, dark):
    t = theme(dark)
    return {"primary": "#ffffff", "danger": t["error"]}.get(role, t["text"] if role != "appBarButton" else t["app_bar_text"])


def retint_icons(dark):
    alive = []
    for b, name, role in _ICON_BUTTONS:
        try:
            b.setIcon(icon(name, icon_color(role, dark)))
            alive.append((b, name, role))
        except RuntimeError:  # the button was deleted with its row
            pass
    _ICON_BUTTONS[:] = alive


def button(text, role=None, slot=None, tip=None, icon_name=None, dark=True, small=False):
    """SolidCraft button roles: None (secondary), "primary", "danger", "ghost", "appBarButton"."""
    b = QPushButton(text)
    if role:
        b.setObjectName(role)
    elif small:
        b.setObjectName("small")
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    if icon_name:
        b.setIcon(icon(icon_name, icon_color(role, dark)))
        b.setIconSize(QSize(14, 14))
        _ICON_BUTTONS.append((b, icon_name, role))
    b.setCursor(Qt.PointingHandCursor)
    return b


def relative_time(ts):
    d = time.time() - ts
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    if d < 86400 * 30:
        return f"{int(d // 86400)} d ago"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def short_path(p):
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


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
        row.addWidget(button("OK", "primary", self.accept))
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
            f'<a style="color:{t["accent"]}" href="{REPO_URL}/releases">the official GitHub releases</a>.<br>'
            "• Builds GitHub pull requests (or <i>main</i>) with cargo and launches the result. A Rust toolchain "
            "is found automatically, or installed into the install folder on request (no root).<br>"
            "• Project library: preview pictures come from each version's own <tt>solidcraft-cli</tt>; "
            f'3D view opens the file in <a style="color:{t["accent"]}" href="https://f3d.app/">F3D</a> if it is installed '
            "(SolidCraft designs are exported to STL first).<br>"
            "• Versions it launches don't add their own menu entries; use <i>Desktop entry</i> for that.<br>"
            "• Each SolidCraft it starts gets a private control port, so <i>Open in running instance</i> can "
            "hand it a file.<br><br>"
            f"Install folder: <tt>{install_dir}</tt><br>"
            f"Build log: <tt>{BUILD_LOG_FILE}</tt><br><br>"
            "Unofficial; not affiliated with the SolidCraft project. Modelled on "
            f'<a style="color:{t["accent"]}" href="https://github.com/deltahedra3d/freecad-launcher">FreeCAD Smart '
            "Launcher</a> by Deltahedra (MIT)."
        )
        text.setWordWrap(True)
        text.setOpenExternalLinks(True)
        text.setTextFormat(Qt.RichText)
        lay.addWidget(text)
        lay.addStretch()
        lay.addWidget(button("OK", "primary", self.accept), 0, Qt.AlignRight)


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
        row.addWidget(button("Copy to clipboard", None, lambda: QApplication.clipboard().setText(text.toPlainText())))
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
            b = button("Download", "primary", lambda _=False, k=kind: self.start(k), None, "import_tray", app.dark)
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
        lines.setObjectName("muted" if welcome else "strong")
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
            row.addWidget(button(f"{verb} stable", None if len(kinds) == 2 else "primary", lambda: self.pick("stable")))
        if "nightly" in kinds:
            row.addWidget(button(f"{verb} nightly", None if len(kinds) == 2 else "primary", lambda: self.pick("nightly")))
        if len(kinds) == 2:
            row.addWidget(button(f"{verb} both", "primary", lambda: self.pick("both")))
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
        row.addWidget(button("Close", "primary", self.accept))
        lay.addLayout(row)
        self.sig_loaded.connect(self.apply)
        threading.Thread(target=self.load, daemon=True).start()

    def load(self):
        num = self.pr.get("number")
        images = {}
        try:
            pr = github_get(f"{API}/pulls/{num}")
            comments = github_get(f"{API}/issues/{num}/comments?per_page=100")
            parts = [f"## Description — @{pr['user']['login']} · {pr['created_at'][:10]}\n\n{pr.get('body') or '*(no description)*'}"]
            for c in comments:
                parts.append(f"## @{c['user']['login']} · {c['created_at'][:16].replace('T', ' ')}\n\n{c.get('body') or ''}")
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
        # Qt draws Markdown headings far larger than SolidCraft's type scale: step them down two levels.
        md = re.sub(r"^(#{1,4})(?=\s)", lambda m: "#" * min(6, len(m.group(1)) + 2), md, flags=re.M)
        self.body.setMarkdown(md)


class AddBuildDialog(QDialog):
    """Add a SolidCraft program already on disk, or a fork on GitHub to build."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.result_kind = None
        self.result = None
        self.setWindowTitle("Add build")
        self.setMinimumWidth(560)
        self.setStyleSheet(make_qss(app.dark))
        d = app.dark
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        lay.addWidget(self.tabs)

        local = QWidget()
        ll = QVBoxLayout(local)
        ll.setContentsMargins(0, 10, 0, 0)
        ll.setSpacing(8)
        hint = QLabel("A SolidCraft program or AppImage on this computer, such as a fork you build yourself.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        ll.addWidget(hint)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("/path/to/solidcraft or solidcraft-….AppImage")
        row.addWidget(self.path_edit, 1)
        row.addWidget(button("Browse…", None, self.browse, None, "folder", d))
        ll.addLayout(row)
        self.local_name = QLineEdit()
        self.local_name.setPlaceholderText("Name in the list, e.g. My fork")
        ll.addWidget(self.local_name)
        ll.addStretch()
        self.tabs.addTab(local, "Program on this computer")

        fork = QWidget()
        fl = QVBoxLayout(fork)
        fl.setContentsMargins(0, 10, 0, 0)
        fl.setSpacing(8)
        hint = QLabel("A fork of SolidCraft on GitHub. Its branch is fetched into your source folder and built with "
                      "cargo, like a pull request. Only build code you trust: building runs it.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        fl.addWidget(hint)
        self.repo_edit = QLineEdit()
        self.repo_edit.setPlaceholderText("owner/repo or GitHub URL, e.g. aavits/solidcraft")
        fl.addWidget(self.repo_edit)
        self.branch_edit = QComboBox()
        self.branch_edit.setEditable(True)
        self.branch_edit.lineEdit().setPlaceholderText("branch")
        brow = QHBoxLayout()
        brow.addWidget(self.branch_edit, 1)
        brow.addWidget(button("List branches", None, self.list_branches, None, "refresh", d))
        fl.addLayout(brow)
        self.fork_name = QLineEdit()
        self.fork_name.setPlaceholderText("Name in the list (default: owner/repo @ branch)")
        fl.addWidget(self.fork_name)
        self.fork_msg = QLabel("")
        self.fork_msg.setObjectName("muted")
        self.fork_msg.setWordWrap(True)
        fl.addWidget(self.fork_msg)
        fl.addStretch()
        self.tabs.addTab(fork, "Fork on GitHub")

        foot = QHBoxLayout()
        foot.addStretch()
        foot.addWidget(button("Cancel", None, self.reject))
        self.btn_ok = button("Add", "primary", self.accept_input)
        foot.addWidget(self.btn_ok)
        lay.addLayout(foot)
        self.tabs.currentChanged.connect(lambda i: self.btn_ok.setText("Add" if i == 0 else "Build…"))

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "SolidCraft program", HOME)
        if path:
            self.path_edit.setText(path)
            if not self.local_name.text():
                self.local_name.setText(os.path.basename(os.path.dirname(path)) if os.path.basename(path) == "solidcraft" else os.path.basename(path))

    def repo(self):
        text = self.repo_edit.text().strip().removesuffix(".git").removesuffix("/")
        m = re.search(r"(?:github\.com[/:])?([\w.-]+/[\w.-]+)$", text)
        return m.group(1) if m else ""

    def list_branches(self):
        repo = self.repo()
        if not repo:
            self.fork_msg.setText("Enter owner/repo first.")
            return
        try:
            info = github_get(f"https://api.github.com/repos/{repo}")
            branches = github_get(f"https://api.github.com/repos/{repo}/branches?per_page=100")
        except Exception as e:  # unknown repo or no network
            self.fork_msg.setText(f"Could not read {repo}: {e}")
            return
        current = self.branch_edit.currentText()
        self.branch_edit.clear()
        names = [b["name"] for b in branches]
        default = info.get("default_branch", "")
        names.sort(key=lambda n: (n != default, n))
        self.branch_edit.addItems(names)
        if current:
            self.branch_edit.setCurrentText(current)
        parent = (info.get("parent") or {}).get("full_name", "")
        self.fork_msg.setText(f"{len(names)} branches." + ("" if parent == REPO or repo == REPO else
                                                           f"  Note: {repo} is not a fork of {REPO}."))

    def accept_input(self):
        if self.tabs.currentIndex() == 0:
            path = os.path.expanduser(self.path_edit.text().strip())
            if not (os.path.isfile(path) and os.access(path, os.X_OK)):
                QMessageBox.warning(self, "Add build", "Choose a SolidCraft program file that can be run.")
                return
            self.result_kind = "program"
            self.result = (path, self.local_name.text().strip() or os.path.basename(path))
        else:
            repo, branch = self.repo(), self.branch_edit.currentText().strip()
            if not repo or not branch:
                QMessageBox.warning(self, "Add build", "Enter the fork (owner/repo) and a branch.")
                return
            try:
                github_get(f"https://api.github.com/repos/{repo}/branches/{urllib.parse.quote(branch, safe='')}")
            except Exception as e:  # unknown repo or branch
                QMessageBox.warning(self, "Add build", f"github.com/{repo} has no branch \"{branch}\" ({e}).")
                return
            self.result_kind = "fork"
            self.result = (repo, branch, self.fork_name.text().strip() or f"{repo} @ {branch}")
        self.accept()


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
        if "extra_versions" not in self.config:
            fork = os.path.join(HOME, "Applications", "solidcraft-fork", "bin", "solidcraft")
            self.config["extra_versions"] = [{"display": "Fork (~/Applications/solidcraft-fork)", "path": fork}] if os.path.exists(fork) else []
            self.save_config()
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
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        d = self.dark

        # Application bar, as SolidCraft's title bar: brand mark, name, actions on the right.
        bar = QWidget()
        bar.setObjectName("appBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(40)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 0, 12, 0)
        bl.setSpacing(10)
        icon_file = self.icon_path()
        if icon_file:
            mark = QLabel()
            mark.setPixmap(QPixmap(icon_file).scaled(20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            bl.addWidget(mark)
        title = QLabel("SolidCraft Launcher")
        title.setObjectName("appTitle")
        bl.addWidget(title)
        bl.addStretch()
        bl.addWidget(button("Statistics", "appBarButton", self.show_stats, "Time and launches per version", "chart", d))
        bl.addWidget(button("Infos", "appBarButton", lambda: InfoDialog(self, self.dark, self.install_dir).exec(), None, "help", d))
        ver = QLabel(f"v{APP_VERSION}")
        ver.setObjectName("appVersion")
        bl.addWidget(ver)
        outer.addWidget(bar)

        body = QWidget()
        outer.addWidget(body, 1)
        bod = QVBoxLayout(body)
        bod.setContentsMargins(16, 14, 16, 10)
        bod.setSpacing(10)
        content = QHBoxLayout()
        content.setSpacing(12)
        bod.addLayout(content, 1)

        # Left column: versions, community, options
        left_inner = QWidget()
        left_inner.setObjectName("scrollInner")
        left = QVBoxLayout(left_inner)
        left.setContentsMargins(0, 0, 4, 0)
        left.setSpacing(12)
        left.setAlignment(Qt.AlignTop)

        b1, l1 = card("SolidCraft versions")
        self.version_combos = {}
        self.version_status = {}
        for kind, label in (("stable", "Stable"), ("nightly", "Nightly"), ("other", "Other builds")):
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 2, 0, 2)
            wl.setSpacing(6)
            hdr = QHBoxLayout()
            name = QLabel(label)
            name.setObjectName("strong")
            hdr.addWidget(name)
            hdr.addStretch()
            st = pill("Checking…" if kind != "other" else "")
            st.setVisible(kind != "other")
            hdr.addWidget(st)
            if kind == "other":
                self.btn_rebuild = button("Rebuild", "ghost", self.rebuild_selected, "Fetch the latest commits and build again", "refresh", d)
                self.btn_rebuild.setVisible(False)
                hdr.addWidget(self.btn_rebuild)
                hdr.addWidget(button("Add build…", "ghost", self.add_build, "Add a program on this computer, or build a fork from GitHub", "plus", d))
            wl.addLayout(hdr)
            combo = QComboBox()
            combo.activated.connect(lambda _i, k=kind: self.select_kind(k))
            wl.addWidget(combo)
            self.version_combos[kind] = combo
            self.version_status[kind] = st
            l1.addWidget(wrap)
            if kind == "other":
                self.other_wrap = wrap
                combo.setToolTip("Your installed SolidCraft, your own builds, and pull requests you built")
        l1.addSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(button("Download", None, self.open_download_dialog, None, "import_tray", d))
        row.addWidget(button("Desktop entry", None, self.create_desktop_entry, "Add the selected version to the application menu", "plus", d))
        row.addStretch()
        self.btn_delete = button("Delete", "danger", self.delete_local, None, "trash", d)
        row.addWidget(self.btn_delete)
        row.addWidget(button("Launch", "primary", self.launch_selected, None, "play", d))
        l1.addLayout(row)
        left.addWidget(b1)

        b3, l3 = card("SolidCraft community")
        links = QHBoxLayout()
        links.setSpacing(6)
        for text, url in (("Website", "https://solidcraft.io"), ("Docs", "https://docs.solidcraft.io"),
                          ("Discord", "https://discord.gg/FtU4NQNVDH"), ("GitHub", REPO_URL)):
            links.addWidget(button(text, None, lambda _=False, u=url: webbrowser.open(u), url))
        l3.addLayout(links)
        self.milestone_box = QVBoxLayout()
        self.milestone_box.setSpacing(5)
        ph = QLabel("Milestones: loading…")
        ph.setObjectName("muted")
        self.milestone_box.addWidget(ph)
        l3.addLayout(self.milestone_box)
        left.addWidget(b3)

        b4, l4 = card("Launcher options")
        l4.setSpacing(6)
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
        left_scroll.setMinimumWidth(470)
        left_scroll.setMaximumWidth(520)
        content.addWidget(left_scroll, 0)

        # Centre: pull requests
        refresh = button("", "ghost", lambda: threading.Thread(target=self.fetch_recent_prs, daemon=True).start(),
                         "Refresh pull requests", "refresh", d)
        refresh.setFixedSize(28, 24)
        pr_panel, pl = card("Pull requests", refresh)
        self.pr_tabs = QTabWidget()
        self.pr_tabs.setDocumentMode(True)
        self.pr_recent_layout = self._scroll_tab("Recent")
        search_page = QWidget()
        sl = QVBoxLayout(search_page)
        sl.setContentsMargins(0, 6, 0, 0)
        bar2 = QHBoxLayout()
        self.pr_search_edit = QLineEdit()
        self.pr_search_edit.setPlaceholderText("PR title, @author, or PR number…")
        self.pr_search_edit.returnPressed.connect(self.search_prs)
        bar2.addWidget(self.pr_search_edit, 1)
        bar2.addWidget(button("Search", None, self.search_prs))
        sl.addLayout(bar2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("scrollInner")
        self.pr_search_layout = QVBoxLayout(inner)
        self.pr_search_layout.setAlignment(Qt.AlignTop)
        self.pr_search_layout.setContentsMargins(0, 0, 4, 0)
        self.pr_search_layout.setSpacing(0)
        scroll.setWidget(inner)
        sl.addWidget(scroll, 1)
        self.pr_tabs.addTab(search_page, "Search")
        self.pr_fav_layout = self._scroll_tab("Favorites")
        pl.addWidget(self.pr_tabs, 1)
        more = QHBoxLayout()
        more.addStretch()
        more.addWidget(button("View more on GitHub", None, lambda: webbrowser.open(f"{REPO_URL}/pulls"), None, "external", d))
        pl.addLayout(more)

        b2, l2 = card("Test a GitHub pull request")
        b2.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.pr_stack = QStackedWidget()
        onboard = QWidget()
        ol = QVBoxLayout(onboard)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(8)
        msg = QLabel("Want to try a PR? You need a SolidCraft source folder (a git clone) first.")
        msg.setObjectName("muted")
        msg.setWordWrap(True)
        ol.addWidget(msg)
        ob = QHBoxLayout()
        ob.addWidget(button("Use an existing clone", None, self.pick_source, "Select a folder with a clone of tangency-labs/solidcraft", "folder", d))
        ob.addWidget(button("Clone && build SolidCraft…", "primary", self.build_from_source, "Clone SolidCraft and build it with cargo", "build", d))
        ob.addStretch()
        ol.addLayout(ob)
        self.pr_stack.addWidget(onboard)

        ready = QWidget()
        rl = QVBoxLayout(ready)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        src = QHBoxLayout()
        lab = QLabel("Source")
        lab.setFixedWidth(54)
        src.addWidget(lab)
        self.src_edit = QLineEdit(self.config.get("src_folder", ""))
        self.src_edit.editingFinished.connect(self._src_edited)
        src.addWidget(self.src_edit, 1)
        src.addWidget(button("Browse…", None, self.pick_source, None, "folder", d))
        rl.addLayout(src)
        prow = QHBoxLayout()
        lab = QLabel("PR #")
        lab.setFixedWidth(54)
        prow.addWidget(lab)
        self.combo_pr = QComboBox()
        self.combo_pr.setEditable(True)
        self.combo_pr.addItems([str(x) for x in self.config.get("pr_history", [])])
        self.combo_pr.setCurrentText(str(self.config.get("last_pr", "")))
        self.combo_pr.setFixedWidth(110)
        self.combo_pr.currentTextChanged.connect(self.on_pr_changed)
        prow.addWidget(self.combo_pr)
        prow.addWidget(button("GitHub", None, self.open_pr_on_github, None, "external", d))
        prow.addWidget(button("Conversation", None, self.open_pr_conversation, None, "comment", d))
        prow.addStretch()
        rl.addLayout(prow)
        brow = QHBoxLayout()
        self.btn_build = button("Build", "primary", self.build_pr, None, "build", d)
        self.btn_stop = button("Stop", "danger", self.stop_build, None, "stop", d)
        self.btn_stop.setEnabled(False)
        brow.addWidget(self.btn_build, 1)
        brow.addWidget(self.btn_stop, 1)
        brow.addWidget(button("Launch", None, self.launch_pr, None, "play", d), 1)
        rl.addLayout(brow)
        self.pr_progress = QProgressBar()
        self.pr_progress.setObjectName("thin")
        self.pr_progress.setRange(0, 100)
        self.pr_progress.setTextVisible(False)
        self.pr_progress.hide()
        rl.addWidget(self.pr_progress)
        self.lbl_pr_info = ElidedLabel("Title: -\nAuthor: -")
        self.lbl_pr_info.setObjectName("muted")
        rl.addWidget(self.lbl_pr_info)
        self.lbl_pr_status = ElidedLabel("Ready.")
        self.lbl_pr_status.setObjectName("muted")
        rl.addWidget(self.lbl_pr_status)
        xrow = QHBoxLayout()
        xrow.addWidget(button("Build SolidCraft main…", None, self.build_from_source, "Update the source folder to main and build it", "build", d))
        xrow.addWidget(button("Free build space", None, self.free_build_space, "Delete the source folder's cargo build files (target/)", "trash", d))
        xrow.addStretch()
        rl.addLayout(xrow)
        self.pr_stack.addWidget(ready)
        l2.addWidget(self.pr_stack)
        pl.addWidget(b2)
        pl.setStretch(2, 1)
        content.addWidget(pr_panel, 1)

        # Right: project library
        right, rr = card("Project library")
        thumb_frame = QFrame()
        thumb_frame.setObjectName("field")
        thumb_frame.setFixedHeight(176)
        tl = QVBoxLayout(thumb_frame)
        tl.setContentsMargins(4, 4, 4, 4)
        self.thumb = QLabel("No project selected")
        self.thumb.setObjectName("muted")
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setWordWrap(True)
        tl.addWidget(self.thumb)
        rr.addWidget(thumb_frame)
        vrow = QHBoxLayout()
        self.lbl_file = ElidedLabel("No project selected")
        self.lbl_file.setObjectName("muted")
        vrow.addWidget(self.lbl_file, 1)
        self.btn_3d = button("3D view", None, self.open_3d_view, "Open the selected file in F3D (designs are exported to STL first)", "eye", d)
        self.btn_3d.setEnabled(False)
        vrow.addWidget(self.btn_3d)
        rr.addLayout(vrow)
        self.project_list = QListWidget()
        self.project_list.setSpacing(1)
        self.project_list.itemSelectionChanged.connect(self.on_project_select)
        self.project_list.itemDoubleClicked.connect(lambda _i: self.open_3d_view())
        rr.addWidget(self.project_list, 1)
        srow = QHBoxLayout()
        srow.addWidget(button("Scan folder…", None, self.change_scan_folder, None, "folder", d))
        srow.addWidget(button("Refresh", None, self.refresh_projects, None, "refresh", d))
        srow.addStretch()
        rr.addLayout(srow)
        rr.addWidget(rule())
        rr.addWidget(button("Launch project", "primary", self.launch_project, "Open the selected file with the selected version", "play", d))
        rr.addWidget(button("Open in running instance", None, self.open_in_running, "Send the file to the last SolidCraft started from here", "external", d))
        rr.addWidget(button("Launch project with PR", None, self.launch_project_with_pr, "Open the selected file with the PR build", "build", d))
        content.addWidget(right, 1)

        # Status line and footer
        foot = QHBoxLayout()
        foot.setSpacing(12)
        self.lbl_status = ElidedLabel("Checking GitHub for releases…")
        self.lbl_status.setObjectName("footer")
        foot.addWidget(self.lbl_status, 1)
        self.lbl_footer = QLabel("")
        self.lbl_footer.setObjectName("footer")
        foot.addWidget(self.lbl_footer)
        credit = QLabel(f'<a href="https://github.com/deltahedra3d/freecad-launcher" style="color:{theme(d)["dim"]}; text-decoration:none;">'
                        "Based on FreeCAD Smart Launcher by Deltahedra</a>")
        credit.setObjectName("footer")
        credit.setOpenExternalLinks(True)
        self.credit = credit
        foot.addWidget(credit)
        bod.addLayout(foot)

        for layout, text in ((self.pr_recent_layout, "Loading recent PRs…"),
                             (self.pr_search_layout, "Type a keyword or PR number and press Enter.")):
            ph = QLabel(text)
            ph.setObjectName("muted")
            ph.setAlignment(Qt.AlignCenter)
            ph.setWordWrap(True)
            layout.addWidget(ph)
        self.render_favorites()

    def _scroll_tab(self, name):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("scrollInner")
        layout = QVBoxLayout(inner)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(0, 4, 4, 0)
        layout.setSpacing(0)
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
        retint_icons(value)
        self.credit.setText(self.credit.text().replace(theme(not value)["dim"], theme(value)["dim"]))
        self.render_favorites()
        self.render_milestones(getattr(self, "_milestones", []))
        self.refresh_projects()
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
                               "display": b.get("display", b["id"]), "managed": True, "build": True, "meta": b})
        for extra in self.config.get("extra_versions", []):
            if os.path.exists(extra["path"]):
                others.append({"name": extra["display"], "path": extra["path"], "ver": extra["display"], "kind": "other",
                               "display": extra["display"], "managed": False, "extra": True})
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
        self.other_wrap.setVisible(True)
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
        self.btn_delete.setEnabled(bool(sel and (sel["managed"] or sel.get("extra"))))
        self.btn_delete.setText("Remove" if sel and sel.get("extra") else "Delete")
        meta = (sel or {}).get("meta") or {}
        self.btn_rebuild.setVisible(self.selected_kind == "other" and bool(meta.get("fork") or meta.get("id") == "main"))

    def selected_version(self):
        path = self.version_combos[self.selected_kind].currentData()
        return next((v for v in self.local if v["path"] == path), None)

    def update_version_status(self):
        for kind in ("stable", "nightly"):
            lbl = self.version_status[kind]
            latest = self.remote[kind][0] if self.remote[kind] else None
            have = [v for v in self.local if v["kind"] == kind]
            if latest is None:
                text, kind_name = ("Not installed" if not have else f"{len(have)} installed"), "Muted"
            elif any(v["name"] == latest["filename"] for v in have):
                text, kind_name = "Up to date", "Ok"
            elif have:
                text, kind_name = f"New: {display_name(latest['ver']).replace(' (' + latest['ver'].split('-')[0] + ')', '')}", "Warn"
            else:
                text, kind_name = "Not installed", "Muted"
            lbl.setText(text)
            lbl.setObjectName(f"pill{kind_name}")
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
        if v and v.get("extra"):
            if QMessageBox.question(self, "Remove", f"Remove {v['display']} from the list?\n\nThe program itself stays:\n{v['path']}") == QMessageBox.Yes:
                self.config["extra_versions"] = [e for e in self.config.get("extra_versions", []) if e.get("path") != v["path"]]
                self.save_config()
                self.refresh_local_versions()
            return
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
        t = theme(self.dark)
        ms.sort(key=lambda m: (m.get("due_on") or "9999", -m.get("open_issues", 0)))
        for m in ms[:3]:
            closed, opened = m.get("closed_issues", 0), m.get("open_issues", 0)
            pct = int(closed * 100 / max(1, closed + opened))
            row = QHBoxLayout()
            name = QLabel(f'<a href="{m["html_url"]}" style="color:{t["text"]}; text-decoration:none;">{m["title"]}</a>')
            name.setOpenExternalLinks(True)
            row.addWidget(name)
            row.addStretch()
            count = QLabel(f"{closed}/{closed + opened} · {pct}%")
            count.setObjectName("muted")
            row.addWidget(count)
            self.milestone_box.addLayout(row)
            bar = QProgressBar()
            bar.setObjectName("thin")
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setTextVisible(False)
            self.milestone_box.addWidget(bar)
            self.milestone_box.addSpacing(3)

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
        for p, mtime in sorted(found.items(), key=lambda kv: -kv[1]):
            label = os.path.basename(p)
            names[label] = names.get(label, 0) + 1
            if names[label] > 1:
                label = f"{label}  ({os.path.basename(os.path.dirname(p))})"
            # A row like SolidCraft's Recent list: name, the folder in mono, and how long ago.
            item = QListWidgetItem()
            item.setToolTip(p)
            item.setData(Qt.UserRole, p)
            row = QWidget()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(8, 5, 8, 5)
            rl.setSpacing(1)
            top = QHBoxLayout()
            name = QLabel(os.path.splitext(label)[0] if p.endswith(".solidcraft") else label)
            name.setObjectName("strong")
            top.addWidget(name, 1)
            when = QLabel(relative_time(mtime))
            when.setObjectName("muted")
            top.addWidget(when)
            rl.addLayout(top)
            folder = ElidedLabel(short_path(os.path.dirname(p)) + "/")
            folder.setObjectName("mono")
            rl.addWidget(folder)
            item.setSizeHint(QSize(10, 44))
            self.project_list.addItem(item)
            self.project_list.setItemWidget(item, row)
            self.projects[label] = p
        self.lbl_file.setText(f"{len(self.projects)} files in {short_path(root)} and SolidCraft's recent files")

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
            self.lbl_file.setText(f"{human_size(st.st_size)} · modified {time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}")
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
        """(state text, pill kind)"""
        if pr.get("merged_at"):
            return "Merged", "Merged"
        if pr.get("state") == "closed":
            return "Closed", "Error"
        if pr.get("draft"):
            return "Draft", "Muted"
        return "Open", "Ok"

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
        """One pull request, laid out like a SolidCraft list row."""
        d = self.dark
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 8, 2, 0)
        lay.setSpacing(4)
        num = pr.get("number")
        top = QHBoxLayout()
        top.setSpacing(8)
        lbl = QLabel(f"#{num}")
        lbl.setObjectName("prNumber")
        top.addWidget(lbl)
        title = QLabel(pr.get("title", ""))
        title.setObjectName("strong")
        title.setWordWrap(True)
        top.addWidget(title, 1)
        st, kind = self.pr_status(pr)
        top.addWidget(pill(st, kind), 0, Qt.AlignTop)
        lay.addLayout(top)
        bottom = QHBoxLayout()
        bottom.setSpacing(4)
        meta = QLabel(f"@{(pr.get('user') or {}).get('login', '?')} · {(pr.get('created_at') or '')[:10]}")
        meta.setObjectName("muted")
        bottom.addWidget(meta)
        bottom.addStretch()
        fav = any(f.get("number") == num for f in self.pr_favorites)
        for name, tip, slot in (("star_filled" if fav else "star", "Add/remove favorite", lambda: self.toggle_favorite(pr)),
                                ("comment", "Read the conversation", lambda: PRConversationDialog(self, pr, self.dark).exec()),
                                ("external", "Open on GitHub", lambda: webbrowser.open(f"{REPO_URL}/pull/{num}"))):
            b = button("", "ghost", slot, tip, name, d)
            b.setFixedSize(28, 24)
            if name == "star_filled":
                b.setIcon(icon(name, theme(d)["warning"]))
            bottom.addWidget(b)
        bottom.addWidget(button("Test", None, lambda: self.use_pr(pr), "Put this PR in the test box below", None, d, small=True))
        lay.addLayout(bottom)
        lay.addSpacing(4)
        lay.addWidget(rule())
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
                  os.path.join(HOME, "Applications", "solidcraft-fork", "cargo"), os.path.join(HOME, "solidcraft-fork", "cargo")]
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

    def _build(self, src, ref, build_id, display, log, status, is_cancelled, set_proc, confirm=None, fork=None):
        """Fetch `ref` (a PR number, "main", or with `fork` = (owner/repo, branch) that fork's branch),
        check it out, cargo build, keep the binary."""
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
        if fork:
            remote, refspec = f"https://github.com/{fork[0]}.git", fork[1]
        else:
            remote, refspec = f"{REPO_URL}.git", (f"pull/{ref}/head" if ref != "main" else "main")
        status(f"Fetching {refspec} from {remote.removeprefix('https://github.com/').removesuffix('.git')}…", 6)
        run(["git", "fetch", "--force", remote, refspec], src)
        run(["git", "checkout", "--force", "-B", branch, "FETCH_HEAD"], src)
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
        try:
            with open(os.path.join(src, "Cargo.lock"), encoding="utf-8") as f:
                total = max(1, f.read().count("[[package]]"))
        except OSError:
            total = 600
        compiled = [0]
        current = {"crate": None, "since": time.time(), "pct": 10}

        def progress(line):
            if line.lstrip().startswith(("Compiling", "Checking")):
                compiled[0] += 1
                parts = line.split()
                current.update(crate=parts[1] if len(parts) > 1 else None, since=time.time(),
                               pct=10 + min(85, compiled[0] * 85 // total))
                status(f"Building {display}: {line.strip()[:80]}", current["pct"])

        building = threading.Event()

        def heartbeat():
            # The big crates (engine, UI, the app) compile for minutes without printing anything:
            # say which one is still going, and for how long, so the build doesn't look stuck.
            while not building.wait(1.0):
                quiet = time.time() - current["since"]
                if current["crate"] and quiet >= 4:
                    m, sec = divmod(int(quiet), 60)
                    status(f"Building {display}: still compiling {current['crate']}… {m}:{sec:02d}", current["pct"])

        status(f"Building {display} with cargo (several minutes)…", 10)
        threading.Thread(target=heartbeat, daemon=True).start()
        try:
            run([cargo, "build", "--release", "-p", "solidcraft"], src, cenv, progress)
        finally:
            building.set()
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
        entry = {"id": build_id, "path": dest, "display": display, "commit": commit, "built": time.strftime("%Y-%m-%d %H:%M")}
        if fork:
            entry["fork"] = {"repo": fork[0], "branch": fork[1]}
        builds.insert(0, entry)
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
        self.guided_build("main", "main (built from source)", "Build SolidCraft main",
                          "Update {src} to the latest main and build it?")

    def guided_build(self, build_id, display, title, question, fork=None):
        """Clone the source if needed, confirm, then build `fork`'s branch (or main) in a progress dialog."""
        src = self.source_dir()
        dlg = SourceBuildDialog(self, self.dark)
        dlg.setWindowTitle(title)
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
                        if not dlg.confirm("Clone SolidCraft", f"Builds need a SolidCraft source folder.\n\nClone {REPO_URL}\ninto {default_src}?\n\n(about 200 MB)"):
                            raise BuildCancelled()
                        os.makedirs(os.path.dirname(default_src), exist_ok=True)
                        status("Cloning SolidCraft…", 1)
                        self._run_logged(["git", "clone", "--depth", "50", f"{REPO_URL}.git", default_src], os.path.dirname(default_src),
                                         None, log, lambda: dlg.cancelled, lambda p: setattr(dlg, "proc", p))
                        src = default_src
                    self.config["src_folder"] = src
                    self.save_config()
                if not dlg.confirm(title, question.format(src=src) + "\n\nThis takes several minutes and up to ~7 GB of build files "
                                   "(\"Free build space\" deletes them afterwards). Uncommitted changes in the source folder are stashed."):
                    raise BuildCancelled()
                self._build(src, "main", build_id, display, log, status, lambda: dlg.cancelled,
                            lambda p: setattr(dlg, "proc", p), confirm=dlg.confirm, fork=fork)
                dlg.sig_done.emit(True, f"Done. \"{display}\" is under Other builds.")
            except BuildCancelled:
                dlg.sig_done.emit(False, "Cancelled.")
            except Exception as e:  # shown in the dialog with the log above it
                dlg.sig_done.emit(False, f"Failed: {e}")

        threading.Thread(target=work, daemon=True).start()
        dlg.exec()
        self._update_pr_panel()
        self.refresh_local_versions()
        built = next((v for v in self.local if v.get("meta", {}).get("id") == build_id), None)
        if built:
            self.config["last_other"] = built["path"]
            self.select_kind("other")
            self.refresh_local_versions()

    def add_build(self):
        dlg = AddBuildDialog(self)
        if not dlg.exec():
            return
        if dlg.result_kind == "program":
            path, name = dlg.result
            same = next((v for v in self.local if os.path.realpath(v["path"]) == os.path.realpath(path)), None)
            if same:
                QMessageBox.information(self, "Add build", f"That program is already in the list as \"{same['display']}\".")
                return
            extras = [e for e in self.config.get("extra_versions", []) if e.get("path") != path]
            extras.append({"display": name, "path": path})
            self.config["extra_versions"] = extras
            self.config["last_other"] = path
            self.save_config()
            self.selected_kind = "other"
            self.refresh_local_versions()
            self.select_kind("other")
        else:
            repo, branch, name = dlg.result
            build_id = "fork-" + re.sub(r"[^a-z0-9]+", "-", f"{repo}-{branch}".lower()).strip("-")
            self.guided_build(build_id, name, f"Build {repo} @ {branch}",
                              f"Fetch {branch} from github.com/{repo} into {{src}} and build it?", fork=(repo, branch))

    def rebuild_selected(self):
        v = self.selected_version()
        meta = (v or {}).get("meta") or {}
        if meta.get("fork"):
            f = meta["fork"]
            self.guided_build(meta["id"], meta.get("display", v["display"]), f"Rebuild {f['repo']} @ {f['branch']}",
                              f"Fetch the latest {f['branch']} from github.com/{f['repo']} into {{src}} and build it again?",
                              fork=(f["repo"], f["branch"]))
        elif meta.get("id") == "main":
            self.build_from_source()

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
    os.makedirs(CACHE_DIR, exist_ok=True)
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
    load_fonts()
    weights = WeightFilter(app)
    app.installEventFilter(weights)
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
