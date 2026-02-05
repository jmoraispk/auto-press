# press_enter.py
# Simplified version: mouse click + optional key press, no window switching logic
import argparse
import sys
import time
import threading

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


# -------------------------
# Core action
# -------------------------
def do_cycle(x: int, y: int, mouse_only: bool) -> None:
    """Click at target, optionally press Enter."""
    old = pyautogui.position()
    pyautogui.moveTo(x, y, duration=0)
    pyautogui.click()
    if not mouse_only:
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
def run_ui(initial_seconds: float, toggle_hk: str, calibrate_hk: str, mouse_only: bool, num_targets: int = 1, show_timer: bool = False) -> None:
    import tkinter as tk

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    state = {
        "running": False,
        "targets": [None] * num_targets,  # List of (x, y) tuples
        "calibrating_index": 0,  # Which target we're calibrating next
        "last_click_time": 0.0,  # time.perf_counter() of last click (for timer)
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
        set_count = sum(1 for t in targets if t is not None)
        if num_targets == 1:
            if targets[0] is None:
                return f"Target: not set (press {calibrate_hk})"
            else:
                return f"Target: x={targets[0][0]}, y={targets[0][1]}"
        else:
            parts = []
            for i, t in enumerate(targets):
                if t is None:
                    parts.append(f"T{i+1}: -")
                else:
                    parts.append(f"T{i+1}: ({t[0]},{t[1]})")
            next_idx = state["calibrating_index"]
            if set_count < num_targets:
                return f"{' | '.join(parts)}  [next: T{next_idx+1}]"
            else:
                return " | ".join(parts)

    def set_label_target(label: tk.Label) -> None:
        label.config(text=get_target_text())

    def all_targets_set() -> bool:
        return all(t is not None for t in state["targets"])

    def worker_loop(get_seconds) -> None:
        while True:
            # Block until running - zero CPU when idle
            running_event.wait()

            if stop_event.is_set():
                break

            if not all_targets_set():
                time.sleep(0.1)
                continue

            targets = state["targets"]
            interval = max(0.01, float(get_seconds()))
            sub_interval = interval / num_targets

            for i, (x, y) in enumerate(targets):
                if stop_event.is_set() or not running_event.is_set():
                    break

                try:
                    do_cycle(x, y, mouse_only)
                    state["last_click_time"] = time.perf_counter()
                except Exception as e:
                    print(f"[worker] Error during cycle on target {i+1}: {e}")
                    continue

                # Sleep for sub_interval - wakes immediately if interrupted
                if interrupt_event.wait(timeout=sub_interval):
                    break  # Interrupted

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
    mode_text = "Mouse Only" if mouse_only else "Click + Enter"
    root = tk.Tk()
    root.title(f"Auto Clicker ({mode_text})")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    root.resizable(False, False)

    FONT = ("Segoe UI", 11)
    FONT_SMALL = ("Segoe UI", 10)
    BTN_PADX = 16
    BTN_PADY = 8

    frm = tk.Frame(root, padx=16, pady=16, bg=BG)
    frm.pack()

    # Main content frame using grid for alignment
    content_frm = tk.Frame(frm, bg=BG)
    content_frm.pack()

    # Status light (spans 3 rows, vertically centered with target row)
    status_canvas = tk.Canvas(
        content_frm,
        width=LIGHT_SIZE,
        height=LIGHT_SIZE,
        highlightthickness=0,
        bg=BG,
    )
    status_canvas.grid(row=0, column=0, rowspan=3, padx=(0, 20), pady=(3, 0))
    set_status(status_canvas, False)

    # Mode label (left-aligned)
    mode_lbl = tk.Label(
        content_frm,
        text=f"Mode: {mode_text}",
        bg=BG,
        fg=MUTED,
        font=FONT_SMALL,
    )
    mode_lbl.grid(row=0, column=1, columnspan=2, sticky="w")

    # Target label (left-aligned)
    target_lbl = tk.Label(
        content_frm,
        text=get_target_text(),
        bg=BG,
        fg=FG,
        font=FONT,
    )
    target_lbl.grid(row=1, column=1, columnspan=2, sticky="w")

    # Interval row (left-aligned with above)
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

    # Timer label (only shown with --timer)
    timer_lbl = None
    if show_timer:
        timer_lbl = tk.Label(
            content_frm,
            text="",
            bg=BG,
            fg=MUTED,
            font=FONT_SMALL,
            width=8,
        )
        timer_lbl.grid(row=2, column=3, sticky="w", padx=(8, 0), pady=(8, 0))

    def get_seconds():
        try:
            return float(interval_var.get())
        except ValueError:
            return initial_seconds

    # Buttons row (centered, equal width)
    btn_frm = tk.Frame(frm, bg=BG)
    btn_frm.pack(pady=(12, 0))

    btn_toggle = tk.Button(
        btn_frm,
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
        idx = state["calibrating_index"]
        state["targets"][idx] = (pt.x, pt.y)
        # Move to next target (cycle back to 0 if all set)
        state["calibrating_index"] = (idx + 1) % num_targets
        set_label_target(target_lbl)

    btn_cal = tk.Button(
        btn_frm,
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

    # Error label (only shown if hotkeys fail - no space reserved)
    error_lbl = None

    def show_error(msg):
        nonlocal error_lbl
        if error_lbl is None:
            error_lbl = tk.Label(frm, text=msg, bg=BG, fg="#ff5252", font=FONT_SMALL)
            error_lbl.pack(pady=(10, 0))

    worker = threading.Thread(target=worker_loop, args=(get_seconds,), daemon=True)
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
        if timer_lbl is not None:
            if state["running"] and state["last_click_time"] > 0:
                interval = max(0.01, get_seconds())
                elapsed = time.perf_counter() - state["last_click_time"]
                remaining = max(0.0, interval - elapsed)
                timer_lbl.config(text=f"{remaining:.1f}s")
            elif not state["running"]:
                timer_lbl.config(text="")
        # Reschedule every 100ms (10 updates/sec = 1 decimal precision)
        root.after(100, update_timer)

    if show_timer:
        update_timer()

    root.mainloop()


# -------------------------
# Headless mode (optional)
# -------------------------
def run_headless(seconds: float, x: int | None, y: int | None, force_calibrate: bool, mouse_only: bool) -> None:
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    if force_calibrate or x is None or y is None:
        x, y = calibrate_point_hover_console()

    mode_text = "Mouse Only" if mouse_only else "Click + Enter"
    print(f"Mode: {mode_text}")
    print(f"Target: x={x}, y={y}")
    print(f"Interval: {seconds}s")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            do_cycle(x, y, mouse_only)
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

    # Simple mode - just press Enter repeatedly
    p.add_argument(
        "--simple", action="store_true",
        help="Simple mode: just press Enter every N seconds (no mouse, no UI)."
    )

    p.add_argument("--headless", action="store_true", help="Run without UI.")
    p.add_argument("--x", type=int, help="Target X coordinate (headless).")
    p.add_argument("--y", type=int, help="Target Y coordinate (headless).")
    p.add_argument("--calibrate", action="store_true", help="Force calibration (headless).")

    # Mouse-only mode
    p.add_argument(
        "--mouse-only", action="store_true",
        help="Only click the mouse, do not press Enter."
    )

    # Multi-target mode
    p.add_argument(
        "--targets", type=int, default=1, choices=[1, 2, 3],
        help="Number of click targets (1-3). Default: 1"
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
    p.add_argument(
        "--timer", action="store_true",
        help="Show a countdown timer next to the interval (time to next click)."
    )
    return p.parse_args()


def run_simple(seconds: float) -> None:
    """Simple mode: just press Enter repeatedly, no mouse, no UI."""
    from datetime import datetime

    pyautogui.PAUSE = 0

    print(f"Simple mode: pressing Enter every {seconds} seconds.")
    print("Focus the target window, then leave this running.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] press enter")
            pyautogui.press("enter")
            time.sleep(seconds)
    except KeyboardInterrupt:
        print("\nStopped.")


def main() -> None:
    args = parse_args()
    if args.seconds <= 0:
        raise SystemExit("seconds must be > 0")

    if args.simple:
        run_simple(args.seconds)
    elif args.headless:
        run_headless(
            args.seconds, args.x, args.y,
            args.calibrate or (args.x is None or args.y is None),
            args.mouse_only
        )
    else:
        run_ui(args.seconds, args.toggle, args.calibrate_key, args.mouse_only, args.targets, args.timer)


if __name__ == "__main__":
    main()
