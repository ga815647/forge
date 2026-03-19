"""main.py - Forge Gradio UI entry point."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Generator

from .main_config import load_config
from .ui_builder import build_main_ui, build_setup_ui


# ── Session state ─────────────────────────────────────────────────────────────


class _Session:
    def __init__(self) -> None:
        self.round_num = 1
        self.project_path: Path | None = None
        self._lock = threading.Lock()
        self._init_tracker()

    def _init_tracker(self) -> None:
        from .orchestrator_main import CostTracker
        self.cost_tracker = CostTracker()

    def next_round(self) -> int:
        with self._lock:
            r = self.round_num
            self.round_num += 1
            return r

    def reset(self) -> None:
        with self._lock:
            self.round_num = 1
            self._init_tracker()


_session = _Session()


# ── Chat handler ──────────────────────────────────────────────────────────────


def chat(
    message: str,
    history: list,
    project_path_str: str,
    engine: str,
    mode: str,
    review_mode: bool,
) -> Generator[list, None, None]:
    from .orchestrator_main import handle_input

    if not message.strip():
        yield history
        return

    project_path = Path(project_path_str.strip()) if project_path_str.strip() else Path(".")
    if not project_path.exists():
        yield history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": f"路徑不存在: {project_path}"},
        ]
        return

    _session.project_path = project_path
    log_lines: list[str] = []
    history = history + [{"role": "user", "content": message},
                         {"role": "assistant", "content": "⏳ 處理中..."}]
    yield history

    round_num = _session.next_round()
    result = handle_input(
        user_input=message,
        uploaded_files=[],
        mode=mode,
        project_path=project_path,
        engine=engine,
        on_log=lambda msg: log_lines.append(msg),
        review_mode=review_mode,
        round_num=round_num,
        cost_tracker=_session.cost_tracker,
    )

    status = result.get("status", "done")
    output = result.get("output", "")
    log_text = "\n".join(log_lines)
    response = f"**狀態**: {status}\n\n{output}"
    if log_text:
        response += f"\n\n<details><summary>Log</summary>\n\n```\n{log_text}\n```\n</details>"

    history[-1] = {"role": "assistant", "content": response}
    yield history


# ── Action handlers ───────────────────────────────────────────────────────────


def stop_forge() -> str:
    from .orchestrator_main import force_stop
    force_stop()
    return "已停止"


def rollback_ui(project_path_str: str, target_hash: str) -> str:
    from .git_ops import rollback
    from .orchestrator_main import force_stop
    ok = rollback(Path(project_path_str.strip()), target_hash.strip(), force_stop_fn=force_stop)
    return "✅ 回滾成功" if ok else "❌ 回滾失敗"


def list_commits_ui(project_path_str: str) -> str:
    from .git_ops import list_commits
    commits = list_commits(Path(project_path_str.strip()), max_count=15)
    if not commits:
        return "無 commit 記錄（git 未初始化或無 commit）"
    return "\n".join(f"`{c['hash'][:8]}` {c['msg']}" for c in commits)


def cost_summary() -> str:
    return _session.cost_tracker.summary()


# ── Launch ────────────────────────────────────────────────────────────────────


def launch(share: bool = False) -> None:
    cfg = load_config()
    if cfg is None:
        ui = build_setup_ui()
    else:
        ui = build_main_ui(cfg, chat, stop_forge, rollback_ui, list_commits_ui, cost_summary)
    ui.launch(share=share)


if __name__ == "__main__":
    launch()
