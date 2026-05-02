<p align="center">
  <img src="imgs/ui.jpg" alt="auto-press UI" width="760">
</p>

<h1 align="center">🖱️ auto-press</h1>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.14%20preferred-blue.svg" alt="Python"></a>
  <a href="#-faq"><img src="https://img.shields.io/badge/platform-Windows-0078D6.svg" alt="Platform"></a>
  <a href="https://github.com/astral-sh/uv"><img src="https://img.shields.io/badge/packaged%20with-uv-261230.svg" alt="uv"></a>
  <a href="#"><img src="https://img.shields.io/badge/status-active-2e7d32.svg" alt="Status"></a>
</p>

<p align="center"><strong>Automate LLMs that run outside of sandboxes — no containers, no APIs, just your screen.</strong></p>

auto-press was built to keep [Cursor](https://cursor.com/) agents moving without babysitting. Sandboxes take time to spin up and don't work for every workflow; if you already have a Cursor window open, this tool watches it, clicks the right button, and keeps the agent running on its own. It's fast, local, and it works on anything you can see on your screen.

## ✨ Why

- 🧠 **Built for LLM agents** — keeps Cursor loops unblocked while you do other work.
- 🖥️ **No sandbox required** — if you can see it, auto-press can click it.
- ⚡ **Fast** — configurable scan interval and search region.
- 🎯 **Template-matching rules** — screenshot the button once, forget about it.
- 🌙 **Runs overnight** — types `continue` whenever the agent stalls so it works while you sleep.
- 🔕 **Stays out of the way** — lives in the system tray with a red/green status dot.

## 🚀 Quickstart

Install [uv](https://github.com/astral-sh/uv), then:

```bash
uv sync
uv run main.py
```

That's it — the UI opens and you're ready to add your first rule.

Python 3.14 is pinned via `.python-version`; the code works on 3.10+ so `uv python pin 3.11` (or any newer) is fine too.

## 🧭 The workflow

Each rule is a few clicks:

1. **Add a rule.** Name it and toggle it on.
2. **Pick an action.** Click, click + Enter, or click + send (send = type a word, then Enter).
3. **Capture the target.** A crop of the button — triggers the rule; the cursor clicks its center.
4. **Test & Save.** Run a single match to confirm, then save.
5. **Press Page Down** to start / stop. Prefer a different key? Click the **Hotkey** button in the toolbar, press any key (with modifiers if you like), and it's saved. The window can stay in the background or hide to the tray.

## 🟢🔴 Tray indicator

auto-press sits in the Windows system tray. The dot color tells you what it's doing:

<table>
  <tr>
    <td align="center"><img src="imgs/tray_off.png" alt="Stopped" width="110"><br><strong>Stopped</strong><br><sub>not scanning</sub></td>
    <td align="center"><img src="imgs/tray_on.png" alt="Running" width="110"><br><strong>Running</strong><br><sub>scanning &amp; firing rules</sub></td>
  </tr>
</table>

Left-click the icon to show/hide the window. Right-click for Start/Stop and Quit.

## 📱 Remote Bridge (optional)

A tiny FastAPI server you can opt into so a phone over Tailscale becomes a remote control for your Cursor windows:

- 🟢🔴 **Live status per window** — idle / busy dots over Server-Sent Events; no polling, no flicker.
- ✍️ **Send (or queue) replies** — type on the phone; sends immediately when the window is idle, queues otherwise and auto-drains on the next busy → idle.
- 📜 **Scroll the chat history** — one-tap "scroll up & screenshot" so you can read older context without touching the laptop.
- 🖼️ **Snapshot lightbox** — every busy → idle (and every scroll) saves a tile; tap to expand, swipe to step through.
- 📲 **PWA install + push** — "Add to Home Screen" for a fullscreen app, optional ntfy notifications when a window flips idle.

The bridge is **off by default** and adds no overhead until you pass `--bridge`.

### Enable it

```bash
uv sync --extra bridge
uv run main.py --bridge --activate
```

`--bridge` starts the FastAPI listener and unlocks the **Bridge** tab in the desktop UI. `--activate` is a convenience: same as clicking Start at launch. Optional bind overrides:

```bash
uv run main.py --bridge --bridge-host 127.0.0.1 --bridge-port 8765
```

Once running, open `http://<windows-tailscale-name>:8765` from the phone. Use the browser's share/install icon to add the PWA to the home screen — it runs in standalone display mode.

### One-time setup (Bridge tab)

1. **Capture the idle template.** Snip the on-screen marker that means "Cursor is waiting for input" (e.g. the white-on-blue Send arrow). Re-used for every window.
2. **Add a window.** Drag a rectangle around the Cursor window you want to monitor. Optionally set a `chat_target` click-point (defaults to centre-bottom of the region) — that's where pastes land.
3. **Turn the bridge service on.** The toggle in the Bridge tab starts the per-window scanner. The phone immediately sees the windows and their idle/busy status.

Window geometries, splitter sizes, and section collapse states persist via `QSettings` between launches.

### Phone UI in 30 seconds

| Tap | What happens |
| --- | --- |
| A window card | Opens the detail view: composer, queue, scroll button, snapshots. |
| **Send** with idle window | Pastes the textarea content into `chat_target` and presses Enter. Empty textarea = just clicks + Enter (useful when you already typed in the laptop). |
| **Send** with busy window | Queues the message; sent on the next busy → idle. |
| Queued message text (double-tap) | Inline edit — Save / Cancel right under the textarea. |
| **Scroll up & screenshot** | Double-clicks the gutter, presses ↑ a few times, captures a fresh snapshot. |
| Snapshot tile | Lightbox with prev / next arrows, swipe, keyboard, and counter. |
| ⚙ Settings | Toggle rules automation (Start/Stop), notifications, soft auto-refresh, reload bridge. |

### Configuration

`templates/config.json`'s `bridge` block stores all bridge settings; the desktop UI writes it for you:

```json
{
  "bridge": {
    "host": "0.0.0.0",
    "port": 8765,
    "idle_threshold": 0.90,
    "idle_template_path": "templates/idle.png",
    "windows": [
      { "id": "main", "name": "Main", "region": [100, 200, 800, 1200], "chat_target": [500, 1300] }
    ],
    "ntfy_topic": "your-secret-topic",
    "ntfy_server": "https://ntfy.sh"
  }
}
```

### Notifications

Two channels, both optional:

- **Browser** — flip *Notifications* on in the phone's settings drawer; the PWA fires a native notification on each busy → idle.
- **ntfy** — set `ntfy_topic` and every busy → idle POSTs to `<ntfy_server>/<topic>`. Subscribe in the [ntfy app](https://ntfy.sh/) (no account required). Tapping a notification deep-links into the matching window.

### Security

There is no auth on the bridge **by design** — Tailscale is the access boundary. The default `0.0.0.0` bind only matters if the host is on a hostile network; on a Tailscale-only laptop, the tailnet ACL is the firewall. Don't expose port 8765 to the public internet.

### Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| **No windows on the phone** | Bridge service is off, or no windows configured yet — open the Bridge tab and add at least one. |
| **All windows stuck on "busy"** | The captured idle template doesn't match — recapture against the actual on-screen state. Lower `match_threshold` if needed. |
| **Send → 4xx error** | Window region moved off-screen or was minimised; bring Cursor back into view and retry. |
| **Snapshots never refresh** | The window never flipped busy → idle since startup. Trigger any agent reply in Cursor and the next idle transition captures a snapshot. |
| **No notifications** | Browser denied permission, or wrong `ntfy_topic`; verify by opening `<ntfy_server>/<topic>` in a browser. |
| **PWA won't install** | Some browsers require HTTPS for install; use Chrome / Safari "Add to Home Screen" directly. |

## ❓ FAQ

<details>
<summary><strong>How do I keep the tray icon always visible on Windows?</strong></summary>

By default Windows hides new tray icons inside the `^` overflow flyout. Windows doesn't let apps force the icon to be pinned — it's a per-user setting you toggle once:

- **Windows 11**: Settings → Personalization → Taskbar → *Other system tray icons* → turn on `Auto Press` (or `python.exe` while the app is running).
- **Windows 10**: Settings → Personalization → Taskbar → *Select which icons appear on the taskbar* → turn on `Auto Press`.

After that, the red/green dot stays next to the clock whenever auto-press is running.
</details>

<details>
<summary><strong>Does this work on macOS or Linux?</strong></summary>

Today auto-press is **Windows-only in practice**. The engine, UI, and template matching are cross-platform (PySide6 + Pillow + OpenCV run everywhere), but three pieces lean on Win32 APIs:

- Per-monitor-v2 DPI awareness for reliable capture across mixed-DPI monitors.
- Physical-pixel cursor and monitor enumeration (`GetCursorPos`, `EnumDisplayMonitors`).
- Global **Page Down** hotkey (`RegisterHotKey`).

macOS / Linux parity is on the roadmap but **low priority** — happy to pick it up if someone finds it useful. Contributions welcome; the three items above are all that'd need writing, the rest already works.

</details>

<details>
<summary><strong>Why does my template match on one monitor but not another?</strong></summary>

Different DPI scalings. Template matching isn't scale-invariant — capture the template on the monitor you want it to match on, or set both monitors to the same Windows display scaling.
</details>

<details>
<summary><strong>How do I set a custom Start/Stop shortcut?</strong></summary>

Click the **Hotkey** button in the toolbar (next to Start), then press the key combination you want — modifiers included, so `Ctrl+Alt+F9` works just as well as plain `F12`. Your choice is saved to `templates/config.json` and re-registered on the spot. Default is **Page Down**; press **Esc** while capturing to cancel.
</details>

<details>
<summary><strong>What does each file do?</strong></summary>

- [main.py](main.py) — entrypoint; forces per-monitor-v2 DPI awareness and installs a SIGINT handler.
- [press_ui.py](press_ui.py) — Fluent-Design UI, engine worker thread, per-monitor drag-capture overlays, hotkey picker, tray icon.
- [press_engine.py](press_engine.py) — screen capture + template matching + action dispatch.
- [press_store.py](press_store.py) — config persistence (rules + hotkey + interval) and template-file helpers.
- [press_core.py](press_core.py) — click / type / vision primitives used by the engine.
- [press_bridge.py](press_bridge.py) — optional FastAPI server, SSE event hub, send pipeline, ntfy publisher.
- [bridge_phone/](bridge_phone/) — vanilla HTML/CSS/JS PWA served by the bridge.
- [templates/](templates/) — captured template images and `config.json`. User-local; gitignored.
</details>
