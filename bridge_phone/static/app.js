// auto-press phone bridge — vanilla JS, no build step.
//
// Lists configured Cursor windows with idle/busy status from SSE; tap
// in for snapshot thumbnails (click to expand) and a send composer.

const $ = (id) => document.getElementById(id);

const state = {
  windows: new Map(),       // id -> summary dict
  snapshotsPerWindow: 5,
  view: "list",             // "list" | "snapshots"
  current: null,            // window id when in "snapshots" view
  lastEventAt: 0,           // ms epoch of last SSE event for the freshness pill
  notifEnabled: localStorage.getItem("ap.notif") === "1",
  autoReloadEnabled: localStorage.getItem("ap.autoreload") === "1",
  intervalSeconds: 10,      // populated from /api/state
};

const AUTO_RELOAD_DEFAULT_S = 10;

// ---- SSE ----------------------------------------------------------------

let sse;
let backoff = 1000;
function connectSSE() {
  if (sse) try { sse.close(); } catch {}
  sse = new EventSource("/api/events");
  sse.onopen = () => { backoff = 1000; markEvent(); };
  sse.addEventListener("window_state", (ev) => {
    markEvent();
    try {
      const data = JSON.parse(ev.data);
      if (!data || !data.id) return;
      const prev = state.windows.get(data.id);
      maybeNotify(prev, data);
      state.windows.set(data.id, data);
      renderWindows();
      if (state.view === "snapshots" && state.current === data.id) {
        const snapshotsGrew =
          !prev || (data.snapshot_count || 0) > (prev.snapshot_count || 0);
        renderWindowDetail(snapshotsGrew);
      }
    } catch {}
  });
  sse.addEventListener("rule_matched", () => markEvent());
  sse.onerror = () => {
    try { sse.close(); } catch {}
    setTimeout(connectSSE, backoff);
    backoff = Math.min(backoff * 2, 30000);
  };
}

function markEvent() {
  state.lastEventAt = Date.now();
  renderFreshness();
}

function renderFreshness() {
  const el = $("freshness");
  if (!state.lastEventAt) {
    el.textContent = "—";
    el.className = "freshness";
    return;
  }
  const sec = Math.floor((Date.now() - state.lastEventAt) / 1000);
  el.textContent = sec < 1 ? "just now" : `${sec}s ago`;
  el.className =
    "freshness " + (sec > 30 ? "stale" : sec > 15 ? "" : "live");
}
setInterval(renderFreshness, 1000);

// ---- initial paint ------------------------------------------------------

async function loadState() {
  try {
    const res = await fetch("/api/state");
    if (!res.ok) return;
    const data = await res.json();
    state.snapshotsPerWindow = data.snapshots_per_window || 5;
    state.intervalSeconds = data.interval_seconds || AUTO_RELOAD_DEFAULT_S;
    state.windows.clear();
    for (const w of data.windows || []) state.windows.set(w.id, w);
    if ((data.windows || []).length) markEvent();
    renderWindows();
    applyAutoReload();
  } catch {}
}

// ---- views --------------------------------------------------------------

function renderWindows() {
  const container = $("windows");
  const empty = $("empty-hint");
  container.innerHTML = "";
  const all = [...state.windows.values()];
  empty.hidden = all.length > 0;
  for (const w of all) {
    const li = document.createElement("li");
    li.className = "window " + (w.idle ? "idle" : "busy");
    li.dataset.id = w.id;
    const pendingTag =
      (w.pending && w.pending.length)
        ? ` · ${w.pending.length} queued`
        : "";
    li.innerHTML = `
      <span class="dot" aria-hidden="true"></span>
      <div class="meta">
        <div class="name">${escapeHtml(w.name || w.id)}</div>
        <div class="sub">${w.idle ? "idle" : "busy"} · ${formatScore(w.score)}${pendingTag}</div>
      </div>
      <span class="chev" aria-hidden="true">›</span>
    `;
    li.addEventListener("click", () => openWindow(w.id));
    container.appendChild(li);
  }
}

function openWindow(id) {
  state.view = "snapshots";
  state.current = id;
  $("windows-section").hidden = true;
  $("snapshots-section").hidden = false;
  $("send-text").value = "";
  setSendStatus("");
  renderWindowDetail(true);
}

function closeSnapshots() {
  state.view = "list";
  state.current = null;
  $("snapshots-section").hidden = true;
  $("windows-section").hidden = false;
}

function renderWindowDetail(refetchSnapshots) {
  const id = state.current;
  if (!id) return;
  const w = state.windows.get(id);
  $("snap-window-name").textContent = w ? (w.name || id) : id;
  $("snap-status").textContent = w
    ? `${w.idle ? "idle" : "busy"} · ${formatScore(w.score)}`
    : "—";
  renderQueue();
  if (refetchSnapshots) renderSnapshots();
}

function renderQueue() {
  const id = state.current;
  const w = id ? state.windows.get(id) : null;
  const pending = (w && w.pending) || [];
  const section = $("queue-section");
  section.hidden = pending.length === 0;
  $("queue-count").textContent = pending.length ? `(${pending.length})` : "";
  const list = $("queue-list");
  list.innerHTML = "";
  for (let i = 0; i < pending.length; i++) {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "queue-text";
    span.textContent = pending[i];
    const btn = document.createElement("button");
    btn.className = "queue-send-now";
    btn.textContent = "Send now";
    const idx = i;
    btn.addEventListener("click", () => sendQueuedNow(idx, btn));
    li.appendChild(span);
    li.appendChild(btn);
    list.appendChild(li);
  }
}

async function sendQueuedNow(idx, btn) {
  const id = state.current;
  if (!id) return;
  btn.disabled = true;
  btn.textContent = "Sending…";
  try {
    const res = await fetch(
      `/api/windows/${encodeURIComponent(id)}/queue/${idx}/send_now`,
      { method: "POST" }
    );
    if (!res.ok) {
      const detail = await res.text();
      setSendStatus(`Send-now failed: ${res.status} ${detail}`, "error");
      btn.disabled = false;
      btn.textContent = "Send now";
    } else {
      // Optimistic remove so the row disappears immediately. The
      // window_state SSE event lands shortly after and overwrites with
      // the authoritative server view (no-op if it matches).
      patchPendingOptimistic((p) => p.filter((_, i) => i !== idx));
      setSendStatus("Sent.", "success");
    }
  } catch (e) {
    setSendStatus(`Network error: ${e.message}`, "error");
    btn.disabled = false;
    btn.textContent = "Send now";
  }
}

function renderSnapshots() {
  const id = state.current;
  if (!id) return;
  const w = state.windows.get(id);
  const count = w ? Math.min(w.snapshot_count || 0, state.snapshotsPerWindow) : 0;
  const empty = $("snap-empty");
  const wrap = $("snapshots");
  empty.hidden = count > 0;
  wrap.innerHTML = "";
  if (count === 0) return;
  // Cache-bust so the same idx returns the latest snapshot after a
  // re-capture. Cache-Control: no-store from the server too.
  const bust = Date.now();
  const url = `/api/windows/${encodeURIComponent(id)}/snapshot/0?t=${bust}`;
  const card = document.createElement("div");
  card.className = "snapshot";
  const tsLabel = w.snapshot_at ? `captured ${formatRelativeTime(w.snapshot_at)}` : "captured";
  card.innerHTML = `
    <img src="${url}" alt="last snapshot" loading="lazy">
    <div class="ts">${tsLabel}</div>
  `;
  card.addEventListener("click", () => openLightbox(url));
  wrap.appendChild(card);
}

function formatRelativeTime(iso) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (isNaN(t)) return "—";
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ago`;
}

function openLightbox(url) {
  $("lightbox-img").src = url;
  $("lightbox").hidden = false;
}
function closeLightbox() {
  $("lightbox").hidden = true;
  $("lightbox-img").src = "";
}

function setSendStatus(msg, kind) {
  const el = $("send-status");
  el.textContent = msg || "";
  el.className = "status" + (kind ? " " + kind : "");
}

// Apply a function to the current window's pending list and re-render
// immediately, without waiting for the SSE round-trip. Whenever the
// window_state event eventually lands, it overwrites this with the
// authoritative server view; until then the user sees the result of
// their tap right away.
function patchPendingOptimistic(transform) {
  const id = state.current;
  if (!id) return;
  const w = state.windows.get(id);
  if (!w) return;
  const next = { ...w, pending: transform(w.pending || []) };
  state.windows.set(id, next);
  renderWindows();
  renderWindowDetail(false);
}

async function sendOrQueue() {
  const id = state.current;
  if (!id) return;
  // Allow empty / whitespace — that's the "just press Enter" case for
  // when the user already typed the message in the target window from
  // the laptop. No client-side .trim(); the bridge accepts any string.
  const text = $("send-text").value;
  setSendStatus("Sending…");
  try {
    const res = await fetch(
      `/api/windows/${encodeURIComponent(id)}/send`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      }
    );
    if (res.ok) {
      const data = await res.json();
      if (data.queued) {
        // Optimistic append so the queue list shows the new item without
        // waiting for the SSE event.
        patchPendingOptimistic((p) => [...p, text]);
        setSendStatus(`Queued (#${data.position}). Sends on next idle.`, "success");
      } else {
        setSendStatus("Sent.", "success");
      }
      $("send-text").value = "";
    } else if (res.status === 202) {
      const data = await res.json();
      patchPendingOptimistic((p) => [...p, text]);
      setSendStatus(`Queued (#${data.position}). Sends on next idle.`, "success");
      $("send-text").value = "";
    } else {
      const detail = await res.text();
      setSendStatus(`Error ${res.status}: ${detail}`, "error");
    }
  } catch (e) {
    setSendStatus(`Network error: ${e.message}`, "error");
  }
}

async function clearQueue() {
  const id = state.current;
  if (!id) return;
  // Optimistic empty so the list collapses immediately. If the server
  // 500s, the SSE-driven re-sync will put the items back.
  patchPendingOptimistic(() => []);
  try {
    const res = await fetch(`/api/windows/${encodeURIComponent(id)}/queue`, { method: "DELETE" });
    if (res.ok) setSendStatus("Queue cleared.", "success");
    else setSendStatus(`Clear failed: ${res.status}`, "error");
  } catch (e) {
    setSendStatus(`Could not clear queue: ${e.message}`, "error");
  }
}

// ---- helpers ------------------------------------------------------------

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function formatScore(s) {
  if (typeof s !== "number" || isNaN(s)) return "—";
  return `score ${s.toFixed(2)}`;
}

// ---- settings / notifications / admin reload ---------------------------

function renderNotifToggle() {
  const btn = $("notif-toggle");
  btn.textContent = state.notifEnabled ? "on" : "off";
  btn.classList.toggle("on", state.notifEnabled);
}

function maybeNotify(prev, next) {
  // Only fire on a real busy → idle transition while the toggle is on.
  if (!state.notifEnabled) return;
  if (!prev || prev.idle === next.idle) return;
  if (!next.idle) return;
  if (typeof Notification === "undefined") return;
  if (Notification.permission !== "granted") return;
  try {
    const n = new Notification(next.name || "Cursor", {
      body: "is idle — ready for input",
      icon: "/static/favicon.svg",
      tag: `idle-${next.id}`,    // dedupe rapid duplicates
    });
    n.onclick = () => { window.focus(); n.close(); };
  } catch {}
}

async function toggleNotifications() {
  if (state.notifEnabled) {
    state.notifEnabled = false;
    localStorage.setItem("ap.notif", "0");
    renderNotifToggle();
    return;
  }
  if (typeof Notification === "undefined") {
    alert("This browser doesn't expose the Notification API.");
    return;
  }
  if (Notification.permission === "denied") {
    alert("Notifications are blocked for this site. Enable them in browser settings.");
    return;
  }
  if (Notification.permission !== "granted") {
    const result = await Notification.requestPermission();
    if (result !== "granted") return;
  }
  state.notifEnabled = true;
  localStorage.setItem("ap.notif", "1");
  renderNotifToggle();
}

async function reloadBridge() {
  const ok = confirm(
    "Reload the bridge service? The connection will drop for ~1 second " +
    "and reconnect automatically. If the new code fails to import, the " +
    "current service stays up."
  );
  if (!ok) return;
  const btn = $("reload-btn");
  btn.disabled = true;
  btn.textContent = "Reloading…";
  try {
    const res = await fetch("/api/admin/reload", { method: "POST" });
    if (!res.ok) {
      const detail = await res.text();
      alert(`Reload failed: ${res.status} ${detail}`);
    }
  } catch (e) {
    // Expected: the request was in flight when the listener was torn down.
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "Reload";
    }, 2000);
  }
}

// ---- auto-reload (page refresh on a fixed cadence) ---------------------

let autoReloadHandle = null;

function renderAutoReloadToggle() {
  const btn = $("autoreload-toggle");
  btn.textContent = state.autoReloadEnabled ? "on" : "off";
  btn.classList.toggle("on", state.autoReloadEnabled);
}

function applyAutoReload() {
  if (autoReloadHandle) {
    clearInterval(autoReloadHandle);
    autoReloadHandle = null;
  }
  if (!state.autoReloadEnabled) return;
  // Match the bridge tick cadence; clamp so a sub-second interval can't
  // turn this into a reload-storm if someone mis-configures.
  const ms = Math.max(2000, Math.round(state.intervalSeconds * 1000));
  autoReloadHandle = setInterval(() => {
    // Don't reload while typing into the composer — losing draft text on
    // every interval would make the composer unusable.
    const active = document.activeElement;
    if (active && (active.tagName === "TEXTAREA" || active.tagName === "INPUT")) {
      return;
    }
    location.reload();
  }, ms);
}

function toggleAutoReload() {
  state.autoReloadEnabled = !state.autoReloadEnabled;
  localStorage.setItem("ap.autoreload", state.autoReloadEnabled ? "1" : "0");
  renderAutoReloadToggle();
  applyAutoReload();
}

renderNotifToggle();
renderAutoReloadToggle();

$("settings-btn").addEventListener("click", () => {
  const panel = $("settings-panel");
  panel.hidden = !panel.hidden;
  $("settings-btn").setAttribute("aria-expanded", String(!panel.hidden));
});
$("notif-toggle").addEventListener("click", toggleNotifications);
$("autoreload-toggle").addEventListener("click", toggleAutoReload);
$("reload-btn").addEventListener("click", reloadBridge);

$("snap-back").addEventListener("click", closeSnapshots);
$("send-go").addEventListener("click", sendOrQueue);
$("queue-clear").addEventListener("click", clearQueue);
$("send-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    sendOrQueue();
  }
});
$("lightbox").addEventListener("click", (e) => {
  // Click on backdrop or close button closes; clicking the image itself
  // doesn't, so the user can pinch-zoom on mobile.
  if (e.target === $("lightbox") || e.target === $("lightbox-close")) {
    closeLightbox();
  }
});
$("lightbox-close").addEventListener("click", closeLightbox);

loadState();
connectSSE();
