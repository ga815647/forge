"""audit_runner.py - Auto-detect project check tools and run them."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .security import is_safe_path

# ── Tool detection ────────────────────────────────────────────────────────────


def detect_tools(project_path: Path) -> list[dict]:
    """Detect available audit/test tools for the project.

    Returns list of {"cmd": str, "type": str, "name": str}.
    """
    tools: list[dict] = []

    # ── Python ───────────────────────────────────────────────────────────────
    if (
        (project_path / "pytest.ini").exists()
        or (project_path / "conftest.py").exists()
        or (project_path / "tests").is_dir()
        or _has_pyproject_section(project_path, "tool.pytest")
    ):
        if shutil.which("pytest"):
            tools.append({"cmd": "pytest tests/ -v", "type": "test", "name": "pytest"})

    if (project_path / "tools" / "audit.py").exists():
        if _is_allowed_script(project_path / "tools" / "audit.py", project_path):
            tools.append(
                {
                    "cmd": "python tools/audit.py .",
                    "type": "audit",
                    "name": "audit.py",
                }
            )

    if _has_pyproject_section(project_path, "tool.ruff") or (
        project_path / ".ruff.toml"
    ).exists():
        if shutil.which("ruff"):
            tools.append({"cmd": "ruff check .", "type": "lint", "name": "ruff"})

    if (project_path / "mypy.ini").exists() or _has_pyproject_section(
        project_path, "tool.mypy"
    ):
        if shutil.which("mypy"):
            tools.append({"cmd": "mypy .", "type": "typecheck", "name": "mypy"})

    # ── Node / TypeScript ─────────────────────────────────────────────────────
    pkg_json = project_path / "package.json"
    if pkg_json.exists():
        import json

        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            if "test" in scripts and shutil.which("npm"):
                tools.append({"cmd": "npm test", "type": "test", "name": "npm test"})
        except (json.JSONDecodeError, OSError):
            pass

        eslint_configs = [
            ".eslintrc",
            ".eslintrc.js",
            ".eslintrc.json",
            ".eslintrc.yaml",
            "eslint.config.js",
            "eslint.config.mjs",
        ]
        if any((project_path / c).exists() for c in eslint_configs):
            if shutil.which("npx"):
                tools.append(
                    {
                        "cmd": "npx eslint src/ --max-warnings=0",
                        "type": "lint",
                        "name": "eslint",
                    }
                )

        if (project_path / "tsconfig.json").exists() and shutil.which("npx"):
            tools.append(
                {"cmd": "npx tsc --noEmit", "type": "typecheck", "name": "tsc"}
            )

    # ── Rust ──────────────────────────────────────────────────────────────────
    if (project_path / "Cargo.toml").exists():
        if shutil.which("cargo"):
            tools.append({"cmd": "cargo test", "type": "test", "name": "cargo test"})
            tools.append(
                {"cmd": "cargo clippy", "type": "lint", "name": "cargo clippy"}
            )

    return tools


# ── Run audit ─────────────────────────────────────────────────────────────────


def run_audit(
    project_path: Path,
    on_log: Callable[[str], None] | None = None,
) -> list[dict]:
    """Run all detected tools. Returns list of result dicts."""
    tools = detect_tools(project_path)
    results: list[dict] = []
    if on_log:
        on_log(
            f"Audit tool detection complete: count={len(tools)}, "
            f"tools={', '.join(tool['name'] for tool in tools) if tools else '(none)'}"
        )

    for tool in tools:
        if on_log:
            on_log(
                f"Running audit tool: name={tool['name']}, type={tool['type']}, cmd={tool['cmd']}"
            )
        result = _run_tool(tool["cmd"], project_path)
        level = _classify(result["returncode"], result["output"])
        if on_log:
            on_log(
                f"Audit tool finished: name={tool['name']}, returncode={result['returncode']}, "
                f"level={level}, output_chars={len(result['output'])}"
            )
        results.append(
            {
                "name": tool["name"],
                "type": tool["type"],
                "cmd": tool["cmd"],
                "returncode": result["returncode"],
                "output": result["output"],
                "level": level,  # 🔴 FAIL / 🟡 WARN / 🔵 INFO
            }
        )

    if not tools:
        if on_log:
            on_log("No audit tools detected; emitting informational placeholder result")
        results.append(
            {
                "name": "no-tools",
                "type": "info",
                "cmd": "",
                "returncode": 0,
                "output": "未偵測到任何測試/lint 工具",
                "level": "🔵 INFO",
            }
        )

    return results


# ── Security scan ─────────────────────────────────────────────────────────────


def run_security_scan(
    project_path: Path,
    on_log: Callable[[str], None] | None = None,
) -> list[dict]:
    """Scan for hardcoded secrets and known vulnerabilities."""
    results: list[dict] = []

    # Grep for hardcoded secrets
    patterns = [
        r"(password|passwd|pwd)\s*=\s*['\"][^'\"]{4,}",
        r"(api_key|apikey|api-key)\s*=\s*['\"][^'\"]{8,}",
        r"(secret|token)\s*=\s*['\"][^'\"]{8,}",
        r"(aws_access_key|aws_secret)",
        r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
    ]

    for pattern in patterns:
        if on_log:
            on_log(f"Security grep scan: pattern={pattern}")
        try:
            proc = subprocess.run(
                ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
                 "--include=*.env", "-i", pattern, str(project_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            if on_log:
                on_log("Skipping grep-based secret scan because grep is unavailable")
            break
        if proc.stdout.strip():
            if on_log:
                on_log(
                    f"Security grep matched: pattern={pattern}, output_chars={len(proc.stdout.strip())}"
                )
            results.append(
                {
                    "name": "hardcoded-secret",
                    "type": "security",
                    "pattern": pattern,
                    "output": proc.stdout.strip(),
                    "level": "🔴 FAIL",
                }
            )

    # pip-audit
    req = project_path / "requirements.txt"
    if req.exists() and shutil.which("pip-audit"):
        if on_log:
            on_log(f"Running dependency security audit: pip-audit -r {req.name}")
        proc = subprocess.run(
            ["pip-audit", "-r", str(req)],
            capture_output=True,
            text=True,
            cwd=str(project_path),
            encoding="utf-8",
            errors="replace",
        )
        level = "🔴 FAIL" if proc.returncode != 0 else "🔵 INFO"
        if on_log:
            on_log(
                f"pip-audit finished: returncode={proc.returncode}, level={level}, "
                f"output_chars={len(proc.stdout + proc.stderr)}"
            )
        results.append(
            {
                "name": "pip-audit",
                "type": "security",
                "cmd": f"pip-audit -r {req.name}",
                "returncode": proc.returncode,
                "output": proc.stdout + proc.stderr,
                "level": level,
            }
        )

    # npm audit
    pkg_json = project_path / "package.json"
    if pkg_json.exists() and shutil.which("npm"):
        if on_log:
            on_log("Running dependency security audit: npm audit --json")
        proc = subprocess.run(
            ["npm", "audit", "--json"],
            capture_output=True,
            text=True,
            cwd=str(project_path),
            encoding="utf-8",
            errors="replace",
        )
        level = "🔴 FAIL" if proc.returncode != 0 else "🔵 INFO"
        if on_log:
            on_log(
                f"npm audit finished: returncode={proc.returncode}, level={level}, "
                f"output_chars={len(proc.stdout)}"
            )
        results.append(
            {
                "name": "npm-audit",
                "type": "security",
                "cmd": "npm audit",
                "returncode": proc.returncode,
                "output": proc.stdout[:2000],
                "level": level,
            }
        )

    if not results:
        if on_log:
            on_log("Security scan found no matches or dependency audit issues")
        results.append(
            {
                "name": "opensec",
                "type": "security",
                "output": "無發現硬編碼機密或已知漏洞",
                "level": "🔵 INFO",
            }
        )

    return results


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run_tool(cmd: str, cwd: Path) -> dict:
    """Run a shell command and return {returncode, output}."""
    try:
        proc = subprocess.run(
            cmd,
            shell=True,  # noqa: S602 - cmd comes from detect_tools(), not user input
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "returncode": proc.returncode,
            "output": (proc.stdout + proc.stderr).strip(),
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "output": "⏰ 超時"}
    except OSError as e:
        return {"returncode": -1, "output": f"執行失敗: {e}"}


def _classify(returncode: int, output: str) -> str:
    """Classify result as FAIL / WARN / INFO."""
    if returncode != 0:
        return "🔴 FAIL"
    lower = output.lower()
    if "warning" in lower or "warn" in lower or "⚠" in lower:
        return "🟡 WARN"
    return "🔵 INFO"


def _has_pyproject_section(project_path: Path, section: str) -> bool:
    """Check if pyproject.toml contains a given [tool.xxx] section."""
    pyproject = project_path / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
        return f"[{section}]" in text or f"[{section}." in text
    except OSError:
        return False


def _is_allowed_script(script_path: Path, project_root: Path) -> bool:
    """Check if a script is safe to run (inside project, not newly appeared)."""
    return is_safe_path(script_path, project_root) and script_path.exists()
