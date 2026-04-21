"""Qt-based rule automation UI for Auto Press."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEvent,
    QEventLoop,
    QObject,
    QPoint,
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
    QFont,
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
    QFormLayout,
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
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSplitter,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QToolBar,
    QToolButton,
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

ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
BG = "#17181d"
SURFACE = "#23242b"
SURFACE_2 = "#2a2c34"
BORDER = "#34363f"
TEXT = "#e6e7ea"
MUTED = "#8e90a0"
STATUS_RUNNING = "#22c55e"
STATUS_STOPPED = "#ef4444"
RECT_STROKE = "#22c55e"

STYLESHEET = f"""
QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; font-family: "Segoe UI"; font-size: 10pt; }}
QToolBar {{ background: {SURFACE}; border: none; padding: 8px 10px; spacing: 10px; }}
QToolBar QLabel[role="status"] {{ font-weight: 600; padding-left: 4px; }}
QToolBar QLabel[role="countdown"] {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 13pt; color: {MUTED}; }}
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
QPushButton:hover {{ background: #323542; border-color: #434656; }}
QPushButton:pressed {{ background: #1d1f26; }}
QPushButton:disabled {{ color: #5c5e68; background: #22232a; }}
QPushButton[role="primary"] {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[role="primary"]:pressed {{ background: #1d4ed8; }}

QLineEdit, QDoubleSpinBox, QComboBox {{
    background: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 3px; border: 1px solid {BORDER}; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}

QListWidget {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
    outline: 0;
}}
QListWidget::item {{ padding: 7px 8px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {ACCENT}; color: white; }}
QListWidget::item:hover:!selected {{ background: #2d2f38; }}

QPlainTextEdit {{
    background: #12131a;
    color: #d1d5db;
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 10pt;
    padding: 8px;
}}

QGroupBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 14px 12px 14px;
    font-weight: 600;
    color: {TEXT};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; }}

QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}

QLabel[role="section"] {{ color: {TEXT}; font-weight: 600; font-size: 11pt; }}
QLabel[role="hint"] {{ color: {MUTED}; }}
QLabel[role="preview"] {{ background: #0f1014; border: 1px solid {BORDER}; border-radius: 6px; color: {MUTED}; }}

QMenu {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
"""


# -------------------------- tray icons ---------------------------------


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
    tick_done = Signal(list, list, float)  # results, actions, interval
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
            # wait out the interval in small slices so stop/pause is responsive
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
    """Frameless, translucent widget covering one monitor for drag-capture."""

    def __init__(self, screen_rect: QRect, controller: "CaptureController"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setCursor(Qt.CrossCursor)
        self._screen_rect = screen_rect
        self._controller = controller
        self.setGeometry(screen_rect)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if self._controller.start is None:
            return
        sx, sy = self._controller.start
        cx, cy = self._controller.current
        left = min(sx, cx)
        right = max(sx, cx)
        top = min(sy, cy)
        bottom = max(sy, cy)
        ml = self._screen_rect.left()
        mt = self._screen_rect.top()
        mr = self._screen_rect.right() + 1
        mb = self._screen_rect.bottom() + 1
        il = max(left, ml)
        it = max(top, mt)
        ir = min(right, mr)
        ib = min(bottom, mb)
        if ir <= il or ib <= it:
            return
        # clear the selection area (looks crisp against the dim fill)
        inner = QRect(il - ml, it - mt, ir - il, ib - it)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(inner, QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setPen(QPen(QColor(RECT_STROKE), 2))
        p.drawRect(inner.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            pos = QCursor.pos()
            self._controller.on_press(pos.x(), pos.y())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._controller.start is not None:
            pos = QCursor.pos()
            self._controller.on_motion(pos.x(), pos.y())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._controller.start is not None:
            pos = QCursor.pos()
            self._controller.on_release(pos.x(), pos.y())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self._controller.cancel()


class CaptureController(QObject):
    done = Signal(object)  # list[int] | None

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.start: Optional[tuple[int, int]] = None
        self.current: Optional[tuple[int, int]] = None
        self._overlays: list[CaptureOverlay] = []

    def begin(self) -> None:
        self.start = None
        self.current = None
        self._overlays = []
        for screen in QGuiApplication.screens():
            overlay = CaptureOverlay(screen.geometry(), self)
            overlay.show()
            self._overlays.append(overlay)
        if self._overlays:
            first = self._overlays[0]
            first.activateWindow()
            first.raise_()
            first.setFocus()

    def _redraw(self) -> None:
        for ov in self._overlays:
            ov.update()

    def on_press(self, x: int, y: int) -> None:
        self.start = (x, y)
        self.current = (x, y)
        self._redraw()

    def on_motion(self, x: int, y: int) -> None:
        self.current = (x, y)
        self._redraw()

    def on_release(self, x: int, y: int) -> None:
        if self.start is None:
            self._cleanup()
            self.done.emit(None)
            return
        sx, sy = self.start
        left = min(sx, x)
        right = max(sx, x)
        top = min(sy, y)
        bottom = max(sy, y)
        w = right - left
        h = bottom - top
        self._cleanup()
        if w >= 5 and h >= 5:
            self.done.emit([left, top, w, h])
        else:
            self.done.emit(None)

    def cancel(self) -> None:
        self._cleanup()
        self.done.emit(None)

    def _cleanup(self) -> None:
        for ov in self._overlays:
            ov.close()
            ov.deleteLater()
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
        layout.addWidget(QLabel("Restrict the scan to a single monitor:"))

        for i, screen in enumerate(QGuiApplication.screens(), start=1):
            geom = screen.geometry()
            bbox = [geom.left(), geom.top(), geom.width(), geom.height()]
            btn = QPushButton(
                f"Monitor {i}: {geom.width()}×{geom.height()} @ ({geom.left()}, {geom.top()})"
            )
            btn.setMinimumWidth(300)
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

    def __init__(self, initial_seconds: float):
        super().__init__()
        self.hotkey_triggered.connect(self._toggle_running, Qt.QueuedConnection)
        self.setWindowTitle("Auto Press")
        self.resize(1040, 680)
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

        # Tray state
        self._icon_running = _make_dot_icon(QColor(STATUS_RUNNING))
        self._icon_stopped = _make_dot_icon(QColor(STATUS_STOPPED))
        self.setWindowIcon(self._icon_stopped)

        # UI
        self._build_ui(initial_seconds)
        self._build_tray()

        # Engine worker on a background thread
        self._worker = EngineWorker(self._snapshot_cfg)
        self._worker_thread = QThread(self)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.tick_done.connect(self._on_tick_done)
        self._worker.tick_error.connect(self._on_worker_error)
        self._worker.running_changed.connect(self._on_running_changed)
        self._worker.needs_rules.connect(self._on_needs_rules)
        self._worker_thread.start()

        # Countdown timer
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(100)
        self._countdown_timer.timeout.connect(self._update_countdown)
        self._countdown_timer.start()

        # Global Page Down hotkey
        self._hotkey_stop = threading.Event()
        self._hotkey_thread_id: dict[str, int | None] = {"tid": None}
        if IS_WINDOWS:
            threading.Thread(target=self._hotkey_loop, daemon=True).start()

        # Initial UI state
        self._refresh_template_choices()
        self._refresh_rule_list(0 if self._cfg.get("rules") else None)
        self._set_running_status(False)
        self._log(f"[ready] loaded {CONFIG_PATH}")

    # --- layout ----------------------------------------------------

    def _build_ui(self, initial_seconds: float) -> None:
        self._toolbar = QToolBar("Main")
        self._toolbar.setMovable(False)
        self._toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(Qt.TopToolBarArea, self._toolbar)

        self._start_btn = QPushButton("Start")
        self._start_btn.setProperty("role", "primary")
        self._start_btn.setMinimumWidth(96)
        self._start_btn.clicked.connect(self._toggle_running)
        self._toolbar.addWidget(self._start_btn)
        self._toolbar.addWidget(_spacer(8))

        self._toolbar.addWidget(_label("Interval (s):", muted=True))
        self._interval_spin = QDoubleSpinBox()
        self._interval_spin.setRange(0.1, 86400.0)
        self._interval_spin.setDecimals(1)
        self._interval_spin.setSingleStep(0.5)
        self._interval_spin.setValue(float(initial_seconds))
        self._interval_spin.setFixedWidth(90)
        self._interval_spin.valueChanged.connect(self._on_interval_changed)
        self._toolbar.addWidget(self._interval_spin)

        self._toolbar.addWidget(_spacer(12))
        self._countdown_label = QLabel("")
        self._countdown_label.setProperty("role", "countdown")
        self._toolbar.addWidget(self._countdown_label)

        self._toolbar.addWidget(_spacer(12))
        self._status_label = QLabel("Stopped")
        self._status_label.setProperty("role", "status")
        self._status_label.setStyleSheet(f"color: {STATUS_STOPPED};")
        self._toolbar.addWidget(self._status_label)

        self._action_status = QLabel("Idle")
        self._action_status.setProperty("role", "hint")
        self._toolbar.addWidget(_spacer(10))
        self._toolbar.addWidget(self._action_status)

        self._toolbar.addWidget(_flex_spacer())

        self._workspace_toggle = QCheckBox("Workspace")
        self._workspace_toggle.setChecked(True)
        self._workspace_toggle.stateChanged.connect(self._update_panels)
        self._toolbar.addWidget(self._workspace_toggle)

        self._toolbar.addWidget(_spacer(10))
        self._log_toggle = QCheckBox("Log")
        self._log_toggle.setChecked(True)
        self._log_toggle.stateChanged.connect(self._update_panels)
        self._toolbar.addWidget(self._log_toggle)
        self._toolbar.addWidget(_spacer(4))

        # Body
        self._v_splitter = QSplitter(Qt.Vertical)
        self._v_splitter.setChildrenCollapsible(False)
        self.setCentralWidget(self._v_splitter)

        self._h_splitter = QSplitter(Qt.Horizontal)
        self._h_splitter.setChildrenCollapsible(False)
        self._v_splitter.addWidget(self._h_splitter)

        self._h_splitter.addWidget(self._build_rules_panel())
        self._h_splitter.addWidget(self._build_editor_panel())
        self._h_splitter.setStretchFactor(0, 1)
        self._h_splitter.setStretchFactor(1, 2)
        self._h_splitter.setSizes([300, 700])

        self._log_panel = self._build_log_panel()
        self._v_splitter.addWidget(self._log_panel)
        self._v_splitter.setStretchFactor(0, 4)
        self._v_splitter.setStretchFactor(1, 1)
        self._v_splitter.setSizes([520, 160])

        # Status bar with config path
        status_bar = QStatusBar(self)
        status_bar.showMessage(f"Config: {CONFIG_PATH}")
        self.setStatusBar(status_bar)

    def _build_rules_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 8, 10)
        layout.setSpacing(10)

        header = QLabel("Rules")
        header.setProperty("role", "section")
        layout.addWidget(header)

        self._rules_list = QListWidget()
        self._rules_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._rules_list.currentRowChanged.connect(self._load_selected_rule)
        layout.addWidget(self._rules_list, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        self._add_btn = QPushButton("Add")
        self._delete_btn = QPushButton("Delete")
        self._up_btn = QPushButton("↑")
        self._down_btn = QPushButton("↓")
        self._up_btn.setMaximumWidth(40)
        self._down_btn.setMaximumWidth(40)
        self._add_btn.clicked.connect(self._add_rule)
        self._delete_btn.clicked.connect(self._delete_rule)
        self._up_btn.clicked.connect(lambda: self._move_rule(-1))
        self._down_btn.clicked.connect(lambda: self._move_rule(1))
        for btn in (self._add_btn, self._delete_btn, self._up_btn, self._down_btn):
            button_row.addWidget(btn)
        layout.addLayout(button_row)

        return panel

    def _build_editor_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 14, 14, 10)
        layout.setSpacing(12)

        header = QLabel("Rule Editor")
        header.setProperty("role", "section")
        layout.addWidget(header)

        # -- basics
        basics = QGroupBox("Basics")
        basics_layout = QGridLayout(basics)
        basics_layout.setContentsMargins(14, 18, 14, 14)
        basics_layout.setHorizontalSpacing(12)
        basics_layout.setVerticalSpacing(8)
        basics_layout.addWidget(_label("Name", muted=True), 0, 0)
        basics_layout.addWidget(_label("Action", muted=True), 0, 1)
        basics_layout.addWidget(_label("Text (optional)", muted=True), 0, 2)
        self._name_edit = QLineEdit()
        self._action_combo = QComboBox()
        self._action_combo.addItems(ACTION_TYPES)
        self._action_combo.currentTextChanged.connect(self._update_action_fields)
        self._text_edit = QLineEdit()
        basics_layout.addWidget(self._name_edit, 1, 0)
        basics_layout.addWidget(self._action_combo, 1, 1)
        basics_layout.addWidget(self._text_edit, 1, 2)
        self._enabled_check = QCheckBox("Enabled")
        basics_layout.addWidget(self._enabled_check, 2, 0)
        basics_layout.setColumnStretch(0, 2)
        basics_layout.setColumnStretch(1, 2)
        basics_layout.setColumnStretch(2, 2)
        layout.addWidget(basics)

        # -- template & matching
        template = QGroupBox("Template & Matching")
        tlayout = QGridLayout(template)
        tlayout.setContentsMargins(14, 18, 14, 14)
        tlayout.setHorizontalSpacing(12)
        tlayout.setVerticalSpacing(10)

        self._template_combo = QComboBox()
        self._template_combo.setMinimumWidth(220)
        self._template_combo.currentTextChanged.connect(self._update_template_preview)
        use_existing_btn = QPushButton("Use Existing")
        use_existing_btn.clicked.connect(self._use_selected_template)
        capture_pattern_btn = QPushButton("Capture Pattern")
        capture_pattern_btn.setProperty("role", "primary")
        capture_pattern_btn.clicked.connect(self._capture_template)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(self._template_combo, 1)
        row1.addWidget(use_existing_btn)
        row1.addWidget(capture_pattern_btn)
        tlayout.addLayout(row1, 0, 0, 1, 2)

        self._preview_label = QLabel("(no template selected)")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setFixedSize(260, 140)
        self._preview_label.setProperty("role", "preview")
        tlayout.addWidget(self._preview_label, 1, 0, 2, 1)

        threshold_row = QHBoxLayout()
        threshold_row.setSpacing(6)
        threshold_row.addWidget(_label("Match threshold", muted=True))
        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.0, 1.0)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setSingleStep(0.01)
        self._threshold_spin.setValue(0.90)
        self._threshold_spin.setFixedWidth(90)
        threshold_row.addWidget(self._threshold_spin)
        threshold_row.addStretch(1)
        threshold_wrap = QWidget()
        threshold_wrap.setLayout(threshold_row)
        tlayout.addWidget(threshold_wrap, 1, 1)

        self._template_meta = QLabel("")
        self._template_meta.setProperty("role", "hint")
        self._template_meta.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._template_meta.setWordWrap(True)
        tlayout.addWidget(self._template_meta, 2, 1)
        tlayout.setColumnStretch(1, 1)
        layout.addWidget(template)

        # -- search scope
        scope = QGroupBox("Search Scope")
        slayout = QHBoxLayout(scope)
        slayout.setContentsMargins(14, 18, 14, 14)
        slayout.setSpacing(10)
        self._region_label = QLabel("All monitors")
        self._region_label.setProperty("role", "hint")
        slayout.addWidget(self._region_label, 1)
        capture_region_btn = QPushButton("Capture Search Region")
        capture_region_btn.clicked.connect(self._capture_search_region)
        all_monitors_btn = QPushButton("All Monitors")
        all_monitors_btn.clicked.connect(self._use_all_monitors)
        pick_monitor_btn = QPushButton("Pick Monitor")
        pick_monitor_btn.clicked.connect(self._pick_monitor)
        slayout.addWidget(capture_region_btn)
        slayout.addWidget(all_monitors_btn)
        slayout.addWidget(pick_monitor_btn)
        layout.addWidget(scope)

        # -- actions
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        test_btn = QPushButton("Test Match")
        test_btn.clicked.connect(self._test_selected_rule)
        save_btn = QPushButton("Save Rule")
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._save_selected_rule)
        actions_row.addWidget(test_btn)
        actions_row.addWidget(save_btn)
        actions_row.addStretch(1)
        layout.addLayout(actions_row)
        layout.addStretch(1)
        return panel

    def _build_log_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 6, 14, 14)
        layout.setSpacing(6)

        header = QLabel("Log")
        header.setProperty("role", "section")
        layout.addWidget(header)

        self._log_box = QPlainTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setMaximumBlockCount(1000)
        layout.addWidget(self._log_box, 1)
        return panel

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._icon_stopped, self)
        self._tray.setToolTip("Auto Press — Stopped")
        self._tray_menu = QMenu()
        self._tray_show_action = QAction("Show window", self)
        self._tray_toggle_action = QAction("Start", self)
        self._tray_quit_action = QAction("Quit Auto Press", self)
        self._tray_show_action.triggered.connect(self._toggle_window_visibility)
        self._tray_toggle_action.triggered.connect(self._toggle_running)
        self._tray_quit_action.triggered.connect(self._quit_app)
        self._tray_menu.addAction(self._tray_show_action)
        self._tray_menu.addAction(self._tray_toggle_action)
        self._tray_menu.addSeparator()
        self._tray_menu.addAction(self._tray_quit_action)
        self._tray.setContextMenu(self._tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

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
        if idx is None or idx < 0:
            return None
        return idx

    def _current_rule(self) -> Optional[dict]:
        idx = self._current_rule_index()
        if idx is None:
            return None
        rules = self._cfg.get("rules", [])
        if 0 <= idx < len(rules):
            return rules[idx]
        return None

    # --- log ------------------------------------------------------

    def _log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        self._log_box.appendPlainText(line)

    # --- rule list ------------------------------------------------

    def _refresh_rule_list(self, select_idx: Optional[int] = None) -> None:
        current = select_idx if select_idx is not None else self._current_rule_index()
        self._rules_list.blockSignals(True)
        self._rules_list.clear()
        for rule in self._cfg.get("rules", []):
            item = QListWidgetItem(make_rule_summary(rule, self._last_scores.get(rule["id"])))
            self._rules_list.addItem(item)
        self._rules_list.blockSignals(False)
        if current is not None and self._rules_list.count() > 0:
            bounded = max(0, min(self._rules_list.count() - 1, current))
            self._rules_list.setCurrentRow(bounded)
        else:
            self._clear_editor()

    def _load_selected_rule(self, _row: int = -1) -> None:
        rule = self._current_rule()
        if rule is None:
            self._clear_editor()
            return
        self._name_edit.setText(rule.get("name", ""))
        self._enabled_check.setChecked(bool(rule.get("enabled", True)))
        self._threshold_spin.setValue(float(rule.get("threshold", 0.90)))
        self._action_combo.setCurrentText(rule.get("action", ACTION_CLICK))
        self._text_edit.setText(rule.get("text", "continue"))
        stored_template = rule.get("template_path") or ""
        self._template_combo.setCurrentText(stored_template)
        region = rule.get("search_region")
        if region:
            self._region_label.setText(
                f"{region[2]}×{region[3]} @ ({region[0]}, {region[1]})"
            )
        else:
            self._region_label.setText("All monitors")
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
        is_type_enter = self._action_combo.currentText() == ACTION_CLICK_TYPE_ENTER
        self._text_edit.setEnabled(is_type_enter)

    def _add_rule(self) -> None:
        with self._cfg_lock:
            rule = default_rule(name=f"Rule {len(self._cfg['rules']) + 1}")
            rule["priority"] = len(self._cfg["rules"]) + 1
            self._cfg["rules"].append(rule)
            idx = len(self._cfg["rules"]) - 1
        self._persist()
        self._refresh_rule_list(idx)
        self._log(f"[rule] added {rule['name']}")

    def _delete_rule(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[rule] select a rule to delete")
            return
        with self._cfg_lock:
            removed = self._cfg["rules"].pop(idx)
            for pos, item in enumerate(self._cfg["rules"], start=1):
                item["priority"] = pos
            self._last_scores.pop(removed["id"], None)
        self._persist()
        self._refresh_rule_list(max(0, idx - 1))
        self._log(f"[rule] deleted {removed['name']}")

    def _move_rule(self, direction: int) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[rule] select a rule to move")
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
        self._persist()
        self._refresh_rule_list(new_idx)

    def _save_selected_rule(self) -> bool:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[rule] select a rule first")
            return False
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
        self._persist()
        self._refresh_rule_list(idx)
        self._log(f"[rule] saved {self._name_edit.text().strip() or f'Rule {idx + 1}'}")
        return True

    # --- templates & search region --------------------------------

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
            self._template_meta.setText("")
            return
        path = resolve_template_path(name)
        if path is None or not Path(path).exists():
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("(file missing)")
            self._template_meta.setText(f"File: {name}\n(not found under templates/)")
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("(preview error)")
            self._template_meta.setText(f"File: {name}")
            return
        native_w, native_h = pixmap.width(), pixmap.height()
        box_w, box_h = self._preview_label.width() - 8, self._preview_label.height() - 8
        if native_w > box_w or native_h > box_h:
            scaled = pixmap.scaled(box_w, box_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scale_note = " (fit to preview)"
        else:
            scaled = pixmap
            scale_note = " (actual size)"
        self._preview_label.setPixmap(scaled)
        self._preview_label.setText("")
        self._template_meta.setText(
            f"File: {name}\nSize: {native_w} × {native_h} px{scale_note}"
        )

    def _use_selected_template(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[template] select a rule first")
            return
        choice = self._template_combo.currentText().strip()
        if not choice:
            self._log("[template] choose an existing template first")
            return
        with self._cfg_lock:
            self._cfg["rules"][idx]["template_path"] = choice
        self._persist()
        self._refresh_rule_list(idx)
        self._log(f"[template] selected {choice}")

    def _capture_template(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[capture] add or select a rule first")
            return
        try:
            ensure_vision()
        except Exception as exc:
            self._log(f"[error] {exc}")
            return
        bbox = capture_drag_bbox(self)
        if not bbox:
            self._log("[capture] template capture cancelled")
            return
        try:
            gray = capture_screen_gray(tuple(bbox))
            file_name = f"rule_{self._cfg['rules'][idx]['id']}.png"
            path = template_asset_path(file_name)
            save_gray_image(str(path), gray)
            stored_path = serialize_template_path(path)
            with self._cfg_lock:
                self._cfg["rules"][idx]["template_path"] = stored_path
            self._persist()
            self._refresh_rule_list(idx)
            self._refresh_template_choices(stored_path)
            self._log(
                f"[capture] template saved to {path.name} "
                f"bbox=({bbox[0]},{bbox[1]}) size={bbox[2]}x{bbox[3]} -> captured {gray.shape[1]}x{gray.shape[0]}"
            )
        except Exception as exc:
            self._log(f"[error] template capture failed: {exc}")

    def _capture_search_region(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[capture] select a rule first")
            return
        bbox = capture_drag_bbox(self)
        if not bbox:
            self._log("[capture] search region cancelled")
            return
        with self._cfg_lock:
            self._cfg["rules"][idx]["search_region"] = bbox
        self._persist()
        self._refresh_rule_list(idx)
        self._region_label.setText(f"{bbox[2]}×{bbox[3]} @ ({bbox[0]}, {bbox[1]})")
        self._log(f"[capture] search region set: bbox=({bbox[0]},{bbox[1]}) size={bbox[2]}x{bbox[3]}")

    def _use_all_monitors(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[capture] select a rule first")
            return
        with self._cfg_lock:
            self._cfg["rules"][idx]["search_region"] = None
        self._persist()
        self._refresh_rule_list(idx)
        self._region_label.setText("All monitors")
        self._log("[capture] rule now scans all monitors")

    def _pick_monitor(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[monitor] select a rule first")
            return
        dialog = MonitorPickDialog(self)
        if dialog.exec() == QDialog.Accepted and dialog.selected:
            bbox = dialog.selected
            with self._cfg_lock:
                self._cfg["rules"][idx]["search_region"] = bbox
            self._persist()
            self._refresh_rule_list(idx)
            self._region_label.setText(f"Monitor {bbox[2]}×{bbox[3]} @ ({bbox[0]}, {bbox[1]})")
            self._log(f"[monitor] search region set to {bbox[2]}x{bbox[3]} @ ({bbox[0]},{bbox[1]})")

    def _test_selected_rule(self) -> None:
        idx = self._current_rule_index()
        if idx is None:
            self._log("[test] select a rule first")
            return
        if not self._save_selected_rule():
            return
        try:
            with self._cfg_lock:
                rule = dict(self._cfg["rules"][idx])
            tpl_path = resolve_template_path(rule.get("template_path"))
            if tpl_path is None or not Path(tpl_path).exists():
                self._log("[test] capture a template first")
                return
            runtime_rule = build_runtime_rules({"rules": [rule]})
            if not runtime_rule:
                self._log("[test] rule is not ready")
                return
            frame = capture_screen_gray()
            score, center = evaluate_rule_on_frame(frame, runtime_rule[0])
            matched = center is not None and score >= float(rule.get("threshold", 0.90))
            self._last_scores[rule["id"]] = score
            self._refresh_rule_list(idx)
            self._log(
                f"[test] {rule['name']} result={'match' if matched else 'no-match'} "
                f"score={score:.3f} center={center}"
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
            self._action_status.setText("Idle")

    def _on_needs_rules(self) -> None:
        self._log("[control] add at least one enabled rule with a selected or captured template")

    def _set_running_status(self, running: bool) -> None:
        if running:
            self._status_label.setText("Running")
            self._status_label.setStyleSheet(f"color: {STATUS_RUNNING};")
            self._start_btn.setText("Stop")
            self._tray.setIcon(self._icon_running)
            self._tray.setToolTip("Auto Press — Running")
            self._tray_toggle_action.setText("Stop")
            self.setWindowIcon(self._icon_running)
        else:
            self._status_label.setText("Stopped")
            self._status_label.setStyleSheet(f"color: {STATUS_STOPPED};")
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
            summary_text = ", ".join(f"{name} ×{count}" for name, count in summaries.items())
            self._action_status.setText(summary_text)
            self._log(f"[tick] matched {summary_text}")
        else:
            self._action_status.setText("No match")
            self._log("[tick] no eligible rule matched")
        self._next_tick_at = time.monotonic() + interval

    def _on_worker_error(self, message: str) -> None:
        self._log(f"[error] {message}")

    def _update_countdown(self) -> None:
        if self._running and self._next_tick_at is not None:
            remaining = max(0.0, float(self._next_tick_at) - time.monotonic())
            self._countdown_label.setText(f"{remaining:.1f}s")
        else:
            self._countdown_label.setText("")

    # --- panel toggles --------------------------------------------

    def _update_panels(self) -> None:
        show_workspace = self._workspace_toggle.isChecked()
        show_log = self._log_toggle.isChecked()
        self._h_splitter.setVisible(show_workspace)
        self._log_panel.setVisible(show_log)

    # --- tray / window --------------------------------------------

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._toggle_window_visibility()

    def _toggle_window_visibility(self) -> None:
        if self.isVisible():
            self.hide()
            self._tray_show_action.setText("Show window")
        else:
            self.show()
            self.raise_()
            self.activateWindow()
            self._tray_show_action.setText("Hide window")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._quitting or not self._tray.isVisible():
            self._shutdown()
            event.accept()
            return
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


# -------------------------- layout helpers -----------------------------


def _label(text: str, *, muted: bool = False) -> QLabel:
    label = QLabel(text)
    if muted:
        label.setProperty("role", "hint")
    return label


def _spacer(width: int) -> QWidget:
    w = QWidget()
    w.setFixedWidth(width)
    return w


def _flex_spacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    return w
