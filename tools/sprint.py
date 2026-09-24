#!/usr/bin/env python3
"""Статус и этапы спринта выдры: status [--short] | begin sN | end | finish | cli <ветка> [id] [путь] | push-ok."""
import json, os, re, socket, subprocess, sys, time
from pathlib import Path

def main_root() -> Path:
    here = Path(__file__).resolve().parents[1]
    try:
        out = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        return Path(out).parent if out else here
    except Exception:
        return here

MAIN = main_root()
STATE = MAIN / ".claude" / "state"
GOALS = Path(os.environ.get("VYDRA_GOALS") or MAIN / "docs" / "sprint" / "GOALS.md")

def busy(port: int) -> bool:  # bind, не connect: в mirrored-WSL connect к закрытому порту висит
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port)); return False
        except OSError:
            return True

def status(short: bool) -> None:
    text = GOALS.read_text(encoding="utf-8") if GOALS.exists() else ""
    sections, cur = [], None
    for line in text.splitlines():
        if m := re.match(r"^## (S\d|CLI)\b", line):
            cur = [m.group(1), [], []]; sections.append(cur)
        elif cur and (m := re.match(r"^- \[( |x)\] ([GC]\d+)", line)):
            (cur[2] if m.group(1) == "x" else cur[1]).append(m.group(2))
    stage = (STATE / "stage").read_text().strip() if (STATE / "stage").exists() else "—"
    print(f"Этап: {stage}{' (параллельный режим)' if (STATE / 'parallel').exists() else ''}")
    for name, open_, done in sections:
        print(f"  {name}: {len(done)}/{len(open_) + len(done)}" + (f" открыто {' '.join(open_)}" if open_ else " ✓"))
    hb = STATE / "cli-heartbeat"
    cli = json.loads((STATE / "cli.json").read_text()) if (STATE / "cli.json").exists() else None
    age = f"{(time.time() - hb.stat().st_mtime) / 60:.0f} мин назад" if hb.exists() else "нет"
    print(f"CLI-трек: {'ветка ' + cli.get('branch', '?') if cli else 'не запущен'}; пульс: {age}")
    if not short:
        print("Серверы: " + ", ".join(f"{p} {'занят' if busy(p) else 'свободен'}" for p in (8799, 8798, 8797, 9333)))
        for log in ("gate-warnings.log", "hook-errors.log"):
            if (STATE / log).exists():
                print(f"{log}:\n" + "".join((STATE / log).read_text().splitlines(True)[-5:]))
    open_all = sum(len(s[1]) for s in sections)
    nxt = next((s[0] for s in sections if s[0] != "CLI" and s[1]), "S5")
    print("SPRINT: ALL DONE" if sections and not open_all else f"SPRINT: OPEN {open_all} (next: {nxt})")

def main() -> None:
    a = sys.argv[1:] or ["status"]
    STATE.mkdir(parents=True, exist_ok=True)
    cmd = a[0]
    if cmd == "status":
        status("--short" in a)
    elif cmd == "begin" and len(a) > 1:
        (STATE / "stage").write_text(a[1] + "\n"); (STATE / "sprint-active").write_text("1\n")
        p = STATE / "parallel"
        p.write_text("1\n") if a[1] == "s3" else p.unlink(missing_ok=True)
        print(f"этап {a[1]} начат")
    elif cmd == "end":
        (STATE / "parallel").unlink(missing_ok=True); print("параллельный режим снят")
    elif cmd == "finish":
        for n in ("stage", "sprint-active", "parallel", "push-ok", "allow-backend"):
            (STATE / n).unlink(missing_ok=True)
        print("спринт закрыт")
    elif cmd == "cli" and len(a) > 1:
        (STATE / "cli.json").write_text(json.dumps({"branch": a[1], "agent": a[2] if len(a) > 2 else "",
                                                    "path": a[3] if len(a) > 3 else ""}, ensure_ascii=False))
        print("CLI-трек записан")
    elif cmd == "push-ok":
        (STATE / "push-ok").write_text("user said yes\n"); print("пуш разрешён")
    else:
        print(__doc__); sys.exit(2)

if __name__ == "__main__":
    main()
