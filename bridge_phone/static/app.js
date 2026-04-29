// auto-press phone bridge — vanilla JS, no build step.
//
// Wire model:
//   /api/state    — initial paint
//   /api/events   — server-sent events with backoff reconnect
//   /api/send     — paste-and-Enter with optional match_index
//   /api/dismiss  — server-side dismiss flag
//   /api/refresh_targets — manual rule refresh

const $ = (id) => document.getElementById(id);

const state = {
  rules: [],
  events: [],
  focusRuleId: null,
  pendingRuleId: null,
  pendingText: "",
  matches: null,
};

const conn = $("conn");
function setConn(ok) {
  conn.classList.toggle("online", ok);
  conn.classList.toggle("offline", !ok);
  conn.textContent = ok ? "live" : "offline";
}

function fmtTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleTimeString(); } catch { return iso; }
}

function renderRules() {
  const container = $("rules");
  container.innerHTML = "";
  if (!state.rules.length) {
    container.innerHTML = `<div class="rule disabled"><span class="name">No rules yet — add one in the desktop app.</span></div>`;
    return;
  }
  for (const r of state.rules) {
    const el = document.createElement("div");
    el.className = "rule" + (r.enabled ? "" : " disabled") + (r.id === state.focusRuleId ? " focus" : "");
    el.dataset.ruleId = r.id;
    el.innerHTML = `
      <div class="name">${escapeHtml(r.friendly_name || r.name || r.id)}</div>
      <div class="meta">${escapeHtml(r.matcher || "")} · ${escapeHtml(r.action || "")}</div>
      <button class="primary" data-act="send">Send</button>
    `;
    el.querySelector('[data-act="send"]').addEventListener("click", () => openSend(r.id));
    container.appendChild(el);
  }
}

function renderEvents() {
  const ul = $("events");
  ul.innerHTML = "";
  const recent = state.events.slice(-30).reverse();
  if (!recent.length) {
    ul.innerHTML = `<li><span class="name" style="color:var(--muted)">No events yet.</span></li>`;
    return;
  }
  for (const e of recent) {
    const li = document.createElement("li");
    if (e.dismissed) li.classList.add("dismissed");
    li.innerHTML = `
      <span class="ts">${fmtTime(e.timestamp_iso)}</span>
      <span class="name">${escapeHtml(e.rule_name || e.rule_id || "rule")}</span>
      <button class="ghost" data-act="dismiss">×</button>
    `;
    li.querySelector('[data-act="dismiss"]').addEventListener("click", () => dismissEvent(e.event_id, li));
    ul.appendChild(li);
  }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function loadState() {
  try {
    const res = await fetch("/api/state");
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    state.rules = data.rules || [];
    state.events = data.events || [];
    renderRules();
    renderEvents();
  } catch (e) {
    setConn(false);
  }
}

function openSend(ruleId) {
  state.pendingRuleId = ruleId;
  state.matches = null;
  $("send-section").hidden = false;
  $("picker").hidden = true;
  const rule = state.rules.find((r) => r.id === ruleId);
  $("send-rule-name").textContent = rule ? (rule.friendly_name || rule.name || rule.id) : ruleId;
  $("send-status").textContent = "";
  $("send-status").className = "status";
  $("send-text").focus();
}

function closeSend() {
  $("send-section").hidden = true;
  state.pendingRuleId = null;
  state.matches = null;
  $("send-text").value = "";
  $("picker").hidden = true;
}

async function performSend(matchIndex) {
  const ruleId = state.pendingRuleId;
  const text = $("send-text").value.trim();
  if (!ruleId) return;
  if (!text) {
    setStatus("Type something first.", "error");
    return;
  }
  setStatus("Sending…");
  const body = { rule_id: ruleId, text };
  if (matchIndex !== undefined) body.match_index = matchIndex;
  try {
    const res = await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 409) {
      const data = await res.json();
      state.matches = data.matches || [];
      renderPicker();
      setStatus(`Multiple matches (${data.count}). Pick one.`);
      return;
    }
    if (!res.ok) {
      const detail = await res.text();
      setStatus(`Error ${res.status}: ${detail}`, "error");
      return;
    }
    const data = await res.json();
    setStatus(`Sent in ${data.duration_ms} ms.`, "success");
    setTimeout(closeSend, 700);
  } catch (e) {
    setStatus(`Network error: ${e.message}`, "error");
  }
}

function setStatus(msg, kind) {
  const el = $("send-status");
  el.textContent = msg || "";
  el.className = "status" + (kind ? " " + kind : "");
}

function renderPicker() {
  const wrap = $("picker");
  const ul = $("picker-list");
  ul.innerHTML = "";
  for (const m of state.matches || []) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.innerHTML = `<span>#${m.index} · ${m.center[0]}, ${m.center[1]}</span><span style="color:var(--muted)">score ${m.score.toFixed(2)}</span>`;
    btn.addEventListener("click", () => performSend(m.index));
    li.appendChild(btn);
    ul.appendChild(li);
  }
  wrap.hidden = !state.matches || !state.matches.length;
}

async function dismissEvent(eventId, li) {
  try {
    await fetch(`/api/dismiss/${encodeURIComponent(eventId)}`, { method: "POST" });
    if (li) li.classList.add("dismissed");
  } catch { /* ignore */ }
}

// SSE with exponential backoff (1s → 30s).
let sse;
let backoff = 1000;
function connectSSE() {
  if (sse) try { sse.close(); } catch {}
  sse = new EventSource("/api/events");
  sse.onopen = () => { setConn(true); backoff = 1000; };
  sse.addEventListener("rule_matched", (ev) => {
    try {
      const data = JSON.parse(ev.data);
      const idx = state.events.findIndex((e) => e.event_id === data.event_id);
      if (idx >= 0) state.events[idx] = data;
      else state.events.push(data);
      renderEvents();
    } catch {}
  });
  sse.onerror = () => {
    setConn(false);
    try { sse.close(); } catch {}
    setTimeout(connectSSE, backoff);
    backoff = Math.min(backoff * 2, 30000);
  };
}

$("refresh").addEventListener("click", async () => {
  await fetch("/api/refresh_targets", { method: "POST" }).catch(() => {});
  loadState();
});
$("send-cancel").addEventListener("click", closeSend);
$("send-go").addEventListener("click", () => performSend());
$("send-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    performSend();
  }
});

// Notification deep-link via ntfy "Click" header → /?focus=<rule_id>.
const params = new URLSearchParams(location.search);
state.focusRuleId = params.get("focus");

loadState().then(() => {
  if (state.focusRuleId) openSend(state.focusRuleId);
});
connectSSE();
