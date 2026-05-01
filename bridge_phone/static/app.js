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
  rulesRunning: false,      // populated from /api/state; toggle in settings
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
    state.rulesRunning = Boolean(data.rules_running);
    state.windows.clear();
    for (const w of data.windows || []) state.windows.set(w.id, w);
    if ((data.windows || []).length) markEvent();
    renderWindows();
    renderRulesToggle();
    applyAutoReload();
    // After auto-refresh / page reload, restore the previously open
    // window if one was selected and still exists.
    let saved = null;
    try { saved = sessionStorage.getItem("ap.current"); } catch {}
    if (saved && state.windows.has(saved)) {
      openWindow(saved);
    }
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
    const isSelected = state.current === w.id;
    li.className =
      "window " +
      (w.idle ? "idle" : "busy") +
      (isSelected ? " selected" : "");
    li.dataset.id = w.id;
    const pendingTag =
      (w.pending && w.pending.length)
        ? ` · ${w.pending.length} queued`
        : "";
    li.innerHTML = `
      <div class="window-head">
        <span class="dot" aria-hidden="true"></span>
        <div class="name"></div>
        <button class="window-rename" title="Rename" aria-label="Rename">✎</button>
      </div>
      <div class="sub">${w.idle ? "idle" : "busy"}${pendingTag}</div>
    `;
    li.querySelector(".name").textContent = w.name || w.id;
    li.querySelector(".window-rename").addEventListener("click", (e) => {
      e.stopPropagation();
      promptRenameWindow(w);
    });
    li.addEventListener("click", () => {
      // Toggle: tapping the selected row collapses the detail back to
      // the overview; tapping any other row switches.
      if (state.current === w.id) {
        closeSnapshots();
      } else {
        openWindow(w.id);
      }
    });
    container.appendChild(li);
  }
}

function openWindow(id) {
  state.view = "snapshots";
  state.current = id;
  // Master-detail: keep the windows list visible above the detail
  // panel. The selected row is highlighted via renderWindows.
  $("snapshots-section").hidden = false;
  $("send-text").value = "";
  setSendStatus("");
  // Persist so an auto-refresh / accidental reload restores the view.
  try { sessionStorage.setItem("ap.current", id); } catch {}
  renderWindows();
  renderWindowDetail(true);
}

function closeSnapshots() {
  state.view = "list";
  state.current = null;
  $("snapshots-section").hidden = true;
  try { sessionStorage.removeItem("ap.current"); } catch {}
  renderWindows();
}

async function promptRenameWindow(w) {
  const current = w.name || w.id;
  const next = prompt("Rename window", current);
  if (next === null) return;
  const trimmed = next.trim();
  if (!trimmed || trimmed === current) return;
  // Optimistic local update so the rename is visible immediately.
  const stored = state.windows.get(w.id);
  if (stored) state.windows.set(w.id, { ...stored, name: trimmed });
  renderWindows();
  if (state.view === "snapshots" && state.current === w.id) {
    renderWindowDetail(false);
  }
  try {
    const res = await fetch(`/api/windows/${encodeURIComponent(w.id)}/name`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: trimmed }),
    });
    if (!res.ok) {
      const detail = await res.text();
      alert(`Rename failed: ${res.status} ${detail}`);
      // Roll back optimistic update.
      if (stored) state.windows.set(w.id, stored);
      renderWindows();
    }
  } catch (e) {
    alert(`Rename network error: ${e.message}`);
    if (stored) state.windows.set(w.id, stored);
    renderWindows();
  }
}

function renderWindowDetail(refetchSnapshots) {
  const id = state.current;
  if (!id) return;
  const w = state.windows.get(id);
  $("snap-window-name").textContent = w ? (w.name || id) : id;
  $("snap-status").textContent = w ? (w.idle ? "idle" : "busy") : "—";
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
    list.appendChild(li);
    renderQueueRow(li, i, pending[i], false);
  }
}

function renderQueueRow(li, idx, text, editing) {
  li.innerHTML = "";
  li.classList.toggle("editing", editing);
  if (editing) {
    const ta = document.createElement("textarea");
    ta.className = "queue-edit-text";
    ta.rows = 2;
    ta.value = text;
    const saveBtn = document.createElement("button");
    saveBtn.className = "queue-save";
    saveBtn.textContent = "Save";
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "queue-cancel ghost";
    cancelBtn.textContent = "Cancel";
    saveBtn.addEventListener("click", () => saveQueuedEdit(idx, ta.value, li, text));
    cancelBtn.addEventListener("click", () => renderQueueRow(li, idx, text, false));
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        saveQueuedEdit(idx, ta.value, li, text);
      } else if (e.key === "Escape") {
        e.preventDefault();
        renderQueueRow(li, idx, text, false);
      }
    });
    li.appendChild(ta);
    li.appendChild(saveBtn);
    li.appendChild(cancelBtn);
    setTimeout(() => ta.focus(), 0);
    return;
  }
  const span = document.createElement("span");
  span.className = "queue-text";
  span.textContent = text;
  const editBtn = document.createElement("button");
  editBtn.className = "queue-edit";
  editBtn.textContent = "✎";
  editBtn.title = "Edit";
  editBtn.setAttribute("aria-label", "Edit message");
  const sendBtn = document.createElement("button");
  sendBtn.className = "queue-send-now";
  sendBtn.textContent = "Send now";
  const delBtn = document.createElement("button");
  delBtn.className = "queue-delete";
  delBtn.textContent = "✕";
  delBtn.title = "Remove from queue";
  delBtn.setAttribute("aria-label", "Remove from queue");
  editBtn.addEventListener("click", () => renderQueueRow(li, idx, text, true));
  sendBtn.addEventListener("click", () => sendQueuedNow(idx, sendBtn));
  delBtn.addEventListener("click", () => deleteQueuedItem(idx, delBtn));
  li.appendChild(span);
  li.appendChild(editBtn);
  li.appendChild(sendBtn);
  li.appendChild(delBtn);
}

async function saveQueuedEdit(idx, newText, li, prevText) {
  const id = state.current;
  if (!id) return;
  // Optimistic update: replace local pending entry, exit edit mode.
  patchPendingOptimistic((p) => p.map((t, i) => (i === idx ? newText : t)));
  try {
    const res = await fetch(
      `/api/windows/${encodeURIComponent(id)}/queue/${idx}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: newText }),
      }
    );
    if (!res.ok) {
      const detail = await res.text();
      setSendStatus(`Edit failed: ${res.status} ${detail}`, "error");
      // Roll back optimistic update on failure.
      patchPendingOptimistic((p) => p.map((t, i) => (i === idx ? prevText : t)));
    } else {
      setSendStatus("Edited.", "success");
    }
  } catch (e) {
    setSendStatus(`Network error: ${e.message}`, "error");
    patchPendingOptimistic((p) => p.map((t, i) => (i === idx ? prevText : t)));
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
  // Show all stored snapshots. Index 0 is newest; later indices step
  // back through previous scroll captures from this idle session.
  // Cache-bust so the same idx returns the latest bytes after a
  // re-capture; Cache-Control: no-store from the server too.
  const bust = Date.now();
  for (let i = 0; i < count; i++) {
    const url = `/api/windows/${encodeURIComponent(id)}/snapshot/${i}?t=${bust}-${i}`;
    const card = document.createElement("div");
    card.className = "snapshot";
    const label =
      i === 0
        ? (w.snapshot_at ? `newest · ${formatRelativeTime(w.snapshot_at)}` : "newest")
        : `${i} scroll${i === 1 ? "" : "s"} ago`;
    card.innerHTML = `
      <img src="${url}" alt="snapshot ${i + 1}" loading="lazy">
      <div class="ts">${label}</div>
    `;
    card.addEventListener("click", () => openLightbox(url));
    wrap.appendChild(card);
  }
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

async function deleteQueuedItem(idx, btn) {
  const id = state.current;
  if (!id) return;
  if (btn) btn.disabled = true;
  // Optimistic local removal so the row disappears immediately. SSE
  // will reconcile if the server rejects (e.g. wrong index).
  patchPendingOptimistic((p) => p.filter((_, i) => i !== idx));
  try {
    const res = await fetch(
      `/api/windows/${encodeURIComponent(id)}/queue/${idx}`,
      { method: "DELETE" }
    );
    if (!res.ok) {
      const detail = await res.text();
      setSendStatus(`Delete failed: ${res.status} ${detail}`, "error");
    } else {
      setSendStatus("Removed from queue.", "success");
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

// ---- rules automation toggle -------------------------------------------

function renderRulesToggle() {
  const btn = $("rules-toggle");
  const on = !!state.rulesRunning;
  btn.textContent = on ? "on" : "off";
  btn.classList.toggle("on", on);
}

async function toggleRules() {
  const next = !state.rulesRunning;
  // Optimistic flip so the toggle reacts on tap; the server response
  // confirms (and the next /api/state load is the authoritative read).
  state.rulesRunning = next;
  renderRulesToggle();
  try {
    const res = await fetch("/api/admin/rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ running: next }),
    });
    if (!res.ok) {
      const detail = await res.text();
      alert(`Rules toggle failed: ${res.status} ${detail}`);
      state.rulesRunning = !next;
      renderRulesToggle();
    }
  } catch (e) {
    alert(`Rules toggle network error: ${e.message}`);
    state.rulesRunning = !next;
    renderRulesToggle();
  }
}

// ---- scroll the open window --------------------------------------------

async function scrollWindow(amount, btn) {
  const id = state.current;
  if (!id) return;
  if (btn) {
    btn.disabled = true;
    btn.classList.add("loading");
  }
  try {
    const res = await fetch(
      `/api/windows/${encodeURIComponent(id)}/scroll`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount }),
      }
    );
    if (!res.ok) {
      const detail = await res.text();
      setSendStatus(`Scroll failed: ${res.status} ${detail}`, "error");
    } else {
      setSendStatus("Scrolled. New screenshot incoming.", "success");
    }
  } catch (e) {
    setSendStatus(`Scroll network error: ${e.message}`, "error");
  } finally {
    if (btn) {
      // Lockout long enough for the post-scroll capture to settle and
      // the SSE event to land — otherwise a fast double-tap shoots
      // off two scrolls before the screenshot rolls in.
      setTimeout(() => {
        btn.disabled = false;
        btn.classList.remove("loading");
      }, 1200);
    }
  }
}

renderNotifToggle();
renderAutoReloadToggle();
renderRulesToggle();

$("settings-btn").addEventListener("click", () => {
  const panel = $("settings-panel");
  panel.hidden = !panel.hidden;
  $("settings-btn").setAttribute("aria-expanded", String(!panel.hidden));
});
$("notif-toggle").addEventListener("click", toggleNotifications);
$("autoreload-toggle").addEventListener("click", toggleAutoReload);
$("reload-btn").addEventListener("click", reloadBridge);
$("rules-toggle").addEventListener("click", toggleRules);
for (const btn of document.querySelectorAll(".scroll-btn")) {
  const amount = Number(btn.dataset.amount || "1");
  btn.addEventListener("click", () => scrollWindow(amount, btn));
}

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
