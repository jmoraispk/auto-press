"""Auto Press — Fluent Design UI entrypoint (experimental).

The stable tkinter UI is still the daily driver; run `main_press.py` for that.
This file boots the Fluent / QFluentWidgets draft.
"""

import argparse
import os
import sys

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")

from PySide6.QtWidgets import QApplication  # noqa: E402

from press_ui_fluent import MainWindow  # noqa: E402


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
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
