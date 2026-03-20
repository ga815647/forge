"""Tests for forge/monitor.py"""
import io
import json
import subprocess
from unittest.mock import MagicMock

from forge.monitor import _extract_text, _extract_usage, kill_proc_tree, monitor_process


def _make_process(lines: list[str]) -> MagicMock:
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


def test_extract_text_claude_format():
    parts: list[str] = []
    _extract_text({"message": {"content": [{"type": "text", "text": "hello"}]}}, parts)
    assert parts == ["hello"]


def test_extract_text_direct_text_field():
    parts: list[str] = []
    _extract_text({"text": "world"}, parts)
    assert parts == ["world"]


def test_extract_text_codex_output():
    parts: list[str] = []
    _extract_text({"output": "codex result"}, parts)
    assert parts == ["codex result"]


def test_extract_text_codex_exec_agent_message():
    parts: list[str] = []
    _extract_text({"item": {"type": "agent_message", "text": "done"}}, parts)
    assert parts == ["done"]


def test_extract_text_empty_obj():
    parts: list[str] = []
    _extract_text({}, parts)
    assert parts == []


def test_extract_text_non_dict_is_ignored():
    parts: list[str] = []
    _extract_text(123, parts)
    assert parts == []


def test_extract_text_non_text_block():
    parts: list[str] = []
    _extract_text({"message": {"content": [{"type": "tool_use", "id": "x"}]}}, parts)
    assert parts == []


def test_extract_usage_sums_input_output():
    assert _extract_usage({"usage": {"input_tokens": 100, "output_tokens": 50}}) == 150


def test_extract_usage_missing_returns_zero():
    assert _extract_usage({}) == 0


def test_extract_usage_partial():
    assert _extract_usage({"usage": {"input_tokens": 30}}) == 30


def test_extract_usage_non_dict_usage():
    assert _extract_usage({"usage": "bad"}) == 0


def test_extract_usage_non_dict_obj():
    assert _extract_usage(123) == 0


def test_monitor_normal_completion():
    proc = _make_process([_stream_line("hello", 10, 5)])
    warned = []
    killed = []
    result = monitor_process(
        proc,
        max_tokens=1000,
        on_warning=lambda: warned.append(1),
        on_kill=lambda: killed.append(1),
    )
    assert result["status"] == "completed"
    assert result["tokens_used"] == 15
    assert "hello" in result["output"]
    assert warned == []
    assert killed == []


def test_monitor_warning_threshold():
    proc = _make_process([_stream_line("x", 850, 0)])
    warned = []
    monitor_process(
        proc,
        max_tokens=1000,
        on_warning=lambda: warned.append(1),
        on_kill=lambda: None,
    )
    assert warned == [1]


def test_monitor_kill_threshold():
    proc = _make_process([_stream_line("x", 950, 0)])
    killed = []
    result = monitor_process(
        proc,
        max_tokens=1000,
        on_warning=lambda: None,
        on_kill=lambda: killed.append(1),
    )
    assert result["status"] == "killed"
    assert killed == [1]


def test_monitor_skips_invalid_json():
    proc = _make_process(["not json at all", _stream_line("ok", 5, 0)])
    result = monitor_process(
        proc,
        max_tokens=1000,
        on_warning=lambda: None,
        on_kill=lambda: None,
    )
    assert result["status"] == "completed"
    assert "not json at all" in result["output"]
    assert "ok" in result["output"]


def test_monitor_tolerates_scalar_json_lines():
    proc = _make_process(["123", _stream_line("ok", 5, 0)])
    result = monitor_process(
        proc,
        max_tokens=1000,
        on_warning=lambda: None,
        on_kill=lambda: None,
    )
    assert result["status"] == "completed"
    assert "123" in result["output"]
    assert "ok" in result["output"]


def test_monitor_nonzero_exit_is_truncated():
    proc = _make_process([_stream_line("x", 1, 0)])
    proc.returncode = 1
    result = monitor_process(
        proc,
        max_tokens=1000,
        on_warning=lambda: None,
        on_kill=lambda: None,
    )
    assert result["status"] == "truncated"


def test_monitor_accumulates_text():
    proc = _make_process([_stream_line("part1", 1, 0), _stream_line("part2", 2, 0)])
    result = monitor_process(
        proc,
        max_tokens=1000,
        on_warning=lambda: None,
        on_kill=lambda: None,
    )
    assert "part1" in result["output"]
    assert "part2" in result["output"]


def test_kill_proc_tree_no_crash_on_invalid_pid():
    kill_proc_tree(999999999)
