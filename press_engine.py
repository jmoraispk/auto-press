"""Rule-based screen scanning engine."""

from __future__ import annotations

import sys
import time

from PIL import ImageGrab

from press_core import MODE_CLICK, MODE_CLICK_ENTER, do_action, load_template_gray, try_import_vision
from press_store import (
    ACTION_CLICK,
    ACTION_CLICK_TYPE_ENTER,
    MATCHER_COLOR,
    MATCHER_TEMPLATE,
    resolve_template_path,
)


ACTION_SETTLE_DELAY_SEC = 0.20

# A color rule clicks every contiguous patch of the captured RGB whose
# pixel area is at least this fraction of the originally dragged region.
# Self-calibrates: drag the whole button -> noisy small accents fail this.
COLOR_AREA_RATIO = 0.5
# Cap the number of clicks per tick so a freak case (target colour appears
# in dozens of places) doesn't lock the cursor for minutes.
COLOR_MAX_CLICKS = 5


def ensure_vision() -> tuple[object, object]:
    cv2, np, err = try_import_vision()
    if err:
        raise RuntimeError("Vision deps missing. Install with: uv sync")
    return cv2, np


def _pin_thread_v2_dpi() -> None:
    """Ensure the current thread is PER_MONITOR_AWARE_V2.

    Windows inherits thread DPI context from the process default at thread
    creation. If a background thread was started before Qt/main set V2,
    GetSystemMetrics and ImageGrab disagree with V2-aware callers. Pinning V2
    here is idempotent and cheap; it guarantees every capture path agrees on
    physical pixels.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def _virtual_screen_origin() -> tuple[int, int]:
    """Top-left screen coordinate of the virtual desktop (can be negative on Windows)."""
    if sys.platform.startswith("win"):
        import ctypes

        _pin_thread_v2_dpi()
        gm = ctypes.windll.user32.GetSystemMetrics
        return gm(76), gm(77)  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
    return (0, 0)


def _grab_screen(region: tuple[int, int, int, int] | None):
    _pin_thread_v2_dpi()
    bbox = None
    if region:
        left, top, width, height = region
        bbox = (left, top, left + width, top + height)
    try:
        return ImageGrab.grab(bbox=bbox, all_screens=True)
    except TypeError:
        return ImageGrab.grab(bbox=bbox)


def capture_screen_rgb(region: tuple[int, int, int, int] | None = None):
    """Full screen / region as an HxWx3 RGB ndarray (uint8). Same coord rules as gray."""
    _cv2, np = ensure_vision()
    return np.array(_grab_screen(region).convert("RGB"))


def capture_screen_gray(region: tuple[int, int, int, int] | None = None):
    cv2, _np = ensure_vision()
    return cv2.cvtColor(capture_screen_rgb(region), cv2.COLOR_RGB2GRAY)


def dominant_rgb(rgb_array) -> tuple[int, int, int]:
    """Return the most-frequent (r, g, b) triple in an HxWx3 RGB array.

    Mode rather than mean: the centre of a button is one solid colour while
    anti-aliased edges smear into many unique colours. The mean of that mix
    is a colour that often does not exist anywhere, which makes an exact
    match scan pointless.
    """
    _cv2, np = ensure_vision()
    flat = rgb_array.reshape(-1, 3)
    if flat.size == 0:
        return (0, 0, 0)
    unique, counts = np.unique(flat, axis=0, return_counts=True)
    r, g, b = unique[counts.argmax()]
    return int(r), int(g), int(b)


def build_runtime_rules(config: dict) -> list[dict]:
    runtime_rules: list[dict] = []
    for rule in sorted(config.get("rules", []), key=lambda item: int(item.get("priority", 9999))):
        if not rule.get("enabled"):
            continue
        matcher = rule.get("matcher", MATCHER_TEMPLATE)
        if matcher == MATCHER_COLOR:
            color = rule.get("color_rgb")
            area = int(rule.get("color_capture_area") or 0)
            if not color or area <= 0:
                continue
            runtime_rules.append({**rule})
            continue
        # template path
        template_path = resolve_template_path(rule.get("template_path"))
        if template_path is None or not template_path.exists():
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


def _find_color_matches(
    search_rgb,
    target_rgb: tuple[int, int, int],
    capture_area: int,
    offset_x: int,
    offset_y: int,
) -> list[tuple[float, tuple[int, int]]]:
    """Click every contiguous run of pixels at exactly target_rgb that's at least
    half the size of the originally captured region. Returns up to N matches in
    descending area order; score is a synthetic 1.0 (color matches don't have a
    correlation score).
    """
    cv2, _np = ensure_vision()
    if search_rgb is None or search_rgb.size == 0:
        return []
    # cv2.inRange wants Scalar bounds (a 3-tuple), not a (3,) ndarray; passing
    # the latter trips a "sizes do not match" error on some OpenCV builds.
    bound = (int(target_rgb[0]), int(target_rgb[1]), int(target_rgb[2]))
    mask = cv2.inRange(search_rgb, bound, bound)  # 255 where exact match
    if not mask.any():
        return []
    min_area = max(1, int(capture_area * COLOR_AREA_RATIO))
    num_labels, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    survivors: list[tuple[int, tuple[int, int]]] = []
    for i in range(1, num_labels):  # 0 is the background label
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        cx, cy = centroids[i]
        survivors.append((area, (offset_x + int(round(cx)), offset_y + int(round(cy)))))
    survivors.sort(key=lambda item: item[0], reverse=True)
    return [(1.0, center) for _, center in survivors[:COLOR_MAX_CLICKS]]


def find_rule_matches(frame, runtime_rule: dict) -> list[tuple[float, tuple[int, int]]]:
    """Evaluate one rule.

    `frame` is the cached full-virtual-screen capture: gray for template
    rules, RGB for color rules. If the rule carries its own ``search_region``
    that region is re-captured so screen-coord slicing stays correct on
    monitors at negative virtual-screen coordinates.
    """
    region = runtime_rule.get("search_region")
    matcher = runtime_rule.get("matcher", MATCHER_TEMPLATE)

    if matcher == MATCHER_COLOR:
        if region:
            search_rgb = capture_screen_rgb(region)
            offset_x, offset_y = int(region[0]), int(region[1])
        else:
            if frame is None:
                frame = capture_screen_rgb()
            search_rgb = frame
            offset_x, offset_y = _virtual_screen_origin()
        return _find_color_matches(
            search_rgb,
            tuple(runtime_rule["color_rgb"]),
            int(runtime_rule.get("color_capture_area") or 0),
            offset_x,
            offset_y,
        )

    # template
    if region:
        search_gray = capture_screen_gray(region)
        offset_x, offset_y = int(region[0]), int(region[1])
    else:
        if frame is None:
            frame = capture_screen_gray()
        search_gray = frame
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
    # Color rules want RGB, template rules want gray; lazily compute either.
    shared_rgb = None
    shared_gray = None
    results: list[dict] = []
    actions: list[dict] = []

    for rule in runtime_rules:
        matcher = rule.get("matcher", MATCHER_TEMPLATE)
        if not rule.get("search_region"):
            if matcher == MATCHER_COLOR:
                if shared_rgb is None:
                    shared_rgb = capture_screen_rgb()
                shared = shared_rgb
            else:
                if shared_gray is None:
                    shared_gray = capture_screen_gray()
                shared = shared_gray
        else:
            shared = None  # find_rule_matches captures its own region
        matches = find_rule_matches(shared, rule)
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
