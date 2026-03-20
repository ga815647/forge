"""Tests for forge/main_config.py"""
from pathlib import Path
from unittest.mock import patch

from forge.main_config import detect_engines, find_engine_path


def test_find_engine_path_prefers_configured_path(tmp_path):
    exe = tmp_path / "codex.exe"
    exe.write_text("", encoding="utf-8")
    with patch("forge.main_config.load_config", return_value={
        "engines": {"codex": {"path": str(exe)}}
    }):
        assert find_engine_path("codex") == str(exe)


def test_find_engine_path_falls_back_to_vscode_extension(tmp_path):
    exe = tmp_path / ".vscode" / "extensions" / "openai.chatgpt-1.0.0" / "bin" / "windows-x86_64" / "codex.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    with patch("forge.main_config.load_config", return_value={}), \
         patch("forge.main_config.shutil.which", return_value=None), \
         patch("forge.main_config.Path.home", return_value=tmp_path):
        assert find_engine_path("codex") == str(exe)


def test_detect_engines_uses_shared_discovery():
    with patch("forge.main_config.find_engine_path", side_effect=lambda engine: f"/fake/{engine}" if engine == "codex" else None):
        result = detect_engines()
        assert result["claude"] == {"installed": False, "path": ""}
        assert result["codex"] == {"installed": True, "path": "/fake/codex"}
