"""Rule-based screen scanning engine for the simplified v2 UI."""

from __future__ import annotations

import time

import pyautogui

from press_core import MODE_CLICK, MODE_CLICK_ENTER, best_run_match, do_action, load_template_gray, try_import_vision
from press_v2_store import ACTION_CLICK, ACTION_CLICK_TYPE_ENTER, resolve_template_path


def ensure_vision() -> tuple[object, object]:
    cv2, np, err = try_import_vision()
    if err:
        raise RuntimeError("Vision deps missing. Install with: uv sync --extra vision")
    return cv2, np


def capture_screen_gray(region: tuple[int, int, int, int] | None = None):
    cv2, np = ensure_vision()
    img = pyautogui.screenshot(region=region)
    arr = np.array(img)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)


def build_runtime_rules(config: dict) -> list[dict]:
    runtime_rules: list[dict] = []
    for rule in sorted(config.get("rules", []), key=lambda item: int(item.get("priority", 9999))):
        template_path = resolve_template_path(rule.get("template_path"))
        if not rule.get("enabled") or template_path is None or not template_path.exists():
            continue
        runtime_rules.append(
            {
                **rule,
                "template_gray": load_template_gray(str(template_path)),
            }
        )
    return runtime_rules


def evaluate_rule_on_frame(frame_gray, runtime_rule: dict) -> tuple[float, tuple[int, int] | None]:
    region = runtime_rule.get("search_region")
    search_gray = frame_gray
    offset_x = 0
    offset_y = 0
    if region:
        left, top, width, height = [int(v) for v in region]
        offset_x = left
        offset_y = top
        search_gray = frame_gray[top : top + height, left : left + width]
    score, center = best_run_match(search_gray, [runtime_rule["template_gray"]])
    if center is None:
        return score, None
    return score, (offset_x + center[0], offset_y + center[1])


def evaluate_rules(runtime_rules: list[dict], cooldowns: dict[str, float], now: float | None = None) -> tuple[list[dict], dict | None]:
    if not runtime_rules:
        return [], None
    frame_gray = capture_screen_gray()
    ts = time.time() if now is None else now
    results: list[dict] = []
    chosen: dict | None = None

    for rule in runtime_rules:
        score, center = evaluate_rule_on_frame(frame_gray, rule)
        matched = center is not None and score >= float(rule.get("threshold", 0.90))
        last_hit = cooldowns.get(rule["id"], 0.0)
        cooldown_ready = (ts - last_hit) >= float(rule.get("cooldown_seconds", 0.0))
        result = {
            "id": rule["id"],
            "name": rule["name"],
            "score": score,
            "matched": matched,
            "cooldown_ready": cooldown_ready,
            "center": center,
            "action": rule.get("action", ACTION_CLICK),
            "text": rule.get("text", "continue"),
        }
        results.append(result)
        if chosen is None and matched and cooldown_ready:
            chosen = result
    return results, chosen


def execute_match(match: dict) -> None:
    center = match.get("center")
    if center is None:
        return
    if match.get("action") == ACTION_CLICK_TYPE_ENTER:
        do_action(MODE_CLICK_ENTER, center, text_before_enter=match.get("text") or "continue")
    else:
        do_action(MODE_CLICK, center)
