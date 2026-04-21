"""Auto Press entrypoint."""

import argparse
import sys


def _enable_per_monitor_v2_dpi() -> None:
    """Force PER_MONITOR_AWARE_V2 before any GUI toolkit imports.

    CustomTkinter only sets per-monitor V1, which misreports coordinates on
    mixed-DPI multi-monitor setups. V2 must be set before any other DPI-aware
    call in the process.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)
    except (AttributeError, OSError, Exception):
        pass


_enable_per_monitor_v2_dpi()

from press_ui import run_ui  # noqa: E402  (DPI init must come first)


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
    run_ui(args.seconds)


if __name__ == "__main__":
    main()
