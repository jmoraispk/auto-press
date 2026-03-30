import press_core
from press_core import choose_run_first_action


def test_run_watch_match_short_circuits_state_detection():
    assert choose_run_first_action(run_match=True, state_match=True) == "run-click"


def test_run_watch_no_match_falls_back_to_state_detection():
    assert choose_run_first_action(run_match=False, state_match=True) == "state-action"


def test_run_watch_no_match_and_no_state_uses_default_action():
    assert choose_run_first_action(run_match=False, state_match=False) == "default-action"


def test_click_enter_waits_briefly_after_typing(monkeypatch):
    events = []

    class DummyPos:
        x = 1
        y = 2

    monkeypatch.setattr(press_core.pyautogui, "position", lambda: DummyPos())
    monkeypatch.setattr(press_core.pyautogui, "moveTo", lambda *args, **kwargs: events.append(("move", args)))
    monkeypatch.setattr(press_core.pyautogui, "click", lambda: events.append(("click",)))
    monkeypatch.setattr(press_core.pyautogui, "press", lambda key: events.append(("press", key)))
    monkeypatch.setattr(press_core, "type_word_with_retry", lambda word: events.append(("type", word)))
    monkeypatch.setattr(press_core.time, "sleep", lambda seconds: events.append(("sleep", seconds)))

    press_core.do_action(press_core.MODE_CLICK_ENTER, (10, 20), text_before_enter="continue")

    assert ("type", "continue") in events
    assert ("sleep", press_core.ENTER_AFTER_WORD_DELAY_SEC) in events
    assert events.index(("type", "continue")) < events.index(("sleep", press_core.ENTER_AFTER_WORD_DELAY_SEC)) < events.index(("press", "enter"))
