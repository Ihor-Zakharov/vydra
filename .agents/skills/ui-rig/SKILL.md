---
name: ui-rig
description: Как смотреть на интерфейс выдры глазами — Edge на Windows через CDP из WSL, стенд tools/ui_rig.py со всеми состояниями, листы-контакты, report.json, черновики CSS и сравнение кадров. Использовать для любых скриншотов, проверки вёрстки, e2e-кликов и замеров.
---

# Стенд интерфейса

## Как это устроено
WSL в режиме mirrored networking: `localhost` у WSL и Windows общий, поэтому Playwright из WSL подключается к Edge
на Windows по CDP. Локальный Chromium в WSL не запустится (нет libnss3, sudo нет) — и не нужен.
Внимание: в этом режиме `connect()` к закрытому localhost-порту висит минутами — всегда ставь таймауты.

## 1. Edge — один на всех, порт 9333
```sh
curl -s -m2 localhost:9333/json/version >/dev/null || \
"/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu \
  --remote-debugging-port=9333 --user-data-dir='C:\Users\Ihor\AppData\Local\Temp\vydra-rig-edge' \
  --no-first-run --hide-scrollbars about:blank >/dev/null 2>&1 &
```
Отдельный профиль не мешает браузеру пользователя. `taskkill msedge` запрещён — закроет и его Edge.
В конце сессии дирижёр закрывает стендовый Edge через CDP (`Browser.close`).

## 2. Стенд (после S0)
```sh
python3 tools/ui_rig.py --label <метка> [--states idle,job-*] [--viewports s,m,l|all] [--css .sprint/proto/x.css] \
  [--diff <другая-метка>] [--kbd] [--motion] [--live] [--jobs N]
```
Окна (только ПК): `s` 1366×657, `m` 1536×730 (dpr 1.25), `l` 1920×960, `xl` 2560×1305; по умолчанию `s,m,l`.

- Без `--live` API подменяется фикстурами (`tools/rig_fixtures/`), EventSource поддельный, кадры детерминированы.
- Выход: `.sprint/shots/<метка>/<окно>/<состояние>.png`, `contact-<окно>.png` (лист всех состояний с подписями),
  `report.json` (ошибки консоли, вылет по ширине, обрезанный текст, наложения, axe). Последняя строка — `report: <путь>`.
- `--css` подмешивает черновик поверх живой страницы — так арт-директор сравнивает варианты, не трогая код.
- `--diff` — попиксельное сравнение с другой меткой: доля отличий по кадрам и картинки разницы.
- Метки: `baseline`, `split`, `mech`, `s2-…`, `s3-<регион>-<n>`, `s4-…`. Сравнение «до/после» — два листа рядом.
- Параллельно с другими агентами — `--jobs 2`, своя метка.

## 3. Разовый кадр без стенда (пока стенда нет)
```python
import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://localhost:9333")
        ctx = await b.new_context(viewport={"width": 1920, "height": 960}, color_scheme="dark", service_workers="block")
        page = await ctx.new_page()
        await page.goto("http://localhost:8799/?motion=0", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        await page.screenshot(path=".sprint/shots/adhoc.png")
        await ctx.close()   # закрывай контекст, а не браузер: Edge общий
asyncio.run(main())
```
Запуск: `uv run --with playwright python <скрипт>.py`.

## Правила
- **Смотри на свои кадры:** Read открывает PNG. Отчёт без просмотренных глазами кадров не считается.
- Всегда `color_scheme="dark"`, иначе Edge возьмёт светлую тему Windows.
- `?motion=0` — для статичных кадров; движение проверяй отдельно (`--motion`, серия кадров или счётчик rAF).
- `--virtual-time-budget` зависает из-за SSE — не использовать.
- Шейдер в headless Edge рисуется программно (`--disable-gpu`): для замеров скорости это не показатель.
