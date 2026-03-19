"""monitor.py - Real-time token monitoring and process lifecycle management."""
from __future__ import annotations

import json
import subprocess
from typing import Callable


def monitor_process(
    process: subprocess.Popen,
    max_tokens: int,
    on_warning: Callable[[], None],
    on_kill: Callable[[], None],
    warn_pct: float = 0.85,
    kill_pct: float = 0.95,
) -> dict:
    """Stream-read process stdout, monitor token usage, enforce limits.

    Returns:
        {
            "status": "completed" | "truncated" | "killed",
            "tokens_used": int,
            "output": str,
        }
    """
    warn_threshold = int(max_tokens * warn_pct)
    kill_threshold = int(max_tokens * kill_pct)

    output_parts: list[str] = []
    tokens_used = 0
    warned = False
    status = "completed"

    assert process.stdout is not None

    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue

        # Parse stream-json line
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Non-JSON output (e.g. codex plain text fallback)
            output_parts.append(raw_line)
            continue

        # Extract text content
        _extract_text(obj, output_parts)

        # Extract token usage
        usage = _extract_usage(obj)
        if usage > 0:
            tokens_used = max(tokens_used, usage)

        # Enforce limits
        if tokens_used >= kill_threshold and status != "killed":
            status = "killed"
            on_kill()
            try:
                process.kill()
            except OSError:
                pass
            break

        if tokens_used >= warn_threshold and not warned:
            warned = True
            on_warning()

    # Drain remaining stdout after kill (avoid pipe deadlock)
    try:
        remaining = process.stdout.read()
        if remaining:
            output_parts.append(remaining)
    except OSError:
        pass

    process.wait()

    if status == "completed" and process.returncode != 0:
        status = "truncated"

    return {
        "status": status,
        "tokens_used": tokens_used,
        "output": "".join(output_parts),
    }


def _extract_text(obj: dict, parts: list[str]) -> None:
    """Extract text content from a stream-json event object."""
    # claude stream-json format
    msg = obj.get("message") or {}
    content = msg.get("content") or []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if text:
                parts.append(text)
        elif isinstance(block, str):
            parts.append(block)

    # Direct text field (some events)
    if "text" in obj and isinstance(obj["text"], str):
        if not content:  # avoid double-counting
            parts.append(obj["text"])

    # codex --json format: {"output": "..."}
    if "output" in obj and isinstance(obj["output"], str):
        parts.append(obj["output"])


def _extract_usage(obj: dict) -> int:
    """Extract total token count from a stream-json event. Returns 0 if absent."""
    usage = obj.get("usage") or {}
    if not isinstance(usage, dict):
        return 0
    input_t = usage.get("input_tokens", 0) or 0
    output_t = usage.get("output_tokens", 0) or 0
    return int(input_t) + int(output_t)


# ── psutil-based kill tree (cross-platform) ───────────────────────────────────


def kill_proc_tree(pid: int) -> None:
    """Kill a process and all its children (Windows-safe)."""
    try:
        import psutil

        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return  # Already gone
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            pass
    except ImportError:
        # psutil not installed — fall back to basic kill
        import subprocess as sp

        try:
            sp.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        except FileNotFoundError:
            import os
            import signal

            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
