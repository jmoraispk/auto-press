#!/usr/bin/env sh
# CodeAway installer — macOS and Linux.
#
# WARNING: This is the cross-platform installer for an app whose
# automation engine currently uses Win32 APIs (per-monitor DPI
# awareness, RegisterHotKey, EnumDisplayMonitors). The install path
# below works on macOS / Linux, but launching `main.py` will fail at
# import time on these platforms today. The phone bridge HTTP service
# is platform-independent and would still serve, but there's nothing
# for it to drive.
#
# In other words: this script gets you a working source checkout and
# a synced .venv. The runtime itself needs a port — see `Request a
# port` on https://codeaway.dev if you want it, or open a PR.
#
# Read the source: https://github.com/jmoraispk/codeaway/blob/main/site/install.sh
# Run with:        curl -fsSL https://codeaway.dev/install.sh | sh

set -e

REPO_URL="https://github.com/jmoraispk/codeaway.git"
INSTALL_DIR="${CODEAWAY_DIR:-$HOME/codeaway}"

step() { printf '\n\033[36m>> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m%s\033[0m\n' "$1"; }
warn() { printf '\033[33m%s\033[0m\n' "$1"; }

step "CodeAway installer (untested platform)"
echo "Target: $INSTALL_DIR"
echo "Source: $REPO_URL"

# -- 1. uv -----------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    step "Installing uv (Astral)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv's installer drops a $HOME/.local/bin/env file that exports the
    # right PATH; sourcing it makes uv visible in this shell.
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env"
    fi
    if ! command -v uv >/dev/null 2>&1; then
        warn "uv installed but not on this session's PATH."
        warn "Open a new terminal and rerun the installer."
        exit 1
    fi
else
    echo "uv: $(uv --version)"
fi

# -- 2. git clone / pull --------------------------------------------
if ! command -v git >/dev/null 2>&1; then
    warn "git is required but was not found on PATH."
    exit 1
fi
if [ -d "$INSTALL_DIR" ]; then
    step "Updating existing checkout in $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
else
    step "Cloning into $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# -- 3. dependencies -------------------------------------------------
step "Syncing dependencies (uv sync --extra bridge)"
( cd "$INSTALL_DIR" && uv sync --extra bridge )

# -- 4. done ---------------------------------------------------------
echo
ok "================================================================"
ok " CodeAway installed at $INSTALL_DIR."
echo
warn " HEADS UP: macOS / Linux runtime is currently UNTESTED."
warn " The engine relies on Win32 APIs and will not function on this"
warn " platform yet. Help wanted:"
warn "   https://github.com/jmoraispk/codeaway/issues"
echo
echo " Run it (will likely fail today):"
echo "   cd $INSTALL_DIR"
echo "   uv run main.py --bridge --activate"
ok "================================================================"
