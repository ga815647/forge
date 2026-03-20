"""orchestrator_init.py - First-time project initialization flow."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Callable

from . import agent as _agent
from .live_log import make_live_logger, summarize_paths, summarize_text
from . import prompts as _prompts
from .init_chunker import chunk_file
from .security import build_manifest, is_safe_path, safe_write, verify_manifest

# ── Public entry point ────────────────────────────────────────────────────────


def run(
    user_input: str,
    uploaded_files: list[Path],
    project_path: Path,
    engine: str,
    on_log: Callable[[str], None] | None = None,
    review_mode: bool = False,
) -> dict:
    """Run first-time initialization. Returns {"plan": str, "agent_dir": Path}."""

    log = make_live_logger(on_log, "init")
    agent_dir = project_path / ".agent"
    log(
        "Starting initialization: "
        f"project={project_path}, engine={engine}, review_mode={review_mode}, "
        f"uploads={len(uploaded_files)}"
    )
    if uploaded_files:
        log(f"Incoming uploads: {summarize_paths(uploaded_files)}")

    # ── Step 1: detect engine ──────────────────────────────────────────────
    log(f"🔍 使用 engine: {engine}")

    # ── Step 2: check for existing .agent/ ────────────────────────────────
    if agent_dir.exists() and (agent_dir / "purpose.md").exists():
        log("📂 發現現有 .agent/ 目錄")
        _prompt_existing_agent(agent_dir, log)

    # Clean up stale .tmp files
    removed_tmp = 0
    if agent_dir.exists():
        for tmp in agent_dir.rglob("*.tmp"):
            try:
                tmp.unlink()
                removed_tmp += 1
            except OSError:
                pass
    log(f"Temporary cleanup finished; removed_tmp_files={removed_tmp}")

    # Create directory structure
    created_dirs = ["upper", "lower", "lower/summaries", "chunks", "uploads"]
    for subdir in created_dirs:
        (agent_dir / subdir).mkdir(parents=True, exist_ok=True)
    log(f"Ensured agent directories: {', '.join(created_dirs)}")

    # ── Step 3: handle .zip uploads ───────────────────────────────────────
    for f in uploaded_files:
        if f.suffix.lower() == ".zip" and is_safe_path(f, f.parent):
            log(f"📦 解壓縮: {f.name}")
            _extract_zip(f, project_path)
            log(f"Archive extracted into project root: {project_path}")

    # ── Step 4: copy uploaded files + chunk large ones ────────────────────
    log("📎 處理上傳檔案...")
    chunk_titles: list[str] = []
    for f in uploaded_files:
        if f.suffix.lower() == ".zip":
            continue
        dest = agent_dir / "uploads" / f.name
        log(f"Copying upload into agent workspace: src={f} -> dest={dest}")
        _copy_normalized(f, dest)
        dest_text = dest.read_text(encoding="utf-8", errors="replace")
        lines = dest_text.splitlines()
        log(
            "Upload normalized: "
            f"name={f.name}, lines={len(lines)}, chars={len(dest_text)}"
        )
        if len(lines) > 300:
            log(f"✂️ 切割大檔案: {f.name} ({len(lines)} 行)")
            titles = chunk_file(dest, agent_dir / "chunks", f.name)
            chunk_titles.extend(titles)
            log(
                "Chunking complete: "
                f"file={f.name}, chunks={len(titles)}, sample={summarize_text(' | '.join(titles), 160)}"
            )
        else:
            chunk_titles.append(f"[{f.name}] ({len(lines)} 行)")
            log(f"Upload kept as single chunk reference: {f.name}")
    log(f"Upload processing finished; chunk_entries={len(chunk_titles)}")

    # ── Step 5: recon ──────────────────────────────────────────────────────
    log("🔍 偵察專案結構...")
    recon_result = _agent.do(
        _prompts.recon_prompt(project_path),
        context_files=[],
        engine=engine,
        cwd=project_path,
        model="sonnet",
        on_log=log,
    )
    recon_path = agent_dir / "recon.md"
    safe_write(recon_path, recon_result)
    build_manifest(agent_dir)
    log(
        "Recon written: "
        f"path={recon_path}, chars={len(recon_result)}, preview={summarize_text(recon_result, 140)}"
    )
    log("Manifest rebuilt after recon output")

    # ── Step 6: pre-flight think() ────────────────────────────────────────
    log(
        "🧠 Pre-flight 分析..."
        f" chunk_context={len(chunk_titles)}, recon_chars={len(recon_result)}"
    )
    preflight_result = _agent.think(
        _prompts.preflight_prompt(recon_result, user_input, chunk_titles),
        context_files=[],
        engine=engine,
        cwd=agent_dir,
        model="sonnet",
    )
    safe_write(agent_dir / "preflight.md", preflight_result)
    log(
        "Pre-flight response captured: "
        f"chars={len(preflight_result)}, preview={summarize_text(preflight_result, 160)}"
    )

    # Parse and write individual files from preflight output
    written_files = _extract_and_write_files(preflight_result, agent_dir, engine)
    log(
        "Pre-flight files materialized: "
        f"count={len(written_files)}, files={', '.join(written_files) if written_files else '(none)'}"
    )
    log("✅ Pre-flight 完成")

    # ── Step 6b: chunk ordering (if big attachments) ───────────────────────
    if chunk_titles:
        log("📋 排序 chunks 執行順序...")
        chunks_dir = agent_dir / "chunks"
        chunk_files = list(chunks_dir.glob("*.md"))
        if chunk_files:
            log(f"Chunk ordering input files: {summarize_paths(chunk_files, base=agent_dir)}")
            ordering_prompt = (
                f"根據以下 chunks 列表，決定最合理的讀取順序：\n"
                + "\n".join(f"- {c.name}" for c in chunk_files)
                + "\n\n輸出有序列表（chunk 名稱），不要解釋。"
            )
            ordering_result = _agent.think(
                ordering_prompt, [], engine, agent_dir, model="sonnet"
            )
            log(f"Chunk ordering suggestion: {summarize_text(ordering_result, 160)}")

    # ── Step 7: large task estimation ─────────────────────────────────────
    plan_path = agent_dir / "plan.md"
    plan_content = ""
    if plan_path.exists():
        plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
        step_count = sum(
            1
            for line in plan_content.splitlines()
            if line.strip().startswith(("- ", "* ", "1.", "2.", "3."))
        )
        log(
            f"Plan detected: path={plan_path}, chars={len(plan_content)}, "
            f"estimated_steps={step_count}"
        )
        if _is_large_task(plan_content):
            log("📅 大型任務，沙盤推演中...")
    else:
        log("No plan.md was produced during initialization")

    # ── Step 8: review mode gate ──────────────────────────────────────────
    if review_mode and plan_content:
        log("👀 審核模式：等待使用者確認 plan...")
        return {
            "plan": plan_content,
            "agent_dir": agent_dir,
            "needs_review": True,
        }

    # ── Step 9: init upper/context.md ─────────────────────────────────────
    _init_context(agent_dir, engine, recon_result)
    context_path = agent_dir / "upper" / "context.md"
    log(
        "Context initialized: "
        f"path={context_path}, chars={len(context_path.read_text(encoding='utf-8', errors='replace'))}"
    )
    log("✅ 初始化完成，進入主迴圈")

    return {
        "plan": plan_content,
        "agent_dir": agent_dir,
        "needs_review": False,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _prompt_existing_agent(agent_dir: Path, on_log: Callable | None) -> None:
    """Warn user about existing .agent/ session."""
    if on_log:
        on_log(
            f"⚠️ 子目錄 {agent_dir.parent.name}/ 有之前的 Forge 記憶。\n"
            "要在這裡重新任務，還是繼續上次？"
        )


def _extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract zip to destination directory."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest_dir)
    except (zipfile.BadZipFile, OSError):
        pass


def _copy_normalized(src: Path, dest: Path) -> None:
    """Copy file with normalized line endings and encoding."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        content = src.read_text(encoding="utf-8", errors="replace").replace(
            "\r\n", "\n"
        )
        safe_write(dest, content)
    except OSError:
        shutil.copy2(str(src), str(dest))


def _extract_and_write_files(preflight_output: str, agent_dir: Path, engine: str) -> list[str]:
    """Parse preflight output and write purpose.md, architecture.md, skill.md, meta.md, plan.md."""
    sections = {
        "purpose.md": ["purpose.md", "目的", "purpose"],
        "architecture.md": ["architecture.md", "架構", "architecture"],
        "skill.md": ["skill.md", "技能", "踩坑", "skill"],
        "meta.md": ["meta.md", "品質", "meta"],
        "plan.md": ["plan.md", "計劃", "plan"],
    }

    # Try to parse structured output with ``` blocks or ## headers
    lines = preflight_output.split("\n")
    current_file: str | None = None
    current_content: list[str] = []
    file_contents: dict[str, str] = {}

    for line in lines:
        # Detect section headers like "## purpose.md" or "### plan.md"
        stripped = line.strip()
        matched_file = None
        for fname, keywords in sections.items():
            if any(kw.lower() in stripped.lower() for kw in keywords):
                if stripped.startswith("#"):
                    matched_file = fname
                    break

        if matched_file:
            if current_file and current_content:
                file_contents[current_file] = "\n".join(current_content).strip()
            current_file = matched_file
            current_content = []
        else:
            if current_file:
                current_content.append(line)

    if current_file and current_content:
        file_contents[current_file] = "\n".join(current_content).strip()

    # Write extracted files
    written: list[str] = []
    for fname, content in file_contents.items():
        if content:
            path = agent_dir / fname
            _agent.write_agent_file(path, content, engine, project_root=agent_dir.parent)
            written.append(fname)

    # If parsing failed, write the whole output as preflight.md (already done by caller)
    return written


def _init_context(agent_dir: Path, engine: str, recon: str) -> None:
    """Initialize upper/context.md with compressed global knowledge."""
    purpose_path = agent_dir / "purpose.md"
    plan_path = agent_dir / "plan.md"

    parts = [f"# 初始上下文\n\n## 偵察摘要\n{recon[:500]}"]
    if purpose_path.exists():
        parts.append(
            f"## 目標\n{purpose_path.read_text(encoding='utf-8', errors='replace')[:300]}"
        )
    if plan_path.exists():
        parts.append(
            f"## 計劃概覽\n{plan_path.read_text(encoding='utf-8', errors='replace')[:300]}"
        )

    context = "\n\n".join(parts)
    safe_write(agent_dir / "upper" / "context.md", context)


def _is_large_task(plan_content: str) -> bool:
    """Heuristic: plan with 10+ steps is considered large."""
    step_count = sum(
        1
        for line in plan_content.splitlines()
        if line.strip().startswith(("- ", "* ", "1.", "2.", "3."))
    )
    return step_count >= 10
