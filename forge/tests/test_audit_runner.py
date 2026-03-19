"""Tests for forge/audit_runner.py"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge.audit_runner import (
    _classify,
    _has_pyproject_section,
    _is_allowed_script,
    _run_tool,
    detect_tools,
    run_audit,
    run_security_scan,
)


# ── _classify ─────────────────────────────────────────────────────────────────


def test_classify_nonzero_is_fail():
    assert _classify(1, "") == "🔴 FAIL"


def test_classify_zero_clean_is_info():
    assert _classify(0, "all good") == "🔵 INFO"


def test_classify_zero_with_warning():
    assert _classify(0, "1 warning found") == "🟡 WARN"


def test_classify_zero_with_warn_keyword():
    assert _classify(0, "some WARN messages") == "🟡 WARN"


# ── _has_pyproject_section ────────────────────────────────────────────────────


def test_has_pyproject_section_present(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\naddopts = -v\n", encoding="utf-8")
    assert _has_pyproject_section(tmp_path, "tool.pytest") is True


def test_has_pyproject_section_absent(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
    assert _has_pyproject_section(tmp_path, "tool.ruff") is False


def test_has_pyproject_section_no_file(tmp_path):
    assert _has_pyproject_section(tmp_path, "tool.ruff") is False


# ── _is_allowed_script ────────────────────────────────────────────────────────


def test_is_allowed_script_inside_project(tmp_path):
    script = tmp_path / "tools" / "audit.py"
    script.parent.mkdir()
    script.write_text("# audit", encoding="utf-8")
    assert _is_allowed_script(script, tmp_path) is True


def test_is_allowed_script_nonexistent(tmp_path):
    script = tmp_path / "tools" / "nonexistent.py"
    assert _is_allowed_script(script, tmp_path) is False


def test_is_allowed_script_outside_project(tmp_path):
    outside = tmp_path.parent / "evil.py"
    outside.write_text("# evil", encoding="utf-8")
    assert _is_allowed_script(outside, tmp_path) is False


# ── _run_tool ─────────────────────────────────────────────────────────────────


def test_run_tool_success(tmp_path):
    result = _run_tool("echo hello", tmp_path)
    assert result["returncode"] == 0
    assert "hello" in result["output"]


def test_run_tool_timeout():
    # Use a very short timeout to simulate timeout
    with patch("subprocess.run") as mock_run:
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 1)
        result = _run_tool("slow command", Path("."))
    assert result["returncode"] == -1
    assert "超時" in result["output"] or "timeout" in result["output"].lower()


def test_run_tool_oserror():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = OSError("No such file")
        result = _run_tool("nonexistent_command_xyz", Path("."))
    assert result["returncode"] == -1


# ── detect_tools ──────────────────────────────────────────────────────────────


def test_detect_tools_empty_project(tmp_path):
    """No tool markers → no tools detected."""
    tools = detect_tools(tmp_path)
    assert tools == []


def test_detect_tools_detects_pytest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("", encoding="utf-8")
    with patch("shutil.which", return_value="/usr/bin/pytest"):
        tools = detect_tools(tmp_path)
    names = [t["name"] for t in tools]
    assert "pytest" in names


def test_detect_tools_no_pytest_if_not_installed(tmp_path):
    (tmp_path / "tests").mkdir()
    with patch("shutil.which", return_value=None):
        tools = detect_tools(tmp_path)
    names = [t["name"] for t in tools]
    assert "pytest" not in names


def test_detect_tools_detects_cargo(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    with patch("shutil.which", return_value="/usr/bin/cargo"):
        tools = detect_tools(tmp_path)
    types = [t["type"] for t in tools]
    assert "test" in types
    assert "lint" in types


def test_detect_tools_npm_test(tmp_path):
    pkg = {"scripts": {"test": "jest"}, "name": "x", "version": "1.0"}
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    with patch("shutil.which", return_value="/usr/bin/npm"):
        tools = detect_tools(tmp_path)
    names = [t["name"] for t in tools]
    assert "npm test" in names


# ── run_audit ─────────────────────────────────────────────────────────────────


def test_run_audit_empty_project_returns_info(tmp_path):
    """No tools → returns one INFO entry."""
    results = run_audit(tmp_path)
    assert len(results) == 1
    assert results[0]["level"] == "🔵 INFO"
    assert results[0]["name"] == "no-tools"


def test_run_audit_returns_result_dicts(tmp_path):
    (tmp_path / "tests").mkdir()
    with patch("shutil.which", return_value="/fake/pytest"), \
         patch("forge.audit_runner._run_tool", return_value={"returncode": 0, "output": "1 passed"}):
        results = run_audit(tmp_path)
    assert all("level" in r for r in results)
    assert all("name" in r for r in results)


# ── run_security_scan ─────────────────────────────────────────────────────────


def test_run_security_scan_no_secrets_returns_info(tmp_path):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        results = run_security_scan(tmp_path)
    # Should return at least one entry
    assert len(results) >= 1
    # With no secrets found, should be INFO
    levels = {r["level"] for r in results}
    assert "🔵 INFO" in levels


def test_run_security_scan_detects_hardcoded_password(tmp_path):
    """When grep finds a match (returncode 0 + stdout), level should be FAIL."""
    match_output = 'app.py:1:password = "supersecret123"'
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=match_output, stderr="")
        results = run_security_scan(tmp_path)
    levels = [r["level"] for r in results]
    assert "🔴 FAIL" in levels


def test_run_security_scan_no_crash_without_tools(tmp_path):
    """Should not crash if pip-audit/npm are not installed."""
    (tmp_path / "requirements.txt").write_text("requests==2.28.0\n", encoding="utf-8")
    with patch("shutil.which", return_value=None):
        results = run_security_scan(tmp_path)
    assert isinstance(results, list)
