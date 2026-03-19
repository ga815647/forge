"""Tests for forge/orchestrator_loop.py (deterministic helpers) and loop_helpers.py"""
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from forge.orchestrator_loop import _should_use_lightweight, _get_upper_context_files
from forge.loop_helpers import (
    append_lessons,
    format_audit,
    handle_judge,
    is_plan_complete,
    parse_current_task,
    read_file,
    save_summary,
    update_upper_files,
)
from forge.orchestrator_main import safety_check, CostTracker


# ── safety_check ──────────────────────────────────────────────────────────────


def test_safety_check_rm_rf():
    assert safety_check("rm -rf /") is not None


def test_safety_check_drop_table():
    assert safety_check("drop table users") is not None


def test_safety_check_push_main():
    assert safety_check("git push origin main") is not None


def test_safety_check_push_master():
    assert safety_check("git push origin master") is not None


def test_safety_check_force_push():
    assert safety_check("git push --force") is not None


def test_safety_check_reset_hard():
    assert safety_check("git reset --hard HEAD") is not None


def test_safety_check_safe_status():
    assert safety_check("git status") is None


def test_safety_check_safe_ls():
    assert safety_check("ls -la") is None


def test_safety_check_safe_pytest():
    assert safety_check("pytest tests/ -v") is None


def test_safety_check_safe_cat():
    assert safety_check("cat README.md") is None


def test_safety_check_case_insensitive():
    assert safety_check("RM -RF /home") is not None


# ── CostTracker ───────────────────────────────────────────────────────────────


def test_cost_tracker_add_accumulates():
    ct = CostTracker()
    ct.add(1, "think", 100)
    ct.add(2, "do", 200)
    assert ct.total_tokens == 300


def test_cost_tracker_summary_includes_tokens():
    ct = CostTracker()
    ct.add(1, "do", 1500)
    summary = ct.summary()
    assert "1,500" in summary or "1500" in summary


def test_cost_tracker_summary_includes_rounds():
    ct = CostTracker()
    ct.add(1, "do", 100)
    ct.add(2, "think", 200)
    summary = ct.summary()
    assert "2" in summary


def test_cost_tracker_initial_zero():
    ct = CostTracker()
    assert ct.total_tokens == 0
    assert ct.rounds == []


# ── _should_use_lightweight ───────────────────────────────────────────────────


def test_lightweight_no_plan():
    assert _should_use_lightweight("", "summary") is False


def test_lightweight_no_prev_summary():
    assert _should_use_lightweight("some plan", "") is False


def test_lightweight_with_fail_in_summary():
    assert _should_use_lightweight("plan", "🔴 FAIL happened") is False


def test_lightweight_with_warn_in_summary():
    assert _should_use_lightweight("plan", "🟡 WARN detected") is False


def test_lightweight_all_pass():
    assert _should_use_lightweight("plan content", "all good, no issues") is True


# ── _get_upper_context_files ─────────────────────────────────────────────────


def test_get_upper_context_files_returns_existing(tmp_path):
    agent = tmp_path / ".agent"
    upper = agent / "upper"
    upper.mkdir(parents=True)
    (upper / "context.md").write_text("ctx", encoding="utf-8")
    (upper / "progress.md").write_text("prog", encoding="utf-8")
    files = _get_upper_context_files(agent)
    names = {f.name for f in files}
    assert "context.md" in names
    assert "progress.md" in names


def test_get_upper_context_files_skips_missing(tmp_path):
    agent = tmp_path / ".agent"
    (agent / "upper").mkdir(parents=True)
    files = _get_upper_context_files(agent)
    assert files == []


# ── parse_current_task ────────────────────────────────────────────────────────


def test_parse_current_task_extracts_task_section():
    output = "## 當前任務\n修改 foo.py 的函式\n完成後報告\n"
    result = parse_current_task(output, "user message")
    assert "當前任務" in result or "修改" in result


def test_parse_current_task_fallback_to_user_message():
    output = "general analysis without task section"
    result = parse_current_task(output, "user wants X")
    assert "user wants X" in result


def test_parse_current_task_limits_length():
    output = "## 當前任務\n" + "line\n" * 100
    result = parse_current_task(output, "msg")
    # Should not be too long (capped at 30 lines)
    assert result.count("\n") <= 35


# ── read_file ─────────────────────────────────────────────────────────────────


def test_read_file_existing(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("hello", encoding="utf-8")
    assert read_file(f) == "hello"


def test_read_file_missing(tmp_path):
    assert read_file(tmp_path / "nonexistent.md") == ""


def test_read_file_normalizes_crlf(tmp_path):
    f = tmp_path / "win.md"
    f.write_bytes(b"line1\r\nline2\r\n")
    result = read_file(f)
    assert "\r\n" not in result


# ── save_summary ──────────────────────────────────────────────────────────────


def test_save_summary_creates_file(tmp_path):
    path = tmp_path / "summaries" / "round_001.md"
    save_summary(path, 1, "do output")
    assert path.exists()


def test_save_summary_contains_round_num(tmp_path):
    path = tmp_path / "round_005.md"
    save_summary(path, 5, "result here")
    content = path.read_text(encoding="utf-8")
    assert "005" in content


def test_save_summary_contains_output(tmp_path):
    path = tmp_path / "round_001.md"
    save_summary(path, 1, "do output XYZ")
    content = path.read_text(encoding="utf-8")
    assert "do output XYZ" in content


# ── format_audit ─────────────────────────────────────────────────────────────


def test_format_audit_empty():
    result = format_audit([])
    assert isinstance(result, str)


def test_format_audit_includes_name_and_level():
    results = [{"name": "pytest", "level": "🔵 INFO", "output": "1 passed"}]
    out = format_audit(results)
    assert "pytest" in out
    assert "INFO" in out


def test_format_audit_multiple():
    results = [
        {"name": "pytest", "level": "🔵 INFO", "output": "ok"},
        {"name": "ruff", "level": "🔴 FAIL", "output": "error"},
    ]
    out = format_audit(results)
    assert "pytest" in out
    assert "ruff" in out


# ── handle_judge ─────────────────────────────────────────────────────────────


def _make_agent(tmp_path: Path) -> Path:
    agent = tmp_path / ".agent"
    (agent / "lower").mkdir(parents=True)
    (agent / "upper").mkdir(parents=True)
    return agent


def test_handle_judge_continue_on_pass(tmp_path):
    agent = _make_agent(tmp_path)
    status, decision = handle_judge("繼續下一步", [], agent, 1, lambda m: None)
    assert status == "continue"


def test_handle_judge_blocked_on_conflict(tmp_path):
    agent = _make_agent(tmp_path)
    status, _ = handle_judge(
        "這個做法與 purpose 衝突，需要調整", [], agent, 1, lambda m: None
    )
    assert status == "blocked"


def test_handle_judge_blocked_on_cannot_do(tmp_path):
    agent = _make_agent(tmp_path)
    status, _ = handle_judge("這個任務做不到", [], agent, 1, lambda m: None)
    assert status == "blocked"


def test_handle_judge_records_fail_lesson(tmp_path):
    agent = _make_agent(tmp_path)
    audit_results = [{"name": "pytest", "level": "🔴 FAIL", "output": ""}]
    handle_judge("result OK", audit_results, agent, 3, lambda m: None)
    lessons = agent / "lower" / "lessons.md"
    assert lessons.exists()
    assert "FAIL" in lessons.read_text(encoding="utf-8")


def test_handle_judge_suggest_real_test(tmp_path):
    agent = _make_agent(tmp_path)
    status, decision = handle_judge("建議實測這個功能", [], agent, 1, lambda m: None)
    assert status == "continue"
    assert "實測" in decision


# ── is_plan_complete ─────────────────────────────────────────────────────────


def test_is_plan_complete_all_done(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "plan.md").write_text("- [x] step 1\n- [x] step 2\n", encoding="utf-8")
    assert is_plan_complete(agent) is True


def test_is_plan_complete_has_unchecked(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "plan.md").write_text("- [x] done\n- [ ] pending\n", encoding="utf-8")
    assert is_plan_complete(agent) is False


def test_is_plan_complete_no_plan_file(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    assert is_plan_complete(agent) is False


def test_is_plan_complete_empty_plan(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "plan.md").write_text("", encoding="utf-8")
    assert is_plan_complete(agent) is False


# ── append_lessons ────────────────────────────────────────────────────────────


def test_append_lessons_creates_file(tmp_path):
    path = tmp_path / "lessons.md"
    append_lessons(path, "lesson 1")
    assert path.exists()
    assert "lesson 1" in path.read_text(encoding="utf-8")


def test_append_lessons_accumulates(tmp_path):
    path = tmp_path / "lessons.md"
    append_lessons(path, "first lesson")
    append_lessons(path, "second lesson")
    content = path.read_text(encoding="utf-8")
    assert "first lesson" in content
    assert "second lesson" in content


# ── update_upper_files ────────────────────────────────────────────────────────


def test_update_upper_files_no_crash_no_decisions(tmp_path):
    agent = tmp_path / ".agent"
    (agent / "upper").mkdir(parents=True)
    update_upper_files(agent, "random output with no decisions")  # Should not crash


def test_update_upper_files_appends_decisions(tmp_path):
    agent = tmp_path / ".agent"
    (agent / "upper").mkdir(parents=True)
    think_output = "analysis → decision A\nother stuff\n決策 → decision B"
    update_upper_files(agent, think_output)
    progress = agent / "upper" / "progress.md"
    content = progress.read_text(encoding="utf-8")
    assert "decision A" in content or "decision B" in content
