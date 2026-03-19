"""Tests for forge/monitor.py"""
import io
import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from forge.monitor import _extract_text, _extract_usage, kill_proc_tree, monitor_process


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_process(lines: list[str]) -> MagicMock:
    """Build a mock Popen whose stdout yields the given lines."""
    proc = MagicMock(spec=subprocess.Popen)
    proc.stdout = io.StringIO("\n".join(lines) + "\n")
    proc.returncode = 0
    proc.wait.return_value = None
    return proc


def _stream_line(text: str = "", input_tokens: int = 0, output_tokens: int = 0) -> str:
    obj = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    return json.dumps(obj)


# ── _extract_text ─────────────────────────────────────────────────────────────


def test_extract_text_claude_format():
    parts: list[str] = []
    obj = {"message": {"content": [{"type": "text", "text": "hello"}]}}
    _extract_text(obj, parts)
    assert parts == ["hello"]


def test_extract_text_direct_text_field():
    parts: list[str] = []
    _extract_text({"text": "world"}, parts)
    assert parts == ["world"]


def test_extract_text_codex_output():
    parts: list[str] = []
    _extract_text({"output": "codex result"}, parts)
    assert parts == ["codex result"]


def test_extract_text_empty_obj():
    parts: list[str] = []
    _extract_text({}, parts)
    assert parts == []


def test_extract_text_non_text_block():
    parts: list[str] = []
    obj = {"message": {"content": [{"type": "tool_use", "id": "x"}]}}
    _extract_text(obj, parts)
    assert parts == []


# ── _extract_usage ────────────────────────────────────────────────────────────


def test_extract_usage_sums_input_output():
    obj = {"usage": {"input_tokens": 100, "output_tokens": 50}}
    assert _extract_usage(obj) == 150


def test_extract_usage_missing_returns_zero():
    assert _extract_usage({}) == 0


def test_extract_usage_partial():
    assert _extract_usage({"usage": {"input_tokens": 30}}) == 30


def test_extract_usage_non_dict_usage():
    assert _extract_usage({"usage": "bad"}) == 0


# ── monitor_process ───────────────────────────────────────────────────────────


def test_monitor_normal_completion():
    lines = [_stream_line("hello", 10, 5)]
    proc = _make_process(lines)
    warned = []
    killed = []

    result = monitor_process(
        proc, max_tokens=1000,
        on_warning=lambda: warned.append(1),
        on_kill=lambda: killed.append(1),
    )
    assert result["status"] == "completed"
    assert result["tokens_used"] == 15
    assert "hello" in result["output"]
    assert warned == []
    assert killed == []


def test_monitor_85_percent_warning():
    lines = [_stream_line("x", 850, 0)]
    proc = _make_process(lines)
    warned = []

    monitor_process(
        proc, max_tokens=1000,
        on_warning=lambda: warned.append(1),
        on_kill=lambda: None,
    )
    assert warned == [1]


def test_monitor_95_percent_kill():
    lines = [_stream_line("x", 950, 0)]
    proc = _make_process(lines)
    killed = []

    result = monitor_process(
        proc, max_tokens=1000,
        on_warning=lambda: None,
        on_kill=lambda: killed.append(1),
    )
    assert result["status"] == "killed"
    assert killed == [1]


def test_monitor_skips_invalid_json():
    lines = ["not json at all", _stream_line("ok", 5, 0)]
    proc = _make_process(lines)
    result = monitor_process(
        proc, max_tokens=1000,
        on_warning=lambda: None,
        on_kill=lambda: None,
    )
    assert result["status"] == "completed"
    assert "ok" in result["output"]


def test_monitor_nonzero_exit_is_truncated():
    lines = [_stream_line("x", 1, 0)]
    proc = _make_process(lines)
    proc.returncode = 1
    result = monitor_process(
        proc, max_tokens=1000,
        on_warning=lambda: None,
        on_kill=lambda: None,
    )
    assert result["status"] == "truncated"


def test_monitor_accumulates_text():
    lines = [_stream_line("part1", 1, 0), _stream_line("part2", 2, 0)]
    proc = _make_process(lines)
    result = monitor_process(
        proc, max_tokens=1000,
        on_warning=lambda: None,
        on_kill=lambda: None,
    )
    assert "part1" in result["output"]
    assert "part2" in result["output"]


# ── kill_proc_tree ────────────────────────────────────────────────────────────


def test_kill_proc_tree_no_crash_on_invalid_pid():
    """kill_proc_tree with a dead PID should not raise."""
    kill_proc_tree(999999999)  # almost certainly not a real process
