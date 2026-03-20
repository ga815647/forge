"""Tests for detailed live log helpers and instrumentation."""
from pathlib import Path
from unittest.mock import patch

from forge import audit_runner, orchestrator_main
from forge.live_log import make_live_logger


def test_make_live_logger_prefixes_scope_and_indents():
    messages: list[str] = []
    log = make_live_logger(messages.append, "scope")

    log("first line\nsecond line")

    assert len(messages) == 1
    assert "[scope]" in messages[0]
    assert "first line" in messages[0]
    assert "second line" in messages[0]


def test_handle_input_direct_logs_routing_details(tmp_path: Path):
    messages: list[str] = []

    with patch("forge.orchestrator_main._agent.do", return_value="ok"):
        result = orchestrator_main.handle_input(
            user_input="run tests",
            uploaded_files=[],
            mode="direct",
            project_path=tmp_path,
            engine="codex",
            on_log=messages.append,
            round_num=1,
        )

    joined = "\n".join(messages)
    assert result["status"] == "done"
    assert "[router r001]" in joined
    assert "Routing to direct execution mode" in joined
    assert "Calling agent.do() with 0 context files" in joined
    assert "Direct execution completed; output_chars=2" in joined


def test_run_audit_logs_detected_tools(tmp_path: Path):
    messages: list[str] = []

    with patch(
        "forge.audit_runner.detect_tools",
        return_value=[{"name": "pytest", "type": "test", "cmd": "pytest -q"}],
    ), patch(
        "forge.audit_runner._run_tool",
        return_value={"returncode": 0, "output": "1 passed"},
    ):
        results = audit_runner.run_audit(tmp_path, on_log=messages.append)

    joined = "\n".join(messages)
    assert results[0]["name"] == "pytest"
    assert "Audit tool detection complete: count=1, tools=pytest" in joined
    assert "Running audit tool: name=pytest, type=test, cmd=pytest -q" in joined
    assert "Audit tool finished: name=pytest, returncode=0, level=🔵 INFO" in joined
