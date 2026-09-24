export const meta = {
  name: 'vydra-s2-foundation',
  description: 'Выдра S2: фундамент по DIRECTION — токены, типографика, оболочка и сцена первого экрана; приёмка арт-директором',
  whenToUse: 'Третий этап UI-спринта выдры, после направления (S1)',
  phases: [
    { title: 'Фундамент', detail: 'vydra-ui (Opus 5.5, high): core.css, shell.css, сцена героя, cosmos.js' },
    { title: 'Ревью', detail: 'vydra-designer (Opus 5.5, max): одобрить или замечания' },
    { title: 'Правки', detail: 'vydra-ui: blocker и major из ревью, до 2 кругов' },
  ],
}

const ROUNDS = (args && args.rounds) || 2
const NOTES = args && args.notes ? `\n\nЗамечания пользователя и дирижёра (важнее всего остального):\n${args.notes}` : ''

const BUILD = {
  type: 'object',
  properties: {
    region: { type: 'string' },
    done: { type: 'boolean', description: 'сделано по DIRECTION и проверено стендом' },
    summary: { type: 'string', description: '3–6 строк: что сделано' },
    decisions: { type: 'array', items: { type: 'string' }, description: 'решения, которых нет в DIRECTION: «решил сам: … потому что …»' },
    shots: { type: 'string', description: 'папка последнего прогона стенда' },
    issues: { type: 'array', items: { type: 'string' }, description: 'что не удалось: состояние, окно, почему' },
    api_bugs: { type: 'array', items: { type: 'string' } },
  },
  required: ['region', 'done', 'summary', 'decisions', 'shots', 'issues'],
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
    approved: { type: 'boolean', description: 'true только если нет blocker и major' },
    summary: { type: 'string' },
    notes: { type: 'array', items: NOTE },
    looked_at: { type: 'array', items: { type: 'string' }, description: 'кадры, которые ты открыл' },
  },
  required: ['approved', 'summary', 'notes', 'looked_at'],
}

const SCOPE = `Регион «фундамент» из docs/sprint/REGIONS.md: токены, типографика и общие компоненты (css/core.css), оболочка —
верхняя панель, подвал, заголовки разделов, тосты, «сервер недоступен» (css/shell.css) — и сцена первого экрана:
чёрная дыра (cosmos.js, её место, масштаб и параметры), заголовок и композиция героя вокруг формы. Сами органы формы,
загрузки, хранилище и диалоги — следующий этап, их не переделывай, но и не ломай: после фундамента они должны
выглядеть не хуже, чем сейчас.`

phase('Фундамент')
let build = await agent(`Сделай фундамент интерфейса выдры по docs/design/DIRECTION.md — цель G5 в docs/sprint/GOALS.md.
${SCOPE}
Отправная точка — черновик выбранного варианта первого экрана из .sprint/proto/ (см. DIRECTION.md), но результат —
чистый код в vydra/static, а не подмешанный черновик. Прочитай скилл region-pass и работай по нему.
Состояния для стенда: idle, offline, toast-*, typing-youtube, preview-landscape, job-downloading, library-grid; окна s, m, l, xl.${NOTES}`,
  { label: 'фундамент', phase: 'Фундамент', agentType: 'vydra-ui', schema: BUILD })

let review = null
for (let round = 1; round <= ROUNDS; round++) {
  phase('Ревью')
  review = await agent(`Прими фундамент интерфейса выдры (круг ${round} из ${ROUNDS}) по своему чек-листу в docs/design/DIRECTION.md.
${SCOPE}
Отчёт исполнителя: ${build ? build.summary : 'нет'}
Его решения вне DIRECTION: ${build && build.decisions.length ? build.decisions.join('; ') : 'нет'}
Сними стендом сам (скилл ui-rig): idle, typing-youtube, preview-landscape, job-downloading, library-grid, offline в окнах s, l, xl
с меткой s2-review-${round}; открой кадры через Read и сравни с DIRECTION и с baseline (.sprint/shots/baseline).
Одобряй, только если нет blocker и major. Решения исполнителя вне DIRECTION, с которыми согласен, внеси в DIRECTION.md.`,
    { label: `ревью ${round}`, phase: 'Ревью', agentType: 'vydra-designer', schema: REVIEW })
  const blocking = review ? review.notes.filter(n => n.level !== 'minor') : []
  if (review && review.approved && !blocking.length) break
  if (round === ROUNDS) break
  phase('Правки')
  const todo = (review ? review.notes : []).map(n => `- [${n.level}] ${n.state || ''} ${n.viewport || ''}: ${n.what} → ${n.fix}`).join('\n')
  build = await agent(`Исправь фундамент интерфейса выдры по замечаниям арт-директора (круг ${round}).
${SCOPE}
Замечания (blocker и major обязательно, minor — если не ломает остальное):
${todo || '- арт-директор не вернул замечаний: перепроверь фундамент по чек-листу DIRECTION.md'}
Работай по скиллу region-pass; метка стенда s2-fix-${round}.`,
    { label: `правки ${round}`, phase: 'Правки', agentType: 'vydra-ui', schema: BUILD })
}

const approved = !!(review && review.approved && !review.notes.some(n => n.level !== 'minor'))
if (!approved) log('Фундамент не одобрен за отведённые круги — решение за дирижёром')
return { stage: 's2', ok: approved, build, review }
