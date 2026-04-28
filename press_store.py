"""Persistence helpers for rules, templates, and the UI config."""

from __future__ import annotations

import json
import uuid
from pathlib import Path


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
CONFIG_PATH = TEMPLATES_DIR / "config.json"

ACTION_CLICK = "click"
ACTION_CLICK_TYPE_ENTER = "click+type+enter"
ACTION_TYPES = [ACTION_CLICK, ACTION_CLICK_TYPE_ENTER]

MATCHER_TEMPLATE = "template"
MATCHER_COLOR = "color"
MATCHER_TYPES = [MATCHER_TEMPLATE, MATCHER_COLOR]


def default_rule(name: str = "New Rule") -> dict:
    return {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "enabled": True,
        "matcher": MATCHER_TEMPLATE,
        "template_path": None,
        # Color-matcher fields. Empty until a color is captured.
        "color_rgb": None,
        "color_capture_area": 0,
        "search_region": None,
        "threshold": 0.90,
        "action": ACTION_CLICK,
        "text": "continue",
        "priority": 1,
    }


# Windows Virtual-Key code for the default global start/stop hotkey.
# 0x22 is VK_PAGEDOWN; modifiers bitmask matches RegisterHotKey MOD_* flags.
DEFAULT_HOTKEY_VK = 0x22
DEFAULT_HOTKEY_MODS = 0


def default_config() -> dict:
    return {
        "interval_seconds": 10.0,
        "hotkey_vk": DEFAULT_HOTKEY_VK,
        "hotkey_mods": DEFAULT_HOTKEY_MODS,
        "rules": [],
    }


def ensure_templates_dir() -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def _clamp_float(value, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def _valid_region(region) -> bool:
    if region is None:
        return True
    if not isinstance(region, list) or len(region) != 4:
        return False
    try:
        left, top, width, height = [int(v) for v in region]
    except (TypeError, ValueError):
        return False
    return width > 0 and height > 0 and left >= 0 and top >= 0


def _valid_rgb(value) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return False
    try:
        return all(0 <= int(c) <= 255 for c in value)
    except (TypeError, ValueError):
        return False


def _normalize_rule(rule: dict, priority: int) -> dict:
    base = default_rule()
    if isinstance(rule, dict):
        for key in base:
            if key in rule:
                base[key] = rule[key]
    if not isinstance(base.get("id"), str) or not base["id"].strip():
        base["id"] = uuid.uuid4().hex[:8]
    if not isinstance(base.get("name"), str) or not base["name"].strip():
        base["name"] = f"Rule {priority}"
    if base.get("action") not in ACTION_TYPES:
        base["action"] = ACTION_CLICK
    if base.get("matcher") not in MATCHER_TYPES:
        base["matcher"] = MATCHER_TEMPLATE
    base["enabled"] = bool(base.get("enabled", True))
    base["threshold"] = _clamp_float(base.get("threshold"), 0.90, 0.0, 1.0)
    base["priority"] = priority
    if not isinstance(base.get("text"), str):
        base["text"] = "continue"
    if not _valid_region(base.get("search_region")):
        base["search_region"] = None
    tpl = base.get("template_path")
    if not isinstance(tpl, str) or not tpl.strip():
        base["template_path"] = None
    if _valid_rgb(base.get("color_rgb")):
        base["color_rgb"] = [int(c) for c in base["color_rgb"]]
    else:
        base["color_rgb"] = None
    try:
        area = int(base.get("color_capture_area") or 0)
    except (TypeError, ValueError):
        area = 0
    base["color_capture_area"] = max(0, area)
    return base


def _valid_vk(value) -> bool:
    try:
        return 0 <= int(value) <= 0xFFFF
    except (TypeError, ValueError):
        return False


def normalize_config(config: dict | None) -> dict:
    base = default_config()
    if isinstance(config, dict):
        if "interval_seconds" in config:
            base["interval_seconds"] = _clamp_float(config["interval_seconds"], 10.0, 0.1, 86400.0)
        if _valid_vk(config.get("hotkey_vk")):
            base["hotkey_vk"] = int(config["hotkey_vk"])
        if _valid_vk(config.get("hotkey_mods")):
            base["hotkey_mods"] = int(config["hotkey_mods"])
        raw_rules = config.get("rules")
        if isinstance(raw_rules, list):
            base["rules"] = [_normalize_rule(rule, idx + 1) for idx, rule in enumerate(raw_rules)]
    return base


def load_config() -> dict:
    ensure_templates_dir()
    if not CONFIG_PATH.exists():
        return default_config()
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default_config()
    return normalize_config(loaded)


def save_config(config: dict) -> None:
    ensure_templates_dir()
    normalized = normalize_config(config)
    CONFIG_PATH.write_text(json.dumps(normalized, indent=2), encoding="utf-8")


def template_asset_path(name: str) -> Path:
    ensure_templates_dir()
    return TEMPLATES_DIR / name


def serialize_template_path(path: str | Path) -> str:
    p = Path(path)
    try:
        return str(p.resolve().relative_to(TEMPLATES_DIR.resolve()))
    except ValueError:
        return str(p.resolve())


def resolve_template_path(template_ref: str | None) -> Path | None:
    if not template_ref:
        return None
    p = Path(template_ref)
    if p.is_absolute():
        return p
    return TEMPLATES_DIR / p


def relativize_template_path(path: str | Path) -> str:
    p = Path(path)
    try:
        return str(p.resolve().relative_to(TEMPLATES_DIR.resolve()))
    except ValueError:
        return p.name


def list_template_files() -> list[str]:
    ensure_templates_dir()
    exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
    files = [p.name for p in TEMPLATES_DIR.iterdir() if p.is_file() and p.suffix.lower() in exts]
    return sorted(files)


def make_rule_summary(rule: dict, last_score: float | None = None) -> str:
    enabled = "on" if rule.get("enabled") else "off"
    scope = "screen" if not rule.get("search_region") else "region"
    action = rule.get("action", ACTION_CLICK)
    score = "-" if last_score is None else f"{last_score:.3f}"
    return f"{rule.get('priority', '?')}. {rule.get('name', 'Rule')} [{enabled}] {action} {scope} score={score}"
