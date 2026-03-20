"""orchestrator_loop.py - Main loop: one round of think → do → audit → judge."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import agent as _agent
from . import audit_runner as _audit
from .live_log import make_live_logger, summarize_paths, summarize_text
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
from .security import backup_before_do, is_safe_path, restore_from_backup, safe_write, verify_manifest

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

    log = make_live_logger(on_log, f"loop r{round_num:03d}")
    agent_dir = project_path / ".agent"
    timeline_path = agent_dir / "timeline.md"
    log(
        "Starting loop round: "
        f"project={project_path}, engine={engine}, review_mode={review_mode}"
    )
    log(f"User message preview: {summarize_text(user_message, 180)}")

    # ── Step 1: compress check ────────────────────────────────────────────
    log("Checking whether upper/lower memory files need compression")
    compress_if_needed(agent_dir, engine, log)
    log("Compression check finished")

    # ── Step 1.5: external change detection ───────────────────────────────
    external = detect_external_changes(project_path)
    if external:
        log(f"⚠️ 偵測到外部修改: {', '.join(external)}")
        choice = ask_integrate_external(external, log)
        log(f"External change policy selected: {choice}")
        if choice == "integrate":
            integrate_external_changes(external, project_path, agent_dir, engine, log)
        else:
            revert_external(external, project_path, log)
    else:
        log("No external modifications detected outside .agent/")

    # ── Step 1.6: manifest integrity check ────────────────────────────────
    anomalies = verify_manifest(agent_dir)
    if anomalies:
        log(f"Manifest verification found {len(anomalies)} issue(s)")
    for a in anomalies:
        log(f"🔒 Manifest: {a}")
    if not anomalies:
        log("Manifest verification passed with no anomalies")

    # ── Step 2: decide flow ───────────────────────────────────────────────
    plan = read_file(agent_dir / "plan.md")
    prev_summary_path = (
        agent_dir / "lower" / "summaries" / f"round_{round_num - 1:03d}.md"
    )
    audit_results_prev = read_file(prev_summary_path)
    use_lightweight = _should_use_lightweight(plan, audit_results_prev)
    log(
        "Round planning state: "
        f"plan_chars={len(plan)}, prev_summary_exists={prev_summary_path.exists()}, "
        f"lightweight={use_lightweight}"
    )

    # ── Step 3: think() (normal mode only) ───────────────────────────────
    tokens_used = 0

    if not use_lightweight:
        log(f"🧠 第 {round_num} 輪 think()...")
        context_files = _get_upper_context_files(agent_dir)
        log(f"think() context files: {summarize_paths(context_files, base=agent_dir)}")
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
        log(
            "think() completed: "
            f"chars={len(think_result)}, current_task={summarize_text(current_task, 160)}"
        )

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
        log(
            "Reusing previous planning artifacts because plan exists and previous summary had no FAIL/WARN markers"
        )

    # ── Step 5: do() ──────────────────────────────────────────────────────
    log(f"🔨 第 {round_num} 輪 do()...")
    do_context = [
        agent_dir / "current_task.md",
        agent_dir / "skill.md",
        agent_dir / "lower" / "progress.md",
    ]
    do_context = [f for f in do_context if f.exists()]
    log(f"do() context files: {summarize_paths(do_context, base=project_path)}")

    # 備份 do() 前已修改的檔案，供範圍外寫入時還原
    pre_changed = _get_changed_files(project_path)
    log(
        f"Pre-do changed files snapshot: count={len(pre_changed)}, "
        f"files={summarize_paths(pre_changed, base=project_path)}"
    )
    backup_mapping = backup_before_do(pre_changed, project_path)
    log(f"Backup snapshot prepared for {len(backup_mapping)} file(s)")

    do_result = _agent.do(
        read_file(agent_dir / "current_task.md"),
        context_files=do_context,
        engine=engine,
        cwd=project_path,
        model="opus",
        on_token_warning=on_token_warning,
        on_token_kill=on_token_kill,
        on_log=log,
    )
    log(
        "do() completed: "
        f"chars={len(do_result)}, preview={summarize_text(do_result, 160)}"
    )

    # 驗證 do() 沒有寫出專案範圍外
    changed = _get_changed_files(project_path)
    log(
        f"Post-do changed files snapshot: count={len(changed)}, "
        f"files={summarize_paths(changed, base=project_path)}"
    )
    outside = [f for f in changed if not is_safe_path(f, project_path)]
    if outside:
        log(f"Out-of-scope writes detected; restoring backups for {len(outside)} file(s)")
        restore_from_backup(backup_mapping)
        raise ValueError(f"do() 寫出了專案目錄範圍外：{outside}")
    log("do() write scope verification passed")

    summary_path = (
        agent_dir / "lower" / "summaries" / f"round_{round_num:03d}.md"
    )
    save_summary(summary_path, round_num, do_result)
    log(f"Round summary saved: {summary_path}")

    # ── Step 6: audit ──────────────────────────────────────────────────────
    log("🔎 執行 audit...")
    audit_results = _audit.run_audit(project_path, on_log=log)
    audit_summary = format_audit(audit_results)
    log(f"Audit summary: {_summarize_audit_results(audit_results)}")

    # ── Step 7: security scan ─────────────────────────────────────────────
    log("🔒 執行安全掃描...")
    sec_results = _audit.run_security_scan(project_path, on_log=log)
    sec_summary = format_audit(sec_results)
    log(f"Security summary: {_summarize_audit_results(sec_results)}")

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
    log(f"Judge output preview: {summarize_text(judge_result, 180)}")

    status, decision_desc = handle_judge(
        judge_result, audit_results, agent_dir, round_num, log
    )
    log(f"Judge decision resolved: status={status}, decision={decision_desc}")

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
    log(f"Timeline updated: {timeline_path}")

    # ── Step 9.5: anomaly detection ───────────────────────────────────────
    timeline_anomalies = _timeline.detect_anomalies(timeline_path)
    if timeline_anomalies:
        log(f"Timeline anomaly scan found {len(timeline_anomalies)} issue(s)")
    for anomaly in timeline_anomalies:
        log(anomaly)
    if not timeline_anomalies:
        log("Timeline anomaly scan found no issues")

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
        log(
            "Reality check written: "
            f"chars={len(rc)}, preview={summarize_text(rc, 140)}"
        )

    # ── Step 11: lessons extraction (every 10 rounds) ────────────────────
    if round_num % 10 == 0:
        log("📚 提取 lessons...")
        extract_lessons(agent_dir, engine)
        log("Lessons extraction completed")

    # ── Step 12: check plan completion ───────────────────────────────────
    if status == "done" or is_plan_complete(agent_dir):
        log("Plan completion detected; entering finale sequence")
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


def _get_changed_files(project_path: Path) -> list[Path]:
    """Return list of files changed since last git commit."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(project_path), capture_output=True, text=True
        )
        return [project_path / f for f in result.stdout.splitlines() if f]
    except Exception:
        return []


def _summarize_audit_results(results: list[dict]) -> str:
    """Render compact status counts for audit/security logs."""
    if not results:
        return "no results"

    counts: dict[str, int] = {}
    names: list[str] = []
    for result in results:
        level = str(result.get("level", "INFO"))
        counts[level] = counts.get(level, 0) + 1
        names.append(f"{result.get('name', '?')}={level}")

    counts_text = ", ".join(f"{level}:{count}" for level, count in sorted(counts.items()))
    return f"{counts_text} | {'; '.join(names[:5])}"
