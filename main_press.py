"""Auto Press entrypoint."""

import argparse

from press_ui import run_ui


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
