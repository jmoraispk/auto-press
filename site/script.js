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
// Keyed [method][platform] → { comment, cmd, tested }. Strings are
// rendered into a plain text node (no innerHTML) so backticks, $, &
// etc. are safe by default — and the `cmd` element keeps its own
// monospace styling.

const INSTALL = {
  "one-liner": {
    windows: {
      comment: "# 100% local. Installs uv, clones the source, syncs deps.",
      cmd: 'powershell -c "irm https://codeaway.dev/install.ps1 | iex"',
      tested: true,
    },
    macos: {
      comment: "# Untested on macOS — script fetches and syncs, runtime is Win32-only today.",
      cmd: "curl -fsSL https://codeaway.dev/install.sh | sh",
      tested: false,
    },
    linux: {
      comment: "# Untested on Linux — script fetches and syncs, runtime is Win32-only today.",
      cmd: "curl -fsSL https://codeaway.dev/install.sh | sh",
      tested: false,
    },
  },
  hackable: {
    windows: {
      comment: "# Read it, run it. Nothing magic.",
      cmd:
        "git clone https://github.com/jmoraispk/codeaway.git $HOME\\codeaway\n" +
        "cd $HOME\\codeaway; uv sync --extra bridge\n" +
        "uv run main.py --bridge --activate",
      tested: true,
    },
    macos: {
      comment: "# Untested on macOS — clone + sync work, runtime is Win32-only today.",
      cmd:
        "git clone https://github.com/jmoraispk/codeaway.git ~/codeaway\n" +
        "cd ~/codeaway && uv sync --extra bridge\n" +
        "uv run main.py --bridge --activate",
      tested: false,
    },
    linux: {
      comment: "# Untested on Linux — clone + sync work, runtime is Win32-only today.",
      cmd:
        "git clone https://github.com/jmoraispk/codeaway.git ~/codeaway\n" +
        "cd ~/codeaway && uv sync --extra bridge\n" +
        "uv run main.py --bridge --activate",
      tested: false,
    },
  },
};

const PLATFORM_LABEL = { windows: "Windows", macos: "macOS", linux: "Linux" };

(function wireQuickstart() {
  const term = document.querySelector(".terminal");
  if (!term) return;

  const cmdEl = $("install-cmd");
  const commentEl = $("install-comment");
  const warn = $("untested-warn");
  const requestBtn = $("request-port");

  function render() {
    const method = term.dataset.method;
    const platform = term.dataset.platform;
    const entry = INSTALL[method] && INSTALL[method][platform];
    if (!entry) return;
    cmdEl.textContent = entry.cmd;
    commentEl.textContent = entry.comment;
    warn.hidden = entry.tested;
    if (!entry.tested) {
      const label = PLATFORM_LABEL[platform] || platform;
      $("untested-os").textContent = label;
      $("untested-os-2").textContent = label;
    }
    // Mark untested platform tabs visually so the row reads as
    // "Windows is the safe one" without needing to hover or read.
    document.querySelectorAll(".platform-tab").forEach((btn) => {
      const p = btn.dataset.platform;
      const tested = INSTALL[method][p] && INSTALL[method][p].tested;
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
      const platform = term.dataset.platform;
      const label = PLATFORM_LABEL[platform] || platform;
      const text = $("fr-text");
      if (text) {
        text.value =
          `Please add tested support for ${label}.\n\n` +
          `Today the engine relies on Win32 APIs (per-monitor DPI, ` +
          `RegisterHotKey, EnumDisplayMonitors). Happy to help test if a ` +
          `port lands.`;
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
})();

// ---- Copy install command --------------------------------------------

(function wireCopyInstall() {
  const btn = $("copy-cmd");
  const cmdEl = $("install-cmd");
  if (!btn || !cmdEl) return;

  btn.addEventListener("click", async () => {
    const text = cmdEl.textContent;
    try {
      await navigator.clipboard.writeText(text);
      btn.classList.add("copied");
      setTimeout(() => btn.classList.remove("copied"), 1200);
    } catch {
      // Selection fallback for browsers that block clipboard writes
      // outside HTTPS or off a user gesture chain.
      const range = document.createRange();
      range.selectNode(cmdEl);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
  });
})();

// ---- Feature-request channels ----------------------------------------
//
// All three buttons funnel through the same handler; the channel decides
// which URL we open. The textarea content is always passed along so the
// user doesn't have to retype.

(function wireFeatureRequest() {
  const text = $("fr-text");
  const status = $("fr-status");
  if (!text || !status) return;

  function setStatus(msg, kind) {
    status.textContent = msg || "";
    status.className = "request-status" + (kind ? " " + kind : "");
  }

  function payload() {
    return text.value.trim();
  }

  function open(url) {
    // _blank with noopener so the new tab can't reach back into this
    // page. Falls back to same-tab navigation if the popup is blocked.
    const w = window.open(url, "_blank", "noopener,noreferrer");
    if (!w) window.location.href = url;
  }

  function submit(channel) {
    const body = payload();
    if (!body) {
      text.focus();
      setStatus("Type something first.", "error");
      return;
    }

    if (channel === "issue") {
      const url =
        `https://github.com/${REPO}/issues/new?body=` +
        encodeURIComponent(body);
      open(url);
      setStatus("Opening a new GitHub issue with your text…", "success");
      return;
    }

    if (channel === "pr") {
      // PRs need a fork+branch+commit, which we can't pre-build from the
      // browser. Best we can do: copy the text to the clipboard so it's
      // ready when they open the PR description box, then send them to
      // the contributing flow.
      navigator.clipboard.writeText(body).catch(() => {});
      const url = `https://github.com/${REPO}/compare`;
      open(url);
      setStatus(
        "Opening the compare page. Your text is on the clipboard — paste it into the PR description.",
        "success"
      );
      return;
    }

    if (channel === "email") {
      const url =
        `mailto:${CONTACT_EMAIL}` +
        `?subject=` + encodeURIComponent("CodeAway — feature request") +
        `&body=` + encodeURIComponent(body);
      // Same-tab navigation is correct for mailto: — opening in _blank
      // leaves an empty tab behind on most browsers.
      window.location.href = url;
      setStatus("Opening your mail client…", "success");
      return;
    }
  }

  for (const btn of document.querySelectorAll(".channel")) {
    btn.addEventListener("click", () => submit(btn.dataset.channel));
  }
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
