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


def test_window_store_summary_carries_latest_snapshot_timestamp():
    """Summaries include snapshot_at so the phone can show
    "captured Xs ago" without having to inspect the PNG headers."""
    from press_bridge import WindowStore

    store = WindowStore(snapshots_per_window=1)
    base = {"id": "w1", "name": "X", "idle": True, "score": 0.95, "configured": True}
    # First update with an image — snapshot_at populated.
    store.update([base], {"w1": b"png-1"})
    [s1] = store.summaries()
    assert s1["snapshot_at"] is not None
    first_ts = s1["snapshot_at"]
    # Update without an image — timestamp stays the same.
    store.update([base], {})
    [s2] = store.summaries()
    assert s2["snapshot_at"] == first_ts
    assert s2["snapshot_count"] == 1


def test_window_store_no_snapshot_yet_returns_none():
    from press_bridge import WindowStore

    store = WindowStore()
    store.update(
        [{"id": "w1", "name": "X", "idle": True, "score": 0.95, "configured": True}], {}
    )
    [summary] = store.summaries()
    assert summary["snapshot_at"] is None
    assert summary["snapshot_count"] == 0


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


def test_window_store_pop_at_removes_specific_message():
    from press_bridge import WindowStore

    store = WindowStore()
    store.enqueue("w1", "first")
    store.enqueue("w1", "second")
    store.enqueue("w1", "third")
    assert store.pop_at("w1", 1) == "second"
    assert store.pending("w1") == ["first", "third"]
    # Out-of-range returns None and leaves the queue alone.
    assert store.pop_at("w1", 5) is None
    assert store.pending("w1") == ["first", "third"]
    # Popping the last item clears the per-window deque entry.
    store.pop_at("w1", 0)
    store.pop_at("w1", 0)
    assert store.pending("w1") == []


def test_window_store_queue_enqueue_dequeue():
    from press_bridge import WindowStore

    store = WindowStore()
    accepted, pos = store.enqueue("w1", "hello")
    assert accepted is True
    assert pos == 1
    accepted, pos = store.enqueue("w1", "world")
    assert pos == 2
    # Empty / whitespace strings are allowed: the send pipeline interprets
    # them as "click + Enter, no paste". Only None is rejected.
    accepted, pos = store.enqueue("w1", "")
    assert accepted is True
    assert pos == 3
    accepted, _ = store.enqueue("w1", None)
    assert accepted is False
    assert store.pending("w1") == ["hello", "world", ""]
    assert store.dequeue("w1") == "hello"
    assert store.pending("w1") == ["world", ""]
    assert store.dequeue("w1") == "world"
    assert store.dequeue("w1") == ""
    assert store.dequeue("w1") is None  # empty queue
    assert store.pending("w1") == []


def test_window_store_summary_includes_pending():
    from press_bridge import WindowStore

    store = WindowStore()
    store.update([{"id": "w1", "name": "X", "idle": False, "score": 0.0, "configured": True}], {})
    store.enqueue("w1", "ship it")
    [summary] = store.summaries()
    assert summary["pending"] == ["ship it"]


@pytest.fixture
def fastapi_client():
    """Boots a real BridgeService + FastAPI app behind TestClient. Tests
    that need a custom config mutate ``calls['cfg']`` directly; the
    cfg_snapshot callback returns a shallow copy so mutations don't bleed
    between requests but can still be set up with a single dict."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from press_bridge import BridgeCallbacks, BridgeService, build_app

    calls: dict = {
        "send": [],
        "match": [],
        "window_send": [],
        "window_scroll": [],
        "rules_running": False,
        "rules_set": [],
        "cfg": {
            "interval_seconds": 10.0,
            "bridge": {"pre_paste_delay_ms": 5, "clipboard_restore_delay_ms": 5},
            "rules": [],
        },
    }

    def cfg_snapshot():
        snap = dict(calls["cfg"])
        snap["rules"] = [dict(r) for r in calls["cfg"].get("rules", [])]
        snap["bridge"] = dict(calls["cfg"].get("bridge", {}))
        return snap

    def re_match_rule(rule_id):
        calls["match"].append(rule_id)
        return calls.get("match_result", [(0.99, (100, 200))])

    def perform_send(point, text, bridge_cfg):
        calls["send"].append((point, text, dict(bridge_cfg)))

    def perform_window_send(window, text, bridge_cfg):
        calls["window_send"].append((dict(window), text))

    def perform_window_scroll(window, amount, bridge_cfg):
        calls["window_scroll"].append((dict(window), int(amount)))

    def is_rules_running():
        return bool(calls["rules_running"])

    def set_rules_running(running):
        calls["rules_running"] = bool(running)
        calls["rules_set"].append(bool(running))

    callbacks = BridgeCallbacks(
        cfg_snapshot=cfg_snapshot,
        re_match_rule=re_match_rule,
        perform_send=perform_send,
        perform_window_send=perform_window_send,
        perform_window_scroll=perform_window_scroll,
        is_rules_running=is_rules_running,
        set_rules_running=set_rules_running,
    )
    service = BridgeService(callbacks)
    app = build_app(service)
    client = TestClient(app)
    yield client, service, calls


def test_window_send_endpoint_sends_immediately_when_idle(fastapi_client):
    """When the live state for a window is idle, /api/windows/{id}/send
    fires the callback synchronously and reports sent=True."""
    client, service, calls = fastapi_client
    # Configure a window in cfg + mark it idle in the store.
    calls["cfg"]["bridge"] = {
        "windows": [
            {
                "id": "w1",
                "name": "Cursor",
                "region": [0, 0, 800, 600],
                "chat_target": [400, 510],
            }
        ]
    }
    service.windows.update(
        [{"id": "w1", "name": "Cursor", "idle": True, "score": 0.95, "configured": True}],
        {},
    )

    res = client.post("/api/windows/w1/send", json={"text": "ship it"})
    assert res.status_code == 200
    body = res.json()
    assert body["sent"] is True
    assert body["queued"] is False
    assert calls["window_send"] == [
        (
            {
                "id": "w1",
                "name": "Cursor",
                "region": [0, 0, 800, 600],
                "chat_target": [400, 510],
            },
            "ship it",
        )
    ]


def test_window_send_endpoint_queues_when_busy(fastapi_client):
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    service.windows.update(
        [{"id": "w1", "name": "X", "idle": False, "score": 0.1, "configured": True}],
        {},
    )
    res = client.post("/api/windows/w1/send", json={"text": "hold"})
    assert res.status_code == 202
    assert res.json()["queued"] is True
    assert service.windows.pending("w1") == ["hold"]
    assert calls["window_send"] == []  # not fired yet


def test_window_send_endpoint_accepts_empty_text_when_idle(fastapi_client):
    """Empty payload = "just click + Enter" — useful when the user has
    already typed the message in the target window and only needs the
    submit keystroke from the bridge."""
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    service.windows.update(
        [{"id": "w1", "name": "X", "idle": True, "score": 0.95, "configured": True}], {}
    )
    res = client.post("/api/windows/w1/send", json={"text": ""})
    assert res.status_code == 200
    assert calls["window_send"][0][1] == ""


def test_window_send_endpoint_accepts_whitespace_text(fastapi_client):
    """A single space / dot is a real message — pass it through verbatim."""
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    service.windows.update(
        [{"id": "w1", "name": "X", "idle": True, "score": 0.95, "configured": True}], {}
    )
    res = client.post("/api/windows/w1/send", json={"text": " "})
    assert res.status_code == 200
    assert calls["window_send"][0][1] == " "


def test_window_send_endpoint_400_when_text_field_missing(fastapi_client):
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    service.windows.update(
        [{"id": "w1", "name": "X", "idle": True, "score": 0.95, "configured": True}], {}
    )
    res = client.post("/api/windows/w1/send", json={})
    assert res.status_code == 400


def test_window_send_endpoint_404_for_unknown_window(fastapi_client):
    client, *_ = fastapi_client
    res = client.post("/api/windows/nope/send", json={"text": "x"})
    assert res.status_code == 404


def test_window_queue_drains_one_per_idle_transition(fastapi_client):
    """Going busy → idle pops one queued message and dispatches it. A
    second message stays queued until the next idle transition."""
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    # Start busy.
    service.update_window_states(
        [{"id": "w1", "name": "X", "idle": False, "score": 0.1, "configured": True}], {}
    )
    # Queue two messages while busy.
    service.windows.enqueue("w1", "first")
    service.windows.enqueue("w1", "second")
    # Flip idle: should drain one.
    service.update_window_states(
        [{"id": "w1", "name": "X", "idle": True, "score": 0.95, "configured": True}], {}
    )
    # Drain happens in a daemon thread; give it a moment.
    import time
    for _ in range(50):
        if calls["window_send"]:
            break
        time.sleep(0.02)
    assert len(calls["window_send"]) == 1
    assert calls["window_send"][0][1] == "first"
    assert service.windows.pending("w1") == ["second"]


def test_window_queue_send_now_pops_specific_index_and_fires(fastapi_client):
    """The 'Send now' button on a queued message should pop *that*
    message from the queue and dispatch it via perform_window_send,
    even if the window is currently busy."""
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    service.windows.update(
        [{"id": "w1", "name": "X", "idle": False, "score": 0.1, "configured": True}], {}
    )
    service.windows.enqueue("w1", "first")
    service.windows.enqueue("w1", "second")
    service.windows.enqueue("w1", "third")

    res = client.post("/api/windows/w1/queue/1/send_now")
    assert res.status_code == 200
    assert res.json() == {"sent": True, "text": "second"}
    assert calls["window_send"][0][1] == "second"
    assert service.windows.pending("w1") == ["first", "third"]


def test_window_store_update_at_replaces_text():
    from press_bridge import WindowStore

    store = WindowStore()
    store.enqueue("w1", "first")
    store.enqueue("w1", "second")
    store.enqueue("w1", "third")
    assert store.update_at("w1", 1, "second-edited") is True
    assert store.pending("w1") == ["first", "second-edited", "third"]
    # Empty / whitespace allowed.
    assert store.update_at("w1", 0, "") is True
    assert store.pending("w1") == ["", "second-edited", "third"]
    # None rejected.
    assert store.update_at("w1", 0, None) is False
    # Out-of-range rejected.
    assert store.update_at("w1", 9, "nope") is False


def test_window_queue_update_one_endpoint(fastapi_client):
    """PUT swaps the text at the given index, returns the new value, and
    fires an SSE so other phones see the change."""
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    service.windows.enqueue("w1", "first")
    service.windows.enqueue("w1", "second")
    res = client.put("/api/windows/w1/queue/0", json={"text": "first-edited"})
    assert res.status_code == 200
    assert res.json() == {"updated": True, "text": "first-edited"}
    assert service.windows.pending("w1") == ["first-edited", "second"]


def test_window_queue_update_one_400_when_text_missing(fastapi_client):
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    service.windows.enqueue("w1", "first")
    res = client.put("/api/windows/w1/queue/0", json={})
    assert res.status_code == 400


def test_window_queue_update_one_404_for_bad_index(fastapi_client):
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    res = client.put("/api/windows/w1/queue/0", json={"text": "x"})
    assert res.status_code == 404


def test_window_queue_delete_one_pops_specific_index(fastapi_client):
    """The trash button on a queued row should drop *that* message
    without firing it. Adjacent items keep their order."""
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    service.windows.enqueue("w1", "first")
    service.windows.enqueue("w1", "second")
    service.windows.enqueue("w1", "third")

    res = client.delete("/api/windows/w1/queue/1")
    assert res.status_code == 200
    assert res.json() == {"deleted": True, "text": "second"}
    assert service.windows.pending("w1") == ["first", "third"]
    # No perform_window_send fired — delete is silent.
    assert calls["window_send"] == []


def test_window_queue_delete_one_404_for_bad_index(fastapi_client):
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    res = client.delete("/api/windows/w1/queue/0")
    assert res.status_code == 404


def test_window_queue_send_now_404_for_bad_index(fastapi_client):
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    res = client.post("/api/windows/w1/queue/0/send_now")
    assert res.status_code == 404


def test_window_scroll_endpoint_calls_callback_with_centered_point(fastapi_client):
    """The bridge endpoint forwards the configured window dict + amount
    to perform_window_scroll. The callback is responsible for clicking
    the centre and sending wheel events."""
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [100, 200, 800, 600]}]
    }
    res = client.post("/api/windows/w1/scroll", json={"amount": 3})
    assert res.status_code == 200
    assert res.json() == {"scrolled": 3}
    assert len(calls["window_scroll"]) == 1
    win, amount = calls["window_scroll"][0]
    assert win["region"] == [100, 200, 800, 600]
    assert amount == 3


def test_window_scroll_endpoint_400_for_zero_amount(fastapi_client):
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}]
    }
    res = client.post("/api/windows/w1/scroll", json={"amount": 0})
    assert res.status_code == 400


def test_window_scroll_endpoint_400_when_window_has_no_region(fastapi_client):
    client, service, calls = fastapi_client
    calls["cfg"]["bridge"] = {"windows": [{"id": "w1", "name": "X", "region": None}]}
    res = client.post("/api/windows/w1/scroll", json={"amount": 1})
    assert res.status_code == 400


def test_window_scroll_endpoint_404_for_unknown_window(fastapi_client):
    client, service, calls = fastapi_client
    res = client.post("/api/windows/missing/scroll", json={"amount": 1})
    assert res.status_code == 404


def test_admin_rules_get_reflects_callback(fastapi_client):
    client, service, calls = fastapi_client
    calls["rules_running"] = True
    res = client.get("/api/admin/rules")
    assert res.status_code == 200
    assert res.json() == {"running": True}


def test_admin_rules_post_calls_setter(fastapi_client):
    client, service, calls = fastapi_client
    res = client.post("/api/admin/rules", json={"running": True})
    assert res.status_code == 200
    assert calls["rules_set"] == [True]
    assert calls["rules_running"] is True
    res = client.post("/api/admin/rules", json={"running": False})
    assert res.status_code == 200
    assert calls["rules_set"] == [True, False]


def test_admin_rules_post_400_when_payload_malformed(fastapi_client):
    client, service, calls = fastapi_client
    res = client.post("/api/admin/rules", json={"running": "yes"})
    assert res.status_code == 400


def test_state_endpoint_reports_rules_running(fastapi_client):
    client, service, calls = fastapi_client
    calls["rules_running"] = True
    res = client.get("/api/state")
    assert res.status_code == 200
    assert res.json()["rules_running"] is True


def test_window_store_set_snapshot_force_inserts_when_state_exists(fastapi_client):
    """set_snapshot bypasses the busy↔idle gate that update() applies —
    used by the scroll path so the user sees the new visible region
    immediately, regardless of whether the window flipped state."""
    from press_bridge import WindowStore

    store = WindowStore(snapshots_per_window=2)
    # No state yet → set_snapshot should refuse.
    assert store.set_snapshot("w1", b"png-x") is False
    # Seed state via update (no image), then force-insert.
    store.update(
        [{"id": "w1", "name": "X", "idle": True, "score": 0.95, "configured": True}], {}
    )
    assert store.set_snapshot("w1", b"png-1") is True
    assert store.snapshot("w1", 0)[1] == b"png-1"
    # A second forced snapshot pushes the first along the ring.
    store.set_snapshot("w1", b"png-2")
    assert store.snapshot("w1", 0)[1] == b"png-2"
    assert store.snapshot("w1", 1)[1] == b"png-1"


def test_window_clear_queue(fastapi_client):
    client, service, _ = fastapi_client
    service.windows.enqueue("w1", "a")
    service.windows.enqueue("w1", "b")
    res = client.delete("/api/windows/w1/queue")
    assert res.status_code == 200
    assert res.json()["cleared"] == 2
    assert service.windows.pending("w1") == []


def test_evaluate_bridge_windows_returns_empty_if_template_file_missing():
    bridge_cfg = {
        "idle_template_path": "does_not_exist.png",
        "windows": [{"id": "w1", "name": "X", "region": [0, 0, 100, 100]}],
    }
    assert press_engine.evaluate_bridge_windows(bridge_cfg) == []
