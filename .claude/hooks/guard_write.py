#!/usr/bin/env python3
"""PreToolUse(Edit|Write|MultiEdit): секреты и границы этапа 1."""
import re

from _common import STATE, block, read_input, rel, sprint_active

data = read_input()
ti = data.get("tool_input") or {}
path = rel(ti.get("file_path") or ti.get("notebook_path") or "")

if re.search(r"(^|/)(\.env[^/]*|cookies\.txt|\.credentials\.json|[^/]*oauth-token[^/]*)$", path):
    block(f"{path} — секрет, его не пишем.")

if sprint_active():
    backend = re.match(r"^(vydra/[^/]+\.py|install\.(sh|ps1)|pyproject\.toml|uv\.lock)$", path)
    if backend and not (STATE / "allow-backend").exists():
        block(f"Этап 1 меняет только интерфейс, а {path} — бэкенд. Если баг бэкенда мешает UI, это решает дирижёр: "
              "он пишет причину в .claude/state/allow-backend, делает минимальный фикс отдельным коммитом и удаляет файл.")

    owner_file = STATE / "static-owner"
    if path.startswith("vydra/static/") and owner_file.exists():
        owner = owner_file.read_text(encoding="utf-8").strip()
        me = data.get("agent_type") or "main"
        if owner and me != owner:
            block(f"Сейчас vydra/static/ правит только «{owner}» (.claude/state/static-owner), а ты — «{me}». "
                  "Два агента в одних файлах затирают друг друга. Верни задачу дирижёру.")
