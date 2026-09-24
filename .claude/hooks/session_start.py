#!/usr/bin/env python3
"""SessionStart: этап, открытые цели и состояние стенда — в контекст с первой секунды."""
import json
import socket

from _common import STATE, open_goals, read_input, sprint_active

read_input()


def up(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


goals = open_goals()
lines = [
    "ВЫДРА — этап 1: идеальный UI/UX, только тёмная тема. Функциональность не меняется.",
    "Правила — AGENTS.md; план сессии — docs/AI-WORKFLOW.md; цели — docs/sprint/GOALS.md; бриф — docs/sprint/BRIEF.md.",
    f"Спринт: {'АКТИВЕН' if sprint_active() else 'не запущен (запуск: /ui-sprint)'}"
    + (" — на паузе (.claude/state/pause)" if (STATE / "pause").exists() else ""),
    f"Открытых целей: {len(goals)}" + (" — " + "; ".join(g.split(':')[0][:60] for g in goals[:12]) if goals else ""),
    f"Сервер dev :8799 — {'работает' if up(8799) else 'не запущен'}; e2e :8798 — {'работает' if up(8798) else 'не запущен'}; "
    f"Edge CDP :9333 — {'работает' if up(9333) else 'не запущен'}.",
]
owner = STATE / "static-owner"
if owner.exists():
    lines.append(f"vydra/static/ сейчас правит: {owner.read_text(encoding='utf-8').strip()}")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}},
                 ensure_ascii=False))
