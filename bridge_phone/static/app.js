// auto-press phone bridge — vanilla JS, no build step.
//
// Single-purpose UI: list configured Cursor windows with idle/busy
// status from SSE, drill in to see the last N PNG snapshots.

const $ = (id) => document.getElementById(id);

const state = {
  windows: new Map(),       // id -> summary dict
  snapshotsPerWindow: 5,
  view: "list",             // "list" | "snapshots"
  current: null,            // window id when in "snapshots" view
};

// ---- SSE ----------------------------------------------------------------

let sse;
let backoff = 1000;
function connectSSE() {
  if (sse) try { sse.close(); } catch {}
  sse = new EventSource("/api/events");
  sse.onopen = () => { backoff = 1000; };
  sse.addEventListener("window_state", (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (!data || !data.id) return;
      state.windows.set(data.id, data);
      renderWindows();
      if (state.view === "snapshots" && state.current === data.id) {
        // Stay on the snapshots view but refresh thumbnails so the user
        // sees the new tick land. Cheap because we only fetch when the
        // snapshot count actually grew.
        renderSnapshots();
      }
    } catch {}
  });
  sse.onerror = () => {
    try { sse.close(); } catch {}
    setTimeout(connectSSE, backoff);
    backoff = Math.min(backoff * 2, 30000);
  };
}

// ---- initial paint ------------------------------------------------------

async function loadState() {
  try {
    const res = await fetch("/api/state");
    if (!res.ok) return;
    const data = await res.json();
    state.snapshotsPerWindow = data.snapshots_per_window || 5;
    state.windows.clear();
    for (const w of data.windows || []) state.windows.set(w.id, w);
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
    li.innerHTML = `
      <span class="dot" aria-hidden="true"></span>
      <div class="meta">
        <div class="name">${escapeHtml(w.name || w.id)}</div>
        <div class="sub">${w.idle ? "idle" : "busy"} · ${formatScore(w.score)} · ${w.snapshot_count || 0} snap${w.snapshot_count === 1 ? "" : "s"}</div>
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
  const w = state.windows.get(id);
  $("snap-window-name").textContent = w ? (w.name || id) : id;
  renderSnapshots();
}

function closeSnapshots() {
  state.view = "list";
  state.current = null;
  $("snapshots-section").hidden = true;
  $("windows-section").hidden = false;
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
  // after a tick. We rely on Cache-Control: no-store from the server too.
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
    wrap.appendChild(card);
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

$("snap-back").addEventListener("click", closeSnapshots);

loadState();
connectSSE();
