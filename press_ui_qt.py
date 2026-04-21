"""Qt-based rule automation UI for Auto Press."""

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
    QRect,
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
    QCursor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QSystemTrayIcon,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from press_core import save_gray_image
from press_engine import (
    build_runtime_rules,
    capture_screen_gray,
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
    default_rule,
    list_template_files,
    load_config,
    make_rule_summary,
    resolve_template_path,
    save_config,
    serialize_template_path,
    template_asset_path,
)


IS_WINDOWS = sys.platform.startswith("win")


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
        from PySide6.QtGui import QGuiApplication as _QGuiApp

        return [
            (s.geometry().left(), s.geometry().top(), s.geometry().width(), s.geometry().height())
            for s in _QGuiApp.screens()
        ]

    def physical_cursor_pos() -> tuple[int, int]:
        from PySide6.QtGui import QCursor as _QCursor

        pos = _QCursor.pos()
        return pos.x(), pos.y()


# ----- theme ----------------------------------------------------------

ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
BG = "#15161b"
SURFACE = "#1f2028"
SURFACE_2 = "#272832"
SURFACE_3 = "#30313c"
BORDER = "#363846"
BORDER_STRONG = "#41434f"
TEXT = "#e8e9ec"
MUTED = "#93949f"
STATUS_RUNNING = "#22c55e"
STATUS_STOPPED = "#ef4444"
RECT_STROKE = "#22c55e"

STYLESHEET = f"""
* {{ font-family: "Segoe UI", sans-serif; }}
QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; font-size: 10pt; }}

QToolBar {{
    background: {SURFACE};
    border-bottom: 1px solid {BORDER};
    padding: 8px 12px;
    spacing: 10px;
}}
QToolBar QLabel {{ color: {TEXT}; }}
QToolBar QLabel[role="muted"] {{ color: {MUTED}; }}
QToolBar QLabel[role="countdown"] {{
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 12pt;
    color: {MUTED};
    min-width: 54px;
}}
QToolBar QLabel[role="status"] {{ font-weight: 600; padding-left: 2px; }}
QToolBar QLabel[role="action"] {{ color: {MUTED}; font-style: italic; }}

QStatusBar {{ background: {SURFACE}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}

QPushButton {{
    background: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 22px;
}}
QPushButton:hover {{ background: {SURFACE_3}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background: #181920; }}
QPushButton:disabled {{ color: #5f606a; background: #1a1b22; }}
QPushButton[role="primary"] {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[role="primary"]:pressed {{ background: #1d4ed8; }}
QPushButton[role="nav"] {{ padding: 4px 8px; min-width: 34px; }}

QLineEdit, QDoubleSpinBox, QComboBox {{
    background: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    background: #191a21;
    color: #585963;
    border-color: #2a2b34;
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    padding: 4px;
    outline: 0;
}}

QCheckBox {{ spacing: 8px; color: {TEXT}; }}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1px solid {BORDER};
    background: {SURFACE_2};
}}
QCheckBox::indicator:hover {{ border-color: {BORDER_STRONG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}

QListWidget {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
    outline: 0;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {ACCENT}; color: white; }}
QListWidget::item:hover:!selected {{ background: {SURFACE_2}; }}

QPlainTextEdit {{
    background: #0f1016;
    color: #d8dae0;
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 10pt;
    padding: 6px 8px;
}}

QGroupBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 12px 10px 12px;
    color: {TEXT};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: {MUTED};
    font-weight: 600;
    font-size: 9pt;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:horizontal {{ width: 1px; margin: 0 3px; background: {BORDER}; }}
QSplitter::handle:vertical   {{ height: 1px; margin: 3px 0; background: {BORDER}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #474956; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}

QLabel[role="section-title"] {{ color: {TEXT}; font-weight: 600; font-size: 11pt; }}
QLabel[role="hint"] {{ color: {MUTED}; }}
QLabel[role="preview"] {{
    background: #0d0e14;
    border: 1px dashed {BORDER};
    border-radius: 6px;
    color: {MUTED};
}}

QMenu {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
"""


# -------------------------- small helpers ------------------------------


class _Spacer(QWidget):
    def __init__(self, width: int):
        super().__init__()
        self.setFixedWidth(width)


class _VRule(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.VLine)
        self.setFixedWidth(1)
        self.setStyleSheet(f"background: {BORDER}; border: none;")


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


# -------------------------- engine worker ------------------------------


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
        # Pin PER_MONITOR_AWARE_V2 on this worker thread once. On Windows,
        # SetThreadDpiAwarenessContext is sticky for the thread lifetime, so
        # subsequent capture / click iterations all share one coord system.
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


# -------------------------- drag capture -------------------------------


class CaptureOverlay(QWidget):
    """Per-monitor overlay. start/current in controller are PHYSICAL pixel coords."""

    def __init__(self, qt_screen, physical_rect: tuple[int, int, int, int], controller: "CaptureController"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setCursor(Qt.CrossCursor)
        self._qt_screen = qt_screen
        self._physical_rect = physical_rect  # (left, top, w, h) in physical px
        self._controller = controller
        self.setGeometry(qt_screen.geometry())  # Qt places us at the physical monitor

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 100))
        if self._controller.start is None:
            return
        # Everything in the controller is physical.
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
        # Translate physical → overlay-local logical for Qt painting
        from PySide6.QtCore import QRectF

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
                # Fallback: multiply Qt logical by dpr
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


# -------------------------- monitor picker -----------------------------


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
        title.setProperty("role", "hint")
        layout.addWidget(title)

        # Physical monitor rects match what the engine passes to ImageGrab.
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


# -------------------------- main window --------------------------------


class MainWindow(QMainWindow):
    hotkey_triggered = Signal()

    # Reasonable floor for toolbar+status+splitter handles so the window
    # can still shrink when both panels are hidden.
    CHROME_HEIGHT = 110

    def __init__(self, initial_seconds: float):
        super().__init__()
        self.hotkey_triggered.connect(self._toggle_running, Qt.QueuedConnection)

        self.setWindowTitle("Auto Press")
        self.setMinimumSize(560, 220)
        self.resize(1060, 700)
        self.setStyleSheet(STYLESHEET)

        # State
        self._cfg = load_config()
        self._cfg["interval_seconds"] = float(initial_seconds)
        save_config(self._cfg)
        self._cfg_lock = threading.Lock()
        self._last_scores: dict = {}
        self._next_tick_at: Optional[float] = None
        self._running = False
        self._quitting = False
        # remembered panel sizes so toggling doesn't lose the user's layout
        self._remembered_workspace_h = 440
        self._remembered_log_h = 180

        self._icon_running = _make_dot_icon(QColor(STATUS_RUNNING))
        self._icon_stopped = _make_dot_icon(QColor(STATUS_STOPPED))
        self.setWindowIcon(self._icon_stopped)

        self._build_toolbar(initial_seconds)
        self._build_body()
        self._build_statusbar()
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
        self._hotkey_stop = threading.Event()
        self._hotkey_thread_id: dict[str, int | None] = {"tid": None}
        if IS_WINDOWS:
            threading.Thread(target=self._hotkey_loop, daemon=True).start()

        # Initial content
        self._refresh_template_choices()
        self._refresh_rule_list(0 if self._cfg.get("rules") else None)
        self._set_running_status(False)
        self._log(f"[ready] loaded {CONFIG_PATH}")

    # --- toolbar --------------------------------------------------

    def _build_toolbar(self, initial_seconds: float) -> None:
        bar = QToolBar("Main")
        bar.setMovable(False)
        bar.setIconSize(QSize(18, 18))
        self.addToolBar(Qt.TopToolBarArea, bar)
        self._toolbar = bar

        self._start_btn = QPushButton("Start")
        self._start_btn.setProperty("role", "primary")
        self._start_btn.setMinimumWidth(88)
        self._start_btn.clicked.connect(self._toggle_running)
        bar.addWidget(self._start_btn)

        bar.addWidget(_Spacer(6))
        bar.addWidget(_VRule())
        bar.addWidget(_Spacer(6))

        bar.addWidget(_mk_label("Interval", muted=True))
        self._interval_spin = QDoubleSpinBox()
        self._interval_spin.setRange(0.1, 86400.0)
        self._interval_spin.setDecimals(1)
        self._interval_spin.setSingleStep(0.5)
        self._interval_spin.setValue(float(initial_seconds))
        self._interval_spin.setFixedWidth(82)
        self._interval_spin.valueChanged.connect(self._on_interval_changed)
        bar.addWidget(self._interval_spin)
        bar.addWidget(_mk_label("s", muted=True))

        bar.addWidget(_Spacer(6))
        bar.addWidget(_VRule())
        bar.addWidget(_Spacer(6))

        self._status_dot = StatusDot()
        bar.addWidget(self._status_dot)
        bar.addWidget(_Spacer(4))
        self._status_label = QLabel("Stopped")
        self._status_label.setProperty("role", "status")
        self._status_label.setStyleSheet(f"color: {STATUS_STOPPED};")
        bar.addWidget(self._status_label)

        bar.addWidget(_Spacer(12))
        self._countdown_label = QLabel("")
        self._countdown_label.setProperty("role", "countdown")
        bar.addWidget(self._countdown_label)

        self._action_status = QLabel("")
        self._action_status.setProperty("role", "action")
        bar.addWidget(_Spacer(8))
        bar.addWidget(self._action_status)

        bar.addWidget(_flex_spacer())

        self._workspace_toggle = QCheckBox("Workspace")
        self._workspace_toggle.setChecked(True)
        self._workspace_toggle.toggled.connect(self._on_workspace_toggled)
        bar.addWidget(self._workspace_toggle)

        bar.addWidget(_Spacer(12))
        self._log_toggle = QCheckBox("Log")
        self._log_toggle.setChecked(True)
        self._log_toggle.toggled.connect(self._on_log_toggled)
        bar.addWidget(self._log_toggle)

    # --- body -----------------------------------------------------

    def _build_body(self) -> None:
        self._v_splitter = QSplitter(Qt.Vertical)
        self._v_splitter.setChildrenCollapsible(False)
        self._v_splitter.setHandleWidth(1)

        self._workspace_panel = self._build_workspace_panel()
        self._log_panel = self._build_log_panel()

        self._v_splitter.addWidget(self._workspace_panel)
        self._v_splitter.addWidget(self._log_panel)
        self._v_splitter.setStretchFactor(0, 3)
        self._v_splitter.setStretchFactor(1, 1)
        self._v_splitter.setSizes([self._remembered_workspace_h, self._remembered_log_h])

        self._workspace_panel.setMinimumHeight(130)
        self._log_panel.setMinimumHeight(90)

        self._v_splitter.splitterMoved.connect(self._on_splitter_moved)
        self.setCentralWidget(self._v_splitter)

    def _build_workspace_panel(self) -> QWidget:
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        split.addWidget(self._build_rules_panel())
        split.addWidget(self._build_editor_panel())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        split.setSizes([300, 740])
        return split

    def _build_rules_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(220)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 8, 10)
        layout.setSpacing(8)

        header = QLabel("Rules")
        header.setProperty("role", "section-title")
        layout.addWidget(header)

        self._rules_list = QListWidget()
        self._rules_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._rules_list.currentRowChanged.connect(self._load_selected_rule)
        layout.addWidget(self._rules_list, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._add_btn = QPushButton("Add")
        self._delete_btn = QPushButton("Delete")
        self._up_btn = QPushButton("↑"); self._up_btn.setProperty("role", "nav")
        self._down_btn = QPushButton("↓"); self._down_btn.setProperty("role", "nav")
        self._add_btn.clicked.connect(self._add_rule)
        self._delete_btn.clicked.connect(self._delete_rule)
        self._up_btn.clicked.connect(lambda: self._move_rule(-1))
        self._down_btn.clicked.connect(lambda: self._move_rule(1))
        row.addWidget(self._add_btn)
        row.addWidget(self._delete_btn)
        row.addStretch(1)
        row.addWidget(self._up_btn)
        row.addWidget(self._down_btn)
        layout.addLayout(row)
        return panel

    def _build_editor_panel(self) -> QWidget:
        outer = QWidget()
        outer.setMinimumWidth(280)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(8, 14, 14, 10)
        outer_layout.setSpacing(8)

        header = QLabel("Rule Editor")
        header.setProperty("role", "section-title")
        outer_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(10)

        # Basics
        basics = QGroupBox("Basics")
        bl = QGridLayout(basics)
        bl.setContentsMargins(12, 18, 12, 12)
        bl.setHorizontalSpacing(10)
        bl.setVerticalSpacing(6)
        bl.addWidget(_mk_label("Name", muted=True), 0, 0)
        bl.addWidget(_mk_label("Action", muted=True), 0, 1)
        bl.addWidget(_mk_label("Text", muted=True), 0, 2)
        self._name_edit = QLineEdit()
        self._action_combo = QComboBox()
        self._action_combo.addItems(ACTION_TYPES)
        self._action_combo.currentTextChanged.connect(self._update_action_fields)
        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("(used when action is click+type+enter)")
        bl.addWidget(self._name_edit, 1, 0)
        bl.addWidget(self._action_combo, 1, 1)
        bl.addWidget(self._text_edit, 1, 2)
        self._enabled_check = QCheckBox("Enabled")
        bl.addWidget(self._enabled_check, 2, 0)
        bl.setColumnStretch(0, 2)
        bl.setColumnStretch(1, 2)
        bl.setColumnStretch(2, 2)
        layout.addWidget(basics)

        # Template
        template = QGroupBox("Template")
        tl = QGridLayout(template)
        tl.setContentsMargins(12, 18, 12, 12)
        tl.setHorizontalSpacing(10)
        tl.setVerticalSpacing(10)

        self._template_combo = QComboBox()
        self._template_combo.setMinimumWidth(180)
        self._template_combo.currentTextChanged.connect(self._update_template_preview)
        use_existing_btn = QPushButton("Use Existing")
        use_existing_btn.clicked.connect(self._use_selected_template)
        capture_pattern_btn = QPushButton("Capture Pattern")
        capture_pattern_btn.setProperty("role", "primary")
        capture_pattern_btn.clicked.connect(self._capture_template)
        top_row = QHBoxLayout(); top_row.setSpacing(8)
        top_row.addWidget(self._template_combo, 1)
        top_row.addWidget(use_existing_btn)
        top_row.addWidget(capture_pattern_btn)
        tl.addLayout(top_row, 0, 0, 1, 2)

        self._preview_label = QLabel("(no template selected)")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setFixedSize(240, 128)
        self._preview_label.setProperty("role", "preview")
        tl.addWidget(self._preview_label, 1, 0, 2, 1)

        meta_wrap = QWidget()
        meta_layout = QVBoxLayout(meta_wrap)
        meta_layout.setContentsMargins(0, 2, 0, 0)
        meta_layout.setSpacing(8)
        thr_row = QHBoxLayout(); thr_row.setSpacing(6)
        thr_row.addWidget(_mk_label("Threshold", muted=True))
        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.0, 1.0)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setSingleStep(0.01)
        self._threshold_spin.setValue(0.90)
        self._threshold_spin.setFixedWidth(78)
        thr_row.addWidget(self._threshold_spin)
        thr_row.addStretch(1)
        meta_layout.addLayout(thr_row)
        self._template_meta = QLabel("")
        self._template_meta.setProperty("role", "hint")
        self._template_meta.setWordWrap(True)
        self._template_meta.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        meta_layout.addWidget(self._template_meta)
        meta_layout.addStretch(1)
        tl.addWidget(meta_wrap, 1, 1, 2, 1)
        tl.setColumnStretch(1, 1)
        layout.addWidget(template)

        # Search scope
        scope = QGroupBox("Search scope")
        sl = QHBoxLayout(scope)
        sl.setContentsMargins(12, 18, 12, 12)
        sl.setSpacing(10)
        self._region_label = QLabel("All monitors")
        self._region_label.setProperty("role", "hint")
        sl.addWidget(self._region_label, 1)
        cap_region_btn = QPushButton("Capture Region")
        cap_region_btn.clicked.connect(self._capture_search_region)
        all_btn = QPushButton("All Monitors")
        all_btn.clicked.connect(self._use_all_monitors)
        pick_btn = QPushButton("Pick Monitor")
        pick_btn.clicked.connect(self._pick_monitor)
        sl.addWidget(cap_region_btn)
        sl.addWidget(all_btn)
        sl.addWidget(pick_btn)
        layout.addWidget(scope)

        # Actions
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        test_btn = QPushButton("Test Match")
        test_btn.clicked.connect(self._test_selected_rule)
        save_btn = QPushButton("Save Rule")
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._save_selected_rule)
        actions_row.addStretch(1)
        actions_row.addWidget(test_btn)
        actions_row.addWidget(save_btn)
        layout.addLayout(actions_row)
        layout.addStretch(1)

        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)
        return outer

    def _build_log_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 8, 14, 12)
        layout.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Log")
        title.setProperty("role", "section-title")
        header_row.addWidget(title)
        header_row.addStretch(1)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self._log_box.clear())
        header_row.addWidget(clear_btn)
        layout.addLayout(header_row)

        self._log_box = QPlainTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setMaximumBlockCount(1000)
        layout.addWidget(self._log_box, 1)
        return panel

    def _build_statusbar(self) -> None:
        bar = QStatusBar(self)
        bar.setSizeGripEnabled(True)
        bar.showMessage(f"Config: {CONFIG_PATH}")
        self.setStatusBar(bar)

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

    # --- panel toggle + window auto-resize -----------------------

    def _on_workspace_toggled(self, checked: bool) -> None:
        if checked:
            self._workspace_panel.setVisible(True)
            # restore workspace size and grow the window
            self._v_splitter.setSizes([self._remembered_workspace_h, self._v_splitter.sizes()[1]])
            self.resize(self.width(), self.height() + self._remembered_workspace_h)
        else:
            sizes = self._v_splitter.sizes()
            if sizes[0] > 0:
                self._remembered_workspace_h = sizes[0]
            self.resize(self.width(), max(self.minimumHeight(), self.height() - sizes[0]))
            self._workspace_panel.setVisible(False)

    def _on_log_toggled(self, checked: bool) -> None:
        if checked:
            self._log_panel.setVisible(True)
            self._v_splitter.setSizes([self._v_splitter.sizes()[0], self._remembered_log_h])
            self.resize(self.width(), self.height() + self._remembered_log_h)
        else:
            sizes = self._v_splitter.sizes()
            if sizes[1] > 0:
                self._remembered_log_h = sizes[1]
            self.resize(self.width(), max(self.minimumHeight(), self.height() - sizes[1]))
            self._log_panel.setVisible(False)

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        sizes = self._v_splitter.sizes()
        if self._workspace_panel.isVisible() and sizes[0] > 0:
            self._remembered_workspace_h = sizes[0]
        if self._log_panel.isVisible() and sizes[1] > 0:
            self._remembered_log_h = sizes[1]

    # --- cfg helpers ----------------------------------------------

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

    # --- log -------------------------------------------------------

    def _log(self, message: str) -> None:
        self._log_box.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")

    # --- rule list / editor ---------------------------------------

    def _refresh_rule_list(self, select_idx: Optional[int] = None) -> None:
        current = select_idx if select_idx is not None else self._current_rule_index()
        self._rules_list.blockSignals(True)
        self._rules_list.clear()
        for rule in self._cfg.get("rules", []):
            self._rules_list.addItem(
                QListWidgetItem(make_rule_summary(rule, self._last_scores.get(rule["id"])))
            )
        self._rules_list.blockSignals(False)
        if current is not None and self._rules_list.count() > 0:
            self._rules_list.setCurrentRow(max(0, min(self._rules_list.count() - 1, current)))
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
        self._template_combo.setCurrentText(rule.get("template_path") or "")
        region = rule.get("search_region")
        self._region_label.setText(
            f"{region[2]} × {region[3]} @ ({region[0]}, {region[1]})" if region else "All monitors"
        )
        self._update_action_fields()
        self._update_template_preview(self._template_combo.currentText())

    def _clear_editor(self) -> None:
        self._name_edit.clear()
        self._enabled_check.setChecked(True)
        self._threshold_spin.setValue(0.90)
        self._action_combo.setCurrentText(ACTION_CLICK)
        self._text_edit.setText("continue")
        self._template_combo.setCurrentText("")
        self._region_label.setText("All monitors")
        self._update_action_fields()

    def _update_action_fields(self, *_args) -> None:
        self._text_edit.setEnabled(self._action_combo.currentText() == ACTION_CLICK_TYPE_ENTER)

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

    # --- templates & region ---------------------------------------

    def _refresh_template_choices(self, selected: Optional[str] = None) -> None:
        current = selected if selected is not None else self._template_combo.currentText()
        items = [""] + list_template_files()
        self._template_combo.blockSignals(True)
        self._template_combo.clear()
        self._template_combo.addItems(items)
        self._template_combo.setCurrentText(current if current in items else "")
        self._template_combo.blockSignals(False)
        self._update_template_preview(self._template_combo.currentText())

    def _update_template_preview(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("(no template selected)")
            self._template_meta.setText(""); return
        path = resolve_template_path(name)
        if path is None or not Path(path).exists():
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("(file missing)")
            self._template_meta.setText(f"File: {name}\n(not found under templates/)"); return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("(preview error)")
            self._template_meta.setText(f"File: {name}"); return
        nw, nh = pixmap.width(), pixmap.height()
        bw, bh = self._preview_label.width() - 8, self._preview_label.height() - 8
        if nw > bw or nh > bh:
            scaled = pixmap.scaled(bw, bh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            note = "fit to preview"
        else:
            scaled = pixmap
            note = "actual size"
        self._preview_label.setPixmap(scaled)
        self._preview_label.setText("")
        self._template_meta.setText(f"{name}\n{nw} × {nh} px  ·  {note}")

    def _use_selected_template(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[template] select a rule first"); return
        choice = self._template_combo.currentText().strip()
        if not choice:
            self._log("[template] choose an existing template first"); return
        with self._cfg_lock:
            self._cfg["rules"][idx]["template_path"] = choice
        self._persist(); self._refresh_rule_list(idx)
        self._log(f"[template] selected {choice}")

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
                self._cfg["rules"][idx]["template_path"] = stored_path
            self._persist(); self._refresh_rule_list(idx); self._refresh_template_choices(stored_path)
            self._log(
                f"[capture] {path.name}  bbox=({bbox[0]},{bbox[1]}) size={bbox[2]}x{bbox[3]} → {gray.shape[1]}x{gray.shape[0]}"
            )
        except Exception as exc:
            self._log(f"[error] template capture failed: {exc}")

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
        if dialog.exec() == QDialog.Accepted and dialog.selected:
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
                rule = dict(self._cfg["rules"][idx])
            tpl_path = resolve_template_path(rule.get("template_path"))
            if tpl_path is None or not Path(tpl_path).exists():
                self._log("[test] capture a template first"); return
            runtime_rule = build_runtime_rules({"rules": [rule]})
            if not runtime_rule:
                self._log("[test] rule is not ready"); return
            frame = capture_screen_gray()
            score, center = evaluate_rule_on_frame(frame, runtime_rule[0])
            matched = center is not None and score >= float(rule.get("threshold", 0.90))
            self._last_scores[rule["id"]] = score
            self._refresh_rule_list(idx)
            self._log(
                f"[test] {rule['name']}  {'match' if matched else 'no-match'}  "
                f"score={score:.3f}  center={center}"
            )
        except Exception as exc:
            self._log(f"[error] test failed: {exc}")

    # --- run control ----------------------------------------------

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
            self._tray.setIcon(self._icon_running)
            self._tray.setToolTip("Auto Press — Running")
            self._tray_toggle_action.setText("Stop")
            self.setWindowIcon(self._icon_running)
        else:
            self._status_label.setText("Stopped")
            self._status_label.setStyleSheet(f"color: {STATUS_STOPPED};")
            self._status_dot.set_color(STATUS_STOPPED)
            self._start_btn.setText("Start")
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

    # --- tray / window --------------------------------------------

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

    # --- global hotkey --------------------------------------------

    def _hotkey_loop(self) -> None:
        if not IS_WINDOWS:
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        from ctypes import wintypes

        VK_PAGEDOWN = 0x22
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

        self._hotkey_thread_id["tid"] = int(kernel32.GetCurrentThreadId())
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, VK_PAGEDOWN):
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


# -------------------------- helpers ------------------------------------


def _mk_label(text: str, *, muted: bool = False) -> QLabel:
    label = QLabel(text)
    if muted:
        label.setProperty("role", "muted")
    return label


def _flex_spacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    return w
