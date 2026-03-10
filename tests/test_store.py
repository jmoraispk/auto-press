from pathlib import Path

import press_store


def test_config_roundtrip(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    config_path = templates_dir / "config.json"
    monkeypatch.setattr(press_store, "TEMPLATES_DIR", templates_dir)
    monkeypatch.setattr(press_store, "CONFIG_PATH", config_path)

    cfg = press_store.default_config(num_targets=2)
    cfg["mode"] = "watch-run"
    cfg["run_templates"] = ["run_template_1.png"]
    cfg["targets"][0]["click_target"] = [100, 200]
    cfg["targets"][0]["run_roi"] = [10, 20, 300, 100]
    cfg["targets"][1]["state_roi"] = [11, 22, 333, 120]
    cfg["targets"][1]["state_template"] = "state_t2.png"

    press_store.save_config(cfg)
    loaded = press_store.load_config(num_targets=2)

    assert loaded["mode"] == "watch-run"
    assert loaded["run_templates"] == ["run_template_1.png"]
    assert loaded["targets"][0]["click_target"] == [100, 200]
    assert loaded["targets"][0]["run_roi"] == [10, 20, 300, 100]
    assert loaded["targets"][1]["state_roi"] == [11, 22, 333, 120]
    assert loaded["targets"][1]["state_template"] == "state_t2.png"


def test_template_path_uses_templates_dir(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    monkeypatch.setattr(press_store, "TEMPLATES_DIR", templates_dir)
    p = press_store.template_path("foo.png")
    assert p == templates_dir / "foo.png"
