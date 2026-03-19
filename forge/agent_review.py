"""agent_review.py - auto_review() and quick_review() for .agent/*.md files."""
from __future__ import annotations

from pathlib import Path


def auto_review(content: str, engine: str, max_rounds: int = 6) -> tuple[str, bool]:
    """Full ✅⚡🌸 review loop for CRITICAL_FILES.

    Returns (final_content, suggest_real_test).
    """
    from . import agent as _agent  # lazy — avoids circular import
    from . import prompts as _p

    current = content
    for round_num in range(1, max_rounds + 1):
        prompt = _p.review_prompt(current)
        response = _agent.think(prompt, [], engine, Path("."), model="sonnet")

        # Every 3 rounds, slim
        if round_num % 3 == 0:
            slimmed = _agent.think(
                _p.slim_prompt(current), [], engine, Path("."), model="sonnet"
            )
            if slimmed.strip():
                current = slimmed

        if response.strip().startswith("✅"):
            return current, False
        if "建議實測" in response:
            return current, True
        if response.strip().startswith("🌸"):
            slimmed = _agent.think(
                _p.slim_prompt(current), [], engine, Path("."), model="sonnet"
            )
            if slimmed.strip():
                current = slimmed
            continue
        if "⚡" in response:
            lines = response.split("\n")
            in_fix = False
            fixed_lines: list[str] = []
            for line in lines:
                if "⚡" in line:
                    in_fix = True
                    after = line.split("⚡", 1)[1].strip()
                    if after and not after.startswith("→") and not after.startswith("["):
                        fixed_lines.append(after)
                    continue
                if in_fix:
                    fixed_lines.append(line)
            if fixed_lines:
                current = "\n".join(fixed_lines).strip()
            continue

    # Exceeded max rounds → force pass
    return current, True


def quick_review(content: str, engine: str) -> str:
    """Single-pass ✅/⚡ review for NORMAL_FILES. Returns (possibly fixed) content."""
    from . import agent as _agent  # lazy — avoids circular import
    from . import prompts as _p

    prompt = _p.quick_review_prompt(content)
    response = _agent.think(prompt, [], engine, Path("."), model="sonnet")

    if "⚡" in response:
        lines = response.split("\n")
        fixed_lines: list[str] = []
        in_fix = False
        for line in lines:
            if "⚡" in line:
                in_fix = True
                after = line.split("⚡", 1)[1].strip()
                if "→" in after:
                    after = after.split("→", 1)[1].strip()
                if after:
                    fixed_lines.append(after)
                continue
            if in_fix:
                fixed_lines.append(line)
        if fixed_lines:
            return "\n".join(fixed_lines).strip()

    return content
