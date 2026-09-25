#!/usr/bin/env python3
"""PreToolUse(Bash): правила проекта, которые легко нарушить случайно."""
import re

from _common import block, read_input

cmd = (read_input().get("tool_input") or {}).get("command", "")
low = cmd.lower()

# Пути вида .claude/… и /tmp/claude-… — не атрибуция: убираем их перед проверкой текста коммита.
attrib_text = re.sub(r"\S*\.claude\S*|\S*/claude-\S*", " ", low)
if re.search(r"\bgit\s+commit\b", low) and re.search(
        r"co-authored-by|generated with|anthropic|(?<![\w./-])claude(?![\w-])", attrib_text):
    block("Правило пользователя: никакой атрибуции ассистента в коммитах (Co-Authored-By, «Generated with», Claude). "
          "Убери эти строки из сообщения и повтори.")

if re.search(r"\bgit\s+push\b.*(\s--force\b|\s-f\b|--force-with-lease)", low):
    block("Force-push запрещён. Если история разошлась — `git pull --rebase` и обычный push.")

if re.search(r"\bgit\s+add\b.*(\.env\b|cookies\.txt|\.credentials\.json|oauth-token)", low):
    block("Это секрет — его нельзя добавлять в git.")

if re.search(r"\b(pkill|killall)\b", low):
    block("`pkill -f`/`killall` цепляют и твой собственный шелл, и чужие процессы. "
          "Найди PID: `ss -ltnp | grep <порт>` и `kill <PID>`.")

if re.search(r"taskkill(\.exe)?\b.*msedge", low):
    block("Так закроется Edge пользователя. Закрывай стендовый Edge через CDP (`Browser.close`, см. скилл ui-rig).")

if "--virtual-time-budget" in low:
    block("`--virtual-time-budget` зависает из-за SSE. Используй стенд (скилл ui-rig) или `--timeout=4000`.")
