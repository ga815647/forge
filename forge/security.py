"""security.py - Path safety, manifest validation, prompt injection detection, safe file writes."""
from __future__ import annotations


class SessionLimitExceeded(Exception):
    """Raised when SessionGuard turn or token limits are exceeded."""

import ast
import hashlib
import json
import os
import re
import time
from pathlib import Path

# ── Prompt injection patterns ────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    r"ignore\s+(previous|all|prior)\s+instructions?",
    r"system\s*:",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"\[INST\]",
    r"</s>",
    r"### Instruction",
    r"Human:\s",
    r"Assistant:\s",
    r"BEGINNING OF CONVERSATION",
    r"You are now",
    r"Disregard\s+(all|previous|prior)",
    r"Forget\s+(everything|all|your|prior)",
]

_INJECTION_RE = re.compile(
    "|".join(_INJECTION_PATTERNS), re.IGNORECASE | re.MULTILINE
)


def detect_prompt_injection(text: str) -> bool:
    """Return True if text contains known prompt injection patterns."""
    return bool(_INJECTION_RE.search(text))


# ── Path safety ───────────────────────────────────────────────────────────────


def is_safe_path(path: Path, project_root: Path) -> bool:
    """Return True iff path is inside project_root (no escaping).

    防護：symlink 穿越、路徑穿越、Windows NTFS ADS、大小寫特例。
    """
    import sys
    # Windows NTFS ADS 防護：safe.txt:evil.exe 類型路徑直接拒絕
    if sys.platform == "win32":
        path_str = str(path)
        stripped = path_str[2:] if len(path_str) >= 2 and path_str[1] == ":" else path_str
        if ":" in stripped:
            return False
    try:
        resolved = path.resolve(strict=False)
        root_resolved = project_root.resolve()
        # Windows/macOS 檔案系統大小寫不敏感，統一小寫比對
        if sys.platform in ("win32", "darwin"):
            return str(resolved).lower().startswith(str(root_resolved).lower())
        return resolved.is_relative_to(root_resolved)
    except (OSError, ValueError):
        return False


# ── Safe atomic write ─────────────────────────────────────────────────────────


def atomic_write(path: Path, content: str, project_root: Path) -> None:
    """Write content to path atomically, with safety checks.

    1. 驗證路徑在 project_root 內
    2. 寫入到同目錄暫存檔，確保 os.replace 是原子操作
    3. 保留原始檔案的執行權限
    4. os.replace 原子覆蓋
    """
    import shutil
    import tempfile
    if not is_safe_path(path, project_root):
        raise ValueError(f"atomic_write 拒絕寫入範圍外路徑：{path}")
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".forge_tmp")
    try:
        if path.exists():
            shutil.copymode(path, tmp_path)  # 保留 chmod +x 等權限
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def safe_write(path: Path, content: str) -> None:
    """Write content to path using write-then-rename for crash safety.

    Retries up to 3 times on Windows file-lock errors.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    for attempt in range(3):
        try:
            os.replace(str(tmp), str(path))
            return
        except OSError:
            if attempt < 2:
                time.sleep(0.5)
            else:
                # Last resort
                tmp.rename(path)


# ── Manifest (.agent/.manifest) ───────────────────────────────────────────────


def _file_hash(path: Path) -> str:
    """SHA-256 hex digest of file contents."""
    try:
        data = path.read_bytes()
        return hashlib.sha256(data).hexdigest()
    except OSError:
        return ""


def _manifest_path(agent_dir: Path) -> Path:
    return agent_dir / ".manifest"


def load_manifest(agent_dir: Path) -> dict[str, str]:
    """Load {relative_path: sha256} manifest from .agent/.manifest."""
    mpath = _manifest_path(agent_dir)
    if not mpath.exists():
        return {}
    try:
        return json.loads(mpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def update_manifest(file_path: Path) -> None:
    """Update the manifest entry for file_path.

    Assumes file_path is inside an .agent/ directory.
    """
    agent_dir = _find_agent_dir(file_path)
    if agent_dir is None:
        return
    manifest = load_manifest(agent_dir)
    rel = str(file_path.relative_to(agent_dir))
    manifest[rel] = _file_hash(file_path)
    mpath = _manifest_path(agent_dir)
    safe_write(mpath, json.dumps(manifest, indent=2, ensure_ascii=False))


def build_manifest(agent_dir: Path) -> None:
    """Build manifest from scratch for all files in agent_dir."""
    manifest: dict[str, str] = {}
    for f in agent_dir.rglob("*"):
        if f.is_file() and f.name != ".manifest":
            rel = str(f.relative_to(agent_dir))
            manifest[rel] = _file_hash(f)
    mpath = _manifest_path(agent_dir)
    safe_write(mpath, json.dumps(manifest, indent=2, ensure_ascii=False))


def verify_manifest(agent_dir: Path) -> list[str]:
    """Check manifest against actual files. Return list of anomaly descriptions."""
    manifest = load_manifest(agent_dir)
    anomalies: list[str] = []

    # Check for files modified outside Forge
    for rel, expected_hash in manifest.items():
        actual_path = agent_dir / rel
        if not actual_path.exists():
            anomalies.append(f"消失的檔案: {rel}")
            continue
        actual = _file_hash(actual_path)
        if actual != expected_hash:
            anomalies.append(f"外部修改: {rel}")

    # Check for new files not in manifest
    for f in agent_dir.rglob("*"):
        if f.is_file() and f.name != ".manifest" and not f.suffix == ".tmp":
            rel = str(f.relative_to(agent_dir))
            if rel not in manifest:
                anomalies.append(f"未知檔案: {rel}")

    return anomalies


def _find_agent_dir(path: Path) -> Path | None:
    """Walk up from path to find the .agent directory it belongs to."""
    for parent in [path.parent, path.parent.parent]:
        if parent.name == ".agent" or (parent / ".manifest").exists():
            return parent if parent.name == ".agent" else None
        candidate = parent
        if candidate.name == ".agent":
            return candidate
    # Try: path is inside .agent/something
    parts = path.parts
    for i, part in enumerate(parts):
        if part == ".agent":
            return Path(*parts[: i + 1])
    return None


# ── Project directory locks ───────────────────────────────────────────────────

_PROJECT_HARDBLOCK = [
    ".git/hooks", ".git/config",
    ".github/workflows", ".gitlab-ci.yml", ".circleci", "Jenkinsfile",
    "venv", ".venv", "node_modules", ".tox",
]

_PROJECT_CONFIRM = [
    "setup.py", "pyproject.toml", "setup.cfg", "Makefile",
    ".vscode", ".idea",
]


def is_project_hardblock(path: Path, project_root: Path) -> bool:
    """Return True if this path is permanently blocked (no confirmation option)."""
    try:
        rel = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return False
    rel_lower = str(rel).lower().replace("\\", "/")
    return any(
        rel_lower == b or rel_lower.startswith(b + "/")
        for b in _PROJECT_HARDBLOCK
    )


def is_project_confirm(path: Path, project_root: Path) -> bool:
    """Return True if this path requires one-time user confirmation (not repeated in session)."""
    try:
        rel = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return False
    rel_lower = str(rel).lower().replace("\\", "/")
    return any(
        rel_lower == c or rel_lower.startswith(c + "/")
        for c in _PROJECT_CONFIRM
    )


# ── Safe subprocess ───────────────────────────────────────────────────────────

import signal
import subprocess
import sys

_CREDENTIAL_BLACKLIST_EXACT = {
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "DATABASE_URL", "DB_PASSWORD",
    "STRIPE_SECRET_KEY", "STRIPE_API_KEY",
}
_CREDENTIAL_BLACKLIST_KEYWORDS = [
    "SECRET", "TOKEN", "PASSWORD", "PRIVATE_KEY", "API_KEY"
]


def get_authorized_credentials(project_root: Path) -> set[str]:
    """從 purpose.md 讀取 required_credentials，允許這些 key 傳給子程序。

    purpose.md 格式：
        required_credentials:
          - AWS_ACCESS_KEY_ID
          - AWS_SECRET_ACCESS_KEY
    """
    purpose = project_root / ".agent" / "purpose.md"
    if not purpose.exists():
        return set()
    authorized: set[str] = set()
    in_section = False
    for line in purpose.read_text(encoding="utf-8").splitlines():
        if line.strip() == "required_credentials:":
            in_section = True
            continue
        if in_section:
            if line.startswith("  - "):
                key = line.strip().lstrip("- ").strip()
                if key in os.environ:
                    authorized.add(key)
            elif not line.startswith(" "):
                break
    return authorized


def _build_safe_env(authorized: set[str]) -> dict[str, str]:
    """複製環境變數，刪除憑證類 key，保留明確授權的。"""
    return {
        k: v for k, v in os.environ.items()
        if k in authorized or (
            k not in _CREDENTIAL_BLACKLIST_EXACT and
            not any(kw in k.upper() for kw in _CREDENTIAL_BLACKLIST_KEYWORDS)
        )
    }


def safe_subprocess(
    cmd: list[str],
    cwd: Path,
    timeout: int = 300,
    authorized_credentials: set[str] | None = None,
) -> subprocess.CompletedProcess:
    """安全子程序執行。

    - shell=False 強制
    - 環境變數黑名單，刪除 AWS/GitHub/OpenAI 等憑證
    - purpose.md 宣告的 required_credentials 不刪除
    - Unix：os.setpgrp() 建立 Process Group，timeout 後 killpg 廣播殺孤兒
    - Windows：Job Object KILL_ON_JOB_CLOSE 防止孤兒行程
    """
    safe_env = _build_safe_env(authorized_credentials or set())

    if sys.platform == "win32":
        return _safe_subprocess_windows(cmd, cwd, timeout, safe_env)
    return _safe_subprocess_unix(cmd, cwd, timeout, safe_env)


def _safe_subprocess_unix(
    cmd: list[str], cwd: Path, timeout: int, env: dict
) -> subprocess.CompletedProcess:
    try:
        import resource
        def _preexec():
            os.setpgrp()
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
            resource.setrlimit(resource.RLIMIT_CPU, (600, 600))
        preexec_fn = _preexec
    except ImportError:
        preexec_fn = os.setpgrp

    process = subprocess.Popen(
        cmd, cwd=str(cwd), shell=False, env=env,
        preexec_fn=preexec_fn,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        process.wait()
        raise


def _safe_subprocess_windows(
    cmd: list[str], cwd: Path, timeout: int, env: dict
) -> subprocess.CompletedProcess:
    try:
        import ctypes
        JOB_KILL_ON_CLOSE = 0x2000
        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if job:
            info = ctypes.create_string_buffer(32)
            ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32))[5] = JOB_KILL_ON_CLOSE
            kernel32.SetInformationJobObject(job, 9, info, ctypes.sizeof(info))
    except Exception:
        job = None

    process = subprocess.Popen(
        cmd, cwd=str(cwd), shell=False, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    if job:
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, process.pid)
            ctypes.windll.kernel32.AssignProcessToJobObject(job, handle)
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass

    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        if job:
            try:
                ctypes.windll.kernel32.CloseHandle(job)
            except Exception:
                pass


# ── SessionGuard ──────────────────────────────────────────────────────────────

MAX_TURNS_DEFAULT = 50
MAX_TURNS_MINIMUM = 5
MAX_TURNS_MAXIMUM = 500
MAX_TOKENS_PER_SESSION = 2_000_000


class SessionGuard:
    """Session 級別的用量守衛，orchestrator 初始化時建立一個實例。

    自訂 turns 優先順序：
      1. purpose.md 的 max_turns（最優先）
      2. UI 傳入的 ui_max_turns
      3. 預設值 MAX_TURNS_DEFAULT

    有效範圍強制在 MAX_TURNS_MINIMUM ~ MAX_TURNS_MAXIMUM 之間。
    """

    def __init__(
        self,
        max_turns: int = MAX_TURNS_DEFAULT,
        max_tokens: int = MAX_TOKENS_PER_SESSION,
        ui_update_callback=None,
    ):
        self.turns = 0
        self.tokens = 0
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.ui_update_callback = ui_update_callback

    @classmethod
    def from_purpose(
        cls,
        project_root: Path,
        ui_max_turns: int | None = None,
        ui_update_callback=None,
    ) -> "SessionGuard":
        """從 purpose.md 讀取 max_turns，套用合法範圍限制後建立實例。"""
        max_turns = ui_max_turns if ui_max_turns is not None else MAX_TURNS_DEFAULT

        purpose_file = project_root / ".agent" / "purpose.md"
        if purpose_file.exists():
            for line in purpose_file.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("max_turns:"):
                    try:
                        max_turns = int(line.split(":", 1)[1].strip())
                        break
                    except ValueError:
                        pass  # 格式錯誤，使用現有值

        # 強制合法範圍
        max_turns = max(MAX_TURNS_MINIMUM, min(max_turns, MAX_TURNS_MAXIMUM))
        return cls(max_turns=max_turns, ui_update_callback=ui_update_callback)

    def check_and_increment(self, tokens_this_turn: int = 0) -> None:
        """每輪 do() 開始前呼叫。超限拋 RuntimeError，並更新 UI 進度。"""
        self.turns += 1
        self.tokens += tokens_this_turn

        if self.ui_update_callback:
            self.ui_update_callback(
                turns=self.turns,
                max_turns=self.max_turns,
                tokens=self.tokens,
                max_tokens=self.max_tokens,
            )

        if self.turns > self.max_turns:
            raise SessionLimitExceeded(
                f"Session 超過最大輪數限制（{self.max_turns} 輪）。"
                f"如需繼續，請在 purpose.md 設定 max_turns: N 後重新啟動。"
            )
        if self.tokens > self.max_tokens:
            raise SessionLimitExceeded(
                f"Session token 用量超過 {self.max_tokens:,}，已強制停止。"
            )

    @property
    def progress_text(self) -> str:
        return (
            f"輪數：{self.turns} / {self.max_turns}  "
            f"Token：{self.tokens:,} / {self.max_tokens:,}"
        )

    @property
    def is_near_limit(self) -> bool:
        """80% 時回傳 True，供 UI 顯示警告。"""
        return self.turns >= self.max_turns * 0.8


# ── Truncated feedback ────────────────────────────────────────────────────────

from dataclasses import dataclass
from typing import Literal


@dataclass
class ScanResult:
    safe: bool
    reason: str | None
    matched_pattern: str | None
    severity: Literal["hard_block", "confirm_required", "log_only"]
    line: int | None = None  # AST 掃描時填入行號，供 Truncated Feedback 使用


_FIX_HINTS: dict[str, str] = {
    "__import__":           "移除動態 import，改用直接 import 語句",
    "動態程式碼執行":        "移除 eval/exec，改用明確的函數呼叫",
    "sudo":                 "移除 sudo，改用最小權限設計",
    "runas":                "移除 runas，說明為何需要提權讓使用者決定",
    "資料傳輸模式":          "分離網路呼叫與本地 IO，或確認資料流向",
    "專案敏感路徑（硬封鎖）": "此路徑禁止自動修改，請告知使用者手動處理",
    "建置設定檔（需確認）":   "等待使用者確認後再繼續",
    "設定檔可執行欄位含提權模式": "移除 scripts 內的提權指令",
}

_confirm_counter: dict[tuple[str, str], int] = {}


def make_truncated_feedback(
    results: list[ScanResult],
    target_path: Path,
    project_root: Path,
) -> str:
    """將 ScanResult 轉為精簡錯誤訊息（< 200 token），含行號。

    回傳空字串表示無問題。
    """
    if not results:
        return ""

    _priority = {"hard_block": 3, "confirm_required": 2, "log_only": 1}
    worst = max(results, key=lambda r: _priority.get(r.severity, 0))

    try:
        rel_path = target_path.relative_to(project_root)
    except ValueError:
        rel_path = target_path

    location = f"{rel_path}:{worst.line}" if worst.line else str(rel_path)
    hint = _FIX_HINTS.get(worst.reason or "", "請修正後重試")

    return (
        f"SECURITY EXCEPTION [{worst.severity.upper()}]\n"
        f"檔案：{location}\n"
        f"原因：{worst.reason}（{worst.matched_pattern}）\n"
        f"指令：{hint}"
    )


def record_confirm(target_path: Path, reason: str) -> bool:
    """記錄 confirm_required 次數。

    同路徑 + 同 reason 連續 3 次 → 回傳 True（應升級為 hard_block）。
    不同 reason 不計入（換方法不算惡意繞過）。
    """
    key = (str(target_path), reason)
    _confirm_counter[key] = _confirm_counter.get(key, 0) + 1
    return _confirm_counter[key] >= 3


# ── Backup / restore (Rule 1) ─────────────────────────────────────────────────


def backup_before_do(files: list[Path], project_root: Path) -> dict[Path, Path]:
    """do() 執行前備份即將被修改的檔案。

    回傳 {原始路徑: 備份路徑} 對應表，供 restore_from_backup 使用。
    備份存在 .agent/tmp/backup/，不使用 git checkout 避免蓋掉未 commit 的心血。
    """
    import shutil
    backup_dir = project_root / ".agent" / "tmp" / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[Path, Path] = {}
    for f in files:
        if f.exists():
            rel = f.resolve().relative_to(project_root.resolve())
            safe_name = str(rel).replace("/", "_").replace("\\", "_")
            bak = backup_dir / safe_name
            shutil.copy2(f, bak)
            mapping[f] = bak
    return mapping


def restore_from_backup(mapping: dict[Path, Path]) -> None:
    """從備份還原檔案。

    範圍外寫入時呼叫，用自己的備份還原，
    不呼叫 git checkout（避免蓋掉使用者未 commit 的心血）。
    """
    import shutil
    for original, backup in mapping.items():
        if backup.exists():
            shutil.copy2(backup, original)


# ── Package install interception (Rule 26) ────────────────────────────────────

import difflib

_PACKAGE_INSTALL_PATTERNS = [
    (r"^pip\d*\s+install\b", "pip"),
    (r"^pip\d*\s+.*-r\s+", "pip -r"),
    (r"^poetry\s+add\b", "poetry"),
    (r"^uv\s+add\b", "uv"),
    (r"^npm\s+(install|i)\b", "npm"),
    (r"^yarn\s+add\b", "yarn"),
    (r"^pnpm\s+add\b", "pnpm"),
    (r"^cargo\s+add\b", "cargo"),
    (r"^go\s+get\b", "go get"),
]

_KNOWN_PACKAGES = {
    "requests", "numpy", "pandas", "flask", "django", "fastapi",
    "beautifulsoup4", "sqlalchemy", "pytest", "pydantic", "httpx",
    "express", "react", "lodash", "axios", "typescript",
}


def check_package_install(cmd: list[str]) -> tuple[bool, list[str]]:
    """偵測是否為套件安裝指令。

    回傳 (is_install, packages_to_install)
    """
    if not cmd:
        return False, []
    cmd_str = " ".join(cmd)
    for pattern, manager in _PACKAGE_INSTALL_PATTERNS:
        if re.search(pattern, cmd_str, re.IGNORECASE):
            packages = _extract_package_names(cmd, manager)
            return True, packages
    return False, []


def _extract_package_names(cmd: list[str], manager: str) -> list[str]:
    """從指令中提取套件名稱，過濾 flags 和設定檔。"""
    packages = [
        arg for arg in cmd[2:]
        if not arg.startswith("-")
        and not arg.endswith(".txt")
        and not arg.endswith(".toml")
    ]
    # pip install -r requirements.txt：讀取檔案列出套件
    if "-r" in cmd:
        idx = cmd.index("-r") + 1
        if idx < len(cmd):
            try:
                req_path = Path(cmd[idx])
                if req_path.exists():
                    packages = [
                        line.strip().split("==")[0].split(">=")[0].split("<=")[0]
                        for line in req_path.read_text().splitlines()
                        if line.strip() and not line.startswith("#")
                    ]
            except Exception:
                pass
    return packages


def check_typosquatting(package_name: str) -> str | None:
    """檢查套件名稱是否與已知套件高度相似（疑似 typosquatting）。

    回傳最接近的已知套件名，或 None 表示無疑慮。
    """
    clean = package_name.split("[")[0].lower()
    if clean in _KNOWN_PACKAGES:
        return None
    matches = difflib.get_close_matches(clean, _KNOWN_PACKAGES, n=1, cutoff=0.85)
    if matches and matches[0] != clean:
        return matches[0]
    return None


# ── Static code scan (Rule 9, simplified) ────────────────────────────────────

_PRIVILEGE_KEYWORDS = [
    # Unix
    r"\bsudo\b", r"\bsetuid\b", r"\bsetgid\b",
    # macOS
    r"osascript", r"AuthorizationExecuteWithPrivileges",
    r"Library/LaunchAgents",
    # Windows
    r"\brunas\b", r"ctypes\.windll", r"ctypes\.cdll",
    r"HKEY_LOCAL_MACHINE", r"\bschtasks\b",
    r"Invoke-Expression", r"\bIEX\b",
    # 排程植入
    r"/etc/cron", r"~/.bashrc", r"~/.profile",
    r"/etc/sudoers",
]

_WINDOWS_EXEC_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".msi", ".scr",
}


def scan_code(code: str, filename: str = "") -> list[str]:
    """掃描程式碼，回傳警告訊息清單（空清單表示安全）。

    偵測項目：
    1. exec(base64.b64decode(...)) 混淆執行組合
    2. 提權關鍵字
    3. Windows 可執行檔副檔名（從 filename 判斷）

    只保留高效益低誤報的項目，不做完整 AST 掃描。
    """
    warnings: list[str] = []

    # 1MB 上限，防止 AST DoS
    if len(code.encode("utf-8")) > 1 * 1024 * 1024:
        return ["程式碼體積超過 1MB，拒絕掃描"]

    # 偵測 1：exec(b64decode(...)) 混淆組合（AST）
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if (isinstance(node.func, ast.Name) and
                        node.func.id in ("exec", "eval", "compile")):
                    for arg in node.args:
                        if (isinstance(arg, ast.Call) and
                                isinstance(arg.func, ast.Attribute) and
                                arg.func.attr in ("b64decode", "b32decode", "decompress")):
                            lineno = getattr(node, "lineno", "?")
                            warnings.append(
                                f"第 {lineno} 行：exec({arg.func.attr}(...)) "
                                f"疑似混淆執行，請改用明確的函數呼叫"
                            )
    except SyntaxError:
        pass  # 非 Python 檔案或語法錯誤，跳過 AST

    # 偵測 2：提權關鍵字（Regex）
    for pattern in _PRIVILEGE_KEYWORDS:
        match = re.search(pattern, code, re.IGNORECASE)
        if match:
            warnings.append(
                f"偵測到可能的提權操作：{match.group()!r}，請確認是否必要"
            )
            break  # 同類只回報一次

    # 偵測 3：Windows 可執行檔副檔名
    if filename:
        ext = Path(filename).suffix.lower()
        if ext in _WINDOWS_EXEC_EXTENSIONS:
            warnings.append(
                f"即將生成可執行檔：{filename}，請確認用途"
            )

    return warnings


# ── ApprovedPaths session management (Rule 3) ─────────────────────────────────


class ApprovedPaths:
    """Session 內的已批准路徑管理。

    - 只存在記憶體，不寫磁碟，不跨 session
    - 子目錄繼承：批准 /foo/bar 後，/foo/bar/baz 自動通過
    - 支援批量預批准（啟動時一次設定，不再打斷）
    """

    BATCH_OPTIONS = {
        "package_install",   # pip/npm 套件安裝
        "build_config",      # pyproject.toml/Makefile 等建置設定
        "write_outside",     # 寫入 project_root 外的路徑
    }

    def __init__(self) -> None:
        self._paths: set[Path] = set()
        self.batch_approved: set[str] = set()

    def approve(self, path: Path) -> None:
        """批准單一路徑，子目錄自動繼承。"""
        self._paths.add(path.resolve())

    def approve_batch(self, operation_type: str) -> None:
        """批量批准某類操作，整個 session 不再詢問。

        operation_type: "package_install" | "build_config" | "write_outside"
        """
        self.batch_approved.add(operation_type)

    def is_approved(self, path: Path) -> bool:
        """檢查路徑是否已批准（含子目錄繼承）。"""
        resolved = path.resolve()
        return any(
            resolved == approved or resolved.is_relative_to(approved)
            for approved in self._paths
        )

    def is_batch_approved(self, operation_type: str) -> bool:
        """檢查某類操作是否已批量批准。"""
        return operation_type in self.batch_approved

    def clear(self) -> None:
        """清除所有批准記錄（session 結束時呼叫）。"""
        self._paths.clear()
        self.batch_approved.clear()
