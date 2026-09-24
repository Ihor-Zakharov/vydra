---
name: dev-server
description: Как поднимать, проверять и гасить серверы выдры в WSL для разработки и e2e — порты, изолированные библиотеки, запуск из worktree. Использовать перед любой работой, где нужен запущенный интерфейс или API.
---

# Серверы выдры для разработки

| Порт | Зачем | Библиотека |
|---|---|---|
| 8799 | дизайн и стенд, смотрит пользователь | настоящая библиотека пользователя, **только чтение**: ничего не скачивать, не удалять, не перемещать |
| 8798 | e2e: настоящие загрузки и смена папки | `.sprint/e2e-lib` (одноразовая) |
| 8797 | CLI-трек (`vydra-cli`, из своего worktree) | `.sprint/cli-lib` в worktree |
| 8765 | установленная выдра пользователя | **не трогать** |

## Запуск
Команду запускай в фоне (`run_in_background`), а не через `&`. Для разработки uvicorn запускается напрямую:

```sh
cd ~/projects/video-downloader
# dev
VD_PORT=8799 uv run uvicorn vydra.main:create_app --factory --host 127.0.0.1 --port 8799 --log-level warning
# e2e
mkdir -p .sprint/e2e-lib .sprint/e2e-work
VD_PORT=8798 VD_LIBRARY_DIR=$PWD/.sprint/e2e-lib VD_WORK_DIR=$PWD/.sprint/e2e-work \
  uv run uvicorn vydra.main:create_app --factory --host 127.0.0.1 --port 8798 --log-level warning
```
Статика берётся с диска: после правки `vydra/static/**` перезапуск не нужен — перезагрузи страницу.
Правка `vydra/*.py` требует перезапуска. `vydra ui` больше не зависает на проверке порта (`bc4f5c9`).

## Проверка
```sh
curl -s -m5 -o /dev/null -w "%{http_code}\n" localhost:8799/     # 200
curl -s -m5 localhost:8799/api/health                             # {"ok":true,...}
ss -ltnp | grep -E ':(8799|8798|8797|9333)\b'
```
Всегда с `-m`: в mirrored-режиме WSL запрос к закрытому порту висит минутами.
Сервер уже слушает порт — второй не поднимай, используй работающий.

## Остановка
Только по PID: `ss -ltnp | grep :8798` → `kill <PID>`. `pkill -f` запрещён хуком: он цепляет твой же шелл.
