export const meta = {
  name: 'vydra-s3-regions',
  description: 'Выдра S3: четыре исполнителя параллельно доводят регионы страницы по DIRECTION, арт-директор принимает всё вместе',
  whenToUse: 'Четвёртый этап UI-спринта выдры, после фундамента (S2); перед запуском дирижёр делает `python3 tools/sprint.py begin s3`',
  phases: [
    { title: 'Регионы', detail: 'vydra-ui ×4 (Opus 5.5, high) параллельно: форма, загрузки, хранилище, диалоги' },
    { title: 'Ревью', detail: 'vydra-designer (Opus 5.5, max): все регионы вместе — единство мира и чек-лист' },
    { title: 'Правки', detail: 'vydra-ui параллельно по неодобренным регионам, до 2 кругов' },
  ],
}

const ROUNDS = (args && args.rounds) || 2
const NOTES = args && args.notes ? `\n\nЗамечания пользователя и дирижёра (важнее всего остального):\n${args.notes}` : ''

const REGIONS = {
  hero: {
    title: 'форма',
    what: 'органы формы в герое: вкладки «По ссылке / Конвертер», поле ссылки и «Вставить», кнопка «Скачать», распознанная платформа, несколько ссылок, превью ролика, отрезок, форматы MP4 / MP3 / оба, «Ещё» (качество, битрейт), «Сохранять в», конвертер и зона перетаскивания',
    states: 'typing-*, multi-links, preview-*, format-*, controls-open, dest-menu, converter, converter-drag',
  },
  jobs: {
    title: 'загрузки',
    what: 'остров (Dynamic Island) и список задач: в очереди, скачивание с процентом и скоростью, без процента, конвертация, сохранение, вопрос «не выходит → вот вариант → продолжить?», готово (путь, «Открыть», «Показать в папке»), ошибка с «Повторить», отменено, много задач',
    states: 'job-*, jobs-many',
  },
  library: {
    title: 'хранилище',
    what: 'раздел «Хранилище»: крошки, дерево папок YouTube / TikTok / Instagram / …, плитки и список, поиск, сортировка, пусто, ничего не найдено, выделение и панель действий, переименование и перемещение, перетаскивание файлов',
    states: 'library-*, explorer-tree',
  },
  dialogs: {
    title: 'диалоги',
    what: 'плеер, настройки (хранилище, cookies), состояние системы (всё хорошо / есть проблемы), выбор и создание папки с вопросом «перенести уже скачанное?», подтверждение, шпаргалка клавиш',
    states: 'player, settings, health-*, folder-dialog, confirm, cheats',
  },
}
const KEYS = (args && args.regions) || Object.keys(REGIONS)

const BUILD = {
  type: 'object',
  properties: {
    region: { type: 'string' },
    done: { type: 'boolean', description: 'сделано по DIRECTION и проверено стендом' },
    summary: { type: 'string', description: '3–6 строк: что сделано' },
    decisions: { type: 'array', items: { type: 'string' }, description: 'решения, которых нет в DIRECTION: «решил сам: … потому что …»' },
    shots: { type: 'string', description: 'папка последнего прогона стенда' },
    issues: { type: 'array', items: { type: 'string' } },
    needs: { type: 'array', items: { type: 'string' }, description: 'что нужно от фундамента или соседних регионов' },
    api_bugs: { type: 'array', items: { type: 'string' } },
  },
  required: ['region', 'done', 'summary', 'decisions', 'shots', 'issues', 'needs'],
}

const NOTE = {
  type: 'object',
  properties: {
    level: { type: 'string', enum: ['blocker', 'major', 'minor'] },
    state: { type: 'string' },
    viewport: { type: 'string' },
    what: { type: 'string' },
    fix: { type: 'string', description: 'как исправить — точными значениями' },
  },
  required: ['level', 'what', 'fix'],
}

const REVIEW = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    coherence: { type: 'string', description: 'единство мира между регионами и с фундаментом' },
    regions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          region: { type: 'string', enum: Object.keys(REGIONS) },
          approved: { type: 'boolean', description: 'true только если нет blocker и major' },
          notes: { type: 'array', items: NOTE },
        },
        required: ['region', 'approved', 'notes'],
      },
    },
    foundation: { type: 'array', items: NOTE, description: 'правки фундамента (core.css, shell.css, cosmos.js), которые нельзя решить внутри регионов' },
    looked_at: { type: 'array', items: { type: 'string' } },
  },
  required: ['summary', 'coherence', 'regions', 'foundation', 'looked_at'],
}

const scope = key => `Твой регион — «${REGIONS[key].title}» (${key} в docs/sprint/REGIONS.md): ${REGIONS[key].what}.
Состояния для стенда: ${REGIONS[key].states}; окна s, m, l (xl — для финальной проверки).
Параллельно с тобой три других исполнителя делают соседние регионы в тех же общих файлах: свой CSS-файл правь
свободно, index.html / app.js / explorer.js — только Edit и только в своих секциях; фундамент (core.css, shell.css,
cosmos.js, spring.js) заморожен.`

phase('Регионы')
const builds = {}
const first = await parallel(KEYS.map(key => () =>
  agent(`Сделай регион интерфейса выдры по docs/design/DIRECTION.md — часть цели G6 в docs/sprint/GOALS.md.
${scope(key)}
Прочитай скилл region-pass и работай по нему: каждое состояние региона — как в DIRECTION, функции — как в
docs/sprint/FEATURES.md. Метка стенда: s3-${key}-<n>.${NOTES}`,
    { label: `регион: ${REGIONS[key].title}`, phase: 'Регионы', agentType: 'vydra-ui', schema: BUILD })))
KEYS.forEach((key, i) => { builds[key] = first[i] })

let todo = KEYS.slice()
let review = null
const approvedSet = new Set()
const foundationNotes = []
for (let round = 1; round <= ROUNDS && todo.length; round++) {
  phase('Ревью')
  const reports = todo.map(k => {
    const b = builds[k]
    return `- ${k}: ${b ? b.summary : 'исполнитель не вернул отчёт'}${b && b.decisions.length ? `\n  решения вне DIRECTION: ${b.decisions.join('; ')}` : ''}${b && b.needs.length ? `\n  просит: ${b.needs.join('; ')}` : ''}`
  }).join('\n')
  review = await agent(`Прими регионы интерфейса выдры (круг ${round} из ${ROUNDS}): ${todo.join(', ')}.
Уже одобрены: ${[...approvedSet].join(', ') || 'нет'}.
Отчёты исполнителей:
${reports}
Сними стендом сам (скилл ui-rig) все состояния этих регионов в окнах s и l (xl — ключевые) с меткой s3-review-${round};
открой листы-контакты и все кадры, по которым судишь, через Read. Сверь с чек-листом DIRECTION.md и с соседями:
страница должна читаться как один мир KOCMOC / UNLEASHED, а не как четыре разных сайта.
По каждому региону — approved и замечания с точными значениями. Одобряй, только если нет blocker и major.
Решения исполнителей вне DIRECTION, с которыми согласен, внеси в DIRECTION.md. Просьбы к фундаменту реши сам:
либо «сделать локально в регионе» (замечание региону), либо правка фундамента — в поле foundation; её сделают после
этапа, когда фундамент разморозят.${NOTES}`,
    { label: `ревью ${round}`, phase: 'Ревью', agentType: 'vydra-designer', schema: REVIEW })
  if (!review) break
  foundationNotes.push(...(review.foundation || []))
  const verdicts = new Map(review.regions.map(r => [r.region, r]))
  for (const k of todo) {
    const v = verdicts.get(k)
    if (v && v.approved && !v.notes.some(n => n.level !== 'minor')) approvedSet.add(k)
  }
  todo = todo.filter(k => !approvedSet.has(k))
  if (!todo.length || round === ROUNDS) break

  phase('Правки')
  const fixed = await parallel(todo.map(key => () => {
    const v = verdicts.get(key)
    const notes = v ? v.notes.map(n => `- [${n.level}] ${n.state || ''} ${n.viewport || ''}: ${n.what} → ${n.fix}`).join('\n') : ''
    return agent(`Исправь регион интерфейса выдры по замечаниям арт-директора (круг ${round}).
${scope(key)}
Замечания (blocker и major обязательно, minor — если не ломает остальное):
${notes || '- замечаний по региону нет в ответе: перепроверь регион по чек-листу DIRECTION.md'}
Работай по скиллу region-pass; метка стенда s3-${key}-fix-${round}.${NOTES}`,
      { label: `правки: ${REGIONS[key].title}`, phase: 'Правки', agentType: 'vydra-ui', schema: BUILD })
  }))
  todo.forEach((key, i) => { if (fixed[i]) builds[key] = fixed[i] })
}

if (todo.length) log(`Не одобрены: ${todo.join(', ')} — решение за дирижёром`)
return {
  stage: 's3',
  ok: todo.length === 0,
  approved: [...approvedSet],
  pending: todo,
  foundation_notes: foundationNotes,
  api_bugs: KEYS.flatMap(k => (builds[k] && builds[k].api_bugs) || []),
  coherence: review ? review.coherence : '',
}
