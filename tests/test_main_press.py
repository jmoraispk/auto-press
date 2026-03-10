import argparse

import pytest

import main_press


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr("sys.argv", ["main_press.py"])
    args = main_press.parse_args()
    assert args.mode == "click+enter"
    assert args.seconds == 10.0


def test_main_rejects_invalid_seconds(monkeypatch):
    ns = argparse.Namespace(
        seconds=0.0,
        mode="click",
        headless=False,
        x=None,
        y=None,
        calibrate=False,
        state_detect=False,
        state_word="continue",
        state_bbox=None,
        state_finished_template=None,
        state_threshold=0.8,
        targets=1,
        toggle="PAGEDOWN",
        calibrate_key="PAGEUP",
    )
    monkeypatch.setattr(main_press, "parse_args", lambda: ns)
    with pytest.raises(SystemExit, match="seconds must be > 0"):
        main_press.main()
