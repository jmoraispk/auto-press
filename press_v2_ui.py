"""Simplified v2 UI for rule-based screen scanning automation."""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter.scrolledtext import ScrolledText

import pyautogui
from PIL import Image, ImageTk

from press_core import save_gray_image
from press_tray import PYSTRAY_AVAILABLE, PYSTRAY_IMPORT_ERROR, TrayController
from press_v2_engine import build_runtime_rules, capture_screen_gray, ensure_vision, evaluate_rule_on_frame, evaluate_rules, execute_matches
from press_v2_store import (
    ACTION_CLICK,
    ACTION_CLICK_TYPE_ENTER,
    ACTION_TYPES,
    V2_CONFIG_PATH,
    default_rule,
    list_template_files,
    load_config,
    make_rule_summary,
    serialize_template_path,
    resolve_template_path,
    save_config,
    template_asset_path,
)


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
    MOD_NOREPEAT = 0x4000
    VK_PAGEDOWN = 0x22

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", wintypes.POINT),
        ]


def run_v2_ui(initial_seconds: float) -> None:
    import customtkinter as ctk

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    cfg = load_config()
    cfg["interval_seconds"] = float(initial_seconds)
    save_config(cfg)

    cfg_lock = threading.Lock()
    state = {
        "runtime_rules": [],
        "last_scores": {},
        "running": False,
        "last_action": "Idle",
        "next_tick_at": None,
    }

    root = ctk.CTk()
    root.title("Auto Press V2")
    root.geometry("900x560")
    root.attributes("-topmost", True)

    FONT = ("Segoe UI", 11)
    FONT_SMALL = ("Segoe UI", 10)
    MUTED = "#A8A8A8"
    STATUS_STOPPED = "#d32f2f"
    STATUS_RUNNING = "#2e7d32"

    running_event = threading.Event()
    stop_event = threading.Event()
    interrupt_event = threading.Event()
    tooltip_state = {"window": None}
    hotkey_thread_stop = threading.Event()
    hotkey_thread_id = {"tid": None}
    hotkey_ok = {"ok": True, "err": ""}
    window_visible = {"value": True}
    tray: TrayController | None = None

    def log_event(message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}\n"

        def append() -> None:
            log_box.configure(state="normal")
            log_box.insert("end", line)
            if int(log_box.index("end-1c").split(".")[0]) > 250:
                log_box.delete("1.0", "80.0")
            log_box.see("end")
            log_box.configure(state="disabled")

        root.after(0, append)

    def attach_tooltip(widget, text: str) -> None:
        def hide_tooltip(_event=None) -> None:
            tip = tooltip_state.get("window")
            if tip is not None:
                tip.destroy()
                tooltip_state["window"] = None

        def show_tooltip(_event=None) -> None:
            hide_tooltip()
            tip = tk.Toplevel(root)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            tip.configure(bg="#1f1f1f")
            label = tk.Label(
                tip,
                text=text,
                justify="left",
                wraplength=260,
                bg="#1f1f1f",
                fg="#f0f0f0",
                relief="solid",
                borderwidth=1,
                padx=8,
                pady=6,
            )
            label.pack()
            x = widget.winfo_rootx() + widget.winfo_width() + 8
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tip.geometry(f"+{x}+{y}")
            tooltip_state["window"] = tip

        widget.bind("<Enter>", show_tooltip, add="+")
        widget.bind("<Leave>", hide_tooltip, add="+")
        widget.bind("<ButtonPress>", hide_tooltip, add="+")

    def info_badge(parent, text: str, side: str = "left", padx: tuple[int, int] = (4, 0)):
        badge = ctk.CTkLabel(parent, text="(?)", text_color=MUTED, font=FONT_SMALL)
        badge.pack(side=side, padx=padx)
        attach_tooltip(badge, text)
        return badge

    def capture_drag_bbox() -> list[int] | None:
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
                result["bbox"] = [left, top, width, height]
            overlay.destroy()

        overlay.bind("<Escape>", lambda _e: overlay.destroy())
        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_motion)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.wait_window(overlay)
        return result["bbox"]

    def selected_index() -> int | None:
        selection = rule_list.curselection()
        if not selection:
            return None
        return int(selection[0])

    def selected_rule() -> dict | None:
        idx = selected_index()
        if idx is None:
            return None
        with cfg_lock:
            rules = cfg.get("rules", [])
            if 0 <= idx < len(rules):
                return rules[idx]
        return None

    def update_runtime_rules() -> None:
        try:
            with cfg_lock:
                state["runtime_rules"] = build_runtime_rules(cfg)
        except Exception as exc:
            state["runtime_rules"] = []
            log_event(f"[error] runtime rules unavailable: {exc}")

    def persist_and_refresh(select_idx: int | None = None) -> None:
        with cfg_lock:
            save_config(cfg)
        refresh_rule_list(select_idx)
        update_runtime_rules()

    def refresh_rule_list(select_idx: int | None = None) -> None:
        current = selected_index() if select_idx is None else select_idx
        rule_list.delete(0, "end")
        with cfg_lock:
            rules = cfg.get("rules", [])
            for rule in rules:
                rule_list.insert("end", make_rule_summary(rule, state["last_scores"].get(rule["id"])))
        if current is not None and rule_list.size() > 0:
            bounded = max(0, min(rule_list.size() - 1, current))
            rule_list.selection_clear(0, "end")
            rule_list.selection_set(bounded)
            rule_list.activate(bounded)
            load_selected_rule()
        else:
            clear_editor()

    def current_interval() -> float:
        try:
            return max(0.1, float(interval_var.get().strip()))
        except ValueError:
            return 10.0

    def clear_editor() -> None:
        name_var.set("")
        enabled_var.set(True)
        threshold_var.set("0.90")
        action_var.set(ACTION_CLICK)
        text_var.set("continue")
        template_choice_var.set("")
        region_var.set("Whole screen")
        update_action_fields()

    def load_selected_rule(_event=None) -> None:
        rule = selected_rule()
        if rule is None:
            clear_editor()
            return
        name_var.set(rule.get("name", ""))
        enabled_var.set(bool(rule.get("enabled", True)))
        threshold_var.set(f"{float(rule.get('threshold', 0.90)):.2f}")
        action_var.set(rule.get("action", ACTION_CLICK))
        text_var.set(rule.get("text", "continue"))
        template_choice_var.set(rule.get("template_path") or "")
        region = rule.get("search_region")
        region_var.set("Whole screen" if not region else f"{tuple(region)}")
        update_action_fields()

    def refresh_template_choices(selected: str | None = None) -> None:
        options = [""] + list_template_files()
        template_choice_menu.configure(values=options)
        if selected is not None:
            template_choice_var.set(selected if selected in options else "")
        elif template_choice_var.get() not in options:
            template_choice_var.set("")

    def update_action_fields(*_args) -> None:
        text_entry.configure(state="normal" if action_var.get() == ACTION_CLICK_TYPE_ENTER else "disabled")

    def set_running_status(running: bool) -> None:
        if running:
            status_var.set("Running")
            status_label.configure(text_color=STATUS_RUNNING)
        else:
            status_var.set("Stopped")
            status_label.configure(text_color=STATUS_STOPPED)
        if tray is not None:
            try:
                tray.update_status(running)
            except Exception as exc:
                log_event(f"[tray] update failed: {exc}")

    def save_selected_rule() -> bool:
        idx = selected_index()
        if idx is None:
            log_event("[rule] select a rule first")
            return False
        with cfg_lock:
            rule = cfg["rules"][idx]
            rule["name"] = name_var.get().strip() or f"Rule {idx + 1}"
            rule["enabled"] = bool(enabled_var.get())
            try:
                rule["threshold"] = max(0.0, min(1.0, float(threshold_var.get().strip())))
            except ValueError:
                rule["threshold"] = 0.90
            rule["action"] = action_var.get() if action_var.get() in ACTION_TYPES else ACTION_CLICK
            rule["text"] = text_var.get().strip() or "continue"
            for pos, item in enumerate(cfg["rules"], start=1):
                item["priority"] = pos
        persist_and_refresh(idx)
        log_event(f"[rule] saved {name_var.get().strip() or f'Rule {idx + 1}'}")
        return True

    def add_rule() -> None:
        with cfg_lock:
            rule = default_rule(name=f"Rule {len(cfg['rules']) + 1}")
            rule["priority"] = len(cfg["rules"]) + 1
            cfg["rules"].append(rule)
            idx = len(cfg["rules"]) - 1
        persist_and_refresh(idx)
        log_event(f"[rule] added {rule['name']}")

    def delete_rule() -> None:
        idx = selected_index()
        if idx is None:
            log_event("[rule] select a rule to delete")
            return
        with cfg_lock:
            removed = cfg["rules"].pop(idx)
            for pos, item in enumerate(cfg["rules"], start=1):
                item["priority"] = pos
            state["last_scores"].pop(removed["id"], None)
        persist_and_refresh(max(0, idx - 1))
        log_event(f"[rule] deleted {removed['name']}")

    def move_rule(direction: int) -> None:
        idx = selected_index()
        if idx is None:
            log_event("[rule] select a rule to move")
            return
        with cfg_lock:
            new_idx = idx + direction
            if not (0 <= new_idx < len(cfg["rules"])):
                return
            cfg["rules"][idx], cfg["rules"][new_idx] = cfg["rules"][new_idx], cfg["rules"][idx]
            for pos, item in enumerate(cfg["rules"], start=1):
                item["priority"] = pos
        persist_and_refresh(new_idx)

    def capture_template() -> None:
        idx = selected_index()
        if idx is None:
            log_event("[capture] add or select a rule first")
            return
        try:
            ensure_vision()
        except Exception as exc:
            log_event(f"[error] {exc}")
            return
        bbox = capture_drag_bbox()
        if not bbox:
            log_event("[capture] template capture cancelled")
            return
        try:
            gray = capture_screen_gray(tuple(bbox))
            file_name = f"v2_rule_{cfg['rules'][idx]['id']}.png"
            path = template_asset_path(file_name)
            save_gray_image(str(path), gray)
            stored_path = serialize_template_path(path)
            with cfg_lock:
                cfg["rules"][idx]["template_path"] = stored_path
            persist_and_refresh(idx)
            refresh_template_choices(stored_path)
            log_event(f"[capture] template saved to {path.name}")
        except Exception as exc:
            log_event(f"[error] template capture failed: {exc}")

    def use_selected_template() -> None:
        idx = selected_index()
        if idx is None:
            log_event("[template] select a rule first")
            return
        choice = template_choice_var.get().strip()
        if not choice:
            log_event("[template] choose an existing template first")
            return
        with cfg_lock:
            cfg["rules"][idx]["template_path"] = choice
        persist_and_refresh(idx)
        log_event(f"[template] selected {choice}")

    def capture_search_region() -> None:
        idx = selected_index()
        if idx is None:
            log_event("[capture] select a rule first")
            return
        bbox = capture_drag_bbox()
        if not bbox:
            log_event("[capture] search region cancelled")
            return
        with cfg_lock:
            cfg["rules"][idx]["search_region"] = bbox
        persist_and_refresh(idx)
        region_var.set(str(tuple(bbox)))
        log_event(f"[capture] search region set to {tuple(bbox)}")

    def use_whole_screen() -> None:
        idx = selected_index()
        if idx is None:
            log_event("[capture] select a rule first")
            return
        with cfg_lock:
            cfg["rules"][idx]["search_region"] = None
        persist_and_refresh(idx)
        region_var.set("Whole screen")
        log_event("[capture] rule now scans the whole screen")

    def test_selected_rule() -> None:
        idx = selected_index()
        if idx is None:
            log_event("[test] select a rule first")
            return
        if not save_selected_rule():
            return
        try:
            with cfg_lock:
                rule = dict(cfg["rules"][idx])
            template_path = resolve_template_path(rule.get("template_path"))
            if template_path is None or not Path(template_path).exists():
                log_event("[test] capture a template first")
                return
            runtime_rule = build_runtime_rules({"rules": [rule]})
            if not runtime_rule:
                log_event("[test] rule is not ready")
                return
            frame = capture_screen_gray()
            score, center = evaluate_rule_on_frame(frame, runtime_rule[0])
            matched = center is not None and score >= float(rule.get("threshold", 0.90))
            state["last_scores"][rule["id"]] = score
            refresh_rule_list(idx)
            result = "match" if matched else "no-match"
            log_event(f"[test] {rule['name']} result={result} score={score:.3f} center={center}")
        except Exception as exc:
            log_event(f"[error] test failed: {exc}")

    top = ctk.CTkFrame(root)
    top.pack(fill="x", padx=14, pady=(14, 8))

    status_var = tk.StringVar(value="Stopped")
    action_status_var = tk.StringVar(value="Idle")
    interval_var = tk.StringVar(value=str(cfg.get("interval_seconds", initial_seconds)))
    countdown_var = tk.StringVar(value="")
    show_workspace_var = tk.BooleanVar(value=True)
    show_log_var = tk.BooleanVar(value=True)

    ctk.CTkButton(top, text="Start / Stop", command=lambda: toggle_running(), width=120).pack(side="left", padx=(0, 8))
    interval_frame = ctk.CTkFrame(top, fg_color="transparent")
    interval_frame.pack(side="left")
    ctk.CTkLabel(interval_frame, text="Interval (s):", font=FONT, text_color=MUTED).pack(side="left")
    info_badge(interval_frame, "How often the app captures the screen and evaluates all enabled rules.")
    ctk.CTkEntry(interval_frame, textvariable=interval_var, width=80).pack(side="left", padx=(6, 14))
    countdown_label = ctk.CTkLabel(top, textvariable=countdown_var, font=("Consolas", 18, "bold"), text_color=MUTED)
    status_label = ctk.CTkLabel(top, textvariable=status_var, font=FONT, text_color=STATUS_STOPPED)
    status_label.pack(side="left", padx=(0, 14))
    ctk.CTkLabel(top, textvariable=action_status_var, font=FONT_SMALL, text_color=MUTED).pack(side="left")
    panel_toggle_frame = ctk.CTkFrame(top, fg_color="transparent")
    panel_toggle_frame.pack(side="right")

    body = ctk.CTkFrame(root)
    body.pack(fill="both", expand=True, padx=14, pady=(0, 8))

    left = ctk.CTkFrame(body)
    left.pack(side="left", fill="y", padx=(0, 8), pady=8)
    right = ctk.CTkFrame(body)
    right.pack(side="left", fill="both", expand=True, pady=8)

    rules_header = ctk.CTkFrame(left, fg_color="transparent")
    rules_header.pack(anchor="w", padx=12, pady=(12, 8))
    ctk.CTkLabel(rules_header, text="Rules", font=("Segoe UI", 14, "bold")).pack(side="left")
    info_badge(rules_header, "Enabled rules are checked every scan. Use Up and Down to control evaluation order.")
    rule_list = tk.Listbox(left, width=34, height=14, activestyle="dotbox", exportselection=False)
    rule_list.pack(padx=12, pady=(0, 10))
    rule_list.bind("<<ListboxSelect>>", load_selected_rule)

    left_buttons = ctk.CTkFrame(left, fg_color="transparent")
    left_buttons.pack(fill="x", padx=12, pady=(0, 10))
    ctk.CTkButton(left_buttons, text="Add", command=add_rule, width=70).pack(side="left", padx=(0, 6))
    ctk.CTkButton(left_buttons, text="Delete", command=delete_rule, width=70).pack(side="left", padx=(0, 6))
    ctk.CTkButton(left_buttons, text="Up", command=lambda: move_rule(-1), width=60).pack(side="left", padx=(0, 6))
    ctk.CTkButton(left_buttons, text="Down", command=lambda: move_rule(1), width=60).pack(side="left")

    ctk.CTkLabel(left, text=f"Config: {V2_CONFIG_PATH.name}", font=FONT_SMALL, text_color=MUTED).pack(anchor="w", padx=12, pady=(0, 12))

    editor = ctk.CTkFrame(right, fg_color="transparent")
    editor.pack(fill="both", expand=True, padx=14, pady=(14, 8))

    name_var = tk.StringVar()
    enabled_var = tk.BooleanVar(value=True)
    threshold_var = tk.StringVar(value="0.90")
    action_var = tk.StringVar(value=ACTION_CLICK)
    text_var = tk.StringVar(value="continue")
    template_choice_var = tk.StringVar(value="")
    region_var = tk.StringVar(value="Whole screen")

    editor_header = ctk.CTkFrame(editor, fg_color="transparent")
    editor_header.pack(anchor="w", pady=(0, 10))
    ctk.CTkLabel(editor_header, text="Rule Editor", font=("Segoe UI", 14, "bold")).pack(side="left")
    info_badge(editor_header, "Edit the selected rule. Rules match a template on screen and then run the chosen action.")

    basics_frame = ctk.CTkFrame(editor, fg_color="transparent")
    basics_frame.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(basics_frame, text="Name", font=FONT_SMALL, text_color=MUTED).grid(row=0, column=0, sticky="w")
    name_help = ctk.CTkLabel(basics_frame, text="(?)", text_color=MUTED, font=FONT_SMALL)
    name_help.grid(row=0, column=1, sticky="w", padx=(4, 12))
    attach_tooltip(name_help, "Friendly label used in the rules list and log output.")
    ctk.CTkLabel(basics_frame, text="Action", font=FONT_SMALL, text_color=MUTED).grid(row=0, column=2, sticky="w")
    action_help = ctk.CTkLabel(basics_frame, text="(?)", text_color=MUTED, font=FONT_SMALL)
    action_help.grid(row=0, column=3, sticky="w", padx=(4, 12))
    attach_tooltip(action_help, "Click: click the matched center. Click+type+enter: click, type the text, then press Enter.")
    ctk.CTkLabel(basics_frame, text="Text (optional)", font=FONT_SMALL, text_color=MUTED).grid(row=0, column=4, sticky="w")
    text_help = ctk.CTkLabel(basics_frame, text="(?)", text_color=MUTED, font=FONT_SMALL)
    text_help.grid(row=0, column=5, sticky="w", padx=(4, 0))
    attach_tooltip(text_help, "Only used by click+type+enter. Leave it as the default if you just want a simple continuation word.")
    ctk.CTkEntry(basics_frame, textvariable=name_var, width=180).grid(row=1, column=0, sticky="w", padx=(0, 12))
    ctk.CTkCheckBox(basics_frame, text="Enabled", variable=enabled_var).grid(row=1, column=1, columnspan=2, sticky="w", padx=(0, 12))
    action_menu = ctk.CTkOptionMenu(basics_frame, values=ACTION_TYPES, variable=action_var, command=update_action_fields, width=150)
    action_menu.grid(row=1, column=2, columnspan=2, sticky="w", padx=(0, 12))
    text_entry = ctk.CTkEntry(basics_frame, textvariable=text_var, width=160)
    text_entry.grid(row=1, column=4, columnspan=2, sticky="w")

    PREVIEW_BOX_W = 240
    PREVIEW_BOX_H = 120
    PREVIEW_BG = "#1a1a1a"
    PREVIEW_PLACEHOLDER = "(no template selected)"
    preview_meta_var = tk.StringVar(value="")

    template_section = ctk.CTkFrame(editor)
    template_section.pack(fill="x", pady=(0, 8))
    template_header = ctk.CTkFrame(template_section, fg_color="transparent")
    template_header.pack(anchor="w", padx=12, pady=(10, 4))
    ctk.CTkLabel(template_header, text="Template & Matching", font=("Segoe UI", 12, "bold")).pack(side="left")
    info_badge(template_header, "Choose or capture the image that defines a match. The preview shows exactly what the engine will search for.")

    template_row = ctk.CTkFrame(template_section, fg_color="transparent")
    template_row.pack(fill="x", padx=12, pady=(0, 8))
    template_choice_menu = ctk.CTkOptionMenu(template_row, values=[""], variable=template_choice_var, width=220)
    template_choice_menu.pack(side="left", padx=(0, 8))
    ctk.CTkButton(template_row, text="Use Existing", command=use_selected_template, width=110).pack(side="left", padx=(0, 8))
    ctk.CTkButton(template_row, text="Capture Pattern", command=capture_template, width=130).pack(side="left")

    preview_row = ctk.CTkFrame(template_section, fg_color="transparent")
    preview_row.pack(fill="x", padx=12, pady=(0, 12))

    preview_container = ctk.CTkFrame(preview_row, width=PREVIEW_BOX_W, height=PREVIEW_BOX_H, fg_color=PREVIEW_BG, corner_radius=6)
    preview_container.pack(side="left", padx=(0, 14))
    preview_container.pack_propagate(False)
    preview_label = tk.Label(preview_container, bg=PREVIEW_BG, fg=MUTED, text=PREVIEW_PLACEHOLDER, font=FONT_SMALL)
    preview_label.pack(expand=True, fill="both")
    attach_tooltip(preview_label, "Live preview of the currently selected template. Shown at its native resolution (templates are usually small buttons or icons).")

    preview_details = ctk.CTkFrame(preview_row, fg_color="transparent")
    preview_details.pack(side="left", fill="both", expand=True)

    threshold_row = ctk.CTkFrame(preview_details, fg_color="transparent")
    threshold_row.pack(anchor="w")
    ctk.CTkLabel(threshold_row, text="Match threshold", font=FONT_SMALL, text_color=MUTED).pack(side="left")
    threshold_help = ctk.CTkLabel(threshold_row, text="(?)", text_color=MUTED, font=FONT_SMALL)
    threshold_help.pack(side="left", padx=(4, 8))
    attach_tooltip(threshold_help, "Higher values are stricter. Around 0.90 is a good default for stable button templates.")
    ctk.CTkEntry(threshold_row, textvariable=threshold_var, width=80).pack(side="left")

    meta_label = ctk.CTkLabel(preview_details, textvariable=preview_meta_var, font=FONT_SMALL, text_color=MUTED, justify="left", anchor="w")
    meta_label.pack(anchor="w", pady=(10, 0), fill="x")

    def update_template_preview(*_args) -> None:
        name = template_choice_var.get().strip()
        if not name:
            preview_label.configure(image="", text=PREVIEW_PLACEHOLDER, fg=MUTED)
            preview_label.image = None
            preview_meta_var.set("")
            return
        path = resolve_template_path(name)
        if path is None or not Path(path).exists():
            preview_label.configure(image="", text="(file missing)", fg="#ef5350")
            preview_label.image = None
            preview_meta_var.set(f"File: {name}\n(not found under templates/)")
            return
        try:
            img = Image.open(path).convert("RGB")
            native_w, native_h = img.size
            if native_w > PREVIEW_BOX_W - 8 or native_h > PREVIEW_BOX_H - 8:
                img = img.copy()
                img.thumbnail((PREVIEW_BOX_W - 8, PREVIEW_BOX_H - 8), Image.LANCZOS)
                scale_note = f" (fit to preview)"
            else:
                scale_note = " (actual size)"
            photo = ImageTk.PhotoImage(img)
            preview_label.configure(image=photo, text="")
            preview_label.image = photo  # keep reference, tk only holds a weak one
            preview_meta_var.set(f"File: {name}\nSize: {native_w} \u00d7 {native_h} px{scale_note}")
        except Exception as exc:
            preview_label.configure(image="", text="(preview error)", fg="#ef5350")
            preview_label.image = None
            preview_meta_var.set(f"File: {name}\n({exc})")

    template_choice_var.trace_add("write", update_template_preview)

    search_section = ctk.CTkFrame(editor)
    search_section.pack(fill="x", pady=(0, 8))
    search_header = ctk.CTkFrame(search_section, fg_color="transparent")
    search_header.pack(anchor="w", padx=12, pady=(10, 4))
    ctk.CTkLabel(search_header, text="Search Scope", font=("Segoe UI", 12, "bold")).pack(side="left")
    info_badge(search_header, "Whole screen scans everywhere. A search region makes matching faster and reduces false positives.")
    search_row = ctk.CTkFrame(search_section, fg_color="transparent")
    search_row.pack(fill="x", padx=12, pady=(0, 10))
    ctk.CTkLabel(search_row, textvariable=region_var, font=FONT_SMALL, text_color=MUTED).pack(side="left", padx=(0, 12))
    ctk.CTkButton(search_row, text="Capture Search Region", command=capture_search_region, width=160).pack(side="left", padx=(0, 8))
    ctk.CTkButton(search_row, text="Use Whole Screen", command=use_whole_screen, width=130).pack(side="left")

    editor_actions = ctk.CTkFrame(editor, fg_color="transparent")
    editor_actions.pack(fill="x", pady=(4, 0))
    ctk.CTkButton(editor_actions, text="Test Match", command=test_selected_rule, width=100).pack(side="left", padx=(0, 8))
    ctk.CTkButton(editor_actions, text="Save Rule", command=save_selected_rule, width=100).pack(side="left")
    info_badge(editor_actions, "Test Match runs a single scan without starting the loop. Save Rule persists your edits to disk.", padx=(8, 0))

    log_frame = ctk.CTkFrame(root)
    log_frame.pack(fill="x", padx=14, pady=(0, 14))
    log_header = ctk.CTkFrame(log_frame, fg_color="transparent")
    log_header.pack(anchor="w", padx=12, pady=(10, 8))
    ctk.CTkLabel(log_header, text="Log", font=("Segoe UI", 14, "bold")).pack(side="left")
    info_badge(log_header, "Shows setup actions, match results, and runtime errors for the current session.")
    log_box = ScrolledText(log_frame, height=14, wrap="word")
    log_box.pack(fill="x", padx=12, pady=(0, 12))
    log_box.configure(state="disabled")

    def update_panel_visibility() -> None:
        show_workspace = bool(show_workspace_var.get())
        show_log = bool(show_log_var.get())

        if show_workspace:
            if not body.winfo_ismapped():
                if log_frame.winfo_ismapped():
                    body.pack(fill="both", expand=True, padx=14, pady=(0, 8), before=log_frame)
                else:
                    body.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        elif body.winfo_ismapped():
            body.pack_forget()

        if show_log:
            if not log_frame.winfo_ismapped():
                log_frame.pack(fill="x", padx=14, pady=(0, 14))
        elif log_frame.winfo_ismapped():
            log_frame.pack_forget()

    workspace_toggle = ctk.CTkCheckBox(panel_toggle_frame, text="Workspace", variable=show_workspace_var, command=update_panel_visibility, width=110)
    workspace_toggle.pack(side="left", padx=(0, 6))
    attach_tooltip(workspace_toggle, "Show or hide the main workspace containing both the rules list and rule editor.")
    log_toggle = ctk.CTkCheckBox(panel_toggle_frame, text="Log", variable=show_log_var, command=update_panel_visibility, width=70)
    log_toggle.pack(side="left")
    attach_tooltip(log_toggle, "Show or hide the log panel. You can hide both workspace and log for a minimal top-bar view.")

    def update_top_row_visibility() -> None:
        if state["running"]:
            if interval_frame.winfo_ismapped():
                interval_frame.pack_forget()
            if not countdown_label.winfo_ismapped():
                countdown_label.pack(side="left", padx=(0, 14), before=status_label)
        else:
            if countdown_label.winfo_ismapped():
                countdown_label.pack_forget()
            if not interval_frame.winfo_ismapped():
                interval_frame.pack(side="left", before=status_label)

    def update_timer() -> None:
        if stop_event.is_set():
            return
        if state["running"] and state["next_tick_at"] is not None:
            remaining = max(0.0, float(state["next_tick_at"]) - time.perf_counter())
            countdown_var.set(f"{remaining:.1f}s")
        else:
            countdown_var.set("")
        root.after(100, update_timer)

    def toggle_running() -> None:
        if state["running"]:
            state["running"] = False
            running_event.clear()
            interrupt_event.set()
            state["next_tick_at"] = None
            set_running_status(False)
            update_top_row_visibility()
            action_status_var.set("Idle")
            log_event("[control] stopped")
            return
        with cfg_lock:
            cfg["interval_seconds"] = current_interval()
            save_config(cfg)
        update_runtime_rules()
        if not state["runtime_rules"]:
            log_event("[control] add at least one enabled rule with a selected or captured template")
            return
        state["running"] = True
        state["next_tick_at"] = time.perf_counter() + current_interval()
        interrupt_event.clear()
        running_event.set()
        set_running_status(True)
        update_top_row_visibility()
        log_event("[control] started")

    def start_hotkeys() -> None:
        if not IS_WINDOWS:
            hotkey_ok["ok"] = False
            hotkey_ok["err"] = "Global hotkeys are only supported on Windows."
            return

        def hotkey_loop() -> None:
            tid = GetCurrentThreadId()
            hotkey_thread_id["tid"] = tid
            HOTKEY_ID = 1

            if not RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, VK_PAGEDOWN):
                hotkey_ok["ok"] = False
                hotkey_ok["err"] = "Failed to register Page Down hotkey."
                return

            msg = MSG()
            while not hotkey_thread_stop.is_set():
                ok = GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ok <= 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    root.after(0, toggle_running)
                TranslateMessage(ctypes.byref(msg))
                DispatchMessageW(ctypes.byref(msg))
            UnregisterHotKey(None, HOTKEY_ID)

        threading.Thread(target=hotkey_loop, daemon=True).start()

    def stop_hotkeys() -> None:
        if not IS_WINDOWS:
            return
        hotkey_thread_stop.set()
        tid = hotkey_thread_id["tid"]
        if tid:
            PostThreadMessageW(tid, WM_QUIT, 0, 0)

    def worker_loop() -> None:
        while not stop_event.is_set():
            if not running_event.wait(timeout=0.2):
                continue
            try:
                with cfg_lock:
                    interval = current_interval()
                    cfg["interval_seconds"] = interval
                    runtime_rules = list(state["runtime_rules"])
                results, actions = evaluate_rules(runtime_rules)
                for result in results:
                    state["last_scores"][result["id"]] = float(result["score"])
                root.after(0, refresh_rule_list)
                if actions:
                    execute_matches(actions)
                    summaries: dict[str, int] = {}
                    for action in actions:
                        summaries[action["name"]] = summaries.get(action["name"], 0) + 1
                    summary_text = ", ".join(f"{name} x{count}" for name, count in summaries.items())
                    state["last_action"] = summary_text
                    root.after(0, lambda text=state["last_action"]: action_status_var.set(text))
                    log_event(f"[tick] matched {summary_text}")
                else:
                    state["last_action"] = "No match"
                    root.after(0, lambda: action_status_var.set("No match"))
                    log_event("[tick] no eligible rule matched")
                state["next_tick_at"] = time.perf_counter() + interval
            except Exception as exc:
                log_event(f"[error] worker failed: {exc}")
            if interrupt_event.wait(timeout=current_interval()):
                interrupt_event.clear()

    worker = threading.Thread(target=worker_loop, daemon=True)
    worker.start()

    root.bind_all("<Next>", lambda _event: toggle_running())
    start_hotkeys()
    if not hotkey_ok["ok"]:
        log_event(f"[error] {hotkey_ok['err']}")

    update_action_fields()
    refresh_template_choices()
    refresh_rule_list(0 if cfg.get("rules") else None)
    update_runtime_rules()
    set_running_status(False)
    update_top_row_visibility()
    update_panel_visibility()
    update_timer()
    log_event(f"[ready] loaded {V2_CONFIG_PATH}")

    def show_window() -> None:
        window_visible["value"] = True
        try:
            root.deiconify()
            root.lift()
            root.focus_force()
        except Exception:
            pass
        if tray is not None:
            tray.refresh_menu()

    def hide_window() -> None:
        window_visible["value"] = False
        try:
            root.withdraw()
        except Exception:
            pass
        if tray is not None:
            tray.refresh_menu()

    def toggle_window_visibility() -> None:
        if window_visible["value"]:
            hide_window()
        else:
            show_window()

    def quit_app() -> None:
        state["running"] = False
        state["next_tick_at"] = None
        running_event.clear()
        interrupt_event.set()
        stop_event.set()
        stop_hotkeys()
        with cfg_lock:
            cfg["interval_seconds"] = current_interval()
            save_config(cfg)
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        try:
            root.destroy()
        except Exception:
            pass

    def on_close() -> None:
        if tray is not None:
            hide_window()
            log_event("[tray] window minimized to tray (right-click the tray icon to quit)")
        else:
            quit_app()

    if PYSTRAY_AVAILABLE:
        try:
            tray = TrayController(
                on_show_hide=lambda: root.after(0, toggle_window_visibility),
                on_toggle_running=lambda: root.after(0, toggle_running),
                on_quit=lambda: root.after(0, quit_app),
                is_running=lambda: bool(state["running"]),
                is_window_visible=lambda: bool(window_visible["value"]),
            )
            tray.start()
            tray.update_status(False)
            log_event("[tray] system tray icon active (red = stopped, green = running)")
        except Exception as exc:
            tray = None
            log_event(f"[tray] unavailable: {exc}")
    else:
        log_event(
            "[tray] pystray is not installed; closing the window will quit the app. "
            f"Install with `uv add pystray`. ({PYSTRAY_IMPORT_ERROR})"
        )

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
