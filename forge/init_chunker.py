"""init_chunker.py - Large file chunking logic for orchestrator_init."""
from __future__ import annotations

from pathlib import Path

from .security import safe_write


def chunk_file(source: Path, chunks_dir: Path, original_name: str) -> list[str]:
    """Split large file into ~300-line chunks. Returns list of chunk title strings."""
    chunks_dir.mkdir(parents=True, exist_ok=True)
    content = source.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    lines = content.splitlines(keepends=True)
    chunk_size = 300

    split_points = _find_split_points(lines, chunk_size)

    chunks: list[list[str]] = []
    current: list[str] = []
    splits_iter = iter(split_points)
    next_split = next(splits_iter, len(lines))

    for i, line in enumerate(lines):
        current.append(line)
        if i + 1 >= next_split and current:
            chunks.append(current)
            current = []
            next_split = next(splits_iter, len(lines))
    if current:
        chunks.append(current)

    stem = Path(original_name).stem
    titles: list[str] = []
    for idx, chunk_lines in enumerate(chunks):
        chunk_name = f"{stem}_chunk_{idx + 1:03d}.md"
        chunk_path = chunks_dir / chunk_name
        title = _extract_title(chunk_lines)
        header = f"# Chunk {idx + 1}/{len(chunks)}: {original_name}\n## {title}\n\n"
        safe_write(chunk_path, header + "".join(chunk_lines))
        titles.append(f"[{chunk_name}] {title}")

    return titles


def _find_split_points(lines: list[str], chunk_size: int) -> list[int]:
    """Find natural split points (## > ### > blank lines > hard cut)."""
    points: list[int] = []
    i = chunk_size - 1
    while i < len(lines):
        best = i
        for j in range(i, max(i - chunk_size // 2, 0), -1):
            line = lines[j].strip()
            if line.startswith("## "):
                best = j
                break
            if line.startswith("### "):
                best = j
                break
            if line == "" and j < i:
                best = j + 1
                break
        points.append(best)
        i = best + chunk_size
    return points


def _extract_title(chunk_lines: list[str]) -> str:
    """Extract a title from first meaningful content line."""
    for line in chunk_lines[:10]:
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:60]
        if stripped and not stripped.startswith("```"):
            return stripped[:60]
    return "(no title)"
