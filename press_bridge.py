"""Optional remote bridge for auto-press.

Exposes a tiny FastAPI app over Tailscale that lets a phone:
  1. see which Cursor windows are idle vs busy
  2. browse the last N PNG snapshots of each window
  3. receive ntfy notifications when a window flips to idle
  4. (planned) send a reply into a specific window

Designed to add zero overhead when the bridge service is off — the
server thread is only started when the user flips the toggle on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


LOG = logging.getLogger("press_bridge")

EVENT_BUFFER_MAX = 100
SSE_KEEPALIVE_SECS = 15.0
SNAPSHOTS_PER_WINDOW = 5

PHONE_DIR = Path(__file__).resolve().parent / "bridge_phone"


# ---- pluggable callbacks supplied by the host (press_ui.MainWindow) -----


@dataclass
class BridgeCallbacks:
    """Surface area the bridge needs from the rest of the app.

    Keeping this as a dataclass of callables (rather than importing press_ui)
    lets press_bridge run headless in tests and avoids a Qt import cycle.
    """

    cfg_snapshot: Callable[[], dict]
    re_match_rule: Callable[[str], list[tuple[float, tuple[int, int]]]]
    perform_send: Callable[[tuple[int, int], str, dict], None]
    perform_read: Optional[Callable[[str, dict], Optional[str]]] = None


# ---- per-window state + snapshot ring buffer ----------------------------


class WindowStore:
    """Thread-safe store of latest window states + a small ring of PNG
    snapshots per window. Bounded to ``snapshots_per_window`` so memory
    stays predictable; older snapshots fall off when new ones land."""

    def __init__(self, snapshots_per_window: int = SNAPSHOTS_PER_WINDOW) -> None:
        self._max = max(1, int(snapshots_per_window))
        self._lock = threading.Lock()
        # window_id -> {"state": dict, "snapshots": deque[(iso_ts, png_bytes)]}
        self._windows: dict[str, dict] = {}

    def update(self, states: list[dict], images: dict[str, bytes]) -> list[dict]:
        """Apply a fresh detector tick. Returns the list of windows whose
        idle/busy state changed since the previous tick — callers can use
        this to decide which transitions to surface (SSE, ntfy)."""
        now = datetime.now(timezone.utc).isoformat()
        transitions: list[dict] = []
        with self._lock:
            seen_ids: set[str] = set()
            for state in states:
                wid = state.get("id")
                if not wid:
                    continue
                seen_ids.add(wid)
                entry = self._windows.setdefault(
                    wid, {"state": {}, "snapshots": deque(maxlen=self._max)}
                )
                prev_idle = entry["state"].get("idle") if entry["state"] else None
                stored = {
                    "id": wid,
                    "name": state.get("name", "Cursor"),
                    "idle": bool(state.get("idle")),
                    "score": float(state.get("score", 0.0)),
                    "configured": bool(state.get("configured", False)),
                    "last_update": now,
                }
                entry["state"] = stored
                png = images.get(wid)
                if png is not None:
                    entry["snapshots"].append((now, png))
                if prev_idle is not None and prev_idle != stored["idle"]:
                    transitions.append(stored)
            # Drop windows that disappeared from config (e.g. user removed).
            for wid in list(self._windows):
                if wid not in seen_ids:
                    self._windows.pop(wid, None)
        return transitions

    def summaries(self) -> list[dict]:
        with self._lock:
            return [
                dict(entry["state"], snapshot_count=len(entry["snapshots"]))
                for entry in self._windows.values()
                if entry["state"]
            ]

    def snapshot(self, window_id: str, idx: int) -> Optional[tuple[str, bytes]]:
        """Snapshot at ``idx`` (0 = newest). Returns (iso_ts, png_bytes)."""
        with self._lock:
            entry = self._windows.get(window_id)
            if entry is None:
                return None
            snaps = list(entry["snapshots"])
        if not (0 <= idx < len(snaps)):
            return None
        # idx 0 is newest, deque appends new on the right.
        return snaps[len(snaps) - 1 - idx]

    def clear_window(self, window_id: str) -> None:
        with self._lock:
            self._windows.pop(window_id, None)


# ---- in-process event hub -----------------------------------------------


class EventHub:
    """Thread-safe ring buffer + asyncio fan-out for rule_matched events.

    The Qt worker emits via ``publish`` (any thread). SSE clients in the
    asyncio loop each get their own ``asyncio.Queue`` registered through
    ``subscribe`` and torn down via ``unsubscribe``. ``dismiss`` flips a
    flag on a buffered event so the phone UI can hide it.
    """

    def __init__(self, maxlen: int = EVENT_BUFFER_MAX) -> None:
        self._buffer: deque[dict] = deque(maxlen=maxlen)
        self._dismissed: set[str] = set()
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: list[asyncio.Queue] = []

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: dict) -> None:
        """Publish a rule_matched event (back-compat name for the existing
        SSE channel). Equivalent to publish_typed("rule_matched", event)."""
        self.publish_typed("rule_matched", event)

    def publish_typed(self, event_type: str, event: dict) -> None:
        """Publish an event tagged with an SSE event-name. Subscribers
        receive a tuple (event_type, payload) and the SSE handler routes
        it to the matching named channel (event: <event_type>)."""
        payload = (event_type, event)
        with self._lock:
            # Keep only rule_matched in the persistent buffer; window_state
            # events are ephemeral (the WindowStore is the source of truth).
            if event_type == "rule_matched":
                self._buffer.append(event)
            subs = list(self._subscribers)
        loop = self._loop
        if loop is None:
            return
        for queue in subs:
            with suppress(Exception):
                loop.call_soon_threadsafe(queue.put_nowait, payload)

    def recent(self) -> list[dict]:
        with self._lock:
            return [dict(e, dismissed=(e["event_id"] in self._dismissed)) for e in self._buffer]

    def find(self, event_id: str) -> Optional[dict]:
        with self._lock:
            for event in self._buffer:
                if event.get("event_id") == event_id:
                    return dict(event)
        return None

    def dismiss(self, event_id: str) -> bool:
        with self._lock:
            for event in self._buffer:
                if event.get("event_id") == event_id:
                    self._dismissed.add(event_id)
                    return True
        return False

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            with suppress(ValueError):
                self._subscribers.remove(queue)


# ---- ntfy notifier (best-effort) ----------------------------------------


def send_ntfy(bridge_cfg: dict, event: dict) -> None:
    """Fire-and-forget notification. Failures only log; never raise."""
    topic = (bridge_cfg.get("ntfy_topic") or "").strip()
    if not topic:
        return
    server = (bridge_cfg.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    rule_name = event.get("rule_name") or event.get("rule_id") or "Rule"
    monitor = event.get("monitor_index", -1)
    title = str(rule_name)
    body = f"{event.get('timestamp_iso','?')} · monitor {monitor}"
    url = f"{server}/{topic}"
    try:
        import httpx  # type: ignore

        with httpx.Client(timeout=5.0) as client:
            client.post(
                url,
                content=body.encode("utf-8"),
                headers={
                    "Title": title,
                    "Tags": "computer",
                    # ntfy custom click-action: open the phone UI focused on
                    # this rule. Hosting URL is unknown here, so we just use
                    # the relative path; ntfy lets the user override.
                    "Click": f"/?focus={event.get('rule_id','')}",
                },
            )
    except Exception as exc:
        LOG.warning("ntfy publish failed: %s", exc)


# ---- bridge service (started/stopped by MainWindow) ---------------------


class BridgeService:
    def __init__(self, callbacks: BridgeCallbacks) -> None:
        self.callbacks = callbacks
        self.hub = EventHub()
        self.windows = WindowStore()
        self._thread: Optional[threading.Thread] = None
        self._server: Any = None  # uvicorn.Server
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._cfg_snapshot_at_start: dict = {}

    # --- lifecycle ---

    def start(self, bridge_cfg: dict) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._cfg_snapshot_at_start = dict(bridge_cfg)
        self._thread = threading.Thread(target=self._run, daemon=True, name="bridge")
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        server = self._server
        if server is not None:
            server.should_exit = True
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        self._server = None
        self._loop = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def publish_event(self, event: dict) -> None:
        """Called from any thread when a rule matches."""
        self.hub.publish(event)
        # Notify in a worker thread so a slow ntfy server can't stall the
        # Qt event loop.
        bridge_cfg = self._current_bridge_cfg()
        if bridge_cfg.get("ntfy_topic"):
            threading.Thread(
                target=send_ntfy, args=(bridge_cfg, event), daemon=True
            ).start()

    def update_window_states(self, states: list[dict], images: dict[str, bytes]) -> None:
        """Called from the engine worker every detection tick. Updates the
        ring buffer, fans state out over SSE, and fires ntfy on a window
        flipping busy → idle."""
        transitions = self.windows.update(states, images)
        # Always fan a "window_state" event so phone clients can stay in
        # sync without polling /api/state.
        for state in self.windows.summaries():
            self.hub.publish_typed("window_state", state)
        # Notify only on busy → idle (more interesting than the reverse).
        bridge_cfg = self._current_bridge_cfg()
        if bridge_cfg.get("ntfy_topic"):
            for tr in transitions:
                if tr.get("idle"):
                    threading.Thread(
                        target=send_ntfy,
                        args=(bridge_cfg, {
                            "rule_name": tr.get("name"),
                            "rule_id": tr.get("id"),
                            "monitor_index": -1,
                            "timestamp_iso": tr.get("last_update", ""),
                        }),
                        daemon=True,
                    ).start()

    def _current_bridge_cfg(self) -> dict:
        try:
            cfg = self.callbacks.cfg_snapshot() or {}
        except Exception:
            cfg = {}
        return dict(cfg.get("bridge") or self._cfg_snapshot_at_start or {})

    # --- thread entrypoint ---

    def _run(self) -> None:
        try:
            import uvicorn  # type: ignore
        except Exception as exc:
            LOG.error("bridge: uvicorn not installed (%s); install with: uv sync", exc)
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self.hub.attach_loop(loop)

        app = build_app(self)

        cfg = self._cfg_snapshot_at_start
        host = cfg.get("host", "0.0.0.0")
        port = int(cfg.get("port", 8765))
        config = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="off")
        server = uvicorn.Server(config)
        self._server = server
        try:
            loop.run_until_complete(server.serve())
        except Exception as exc:
            LOG.error("bridge server crashed: %s", exc)
        finally:
            with suppress(Exception):
                loop.close()


# ---- FastAPI app ---------------------------------------------------------


def build_app(service: BridgeService):
    """Construct the FastAPI app. Imported lazily so the rest of the file
    is testable without FastAPI installed."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import (
        FileResponse,
        JSONResponse,
        PlainTextResponse,
        Response,
        StreamingResponse,
    )
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="auto-press bridge", docs_url=None, redoc_url=None)
    started_at = time.time()

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True, "uptime_s": int(time.time() - started_at)})

    @app.get("/api/state")
    async def state() -> JSONResponse:
        cfg = service.callbacks.cfg_snapshot()
        return JSONResponse(
            {
                "windows": service.windows.summaries(),
                "events": service.hub.recent(),
                "interval_seconds": cfg.get("interval_seconds", 10.0),
                "snapshots_per_window": SNAPSHOTS_PER_WINDOW,
            }
        )

    @app.get("/api/windows")
    async def windows_list() -> JSONResponse:
        return JSONResponse(service.windows.summaries())

    @app.get("/api/windows/{window_id}/snapshot/{idx}")
    async def window_snapshot(window_id: str, idx: int) -> Response:
        snap = service.windows.snapshot(window_id, idx)
        if snap is None:
            raise HTTPException(status_code=404, detail="snapshot not available")
        ts, png = snap
        return Response(
            content=png,
            media_type="image/png",
            headers={
                "X-Snapshot-Timestamp": ts,
                # Each snapshot at a given (window, idx) shifts every tick,
                # so the phone shouldn't cache them — bypass.
                "Cache-Control": "no-store",
            },
        )

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        queue = service.hub.subscribe()

        async def stream():
            # Replay buffered rule events first so a freshly-connected
            # phone doesn't have to /api/state to backfill rule history.
            for event in service.hub.recent():
                yield f"event: rule_matched\ndata: {json.dumps(event)}\n\n"
            # And current window summaries, as window_state events.
            for w in service.windows.summaries():
                yield f"event: window_state\ndata: {json.dumps(w)}\n\n"
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.wait_for(
                            queue.get(), timeout=SSE_KEEPALIVE_SECS
                        )
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if isinstance(payload, tuple):
                        ev_type, ev_data = payload
                    else:  # legacy: untagged events default to rule_matched
                        ev_type, ev_data = "rule_matched", payload
                    yield f"event: {ev_type}\ndata: {json.dumps(ev_data)}\n\n"
            finally:
                service.hub.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/send")
    async def send(payload: dict) -> JSONResponse:
        rule_id = payload.get("rule_id")
        text = payload.get("text")
        match_index = payload.get("match_index")
        if not isinstance(rule_id, str) or not rule_id:
            raise HTTPException(status_code=400, detail="rule_id required")
        if not isinstance(text, str) or not text:
            raise HTTPException(status_code=400, detail="text required")

        cfg = service.callbacks.cfg_snapshot()
        rule = next((r for r in cfg.get("rules", []) if r.get("id") == rule_id), None)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")

        # Re-run matching now. Cached coords lie when windows have moved.
        loop = asyncio.get_running_loop()
        try:
            matches = await loop.run_in_executor(None, service.callbacks.re_match_rule, rule_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"match failed: {exc}") from exc
        if not matches:
            raise HTTPException(status_code=404, detail="no matches for rule right now")

        if match_index is None:
            if len(matches) > 1:
                return JSONResponse(
                    status_code=409,
                    content={
                        "count": len(matches),
                        "matches": [
                            {"index": i, "score": float(s), "center": [int(c[0]), int(c[1])]}
                            for i, (s, c) in enumerate(matches)
                        ],
                    },
                )
            chosen = matches[0]
        else:
            try:
                idx = int(match_index)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="match_index must be int")
            if not 0 <= idx < len(matches):
                raise HTTPException(status_code=400, detail="match_index out of range")
            chosen = matches[idx]

        score, center = chosen
        offset = rule.get("bridge_paste_offset") or [0, 0]
        try:
            paste_point = (int(center[0]) + int(offset[0]), int(center[1]) + int(offset[1]))
        except (TypeError, ValueError):
            paste_point = (int(center[0]), int(center[1]))

        bridge_cfg = cfg.get("bridge") or {}
        t0 = time.monotonic()
        try:
            await loop.run_in_executor(
                None, service.callbacks.perform_send, paste_point, text, bridge_cfg
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"send failed: {exc}") from exc
        dur_ms = int((time.monotonic() - t0) * 1000)
        return JSONResponse(
            {
                "success": True,
                "matched_at": [int(paste_point[0]), int(paste_point[1])],
                "score": float(score),
                "duration_ms": dur_ms,
            }
        )

    @app.post("/api/dismiss/{event_id}")
    async def dismiss(event_id: str) -> JSONResponse:
        ok = service.hub.dismiss(event_id)
        if not ok:
            raise HTTPException(status_code=404, detail="event not found")
        return JSONResponse({"success": True})

    @app.post("/api/refresh_targets")
    async def refresh_targets() -> JSONResponse:
        # Same body as /api/state; phone uses this as a "force re-scan" pull.
        cfg = service.callbacks.cfg_snapshot()
        return JSONResponse(
            {
                "rules": [
                    {
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "friendly_name": r.get("bridge_friendly_name") or r.get("name"),
                    }
                    for r in cfg.get("rules", [])
                ]
            }
        )

    @app.post("/api/read")
    async def read(payload: dict) -> JSONResponse:
        rule_id = payload.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            raise HTTPException(status_code=400, detail="rule_id required")
        if service.callbacks.perform_read is None:
            raise HTTPException(status_code=501, detail="read not implemented")
        cfg = service.callbacks.cfg_snapshot()
        rule = next((r for r in cfg.get("rules", []) if r.get("id") == rule_id), None)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        loop = asyncio.get_running_loop()
        try:
            text = await loop.run_in_executor(
                None, service.callbacks.perform_read, rule_id, cfg.get("bridge") or {}
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"read failed: {exc}") from exc
        return JSONResponse({"text": text or ""})

    # --- phone UI ---

    @app.get("/")
    async def root() -> Response:
        index = PHONE_DIR / "index.html"
        if not index.exists():
            return PlainTextResponse("bridge_phone/ missing", status_code=500)
        return FileResponse(index)

    static_dir = PHONE_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/manifest.webmanifest")
    async def manifest() -> Response:
        path = static_dir / "manifest.webmanifest"
        if not path.exists():
            return PlainTextResponse("manifest missing", status_code=404)
        return FileResponse(path, media_type="application/manifest+json")

    return app
