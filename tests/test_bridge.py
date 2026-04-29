"""Bridge config + idle-detection tests.

Scope of this slice: schema migration and the per-window idle detector.
The HTTP/SSE/send pipeline is being reshaped around the new "windows"
contract, so its tests will land with that next commit.
"""

from __future__ import annotations

import numpy as np
import pytest

import press_engine
import press_store


# ---- config schema migration -------------------------------------------


def test_old_config_without_bridge_block_loads_with_defaults():
    cfg = press_store.normalize_config({"rules": [{"name": "r", "matcher": "template"}]})
    assert "bridge" in cfg
    assert "enabled" not in cfg["bridge"]  # gated by --bridge flag, not config
    assert cfg["bridge"]["port"] == 8765
    assert cfg["bridge"]["windows"] == []
    assert cfg["bridge"]["idle_template_path"] is None
    assert cfg["bridge"]["idle_threshold"] == 0.90


def test_legacy_per_rule_bridge_fields_are_dropped():
    """Old rules carrying bridge_paste_offset / bridge_friendly_name etc.
    load cleanly — those fields are now dead and silently stripped."""
    cfg = press_store.normalize_config(
        {
            "rules": [
                {
                    "name": "r",
                    "bridge_paste_offset": [10, 20],
                    "bridge_friendly_name": "Cursor #1",
                    "bridge_read_strategy": "ocr",
                    "bridge_read_region": [0, 0, 100, 100],
                }
            ]
        }
    )
    rule = cfg["rules"][0]
    for key in (
        "bridge_paste_offset",
        "bridge_friendly_name",
        "bridge_read_strategy",
        "bridge_read_region",
    ):
        assert key not in rule


def test_window_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(press_store, "TEMPLATES_DIR", tmp_path)
    monkeypatch.setattr(press_store, "CONFIG_PATH", tmp_path / "config.json")

    cfg = press_store.default_config()
    cfg["bridge"]["idle_template_path"] = "idle.png"
    cfg["bridge"]["idle_threshold"] = 0.85
    win = press_store.default_bridge_window("Cursor #1")
    win["region"] = [100, 200, 800, 600]
    win["chat_target"] = [500, 750]
    win["read_region"] = [110, 210, 780, 400]
    cfg["bridge"]["windows"].append(win)

    press_store.save_config(cfg)
    loaded = press_store.load_config()

    assert loaded["bridge"]["idle_template_path"] == "idle.png"
    assert loaded["bridge"]["idle_threshold"] == 0.85
    [w] = loaded["bridge"]["windows"]
    assert w["name"] == "Cursor #1"
    assert w["region"] == [100, 200, 800, 600]
    assert w["chat_target"] == [500, 750]
    assert w["read_region"] == [110, 210, 780, 400]


def test_invalid_window_fields_normalize_to_none():
    cfg = press_store.normalize_config(
        {
            "bridge": {
                "windows": [
                    {"name": "X", "region": [10, 20, -5, 5], "chat_target": "nope"},
                    {"name": "", "region": "not a list"},
                ]
            }
        }
    )
    [w1, w2] = cfg["bridge"]["windows"]
    assert w1["region"] is None
    assert w1["chat_target"] is None
    assert w2["name"] == "Cursor"  # default fallback
    assert w2["region"] is None


# ---- idle detector ------------------------------------------------------


@pytest.fixture
def idle_template(tmp_path, monkeypatch):
    """Drop a recognisable 24×24 cross pattern as templates/idle.png.

    A uniform-grey template would correlate equally with any flat region
    (TM_CCOEFF_NORMED is undefined when both signals have zero variance),
    so we use a cross so detection is unambiguous.
    """
    monkeypatch.setattr(press_store, "TEMPLATES_DIR", tmp_path)
    cv2 = pytest.importorskip("cv2")
    template = np.zeros((24, 24), dtype=np.uint8)
    template[11:13, :] = 255   # horizontal bar
    template[:, 11:13] = 255   # vertical bar
    cv2.imwrite(str(tmp_path / "idle.png"), template)
    return "idle.png"


def test_evaluate_bridge_windows_returns_empty_when_unconfigured():
    assert press_engine.evaluate_bridge_windows({}) == []
    assert press_engine.evaluate_bridge_windows({"windows": [{"name": "X"}]}) == []


def test_evaluate_bridge_windows_skips_windows_without_region(idle_template, monkeypatch):
    bridge_cfg = {
        "idle_template_path": idle_template,
        "idle_threshold": 0.9,
        "windows": [{"id": "w1", "name": "Cursor #1", "region": None}],
    }
    states = press_engine.evaluate_bridge_windows(bridge_cfg)
    assert states == [
        {"id": "w1", "name": "Cursor #1", "idle": False, "score": 0.0, "configured": False}
    ]


def _frame_with_cross_at(width: int, height: int, x: int, y: int):
    frame = np.full((height, width), 80, dtype=np.uint8)  # mid-grey background
    frame[y + 11:y + 13, x:x + 24] = 255
    frame[y:y + 24, x + 11:x + 13] = 255
    return frame


def test_evaluate_bridge_windows_detects_idle(idle_template, monkeypatch):
    """A frame containing the idle template anywhere in the region → idle."""
    frame = _frame_with_cross_at(200, 200, 50, 60)

    def fake_capture_screen_gray(region):
        # Region passes through unmodified — we ignore it because our fake
        # frame is the whole "screen".
        return frame

    monkeypatch.setattr(press_engine, "capture_screen_gray", fake_capture_screen_gray)

    bridge_cfg = {
        "idle_template_path": idle_template,
        "idle_threshold": 0.9,
        "windows": [{"id": "w1", "name": "Cursor #1", "region": [0, 0, 200, 200]}],
    }
    [state] = press_engine.evaluate_bridge_windows(bridge_cfg)
    assert state["idle"] is True
    assert state["score"] >= 0.9
    assert state["configured"] is True


def test_evaluate_bridge_windows_busy_when_template_absent(idle_template, monkeypatch):
    # Diagonal stripe — non-uniform, but contains no cross.
    frame = np.zeros((200, 200), dtype=np.uint8)
    for i in range(200):
        frame[i, i] = 255

    monkeypatch.setattr(press_engine, "capture_screen_gray", lambda region: frame)

    bridge_cfg = {
        "idle_template_path": idle_template,
        "idle_threshold": 0.9,
        "windows": [{"id": "w1", "name": "Cursor #1", "region": [0, 0, 200, 200]}],
    }
    [state] = press_engine.evaluate_bridge_windows(bridge_cfg)
    assert state["idle"] is False
    assert state["configured"] is True


def test_evaluate_bridge_windows_returns_empty_if_template_file_missing():
    bridge_cfg = {
        "idle_template_path": "does_not_exist.png",
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}],
    }
    assert press_engine.evaluate_bridge_windows(bridge_cfg) == []
