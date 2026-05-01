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
};

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
    state.windows.clear();
    for (const w of data.windows || []) state.windows.set(w.id, w);
    if ((data.windows || []).length) markEvent();
    renderWindows();
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
  for (const text of pending) {
    const li = document.createElement("li");
    li.textContent = text;
    list.appendChild(li);
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
  // Cache-bust each request so the same idx returns the latest snapshot
  // after a tick. Cache-Control: no-store from the server too.
  const bust = Date.now();
  wrap.innerHTML = "";
  for (let i = 0; i < count; i++) {
    const card = document.createElement("div");
    card.className = "snapshot";
    const url = `/api/windows/${encodeURIComponent(id)}/snapshot/${i}?t=${bust}-${i}`;
    card.innerHTML = `
      <img src="${url}" alt="snapshot ${i + 1}" loading="lazy">
      <div class="ts">${i === 0 ? "newest" : `${i} tick${i === 1 ? "" : "s"} ago`}</div>
    `;
    card.addEventListener("click", () => openLightbox(url));
    wrap.appendChild(card);
  }
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

async function sendOrQueue() {
  const id = state.current;
  if (!id) return;
  const text = $("send-text").value.trim();
  if (!text) {
    setSendStatus("Type something first.", "error");
    return;
  }
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
        setSendStatus(`Queued (#${data.position}). Sends on next idle.`, "success");
      } else {
        setSendStatus("Sent.", "success");
      }
      $("send-text").value = "";
    } else if (res.status === 202) {
      const data = await res.json();
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
  try {
    await fetch(`/api/windows/${encodeURIComponent(id)}/queue`, { method: "DELETE" });
    setSendStatus("Queue cleared.", "success");
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

renderNotifToggle();

$("settings-btn").addEventListener("click", () => {
  const panel = $("settings-panel");
  panel.hidden = !panel.hidden;
  $("settings-btn").setAttribute("aria-expanded", String(!panel.hidden));
});
$("notif-toggle").addEventListener("click", toggleNotifications);
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
