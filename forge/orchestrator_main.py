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
from .live_log import make_live_logger, summarize_paths, summarize_text
from .security import (
    ApprovedPaths,
    SessionGuard,
    check_package_install,
    check_typosquatting,
    detect_prompt_injection,
    is_project_confirm,
    is_safe_path,
)

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


def _handle_package_install(
    cmd: list[str],
    approved: ApprovedPaths,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """回傳 True 表示可以繼續執行，False 表示使用者拒絕。"""
    is_install, packages = check_package_install(cmd)
    if not is_install:
        return True

    # 已批量批准套件安裝，只提示 typosquatting，不擋
    if approved.is_batch_approved("package_install"):
        suspects = [(p, check_typosquatting(p)) for p in packages]
        typos = [(p, s) for p, s in suspects if s]
        if typos and on_log:
            for pkg, suggestion in typos:
                on_log(f"⚠️ 套件名稱疑似拼寫錯誤：{pkg!r}（接近 {suggestion!r}），請確認")
        return True  # 不擋，繼續執行

    # 需要逐次確認：回傳套件清單讓 UI 顯示
    return False  # 由 UI 層處理確認邏輯


def should_confirm_path(path: Path, project_root: Path, approved: ApprovedPaths) -> bool:
    """回傳 True 表示需要使用者確認。"""
    if approved.is_approved(path):
        return False  # 已單獨批准
    if is_project_confirm(path, project_root) and approved.is_batch_approved("build_config"):
        return False  # 已批量批准建置設定
    return is_project_confirm(path, project_root)


def format_confirm_message(
    reason: str,
    detail: str,
    rule: str,
    continue_action: str = "確認，繼續執行",
    cancel_action: str = "取消，重新規劃",
) -> str:
    """格式化確認訊息，讓使用者知道發生什麼事。"""
    return (
        f"⚠️ **Forge 需要你確認才能繼續**\n\n"
        f"**原因：** {reason}\n"
        f"**詳細：** {detail}\n"
        f"**對應規則：** {rule}\n\n"
        f"[{continue_action}] [批量批准此類操作] [{cancel_action}]"
    )


def format_hardblock_message(reason: str, detail: str) -> str:
    """格式化 hard block 訊息。"""
    return (
        f"🔴 **Forge 已停止**\n\n"
        f"**原因：** {reason}\n"
        f"**詳細：** {detail}\n\n"
        f"請修正後繼續，或切換到直接執行模式手動處理。"
    )


def parse_clarification_reply(user_input: str) -> str:
    """Parse user reply to a clarification bubble.

    Returns:
        'proceed'   - user confirms, continue as-is
        'abort'     - user explicitly cancels
        'rerecon'   - user wants to refresh project understanding
    Fuzzy matching: unrecognised replies default to 'proceed'.
    """
    stripped = user_input.strip()
    ABORT_WORDS = {"終止", "停止", "取消", "不要", "算了", "abort", "cancel", "stop"}
    RERECON_WORDS = {"重新認識", "重新掃描", "更新認識", "recon", "re-recon", "rescan"}

    if stripped.lower() in ABORT_WORDS:
        return "abort"
    if any(w in stripped for w in RERECON_WORDS):
        return "rerecon"
    return "proceed"


def parse_review_reply(user_input: str) -> str:
    """解析 needs_review 後的使用者回覆。
    回傳 'terminate' | 'continue' | 'correction:<text>'
    """
    stripped = user_input.strip()
    if stripped in ("終止", "停止", "取消", "terminate", "stop", "cancel"):
        return "terminate"
    if stripped.startswith("修正：") or stripped.startswith("修正:"):
        correction = stripped.split("：", 1)[-1].split(":", 1)[-1].strip()
        return f"correction:{correction}"
    if stripped.startswith("Correction:") or stripped.startswith("correction:"):
        correction = stripped.split(":", 1)[-1].strip()
        return f"correction:{correction}"
    # 繼續 or anything else we don't recognise
    return "continue"


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
    session_guard: SessionGuard | None = None,
    approved_paths: ApprovedPaths | None = None,
) -> dict:
    """Route user input to direct mode or Forge mode.

    Args:
        mode: "direct" or "forge"
        round_num: current round number (for Forge mode)

    Returns:
        {"status": str, "output": str, "round": int}
    """

    log = make_live_logger(on_log, f"router r{round_num:03d}")
    log(
        "Received request: "
        f"mode={mode}, engine={engine}, review_mode={review_mode}, "
        f"project={project_path}, uploads={len(uploaded_files)}"
    )
    log(f"User input preview: {summarize_text(user_input, 180)}")
    if uploaded_files:
        log(f"Uploaded files: {summarize_paths(uploaded_files)}")

    # ── Prompt injection check ────────────────────────────────────────────
    if detect_prompt_injection(user_input):
        log("Blocked request because prompt injection patterns were detected")
        return {
            "status": "blocked",
            "output": "⚠️ 偵測到可能的 prompt injection 攻擊，已阻止執行。",
            "round": round_num,
        }

    # ── Direct execution mode ─────────────────────────────────────────────
    if mode == "direct":
        log("Routing to direct execution mode")
        warning = safety_check(user_input)
        if warning:
            log(f"Direct execution paused for confirmation: {summarize_text(warning)}")
            return {
                "status": "needs_confirm",
                "output": warning,
                "round": round_num,
            }
        log("Calling agent.do() with 0 context files")
        output = _agent.do(
            user_input,
            context_files=[],
            engine=engine,
            cwd=project_path,
            model="opus",
            on_log=log,
        )
        log(f"Direct execution completed; output_chars={len(output)}")
        return {"status": "done", "output": output, "round": round_num}

    # ── Forge mode ────────────────────────────────────────────────────────
    agent_dir = project_path / ".agent"
    log(f"Using agent directory: {agent_dir}")

    if not agent_dir.exists() or not (agent_dir / "purpose.md").exists():
        # First time initialization
        log("Forge state is missing or incomplete; starting initialization flow")
        result = _init.run(
            user_input,
            uploaded_files,
            project_path,
            engine,
            on_log=on_log,
            review_mode=review_mode,
        )
        log(
            "Initialization finished: "
            f"needs_review={result.get('needs_review', False)}, "
            f"plan_chars={len(result.get('plan', ''))}"
        )
        if result.get("needs_clarification"):
            _status = "needs_clarification"
        elif result.get("needs_review"):
            _status = "needs_review"
        else:
            _status = "initialized"
        if _status == "needs_clarification":
            from . import main as _main_module
            _sess = getattr(_main_module, "_session", None)
            if _sess is not None:
                _sess.pending_clarification = True
                _sess.pending_input = user_input
                log("pending_clarification 已設為 True，等待使用者回覆")
        elif _status == "needs_review":
            from . import main as _main_module
            _sess = getattr(_main_module, "_session", None)
            if _sess is not None:
                _sess.pending_review = True
                log("pending_review 已設為 True，等待使用者回覆")
        return {
            "status": _status,
            "output": result.get("plan", "初始化完成"),
            "round": round_num,
        }
    else:
        # Continuation: run one loop round
        log("Found existing Forge state; continuing main loop")

        # ── Clarification reply handling ───────────────────────────────────
        from . import main as _main_module
        _sess = getattr(_main_module, "_session", None)
        if _sess is not None and getattr(_sess, "pending_clarification", False):
            _sess.pending_clarification = False
            reply = parse_clarification_reply(user_input)
            log(f"Clarification reply parsed: {reply}")

            if reply == "abort":
                log("使用者選擇中止，不繼續執行")
                return {
                    "status": "blocked",
                    "output": "已取消。你可以重新描述需求再試一次。",
                    "round": round_num,
                }

            if reply == "rerecon":
                log("使用者要求重新認識程式，重跑 recon...")
                from .orchestrator_init import _fast_recon
                from .security import safe_write, update_manifest, build_manifest
                new_recon = _fast_recon(project_path)
                recon_path = agent_dir / "recon.md"
                safe_write(recon_path, new_recon)
                build_manifest(agent_dir)
                log(f"Recon refreshed: chars={len(new_recon)}")
                return {
                    "status": "continue",
                    "output": "已更新對程式的認識，繼續執行。",
                    "round": round_num,
                }

            # proceed: run init again with stored pending input
            pending_input = getattr(_sess, "pending_input", user_input)
            log(f"Proceeding with clarified input: {summarize_text(pending_input, 80)}")
            result = _init.run(
                pending_input,
                uploaded_files,
                project_path,
                engine,
                on_log=on_log,
                review_mode=review_mode,
                skip_clarification=True,
            )
            if result.get("needs_clarification"):
                _status = "needs_clarification"
            elif result.get("needs_review"):
                _status = "needs_review"
            else:
                _status = "initialized"
            return {
                "status": _status,
                "output": result.get("plan", "初始化完成"),
                "round": round_num,
            }

        # ── Review reply handling ──────────────────────────────────────
        _sess = getattr(_main_module, "_session", None)
        if _sess is not None and getattr(_sess, "pending_review", False):
            _sess.pending_review = False
            reply = parse_review_reply(user_input)
            log(f"Review reply parsed: {reply}")

            if reply == "terminate":
                log("使用者選擇終止任務")
                return {
                    "status": "blocked",
                    "output": "任務已由使用者終止。",
                    "round": round_num,
                }

            if reply.startswith("correction:"):
                correction_text = reply[len("correction:"):]
                correction_path = agent_dir / "current_task.md"
                existing = correction_path.read_text(encoding="utf-8", errors="replace") if correction_path.exists() else ""
                from .security import safe_write, update_manifest
                safe_write(correction_path, f"{existing}\n\n## 使用者修正意見\n{correction_text}")
                update_manifest(correction_path)
                log(f"Correction written to current_task.md: {summarize_text(correction_text, 80)}")
                # Fall through: let think() pick up the correction
                review_mode = False  # run full think() with correction

            if reply == "continue":
                log("使用者選擇繼續，跳過 think()，直接進 do()")
                review_mode = False  # disable so loop doesn't gate again

        if session_guard is not None:
            log("Incrementing session guard turn counter")
            session_guard.check_and_increment()
            log(
                "Session guard state: "
                f"turns={session_guard.turns}/{session_guard.max_turns}, "
                f"tokens={session_guard.tokens:,}/{session_guard.max_tokens:,}"
            )
        log(f"Creating checkpoint before round {round_num}")
        create_checkpoint(project_path, round_num)
        commits = list_commits(project_path, max_count=1)
        if commits:
            latest = commits[0]
            log(
                "Checkpoint created: "
                f"{latest.get('hash', '')[:8]} {summarize_text(latest.get('msg', ''), 90)}"
            )
        else:
            log("Checkpoint created; no commit metadata available")
        result = _loop.run(
            user_message=user_input,
            project_path=project_path,
            engine=engine,
            round_num=round_num,
            on_log=on_log,
            review_mode=review_mode,
        )
        status = result.get("status", "continue")
        log(
            "Loop round finished: "
            f"status={status}, tokens={result.get('tokens', 0)}, "
            f"summary={summarize_text(result.get('summary', ''), 120)}"
        )
        if status == "done":
            notify("Forge ✅", f"任務完成（共 {round_num} 輪）")
        elif status == "blocked":
            notify("Forge ⚠️", "需要你的決定")

        if status in ("needs_review", "needs_clarification"):
            from . import main as _main_module
            _sess = getattr(_main_module, "_session", None)
            if _sess is not None:
                if status == "needs_clarification":
                    _sess.pending_clarification = True
                else:
                    _sess.pending_review = True
                log(f"pending state 已設為 True：{status}")

        return {
            "status": status,
            "output": result.get("summary", ""),
            "round": round_num,
        }
