# 🖱️ auto-press

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg)](https://www.microsoft.com/windows)
[![uv](https://img.shields.io/badge/packaged%20with-uv-261230.svg)](https://github.com/astral-sh/uv)
[![Status](https://img.shields.io/badge/status-active-2e7d32.svg)](#)

**Automate LLMs that run outside of sandboxes — no containers, no APIs, just your screen.**

auto-press was built to keep [Cursor](https://cursor.com/) agents moving without babysitting. Cloud sandboxes take minutes to spin up and don't work for every workflow; if you already have a Cursor window (or a Cloud Codex extension tab) open, this tool watches it, clicks the right button, and keeps the agent running on its own. It's fast, it's local, and it works on anything you can see on your screen.

![auto-press UI](imgs/ui.png)

## ✨ Why

- 🧠 **Built for LLM agents** — keeps Cursor and Cloud Codex loops unblocked while you do other work.
- 🖥️ **No sandbox required** — if you can see it, auto-press can click it.
- ⚡ **Fast screen scans** — configurable interval and a configurable search region, so you pay for exactly the pixels you care about.
- 🎯 **Template-matching rules** — screenshot the button once, forget about it.
- 🔕 **Stays out of the way** — lives in the system tray with a red/green status dot.

## 🚀 Quickstart

```bash
uv sync
uv run main_press.py
```

That's it — the UI opens and you're ready to add your first rule.

## 🧭 The workflow

Setting up an automation is two steps, full stop:

1. **Add a rule.** Pick an action: click on an element, or send `Enter` to it (optionally typing a word first).
2. **Take a screenshot of where the action should happen.** Use `Capture Pattern` in the UI to crop the button or region. auto-press will scan the screen for that template and fire the action when it matches.

Repeat for every button you want automated (run, continue, accept, etc.). Reorder them in the list and press **Start / Stop**.

## 🟢🔴 Tray indicator

auto-press sits in the Windows system tray. The dot color tells you what it's doing:

| Icon | State |
|---|---|
| ![stopped](imgs/tray_off.png) | 🔴 Stopped — not scanning |
| ![running](imgs/tray_on.png) | 🟢 Running — scanning and firing rules |

Left-click the icon to show/hide the window. Right-click for Start/Stop and Quit.

## ❓ FAQ

<details>
<summary><strong>How do I keep the tray icon always visible on Windows?</strong></summary>

By default Windows hides new tray icons inside the `^` overflow flyout. Windows doesn't let apps force the icon to be pinned — it's a per-user setting you toggle once:

- **Windows 11**: Settings → Personalization → Taskbar → *Other system tray icons* → turn on `Auto Press` (or `python.exe` while the app is running).
- **Windows 10**: Settings → Personalization → Taskbar → *Select which icons appear on the taskbar* → turn on `Auto Press`.

After that, the red/green dot stays next to the clock whenever auto-press is running.
</details>

<details>
<summary><strong>What's the scan interval and why does it matter?</strong></summary>

The interval (seconds) controls how often auto-press captures the screen and tests the active rules. Lower = more responsive, higher = less CPU. You can also restrict the search region per rule so scans are cheap even at sub-second intervals.
</details>

<details>
<summary><strong>Does this work on macOS / Linux?</strong></summary>

Core logic is cross-platform, but the global hotkeys and tray integration are tuned for Windows. You can run the UI on other platforms, but some features may degrade.
</details>

<details>
<summary><strong>pystray isn't installed — what happens?</strong></summary>

The app still runs; you just lose the tray indicator and the X button closes the app normally. Run `uv sync` (or `uv add pystray`) to get it back.
</details>

## 🧩 Advanced

<details>
<summary><strong>CLI & hotkeys</strong></summary>

```bash
uv run main_press.py [seconds]
```

- `seconds` (optional) — default scan interval; can also be edited live in the UI. Default: `10.0`.
- **Page Down** (Windows) — global hotkey to Start/Stop without focusing the window.

Per-rule matching options (template, threshold, search region, action, optional text) all live in the UI.
</details>

<details>
<summary><strong>Code layout</strong></summary>

- [main_press.py](main_press.py) — CLI entrypoint, launches the UI
- [press_ui.py](press_ui.py) — rule-based UI with the tray indicator
- [press_engine.py](press_engine.py) — screen capture + template matching + action dispatch
- [press_store.py](press_store.py) — config and template persistence
- [press_core.py](press_core.py) — click / type / vision primitives
- [press_tray.py](press_tray.py) — `pystray` wrapper for the tray icon
- [templates/](templates/) — captured template images and `config.json`
</details>

## 📦 Requirements

- **Windows** recommended (global hotkeys and tray use Win32 APIs)
- **Python 3.10+** — managed through [`uv`](https://github.com/astral-sh/uv)

Dependencies (`numpy`, `opencv-python`, `pillow`, `pystray`, `customtkinter`) are installed automatically by `uv sync`.
