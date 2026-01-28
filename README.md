# auto-press

Auto clicker with optional Enter key press, global hotkeys, and mouse position calibration.

## Features

- Click at a calibrated screen position on an interval
- Optional Enter key press after each click (or mouse-only mode)
- **Simple mode**: just press Enter repeatedly (no mouse, no UI)
- Global hotkeys (work even when the app is not focused)
- Efficient event-driven design (zero CPU usage when idle)

## Requirements

- Windows (hotkeys use Win32 API; UI buttons still work on other platforms)
- Python 3.10+

## Installation & Running

### With UV (recommended)

```bash
# Run directly (UV handles dependencies automatically)
uv run press_enter.py

# Or with options
uv run press_enter.py 5 --mouse-only
```

### With pip

```bash
pip install pyautogui
python press_enter.py
```

## Usage

```
press_enter.py [seconds] [options]
```

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `seconds` | `10.0` | Interval between clicks (in seconds) |

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--simple` | off | Simple mode: just press Enter repeatedly (no mouse, no UI) |
| `--mouse-only` | off | Only click, don't press Enter |
| `--toggle` | `PAGEDOWN` | Hotkey to start/stop clicking |
| `--calibrate-key` | `PAGEUP` | Hotkey to set click position |
| `--quit` | `END` | Hotkey to quit the app |
| `--headless` | off | Run without UI (console mode) |
| `--x`, `--y` | - | Target coordinates (headless mode) |
| `--calibrate` | off | Force calibration (headless mode) |

### Examples

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

## How to Use

1. **Start the app** - A small window appears with a red status light
2. **Position your mouse** over the target click location
3. **Press PageUp** (or your calibrate hotkey) - The position is saved
4. **Press PageDown** (or your toggle hotkey) - Clicking starts (light turns green)
5. **Press PageDown again** to pause, **End** to quit

The app stays on top so you can always see the status.
