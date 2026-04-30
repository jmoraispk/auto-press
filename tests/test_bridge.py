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


def test_window_with_negative_origin_survives_round_trip():
    """Multi-monitor Windows: monitors placed left of/above the primary
    live at negative virtual-screen coordinates. Captures from those
    monitors must persist; an earlier validator rejected them outright
    and silently wiped the region on save."""
    cfg = press_store.normalize_config(
        {"bridge": {"windows": [{"name": "Left mon", "region": [-1920, 0, 800, 600]}]}}
    )
    assert cfg["bridge"]["windows"][0]["region"] == [-1920, 0, 800, 600]


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


def _rgb_frame_with_cross_at(width: int, height: int, x: int, y: int):
    """Mid-grey RGB background with a white cross at (x, y). Detector
    converts RGB → gray internally, so the cross still matches the
    grayscale template."""
    frame = np.full((height, width, 3), 80, dtype=np.uint8)
    frame[y + 11:y + 13, x:x + 24, :] = 255
    frame[y:y + 24, x + 11:x + 13, :] = 255
    return frame


def test_evaluate_bridge_windows_detects_idle(idle_template, monkeypatch):
    """A frame containing the idle template anywhere in the region → idle."""
    frame = _rgb_frame_with_cross_at(200, 200, 50, 60)

    monkeypatch.setattr(press_engine, "capture_screen_rgb", lambda region: frame)

    bridge_cfg = {
        "idle_template_path": idle_template,
        "idle_threshold": 0.9,
        "windows": [{"id": "w1", "name": "Cursor #1", "region": [0, 0, 200, 200]}],
    }
    [state] = press_engine.evaluate_bridge_windows(bridge_cfg)
    assert state["idle"] is True
    assert state["score"] >= 0.9
    assert state["configured"] is True


def test_evaluate_bridge_windows_returns_rgb_when_requested(idle_template, monkeypatch):
    """capture_rgb=True attaches the captured ndarray for the snapshot path."""
    frame = _rgb_frame_with_cross_at(200, 200, 50, 60)
    monkeypatch.setattr(press_engine, "capture_screen_rgb", lambda region: frame)
    [state] = press_engine.evaluate_bridge_windows(
        {
            "idle_template_path": idle_template,
            "idle_threshold": 0.9,
            "windows": [{"id": "w1", "name": "Cursor #1", "region": [0, 0, 200, 200]}],
        },
        capture_rgb=True,
    )
    assert state["rgb"].shape == (200, 200, 3)


def test_evaluate_bridge_windows_busy_when_template_absent(idle_template, monkeypatch):
    # Diagonal stripe — non-uniform, but contains no cross.
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    for i in range(200):
        frame[i, i, :] = 255

    monkeypatch.setattr(press_engine, "capture_screen_rgb", lambda region: frame)

    bridge_cfg = {
        "idle_template_path": idle_template,
        "idle_threshold": 0.9,
        "windows": [{"id": "w1", "name": "Cursor #1", "region": [0, 0, 200, 200]}],
    }
    [state] = press_engine.evaluate_bridge_windows(bridge_cfg)
    assert state["idle"] is False
    assert state["configured"] is True


# ---- window store -------------------------------------------------------


def test_window_store_ring_buffer_caps_at_max():
    from press_bridge import WindowStore

    store = WindowStore(snapshots_per_window=3)
    states = [{"id": "w1", "name": "X", "idle": False, "score": 0.0, "configured": True}]
    for i in range(5):
        store.update(states, {"w1": f"png-{i}".encode()})
    [summary] = store.summaries()
    assert summary["snapshot_count"] == 3
    # Newest first.
    assert store.snapshot("w1", 0)[1] == b"png-4"
    assert store.snapshot("w1", 2)[1] == b"png-2"
    assert store.snapshot("w1", 3) is None  # past the buffer


def test_window_store_reports_only_idle_transitions():
    from press_bridge import WindowStore

    store = WindowStore()
    base = {"id": "w1", "name": "X", "configured": True}
    # First observation: no transition (we don't know the prior state).
    assert store.update([dict(base, idle=False, score=0.1)], {}) == []
    # Same state: still no transition.
    assert store.update([dict(base, idle=False, score=0.1)], {}) == []
    # Flip → reports.
    [tr] = store.update([dict(base, idle=True, score=0.95)], {})
    assert tr["idle"] is True


def test_window_store_drops_removed_windows():
    from press_bridge import WindowStore

    store = WindowStore()
    s1 = {"id": "w1", "name": "X", "idle": False, "score": 0.0, "configured": True}
    s2 = {"id": "w2", "name": "Y", "idle": False, "score": 0.0, "configured": True}
    store.update([s1, s2], {})
    assert len(store.summaries()) == 2
    store.update([s1], {})  # w2 removed from cfg
    [only] = store.summaries()
    assert only["id"] == "w1"


def test_evaluate_bridge_windows_returns_empty_if_template_file_missing():
    bridge_cfg = {
        "idle_template_path": "does_not_exist.png",
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}],
    }
    assert press_engine.evaluate_bridge_windows(bridge_cfg) == []
