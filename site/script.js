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
        { type: "cmd", text: "uv sync",
          trail: '# install uv: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"' },
        { type: "cmd", text: "uv run main.py" },
      ],
    },
    unix: {
      tested: false,
      lines: [
        { type: "cmd", text: "git clone https://github.com/jmoraispk/codeaway.git" },
        { type: "cmd", text: "cd codeaway" },
        { type: "cmd", text: "uv sync",
          trail: "# install uv: curl -LsSf https://astral.sh/uv/install.sh | sh" },
        { type: "cmd", text: "uv run main.py" },
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
      // Open a GitHub issue with the platform name pre-filled. The
      // page no longer has a feedback textarea to fall back on, so
      // this button now goes straight to the issue tracker — same
      // outcome, fewer clicks.
      const label = PLATFORM_LABEL[term.dataset.platform] || term.dataset.platform;
      const title = encodeURIComponent(`Add tested support for ${label}`);
      const body = encodeURIComponent(
        `Please add tested support for ${label}.\n\n` +
        `Today the engine relies on Win32 APIs (per-monitor DPI, ` +
        `RegisterHotKey, EnumDisplayMonitors). Happy to help test ` +
        `if a port lands.`
      );
      window.open(
        `https://github.com/${REPO}/issues/new?title=${title}&body=${body}`,
        "_blank",
        "noopener,noreferrer"
      );
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
// Stripped down: the page now ships a single static <a> linking to
// https://github.com/.../issues/new. No textarea, no email channel,
// no JS wiring needed.

// ---- Why-section timeline reveal -------------------------------------
//
// Bars stay collapsed until the user scrolls the Why section into view.
// First trip past the threshold flips an `is-visible` class that the CSS
// listens for; the observer disconnects after that so the bars don't
// re-animate on every subsequent scroll past.
//
// Same trigger also kicks off the number-ticker animation on the
// total-hours stats — they count up from 0 to their target (6 / 12)
// in sync with the bar fills.

function tickerAnimate(el, durationMs) {
  const target = parseInt(el.textContent, 10);
  if (Number.isNaN(target)) return;
  // Stash the target so a re-trigger doesn't compound from the wrong
  // base value (we set textContent to '0' immediately below).
  el.textContent = "0";
  const start = performance.now();
  function frame(now) {
    const t = Math.min(1, (now - start) / durationMs);
    // Ease-out cubic: starts fast, settles into the target. Matches the
    // bar-fill curve below (cubic-bezier(0.2, 0.8, 0.2, 1)).
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = Math.round(eased * target);
    if (t < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

(function wireWhyReveal() {
  const why = $("why");
  if (!why) return;

  function reveal() {
    why.classList.add("is-visible");
    // Timing matches the staggered bar fills:
    //   Without bar starts at 0.05s, fills over 1.4s
    //   With bar starts at 0.65s, fills over 1.6s
    // Number tickers run a hair shorter than the fill so they finish
    // before the markers fade in (1.8s onwards) and the eye lands on
    // the totals as a punctuation, not as a competing animation.
    const nums = why.querySelectorAll(".total-hours-num");
    if (nums[0]) setTimeout(() => tickerAnimate(nums[0], 1100), 120);
    if (nums[1]) setTimeout(() => tickerAnimate(nums[1], 1300), 700);
  }

  if (typeof IntersectionObserver === "undefined") {
    // Old browsers or test runners — show the final state immediately.
    reveal();
    return;
  }
  const obs = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          reveal();
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
