"""orchestrator_loop.py - Main loop: one round of think → do → audit → judge."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import agent as _agent
from . import audit_runner as _audit
from . import prompts as _prompts
from . import timeline as _timeline
from .loop_helpers import (
    ask_integrate_external,
    compress_if_needed,
    detect_external_changes,
    extract_lessons,
    format_audit,
    handle_judge,
    integrate_external_changes,
    is_plan_complete,
    parse_current_task,
    read_file,
    revert_external,
    run_finale,
    save_summary,
    update_upper_files,
)
from .security import safe_write, verify_manifest

# ── Public entry point ────────────────────────────────────────────────────────


def run(
    user_message: str,
    project_path: Path,
    engine: str,
    round_num: int,
    on_log: Callable[[str], None] | None = None,
    on_token_warning: Callable[[], None] | None = None,
    on_token_kill: Callable[[], None] | None = None,
    review_mode: bool = False,
) -> dict:
    """Execute one round of the main loop.

    Returns:
        {
            "status": "continue" | "done" | "blocked" | "needs_review",
            "round": int,
            "tokens": int,
            "summary": str,
        }
    """

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    agent_dir = project_path / ".agent"
    timeline_path = agent_dir / "timeline.md"

    # ── Step 1: compress check ────────────────────────────────────────────
    compress_if_needed(agent_dir, engine, log)

    # ── Step 1.5: external change detection ───────────────────────────────
    external = detect_external_changes(project_path)
    if external:
        log(f"⚠️ 偵測到外部修改: {', '.join(external)}")
        choice = ask_integrate_external(external, log)
        if choice == "integrate":
            integrate_external_changes(external, project_path, agent_dir, engine, log)
        else:
            revert_external(external, project_path, log)

    # ── Step 1.6: manifest integrity check ────────────────────────────────
    anomalies = verify_manifest(agent_dir)
    for a in anomalies:
        log(f"🔒 Manifest: {a}")

    # ── Step 2: decide flow ───────────────────────────────────────────────
    plan = read_file(agent_dir / "plan.md")
    prev_summary_path = (
        agent_dir / "lower" / "summaries" / f"round_{round_num - 1:03d}.md"
    )
    audit_results_prev = read_file(prev_summary_path)
    use_lightweight = _should_use_lightweight(plan, audit_results_prev)

    # ── Step 3: think() (normal mode only) ───────────────────────────────
    tokens_used = 0

    if not use_lightweight:
        log(f"🧠 第 {round_num} 輪 think()...")
        context_files = _get_upper_context_files(agent_dir)
        think_result = _agent.think(
            _prompts.task_prompt(
                read_file(agent_dir / "current_task.md"),
                read_file(agent_dir / "skill.md"),
                read_file(agent_dir / "lower" / "progress.md"),
            ),
            context_files=context_files,
            engine=engine,
            cwd=agent_dir,
            model="sonnet",
            on_token_warning=on_token_warning,
            on_token_kill=on_token_kill,
        )

        current_task = parse_current_task(think_result, user_message)
        safe_write(agent_dir / "current_task.md", current_task)
        update_upper_files(agent_dir, think_result)

        if review_mode:
            log("👀 審核模式：請確認當前任務")
            return {
                "status": "needs_review",
                "round": round_num,
                "tokens": tokens_used,
                "summary": current_task,
            }

    else:
        log(f"⚡ 第 {round_num} 輪 lightweight（跳過 think）")

    # ── Step 5: do() ──────────────────────────────────────────────────────
    log(f"🔨 第 {round_num} 輪 do()...")
    do_context = [
        agent_dir / "current_task.md",
        agent_dir / "skill.md",
        agent_dir / "lower" / "progress.md",
    ]
    do_context = [f for f in do_context if f.exists()]

    do_result = _agent.do(
        read_file(agent_dir / "current_task.md"),
        context_files=do_context,
        engine=engine,
        cwd=project_path,
        model="opus",
        on_token_warning=on_token_warning,
        on_token_kill=on_token_kill,
    )

    summary_path = (
        agent_dir / "lower" / "summaries" / f"round_{round_num:03d}.md"
    )
    save_summary(summary_path, round_num, do_result)

    # ── Step 6: audit ──────────────────────────────────────────────────────
    log("🔎 執行 audit...")
    audit_results = _audit.run_audit(project_path)
    audit_summary = format_audit(audit_results)

    # ── Step 7: security scan ─────────────────────────────────────────────
    log("🔒 執行安全掃描...")
    sec_results = _audit.run_security_scan(project_path)
    sec_summary = format_audit(sec_results)

    full_summary = f"## Audit\n{audit_summary}\n\n## Security\n{sec_summary}"

    # ── Step 8: judge think() ─────────────────────────────────────────────
    log("⚖️ 評審結果...")
    judge_result = _agent.think(
        _prompts.judge_prompt(
            full_summary,
            read_file(agent_dir / "plan.md"),
            read_file(agent_dir / "purpose.md"),
        ),
        context_files=[summary_path] if summary_path.exists() else [],
        engine=engine,
        cwd=agent_dir,
        model="sonnet",
    )

    status, decision_desc = handle_judge(
        judge_result, audit_results, agent_dir, round_num, log
    )

    # ── Step 9: update timeline ───────────────────────────────────────────
    result_emoji = (
        "✅" if status == "continue" else ("✋" if status == "done" else "❌")
    )
    _timeline.append_round(
        timeline_path,
        round_num=round_num,
        round_type="do+audit",
        task=read_file(agent_dir / "current_task.md")[:60],
        result=result_emoji,
        decision=decision_desc[:60],
        tokens=tokens_used,
    )

    # ── Step 9.5: anomaly detection ───────────────────────────────────────
    for anomaly in _timeline.detect_anomalies(timeline_path):
        log(anomaly)

    # ── Step 10: reality check (every 5 rounds) ───────────────────────────
    if round_num % 5 == 0:
        log("🔭 幻覺自查...")
        rc = _agent.think(
            _prompts.reality_check_prompt(
                read_file(agent_dir / "recon.md"),
                read_file(agent_dir / "upper" / "context.md"),
            ),
            context_files=[],
            engine=engine,
            cwd=agent_dir,
            model="sonnet",
        )
        safe_write(agent_dir / "upper" / "reality_check.md", rc)

    # ── Step 11: lessons extraction (every 10 rounds) ────────────────────
    if round_num % 10 == 0:
        log("📚 提取 lessons...")
        extract_lessons(agent_dir, engine)

    # ── Step 12: check plan completion ───────────────────────────────────
    if status == "done" or is_plan_complete(agent_dir):
        log("🎉 計劃完成，進入收尾")
        run_finale(agent_dir, project_path, engine, log)
        return {
            "status": "done",
            "round": round_num,
            "tokens": tokens_used,
            "summary": judge_result[:200],
        }

    return {
        "status": status,
        "round": round_num,
        "tokens": tokens_used,
        "summary": judge_result[:200],
    }


# ── Simple inline helpers ─────────────────────────────────────────────────────


def _should_use_lightweight(plan: str, prev_summary: str) -> bool:
    """Decide if this round can skip think() (lightweight mode)."""
    if not plan or not prev_summary:
        return False
    return (
        "🔴 FAIL" not in prev_summary
        and "🟡 WARN" not in prev_summary
        and bool(plan)
    )


def _get_upper_context_files(agent_dir: Path) -> list[Path]:
    """Return existing upper/ context files."""
    files = []
    for name in ["context.md", "progress.md", "lessons.md"]:
        p = agent_dir / "upper" / name
        if p.exists():
            files.append(p)
    return files
