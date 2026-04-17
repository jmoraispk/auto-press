"""Core automation and vision helpers used by UI/headless runners."""

import time
from pathlib import Path

import pyautogui


MODE_ENTER = "enter"
MODE_CLICK = "click"
MODE_CLICK_ENTER = "click+enter"
MODE_WATCH_RUN = "watch-run"
MODES = [MODE_ENTER, MODE_CLICK, MODE_CLICK_ENTER, MODE_WATCH_RUN]

WORD_PRE_DELAY_SEC = 0.30
WORD_RETRY_DELAY_SEC = 0.30
WORD_POST_DELAY_SEC = 0.30
ENTER_AFTER_WORD_DELAY_SEC = 0.15


def try_import_vision():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        return cv2, np, None
    except ImportError as e:
        return None, None, str(e)


def _require_vision():
    """Return (cv2, np) or raise a helpful dependency error."""
    cv2, np, err = try_import_vision()
    if err:
        raise RuntimeError("Vision deps missing. Install with: uv sync")
    return cv2, np


def load_template_gray(path: str):
    cv2, _ = _require_vision()
    tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if tpl is None:
        raise FileNotFoundError(f"Template unreadable: {path}")
    return tpl


def save_gray_image(path: str, gray_image) -> None:
    cv2, _ = _require_vision()
    ok = cv2.imwrite(path, gray_image)
    if not ok:
        raise RuntimeError(f"Failed to save template image: {path}")


def grab_region_gray(bbox: tuple[int, int, int, int]):
    cv2, np = _require_vision()
    left, top, width, height = bbox
    img = pyautogui.screenshot(region=(left, top, width, height))
    arr = np.array(img)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)


def match_template_score(region_gray, template_gray) -> float:
    cv2, _, err = try_import_vision()
    if err:
        return 0.0
    return float(cv2.minMaxLoc(cv2.matchTemplate(region_gray, template_gray, cv2.TM_CCOEFF_NORMED))[1])


def best_run_match(region_gray, run_templates: list) -> tuple[float, tuple[int, int] | None]:
    """
    Returns (best_score, center_xy_in_region).
    """
    cv2, _ = _require_vision()

    best_score = 0.0
    best_center = None
    for tpl in run_templates:
        res = cv2.matchTemplate(region_gray, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        score = float(max_val)
        if score > best_score:
            h, w = tpl.shape[:2]
            center = (max_loc[0] + (w // 2), max_loc[1] + (h // 2))
            best_score = score
            best_center = center
    return best_score, best_center


def type_word_with_retry(word: str) -> None:
    sent = False
    last_err = None
    for _ in range(2):
        try:
            time.sleep(WORD_PRE_DELAY_SEC)
            pyautogui.typewrite(word)
            sent = True
            break
        except Exception as e:
            last_err = e
            time.sleep(WORD_RETRY_DELAY_SEC)
    if not sent and last_err is not None:
        raise last_err
    time.sleep(WORD_POST_DELAY_SEC)


def do_action(mode: str, click_target: tuple[int, int] | None = None, text_before_enter: str | None = None) -> None:
    if mode == MODE_ENTER:
        pyautogui.press("enter")
        return

    if click_target is None:
        raise ValueError("click_target is required for click-based modes")

    x, y = click_target
    old = pyautogui.position()
    pyautogui.moveTo(x, y, duration=0)
    pyautogui.click()

    if mode == MODE_CLICK_ENTER:
        if text_before_enter:
            type_word_with_retry(text_before_enter)
            time.sleep(ENTER_AFTER_WORD_DELAY_SEC)
        pyautogui.press("enter")

    pyautogui.moveTo(old.x, old.y, duration=0)


def click_point(point: tuple[int, int]) -> None:
    old = pyautogui.position()
    pyautogui.moveTo(point[0], point[1], duration=0)
    pyautogui.click()
    pyautogui.moveTo(old.x, old.y, duration=0)


def parse_bbox(spec: str) -> tuple[int, int, int, int]:
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be 'left,top,width,height'")
    left, top, width, height = map(int, parts)
    if width <= 0 or height <= 0:
        raise ValueError("bbox width and height must be > 0")
    return left, top, width, height


def evaluate_state_for_target(target_cfg: dict, state_detect_enabled: bool, threshold: float, finished_tpl_cache: dict, log_event) -> tuple[bool, float | None, str]:
    if not state_detect_enabled:
        return False, None, "off"

    roi = target_cfg.get("state_roi")
    tpl_path = target_cfg.get("state_template")
    if not roi or not tpl_path:
        return False, None, "not-configured"

    try:
        roi_t = tuple(roi)
        region_gray = grab_region_gray(roi_t)  # type: ignore[arg-type]
        tpl = finished_tpl_cache.get(tpl_path)
        if tpl is None:
            tpl = load_template_gray(tpl_path)
            finished_tpl_cache[tpl_path] = tpl
        score = match_template_score(region_gray, tpl)
        return score >= threshold, score, ("match" if score >= threshold else "no-match")
    except Exception as e:
        log_event(f"[error] state detection failed: {e}")
        return False, None, "error"


def evaluate_run_for_target(target_cfg: dict, run_templates: list, run_threshold: float, log_event) -> tuple[bool, float | None, tuple[int, int] | None, str]:
    run_roi = target_cfg.get("run_roi")
    if not run_roi or not run_templates:
        return False, None, None, "not-configured"

    try:
        run_roi_t = tuple(run_roi)
        left, top, _, _ = run_roi_t
        region_gray = grab_region_gray(run_roi_t)  # type: ignore[arg-type]
        score, center_in_roi = best_run_match(region_gray, run_templates)
        if center_in_roi is None:
            return False, score, None, "no-match"
        abs_center = (left + center_in_roi[0], top + center_in_roi[1])
        return score >= run_threshold, score, abs_center, ("match" if score >= run_threshold else "no-match")
    except Exception as e:
        log_event(f"[error] run-watch failed: {e}")
        return False, None, None, "error"


def load_run_templates(template_paths: list[str], base_dir: Path) -> list:
    templates = []
    for rel in template_paths:
        p = base_dir / rel
        if p.exists():
            try:
                templates.append(load_template_gray(str(p)))
            except Exception:
                continue
    return templates


def choose_run_first_action(run_match: bool, state_match: bool) -> str:
    """
    Returns one of: run-click, state-action, default-action.
    """
    if run_match:
        return "run-click"
    if state_match:
        return "state-action"
    return "default-action"
