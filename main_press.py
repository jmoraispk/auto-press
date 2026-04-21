"""Auto Press entrypoint."""

import argparse
import sys

from PySide6.QtWidgets import QApplication

from press_ui import MainWindow


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
    app.setQuitOnLastWindowClosed(False)  # tray keeps the process alive

    window = MainWindow(initial_seconds=float(args.seconds))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
