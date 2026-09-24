---
name: ship
description: Выпуск выдры в конце спринта (S5) — слияние CLI-ветки, финальные ворота, галерея, локальный коммит без атрибуции, HANDOFF; пуш только после явного «да» пользователя. Использовать, когда цели S0–S4 закрыты.
---

# Выпуск

1. **CLI-трек.** Агент ещё работает — SendMessage: «заверши: закоммить, обнови docs/cli/PROGRESS.md и отчитайся»,
   дождись отчёта. Затем в основном дереве: `git merge --no-ff <ветка CLI>`; конфликты решай по зонам (бэкенд — версия
   CLI-ветки, `vydra/static` — версия main). Отметь C1–C4 по доказательствам из отчёта и `docs/cli/MATRIX.md`.
   Перезапусти dev-сервер :8799 (бэкенд поменялся).
2. **Ворота** — все должны пройти, иначе не выпускаем:
   - `uv run pytest -q` — зелёный;
   - `python3 tools/ui_rig.py --label final --viewports all`: 0 ошибок консоли, 0 вылетов, 0 axe serious/critical;
   - `python3 tools/e2e.py --label final --compare .sprint/baseline/e2e.json` — совпадает с базовым;
   - секреты — только в staged-диффе, сами секретные файлы не открывать:
     ```sh
     git diff --cached | grep -nE 'ghp_[A-Za-z0-9]{30,}|github_pat_|AKIA[0-9A-Z]{16}|PRIVATE KEY|sessionid=|sk-ant-' && echo "STOP: похоже на секрет"
     git diff --cached --name-only | grep -E '(^|/)\.env|cookies\.txt|\.credentials\.json|oauth-token' && echo "STOP: запрещённый файл"
     ```
3. **Галерея.** Пары «до/после» из подписи арт-директора (G13) → `docs/screenshots/` (`before-*.png`, `after-*.png`),
   старые кадры кинотеатра удалить; README ссылается только на существующие картинки.
4. **Коммит** — локально: осмысленное сообщение по-русски, **без** Co-Authored-By, «Generated with» и упоминаний
   ассистента (правило пользователя; хук проверяет). Автор — как в `git log -1`.
5. **HANDOFF.md** — что сделано, что осталось (ссылка на `docs/ROADMAP.md`). В GOALS.md закрыть G14 с хешем коммита.
   `python3 tools/sprint.py status` → `SPRINT: ALL DONE`.
6. **Пользователю** — коротко: что изменилось в интерфейсе и в консоли (3–5 пунктов), путь к галерее, итог матрицы
   загрузок, и вопрос: «пушим в main?».
7. **Пуш — только после явного «да»:** `python3 tools/sprint.py push-ok` → `git push origin main` (без force).
   Затем готовый текст для друга:
   > Закрой окно выдры, выполни `curl -LsSf https://raw.githubusercontent.com/Ihor-Zakharov/vydra/main/install.sh | sh`,
   > запусти ярлык «Выдра» и нажми в браузере Ctrl+F5.
8. **Уборка:** `python3 tools/sprint.py finish`; закрыть стендовый Edge (CDP `Browser.close`) и dev-серверы по PID;
   `git worktree list` — worktree CLI-трека после слияния удалить (`git worktree remove <путь>`).
