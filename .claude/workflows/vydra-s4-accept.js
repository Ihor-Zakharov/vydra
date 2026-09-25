export const meta = {
  name: 'vydra-s4-accept',
  description: 'Выдра S4: приёмка — замер (вёрстка, доступность, движение, консоль, e2e), доводка, финальная подпись арт-директора',
  whenToUse: 'Пятый этап UI-спринта выдры, после регионов (S3)',
  phases: [
    { title: 'Замер', detail: 'vydra-qa: стенд s,m,l,xl, клавиатура, reduced-motion, производительность, e2e, pytest' },
    { title: 'Доводка', detail: 'vydra-mech: дефекты G7–G12, до 2 кругов' },
    { title: 'Подпись', detail: 'vydra-designer (Opus 5.5, max): «так и отдаём» + галерея до/после' },
    { title: 'Последние правки', detail: 'vydra-ui — только если подпись не дана' },
  ],
}

const ROUNDS = (args && args.rounds) || 2
const NOTES = args && args.notes ? `\n\nЗамечания пользователя и дирижёра (важнее всего остального):\n${args.notes}` : ''

const DEFECT = {
  type: 'object',
  properties: {
    goal: { type: 'string', enum: ['G7', 'G8', 'G9', 'G10', 'G11', 'G12'] },
    state: { type: 'string' },
    viewport: { type: 'string' },
    what: { type: 'string' },
    evidence: { type: 'string', description: 'путь к кадру, строка report.json или вывод команды' },
  },
  required: ['goal', 'what', 'evidence'],
}

const MEASURE = {
  type: 'object',
  properties: {
    label: { type: 'string', description: 'метка прогона стенда' },
    goals: {
      type: 'array',
      items: {
        type: 'object',
        properties: { id: { type: 'string' }, ok: { type: 'boolean' }, evidence: { type: 'string' } },
        required: ['id', 'ok', 'evidence'],
      },
    },
    defects: { type: 'array', items: DEFECT },
  },
  required: ['label', 'goals', 'defects'],
}

const FIX = {
  type: 'object',
  properties: {
    fixed: { type: 'array', items: { type: 'string' } },
    left: { type: 'array', items: { type: 'string' }, description: 'не исправлено — почему (в том числе «нужно решение арт-директора»)' },
    shots: { type: 'string' },
  },
  required: ['fixed', 'left', 'shots'],
}

const NOTE = {
  type: 'object',
  properties: {
    level: { type: 'string', enum: ['blocker', 'major', 'minor'] },
    region: { type: 'string' },
    state: { type: 'string' },
    viewport: { type: 'string' },
    what: { type: 'string' },
    fix: { type: 'string' },
  },
  required: ['level', 'region', 'what', 'fix'],
}

const SIGN = {
  type: 'object',
  properties: {
    approved: { type: 'boolean', description: '«так и отдаём» — нет blocker и major' },
    summary: { type: 'string' },
    notes: { type: 'array', items: NOTE },
    gallery: {
      type: 'array',
      description: '6–8 пар «до/после» для docs/screenshots/: ключевые состояния в окнах l и s',
      items: {
        type: 'object',
        properties: { before: { type: 'string' }, after: { type: 'string' }, caption: { type: 'string' } },
        required: ['before', 'after', 'caption'],
      },
    },
    looked_at: { type: 'array', items: { type: 'string' } },
  },
  required: ['approved', 'summary', 'notes', 'gallery', 'looked_at'],
}

const measure = n => agent(`Приёмочный замер интерфейса выдры (круг ${n}) — цели G7–G12 в docs/sprint/GOALS.md. Ничего не чини.
1. Стенд (скилл ui-rig) на все состояния в окнах s, m, l, xl с меткой s4-measure-${n}; плюс серия --kbd; плюс прогон
   с эмуляцией prefers-reduced-motion. Из report.json: вылеты по ширине, наложения, обрезанный текст (G7), axe
   serious/critical (G8), ошибки и предупреждения консоли (G11). Форма и «Скачать» видны без прокрутки на s (G7).
2. Клавиатура (G8): весь сценарий «ссылка → формат → отрезок → скачать → открыть» и диалоги проходятся с клавиатуры,
   фокус виден на каждом шаге (кадры kbd-*), Esc закрывает диалоги, фокус возвращается. Контраст текста на фоне диска.
3. Движение и скорость (G9): ?motion=0 и reduced-motion действительно останавливают анимации; шейдер cosmos.js не
   рисует кадры, когда вкладка скрыта и когда герой вне экрана (проверь счётчиком rAF через page.evaluate);
   PerformanceObserver longtask при вводе ссылки и во время загрузки — задач длиннее 200 мс нет.
4. Функции (G10): uv run pytest -q; python3 tools/e2e.py --label s4-${n} --compare .sprint/baseline/e2e.json (скилл dev-server).
5. Чистота (G12): id и классы без пары между index.html / app.js / explorer.js / css, отладочный код (console.log,
   debugger, закомментированные блоки), CSS-правила без элементов.
Открой через Read листы-контакты всех окон. Каждый дефект — с доказательством. Отчёт — .sprint/qa/s4-${n}.md.${NOTES}`,
  { label: `замер ${n}`, phase: 'Замер', agentType: 'vydra-qa', schema: MEASURE })

let m = null
for (let round = 1; round <= ROUNDS; round++) {
  phase('Замер')
  m = await measure(round)
  if (!m || !m.defects.length) break
  if (round === ROUNDS) break
  phase('Доводка')
  await agent(`Исправь дефекты приёмки интерфейса выдры (круг ${round}). Художественных решений не принимай: вид — по
docs/design/DIRECTION.md и существующим токенам; если исправление требует вкусового решения — сделай самое нейтральное
и перечисли в left с пометкой «нужно решение арт-директора». Функции — контракт docs/sprint/FEATURES.md.
Дефекты:
${m.defects.map(d => `- [${d.goal}] ${d.state || ''} ${d.viewport || ''}: ${d.what} (${d.evidence})`).join('\n')}
После правок — стенд на затронутые состояния (метка s4-fix-${round}), кадры через Read, uv run pytest -q.${NOTES}`,
    { label: `доводка ${round}`, phase: 'Доводка', agentType: 'vydra-mech', schema: FIX })
}

phase('Подпись')
const sign = () => agent(`Финальная приёмка интерфейса выдры — цель G13 в docs/sprint/GOALS.md: «так и отдаём» или нет.
Последний замер: ${m ? m.label : 'нет'}; открытые дефекты: ${m && m.defects.length ? m.defects.map(d => `[${d.goal}] ${d.what}`).join('; ') : 'нет'}.
Сними стендом все состояния в окнах s, l, xl с меткой s4-sign; открой все листы-контакты и каждый ключевой кадр через
Read. Сравни с DIRECTION.md и с baseline (.sprint/shots/baseline): это тот уровень, который обещали пользователю —
«вау, это кино» за 3 секунды и «понятно, куда вставлять ссылку» за 10?
Выбери 6–8 пар «до/после» для галереи (baseline → s4-sign), окна l и s. Замечания — с регионом и точными значениями.${NOTES}`,
  { label: 'подпись', phase: 'Подпись', agentType: 'vydra-designer', schema: SIGN })

let s = await sign()
const blocking = x => x ? x.notes.filter(n => n.level !== 'minor') : []
if (s && (!s.approved || blocking(s).length)) {
  phase('Последние правки')
  const byRegion = {}
  for (const n of blocking(s)) (byRegion[n.region] = byRegion[n.region] || []).push(n)
  await parallel(Object.entries(byRegion).map(([region, notes]) => () =>
    agent(`Последние правки региона «${region}» интерфейса выдры по финальному ревью арт-директора.
Работай по скиллу region-pass (метка стенда s4-last-${region}); фундамент можно править, только если замечание о нём.
${notes.map(n => `- [${n.level}] ${n.state || ''} ${n.viewport || ''}: ${n.what} → ${n.fix}`).join('\n')}${NOTES}`,
      { label: `правки: ${region}`, phase: 'Последние правки', agentType: 'vydra-ui', schema: FIX })))
  phase('Подпись')
  s = await sign()
}

return {
  stage: 's4',
  ok: !!(s && s.approved && !blocking(s).length && m && !m.defects.length),
  measure: m,
  sign: s,
}
