"""main.py - Forge Gradio UI entry point."""
from __future__ import annotations

import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Generator


_LOG_POLL_INTERVAL = 0.2


class _Session:
    def __init__(self) -> None:
        self.round_num = 1
        self.project_path: Path | None = None
        self.session_guard = None
        self.pending_review: bool = False
        self.pending_clarification: bool = False
        self.pending_input: str = ""
        self._lock = threading.Lock()
        self._init_tracker()
        self._init_approved()

    def _init_tracker(self) -> None:
        from .orchestrator_main import CostTracker

        self.cost_tracker = CostTracker()

    def _init_approved(self) -> None:
        from .security import ApprovedPaths

        self.approved: ApprovedPaths = ApprovedPaths()

    def next_round(self) -> int:
        with self._lock:
            round_num = self.round_num
            self.round_num += 1
            return round_num

    def reset(self) -> None:
        with self._lock:
            self.round_num = 1
            self.session_guard = None
            self.pending_review = False
            self.pending_clarification = False
            self.pending_input = ""
            self._init_tracker()
            self._init_approved()


_session = _Session()


def create_session_state() -> dict:
    """Create a fresh Gradio-friendly session state object."""
    from .security import ApprovedPaths

    return {
        "approved": ApprovedPaths(),
        "guard": None,
    }


def update_progress(
    state: dict,
    turns: int,
    max_turns: int,
    tokens: int,
    max_tokens: int,
) -> tuple[str, object]:
    """Render coarse session progress for optional UI callbacks."""
    pct = turns / max_turns if max_turns else 0
    progress_text = f"Turns: {turns} / {max_turns} | Tokens: {tokens:,} / {max_tokens:,}"
    near_limit = pct >= 0.8
    warning_text = (
        f"Warning: session is at {pct:.0%} of the configured turn budget. "
        "Review purpose.md if you need to raise the limit."
    )
    try:
        import gradio as gr

        return progress_text, gr.update(value=warning_text, visible=near_limit)
    except ImportError:
        return progress_text, {"value": warning_text, "visible": near_limit}


def _format_live_log(log_lines: list[str]) -> str:
    """Render the dedicated live log panel."""
    if not log_lines:
        return "Waiting for backend log..."
    return "\n".join(log_lines)


def _format_response(status: str, output: str, log_lines: list[str], running: bool) -> str:
    """Render the assistant bubble with status and current backend log.

    Blocking statuses (needs_clarification, needs_review) get a distinct
    visual treatment to ensure the user notices they must reply.
    """
    BLOCKING_STATUSES = ("needs_clarification", "needs_review")

    if status in BLOCKING_STATUSES and output.strip():
        # Blocking bubble: output is the whole content, no status prefix, no log
        parts = [
            "> ⏸ **Forge 正在等待你的回覆，請回答後才能繼續。**",
            "",
            output.strip(),
        ]
        return "\n".join(parts)

    parts = [f"**Status**: {status}"]

    if output.strip():
        parts.extend(["", output.strip()])
    elif running:
        parts.extend(["", "Running. Backend log will appear below as soon as work starts."])

    parts.extend(
        [
            "",
            "<details open><summary>Live Log</summary>",
            "",
            "```text",
            _format_live_log(log_lines),
            "```",
            "</details>",
        ]
    )
    return "\n".join(parts)


def _snapshot_history(history: list) -> list:
    """Return a shallow copy safe for incremental UI yields."""
    return [dict(item) if isinstance(item, dict) else item for item in history]


def _ensure_session_guard(project_path: Path) -> None:
    if _session.session_guard is not None:
        return

    from .security import SessionGuard

    _session.session_guard = SessionGuard.from_purpose(
        project_path,
        ui_max_turns=None,
    )


def chat(
    message: str,
    history: list,
    project_path_str: str,
    engine: str,
    mode: str,
    review_mode: bool,
) -> Generator[tuple[list, str], None, None]:
    """Stream chat updates and live backend logs into the UI."""
    from .orchestrator_main import handle_input

    history = list(history or [])
    if not message.strip():
        yield history, ""
        return

    project_path = Path(project_path_str.strip()) if project_path_str.strip() else Path(".")
    if not project_path.exists():
        yield history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": f"Project path does not exist: {project_path}"},
        ], ""
        return

    _session.project_path = project_path
    _ensure_session_guard(project_path)

    log_lines: list[str] = []
    history = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": _format_response("running", "", log_lines, running=True)},
    ]
    yield _snapshot_history(history), _format_live_log(log_lines)

    round_num = _session.next_round()
    log_queue: queue.Queue[str] = queue.Queue()
    result_holder: dict[str, dict] = {}
    error_holder: dict[str, str] = {}

    def _worker() -> None:
        try:
            result_holder["result"] = handle_input(
                user_input=message,
                uploaded_files=[],
                mode=mode,
                project_path=project_path,
                engine=engine,
                on_log=log_queue.put,
                review_mode=review_mode,
                round_num=round_num,
                cost_tracker=_session.cost_tracker,
                session_guard=_session.session_guard,
                approved_paths=_session.approved,
            )
        except Exception:
            error_holder["traceback"] = traceback.format_exc()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    while worker.is_alive() or not log_queue.empty():
        updated = False

        while True:
            try:
                log_lines.append(log_queue.get_nowait())
                updated = True
            except queue.Empty:
                break

        if updated:
            history[-1] = {
                "role": "assistant",
                "content": _format_response("running", "", log_lines, running=True),
            }
            yield _snapshot_history(history), _format_live_log(log_lines)

        if worker.is_alive():
            time.sleep(_LOG_POLL_INTERVAL)

    worker.join()

    while not log_queue.empty():
        log_lines.append(log_queue.get_nowait())

    if "traceback" in error_holder:
        log_lines.append("Unhandled exception")
        log_lines.append(error_holder["traceback"].rstrip())
        status = "error"
        output = "Backend execution failed. See Live Log for the traceback."
    else:
        result = result_holder.get("result", {})
        status = result.get("status", "done")
        output = result.get("output", "")

    history[-1] = {
        "role": "assistant",
        "content": _format_response(status, output, log_lines, running=False),
    }
    yield _snapshot_history(history), _format_live_log(log_lines)


def stop_forge() -> str:
    from .orchestrator_main import force_stop

    force_stop()
    return "Stopped."


def rollback_ui(project_path_str: str, target_hash: str) -> str:
    from .git_ops import rollback
    from .orchestrator_main import force_stop

    ok = rollback(
        Path(project_path_str.strip()),
        target_hash.strip(),
        force_stop_fn=force_stop,
    )
    return "Rollback completed." if ok else "Rollback failed."


def list_commits_ui(project_path_str: str) -> str:
    from .git_ops import list_commits

    commits = list_commits(Path(project_path_str.strip()), max_count=15)
    if not commits:
        return "No commits found."
    return "\n".join(f"`{c['hash'][:8]}` {c['msg']}" for c in commits)


def cost_summary() -> str:
    return _session.cost_tracker.summary()


def launch(share: bool = False) -> None:
    from .ui_builder import build_combined_ui

    ui = build_combined_ui(chat, stop_forge, rollback_ui, list_commits_ui, cost_summary)
    ui.launch(share=share)


if __name__ == "__main__":
    launch()
