"""Tests for forge/orchestrator_init.py and forge/init_chunker.py"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge.init_chunker import _extract_title, _find_split_points, chunk_file
from forge.orchestrator_init import (
    _copy_normalized,
    _extract_and_write_files,
    _extract_zip,
    _is_large_task,
    _prompt_existing_agent,
)


# ── _is_large_task ────────────────────────────────────────────────────────────


def test_is_large_task_with_10_steps():
    plan = "\n".join([f"- step {i}" for i in range(10)])
    assert _is_large_task(plan) is True


def test_is_large_task_with_5_steps():
    plan = "\n".join([f"- step {i}" for i in range(5)])
    assert _is_large_task(plan) is False


def test_is_large_task_empty():
    assert _is_large_task("") is False


def test_is_large_task_mixed_bullets():
    # Mix of "- ", "* " bullets — 10 of them should be large
    plan = "\n".join(["- step"] * 7 + ["* step"] * 4)
    assert _is_large_task(plan) is True


# ── _extract_zip ──────────────────────────────────────────────────────────────


def test_extract_zip_creates_files(tmp_path):
    import zipfile
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("hello.txt", "hello world")
    dest = tmp_path / "extracted"
    dest.mkdir()
    _extract_zip(zip_path, dest)
    assert (dest / "hello.txt").exists()


def test_extract_zip_bad_zip_no_crash(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip file")
    _extract_zip(bad, tmp_path)  # Should not raise


# ── _copy_normalized ──────────────────────────────────────────────────────────


def test_copy_normalized_converts_crlf(tmp_path):
    src = tmp_path / "src.md"
    src.write_bytes(b"line1\r\nline2\r\n")
    dest = tmp_path / "dest" / "src.md"
    _copy_normalized(src, dest)
    content = dest.read_text(encoding="utf-8")
    assert "\r\n" not in content
    assert "line1" in content
    assert "line2" in content


def test_copy_normalized_creates_parent(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("data", encoding="utf-8")
    dest = tmp_path / "deep" / "nested" / "dest.txt"
    _copy_normalized(src, dest)
    assert dest.exists()


# ── _prompt_existing_agent ────────────────────────────────────────────────────


def test_prompt_existing_agent_calls_log(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    messages = []
    _prompt_existing_agent(agent, lambda m: messages.append(m))
    assert len(messages) == 1
    assert "Forge" in messages[0] or "記憶" in messages[0]


def test_prompt_existing_agent_no_log(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    _prompt_existing_agent(agent, None)  # Should not crash


# ── _find_split_points ────────────────────────────────────────────────────────


def test_find_split_points_empty():
    points = _find_split_points([], 300)
    assert points == []


def test_find_split_points_short_file():
    lines = ["line\n"] * 100
    points = _find_split_points(lines, 300)
    assert points == []


def test_find_split_points_prefers_h2_header():
    lines = ["content\n"] * 200 + ["## New Section\n"] + ["more\n"] * 200
    points = _find_split_points(lines, 300)
    assert len(points) > 0
    # First split should be at or near the ## header (line 200)
    first = points[0]
    assert abs(first - 200) <= 150  # within search window


def test_find_split_points_multiple_chunks():
    lines = ["line\n"] * 900
    points = _find_split_points(lines, 300)
    assert len(points) >= 2


# ── _extract_title ────────────────────────────────────────────────────────────


def test_extract_title_from_markdown_header():
    lines = ["# My Title\n", "content\n"]
    assert _extract_title(lines) == "My Title"


def test_extract_title_from_h2():
    lines = ["## Section Title\n"]
    assert _extract_title(lines) == "Section Title"


def test_extract_title_from_plain_text():
    lines = ["Just some text here\n"]
    assert _extract_title(lines) == "Just some text here"


def test_extract_title_empty_lines():
    lines = ["\n", "\n", "## Header\n"]
    assert _extract_title(lines) == "Header"


def test_extract_title_no_content():
    assert _extract_title([]) == "(no title)"


# ── chunk_file ────────────────────────────────────────────────────────────────


def test_chunk_file_small_file_no_chunks(tmp_path):
    """File under 300 lines → still creates 1 chunk."""
    src = tmp_path / "small.md"
    src.write_text("\n".join(["line"] * 50), encoding="utf-8")
    chunks_dir = tmp_path / "chunks"
    titles = chunk_file(src, chunks_dir, "small.md")
    assert len(titles) == 1


def test_chunk_file_large_file_splits(tmp_path):
    src = tmp_path / "large.md"
    src.write_text("\n".join([f"line {i}" for i in range(900)]), encoding="utf-8")
    chunks_dir = tmp_path / "chunks"
    titles = chunk_file(src, chunks_dir, "large.md")
    assert len(titles) >= 2
    assert len(list(chunks_dir.glob("*.md"))) >= 2


def test_chunk_file_chunk_has_header(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("\n".join([f"line {i}" for i in range(400)]), encoding="utf-8")
    chunks_dir = tmp_path / "chunks"
    chunk_file(src, chunks_dir, "doc.md")
    first_chunk = sorted(chunks_dir.glob("*.md"))[0]
    content = first_chunk.read_text(encoding="utf-8")
    assert "# Chunk" in content
    assert "doc.md" in content


def test_chunk_file_returns_title_strings(tmp_path):
    src = tmp_path / "spec.md"
    src.write_text("\n".join(["content"] * 600), encoding="utf-8")
    chunks_dir = tmp_path / "chunks"
    titles = chunk_file(src, chunks_dir, "spec.md")
    assert all(isinstance(t, str) for t in titles)
    assert all("[spec_chunk_" in t for t in titles)


# ── _extract_and_write_files ─────────────────────────────────────────────────


def test_extract_and_write_files_parses_purpose(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    preflight_output = "## purpose.md\nThis is the purpose.\n\n## plan.md\n- step 1\n"
    with patch("forge.agent.write_agent_file") as mock_write:
        _extract_and_write_files(preflight_output, agent, "claude")
    calls = [c[0][0].name for c in mock_write.call_args_list]
    assert "purpose.md" in calls


def test_extract_and_write_files_no_crash_empty(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    with patch("forge.agent.write_agent_file"):
        _extract_and_write_files("", agent, "claude")  # Should not crash
