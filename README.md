# auto-press

Auto clicker with mode selection, global hotkeys, multi-target support, timer UI, and optional per-target state detection.

## Features

- Click-only, enter-only, and click+enter modes
- Multi-target rotation (1-3 targets)
- Lightweight countdown timer checkbox in UI
- Global hotkeys (Windows) for start/stop and click calibration
- Optional per-target region state detection:
  - finished match above threshold -> click + `continue` + enter
  - otherwise -> click + enter

## Requirements

- Windows recommended (global hotkeys use Win32 API)
- Python 3.10+ (run through `uv`)

Optional state detection dependencies:

```bash
uv sync --extra vision
```

## Usage

```bash
uv run press_enter.py [seconds] [options]
```

### Core options

| Option | Default | Description |
|--------|---------|-------------|
| `seconds` | `10.0` | Interval between actions in seconds |
| `--mode` | `click` | `enter`, `click`, or `click+enter` |
| `--targets` | `1` | Number of targets (1-3), click modes only |
| `--headless` | off | Run without UI |
| `--toggle` | `PAGEDOWN` | Global hotkey to start/stop (UI) |
| `--calibrate-key` | `PAGEUP` | Global hotkey to set click position (UI) |
| `--x`, `--y` | - | Click target coordinate in headless |
| `--calibrate` | off | Force console calibration in headless |

### State detection options (headless)

| Option | Default | Description |
|--------|---------|-------------|
| `--state-detect` | off | Enable state detection in `click+enter` mode |
| `--state-word` | `continue` | Word typed before Enter when state is finished |
| `--state-bbox` | - | Region as `left,top,width,height` |
| `--state-finished-template` | - | Path to FINISHED template image |
| `--state-threshold` | `0.80` | Minimum confidence for best match |

## Examples

```bash
# UI, default mode (click only)
uv run press_enter.py

# UI, click + enter mode with 2 targets
uv run press_enter.py 10 --mode click+enter --targets 2

# Headless enter-only (replacement for old simple mode)
uv run press_enter.py 5 --headless --mode enter

# Headless click-only
uv run press_enter.py 5 --headless --mode click --x 500 --y 300

# Headless click+enter with state detection
uv run press_enter.py 5 --headless --mode click+enter \
  --x 500 --y 300 \
  --state-detect \
  --state-bbox 120,80,900,140 \
  --state-finished-template finished.png
```

## UI state detection workflow

1. Start in `click+enter` mode.
2. Choose setup target (`T1`, `T2`, etc.).
3. Set click point with `Calibrate`.
4. Use `Drag Capture Finished` while target is in finished state.
5. Enable `State Detection`.
6. Start run.

Target status legend in UI:
- `C*` click point set
- `R*` region set
- `F*` finished template set
