"""Auto Press — Fluent Design UI entrypoint (experimental).

The stable tkinter UI is still the daily driver; run `main_press.py` for that.
This file boots the Fluent / QFluentWidgets draft.
"""

import argparse
import os
import signal
import sys

# Force PER_MONITOR_AWARE_V2 before any Qt / PIL import so every thread in
# the process agrees on physical pixel coordinates. Without this, Windows'
# app-compat shim leaves python.exe at V1, which gives ImageGrab and
# GetSystemMetrics an inconsistent virtual-screen origin on mixed-DPI setups.
if sys.platform.startswith("win"):
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from press_ui_fluent import MainWindow  # noqa: E402


def _install_sigint(app: QApplication, window: MainWindow) -> QTimer:
    """Let Ctrl+C kill the app cleanly.

    Qt's C event loop doesn't yield to Python often enough for a SIGINT
    handler to run. A 100 ms no-op timer forces Python bytecode to execute,
    which is where signal handlers are dispatched.
    """

    def handler(*_):
        print("\n[ctrl+c] shutting down...", flush=True)
        try:
            window._quit_app()
        except Exception:
            app.quit()

    signal.signal(signal.SIGINT, handler)
    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(lambda: None)
    timer.start()
    return timer


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto Press — Fluent Design draft."
    )
    parser.add_argument(
        "seconds",
        nargs="?",
        type=float,
        default=10.0,
        help="Default scan interval in seconds. Default: 10",
    )
    args = parser.parse_args()
    if args.seconds <= 0:
        raise SystemExit("seconds must be > 0")

    app = QApplication(sys.argv)
    app.setApplicationName("Auto Press")
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(initial_seconds=float(args.seconds))
    window.show()
    _keepalive = _install_sigint(app, window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
