"""timeline.py - timeline.md maintenance and anomaly detection."""
from __future__ import annotations

import re
from pathlib import Path

from .security import safe_write

_HEADER = "| 輪 | 類型 | 任務 | 結果 | 決策 | tokens |\n|----|------|------|------|------|--------|\n"


def append_round(
    timeline_path: Path,
    round_num: int,
    round_type: str,
    task: str,
    result: str,
    decision: str,
    tokens: int = 0,
) -> None:
    """Append one row to timeline.md, creating file if needed."""
    if not timeline_path.exists():
        content = "# Forge Timeline\n\n" + _HEADER
    else:
        content = timeline_path.read_text(encoding="utf-8", errors="replace").replace(
            "\r\n", "\n"
        )
        if "| 輪 |" not in content:
            content += "\n" + _HEADER

    # Escape pipe characters in cell values
    def _esc(s: str) -> str:
        return str(s).replace("|", "｜").replace("\n", " ")

    row = (
        f"| {round_num:03d} | {_esc(round_type)} | {_esc(task)} | "
        f"{_esc(result)} | {_esc(decision)} | {tokens:,} |\n"
    )
    content += row
    safe_write(timeline_path, content)


def detect_anomalies(timeline_path: Path) -> list[str]:
    """Scan timeline.md for suspicious patterns. Return list of anomaly descriptions."""
    if not timeline_path.exists():
        return []

    text = timeline_path.read_text(encoding="utf-8", errors="replace")
    rows = _parse_rows(text)
    anomalies: list[str] = []

    if not rows:
        return anomalies

    # 1. Same file failing 3+ consecutive rounds
    fail_streak: dict[str, int] = {}
    prev_tasks: list[str] = []
    consecutive_fails = 0
    for row in rows:
        result = row.get("result", "")
        task = row.get("task", "")
        if "FAIL" in result or "❌" in result or "失敗" in result:
            consecutive_fails += 1
            fail_streak[task] = fail_streak.get(task, 0) + 1
        else:
            consecutive_fails = 0
            fail_streak = {}
        prev_tasks.append(task)

    for task, count in fail_streak.items():
        if count >= 3:
            anomalies.append(f"⚠️ 連續失敗 {count} 輪: {task}")

    # 2. think() reversing its own previous decisions
    decisions = [r.get("decision", "") for r in rows]
    for i in range(2, len(decisions)):
        d = decisions[i].lower()
        prev_d = decisions[i - 1].lower()
        if ("繼續" in d or "keep" in d) and ("停止" in prev_d or "回退" in prev_d):
            anomalies.append(
                f"⚠️ 第 {rows[i].get('round', i)} 輪: think() 推翻了上一輪決策"
            )
        if ("停止" in d or "回退" in d) and ("繼續" in prev_d or "keep" in prev_d):
            anomalies.append(
                f"⚠️ 第 {rows[i].get('round', i)} 輪: think() 推翻了上一輪決策"
            )

    # 3. 5+ rounds without progress (no task change)
    if len(rows) >= 5:
        last5_tasks = [r.get("task", "") for r in rows[-5:]]
        if len(set(last5_tasks)) == 1:
            anomalies.append(f"⚠️ 連續 5 輪沒有進度: 任務停滯在「{last5_tasks[0]}」")

    # 4. do() modified purpose.md (not expected)
    for row in rows:
        if row.get("type", "") == "do" and "purpose" in row.get("task", "").lower():
            anomalies.append(
                f"⚠️ 第 {row.get('round', '?')} 輪: do() 疑似修改了 purpose.md"
            )

    return anomalies


def _parse_rows(text: str) -> list[dict]:
    """Parse markdown table rows from timeline text."""
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|-"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 5:
            continue
        # Skip header row
        if parts[0] in ("輪", "round", "#"):
            continue
        try:
            rows.append(
                {
                    "round": parts[0],
                    "type": parts[1],
                    "task": parts[2],
                    "result": parts[3],
                    "decision": parts[4],
                    "tokens": parts[5] if len(parts) > 5 else "0",
                }
            )
        except IndexError:
            continue
    return rows
