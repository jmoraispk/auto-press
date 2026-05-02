// CodeAway landing — vanilla JS, no build step.
//
// Two interactions:
//   1. Copy install snippet from the hero to the clipboard.
//   2. Feature-request box submits through one of three channels.
// Plus a film-grain canvas overlay generated on load + resize.

const REPO = "jmoraispk/codeaway";
// Public contact address — replace once a dedicated codeaway.org alias
// is set up. Until then, mailto: opens the user's mail client with the
// textarea content already filled in.
const CONTACT_EMAIL = "hello@codeaway.org";

const $ = (id) => document.getElementById(id);

// ---- Install-command copy button --------------------------------------

(function wireCopyInstall() {
  const btn = $("copy-install");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const text = "uv sync && uv run main.py";
    try {
      await navigator.clipboard.writeText(text);
      const original = btn.querySelector("code").innerHTML;
      btn.querySelector("code").textContent = "Copied to clipboard";
      btn.classList.add("copied");
      setTimeout(() => {
        btn.querySelector("code").innerHTML = original;
        btn.classList.remove("copied");
      }, 1400);
    } catch {
      // Selection fallback for browsers that block clipboard writes
      // outside HTTPS or off a user gesture chain.
      const range = document.createRange();
      range.selectNode(btn.querySelector("code"));
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
  });
})();

// ---- Feature-request channels -----------------------------------------
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
      // GitHub's web UI lets us pre-fill the body via query string. Title
      // stays empty so the user can write a tight summary of their own.
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

// ---- Film-grain overlay -----------------------------------------------

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
