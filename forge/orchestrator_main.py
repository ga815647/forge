"""orchestrator_main.py - Entry point: routing, safety_check, CostTracker, rollback."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from . import agent as _agent
from . import orchestrator_init as _init
from . import orchestrator_loop as _loop
from .git_ops import create_checkpoint, list_commits, rollback, squash_and_push
from .security import detect_prompt_injection, is_safe_path

# ── Dangerous pattern detection (Python deterministic) ───────────────────────

_DANGEROUS_PATTERNS = [
    r"rm\s+-rf",
    r"drop\s+table",
    r"\bformat\b.*[cC]:",
    r"\bdeltree\b",
    r"push\s+.*\bmain\b",
    r"push\s+.*\bmaster\b",
    r"force\s+push",
    r"--force\b",
    r"reset\s+--hard",
    r"git\s+clean\s+-fd",
]

_DANGEROUS_RE = re.compile(
    "|".join(_DANGEROUS_PATTERNS), re.IGNORECASE | re.MULTILINE
)


def safety_check(user_input: str) -> str | None:
    """Return warning message if user_input contains dangerous patterns, else None."""
    match = _DANGEROUS_RE.search(user_input)
    if match:
        return f"⚠️ 偵測到危險操作：`{match.group()}`。確定要執行嗎？"
    return None


# ── Cost tracker ──────────────────────────────────────────────────────────────


class CostTracker:
    def __init__(self) -> None:
        self.total_tokens: int = 0
        self.rounds: list[dict] = []

    def add(self, round_num: int, round_type: str, tokens: int) -> None:
        self.total_tokens += tokens
        self.rounds.append(
            {"round": round_num, "type": round_type, "tokens": tokens}
        )

    def summary(self) -> str:
        return f"💰 本次: {self.total_tokens:,} token（{len(self.rounds)} 輪）"


# ── Force stop ────────────────────────────────────────────────────────────────


def force_stop() -> None:
    """Immediately kill any running LLM subprocess and save state."""
    process = _agent.get_current_process()
    if process is not None:
        try:
            from .monitor import kill_proc_tree
            kill_proc_tree(process.pid)
        except Exception:
            try:
                process.kill()
            except OSError:
                pass


# ── Desktop notification ──────────────────────────────────────────────────────


def notify(title: str, message: str) -> None:
    """Cross-platform desktop notification. Silent if plyer not installed."""
    try:
        from plyer import notification  # type: ignore[import]
        notification.notify(title=title, message=message, timeout=10)
    except (ImportError, Exception):
        pass


# ── Main routing ──────────────────────────────────────────────────────────────


def handle_input(
    user_input: str,
    uploaded_files: list[Path],
    mode: str,
    project_path: Path,
    engine: str,
    on_log: Callable[[str], None] | None = None,
    review_mode: bool = False,
    round_num: int = 1,
    cost_tracker: CostTracker | None = None,
) -> dict:
    """Route user input to direct mode or Forge mode.

    Args:
        mode: "direct" or "forge"
        round_num: current round number (for Forge mode)

    Returns:
        {"status": str, "output": str, "round": int}
    """

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    # ── Prompt injection check ────────────────────────────────────────────
    if detect_prompt_injection(user_input):
        return {
            "status": "blocked",
            "output": "⚠️ 偵測到可能的 prompt injection 攻擊，已阻止執行。",
            "round": round_num,
        }

    # ── Direct execution mode ─────────────────────────────────────────────
    if mode == "direct":
        warning = safety_check(user_input)
        if warning:
            return {
                "status": "needs_confirm",
                "output": warning,
                "round": round_num,
            }
        output = _agent.do(
            user_input,
            context_files=[],
            engine=engine,
            cwd=project_path,
            model="opus",
        )
        return {"status": "done", "output": output, "round": round_num}

    # ── Forge mode ────────────────────────────────────────────────────────
    agent_dir = project_path / ".agent"

    if not agent_dir.exists() or not (agent_dir / "purpose.md").exists():
        # First time initialization
        log("🚀 首次啟動 Forge 初始化...")
        result = _init.run(
            user_input,
            uploaded_files,
            project_path,
            engine,
            on_log=on_log,
            review_mode=review_mode,
        )
        return {
            "status": "needs_review" if result.get("needs_review") else "initialized",
            "output": result.get("plan", "初始化完成"),
            "round": round_num,
        }
    else:
        # Continuation: run one loop round
        create_checkpoint(project_path, round_num)
        result = _loop.run(
            user_message=user_input,
            project_path=project_path,
            engine=engine,
            round_num=round_num,
            on_log=on_log,
            review_mode=review_mode,
        )
        status = result.get("status", "continue")
        if status == "done":
            notify("Forge ✅", f"任務完成（共 {round_num} 輪）")
        elif status == "blocked":
            notify("Forge ⚠️", "需要你的決定")

        return {
            "status": status,
            "output": result.get("summary", ""),
            "round": round_num,
        }
