"""System tray indicator for the v2 UI.

Provides a small wrapper around `pystray` so the rest of the UI can show a
running/stopped indicator and offer minimize-to-tray behaviour without caring
about thread management.

The tray runs on its own daemon thread; menu callbacks are invoked from that
thread, so consumers should marshal back to the Tk main thread (e.g. via
`root.after(0, ...)`) before touching widgets.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

try:
    import pystray
    from PIL import Image, ImageDraw

    PYSTRAY_AVAILABLE = True
    PYSTRAY_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - import guard
    PYSTRAY_AVAILABLE = False
    PYSTRAY_IMPORT_ERROR = exc


_RUNNING_COLOR = (46, 125, 50)   # green, matches the in-app "Running" status
_STOPPED_COLOR = (211, 47, 47)   # red, matches the in-app "Stopped" status
_OUTLINE_COLOR = (20, 20, 20, 255)


def _make_icon_image(color: tuple[int, int, int]) -> "Image.Image":
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, size - 6, size - 6), fill=color, outline=_OUTLINE_COLOR, width=2)
    return image


class TrayController:
    """Owns the pystray icon and exposes a small UI-friendly interface."""

    def __init__(
        self,
        *,
        on_show_hide: Callable[[], None],
        on_toggle_running: Callable[[], None],
        on_quit: Callable[[], None],
        is_running: Callable[[], bool],
        is_window_visible: Callable[[], bool],
        app_name: str = "Auto Press",
    ) -> None:
        if not PYSTRAY_AVAILABLE:
            raise RuntimeError(
                "pystray is not installed. Run `uv add pystray` (or `uv sync`)."
            )

        self._on_show_hide = on_show_hide
        self._on_toggle_running = on_toggle_running
        self._on_quit = on_quit
        self._is_running = is_running
        self._is_window_visible = is_window_visible
        self._app_name = app_name

        self._icon_running = _make_icon_image(_RUNNING_COLOR)
        self._icon_stopped = _make_icon_image(_STOPPED_COLOR)

        menu = pystray.Menu(
            pystray.MenuItem(
                lambda _item: "Hide window" if self._is_window_visible() else "Show window",
                self._handle_show_hide,
                default=True,
            ),
            pystray.MenuItem(
                lambda _item: "Stop" if self._is_running() else "Start",
                self._handle_toggle_running,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Auto Press", self._handle_quit),
        )

        self._icon = pystray.Icon(
            "auto-press",
            self._icon_stopped,
            f"{app_name} - Stopped",
            menu=menu,
        )

        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._icon.run, name="auto-press-tray", daemon=True)
        self._thread.start()

    def update_status(self, running: bool) -> None:
        self._icon.icon = self._icon_running if running else self._icon_stopped
        self._icon.title = f"{self._app_name} - {'Running' if running else 'Stopped'}"
        self.refresh_menu()

    def refresh_menu(self) -> None:
        try:
            self._icon.update_menu()
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self._icon.visible = False
        except Exception:
            pass
        try:
            self._icon.stop()
        except Exception:
            pass

    def _handle_show_hide(self, _icon=None, _item=None) -> None:
        self._on_show_hide()

    def _handle_toggle_running(self, _icon=None, _item=None) -> None:
        self._on_toggle_running()

    def _handle_quit(self, _icon=None, _item=None) -> None:
        self._on_quit()
