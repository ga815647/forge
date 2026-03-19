"""security.py - Path safety, manifest validation, prompt injection detection, safe file writes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

# ── Prompt injection patterns ────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    r"ignore\s+(previous|all|prior)\s+instructions?",
    r"system\s*:",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"\[INST\]",
    r"</s>",
    r"### Instruction",
    r"Human:\s",
    r"Assistant:\s",
    r"BEGINNING OF CONVERSATION",
    r"You are now",
    r"Disregard\s+(all|previous|prior)",
    r"Forget\s+(everything|all|your|prior)",
]

_INJECTION_RE = re.compile(
    "|".join(_INJECTION_PATTERNS), re.IGNORECASE | re.MULTILINE
)


def detect_prompt_injection(text: str) -> bool:
    """Return True if text contains known prompt injection patterns."""
    return bool(_INJECTION_RE.search(text))


# ── Path safety ───────────────────────────────────────────────────────────────


def is_safe_path(path: Path, project_root: Path) -> bool:
    """Return True iff path is inside project_root (no escaping)."""
    try:
        path.resolve().relative_to(project_root.resolve())
        return True
    except ValueError:
        return False


# ── Safe atomic write ─────────────────────────────────────────────────────────


def safe_write(path: Path, content: str) -> None:
    """Write content to path using write-then-rename for crash safety.

    Retries up to 3 times on Windows file-lock errors.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    for attempt in range(3):
        try:
            os.replace(str(tmp), str(path))
            return
        except OSError:
            if attempt < 2:
                time.sleep(0.5)
            else:
                # Last resort
                tmp.rename(path)


# ── Manifest (.agent/.manifest) ───────────────────────────────────────────────


def _file_hash(path: Path) -> str:
    """SHA-256 hex digest of file contents."""
    try:
        data = path.read_bytes()
        return hashlib.sha256(data).hexdigest()
    except OSError:
        return ""


def _manifest_path(agent_dir: Path) -> Path:
    return agent_dir / ".manifest"


def load_manifest(agent_dir: Path) -> dict[str, str]:
    """Load {relative_path: sha256} manifest from .agent/.manifest."""
    mpath = _manifest_path(agent_dir)
    if not mpath.exists():
        return {}
    try:
        return json.loads(mpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def update_manifest(file_path: Path) -> None:
    """Update the manifest entry for file_path.

    Assumes file_path is inside an .agent/ directory.
    """
    agent_dir = _find_agent_dir(file_path)
    if agent_dir is None:
        return
    manifest = load_manifest(agent_dir)
    rel = str(file_path.relative_to(agent_dir))
    manifest[rel] = _file_hash(file_path)
    mpath = _manifest_path(agent_dir)
    safe_write(mpath, json.dumps(manifest, indent=2, ensure_ascii=False))


def build_manifest(agent_dir: Path) -> None:
    """Build manifest from scratch for all files in agent_dir."""
    manifest: dict[str, str] = {}
    for f in agent_dir.rglob("*"):
        if f.is_file() and f.name != ".manifest":
            rel = str(f.relative_to(agent_dir))
            manifest[rel] = _file_hash(f)
    mpath = _manifest_path(agent_dir)
    safe_write(mpath, json.dumps(manifest, indent=2, ensure_ascii=False))


def verify_manifest(agent_dir: Path) -> list[str]:
    """Check manifest against actual files. Return list of anomaly descriptions."""
    manifest = load_manifest(agent_dir)
    anomalies: list[str] = []

    # Check for files modified outside Forge
    for rel, expected_hash in manifest.items():
        actual_path = agent_dir / rel
        if not actual_path.exists():
            anomalies.append(f"消失的檔案: {rel}")
            continue
        actual = _file_hash(actual_path)
        if actual != expected_hash:
            anomalies.append(f"外部修改: {rel}")

    # Check for new files not in manifest
    for f in agent_dir.rglob("*"):
        if f.is_file() and f.name != ".manifest" and not f.suffix == ".tmp":
            rel = str(f.relative_to(agent_dir))
            if rel not in manifest:
                anomalies.append(f"未知檔案: {rel}")

    return anomalies


def _find_agent_dir(path: Path) -> Path | None:
    """Walk up from path to find the .agent directory it belongs to."""
    for parent in [path.parent, path.parent.parent]:
        if parent.name == ".agent" or (parent / ".manifest").exists():
            return parent if parent.name == ".agent" else None
        candidate = parent
        if candidate.name == ".agent":
            return candidate
    # Try: path is inside .agent/something
    parts = path.parts
    for i, part in enumerate(parts):
        if part == ".agent":
            return Path(*parts[: i + 1])
    return None
