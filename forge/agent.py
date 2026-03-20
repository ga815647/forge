"""agent.py - think() / do() / compress() / write_agent_file() wrappers."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from . import monitor as _monitor
from .agent_review import auto_review, quick_review
from .main_config import find_engine_path
from .security import is_safe_path, safe_write, scan_code, update_manifest

# ── Anti-hallucination / command-confirm injections ───────────────────────────

_ANTI_HALLUCINATION = """

重要警告：
- 如果你不確定某件事，說「我不確定」，不要編造答案
- 如果你沒在文件裡看到某些你被求的東西，不要假設它存在
- 如果使用者的描述有多種解讀，列出所有可能性，不要選一個當預設
- 引用具體文件路徑和行號來支持你的判斷
- 如果你的判斷基於推測，明確標注 ⚠️ 推測"""

_COMMAND_CONFIRM = """在執行任何動作前，判斷這個命令是否有模糊或歧義之處。
如果涉及刪除檔案、覆寫資料、或修改 config，則列出影響範圍。
如果有疑問，則報告並不是自行假設。

"""

# ── Files that gate full auto_review vs quick_review vs skip ──────────────────

_CRITICAL_FILES = {"purpose.md", "meta.md", "plan.md"}
_NORMAL_FILES = {"architecture.md", "skill.md"}
_SKIP_DIRS = {"upper", "lower", "summaries", "uploads", "chunks"}
_SKIP_FILES = {
    "context.md",
    "progress.md",
    "lessons.md",
    "timeline.md",
    "recon.md",
    "preflight.md",
    "reality_check.md",
    "current_task.md",
    "final_check.md",
    ".manifest",
}

# ── Global process reference for force_stop ───────────────────────────────────

_current_process: subprocess.Popen | None = None


def get_current_process() -> subprocess.Popen | None:
    return _current_process


def _set_current_process(p: subprocess.Popen | None) -> None:
    global _current_process
    _current_process = p


# ── CLI launcher ──────────────────────────────────────────────────────────────


def call_cli(
    prompt: str,
    engine: str,
    cwd: Path,
    model: str,
    allowed_tools: list[str] | None = None,
) -> subprocess.Popen:
    """Launch CLI subprocess and return Popen object."""
    cwd = Path(cwd)
    if not cwd.exists():
        raise FileNotFoundError(f"Working directory does not exist: {cwd}")

    if engine == "claude":
        executable = _resolve_engine_executable(engine)
        cmd = [
            executable,
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--no-loading-indicator",
        ]
        if allowed_tools:
            cmd += ["--allowedTools", ",".join(allowed_tools)]
    elif engine == "codex":
        executable = _resolve_engine_executable(engine)
        cmd = [
            executable,
            "-a",
            "never",
            "exec",
            "--sandbox",
            "workspace-write" if allowed_tools else "read-only",
            "--json",
        ]
        if not _is_git_repo(cwd):
            cmd.append("--skip-git-repo-check")
        codex_model = _resolve_codex_model(model)
        if codex_model:
            cmd += ["-m", codex_model]
        cmd.append(prompt)
    else:
        raise ValueError(f"Unknown engine: {engine!r}")

    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _resolve_codex_model(model: str) -> str | None:
    """Map Forge's generic model labels to Codex CLI behavior."""
    if model in {"sonnet", "opus"}:
        return None
    return model


def _resolve_engine_executable(engine: str) -> str:
    """Resolve the CLI executable path from shared engine discovery."""
    path = find_engine_path(engine)
    if path:
        return path

    searched = ", ".join([engine, f"{engine}.exe", f"{engine}.cmd"])
    raise FileNotFoundError(
        f"Could not find the {engine} CLI executable. Searched: {searched}"
    )


def _is_git_repo(path: Path) -> bool:
    """Return True when the working directory is inside a Git repository."""
    current = path.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return True
    return False


# ── Core functions ────────────────────────────────────────────────────────────


def think(
    prompt: str,
    context_files: list[Path],
    engine: str,
    cwd: Path,
    model: str = "sonnet",
    max_tokens: int = 100_000,
    on_token_warning: Callable[[], None] | None = None,
    on_token_kill: Callable[[], None] | None = None,
) -> str:
    """Read-only LLM call. Injects anti-hallucination suffix. Returns output text."""
    full_prompt = _build_context(context_files) + prompt + _ANTI_HALLUCINATION

    def _warn():
        if on_token_warning:
            on_token_warning()

    def _kill():
        if on_token_kill:
            on_token_kill()

    process = call_cli(full_prompt, engine, cwd, model, allowed_tools=None)
    result = _monitor.monitor_process(
        process, max_tokens=max_tokens, on_warning=_warn, on_kill=_kill
    )
    return result["output"]


def do(
    prompt: str,
    context_files: list[Path],
    engine: str,
    cwd: Path,
    model: str = "opus",
    max_tokens: int = 100_000,
    on_token_warning: Callable[[], None] | None = None,
    on_token_kill: Callable[[], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> str:
    """Write-mode LLM call. Injects command-confirm prefix + anti-hallucination suffix."""
    full_prompt = (
        _COMMAND_CONFIRM
        + _build_context(context_files)
        + prompt
        + _ANTI_HALLUCINATION
    )

    allowed_tools = ["Read", "Write", "Edit", "Bash"]

    process = call_cli(full_prompt, engine, cwd, model, allowed_tools=allowed_tools)
    _set_current_process(process)

    def _warn():
        if on_token_warning:
            on_token_warning()

    def _kill():
        _set_current_process(None)
        if on_token_kill:
            on_token_kill()

    try:
        result = _monitor.monitor_process(
            process, max_tokens=max_tokens, on_warning=_warn, on_kill=_kill
        )
    finally:
        _set_current_process(None)

    output = result["output"]
    scan_warnings = scan_code(output)
    if scan_warnings:
        for w in scan_warnings:
            if on_log:
                on_log(f"⚠️ 安全掃描：{w}")
    # 只記錄，不阻擋（由使用者決定是否繼續）
    return output


def compress(file_path: Path, engine: str, max_lines: int = 100) -> None:
    """Compress an agent file in-place using LLM. Uses skip_review to avoid loops."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace").replace(
            "\r\n", "\n"
        )
    except OSError:
        return

    from . import prompts as _p  # lazy import to avoid circular

    prompt = _p.compress_prompt(content, max_lines)
    result = think(prompt, [], engine, file_path.parent, model="sonnet")
    if result.strip():
        write_agent_file(file_path, result, engine, skip_review=True)


# ── write_agent_file ──────────────────────────────────────────────────────────


def write_agent_file(
    path: Path,
    content: str,
    engine: str,
    skip_review: bool = False,
    project_root: Path | None = None,
) -> None:
    """All writes to .agent/*.md go through this function."""
    # 路徑安全驗證（不受 skip_review 影響）
    if project_root is not None and not is_safe_path(path, project_root):
        raise ValueError(f"write_agent_file 拒絕寫入範圍外路徑：{path}")
    safe_write(path, content)
    update_manifest(path)

    if skip_review:
        return

    filename = path.name
    parent_name = path.parent.name

    # Skip review for operational files and files inside upper/lower/summaries
    if parent_name in _SKIP_DIRS or filename in _SKIP_FILES:
        return

    if filename in _CRITICAL_FILES:
        new_content, _ = auto_review(content, engine)
        if new_content != content:
            safe_write(path, new_content)
            update_manifest(path)

    elif filename in _NORMAL_FILES:
        new_content = quick_review(content, engine)
        if new_content != content:
            safe_write(path, new_content)
            update_manifest(path)

    # Other files: no review


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_context(context_files: list[Path]) -> str:
    """Read context files and join into a string prefix."""
    parts: list[str] = []
    for f in context_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace").replace(
                "\r\n", "\n"
            )
            parts.append(f"=== {f.name} ===\n{text}")
        except OSError:
            parts.append(f"=== {f.name} ===\n[無法讀取]")
    return "\n\n".join(parts) + ("\n\n" if parts else "")
