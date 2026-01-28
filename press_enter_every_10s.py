import time
from datetime import datetime

try:
    import pyautogui
except ImportError:
    raise SystemExit(
        "pyautogui is required. Install with:\n\n"
        "    uv run --with pyautogui python press_enter_every_10s.py\n"
        "or:\n"
        "    pip install pyautogui"
    )

DELAY_SECONDS = 10  # seconds between Enter presses

print(f"Sending Enter every {DELAY_SECONDS} seconds.")
print("Focus the target window, then leave this running.")
print("Press Ctrl+C here to stop.\n")

try:
    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] press enter")
        pyautogui.press("enter")
        time.sleep(DELAY_SECONDS)
except KeyboardInterrupt:
    print("\nStopped.")
