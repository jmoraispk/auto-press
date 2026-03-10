"""Thin CLI entrypoint for UI and headless runners."""

import argparse

from press_core import MODE_CLICK, MODE_CLICK_ENTER, MODE_ENTER, MODE_WATCH_RUN, MODES, parse_bbox
from press_headless import run_headless
from press_ui import run_ui


DETECT_THRESHOLD_DEFAULT = 0.80
DETECT_WORD_DEFAULT = "continue"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto Clicker: Click (+ optional Enter) at a target location."
    )
    parser.add_argument(
        "seconds",
        nargs="?",
        type=float,
        default=10.0,
        help="Interval between cycles (seconds). Default: 10",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=MODE_CLICK_ENTER,
        help=(
            f"Action mode: {MODE_ENTER}=press Enter only, {MODE_CLICK}=click only, "
            f"{MODE_CLICK_ENTER}=click then Enter, {MODE_WATCH_RUN}=watch run button first."
        ),
    )
    parser.add_argument("--headless", action="store_true", help="Run without UI.")
    parser.add_argument("--x", type=int, help="Target X coordinate (headless, for click modes).")
    parser.add_argument("--y", type=int, help="Target Y coordinate (headless, for click modes).")
    parser.add_argument("--calibrate", action="store_true", help="Force calibration (headless, for click modes).")
    parser.add_argument(
        "--state-detect",
        action="store_true",
        help="Enable state detection (click+enter mode): finished => type word before Enter.",
    )
    parser.add_argument(
        "--state-word",
        default=DETECT_WORD_DEFAULT,
        help=f"Word to type when state is finished. Default: {DETECT_WORD_DEFAULT}",
    )
    parser.add_argument(
        "--state-bbox",
        help="State detection region as left,top,width,height (headless).",
    )
    parser.add_argument(
        "--state-finished-template",
        help="Path to FINISHED template image (headless state detection).",
    )
    parser.add_argument(
        "--state-threshold",
        type=float,
        default=DETECT_THRESHOLD_DEFAULT,
        help=f"State match threshold. Default: {DETECT_THRESHOLD_DEFAULT}",
    )
    parser.add_argument(
        "--targets",
        type=int,
        default=1,
        choices=[1, 2, 3],
        help="Number of click targets (1-3). Default: 1. Only applies to click modes.",
    )
    parser.add_argument("--toggle", default="PAGEDOWN", help="Toggle hotkey. Default: PAGEDOWN")
    parser.add_argument("--calibrate-key", default="PAGEUP", help="Calibrate hotkey. Default: PAGEUP")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.seconds <= 0:
        raise SystemExit("seconds must be > 0")

    bbox = None
    if args.state_bbox:
        try:
            bbox = parse_bbox(args.state_bbox)
        except ValueError as e:
            raise SystemExit(f"Invalid --state-bbox: {e}")

    if args.headless:
        run_headless(
            args.seconds,
            args.mode,
            args.x,
            args.y,
            args.calibrate,
            args.state_detect,
            args.state_word,
            bbox,
            args.state_finished_template,
            args.state_threshold,
        )
        return

    run_ui(
        args.seconds,
        args.toggle,
        args.calibrate_key,
        args.mode,
        args.targets,
        args.state_threshold,
        args.state_word,
    )


if __name__ == "__main__":
    main()
