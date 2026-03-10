"""Headless runner for click/enter automation."""

from datetime import datetime
import time

import pyautogui

from press_core import (
    MODE_CLICK,
    MODE_CLICK_ENTER,
    MODE_ENTER,
    do_action,
    grab_region_gray,
    load_template_gray,
    match_template_score,
)


MODE_LABELS = {
    MODE_ENTER: "Enter Only",
    MODE_CLICK: "Click Only",
    MODE_CLICK_ENTER: "Click + Enter",
}


def calibrate_point_hover_console() -> tuple[int, int]:
    print("\nCalibration (hover):")
    print("Hover your mouse over the exact spot to click.")
    input("Press Enter in this console to capture the current mouse position...")
    pt = pyautogui.position()
    print(f"Captured target: x={pt.x}, y={pt.y}\n")
    return pt.x, pt.y


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
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    if mode == "watch-run":
        raise SystemExit("Headless watch-run is not supported. Use UI mode.")

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
            print(f"[state] enabled bbox={state_bbox}, word={state_word!r}", flush=True)

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
                fin_score = match_template_score(region_gray, finished_tpl)
                if fin_score >= state_threshold:
                    inject_text = state_word
                    print(f"[state] match (score={fin_score:.3f})", flush=True)
                else:
                    print(
                        f"[state] no-match (score={fin_score:.3f}); fallback click+enter",
                        flush=True,
                    )
            elif mode == MODE_CLICK_ENTER:
                reason = "disabled" if not state_detect else "not configured"
                print(f"[state] no-match (reason={reason}); fallback click+enter", flush=True)

            do_action(mode, (x, y) if (x is not None and y is not None) else None, text_before_enter=inject_text)
            time.sleep(seconds)
    except KeyboardInterrupt:
        print("\nStopped.")
