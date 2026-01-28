# cursor_enter_pinger.py
import argparse
import sys
import time
import threading

try:
    import pyautogui
except ImportError:
    raise SystemExit(
        "pyautogui is required. Install with:\n\n"
        "    uv run --with pyautogui python cursor_enter_pinger.py\n"
        "or:\n"
        "    pip install pyautogui"
    )

IS_WINDOWS = sys.platform.startswith("win")


# 1) Dark mode colors
BG = "#121212"
FG = "#E6E6E6"
BTN_BG = "#1E1E1E"
BTN_FG = FG
ENTRY_BG = "#1A1A1A"
ENTRY_FG = FG
MUTED = "#A8A8A8"

# 2) Bigger status light
LIGHT_SIZE = 54          # overall canvas size
LIGHT_PAD = 8            # padding inside
DOT_DIAM = LIGHT_SIZE - 2 * LIGHT_PAD  # diameter of circle

# -------------------------
# Windows focus + hotkeys (ctypes, no deps)
# -------------------------
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    GetForegroundWindow = user32.GetForegroundWindow
    GetForegroundWindow.restype = wintypes.HWND

    SetForegroundWindow = user32.SetForegroundWindow
    SetForegroundWindow.argtypes = [wintypes.HWND]
    SetForegroundWindow.restype = wintypes.BOOL

    BringWindowToTop = user32.BringWindowToTop
    BringWindowToTop.argtypes = [wintypes.HWND]
    BringWindowToTop.restype = wintypes.BOOL

    ShowWindow = user32.ShowWindow
    ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    ShowWindow.restype = wintypes.BOOL

    SW_RESTORE = 9

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
      "F8"
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


def bring_to_foreground(hwnd: int) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    ShowWindow(hwnd, SW_RESTORE)
    BringWindowToTop(hwnd)
    SetForegroundWindow(hwnd)


# -------------------------
# Core action (Option A)
# -------------------------
def do_cycle(x: int, y: int, state: dict) -> None:
    # save current focus
    prev_hwnd = GetForegroundWindow() if IS_WINDOWS else None

    # bring Cursor window forward if we know it
    cursor_hwnd = state.get("cursor_hwnd")
    if IS_WINDOWS and cursor_hwnd:
        bring_to_foreground(cursor_hwnd)

    # mouse save -> click+enter -> mouse restore
    old = pyautogui.position()
    pyautogui.moveTo(x, y, duration=0)
    pyautogui.click()
    # learn Cursor HWND on first successful focus after click
    if IS_WINDOWS and not state.get("cursor_hwnd"):
        state["cursor_hwnd"] = GetForegroundWindow()
    pyautogui.press("enter")
    pyautogui.moveTo(old.x, old.y, duration=0)

    # restore previous app focus
    if IS_WINDOWS and prev_hwnd:
        bring_to_foreground(prev_hwnd)


# -------------------------
# Calibration (hover)
# -------------------------
def calibrate_point_hover_console() -> tuple[int, int]:
    print("\nCalibration (hover):")
    print("Hover your mouse over the exact spot to click in Cursor.")
    input("Press Enter in this console to capture the current mouse position...")
    pt = pyautogui.position()
    print(f"Captured target: x={pt.x}, y={pt.y}\n")
    return pt.x, pt.y


# -------------------------
# UI mode (default)
# -------------------------
def run_ui(initial_seconds: float, toggle_hk: str, calibrate_hk: str, quit_hk: str) -> None:
    import tkinter as tk

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    state = {
        "running": False,
        "x": None,
        "y": None,
        "cursor_hwnd": None,  # learned on first click
    }

    stop_event = threading.Event()
    running_event = threading.Event()

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
            running_event.set()
        else:
            running_event.clear()
        set_status(canvas, state["running"])

    def set_label_target(label: tk.Label) -> None:
        x, y = state["x"], state["y"]
        if x is None or y is None:
            label.config(text=f"Target: not set (press {calibrate_hk})")
        else:
            label.config(text=f"Target: x={x}, y={y}")

    def worker_loop(get_seconds) -> None:
        while not stop_event.is_set():
            if not running_event.wait(timeout=0.05):
                continue

            x, y = state["x"], state["y"]
            if x is None or y is None:
                time.sleep(0.05)
                continue

            do_cycle(x, y, state)

            interval = max(0.01, float(get_seconds()))
            end = time.monotonic() + interval
            while time.monotonic() < end:
                if stop_event.is_set() or not running_event.is_set():
                    break
                time.sleep(0.01)

    # Hotkey thread (Windows only)
    hotkey_thread_stop = threading.Event()
    hotkey_thread_id = {"tid": None}
    hotkey_ok = {"ok": True, "err": ""}

    def start_hotkeys(on_toggle, on_calibrate, on_quit):
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
                q_mod, q_vk = parse_hotkey(quit_hk)
            except ValueError as e:
                hotkey_ok["ok"] = False
                hotkey_ok["err"] = str(e)
                return

            ID_TOGGLE = 1
            ID_CALIB = 2
            ID_QUIT = 3

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
            if not reg(ID_QUIT, q_mod, q_vk):
                UnregisterHotKey(None, ID_TOGGLE)
                UnregisterHotKey(None, ID_CALIB)
                hotkey_ok["ok"] = False
                hotkey_ok["err"] = f"Failed to register quit hotkey {quit_hk} (collision likely)."
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
                    elif msg.wParam == ID_QUIT:
                        on_quit()
                TranslateMessage(ctypes.byref(msg))
                DispatchMessageW(ctypes.byref(msg))

            UnregisterHotKey(None, ID_TOGGLE)
            UnregisterHotKey(None, ID_CALIB)
            UnregisterHotKey(None, ID_QUIT)

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
    root.title("Cursor REPEAT <Enter>")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    root.resizable(False, False)

    frm = tk.Frame(root, padx=12, pady=12, bg=BG)
    frm.pack()

    status_canvas = tk.Canvas(
        frm,
        width=LIGHT_SIZE,
        height=LIGHT_SIZE,
        highlightthickness=0,
        bg=BG,
    )
    status_canvas.grid(row=0, column=0, rowspan=2, padx=(0, 12))
    set_status(status_canvas, False)

    # Target label (single label; remove the duplicate target_lbl you had)
    target_lbl = tk.Label(
        frm,
        text=f"Target: not set (press {calibrate_hk})",
        anchor="w",
        justify="left",
        bg=BG,
        fg=FG,
    )
    target_lbl.grid(row=0, column=1, columnspan=3, sticky="w")

    # Interval row
    tk.Label(frm, text="Interval (s):", bg=BG, fg=MUTED).grid(row=1, column=1, sticky="e")

    interval_var = tk.StringVar(value=str(initial_seconds))
    interval_entry = tk.Entry(
        frm,
        textvariable=interval_var,
        width=8,
        bg=ENTRY_BG,
        fg=ENTRY_FG,
        insertbackground=FG,  # caret color
    )
    interval_entry.grid(row=1, column=2, sticky="w", padx=(6, 10))

    def get_seconds():
        try:
            return float(interval_var.get())
        except ValueError:
            return initial_seconds

    btn_toggle = tk.Button(
        frm,
        text=f"Start/Stop ({toggle_hk})",
        width=18,
        command=lambda: toggle_running(status_canvas),
        bg=BTN_BG,
        fg=BTN_FG,
        activebackground=BTN_BG,
        activeforeground=BTN_FG,
        bd=0,
        highlightthickness=0,
    )
    btn_toggle.grid(row=2, column=0, pady=(10, 0))

    def ui_calibrate():
        # hover now; capture immediately, no clicking
        pt = pyautogui.position()
        state["x"], state["y"] = pt.x, pt.y
        set_label_target(target_lbl)

    btn_cal = tk.Button(
        frm,
        text=f"Calibrate ({calibrate_hk})",
        width=18,
        command=ui_calibrate,
        bg=BTN_BG,
        fg=BTN_FG,
        activebackground=BTN_BG,
        activeforeground=BTN_FG,
        bd=0,
        highlightthickness=0,
    )
    btn_cal.grid(row=2, column=1, pady=(10, 0), padx=(10, 0))

    btn_quit = tk.Button(
        frm,
        text=f"Quit ({quit_hk})",
        width=18,
        command=root.destroy,
        bg=BTN_BG,
        fg=BTN_FG,
        activebackground=BTN_BG,
        activeforeground=BTN_FG,
        bd=0,
        highlightthickness=0,
    )
    btn_quit.grid(row=2, column=2, pady=(10, 0), padx=(10, 0))

    info_lbl = tk.Label(frm, text="", anchor="w", justify="left", bg=BG, fg=MUTED)
    info_lbl.grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))

    worker = threading.Thread(target=worker_loop, args=(get_seconds,), daemon=True)
    worker.start()

    # hotkey callbacks
    def hk_toggle():
        root.after(0, lambda: toggle_running(status_canvas))

    def hk_calibrate():
        root.after(0, ui_calibrate)

    def hk_quit():
        root.after(0, root.destroy)

    start_hotkeys(hk_toggle, hk_calibrate, hk_quit)

    # show hotkey status
    def refresh_hotkey_status():
        if not hotkey_ok["ok"]:
            info_lbl.config(text=hotkey_ok["err"])
        else:
            info_lbl.config(text=f"Hotkeys active: {toggle_hk} toggle, {calibrate_hk} calibrate, {quit_hk} quit")

    root.after(200, refresh_hotkey_status)

    def on_close():
        stop_event.set()
        running_event.clear()
        stop_hotkeys()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


# -------------------------
# Headless mode (optional)
# -------------------------
def run_headless(seconds: float, x: int | None, y: int | None, force_calibrate: bool) -> None:
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    if force_calibrate or x is None or y is None:
        x, y = calibrate_point_hover_console()

    state = {"cursor_hwnd": None}

    print(f"Target: x={x}, y={y}")
    print(f"Interval: {seconds}s")
    print("Restoring previous window focus after each cycle (Windows only).")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            do_cycle(x, y, state)
            time.sleep(seconds)
    except KeyboardInterrupt:
        print("\nStopped.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cursor Enter pinger (UI default, headless optional).")
    p.add_argument("seconds", nargs="?", type=float, default=3.0, help="Interval between cycles (seconds). Default: 3")
    p.add_argument("--headless", action="store_true", help="Run without UI.")
    p.add_argument("--x", type=int, help="Target X coordinate (headless).")
    p.add_argument("--y", type=int, help="Target Y coordinate (headless).")
    p.add_argument("--calibrate", action="store_true", help="Force calibration (headless).")

    # hotkey config (UI)
    p.add_argument("--toggle", default="F8", help='Toggle hotkey, e.g. "F8" or "CTRL+ALT+P".')
    p.add_argument("--calibrate-key", default="F9", help='Calibrate hotkey, e.g. "F9" or "CTRL+ALT+C".')
    p.add_argument("--quit", default="F10", help='Quit hotkey, e.g. "F10" or "CTRL+ALT+Q".')
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.seconds <= 0:
        raise SystemExit("seconds must be > 0")

    if args.headless:
        run_headless(args.seconds, args.x, args.y, args.calibrate or (args.x is None or args.y is None))
    else:
        run_ui(args.seconds, args.toggle, args.calibrate_key, args.quit)


if __name__ == "__main__":
    main()



