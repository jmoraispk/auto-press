"""Optional remote bridge for auto-press.

Exposes a tiny FastAPI app over Tailscale that lets a phone:
  1. receive notifications when rules fire (via ntfy)
  2. stream recent rule events over SSE
  3. paste text from the phone into the matched Cursor chat input

Designed to add zero overhead when ``bridge.enabled`` is False — the server
thread is only started by ``MainWindow`` when the user opts in.
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
from pathlib import Path
from typing import Any, Callable, Optional


LOG = logging.getLogger("press_bridge")

EVENT_BUFFER_MAX = 100
SSE_KEEPALIVE_SECS = 15.0

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
        with self._lock:
            self._buffer.append(event)
            subs = list(self._subscribers)
        loop = self._loop
        if loop is None:
            return
        for queue in subs:
            with suppress(Exception):
                loop.call_soon_threadsafe(queue.put_nowait, event)

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
        rules = []
        for rule in cfg.get("rules", []):
            rules.append(
                {
                    "id": rule.get("id"),
                    "name": rule.get("name"),
                    "friendly_name": rule.get("bridge_friendly_name") or rule.get("name"),
                    "enabled": bool(rule.get("enabled", True)),
                    "matcher": rule.get("matcher"),
                    "action": rule.get("action"),
                    "read_strategy": rule.get("bridge_read_strategy", "none"),
                    "has_read_region": bool(rule.get("bridge_read_region")),
                }
            )
        return JSONResponse(
            {
                "rules": rules,
                "events": service.hub.recent(),
                "interval_seconds": cfg.get("interval_seconds", 10.0),
            }
        )

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        queue = service.hub.subscribe()

        async def stream():
            # Replay buffered events first so a fresh phone doesn't have to
            # call /api/state separately to backfill.
            for event in service.hub.recent():
                yield f"event: rule_matched\ndata: {json.dumps(event)}\n\n"
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_SECS)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"event: rule_matched\ndata: {json.dumps(event)}\n\n"
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
