"""main_config.py - Config loading, detection, and CostTracker for main.py."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

CONFIG_PATH = Path.home() / ".forge" / "config.json"

_DEFAULT_CONFIG = {
    "engines": {
        "claude": {"path": "", "installed": False},
        "codex": {"path": "", "installed": False},
    },
    "default_engine": "claude",
    "default_mode": "forge",
    "review_mode": False,
    "token_warning_pct": 85,
    "token_kill_pct": 95,
}


def detect_engines() -> dict:
    result = {}
    for engine in ["claude", "codex"]:
        path = shutil.which(engine)
        result[engine] = {"installed": path is not None, "path": path or ""}
    return result


def load_config() -> dict | None:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
