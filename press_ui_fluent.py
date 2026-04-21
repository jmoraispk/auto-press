"""Auto Press — Fluent Design UI draft.

Uses QFluentWidgets on top of PySide6 for a Windows 11 Settings-app look.
Engine / store / core are reused; capture overlays, engine worker, tray
helpers, and the Win32 hotkey loop are reused from press_ui_qt.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSystemTrayIcon,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    HeaderCardWidget,
    LineEdit,
    TableWidget,
    PlainTextEdit as FluentPlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    Theme,
    ToolButton,
    setTheme,
    setThemeColor,
)
from qfluentwidgets.components.widgets.spin_box import DoubleSpinBox

from press_core import save_gray_image
from press_engine import (
    build_runtime_rules,
    capture_screen_gray,
    ensure_vision,
    evaluate_rule_on_frame,
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
from press_ui_qt import (
    STATUS_RUNNING,
    STATUS_STOPPED,
    EngineWorker,
    MonitorPickDialog,
    StatusDot,
    _make_dot_icon,
    capture_drag_bbox,
)


IS_WINDOWS = sys.platform.startswith("win")

WINDOWS_ACCENT = "#2b7de9"


class _VLine(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.VLine)
        self.setFixedWidth(1)
        self.setStyleSheet("background: rgba(255,255,255,24); border: none;")


class CollapsibleCard(HeaderCardWidget):
    """HeaderCardWidget with a chevron toggle that hides/shows the body."""

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

    def setExpanded(self, expanded: bool) -> None:
        if self._expanded != expanded:
            self._toggle()


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

        # Background matches Fluent dark surface
        self.setStyleSheet(
            "QMainWindow { background: #1b1b1f; }"
            "QWidget { background: transparent; color: #e4e4e7; }"
            "QScrollArea { background: transparent; border: none; }"
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
        self._remembered_workspace_h = 460
        self._remembered_log_h = 180

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
        self._hotkey_stop = threading.Event()
        self._hotkey_thread_id: dict[str, int | None] = {"tid": None}
        if IS_WINDOWS:
            threading.Thread(target=self._hotkey_loop, daemon=True).start()

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

        self._v_splitter = QSplitter(Qt.Vertical)
        self._v_splitter.setChildrenCollapsible(False)
        self._v_splitter.setHandleWidth(6)

        self._workspace_panel = self._build_workspace_panel()
        self._log_panel = self._build_log_panel()

        self._v_splitter.addWidget(self._workspace_panel)
        self._v_splitter.addWidget(self._log_panel)
        self._v_splitter.setStretchFactor(0, 3)
        self._v_splitter.setStretchFactor(1, 1)
        self._v_splitter.setSizes([self._remembered_workspace_h, self._remembered_log_h])
        self._v_splitter.splitterMoved.connect(self._on_splitter_moved)
        self._workspace_panel.setMinimumHeight(140)
        self._log_panel.setMinimumHeight(110)

        root.addWidget(self._v_splitter, 1)
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
        lay.addWidget(self._action_status)

        lay.addStretch(1)

        self._workspace_toggle = CheckBox("Workspace")
        self._workspace_toggle.setChecked(True)
        self._workspace_toggle.toggled.connect(self._on_workspace_toggled)
        lay.addWidget(self._workspace_toggle)

        self._log_toggle = CheckBox("Log")
        self._log_toggle.setChecked(True)
        self._log_toggle.toggled.connect(self._on_log_toggled)
        lay.addWidget(self._log_toggle)

        return bar

    def _build_workspace_panel(self) -> QWidget:
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        split.addWidget(self._build_rules_card())
        split.addWidget(self._build_editor_scroll())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        split.setSizes([320, 760])
        return split

    def _build_rules_card(self) -> QWidget:
        card = HeaderCardWidget()
        card.setTitle("Rules")
        card.setMinimumWidth(260)
        body = QVBoxLayout()
        body.setContentsMargins(2, 0, 2, 0)
        body.setSpacing(8)

        self._rules_list = TableWidget()
        self._rules_list.setColumnCount(3)
        self._rules_list.setHorizontalHeaderLabels(["Rule", "", "Action"])
        self._rules_list.verticalHeader().setVisible(False)
        self._rules_list.horizontalHeader().setVisible(False)
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

        grid.addWidget(CaptionLabel("Name"), 0, 0)
        grid.addWidget(CaptionLabel("Action"), 0, 1)
        grid.addWidget(CaptionLabel("Text"), 0, 2)

        self._name_edit = LineEdit()
        self._action_combo = ComboBox()
        self._action_combo.addItems(ACTION_TYPES)
        self._action_combo.currentTextChanged.connect(self._update_action_fields)
        self._text_edit = LineEdit()
        self._text_edit.setPlaceholderText("used when action is click+type+enter")

        grid.addWidget(self._name_edit, 1, 0)
        grid.addWidget(self._action_combo, 1, 1)
        grid.addWidget(self._text_edit, 1, 2)

        self._enabled_check = CheckBox("Enabled")
        grid.addWidget(self._enabled_check, 2, 0)

        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)

        card.viewLayout.addLayout(grid)
        return card

    def _build_template_card(self) -> QWidget:
        card = CollapsibleCard("Template")

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        self._template_combo = ComboBox()
        self._template_combo.setMinimumWidth(200)
        self._template_combo.currentTextChanged.connect(self._update_template_preview)
        use_existing_btn = PushButton(FIF.LINK, "Use existing")
        use_existing_btn.clicked.connect(self._use_selected_template)
        capture_btn = PrimaryPushButton(FIF.CAMERA, "Capture pattern")
        capture_btn.clicked.connect(self._capture_template)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.addWidget(self._template_combo, 1)
        top_row.addWidget(use_existing_btn)
        top_row.addWidget(capture_btn)
        grid.addLayout(top_row, 0, 0, 1, 2)

        self._preview_label = QLabel("(no template selected)")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setFixedSize(240, 128)
        self._preview_label.setStyleSheet(
            "QLabel { background: rgba(0,0,0,0.25); "
            "border: 1px dashed rgba(255,255,255,0.18); border-radius: 6px; "
            "color: #a1a1aa; }"
        )
        grid.addWidget(self._preview_label, 1, 0, 2, 1)

        meta_box = QWidget()
        meta_lay = QVBoxLayout(meta_box)
        meta_lay.setContentsMargins(0, 2, 0, 0)
        meta_lay.setSpacing(6)

        thr_row = QHBoxLayout()
        thr_row.setSpacing(6)
        thr_row.addWidget(CaptionLabel("Threshold"))
        self._threshold_spin = DoubleSpinBox()
        self._threshold_spin.setRange(0.0, 1.0)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setSingleStep(0.01)
        self._threshold_spin.setValue(0.90)
        self._threshold_spin.setFixedWidth(130)
        self._threshold_spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        thr_row.addWidget(self._threshold_spin)
        thr_row.addStretch(1)
        meta_lay.addLayout(thr_row)

        self._template_meta = BodyLabel("")
        self._template_meta.setStyleSheet("color: #9ca3af;")
        self._template_meta.setWordWrap(True)
        self._template_meta.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        meta_lay.addWidget(self._template_meta)
        meta_lay.addStretch(1)

        grid.addWidget(meta_box, 1, 1, 2, 1)
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
        card = HeaderCardWidget()
        card.setTitle("Log")

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

    # ---------- panel toggle auto-resize ----------

    def _on_workspace_toggled(self, checked: bool) -> None:
        if checked:
            self._workspace_panel.setVisible(True)
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
            font = mark_item.font()
            font.setPointSize(11)
            font.setBold(True)
            mark_item.setFont(font)
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

    # ---------- templates ----------

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
