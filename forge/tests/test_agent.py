"""Tests for forge/agent.py"""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge.agent import (
    _build_context,
    _ANTI_HALLUCINATION,
    _COMMAND_CONFIRM,
    call_cli,
    compress,
    do,
    think,
    write_agent_file,
)


# ── _build_context ────────────────────────────────────────────────────────────


def test_build_context_empty():
    assert _build_context([]) == ""


def test_build_context_reads_file(tmp_path):
    f = tmp_path / "ctx.md"
    f.write_text("hello world", encoding="utf-8")
    result = _build_context([f])
    assert "ctx.md" in result
    assert "hello world" in result


def test_build_context_missing_file(tmp_path):
    f = tmp_path / "missing.md"
    result = _build_context([f])
    assert "missing.md" in result
    assert "無法讀取" in result


def test_build_context_normalizes_line_endings(tmp_path):
    f = tmp_path / "win.md"
    f.write_bytes(b"line1\r\nline2\r\n")
    result = _build_context([f])
    assert "\r\n" not in result


# ── call_cli ──────────────────────────────────────────────────────────────────


def test_call_cli_claude_command(tmp_path):
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        call_cli("test prompt", "claude", tmp_path, "sonnet")
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "test prompt" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd


def test_call_cli_claude_with_model(tmp_path):
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        call_cli("p", "claude", tmp_path, "opus")
        cmd = mock_popen.call_args[0][0]
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "opus"


def test_call_cli_claude_allowed_tools(tmp_path):
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        call_cli("p", "claude", tmp_path, "sonnet", allowed_tools=["Read", "Write"])
        cmd = mock_popen.call_args[0][0]
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert "Read" in cmd[idx + 1]
        assert "Write" in cmd[idx + 1]


def test_call_cli_codex_command(tmp_path):
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        call_cli("test", "codex", tmp_path, "any")
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "codex"
        assert "-q" in cmd
        assert "--json" in cmd


def test_call_cli_unknown_engine_raises(tmp_path):
    with pytest.raises(ValueError, match="Unknown engine"):
        call_cli("p", "unknown", tmp_path, "m")


# ── think() ───────────────────────────────────────────────────────────────────


def _mock_monitor_result(text: str = "output"):
    return {"status": "completed", "tokens_used": 10, "output": text}


def test_think_injects_anti_hallucination(tmp_path):
    captured_prompt = []

    def fake_call_cli(prompt, engine, cwd, model, allowed_tools=None):
        captured_prompt.append(prompt)
        m = MagicMock(spec=subprocess.Popen)
        m.stdout = iter([])
        m.returncode = 0
        m.wait.return_value = None
        return m

    with patch("forge.agent.call_cli", side_effect=fake_call_cli), \
         patch("forge.monitor.monitor_process", return_value=_mock_monitor_result()):
        think("my prompt", [], "claude", tmp_path)

    assert len(captured_prompt) == 1
    assert "不確定" in captured_prompt[0] or "推測" in captured_prompt[0]


def test_think_no_allowed_tools(tmp_path):
    captured = []

    def fake_call_cli(prompt, engine, cwd, model, allowed_tools=None):
        captured.append(allowed_tools)
        m = MagicMock(spec=subprocess.Popen)
        m.stdout = iter([])
        m.returncode = 0
        m.wait.return_value = None
        return m

    with patch("forge.agent.call_cli", side_effect=fake_call_cli), \
         patch("forge.monitor.monitor_process", return_value=_mock_monitor_result()):
        think("p", [], "claude", tmp_path)

    assert captured[0] is None


# ── do() ──────────────────────────────────────────────────────────────────────


def test_do_injects_command_confirm(tmp_path):
    captured_prompt = []

    def fake_call_cli(prompt, engine, cwd, model, allowed_tools=None):
        captured_prompt.append(prompt)
        m = MagicMock(spec=subprocess.Popen)
        m.stdout = iter([])
        m.returncode = 0
        m.wait.return_value = None
        return m

    with patch("forge.agent.call_cli", side_effect=fake_call_cli), \
         patch("forge.monitor.monitor_process", return_value=_mock_monitor_result()):
        do("do something", [], "claude", tmp_path)

    assert len(captured_prompt) == 1
    assert "模糊" in captured_prompt[0] or "歧義" in captured_prompt[0] or "動作" in captured_prompt[0]


def test_do_allows_write_tools(tmp_path):
    captured = []

    def fake_call_cli(prompt, engine, cwd, model, allowed_tools=None):
        captured.append(allowed_tools)
        m = MagicMock(spec=subprocess.Popen)
        m.stdout = iter([])
        m.returncode = 0
        m.wait.return_value = None
        return m

    with patch("forge.agent.call_cli", side_effect=fake_call_cli), \
         patch("forge.monitor.monitor_process", return_value=_mock_monitor_result()):
        do("p", [], "claude", tmp_path)

    tools = captured[0]
    assert tools is not None
    assert "Write" in tools
    assert "Edit" in tools


# ── write_agent_file ──────────────────────────────────────────────────────────


def test_write_agent_file_creates_file(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    path = agent / "recon.md"
    write_agent_file(path, "content", "claude", skip_review=True)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "content"


def test_write_agent_file_skip_files_no_review(tmp_path):
    """Files in SKIP_FILES should not trigger review even without skip_review=True."""
    agent = tmp_path / ".agent"
    agent.mkdir()
    path = agent / "timeline.md"
    # No mock needed — timeline.md is in SKIP_FILES, so no LLM call happens
    write_agent_file(path, "data", "claude", skip_review=False)
    assert path.read_text(encoding="utf-8") == "data"


def test_write_agent_file_upper_dir_no_review(tmp_path):
    agent = tmp_path / ".agent"
    (agent / "upper").mkdir(parents=True)
    path = agent / "upper" / "context.md"
    write_agent_file(path, "ctx", "claude", skip_review=False)
    assert path.read_text(encoding="utf-8") == "ctx"


# ── compress ──────────────────────────────────────────────────────────────────


def test_compress_replaces_file(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    f = agent / "upper" / "context.md"
    f.parent.mkdir()
    f.write_text("original content", encoding="utf-8")

    with patch("forge.agent.think", return_value="compressed"), \
         patch("forge.agent.write_agent_file") as mock_write:
        compress(f, "claude")
        mock_write.assert_called_once()
        args = mock_write.call_args[0]
        assert args[1] == "compressed"


def test_compress_skips_empty_result(tmp_path):
    f = tmp_path / "empty.md"
    f.write_text("some content", encoding="utf-8")

    with patch("forge.agent.think", return_value=""), \
         patch("forge.agent.write_agent_file") as mock_write:
        compress(f, "claude")
        mock_write.assert_not_called()


def test_compress_skips_missing_file(tmp_path):
    f = tmp_path / "nonexistent.md"
    with patch("forge.agent.think") as mock_think:
        compress(f, "claude")
        mock_think.assert_not_called()
