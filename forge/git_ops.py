"""git_ops.py - Git checkpoint, rollback, and squash operations."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def create_checkpoint(project_path: Path, round_num: int) -> bool:
    """Create a git checkpoint commit. Returns True on success."""
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(project_path),
                       capture_output=True, check=False)
        result = subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"forge-checkpoint-{round_num:03d}"],
            cwd=str(project_path), capture_output=True, check=False,
        )
        return result.returncode == 0
    except (OSError, FileNotFoundError):
        return False


def list_commits(project_path: Path, max_count: int = 30) -> list[dict]:
    """Return list of recent git commits as {"hash": str, "msg": str}."""
    try:
        result = subprocess.run(
            ["git", "log", f"--max-count={max_count}", "--format=%H %s"],
            cwd=str(project_path), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        if result.returncode != 0:
            return []
        commits = []
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2:
                commits.append({"hash": parts[0], "msg": parts[1]})
        return commits
    except (OSError, FileNotFoundError):
        return []


def rollback(project_path: Path, target_hash: str, force_stop_fn=None) -> bool:
    """Rollback to target git commit. Returns True on success."""
    if not re.match(r"^[0-9a-f]{7,40}$", target_hash):
        return False

    if force_stop_fn:
        force_stop_fn()

    try:
        result = subprocess.run(
            ["git", "reset", "--hard", target_hash],
            cwd=str(project_path), capture_output=True, check=False,
        )
        if result.returncode == 0:
            agent_dir = project_path / ".agent"
            timeline_path = agent_dir / "timeline.md"
            if timeline_path.exists():
                from . import timeline as _tl
                _tl.append_round(
                    timeline_path, round_num=0, round_type="rollback",
                    task=f"to {target_hash[:7]}", result="ok", decision="user",
                )
        return result.returncode == 0
    except (OSError, FileNotFoundError):
        return False


def squash_and_push(project_path: Path) -> dict:
    """Squash forge checkpoints into one meaningful commit. Returns status dict."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H %s", "--all"],
            cwd=str(project_path), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        lines = result.stdout.strip().splitlines()
        checkpoint_hashes = [l.split()[0] for l in lines if "forge-checkpoint" in l]
        if not checkpoint_hashes:
            return {"status": "no_checkpoints"}

        earliest = checkpoint_hashes[-1]
        subprocess.run(
            ["git", "reset", "--soft", f"{earliest}^"],
            cwd=str(project_path), capture_output=True, check=False,
        )

        agent_dir = project_path / ".agent"
        purpose_path = agent_dir / "purpose.md"
        msg = "feat: Forge task complete"
        if purpose_path.exists():
            for line in purpose_path.read_text(encoding="utf-8", errors="replace").splitlines()[:5]:
                if line.strip() and not line.startswith("#"):
                    msg = f"feat: {line.strip()[:60]}"
                    break

        return {"status": "ready", "message": msg}
    except (OSError, FileNotFoundError):
        return {"status": "error"}
