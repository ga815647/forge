"""Helpers for formatting detailed live log output."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


def make_live_logger(
    on_log: Callable[[str], None] | None,
    scope: str,
) -> Callable[[str], None]:
    """Return a logger that prefixes messages with time and scope."""
    prefix_scope = scope.strip() or "log"

    def log(message: str) -> None:
        if on_log is None:
            return

        stamp = datetime.now().strftime("%H:%M:%S")
        prefix = f"[{stamp}] [{prefix_scope}] "
        continuation = " " * len(prefix)
        lines = str(message).splitlines() or [""]
        rendered = [prefix + lines[0]]
        rendered.extend(continuation + line for line in lines[1:])
        on_log("\n".join(rendered))

    return log


def summarize_text(text: str, limit: int = 120) -> str:
    """Collapse whitespace and trim long text for compact logging."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)].rstrip() + "..."


def summarize_paths(
    paths: Iterable[Path | str],
    *,
    base: Path | None = None,
    limit: int = 5,
) -> str:
    """Render a compact file list for logs."""
    items: list[str] = []
    total = 0
    for raw in paths:
        total += 1
        if len(items) >= limit:
            continue
        path = Path(raw) if not isinstance(raw, Path) else raw
        try:
            items.append(str(path.relative_to(base)) if base else str(path))
        except ValueError:
            items.append(str(path))

    if not items:
        return "(none)"
    if total > limit:
        return f"{', '.join(items)} (+{total - limit} more)"
    return ", ".join(items)
