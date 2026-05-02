// CodeAway landing — vanilla JS, no build step.
//
// Three interactions:
//   1. Quickstart terminal — method × platform tabs swap the install
//      command and surface an "untested platform" warning where it
//      applies. The warning's CTA hands off to the feature-request
//      box pre-filled with a port request.
//   2. Copy install command to clipboard.
//   3. Feature-request box submits through one of three channels.
// Plus a film-grain canvas overlay generated on load + resize.

const REPO = "jmoraispk/codeaway";
// Public contact address. Update if the codeaway.dev alias changes.
const CONTACT_EMAIL = "hi@codeaway.dev";

const $ = (id) => document.getElementById(id);

// ---- Quickstart matrix ------------------------------------------------
//
// Keyed [method][platform] → { tested, lines }. Each line is either
// { type: "cmd", text } (rendered with a $ prompt) or { type: "comment",
// text } (rendered grey, no prompt). Building the block as discrete
// lines lets the uv-install hint sit between `cd codeaway` and
// `uv sync` without breaking the prompt rhythm, and the copy button
// can pull out only the cmd lines.

const INSTALL = {
  "one-liner": {
    windows: {
      tested: true,
      lines: [
        { type: "comment", text: "# 100% local. Installs uv, clones the source, syncs deps." },
        { type: "cmd",     text: 'powershell -c "irm https://codeaway.dev/install.ps1 | iex"' },
      ],
    },
    unix: {
      tested: false,
      lines: [
        { type: "comment", text: "# Untested on macOS / Linux — fetches and syncs, runtime is Win32-only today." },
        { type: "cmd",     text: "curl -fsSL https://codeaway.dev/install.sh | sh" },
      ],
    },
  },
  hackable: {
    windows: {
      tested: true,
      lines: [
        { type: "cmd", text: "git clone https://github.com/jmoraispk/codeaway.git" },
        { type: "cmd", text: "cd codeaway" },
        { type: "cmd", text: "uv sync --extra bridge",
          trail: '# install uv: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"' },
        { type: "cmd", text: "uv run main.py --bridge --activate" },
      ],
    },
    unix: {
      tested: false,
      lines: [
        { type: "cmd", text: "git clone https://github.com/jmoraispk/codeaway.git" },
        { type: "cmd", text: "cd codeaway" },
        { type: "cmd", text: "uv sync --extra bridge",
          trail: "# install uv: curl -LsSf https://astral.sh/uv/install.sh | sh" },
        { type: "cmd", text: "uv run main.py --bridge --activate" },
      ],
    },
  },
};

const PLATFORM_LABEL = { windows: "Windows", unix: "macOS / Linux" };

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderInstallLines(entry) {
  // One element per line. Each cmd gets a $ prompt; an optional
  // `trail` field becomes a muted shell-style comment on the same
  // line so the uv-install hint sits next to `uv sync` instead of
  // breaking the prompt rhythm with a free-floating banner.
  //
  // Joining with "" (not "\n") because each span is display:block —
  // the extra newline in <pre> would render as a second blank line.
  return entry.lines.map((line) => {
    if (line.type === "comment") {
      return `<span class="comment">${escapeHtml(line.text)}</span>`;
    }
    const trail = line.trail
      ? `<span class="trail-comment"> ${escapeHtml(line.trail)}</span>`
      : "";
    return `<span class="line"><span class="prompt">$</span> <span class="cmd">${escapeHtml(line.text)}</span>${trail}</span>`;
  }).join("");
}

(function wireQuickstart() {
  const term = document.querySelector(".terminal");
  if (!term) return;

  const preEl = $("install-pre");
  const warn = $("untested-warn");
  const requestBtn = $("request-port");

  function currentEntry() {
    return INSTALL[term.dataset.method] &&
      INSTALL[term.dataset.method][term.dataset.platform];
  }

  function render() {
    const entry = currentEntry();
    if (!entry || !preEl) return;
    preEl.innerHTML = renderInstallLines(entry);
    warn.hidden = entry.tested;
    // Untested platform tabs paint red while active so the row reads
    // as "Windows is the safe one" without needing to hover.
    document.querySelectorAll(".platform-tab").forEach((btn) => {
      const p = btn.dataset.platform;
      const tested = INSTALL[term.dataset.method][p] && INSTALL[term.dataset.method][p].tested;
      btn.classList.toggle(
        "beta-warning",
        btn.classList.contains("is-active") && !tested
      );
    });
  }

  function selectTab(group, value) {
    const attr = group === "method" ? "data-method" : "data-platform";
    document
      .querySelectorAll(group === "method" ? ".method-tab" : ".platform-tab")
      .forEach((btn) => {
        const on = btn.getAttribute(attr) === value;
        btn.classList.toggle("is-active", on);
        btn.setAttribute("aria-selected", String(on));
      });
    term.dataset[group === "method" ? "method" : "platform"] = value;
    render();
  }

  document.querySelectorAll(".method-tab").forEach((btn) => {
    btn.addEventListener("click", () => selectTab("method", btn.dataset.method));
  });
  document.querySelectorAll(".platform-tab").forEach((btn) => {
    btn.addEventListener("click", () => selectTab("platform", btn.dataset.platform));
  });

  if (requestBtn) {
    requestBtn.addEventListener("click", () => {
      const label = PLATFORM_LABEL[term.dataset.platform] || term.dataset.platform;
      const text = $("fr-text");
      if (text) {
        text.value =
          `Please add tested support for ${label}.\n\n` +
          `Today the engine relies on Win32 APIs (per-monitor DPI, ` +
          `RegisterHotKey, EnumDisplayMonitors). Happy to help test if a ` +
          `port lands.`;
        // Trigger the input handler so the feedback channel hrefs pick
        // up the new body without a manual key press.
        text.dispatchEvent(new Event("input", { bubbles: true }));
      }
      const target = $("request");
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      // Focus the textarea after the smooth scroll has had a moment to
      // start — focusing immediately would jerk the page to the input
      // and override the smooth scroll on some browsers.
      setTimeout(() => text && text.focus(), 400);
    });
  }

  render();

  // Expose the current cmd-line text for the copy button below.
  window.__copyInstall = function () {
    const entry = currentEntry();
    if (!entry) return "";
    return entry.lines
      .filter((l) => l.type === "cmd")
      .map((l) => l.text)
      .join("\n");
  };
})();

// ---- Copy install command --------------------------------------------

(function wireCopyInstall() {
  const btn = $("copy-cmd");
  if (!btn) return;

  btn.addEventListener("click", async () => {
    // Pull from the helper the Quickstart wiring exposed — that way
    // we copy only the cmd lines, not the leading comment, joined by
    // newlines so a paste at a shell prompt runs them as a script.
    const text = (window.__copyInstall && window.__copyInstall()) || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      btn.classList.add("copied");
      setTimeout(() => btn.classList.remove("copied"), 1200);
    } catch {
      // Clipboard API can be blocked in non-HTTPS contexts; fall back
      // to selecting the rendered <pre> so the user can ctrl+c.
      const pre = $("install-pre");
      if (!pre) return;
      const range = document.createRange();
      range.selectNode(pre);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
  });
})();

// ---- Feature-request channels ----------------------------------------
//
// Two real <a href> links instead of buttons-that-trigger-window.open —
// popup blockers and "nothing happened" feedback go away because click
// is just normal navigation. We keep the link hrefs in sync with the
// textarea so whatever the user typed travels with them to GitHub or
// their mail client. Empty textarea is fine: the link still works and
// the destination form stays empty.

(function wireFeatureRequest() {
  const text = $("fr-text");
  const emailLink = $("channel-email");
  const issueLink = $("channel-issue");
  if (!text || !emailLink || !issueLink) return;

  const subject = encodeURIComponent("CodeAway — feature request");

  function sync() {
    const body = text.value;
    const enc = body ? encodeURIComponent(body) : "";
    emailLink.href = body
      ? `mailto:${CONTACT_EMAIL}?subject=${subject}&body=${enc}`
      : `mailto:${CONTACT_EMAIL}?subject=${subject}`;
    issueLink.href = body
      ? `https://github.com/${REPO}/issues/new?body=${enc}`
      : `https://github.com/${REPO}/issues/new`;
  }

  text.addEventListener("input", sync);
  sync();
})();

// ---- Why-section timeline reveal -------------------------------------
//
// Bars stay collapsed until the user scrolls the Why section into view.
// First trip past the threshold flips an `is-visible` class that the CSS
// listens for; the observer disconnects after that so the bars don't
// re-animate on every subsequent scroll past.

(function wireWhyReveal() {
  const why = $("why");
  if (!why) return;
  if (typeof IntersectionObserver === "undefined") {
    // Old browsers or test runners — just show the final state.
    why.classList.add("is-visible");
    return;
  }
  const obs = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          why.classList.add("is-visible");
          obs.disconnect();
          break;
        }
      }
    },
    { threshold: 0.25 }
  );
  obs.observe(why);
})();

// ---- Film-grain overlay ----------------------------------------------

(function paintGrain() {
  const c = $("grain");
  if (!c) return;
  const ctx = c.getContext("2d");

  function draw() {
    const w = c.width = window.innerWidth;
    const h = c.height = window.innerHeight;
    const img = ctx.createImageData(w, h);
    const data = img.data;
    for (let i = 0; i < data.length; i += 4) {
      const v = (Math.random() * 255) | 0;
      data[i] = data[i + 1] = data[i + 2] = v;
      data[i + 3] = 9;
    }
    ctx.putImageData(img, 0, 0);
  }

  draw();
  let t;
  window.addEventListener("resize", () => {
    clearTimeout(t);
    t = setTimeout(draw, 150);
  });
})();
