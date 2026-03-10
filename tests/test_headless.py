import pytest

import press_headless


def test_headless_rejects_watch_run_mode():
    with pytest.raises(SystemExit, match="Headless watch-run is not supported"):
        press_headless.run_headless(
            seconds=1.0,
            mode="watch-run",
            x=None,
            y=None,
            force_calibrate=False,
            state_detect=False,
            state_word="continue",
            state_bbox=None,
            state_finished_template=None,
            state_threshold=0.8,
        )
