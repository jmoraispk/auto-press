# press_enter_repeatedly.py
import time
import argparse
from datetime import datetime

try:
    import pyautogui
except ImportError:
    raise SystemExit(
        "pyautogui is required. Install with:\n\n"
        "    uv run --with pyautogui python press_enter_repeatedly.py\n"
        "or:\n"
        "    pip install pyautogui"
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Press Enter repeatedly at a fixed interval.")
    p.add_argument(
        "seconds",
        nargs="?",
        type=float,
        default=10.0,
        help="Delay between Enter presses (seconds). Default: 10",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    delay_seconds = args.seconds

    if delay_seconds <= 0:
        raise SystemExit("seconds must be > 0")

    print(f"Sending Enter every {delay_seconds} seconds.")
    print("Focus the target window, then leave this running.")
    print("Press Ctrl+C here to stop.\n")

    try:
        while True:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] press enter")
            pyautogui.press("enter")
            time.sleep(delay_seconds)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
