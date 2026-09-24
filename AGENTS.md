# выдра — правила проекта

Загрузчик видео (YouTube / TikTok / Instagram) с веб-интерфейсом и консолью. Python 3.13 + uv, FastAPI, yt-dlp, ffmpeg.
Состояние — `HANDOFF.md`, план и роли агентов — `docs/AI-WORKFLOW.md`, цели спринта — `docs/sprint/GOALS.md`.
Запуск спринта — `/ui-sprint`.

## Приоритеты (решение пользователя, не менять без него)
1. Идеальный UI/UX — **только ПК и только тёмная тема** — и консольная выдра, надёжная в WSL и на macOS.
2. Тёмная тема — чёрная дыра, как в **KOCMOC / KOCMOC UNLEASHED** (`vydra/static/cosmos.js`).
3. Телефон, светлая тема, PowerShell и нативная Windows — потом.

## Команды
- Тесты: `uv run pytest -q` (быстрые, без сети); живые загрузки: `uv run pytest -m live`.
- Серверы для разработки и e2e — скилл `dev-server`; интерфейс глазами — скилл `ui-rig` (`python3 tools/ui_rig.py`).
- Статус спринта: `python3 tools/sprint.py status`.

## Правила
- Зоны: `vydra/static/**` — UI-агенты (`vydra-ui`, `vydra-mech`); `vydra/*.py`, `tests/**`, `install.*` — только
  `vydra-cli` (в своём git worktree); `docs/design/**` — `vydra-designer`; `docs/sprint/GOALS.md` — только дирижёр.
  Границы держат хуки (`.claude/hooks/`).
- UI-регионы на этапе S3 работают в одном дереве: свой CSS-файл — свободно, общие файлы — только Edit и только свои
  секции, фундамент заморожен (`docs/sprint/REGIONS.md`, скилл `region-pass`).
- Дизайнерские решения (композиция, цвет, движение, типографика) — только `vydra-designer`.
- Перед коммитом: `uv run pytest -q` зелёный, кадры стенда просмотрены глазами. Коммитит дирижёр; пуш — только после
  явного «да» пользователя.
- Никакой атрибуции ассистента в коммитах, README и PR. Не коммитить `.env*`, `cookies.txt`, токены.
- В этом WSL (mirrored networking + firewall) `connect()` к закрытому localhost-порту висит минутами: всё сетевое —
  с таймаутом, занятость порта — через `bind()`.
- `pkill -f` с шаблоном, совпадающим с командой твоего же шелла, убивает шелл: PID ищи через `ss -ltnp`.
