"""Core click / type / vision helpers used by the engine and UI."""

import sys
import time

import pyautogui


MODE_CLICK = "click"
MODE_CLICK_ENTER = "click+enter"


def _pin_thread_v2_dpi() -> None:
    """Ensure current thread is PER_MONITOR_AWARE_V2 before any Win32 pixel call.

    pyautogui uses SetCursorPos / mouse_event under the hood, which both
    interpret coordinates in the CURRENT THREAD's DPI context. If the thread
    has drifted off V2 (stale inheritance, another library resetting it),
    physical pixel coords land on the wrong spot. Idempotent, nanoseconds.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

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
    _pin_thread_v2_dpi()
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


# ---- bridge primitives ---------------------------------------------------

def click_point(point: tuple[int, int]) -> None:
    """Move the cursor and click — without restoring the previous cursor pos.

    do_action snaps back so unattended automation looks invisible. The bridge
    instead keeps the cursor at the click target so the pasted text lands in
    the right field, since some apps refuse focus until the next user input.
    """
    _pin_thread_v2_dpi()
    x, y = int(point[0]), int(point[1])
    pyautogui.moveTo(x, y, duration=0)
    pyautogui.click()


def get_clipboard_text() -> str:
    """Best-effort current clipboard text (empty string if unavailable)."""
    import pyperclip  # ships transitively with pyautogui
    try:
        return pyperclip.paste() or ""
    except Exception:
        return ""


def set_clipboard_text(text: str) -> None:
    import pyperclip
    try:
        pyperclip.copy(text)
    except Exception:
        pass


def paste_text_and_enter(
    text: str,
    pre_paste_delay_ms: int = 150,
    clipboard_restore_delay_ms: int = 500,
) -> None:
    """Paste ``text`` via Ctrl+V then press Enter, restoring the prior
    clipboard. Empty text is allowed: in that case the clipboard is left
    untouched and we just press Enter — useful when the user already
    composed the message in the target field and only needs the submit
    keystroke from the bridge.

    Pre-delay lets the focused field settle after the click; the restore
    delay covers slow-clipboard apps that otherwise read the new value back
    into themselves before we put the original contents back.
    """
    _pin_thread_v2_dpi()
    if pre_paste_delay_ms > 0:
        time.sleep(pre_paste_delay_ms / 1000.0)
    if text:
        saved = get_clipboard_text()
        set_clipboard_text(text)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.05)
        pyautogui.press("enter")
        if clipboard_restore_delay_ms > 0:
            time.sleep(clipboard_restore_delay_ms / 1000.0)
        set_clipboard_text(saved)
    else:
        pyautogui.press("enter")
