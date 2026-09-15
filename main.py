import sys
import os
import re
import json
import time
import random
import shutil
import subprocess
import zipfile
import concurrent.futures
import ctypes
import nexus
import html
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import (
    Qt,
    QUrl,
    QRect,
    QPoint,
    QSize,
    QTimer,
    QPropertyAnimation,
    QVariantAnimation,
    QEasingCurve,
    pyqtSignal,
    pyqtProperty,
    QThread,
    QEvent,
    QObject,
)
from PyQt6.QtGui import (
    QFont,
    QFontDatabase,
    QFontMetrics,
    QDesktopServices,
    QCursor,
    QIcon,
    QImage,
    QPixmap,
    QPainter,
    QPen,
    QBrush,
    QColor,
    QPolygon,
    QMovie,
)
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QPushButton,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QScrollArea,
    QFileDialog,
    QStackedWidget,
    QSizePolicy,
    QGraphicsOpacityEffect,
    QGraphicsBlurEffect,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QMessageBox,
    QCheckBox,
    QComboBox,
    QMenu,
    QInputDialog,
    QDialog,
)

try:
    from pipeline import ModPipeline, MERGEABLE_EXTENSIONS, FCB_EXTENSIONS
except ImportError as exc:
    ModPipeline = None
    MERGEABLE_EXTENSIONS = {".fcb", ".lib", ".obj", ".xml"}
    FCB_EXTENSIONS = {".fcb", ".lib", ".obj"}
    PIPELINE_IMPORT_ERROR = exc
else:
    PIPELINE_IMPORT_ERROR = None


# -----------------------------------------------------------------------------
# Global Scrollbar Style
# -----------------------------------------------------------------------------
SCROLLBAR_QSS = (
    "QScrollBar:vertical{background:transparent;width:8px;margin:5px 2px 5px 0;border:none;}"
    "QScrollBar::handle:vertical{background:#333333;min-height:42px;border-radius:0px;border:none;}"
    "QScrollBar::handle:vertical:hover{background:#00A3E0;}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{width:0;height:0;border:none;background:none;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
)


# -----------------------------------------------------------------------------
# Registry Auto-Detect Helper
# -----------------------------------------------------------------------------
def _auto_detect_game_path():
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs\2688", 0, winreg.KEY_READ) as key:
            path, _ = winreg.QueryValueEx(key, "InstallDir")
            exe = Path(path) / "bin" / "WatchDogs2.exe"
            if exe.exists(): return str(exe)
    except Exception:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", 0, winreg.KEY_READ) as key:
            steam_path, _ = winreg.QueryValueEx(key, "InstallPath")
            vdf = Path(steam_path) / "steamapps" / "libraryfolders.vdf"
            if vdf.exists():
                content = vdf.read_text(encoding='utf-8', errors='ignore')
                paths = re.findall(r'"path"\s+"([^"]+)"', content)
                for p in paths:
                    exe = Path(p.replace('\\\\', '\\')) / "steamapps" / "common" / "Watch_Dogs2" / "bin" / "WatchDogs2.exe"
                    if exe.exists(): return str(exe.resolve())
    except Exception:
        pass
    return None

# -----------------------------------------------------------------------------
# Paths / constants
# -----------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(__file__).resolve().parent
    ROOT_DIR = Path(sys.executable).resolve().parent
else:
    ROOT_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = ROOT_DIR

# user data (workspace/tools/config) always sits next to the exe, sys.executable
# handles that fine under PyInstaller, Nuitka or plain python.
# bundled assets are the annoying part - PyInstaller onefile unpacks to sys._MEIPASS,
# Nuitka doesn't have that so we check __compiled__ instead. find_asset() checks both.
_IS_NUITKA = "__compiled__" in globals()

ASSETS_DIR = ROOT_DIR / "assets"
WORKSPACE_DIR = ROOT_DIR / "workspace"
TOOLS_DIR = ROOT_DIR / "tools"
ARCHIVES_DIR = WORKSPACE_DIR / "archives"
CONFIG_FILE = ROOT_DIR / "config.json"
LOAD_ORDER_FILE = WORKSPACE_DIR / "load_order.json"
CONFLICT_RULES_FILE = WORKSPACE_DIR / "conflict_rules.json"
NEXUS_META_FILE = WORKSPACE_DIR / "nexus_meta.json"

CYAN = QColor(0, 163, 224, 210) 
CYAN_HOVER = QColor(0, 139, 191, 255)
PURPLE = QColor(124, 40, 161, 210)
PURPLE_HOVER = QColor(89, 29, 115, 255)
ORANGE = QColor("#FF6B00")
WHITE = QColor("#FFFFFF")
TEXT = QColor("#F5F5F5")
DIM = QColor("#888888")
BORDER = QColor("#1F1F1F")
SUBTLE = QColor("#141414")

PANEL_ALPHA = 215
WINDOW_SCRIM_ALPHA = 150
BACKGROUND_BLUR_RADIUS = 5

for directory in (WORKSPACE_DIR, TOOLS_DIR, ARCHIVES_DIR):
    directory.mkdir(parents=True, exist_ok=True)


_ASSET_CACHE = {}

def tinted_qicon(name: str, color: QColor, size: int = 16, right_pad: int = 6) -> QIcon:
    """QIcon version of draw_image_icon for regular setIcon() buttons.
    right_pad exists because the source pngs are cropped tight - without it the icon
    crowds the button text. Baked into the pixmap since QSS can't add gap here."""
    key = (name, color.name(QColor.NameFormat.HexArgb), size, right_pad)
    pixmap = _ICON_PIXMAP_CACHE.get(key)
    if pixmap is None:
        path = find_asset(f"icons/{name}.png")
        if path is None or not path.exists():
            return QIcon()
        raw = QPixmap(str(path))
        if raw.isNull():
            return QIcon()
        raw = raw.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        tinted = QPixmap(raw.width() + right_pad, raw.height())
        tinted.fill(Qt.GlobalColor.transparent)
        tp = QPainter(tinted)
        tp.drawPixmap(0, 0, raw)
        tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        tp.fillRect(0, 0, raw.width(), raw.height(), color)
        tp.end()
        pixmap = tinted
        if len(_ICON_PIXMAP_CACHE) > 256:
            _ICON_PIXMAP_CACHE.clear()
        _ICON_PIXMAP_CACHE[key] = pixmap
    return QIcon(pixmap)


_ICON_PIXMAP_CACHE = {}   # (name, color_hex, size) -> QPixmap, tinted and ready to paint


class SpinIconButton(QPushButton):
    """Button whose icon crossfades to a spinner gif on click, then back after hold_ms.
    Uses grayscale-as-alpha trick (like BlendedGifLabel) so the gif's white bg doesn't show."""

    def __init__(self, icon_name: str, color: QColor, size: int = 16,
                 gif_path: Optional[Path] = None, gif_size: int = 28,
                 hold_ms: int = 900, parent=None):
        super().__init__(parent)
        self._icon_size = size
        self._pixmap = tinted_qicon(icon_name, color, size, right_pad=0).pixmap(size, size)
        self._gif_size = gif_size
        self._blend = 0.0       # 0 = icon, 1 = gif
        self._gif_frame = None  # QImage, alpha channel = frame's own grayscale

        self._movie = None
        if gif_path and gif_path.exists():
            self._movie = QMovie(str(gif_path))
            self._movie.setScaledSize(QSize(gif_size, gif_size))
            self._movie.frameChanged.connect(self._on_frame)

        self.blend_anim = QVariantAnimation(self)
        self.blend_anim.setDuration(220)
        self.blend_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.blend_anim.valueChanged.connect(self._set_blend)

        self.hold_ms = hold_ms
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._revert)

        self.clicked.connect(self.play)

    def _on_frame(self, _index):
        pm = self._movie.currentPixmap()
        if pm.isNull():
            return
        frame = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        gray = frame.convertToFormat(QImage.Format.Format_Grayscale8)
        frame.setAlphaChannel(gray)
        self._gif_frame = frame
        self.update()

    def _set_blend(self, value):
        self._blend = value
        self.update()

    def play(self):
        if self._movie:
            self._movie.start()
        self.blend_anim.stop()
        self.blend_anim.setStartValue(self._blend)
        self.blend_anim.setEndValue(1.0)
        self.blend_anim.start()
        self._hold_timer.start(self.hold_ms)

    def _revert(self):
        self.blend_anim.stop()
        self.blend_anim.setStartValue(self._blend)
        self.blend_anim.setEndValue(0.0)
        self.blend_anim.start()
        if self._movie:
            QTimer.singleShot(self.blend_anim.duration(), self._movie.stop)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        cx, cy = self.width() / 2, self.height() / 2
        if self._blend < 1.0:
            painter.save()
            painter.setOpacity(1.0 - self._blend)
            painter.drawPixmap(int(cx - self._icon_size / 2), int(cy - self._icon_size / 2), self._pixmap)
            painter.restore()
        if self._blend > 0.0 and self._gif_frame is not None:
            painter.save()
            painter.setOpacity(self._blend)
            target = QRect(int(cx - self._gif_size / 2), int(cy - self._gif_size / 2), self._gif_size, self._gif_size)
            painter.drawImage(target, self._gif_frame)
            painter.restore()
        painter.end()


def draw_image_icon(painter: QPainter, name: str, rect: QRect, color: QColor):
    """Paints a tinted icon from assets/icons/<name>.png. Source pngs are white
    silhouettes so tinting is just alpha masking. Cached since repaint happens on every hover."""
    size = rect.width()
    key = (name, color.name(QColor.NameFormat.HexArgb), size)
    pixmap = _ICON_PIXMAP_CACHE.get(key)
    if pixmap is None:
        path = find_asset(f"icons/{name}.png")
        if path is None or not path.exists():
            return False
        raw = QPixmap(str(path))
        if raw.isNull():
            return False
        raw = raw.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        tinted = QPixmap(raw.size())
        tinted.fill(Qt.GlobalColor.transparent)
        tp = QPainter(tinted)
        tp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tp.drawPixmap(0, 0, raw)
        tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        tp.fillRect(tinted.rect(), color)
        tp.end()
        pixmap = tinted
        if len(_ICON_PIXMAP_CACHE) > 256:
            _ICON_PIXMAP_CACHE.clear()
        _ICON_PIXMAP_CACHE[key] = pixmap

    px = rect.x() + (rect.width() - pixmap.width()) // 2
    py = rect.y() + (rect.height() - pixmap.height()) // 2
    painter.drawPixmap(px, py, pixmap)
    return True


_APP_CURSOR = None
_APP_CURSOR_BUILT = False


def interactive_cursor():
    """use this everywhere instead of Qt's PointingHandCursor directly - that's the whole
    point, otherwise every button reverts to the system hand cursor on hover. falls back
    to it too if the custom asset didn't load."""
    return get_app_cursor() or Qt.CursorShape.PointingHandCursor


def get_app_cursor() -> Optional[QCursor]:
    # cached, don't rebuild every time
    global _APP_CURSOR, _APP_CURSOR_BUILT
    if not _APP_CURSOR_BUILT:
        _APP_CURSOR = build_app_cursor()
        _APP_CURSOR_BUILT = True
    return _APP_CURSOR


def build_app_cursor() -> Optional[QCursor]:
    # default cursor for the main window - widgets with their own cursor set
    # (buttons etc) aren't affected, this only reaches children that don't override it
    path = find_asset("icons/cursor_finger.png")
    if path is None or not path.exists():
        return None
    raw = QPixmap(str(path))
    if raw.isNull():
        return None
    size = 32
    scaled = raw.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
    # hotspot at the fingertip, roughly where the normal hand cursor points
    return QCursor(scaled, scaled.width() // 2, 2)


def find_asset(pattern: str) -> Optional[Path]:
    if pattern in _ASSET_CACHE:
        return _ASSET_CACHE[pattern]

    # beside-the-exe first, then the PyInstaller temp dir if it exists
    meipass_dir = Path(getattr(sys, "_MEIPASS", BUNDLE_DIR))
    roots = [
        ASSETS_DIR,
        ROOT_DIR / "assets",
        BUNDLE_DIR / "assets",
        meipass_dir / "assets",
    ]

    found = None
    seen = set()
    normalized = pattern.replace("\\", os.sep).replace("/", os.sep)
    for root in roots:
        if found or not root or not root.exists():
            continue
        root_key = str(root.resolve())
        if root_key in seen:
            continue
        seen.add(root_key)

        direct = root / normalized
        if direct.is_file():
            found = direct
            break

        target_name = Path(normalized).name
        try:
            for base, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in {"workspace", "dist", "build", "__pycache__"}]
                for filename in files:
                    if fnmatch(filename, target_name) or fnmatch(filename, pattern):
                        found = Path(base) / filename
                        break
                if found:
                    break
        except OSError:
            continue

    _ASSET_CACHE[pattern] = found
    return found


_BOLD_FAMILY_MAP = {}

def make_font(family: str, px: int, weight=QFont.Weight.Normal, tracking: float = 0.0, no_antialias: bool = False) -> QFont:
    if weight >= QFont.Weight.Bold:
        family = _BOLD_FAMILY_MAP.get(family, family)
    font = QFont(family)
    font.setPixelSize(px)
    font.setWeight(weight)
    if tracking:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking)
    if no_antialias:
        font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    return font


def short_mod_name(name: str, limit: int = 26) -> str:
    s = (name or "").strip()
    for _ in range(4):
        before = s
        s = re.sub(r"\s*\d{4}-\d{2}-\d{2}T\d{2}-\d{2,}Z", "", s)
        s = re.sub(r"(-\d+)+$", "", s) 
        s = re.sub(r"-\d{6,}$", "", s)
        parts = s.split()
        if len(parts) > 1:
            last = parts[-1]
            if (len(last) >= 8 and any(c.isdigit() for c in last)
                    and any(c.isupper() for c in last) and any(c.islower() for c in last)):
                s = " ".join(parts[:-1])
        s = s.strip(" -_")
        if s == before:
            break
    s = s.replace("_", " ").strip()
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + "\u2026"
    return s or (name or "")[:limit]


class FontBook:
    def __init__(self) -> None:
        self.univers = "Arial"
        self.univers_bold = "Arial"
        self.helvetica = "Arial"
        self.helvetica_bold = "Arial"
        self.pexico = "Courier New"
        self.ids = []
        self.load_all()

    def _load_one(self, pattern: str) -> Optional[str]:
        path = find_asset(pattern)
        if not path or not path.exists():
            return None
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            return None
        families = QFontDatabase.applicationFontFamilies(font_id)
        self.ids.append(font_id)
        return families[0] if families else None

    def load_all(self) -> None:
        self.univers = self._load_one("fonts/Rajdhani-Regular.ttf") or "Arial"
        self.univers_bold = self._load_one("fonts/Rajdhani-Bold.ttf") or self.univers
        
        self.helvetica = self._load_one("fonts/Inter_18pt-Medium.ttf") or "Arial"
        self.helvetica_bold = self._load_one("fonts/Inter_18pt-Bold.ttf") or self.helvetica
        
        self.pexico = (
            self._load_one("fonts/PixeloidMono.ttf")
            or "Courier New"
        )
        
        _BOLD_FAMILY_MAP[self.univers] = self.univers_bold
        _BOLD_FAMILY_MAP[self.helvetica] = self.helvetica_bold



# -----------------------------------------------------------------------------
# Backend
# -----------------------------------------------------------------------------

class Backend(QObject):
    log_signal = pyqtSignal(str)

    DEFAULT_EXE = r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games\WATCH_DOGS2\bin\WatchDogs2.exe"

    def __init__(self) -> None:
        super().__init__()
        self.game_exe_path, self.tools_dir, self.eac_method, self.eac_enabled, self.nexus_api_key = self.load_config()
        self.auto_reference = True
        self.merging_enabled = True
        self.pipe = None
        # overlap/identical caches, keyed on mtimes. cleared in _invalidate_mod_caches()
        self._overlap_cache = {}
        self._identical_cache = {}
        self.build_pipeline()

    def build_pipeline(self):
        if ModPipeline is None:
            return
        Path(self.tools_dir).mkdir(parents=True, exist_ok=True)
        self.pipe = ModPipeline(
            str(WORKSPACE_DIR),
            str(self.tools_dir),
            log_callback=self.ui_logger,
        )
        self.pipe.init_workspace()

    def load_config(self):
        exe = self.DEFAULT_EXE
        tools = str(TOOLS_DIR)
        eac = 0
        eac_enabled = True
        nexus_key = ""
        if CONFIG_FILE.exists():
            try:
                with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    exe = data.get("exe_path") or exe
                    tools = data.get("tools_dir") or tools
                    eac = data.get("eac_method", 0)
                    eac_enabled = data.get("eac_enabled", True)
                    nexus_key = data.get("nexus_api_key", "")
            except Exception:
                pass
        
        if exe == self.DEFAULT_EXE and not Path(exe).exists():
            detected = _auto_detect_game_path()
            if detected:
                exe = detected

        return exe, tools, eac, eac_enabled, nexus_key

    def save_config(self, path: str = None, tools_dir: str = None, eac_method: int = None,
                    eac_enabled: bool = None, nexus_api_key: str = None) -> None:
        data = {}
        if CONFIG_FILE.exists():
            try:
                with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                pass
                
        if path is not None: self.game_exe_path = path
        if tools_dir is not None: self.tools_dir = tools_dir
        if eac_method is not None: self.eac_method = eac_method
        if eac_enabled is not None: self.eac_enabled = eac_enabled
        if nexus_api_key is not None: self.nexus_api_key = nexus_api_key
            
        data["exe_path"] = self.game_exe_path
        data["tools_dir"] = str(self.tools_dir)
        data["eac_method"] = self.eac_method
        data["eac_enabled"] = self.eac_enabled
        data["nexus_api_key"] = self.nexus_api_key
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    def get_tools_dir(self) -> str:
        return str(self.tools_dir)

    def set_tools_dir(self, path: str) -> str:
        self.tools_dir = os.path.normpath(path)
        self.save_config(tools_dir=self.tools_dir)
        self.build_pipeline()
        self.ui_logger(f"Tools directory set to: {self.tools_dir}")
        return self.tools_dir

    def browse_tools_dir(self, parent=None):
        path = QFileDialog.getExistingDirectory(parent, "Select Gibbed Tools Folder", str(self.tools_dir))
        if path:
            return self.set_tools_dir(path)
        return None

    def ui_logger(self, msg: str) -> None:
        print(f"[Backend] {msg}")
        self.log_signal.emit(f"[SYSTEM] {msg}")

    def get_exe_path(self) -> str:
        return self.game_exe_path

    def load_state(self):
        order, enabled = [], []
        try:
            if LOAD_ORDER_FILE.exists():
                with LOAD_ORDER_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    order = [str(x) for x in data]
                elif isinstance(data, dict):
                    order = [str(x) for x in data.get("order", [])]
                    enabled = [str(x) for x in data.get("enabled", [])]
        except Exception:
            pass
        return order, enabled

    def save_state(self, order, enabled):
        try:
            LOAD_ORDER_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "order": [str(x) for x in order],
                "enabled": [str(x) for x in enabled],
            }
            with LOAD_ORDER_FILE.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except Exception as exc:
            self.ui_logger(f"WARN: Could not save mod load order: {exc}")

    def set_load_order(self, names, enabled=None):
        if enabled is None:
            _, enabled = self.load_state()
        self.save_state(names, enabled)
        return True

    def set_enabled(self, enabled_names):
        order, _ = self.load_state()
        self.save_state(order, enabled_names)
        return True

    # ------------------------------------------------------------------
    # Nexus mod metadata (real name / author / category, MO2-style)
    # ------------------------------------------------------------------
    def load_nexus_meta(self) -> dict:
        try:
            if NEXUS_META_FILE.exists():
                with NEXUS_META_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def save_nexus_meta(self, meta: dict) -> None:
        try:
            NEXUS_META_FILE.parent.mkdir(parents=True, exist_ok=True)
            with NEXUS_META_FILE.open("w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2)
        except Exception as exc:
            self.ui_logger(f"WARN: Could not save Nexus mod metadata: {exc}")

    def set_nexus_meta_for_mod(self, mod_folder: str, info: dict) -> None:
        meta = self.load_nexus_meta()
        meta[mod_folder] = info
        self.save_nexus_meta(meta)

    def link_mod_to_nexus(self, mod_folder: str, reference: str):
        """worker thread. manually links a mod that wasn't installed via Mod Manager
        Download, so it never got metadata automatically."""
        mod_id = nexus.parse_mod_reference(reference)
        if mod_id is None:
            return False, f"'{reference}' doesn't look like a mod URL or a mod ID."
        if not self.nexus_api_key:
            return False, "No Nexus API key set. Add one in Settings."

        client = nexus.NexusClient(self.nexus_api_key)
        try:
            info = client.get_mod_display_info("watchdogs2", mod_id)
        except nexus.NexusApiError as exc:
            return False, f"Could not fetch mod info: {exc}"

        self.set_nexus_meta_for_mod(mod_folder, info)
        return True, f"Linked to '{info['name']}'."

    def find_source_archive(self, mod_folder: str):
        # Matches by stem (same convention get_missing_sources uses).
        if not ARCHIVES_DIR.exists():
            return None
        for f in ARCHIVES_DIR.iterdir():
            if f.is_file() and f.stem == mod_folder:
                return f
        return None

    def auto_identify_mod(self, mod_folder: str):
        # worker thread. tier 1: hash the source archive, look for an MD5 match on Nexus
        # (same trick MO2's Query Info uses) - if it hits, it's certain.
        # tier 2: no archive to hash, so guess the mod ID from the manual-download
        # filename convention and sanity check the name matches. this is a guess,
        # not proof, hence the different log message - fixable via right-click Re-link.
        # no match at all is normal for Discord/manual installs, not an error.
        if not self.nexus_api_key:
            return False, "skipped (no API key)"

        archive = self.find_source_archive(mod_folder)
        if archive is not None:
            try:
                md5 = nexus.hash_file_md5(str(archive))
                client = nexus.NexusClient(self.nexus_api_key)
                match = client.search_by_md5("watchdogs2", md5, expected_size=archive.stat().st_size)
            except nexus.NexusApiError as exc:
                return False, f"lookup failed: {exc}"
            except Exception as exc:
                return False, f"could not hash file: {exc}"
            if match is not None:
                self.set_nexus_meta_for_mod(mod_folder, match)
                return True, f"identified as '{match['name']}' (hash match)"
            return False, "not on Nexus"

        guessed_id = nexus.guess_mod_id_from_folder_name(mod_folder)
        if guessed_id is None:
            return False, "no source archive to hash"

        try:
            client = nexus.NexusClient(self.nexus_api_key)
            info = client.get_mod_display_info("watchdogs2", guessed_id)
        except nexus.NexusApiError as exc:
            return False, f"no source archive; filename suggested mod {guessed_id} but lookup failed: {exc}"

        if not nexus.names_plausibly_match(mod_folder, info["name"]):
            return False, (f"no source archive; filename suggested mod {guessed_id} "
                           f"('{info['name']}') but that doesn't look like a match -- skipped")

        self.set_nexus_meta_for_mod(mod_folder, info)
        return True, f"identified as '{info['name']}' (guessed from filename, no archive to hash -- verify this is right)"

    def auto_identify_all_mods(self, progress_cb=None):
        # worker thread. catch-up pass for mods installed before auto-id existed.
        # skips anything already linked. the sleep between calls is just being polite
        # to the API, not something Nexus actually requires.
        if self.pipe is None:
            return "No pipeline available."
        cache_dir = Path(self.pipe.cache_dir)
        if not cache_dir.exists():
            return "No mods installed."

        existing_meta = self.load_nexus_meta()
        candidates = [d.name for d in cache_dir.iterdir()
                     if d.is_dir() and d.name not in existing_meta]
        if not candidates:
            return "Every installed mod already has Nexus info (or none to check)."

        found, skipped = 0, 0
        for i, mod_folder in enumerate(candidates):
            if progress_cb:
                progress_cb(i + 1, len(candidates), mod_folder)
            ok, detail = self.auto_identify_mod(mod_folder)
            self.ui_logger(f"  {mod_folder}: {detail}")
            if ok:
                found += 1
            else:
                skipped += 1
            if i < len(candidates) - 1:
                time.sleep(0.4)

        return f"Checked {len(candidates)} mod(s): {found} identified, {skipped} not found on Nexus."

    def check_for_mod_updates(self, progress_cb=None):
        # worker thread. checks every linked mod's version against Nexus.
        # no stored version yet? record it as baseline, don't flag an update on
        # the first check since we've got nothing to compare against.
        # update_available/latest_version are stored separately from the installed
        # version so the flag persists across restarts and clears itself once they match again.
        if not self.nexus_api_key:
            return []
        meta = self.load_nexus_meta()
        linked = [(folder, info) for folder, info in meta.items() if info.get("mod_id")]
        if not linked:
            return []

        client = nexus.NexusClient(self.nexus_api_key)
        updates = []
        for i, (mod_folder, info) in enumerate(linked):
            display = info.get("name", mod_folder)
            if progress_cb:
                progress_cb(i + 1, len(linked), display)
            try:
                fresh = client.get_mod_info("watchdogs2", info["mod_id"])
            except nexus.NexusApiError as exc:
                self.ui_logger(f"  {display}: update check failed: {exc}")
                if i < len(linked) - 1:
                    time.sleep(0.4)
                continue

            latest_version = fresh.get("version")
            stored_version = info.get("version")

            if stored_version is None:
                info["version"] = latest_version
                info["update_available"] = False
                self.ui_logger(f"  {display}: recorded version baseline ({latest_version})")
            elif latest_version and latest_version != stored_version:
                info["latest_version"] = latest_version
                info["update_available"] = True
                updates.append({
                    "mod_folder": mod_folder, "name": display,
                    "installed_version": stored_version, "latest_version": latest_version,
                })
                self.ui_logger(f"  {display}: UPDATE AVAILABLE ({stored_version} -> {latest_version})")
            else:
                info["update_available"] = False
                self.ui_logger(f"  {display}: up to date ({stored_version})")

            meta[mod_folder] = info
            if i < len(linked) - 1:
                time.sleep(0.4)

        self.save_nexus_meta(meta)
        return updates

    def generate_health_report(self) -> str:
        # worker thread (touches network + disk). bundles the existing diagnostics
        # into one shareable text report - basically our version of Vortex's Health Check.
        lines = []
        lines.append("WD2 MOD MANAGER -- HEALTH CHECK")
        lines.append(time.strftime("%Y-%m-%d %H:%M:%S"))
        lines.append("=" * 50)

        lines.append("\n[ GAME ]")
        exe = self.game_exe_path
        lines.append(f"  Executable: {exe or '(not set)'}")
        lines.append(f"  Exists: {Path(exe).exists() if exe else False}")
        data_dir = self.game_data_dir()
        lines.append(f"  data_win64: {data_dir}")
        lines.append(f"  Exists: {data_dir.exists()}")
        if self.pipe is not None:
            lines.append(f"  Currently deployed (patch3 present): {self.pipe.is_deployed(str(data_dir))}")

        lines.append("\n[ TOOLS ]")
        if self.pipe is not None and hasattr(self.pipe, "gibbed"):
            status = self.pipe.gibbed.tools_status()
            lines.append(f"  Toolset: {status['toolset']}")
            for key in ("unpack", "pack", "convert"):
                entry = status[key]
                lines.append(f"  {entry['name']}: {'READY' if entry['ok'] else 'MISSING'}")
            lines.append(f"  {status['seven_zip']['name']}: "
                         f"{'READY' if status['seven_zip']['ok'] else 'OPTIONAL, not found'}")
        else:
            lines.append("  pipeline.py could not be imported -- nothing else here will work.")

        lines.append("\n[ MODS ]")
        mods = self.get_installed_mods()
        lines.append(f"  Installed: {len(mods)}")
        lines.append(f"  Queued: {sum(1 for m in mods if m.get('active'))}")
        missing = self.get_missing_sources()
        if missing:
            lines.append(f"  Missing source archive ({len(missing)}): {', '.join(missing[:10])}"
                         + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""))
        else:
            lines.append("  Missing source archives: none")

        active_names = [m["name"] for m in mods if m.get("active")]
        if len(active_names) >= 2:
            lines.append("\n[ CONFLICT SCAN (currently queued mods) ]")
            rows = self.scan_conflicts(active_names)
            unmergeable = [r for r in rows if r["action"] == "UNMERGEABLE"]
            merged = sum(1 for r in rows if r["action"] == "MERGE")
            identical = sum(1 for r in rows if r["action"] == "IDENTICAL")
            lines.append(f"  Contested files: {len(rows)} ({merged} merge, {identical} identical, "
                         f"{len(unmergeable)} unmergeable)")
            for r in unmergeable[:10]:
                lines.append(f"    UNMERGEABLE: {r['path']} ({', '.join(r['blocked'])})")
            if len(unmergeable) > 10:
                lines.append(f"    ...and {len(unmergeable) - 10} more")
        else:
            lines.append("\n[ CONFLICT SCAN ]")
            lines.append("  Fewer than 2 mods queued -- nothing to scan.")

        lines.append("\n[ NEXUS ]")
        if not self.nexus_api_key:
            lines.append("  API key: not set")
        else:
            try:
                info = nexus.NexusClient(self.nexus_api_key).validate()
                tier = "Premium" if info.get("is_premium") else "Free"
                lines.append(f"  API key: valid ({info.get('name', '?')}, {tier})")
            except nexus.NexusApiError as exc:
                lines.append(f"  API key: FAILED -- {exc}")
            linked = sum(1 for m in mods if m.get("nexus_mod_id"))
            updates_pending = sum(1 for m in mods if m.get("nexus_update_available"))
            lines.append(f"  Mods linked to Nexus: {linked} of {len(mods)}")
            if updates_pending:
                lines.append(f"  Updates available: {updates_pending} (see CHECK UPDATES)")

        nxm_command, nxm_current = self.nxm_handler_status()
        lines.append("\n[ NEXUS DOWNLOAD HANDLER ]")
        if nxm_command is None:
            lines.append("  Not registered")
        elif nxm_current:
            lines.append("  Registered and pointing at this install")
        else:
            lines.append(f"  Registered, but to a DIFFERENT path: {nxm_command}")

        lines.append("\n" + "=" * 50)
        report = "\n".join(lines)

        # redact the key unconditionally - error bodies from Nexus get echoed verbatim
        # above and this report is meant to be pasted into bug reports
        if self.nexus_api_key:
            report = report.replace(self.nexus_api_key, "[REDACTED]")
        return report

    def get_installed_mods(self):
        if self.pipe is None or not hasattr(self.pipe, "cache_dir"):
            return []
        mods = []
        cache_dir = Path(self.pipe.cache_dir)
        saved_order, saved_enabled = self.load_state()
        enabled_set = set(saved_enabled)
        nexus_meta = self.load_nexus_meta()

        if cache_dir.exists():
            for mod_name in os.listdir(cache_dir):
                mod_path = cache_dir / mod_name
                if mod_path.is_dir():
                    file_count = sum(len(files) for _, _, files in os.walk(mod_path))
                    entry = {
                        "name": mod_name,
                        "category": "MOD DATA",
                        "files": file_count,
                        "active": mod_name in enabled_set,
                    }
                    # nexus_* fields are display-only, additive. deploy/merge/conflict-scan
                    # still key everything off the raw folder name
                    meta = nexus_meta.get(mod_name)
                    if meta:
                        entry["nexus_name"] = meta.get("name")
                        entry["nexus_author"] = meta.get("author")
                        entry["nexus_category"] = meta.get("category")
                        entry["nexus_mod_id"] = meta.get("mod_id")
                        entry["nexus_update_available"] = bool(meta.get("update_available"))
                        entry["nexus_latest_version"] = meta.get("latest_version")
                    mods.append(entry)

        if saved_order:
            position = {name: i for i, name in enumerate(saved_order)}
            original_order = {mod["name"]: i for i, mod in enumerate(mods)}
            unknown_base = len(position) + 100000
            mods.sort(
                key=lambda mod: (
                    position.get(mod["name"], unknown_base + original_order.get(mod["name"], 0))
                )
            )

        names = [m["name"] for m in mods]
        still_enabled = [n for n in names if n in enabled_set]
        if names != saved_order or still_enabled != saved_enabled:
            self.save_state(names, still_enabled)
        return mods

    def delete_mods(self, names):
        if self.pipe is None or not hasattr(self.pipe, "cache_dir"):
            return "Removal failed: pipeline.py could not be imported."
        cache_dir = Path(self.pipe.cache_dir)
        removed = 0
        for name in names:
            target = cache_dir / name
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                    removed += 1
            except Exception as exc:
                self.ui_logger(f"ERROR: Could not remove '{name}': {exc}")
        self.ui_logger(f"Removed {removed} mod(s) from cache.")
        if removed:
            self._invalidate_mod_caches()
        return f"Removed {removed} mod(s)."

    def _extracted_mod_names(self):
        if self.pipe is None or not hasattr(self.pipe, "cache_dir"):
            return set()
        cache_dir = Path(self.pipe.cache_dir)
        if not cache_dir.exists():
            return set()
        return {n for n in os.listdir(cache_dir) if (cache_dir / n).is_dir()}

    def get_mod_files(self, mod_name):
        if self.pipe is None or not hasattr(self.pipe, "cache_dir"):
            return []
        mod_path = Path(self.pipe.cache_dir) / mod_name
        if not mod_path.is_dir():
            return []
        out = []
        for root, _, files in os.walk(mod_path):
            for f in files:
                full = Path(root) / f
                try:
                    size = full.stat().st_size
                except OSError:
                    size = 0
                out.append((full.relative_to(mod_path).as_posix(), size))
        out.sort()
        return out

    def load_conflict_rules(self):
        try:
            if CONFLICT_RULES_FILE.exists():
                with CONFLICT_RULES_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
        return {}

    def save_conflict_rules(self, rules):
        try:
            CONFLICT_RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with CONFLICT_RULES_FILE.open("w", encoding="utf-8") as fh:
                json.dump(rules, fh, indent=2, sort_keys=True)
        except Exception as exc:
            self.ui_logger(f"WARN: Could not save conflict rules: {exc}")

    def set_conflict_rule(self, rel_path, winner):
        rules = self.load_conflict_rules()
        if winner in (None, "", "AUTO"):
            rules.pop(rel_path, None)
        else:
            rules[rel_path] = winner
        self.save_conflict_rules(rules)
        return rules

    def set_conflict_rules_bulk(self, rel_paths, winner):
        rules = self.load_conflict_rules()
        for rel_path in rel_paths:
            if winner in (None, "", "AUTO"):
                rules.pop(rel_path, None)
            else:
                rules[rel_path] = winner
        self.save_conflict_rules(rules)
        return rules

    @staticmethod
    def _files_byte_identical(paths):
        """true if all paths have identical content - catches mods that ship the
        same file by coincidence (shared fix bundled by multiple authors etc)."""
        if len(paths) < 2:
            return True
        sizes = {os.path.getsize(p) for p in paths}
        if len(sizes) > 1:
            return False
        first = None
        for p in paths:
            with open(p, "rb") as fh:
                content = fh.read()
            if first is None:
                first = content
            elif content != first:
                return False
        return True

    def scan_conflicts(self, mod_names):
        # NOTE: this used to be threaded, ripped that out - pipeline.py's roundtrip_ok
        # uses one shared scratch dir and threads were stomping on each other and
        # leaving junk in the mod cache folders. sequential only, don't "optimize" this.
        overlap = self.get_mod_overlap(mod_names)
        rules = self.load_conflict_rules()
        order = {name: i for i, name in enumerate(mod_names)}
        rows = []
        
        for rel_path in sorted(overlap):
            contributors = sorted(overlap[rel_path], key=lambda n: order.get(n, 0))
            ext = os.path.splitext(rel_path)[1].lower()
            mergeable = ext in MERGEABLE_EXTENSIONS
            rule = rules.get(rel_path, "AUTO")

            # if every contributor's file is byte-identical there's nothing to resolve,
            # check this before the merge/rule logic since it makes that moot
            identical = False
            if self.pipe is not None:
                cache_dir = Path(self.pipe.cache_dir)
                candidate_paths = [cache_dir / name / rel_path.replace("/", os.sep) for name in contributors]
                if all(p.is_file() for p in candidate_paths):
                    try:
                        id_key = tuple(sorted(
                            (str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in candidate_paths
                        ))
                    except OSError:
                        id_key = None
                    if id_key is not None and id_key in self._identical_cache:
                        identical = self._identical_cache[id_key]
                    else:
                        identical = self._files_byte_identical([str(p) for p in candidate_paths])
                        if id_key is not None:
                            if len(self._identical_cache) > 4096:
                                self._identical_cache.clear()
                            self._identical_cache[id_key] = identical

            if identical:
                rows.append({
                    "path": rel_path, "mods": contributors, "mergeable": mergeable,
                    "rule": rule, "action": "IDENTICAL", "winner": contributors[-1],
                    "blocked": [], "identical": True,
                })
                continue

            blocked = []
            if mergeable and ext in FCB_EXTENSIONS and self.pipe is not None:
                cache_dir = Path(self.pipe.cache_dir)
                for name in contributors:
                    candidate = cache_dir / name / rel_path.replace("/", os.sep)
                    # fast after first run (cached in pipeline.py)
                    if candidate.is_file() and not self.pipe.roundtrip_ok(str(candidate)):
                        blocked.append(name)

            if rule != "AUTO" and rule in contributors:
                action, winner = "FORCED", rule
            elif blocked:
                action, winner = "UNMERGEABLE", contributors[-1]
            elif mergeable:
                action, winner = "MERGE", None
            else:
                action, winner = "OVERWRITE", contributors[-1]
                
            rows.append({
                "path": rel_path,
                "mods": contributors,
                "mergeable": mergeable,
                "rule": rule,
                "action": action,
                "winner": winner,
                "blocked": blocked,
            })
        return rows

    def get_mod_overlap(self, mod_names):
        if self.pipe is None or not hasattr(self.pipe, "cache_dir"):
            return {}
        cache_dir = Path(self.pipe.cache_dir)

        # Keyed on mtimes, not just names, so delete+re-add under the same name still busts the cache.
        mtimes = []
        for name in mod_names:
            p = cache_dir / name
            try:
                mtimes.append((name, p.stat().st_mtime_ns))
            except OSError:
                mtimes.append((name, None))
        cache_key = tuple(sorted(mtimes))
        if cache_key in self._overlap_cache:
            return self._overlap_cache[cache_key]

        file_map = {}
        for name in mod_names:
            mod_path = cache_dir / name
            if not mod_path.is_dir():
                continue
            for root, _, files in os.walk(mod_path):
                for f in files:
                    full = Path(root) / f
                    rel = full.relative_to(mod_path).as_posix()
                    file_map.setdefault(rel, []).append(name)
        result = {rel: mods for rel, mods in file_map.items() if len(mods) > 1}

        # just a size cap, not real LRU
        if len(self._overlap_cache) > 64:
            self._overlap_cache.clear()
        self._overlap_cache[cache_key] = result
        return result

    def _invalidate_mod_caches(self):
        # call whenever mods change on disk
        self._overlap_cache.clear()
        self._identical_cache.clear()

    def get_missing_sources(self):
        stems = set()
        if ARCHIVES_DIR.exists():
            for file_name in os.listdir(ARCHIVES_DIR):
                stems.add(Path(file_name).stem)
        return sorted(n for n in self._extracted_mod_names() if n not in stems)

    def game_data_dir(self) -> Path:
        return Path(self.game_exe_path).parent.parent / "data_win64"

    def get_deploy_state(self):
        rows = []
        if self.pipe is not None and hasattr(self.pipe, "gibbed"):
            status = self.pipe.gibbed.tools_status()
            for key, label in (("unpack", "Unpacker"), ("pack", "Packer"), ("convert", "XML Converter")):
                entry = status[key]
                rows.append({
                    "name": entry["name"],
                    "desc": f"{label} // {status['toolset']}",
                    "status": "READY" if entry["ok"] else "MISSING",
                    "icon": "drive",
                })
            sz = status["seven_zip"]
            rows.append({
                "name": sz["name"],
                "desc": "Archive extractor // optional",
                "status": "READY" if sz["ok"] else "OPTIONAL",
                "icon": "filezip",
            })
        else:
            rows.append({
                "name": "Toolchain",
                "desc": "pipeline.py could not be imported",
                "status": "MISSING",
                "icon": "drive",
            })

        data_dir = self.game_data_dir()
        if not data_dir.exists():
            rows.append({
                "name": "Game Directory",
                "desc": f"Not found: {data_dir}",
                "status": "MISSING",
                "icon": "drive",
            })
        else:
            found = False
            for name in ("patch3.fat", "patch3.dat"):
                path = data_dir / name
                if path.is_file():
                    found = True
                    size_mb = path.stat().st_size / (1024 * 1024)
                    rows.append({
                        "name": name,
                        "desc": f"Deployed to game // {size_mb:,.1f} MB",
                        "status": "DEPLOYED",
                        "icon": "drive",
                    })
            if not found:
                rows.append({
                    "name": "patch3.fat / patch3.dat",
                    "desc": "Not present // game is in a vanilla state",
                    "status": "VANILLA",
                    "icon": "drive",
                })

        summary = self.get_baseline_summary()
        if summary["ready"]:
            rows.append({
                "name": "3-Way Merge",
                "desc": f"Vanilla reference cached // {summary['files']:,} files",
                "status": "READY",
                "icon": "filezip",
            })
        else:
            rows.append({
                "name": "3-Way Merge",
                "desc": "Reference read automatically when mods clash",
                "status": "ON DEMAND",
                "icon": "filezip",
            })

        out_fat = WORKSPACE_DIR / "output" / "patch3.fat"
        if out_fat.is_file():
            size_mb = out_fat.stat().st_size / (1024 * 1024)
            rows.append({
                "name": "Last Build",
                "desc": f"workspace/output // {size_mb:,.1f} MB",
                "status": "READY",
                "icon": "filezip",
            })
        else:
            rows.append({
                "name": "Last Build",
                "desc": "No patch built yet",
                "status": "PENDING",
                "icon": "filezip",
            })
        return rows

    def list_mod_files(self, mod_name):
        if self.pipe is None: return []
        return self.pipe.list_mod_files(mod_name)

    def compute_conflicts(self, mod_names):
        if self.pipe is None: return []
        return self.pipe.compute_conflicts(mod_names)

    def get_baseline_summary(self):
        if self.pipe is None or not hasattr(self.pipe, "baseline_summary"):
            return {"archives": [], "files": 0, "ready": False}
        return self.pipe.baseline_summary()

    def restore_vanilla(self):
        if self.pipe is None:
            return "Restore failed: pipeline.py could not be imported."
        data_dir = self.game_data_dir()
        if not data_dir.exists():
            return "Restore failed: Invalid game path."
        ok, message = self.pipe.restore_vanilla(str(data_dir))
        return message

    # ------------------------------------------------------------------
    # Nexus Mods integration
    # ------------------------------------------------------------------
    def get_nexus_api_key(self) -> str:
        return self.nexus_api_key

    def set_nexus_api_key(self, key: str) -> None:
        self.save_config(nexus_api_key=(key or "").strip())

    def validate_nexus_key(self, key: str):
        # worker thread
        try:
            info = nexus.NexusClient(key).validate()
            name = info.get("name", "?")
            premium = "Premium" if info.get("is_premium") else "Free"
            return True, f"Connected as {name} ({premium})"
        except nexus.NexusApiError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Unexpected error: {exc}"

    def register_nxm_handler(self):
        try:
            command = nexus.register_nxm_handler()
            self.ui_logger(f"Registered as nxm:// handler -> {command}")
            return True, command
        except nexus.NexusApiError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Registration failed: {exc}"

    def nxm_handler_status(self):
        """(registered_command_or_None, is_current_process)"""
        try:
            current = nexus.get_registered_nxm_command()
            return current, nexus.is_nxm_handler_current()
        except Exception:
            return None, False

    def handle_nxm_download(self, nxm_info: dict, progress_cb=None):
        # worker thread. resolves nxm:// to a real URL, downloads, then runs it through
        # the same import path as a manually dropped archive so we get extraction
        # failure detection etc for free. also grabs name/author/category like MO2 does.
        if not self.nexus_api_key:
            return "Download failed: no Nexus API key set. Add one in Settings."
        if self.pipe is None:
            return "Download failed: pipeline.py could not be imported."

        client = nexus.NexusClient(self.nexus_api_key)
        game_domain = nxm_info.get("game_domain", "watchdogs2")
        mod_id = nxm_info.get("mod_id")
        file_id = nxm_info.get("file_id")

        display_info = None
        mod_name = f"mod-{mod_id}"
        try:
            display_info = client.get_mod_display_info(game_domain, mod_id)
            mod_name = display_info["name"]
        except nexus.NexusApiError as exc:
            self.ui_logger(f"WARN: Could not fetch mod info: {exc}")

        try:
            url = client.get_download_url(
                game_domain, mod_id, file_id,
                nxm_key=nxm_info.get("key"), nxm_expires=nxm_info.get("expires"),
            )
        except nexus.NexusApiError as exc:
            return f"Download failed: {exc}"

        # mod_name is API input, don't trust it as a path - strip bad chars and
        # verify the result actually resolves inside ARCHIVES_DIR
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", mod_name).strip(". ")[:80] or f"mod-{mod_id}"
        dest = ARCHIVES_DIR / f"{safe_name}-{mod_id}-{file_id}.zip"
        try:
            dest.resolve().relative_to(Path(ARCHIVES_DIR).resolve())
        except ValueError:
            return f"Download failed: refusing unsafe filename derived from '{mod_name}'."
        self.ui_logger(f"Downloading '{mod_name}' from Nexus...")
        try:
            client.download_file(str(url), str(dest), progress_cb=progress_cb)
        except nexus.NexusApiError as exc:
            return f"Download failed: {exc}"
        except Exception as exc:
            return f"Download failed: {exc}"

        self.ui_logger(f"Downloaded '{mod_name}'. Extracting...")
        result = self.process_dropped_archives([str(dest)])

        # cache folder = archive stem, same string dest was built from
        cache_folder = dest.stem
        if display_info and (Path(self.pipe.cache_dir) / cache_folder).is_dir():
            self.set_nexus_meta_for_mod(cache_folder, display_info)

        return result

    def process_dropped_archives(self, file_paths):
        if self.pipe is None:
            return "Extraction failed: pipeline.py could not be imported."
        self.ui_logger(f"Intercepted {len(file_paths)} dropped files. Processing...")
        succeeded, failed = [], []
        for raw_path in file_paths:
            src = Path(raw_path)
            mod_name = src.stem
            dest_archive = ARCHIVES_DIR / src.name
            
            try:
                shutil.copy2(src, dest_archive)
            except Exception as exc:
                self.ui_logger(f"WARN: Could not cache archive '{src.name}': {exc}")

            result = self.pipe.extract_mod(str(src), mod_name)
            landed = (Path(self.pipe.cache_dir) / mod_name).is_dir()
            if result is False or not landed:
                failed.append(mod_name)
            else:
                succeeded.append(mod_name)
                if self.nexus_api_key:
                    ok, detail = self.auto_identify_mod(mod_name)
                    if ok:
                        self.ui_logger(f"Identified '{mod_name}' as a Nexus mod: {detail}")
                    # not found = normal for non-Nexus mods

        if succeeded:
            self._invalidate_mod_caches()
        if failed and succeeded:
            return f"Extracted {len(succeeded)}, FAILED {len(failed)}: {', '.join(failed)}"
        if failed:
            return f"Extraction FAILED for {len(failed)} archive(s): {', '.join(failed)}"
        return f"Extraction complete. {len(succeeded)} mod(s) cached."

    def deploy_active_mods(self, mod_list):
        if self.pipe is None: return "Deployment failed."
        game_dir = Path(self.game_exe_path).parent.parent
        data_win64_dir = game_dir / "data_win64"
        if not data_win64_dir.exists():
            return "Deployment failed: Invalid game path."
        self.ui_logger(f"Deploying {len(mod_list)} mods to '{data_win64_dir}'...")
        success = self.pipe.deploy(mod_list, str(data_win64_dir),
                                   auto_reference=self.auto_reference,
                                   overrides=self.load_conflict_rules(),
                                   merging_enabled=self.merging_enabled)
        return "Deployment complete." if success else "Deployment failed. Check logs."

    def run_game(self, use_eac_bypass: bool = True, eac_method: int = 0):
        if use_eac_bypass and eac_method == 2:
            subprocess.Popen(["cmd", "/c", "start", "uplay://launch/2688/0"])
            return "Game launched via Ubisoft Connect."

        exe = Path(self.game_exe_path)
        if exe.exists():
            try:
                command = [str(exe)]
                if use_eac_bypass:
                    if eac_method == 0:
                        command.append("-eac_launcher")
                        self.ui_logger("Launching Watch_Dogs 2 (EAC Bypassed via -eac_launcher)...")
                    elif eac_method == 1:
                        command.append("-eac_index")
                        command.append("0")
                        self.ui_logger("Launching Watch_Dogs 2 (EAC Bypassed via -eac_index 0)...")
                else:
                    self.ui_logger("Launching Watch_Dogs 2 (Standard)...")
                subprocess.Popen(command, cwd=str(exe.parent))
                return "Game launched."
            except Exception as exc:
                self.ui_logger(f"Launch error: {exc}")
                return "Launch error."
        self.ui_logger("ERROR: Executable not found. Please set path in Settings.")
        return "Launch failed."

    def browse_exe_path(self, parent=None) -> Optional[str]:
        path, _ = QFileDialog.getOpenFileName(parent, "Locate WatchDogs2.exe", "", "Executable Files (*.exe)")
        if path:
            self.game_exe_path = os.path.normpath(path)
            self.save_config(path=self.game_exe_path)
            self.ui_logger(f"Updated game executable path to: {self.game_exe_path}")
            return self.game_exe_path
        return None

    def browse_for_archive(self, parent=None):
        files, _ = QFileDialog.getOpenFileNames(parent, "Select Mod Archives", "", "Mod Archives (*.zip *.rar *.7z *.fat);;All Files (*.*)")
        return files

    def export_logs(self, log_data: str):
        log_path = ROOT_DIR / "wd2_manager_logs.txt"
        log_path.write_text(log_data, encoding="utf-8")
        self.ui_logger(f"Logs exported to {log_path}")
        return "Success"

    def export_profile(self, dest_path: str):
        try:
            with zipfile.ZipFile(dest_path, 'w') as zf:
                if LOAD_ORDER_FILE.exists():
                    zf.write(LOAD_ORDER_FILE, LOAD_ORDER_FILE.name)
                if CONFLICT_RULES_FILE.exists():
                    zf.write(CONFLICT_RULES_FILE, CONFLICT_RULES_FILE.name)
            return "Profile exported successfully."
        except Exception as exc:
            return f"Failed to export profile: {exc}"

    def import_profile(self, src_path: str):
        allowed = {LOAD_ORDER_FILE.name, CONFLICT_RULES_FILE.name}
        try:
            with zipfile.ZipFile(src_path, 'r') as zf:
                imported = []
                for info in zf.infolist():
                    name = os.path.basename(info.filename)
                    if name not in allowed or info.filename != name:
                        continue  
                    target = WORKSPACE_DIR / name
                    with zf.open(info) as src, open(target, "wb") as dst:
                        dst.write(src.read())
                    imported.append(name)
            if not imported:
                return "Nothing to import -- zip had no recognized profile files."
            return f"Profile imported successfully ({', '.join(imported)})."
        except Exception as exc:
            return f"Failed to import profile: {exc}"


class Worker(QThread):
    result = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            self.result.emit(self.fn(*self.args, **self.kwargs))
        except Exception as exc:
            self.failed.emit(str(exc))


# -----------------------------------------------------------------------------
# Icons
# -----------------------------------------------------------------------------
class IconWidget(QWidget):
    def __init__(self, kind: str, color=WHITE, size: int = 18, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.color = QColor(color)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_icon(painter, self.kind, self.rect(), self.color)


def paint_icon(painter: QPainter, kind: str, rect: QRect, color: QColor):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(QPen(color, 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.save()
    painter.translate(rect.topLeft())
    w, h = rect.width(), rect.height()

    if kind == "play":
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygon([QPoint(5, 3), QPoint(w - 4, h // 2), QPoint(5, h - 3)]))
    elif kind == "drive":
        painter.drawRoundedRect(3, 5, w - 6, h - 10, 1.5, 1.5)
        painter.drawLine(5, 8, w - 5, 8)
        painter.drawLine(w - 7, h - 7, w - 4, h - 7)
    elif kind == "trash":
        draw_image_icon(painter, "icon_trash", QRect(0, 0, w, h), color)
        painter.drawLine(w - 9, 8, w - 9, h - 3)
    elif kind == "search":
        painter.drawEllipse(3, 3, 9, 9)
        painter.drawLine(11, 11, 15, 15)
    elif kind == "database":
        painter.drawEllipse(4, 2, w - 8, 5)
        painter.drawLine(4, 4, 4, h - 5)
        painter.drawArc(4, h - 8, w - 8, 6, 180 * 16, 180 * 16)
        painter.drawLine(w - 4, 4, w - 4, h - 5)
    elif kind == "plus":
        painter.setPen(QPen(color, 2))
        painter.drawLine(w // 2, 4, w // 2, h - 4)
        painter.drawLine(4, h // 2, w - 4, h // 2)
    elif kind == "grip":
        painter.setPen(QPen(color, 1))
        for y in (4, 8, 12):
            painter.drawPoint(6, y)
            painter.drawPoint(10, y)
    elif kind == "refresh":
        painter.drawArc(3, 3, w - 6, h - 6, 35 * 16, 280 * 16)
        painter.drawLine(w - 4, 5, w - 4, 10)
        painter.drawLine(w - 4, 5, w - 9, 5)
    elif kind == "filezip":
        painter.drawRect(4, 2, w - 8, h - 4)
        painter.drawLine(7, 4, 7, 7)
        painter.drawLine(7, 8, 7, 11)
        painter.drawLine(7, 12, 7, 14)

    painter.restore()


def icon_pixmap(kind: str, color: QColor, size: int = 16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    paint_icon(painter, kind, QRect(0, 0, size, size), QColor(color))
    painter.end()
    return QIcon(pm)


# -----------------------------------------------------------------------------
# Base visual widgets
# -----------------------------------------------------------------------------

class GlassPanel(QFrame):
    def __init__(self, opacity=PANEL_ALPHA, parent=None):
        super().__init__(parent)
        self.opacity = opacity
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(8, 8, 8, self.opacity))
        painter.setPen(QPen(QColor(31, 31, 31, 235), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        super().paintEvent(event)


class HudButton(QPushButton):
    def __init__(
        self,
        text: str,
        icon_kind: str,
        bg_color: QColor,
        hover_color: QColor,
        text_color: QColor,
        cut_size=16,
        parent=None,
        center_text_only: bool = False,
    ):
        super().__init__(parent)
        self.button_text = text
        self.icon_kind = icon_kind
        self.bg_color = QColor(bg_color)
        self.hover_color = QColor(hover_color)
        self.current_bg = QColor(bg_color)
        self.text_color = QColor(text_color)
        self.cut_size = cut_size
        self.center_text_only = center_text_only
        self.badge_text = ""
        self.busy = False
        self._is_pressed = False
        self._scale = 1.0

        self.spin_angle = 0
        self.spin_timer = QTimer(self)
        self.spin_timer.timeout.connect(self.update_spin)

        self.bg_anim = QVariantAnimation(self)
        self.bg_anim.setDuration(150)
        self.bg_anim.valueChanged.connect(self._on_bg_anim)

        self.scale_anim = QVariantAnimation(self)
        self.scale_anim.setDuration(100)
        self.scale_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.scale_anim.valueChanged.connect(self._on_scale_anim)

        self.setCursor(interactive_cursor())
        self.setFlat(True)
        self.setStyleSheet("border: none; background: transparent;")

    def _on_bg_anim(self, val):
        self.current_bg = val
        self.update()

    def _on_scale_anim(self, val):
        self._scale = val
        self.update()

    def enterEvent(self, event):
        self.bg_anim.stop()
        self.bg_anim.setStartValue(self.current_bg)
        self.bg_anim.setEndValue(self.hover_color)
        self.bg_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.bg_anim.stop()
        self.bg_anim.setStartValue(self.current_bg)
        self.bg_anim.setEndValue(self.bg_color)
        self.bg_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_pressed = True
            self.scale_anim.stop()
            self.scale_anim.setStartValue(self._scale)
            self.scale_anim.setEndValue(0.96)
            self.scale_anim.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_pressed = False
            self.scale_anim.stop()
            self.scale_anim.setStartValue(self._scale)
            self.scale_anim.setEndValue(1.0)
            self.scale_anim.start()
        super().mouseReleaseEvent(event)

    def update_spin(self):
        self.spin_angle = (self.spin_angle + 15) % 360
        self.update()

    def set_busy(self, busy: bool, text: str = "DEPLOYING..."):
        if busy:
            if not hasattr(self, "_idle_text"):
                self._idle_text = self.button_text
            self.button_text = text
            if not hasattr(self, "_old_icon"):
                self._old_icon = self.icon_kind
            self.icon_kind = "spinner"
            self.spin_timer.start(30)
        else:
            self.button_text = getattr(self, "_idle_text", self.button_text)
            self.icon_kind = getattr(self, "_old_icon", self.icon_kind)
            self.spin_timer.stop()
        self.busy = busy
        self.setEnabled(not busy)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        
        w, h = self.width(), self.height()

        painter.save()
        if getattr(self, '_is_pressed', False) and not self.busy:
            painter.translate(w / 2, h / 2)
            painter.scale(self._scale, self._scale)
            painter.translate(-w / 2, -h / 2)

        c = min(self.cut_size, h)
        poly = QPolygon([
            QPoint(0, 0),
            QPoint(w, 0),
            QPoint(w, h - c),
            QPoint(w - c, h),
            QPoint(0, h),
        ])

        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.setBrush(QBrush(self.current_bg))
        painter.drawPolygon(poly)

        dot_color = (
            QColor(0, 0, 0, 28)
            if self.text_color == QColor("#000000")
            else QColor(255, 255, 255, 24)
        )
        painter.setPen(QPen(dot_color, 1))
        for x in range(4, w - 3, 6):
            for y in range(4, h - 3, 6):
                painter.drawPoint(x, y)

        text_font = QFont(self.font())
        text_font.setWeight(QFont.Weight.Bold)
        text_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.5)

        badge_font = None
        badge_rect = QRect()
        badge_width = 0
        if self.badge_text:
            badge_font = QFont(text_font)
            badge_font.setPixelSize(max(8, text_font.pixelSize() - 3))
            badge_font.setWeight(QFont.Weight.Normal)
            badge_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.7)
            badge_metrics = QFontMetrics(badge_font)
            badge_width = max(58, badge_metrics.horizontalAdvance(self.badge_text) + 16)
            badge_rect = QRect(
                w - badge_width - 12,
                max(0, (h - 22) // 2),
                badge_width,
                22,
            )

        painter.setFont(text_font)
        text_metrics = QFontMetrics(text_font)
        text_width = text_metrics.horizontalAdvance(self.button_text)

        icon_width = 18 if self.icon_kind else 0
        icon_gap = 10 if self.icon_kind else 0
        badge_gap = 14 if self.badge_text else 0
        group_width = icon_width + icon_gap + text_width + badge_gap + badge_width
        group_x = max(0, (w - group_width) // 2)
        center_y = h // 2

        if self.center_text_only:
            # badge eats space on the right, so centering against the full button
            # width looks centered on paper but is visually off. reserve the badge's
            # width and center icon+text as one group in what's left (centering just
            # the text isn't enough, the icon ends up hanging too far left)
            badge_reserved = (badge_width + 12 + badge_gap) if self.badge_text else 0
            available_w = max(0, w - badge_reserved)
            # icon-only button: drop the icon-text gap or it centers as if there's an invisible label after it
            has_text = bool(self.button_text)
            local_group_width = icon_width + (icon_gap if has_text else 0) + text_width
            group_left = max(0, (available_w - local_group_width) // 2)
            if self.icon_kind:
                ix = group_left
                text_x = ix + icon_width + (icon_gap if has_text else 0)
            else:
                ix = group_left
                text_x = group_left
        elif self.icon_kind:
            text_x = group_x + icon_width + icon_gap
            ix = group_x
        else:
            text_x = group_x
            ix = group_x

        if self.icon_kind:
            iy = center_y - 9
            painter.setPen(QPen(self.text_color, 1.4))
            painter.setBrush(QBrush(self.text_color))

            if self.icon_kind == "play":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPolygon(QPolygon([
                    QPoint(ix + 2, iy + 1),
                    QPoint(ix + 15, iy + 9),
                    QPoint(ix + 2, iy + 17),
                ]))
            elif self.icon_kind == "drive":
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(ix + 1, iy + 4, 16, 11, 1.5, 1.5)
                painter.drawLine(ix + 3, iy + 7, ix + 15, iy + 7)
                painter.drawLine(ix + 12, iy + 11, ix + 14, iy + 11)
            elif self.icon_kind == "refresh":
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawArc(ix + 2, iy + 2, 14, 14, 35 * 16, 280 * 16)
                painter.drawLine(ix + 15, iy + 4, ix + 15, iy + 9)
                painter.drawLine(ix + 15, iy + 4, ix + 10, iy + 4)
            elif self.icon_kind == "trash":
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(ix + 4, iy + 6, 10, 11)
                painter.drawLine(ix + 3, iy + 5, ix + 15, iy + 5)
                painter.drawLine(ix + 7, iy + 3, ix + 11, iy + 3)
            elif self.icon_kind == "filezip":
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(ix + 2, iy + 1, 14, 16)
                painter.drawLine(ix + 5, iy + 3, ix + 5, iy + 6)
                painter.drawLine(ix + 5, iy + 7, ix + 5, iy + 10)
                painter.drawLine(ix + 5, iy + 11, ix + 5, iy + 13)
            elif self.icon_kind in ("img_refresh", "img_eye", "img_usb", "img_aperture",
                                    "img_heart", "img_trash"):
                # tag is "img_usb" but the file on disk is "icon_usb.png", swap the prefix
                draw_image_icon(painter, "icon_" + self.icon_kind[4:], QRect(ix, iy, 18, 18), self.text_color)
            elif self.icon_kind == "spinner":
                painter.save()
                painter.translate(ix + 9, iy + 9)
                painter.rotate(self.spin_angle)
                painter.translate(-(ix + 9), -(iy + 9))
                painter.setPen(QPen(self.text_color, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawArc(ix + 2, iy + 2, 14, 14, 25 * 16, 285 * 16)
                painter.restore()

            if not self.center_text_only:
                text_x = ix + icon_width + icon_gap

        painter.setFont(text_font)
        painter.setPen(self.text_color)
        painter.drawText(
            QRect(text_x, 0, text_width + 3, h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.button_text,
        )

        if self.badge_text and badge_font is not None:
            painter.setBrush(QBrush(QColor(0, 0, 0, 95)))
            painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
            painter.drawRect(badge_rect)

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setFont(badge_font)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(
                badge_rect.adjusted(3, 0, -3, 0),
                Qt.AlignmentFlag.AlignCenter,
                self.badge_text,
            )

        painter.restore()


class PlusButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hovered = False
        self._is_pressed = False
        self._scale = 1.0
        
        self.scale_anim = QVariantAnimation(self)
        self.scale_anim.setDuration(100)
        self.scale_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.scale_anim.valueChanged.connect(self._on_scale_anim)
        
        self.setFixedSize(48, 48)
        self.setCursor(interactive_cursor())
        self.setStyleSheet("background: transparent; border: none;")

    def _on_scale_anim(self, val):
        self._scale = val
        self.update()

    def enterEvent(self, e):
        self.hovered = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hovered = False
        self.update()
        super().leaveEvent(e)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_pressed = True
            self.scale_anim.stop()
            self.scale_anim.setStartValue(self._scale)
            self.scale_anim.setEndValue(0.92)
            self.scale_anim.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_pressed = False
            self.scale_anim.stop()
            self.scale_anim.setStartValue(self._scale)
            self.scale_anim.setEndValue(1.0)
            self.scale_anim.start()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        
        painter.save()
        painter.translate(w / 2, h / 2)
        painter.scale(self._scale, self._scale)
        painter.translate(-w / 2, -h / 2)
            
        c = 14
        poly = QPolygon([QPoint(1, 1), QPoint(w - 1, 1), QPoint(w - 1, h - c), QPoint(w - c, h - 1), QPoint(1, h - 1)])
        painter.setBrush(QBrush(QColor(255, 255, 255, 230) if self.hovered else QColor(0, 0, 0, 80)))
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawPolygon(poly)
        color = QColor(0, 0, 0) if self.hovered else QColor(255, 255, 255)
        painter.setPen(QPen(color, 2))
        painter.drawLine(w // 2, 15, w // 2, h - 15)
        painter.drawLine(15, h // 2, w - 15, h // 2)
        painter.restore()


class TickCheckBox(QCheckBox):
    INDICATOR = 20

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        painter = QPainter(self)
        box = QRect(0, 0, self.INDICATOR, self.INDICATOR)
        box.moveCenter(QPoint(self.INDICATOR // 2, self.height() // 2))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(CYAN, max(1.6, box.width() * 0.12))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x, y, s = box.x(), box.y(), box.width()
        painter.drawPolyline(QPolygon([
            QPoint(int(x + s * 0.24), int(y + s * 0.52)),
            QPoint(int(x + s * 0.43), int(y + s * 0.70)),
            QPoint(int(x + s * 0.77), int(y + s * 0.30)),
        ]))


class TriStateBox(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = 0
        self.setFixedSize(16, 16)
        self.setCursor(interactive_cursor())

    def set_state(self, state: int):
        if state != self.state:
            self.state = state
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        box = QRect(0, 0, 15, 15)
        if self.state == 2:
            draw_checkbox(painter, box, True, idle_border="#777777")
        elif self.state == 1:
            draw_checkbox(painter, box, False, idle_border="#00A3E0")
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.fillRect(QRect(box.left() + 4, box.center().y(), box.width() - 8, 2), CYAN)
        else:
            draw_checkbox(painter, box, False, idle_border="#777777")


class DangerButton(QPushButton):
    IDLE = QColor("#888888")
    IDLE_BORDER = QColor("#222222")
    HOT = QColor("#FF4444")
    HOT_BORDER = QColor("#FF0000")
    OFF = QColor("#444444")
    OFF_BORDER = QColor("#181818")

    def __init__(self, text, fonts: FontBook, parent=None):
        super().__init__(text, parent)
        self.fonts = fonts
        self._hot = 0.0
        self._scale = 1.0
        self.setCursor(interactive_cursor())
        self.setMinimumHeight(30)
        self.setFlat(True)
        self.setStyleSheet("background:transparent; border:none;")
        self._anim = QPropertyAnimation(self, b"hot", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        self.scale_anim = QVariantAnimation(self)
        self.scale_anim.setDuration(100)
        self.scale_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.scale_anim.valueChanged.connect(self._on_scale_anim)

    def get_hot(self):
        return self._hot

    def set_hot(self, value):
        self._hot = float(value)
        self.update()

    hot = pyqtProperty(float, fget=get_hot, fset=set_hot)

    def _on_scale_anim(self, val):
        self._scale = val
        self.update()

    def _animate_to(self, target):
        self._anim.stop()
        self._anim.setStartValue(self._hot)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event):
        if self.isEnabled():
            self._animate_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_to(0.0)
        super().leaveEvent(event)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.scale_anim.stop()
            self.scale_anim.setStartValue(self._scale)
            self.scale_anim.setEndValue(0.96)
            self.scale_anim.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.scale_anim.stop()
            self.scale_anim.setStartValue(self._scale)
            self.scale_anim.setEndValue(1.0)
            self.scale_anim.start()
        super().mouseReleaseEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._anim.stop()
            self._hot = 0.0
            self._scale = 1.0
        super().changeEvent(event)

    def paintEvent(self, event):
        t = max(0.0, min(1.0, self._hot))
        on = self.isEnabled()
        base = self.IDLE if on else self.OFF
        base_border = self.IDLE_BORDER if on else self.OFF_BORDER
        fg = StatusBadge._mix(base, self.HOT, t)
        border = StatusBadge._mix(base_border, self.HOT_BORDER, t)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        
        painter.save()
        painter.translate(w / 2, h / 2)
        painter.scale(self._scale, self._scale)
        painter.translate(-w / 2, -h / 2)
        
        rect = QRect(0, 0, w, h).adjusted(0, 0, -1, -1)

        if on:
            painter.fillRect(rect, QColor(255, 0, 0, int(8 + 18 * t)))
        painter.setPen(QPen(border, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        painter.setFont(self.font())
        metrics = QFontMetrics(self.font())
        text_width = metrics.horizontalAdvance(self.text())
        
        icon_width = 16
        icon_gap = 8
        total_width = icon_width + icon_gap + text_width
        
        start_x = rect.left() + (rect.width() - total_width) // 2
        
        icon_box = QRect(int(start_x), int(rect.center().y() - 8), icon_width, icon_width)
        paint_icon(painter, "trash", icon_box, fg)

        text_rect = QRect(int(start_x + icon_width + icon_gap), int(rect.top()), int(text_width), int(rect.height()))
        painter.setPen(fg)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        
        painter.restore()


class StatusBadge(QWidget):
    DISABLED_TEXT = QColor("#777777")
    DISABLED_BORDER = QColor("#222222")

    def __init__(self, text, active, fonts: FontBook, parent=None):
        super().__init__(parent)
        self.active = bool(active)
        self.fonts = fonts
        self._blend = 1.0 if self.active else 0.0
        self.setFixedSize(78, 24)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._font = make_font(fonts.univers, 10, QFont.Weight.Normal, 1.0)

        self._anim = QPropertyAnimation(self, b"blend", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_blend(self):
        return self._blend

    def set_blend(self, value):
        self._blend = float(value)
        self.update()

    blend = pyqtProperty(float, fget=get_blend, fset=set_blend)

    def set_state(self, active: bool, any_selected: bool = False):
        active = bool(active)
        if active == self.active:
            return
        self.active = active
        self._anim.stop()
        self._anim.setStartValue(self._blend)
        self._anim.setEndValue(1.0 if active else 0.0)
        self._anim.start()

    @staticmethod
    def _mix(a: QColor, b: QColor, t: float) -> QColor:
        return QColor(
            int(a.red() + (b.red() - a.red()) * t),
            int(a.green() + (b.green() - a.green()) * t),
            int(a.blue() + (b.blue() - a.blue()) * t),
        )

    def paintEvent(self, event):
        t = max(0.0, min(1.0, self._blend))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect().adjusted(0, 0, -1, -1)

        painter.fillRect(rect, QColor(0, 163, 224, int(14 * t)))
        painter.setPen(QPen(self._mix(self.DISABLED_BORDER, CYAN, t), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        painter.setFont(self._font)
        if t < 1.0:
            c = QColor(self.DISABLED_TEXT)
            c.setAlpha(int(255 * (1.0 - t)))
            painter.setPen(c)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "DISABLED")
        if t > 0.0:
            c = QColor(CYAN)
            c.setAlpha(int(255 * t))
            painter.setPen(c)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "QUEUED")


def draw_checkbox(painter: QPainter, box: QRect, checked: bool,
                  accent: QColor = None, idle_border: str = "#444444"):
    accent = accent or CYAN
    painter.save()

    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    if checked:
        painter.setPen(QPen(accent, 1))
        painter.setBrush(QBrush(QColor(accent.red(), accent.green(), accent.blue(), 28)))
    else:
        painter.setPen(QPen(QColor(idle_border), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(box)

    if checked:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(accent, max(1.6, box.width() * 0.12))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        x, y, s = box.x(), box.y(), box.width()
        painter.drawPolyline(QPolygon([
            QPoint(int(x + s * 0.24), int(y + s * 0.52)),
            QPoint(int(x + s * 0.43), int(y + s * 0.70)),
            QPoint(int(x + s * 0.77), int(y + s * 0.30)),
        ]))

    painter.restore()


class ModRow(QWidget):
    clicked_with_modifiers = pyqtSignal(object, object)
    checkbox_toggled = pyqtSignal(object, object)
    drag_started = pyqtSignal(object, QPoint)
    drag_moved = pyqtSignal(object, QPoint)
    drag_finished = pyqtSignal(object)
    link_requested = pyqtSignal(object)
    CHECKBOX_ZONE = 68

    def __init__(self, mod: dict, fonts: FontBook, parent=None):
        super().__init__(parent)
        self.mod = dict(mod)
        self.fonts = fonts
        self.selected = bool(mod.get("active", False))
        self.viewing = False
        self._press_pos = None
        self._gesture_dragging = False
        self._dragging = False
        
        self._hover_alpha = 0
        self.hover_anim = QVariantAnimation(self)
        self.hover_anim.setDuration(150)
        self.hover_anim.valueChanged.connect(self._on_hover_changed)

        self._is_pressed = False

        self.setMouseTracking(True)
        self.setFixedHeight(62)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(interactive_cursor())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        self.grip = IconWidget("grip", QColor("#595959"), 16, self)
        layout.addWidget(self.grip)

        self.checkbox = QWidget(self)
        self.checkbox.setFixedSize(16, 16)
        self.checkbox.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.checkbox)

        info = QVBoxLayout()
        info.setSpacing(1)
        info.setContentsMargins(0, 0, 0, 0)

        display_name = mod.get("nexus_name") or mod.get("name", "")
        self.name_label = QLabel(display_name, self)
        self.name_label.setFont(make_font(fonts.univers, 13, QFont.Weight.Bold))
        self.name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if mod.get("nexus_name"):
            # Keep the real folder name visible on hover; deploy/merge/conflict scan still key on it.
            self.name_label.setToolTip(f"Cache folder: {mod.get('name', '')}")

        meta_parts = []
        if mod.get("nexus_author"):
            meta_parts.append(f"by {mod['nexus_author']}")
        if mod.get("nexus_category"):
            meta_parts.append(mod["nexus_category"])
        if meta_parts:
            meta_parts.append(f"{mod.get('files', 0)} FILES")
            meta_text = "  //  ".join(meta_parts)
        else:
            meta_text = f"{mod.get('category', 'MOD DATA')}  //  {mod.get('files', 0)} FILES MODIFIED"
        self.meta_label = QLabel(meta_text, self)
        self.meta_label.setFont(make_font(fonts.pexico, 9, QFont.Weight.Normal, 0.8))
        self.meta_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        info.addWidget(self.name_label)
        info.addWidget(self.meta_label)
        layout.addLayout(info, 1)

        self.nexus_link = QLabel("NEXUS \u2197", self)
        self.nexus_link.setFont(make_font(fonts.univers, 10, QFont.Weight.Bold, 0.6))
        self.nexus_link.setStyleSheet("color:#00A3E0; background:transparent;")
        self.nexus_link.setCursor(interactive_cursor())
        self.nexus_link.setToolTip("Open this mod's page on Nexus Mods")
        self.nexus_link.mousePressEvent = self._open_nexus_page
        self.nexus_link.setVisible(bool(mod.get("nexus_mod_id")))
        layout.addWidget(self.nexus_link)

        self.update_badge = QLabel("UPDATE", self)
        self.update_badge.setFont(make_font(fonts.univers, 9, QFont.Weight.Bold, 1.0))
        self.update_badge.setStyleSheet(
            "color:#FF6B00; background:rgba(255,107,0,0.12); border:1px solid #FF6B00; padding:1px 6px;"
        )
        latest = mod.get("nexus_latest_version")
        self.update_badge.setToolTip(f"Newer version available: {latest}" if latest else "Newer version available")
        self.update_badge.setVisible(bool(mod.get("nexus_update_available")))
        layout.addWidget(self.update_badge)

        self.badge = StatusBadge("", self.selected, fonts, self)
        layout.addWidget(self.badge)
        self.update_visuals()

    def _on_hover_changed(self, val):
        self._hover_alpha = val
        self.update()

    def set_dragging(self, dragging: bool):
        if self._dragging == dragging: return
        self._dragging = dragging
        self.update()

    def set_viewing(self, viewing: bool):
        if self.viewing == viewing: return
        self.viewing = viewing
        self.update()

    def set_selected(self, selected: bool):
        self.selected = selected
        self.mod["active"] = selected
        self.badge.set_state(selected)
        self.update_visuals()

    def update_visuals(self):
        if self.selected:
            self.name_label.setStyleSheet("color:#FFFFFF; background:transparent;")
            self.meta_label.setStyleSheet("color:#777777; background:transparent;")
        else:
            self.name_label.setStyleSheet("color:#777777; background:transparent;")
            self.meta_label.setStyleSheet("color:#555555; background:transparent;")
        self.update()

    def enterEvent(self, event):
        self.hover_anim.stop()
        self.hover_anim.setStartValue(self._hover_alpha)
        self.hover_anim.setEndValue(90)
        self.hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_anim.stop()
        self.hover_anim.setStartValue(self._hover_alpha)
        self.hover_anim.setEndValue(0)
        self.hover_anim.start()
        
        self._is_pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            if self._press_pos.x() > self.CHECKBOX_ZONE:
                self._is_pressed = True
                self.update()
            else:
                event.accept()
                return
            self.clicked_with_modifiers.emit(self, event.modifiers())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        current = event.position().toPoint()
        distance = (current - self._press_pos).manhattanLength()
        if distance >= QApplication.startDragDistance():
            self._is_pressed = False
            self.update()
            if not self._gesture_dragging:
                self._gesture_dragging = True
                self.drag_started.emit(self, event.globalPosition().toPoint())
            self.drag_moved.emit(self, event.globalPosition().toPoint())
            event.accept()
            return
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_pressed = False
            self.update()
            if self._gesture_dragging:
                self.drag_finished.emit(self)
            elif self._press_pos is not None and self._press_pos.x() <= self.CHECKBOX_ZONE:
                self.checkbox_toggled.emit(self, event.modifiers())
            self._press_pos = None
            self._gesture_dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _open_nexus_page(self, event):
        mod_id = self.mod.get("nexus_mod_id")
        if mod_id:
            QDesktopServices.openUrl(QUrl(f"https://www.nexusmods.com/watchdogs2/mods/{mod_id}"))
        event.accept()  # don't let this bubble into the row's own click handling

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#111111; color:#DDDDDD; border:1px solid #2A2A2A; padding:4px;}"
            "QMenu::item{padding:6px 20px;}"
            "QMenu::item:selected{background:#00A3E0; color:#000000;}"
        )
        label = "Re-link to Nexus Mod..." if self.mod.get("nexus_name") else "Link to Nexus Mod..."
        action = menu.addAction(label)
        action.triggered.connect(lambda: self.link_requested.emit(self))
        menu.exec(event.globalPos())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        
        w, h = self.width(), self.height()
            
        if self._dragging: bg = QColor(0, 163, 224, 40)
        elif getattr(self, "_is_pressed", False): bg = QColor(0, 163, 224, 22)
        elif self.selected: bg = QColor(0, 0, 0, 150)
        else: bg = QColor(0, 0, 0, int(self._hover_alpha))

        painter.fillRect(0, 0, w, h, bg)
        painter.setPen(QPen(QColor("#202020"), 1))
        painter.drawLine(0, h - 1, w, h - 1)

        box = QRect(0, 0, 16, 16)
        box.moveCenter(self.checkbox.geometry().center())
        draw_checkbox(painter, box, self.selected)

        if self.viewing:
            painter.fillRect(0, 0, 3, h, CYAN)


class SmoothScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.target_scroll = 0
        self.scroll_anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self.scroll_anim.setEasingCurve(QEasingCurve.Type.OutExpo)
        self.scroll_anim.setDuration(400)
        self.setStyleSheet("QScrollArea{background:transparent;border:none;}" + SCROLLBAR_QSS)
        # viewport() doesn't reliably inherit the window's cursor, without this
        # it flickers between custom cursor and default arrow near scroll areas
        app_cursor = get_app_cursor()
        if app_cursor is not None:
            self.setCursor(app_cursor)
            self.viewport().setCursor(app_cursor)

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        delta = event.angleDelta().y()
        
        if self.scroll_anim.state() != QPropertyAnimation.State.Running:
            self.target_scroll = bar.value()
        
        self.target_scroll = max(bar.minimum(), min(bar.maximum(), self.target_scroll - delta))
        
        self.scroll_anim.stop()
        self.scroll_anim.setStartValue(bar.value())
        self.scroll_anim.setEndValue(self.target_scroll)
        self.scroll_anim.start()


class ModListArea(SmoothScrollArea):
    mod_clicked = pyqtSignal(object, object)
    mod_checkbox_toggled = pyqtSignal(object, object)
    mod_drag_started = pyqtSignal(object, QPoint)
    mod_drag_moved = pyqtSignal(object, QPoint)
    mod_drag_finished = pyqtSignal(object)
    mod_link_requested = pyqtSignal(object)

    def __init__(self, fonts: FontBook, parent=None):
        super().__init__(parent)
        self.fonts = fonts
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.viewport().setStyleSheet("background:transparent;")
        self.container = QWidget()
        self.container.setStyleSheet("background:transparent;")
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(0, 0, 0, 90)
        self.layout.setSpacing(0)
        self.setWidget(self.container)
        self.rows = []
        self.diagnostics = None
        self._row_anims = {}          

    def clear_drag_states(self):
        for row in self.rows: row.set_dragging(False)

    def clear_viewing_states(self):
        for row in self.rows: row.set_viewing(False)

    def clear_rows(self):
        self.stop_row_animations()
        for row in self.rows: row.deleteLater()
        self.rows.clear()
        self.diagnostics = None
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def populate(self, mods):
        self.clear_rows()
        for mod in mods:
            row = ModRow(mod, self.fonts, self.container)
            row.clicked_with_modifiers.connect(self.mod_clicked.emit)
            row.checkbox_toggled.connect(self.mod_checkbox_toggled.emit)
            row.link_requested.connect(self.mod_link_requested.emit)
            row.drag_started.connect(self.mod_drag_started.emit)
            row.drag_moved.connect(self.mod_drag_moved.emit)
            row.drag_finished.connect(self.mod_drag_finished.emit)
            self.rows.append(row)
            self.layout.addWidget(row)

        self.layout.addStretch(1)
        self.diagnostics = DiagnosticsWidget(self.fonts, self.container)
        self.layout.addWidget(self.diagnostics)

    def update_diagnostics(self, active_count: int, file_count: int, xml_conflicts: int = 0, status: str = None):
        if self.diagnostics:
            self.diagnostics.update_values(active_count, file_count, xml_conflicts, status)

    def ordered_mod_names(self):
        return [row.mod["name"] for row in self.rows]

    def enabled_mod_names(self):
        return [row.mod["name"] for row in self.rows if row.selected]

    def viewing_mod_names(self):
        return [row.mod["name"] for row in self.rows if row.viewing]

    def move_row(self, row: ModRow, target_index: int):
        try: current = self.rows.index(row)
        except ValueError: return
        if current == target_index: return
        target_index = max(0, min(target_index, len(self.rows) - 1))

        before = {r: r.pos() for r in self.rows}

        self.rows.pop(current)
        self.rows.insert(target_index, row)
        self.layout.removeWidget(row)
        self.layout.insertWidget(target_index, row)
        self.layout.activate()          
        row.raise_()

        for other in self.rows:
            if other is row:
                continue              
            start = before.get(other)
            end = other.pos()
            if start is None or start == end:
                continue

            existing = self._row_anims.get(other)
            if existing is not None:
                existing.stop()

            anim = QPropertyAnimation(other, b"pos", self)
            anim.setDuration(160)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(start)
            anim.setEndValue(end)
            anim.finished.connect(lambda w=other: self._row_anims.pop(w, None))
            self._row_anims[other] = anim
            anim.start()

    def stop_row_animations(self):
        for anim in list(self._row_anims.values()):
            anim.stop()
        self._row_anims.clear()


class DiagnosticsWidget(QFrame):
    def __init__(self, fonts: FontBook, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent; border-top:1px solid #141414;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 18, 24, 20)
        layout.setSpacing(5)
        self.head = QLabel("// SYSTEM_DIAGNOSTICS")
        self.head.setFont(make_font(fonts.pexico, 10, QFont.Weight.Normal, 1.3))
        self.head.setStyleSheet("color:#00A3E0; background:transparent; border:none;")
        layout.addWidget(self.head)
        self.labels = []
        for text in [
            "> ACTIVE_MODS: 00",
            "> XML_CONFLICTS: 00",
            "> TOTAL_FILES_QUEUED: 000",
            "> PATCH3_INTEGRITY: VERIFIED",
            "> WAITING FOR DEPLOYMENT COMMAND...",
        ]:
            lbl = QLabel(text)
            lbl.setFont(make_font(fonts.pexico, 9, QFont.Weight.Normal, 0.8))
            lbl.setStyleSheet("color:#9A9A9A; background:transparent; border:none;")
            layout.addWidget(lbl)
            self.labels.append(lbl)

        self.status_label = self.labels[-1]
        self.status_label.setStyleSheet("color:#FFFFFF; background:transparent; border:none;")

        self.pulse_effect = QGraphicsOpacityEffect(self.status_label)
        self.status_label.setGraphicsEffect(self.pulse_effect)
        self.pulse_anim = QPropertyAnimation(self.pulse_effect, b"opacity", self)
        self.pulse_anim.setDuration(1800)
        self.pulse_anim.setStartValue(1.0)
        self.pulse_anim.setKeyValueAt(0.5, 0.35)
        self.pulse_anim.setEndValue(1.0)
        self.pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.pulse_anim.setLoopCount(-1)
        self.pulse_anim.start()

    def update_values(self, active: int, files: int, conflicts: int, status: str = None):
        self.labels[0].setText(f"> ACTIVE_MODS: {active:02d}")
        self.labels[1].setText(f"> XML_CONFLICTS: {conflicts:02d}")
        self.labels[2].setText(f"> TOTAL_FILES_QUEUED: {files:03d}")
        if status is None: return
        self.status_label.setText(status)
        if "WAITING" in status:
            self.status_label.setStyleSheet("color:#FFFFFF; background:transparent; border:none;")
            self.pulse_anim.start()
        else:
            self.pulse_anim.stop()
            self.pulse_effect.setOpacity(1.0)
            self.status_label.setStyleSheet("color:#00A3E0; background:transparent; border:none;")


class BlendedGifLabel(QLabel):
    def __init__(self, size: int = 64, boost: int = 3, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setStyleSheet("background:transparent; border:none; padding:0;")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._boost = boost
        self._movie = None
        self._frame = None

    def set_movie(self, movie: QMovie):
        if self._movie:
            self._movie.stop()
            try: self._movie.frameChanged.disconnect(self._on_frame)
            except TypeError: pass
        self._movie = movie
        if movie:
            movie.setScaledSize(QSize(self.width(), self.height()))
            movie.frameChanged.connect(self._on_frame)

    def start(self):
        if self._movie: self._movie.start()

    def stop(self):
        if self._movie: self._movie.stop()

    def _on_frame(self, _index):
        pm = self._movie.currentPixmap()
        if pm.isNull(): return
        frame = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        gray = frame.convertToFormat(QImage.Format.Format_Grayscale8)
        frame.setAlphaChannel(gray)
        self._frame = frame
        self.update()

    def paintEvent(self, event):
        if self._frame is None: return
        painter = QPainter(self)
        target = QRect(0, 0, self.width(), self.height())
        for _ in range(max(1, self._boost)):
            painter.drawImage(target, self._frame)


class DropOverlay(QWidget):
    def __init__(self, fonts: FontBook, parent=None):
        super().__init__(parent)
        self.fonts = fonts
        self.mode = "drop"          
        self.busy_text = "WORKING..."
        self.busy_detail = ""
        self._spin_angle = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.timeout.connect(self._advance_spin)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.0)
        self.fade_anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_anim.setDuration(200)
        self.fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade_anim.finished.connect(self._on_fade_finished)
        
        self.hide()

    def _advance_spin(self):
        self._spin_angle = (self._spin_angle + 12) % 360
        self.update()

    def show_drop(self):
        self.mode = "drop"
        self._spin_timer.stop()
        self.show()
        self.fade_anim.stop()
        self.fade_anim.setStartValue(self.opacity_effect.opacity())
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.start()

    def show_busy(self, text="WORKING...", detail=""):
        self.mode = "busy"
        self.busy_text = text
        self.busy_detail = detail
        if not self._spin_timer.isActive():
            self._spin_timer.start(30)
        self.show()
        self.fade_anim.stop()
        self.fade_anim.setStartValue(self.opacity_effect.opacity())
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.start()

    def hide_overlay(self):
        self._spin_timer.stop()
        self.fade_anim.stop()
        self.fade_anim.setStartValue(self.opacity_effect.opacity())
        self.fade_anim.setEndValue(0.0)
        self.fade_anim.start()

    def _on_fade_finished(self):
        if self.opacity_effect.opacity() == 0.0:
            self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 205))

        if self.mode == "busy":
            self._paint_busy(painter)
            return

        painter.setPen(QPen(CYAN, 3, Qt.PenStyle.DashLine))
        painter.drawRect(self.rect().adjusted(18, 18, -18, -18))

        cx = self.width() // 2
        cy = self.height() // 2 - 70
        painter.setPen(QPen(CYAN, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(cx - 18, cy - 12, 36, 24)
        painter.drawLine(cx - 18, cy - 4, cx + 18, cy - 4)
        painter.drawLine(cx - 5, cy + 1, cx + 5, cy + 1)

        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(make_font(self.fonts.univers, 26, QFont.Weight.Bold, 2.5))
        painter.drawText(QRect(0, self.height() // 2 - 55, self.width(), 45), Qt.AlignmentFlag.AlignCenter, "DROP ARCHIVE TO IMPORT")
        painter.setPen(CYAN)
        painter.setFont(make_font(self.fonts.pexico, 12, QFont.Weight.Normal, 1.6))
        painter.drawText(QRect(0, self.height() // 2 + 2, self.width(), 30), Qt.AlignmentFlag.AlignCenter, ".ZIP // .RAR // .7Z // .FAT")

    def _paint_busy(self, painter):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx = self.width() // 2
        cy = self.height() // 2 - 46

        painter.save()
        painter.translate(cx, cy)
        painter.rotate(self._spin_angle)
        painter.setPen(QPen(CYAN, 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(-22, -22, 44, 44, 25 * 16, 285 * 16)
        painter.restore()

        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(make_font(self.fonts.univers, 22, QFont.Weight.Bold, 2.2))
        painter.drawText(QRect(0, self.height() // 2 - 6, self.width(), 40),
                         Qt.AlignmentFlag.AlignCenter, self.busy_text)
        if self.busy_detail:
            painter.setPen(QColor("#888888"))
            painter.setFont(make_font(self.fonts.pexico, 11, QFont.Weight.Normal, 1.2))
            painter.drawText(QRect(40, self.height() // 2 + 34, self.width() - 80, 30),
                             Qt.AlignmentFlag.AlignCenter, self.busy_detail)


class AnimatedNav(QWidget):
    tab_changed = pyqtSignal(int)

    def __init__(self, tabs, fonts: FontBook, parent=None):
        super().__init__(parent)
        self.fonts = fonts
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(62)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(28)
        self.buttons = []
        self.indicator = QFrame(self)
        self.indicator.setStyleSheet("background:#00A3E0;")
        self.indicator.setFixedHeight(2)
        self.current_index = 0

        for i, name in enumerate(tabs):
            active = (i == 0)
            btn = QPushButton(name.upper())
            btn.setFlat(True)
            btn.setCursor(interactive_cursor())
            btn.setFont(make_font(fonts.univers, 15, QFont.Weight.Bold, 2.5))
            btn.setStyleSheet(
                "background:transparent; border:none; padding-bottom:12px;"
                f"color:{'#FFFFFF' if active else '#888888'};"
            )
            btn.clicked.connect(lambda checked=False, idx=i: self.set_current(idx))
            layout.addWidget(btn)
            self.buttons.append(btn)
        layout.addStretch(1)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._position_indicator)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_indicator()

    def _position_indicator(self, animate=False):
        if not self.buttons: return
        btn = self.buttons[self.current_index]
        end = QRect(btn.x(), self.height() - 2, btn.width(), 2)
        if not animate:
            self.indicator.setGeometry(end)
        else:
            anim = QPropertyAnimation(self.indicator, b"geometry", self)
            anim.setDuration(350)
            anim.setEasingCurve(QEasingCurve.Type.OutExpo)
            anim.setStartValue(self.indicator.geometry())
            anim.setEndValue(end)
            anim.start()
            self._anim = anim

    def set_current(self, index):
        if index == self.current_index: return
        old = self.buttons[self.current_index]
        old.setFont(make_font(self.fonts.univers, 15, QFont.Weight.Bold, 2.5))
        old.setStyleSheet("background:transparent; border:none; color:#888888; padding-bottom:12px;")
        new = self.buttons[index]
        new.setFont(make_font(self.fonts.univers, 15, QFont.Weight.Bold, 2.5))
        new.setStyleSheet("background:transparent; border:none; color:#FFFFFF; padding-bottom:12px;")
        self.current_index = index
        self._position_indicator(True)
        self.tab_changed.emit(index)


class TabTitle(QLabel):
    def __init__(self, text, fonts: FontBook, parent=None):
        super().__init__(text, parent)
        self.setFont(make_font(fonts.helvetica, 18, QFont.Weight.Bold, 2.0))
        self.setStyleSheet("color:#FFFFFF; background:transparent;")


STATUS_COLORS = {
    "SECURE": "#55C98A", "READY": "#55C98A", "DEPLOYED": "#00A3E0",
    "VANILLA": "#888888", "PENDING": "#888888", "ON DEMAND": "#666666",
    "OPTIONAL": "#666666", "MISSING": "#FF4444",
}


class CacheItem(QFrame):
    def __init__(self, title: str, detail: str, fonts: FontBook, icon_kind="filezip", status: str = "", parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent; border-bottom:1px solid #1F1F1F;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(10)

        status_color = STATUS_COLORS.get(status.upper(), "#777777")
        icon = IconWidget(icon_kind, QColor(status_color if status.upper() in ("MISSING", "ORPHAN") else "#777777"), 18, self)
        layout.addWidget(icon, alignment=Qt.AlignmentFlag.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setFont(make_font(fonts.univers, 13, QFont.Weight.Bold))
        title_label.setStyleSheet("color:#FFFFFF; background:transparent; border:none;")
        detail_label = QLabel(detail.upper())
        detail_label.setFont(make_font(fonts.pexico, 9, QFont.Weight.Normal, 0.8))
        detail_label.setWordWrap(True)
        detail_label.setStyleSheet("color:#777777; background:transparent; border:none;")
        text_layout.addWidget(title_label)
        text_layout.addWidget(detail_label)
        layout.addLayout(text_layout, 1)

        if status:
            pill = QLabel(status.upper())
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pill.setFixedHeight(22)
            pill.setMinimumWidth(74)
            pill.setFont(make_font(fonts.univers, 10, QFont.Weight.Normal, 1.0))
            pill.setStyleSheet(
                f"color:{status_color}; border:1px solid {status_color};"
                "background:transparent; padding:0 6px;"
            )
            layout.addWidget(pill, alignment=Qt.AlignmentFlag.AlignTop)

        self.setCursor(interactive_cursor())
        self.title_label = title_label

    def enterEvent(self, event):
        self.title_label.setStyleSheet("color:#00A3E0; background:transparent; border:none;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.title_label.setStyleSheet("color:#FFFFFF; background:transparent; border:none;")
        super().leaveEvent(event)


class ConflictRow(QFrame):
    ACTION_COLORS = {"MERGE": "#55C98A", "OVERWRITE": "#FF6B00", "FORCED": "#00A3E0",
                     "UNMERGEABLE": "#FF4444", "IDENTICAL": "#666666"}

    def __init__(self, entry, fonts: FontBook, on_change, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.on_change = on_change
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background:rgba(0,0,0,60); border:1px solid #1A1A1A;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(12)

        name = entry["path"].rsplit("/", 1)[-1]
        folder = entry["path"].rsplit("/", 1)[0] if "/" in entry["path"] else ""

        text = QVBoxLayout()
        text.setSpacing(1)
        name_label = QLabel(name)
        name_label.setFont(make_font(fonts.univers, 13, QFont.Weight.Bold))
        name_label.setStyleSheet("color:#FFFFFF; background:transparent; border:none;")
        text.addWidget(name_label)
        if folder:
            folder_label = QLabel(folder)
            folder_label.setFont(make_font(fonts.pexico, 10, QFont.Weight.Normal, 0.8))
            folder_label.setStyleSheet("color:#888888; background:transparent; border:none;")
            text.addWidget(folder_label)
        layout.addLayout(text, 1)

        self.btn = QPushButton()
        self.btn.setCursor(interactive_cursor())
        self.btn.setFont(make_font(fonts.univers, 11, QFont.Weight.Bold, 0.8))
        self.btn.setFixedHeight(30)
        self.btn.setFixedWidth(230)
        
        self.btn._is_pressed = False
        self.btn._scale = 1.0
        self.btn.scale_anim = QVariantAnimation(self.btn)
        self.btn.scale_anim.setDuration(100)
        self.btn.scale_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.btn.scale_anim.valueChanged.connect(self._on_btn_scale_anim)
        
        self.btn.mousePressEvent = self._btn_press
        self.btn.mouseReleaseEvent = self._btn_release
        self.btn.paintEvent = self._btn_paint
        
        layout.addWidget(self.btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.refresh_button()

    def _on_btn_scale_anim(self, val):
        self.btn._scale = val
        self.btn.update()

    def _btn_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.btn._is_pressed = True
            self.btn.scale_anim.stop()
            self.btn.scale_anim.setStartValue(self.btn._scale)
            self.btn.scale_anim.setEndValue(0.96)
            self.btn.scale_anim.start()
        QPushButton.mousePressEvent(self.btn, event)

    def _btn_release(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.btn._is_pressed = False
            self.btn.scale_anim.stop()
            self.btn.scale_anim.setStartValue(self.btn._scale)
            self.btn.scale_anim.setEndValue(1.0)
            self.btn.scale_anim.start()
            if self.btn.rect().contains(event.pos()):
                self.cycle()
        QPushButton.mouseReleaseEvent(self.btn, event)

    def _btn_paint(self, event):
        painter = QPainter(self.btn)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.btn.width(), self.btn.height()

        painter.save()
        painter.translate(w / 2, h / 2)
        painter.scale(self.btn._scale, self.btn._scale)
        painter.translate(-w / 2, -h / 2)
            
        bg_color = QColor(255, 255, 255, 18) if self.btn.underMouse() else QColor(0, 0, 0, 0)
        border_color = QColor(self._current_color)
        text_color = QColor(self._current_color)
        
        painter.fillRect(0, 0, w, h, bg_color)
        painter.setPen(QPen(border_color, 1))
        painter.drawRect(0, 0, w - 1, h - 1)
        
        painter.setFont(self.btn.font())
        painter.setPen(text_color)
        painter.drawText(self.btn.rect(), Qt.AlignmentFlag.AlignCenter, self.btn.text())
        
        painter.restore()

    def options(self):
        return ["AUTO"] + list(self.entry["mods"])

    def refresh_button(self):
        entry = self.entry
        if entry["action"] == "IDENTICAL":
            label, color = "IDENTICAL \u2014 NO ACTION NEEDED", self.ACTION_COLORS["IDENTICAL"]
        elif entry["action"] == "MERGE":
            label, color = "MERGE BOTH", self.ACTION_COLORS["MERGE"]
        elif entry["action"] == "UNMERGEABLE":
            label = f"CAN'T MERGE \u2192 {short_mod_name(entry['winner'], 14)}"
            color = self.ACTION_COLORS["UNMERGEABLE"]
        elif entry["action"] == "OVERWRITE":
            label = f"AUTO \u2192 {short_mod_name(entry['winner'], 18)}"
            color = self.ACTION_COLORS["OVERWRITE"]
        else:
            label = f"ONLY {short_mod_name(entry['winner'], 20)}"
            color = self.ACTION_COLORS["FORCED"]
            
        self.btn.setText(label)
        self.btn.setToolTip(f"{entry['path']}\n\nContributors: " + ", ".join(entry["mods"]))
        self._current_color = color
        self.btn.setStyleSheet("background:transparent; color:transparent; border:none;") 

    def cycle(self):
        opts = self.options()
        try: idx = opts.index(self.entry["rule"])
        except ValueError: idx = 0
        self.on_change(self.entry["path"], opts[(idx + 1) % len(opts)])


class ConflictGroupHeader(QFrame):
    def __init__(self, mods, paths, fonts: FontBook, on_bulk, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background:rgba(0,163,224,0.05); border:none; border-left:3px solid #00A3E0;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        title = QLabel(" vs ".join(short_mod_name(m, 22) for m in mods) + f"   \u00b7   {len(paths)} file(s)")
        title.setFont(make_font(fonts.univers, 13, QFont.Weight.Bold, 0.8))
        title.setStyleSheet("color:#FFFFFF; background:transparent; border:none;")
        layout.addWidget(title, 1)

        set_all = QLabel("SET ALL:")
        set_all.setFont(make_font(fonts.pexico, 9, QFont.Weight.Normal, 1.0))
        set_all.setStyleSheet("color:#777777; background:transparent; border:none;")
        layout.addWidget(set_all)

        for option in ["AUTO"] + list(mods):
            btn = QPushButton("AUTO" if option == "AUTO" else short_mod_name(option, 16))
            btn.setCursor(interactive_cursor())
            btn.setFont(make_font(fonts.univers, 10, QFont.Weight.Normal, 0.6))
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                "QPushButton{color:#AAAAAA; border:1px solid #333333;"
                "background:transparent; padding:0 10px;}"
                "QPushButton:hover{color:#FFFFFF; border:1px solid #00A3E0;}"
            )
            btn.clicked.connect(lambda _=False, o=option: on_bulk(paths, o))
            layout.addWidget(btn)


class SearchBar(QFrame):
    def __init__(self, fonts: FontBook, parent=None):
        super().__init__(parent)
        self.setFixedHeight(58)
        self.setStyleSheet("background:rgba(0,0,0,190); border-top:1px solid #1F1F1F;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)
        layout.addWidget(IconWidget("search", QColor("#555555"), 18))
        self.input = QLineEdit()
        self.input.setPlaceholderText("QUERY CACHE...")
        self.input.setFont(make_font(fonts.pexico, 11, QFont.Weight.Normal, 1.2))
        self.input.setStyleSheet(
            "QLineEdit{background:transparent; border:none; color:#FFFFFF; padding:0;}"
            "QLineEdit:focus{border:none;}"
        )
        layout.addWidget(self.input, 1)


# -----------------------------------------------------------------------------
# Main window
# -----------------------------------------------------------------------------
class WD2ModManagerNative(QMainWindow):
    PLUS_MARGIN_RIGHT = 76
    PLUS_MARGIN_BOTTOM = 64

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Watch_Dogs 2 Mod Manager")
        self.resize(1280, 800)
        self.setMinimumSize(1260, 720)
        self.setAcceptDrops(True)

        app_cursor = get_app_cursor()
        if app_cursor is not None:
            self.setCursor(app_cursor)

        self.fonts = FontBook()
        self.backend = Backend()
        self.backend.log_signal.connect(self.append_log)
        self.logs = []
        self.worker = None
        self.background = None
        self._external_drag_active = False
        self.last_checkbox_index = None
        self.last_viewed_index = None
        self.dragging_row = None
        self.deployment_progress = 0.0
        self._conflict_entries = {}
        self._conflict_widgets = {}
        self._is_busy = False 

        self.load_background()
        self.build_ui()
        self.build_connections()
        self.load_initial_data()

        if PIPELINE_IMPORT_ERROR:
            self.append_log(f"[ERROR] pipeline import failed: {PIPELINE_IMPORT_ERROR}")

    def browse_tools(self):
        path = self.backend.browse_tools_dir(self)
        if path:
            self.tools_input.setText(path)
            self.refresh_from_backend()

    def save_nexus_key(self):
        self.backend.set_nexus_api_key(self.nexus_key_input.text())

    def validate_nexus_key(self):
        key = self.nexus_key_input.text().strip()
        if not key:
            self.nexus_status_label.setText("> ENTER A KEY FIRST")
            self.nexus_status_label.setStyleSheet("color:#FF6B00; background:transparent;")
            return
        self.save_nexus_key()
        self.nexus_status_label.setText("> CHECKING...")
        self.nexus_status_label.setStyleSheet("color:#888888; background:transparent;")
        self.start_worker(self.backend.validate_nexus_key, key, on_result=self.on_nexus_validated)

    def on_nexus_validated(self, result):
        ok, message = result
        self.nexus_status_label.setText(f"> {message}")
        self.nexus_status_label.setStyleSheet(
            "color:#55C98A; background:transparent;" if ok else "color:#FF4444; background:transparent;"
        )

    def register_nxm_handler(self):
        ok, result = self.backend.register_nxm_handler()
        if ok:
            self.append_log("[UI] Registered as the nxm:// download handler.")
            self.update_nxm_status()
        else:
            self.nxm_status_label.setText(f"> {result}")
            self.nxm_status_label.setStyleSheet("color:#FF4444; background:transparent;")

    def update_nxm_status(self):
        if not hasattr(self, "nxm_status_label"):
            return
        command, is_current = self.backend.nxm_handler_status()
        if command is None:
            self.nxm_status_label.setText("> NOT REGISTERED")
            self.nxm_status_label.setStyleSheet("color:#666666; background:transparent;")
        elif is_current:
            self.nxm_status_label.setText("> REGISTERED TO THIS INSTALL")
            self.nxm_status_label.setStyleSheet("color:#55C98A; background:transparent;")
        else:
            self.nxm_status_label.setText(f"> REGISTERED TO A DIFFERENT PATH: {command}")
            self.nxm_status_label.setStyleSheet("color:#FF6B00; background:transparent;")

    def update_tools_status(self):
        if not hasattr(self, "tools_status_label"): return
        pipe = self.backend.pipe
        if pipe is None or not hasattr(pipe, "gibbed"):
            self.tools_status_label.setText("> PIPELINE UNAVAILABLE")
            self.tools_status_label.setStyleSheet("color:#FF4444; background:transparent;")
            return
        status = pipe.gibbed.tools_status()
        missing = [status[k]["name"] for k in ("unpack", "pack", "convert") if not status[k]["ok"]]
        if not missing:
            self.tools_status_label.setText(f"> TOOLCHAIN READY // {status['toolset']}")
            self.tools_status_label.setStyleSheet("color:#55C98A; background:transparent;")
        else:
            self.tools_status_label.setText("> MISSING: " + ", ".join(missing))
            self.tools_status_label.setStyleSheet("color:#FF4444; background:transparent;")

    def restore_vanilla(self):
        data_dir = self.backend.game_data_dir()
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Restore Vanilla")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText("Remove patch3.fat and patch3.dat from the game directory?")
        confirm.setInformativeText(f"{data_dir}\n\nThis returns the game to an unmodded state. Your cached mods and archives are not affected.")
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes: return

        self.btn_restore.set_busy(True, "RESTORING...")
        self.start_worker(self.backend.restore_vanilla, on_result=self.on_restore_finished)

    def on_restore_finished(self, response):
        self.btn_restore.set_busy(False)
        self.append_log(f"[UI] {response}")
        self.refresh_from_backend()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Warning", "An operation is currently running.\n\nPlease wait for it to finish to prevent game file corruption.")
            event.ignore()
        else:
            event.accept()

    def load_background(self):
        path = find_asset("background.png")
        if not path or not path.exists(): return
        pixmap = QPixmap(str(path))
        if pixmap.isNull(): return
        if pixmap.width() > 1920: pixmap = pixmap.scaledToWidth(1920, Qt.TransformationMode.SmoothTransformation)
        self.background = self._blur_pixmap(pixmap, BACKGROUND_BLUR_RADIUS)

    @staticmethod
    def _blur_pixmap(pixmap: QPixmap, radius: int) -> QPixmap:
        try:
            scene = QGraphicsScene()
            item = QGraphicsPixmapItem(pixmap)
            effect = QGraphicsBlurEffect()
            effect.setBlurRadius(radius)
            item.setGraphicsEffect(effect)
            scene.addItem(item)
            result = QImage(pixmap.size(), QImage.Format.Format_ARGB32_Premultiplied)
            result.fill(Qt.GlobalColor.transparent)
            painter = QPainter(result)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            scene.render(painter)
            painter.end()
            return QPixmap.fromImage(result)
        except Exception:
            return pixmap

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self.background and not self.background.isNull():
            scaled = self.background.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            x = (scaled.width() - self.width()) // 2
            y = (scaled.height() - self.height()) // 2
            painter.drawPixmap(-x, -y, scaled)
            painter.fillRect(self.rect(), QColor(3, 3, 3, WINDOW_SCRIM_ALPHA))
        else:
            painter.fillRect(self.rect(), QColor("#030303"))

    def build_ui(self):
        central = QWidget()
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        central.setStyleSheet("background:transparent;")
        self.setCentralWidget(central)

        main = QVBoxLayout(central)
        main.setContentsMargins(40, 30, 40, 28)
        main.setSpacing(20)

        self.nav = AnimatedNav(["Mods", "Conflict Scan", "Logs", "Settings"], self.fonts)
        self.nav.setStyleSheet("background:rgba(0,0,0,150); border-bottom:1px solid #1F1F1F;")
        self.nav.tab_changed.connect(self.switch_tab)
        main.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background:transparent; border:none;")
        main.addWidget(self.stack, 1)

        self.build_mod_tab()
        self.build_conflict_tab()
        self.build_logs_tab()
        self.build_settings_tab()

        self.drop_overlay = DropOverlay(self.fonts, self)
        self.drop_overlay.raise_()
        QApplication.instance().installEventFilter(self)

    def build_mod_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(28)

        left = GlassPanel(PANEL_ALPHA)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        toolbar = QFrame(left)
        toolbar.setFixedHeight(52)
        toolbar.setStyleSheet("background:rgba(0,0,0,180); border-bottom:1px solid #1F1F1F;")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(14, 0, 14, 0)
        tb.setSpacing(6)

        self.select_all_box = TriStateBox(toolbar)
        self.select_all_box.clicked.connect(self.toggle_select_all)
        tb.addWidget(self.select_all_box)

        select_all = QLabel("SELECT ALL")
        select_all.setFont(make_font(self.fonts.univers, 14, QFont.Weight.Bold, 1.5))
        select_all.setStyleSheet("color:#888888; background:transparent;")
        tb.addWidget(select_all)
        divider = QFrame(toolbar)
        divider.setFixedSize(1, 16)
        divider.setStyleSheet("background:#282828;")
        tb.addSpacing(6)
        tb.addWidget(divider)
        tb.addSpacing(10)

        self.mod_search_input = QLineEdit()
        self.mod_search_input.setPlaceholderText("Filter mods...")
        self.mod_search_input.setFont(make_font(self.fonts.pexico, 12, QFont.Weight.Normal, 0.4))
        self.mod_search_input.setFixedWidth(160)
        self.mod_search_input.setStyleSheet(
            "QLineEdit{background:rgba(0,0,0,130); border:1px solid #1F1F1F; color:#FFFFFF; "
            "padding:5px 10px; border-radius:2px;}"
            "QLineEdit:focus{border:1px solid #00A3E0;}"
        )
        self.mod_search_input.textChanged.connect(self.filter_mod_list)
        tb.addWidget(self.mod_search_input)
        tb.addSpacing(18)

        self.selected_count = QLabel("0 Selected")
        self.selected_count.setFont(make_font(self.fonts.univers, 12, QFont.Weight.Bold, 0.8))
        self.selected_count.setStyleSheet("color:#FFFFFF; background:transparent;")
        tb.addWidget(self.selected_count)
        tb.addStretch(1)

        self.btn_identify_all = QPushButton("IDENTIFY ALL")
        self.btn_identify_all.setFont(make_font(self.fonts.univers, 13, QFont.Weight.Bold, 0.8))
        self.btn_identify_all.setCursor(interactive_cursor())
        self.btn_identify_all.setMinimumHeight(30)
        self.btn_identify_all.setToolTip(
            "Hashes each installed mod's archive and checks Nexus for a match -- "
            "the same technique Mod Organizer 2 uses. Mods from elsewhere (Discord, "
            "manual builds) are silently skipped, not flagged as errors."
        )
        self.btn_identify_all.setStyleSheet(
            "QPushButton{color:#888888; border:1px solid #222222; background:transparent; padding:0 10px;}"
            "QPushButton:hover{color:#00A3E0; border:1px solid #00A3E0;}"
        )
        self.btn_identify_all.setIcon(tinted_qicon("icon_eye", QColor("#888888")))
        self.btn_identify_all.setIconSize(QSize(22, 16))
        self.btn_identify_all.clicked.connect(self.identify_all_mods)
        tb.addSpacing(14)
        tb.addWidget(self.btn_identify_all)

        self.btn_check_updates = QPushButton("CHECK UPDATES")
        self.btn_check_updates.setFont(make_font(self.fonts.univers, 13, QFont.Weight.Bold, 0.8))
        self.btn_check_updates.setCursor(interactive_cursor())
        self.btn_check_updates.setMinimumHeight(30)
        self.btn_check_updates.setToolTip(
            "Compares the version of each Nexus-linked mod against the latest listed "
            "on the site. A mod linked before this existed gets its version recorded "
            "on the first check rather than guessed -- it won't falsely show as "
            "outdated the first time."
        )
        self.btn_check_updates.setStyleSheet(
            "QPushButton{color:#888888; border:1px solid #222222; background:transparent; padding:0 10px;}"
            "QPushButton:hover{color:#00A3E0; border:1px solid #00A3E0;}"
        )
        self.btn_check_updates.setIcon(tinted_qicon("icon_refresh", QColor("#888888")))
        self.btn_check_updates.setIconSize(QSize(22, 16))
        self.btn_check_updates.clicked.connect(self.check_for_updates)
        tb.addWidget(self.btn_check_updates)

        self.btn_remove = DangerButton("REMOVE SELECTED", self.fonts)
        self.btn_remove.setFont(make_font(self.fonts.univers, 13, QFont.Weight.Bold, 0.8))
        self.btn_remove.setMinimumWidth(165)
        self.btn_remove.clicked.connect(self.remove_selected)
        tb.addWidget(self.btn_remove)
        left_layout.addWidget(toolbar)

        self.mod_list = ModListArea(self.fonts, left)
        self.mod_list.mod_clicked.connect(self.handle_mod_view_click)
        self.mod_list.mod_checkbox_toggled.connect(self.handle_checkbox_toggle)
        self.mod_list.mod_drag_started.connect(self.begin_row_drag)
        self.mod_list.mod_drag_moved.connect(self.update_row_drag)
        self.mod_list.mod_drag_finished.connect(self.end_row_drag)
        self.mod_list.mod_link_requested.connect(self.link_mod_to_nexus)
        left_layout.addWidget(self.mod_list, 1)

        self.btn_plus = PlusButton(left)
        self.btn_plus.clicked.connect(self.browse_and_import)
        self.btn_plus.raise_()
        left.resizeEvent = self.make_left_resize_handler(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        action_wrap = QWidget()
        actions = QVBoxLayout(action_wrap)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        self.btn_deploy = HudButton("DEPLOY & MERGE", "", CYAN, CYAN_HOVER, QColor("#000000"), 16, center_text_only=True)
        self.btn_deploy.setFixedHeight(60)
        self.btn_deploy.setFont(make_font(self.fonts.univers, 16, QFont.Weight.Bold, 2.0))
        self.btn_deploy.clicked.connect(self.deploy_selected)
        top_row.addWidget(self.btn_deploy, 1)

        self.btn_run = HudButton("", "play", PURPLE, PURPLE_HOVER, WHITE, 16, center_text_only=True)
        self.btn_run.setFixedSize(60, 60)
        self.btn_run.setFont(make_font(self.fonts.univers, 16, QFont.Weight.Bold, 2.0))
        self.eac_enabled = self.backend.eac_enabled
        self.btn_run.setToolTip(f"Run Game (EAC {'OFF' if self.eac_enabled else 'ON'})")
        self.btn_run.clicked.connect(self.run_game)
        top_row.addWidget(self.btn_run)

        actions.addLayout(top_row)

        eac_row = QHBoxLayout()
        eac_row.setContentsMargins(2, 0, 2, 0)
        self.eac_checkbox_main = TickCheckBox("BYPASS EASY ANTI-CHEAT (EAC)")
        self.eac_checkbox_main.setFont(make_font(self.fonts.univers, 14, QFont.Weight.Bold, 0.6))
        self.eac_checkbox_main.setStyleSheet("""
            QCheckBox { color: #C4C4C4; background: transparent; spacing: 10px; }
            QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #333333; background: rgba(0, 0, 0, 120); }
            QCheckBox::indicator:hover { border: 1px solid #00A3E0; }
            QCheckBox::indicator:checked { border: 1px solid #00A3E0; background: rgba(0, 163, 224, 0.15); }
        """)
        self.eac_checkbox_main.setCursor(interactive_cursor())
        self.eac_checkbox_main.setChecked(self.eac_enabled)
        self.eac_checkbox_main.toggled.connect(self.toggle_eac)
        eac_row.addWidget(self.eac_checkbox_main)
        eac_row.addStretch(1)
        actions.addLayout(eac_row)

        right_layout.addWidget(action_wrap)
        right_layout.addSpacing(28)

        cache = GlassPanel(PANEL_ALPHA)
        cache_layout = QVBoxLayout(cache)
        cache_layout.setContentsMargins(0, 0, 0, 0)
        cache_layout.setSpacing(0)

        cache_tabs = QFrame(cache)
        cache_tabs.setFixedHeight(52)
        cache_tabs.setStyleSheet("background:rgba(0,0,0,175); border-bottom:1px solid #1F1F1F;")
        ct = QHBoxLayout(cache_tabs)
        ct.setContentsMargins(0, 0, 0, 0)
        ct.setSpacing(0)
        self.btn_archive = QPushButton("ARCHIVE CACHE")
        self.btn_archive.setFont(make_font(self.fonts.helvetica, 11, QFont.Weight.Bold, 0.6))
        self.btn_archive.setCursor(interactive_cursor())
        self.btn_archive.setStyleSheet("color:#FFFFFF; background:rgba(0,0,0,90); border:none; border-right:1px solid #1F1F1F; padding:0 12px;")
        self.btn_patch3 = QPushButton("DEPLOY STATE")
        self.btn_patch3.setFont(make_font(self.fonts.helvetica, 11, QFont.Weight.Bold, 0.6))
        self.btn_patch3.setCursor(interactive_cursor())
        self.btn_patch3.setStyleSheet("color:#666666; background:transparent; border:none; padding:0 12px;")
        self.btn_archive.clicked.connect(lambda: self.switch_cache_tab(True))
        self.btn_patch3.clicked.connect(lambda: self.switch_cache_tab(False))
        ct.addWidget(self.btn_archive)
        ct.addWidget(self.btn_patch3)

        self.btn_refresh_deploy = SpinIconButton(
            "icon_refresh", QColor("#888888"), 16,
            gif_path=find_asset("loading_4.gif"), gif_size=24, hold_ms=900,
        )
        self.btn_refresh_deploy.setFixedSize(44, 52)
        self.btn_refresh_deploy.setCursor(interactive_cursor())
        self.btn_refresh_deploy.setToolTip("Re-check tool and game paths")
        self.btn_refresh_deploy.setStyleSheet(
            "QPushButton{background:transparent; border:none; border-left:1px solid #1F1F1F;}"
            "QPushButton:hover{background:rgba(0,163,224,0.12);}"
        )
        self.btn_refresh_deploy.clicked.connect(self.refresh_deploy_state)
        ct.addWidget(self.btn_refresh_deploy)
        cache_layout.addWidget(cache_tabs)

        self.cache_stack = QStackedWidget()
        self.cache_stack.setStyleSheet("background:transparent; border:none;")

        self.cache_scroll = SmoothScrollArea()
        self.cache_content = QWidget()
        self.cache_content.setStyleSheet("background:transparent;")
        self.cache_layout = QVBoxLayout(self.cache_content)
        self.cache_layout.setContentsMargins(16, 16, 16, 16)
        self.cache_layout.setSpacing(10)
        self.cache_layout.addStretch(1)
        self.cache_scroll.setWidgetResizable(True)
        self.cache_scroll.setWidget(self.cache_content)

        self.patch_scroll = SmoothScrollArea()
        self.patch_content = QWidget()
        self.patch_content.setStyleSheet("background:transparent;")
        self.patch_layout = QVBoxLayout(self.patch_content)
        self.patch_layout.setContentsMargins(16, 16, 16, 16)
        self.patch_layout.setSpacing(10)
        self.patch_layout.addStretch(1)
        self.patch_scroll.setWidgetResizable(True)
        self.patch_scroll.setWidget(self.patch_content)

        self.cache_stack.addWidget(self.cache_scroll)
        self.cache_stack.addWidget(self.patch_scroll)
        cache_layout.addWidget(self.cache_stack, 1)

        self.search_bar = SearchBar(self.fonts)
        cache_layout.addWidget(self.search_bar)

        right_layout.addWidget(cache, 1)
        layout.addWidget(left, 8)
        layout.addWidget(right, 4)
        self.mod_order_left = left
        self.stack.addWidget(tab)

    def _position_plus_button(self):
        if not hasattr(self, "btn_plus") or not hasattr(self, "mod_order_left"): return
        self.btn_plus.move(self.mod_order_left.width() - self.PLUS_MARGIN_RIGHT, self.mod_order_left.height() - self.PLUS_MARGIN_BOTTOM)

    def make_left_resize_handler(self, left):
        def handler(event):
            self._position_plus_button()
            QFrame.resizeEvent(left, event)
        return handler

    def build_conflict_tab(self):
        tab = GlassPanel(PANEL_ALPHA)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(TabTitle("FILE CONFLICTS", self.fonts))

        self.conflict_summary = QLabel("Scan to see which files more than one queued mod edits.")
        self.conflict_summary.setFont(make_font(self.fonts.helvetica, 13, QFont.Weight.Normal, 0.4))
        self.conflict_summary.setWordWrap(True)
        self.conflict_summary.setStyleSheet("color:#888888; background:transparent;")
        layout.addWidget(self.conflict_summary)

        self.conflict_area = SmoothScrollArea()
        self.conflict_area.setWidgetResizable(True)
        self.conflict_area.viewport().setStyleSheet("background:transparent; border:none;")
        holder = QWidget()
        holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        holder.setStyleSheet("background:rgba(0,0,0,135); border:none;")
        self.conflict_rows_layout = QVBoxLayout(holder)
        self.conflict_rows_layout.setContentsMargins(14, 14, 14, 14)
        self.conflict_rows_layout.setSpacing(6)
        self.conflict_rows_layout.addStretch(1)
        self.conflict_area.setWidget(holder)
        layout.addWidget(self.conflict_area, 1)

        row = QHBoxLayout()
        hint = QLabel("Click a file's action to cycle: AUTO \u2192 each mod. AUTO merges database files and gives binary files to the last mod in load order.")
        hint.setFont(make_font(self.fonts.helvetica, 11, QFont.Weight.Normal, 0.3))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666666; background:transparent;")
        row.addWidget(hint, 1)
        self.btn_scan = HudButton("SCAN CONFLICTS", "", PURPLE, PURPLE_HOVER, WHITE, 14, center_text_only=True)
        self.btn_scan.setFixedSize(220, 48)
        self.btn_scan.setFont(make_font(self.fonts.univers, 12, QFont.Weight.Bold, 1.8))
        self.btn_scan.clicked.connect(self.scan_conflicts)
        row.addWidget(self.btn_scan)
        layout.addLayout(row)
        self.stack.addWidget(tab)

    def scan_conflicts(self):
        self._conflict_widgets = {}
        names = self.mod_list.enabled_mod_names()
        if not names:
            self.conflict_summary.setText("No mods are queued. Tick some mods first.")
            self.clear_layout_widgets(self.conflict_rows_layout)
            self.conflict_rows_layout.addStretch(1)
            return

        self.btn_scan.set_busy(True, "SCANNING...")
        self.clear_layout_widgets(self.conflict_rows_layout)
        
        loading_lbl = QLabel("ANALYZING XML OVERLAPS AND ROUNDTRIP CAPABILITIES...")
        loading_lbl.setFont(make_font(self.fonts.pexico, 11, QFont.Weight.Normal, 1.4))
        loading_lbl.setStyleSheet("color:#00A3E0; background:transparent;")
        loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.conflict_rows_layout.addWidget(loading_lbl)
        self.conflict_rows_layout.addStretch(1)
        
        self.start_worker(self.backend.scan_conflicts, names, on_result=self.on_scan_finished)
        
    def on_scan_finished(self, rows):
        self.btn_scan.set_busy(False)
        self.clear_layout_widgets(self.conflict_rows_layout)
        names = self.mod_list.enabled_mod_names()
        
        if not rows:
            self.conflict_summary.setText(f"{len(names)} mod(s) queued \u2014 no overlapping files. Nothing to resolve.")
            empty = QLabel("NO CONFLICTS")
            empty.setFont(make_font(self.fonts.pexico, 11, QFont.Weight.Normal, 1.4))
            empty.setStyleSheet("color:#55C98A; background:transparent;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.conflict_rows_layout.addWidget(empty)
            self.conflict_rows_layout.addStretch(1)
            return

        merged = sum(1 for r in rows if r["action"] == "MERGE")
        unmergeable = sum(1 for r in rows if r["action"] == "UNMERGEABLE")
        forced = sum(1 for r in rows if r["action"] == "FORCED")
        overwritten = sum(1 for r in rows if r["action"] == "OVERWRITE")
        identical = sum(1 for r in rows if r["action"] == "IDENTICAL")
        self.conflict_summary.setText(
            f"{len(rows)} contested file(s) across {len(names)} mod(s) \u2014 "
            f"{merged} merged, {overwritten} overwritten, {forced} forced by rule, "
            f"{unmergeable} unmergeable, {identical} identical (no action needed).   "
            "Green merges both mods' changes; orange means one mod's version is discarded."
        )

        self._conflict_entries = {e["path"]: e for e in rows}
        self._conflict_widgets = {}

        groups = {}
        for entry in rows:
            groups.setdefault(tuple(entry["mods"]), []).append(entry)

        for mods, entries in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            paths = [e["path"] for e in entries]
            self.conflict_rows_layout.addWidget(ConflictGroupHeader(list(mods), paths, self.fonts, self.on_conflict_bulk_change))
            for entry in entries:
                widget = ConflictRow(entry, self.fonts, self.on_conflict_rule_changed)
                self._conflict_widgets[entry["path"]] = widget
                self.conflict_rows_layout.addWidget(widget)
        self.conflict_rows_layout.addStretch(1)

    @staticmethod
    def _decide_action(entry):
        rule = entry.get("rule", "AUTO")
        contributors = entry["mods"]
        if entry.get("identical"):
            return "IDENTICAL", contributors[-1]
        if rule != "AUTO" and rule in contributors:
            return "FORCED", rule
        if entry.get("blocked"):
            return "UNMERGEABLE", contributors[-1]
        if entry.get("mergeable"):
            return "MERGE", None
        return "OVERWRITE", contributors[-1]

    def _apply_rule_locally(self, rel_path, winner):
        entry = self._conflict_entries.get(rel_path)
        if entry is None:
            return False
        entry["rule"] = winner
        entry["action"], entry["winner"] = self._decide_action(entry)
        widget = self._conflict_widgets.get(rel_path)
        if widget is not None:
            widget.entry = entry
            widget.refresh_button()
        return True

    def _refresh_conflict_summary(self):
        rows = list(self._conflict_entries.values())
        if not rows:
            return
        names = self.mod_list.enabled_mod_names()
        merged = sum(1 for r in rows if r["action"] == "MERGE")
        unmergeable = sum(1 for r in rows if r["action"] == "UNMERGEABLE")
        forced = sum(1 for r in rows if r["action"] == "FORCED")
        overwritten = sum(1 for r in rows if r["action"] == "OVERWRITE")
        identical = sum(1 for r in rows if r["action"] == "IDENTICAL")
        self.conflict_summary.setText(
            f"{len(rows)} contested file(s) across {len(names)} mod(s) \u2014 "
            f"{merged} merged, {overwritten} overwritten, {forced} forced by rule, "
            f"{unmergeable} unmergeable, {identical} identical (no action needed).   "
            "Green merges both mods' changes; orange means one mod's version is discarded."
        )

    def on_conflict_rule_changed(self, rel_path, winner):
        self.backend.set_conflict_rule(rel_path, winner)
        if not self._apply_rule_locally(rel_path, winner):
            self.scan_conflicts()      
            return
        self._refresh_conflict_summary()

    def on_conflict_bulk_change(self, paths, winner):
        self.backend.set_conflict_rules_bulk(paths, winner)
        label = "AUTO" if winner == "AUTO" else short_mod_name(winner, 30)
        self.append_log(f"[UI] Set {len(paths)} conflict(s) to {label}.")
        missing = False
        for rel_path in paths:
            if not self._apply_rule_locally(rel_path, winner):
                missing = True
        if missing:
            self.scan_conflicts()
            return
        self._refresh_conflict_summary()

    def build_logs_tab(self):
        tab = GlassPanel(PANEL_ALPHA)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QFrame()
        header.setFixedHeight(48)
        header.setStyleSheet("background:rgba(0,0,0,190); border-bottom:1px solid #1F1F1F;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        title = QLabel("BACKEND OPERATIONS LOG")
        title.setFont(make_font(self.fonts.helvetica, 12, QFont.Weight.Bold, 1.2))
        title.setStyleSheet("color:#FFFFFF; background:transparent;")
        hl.addWidget(title)
        hl.addStretch(1)
        export = QPushButton("EXPORT")
        export.setFont(make_font(self.fonts.univers, 11, QFont.Weight.Normal, 1.1))
        export.setCursor(interactive_cursor())
        export.setStyleSheet("color:#888888; background:transparent; border:none;")
        export.clicked.connect(self.export_logs)
        hl.addWidget(export)
        layout.addWidget(header)
        
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFrameShape(QFrame.Shape.NoFrame)
        self.log_view.setStyleSheet(
            f"QTextEdit{{background:rgba(0,0,0,100); padding: 22px; border: none;}} {SCROLLBAR_QSS}"
        )
        app_cursor = get_app_cursor()
        if app_cursor is not None:
            self.log_view.viewport().setCursor(app_cursor)
        layout.addWidget(self.log_view, 1)
        self.stack.addWidget(tab)

    def build_settings_tab(self):
        tab = GlassPanel(PANEL_ALPHA)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(24)
        layout.addWidget(TabTitle("SYSTEM CONFIGURATION", self.fonts))

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background:transparent;")
        fl = QVBoxLayout(scroll_content)
        fl.setContentsMargins(0, 0, 15, 0)
        fl.setSpacing(20)

        path_label = QLabel("GAME EXECUTABLE PATH")
        path_label.setFont(make_font(self.fonts.helvetica, 15, QFont.Weight.Bold, 1.2))
        path_label.setStyleSheet("color:#888888; background:transparent;")
        fl.addWidget(path_label)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.exe_input = QLineEdit(self.backend.get_exe_path())
        self.exe_input.setFont(make_font(self.fonts.pexico, 13, QFont.Weight.Normal, 0.6))
        self.exe_input.setStyleSheet(
            "QLineEdit{background:rgba(0,0,0,130); border:1px solid #1F1F1F; color:#FFFFFF; padding:12px 14px;}"
            "QLineEdit:focus{border:1px solid #00A3E0;}"
        )
        path_row.addWidget(self.exe_input, 1)
        browse = QPushButton("BROWSE")
        browse.setFont(make_font(self.fonts.univers, 12, QFont.Weight.Normal, 1.1))
        browse.setCursor(interactive_cursor())
        browse.setStyleSheet(
            "QPushButton{color:#FFFFFF;background:#111111;border:1px solid #1F1F1F;padding:12px 22px;}"
            "QPushButton:hover{background:#00A3E0;color:#000000;}"
        )
        browse.clicked.connect(self.browse_exe)
        path_row.addWidget(browse)
        fl.addLayout(path_row)

        tools_label = QLabel("GIBBED TOOLS FOLDER")
        tools_label.setFont(make_font(self.fonts.helvetica, 15, QFont.Weight.Bold, 1.2))
        tools_label.setStyleSheet("color:#888888; background:transparent;")
        fl.addWidget(tools_label)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(8)
        self.tools_input = QLineEdit(self.backend.get_tools_dir())
        self.tools_input.setFont(make_font(self.fonts.pexico, 13, QFont.Weight.Normal, 0.6))
        self.tools_input.setStyleSheet(
            "QLineEdit{background:rgba(0,0,0,130); border:1px solid #1F1F1F; color:#FFFFFF; padding:12px 14px;}"
            "QLineEdit:focus{border:1px solid #00A3E0;}"
        )
        tools_row.addWidget(self.tools_input, 1)
        browse_tools = QPushButton("BROWSE")
        browse_tools.setFont(make_font(self.fonts.univers, 12, QFont.Weight.Normal, 1.1))
        browse_tools.setCursor(interactive_cursor())
        browse_tools.setStyleSheet(
            "QPushButton{color:#FFFFFF;background:#111111;border:1px solid #1F1F1F;padding:12px 22px;}"
            "QPushButton:hover{background:#00A3E0;color:#000000;}"
        )
        browse_tools.clicked.connect(self.browse_tools)
        tools_row.addWidget(browse_tools)
        fl.addLayout(tools_row)

        self.tools_status_label = QLabel("")
        self.tools_status_label.setFont(make_font(self.fonts.pexico, 11, QFont.Weight.Normal, 0.8))
        self.tools_status_label.setWordWrap(True)
        fl.addWidget(self.tools_status_label)

        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background:#1F1F1F;")
        fl.addWidget(rule)

        eac_row = QHBoxLayout()
        self.eac_checkbox = TickCheckBox("BYPASS EASY ANTI-CHEAT (EAC)")
        self.eac_checkbox.setFont(make_font(self.fonts.univers, 15, QFont.Weight.Normal, 1.0))
        self.eac_checkbox.setStyleSheet("""
            QCheckBox { color: #FFFFFF; background: transparent; spacing: 12px; }
            QCheckBox::indicator { width: 20px; height: 20px; border: 1px solid #444444; background: rgba(0, 0, 0, 120); }
            QCheckBox::indicator:hover { border: 1px solid #00A3E0; }
            QCheckBox::indicator:checked { border: 1px solid #00A3E0; background: rgba(0, 163, 224, 0.15); }
        """)
        self.eac_checkbox.setCursor(interactive_cursor())
        self.eac_checkbox.setChecked(self.eac_enabled)
        self.eac_checkbox.toggled.connect(self.toggle_eac)
        
        self.eac_method_dropdown = QComboBox()
        self.eac_method_dropdown.addItems([
            "Argument (-eac_launcher) [Default]", 
            "Argument (-eac_index 0) [Alternative]", 
            "Ubisoft Connect (uplay://)"
        ])
        self.eac_method_dropdown.setFont(make_font(self.fonts.pexico, 11, QFont.Weight.Normal, 0.6))
        self.eac_method_dropdown.setStyleSheet(
            "QComboBox { background: rgba(0,0,0,130); border: 1px solid #1F1F1F; color: #FFFFFF; padding: 4px 10px; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView { background: #111111; color: #FFFFFF; selection-background-color: #00A3E0; border: 1px solid #1F1F1F; }"
        )
        self.eac_method_dropdown.setFixedWidth(300)
        self.eac_method_dropdown.setCursor(interactive_cursor())
        self.eac_method_dropdown.setCurrentIndex(self.backend.eac_method)
        self.eac_method_dropdown.currentIndexChanged.connect(self.save_eac_method)
        
        eac_row.addWidget(self.eac_checkbox)
        eac_row.addWidget(self.eac_method_dropdown)
        eac_row.addStretch(1)
        fl.addLayout(eac_row)

        rule_bl = QFrame()
        rule_bl.setFixedHeight(1)
        rule_bl.setStyleSheet("background:#1F1F1F;")
        fl.addWidget(rule_bl)

        prof_label = QLabel("MODPACK PROFILES")
        prof_label.setFont(make_font(self.fonts.helvetica, 15, QFont.Weight.Bold, 1.2))
        prof_label.setStyleSheet("color:#888888; background:transparent;")
        fl.addWidget(prof_label)
        
        prof_note = QLabel("Export your load order and conflict rules to share with others, or import a pre-configured profile.")
        prof_note.setFont(make_font(self.fonts.helvetica, 12, QFont.Weight.Normal, 0.4))
        prof_note.setStyleSheet("color:#666666; background:transparent;")
        fl.addWidget(prof_note)
        
        prof_row = QHBoxLayout()
        prof_row.setSpacing(10)
        
        self.btn_export_prof = HudButton("EXPORT PROFILE", "img_aperture", QColor("#1F1F1F"), QColor("#2A2A2A"), TEXT, 14, center_text_only=True)
        self.btn_export_prof.setFixedSize(240, 44)
        self.btn_export_prof.setFont(make_font(self.fonts.univers, 12, QFont.Weight.Bold, 1.6))
        self.btn_export_prof.clicked.connect(self.export_profile)
        
        self.btn_import_prof = HudButton("IMPORT PROFILE", "img_usb", QColor("#1F1F1F"), QColor("#2A2A2A"), TEXT, 14, center_text_only=True)
        self.btn_import_prof.setFixedSize(240, 44)
        self.btn_import_prof.setFont(make_font(self.fonts.univers, 12, QFont.Weight.Bold, 1.6))
        self.btn_import_prof.clicked.connect(self.import_profile)
        
        prof_row.addWidget(self.btn_export_prof)
        prof_row.addWidget(self.btn_import_prof)
        prof_row.addStretch(1)
        fl.addLayout(prof_row)

        rule3 = QFrame()
        rule3.setFixedHeight(1)
        rule3.setStyleSheet("background:#1F1F1F;")
        fl.addWidget(rule3)

        nexus_label = QLabel("NEXUS MODS")
        nexus_label.setFont(make_font(self.fonts.helvetica, 15, QFont.Weight.Bold, 1.2))
        nexus_label.setStyleSheet("color:#888888; background:transparent;")
        fl.addWidget(nexus_label)

        nexus_note = QLabel(
            "Add your personal API key (Nexus account \u2192 Settings \u2192 API Access) to enable "
            "the \"Mod Manager Download\" button on mod pages. Free accounts still work -- the "
            "site's download button is what supplies the one-time key, Premium just isn't required."
        )
        nexus_note.setFont(make_font(self.fonts.helvetica, 12, QFont.Weight.Normal, 0.4))
        nexus_note.setWordWrap(True)
        nexus_note.setStyleSheet("color:#666666; background:transparent;")
        fl.addWidget(nexus_note)

        nexus_key_row = QHBoxLayout()
        nexus_key_row.setSpacing(8)
        self.nexus_key_input = QLineEdit(self.backend.get_nexus_api_key())
        self.nexus_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.nexus_key_input.setFont(make_font(self.fonts.pexico, 13, QFont.Weight.Normal, 0.6))
        self.nexus_key_input.setStyleSheet(
            "QLineEdit{background:rgba(0,0,0,130); border:1px solid #1F1F1F; color:#FFFFFF; padding:12px 14px;}"
            "QLineEdit:focus{border:1px solid #00A3E0;}"
        )
        self.nexus_key_input.editingFinished.connect(self.save_nexus_key)
        nexus_key_row.addWidget(self.nexus_key_input, 1)

        btn_validate_key = QPushButton("VALIDATE")
        btn_validate_key.setFont(make_font(self.fonts.univers, 12, QFont.Weight.Normal, 1.1))
        btn_validate_key.setCursor(interactive_cursor())
        btn_validate_key.setStyleSheet(
            "QPushButton{color:#FFFFFF;background:#111111;border:1px solid #1F1F1F;padding:12px 22px;}"
            "QPushButton:hover{background:#00A3E0;color:#000000;}"
        )
        btn_validate_key.clicked.connect(self.validate_nexus_key)
        nexus_key_row.addWidget(btn_validate_key)
        fl.addLayout(nexus_key_row)

        self.nexus_status_label = QLabel("> NO API KEY SET")
        self.nexus_status_label.setFont(make_font(self.fonts.pexico, 11, QFont.Weight.Normal, 0.8))
        self.nexus_status_label.setWordWrap(True)
        self.nexus_status_label.setStyleSheet("color:#666666; background:transparent;")
        fl.addWidget(self.nexus_status_label)

        # not shown in the UI, keeping the wiring (register_nxm_handler / update_nxm_status) around for later
        self.btn_register_nxm = HudButton(
            "REGISTER \"MOD MANAGER DOWNLOAD\"", "", QColor("#1F1F1F"), QColor("#2A2A2A"),
            TEXT, 14, center_text_only=True,
        )
        self.btn_register_nxm.setFixedSize(340, 44)
        self.btn_register_nxm.setFont(make_font(self.fonts.univers, 12, QFont.Weight.Bold, 1.4))
        self.btn_register_nxm.clicked.connect(self.register_nxm_handler)

        self.nxm_status_label = QLabel("")
        self.nxm_status_label.setFont(make_font(self.fonts.pexico, 11, QFont.Weight.Normal, 0.8))
        self.nxm_status_label.setWordWrap(True)

        rule_hc = QFrame()
        rule_hc.setFixedHeight(1)
        rule_hc.setStyleSheet("background:#1F1F1F;")
        fl.addWidget(rule_hc)

        health_label = QLabel("HEALTH CHECK")
        health_label.setFont(make_font(self.fonts.helvetica, 15, QFont.Weight.Bold, 1.2))
        health_label.setStyleSheet("color:#888888; background:transparent;")
        fl.addWidget(health_label)

        health_note = QLabel(
            "Bundles tools status, missing archives, unmergeable files, and Nexus "
            "connectivity into one report -- useful for troubleshooting, or to share "
            "when asking for help."
        )
        health_note.setFont(make_font(self.fonts.helvetica, 12, QFont.Weight.Normal, 0.4))
        health_note.setWordWrap(True)
        health_note.setStyleSheet("color:#666666; background:transparent;")
        fl.addWidget(health_note)

        self.btn_health_check = HudButton(
            "RUN HEALTH CHECK", "img_heart", QColor("#1F1F1F"), QColor("#2A2A2A"),
            TEXT, 14, center_text_only=True,
        )
        self.btn_health_check.setFixedSize(240, 44)
        self.btn_health_check.setFont(make_font(self.fonts.univers, 12, QFont.Weight.Bold, 1.4))
        self.btn_health_check.clicked.connect(self.run_health_check)
        fl.addWidget(self.btn_health_check)

        rule4 = QFrame()
        rule4.setFixedHeight(1)
        rule4.setStyleSheet("background:#1F1F1F;")
        fl.addWidget(rule4)

        restore_label = QLabel("RESTORE VANILLA")
        restore_label.setFont(make_font(self.fonts.helvetica, 15, QFont.Weight.Bold, 1.2))
        restore_label.setStyleSheet("color:#888888; background:transparent;")
        fl.addWidget(restore_label)

        restore_note = QLabel("patch3.fat and patch3.dat are generated by this tool, not shipped with the game. Deleting them returns Watch_Dogs 2 to a clean, unmodded state.")
        restore_note.setFont(make_font(self.fonts.helvetica, 12, QFont.Weight.Normal, 0.4))
        restore_note.setWordWrap(True)
        restore_note.setStyleSheet("color:#666666; background:transparent;")
        fl.addWidget(restore_note)

        self.btn_restore = HudButton("RESTORE VANILLA", "img_trash", QColor("#FF6B00"), QColor("#FF8C33"), WHITE, 14, center_text_only=True)
        self.btn_restore.setFixedSize(240, 48)
        self.btn_restore.setFont(make_font(self.fonts.univers, 12, QFont.Weight.Bold, 1.8))
        self.btn_restore.clicked.connect(self.restore_vanilla)
        fl.addWidget(self.btn_restore)

        fl.addStretch(1)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        self.stack.addWidget(tab)

    def build_connections(self):
        self.search_bar.input.textChanged.connect(self.filter_cache_items)

    def filter_cache_items(self, query: str):
        if self.cache_stack.currentIndex() == 0:
            self.render_archive_cache_text(query)
            return
        query = query.strip().lower()
        layout = self.patch_content.layout()
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, CacheItem):
                widget.setVisible(not query or query in widget.title_label.text().lower())

    def load_initial_data(self):
        self.refresh_from_backend()

    def refresh_from_backend(self):
        try:
            mods = self.backend.get_installed_mods()
            deploy_state = self.backend.get_deploy_state()
            missing = self.backend.get_missing_sources()
            self.dragging_row = None
            self.last_checkbox_index = None
            self.last_viewed_index = None
            self.mod_list.populate(mods)
            self.filter_mod_list(self.mod_search_input.text())
            self.update_selection_ui(persist=False)
            self.update_archive_cache_view()
            self.populate_deploy_state(deploy_state)
            self.exe_input.setText(self.backend.get_exe_path())
            self.tools_input.setText(self.backend.get_tools_dir())
            self.update_tools_status()
            self.update_nxm_status()
            self.append_log(f"[UI] App synced. Discovered {len(mods)} mods.")
            if missing:
                self.append_log(f"[WARN] {len(missing)} mod(s) have no stored archive and cannot be re-extracted.")
        except Exception as exc:
            self.append_log(f"[ERROR] Failed to sync backend: {exc}")

    def handle_checkbox_toggle(self, row: ModRow, modifiers):
        if row not in self.mod_list.rows: return
        idx = self.mod_list.rows.index(row)
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self.last_checkbox_index is not None:
            start, end = sorted((idx, self.last_checkbox_index))
            target = not row.selected
            for i, item in enumerate(self.mod_list.rows):
                if start <= i <= end: item.set_selected(target)
        else:
            row.set_selected(not row.selected)
        self.last_checkbox_index = idx
        self.update_selection_ui()

    def filter_mod_list(self, text: str):
        query = text.strip().lower()
        for row in self.mod_list.rows:
            if not query:
                row.setVisible(True)
                continue
            haystack = row.mod.get("name", "")
            if row.mod.get("nexus_name"):
                haystack += " " + row.mod["nexus_name"]
            if row.mod.get("nexus_author"):
                haystack += " " + row.mod["nexus_author"]
            row.setVisible(query in haystack.lower())

    def toggle_select_all(self):
        if not self.mod_list.rows: return
        all_selected = all(row.selected for row in self.mod_list.rows)
        for row in self.mod_list.rows: row.set_selected(not all_selected)
        self.last_checkbox_index = None
        self.update_selection_ui()

    def handle_mod_view_click(self, row: ModRow, modifiers):
        if row not in self.mod_list.rows: return
        idx = self.mod_list.rows.index(row)

        if modifiers & Qt.KeyboardModifier.ShiftModifier and self.last_viewed_index is not None:
            start, end = sorted((idx, self.last_viewed_index))
            for i, item in enumerate(self.mod_list.rows):
                item.set_viewing(start <= i <= end)
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            row.set_viewing(not row.viewing)
        else:
            for item in self.mod_list.rows:
                item.set_viewing(item is row)

        self.last_viewed_index = idx
        self.update_archive_cache_view()

    def update_selection_ui(self, persist: bool = True):
        selected = [r for r in self.mod_list.rows if r.selected]
        count = len(selected)
        files = sum(int(r.mod.get("files", 0)) for r in selected)

        self.selected_count.setText(f"{count} Selected")
        if count and count == len(self.mod_list.rows):
            self.select_all_box.set_state(2)
        elif count > 0:
            self.select_all_box.set_state(1)
        else:
            self.select_all_box.set_state(0)

        active_names = [r.mod["name"] for r in selected]
        conflicts = len(self.backend.get_mod_overlap(active_names))
        
        self.mod_list.update_diagnostics(count, files, conflicts)

        enabled = count > 0
        self.btn_remove.setEnabled(enabled)
        self.btn_remove.update()

        if persist:
            self.backend.set_enabled(active_names)

    def remove_selected(self):
        selected = [row for row in self.mod_list.rows if row.selected]
        if not selected: return
        names = [row.mod["name"] for row in selected]

        confirm = QMessageBox(self)
        confirm.setWindowTitle("Remove Mods")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(f"Permanently delete {len(names)} mod(s) from the cache?")
        confirm.setInformativeText("\n".join(names[:10]) + ("\n..." if len(names) > 10 else ""))
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes: return

        self.dragging_row = None
        self.append_log(f"[UI] Removing {len(names)} mod(s)...")
        result = self.backend.delete_mods(names)
        self.append_log(f"[UI] {result}")
        self.last_checkbox_index = None
        self.last_viewed_index = None
        self.refresh_from_backend()

    def begin_row_drag(self, row: ModRow, global_pos: QPoint):
        if row not in self.mod_list.rows: return
        self.mod_list.clear_drag_states()
        self.dragging_row = row
        self.last_checkbox_index = None
        self.last_viewed_index = None
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        row.set_dragging(True)
        self._update_row_drag_position(global_pos)

    def update_row_drag(self, row: ModRow, global_pos: QPoint):
        if self.dragging_row is not row: return
        self._update_row_drag_position(global_pos)

    def _update_row_drag_position(self, global_pos: QPoint):
        row = self.dragging_row
        if row is None or row not in self.mod_list.rows: return
        viewport = self.mod_list.viewport()
        vp = viewport.mapFromGlobal(global_pos)
        margin = 32
        bar = self.mod_list.verticalScrollBar()
        if vp.y() < margin: bar.setValue(bar.value() - 18)
        elif vp.y() > viewport.height() - margin: bar.setValue(bar.value() + 18)

        container_pos = self.mod_list.container.mapFromGlobal(global_pos)
        y = container_pos.y()
        target = len(self.mod_list.rows) - 1
        for i, candidate in enumerate(self.mod_list.rows):
            if candidate is row: continue
            if y < candidate.geometry().center().y():
                target = i
                break

        current = self.mod_list.rows.index(row)
        if target > current: target -= 1
        target = max(0, min(target, len(self.mod_list.rows) - 1))
        if target != current:
            self.mod_list.move_row(row, target)

    def end_row_drag(self, row: ModRow = None):
        if self.dragging_row is None:
            self.mod_list.clear_drag_states()
            return
        if row is not None and self.dragging_row is not row: return
        self.dragging_row = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.mod_list.clear_drag_states()
        self.backend.set_load_order(self.mod_list.ordered_mod_names(), self.mod_list.enabled_mod_names())

    def clear_layout_widgets(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def update_archive_cache_view(self):
        if not hasattr(self, "cache_content"): return
        self._viewing_names = self.mod_list.viewing_mod_names()
        query = self.search_bar.input.text() if hasattr(self, "search_bar") else ""
        self.render_archive_cache_text(query)

    def render_archive_cache_text(self, query: str = ""):
        self.clear_layout_widgets(self.cache_content.layout())
        layout = self.cache_content.layout()
        names = getattr(self, "_viewing_names", [])
        query = (query or "").strip().lower()

        if not names:
            holder = QWidget()
            vl = QVBoxLayout(holder)
            vl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.setSpacing(6)
            vl.addWidget(IconWidget("filezip", QColor("#333333"), 36), alignment=Qt.AlignmentFlag.AlignCenter)
            title = QLabel("NO MOD SELECTED")
            title.setFont(make_font(self.fonts.pexico, 10, QFont.Weight.Normal, 1.5))
            title.setStyleSheet("color:#444444; background:transparent;")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(title)
            note = QLabel("CLICK A MOD TO SEE ITS FILES\nCTRL/SHIFT-CLICK SEVERAL TO CHECK OVERLAPS")
            note.setFont(make_font(self.fonts.pexico, 9, QFont.Weight.Normal, 1.0))
            note.setStyleSheet("color:#3A3A3A; background:transparent;")
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            note.setWordWrap(True)
            vl.addWidget(note)
            layout.addWidget(holder, 1)
            return

        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setFrameShape(QFrame.Shape.NoFrame)
        viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
                # same deal as the logs viewer - PixeloidMono's narrow i/l/t/v blur together
        # on long file paths, use a real monospace font here instead. setFamilies = fallback chain
        viewer_font = QFont()
        viewer_font.setFamilies(["Consolas", "Courier New", "monospace"])
        viewer_font.setPixelSize(13)
        viewer.setFont(viewer_font)
        viewer.setStyleSheet("QPlainTextEdit{background:transparent; border:none; color:#AAAAAA; padding:2px;}")
        viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        if len(names) == 1:
            files = self.backend.get_mod_files(names[0])
            if query: files = [(p, s) for p, s in files if query in p.lower()]
            if not files: body = "NO FILES MATCH YOUR QUERY." if query else "NO FILES CACHED FOR THIS MOD."
            else: body = "\n".join(p for p, _ in files)
            header = f"// {names[0]} \u2014 {len(files)} FILE(S)\n\n"
            viewer.setPlainText(header + body)
        else:
            overlap = self.backend.get_mod_overlap(names)
            if query: overlap = {p: m for p, m in overlap.items() if query in p.lower()}
            if not overlap:
                text = f"// {len(names)} MODS SELECTED\n\nNO OVERLAPPING FILES."
            else:
                lines = [f"// {len(names)} MODS SELECTED \u2014 {len(overlap)} OVERLAPPING FILE(S)\n"]
                for path in sorted(overlap):
                    contributors = overlap[path]
                    ext = os.path.splitext(path)[1].lower()
                    tag = "MERGE" if ext in MERGEABLE_EXTENSIONS else f"OVERWRITE\u2192{contributors[-1]}"
                    lines.append(f"[{tag}]  {path}")
                    lines.append(f"          {', '.join(contributors)}")
                text = "\n".join(lines)
            viewer.setPlainText(text)

        layout.addWidget(viewer, 1)

    def refresh_deploy_state(self):
        try:
            self.populate_deploy_state(self.backend.get_deploy_state())
            self.append_log("[UI] Deploy state refreshed.")
        except Exception as e:
            self.append_log(f"[UI] Deploy state refresh failed: {e}")

    def populate_deploy_state(self, rows):
        self.clear_layout_widgets(self.patch_content.layout())
        layout = self.patch_content.layout()
        for row in rows:
            layout.addWidget(CacheItem(row["name"], row["desc"], self.fonts, row.get("icon", "filezip"), status=row["status"]))
        layout.addStretch(1)

    def switch_cache_tab(self, archive: bool):
        if archive:
            self.btn_archive.setStyleSheet("color:#FFFFFF; background:rgba(0,0,0,90); border:none; border-right:1px solid #1F1F1F; padding:0 12px;")
            self.btn_patch3.setStyleSheet("color:#666666; background:transparent; border:none; padding:0 12px;")
            self.cache_stack.setCurrentIndex(0)
        else:
            self.btn_patch3.setStyleSheet("color:#FFFFFF; background:rgba(0,0,0,90); border:none; padding:0 12px;")
            self.btn_archive.setStyleSheet("color:#666666; background:transparent; border:none; border-right:1px solid #1F1F1F; padding:0 12px;")
            self.cache_stack.setCurrentIndex(1)
        self.filter_cache_items(self.search_bar.input.text())

    def browse_and_import(self):
        files = self.backend.browse_for_archive(self)
        if files: self.process_archives(files)

    def process_archives(self, files):
        if not files: return
        self.append_log(f"[UI] Processing {len(files)} selected archives...")
        names = ", ".join(short_mod_name(Path(f).stem, 30) for f in files[:3])
        if len(files) > 3:
            names += f" (+{len(files) - 3} more)"
        self.show_busy_overlay(True, "EXTRACTING MOD...", names)
        self.start_worker(self.backend.process_dropped_archives, files, on_result=self.on_archive_processed)

    def handle_nxm_link(self, url: str):
        # handles nxm:// links from argv on launch or a live click (via SingleInstanceGuard)
        info = nexus.parse_nxm_url(url)
        if info is None:
            self.append_log(f"[WARN] Ignored unrecognized link: {url[:120]}")
            return
        if not self.backend.get_nexus_api_key():
            QMessageBox.warning(
                self, "Nexus API Key Required",
                "Add your Nexus API key in Settings before using \"Mod Manager Download\"."
            )
            self.switch_tab(3)
            return
        self.append_log(f"[UI] Received Nexus download link for mod {info['mod_id']}, file {info['file_id']}.")
        self.show_busy_overlay(True, "DOWNLOADING FROM NEXUS...", f"mod {info['mod_id']}")
        self.start_worker(self.backend.handle_nxm_download, info, on_result=self.on_nxm_downloaded)

    def on_nxm_downloaded(self, response):
        self.show_busy_overlay(False)
        self.append_log(f"[UI] {response}")
        self.refresh_from_backend()

    def link_mod_to_nexus(self, row):
        # right-click "Link to Nexus Mod..." - backfills name/author/category
        if not self.backend.get_nexus_api_key():
            QMessageBox.warning(
                self, "Nexus API Key Required",
                "Add your Nexus API key in Settings before linking a mod to Nexus."
            )
            self.switch_tab(3)
            return

        current = row.mod.get("nexus_mod_id")
        prompt = (f"Currently linked to mod ID {current}.\n\n"
                 if current else "") + "Paste a Nexus mod page URL, or just the mod ID:"
        text, ok = QInputDialog.getText(self, "Link to Nexus Mod", prompt)
        if not ok or not text.strip():
            return

        mod_folder = row.mod.get("name")
        self.append_log(f"[UI] Looking up Nexus info for '{mod_folder}'...")
        self.start_worker(
            self.backend.link_mod_to_nexus, mod_folder, text.strip(),
            on_result=self.on_mod_linked,
        )

    def on_mod_linked(self, result):
        ok, message = result
        self.append_log(f"[UI] {message}")
        if ok:
            self.refresh_from_backend()

    def identify_all_mods(self):
        if not self.backend.get_nexus_api_key():
            QMessageBox.warning(
                self, "Nexus API Key Required",
                "Add your Nexus API key in Settings before identifying mods."
            )
            self.switch_tab(3)
            return
        self.append_log("[UI] Checking installed mods against Nexus (hash match)...")
        self.btn_identify_all.setEnabled(False)
        self.btn_identify_all.setText("IDENTIFYING...")
        self.start_worker(self.backend.auto_identify_all_mods, on_result=self.on_identify_all_finished)

    def on_identify_all_finished(self, response):
        self.btn_identify_all.setEnabled(True)
        self.btn_identify_all.setText("IDENTIFY ALL")
        self.append_log(f"[UI] {response}")
        self.refresh_from_backend()

    def run_health_check(self):
        self.append_log("[UI] Running health check...")
        self.btn_health_check.set_busy(True, "CHECKING...")
        self.start_worker(self.backend.generate_health_report, on_result=self.on_health_check_finished)

    def on_health_check_finished(self, report: str):
        self.btn_health_check.set_busy(False)
        self.append_log("[UI] Health check complete.")

        dialog = QDialog(self)
        dialog.setWindowTitle("Health Check Report")
        dialog.resize(640, 560)
        dialog.setStyleSheet("QDialog{background:#0A0A0A;}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(report)
        text.setFont(QFont("Consolas", 11))
        text.setStyleSheet(
            "QTextEdit{background:#111111; color:#DDDDDD; border:1px solid #222222; padding:10px;}"
        )
        layout.addWidget(text, 1)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("COPY TO CLIPBOARD")
        btn_copy.setCursor(interactive_cursor())
        btn_copy.setStyleSheet(
            "QPushButton{color:#FFFFFF;background:#111111;border:1px solid #1F1F1F;padding:10px 18px;}"
            "QPushButton:hover{background:#00A3E0;color:#000000;}"
        )
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(report))
        btn_row.addWidget(btn_copy)

        btn_save = QPushButton("SAVE TO FILE")
        btn_save.setCursor(interactive_cursor())
        btn_save.setStyleSheet(btn_copy.styleSheet())
        def save_report():
            path, _ = QFileDialog.getSaveFileName(
                dialog, "Save Health Check Report", "health_check.txt", "Text Files (*.txt)"
            )
            if path:
                Path(path).write_text(report, encoding="utf-8")
                self.append_log(f"[UI] Health check report saved to {path}")
        btn_save.clicked.connect(save_report)
        btn_row.addWidget(btn_save)
        btn_row.addStretch(1)

        btn_close = QPushButton("CLOSE")
        btn_close.setCursor(interactive_cursor())
        btn_close.setStyleSheet(btn_copy.styleSheet())
        btn_close.clicked.connect(dialog.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        dialog.exec()

    def check_for_updates(self):
        if not self.backend.get_nexus_api_key():
            QMessageBox.warning(
                self, "Nexus API Key Required",
                "Add your Nexus API key in Settings before checking for updates."
            )
            self.switch_tab(3)
            return
        self.append_log("[UI] Checking linked mods for updates...")
        self.btn_check_updates.setEnabled(False)
        self.btn_check_updates.setText("CHECKING...")
        self.start_worker(self.backend.check_for_mod_updates, on_result=self.on_updates_checked)

    def on_updates_checked(self, updates):
        self.btn_check_updates.setEnabled(True)
        self.btn_check_updates.setText("CHECK UPDATES")
        if updates:
            names = ", ".join(u["name"] for u in updates[:5])
            more = f" (+{len(updates) - 5} more)" if len(updates) > 5 else ""
            self.append_log(f"[UI] {len(updates)} update(s) available: {names}{more}")
        else:
            self.append_log("[UI] No updates found -- everything linked is up to date.")
        self.refresh_from_backend()

    def on_archive_processed(self, response):
        self.show_busy_overlay(False)
        self.append_log(f"[UI] {response}")
        self.refresh_from_backend()

    def deploy_selected(self):
        active_mods = [row.mod["name"] for row in self.mod_list.rows if row.selected]
        if not active_mods: return

        files = sum(int(r.mod.get("files", 0)) for r in self.mod_list.rows if r.selected)
        self.btn_deploy.set_busy(True, "DEPLOYING...")
        self.mod_list.update_diagnostics(len(active_mods), files, len(self.backend.get_mod_overlap(active_mods)), "> EXECUTING ATOMIC DEPLOYMENT...")
        self.show_deployment_widget(True)
        self.deployment_progress = 0.0
        self._deployment_timer = QTimer(self)
        self._deployment_timer.timeout.connect(self.tick_deployment)
        self._deployment_timer.start(280)
        self.start_worker(self.backend.deploy_active_mods, active_mods, on_result=self.on_deploy_finished)

    def tick_deployment(self):
        if self.deployment_progress < 90:
            self.deployment_progress = min(90, self.deployment_progress + random.uniform(5, 12))
        self.deployment_bar.setGeometry(0, 0, int(144 * self.deployment_progress / 100.0), 4)

    def on_deploy_finished(self, response):
        if hasattr(self, "_deployment_timer"): self._deployment_timer.stop()
        self.deployment_progress = 100
        self.deployment_bar.setGeometry(0, 0, 144, 4)
        self.append_log(f"[DEPLOY] {response}")
        QTimer.singleShot(700, self.finish_deploy_ui)

    def finish_deploy_ui(self):
        self.show_deployment_widget(False)
        self.btn_deploy.set_busy(False)
        active_mods = [r.mod["name"] for r in self.mod_list.rows if r.selected]
        self.mod_list.update_diagnostics(
            len(active_mods),
            sum(int(r.mod.get("files", 0)) for r in self.mod_list.rows if r.selected),
            len(self.backend.get_mod_overlap(active_mods)),
            "> WAITING FOR DEPLOYMENT COMMAND...",
        )
        self.refresh_from_backend()

    def run_game(self):
        method = self.eac_method_dropdown.currentIndex() if self.eac_enabled else -1
        self.start_worker(
            self.backend.run_game,
            self.eac_enabled,
            method,
            on_result=lambda r: self.append_log(f"[UI] {r}")
        )

    def show_deployment_widget(self, visible):
        if not hasattr(self, "deployment_widget"):
            self.deployment_widget = QFrame(self.mod_list.viewport())
            self.deployment_widget.setStyleSheet("background:transparent; border:none;")
            dl = QVBoxLayout(self.deployment_widget)
            dl.setContentsMargins(14, 12, 14, 12)
            dl.setSpacing(8)

            self.deployment_gif = BlendedGifLabel(64, boost=3)
            dl.addWidget(self.deployment_gif, alignment=Qt.AlignmentFlag.AlignCenter)

            self.deployment_track = QFrame()
            self.deployment_track.setFixedSize(144, 4)
            self.deployment_track.setStyleSheet("background:#404040; border:none;")
            self.deployment_bar = QFrame(self.deployment_track)
            self.deployment_bar.setGeometry(0, 0, 0, 4)
            self.deployment_bar.setStyleSheet("background:#FFFFFF; border:none;")
            dl.addWidget(self.deployment_track, alignment=Qt.AlignmentFlag.AlignCenter)

            self.deployment_widget.setFixedSize(190, 125)

        if visible:
            gif_files = []
            for i in range(1, 100):
                asset = find_asset(f"loading_{i}.gif")
                if asset and asset.exists():
                    gif_files.append(asset)
            if gif_files:
                self.deployment_gif.set_movie(QMovie(str(random.choice(gif_files))))

        self.position_deployment_widget()
        self.deployment_widget.setVisible(visible)
        self.deployment_widget.raise_()
        if visible: self.deployment_gif.start()
        else: self.deployment_gif.stop()

    def position_deployment_widget(self):
        if not hasattr(self, "deployment_widget"): return
        vp = self.mod_list.viewport()
        self.deployment_widget.move(
            max(0, (vp.width() - self.deployment_widget.width()) // 2),
            max(0, (vp.height() - self.deployment_widget.height()) // 2),
        )

    def browse_exe(self):
        path = self.backend.browse_exe_path(self)
        if path:
            self.exe_input.setText(path)
            self.refresh_deploy_state()

    def toggle_eac(self, checked):
        self.eac_enabled = checked
        self.btn_run.setToolTip(f"Run Game (EAC {'OFF' if self.eac_enabled else 'ON'})")
        self.btn_run.update()
        for box in (self.eac_checkbox, self.eac_checkbox_main):
            if box.isChecked() != checked:
                box.blockSignals(True)
                box.setChecked(checked)
                box.blockSignals(False)
        self.backend.save_config(eac_enabled=checked)

    def save_eac_method(self, index):
        self.backend.save_config(eac_method=index)

    def append_log(self, message: str):
        self.logs.append(message)
        if not hasattr(self, "log_view"): return
        color = "#55C98A"
        upper = message.upper()
        if "ERROR" in upper or "CRITICAL" in upper: color = "#FF0000"
        elif "WARN" in upper: color = "#FF6B00"
        elif "SUCCESS" in upper or "MERGE STATUS" in upper or "COMPLETE" in upper or "[UI]" in upper: color = "#00A3E0"

        safe_message = html.escape(message)
        # Consolas here, not Pexico - Pexico's missing glyphs mangle log text
        html_msg = f'<span style="color:{color}; font-family: Consolas, \'Courier New\', monospace; font-size:13px;">{safe_message}</span>'
        self.log_view.append(html_msg)

        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def export_logs(self):
        result = self.backend.export_logs("\n".join(self.logs))
        self.append_log(f"[UI] {result}")

    def export_profile(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Profile", "wd2_mod_profile.zip", "Zip Files (*.zip)")
        if path:
            result = self.backend.export_profile(path)
            self.append_log(f"[UI] {result}")
            
    def import_profile(self):
        allowed = {LOAD_ORDER_FILE.name, CONFLICT_RULES_FILE.name}
        path, _ = QFileDialog.getOpenFileName(self, "Import Profile", "", "Zip Files (*.zip)")
        if path:
            result = self.backend.import_profile(path)
            self.append_log(f"[UI] {result}")
            self.refresh_from_backend()

    def start_worker(self, fn, *args, on_result=None, **kwargs):
        if self.worker and self.worker.isRunning():
            self.append_log("[WARN] Another operation is still running. Please wait.")
            return
        self.worker = Worker(fn, *args, **kwargs)
        if on_result: self.worker.result.connect(on_result)
        self.worker.failed.connect(lambda msg: self.on_worker_error(msg, on_result))
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.finished.connect(self._clear_worker)
        self.worker.start()

    def _clear_worker(self):
        self.worker = None

    def on_worker_error(self, msg, on_result):
        self.append_log(f"[ERROR] {msg}")
        if on_result:
            try: on_result(f"Operation failed: {msg}")
            except Exception: pass

    def switch_tab(self, index):
        old_index = self.stack.currentIndex()
        if old_index == index: return
        
        direction = 1 if index > old_index else -1
        
        self.stack.setCurrentIndex(index)
        widget = self.stack.currentWidget()

        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        effect.setOpacity(0.0)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(300)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(lambda: widget.setGraphicsEffect(None))
        fade.start()
        self._tab_fade = fade

        home = widget.pos()
        slide = QPropertyAnimation(widget, b"pos", self)
        slide.setDuration(350)
        slide.setStartValue(QPoint(home.x() + (30 * direction), home.y()))
        slide.setEndValue(home)
        slide.setEasingCurve(QEasingCurve.Type.OutExpo)
        slide.start()
        self._tab_slide = slide

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "drop_overlay"):
            self.drop_overlay.setGeometry(self.rect())
        self.position_deployment_widget()
        self._position_plus_button()

    def _has_file_urls(self, event) -> bool:
        mime = event.mimeData()
        if not mime or not mime.hasUrls(): return False
        for url in mime.urls():
            if url.isLocalFile(): return True
        return False

    def eventFilter(self, obj, event):
        if getattr(self, "_is_busy", False):
            return super().eventFilter(obj, event)

        etype = event.type()
        if etype == QEvent.Type.DragEnter:
            if self._has_file_urls(event):
                self._external_drag_active = True
                self.drop_overlay.setGeometry(self.rect())
                self.drop_overlay.show_drop()
                event.accept()
                return True
        elif etype == QEvent.Type.DragMove:
            if self._external_drag_active:
                event.accept()
                return True
        elif etype == QEvent.Type.DragLeave:
            if self._external_drag_active:
                self._external_drag_active = False
                self.drop_overlay.hide_overlay()
                event.accept()
                return True
        elif etype == QEvent.Type.Drop:
            if self._external_drag_active and self._has_file_urls(event):
                self._external_drag_active = False
                self.drop_overlay.hide_overlay()
                paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
                allowed = {".zip", ".rar", ".7z", ".fat"}
                paths = [p for p in paths if Path(p).suffix.lower() in allowed]
                if paths: self.process_archives(paths)
                event.accept()
                return True
        elif etype == QEvent.Type.MouseButtonRelease:
            if self.dragging_row is not None:
                self.end_row_drag(self.dragging_row)
        return super().eventFilter(obj, event)

    def show_busy_overlay(self, show: bool, text="WORKING...", detail=""):
        self.drop_overlay.setGeometry(self.rect())
        if show:
            self._is_busy = True
            self.drop_overlay.raise_()
            self.drop_overlay.show_busy(text, detail)
        else:
            self._is_busy = False
            self.drop_overlay.hide_overlay()


def main():
    if sys.platform == "win32":
        try:
            myappid = 'xaex1.watchdogs2.modmanager.1' 
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

    # Windows argv order for nxm:// links isn't guaranteed, so scan instead of indexing.
    nxm_arg = next((a for a in sys.argv[1:] if a.lower().startswith("nxm://")), None)

    app = QApplication(sys.argv)
    app.setApplicationName("Watch_Dogs 2 Mod Manager")
    app.setStyle("Fusion")

    icon_path = find_asset("wd2mmicon.ico")
    if icon_path and icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    guard = nexus.SingleInstanceGuard()
    if not guard.try_acquire():
        # already running elsewhere, hand off the link and bail without opening a 2nd window
        if nxm_arg:
            guard.send_to_running_instance(nxm_arg)
        return 0

    window = WD2ModManagerNative()
    guard.nxm_received.connect(window.handle_nxm_link)
    window._instance_guard = guard  # gotta keep a ref or it gets gc'd
    window.show()

    if nxm_arg:
        # defer until window/backend are actually up
        QTimer.singleShot(0, lambda: window.handle_nxm_link(nxm_arg))

    return app.exec()

if __name__ == "__main__":
    sys.exit(main())