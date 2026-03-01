# press_enter.py
# Simplified version: mouse click + optional key press, no window switching logic
import argparse
import sys
import time
import threading

# Mode constants
MODE_ENTER = "enter"
MODE_CLICK = "click"
MODE_CLICK_ENTER = "click+enter"
MODES = [MODE_ENTER, MODE_CLICK, MODE_CLICK_ENTER]
MODE_LABELS = {
    MODE_ENTER: "Enter Only",
    MODE_CLICK: "Click Only",
    MODE_CLICK_ENTER: "Click + Enter",
}

try:
    import pyautogui
except ImportError:
    raise SystemExit(
        "pyautogui is required. Install with:\n\n"
        "    uv run --with pyautogui python press_enter_v3.py\n"
        "or:\n"
        "    pip install pyautogui"
    )

IS_WINDOWS = sys.platform.startswith("win")


# Dark mode colors
BG = "#121212"
FG = "#E6E6E6"
BTN_BG = "#1E1E1E"
BTN_FG = FG
ENTRY_BG = "#1A1A1A"
ENTRY_FG = FG
MUTED = "#A8A8A8"

# Status light dimensions
LIGHT_SIZE = 62
LIGHT_PAD = 8
DOT_DIAM = LIGHT_SIZE - 2 * LIGHT_PAD

# State-detection defaults
DETECT_THRESHOLD_DEFAULT = 0.80
DETECT_WORD_DEFAULT = "continue"


# -------------------------
# Windows hotkeys (ctypes)
# -------------------------
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
        return 0x22  # VK_NEXT (Page Down)
    if t in ("PAGEUP", "PGUP", "PRIOR"):
        return 0x21  # VK_PRIOR (Page Up)
    if t in ("END",):
        return 0x23  # VK_END
    if t in ("HOME",):
        return 0x24  # VK_HOME

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
    """
    Spec examples:
      "PAGEDOWN"
      "CTRL+ALT+P"
      "SHIFT+F9"
    """
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


def try_import_vision():
    """Lazy import for optional vision dependencies."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        return cv2, np, None
    except ImportError as e:
        return None, None, str(e)


def parse_bbox(spec: str) -> tuple[int, int, int, int]:
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be 'left,top,width,height'")
    left, top, width, height = map(int, parts)
    if width <= 0 or height <= 0:
        raise ValueError("bbox width and height must be > 0")
    return left, top, width, height


def load_template_gray(path: str):
    cv2, _, err = try_import_vision()
    if err:
        raise RuntimeError(
            "State detection needs optional deps: pip install \"auto-press[vision]\""
        )
    tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if tpl is None:
        raise FileNotFoundError(f"Template unreadable: {path}")
    return tpl


def grab_region_gray(bbox: tuple[int, int, int, int]):
    cv2, np, err = try_import_vision()
    if err:
        raise RuntimeError(
            "State detection needs optional deps: pip install \"auto-press[vision]\""
        )
    left, top, width, height = bbox
    img = pyautogui.screenshot(region=(left, top, width, height))
    arr = np.array(img)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)


def match_template_score(region_gray, template_gray) -> float:
    """Return max normalized template-match score."""
    cv2, _, err = try_import_vision()
    if err:
        return 0.0
    return float(
        cv2.minMaxLoc(
            cv2.matchTemplate(region_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        )[1]
    )


# -------------------------
# Core action
# -------------------------
def do_action(mode: str, x: int | None = None, y: int | None = None, text_before_enter: str | None = None) -> None:
    """Perform action based on mode."""
    if mode == MODE_ENTER:
        # Enter only - no mouse movement
        pyautogui.press("enter")
    elif mode == MODE_CLICK:
        # Click only - move, click, return
        old = pyautogui.position()
        pyautogui.moveTo(x, y, duration=0)
        pyautogui.click()
        pyautogui.moveTo(old.x, old.y, duration=0)
    elif mode == MODE_CLICK_ENTER:
        # Click + Enter
        old = pyautogui.position()
        pyautogui.moveTo(x, y, duration=0)
        pyautogui.click()
        if text_before_enter:
            # Retry text send once if the first attempt fails (focus/timing hiccup).
            sent = False
            last_err = None
            for _ in range(2):
                try:
                    time.sleep(0.05)
                    pyautogui.typewrite(text_before_enter)
                    sent = True
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.08)
            if not sent and last_err is not None:
                raise last_err
        pyautogui.press("enter")
        pyautogui.moveTo(old.x, old.y, duration=0)


# -------------------------
# Calibration (hover)
# -------------------------
def calibrate_point_hover_console() -> tuple[int, int]:
    print("\nCalibration (hover):")
    print("Hover your mouse over the exact spot to click.")
    input("Press Enter in this console to capture the current mouse position...")
    pt = pyautogui.position()
    print(f"Captured target: x={pt.x}, y={pt.y}\n")
    return pt.x, pt.y


# -------------------------
# UI mode (default)
# -------------------------
def run_ui(
    initial_seconds: float,
    toggle_hk: str,
    calibrate_hk: str,
    initial_mode: str,
    num_targets: int = 1,
    detect_threshold: float = DETECT_THRESHOLD_DEFAULT,
    detect_word: str = DETECT_WORD_DEFAULT,
) -> None:
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    state = {
        "running": False,
        "targets": [None] * num_targets,  # List of (x, y) tuples
        "regions": [None] * num_targets,  # List of (left, top, width, height)
        "tpl_finished": [None] * num_targets,  # grayscale template arrays
        "last_action_time": 0.0,  # time.perf_counter() of last action (for timer)
        "mode": initial_mode,  # Current mode
    }

    stop_event = threading.Event()
    running_event = threading.Event()
    interrupt_event = threading.Event()  # Set when we need to wake from interval sleep

    def set_status(canvas: tk.Canvas, running: bool) -> None:
        canvas.delete("all")
        color = "#00c853" if running else "#d50000"
        canvas.create_oval(
            LIGHT_PAD,
            LIGHT_PAD,
            LIGHT_PAD + DOT_DIAM,
            LIGHT_PAD + DOT_DIAM,
            fill=color,
            outline=color,
        )

    def mode_needs_target(mode: str) -> bool:
        return mode in (MODE_CLICK, MODE_CLICK_ENTER)

    def setup_target_idx() -> int:
        return max(0, min(num_targets - 1, setup_target_var.get() - 1))

    def toggle_running(canvas: tk.Canvas) -> None:
        state["running"] = not state["running"]
        if state["running"]:
            interrupt_event.clear()  # Reset interrupt for new run
            running_event.set()
        else:
            running_event.clear()
            interrupt_event.set()  # Wake from interval sleep immediately
        set_status(canvas, state["running"])

    def get_target_text() -> str:
        targets = state["targets"]
        regions = state["regions"]
        finished_tpl = state["tpl_finished"]

        def target_marker(i: int) -> str:
            click_ok = "C*" if targets[i] is not None else "C-"
            region_ok = "R*" if regions[i] is not None else "R-"
            tpl_ok = "F*" if finished_tpl[i] is not None else "F-"
            return f"{click_ok}/{region_ok}/{tpl_ok}"

        if num_targets == 1:
            click_text = "not set" if targets[0] is None else f"x={targets[0][0]}, y={targets[0][1]}"
            return f"Target: {click_text}  [{target_marker(0)}]"

        parts = []
        for i, t in enumerate(targets):
            if t is None:
                parts.append(f"T{i+1}: - [{target_marker(i)}]")
            else:
                parts.append(f"T{i+1}: ({t[0]},{t[1]}) [{target_marker(i)}]")
        return "\n".join(parts)

    def set_label_target(label: tk.Label) -> None:
        label.config(text=get_target_text())

    def all_targets_set() -> bool:
        return all(t is not None for t in state["targets"])

    def target_has_state_data(target_idx: int) -> bool:
        return (
            state["regions"][target_idx] is not None
            and state["tpl_finished"][target_idx] is not None
        )

    def worker_loop(get_seconds, get_state_enabled, get_state_word, log_event) -> None:
        while True:
            # Block until running - zero CPU when idle
            running_event.wait()

            if stop_event.is_set():
                break

            current_mode = state["mode"]

            # For enter-only mode, no target needed
            if mode_needs_target(current_mode) and not all_targets_set():
                time.sleep(0.1)
                continue

            targets = state["targets"]
            interval = max(0.01, float(get_seconds()))

            if mode_needs_target(current_mode):
                sub_interval = interval / num_targets
                for i, target in enumerate(targets):
                    if stop_event.is_set() or not running_event.is_set():
                        break
                    if target is None:
                        continue
                    x, y = target
                    try:
                        inject_text = None
                        # Keep runtime decision threshold fixed; UI threshold box is test-only.
                        state_threshold = detect_threshold
                        detection_enabled = get_state_enabled()
                        if detection_enabled and target_has_state_data(i):
                            try:
                                region_gray = grab_region_gray(state["regions"][i])
                                fin_score = match_template_score(
                                    region_gray,
                                    state["tpl_finished"][i],
                                )
                                if fin_score >= state_threshold:
                                    if current_mode == MODE_CLICK_ENTER:
                                        inject_text = get_state_word()
                            except Exception as e:
                                log_event(f"[error] T{i+1} detection failed: {e}")

                        do_action(current_mode, x, y, text_before_enter=inject_text)
                        state["last_action_time"] = time.perf_counter()
                    except Exception as e:
                        log_event(f"[error] T{i+1} action failed: {e}")
                        continue

                    if interrupt_event.wait(timeout=sub_interval):
                        break
            else:
                # Enter-only mode - no targets
                try:
                    do_action(current_mode)
                    state["last_action_time"] = time.perf_counter()
                except Exception as e:
                    log_event(f"[error] Action failed: {e}")

                if interrupt_event.wait(timeout=interval):
                    continue

    # Hotkey thread (Windows only)
    hotkey_thread_stop = threading.Event()
    hotkey_thread_id = {"tid": None}
    hotkey_ok = {"ok": True, "err": ""}

    def start_hotkeys(on_toggle, on_calibrate):
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

            def reg(hkid, mods, vk) -> bool:
                return bool(RegisterHotKey(None, hkid, mods | MOD_NOREPEAT, vk))

            if not reg(ID_TOGGLE, t_mod, t_vk):
                hotkey_ok["ok"] = False
                hotkey_ok["err"] = f"Failed to register toggle hotkey {toggle_hk} (collision likely)."
                return
            if not reg(ID_CALIB, c_mod, c_vk):
                UnregisterHotKey(None, ID_TOGGLE)
                hotkey_ok["ok"] = False
                hotkey_ok["err"] = f"Failed to register calibrate hotkey {calibrate_hk} (collision likely)."
                return

            msg = MSG()
            while not hotkey_thread_stop.is_set():
                ok = GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ok == 0:  # WM_QUIT
                    break
                if ok == -1:
                    break
                if msg.message == WM_HOTKEY:
                    if msg.wParam == ID_TOGGLE:
                        on_toggle()
                    elif msg.wParam == ID_CALIB:
                        on_calibrate()
                TranslateMessage(ctypes.byref(msg))
                DispatchMessageW(ctypes.byref(msg))

            UnregisterHotKey(None, ID_TOGGLE)
            UnregisterHotKey(None, ID_CALIB)

        t = threading.Thread(target=hotkey_loop, daemon=True)
        t.start()

    def stop_hotkeys():
        if not IS_WINDOWS:
            return
        hotkey_thread_stop.set()
        tid = hotkey_thread_id["tid"]
        if tid:
            PostThreadMessageW(tid, WM_QUIT, 0, 0)

    # Tk UI
    root = tk.Tk()
    root.title("Auto Clicker")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    root.resizable(False, False)

    FONT = ("Segoe UI", 11)
    FONT_SMALL = ("Segoe UI", 10)
    BTN_PADX = 16
    BTN_PADY = 8

    # Style for ttk widgets (dark mode)
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Dark.TCombobox",
                    fieldbackground=ENTRY_BG,
                    background=BTN_BG,
                    foreground=FG,
                    arrowcolor=FG)
    style.map("Dark.TCombobox",
              fieldbackground=[("readonly", ENTRY_BG)],
              selectbackground=[("readonly", ENTRY_BG)],
              selectforeground=[("readonly", FG)])
    style.configure("Dark.TCheckbutton",
                    background=BG,
                    foreground=MUTED,
                    font=FONT_SMALL)
    style.map("Dark.TCheckbutton",
              background=[("active", BG)])

    frm = tk.Frame(root, padx=16, pady=16, bg=BG)
    frm.pack()

    # Main content frame using grid for alignment
    content_frm = tk.Frame(frm, bg=BG)
    content_frm.pack()

    # Status light (shifted right/down near threshold row)
    status_canvas = tk.Canvas(
        content_frm,
        width=LIGHT_SIZE,
        height=LIGHT_SIZE,
        highlightthickness=0,
        bg=BG,
    )
    status_canvas.grid(row=1, column=5, rowspan=2, sticky="w", padx=(0, 0), pady=(0, 0))
    set_status(status_canvas, False)

    # Row 0: Mode dropdown
    tk.Label(content_frm, text="Mode:", bg=BG, fg=MUTED, font=FONT_SMALL).grid(row=0, column=1, sticky="w")

    mode_var = tk.StringVar(value=MODE_LABELS[initial_mode])
    mode_combo = ttk.Combobox(
        content_frm,
        textvariable=mode_var,
        values=[MODE_LABELS[m] for m in MODES],
        state="readonly",
        width=12,
        font=FONT_SMALL,
        style="Dark.TCombobox",
    )
    mode_combo.grid(row=0, column=2, columnspan=2, sticky="w", padx=(8, 0))

    tk.Label(content_frm, text="Setup Target:", bg=BG, fg=MUTED, font=FONT_SMALL).grid(row=0, column=4, sticky="w", padx=(12, 0))
    setup_target_var = tk.IntVar(value=1)
    setup_target_combo = ttk.Combobox(
        content_frm,
        values=[f"T{i+1}" for i in range(num_targets)],
        state="readonly",
        width=5,
        font=FONT_SMALL,
        style="Dark.TCombobox",
    )
    setup_target_combo.set("T1")
    setup_target_combo.grid(row=0, column=5, sticky="w", padx=(8, 0))

    def on_setup_target_change(event=None):
        val = setup_target_combo.get().strip().upper().replace("T", "")
        if val.isdigit():
            setup_target_var.set(max(1, min(num_targets, int(val))))
        set_label_target(target_lbl)

    setup_target_combo.bind("<<ComboboxSelected>>", on_setup_target_change)

    def on_mode_change(event=None):
        # Find mode key from label
        label = mode_var.get()
        for key, lbl in MODE_LABELS.items():
            if lbl == label:
                state["mode"] = key
                break
        update_target_visibility()

    mode_combo.bind("<<ComboboxSelected>>", on_mode_change)

    # Row 1: Target label (dynamic - hidden for Enter Only)
    target_lbl = tk.Label(
        content_frm,
        text=get_target_text(),
        bg=BG,
        fg=FG,
        font=FONT,
        justify="left",
        anchor="w",
    )

    def update_target_visibility():
        if mode_needs_target(state["mode"]):
            target_lbl.grid(row=1, column=1, columnspan=3, sticky="w", pady=(4, 0))
            btn_cal.pack(side="left")
            btn_drag_capture_finished.pack(side="left", padx=(8, 0))
            btn_test_capture.pack(side="left", padx=(8, 0))
            setup_target_combo.configure(state="readonly")
        else:
            target_lbl.grid_remove()
            btn_cal.pack_forget()
            btn_drag_capture_finished.pack_forget()
            btn_test_capture.pack_forget()
            setup_target_combo.configure(state="disabled")

    # Row 2: Interval + Timer checkbox
    tk.Label(content_frm, text="Interval (s):", bg=BG, fg=MUTED, font=FONT).grid(row=2, column=1, sticky="w", pady=(8, 0))

    interval_var = tk.StringVar(value=str(initial_seconds))
    interval_entry = tk.Entry(
        content_frm,
        textvariable=interval_var,
        width=6,
        bg=ENTRY_BG,
        fg=ENTRY_FG,
        insertbackground=FG,
        font=FONT,
        relief="flat",
        justify="center",
    )
    interval_entry.grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(8, 0))

    # Show logs toggle (timer is always visible)
    show_logs_var = tk.BooleanVar(value=True)
    show_logs_check = ttk.Checkbutton(
        content_frm,
        text="Show Logs",
        variable=show_logs_var,
        style="Dark.TCheckbutton",
    )
    show_logs_check.grid(row=2, column=3, sticky="w", padx=(12, 0), pady=(8, 0))

    # Timer countdown label (shown when timer is enabled and running)
    timer_lbl = tk.Label(
        content_frm,
        text="",
        bg=BG,
        fg=MUTED,
        font=FONT_SMALL,
        width=6,
    )
    timer_lbl.grid(row=2, column=4, sticky="w", padx=(4, 0), pady=(8, 0))

    # Row 3: state-detection controls
    state_detect_var = tk.BooleanVar(value=True)
    state_detect_check = ttk.Checkbutton(
        content_frm,
        text="State Detection",
        variable=state_detect_var,
        style="Dark.TCheckbutton",
    )
    state_detect_check.grid(row=3, column=1, sticky="w", pady=(8, 0))

    tk.Label(content_frm, text="Word:", bg=BG, fg=MUTED, font=FONT_SMALL).grid(row=3, column=2, sticky="e", padx=(0, 4), pady=(8, 0))
    state_word_var = tk.StringVar(value=detect_word)
    state_word_entry = tk.Entry(
        content_frm,
        textvariable=state_word_var,
        width=10,
        bg=ENTRY_BG,
        fg=ENTRY_FG,
        insertbackground=FG,
        font=FONT_SMALL,
        relief="flat",
        justify="left",
    )
    state_word_entry.grid(row=3, column=3, sticky="w", pady=(8, 0))

    tk.Label(content_frm, text="Threshold:", bg=BG, fg=MUTED, font=FONT_SMALL).grid(row=3, column=4, sticky="e", padx=(0, 4), pady=(8, 0))
    state_threshold_var = tk.StringVar(value=f"{detect_threshold:.2f}")
    state_threshold_entry = tk.Entry(
        content_frm,
        textvariable=state_threshold_var,
        width=6,
        bg=ENTRY_BG,
        fg=ENTRY_FG,
        insertbackground=FG,
        font=FONT_SMALL,
        relief="flat",
        justify="center",
    )
    state_threshold_entry.grid(row=3, column=5, sticky="w", pady=(8, 0))

    log_frame = tk.Frame(frm, bg=BG)
    log_frame.pack(pady=(10, 0), fill="x")
    log_box = ScrolledText(
        log_frame,
        height=4,
        width=56,
        bg=ENTRY_BG,
        fg=FG,
        insertbackground=FG,
        font=("Consolas", 9),
        relief="flat",
        wrap="word",
    )
    log_box.pack(fill="x")
    log_box.config(state="disabled")

    def get_seconds():
        try:
            return float(interval_var.get())
        except ValueError:
            return initial_seconds

    def get_state_enabled() -> bool:
        return state_detect_var.get()

    def get_state_word() -> str:
        txt = state_word_var.get().strip()
        return txt or DETECT_WORD_DEFAULT

    def get_state_threshold() -> float:
        # UI threshold is for manual testing/inspection only.
        try:
            return max(0.0, min(1.0, float(state_threshold_var.get())))
        except ValueError:
            return detect_threshold

    def log_event(msg: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}\n"

        def append_line():
            try:
                log_box.config(state="normal")
                log_box.insert("end", line)
                # Keep the last ~300 lines to avoid growing forever.
                if int(log_box.index("end-1c").split(".")[0]) > 300:
                    log_box.delete("1.0", "80.0")
                log_box.see("end")
                log_box.config(state="disabled")
            except Exception:
                pass

        try:
            root.after(0, append_line)
        except Exception:
            pass

    def update_log_visibility():
        if show_logs_var.get():
            if not log_frame.winfo_ismapped():
                log_frame.pack(side="bottom", pady=(10, 0), fill="x")
        else:
            if log_frame.winfo_ismapped():
                log_frame.pack_forget()

    show_logs_check.configure(command=update_log_visibility)
    update_log_visibility()

    def vision_ready() -> bool:
        _, _, err = try_import_vision()
        if err:
            log_event('Install optional deps for state detection: pip install "auto-press[vision]"')
            return False
        return True

    def capture_drag_bbox():
        """Capture bbox by click-dragging a fullscreen transparent overlay."""
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
            canvas.coords(
                drag["rect"],
                x0 - overlay.winfo_rootx(),
                y0 - overlay.winfo_rooty(),
                event.x_root - overlay.winfo_rootx(),
                event.y_root - overlay.winfo_rooty(),
            )

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
                result["bbox"] = (left, top, width, height)
            overlay.destroy()

        overlay.bind("<Escape>", lambda _e: overlay.destroy())
        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_motion)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.wait_window(overlay)
        return result["bbox"]

    # Buttons rows
    btn_frm_top = tk.Frame(frm, bg=BG)
    btn_frm_top.pack(pady=(12, 0))
    btn_frm_bottom = tk.Frame(frm, bg=BG)
    btn_frm_bottom.pack(pady=(8, 0))

    btn_toggle = tk.Button(
        btn_frm_top,
        text=f"Start/Stop ({toggle_hk})",
        command=lambda: toggle_running(status_canvas),
        bg=BTN_BG,
        fg=BTN_FG,
        activebackground="#2a2a2a",
        activeforeground=BTN_FG,
        bd=0,
        highlightthickness=0,
        font=FONT,
        padx=BTN_PADX,
        pady=BTN_PADY,
        cursor="hand2",
    )
    btn_toggle.pack(side="left", padx=(0, 8))

    def ui_calibrate():
        pt = pyautogui.position()
        idx = setup_target_idx()
        state["targets"][idx] = (pt.x, pt.y)
        set_label_target(target_lbl)
        log_event(f"[setup] T{idx+1} click target set: ({pt.x}, {pt.y})")

    btn_cal = tk.Button(
        btn_frm_top,
        text=f"Calibrate ({calibrate_hk})",
        command=ui_calibrate,
        bg=BTN_BG,
        fg=BTN_FG,
        activebackground="#2a2a2a",
        activeforeground=BTN_FG,
        bd=0,
        highlightthickness=0,
        font=FONT,
        padx=BTN_PADX,
        pady=BTN_PADY,
        cursor="hand2",
    )
    btn_cal.pack(side="left")

    btn_drag_capture_finished = tk.Button(
        btn_frm_bottom,
        text="Drag Capture Finished",
        command=lambda: ui_drag_capture_finished(),
        bg=BTN_BG,
        fg=BTN_FG,
        activebackground="#2a2a2a",
        activeforeground=BTN_FG,
        bd=0,
        highlightthickness=0,
        font=FONT,
        padx=BTN_PADX,
        pady=BTN_PADY,
        cursor="hand2",
    )
    btn_drag_capture_finished.pack(side="left", padx=(8, 0))

    btn_test_capture = tk.Button(
        btn_frm_bottom,
        text="Test Capture",
        command=lambda: ui_test_capture(),
        bg=BTN_BG,
        fg=BTN_FG,
        activebackground="#2a2a2a",
        activeforeground=BTN_FG,
        bd=0,
        highlightthickness=0,
        font=FONT,
        padx=BTN_PADX,
        pady=BTN_PADY,
        cursor="hand2",
    )
    btn_test_capture.pack(side="left", padx=(8, 0))

    def ui_capture_finished():
        idx = setup_target_idx()
        bbox = state["regions"][idx]
        if bbox is None:
            log_event(f"[setup] T{idx+1}: drag-capture area first.")
            return
        if not vision_ready():
            return
        try:
            tpl = grab_region_gray(bbox)
        except Exception as e:
            log_event(f"[setup] T{idx+1}: capture failed: {e}")
            return

        state["tpl_finished"][idx] = tpl
        log_event(f"[setup] T{idx+1} finished template captured.")
        set_label_target(target_lbl)

    def ui_drag_capture_finished():
        idx = setup_target_idx()
        bbox = capture_drag_bbox()
        if bbox is None:
            log_event("[setup] Drag capture cancelled.")
            return
        state["regions"][idx] = bbox
        ui_capture_finished()

    def ui_test_capture():
        idx = setup_target_idx()
        if not get_state_enabled():
            return
        if not target_has_state_data(idx):
            log_event(f"[test] T{idx+1}: not configured")
            return
        try:
            region_gray = grab_region_gray(state["regions"][idx])
            score = match_template_score(region_gray, state["tpl_finished"][idx])
            threshold = get_state_threshold()
            result = "match" if score >= threshold else "no-match"
            log_event(
                f"[test] T{idx+1}: {result} (score={score:.3f}, threshold={threshold:.3f})"
            )
        except Exception as e:
            log_event(f"[test] T{idx+1}: detection error: {e}")

    def on_state_detection_toggle():
        if state_detect_var.get() and not vision_ready():
            state_detect_var.set(False)
            return

    state_detect_check.configure(command=on_state_detection_toggle)

    # Initial visibility update (after btn_cal is created)
    update_target_visibility()
    log_event("[ready] UI started.")

    # Error label (only shown if hotkeys fail - no space reserved)
    error_lbl = None

    def show_error(msg):
        nonlocal error_lbl
        if error_lbl is None:
            error_lbl = tk.Label(frm, text=msg, bg=BG, fg="#ff5252", font=FONT_SMALL)
            error_lbl.pack(pady=(10, 0))

    worker = threading.Thread(
        target=worker_loop,
        args=(
            get_seconds,
            get_state_enabled,
            get_state_word,
            log_event,
        ),
        daemon=True,
    )
    worker.start()

    # hotkey callbacks
    def hk_toggle():
        root.after(0, lambda: toggle_running(status_canvas))

    def hk_calibrate():
        root.after(0, ui_calibrate)

    start_hotkeys(hk_toggle, hk_calibrate)

    # show error if hotkey registration failed
    def check_hotkey_status():
        if not hotkey_ok["ok"]:
            show_error(hotkey_ok["err"])

    root.after(200, check_hotkey_status)

    def on_close():
        stop_event.set()
        interrupt_event.set()  # Wake from interval sleep
        running_event.set()    # Wake from idle wait
        stop_hotkeys()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    # Timer update loop (lightweight, uses after() scheduling)
    def update_timer():
        if stop_event.is_set():
            return
        if state["running"] and state["last_action_time"] > 0:
            interval = max(0.01, get_seconds())
            elapsed = time.perf_counter() - state["last_action_time"]
            remaining = max(0.0, interval - elapsed)
            timer_lbl.config(text=f"{remaining:.1f}s")
        else:
            timer_lbl.config(text="")
        # Reschedule every 100ms (10 updates/sec = 1 decimal precision)
        root.after(100, update_timer)

    update_timer()

    root.mainloop()


# -------------------------
# Headless mode (optional)
# -------------------------
def run_headless(
    seconds: float,
    mode: str,
    x: int | None,
    y: int | None,
    force_calibrate: bool,
    state_detect: bool,
    state_word: str,
    state_bbox: tuple[int, int, int, int] | None,
    state_finished_template: str | None,
    state_threshold: float,
) -> None:
    from datetime import datetime

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    needs_target = mode in (MODE_CLICK, MODE_CLICK_ENTER)

    if needs_target:
        if force_calibrate or x is None or y is None:
            x, y = calibrate_point_hover_console()
        print(f"Target: x={x}, y={y}")

    finished_tpl = None
    if state_detect:
        if mode != MODE_CLICK_ENTER:
            print("[state] State detection only affects click+enter mode. Ignoring.", flush=True)
            state_detect = False
        else:
            if state_bbox is None or not state_finished_template:
                raise SystemExit(
                    "Headless state detection needs --state-bbox and --state-finished-template"
                )
            finished_tpl = load_template_gray(state_finished_template)
            print(
                f"[state] enabled bbox={state_bbox}, threshold={state_threshold}, "
                f"word={state_word!r}",
                flush=True,
            )

    print(f"Mode: {MODE_LABELS[mode]}")
    print(f"Interval: {seconds}s")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] {MODE_LABELS[mode]}")
            inject_text = None
            if state_detect and finished_tpl is not None:
                region_gray = grab_region_gray(state_bbox)
                fin_score = match_template_score(
                    region_gray,
                    finished_tpl,
                )
                if fin_score >= state_threshold:
                    inject_text = state_word
                    print(
                        f"[state] match (score={fin_score:.3f}, threshold={state_threshold:.3f})",
                        flush=True,
                    )
                else:
                    print(
                        f"[state] no-match (score={fin_score:.3f}, threshold={state_threshold:.3f}); "
                        "fallback click+enter",
                        flush=True,
                    )
            elif mode == MODE_CLICK_ENTER:
                reason = "disabled" if not state_detect else "not configured"
                print(
                    f"[state] no-match (reason={reason}, threshold={state_threshold:.3f}); "
                    "fallback click+enter",
                    flush=True,
                )
            do_action(mode, x, y, text_before_enter=inject_text)
            time.sleep(seconds)
    except KeyboardInterrupt:
        print("\nStopped.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Auto Clicker: Click (+ optional Enter) at a target location."
    )
    p.add_argument(
        "seconds", nargs="?", type=float, default=10.0,
        help="Interval between cycles (seconds). Default: 10"
    )

    p.add_argument(
        "--mode", choices=MODES, default=MODE_CLICK_ENTER,
        help=f"Action mode: {MODE_ENTER}=press Enter only, {MODE_CLICK}=click only, "
             f"{MODE_CLICK_ENTER}=click then Enter. Default: {MODE_CLICK_ENTER}"
    )

    p.add_argument("--headless", action="store_true", help="Run without UI.")
    p.add_argument("--x", type=int, help="Target X coordinate (headless, for click modes).")
    p.add_argument("--y", type=int, help="Target Y coordinate (headless, for click modes).")
    p.add_argument("--calibrate", action="store_true", help="Force calibration (headless, for click modes).")
    p.add_argument(
        "--state-detect", action="store_true",
        help="Enable state detection (click+enter mode): finished => type word before Enter."
    )
    p.add_argument(
        "--state-word", default=DETECT_WORD_DEFAULT,
        help=f"Word to type when state is finished. Default: {DETECT_WORD_DEFAULT}"
    )
    p.add_argument(
        "--state-bbox",
        help="State detection region as left,top,width,height (headless)."
    )
    p.add_argument(
        "--state-finished-template",
        help="Path to FINISHED template image (headless state detection)."
    )
    p.add_argument(
        "--state-threshold", type=float, default=DETECT_THRESHOLD_DEFAULT,
        help=f"State match threshold. Default: {DETECT_THRESHOLD_DEFAULT}"
    )

    # Multi-target mode
    p.add_argument(
        "--targets", type=int, default=1, choices=[1, 2, 3],
        help="Number of click targets (1-3). Default: 1. Only applies to click modes."
    )

    # Hotkey config (UI)
    p.add_argument(
        "--toggle", default="PAGEDOWN",
        help='Toggle hotkey. Default: PAGEDOWN'
    )
    p.add_argument(
        "--calibrate-key", default="PAGEUP",
        help='Calibrate hotkey. Default: PAGEUP'
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.seconds <= 0:
        raise SystemExit("seconds must be > 0")

    bbox = None
    if args.state_bbox:
        try:
            bbox = parse_bbox(args.state_bbox)
        except ValueError as e:
            raise SystemExit(f"Invalid --state-bbox: {e}")

    if args.headless:
        run_headless(
            args.seconds,
            args.mode,
            args.x,
            args.y,
            args.calibrate,
            args.state_detect,
            args.state_word,
            bbox,
            args.state_finished_template,
            args.state_threshold,
        )
    else:
        run_ui(
            args.seconds,
            args.toggle,
            args.calibrate_key,
            args.mode,
            args.targets,
            args.state_threshold,
            args.state_word,
        )


if __name__ == "__main__":
    main()
