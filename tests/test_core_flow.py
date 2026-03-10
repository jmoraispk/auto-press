from press_core import choose_run_first_action


def test_run_watch_match_short_circuits_state_detection():
    assert choose_run_first_action(run_match=True, state_match=True) == "run-click"


def test_run_watch_no_match_falls_back_to_state_detection():
    assert choose_run_first_action(run_match=False, state_match=True) == "state-action"


def test_run_watch_no_match_and_no_state_uses_default_action():
    assert choose_run_first_action(run_match=False, state_match=False) == "default-action"
