# CodeAway installer — Windows.
#
# What this script does, in order:
#   1. Installs uv (Astral's Python package manager) if missing.
#   2. Clones https://github.com/jmoraispk/codeaway into ~\codeaway
#      (or git pulls if the directory already exists).
#   3. Runs `uv sync` to materialise dependencies into
#      the local .venv. Nothing is installed system-wide.
#   4. Prints how to launch the app — does NOT auto-launch, so you
#      can read what's about to happen first.
#
# 100% local. The only network calls are to astral.sh (uv installer),
# pypi.org (Python deps), and github.com (the source). No telemetry.
#
# Read the source: https://github.com/jmoraispk/codeaway/blob/main/site/install.ps1
# Run with:        powershell -c "irm https://codeaway.dev/install.ps1 | iex"

$ErrorActionPreference = "Stop"

$RepoUrl    = "https://github.com/jmoraispk/codeaway.git"
$InstallDir = if ($env:CODEAWAY_DIR) { $env:CODEAWAY_DIR } else { Join-Path $HOME "codeaway" }

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host ">> $msg" -ForegroundColor Cyan
}
function Write-Ok([string]$msg)   { Write-Host $msg -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host $msg -ForegroundColor Yellow }

Write-Step "CodeAway installer"
Write-Host "Target: $InstallDir"
Write-Host "Source: $RepoUrl"

# -- 1. uv -----------------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Step "Installing uv (Astral)"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # uv writes itself to %USERPROFILE%\.local\bin and updates User PATH,
    # but the current shell hasn't refreshed yet — re-pull User PATH so
    # the rest of this script can find `uv` without asking the user to
    # restart their terminal.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Warn "uv installed but not on this session's PATH. Open a new terminal and rerun the installer."
        exit 1
    }
} else {
    Write-Host "uv: $(uv --version)"
}

# -- 2. git clone / pull --------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warn "git is required but was not found on PATH."
    Write-Warn "Install Git for Windows: https://git-scm.com/download/win"
    exit 1
}
if (Test-Path $InstallDir) {
    Write-Step "Updating existing checkout in $InstallDir"
    git -C $InstallDir pull --ff-only
} else {
    Write-Step "Cloning into $InstallDir"
    git clone $RepoUrl $InstallDir
}

# -- 3. dependencies ------------------------------------------------
Write-Step "Syncing dependencies (uv sync)"
Push-Location $InstallDir
try {
    uv sync
} finally {
    Pop-Location
}

# -- 4. done --------------------------------------------------------
Write-Host ""
Write-Ok "================================================================"
Write-Ok " CodeAway installed."
Write-Host ""
Write-Host " Run it:"
Write-Host "   cd $InstallDir"
Write-Host "   uv run main.py"
Write-Host ""
Write-Host " Update later: cd $InstallDir; git pull; uv sync"
Write-Host " Source:       https://github.com/jmoraispk/codeaway"
Write-Ok "================================================================"
