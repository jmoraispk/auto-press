import json
from pathlib import Path


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
CONFIG_PATH = TEMPLATES_DIR / "config.json"


def _default_target() -> dict:
    return {
        "click_target": None,  # [x, y]
        "state_roi": None,  # [left, top, width, height]
        "state_template": None,  # relative path under templates/
        "run_roi": None,  # [left, top, width, height]
    }


def default_config(num_targets: int = 1) -> dict:
    return {
        "mode": "click+enter",
        "interval_seconds": 10.0,
        "state_detect_enabled": True,
        "state_word": "continue",
        "state_threshold_ui": 0.80,  # UI/testing threshold only
        "run_threshold": 0.85,
        "run_cooldown_seconds": 1.5,
        "run_templates": [],  # list[str], relative paths under templates/
        "targets": [_default_target() for _ in range(num_targets)],
    }


def ensure_templates_dir() -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def load_config(num_targets: int = 1) -> dict:
    ensure_templates_dir()
    cfg = default_config(num_targets)
    if not CONFIG_PATH.exists():
        return cfg

    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return cfg

    if not isinstance(loaded, dict):
        return cfg

    cfg.update({k: v for k, v in loaded.items() if k in cfg and k != "targets"})

    loaded_targets = loaded.get("targets")
    if isinstance(loaded_targets, list):
        targets = []
        for i in range(num_targets):
            base = _default_target()
            if i < len(loaded_targets) and isinstance(loaded_targets[i], dict):
                base.update({k: v for k, v in loaded_targets[i].items() if k in base})
            targets.append(base)
        cfg["targets"] = targets

    if not isinstance(cfg.get("run_templates"), list):
        cfg["run_templates"] = []

    return cfg


def save_config(config: dict) -> None:
    ensure_templates_dir()
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def template_path(name: str) -> Path:
    ensure_templates_dir()
    return TEMPLATES_DIR / name
