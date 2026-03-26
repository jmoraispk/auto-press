"""Rule-based screen scanning engine for the simplified v2 UI."""

from __future__ import annotations

import time

import pyautogui

from press_core import MODE_CLICK, MODE_CLICK_ENTER, do_action, load_template_gray, try_import_vision
from press_v2_store import ACTION_CLICK, ACTION_CLICK_TYPE_ENTER, resolve_template_path


ACTION_SETTLE_DELAY_SEC = 0.20


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


def find_rule_matches(frame_gray, runtime_rule: dict) -> list[tuple[float, tuple[int, int]]]:
    cv2, np = ensure_vision()
    region = runtime_rule.get("search_region")
    search_gray = frame_gray
    offset_x = 0
    offset_y = 0
    if region:
        left, top, width, height = [int(v) for v in region]
        offset_x = left
        offset_y = top
        search_gray = frame_gray[top : top + height, left : left + width]
    template_gray = runtime_rule["template_gray"]
    result = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    threshold = float(runtime_rule.get("threshold", 0.90))
    ys, xs = np.where(result >= threshold)
    if len(xs) == 0:
        return []

    template_h, template_w = template_gray.shape[:2]
    candidates = sorted(
        [(float(result[y, x]), int(x), int(y)) for x, y in zip(xs.tolist(), ys.tolist())],
        key=lambda item: item[0],
        reverse=True,
    )

    matches: list[tuple[float, tuple[int, int]]] = []
    for score, x, y in candidates:
        center = (x + (template_w // 2), y + (template_h // 2))
        if any(abs(center[0] - chosen[1][0]) < template_w and abs(center[1] - chosen[1][1]) < template_h for chosen in matches):
            continue
        matches.append((score, (offset_x + center[0], offset_y + center[1])))
    return matches


def evaluate_rule_on_frame(frame_gray, runtime_rule: dict) -> tuple[float, tuple[int, int] | None]:
    matches = find_rule_matches(frame_gray, runtime_rule)
    if not matches:
        return 0.0, None
    return matches[0]


def evaluate_rules(runtime_rules: list[dict]) -> tuple[list[dict], list[dict]]:
    if not runtime_rules:
        return [], []
    frame_gray = capture_screen_gray()
    results: list[dict] = []
    actions: list[dict] = []

    for rule in runtime_rules:
        matches = find_rule_matches(frame_gray, rule)
        best_score = matches[0][0] if matches else 0.0
        result = {
            "id": rule["id"],
            "name": rule["name"],
            "score": best_score,
            "matched": bool(matches),
            "match_count": len(matches),
            "centers": [center for _, center in matches],
            "action": rule.get("action", ACTION_CLICK),
            "text": rule.get("text", "continue"),
        }
        results.append(result)
        for score, center in matches:
            actions.append(
                {
                    "id": rule["id"],
                    "name": rule["name"],
                    "score": score,
                    "center": center,
                    "action": rule.get("action", ACTION_CLICK),
                    "text": rule.get("text", "continue"),
                }
            )
    return results, actions


def execute_match(match: dict) -> None:
    center = match.get("center")
    if center is None:
        return
    if match.get("action") == ACTION_CLICK_TYPE_ENTER:
        do_action(MODE_CLICK_ENTER, center, text_before_enter=match.get("text") or "continue")
    else:
        do_action(MODE_CLICK, center)


def execute_matches(matches: list[dict], delay_seconds: float = ACTION_SETTLE_DELAY_SEC) -> None:
    for idx, match in enumerate(matches):
        execute_match(match)
        if idx < len(matches) - 1 and delay_seconds > 0:
            time.sleep(delay_seconds)
