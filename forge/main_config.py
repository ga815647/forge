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
        path = find_engine_path(engine)
        result[engine] = {"installed": path is not None, "path": path or ""}
    return result


def find_engine_path(engine: str) -> str | None:
    configured = _configured_engine_path(engine)
    candidates: list[str] = []
    if configured:
        candidates.append(configured)

    for name in _candidate_engine_names(engine):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    fallback = _fallback_engine_path(engine)
    if fallback is not None:
        candidates.append(str(fallback))

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.exists():
            return str(path)

    return None


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


def _configured_engine_path(engine: str) -> str | None:
    cfg = load_config() or {}
    engines = cfg.get("engines") or {}
    engine_cfg = engines.get(engine) or {}
    path = engine_cfg.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return None


def _candidate_engine_names(engine: str) -> list[str]:
    names = [engine]
    if engine == "claude":
        names.extend(["claude.exe", "claude.cmd"])
    elif engine == "codex":
        names.extend(["codex.exe", "codex.cmd"])
    return names


def _fallback_engine_path(engine: str) -> Path | None:
    if engine != "codex":
        return None

    extensions_dir = Path.home() / ".vscode" / "extensions"
    if not extensions_dir.exists():
        return None

    matches = sorted(
        extensions_dir.glob("openai.chatgpt-*/bin/**/codex.exe"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None
