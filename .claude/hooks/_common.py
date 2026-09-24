"""Общее для хуков выдры: корень проекта, состояние спринта, цели."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
STATE = ROOT / ".claude" / "state"
GOALS = ROOT / "docs" / "sprint" / "GOALS.md"


def read_input() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def sprint_active() -> bool:
    return (STATE / "sprint-active").exists() and not (STATE / "pause").exists()


def open_goals() -> list[str]:
    if not GOALS.exists():
        return []
    return [m.group(1).strip() for m in re.finditer(r"^- \[ \] (.+)$", GOALS.read_text(encoding="utf-8"), re.M)]


def block(reason: str) -> None:
    """PreToolUse/PostToolUse: код 2, текст уходит модели."""
    print(reason, file=sys.stderr)
    sys.exit(2)


def rel(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except Exception:
        return path
