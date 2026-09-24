# выдра — правила проекта

Загрузчик видео (YouTube / TikTok / Instagram) с веб-интерфейсом и консолью. Python 3.13 + uv, FastAPI, yt-dlp, ffmpeg.
Состояние и план — в `HANDOFF.md`, порядок работы агентов — в `docs/AI-WORKFLOW.md`.

## Приоритеты (решение пользователя, не менять без него)
1. Нормальный UI и стабильная работа в WSL. Всё остальное потом.
2. Тёмная тема — чёрная дыра, как в KOCMOC (`vydra/static/cosmos.js`).
3. Светлая тема — в самом конце.

## Команды
- Тесты: `uv run pytest -q` (быстрые, без сети); живые загрузки: `uv run pytest -m live`.
- Сервер из исходников: `uv run vydra ui --port 8799 --no-browser` → http://localhost:8799
- Скриншоты (из WSL через Edge Windows):
  `"/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu --window-size=1440,1300 --timeout=4000 --screenshot='C:\Users\Ihor\AppData\Local\Temp\vd.png' "http://localhost:8799/?motion=0"`
  Headless Edge не бывает уже 500 px — «телефон» снимай на 520. Без `?motion=0` заголовок ещё размыт анимацией.
  `--virtual-time-budget` не использовать: зависает из-за SSE.

## Правила
- Разделение файлов: `vydra/static/**` — только UI-агенты; `vydra/*.py`, `install.*`, `tests/**` — только бэкенд/WSL-агент.
  Два агента в одних файлах одновременно — запрещено. Параллельные агенты работают в отдельных git worktree.
- Дизайнерские решения (композиция, цвет, движение, типографика) — только агент `vydra-designer`.
- Перед коммитом: `uv run pytest -q` зелёный, скриншоты 1440 и 520 просмотрены глазами.
- Никакой атрибуции ассистента в коммитах, README и PR. Не коммитить `.env*`, `cookies.txt`, токены.
- `pkill -f` с шаблоном, который совпадает с командой твоего же шелла, убивает твой шелл: ищи PID через `ss -ltnp`.
