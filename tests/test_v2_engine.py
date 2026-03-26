import press_v2_engine


def test_evaluate_rules_chooses_first_matching_rule(monkeypatch):
    runtime_rules = [
        {"id": "a", "name": "Rule A", "threshold": 0.9, "cooldown_seconds": 0.0},
        {"id": "b", "name": "Rule B", "threshold": 0.9, "cooldown_seconds": 0.0},
    ]

    monkeypatch.setattr(press_v2_engine, "capture_screen_gray", lambda: object())

    def fake_eval(_frame, rule):
        if rule["id"] == "a":
            return 0.95, (10, 10)
        return 0.99, (20, 20)

    monkeypatch.setattr(press_v2_engine, "evaluate_rule_on_frame", fake_eval)

    results, chosen = press_v2_engine.evaluate_rules(runtime_rules, cooldowns={}, now=100.0)

    assert len(results) == 2
    assert chosen is not None
    assert chosen["id"] == "a"
    assert chosen["center"] == (10, 10)


def test_evaluate_rules_skips_cooled_down_rule(monkeypatch):
    runtime_rules = [
        {"id": "a", "name": "Rule A", "threshold": 0.9, "cooldown_seconds": 10.0},
        {"id": "b", "name": "Rule B", "threshold": 0.9, "cooldown_seconds": 0.0},
    ]

    monkeypatch.setattr(press_v2_engine, "capture_screen_gray", lambda: object())

    def fake_eval(_frame, rule):
        if rule["id"] == "a":
            return 0.95, (10, 10)
        return 0.92, (20, 20)

    monkeypatch.setattr(press_v2_engine, "evaluate_rule_on_frame", fake_eval)

    results, chosen = press_v2_engine.evaluate_rules(runtime_rules, cooldowns={"a": 95.0}, now=100.0)

    assert len(results) == 2
    assert results[0]["cooldown_ready"] is False
    assert chosen is not None
    assert chosen["id"] == "b"


def test_execute_match_uses_click_enter(monkeypatch):
    called = {}

    monkeypatch.setattr(
        press_v2_engine,
        "do_action",
        lambda mode, center, text_before_enter=None: called.update(
            mode=mode, center=center, text=text_before_enter
        ),
    )

    press_v2_engine.execute_match(
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
