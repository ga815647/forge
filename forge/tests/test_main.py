"""Tests for forge/main.py"""
import time
from unittest.mock import MagicMock, patch

from forge import main


def test_chat_streams_live_logs(tmp_path):
    main._session.reset()

    def fake_handle_input(**kwargs):
        kwargs["on_log"]("step 1")
        time.sleep(0.05)
        kwargs["on_log"]("step 2")
        return {"status": "done", "output": "finished", "round": 1}

    with patch("forge.orchestrator_main.handle_input", side_effect=fake_handle_input), \
         patch("forge.security.SessionGuard.from_purpose", return_value=MagicMock()), \
         patch("forge.main._LOG_POLL_INTERVAL", 0.01):
        updates = list(main.chat("run", [], str(tmp_path), "codex", "direct", False))

    assert len(updates) >= 2

    first_history, first_log = updates[0]
    assert "running" in first_history[-1]["content"]
    assert "Waiting for backend log" in first_log

    final_history, final_log = updates[-1]
    assert "step 1" in final_history[-1]["content"]
    assert "step 2" in final_log
    assert "finished" in final_history[-1]["content"]
    assert "**Status**: done" in final_history[-1]["content"]


def test_chat_reports_backend_exception_in_log(tmp_path):
    main._session.reset()

    def fake_handle_input(**kwargs):
        kwargs["on_log"]("before crash")
        raise RuntimeError("boom")

    with patch("forge.orchestrator_main.handle_input", side_effect=fake_handle_input), \
         patch("forge.security.SessionGuard.from_purpose", return_value=MagicMock()), \
         patch("forge.main._LOG_POLL_INTERVAL", 0.01):
        updates = list(main.chat("run", [], str(tmp_path), "codex", "direct", False))

    final_history, final_log = updates[-1]
    assert "**Status**: error" in final_history[-1]["content"]
    assert "before crash" in final_log
    assert "RuntimeError: boom" in final_log
