import press_engine


def test_evaluate_rules_returns_all_matches(monkeypatch):
    runtime_rules = [
        {"id": "a", "name": "Rule A", "threshold": 0.9},
        {"id": "b", "name": "Rule B", "threshold": 0.9},
    ]

    monkeypatch.setattr(press_engine, "capture_screen_gray", lambda: object())

    def fake_find(_frame, rule):
        if rule["id"] == "a":
            return [(0.95, (10, 10)), (0.93, (20, 20))]
        return [(0.91, (30, 30))]

    monkeypatch.setattr(press_engine, "find_rule_matches", fake_find)

    results, actions = press_engine.evaluate_rules(runtime_rules)

    assert len(results) == 2
    assert results[0]["match_count"] == 2
    assert results[1]["match_count"] == 1
    assert [action["center"] for action in actions] == [(10, 10), (20, 20), (30, 30)]


def test_evaluate_rule_on_frame_returns_best_match(monkeypatch):
    monkeypatch.setattr(
        press_engine,
        "find_rule_matches",
        lambda _frame, _rule: [(0.97, (40, 50)), (0.92, (60, 70))],
    )

    score, center = press_engine.evaluate_rule_on_frame(object(), {"id": "a"})

    assert score == 0.97
    assert center == (40, 50)


def test_execute_match_uses_click_enter(monkeypatch):
    called = {}

    monkeypatch.setattr(
        press_engine,
        "do_action",
        lambda mode, center, text_before_enter=None: called.update(
            mode=mode, center=center, text=text_before_enter
        ),
    )

    press_engine.execute_match(
        {
            "center": (40, 50),
            "action": "click+type+enter",
            "text": "continue",
        }
    )

    assert called == {
        "mode": "click+enter",
        "center": (40, 50),
        "text": "continue",
    }


def test_execute_matches_waits_between_actions(monkeypatch):
    calls = []

    monkeypatch.setattr(
        press_engine,
        "execute_match",
        lambda match: calls.append(("exec", match["center"])),
    )
    monkeypatch.setattr(
        press_engine.time,
        "sleep",
        lambda seconds: calls.append(("sleep", seconds)),
    )

    press_engine.execute_matches(
        [
            {"center": (10, 10)},
            {"center": (20, 20)},
            {"center": (30, 30)},
        ],
        delay_seconds=0.2,
    )

    assert calls == [
        ("exec", (10, 10)),
        ("sleep", 0.2),
        ("exec", (20, 20)),
        ("sleep", 0.2),
        ("exec", (30, 30)),
    ]
