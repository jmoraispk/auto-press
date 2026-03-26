from pathlib import Path

import press_v2_store


def test_v2_config_roundtrip(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    config_path = templates_dir / "v2_config.json"
    monkeypatch.setattr(press_v2_store, "TEMPLATES_DIR", templates_dir)
    monkeypatch.setattr(press_v2_store, "V2_CONFIG_PATH", config_path)

    cfg = press_v2_store.default_config()
    cfg["interval_seconds"] = 15
    rule = press_v2_store.default_rule("ContinueButton")
    rule["template_path"] = "v2_rule_a.png"
    rule["search_region"] = [10, 20, 300, 200]
    rule["action"] = press_v2_store.ACTION_CLICK_TYPE_ENTER
    rule["text"] = "continue"
    cfg["rules"].append(rule)

    press_v2_store.save_config(cfg)
    loaded = press_v2_store.load_config()

    assert loaded["interval_seconds"] == 15.0
    assert len(loaded["rules"]) == 1
    assert loaded["rules"][0]["name"] == "ContinueButton"
    assert loaded["rules"][0]["template_path"] == "v2_rule_a.png"
    assert loaded["rules"][0]["search_region"] == [10, 20, 300, 200]
    assert loaded["rules"][0]["action"] == press_v2_store.ACTION_CLICK_TYPE_ENTER


def test_relativize_template_path_prefers_templates_relative(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    monkeypatch.setattr(press_v2_store, "TEMPLATES_DIR", templates_dir)
    path = templates_dir / "v2_rule_x.png"
    assert press_v2_store.relativize_template_path(path) == "v2_rule_x.png"


def test_normalize_config_reassigns_priorities():
    cfg = {
        "interval_seconds": 5,
        "rules": [
            {"name": "A", "priority": 9},
            {"name": "B", "priority": 99},
        ],
    }
    normalized = press_v2_store.normalize_config(cfg)
    assert [rule["priority"] for rule in normalized["rules"]] == [1, 2]
