"""Rule-based screen scanning engine."""

from __future__ import annotations

import sys
import time

from PIL import ImageGrab

from press_core import MODE_CLICK, MODE_CLICK_ENTER, do_action, load_template_gray, try_import_vision
from press_store import ACTION_CLICK, ACTION_CLICK_TYPE_ENTER, resolve_template_path


ACTION_SETTLE_DELAY_SEC = 0.20


def ensure_vision() -> tuple[object, object]:
    cv2, np, err = try_import_vision()
    if err:
        raise RuntimeError("Vision deps missing. Install with: uv sync")
    return cv2, np


def _virtual_screen_origin() -> tuple[int, int]:
    """Top-left screen coordinate of the virtual desktop (can be negative on Windows)."""
    if sys.platform.startswith("win"):
        import ctypes

        gm = ctypes.windll.user32.GetSystemMetrics
        return gm(76), gm(77)  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
    return (0, 0)


def _grab_screen(region: tuple[int, int, int, int] | None):
    bbox = None
    if region:
        left, top, width, height = region
        bbox = (left, top, left + width, top + height)
    try:
        return ImageGrab.grab(bbox=bbox, all_screens=True)
    except TypeError:
        return ImageGrab.grab(bbox=bbox)


def capture_screen_gray(region: tuple[int, int, int, int] | None = None):
    cv2, np = ensure_vision()
    arr = np.array(_grab_screen(region).convert("RGB"))
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


def _find_matches_in(search_gray, template_gray, threshold: float, offset_x: int, offset_y: int) -> list[tuple[float, tuple[int, int]]]:
    cv2, np = ensure_vision()
    if search_gray is None or search_gray.size == 0:
        return []
    template_h, template_w = template_gray.shape[:2]
    if search_gray.shape[0] < template_h or search_gray.shape[1] < template_w:
        return []
    result = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(result >= threshold)
    if len(xs) == 0:
        return []

    candidates = sorted(
        [(float(result[y, x]), int(x), int(y)) for x, y in zip(xs.tolist(), ys.tolist())],
        key=lambda item: item[0],
        reverse=True,
    )
    matches: list[tuple[float, tuple[int, int]]] = []
    for score, x, y in candidates:
        abs_center = (offset_x + x + (template_w // 2), offset_y + y + (template_h // 2))
        if any(
            abs(abs_center[0] - chosen[1][0]) < template_w
            and abs(abs_center[1] - chosen[1][1]) < template_h
            for chosen in matches
        ):
            continue
        matches.append((score, abs_center))
    return matches


def find_rule_matches(frame_gray, runtime_rule: dict) -> list[tuple[float, tuple[int, int]]]:
    """Evaluate one rule. `frame_gray` is assumed rooted at the virtual screen origin.

    If the rule carries its own `search_region`, the region is re-captured so
    negative/secondary-monitor coordinates don't break array slicing.
    """
    region = runtime_rule.get("search_region")
    if region:
        search_gray = capture_screen_gray(region)
        offset_x, offset_y = int(region[0]), int(region[1])
    else:
        if frame_gray is None:
            frame_gray = capture_screen_gray()
        search_gray = frame_gray
        offset_x, offset_y = _virtual_screen_origin()
    return _find_matches_in(
        search_gray,
        runtime_rule["template_gray"],
        float(runtime_rule.get("threshold", 0.90)),
        offset_x,
        offset_y,
    )


def evaluate_rule_on_frame(frame_gray, runtime_rule: dict) -> tuple[float, tuple[int, int] | None]:
    matches = find_rule_matches(frame_gray, runtime_rule)
    if not matches:
        return 0.0, None
    return matches[0]


def evaluate_rules(runtime_rules: list[dict]) -> tuple[list[dict], list[dict]]:
    if not runtime_rules:
        return [], []
    # Only pay for the full-virtual-screen grab if at least one rule needs it.
    shared_full = None
    results: list[dict] = []
    actions: list[dict] = []

    for rule in runtime_rules:
        if not rule.get("search_region") and shared_full is None:
            shared_full = capture_screen_gray()
        matches = find_rule_matches(shared_full, rule)
        best_score = matches[0][0] if matches else 0.0
        results.append(
            {
                "id": rule["id"],
                "name": rule["name"],
                "score": best_score,
                "matched": bool(matches),
                "match_count": len(matches),
                "centers": [center for _, center in matches],
                "action": rule.get("action", ACTION_CLICK),
                "text": rule.get("text", "continue"),
            }
        )
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
