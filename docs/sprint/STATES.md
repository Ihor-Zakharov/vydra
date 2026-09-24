# Состояния интерфейса — что стенд обязан уметь показать

Каждое состояние — отдельный кадр стенда `tools/ui_rig.py` (id слева). Данные — моки API через
`page.route` (фикстуры в `tools/rig_fixtures/`), чтобы кадры были детерминированными и не требовали сети.
Аудит (фаза 0) дополняет список тем, что найдёт в коде; дизайнер — тем, что добавит.

## Главный экран
- `idle` — пустое поле, ничего не качается
- `typing-youtube` / `typing-tiktok` / `typing-instagram` / `typing-other` — ссылка распознана, глиф платформы
- `multi-links` — несколько ссылок, textarea раскрыта
- `preview-landscape` / `preview-portrait` — превью ролика
- `preview-trim` — выбран отрезок (начало–конец)
- `format-mp4` / `format-mp3` / `format-both`
- `controls-open` — «Ещё»: качество, битрейт и т. п.
- `dest-menu` — выбор папки «Сохранять в»
- `converter` — вкладка «Конвертер», dropzone; `converter-drag` — файл над зоной

## Загрузки (остров и список задач)
- `job-queued`, `job-downloading` (с процентом и скоростью), `job-downloading-indet`, `job-converting`, `job-saving`
- `job-waiting` — вопрос «не выходит → вот вариант → продолжить?»
- `job-done` — путь к файлу, «Открыть», «Показать в папке»
- `job-error` (+ «Повторить»), `job-cancelled`
- `jobs-many` — 5 задач в разных статусах; остров развёрнут и свёрнут

## Хранилище
- `library-empty`, `library-grid`, `library-list`, `library-search-none`, `library-selection` (панель выделения)
- `explorer-tree` — папки YouTube / TikTok / Instagram / …, переименование, перемещение

## Диалоги и системное
- `player` — просмотр файла
- `settings` — настройки (хранилище, cookies)
- `health-ok` / `health-problems` — диагностика
- `folder-dialog` — выбор/создание папки, вопрос «перенести уже скачанное?»
- `offline` — сервер недоступен
- `toast-*` — уведомления, если есть

## Режимы
Только ПК: `s` 1366×657, `m` 1536×730 (dpr 1.25), `l` 1920×960, `xl` 2560×1305 (размеры окна браузера без
его панелей). Полный прогон — `s,m,l`; `xl` — для героя, ключевых состояний и финальной приёмки.
Отдельно: `?motion=0`, эмуляция `prefers-reduced-motion`, фокус-обход клавиатурой (серия кадров `kbd-*`).
Регионы и владельцы состояний — `docs/sprint/REGIONS.md`.
