"""Auto Press — Fluent Design UI.

QFluentWidgets on top of PySide6 for a Windows 11 Settings-app look.
Self-contained: engine worker, drag-capture overlays, monitor picker and
status widgets all live in this file.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEventLoop,
    QObject,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSystemTrayIcon,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# qfluentwidgets prints a "QFluentWidgets Pro is now released" banner from
# common/config.py at import time. Silence it by redirecting stdout only
# during the first qfluentwidgets import; subsequent imports hit the module
# cache and don't re-fire the print.
import contextlib as _contextlib
import io as _io

with _contextlib.redirect_stdout(_io.StringIO()):
    from qfluentwidgets import (
        BodyLabel,
        CaptionLabel,
        CheckBox,
        ComboBox,
        FluentIcon as FIF,
        HeaderCardWidget,
        LineEdit,
        PlainTextEdit as FluentPlainTextEdit,
        PrimaryPushButton,
        PushButton,
        SegmentedWidget,
        SimpleCardWidget,
        StrongBodyLabel,
        SubtitleLabel,
        TableWidget,
        Theme,
        ToolButton,
        setTheme,
        setThemeColor,
    )
    from qfluentwidgets.components.widgets.spin_box import DoubleSpinBox
    from qfluentwidgets.components.widgets.menu import RoundMenu as _RoundMenu

# RoundMenu (parent of ComboBoxMenu) sets contentsMargins(12, 8, 12, 12) on
# its layout, which creates a gap between its painted outer rect and the
# inner item view — the "box inside a box" look that combo dropdowns get.
# Patching the margin to 0 once means every popup looks like a single
# unified surface instead.
_orig_round_menu_init = _RoundMenu.__init__


def _patched_round_menu_init(self, *args, **kwargs):
    _orig_round_menu_init(self, *args, **kwargs)
    layout = self.layout()
    if layout is not None:
        layout.setContentsMargins(0, 0, 0, 0)


_RoundMenu.__init__ = _patched_round_menu_init


from press_core import save_gray_image
from press_engine import (
    build_runtime_rules,
    capture_screen_gray,
    capture_screen_rgb,
    dominant_rgb,
    ensure_vision,
    evaluate_rule_on_frame,
    evaluate_rules,
    execute_matches,
)
from press_store import (
    ACTION_CLICK,
    ACTION_CLICK_TYPE_ENTER,
    ACTION_TYPES,
    CONFIG_PATH,
    MATCHER_COLOR,
    MATCHER_TEMPLATE,
    default_rule,
    list_template_files,
    load_config,
    resolve_template_path,
    save_config,
    serialize_template_path,
    template_asset_path,
)


IS_WINDOWS = sys.platform.startswith("win")

# ---- Win32 helpers (capture coordinates always in physical pixels) --

if IS_WINDOWS:
    from ctypes import wintypes as _wintypes

    _user32 = ctypes.windll.user32

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    _MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        _wintypes.HMONITOR,
        _wintypes.HDC,
        ctypes.POINTER(_RECT),
        _wintypes.LPARAM,
    )

    def enumerate_physical_monitors() -> list[tuple[int, int, int, int]]:
        """Every connected monitor's rect in physical pixels (per-monitor-v2 context)."""
        monitors: list[tuple[int, int, int, int]] = []

        def _proc(_hm, _hdc, lprect, _lp):
            r = lprect.contents
            monitors.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
            return 1

        _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_proc), 0)
        return monitors

    def physical_cursor_pos() -> tuple[int, int]:
        """Cursor position in physical screen pixels, consistent with ImageGrab."""
        p = _wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(p))
        return int(p.x), int(p.y)

else:

    def enumerate_physical_monitors() -> list[tuple[int, int, int, int]]:
        return [
            (s.geometry().left(), s.geometry().top(), s.geometry().width(), s.geometry().height())
            for s in QGuiApplication.screens()
        ]

    def physical_cursor_pos() -> tuple[int, int]:
        from PySide6.QtGui import QCursor

        pos = QCursor.pos()
        return pos.x(), pos.y()


# ---- shared colors / status widgets ---------------------------------

STATUS_RUNNING = "#22c55e"
STATUS_STOPPED = "#ef4444"
RECT_STROKE = "#22c55e"

WINDOWS_ACCENT = "#2b7de9"


class StatusDot(QWidget):
    """A small filled circle used as a status indicator."""

    def __init__(self, diameter: int = 10):
        super().__init__()
        self._diameter = diameter
        self._color = QColor(STATUS_STOPPED)
        self.setFixedSize(diameter + 2, diameter + 2)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(1, 1, self._diameter, self._diameter)


def _make_dot_icon(color: QColor) -> QIcon:
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(color)
    p.setPen(QPen(QColor(20, 20, 22), 3))
    p.drawEllipse(6, 6, size - 12, size - 12)
    p.end()
    return QIcon(pm)


# ---- hotkey picker --------------------------------------------------

# Win32 RegisterHotKey modifier flags.
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008

# Non-letter / non-digit Qt.Key -> Win32 VK mapping.
_QT_KEY_TO_VK = {
    Qt.Key_F1: 0x70, Qt.Key_F2: 0x71, Qt.Key_F3: 0x72, Qt.Key_F4: 0x73,
    Qt.Key_F5: 0x74, Qt.Key_F6: 0x75, Qt.Key_F7: 0x76, Qt.Key_F8: 0x77,
    Qt.Key_F9: 0x78, Qt.Key_F10: 0x79, Qt.Key_F11: 0x7A, Qt.Key_F12: 0x7B,
    Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22,
    Qt.Key_Home: 0x24, Qt.Key_End: 0x23,
    Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E,
    Qt.Key_Tab: 0x09, Qt.Key_Backtab: 0x09,
    Qt.Key_Space: 0x20, Qt.Key_Return: 0x0D, Qt.Key_Enter: 0x0D,
    Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28,
    Qt.Key_Escape: 0x1B, Qt.Key_Backspace: 0x08,
}

# VK -> short display name. A-Z / 0-9 / F1-F12 are generated on the fly.
_VK_DISPLAY = {
    0x21: "PgUp", 0x22: "PgDn",
    0x24: "Home", 0x23: "End",
    0x2D: "Ins", 0x2E: "Del",
    0x09: "Tab", 0x20: "Space", 0x0D: "Enter",
    0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓",
    0x1B: "Esc", 0x08: "Backspace",
}


def _qt_key_to_vk(qt_key: int) -> int:
    if Qt.Key_A <= qt_key <= Qt.Key_Z:
        return int(qt_key)  # matches VK_A..VK_Z
    if Qt.Key_0 <= qt_key <= Qt.Key_9:
        return int(qt_key)  # matches VK_0..VK_9
    return _QT_KEY_TO_VK.get(qt_key, 0)


def _vk_name(vk: int) -> str:
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x7B:
        return f"F{vk - 0x70 + 1}"
    return _VK_DISPLAY.get(vk, f"VK{vk:02X}")


def _format_hotkey(vk: int, mods: int) -> str:
    parts: list[str] = []
    if mods & _MOD_CONTROL:
        parts.append("Ctrl")
    if mods & _MOD_ALT:
        parts.append("Alt")
    if mods & _MOD_SHIFT:
        parts.append("Shift")
    if mods & _MOD_WIN:
        parts.append("Win")
    parts.append(_vk_name(vk))
    return "+".join(parts)


class HotkeyButton(PushButton):
    """Push-button that shows the current global hotkey and captures a new one on click."""

    hotkey_changed = Signal(int, int)  # (vk, mods) — Win32 codes.

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(132)
        self.setFocusPolicy(Qt.StrongFocus)
        self._capturing = False
        self._vk = 0x22
        self._mods = 0
        self._refresh()
        self.clicked.connect(self._begin_capture)

    def set_hotkey(self, vk: int, mods: int) -> None:
        self._vk = int(vk)
        self._mods = int(mods)
        self._refresh()

    def _begin_capture(self) -> None:
        self._capturing = True
        self.setText("Press keys…")
        self.setFocus()

    def _end_capture(self) -> None:
        self._capturing = False
        self._refresh()

    def _refresh(self) -> None:
        self.setText(_format_hotkey(self._vk, self._mods))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        # Ignore bare modifier presses — we're waiting for the payload key.
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return
        if key == Qt.Key_Escape:
            self._end_capture()
            return
        vk = _qt_key_to_vk(key)
        if vk == 0:
            # Can't express this key as a Win32 VK; keep waiting.
            return
        mods = event.modifiers()
        win_mods = 0
        if mods & Qt.ControlModifier:
            win_mods |= _MOD_CONTROL
        if mods & Qt.AltModifier:
            win_mods |= _MOD_ALT
        if mods & Qt.ShiftModifier:
            win_mods |= _MOD_SHIFT
        if mods & Qt.MetaModifier:
            win_mods |= _MOD_WIN
        self._vk = vk
        self._mods = win_mods
        self._end_capture()
        self.hotkey_changed.emit(vk, win_mods)


# ---- engine worker --------------------------------------------------


class EngineWorker(QObject):
    tick_done = Signal(list, list, float)
    tick_error = Signal(str)
    running_changed = Signal(bool)
    needs_rules = Signal()

    def __init__(self, cfg_snapshot):
        super().__init__()
        self._cfg_snapshot = cfg_snapshot
        self._running = False
        self._stop = False
        self._interval = 10.0
        self._lock = threading.Lock()

    def set_interval(self, seconds: float) -> None:
        with self._lock:
            self._interval = max(0.1, float(seconds))

    def set_running(self, on: bool) -> None:
        with self._lock:
            self._running = bool(on)
        self.running_changed.emit(bool(on))

    def request_stop(self) -> None:
        self._stop = True

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def run(self) -> None:
        # Pin PER_MONITOR_AWARE_V2 on this worker thread once. Sticky for the
        # thread's lifetime, so every capture + click iteration agrees on
        # physical pixel coordinates.
        if IS_WINDOWS:
            try:
                ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            except Exception:
                pass
        while not self._stop:
            if not self.is_running():
                time.sleep(0.1)
                continue
            cfg = self._cfg_snapshot()
            try:
                runtime_rules = build_runtime_rules(cfg)
            except Exception as exc:
                self.tick_error.emit(f"runtime rules unavailable: {exc}")
                self.set_running(False)
                continue
            if not runtime_rules:
                self.needs_rules.emit()
                self.set_running(False)
                continue
            try:
                results, actions = evaluate_rules(runtime_rules)
                if actions:
                    execute_matches(actions)
                self.tick_done.emit(results, actions, self._get_interval())
            except Exception as exc:
                self.tick_error.emit(f"tick failed: {exc}")
            end = time.monotonic() + self._get_interval()
            while time.monotonic() < end and not self._stop:
                if not self.is_running():
                    break
                time.sleep(0.05)

    def _get_interval(self) -> float:
        with self._lock:
            return self._interval


# ---- drag capture (per-monitor overlays, physical coords) -----------


class CaptureOverlay(QWidget):
    """One overlay per monitor. Controller stores start/current in PHYSICAL px."""

    def __init__(self, qt_screen, physical_rect: tuple[int, int, int, int], controller: "CaptureController"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setCursor(Qt.CrossCursor)
        self._qt_screen = qt_screen
        self._physical_rect = physical_rect
        self._controller = controller
        self.setGeometry(qt_screen.geometry())

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 100))
        if self._controller.start is None:
            return
        sx, sy = self._controller.start
        cx, cy = self._controller.current
        left = min(sx, cx); right = max(sx, cx)
        top = min(sy, cy); bottom = max(sy, cy)
        ml, mt, mw, mh = self._physical_rect
        mr, mb = ml + mw, mt + mh
        il = max(left, ml); it = max(top, mt)
        ir = min(right, mr); ib = min(bottom, mb)
        if ir <= il or ib <= it:
            return
        dpr = self._qt_screen.devicePixelRatio() or 1.0
        inner = QRectF(
            (il - ml) / dpr,
            (it - mt) / dpr,
            (ir - il) / dpr,
            (ib - it) / dpr,
        )
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(inner, QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setPen(QPen(QColor(RECT_STROKE), 2))
        p.drawRect(inner.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._controller.on_press(*physical_cursor_pos())

    def mouseMoveEvent(self, _event) -> None:  # noqa: N802
        if self._controller.start is not None:
            self._controller.on_motion(*physical_cursor_pos())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._controller.start is not None:
            self._controller.on_release(*physical_cursor_pos())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self._controller.cancel()


class CaptureController(QObject):
    done = Signal(object)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.start: Optional[tuple[int, int]] = None
        self.current: Optional[tuple[int, int]] = None
        self._overlays: list[CaptureOverlay] = []

    def begin(self) -> None:
        self.start = None
        self.current = None
        self._overlays = []
        qt_screens = QGuiApplication.screens()
        physical = enumerate_physical_monitors()
        for i, screen in enumerate(qt_screens):
            if i < len(physical):
                pr = physical[i]
            else:
                g = screen.geometry()
                d = screen.devicePixelRatio() or 1.0
                pr = (int(g.left() * d), int(g.top() * d), int(g.width() * d), int(g.height() * d))
            overlay = CaptureOverlay(screen, pr, self)
            overlay.show()
            self._overlays.append(overlay)
        if self._overlays:
            first = self._overlays[0]
            first.activateWindow(); first.raise_(); first.setFocus()

    def _redraw(self) -> None:
        for ov in self._overlays:
            ov.update()

    def on_press(self, x: int, y: int) -> None:
        self.start = (x, y); self.current = (x, y); self._redraw()

    def on_motion(self, x: int, y: int) -> None:
        self.current = (x, y); self._redraw()

    def on_release(self, x: int, y: int) -> None:
        if self.start is None:
            self._cleanup(); self.done.emit(None); return
        sx, sy = self.start
        left, right = min(sx, x), max(sx, x)
        top, bottom = min(sy, y), max(sy, y)
        w, h = right - left, bottom - top
        self._cleanup()
        self.done.emit([left, top, w, h] if w >= 5 and h >= 5 else None)

    def cancel(self) -> None:
        self._cleanup(); self.done.emit(None)

    def _cleanup(self) -> None:
        for ov in self._overlays:
            ov.close(); ov.deleteLater()
        self._overlays = []


def capture_drag_bbox(parent: QObject) -> Optional[list[int]]:
    loop = QEventLoop()
    result: dict = {"bbox": None}
    controller = CaptureController(parent)

    def on_done(bbox) -> None:
        result["bbox"] = bbox
        loop.quit()

    controller.done.connect(on_done)
    controller.begin()
    loop.exec()
    controller.deleteLater()
    return result["bbox"]


# ---- monitor picker -------------------------------------------------


class MonitorPickDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Pick Monitor")
        self.setModal(True)
        self.selected: Optional[list[int]] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        title = QLabel("Restrict the scan to a single monitor:")
        layout.addWidget(title)

        for i, rect in enumerate(enumerate_physical_monitors(), start=1):
            left, top, width, height = rect
            bbox = [left, top, width, height]
            btn = QPushButton(
                f"Monitor {i}   ·   {width} × {height}   ·   ({left}, {top})"
            )
            btn.setMinimumWidth(340)
            btn.clicked.connect(lambda _c=False, b=bbox: self._choose(b))
            layout.addWidget(btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose(self, bbox: list[int]) -> None:
        self.selected = bbox
        self.accept()


class _VLine(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.VLine)
        self.setFixedWidth(1)
        self.setStyleSheet("background: rgba(255,255,255,24); border: none;")


class CollapsibleCard(HeaderCardWidget):
    """HeaderCardWidget with a chevron toggle that hides/shows the body."""

    expanded_changed = Signal(bool)

    def __init__(self, title: str = "", parent=None, expanded: bool = True):
        super().__init__(parent)
        self.setTitle(title)

        self._toggle_btn = ToolButton(FIF.UP, self)
        self._toggle_btn.setFixedSize(28, 28)
        self._toggle_btn.setToolTip("Collapse / expand")
        self._toggle_btn.clicked.connect(self._toggle)

        # HeaderCardWidget's headerLayout holds the title label; push our
        # chevron to the far right edge.
        self.headerLayout.addStretch(1)
        self.headerLayout.addWidget(self._toggle_btn)

        self._expanded = True
        if not expanded:
            self._toggle()

        # Let the header remain clickable for toggling too
        self.headerView.setCursor(Qt.PointingHandCursor)
        self.headerView.mousePressEvent = lambda _e: self._toggle()

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self.view.setVisible(self._expanded)
        self._toggle_btn.setIcon(FIF.UP if self._expanded else FIF.DOWN)
        self.expanded_changed.emit(self._expanded)

    def setExpanded(self, expanded: bool) -> None:
        if self._expanded != expanded:
            self._toggle()

    def isExpanded(self) -> bool:  # noqa: N802
        return self._expanded


class MainWindow(QMainWindow):
    hotkey_triggered = Signal()

    CHROME_HEIGHT = 120

    def __init__(self, initial_seconds: float):
        super().__init__()
        self.hotkey_triggered.connect(self._toggle_running, Qt.QueuedConnection)

        setTheme(Theme.DARK)
        setThemeColor(WINDOWS_ACCENT)

        self.setWindowTitle("Auto Press")
        self.setMinimumSize(620, 240)
        self.resize(1120, 720)

        # Background matches Fluent dark surface. The rules here are scoped by
        # widget class; a blanket "QWidget { background: transparent }" would
        # bleed through into the combo popup and make it look like a box
        # inside a box.
        self.setStyleSheet(
            "QMainWindow { background: #1b1b1f; color: #e4e4e7; }"
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            "QSplitter { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }"
            "QScrollBar::handle:vertical { background: #3a3a42; border-radius: 5px; min-height: 30px; }"
            "QScrollBar::handle:vertical:hover { background: #4b4b54; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        # State
        self._cfg = load_config()
        self._cfg["interval_seconds"] = float(initial_seconds)
        save_config(self._cfg)
        self._cfg_lock = threading.Lock()
        self._last_scores: dict = {}
        self._next_tick_at: Optional[float] = None
        self._running = False
        self._quitting = False
        self._remembered_body_h = 600
        # Last expanded heights for the left splitter cards; restored on
        # collapse → expand round-trips.
        self._left_remembered = {"rules": 380, "log": 200}

        self._icon_running = _make_dot_icon(QColor(STATUS_RUNNING))
        self._icon_stopped = _make_dot_icon(QColor(STATUS_STOPPED))
        self.setWindowIcon(self._icon_stopped)

        self._build_central(initial_seconds)
        self._build_tray()

        # Engine worker
        self._worker = EngineWorker(self._snapshot_cfg)
        self._worker_thread = QThread(self)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.tick_done.connect(self._on_tick_done)
        self._worker.tick_error.connect(self._on_worker_error)
        self._worker.running_changed.connect(self._on_running_changed)
        self._worker.needs_rules.connect(self._on_needs_rules)
        self._worker_thread.start()

        # Countdown
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(100)
        self._countdown_timer.timeout.connect(self._update_countdown)
        self._countdown_timer.start()

        # Hotkey
        self._hotkey_vk = int(self._cfg.get("hotkey_vk", 0x22))
        self._hotkey_mods = int(self._cfg.get("hotkey_mods", 0))
        self._hotkey_stop = threading.Event()
        self._hotkey_thread_id: dict[str, int | None] = {"tid": None}
        # Sync the picker with persisted config before we spawn the thread.
        self._hotkey_button.set_hotkey(self._hotkey_vk, self._hotkey_mods)
        if IS_WINDOWS:
            self._start_hotkey_thread()

        self._refresh_template_choices()
        self._refresh_rule_list(0 if self._cfg.get("rules") else None)
        self._set_running_status(False)
        self._log(f"[ready] loaded {CONFIG_PATH}")

    # ---------- layout ----------

    def _build_central(self, initial_seconds: float) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        root.addWidget(self._build_command_bar(initial_seconds))

        # Body: Horizontal split. Left column stacks Rules over Log.
        self._body_splitter = QSplitter(Qt.Horizontal)
        self._body_splitter.setChildrenCollapsible(False)
        self._body_splitter.setHandleWidth(6)

        self._left_splitter = QSplitter(Qt.Vertical)
        self._left_splitter.setChildrenCollapsible(False)
        self._left_splitter.setHandleWidth(6)
        rules_card = self._build_rules_card()
        self._log_panel = self._build_log_panel()
        rules_card.setMinimumHeight(140)
        self._log_panel.setMinimumHeight(110)
        self._left_splitter.addWidget(rules_card)
        self._left_splitter.addWidget(self._log_panel)
        self._left_splitter.setStretchFactor(0, 2)
        self._left_splitter.setStretchFactor(1, 1)
        self._left_splitter.setSizes([380, 200])

        self._body_splitter.addWidget(self._left_splitter)
        self._body_splitter.addWidget(self._build_editor_scroll())
        self._body_splitter.setStretchFactor(0, 1)
        self._body_splitter.setStretchFactor(1, 2)
        self._body_splitter.setSizes([340, 740])

        root.addWidget(self._body_splitter, 1)
        self.setCentralWidget(central)

    def _build_command_bar(self, initial_seconds: float) -> QWidget:
        bar = SimpleCardWidget()
        bar.setFixedHeight(62)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)

        self._start_btn = PrimaryPushButton("Start", self, FIF.PLAY)
        self._start_btn.setFixedWidth(108)
        self._start_btn.clicked.connect(self._toggle_running)
        lay.addWidget(self._start_btn)

        lay.addWidget(CaptionLabel("Hotkey"))
        self._hotkey_button = HotkeyButton()
        self._hotkey_button.hotkey_changed.connect(self._on_hotkey_changed)
        lay.addWidget(self._hotkey_button)

        lay.addWidget(_VLine())

        lay.addWidget(CaptionLabel("Interval"))
        self._interval_spin = DoubleSpinBox()
        self._interval_spin.setRange(0.1, 86400.0)
        self._interval_spin.setDecimals(1)
        self._interval_spin.setSingleStep(0.5)
        self._interval_spin.setValue(float(initial_seconds))
        self._interval_spin.setFixedWidth(130)
        self._interval_spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._interval_spin.valueChanged.connect(self._on_interval_changed)
        lay.addWidget(self._interval_spin)
        lay.addWidget(CaptionLabel("s"))

        lay.addWidget(_VLine())

        self._status_dot = StatusDot()
        lay.addWidget(self._status_dot)
        self._status_label = StrongBodyLabel("Stopped")
        self._status_label.setStyleSheet(f"color: {STATUS_STOPPED};")
        lay.addWidget(self._status_label)

        self._countdown_label = CaptionLabel("")
        self._countdown_label.setStyleSheet("color: #a1a1aa; font-family: Consolas; font-size: 11pt;")
        self._countdown_label.setFixedWidth(56)
        lay.addSpacing(4)
        lay.addWidget(self._countdown_label)

        self._action_status = CaptionLabel("")
        self._action_status.setStyleSheet("color: #a1a1aa; font-style: italic;")
        # When the toolbar is squeezed horizontally the action-status label
        # should collapse before any fixed-width control loses a pixel.
        # Ignored horizontal policy tells Qt this widget has no min width.
        self._action_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._action_status.setMinimumWidth(0)
        lay.addWidget(self._action_status)

        lay.addStretch(1)

        self._collapse_btn = ToolButton(FIF.UP)
        self._collapse_btn.setToolTip("Collapse to toolbar only")
        self._collapse_btn.setFixedSize(32, 28)
        self._collapse_btn.clicked.connect(self._toggle_body_collapsed)
        lay.addWidget(self._collapse_btn)

        return bar

    def _build_rules_card(self) -> QWidget:
        card = CollapsibleCard("Rules")
        card.expanded_changed.connect(self._on_left_card_toggled)
        self._rules_card = card
        card.setMinimumWidth(260)
        body = QVBoxLayout()
        body.setContentsMargins(2, 0, 2, 0)
        body.setSpacing(8)

        self._rules_list = TableWidget()
        self._rules_list.setColumnCount(3)
        self._rules_list.setHorizontalHeaderLabels(["Name", "On", "Action"])
        self._rules_list.verticalHeader().setVisible(False)
        self._rules_list.horizontalHeader().setVisible(True)
        self._rules_list.horizontalHeader().setHighlightSections(False)
        self._rules_list.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._rules_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._rules_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._rules_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._rules_list.setShowGrid(False)
        self._rules_list.setBorderVisible(True)
        self._rules_list.setBorderRadius(8)
        self._rules_list.setWordWrap(False)
        self._rules_list.verticalHeader().setDefaultSectionSize(32)

        header = self._rules_list.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self._rules_list.setColumnWidth(1, 36)
        self._rules_list.setColumnWidth(2, 140)

        self._rules_list.itemSelectionChanged.connect(
            lambda: self._load_selected_rule(self._rules_list.currentRow())
        )
        body.addWidget(self._rules_list, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self._add_btn = PushButton(FIF.ADD, "Add")
        self._delete_btn = PushButton(FIF.DELETE, "Delete")
        self._up_btn = ToolButton(FIF.UP)
        self._down_btn = ToolButton(FIF.DOWN)
        self._add_btn.clicked.connect(self._add_rule)
        self._delete_btn.clicked.connect(self._delete_rule)
        self._up_btn.clicked.connect(lambda: self._move_rule(-1))
        self._down_btn.clicked.connect(lambda: self._move_rule(1))
        buttons.addWidget(self._add_btn)
        buttons.addWidget(self._delete_btn)
        buttons.addStretch(1)
        buttons.addWidget(self._up_btn)
        buttons.addWidget(self._down_btn)
        body.addLayout(buttons)

        card.viewLayout.addLayout(body)
        return card

    def _build_editor_scroll(self) -> QWidget:
        outer = QWidget()
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        content.setMinimumWidth(340)
        stack = QVBoxLayout(content)
        stack.setContentsMargins(2, 0, 6, 0)
        stack.setSpacing(12)

        stack.addWidget(self._build_basics_card())
        stack.addWidget(self._build_template_card())
        stack.addWidget(self._build_scope_card())
        stack.addWidget(self._build_editor_actions())
        stack.addStretch(1)

        scroll.setWidget(content)
        outer_lay.addWidget(scroll, 1)
        return outer

    def _build_basics_card(self) -> QWidget:
        card = CollapsibleCard("Basics")

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        # Header labels over every input column (Enabled's label sits inside
        # the checkbox, so col 1 has no header).
        self._name_label = CaptionLabel("Name")
        self._action_label = CaptionLabel("Action")
        self._text_label = CaptionLabel("Text")
        grid.addWidget(self._name_label, 0, 0)
        grid.addWidget(self._action_label, 0, 2)
        grid.addWidget(self._text_label, 0, 3)

        self._name_edit = LineEdit()
        self._enabled_check = CheckBox("Enabled")
        self._action_combo = ComboBox()
        self._action_combo.addItems(ACTION_TYPES)
        self._action_combo.currentTextChanged.connect(self._update_action_fields)
        self._text_edit = LineEdit()
        self._text_edit.setPlaceholderText("typed before Enter")

        grid.addWidget(self._name_edit, 1, 0)
        grid.addWidget(self._enabled_check, 1, 1)
        grid.addWidget(self._action_combo, 1, 2)
        grid.addWidget(self._text_edit, 1, 3)

        grid.setColumnStretch(0, 3)  # Name — wider
        grid.setColumnStretch(1, 0)  # Enabled — content-width
        grid.setColumnStretch(2, 2)  # Action
        grid.setColumnStretch(3, 3)  # Text

        card.viewLayout.addLayout(grid)
        return card

    def _build_template_card(self) -> QWidget:
        card = CollapsibleCard("Match")

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        # Matcher toggle: shows whether the active rule clicks on a template
        # (pattern) or on a color. Tapping a side switches the rule's matcher
        # and surfaces the right preview / inputs.
        self._matcher_seg = SegmentedWidget()
        self._matcher_seg.addItem(
            MATCHER_TEMPLATE, "Pattern", lambda: self._set_matcher(MATCHER_TEMPLATE)
        )
        self._matcher_seg.addItem(
            MATCHER_COLOR, "Color", lambda: self._set_matcher(MATCHER_COLOR)
        )
        self._matcher_seg.setCurrentItem(MATCHER_TEMPLATE)
        seg_row = QHBoxLayout()
        seg_row.setContentsMargins(0, 0, 0, 0)
        seg_row.addWidget(self._matcher_seg)
        seg_row.addStretch(1)
        grid.addLayout(seg_row, 0, 0, 1, 2)

        self._template_combo = ComboBox()
        self._template_combo.setMinimumWidth(180)
        self._template_combo.currentTextChanged.connect(self._on_template_selected)
        self._rename_template_btn = ToolButton(FIF.EDIT)
        self._rename_template_btn.setToolTip("Rename selected template")
        self._rename_template_btn.setFixedSize(32, 28)
        self._rename_template_btn.clicked.connect(self._rename_selected_template)
        self._delete_template_btn = ToolButton(FIF.DELETE)
        self._delete_template_btn.setToolTip("Delete selected template file")
        self._delete_template_btn.setFixedSize(32, 28)
        self._delete_template_btn.clicked.connect(self._delete_selected_template)
        # Color-matcher source: a dropdown listing every named colour from any
        # rule. Selecting one copies its RGB + name + capture area onto the
        # current rule. Hidden in template mode.
        self._color_library_combo = ComboBox()
        self._color_library_combo.setMinimumWidth(180)
        self._color_library_combo.currentIndexChanged.connect(self._on_color_library_selected)
        self._color_library_combo.setVisible(False)
        # Rename / delete tools for the colour library, mirroring the template
        # pair. Library-level: they act on every rule that shares the chosen
        # colour, so renaming "my red" updates every "my red" rule.
        self._rename_color_btn = ToolButton(FIF.EDIT)
        self._rename_color_btn.setToolTip("Rename selected color (all rules using it)")
        self._rename_color_btn.setFixedSize(32, 28)
        self._rename_color_btn.setVisible(False)
        self._rename_color_btn.clicked.connect(self._rename_selected_color)
        self._delete_color_btn = ToolButton(FIF.DELETE)
        self._delete_color_btn.setToolTip("Clear selected color from all rules using it")
        self._delete_color_btn.setFixedSize(32, 28)
        self._delete_color_btn.setVisible(False)
        self._delete_color_btn.clicked.connect(self._delete_selected_color)
        # Capture buttons follow the matcher selection — only the one that
        # makes sense for the current side is shown.
        self._capture_pattern_btn = PrimaryPushButton(FIF.CAMERA, "Capture pattern")
        self._capture_pattern_btn.clicked.connect(self._capture_template)
        self._capture_color_btn = PrimaryPushButton(FIF.PALETTE, "Capture color")
        self._capture_color_btn.clicked.connect(self._capture_color)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.addWidget(self._template_combo, 1)
        top_row.addWidget(self._color_library_combo, 1)
        top_row.addWidget(self._rename_template_btn)
        top_row.addWidget(self._delete_template_btn)
        top_row.addWidget(self._rename_color_btn)
        top_row.addWidget(self._delete_color_btn)
        top_row.addSpacing(6)
        top_row.addWidget(self._capture_pattern_btn)
        top_row.addWidget(self._capture_color_btn)
        grid.addLayout(top_row, 1, 0, 1, 2)

        # Preview: either a template image (template matcher) or a flat color
        # swatch (color matcher). Same fixed footprint, only one is visible.
        self._preview_label = QLabel("(no template selected)")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setFixedSize(120, 64)
        self._preview_label.setStyleSheet(
            "QLabel { background: rgba(0,0,0,0.25); "
            "border: 1px dashed rgba(255,255,255,0.18); border-radius: 6px; "
            "color: #a1a1aa; }"
        )
        self._color_swatch = QLabel("")
        self._color_swatch.setFixedSize(120, 64)
        self._color_swatch.setVisible(False)

        preview_stack = QHBoxLayout()
        preview_stack.setContentsMargins(0, 0, 0, 0)
        preview_stack.setSpacing(0)
        preview_stack.addWidget(self._preview_label)
        preview_stack.addWidget(self._color_swatch)
        grid.addLayout(preview_stack, 2, 0, 2, 1)

        meta_box = QWidget()
        meta_lay = QVBoxLayout(meta_box)
        meta_lay.setContentsMargins(0, 2, 0, 0)
        meta_lay.setSpacing(6)

        self._threshold_row = QHBoxLayout()
        self._threshold_row.setSpacing(6)
        self._threshold_label = CaptionLabel("Threshold")
        self._threshold_row.addWidget(self._threshold_label)
        self._threshold_spin = DoubleSpinBox()
        self._threshold_spin.setRange(0.0, 1.0)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setSingleStep(0.01)
        self._threshold_spin.setValue(0.90)
        self._threshold_spin.setFixedWidth(130)
        self._threshold_spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._threshold_row.addWidget(self._threshold_spin)
        self._threshold_row.addStretch(1)
        meta_lay.addLayout(self._threshold_row)

        # Color name (color matcher only) — friendly label that shows up in
        # the color library dropdown for re-use across rules.
        self._color_name_row = QHBoxLayout()
        self._color_name_row.setSpacing(6)
        self._color_name_label = CaptionLabel("Color name")
        self._color_name_row.addWidget(self._color_name_label)
        self._color_name_edit = LineEdit()
        self._color_name_edit.setPlaceholderText("e.g. RunBlue")
        self._color_name_edit.editingFinished.connect(self._on_color_name_edited)
        self._color_name_row.addWidget(self._color_name_edit, 1)
        meta_lay.addLayout(self._color_name_row)

        self._template_meta = BodyLabel("")
        self._template_meta.setStyleSheet("color: #9ca3af;")
        self._template_meta.setWordWrap(True)
        self._template_meta.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        meta_lay.addWidget(self._template_meta)
        meta_lay.addStretch(1)

        grid.addWidget(meta_box, 2, 1, 2, 1)
        grid.setColumnStretch(1, 1)

        card.viewLayout.addLayout(grid)
        return card

    def _build_scope_card(self) -> QWidget:
        card = CollapsibleCard("Search scope")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self._region_label = BodyLabel("All monitors")
        self._region_label.setStyleSheet("color: #d4d4d8;")
        row.addWidget(self._region_label, 1)

        cap_btn = PushButton(FIF.VIEW, "Capture region")
        cap_btn.clicked.connect(self._capture_search_region)
        all_btn = PushButton(FIF.TILES, "All monitors")
        all_btn.clicked.connect(self._use_all_monitors)
        pick_btn = PushButton(FIF.ROBOT, "Pick monitor")
        pick_btn.clicked.connect(self._pick_monitor)

        row.addWidget(cap_btn)
        row.addWidget(all_btn)
        row.addWidget(pick_btn)

        card.viewLayout.addLayout(row)
        return card

    def _build_editor_actions(self) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)
        row.addStretch(1)
        test_btn = PushButton(FIF.PLAY, "Test match")
        test_btn.clicked.connect(self._test_selected_rule)
        save_btn = PrimaryPushButton(FIF.SAVE, "Save rule")
        save_btn.clicked.connect(self._save_selected_rule)
        row.addWidget(test_btn)
        row.addWidget(save_btn)
        return wrap

    def _build_log_panel(self) -> QWidget:
        card = CollapsibleCard("Log")
        card.expanded_changed.connect(self._on_left_card_toggled)
        self._log_card = card

        self._log_box = FluentPlainTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setMaximumBlockCount(1000)

        card.viewLayout.addWidget(self._log_box)
        return card

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._icon_stopped, self)
        self._tray.setToolTip("Auto Press — Stopped")
        menu = QMenu()
        self._tray_show_action = QAction("Hide window", self)
        self._tray_toggle_action = QAction("Start", self)
        self._tray_quit_action = QAction("Quit Auto Press", self)
        self._tray_show_action.triggered.connect(self._toggle_window_visibility)
        self._tray_toggle_action.triggered.connect(self._toggle_running)
        self._tray_quit_action.triggered.connect(self._quit_app)
        menu.addAction(self._tray_show_action)
        menu.addAction(self._tray_toggle_action)
        menu.addSeparator()
        menu.addAction(self._tray_quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    # ---------- body collapse ----------

    def _on_left_card_toggled(self, _expanded: bool) -> None:
        """Resize the left splitter when Rules or Log is collapsed/expanded.

        Remembers the last expanded heights for each panel so a collapse →
        expand round-trip restores the previous layout.
        """
        sender = self.sender()
        HEADER_H = 44
        sizes = self._left_splitter.sizes()
        # Snapshot whichever side is still expanded *before* we apply the
        # new min/max constraints, so re-expand later can restore it.
        if sender is self._rules_card and self._rules_card.isExpanded() is False and sizes[0] > HEADER_H:
            self._left_remembered["rules"] = sizes[0]
        elif sender is self._log_card and self._log_card.isExpanded() is False and sizes[1] > HEADER_H:
            self._left_remembered["log"] = sizes[1]

        for card, key, min_open in (
            (self._rules_card, "rules", 140),
            (self._log_card, "log", 110),
        ):
            if card.isExpanded():
                card.setMinimumHeight(min_open)
                card.setMaximumHeight(16777215)
            else:
                card.setMinimumHeight(0)
                card.setMaximumHeight(HEADER_H)

        total = sum(sizes) or (self._left_splitter.height() or 580)
        rules_exp = self._rules_card.isExpanded()
        log_exp = self._log_card.isExpanded()
        if rules_exp and log_exp:
            r = self._left_remembered.get("rules", 380)
            self._left_splitter.setSizes([r, max(HEADER_H, total - r)])
        elif rules_exp:
            self._left_splitter.setSizes([total - HEADER_H, HEADER_H])
        elif log_exp:
            self._left_splitter.setSizes([HEADER_H, total - HEADER_H])
        else:
            self._left_splitter.setSizes([HEADER_H, HEADER_H])

    def _toggle_body_collapsed(self) -> None:
        """Collapse the whole body (rules / log / editor) into toolbar-only mode."""
        if self._body_splitter.isVisible():
            self._remembered_body_h = self._body_splitter.height()
            self._body_splitter.setVisible(False)
            # Drop the min height so the window can shrink tight to the toolbar;
            # content margins (14 top + 14 bottom) + toolbar card (62) + chrome
            # fit into roughly 110 px.
            self._default_min_height = self.minimumHeight()
            self.setMinimumHeight(110)
            self.resize(self.width(), 110)
            self._collapse_btn.setIcon(FIF.DOWN)
            self._collapse_btn.setToolTip("Expand window")
        else:
            self.setMinimumHeight(getattr(self, "_default_min_height", 240))
            self._body_splitter.setVisible(True)
            self.resize(self.width(), 110 + self._remembered_body_h)
            self._collapse_btn.setIcon(FIF.UP)
            self._collapse_btn.setToolTip("Collapse to toolbar only")

    # ---------- cfg helpers ----------

    def _snapshot_cfg(self) -> dict:
        with self._cfg_lock:
            return {
                "interval_seconds": float(self._cfg.get("interval_seconds", 10.0)),
                "rules": [dict(rule) for rule in self._cfg.get("rules", [])],
            }

    def _persist(self) -> None:
        with self._cfg_lock:
            save_config(self._cfg)

    def _current_rule_index(self) -> Optional[int]:
        idx = self._rules_list.currentRow()
        return idx if idx is not None and idx >= 0 else None

    def _current_rule(self) -> Optional[dict]:
        idx = self._current_rule_index()
        if idx is None:
            return None
        rules = self._cfg.get("rules", [])
        return rules[idx] if 0 <= idx < len(rules) else None

    # ---------- log ----------

    def _log(self, message: str) -> None:
        self._log_box.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")

    # ---------- rule list / editor ----------

    def _refresh_rule_list(self, select_idx: Optional[int] = None) -> None:
        current = select_idx if select_idx is not None else self._current_rule_index()
        self._rules_list.blockSignals(True)
        rules = self._cfg.get("rules", [])
        self._rules_list.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            name_item = QTableWidgetItem(rule.get("name", "(unnamed)"))
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self._rules_list.setItem(row, 0, name_item)

            enabled = bool(rule.get("enabled"))
            mark_item = QTableWidgetItem("✓" if enabled else "✗")
            mark_item.setTextAlignment(Qt.AlignCenter)
            mark_item.setForeground(QColor(STATUS_RUNNING) if enabled else QColor(STATUS_STOPPED))
            # Copy the list's resolved font (it has a valid size) and bold it.
            # Directly calling mark_item.font() returns an unattached QFont whose
            # pointSize() is -1, which Qt 6 warns about whenever it re-resolves.
            mark_font = self._rules_list.font()
            mark_font.setBold(True)
            mark_item.setFont(mark_font)
            self._rules_list.setItem(row, 1, mark_item)

            action_item = QTableWidgetItem(rule.get("action", ACTION_CLICK))
            action_item.setForeground(QColor("#a1a1aa"))
            self._rules_list.setItem(row, 2, action_item)
        self._rules_list.blockSignals(False)

        if current is not None and self._rules_list.rowCount() > 0:
            bounded = max(0, min(self._rules_list.rowCount() - 1, current))
            self._rules_list.selectRow(bounded)
        else:
            self._clear_editor()

    def _load_selected_rule(self, _row: int = -1) -> None:
        rule = self._current_rule()
        if rule is None:
            self._clear_editor(); return
        self._name_edit.setText(rule.get("name", ""))
        self._enabled_check.setChecked(bool(rule.get("enabled", True)))
        self._threshold_spin.setValue(float(rule.get("threshold", 0.90)))
        self._action_combo.setCurrentText(rule.get("action", ACTION_CLICK))
        self._text_edit.setText(rule.get("text", "continue"))
        # Block the combo's signal so the programmatic update doesn't fire
        # _on_template_selected and overwrite a colour rule's matcher.
        self._template_combo.blockSignals(True)
        self._template_combo.setCurrentText(rule.get("template_path") or "")
        self._template_combo.blockSignals(False)
        region = rule.get("search_region")
        self._region_label.setText(
            f"{region[2]} × {region[3]} @ ({region[0]}, {region[1]})" if region else "All monitors"
        )
        self._update_action_fields()
        self._update_match_preview()

    def _clear_editor(self) -> None:
        self._name_edit.clear()
        self._enabled_check.setChecked(True)
        self._threshold_spin.setValue(0.90)
        self._action_combo.setCurrentText(ACTION_CLICK)
        self._text_edit.setText("continue")
        self._template_combo.setCurrentText("")
        self._region_label.setText("All monitors")
        self._update_action_fields()
        self._update_match_preview()

    def _update_action_fields(self, *_args) -> None:
        wants_text = self._action_combo.currentText() == ACTION_CLICK_TYPE_ENTER
        self._text_label.setVisible(wants_text)
        self._text_edit.setVisible(wants_text)

    def _add_rule(self) -> None:
        with self._cfg_lock:
            rule = default_rule(name=f"Rule {len(self._cfg['rules']) + 1}")
            rule["priority"] = len(self._cfg["rules"]) + 1
            self._cfg["rules"].append(rule)
            idx = len(self._cfg["rules"]) - 1
        self._persist(); self._refresh_rule_list(idx)
        self._log(f"[rule] added {rule['name']}")

    def _delete_rule(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[rule] select a rule to delete"); return
        with self._cfg_lock:
            removed = self._cfg["rules"].pop(idx)
            for pos, item in enumerate(self._cfg["rules"], start=1):
                item["priority"] = pos
            self._last_scores.pop(removed["id"], None)
        self._persist(); self._refresh_rule_list(max(0, idx - 1))
        self._log(f"[rule] deleted {removed['name']}")

    def _move_rule(self, direction: int) -> None:
        idx = self._current_rule_index()
        if idx is None:
            return
        new_idx = idx + direction
        with self._cfg_lock:
            if not (0 <= new_idx < len(self._cfg["rules"])):
                return
            self._cfg["rules"][idx], self._cfg["rules"][new_idx] = (
                self._cfg["rules"][new_idx],
                self._cfg["rules"][idx],
            )
            for pos, item in enumerate(self._cfg["rules"], start=1):
                item["priority"] = pos
        self._persist(); self._refresh_rule_list(new_idx)

    def _save_selected_rule(self) -> bool:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[rule] select a rule first"); return False
        with self._cfg_lock:
            rule = self._cfg["rules"][idx]
            rule["name"] = self._name_edit.text().strip() or f"Rule {idx + 1}"
            rule["enabled"] = self._enabled_check.isChecked()
            rule["threshold"] = max(0.0, min(1.0, float(self._threshold_spin.value())))
            rule["action"] = (
                self._action_combo.currentText()
                if self._action_combo.currentText() in ACTION_TYPES
                else ACTION_CLICK
            )
            rule["text"] = self._text_edit.text().strip() or "continue"
            for pos, item in enumerate(self._cfg["rules"], start=1):
                item["priority"] = pos
        self._persist(); self._refresh_rule_list(idx)
        self._log(f"[rule] saved {self._name_edit.text().strip() or f'Rule {idx + 1}'}")
        return True

    # ---------- templates ----------

    def _refresh_template_choices(self, selected: Optional[str] = None) -> None:
        current = selected if selected is not None else self._template_combo.currentText()
        items = [""] + list_template_files()
        self._template_combo.blockSignals(True)
        self._template_combo.clear()
        self._template_combo.addItems(items)
        self._template_combo.setCurrentText(current if current in items else "")
        self._template_combo.blockSignals(False)
        self._update_match_preview()

    def _set_matcher(self, matcher: str) -> None:
        """User clicked the segmented toggle. Persist on the active rule, refresh UI."""
        if getattr(self, "_suppress_matcher_signal", False):
            return
        idx = self._current_rule_index()
        if idx is None:
            self._update_match_preview()
            return
        with self._cfg_lock:
            current = self._cfg["rules"][idx].get("matcher", MATCHER_TEMPLATE)
        if current == matcher:
            self._update_match_preview()
            return
        with self._cfg_lock:
            self._cfg["rules"][idx]["matcher"] = matcher
        self._persist()
        self._refresh_rule_list(idx)
        self._update_match_preview()

    def _on_color_name_edited(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            return
        new_name = self._color_name_edit.text().strip()
        with self._cfg_lock:
            rule = self._cfg["rules"][idx]
            if rule.get("color_name", "") == new_name:
                return
            rule["color_name"] = new_name
        self._persist()
        self._refresh_color_library(remember_current=True)
        self._update_match_preview()

    def _refresh_color_library(self, remember_current: bool = False) -> None:
        """Populate the color library dropdown with every captured color across rules."""
        items: list[tuple[str, list[int]]] = []  # (label, rgb)
        seen: set[tuple[int, int, int]] = set()
        for r in self._cfg.get("rules", []):
            rgb = r.get("color_rgb")
            if not rgb or len(rgb) != 3:
                continue
            key = tuple(int(c) for c in rgb)
            if key in seen:
                continue
            seen.add(key)
            hex_label = f"#{key[0]:02X}{key[1]:02X}{key[2]:02X}"
            name = r.get("color_name") or ""
            label = f"{name}  ·  {hex_label}" if name else hex_label
            items.append((label, list(key)))
        prev = self._color_library_combo.currentData() if remember_current else None
        self._color_library_combo.blockSignals(True)
        self._color_library_combo.clear()
        # qfluentwidgets ComboBox.addItem signature is (text, icon=None,
        # userData=None) — the RGB list is user data, not an icon.
        self._color_library_combo.addItem("— choose a color —", userData=None)
        for label, rgb in items:
            self._color_library_combo.addItem(label, userData=rgb)
        # Restore previous selection if still present.
        if prev is not None:
            for i in range(self._color_library_combo.count()):
                if self._color_library_combo.itemData(i) == prev:
                    self._color_library_combo.setCurrentIndex(i)
                    break
        self._color_library_combo.blockSignals(False)

    def _on_color_library_selected(self, idx: int) -> None:
        if idx <= 0:
            return  # placeholder
        rgb = self._color_library_combo.itemData(idx)
        if not rgb:
            return
        rule_idx = self._current_rule_index()
        if rule_idx is None:
            return
        # Pull name + captured area from the first rule that owns this colour.
        name = ""
        area = 0
        target = tuple(int(c) for c in rgb)
        for r in self._cfg.get("rules", []):
            r_rgb = r.get("color_rgb")
            if r_rgb and tuple(int(c) for c in r_rgb) == target:
                name = r.get("color_name") or ""
                area = int(r.get("color_capture_area") or 0)
                break
        with self._cfg_lock:
            rule = self._cfg["rules"][rule_idx]
            rule["matcher"] = MATCHER_COLOR
            rule["color_rgb"] = list(rgb)
            rule["color_name"] = name
            rule["color_capture_area"] = area
        self._persist()
        self._refresh_rule_list(rule_idx)
        self._update_match_preview()

    def _update_match_preview(self) -> None:
        """Render the preview area for whichever matcher the active rule uses."""
        rule = self._current_rule()
        matcher = (rule or {}).get("matcher", MATCHER_TEMPLATE)
        # Keep the segmented toggle in sync without re-firing onClick.
        self._suppress_matcher_signal = True
        try:
            self._matcher_seg.setCurrentItem(matcher)
        finally:
            self._suppress_matcher_signal = False

        is_color = matcher == MATCHER_COLOR
        # Source-row widgets: template combo + rename/delete vs colour library.
        self._template_combo.setVisible(not is_color)
        self._rename_template_btn.setVisible(not is_color)
        self._delete_template_btn.setVisible(not is_color)
        self._color_library_combo.setVisible(is_color)
        self._rename_color_btn.setVisible(is_color)
        self._delete_color_btn.setVisible(is_color)
        # Capture buttons follow the side too — no point offering "capture color"
        # while the user is on the Pattern tab and vice versa.
        self._capture_pattern_btn.setVisible(not is_color)
        self._capture_color_btn.setVisible(is_color)
        # Threshold only applies to template matching.
        self._threshold_label.setVisible(not is_color)
        self._threshold_spin.setVisible(not is_color)
        # Color name only applies to colour matching.
        self._color_name_label.setVisible(is_color)
        self._color_name_edit.setVisible(is_color)

        if is_color:
            self._refresh_color_library(remember_current=False)
            if rule and rule.get("color_rgb"):
                r, g, b = (int(c) for c in rule["color_rgb"])
                area = int(rule.get("color_capture_area") or 0)
                self._color_swatch.setStyleSheet(
                    f"QLabel {{ background: rgb({r},{g},{b}); "
                    f"border: 1px solid rgba(255,255,255,0.18); border-radius: 6px; }}"
                )
                self._template_meta.setText(f"#{r:02X}{g:02X}{b:02X}  ·  {area} px² captured")
                self._color_name_edit.blockSignals(True)
                self._color_name_edit.setText(rule.get("color_name") or "")
                self._color_name_edit.blockSignals(False)
            else:
                self._color_swatch.setStyleSheet(
                    "QLabel { background: rgba(0,0,0,0.25); "
                    "border: 1px dashed rgba(255,255,255,0.18); border-radius: 6px; }"
                )
                self._template_meta.setText("Click 'Capture color' to pick a color")
                self._color_name_edit.blockSignals(True)
                self._color_name_edit.clear()
                self._color_name_edit.blockSignals(False)
            self._preview_label.setVisible(False)
            self._color_swatch.setVisible(True)
            return

        # Template matcher path: show image preview + threshold.
        self._preview_label.setVisible(True)
        self._color_swatch.setVisible(False)
        name = (self._template_combo.currentText() or "").strip()
        if not name:
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("(no template)")
            self._template_meta.setText(""); return
        path = resolve_template_path(name)
        if path is None or not Path(path).exists():
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("(missing)")
            self._template_meta.setText("not found under templates/"); return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("(error)")
            self._template_meta.setText(""); return
        nw, nh = pixmap.width(), pixmap.height()
        bw, bh = self._preview_label.width() - 8, self._preview_label.height() - 8
        if nw > bw or nh > bh:
            scaled = pixmap.scaled(bw, bh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            note = "fit"
        else:
            scaled = pixmap
            note = "actual"
        self._preview_label.setPixmap(scaled)
        self._preview_label.setText("")
        self._template_meta.setText(f"{nw} × {nh} px  ·  {note}")

    def _on_template_selected(self, name: str) -> None:
        """Dropdown-driven template assignment.

        Selecting a template flips the active rule's matcher back to template
        mode and persists the new path. No-op if no rule is active so
        programmatic reloads don't double-save.
        """
        idx = self._current_rule_index()
        if idx is None:
            self._update_match_preview()
            return
        choice = (name or "").strip()
        with self._cfg_lock:
            rule = self._cfg["rules"][idx]
            current = rule.get("template_path") or ""
            current_matcher = rule.get("matcher", MATCHER_TEMPLATE)
        if choice == current and current_matcher == MATCHER_TEMPLATE:
            self._update_match_preview()
            return
        with self._cfg_lock:
            rule = self._cfg["rules"][idx]
            rule["template_path"] = choice or None
            rule["matcher"] = MATCHER_TEMPLATE
        self._persist()
        self._refresh_rule_list(idx)
        self._update_match_preview()
        if choice:
            self._log(f"[template] {choice}")

    def _rename_selected_template(self) -> None:
        choice = (self._template_combo.currentText() or "").strip()
        if not choice:
            self._log("[template] no template selected to rename"); return
        from PySide6.QtWidgets import QInputDialog

        suffix = Path(choice).suffix or ".png"
        stem_default = Path(choice).stem
        new_stem, ok = QInputDialog.getText(
            self, "Rename template", "New filename (without extension):", text=stem_default
        )
        if not ok:
            return
        new_stem = (new_stem or "").strip()
        if not new_stem or any(ch in new_stem for ch in r"\/:*?\"<>|"):
            self._log("[template] rename aborted: empty or contains a path separator"); return
        new_name = f"{new_stem}{suffix}"
        if new_name == choice:
            return
        old_path = template_asset_path(choice)
        new_path = template_asset_path(new_name)
        if new_path.exists():
            self._log(f"[template] '{new_name}' already exists; pick another name"); return
        try:
            old_path.rename(new_path)
        except OSError as exc:
            self._log(f"[error] rename failed: {exc}"); return
        # Re-point every rule that referenced the old filename.
        with self._cfg_lock:
            for r in self._cfg.get("rules", []):
                if r.get("template_path") == choice:
                    r["template_path"] = new_name
        self._persist()
        self._refresh_template_choices(new_name)
        self._refresh_rule_list(self._current_rule_index())
        self._log(f"[template] renamed {choice} -> {new_name}")

    def _delete_selected_template(self) -> None:
        choice = (self._template_combo.currentText() or "").strip()
        if not choice:
            self._log("[template] no template selected to delete"); return
        from PySide6.QtWidgets import QMessageBox

        confirm = QMessageBox.question(
            self,
            "Delete template",
            f"Delete '{choice}' from disk?\nAny rule pointing to it will lose its template.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        path = template_asset_path(choice)
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            self._log(f"[error] delete failed: {exc}"); return
        # Detach the deleted file from every rule that referenced it.
        with self._cfg_lock:
            for r in self._cfg.get("rules", []):
                if r.get("template_path") == choice:
                    r["template_path"] = None
        self._persist()
        self._refresh_template_choices()
        self._refresh_rule_list(self._current_rule_index())
        self._log(f"[template] deleted {choice}")

    def _selected_library_color(self) -> Optional[tuple[int, int, int]]:
        """Return the RGB tuple currently picked in the color library combo."""
        idx = self._color_library_combo.currentIndex()
        if idx <= 0:
            return None
        rgb = self._color_library_combo.itemData(idx)
        if not rgb:
            return None
        return tuple(int(c) for c in rgb)

    def _rename_selected_color(self) -> None:
        """Rename a colour library entry. Updates color_name on every rule that
        shares the chosen RGB, so the friendly label stays consistent."""
        target = self._selected_library_color()
        if target is None:
            self._log("[color] pick a color from the library first"); return
        current_name = ""
        for r in self._cfg.get("rules", []):
            r_rgb = r.get("color_rgb")
            if r_rgb and tuple(int(c) for c in r_rgb) == target and r.get("color_name"):
                current_name = r.get("color_name") or ""
                break
        from PySide6.QtWidgets import QInputDialog

        new_name, ok = QInputDialog.getText(
            self, "Rename color", "New name:", text=current_name
        )
        if not ok:
            return
        new_name = (new_name or "").strip()
        with self._cfg_lock:
            for r in self._cfg.get("rules", []):
                r_rgb = r.get("color_rgb")
                if r_rgb and tuple(int(c) for c in r_rgb) == target:
                    r["color_name"] = new_name
        self._persist()
        self._refresh_rule_list(self._current_rule_index())
        self._update_match_preview()
        hex_label = f"#{target[0]:02X}{target[1]:02X}{target[2]:02X}"
        self._log(f"[color] {hex_label} renamed to '{new_name or '(unnamed)'}'")

    def _delete_selected_color(self) -> None:
        """Clear the chosen colour from every rule that uses it. Rules stay in
        colour matcher mode but lose their RGB data, ready for a re-capture."""
        target = self._selected_library_color()
        if target is None:
            self._log("[color] pick a color from the library first"); return
        from PySide6.QtWidgets import QMessageBox

        hex_label = f"#{target[0]:02X}{target[1]:02X}{target[2]:02X}"
        confirm = QMessageBox.question(
            self,
            "Delete color",
            f"Clear {hex_label} from every rule that uses it?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        cleared = 0
        with self._cfg_lock:
            for r in self._cfg.get("rules", []):
                r_rgb = r.get("color_rgb")
                if r_rgb and tuple(int(c) for c in r_rgb) == target:
                    r["color_rgb"] = None
                    r["color_name"] = ""
                    r["color_capture_area"] = 0
                    cleared += 1
        self._persist()
        self._refresh_rule_list(self._current_rule_index())
        self._update_match_preview()
        self._log(f"[color] {hex_label} cleared from {cleared} rule(s)")

    def _capture_template(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[capture] add or select a rule first"); return
        try:
            ensure_vision()
        except Exception as exc:
            self._log(f"[error] {exc}"); return
        bbox = capture_drag_bbox(self)
        if not bbox:
            self._log("[capture] template capture cancelled"); return
        try:
            gray = capture_screen_gray(tuple(bbox))
            file_name = f"rule_{self._cfg['rules'][idx]['id']}.png"
            path = template_asset_path(file_name)
            save_gray_image(str(path), gray)
            stored_path = serialize_template_path(path)
            with self._cfg_lock:
                rule = self._cfg["rules"][idx]
                rule["template_path"] = stored_path
                rule["matcher"] = MATCHER_TEMPLATE
            self._persist(); self._refresh_rule_list(idx); self._refresh_template_choices(stored_path)
            self._log(
                f"[capture] {path.name}  bbox=({bbox[0]},{bbox[1]}) size={bbox[2]}x{bbox[3]} → {gray.shape[1]}x{gray.shape[0]}"
            )
        except Exception as exc:
            self._log(f"[error] template capture failed: {exc}")

    def _capture_color(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[capture] add or select a rule first"); return
        try:
            ensure_vision()
        except Exception as exc:
            self._log(f"[error] {exc}"); return
        bbox = capture_drag_bbox(self)
        if not bbox:
            self._log("[capture] color capture cancelled"); return
        try:
            rgb = capture_screen_rgb(tuple(bbox))
            r, g, b = dominant_rgb(rgb)
            area = int(bbox[2]) * int(bbox[3])
            with self._cfg_lock:
                rule = self._cfg["rules"][idx]
                rule["matcher"] = MATCHER_COLOR
                rule["color_rgb"] = [r, g, b]
                rule["color_capture_area"] = area
            self._persist(); self._refresh_rule_list(idx); self._update_match_preview()
            self._log(
                f"[capture] color #{r:02X}{g:02X}{b:02X} from {bbox[2]}x{bbox[3]} ({area} px²)"
            )
        except Exception as exc:
            self._log(f"[error] color capture failed: {exc}")

    def _capture_search_region(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[capture] select a rule first"); return
        bbox = capture_drag_bbox(self)
        if not bbox:
            self._log("[capture] search region cancelled"); return
        with self._cfg_lock:
            self._cfg["rules"][idx]["search_region"] = bbox
        self._persist(); self._refresh_rule_list(idx)
        self._region_label.setText(f"{bbox[2]} × {bbox[3]} @ ({bbox[0]}, {bbox[1]})")
        self._log(f"[capture] region set: bbox=({bbox[0]},{bbox[1]}) size={bbox[2]}x{bbox[3]}")

    def _use_all_monitors(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[capture] select a rule first"); return
        with self._cfg_lock:
            self._cfg["rules"][idx]["search_region"] = None
        self._persist(); self._refresh_rule_list(idx)
        self._region_label.setText("All monitors")
        self._log("[capture] rule now scans all monitors")

    def _pick_monitor(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[monitor] select a rule first"); return
        dialog = MonitorPickDialog(self)
        if dialog.exec() and dialog.selected:
            bbox = dialog.selected
            with self._cfg_lock:
                self._cfg["rules"][idx]["search_region"] = bbox
            self._persist(); self._refresh_rule_list(idx)
            self._region_label.setText(f"Monitor  {bbox[2]} × {bbox[3]} @ ({bbox[0]}, {bbox[1]})")
            self._log(f"[monitor] region set to {bbox[2]}x{bbox[3]} @ ({bbox[0]},{bbox[1]})")

    def _test_selected_rule(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[test] select a rule first"); return
        if not self._save_selected_rule():
            return
        try:
            with self._cfg_lock:
                # Make the rule enabled for the duration of the test so a
                # disabled rule still reports its score; we restore the
                # config's view through normalize on next persist.
                rule = dict(self._cfg["rules"][idx])
                rule["enabled"] = True
            matcher = rule.get("matcher", MATCHER_TEMPLATE)
            if matcher == MATCHER_TEMPLATE:
                tpl_path = resolve_template_path(rule.get("template_path"))
                if tpl_path is None or not Path(tpl_path).exists():
                    self._log("[test] capture a template first"); return
                frame = capture_screen_gray()
            else:  # MATCHER_COLOR
                if not rule.get("color_rgb") or int(rule.get("color_capture_area") or 0) <= 0:
                    self._log("[test] capture a color first"); return
                frame = capture_screen_rgb()

            runtime_rule = build_runtime_rules({"rules": [rule]})
            if not runtime_rule:
                self._log("[test] rule is not ready"); return
            score, center = evaluate_rule_on_frame(frame, runtime_rule[0])
            if matcher == MATCHER_COLOR:
                matched = center is not None
            else:
                matched = center is not None and score >= float(rule.get("threshold", 0.90))
            self._last_scores[rule["id"]] = score
            self._refresh_rule_list(idx)
            self._log(
                f"[test] {rule['name']}  {'match' if matched else 'no-match'}  "
                f"score={score:.3f}  center={center}"
            )
        except Exception as exc:
            self._log(f"[error] test failed: {exc}")

    # ---------- run control ----------

    def _on_interval_changed(self, value: float) -> None:
        with self._cfg_lock:
            self._cfg["interval_seconds"] = float(value)
        self._worker.set_interval(float(value))
        if self._running:
            self._next_tick_at = time.monotonic() + float(value)
        save_config(self._snapshot_cfg())

    def _toggle_running(self) -> None:
        if self._running:
            self._worker.set_running(False)
            self._next_tick_at = None
            self._log("[control] stopped")
            return
        with self._cfg_lock:
            self._cfg["interval_seconds"] = float(self._interval_spin.value())
            save_config(self._cfg)
        self._worker.set_interval(float(self._interval_spin.value()))
        self._worker.set_running(True)
        self._next_tick_at = time.monotonic() + float(self._interval_spin.value())
        self._log("[control] started")

    def _on_running_changed(self, running: bool) -> None:
        self._running = running
        self._set_running_status(running)
        if not running:
            self._next_tick_at = None
            self._action_status.setText("")

    def _on_needs_rules(self) -> None:
        self._log("[control] add at least one enabled rule with a template")

    def _set_running_status(self, running: bool) -> None:
        if running:
            self._status_label.setText("Running")
            self._status_label.setStyleSheet(f"color: {STATUS_RUNNING};")
            self._status_dot.set_color(STATUS_RUNNING)
            self._start_btn.setText("Stop")
            self._start_btn.setIcon(FIF.PAUSE)
            self._tray.setIcon(self._icon_running)
            self._tray.setToolTip("Auto Press — Running")
            self._tray_toggle_action.setText("Stop")
            self.setWindowIcon(self._icon_running)
        else:
            self._status_label.setText("Stopped")
            self._status_label.setStyleSheet(f"color: {STATUS_STOPPED};")
            self._status_dot.set_color(STATUS_STOPPED)
            self._start_btn.setText("Start")
            self._start_btn.setIcon(FIF.PLAY)
            self._tray.setIcon(self._icon_stopped)
            self._tray.setToolTip("Auto Press — Stopped")
            self._tray_toggle_action.setText("Start")
            self.setWindowIcon(self._icon_stopped)

    def _on_tick_done(self, results: list, actions: list, interval: float) -> None:
        for result in results:
            self._last_scores[result["id"]] = float(result["score"])
        self._refresh_rule_list()
        if actions:
            summaries: dict[str, int] = {}
            for action in actions:
                summaries[action["name"]] = summaries.get(action["name"], 0) + 1
            summary = ", ".join(f"{name} ×{c}" for name, c in summaries.items())
            self._action_status.setText(summary)
            self._log(f"[tick] {summary}")
        else:
            self._action_status.setText("no match")
            self._log("[tick] no match")
        self._next_tick_at = time.monotonic() + interval

    def _on_worker_error(self, message: str) -> None:
        self._log(f"[error] {message}")

    def _update_countdown(self) -> None:
        if self._running and self._next_tick_at is not None:
            remaining = max(0.0, float(self._next_tick_at) - time.monotonic())
            self._countdown_label.setText(f"{remaining:.1f}s")
        else:
            self._countdown_label.setText("")

    # ---------- tray / window ----------

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._toggle_window_visibility()

    def _toggle_window_visibility(self) -> None:
        if self.isVisible():
            self.hide()
            self._tray_show_action.setText("Show window")
        else:
            self.show(); self.raise_(); self.activateWindow()
            self._tray_show_action.setText("Hide window")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._quitting or not self._tray.isVisible():
            self._shutdown(); event.accept(); return
        event.ignore()
        self.hide()
        self._tray_show_action.setText("Show window")
        self._log("[tray] window minimized to tray (right-click the tray icon to quit)")

    def _quit_app(self) -> None:
        self._quitting = True
        self._shutdown()
        QApplication.instance().quit()

    def _shutdown(self) -> None:
        self._worker.set_running(False)
        self._worker.request_stop()
        self._worker_thread.quit()
        self._worker_thread.wait(2000)
        self._hotkey_stop.set()
        if IS_WINDOWS:
            self._post_wm_quit()
        with self._cfg_lock:
            self._cfg["interval_seconds"] = float(self._interval_spin.value())
            save_config(self._cfg)
        self._tray.hide()

    # ---------- global hotkey ----------

    def _start_hotkey_thread(self) -> None:
        if not IS_WINDOWS:
            return
        self._hotkey_stop = threading.Event()
        threading.Thread(target=self._hotkey_loop, daemon=True).start()

    def _stop_hotkey_thread(self, wait: bool = True) -> None:
        if not IS_WINDOWS:
            return
        self._hotkey_stop.set()
        self._post_wm_quit()
        # PostThreadMessage needs a short grace period for the loop to unwind.
        if wait:
            for _ in range(20):
                if self._hotkey_thread_id.get("tid") is None:
                    break
                time.sleep(0.02)

    def _hotkey_loop(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        from ctypes import wintypes

        WM_HOTKEY = 0x0312
        MOD_NOREPEAT = 0x4000
        HOTKEY_ID = 1

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT),
            ]

        vk = int(self._hotkey_vk)
        mods = int(self._hotkey_mods) | MOD_NOREPEAT
        self._hotkey_thread_id["tid"] = int(kernel32.GetCurrentThreadId())
        try:
            if not user32.RegisterHotKey(None, HOTKEY_ID, mods, vk):
                self._log("[hotkey] failed to register (already taken by another app?)")
                return
            msg = MSG()
            while not self._hotkey_stop.is_set():
                ok = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ok <= 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.hotkey_triggered.emit()
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            user32.UnregisterHotKey(None, HOTKEY_ID)
        finally:
            self._hotkey_thread_id["tid"] = None

    def _post_wm_quit(self) -> None:
        tid = self._hotkey_thread_id.get("tid")
        if not tid:
            return
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            WM_QUIT = 0x0012
            user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
        except Exception:
            pass

    def _on_hotkey_changed(self, vk: int, mods: int) -> None:
        """Swap the global hotkey at runtime; persists to config."""
        self._stop_hotkey_thread(wait=True)
        self._hotkey_vk = int(vk)
        self._hotkey_mods = int(mods)
        with self._cfg_lock:
            self._cfg["hotkey_vk"] = self._hotkey_vk
            self._cfg["hotkey_mods"] = self._hotkey_mods
            save_config(self._cfg)
        self._start_hotkey_thread()
        self._log(f"[hotkey] rebound to {self._hotkey_button.text()}")
