#!/usr/bin/env python3
"""PostToolUse(Edit|Write|MultiEdit): мгновенная проверка синтаксиса правленого файла."""
import glob
import os
import shutil
import subprocess
import tempfile

from _common import ROOT, block, read_input

ti = read_input().get("tool_input") or {}
path = ti.get("file_path") or ""
if not path or not os.path.exists(path):
    raise SystemExit(0)


def node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    cands = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/node")))
    return cands[-1] if cands else None


def run(args: list[str]) -> str | None:
    r = subprocess.run(args, capture_output=True, text=True, timeout=30, cwd=ROOT)
    return None if r.returncode == 0 else (r.stderr or r.stdout).strip()[-1500:]


def check_workflow(n: str) -> str | None:
    # Скрипт воркфлоу исполняется внутри async-функции: return и await на верхнем уровне законны.
    src = open(path, encoding="utf-8").read().replace("export const meta", "const meta", 1)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write("(async () => {\n" + src + "\n})\n")
    try:
        return run([n, "--check", f.name])
    finally:
        os.unlink(f.name)


err = None
if path.endswith((".js", ".mjs")) and (n := node()):
    err = check_workflow(n) if "/.claude/workflows/" in path else run([n, "--check", path])
elif path.endswith(".py"):
    err = run(["python3", "-m", "py_compile", path])
elif path.endswith(".css"):
    text = open(path, encoding="utf-8").read()
    if text.count("{") != text.count("}"):
        err = f"несбалансированные фигурные скобки: {{ {text.count('{')} против }} {text.count('}')}"
elif path.endswith(".json"):
    err = run(["python3", "-m", "json.tool", path])

if err:
    block(f"Синтаксическая ошибка в {os.path.relpath(path, ROOT)} после правки:\n{err}")
