"""Simplified v2 UI for rule-based screen scanning automation."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter.scrolledtext import ScrolledText

import pyautogui

from press_core import save_gray_image
from press_v2_engine import build_runtime_rules, capture_screen_gray, ensure_vision, evaluate_rule_on_frame, evaluate_rules, execute_match
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
            state["cooldowns"].pop(removed["id"], None)
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

    ctk.CTkButton(top, text="Start / Stop", command=lambda: toggle_running(), width=120).pack(side="left", padx=(0, 8))
    interval_frame = ctk.CTkFrame(top, fg_color="transparent")
    interval_frame.pack(side="left")
    ctk.CTkLabel(interval_frame, text="Interval (s):", font=FONT, text_color=MUTED).pack(side="left")
    ctk.CTkEntry(interval_frame, textvariable=interval_var, width=80).pack(side="left", padx=(6, 14))
    countdown_label = ctk.CTkLabel(top, textvariable=countdown_var, font=("Consolas", 18, "bold"), text_color=MUTED)
    status_label = ctk.CTkLabel(top, textvariable=status_var, font=FONT, text_color=STATUS_STOPPED)
    status_label.pack(side="left", padx=(0, 14))
    ctk.CTkLabel(top, textvariable=action_status_var, font=FONT_SMALL, text_color=MUTED).pack(side="left")

    body = ctk.CTkFrame(root)
    body.pack(fill="both", expand=True, padx=14, pady=(0, 8))

    left = ctk.CTkFrame(body)
    left.pack(side="left", fill="y", padx=(0, 8), pady=8)
    right = ctk.CTkFrame(body)
    right.pack(side="left", fill="both", expand=True, pady=8)

    ctk.CTkLabel(left, text="Rules", font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=12, pady=(12, 8))
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

    ctk.CTkLabel(editor, text="Rule Editor", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 10))

    basics_frame = ctk.CTkFrame(editor, fg_color="transparent")
    basics_frame.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(basics_frame, text="Name", font=FONT_SMALL, text_color=MUTED).grid(row=0, column=0, sticky="w")
    ctk.CTkLabel(basics_frame, text="Action", font=FONT_SMALL, text_color=MUTED).grid(row=0, column=2, sticky="w")
    ctk.CTkLabel(basics_frame, text="Text (optional)", font=FONT_SMALL, text_color=MUTED).grid(row=0, column=3, sticky="w")
    ctk.CTkEntry(basics_frame, textvariable=name_var, width=180).grid(row=1, column=0, sticky="w", padx=(0, 12))
    ctk.CTkCheckBox(basics_frame, text="Enabled", variable=enabled_var).grid(row=1, column=1, sticky="w", padx=(0, 12))
    action_menu = ctk.CTkOptionMenu(basics_frame, values=ACTION_TYPES, variable=action_var, command=update_action_fields, width=150)
    action_menu.grid(row=1, column=2, sticky="w", padx=(0, 12))
    text_entry = ctk.CTkEntry(basics_frame, textvariable=text_var, width=160)
    text_entry.grid(row=1, column=3, sticky="w")

    tuning_frame = ctk.CTkFrame(editor, fg_color="transparent")
    tuning_frame.pack(fill="x", pady=(0, 12))
    ctk.CTkLabel(tuning_frame, text="Threshold", font=FONT_SMALL, text_color=MUTED).grid(row=0, column=0, sticky="w")
    ctk.CTkEntry(tuning_frame, textvariable=threshold_var, width=90).grid(row=1, column=0, sticky="w", padx=(0, 12))

    template_section = ctk.CTkFrame(editor)
    template_section.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(template_section, text="Template", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
    template_row = ctk.CTkFrame(template_section, fg_color="transparent")
    template_row.pack(fill="x", padx=12, pady=(0, 10))
    template_choice_menu = ctk.CTkOptionMenu(template_row, values=[""], variable=template_choice_var, width=220)
    template_choice_menu.pack(side="left", padx=(0, 8))
    ctk.CTkButton(template_row, text="Use Existing", command=use_selected_template, width=110).pack(side="left", padx=(0, 8))
    ctk.CTkButton(template_row, text="Capture Pattern", command=capture_template, width=130).pack(side="left")

    search_section = ctk.CTkFrame(editor)
    search_section.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(search_section, text="Search Scope", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
    search_row = ctk.CTkFrame(search_section, fg_color="transparent")
    search_row.pack(fill="x", padx=12, pady=(0, 10))
    ctk.CTkLabel(search_row, textvariable=region_var, font=FONT_SMALL, text_color=MUTED).pack(side="left", padx=(0, 12))
    ctk.CTkButton(search_row, text="Capture Search Region", command=capture_search_region, width=160).pack(side="left", padx=(0, 8))
    ctk.CTkButton(search_row, text="Use Whole Screen", command=use_whole_screen, width=130).pack(side="left")

    editor_actions = ctk.CTkFrame(editor, fg_color="transparent")
    editor_actions.pack(fill="x", pady=(4, 0))
    ctk.CTkButton(editor_actions, text="Test Match", command=test_selected_rule, width=100).pack(side="left", padx=(0, 8))
    ctk.CTkButton(editor_actions, text="Save Rule", command=save_selected_rule, width=100).pack(side="left")

    log_frame = ctk.CTkFrame(root)
    log_frame.pack(fill="x", padx=14, pady=(0, 14))
    ctk.CTkLabel(log_frame, text="Log", font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=12, pady=(10, 8))
    log_box = ScrolledText(log_frame, height=14, wrap="word")
    log_box.pack(fill="x", padx=12, pady=(0, 12))
    log_box.configure(state="disabled")

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
                    for action in actions:
                        execute_match(action)
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

    update_action_fields()
    refresh_template_choices()
    refresh_rule_list(0 if cfg.get("rules") else None)
    update_runtime_rules()
    set_running_status(False)
    update_top_row_visibility()
    update_timer()
    log_event(f"[ready] loaded {V2_CONFIG_PATH}")

    def on_close() -> None:
        state["running"] = False
        state["next_tick_at"] = None
        running_event.clear()
        interrupt_event.set()
        stop_event.set()
        with cfg_lock:
            cfg["interval_seconds"] = current_interval()
            save_config(cfg)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
