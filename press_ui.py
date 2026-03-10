"""Tk UI runner for auto-press modes, including watch-run."""

import sys
import threading
import time

import pyautogui

from press_core import (
    MODE_CLICK,
    MODE_WATCH_RUN,
    MODES,
    MODE_CLICK_ENTER,
    MODE_ENTER,
    click_point,
    evaluate_run_for_target,
    evaluate_state_for_target,
    grab_region_gray,
    save_gray_image,
    do_action,
    load_run_templates,
    type_word_with_retry,
)
from press_store import CONFIG_PATH, TEMPLATES_DIR, load_config, save_config, template_path


MODE_LABELS = {
    MODE_ENTER: "Enter Only",
    MODE_CLICK: "Click Only",
    MODE_CLICK_ENTER: "Click + Enter",
    MODE_WATCH_RUN: "Watch Run",
}

BG = "#121212"
FG = "#E6E6E6"
BTN_BG = "#1E1E1E"
BTN_FG = FG
ENTRY_BG = "#1A1A1A"
ENTRY_FG = FG
MUTED = "#A8A8A8"

LIGHT_SIZE = 62
LIGHT_PAD = 8
DOT_DIAM = LIGHT_SIZE - 2 * LIGHT_PAD

IS_WINDOWS = sys.platform.startswith("win")


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    RegisterHotKey = user32.RegisterHotKey
    RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    RegisterHotKey.restype = wintypes.BOOL
    UnregisterHotKey = user32.UnregisterHotKey
    UnregisterHotKey.argtypes = [wintypes.HWND, wintypes.INT]
    UnregisterHotKey.restype = wintypes.BOOL
    GetMessageW = user32.GetMessageW
    GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
    GetMessageW.restype = wintypes.BOOL
    TranslateMessage = user32.TranslateMessage
    DispatchMessageW = user32.DispatchMessageW
    PostThreadMessageW = user32.PostThreadMessageW
    PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    PostThreadMessageW.restype = wintypes.BOOL
    GetCurrentThreadId = kernel32.GetCurrentThreadId
    GetCurrentThreadId.restype = wintypes.DWORD
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008
    MOD_NOREPEAT = 0x4000

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", wintypes.POINT),
        ]


def _vk_from_token(tok: str) -> int:
    t = tok.upper().strip()
    if t in ("PAGEDOWN", "PGDN", "NEXT"):
        return 0x22
    if t in ("PAGEUP", "PGUP", "PRIOR"):
        return 0x21
    if t in ("END",):
        return 0x23
    if t in ("HOME",):
        return 0x24
    if t.startswith("F") and t[1:].isdigit():
        n = int(t[1:])
        if 1 <= n <= 24:
            return 0x70 + (n - 1)
    if len(t) == 1 and "A" <= t <= "Z":
        return ord(t)
    if len(t) == 1 and "0" <= t <= "9":
        return ord(t)
    raise ValueError(f"Unsupported key token: {tok!r}")


def parse_hotkey(spec: str) -> tuple[int, int]:
    parts = [p.strip().upper() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("Empty hotkey")
    mods = 0
    key_tok = parts[-1]
    for p in parts[:-1]:
        if p in ("CTRL", "CONTROL"):
            mods |= (MOD_CONTROL if IS_WINDOWS else 0)
        elif p == "ALT":
            mods |= (MOD_ALT if IS_WINDOWS else 0)
        elif p == "SHIFT":
            mods |= (MOD_SHIFT if IS_WINDOWS else 0)
        elif p in ("WIN", "WINDOWS"):
            mods |= (MOD_WIN if IS_WINDOWS else 0)
        else:
            raise ValueError(f"Unsupported modifier: {p!r}")
    vk = _vk_from_token(key_tok)
    return mods, vk


def run_ui(
    initial_seconds: float,
    toggle_hk: str,
    calibrate_hk: str,
    initial_mode: str,
    num_targets: int = 1,
    detect_threshold: float = 0.80,
    detect_word: str = "continue",
) -> None:
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    cfg = load_config(num_targets)
    cfg["interval_seconds"] = float(initial_seconds)
    cfg["mode"] = initial_mode
    cfg["state_word"] = detect_word
    cfg["state_threshold_ui"] = detect_threshold
    cfg["state_detect_enabled"] = bool(cfg.get("state_detect_enabled", True))
    save_config(cfg)

    stop_event = threading.Event()
    running_event = threading.Event()
    interrupt_event = threading.Event()
    run_cooldowns: dict[int, float] = {}
    finished_tpl_cache: dict[str, object] = {}

    root = tk.Tk()
    root.title("Auto Clicker")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    root.resizable(False, False)

    FONT = ("Segoe UI", 11)
    FONT_SMALL = ("Segoe UI", 10)
    BTN_PADX = 16
    BTN_PADY = 8
    ACTIVE_BG = "#2a2a2a"

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Dark.TCombobox", fieldbackground=ENTRY_BG, background=BTN_BG, foreground=FG, arrowcolor=FG)
    style.map("Dark.TCombobox", fieldbackground=[("readonly", ENTRY_BG)], selectbackground=[("readonly", ENTRY_BG)], selectforeground=[("readonly", FG)])
    frm = tk.Frame(root, padx=16, pady=16, bg=BG)
    frm.pack()
    content_frm = tk.Frame(frm, bg=BG)
    content_frm.pack()

    status_canvas = tk.Canvas(content_frm, width=LIGHT_SIZE, height=LIGHT_SIZE, highlightthickness=0, bg=BG)
    status_canvas.grid(row=1, column=5, rowspan=2, sticky="w")

    def set_status(running: bool) -> None:
        status_canvas.delete("all")
        color = "#00c853" if running else "#d50000"
        status_canvas.create_oval(LIGHT_PAD, LIGHT_PAD, LIGHT_PAD + DOT_DIAM, LIGHT_PAD + DOT_DIAM, fill=color, outline=color)

    set_status(False)

    tk.Label(content_frm, text="Mode:", bg=BG, fg=MUTED, font=FONT_SMALL).grid(row=0, column=1, sticky="w")
    mode_var = tk.StringVar(value=MODE_LABELS.get(cfg["mode"], MODE_LABELS[MODE_CLICK_ENTER]))
    mode_combo = ttk.Combobox(content_frm, textvariable=mode_var, values=[MODE_LABELS[m] for m in MODES], state="readonly", width=12, font=FONT_SMALL, style="Dark.TCombobox")
    mode_combo.grid(row=0, column=2, columnspan=2, sticky="w", padx=(8, 0))

    tk.Label(content_frm, text="Setup Target:", bg=BG, fg=MUTED, font=FONT_SMALL).grid(row=0, column=4, sticky="w", padx=(12, 0))
    setup_target_var = tk.IntVar(value=1)
    setup_target_combo = ttk.Combobox(content_frm, values=[f"T{i+1}" for i in range(num_targets)], state="readonly", width=5, font=FONT_SMALL, style="Dark.TCombobox")
    setup_target_combo.set("T1")
    setup_target_combo.grid(row=0, column=5, sticky="w", padx=(8, 0))

    target_lbl = tk.Label(content_frm, text="", bg=BG, fg=FG, font=FONT, justify="left", anchor="w")
    target_lbl.grid(row=1, column=1, columnspan=3, sticky="w", pady=(4, 0))

    tk.Label(content_frm, text="Interval (s):", bg=BG, fg=MUTED, font=FONT).grid(row=2, column=1, sticky="w", pady=(8, 0))
    interval_var = tk.StringVar(value=str(cfg["interval_seconds"]))
    tk.Entry(content_frm, textvariable=interval_var, width=6, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=FG, font=FONT, relief="flat", justify="center").grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(8, 0))

    show_logs_var = tk.BooleanVar(value=True)
    show_logs_check = tk.Checkbutton(
        content_frm,
        text="Show Logs",
        variable=show_logs_var,
        bg=BG,
        fg=MUTED,
        selectcolor=ENTRY_BG,
        activebackground=BG,
        activeforeground=FG,
        font=FONT,
        relief="flat",
        bd=0,
        padx=6,
    )
    show_logs_check.grid(row=2, column=3, sticky="w", padx=(12, 0), pady=(8, 0))

    timer_lbl = tk.Label(content_frm, text="", bg=BG, fg=MUTED, font=FONT_SMALL, width=6)
    timer_lbl.grid(row=2, column=4, sticky="w", padx=(4, 0), pady=(8, 0))

    state_detect_var = tk.BooleanVar(value=bool(cfg.get("state_detect_enabled", True)))
    state_detect_check = tk.Checkbutton(
        content_frm,
        text="State Detection",
        variable=state_detect_var,
        bg=BG,
        fg=MUTED,
        selectcolor=ENTRY_BG,
        activebackground=BG,
        activeforeground=FG,
        font=FONT,
        relief="flat",
        bd=0,
        padx=6,
    )
    state_detect_check.grid(row=3, column=1, sticky="w", pady=(8, 0))
    state_opts_frame = tk.Frame(content_frm, bg=BG)
    state_opts_frame.grid(row=4, column=1, columnspan=5, sticky="w", pady=(2, 0))
    tk.Label(state_opts_frame, text="Word:", bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left", padx=(0, 4))
    state_word_var = tk.StringVar(value=str(cfg.get("state_word", "continue")))
    tk.Entry(state_opts_frame, textvariable=state_word_var, width=10, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=FG, font=FONT_SMALL, relief="flat", justify="left").pack(side="left", padx=(0, 12))
    tk.Label(state_opts_frame, text="Threshold:", bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left", padx=(0, 4))
    state_threshold_var = tk.StringVar(value=f"{float(cfg.get('state_threshold_ui', detect_threshold)):.2f}")
    tk.Entry(state_opts_frame, textvariable=state_threshold_var, width=6, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=FG, font=FONT_SMALL, relief="flat", justify="center").pack(side="left")

    log_frame = tk.Frame(frm, bg=BG)
    log_frame.pack(side="bottom", pady=(10, 0), fill="x")
    log_box = ScrolledText(log_frame, height=6, width=56, bg=ENTRY_BG, fg=FG, insertbackground=FG, font=("Consolas", 9), relief="flat", wrap="word")
    log_box.pack(fill="x")
    log_box.config(state="disabled")

    def log_event(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"

        def append_line():
            log_box.config(state="normal")
            log_box.insert("end", line)
            if int(log_box.index("end-1c").split(".")[0]) > 350:
                log_box.delete("1.0", "120.0")
            log_box.see("end")
            log_box.config(state="disabled")

        root.after(0, append_line)

    def update_log_visibility():
        if show_logs_var.get():
            if not log_frame.winfo_ismapped():
                log_frame.pack(side="bottom", pady=(10, 0), fill="x")
        else:
            if log_frame.winfo_ismapped():
                log_frame.pack_forget()

    show_logs_check.configure(command=update_log_visibility)
    update_log_visibility()

    def setup_target_idx() -> int:
        return max(0, min(num_targets - 1, setup_target_var.get() - 1))

    def get_mode_key() -> str:
        lbl = mode_var.get()
        for key, val in MODE_LABELS.items():
            if val == lbl:
                return key
        return MODE_CLICK_ENTER

    def parse_float_clamped(raw: str, default: float, min_value: float, max_value: float) -> float:
        try:
            return max(min_value, min(max_value, float(raw)))
        except ValueError:
            return default

    def get_seconds() -> float:
        return parse_float_clamped(interval_var.get(), 10.0, 0.01, 10_000.0)

    def get_state_threshold_ui() -> float:
        return parse_float_clamped(state_threshold_var.get(), detect_threshold, 0.0, 1.0)

    def target_marker(i: int) -> str:
        t = cfg["targets"][i]
        click_ok = "C*" if t.get("click_target") else "C-"
        state_ok = "S*" if (t.get("state_roi") and t.get("state_template")) else "S-"
        run_ok = "R*" if t.get("run_roi") else "R-"
        return f"{click_ok}/{state_ok}/{run_ok}"

    def refresh_target_text():
        if num_targets == 1:
            t = cfg["targets"][0]
            click = t.get("click_target")
            click_text = "not set" if not click else f"x={click[0]}, y={click[1]}"
            target_lbl.config(text=f"Target: {click_text} [{target_marker(0)}]")
            return
        lines = []
        for i in range(num_targets):
            click = cfg["targets"][i].get("click_target")
            ctext = "-" if not click else f"({click[0]},{click[1]})"
            lines.append(f"T{i+1}: {ctext} [{target_marker(i)}]")
        target_lbl.config(text="\n".join(lines))

    def persist_ui_state():
        cfg["mode"] = get_mode_key()
        cfg["interval_seconds"] = get_seconds()
        cfg["state_detect_enabled"] = bool(state_detect_var.get())
        cfg["state_word"] = (state_word_var.get().strip() or "continue")
        cfg["state_threshold_ui"] = get_state_threshold_ui()
        save_config(cfg)

    def capture_drag_bbox():
        result = {"bbox": None}
        overlay = tk.Toplevel(root)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-topmost", True)
        overlay.attributes("-alpha", 0.22)
        overlay.configure(bg="black")
        overlay.config(cursor="crosshair")
        canvas = tk.Canvas(overlay, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        drag = {"start_root": None, "rect": None}

        def on_press(event):
            drag["start_root"] = (event.x_root, event.y_root)
            if drag["rect"] is not None:
                canvas.delete(drag["rect"])
            drag["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#00c853", width=2)

        def on_motion(event):
            if drag["start_root"] is None or drag["rect"] is None:
                return
            x0, y0 = drag["start_root"]
            canvas.coords(drag["rect"], x0 - overlay.winfo_rootx(), y0 - overlay.winfo_rooty(), event.x_root - overlay.winfo_rootx(), event.y_root - overlay.winfo_rooty())

        def on_release(event):
            if drag["start_root"] is None:
                overlay.destroy()
                return
            x0, y0 = drag["start_root"]
            x1, y1 = event.x_root, event.y_root
            left, right = sorted((x0, x1))
            top, bottom = sorted((y0, y1))
            width = right - left
            height = bottom - top
            if width >= 5 and height >= 5:
                result["bbox"] = [left, top, width, height]
            overlay.destroy()

        overlay.bind("<Escape>", lambda _e: overlay.destroy())
        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_motion)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.wait_window(overlay)
        return result["bbox"]

    actions_frame = tk.Frame(frm, bg=BG)
    actions_frame.pack(anchor="w", pady=(12, 0), fill="x")
    actions_title = tk.Label(actions_frame, text="Setup Steps", bg=BG, fg=MUTED, font=FONT_SMALL)
    actions_title.pack(anchor="w", pady=(0, 6))
    action_items_frame = tk.Frame(actions_frame, bg=BG)
    action_items_frame.pack(anchor="w")

    def make_button(parent, text, command):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=BTN_BG,
            fg=BTN_FG,
            activebackground=ACTIVE_BG,
            activeforeground=BTN_FG,
            bd=0,
            highlightthickness=0,
            font=FONT,
            padx=BTN_PADX,
            pady=BTN_PADY,
            cursor="hand2",
        )

    def add_action_button(text: str, command, help_text: str):
        row = tk.Frame(action_items_frame, bg=BG)
        btn = make_button(row, text, command)
        btn.pack(side="left")
        tk.Button(
            row,
            text="?",
            width=2,
            command=lambda: log_event(f"[help] {help_text}"),
            bg=ENTRY_BG,
            fg=MUTED,
            activebackground=ACTIVE_BG,
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=FONT_SMALL,
            cursor="hand2",
        ).pack(side="left", padx=(6, 0))
        return row, btn

    def toggle_running():
        running = not bool(cfg.get("_running", False))
        cfg["_running"] = running
        if running:
            running_event.set()
            interrupt_event.clear()
            set_status(True)
            log_event("[control] start")
        else:
            running_event.clear()
            interrupt_event.set()
            set_status(False)
            log_event("[control] stop")

    top_buttons = tk.Frame(frm, bg=BG)
    top_buttons.pack(anchor="w", pady=(12, 0))
    btn_toggle = make_button(top_buttons, f"Start/Stop ({toggle_hk})", toggle_running)
    btn_toggle.pack(side="left", padx=(0, 8))

    def ui_calibrate():
        idx = setup_target_idx()
        mode = get_mode_key()
        if mode == MODE_WATCH_RUN:
            bbox = capture_drag_bbox()
            if not bbox:
                log_event("[setup] run ROI capture cancelled")
                return
            cfg["targets"][idx]["run_roi"] = bbox
            log_event(f"[setup] T{idx+1} run ROI set: {tuple(bbox)}")
        else:
            pt = pyautogui.position()
            cfg["targets"][idx]["click_target"] = [pt.x, pt.y]
            log_event(f"[setup] T{idx+1} click target set: ({pt.x}, {pt.y})")
        refresh_target_text()
        persist_ui_state()

    btn_cal = make_button(top_buttons, f"Calibrate ({calibrate_hk})", ui_calibrate)
    btn_cal.pack(side="left")

    def ui_drag_capture_state():
        idx = setup_target_idx()
        bbox = capture_drag_bbox()
        if not bbox:
            log_event("[setup] state capture cancelled")
            return
        cfg["targets"][idx]["state_roi"] = bbox
        gray = grab_region_gray(tuple(bbox))
        rel = f"state_t{idx+1}.png"
        save_gray_image(str(template_path(rel)), gray)
        cfg["targets"][idx]["state_template"] = str(template_path(rel))
        log_event(f"[setup] T{idx+1} state template captured")
        refresh_target_text()
        persist_ui_state()

    row_state_capture, _ = add_action_button("1) Drag Capture State", ui_drag_capture_state, "Drag over the finished-state area to save the template for this target.")

    def ui_capture_run_template():
        bbox = capture_drag_bbox()
        if not bbox:
            log_event("[setup] run-template capture cancelled")
            return
        gray = grab_region_gray(tuple(bbox))
        rel = f"run_template_{int(time.time())}.png"
        save_gray_image(str(template_path(rel)), gray)
        cfg["run_templates"].append(rel)
        log_event(f"[setup] run template added: {rel}")
        persist_ui_state()

    row_run_tpl, _ = add_action_button("1) Capture Run Template", ui_capture_run_template, "Capture one example of the blue Run button (global template).")

    def ui_test_run():
        idx = setup_target_idx()
        rt = load_run_templates(cfg.get("run_templates", []), TEMPLATES_DIR)
        _, score, center, reason = evaluate_run_for_target(cfg["targets"][idx], rt, float(cfg.get("run_threshold", 0.85)), log_event)
        s = "-" if score is None else f"{score:.3f}"
        log_event(f"[test-run] T{idx+1} result={reason} score={s} center={center}")

    row_test_run, _ = add_action_button("2) Test Run", ui_test_run, "Run one detection pass for Run button in selected target ROI.")

    def ui_test_capture():
        idx = setup_target_idx()
        _, score, reason = evaluate_state_for_target(
            cfg["targets"][idx],
            bool(state_detect_var.get()),
            detect_threshold,  # runtime fixed
            finished_tpl_cache,
            log_event,
        )
        s = "-" if score is None else f"{score:.3f}"
        log_event(f"[test-state] T{idx+1} result={reason} score={s}")

    row_test_state, _ = add_action_button("2) Test Capture", ui_test_capture, "Run one state-detection check and log score only (no action).")

    def ui_test_word():
        idx = setup_target_idx()
        ct = cfg["targets"][idx].get("click_target")
        if not ct:
            log_event(f"[test] T{idx+1}: set click target first")
            return
        old = pyautogui.position()
        try:
            click_point((ct[0], ct[1]))
            type_word_with_retry(state_word_var.get().strip() or "continue")
            log_event(f"[test] T{idx+1}: word typed")
        except Exception as e:
            log_event(f"[test] T{idx+1}: word typing failed: {e}")
        finally:
            pyautogui.moveTo(old.x, old.y, duration=0)

    row_test_word, _ = add_action_button("3) Test Word", ui_test_word, "Click selected target and type the configured word (no Enter).")

    def set_visible(widget, visible: bool):
        if visible:
            if not widget.winfo_ismapped():
                widget.pack(anchor="w", pady=(0, 6))
        elif widget.winfo_ismapped():
            widget.pack_forget()

    def update_controls_visibility():
        mode = get_mode_key()
        state_on = bool(state_detect_var.get())
        needs_target = mode in (MODE_CLICK, MODE_CLICK_ENTER, MODE_WATCH_RUN)
        is_watch = mode == MODE_WATCH_RUN
        is_click_enter = mode == MODE_CLICK_ENTER

        if needs_target:
            if not target_lbl.winfo_ismapped():
                target_lbl.grid(row=1, column=1, columnspan=3, sticky="w", pady=(4, 0))
            setup_target_combo.configure(state="readonly")
            if not btn_cal.winfo_ismapped():
                btn_cal.pack(side="left")
        else:
            target_lbl.grid_remove()
            setup_target_combo.configure(state="disabled")
            if btn_cal.winfo_ismapped():
                btn_cal.pack_forget()

        set_visible(state_opts_frame, state_on)
        set_visible(actions_frame, needs_target)

        # Mode-specific action rows
        set_visible(row_run_tpl, is_watch)
        set_visible(row_test_run, is_watch)
        set_visible(row_state_capture, is_click_enter and state_on)
        set_visible(row_test_state, is_click_enter and state_on)
        set_visible(row_test_word, is_click_enter and state_on)

    def worker_loop():
        while True:
            running_event.wait()
            if stop_event.is_set():
                break

            mode = get_mode_key()
            cfg["mode"] = mode
            cfg["state_word"] = state_word_var.get().strip() or "continue"
            cfg["state_detect_enabled"] = bool(state_detect_var.get())
            cfg["state_threshold_ui"] = get_state_threshold_ui()
            cfg["interval_seconds"] = get_seconds()
            save_config(cfg)

            run_templates = load_run_templates(cfg.get("run_templates", []), TEMPLATES_DIR)
            interval = get_seconds()
            per_target_interval = interval / max(1, num_targets)
            for i in range(num_targets):
                if stop_event.is_set() or not running_event.is_set():
                    break
                tcfg = cfg["targets"][i]

                result = "off"
                score = None
                inject_text = None

                # 1) Run watch happens first. A run match short-circuits this target tick.
                if mode == MODE_WATCH_RUN or tcfg.get("run_roi"):
                    hit_run, run_score, center, run_reason = evaluate_run_for_target(
                        tcfg,
                        run_templates,
                        float(cfg.get("run_threshold", 0.85)),
                        log_event,
                    )
                    if hit_run and center is not None:
                        now = time.time()
                        last = run_cooldowns.get(i, 0.0)
                        if now - last >= float(cfg.get("run_cooldown_seconds", 1.5)):
                            click_point(center)
                            run_cooldowns[i] = now
                            score_text = "-" if run_score is None else f"{run_score:.3f}"
                            log_event(f"[tick] T{i+1} result=run-clicked score={score_text}")
                            if interrupt_event.wait(timeout=per_target_interval):
                                break
                            continue
                        else:
                            result = "run-cooldown"
                            score = run_score
                    else:
                        result = f"run-{run_reason}"
                        score = run_score

                # 2) Only if run-watch did not act, evaluate state fallback.
                hit_state, state_score, state_reason = evaluate_state_for_target(
                    tcfg,
                    bool(state_detect_var.get()),
                    detect_threshold,  # fixed runtime threshold
                    finished_tpl_cache,
                    log_event,
                )
                if hit_state and mode == MODE_CLICK_ENTER:
                    inject_text = state_word_var.get().strip() or "continue"

                if state_reason not in ("off", "not-configured"):
                    result = state_reason
                    score = state_score

                if mode in (MODE_CLICK, MODE_CLICK_ENTER):
                    ct = tcfg.get("click_target")
                    if ct:
                        do_action(mode, (ct[0], ct[1]), inject_text)
                        state_text = "-" if score is None else f"{score:.3f}"
                        log_event(f"[tick] T{i+1} result={result} score={state_text}")
                    else:
                        log_event(f"[tick] T{i+1} result=not-configured score=-")
                elif mode == MODE_ENTER:
                    do_action(MODE_ENTER)
                    log_event("[tick] enter mode")
                elif mode == MODE_WATCH_RUN:
                    state_text = "-" if score is None else f"{score:.3f}"
                    log_event(f"[tick] T{i+1} result={result} score={state_text}")

                cfg["last_action_time"] = time.perf_counter()
                if interrupt_event.wait(timeout=per_target_interval):
                    break

    def on_mode_change(_event=None):
        refresh_target_text()
        persist_ui_state()
        update_controls_visibility()

    mode_combo.bind("<<ComboboxSelected>>", on_mode_change)
    def on_target_change(_event=None):
        value = setup_target_combo.get().strip()
        if value.startswith("T"):
            try:
                setup_target_var.set(int(value[1:]))
            except ValueError:
                setup_target_var.set(1)
        refresh_target_text()

    setup_target_combo.bind("<<ComboboxSelected>>", on_target_change)
    def on_state_toggle():
        persist_ui_state()
        update_controls_visibility()

    state_detect_check.configure(command=on_state_toggle)

    worker = threading.Thread(target=worker_loop, daemon=True)
    worker.start()

    # Hotkey callbacks
    hotkey_thread_stop = threading.Event()
    hotkey_thread_id = {"tid": None}
    hotkey_ok = {"ok": True, "err": ""}

    def start_hotkeys():
        if not IS_WINDOWS:
            hotkey_ok["ok"] = False
            hotkey_ok["err"] = "Hotkeys only supported on Windows (UI buttons still work)."
            return

        def hotkey_loop():
            tid = GetCurrentThreadId()
            hotkey_thread_id["tid"] = tid
            try:
                t_mod, t_vk = parse_hotkey(toggle_hk)
                c_mod, c_vk = parse_hotkey(calibrate_hk)
            except ValueError as e:
                hotkey_ok["ok"] = False
                hotkey_ok["err"] = str(e)
                return
            ID_TOGGLE = 1
            ID_CALIB = 2

            if not RegisterHotKey(None, ID_TOGGLE, t_mod | MOD_NOREPEAT, t_vk):
                hotkey_ok["ok"] = False
                hotkey_ok["err"] = f"Failed to register toggle hotkey {toggle_hk}."
                return
            if not RegisterHotKey(None, ID_CALIB, c_mod | MOD_NOREPEAT, c_vk):
                UnregisterHotKey(None, ID_TOGGLE)
                hotkey_ok["ok"] = False
                hotkey_ok["err"] = f"Failed to register calibrate hotkey {calibrate_hk}."
                return

            msg = MSG()
            while not hotkey_thread_stop.is_set():
                ok = GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ok <= 0:
                    break
                if msg.message == WM_HOTKEY:
                    if msg.wParam == ID_TOGGLE:
                        root.after(0, toggle_running)
                    elif msg.wParam == ID_CALIB:
                        root.after(0, ui_calibrate)
                TranslateMessage(ctypes.byref(msg))
                DispatchMessageW(ctypes.byref(msg))
            UnregisterHotKey(None, ID_TOGGLE)
            UnregisterHotKey(None, ID_CALIB)

        threading.Thread(target=hotkey_loop, daemon=True).start()

    def stop_hotkeys():
        if not IS_WINDOWS:
            return
        hotkey_thread_stop.set()
        tid = hotkey_thread_id["tid"]
        if tid:
            PostThreadMessageW(tid, WM_QUIT, 0, 0)

    start_hotkeys()
    if not hotkey_ok["ok"]:
        log_event(f"[error] {hotkey_ok['err']}")

    def update_timer():
        if stop_event.is_set():
            return
        lat = cfg.get("last_action_time", 0.0)
        if cfg.get("_running") and lat > 0:
            interval = get_seconds()
            elapsed = time.perf_counter() - lat
            remaining = max(0.0, interval - elapsed)
            timer_lbl.config(text=f"{remaining:.1f}s")
        else:
            timer_lbl.config(text="")
        root.after(100, update_timer)

    update_timer()
    refresh_target_text()
    update_controls_visibility()
    log_event(f"[ready] loaded {CONFIG_PATH}")

    def on_close():
        persist_ui_state()
        stop_event.set()
        interrupt_event.set()
        running_event.set()
        stop_hotkeys()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
