# 🖱️ auto-press

Auto clicker with optional Enter key press, global hotkeys, and mouse position calibration.

## ✨ Features

- 🎯 Click at a calibrated screen position on an interval
- ⌨️ Optional Enter key press after each click (or mouse-only mode)
- 🚀 **Simple mode**: just press Enter repeatedly (no mouse, no UI)
- 🌐 Global hotkeys (work even when the app is not focused)
- ⚡ Efficient event-driven design (zero CPU usage when idle)

## 📋 Requirements

- 🪟 Windows (hotkeys use Win32 API; UI buttons still work on other platforms)
- 🐍 Python 3.10+

## 🚀 Usage

```bash
uv run press_enter.py [seconds] [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `seconds` | `10.0` | Interval between actions (in seconds) |
| `--simple` | off | Simple mode: just press Enter (no mouse, no UI) |
| `--mouse-only` | off | Only click, don't press Enter |
| `--headless` | off | Run without UI (console mode) |
| `--toggle` | `PAGEUP` | Hotkey to start/stop |
| `--calibrate-key` | `PAGEDOWN` | Hotkey to set click position |
| `--x`, `--y` | - | Target coordinates (headless mode) |
| `--calibrate` | off | Force calibration (headless mode) |

## 💡 Examples

```bash
# Simple mode: just press Enter every 5 seconds (no mouse, no UI)
uv run press_enter.py 5 --simple

# UI mode: click + Enter every 10 seconds (default)
uv run press_enter.py

# Mouse click only, every 10 seconds
uv run press_enter.py 10 --mouse-only

# Custom hotkeys
uv run press_enter.py --toggle F8 --calibrate-key F9 --quit F10

# Headless mode with preset coordinates
uv run press_enter.py 5 --headless --x 500 --y 300
```

## 🎮 How to Use

1. **Start the app** — A small window appears with a red status light
2. **Position your mouse** over the target click location
3. **Press PageDown** — The position is saved
4. **Press PageUp** — Clicking starts (light turns green)
5. **Press PageUp again** to pause, close the window to quit

The app stays on top so you can always see the status. 🔝
