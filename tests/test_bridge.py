"""Bridge-side tests: config migration, send pipeline, event hub, ntfy."""

from __future__ import annotations

import json

import pytest

import press_store


# --- config migration ----------------------------------------------------


def test_old_config_without_bridge_block_loads_with_defaults():
    old = {
        "interval_seconds": 5,
        "rules": [{"name": "Continue", "matcher": "template"}],
    }
    cfg = press_store.normalize_config(old)
    assert "bridge" in cfg
    assert cfg["bridge"]["enabled"] is False
    assert cfg["bridge"]["port"] == 8765
    rule = cfg["rules"][0]
    assert rule["bridge_paste_offset"] == [0, 0]
    assert rule["bridge_read_strategy"] == "none"
    assert rule["bridge_read_region"] is None


def test_bridge_block_round_trip(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    config_path = templates_dir / "config.json"
    monkeypatch.setattr(press_store, "TEMPLATES_DIR", templates_dir)
    monkeypatch.setattr(press_store, "CONFIG_PATH", config_path)

    cfg = press_store.default_config()
    cfg["bridge"]["enabled"] = True
    cfg["bridge"]["port"] = 9000
    cfg["bridge"]["ntfy_topic"] = "demo"
    rule = press_store.default_rule("X")
    rule["bridge_paste_offset"] = [10, -4]
    rule["bridge_friendly_name"] = "Cursor #1"
    rule["bridge_read_strategy"] = "ocr"
    rule["bridge_read_region"] = [100, 200, 300, 400]
    cfg["rules"].append(rule)

    press_store.save_config(cfg)
    loaded = press_store.load_config()

    assert loaded["bridge"]["enabled"] is True
    assert loaded["bridge"]["port"] == 9000
    assert loaded["bridge"]["ntfy_topic"] == "demo"
    r = loaded["rules"][0]
    assert r["bridge_paste_offset"] == [10, -4]
    assert r["bridge_friendly_name"] == "Cursor #1"
    assert r["bridge_read_strategy"] == "ocr"
    assert r["bridge_read_region"] == [100, 200, 300, 400]


def test_bridge_invalid_values_fall_back_to_defaults():
    cfg = press_store.normalize_config(
        {
            "bridge": {
                "enabled": "yes",
                "port": "ninety",
                "pre_paste_delay_ms": "fast",
                "ntfy_server": "  ",
            }
        }
    )
    assert cfg["bridge"]["enabled"] is True  # truthy string
    assert cfg["bridge"]["port"] == 8765
    assert cfg["bridge"]["pre_paste_delay_ms"] == 150
    assert cfg["bridge"]["ntfy_server"] == "https://ntfy.sh"


# --- event hub -----------------------------------------------------------


def test_event_hub_buffer_and_dismiss():
    from press_bridge import EventHub

    hub = EventHub(maxlen=3)
    for i in range(5):
        hub.publish({"event_id": f"e{i}", "rule_id": "r1"})
    recent = hub.recent()
    assert [e["event_id"] for e in recent] == ["e2", "e3", "e4"]
    assert hub.dismiss("e3") is True
    assert hub.dismiss("missing") is False
    again = {e["event_id"]: e["dismissed"] for e in hub.recent()}
    assert again["e3"] is True
    assert again["e2"] is False


# --- send pipeline -------------------------------------------------------


@pytest.fixture
def fastapi_client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from press_bridge import BridgeCallbacks, BridgeService, build_app

    calls: dict = {"send": [], "match": []}

    def cfg_snapshot():
        return {
            "interval_seconds": 10.0,
            "bridge": {"enabled": True, "pre_paste_delay_ms": 5, "clipboard_restore_delay_ms": 5},
            "rules": [
                {
                    "id": "rule-a",
                    "name": "A",
                    "enabled": True,
                    "matcher": "template",
                    "action": "click",
                    "bridge_paste_offset": [3, 7],
                    "bridge_friendly_name": "",
                }
            ],
        }

    def re_match_rule(rule_id):
        calls["match"].append(rule_id)
        return calls.get("match_result", [(0.99, (100, 200))])

    def perform_send(point, text, bridge_cfg):
        calls["send"].append((point, text, dict(bridge_cfg)))

    callbacks = BridgeCallbacks(
        cfg_snapshot=cfg_snapshot,
        re_match_rule=re_match_rule,
        perform_send=perform_send,
    )
    service = BridgeService(callbacks)
    app = build_app(service)
    client = TestClient(app)
    yield client, service, calls


def test_send_pipeline_single_match(fastapi_client):
    client, _service, calls = fastapi_client
    res = client.post("/api/send", json={"rule_id": "rule-a", "text": "ship it"})
    assert res.status_code == 200
    body = res.json()
    # match_center=(100,200) + offset=(3,7) = (103, 207).
    assert body["matched_at"] == [103, 207]
    assert calls["match"] == ["rule-a"]
    assert calls["send"][0][0] == (103, 207)
    assert calls["send"][0][1] == "ship it"
    assert calls["send"][0][2]["pre_paste_delay_ms"] == 5


def test_send_pipeline_no_match(fastapi_client):
    client, _service, calls = fastapi_client
    calls["match_result"] = []
    res = client.post("/api/send", json={"rule_id": "rule-a", "text": "x"})
    assert res.status_code == 404


def test_send_pipeline_multiple_matches_returns_409(fastapi_client):
    client, _service, calls = fastapi_client
    calls["match_result"] = [(0.95, (10, 10)), (0.93, (20, 20))]
    res = client.post("/api/send", json={"rule_id": "rule-a", "text": "x"})
    assert res.status_code == 409
    body = res.json()
    assert body["count"] == 2
    assert [m["index"] for m in body["matches"]] == [0, 1]


def test_send_pipeline_multiple_matches_with_index(fastapi_client):
    client, _service, calls = fastapi_client
    calls["match_result"] = [(0.95, (10, 10)), (0.93, (20, 20))]
    res = client.post(
        "/api/send", json={"rule_id": "rule-a", "text": "x", "match_index": 1}
    )
    assert res.status_code == 200
    # 20+3, 20+7
    assert res.json()["matched_at"] == [23, 27]


def test_send_pipeline_unknown_rule_404(fastapi_client):
    client, *_ = fastapi_client
    res = client.post("/api/send", json={"rule_id": "nope", "text": "x"})
    assert res.status_code == 404


# --- ntfy ----------------------------------------------------------------


def test_ntfy_skipped_when_topic_blank():
    """No topic → bail before any network import; never raises."""
    from press_bridge import send_ntfy

    send_ntfy({"ntfy_topic": ""}, {"rule_name": "R", "rule_id": "x"})
    send_ntfy({}, {"rule_name": "R", "rule_id": "x"})


def test_ntfy_post_format(monkeypatch):
    """When httpx is available, ntfy POSTs to {server}/{topic} with a Title header."""
    pytest.importorskip("httpx")
    import press_bridge

    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, content=None, headers=None):
            captured["url"] = url
            captured["body"] = content
            captured["headers"] = headers
            class R:
                status_code = 200
            return R()

    import httpx
    monkeypatch.setattr(httpx, "Client", FakeClient)

    press_bridge.send_ntfy(
        {"ntfy_topic": "demo", "ntfy_server": "https://ntfy.sh/"},
        {
            "rule_name": "Continue",
            "rule_id": "abc",
            "monitor_index": 1,
            "timestamp_iso": "2026-04-29T12:00:00+00:00",
        },
    )
    assert captured["url"] == "https://ntfy.sh/demo"
    assert captured["headers"]["Title"] == "Continue"
    assert "monitor 1" in captured["body"].decode()
