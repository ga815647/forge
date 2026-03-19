"""Tests for forge/prompts.py"""
from pathlib import Path

import pytest

from forge.prompts import (
    compress_prompt,
    doc_prompt,
    judge_prompt,
    plan_prompt,
    preflight_prompt,
    quick_review_prompt,
    reality_check_prompt,
    recon_prompt,
    review_prompt,
    slim_prompt,
    task_prompt,
)


# ── recon_prompt ──────────────────────────────────────────────────────────────


def test_recon_prompt_returns_string():
    result = recon_prompt(Path("/some/path"))
    assert isinstance(result, str)
    assert len(result) > 0


def test_recon_prompt_includes_path():
    p = Path("/my/project")
    result = recon_prompt(p)
    assert str(p) in result


def test_recon_prompt_includes_anti_hallucination():
    result = recon_prompt(Path("/p"))
    assert "不確定" in result or "推測" in result


def test_recon_prompt_says_no_speculation():
    result = recon_prompt(Path("/p"))
    assert "推測" in result or "不要推測" in result


# ── preflight_prompt ──────────────────────────────────────────────────────────


def test_preflight_prompt_returns_string():
    result = preflight_prompt("recon content", "user wants X", [])
    assert isinstance(result, str)
    assert len(result) > 0


def test_preflight_prompt_includes_user_input():
    result = preflight_prompt("recon", "build a todo app", [])
    assert "build a todo app" in result


def test_preflight_prompt_includes_chunks():
    result = preflight_prompt("recon", "req", ["[chunk1.md] Header"])
    assert "chunk1.md" in result


def test_preflight_prompt_no_chunks_no_section():
    result = preflight_prompt("recon", "req", [])
    # No crash and no "None" in output
    assert "None" not in result


# ── plan_prompt ───────────────────────────────────────────────────────────────


def test_plan_prompt_returns_string():
    result = plan_prompt("purpose", "arch", "skill", [])
    assert isinstance(result, str)


def test_plan_prompt_includes_purpose():
    result = plan_prompt("my purpose text", "arch", "skill", [])
    assert "my purpose text" in result


# ── task_prompt ───────────────────────────────────────────────────────────────


def test_task_prompt_includes_current_task():
    result = task_prompt("do this task", "skill info", "done X")
    assert "do this task" in result


def test_task_prompt_includes_command_confirm():
    result = task_prompt("task", "skill", "progress")
    assert "模糊" in result or "歧義" in result or "動作" in result


def test_task_prompt_includes_anti_hallucination():
    result = task_prompt("task", "skill", "progress")
    assert "不確定" in result or "推測" in result


# ── judge_prompt ──────────────────────────────────────────────────────────────


def test_judge_prompt_includes_summary():
    result = judge_prompt("audit passed", "plan content", "purpose content")
    assert "audit passed" in result


def test_judge_prompt_includes_purpose():
    result = judge_prompt("summary", "plan", "purpose content XYZ")
    assert "purpose content XYZ" in result


# ── compress_prompt ───────────────────────────────────────────────────────────


def test_compress_prompt_includes_content():
    result = compress_prompt("original content here", 50)
    assert "original content here" in result


def test_compress_prompt_includes_max_lines():
    result = compress_prompt("content", 75)
    assert "75" in result


# ── review_prompt ─────────────────────────────────────────────────────────────


def test_review_prompt_includes_content():
    result = review_prompt("document content abc")
    assert "document content abc" in result


def test_review_prompt_mentions_emoji_choices():
    result = review_prompt("content")
    assert "✅" in result
    assert "⚡" in result


# ── quick_review_prompt ───────────────────────────────────────────────────────


def test_quick_review_prompt_includes_content():
    result = quick_review_prompt("quick check this")
    assert "quick check this" in result


def test_quick_review_prompt_mentions_fast():
    result = quick_review_prompt("content")
    assert "✅" in result or "⚡" in result


# ── slim_prompt ───────────────────────────────────────────────────────────────


def test_slim_prompt_includes_content():
    result = slim_prompt("verbose content to trim")
    assert "verbose content to trim" in result


# ── reality_check_prompt ──────────────────────────────────────────────────────


def test_reality_check_prompt_includes_recon():
    result = reality_check_prompt("recon info XYZ", "context info")
    assert "recon info XYZ" in result


def test_reality_check_prompt_includes_context():
    result = reality_check_prompt("recon", "context ABC")
    assert "context ABC" in result


# ── doc_prompt ────────────────────────────────────────────────────────────────


def test_doc_prompt_includes_purpose():
    result = doc_prompt("purpose XYZ", "arch", "timeline")
    assert "purpose XYZ" in result


def test_doc_prompt_mentions_readme():
    result = doc_prompt("p", "a", "t")
    assert "README" in result
