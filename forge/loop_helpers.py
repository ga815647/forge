"""loop_helpers.py - Helper functions for the orchestrator main loop."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from . import agent as _agent
from . import prompts as _prompts
from .security import safe_write


def read_file(path: Path) -> str:
    """Read file safely, return empty string on error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    except OSError:
        return ""


def compress_if_needed(agent_dir: Path, engine: str, log: Callable) -> None:
    for subdir in ["upper", "lower"]:
        target = agent_dir / subdir
        if not target.exists():
            continue
        for md in target.glob("*.md"):
            if md.read_text(encoding="utf-8", errors="replace").count("\n") > 100:
                log(f"Compressing {md.relative_to(agent_dir)}")
                _agent.compress(md, engine)


def detect_external_changes(project_path: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(project_path), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return []
        return [
            f.strip() for f in result.stdout.splitlines()
            if f.strip() and not f.strip().startswith(".agent/")
        ]
    except (OSError, FileNotFoundError):
        return []


def ask_integrate_external(files: list[str], log: Callable) -> str:
    log(
        f"External changes detected: {', '.join(files)}\n"
        "(possibly from another editor)\n"
        "Integrate into Forge's context?"
    )
    return "integrate"  # Default; UI layer should override


def integrate_external_changes(
    files: list[str], project_path: Path, agent_dir: Path, engine: str, log: Callable
) -> None:
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--"] + files,
            cwd=str(project_path), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        diff = result.stdout[:3000]
    except (OSError, FileNotFoundError):
        diff = "\n".join(files)

    recon_result = _agent.do(
        f"Report external changes (only facts, no speculation):\n\n{diff}",
        context_files=[], engine=engine, cwd=project_path, model="sonnet",
    )
    context_path = agent_dir / "upper" / "context.md"
    safe_write(context_path, read_file(context_path) + f"\n\n## External changes\n{recon_result[:500]}")
    log("External changes integrated")


def revert_external(files: list[str], project_path: Path, log: Callable) -> None:
    try:
        subprocess.run(["git", "checkout", "--"] + files,
                       cwd=str(project_path), capture_output=True)
        log(f"Reverted external changes: {', '.join(files)}")
    except (OSError, FileNotFoundError):
        log(f"Cannot revert (git unavailable): {', '.join(files)}")


def parse_current_task(think_output: str, user_message: str) -> str:
    lines = think_output.splitlines()
    in_task = False
    task_lines: list[str] = []
    for line in lines:
        if "current_task" in line.lower() or "當前任務" in line or "這輪" in line:
            in_task = True
        if in_task:
            task_lines.append(line)
        if in_task and len(task_lines) > 30:
            break
    if task_lines:
        return "\n".join(task_lines).strip()
    return f"## 使用者要求\n{user_message}\n\n## 分析\n{chr(10).join(lines[:15]).strip()}"


def update_upper_files(agent_dir: Path, think_output: str) -> None:
    progress_path = agent_dir / "upper" / "progress.md"
    lines = think_output.splitlines()
    decisions = [l for l in lines if "決定" in l or "決策" in l or "→" in l][:5]
    if decisions:
        safe_write(progress_path, read_file(progress_path) + "\n\n---\n" + "\n".join(decisions))


def save_summary(summary_path: Path, round_num: int, do_result: str) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    safe_write(summary_path, (
        f"# Round {round_num:03d} Summary\n\n"
        f"## do() output\n{do_result[:2000]}\n\n"
        f"## Status\nPending audit\n"
    ))


def format_audit(results: list[dict]) -> str:
    if not results:
        return "No results"
    lines: list[str] = []
    for r in results:
        lines.append(f"**{r.get('name', '?')}** {r.get('level', 'INFO')}\n```\n{r.get('output', '')[:300]}\n```")
    return "\n\n".join(lines)


def handle_judge(
    judge_result: str, audit_results: list[dict], agent_dir: Path,
    round_num: int, log: Callable,
) -> tuple[str, str]:
    lower = agent_dir / "lower"
    lower.mkdir(exist_ok=True)

    has_fail = any("FAIL" in r.get("level", "") for r in audit_results)
    conflict = "與 purpose 衝突" in judge_result or "衝突" in judge_result

    if conflict:
        log("Conflicts with purpose — reporting to user")
        return "blocked", "與purpose衝突"

    if "做不到" in judge_result:
        log("Cannot do — recording lessons")
        append_lessons(lower / "lessons.md", f"Round {round_num}: " + judge_result[:200])
        return "blocked", "做不到"

    if "你說的 X 不存在" in judge_result or ("不存在" in judge_result and "X" in judge_result):
        log("Correcting hallucination in context.md")
        return "continue", "修正幻覺"

    if has_fail:
        log("FAIL — recording lessons")
        append_lessons(lower / "lessons.md", f"Round {round_num}: FAIL\n{judge_result[:200]}")

    progress_path = lower / "progress.md"
    safe_write(progress_path, read_file(progress_path) + f"\n\n## Round {round_num:03d}\n{judge_result[:300]}")

    if "建議實測" in judge_result:
        return "continue", "建議實測"
    return "continue", "繼續"


def append_lessons(lessons_path: Path, entry: str) -> None:
    safe_write(lessons_path, read_file(lessons_path) + f"\n\n---\n{entry}")


def is_plan_complete(agent_dir: Path) -> bool:
    plan_path = agent_dir / "plan.md"
    if not plan_path.exists():
        return False
    content = plan_path.read_text(encoding="utf-8", errors="replace")
    return content.count("- [ ]") == 0 and "- [x]" in content


def extract_lessons(agent_dir: Path, engine: str) -> None:
    lessons_path = agent_dir / "lower" / "lessons.md"
    skill_path = agent_dir / "skill.md"
    if not lessons_path.exists():
        return
    prompt = (
        f"lessons.md 裡有沒有值得放進 skill.md 的？\n\n"
        f"## lessons.md\n{read_file(lessons_path)[:2000]}\n\n"
        f"## 現有 skill.md\n{read_file(skill_path)[:1000]}\n\n"
        "輸出更新後的 skill.md（如果沒有新的值得加，輸出「無更新」）。"
    )
    result = _agent.think(prompt, [], engine, agent_dir, model="sonnet")
    if "無更新" not in result and result.strip():
        _agent.write_agent_file(skill_path, result, engine, skip_review=False, project_root=agent_dir.parent)


def run_finale(agent_dir: Path, project_path: Path, engine: str, log: Callable) -> None:
    log("Generating final_check.md...")
    final_result = _agent.do(
        (
            "Generate a set of project-specific check commands based on purpose.md and meta.md.\n\n"
            f"## purpose.md\n{read_file(agent_dir / 'purpose.md')}\n\n"
            f"## meta.md\n{read_file(agent_dir / 'meta.md')}\n\n"
            "Run all checks. Write results into final_check.md."
        ),
        context_files=[], engine=engine, cwd=project_path, model="sonnet",
    )
    safe_write(agent_dir / "final_check.md", final_result)

    log("Generating docs...")
    doc_result = _agent.think(
        _prompts.doc_prompt(
            read_file(agent_dir / "purpose.md"),
            read_file(agent_dir / "architecture.md"),
            read_file(agent_dir / "timeline.md")[-2000:],
        ),
        context_files=[], engine=engine, cwd=agent_dir, model="sonnet",
    )
    _agent.do(
        f"Write appropriate docs to the project directory:\n\n{doc_result}",
        context_files=[], engine=engine, cwd=project_path, model="sonnet",
    )
    log("Finale complete")
