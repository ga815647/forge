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


def parse_confirm_reply(user_input: str) -> str:
    """解析 needs_confirm 後的使用者回覆。
    回傳 'yes' | 'no'
    """
    stripped = user_input.strip().lower()
    YES_WORDS = {"yes", "y", "確認", "繼續", "執行", "ok", "confirm", "proceed"}
    if stripped in YES_WORDS:
        return "yes"
    return "no"


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
    on_token_warning: Callable[[], None] | None = None,
    on_token_kill: Callable[[], None] | None = None,
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
            "output": (
                "⚠️ 偵測到可能的 prompt injection 攻擊，已阻止執行。\n\n"
                "**如果你不是在描述攻擊，而是有正當的技術需求：**\n"
                "- 把指令放進引號，例如：「分析以下程式碼：`rm -rf /`」\n"
                "- 改用描述性語句：「說明這個指令的風險：…」\n"
                "- 前綴說明情境：「假設我需要處理一個包含以下字串的輸入：…」"
            ),
            "round": round_num,
        }

    # ── Direct execution mode ─────────────────────────────────────────────
    if mode == "direct":
        log("Routing to direct execution mode")

        # ── Pending confirm reply handling ────────────────────────────────
        from . import main as _main_module
        _sess_d = getattr(_main_module, "_session", None)
        if _sess_d is not None and getattr(_sess_d, "pending_confirm", False):
            _sess_d.pending_confirm = False
            reply = parse_confirm_reply(user_input)
            log(f"Confirm reply parsed: {reply}")
            if reply == "no":
                _sess_d.pending_confirm_input = ""
                return {
                    "status": "blocked",
                    "output": "已取消。",
                    "round": round_num,
                }
            # yes: re-execute the stored dangerous command
            original_input = _sess_d.pending_confirm_input
            _sess_d.pending_confirm_input = ""
            log(f"User confirmed; executing original command: {summarize_text(original_input, 80)}")
            output = _agent.do(
                original_input,
                context_files=[],
                engine=engine,
                cwd=project_path,
                model="opus",
                on_log=log,
            )
            log(f"Confirmed direct execution completed; output_chars={len(output)}")
            return {"status": "done", "output": output, "round": round_num}

        warning = safety_check(user_input)
        if warning:
            log(f"Direct execution paused for confirmation: {summarize_text(warning)}")
            if _sess_d is not None:
                _sess_d.pending_confirm = True
                _sess_d.pending_confirm_input = user_input
                log("pending_confirm 已設為 True，等待使用者確認")
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

        from . import main as _main_module
        _sess = getattr(_main_module, "_session", None)

        # ── Existing agent: ask user on first contact ──────────────────────
        if _sess is not None and not getattr(_sess, "pending_existing_agent", True):
            # pending_existing_agent starts as False → we haven't asked yet
            _sess.pending_existing_agent = True
            _sess.pending_clarification = True
            _sess.pending_input = user_input  # store original message
            log("pending_existing_agent: 首次接觸已有 .agent/，詢問使用者")
            return {
                "status": "needs_clarification",
                "output": (
                    "## ⚠️ 偵測到上次 Forge 記憶\n\n"
                    f"專案 `{project_path.name}` 有上次的 Forge session（`.agent/` 目錄已存在）。\n\n"
                    "---\n請回覆：\n"
                    "- **繼續** — 繼續上次的任務（你剛輸入的訊息會作為本輪 user message）\n"
                    "- **重新開始** — 清除上次記憶，以你剛才的輸入重新規劃\n"
                ),
                "round": round_num,
            }

        # ── Existing agent restart reply handling ──────────────────────────
        if _sess is not None and getattr(_sess, "pending_existing_agent", False) and getattr(_sess, "pending_clarification", False):
            _sess.pending_existing_agent = False
            _sess.pending_clarification = False
            original_input = getattr(_sess, "pending_input", user_input)
            _sess.pending_input = ""
            stripped = user_input.strip()
            RESTART_WORDS = {"重新開始", "重新", "restart", "清除", "reset", "新任務"}
            if any(w in stripped for w in RESTART_WORDS):
                import shutil
                agent_dir_to_delete = project_path / ".agent"
                try:
                    shutil.rmtree(str(agent_dir_to_delete))
                    log("使用者選擇重新開始；.agent/ 已刪除")
                except OSError as e:
                    log(f"刪除 .agent/ 失敗: {e}")
                result = _init.run(
                    original_input,
                    uploaded_files,
                    project_path,
                    engine,
                    on_log=on_log,
                    review_mode=review_mode,
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
            else:
                # 繼續上次：用原始 user_input 繼續
                log("使用者選擇繼續上次 session")
                user_input = original_input  # use stored original for the loop

        # ── Path confirmation reply handling ───────────────────────────────
        _sess = getattr(_main_module, "_session", None)
        if _sess is not None and getattr(_sess, "pending_path_confirm", False):
            _sess.pending_path_confirm = False
            files = getattr(_sess, "pending_path_confirm_files", [])
            reply = parse_confirm_reply(user_input)
            log(f"Path confirm reply parsed: {reply}, files={files}")
            if reply == "no":
                _sess.pending_path_confirm_files = []
                import subprocess as _sp
                for f_rel in files:
                    try:
                        _sp.run(
                            ["git", "checkout", "--", f_rel],
                            cwd=str(project_path), capture_output=True,
                        )
                    except Exception:
                        pass
                log(f"Reverted {len(files)} path(s) after user denial")
                return {
                    "status": "blocked",
                    "output": f"已還原修改的敏感路徑：{', '.join(files)}",
                    "round": round_num,
                }
            # yes: approve paths in approved_paths and continue
            _sess.pending_path_confirm_files = []
            if approved_paths is not None:
                for f_rel in files:
                    approved_paths.approve(project_path / f_rel)
            log(f"Approved {len(files)} confirm-required path(s)")
            return {
                "status": "continue",
                "output": f"已確認，繼續執行。批准路徑：{', '.join(files)}",
                "round": round_num,
            }

        # ── External change reply handling ─────────────────────────────────
        if _sess is not None and getattr(_sess, "pending_clarification", False) and getattr(_sess, "pending_external_files", []):
            _sess.pending_clarification = False
            ext_files = list(_sess.pending_external_files)
            _sess.pending_external_files = []
            stripped = user_input.strip()
            INTEGRATE_WORDS = {"整合", "integrate", "合併", "納入"}
            REVERT_WORDS = {"還原", "revert", "還原修改", "回復"}
            IGNORE_WORDS = {"略過", "ignore", "跳過", "繼續"}
            if any(w in stripped for w in REVERT_WORDS):
                from .loop_helpers import revert_external
                revert_external(ext_files, project_path, log)
                log(f"User chose revert for external files: {ext_files}")
                return {"status": "continue", "output": "已還原外部修改，繼續執行。", "round": round_num}
            elif any(w in stripped for w in IGNORE_WORDS):
                log(f"User chose ignore for external files: {ext_files}")
                return {"status": "continue", "output": "已略過外部修改，繼續執行。", "round": round_num}
            else:
                # Default: integrate
                from .loop_helpers import integrate_external_changes
                from .security import SessionGuard
                agent_dir_tmp = project_path / ".agent"
                integrate_external_changes(ext_files, project_path, agent_dir_tmp, engine, log)
                log(f"User chose integrate for external files: {ext_files}")
                return {"status": "continue", "output": "已整合外部修改，繼續執行。", "round": round_num}

        # ── Clarification reply handling ───────────────────────────────────
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
            on_token_warning=on_token_warning,
            on_token_kill=on_token_kill,
            approved_paths=approved_paths,
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
