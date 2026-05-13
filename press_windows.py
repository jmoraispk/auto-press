"""Win32 enumeration for auto-detecting Cursor windows.

The bridge configures each Cursor window manually today: drag-capture a
bounding box per window. With many monitors and frequent re-tiling that
gets old fast. Windows already knows where every visible top-level
window is — calling EnumWindows + GetWindowRect skips the manual step
entirely, and the rects come back in physical pixels when the process
is pinned to PER_MONITOR_AWARE_V2 (which we already do at startup).

This module is Windows-only. On any other platform the public function
returns an empty list, so callers can ship the feature without
guarding the import.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

IS_WINDOWS = sys.platform.startswith("win")


def _pin_thread_v2_dpi() -> None:
    """Match the rest of the engine: tell Windows this thread renders /
    queries coordinates in physical pixels, regardless of system DPI
    scaling. Idempotent; cheap to call on every detection pass."""
    if not IS_WINDOWS:
        return
    try:
        # PER_MONITOR_AWARE_V2 = -4 per SetThreadDpiAwarenessContext docs
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def _short_label(title: str) -> str:
    """Trim Cursor's repetitive " - Cursor" suffix so the auto-generated
    window name reads naturally — "Polish README — auto-press" instead
    of "Polish README — auto-press — Cursor"."""
    suffix = " - Cursor"
    if title.endswith(suffix):
        title = title[: -len(suffix)]
    # Some Cursor titles are "PROJECT - filename - Cursor"; the
    # truncated version above is plenty.
    return title.strip() or "Cursor"


def list_cursor_windows() -> list[dict]:
    """Enumerate visible Cursor windows on the current desktop.

    Each entry:
        {
          "title": <full window title>,
          "name": <short human label derived from title>,
          "region": [x, y, w, h]   # physical px
          "hwnd": <Win32 handle, int>
        }

    Filters out hidden windows, minimised windows, ones with no
    "Cursor" substring in the title, and ones with degenerate (<200 px)
    width or height. Returns [] on non-Windows platforms.
    """
    if not IS_WINDOWS:
        return []

    _pin_thread_v2_dpi()
    user32 = ctypes.windll.user32
    out: list[dict] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
    )

    def cb(hwnd, _lparam):
        # Each EnumWindows callback runs synchronously on this thread,
        # so it's safe to mutate `out` directly. The closure also keeps
        # the WNDENUMPROC reference alive — without that, ctypes would
        # GC it mid-iteration on some Pythons.
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.IsIconic(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if "Cursor" not in title:
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = int(rect.right) - int(rect.left)
            h = int(rect.bottom) - int(rect.top)
            if w < 200 or h < 200:
                return True
            out.append(
                {
                    "title": title,
                    "name": _short_label(title),
                    "region": [int(rect.left), int(rect.top), int(w), int(h)],
                    "hwnd": int(hwnd),
                }
            )
        except Exception:
            # One pathological HWND shouldn't kill the whole sweep.
            pass
        return True

    proc = WNDENUMPROC(cb)
    try:
        user32.EnumWindows(proc, 0)
    except Exception:
        # If EnumWindows itself blows up, return whatever we got so far
        # rather than crashing the bridge / UI.
        pass
    # Sort left-to-right, then top-to-bottom — matches how the user
    # would scan a tiled monitor and makes auto-generated #1, #2, etc.
    # names line up with what they see.
    out.sort(key=lambda w: (w["region"][0], w["region"][1]))
    return out
