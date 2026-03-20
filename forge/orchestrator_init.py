"""orchestrator_init.py - First-time project initialization flow."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Callable

from . import agent as _agent
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

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    agent_dir = project_path / ".agent"

    # ── Step 1: detect engine ──────────────────────────────────────────────
    log(f"🔍 使用 engine: {engine}")

    # ── Step 2: check for existing .agent/ ────────────────────────────────
    if agent_dir.exists() and (agent_dir / "purpose.md").exists():
        log("📂 發現現有 .agent/ 目錄")
        _prompt_existing_agent(agent_dir, on_log)

    # Clean up stale .tmp files
    if agent_dir.exists():
        for tmp in agent_dir.rglob("*.tmp"):
            try:
                tmp.unlink()
            except OSError:
                pass

    # Create directory structure
    for subdir in ["upper", "lower", "lower/summaries", "chunks", "uploads"]:
        (agent_dir / subdir).mkdir(parents=True, exist_ok=True)

    # ── Step 3: handle .zip uploads ───────────────────────────────────────
    for f in uploaded_files:
        if f.suffix.lower() == ".zip" and is_safe_path(f, f.parent):
            log(f"📦 解壓縮: {f.name}")
            _extract_zip(f, project_path)

    # ── Step 4: copy uploaded files + chunk large ones ────────────────────
    log("📎 處理上傳檔案...")
    chunk_titles: list[str] = []
    for f in uploaded_files:
        if f.suffix.lower() == ".zip":
            continue
        dest = agent_dir / "uploads" / f.name
        _copy_normalized(f, dest)
        lines = dest.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > 300:
            log(f"✂️ 切割大檔案: {f.name} ({len(lines)} 行)")
            titles = chunk_file(dest, agent_dir / "chunks", f.name)
            chunk_titles.extend(titles)
        else:
            chunk_titles.append(f"[{f.name}] ({len(lines)} 行)")

    # ── Step 5: recon ──────────────────────────────────────────────────────
    log("🔍 偵察專案結構...")
    recon_result = _agent.do(
        _prompts.recon_prompt(project_path),
        context_files=[],
        engine=engine,
        cwd=project_path,
        model="sonnet",
    )
    recon_path = agent_dir / "recon.md"
    safe_write(recon_path, recon_result)
    build_manifest(agent_dir)
    log("✅ recon.md 完成")

    # ── Step 6: pre-flight think() ────────────────────────────────────────
    log("🧠 Pre-flight 分析...")
    preflight_result = _agent.think(
        _prompts.preflight_prompt(recon_result, user_input, chunk_titles),
        context_files=[],
        engine=engine,
        cwd=agent_dir,
        model="sonnet",
    )
    safe_write(agent_dir / "preflight.md", preflight_result)

    # Parse and write individual files from preflight output
    _extract_and_write_files(preflight_result, agent_dir, engine)
    log("✅ Pre-flight 完成")

    # ── Step 6b: chunk ordering (if big attachments) ───────────────────────
    if chunk_titles:
        log("📋 排序 chunks 執行順序...")
        chunks_dir = agent_dir / "chunks"
        chunk_files = list(chunks_dir.glob("*.md"))
        if chunk_files:
            ordering_prompt = (
                f"根據以下 chunks 列表，決定最合理的讀取順序：\n"
                + "\n".join(f"- {c.name}" for c in chunk_files)
                + "\n\n輸出有序列表（chunk 名稱），不要解釋。"
            )
            _agent.think(
                ordering_prompt, [], engine, agent_dir, model="sonnet"
            )

    # ── Step 7: large task estimation ─────────────────────────────────────
    plan_path = agent_dir / "plan.md"
    plan_content = ""
    if plan_path.exists():
        plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
        if _is_large_task(plan_content):
            log("📅 大型任務，沙盤推演中...")

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


def _extract_and_write_files(preflight_output: str, agent_dir: Path, engine: str) -> None:
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
    for fname, content in file_contents.items():
        if content:
            path = agent_dir / fname
            _agent.write_agent_file(path, content, engine, project_root=agent_dir.parent)

    # If parsing failed, write the whole output as preflight.md (already done by caller)


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
