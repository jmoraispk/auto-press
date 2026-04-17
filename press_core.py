"""Core click / type / vision helpers used by the engine and UI."""

import time

import pyautogui


MODE_CLICK = "click"
MODE_CLICK_ENTER = "click+enter"

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


def do_action(mode: str, click_target: tuple[int, int], text_before_enter: str | None = None) -> None:
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
