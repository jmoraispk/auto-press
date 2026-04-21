"""Auto Press — PySide6 (Qt) UI entrypoint.

This is the experimental/second-draft UI. The stable tkinter UI still lives
in main_press.py + press_ui.py. Run this one as `uv run main_qt.py`.
"""

import argparse
import os
import sys

# Force PER_MONITOR_AWARE_V2 before any Qt / PIL import so every thread in
# the process agrees on physical pixel coordinates.
if sys.platform.startswith("win"):
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")

from PySide6.QtWidgets import QApplication  # noqa: E402

from press_ui_qt import MainWindow  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto Press: screen-scanning automation for Cursor and other desktop apps."
    )
    parser.add_argument(
        "seconds",
        nargs="?",
        type=float,
        default=10.0,
        help="Default scan interval in seconds. Can also be changed in the UI. Default: 10",
    )
    args = parser.parse_args()
    if args.seconds <= 0:
        raise SystemExit("seconds must be > 0")

    app = QApplication(sys.argv)
    app.setApplicationName("Auto Press")
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(initial_seconds=float(args.seconds))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
