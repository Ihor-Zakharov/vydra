export const meta = {
  name: 'vydra-s0-rig',
  description: 'Выдра S0: стенд состояний и контракт функций, затем CSS по регионам попиксельно и только тёмная тема',
  whenToUse: 'Первый этап UI-спринта выдры; запускает дирижёр из /ui-sprint',
  phases: [
    { title: 'Стенд и контракт', detail: 'vydra-mech ×2 параллельно: tools/ui_rig.py + FEATURES.md и e2e' },
    { title: 'Механика', detail: 'vydra-mech: CSS по регионам без изменения кадров, только тёмная тема, чистка' },
    { title: 'Проверка', detail: 'vydra-qa: pytest, полный прогон стенда, цели G1–G3' },
  ],
}

const NOTES = args && args.notes ? `\n\nЗамечания дирижёра и пользователя (важнее всего остального):\n${args.notes}` : ''

const RIG = {
  type: 'object',
  properties: {
    ok: { type: 'boolean', description: 'стенд работает и снял baseline' },
    command: { type: 'string', description: 'точная команда полного прогона' },
    seconds: { type: 'number', description: 'время полного прогона s,m,l' },
    states: { type: 'array', items: { type: 'string' }, description: 'id снятых состояний' },
    missing: { type: 'array', items: { type: 'string' }, description: 'состояния из STATES.md, которые снять не удалось, и почему' },
    baseline: { type: 'string', description: 'папка .sprint/shots/baseline' },
    deterministic: { type: 'boolean', description: 'два прогона подряд дают попиксельно одинаковые кадры' },
    findings: { type: 'array', items: { type: 'string' }, description: 'что стенд нашёл в baseline: ошибки консоли, вылеты, axe' },
  },
  required: ['ok', 'command', 'seconds', 'states', 'missing', 'baseline', 'deterministic'],
}

const FEAT = {
  type: 'object',
  properties: {
    features: { type: 'number', description: 'строк в FEATURES.md' },
    states_added: { type: 'array', items: { type: 'string' }, description: 'состояния, добавленные в STATES.md по итогам аудита' },
    dead: { type: 'array', items: { type: 'string' }, description: 'мёртвые id/классы/обработчики и остатки кинотеатра: файл:строка — что' },
    e2e: {
      type: 'array',
      items: {
        type: 'object',
        properties: { scenario: { type: 'string' }, ok: { type: 'boolean' }, detail: { type: 'string' } },
        required: ['scenario', 'ok', 'detail'],
      },
    },
    api_bugs: { type: 'array', items: { type: 'string' } },
  },
  required: ['features', 'states_added', 'dead', 'e2e'],
}

const MECH = {
  type: 'object',
  properties: {
    css_files: { type: 'array', items: { type: 'string' } },
    identical: { type: 'boolean', description: 'после разноса CSS кадры попиксельно совпали с baseline' },
    diff: { type: 'string', description: 'итог сравнения стенда: сколько кадров, сколько отличается и почему' },
    dark_only: { type: 'boolean' },
    removed: { type: 'array', items: { type: 'string' } },
    pytest: { type: 'string', description: 'итоговая строка uv run pytest -q' },
    needs_backend: { type: 'array', items: { type: 'string' }, description: 'тесты/бэкенд, которые надо поправить владельцу' },
  },
  required: ['css_files', 'identical', 'diff', 'dark_only', 'removed', 'pytest'],
}

const QA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['pass', 'fail'] },
    checks: {
      type: 'array',
      items: {
        type: 'object',
        properties: { id: { type: 'string' }, ok: { type: 'boolean' }, evidence: { type: 'string' } },
        required: ['id', 'ok', 'evidence'],
      },
    },
    problems: { type: 'array', items: { type: 'string' } },
  },
  required: ['verdict', 'checks', 'problems'],
}

phase('Стенд и контракт')
const [rig, feat] = await parallel([
  () => agent(`Построй стенд интерфейса выдры — цель G1 в docs/sprint/GOALS.md. От него зависит весь спринт:
по его кадрам работают арт-директор и исполнители, по его report.json принимается работа.

Прочитай скилл ui-rig (там контракт команды, который ты реализуешь), docs/sprint/STATES.md, docs/sprint/REGIONS.md,
скилл dev-server. Изучи vydra/static/app.js и explorer.js: какие запросы к /api делает интерфейс и какие события
приходят по SSE (/api/events).

Сделай tools/ui_rig.py (+ tools/rig_fixtures/, при необходимости tools/vendor/axe.min.js):
- Edge на Windows через CDP (порт 9333), Playwright из WSL; запуск: python3 tools/ui_rig.py … — если playwright
  не импортируется, скрипт сам перезапускает себя через uv run --with playwright --with pillow.
- Каждое состояние STATES.md — детерминированный кадр: /api/** подменяется фикстурами (page.route), EventSource —
  поддельным классом через add_init_script, service worker выключен, color_scheme dark, ?motion=0; время и шейдер
  заморожены так, чтобы два прогона подряд давали попиксельно одинаковые кадры. Состояния, которые получаются только
  действиями пользователя, — доводи действиями (ввод, клик, клавиши).
- Окна s 1366×657, m 1536×730 dpr 1.25, l 1920×960, xl 2560×1305; по умолчанию s,m,l.
- Выход: .sprint/shots/<метка>/<окно>/<состояние>.png, contact-<окно>.png (лист с подписями), report.json
  (по каждому кадру: ошибки и предупреждения консоли, ошибки страницы, упавшие запросы, scrollWidth/clientWidth,
  обрезанный текст, наложения интерактивных элементов, нарушения axe serious/critical).
- Ключи: --label, --states (список и маски вида job-*), --viewports, --css <файл> (подмешать CSS-черновик),
  --diff <метка> (попиксельное сравнение с другой меткой: доля отличий по кадрам + картинки разницы), --live
  (без фикстур), --motion (не замораживать), --kbd (серия кадров обхода клавиатурой), --jobs N (параллельных
  страниц). Последняя строка вывода — "report: <путь>"; код выхода ≠ 0 только если сломался сам стенд.
- Полный прогон s,m,l — меньше 3 минут. Проверь два прогона подряд на детерминизм.

Сними baseline: --label baseline на все состояния s,m,l и xl. Открой через Read все три листа-контакта и 5–6 кадров
крупно: убедись, что каждое состояние действительно показывает то, что обещает его имя. Скилл ui-rig не правь
(не твоя зона): если реальная команда отличается от контракта в нём, опиши разницу в ответе.${NOTES}`,
    { label: 'стенд', phase: 'Стенд и контракт', agentType: 'vydra-mech', schema: RIG }),

  () => agent(`Составь контракт функций интерфейса выдры и базовый e2e — цель G2 в docs/sprint/GOALS.md.

1. Аудит vydra/static/index.html, app.js, explorer.js → docs/sprint/FEATURES.md: таблица «регион | функция или элемент |
   как вызвать (клик, клавиша, перетаскивание) | запрос к API | ожидаемый результат | состояние из STATES.md».
   Регионы — как в docs/sprint/REGIONS.md. Отдельно: горячие клавиши, события SSE, тосты, подсказки.
   Всё, что найдено в коде, но отсутствует в docs/sprint/STATES.md, — добавь туда (в нужный раздел).
   Составь список мёртвого: id и классы, которых нет в одном из html/js/css, обработчики без элементов, остатки
   кинотеатра (cinema, кинотеатр) — файл:строка. Ничего не удаляй: это сделает следующий шаг.
2. tools/e2e.py — живой прогон через Edge (CDP 9333, скилл ui-rig) на сервере e2e :8798 с одноразовой библиотекой
   (скилл dev-server). Сценарии: YouTube MP4, TikTok MP3, Instagram «оба», отрезок, «открыть / показать в папке»
   (проверяй ответ API — Проводник в headless не виден), смена папки хранилища с переносом скачанного, конвертер
   (маленький клип сделай ffmpeg). Ссылки — из tests/test_live.py. Вывод: .sprint/<метка>/e2e.md и e2e.json,
   ключ --compare <e2e.json> сравнивает с базовым.
3. Прогон: --label baseline → .sprint/baseline/e2e.md. Сбой из-за сети или сайта отличай от сбоя интерфейса.${NOTES}`,
    { label: 'контракт и e2e', phase: 'Стенд и контракт', agentType: 'vydra-mech', effort: 'medium', schema: FEAT }),
])

if (!rig || !rig.ok) {
  log('Стенд не готов — механику не начинаю, возвращаю отчёт дирижёру')
  return { stage: 's0', ok: false, rig, feat }
}

phase('Механика')
const mech = await agent(`Механика интерфейса выдры до дизайна — цель G3 в docs/sprint/GOALS.md. Стенд готов:
${rig.command}

1. Разнеси vydra/static/styles.css по регионам в vydra/static/css/ — core.css, shell.css, hero.css, jobs.css,
   library.css, dialogs.css — строго по карте docs/sprint/REGIONS.md (уточни её селекторами по факту разноса).
   Каскад не должен измениться: порядок правил и подключения сохраняет результат; адаптивные блоки и
   prefers-reduced-motion раздай в конец соответствующих файлов. Подключи файлы в index.html, обнови список ресурсов
   в sw.js, styles.css удали. Проверка: стенд --label split --diff baseline на s,m,l,xl — отличий 0 пикселей
   (любое отличие — найди причину и устрани, а не объясняй).
2. Только тёмная тема: data-theme всегда dark, prefers-color-scheme: light игнорируется, переключатель темы скрыт,
   color-scheme — dark. Светлые правила в CSS НЕ удалять — они понадобятся позже.
3. Удали остатки кинотеатра и мёртвое из аудита:
${feat && feat.dead && feat.dead.length ? feat.dead.map(d => '   - ' + d).join('\n') : '   (аудит не вернул список — найди сам: id/классы без пары между html/js/css)'}
4. Стенд --label mech --diff split: отличаться может только то, что ты намеренно поменял (переключатель темы и т. п.).
   Открой листы-контакты и кадры с отличиями через Read. uv run pytest -q — зелёный; если падает тест из-за
   статики — тесты не твоя зона: опиши, что и почему, в needs_backend.${NOTES}`,
  { label: 'механика', phase: 'Механика', agentType: 'vydra-mech', schema: MECH })

phase('Проверка')
const qa = await agent(`Проверь итог этапа S0 спринта выдры (цели G1–G3 в docs/sprint/GOALS.md).
Команда стенда: ${rig.command}
1. uv run pytest -q.
2. Полный прогон стенда --label s0-qa (s,m,l), засеки время. Все ли состояния из docs/sprint/STATES.md сняты?
   Сравни --diff mech: кадры должны совпасть (детерминизм).
3. report.json: ошибки консоли, вылеты по ширине, обрезанный текст, axe serious/critical — перечисли.
4. Открой через Read листы-контакты s и l и 4 случайных кадра: соответствуют ли они именам состояний?
5. Есть ли docs/sprint/FEATURES.md, tools/e2e.py, .sprint/baseline/e2e.md; CSS разнесён (vydra/static/css/*.css,
   styles.css нет), тема всегда тёмная, переключатель скрыт.
Отчёт — в .sprint/qa/s0.md; checks с id G1, G2, G3 и доказательствами.`,
  { label: 'проверка S0', phase: 'Проверка', agentType: 'vydra-qa', schema: QA })

return { stage: 's0', ok: !!(qa && qa.verdict === 'pass'), rig, feat, mech, qa }
