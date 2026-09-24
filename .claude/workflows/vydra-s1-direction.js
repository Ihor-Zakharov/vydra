export const meta = {
  name: 'vydra-s1-direction',
  description: 'Выдра S1: арт-директор (Opus 5.5, max) пишет направление KOCMOC / UNLEASHED и выбирает первый экран из вариантов',
  whenToUse: 'Второй этап UI-спринта выдры, после стенда (S0)',
  phases: [
    { title: 'Направление', detail: 'vydra-designer: варианты первого экрана на стенде → docs/design/DIRECTION.md' },
    { title: 'Полнота', detail: 'vydra-qa: хватит ли DIRECTION исполнителям — пробелы по регионам и состояниям' },
    { title: 'Дополнение', detail: 'vydra-designer закрывает пробелы (только если они есть)' },
  ],
}

const NOTES = args && args.notes ? `\n\nЗамечания пользователя и дирижёра (важнее всего остального):\n${args.notes}` : ''

const DIRECTION = {
  type: 'object',
  properties: {
    path: { type: 'string', description: 'docs/design/DIRECTION.md' },
    thesis: { type: 'string', description: 'тезис направления одной фразой' },
    chosen: { type: 'string', description: 'выбранный вариант первого экрана и почему' },
    variants: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          css: { type: 'string', description: '.sprint/proto/<вариант>.css' },
          shots: { type: 'string', description: 'папка кадров варианта' },
          verdict: { type: 'string' },
        },
        required: ['name', 'css', 'shots', 'verdict'],
      },
    },
    hero_shot: { type: 'string', description: 'кадр выбранного первого экрана в окне l — его увидит пользователь' },
    looked_at: { type: 'array', items: { type: 'string' } },
  },
  required: ['path', 'thesis', 'chosen', 'variants', 'hero_shot', 'looked_at'],
}

const GAPS = {
  type: 'object',
  properties: {
    complete: { type: 'boolean' },
    gaps: {
      type: 'array',
      items: {
        type: 'object',
        properties: { region: { type: 'string' }, state: { type: 'string' }, missing: { type: 'string' } },
        required: ['region', 'missing'],
      },
    },
  },
  required: ['complete', 'gaps'],
}

phase('Направление')
const dir = await agent(`Задай направление интерфейса выдры — цель G4 в docs/sprint/GOALS.md. Это главный документ спринта:
по нему четыре исполнителя параллельно сделают регионы страницы, а ты потом примешь их работу.

Исходные данные:
- бриф docs/sprint/BRIEF.md; функции docs/sprint/FEATURES.md; состояния docs/sprint/STATES.md; регионы docs/sprint/REGIONS.md;
- baseline стенда: .sprint/shots/baseline/ (листы contact-s.png, contact-m.png, contact-l.png и кадры) — посмотри их все;
- референсы KOCMOC / KOCMOC UNLEASHED и движения — в твоём описании агента (прочитай указанные разделы целиком);
- текущие токены: vydra/static/css/core.css; сцена: vydra/static/cosmos.js.

Как работать:
1. Первый экран решает всё. Сделай 3 принципиально разных варианта композиции первого экрана (где и какого масштаба
   чёрная дыра, где заголовок, где поле ссылки и «Скачать», как всё это живёт на s и на xl) черновиками
   .sprint/proto/<вариант>.css и сними каждый стендом с --css на состояниях idle, typing-youtube, preview-landscape,
   job-downloading в окнах s и l (скилл ui-rig). Посмотри все кадры, сравни, выбери один (можно гибрид) и доведи
   черновик выбранного до уверенного вида — исполнитель фундамента возьмёт его за основу.
2. Напиши docs/design/DIRECTION.md. Обязательно:
   - тезис и 5–7 принципов (что делаем и чего никогда не делаем — с опорой на «роковые ошибки» из референсов);
   - токены тёмной темы с точными значениями: цвет (фон, поверхности, текст 3 уровней, линии, излучение, статусы,
     цвета платформ только «в излучении»), радиусы, тени/свечение, стекло, зерно; сетка и шкала отступов; типографика
     (шрифты из vydra/static/fonts, размеры, начертания, интерлиньяж, трекинг; где моноширинный); иконки;
   - движение: длительности, кривые или параметры пружин (spring.js), ступенчатость, фоновое «сердцебиение», что
     происходит при вводе ссылки, старте загрузки, прогрессе, готово, ошибке; правила reduced-motion;
   - первый экран в окнах s, m, l, xl: раскладка с размерами и положением чёрной дыры, параметры сцены cosmos.js;
   - по каждому региону (оболочка, форма, загрузки, хранилище, диалоги) — раскладка и вид КАЖДОГО состояния из
     STATES.md: что видно, иерархия, размеры, отступы, поведение при наведении и фокусе;
   - чек-лист приёмки по регионам — по нему ты будешь принимать работу.
   Пиши так, чтобы исполнитель не угадывал: значения, а не эпитеты.
3. Только ПК и только тёмная тема; функции не меняются.${NOTES}`,
  { label: 'направление', phase: 'Направление', agentType: 'vydra-designer', schema: DIRECTION })

if (!dir) return { stage: 's1', ok: false, reason: 'арт-директор не вернул результат' }

phase('Полнота')
const gaps = await agent(`Проверь, хватит ли исполнителям docs/design/DIRECTION.md, чтобы сделать каждый регион без угадывания.
Сверь с docs/sprint/STATES.md и docs/sprint/REGIONS.md: для каждого региона и каждого состояния есть ли раскладка, размеры,
отступы, цвета (токенами), типографика, наведение/фокус, движение? Есть ли точные значения токенов, а не эпитеты?
Пробел — это место, где исполнителю пришлось бы придумывать самому. Не оценивай вкус, только полноту.
Ничего не правь; отчёт в .sprint/qa/s1-gaps.md.`,
  { label: 'полнота', phase: 'Полнота', agentType: 'vydra-qa', schema: GAPS })

let patch = null
if (gaps && !gaps.complete && gaps.gaps.length) {
  phase('Дополнение')
  patch = await agent(`Исполнителям не хватает в docs/design/DIRECTION.md вот чего:
${gaps.gaps.map(g => `- ${g.region}${g.state ? ' / ' + g.state : ''}: ${g.missing}`).join('\n')}
Закрой каждый пробел точными значениями в самом документе (не в отдельном файле). Если пробел мнимый — одной строкой почему.
Если для решения нужен кадр — сними стендом с черновиком .sprint/proto/*.css.`,
    {
      label: 'дополнение', phase: 'Дополнение', agentType: 'vydra-designer',
      schema: {
        type: 'object',
        properties: {
          closed: { type: 'array', items: { type: 'string' } },
          rejected: { type: 'array', items: { type: 'string' }, description: 'мнимые пробелы — с причиной' },
        },
        required: ['closed', 'rejected'],
      },
    })
} else {
  log('Пробелов в DIRECTION нет — дополнение пропущено')
}

return { stage: 's1', ok: true, direction: dir, gaps: gaps ? gaps.gaps : [], patch }
