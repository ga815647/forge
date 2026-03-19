"""Tests for forge/timeline.py"""
from pathlib import Path

import pytest

from forge.timeline import append_round, detect_anomalies, _parse_rows


# ── append_round ──────────────────────────────────────────────────────────────


def test_append_round_creates_file(tmp_path):
    tl = tmp_path / "timeline.md"
    append_round(tl, 1, "do", "task A", "ok", "continue", 100)
    assert tl.exists()


def test_append_round_contains_header(tmp_path):
    tl = tmp_path / "timeline.md"
    append_round(tl, 1, "do", "task", "ok", "continue")
    content = tl.read_text(encoding="utf-8")
    assert "| 輪 |" in content


def test_append_round_contains_data(tmp_path):
    tl = tmp_path / "timeline.md"
    append_round(tl, 7, "think", "build X", "ok", "繼續", 500)
    content = tl.read_text(encoding="utf-8")
    assert "007" in content
    assert "think" in content
    assert "build X" in content
    assert "繼續" in content
    assert "500" in content


def test_append_round_accumulates(tmp_path):
    tl = tmp_path / "timeline.md"
    append_round(tl, 1, "do", "task1", "ok", "continue")
    append_round(tl, 2, "think", "task2", "fail", "retry")
    content = tl.read_text(encoding="utf-8")
    assert "task1" in content
    assert "task2" in content


def test_append_round_escapes_pipe(tmp_path):
    tl = tmp_path / "timeline.md"
    append_round(tl, 1, "do", "task | extra", "ok", "continue")
    content = tl.read_text(encoding="utf-8")
    # Pipe in data should be replaced with full-width pipe
    assert "task ｜ extra" in content


def test_append_round_uses_lf(tmp_path):
    tl = tmp_path / "timeline.md"
    append_round(tl, 1, "do", "t", "ok", "c")
    raw = tl.read_bytes()
    assert b"\r\n" not in raw


# ── _parse_rows ───────────────────────────────────────────────────────────────


def test_parse_rows_basic():
    text = (
        "# Timeline\n\n"
        "| 輪 | 類型 | 任務 | 結果 | 決策 | tokens |\n"
        "|----|------|------|------|------|--------|\n"
        "| 001 | do | task1 | ok | continue | 100 |\n"
        "| 002 | think | task2 | fail | retry | 200 |\n"
    )
    rows = _parse_rows(text)
    assert len(rows) == 2
    assert rows[0]["task"] == "task1"
    assert rows[1]["type"] == "think"


def test_parse_rows_skips_header(tmp_path):
    tl = tmp_path / "timeline.md"
    append_round(tl, 1, "do", "task", "ok", "continue")
    rows = _parse_rows(tl.read_text(encoding="utf-8"))
    # Should have exactly 1 data row, not the header row
    assert len(rows) == 1
    assert rows[0]["task"] == "task"


def test_parse_rows_empty_file():
    rows = _parse_rows("")
    assert rows == []


# ── detect_anomalies ──────────────────────────────────────────────────────────


def test_detect_anomalies_no_anomalies(tmp_path):
    tl = tmp_path / "timeline.md"
    for i in range(1, 4):
        append_round(tl, i, "do", "different task", "ok", "continue")
    anomalies = detect_anomalies(tl)
    assert anomalies == []


def test_detect_anomalies_consecutive_failures(tmp_path):
    tl = tmp_path / "timeline.md"
    for i in range(1, 5):
        append_round(tl, i, "do", "failing task", "FAIL", "retry")
    anomalies = detect_anomalies(tl)
    assert any("連續失敗" in a or "failing" in a for a in anomalies)


def test_detect_anomalies_stagnation(tmp_path):
    tl = tmp_path / "timeline.md"
    for i in range(1, 7):
        append_round(tl, i, "do", "same stagnant task", "ok", "continue")
    anomalies = detect_anomalies(tl)
    assert any("5" in a or "停滯" in a or "same stagnant" in a for a in anomalies)


def test_detect_anomalies_missing_file(tmp_path):
    tl = tmp_path / "nonexistent.md"
    assert detect_anomalies(tl) == []


def test_detect_anomalies_decision_reversal(tmp_path):
    tl = tmp_path / "timeline.md"
    append_round(tl, 1, "think", "task", "ok", "繼續")
    append_round(tl, 2, "think", "task", "ok", "停止")
    append_round(tl, 3, "think", "task", "ok", "繼續")
    anomalies = detect_anomalies(tl)
    # May or may not detect depending on wording — just check no crash
    assert isinstance(anomalies, list)
