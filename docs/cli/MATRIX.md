# Матрица загрузок консольной выдры

Реальные загрузки в WSL2 (Ubuntu, `networkingMode=mirrored`, брандмауэр включён), yt-dlp 2026.08.19,
25.09.2026. Каждый случай — `vydra download … --json` в изолированной библиотеке (`VD_LIBRARY_DIR`,
`VD_WORK_DIR`, `VD_CONFIG_DIR`), затем проверка: код выхода; файлы на месте; `ffprobe` — длительность
(отрезок ±1 с), кодеки (MP4 — H.264/AAC, MP3 — mp3), меньшая сторона кадра; в хранилище и во временной папке нет
`.part`, `.ytdl`, пустых заготовок и брошенных папок задач. Запись в Windows — `/mnt/c/Users/Ihor/Downloads/vydra-matrix/`
(удалена после проверки). Прогон: `uv run python .sprint/matrix.py` (скрипт живёт в worktree, `.sprint/` не в git).

**Итог: 57 случаев — 42 OK, 9 FIX (баг или медленное место найдено и исправлено, есть тест), 4 SITE (сторона
сайта), 2 KNOWN (ограничение, которое выдра объясняет).** Непройденных по вине выдры нет. Ещё 12 сценариев проверены вручную
(таблица ниже): терминал, пайп, Ctrl+C, буфер обмена, интерактивный режим, «показать в папке», порты, установщик.

Статусы: `OK` — как ожидалось; `FIX` — был баг, исправлен (коммит + регрессионный тест); `SITE` — сбой на стороне сайта
(повторено через 5+ минут — тот же результат); `KNOWN` — ограничение, которое вывод объясняет пользователю.

## Автоматические случаи

| № | Платформа | Случай | Параметры | Ожидалось | Получилось | Статус | Коммит или причина |
|---|---|---|---|---|---|---|---|
| 1 | YouTube | обычное 19 с | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp4 -q 360` | код 0, mp4, 17–21 с, ≤360p | код 0, 9.8 с — mp4 18.9 с h264/aac 240p · «360p у ролика нет — качаю в лучшем доступном: 240p» | OK |  |
| 2 | YouTube | обычное 19 с | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 -b 128` | код 0, mp3, 17–21 с | код 0, 7.0 с — mp3 19.0 с mp3 141 кбит/с | OK |  |
| 3 | YouTube | обычное 19 с | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 -b 320` | код 0, mp3, 17–21 с | код 0, 8.1 с — mp3 19.0 с mp3 334 кбит/с | OK |  |
| 4 | YouTube | shorts, вертикальное | `https://www.youtube.com/shorts/fUrlyCjL8JA -f both -q 720` | код 0, mp3+mp4, ≤720p | код 0, 8.9 с — mp4 32.3 с h264/aac 720p; mp3 32.3 с mp3 213 кбит/с | OK |  |
| 5 | YouTube | shorts, max | `https://www.youtube.com/shorts/fUrlyCjL8JA -q max` | код 0, mp4 | код 0, 21.8 с — mp4 32.3 с h264/aac 1080p | OK |  |
| 6 | YouTube | 4K-ролик в 480 | `https://www.youtube.com/watch?v=aqz-KE-bpKQ -q 480 -c 0:10-0:20` | код 0, mp4, 9–11 с, ≤480p, ≥480p | код 0, 11.6 с — mp4 10.0 с h264/aac 480p | OK |  |
| 7 | YouTube | 4K-ролик в 1080, отрезок | `https://www.youtube.com/watch?v=aqz-KE-bpKQ -q 1080 -c 1:00-1:08` | код 0, mp4, 7–9 с, ≤1080p, ≥1080p | код 0, 9.7 с — mp4 8.0 с h264/aac 1080p | FIX | 93c01c6 — качался весь ролик: 37 → 10 с |
| 8 | YouTube | 4K-ролик в max, отрезок 5 с | `https://www.youtube.com/watch?v=aqz-KE-bpKQ -q max -c 2:00-2:05` | код 0, mp4, 4–6 с, ≥2160p | код 0, 18.2 с — mp4 5.0 с h264/aac 2160p | FIX | 93c01c6 — качался весь 4K-ролик: 87 → 18 с |
| 9 | YouTube | отрезок м:сс | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 -c 0:05-0:12` | код 0, mp3, 6–8 с | код 0, 4.6 с — mp3 7.0 с mp3 230 кбит/с | OK |  |
| 10 | YouTube | отрезок в секундах | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 -c 3-9` | код 0, mp3, 5–7 с | код 0, 3.7 с — mp3 6.0 с mp3 236 кбит/с | OK |  |
| 11 | YouTube | отрезок ч:мм:сс | `https://www.youtube.com/watch?v=aqz-KE-bpKQ -f mp3 -c 0:01:00-0:01:30` | код 0, mp3, 29–31 с | код 0, 3.2 с — mp3 30.0 с mp3 224 кбит/с | OK |  |
| 12 | YouTube | только начало | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 --from 0:15` | код 0, mp3, 3–5 с | код 0, 2.9 с — mp3 4.0 с mp3 259 кбит/с | OK |  |
| 13 | YouTube | только конец | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 --to 0:04` | код 0, mp3, 3–5 с | код 0, 2.5 с — mp3 4.0 с mp3 259 кбит/с | OK |  |
| 14 | YouTube | отрезок за концом (-y → целиком) | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 -c 5:00-6:00 -y` | код 0, mp3, 17–21 с | код 0, 2.9 с — mp3 19.0 с mp3 206 кбит/с · «Отрезок за пределами ролика — скачать ролик целиком (автоматически)» | OK |  |
| 15 | YouTube | конец за длиной ролика | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 -c 0:10-9:00` | код 0, mp3, 8–10 с | код 0, 2.8 с — mp3 9.0 с mp3 222 кбит/с · «Ролик короче отрезка — сохраню до конца (0:19)» | OK |  |
| 16 | YouTube | перевёрнутый отрезок | `https://www.youtube.com/watch?v=jNQXAC9IVRw -c 0:10-0:05` | код 1 | код 1, 0.2 с — ошибка: Конец отрезка должен быть позже начала | OK |  |
| 17 | YouTube | нулевой отрезок | `https://www.youtube.com/watch?v=jNQXAC9IVRw -c 0:05-0:05` | код 1 | код 1, 0.3 с — ошибка: Конец отрезка должен быть позже начала | OK |  |
| 18 | TikTok | видео NASA | `https://www.tiktok.com/@nasa/video/7686148096895569165 -f mp4` | код 0, mp4 | код 0, 3.0 с — mp4 58.6 с h264/aac 720p · «1080p у ролика нет — качаю в лучшем доступном: 720p» | OK |  |
| 19 | TikTok | видео NASA → звук | `https://www.tiktok.com/@nasa/video/7686148096895569165 -f mp3` | код 0, mp3 | код 0, 3.7 с — mp3 58.6 с mp3 203 кбит/с | OK |  |
| 20 | Instagram | reel NASA | `https://www.instagram.com/reel/DW-IhhKjdrC/ -f both -q 720` | код 0, mp3+mp4 | код 0, 6.7 с — mp4 36.5 с h264/aac 720p; mp3 36.5 с mp3 198 кбит/с | OK |  |
| 21 | YouTube+ | три ссылки, одна несуществующая | `https://www.youtube.com/watch?v=jNQXAC9IVRw https://www.youtube.com/shorts/fUrlyCjL8JA htt` | код 3, mp3+mp3 | код 3, 4.2 с — mp3 19.0 с mp3 206 кбит/с; mp3 32.3 с mp3 213 кбит/с; ошибка: Видео недоступно: удалено или закрыто в вашем регионе. | OK |  |
| 22 | — | мусор вместо ссылки | `не-ссылка` | код 1, «не похоже» | код 1, 0.5 с — ошибка: Это не похоже на ссылку: не-ссылка | OK |  |
| 23 | — | сайт без видео | `https://example.com/` | код 1 | код 1, 1.8 с — ошибка: Эта ссылка не поддерживается — нужна ссылка на конкретное видео. | OK |  |
| 24 | YouTube | несуществующий ролик | `https://www.youtube.com/watch?v=xxxxxxxxxxx` | код 1, «недоступно» | код 1, 1.3 с — ошибка: Видео недоступно: удалено или закрыто в вашем регионе. | OK |  |
| 25 | YouTube | ссылка обрезана на & (без кавычек) | `https://www.youtube.com/watch?feature=share` | код 1, «нет номера видео» | код 1, 0.5 с — ошибка: В ссылке нет номера видео (v=…): https://www.youtube.com/watch?feature=share | OK |  |
| 26 | — | несуществующий домен | `https://no-such-host-vydra.example/video/1` | код 1 | код 1, 24.9 с — ошибка: Нет связи с сайтом — проверьте интернет. | KNOWN | несуществующий домен считается «нет связи» и повторяется 3 раза (~25 с): так же выглядит и пропавший интернет, где повторы нужны |
| 27 | YouTube | -o в папку с пробелами | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 -o '/home/ihor/projects/video-downloade` | код 0, mp3 | код 0, 4.4 с — mp3 19.0 с mp3 206 кбит/с | OK |  |
| 28 | YouTube | хранилище на /mnt/c (drvfs) | `https://www.youtube.com/shorts/fUrlyCjL8JA -f both -q 360` | код 0, mp3+mp4, /mnt/c | код 0, 4.8 с — mp4 32.3 с h264/aac 360p; mp3 32.3 с mp3 213 кбит/с | OK |  |
| 29 | YouTube | -o /mnt/c/… с пробелами | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp4 -q 360 -o '/mnt/c/Users/Ihor/Downloads/` | код 0, mp4 | код 0, 4.3 с — mp4 19.1 с h264/aac 240p · «360p у ролика нет — качаю в лучшем доступном: 240p» | OK |  |
| 30 | YouTube | -o C:\…\новая\вложенная (путь Windows, папок нет) | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 -o C:\Users\Ihor\Downloads\vydra-matrix` | код 0, mp3 | код 0, 4.1 с — mp3 19.0 с mp3 206 кбит/с | FIX | 75c2ab3 |
| 31 | YouTube | --папка, которой нет (создаётся) | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 --папка YouTube/Тест` | код 0, mp3 | код 0, 2.9 с — mp3 19.0 с mp3 206 кбит/с | FIX | 66f1483 |
| 32 | YouTube | «\|» в названии, на /mnt/c, отрезок | `https://www.youtube.com/watch?v=eQcmzGIKrzg -f both -q 360 -c 0:10-0:14` | код 0, mp3+mp4, 3–5 с, /mnt/c | код 0, 9.0 с — mp4 4.0 с h264/aac 360p; mp3 3.9 с mp3 414 кбит/с | FIX | 93c01c6 — качалась вся часовая лекция: 21 → 9 с |
| 33 | YouTube | AC/DC — слэш в названии, отрезок | `https://www.youtube.com/watch?v=pAgnJDJN4VA -f mp3 -c 0:10-0:15` | код 0, mp3, 4–6 с, /mnt/c | код 0, 3.0 с — mp3 5.0 с mp3 420 кбит/с | OK |  |
| 34 | YouTube | повтор без --заново | `https://www.youtube.com/watch?v=jNQXAC9IVRw -f mp3 -b 128` | код 0, mp3 | код 0, 0.5 с — mp3 19.0 с mp3 206 кбит/с | OK |  |
| 35 | YouTube | возрастное ограничение (18+) | `https://www.youtube.com/watch?v=HtVdAasjOgU -f mp3` | код 0, mp3 | код 0, 6.0 с — mp3 141.9 с mp3 196 кбит/с | OK | yt-dlp сейчас обходит возрастной барьер без входа |
| 36 | YouTube | приватное | `https://www.youtube.com/watch?v=yZIXLfi8CZQ` | код 1, «приватное» | код 1, 1.3 с — ошибка: Видео приватное — скачать его нельзя. | OK |  |
| 37 | YouTube | удалённое | `https://www.youtube.com/watch?v=Q39EVAstoRM` | код 1, «недоступно» | код 1, 1.3 с — ошибка: Видео недоступно: удалено или закрыто в вашем регионе. | OK |  |
| 38 | YouTube | прямой эфир (NASA ISS, /@NASA/live) | `https://www.youtube.com/@NASA/live -f mp3` | код 1, «прямой эфир» | код 1, 2.3 с — ошибка: Это прямой эфир — его можно будет скачать, когда трансляция закончится | FIX | 66f1483 |
| 39 | YouTube | эфир закончился, записи нет | `https://www.youtube.com/watch?v=jfKfPfyJRdk -f mp3` | код 1, «недоступна» | код 1, 1.6 с — ошибка: Запись этой трансляции недоступна — эфир закончился, а запись не сохранили или скрыли. | FIX | d93067b |
| 40 | YouTube | плейлист из 4 роликов, mp3 | `https://www.youtube.com/playlist?list=PL6IaIsEjSbf96XFRuNccS_RuEXwNdsoEu -f mp3 -b 128` | код 0, mp3+mp3+mp3+mp3 | код 0, 60.3 с — mp3 516.1 с mp3 128 кбит/с; mp3 362.5 с mp3 128 кбит/с; mp3 162.6 с mp3 129 кбит/с; mp3 154.1 с mp3 129 кбит/с | OK |  |
| 41 | YouTube | плейлист > 50 (все ролики NASA) без --весь-плейлист | `https://www.youtube.com/playlist?list=UULA_DiR1FfKNvjuUpBHmylQ -f mp3` | код 1, «больше чем из 50» | код 1, 1.8 с — ошибка: Это плейлист больше чем из 50 роликов — подтвердите, что хотите скачать его целиком | OK | подсказка «добавьте --весь-плейлист» |
| 42 | YouTube | 1:07:40 — только звук | `https://www.youtube.com/watch?v=eQcmzGIKrzg -f mp3 -b 128` | код 0, mp3, 4050–4070 с | код 0, 60.3 с — mp3 4059.7 с mp3 128 кбит/с | OK |  |
| 43 | YouTube | 1:07:40 — отрезок в конце | `https://www.youtube.com/watch?v=eQcmzGIKrzg -q 360 -c 1:05:00-1:05:30` | код 0, mp4, 29–31 с, ≤360p | код 0, 24.8 с — mp4 30.0 с h264/aac 360p · «Отрезок отдельно не скачался — скачал ролик целиком и вырезал сам» | KNOWN | отрезок качается куском (93c01c6), но YouTube отдаёт кусок примерно в 2× реального времени: 30 с — 15–25 с; кусок длиннее 60 с — весь ролик и рез у себя |
| 44 | YouTube | youtu.be со временем ?t= | `https://youtu.be/jNQXAC9IVRw?t=5 -f mp3` | код 0, mp3, 17–21 с | код 0, 5.9 с — mp3 19.0 с mp3 206 кбит/с | OK |  |
| 45 | YouTube | music.youtube.com | `https://music.youtube.com/watch?v=jNQXAC9IVRw -f mp3` | код 0, mp3, 17–21 с | код 0, 5.9 с — mp3 19.0 с mp3 206 кбит/с | OK |  |
| 46 | TikTok | другое видео (27 с) | `https://www.tiktok.com/@patroxofficial/video/6742501081818877190` | код 0, mp4 | код 0, 5.4 с — mp4 27.5 с h264/aac 540p · «1080p у ролика нет — качаю в лучшем доступном: 576p» | OK |  |
| 47 | TikTok | фото-слайдшоу (mp4 → только звук) | `https://www.tiktok.com/@_le_cannibale_/video/7139980461132074283 -y` | код 0, mp3 | код 1, 3.7 с — ошибка: Сайт не показывает этот ролик с вашего IP-адреса (ограничение по региону) — нужен VPN или cookies. | SITE | TikTok закрыл пост для этого IP («IP address is blocked»); теперь это сразу понятная ошибка без 3 повторов; разбор ошибки — d93067b |
| 48 | TikTok | слайдшоу, длинное название | `https://www.tiktok.com/@hara_yoimiya/video/7253412088251534594 -f mp3` | код 0, mp3 | код 1, 4.2 с — ошибка: Сайт не показывает этот ролик с вашего IP-адреса (ограничение по региону) — нужен VPN или cookies. | SITE | то же; разбор ошибки — d93067b |
| 49 | Instagram | reel без звука, просим оба | `https://www.instagram.com/reel/Chunk8-jurw/ -f both` | код 0, mp4 | код 0, 5.0 с — mp4 5.0 с h264/— 720p · «1080p у ролика нет — качаю в лучшем доступном: 720p» | FIX | d9d7d3c — MP4 сохранён, предупреждение «нет звука» |
| 50 | Instagram | карусель из 3 видео | `https://www.instagram.com/p/BQ0eAlwhDrw/` | код 0, mp4+mp4+mp4 | код 0, 4.7 с — mp4 4.0 с h264/— 640p; mp4 31.7 с h264/— 640p; mp4 6.5 с h264/— 640p | OK |  |
| 51 | Vimeo | The New Vimeo Player | `https://vimeo.com/76979871 -q 360` | код 0, mp4, 60–64 с, ≤360p | код 1, 2.7 с — ошибка: Сайт просит войти в аккаунт. Добавьте cookies в настройках и повторите. | SITE | Vimeo: веб-клиент требует входа (cookies) — сообщение и подсказка «выдра cookies» |
| 52 | Vimeo | player.vimeo.com | `https://player.vimeo.com/video/76979871 -q 360` | код 0, mp4, 60–64 с, ≤360p | код 1, 3.8 с — ошибка: Видео защищено DRM — скачать его нельзя. · «360p не скачался (защита DRM) — взял другой источник того же качества» | SITE | все форматы ролика под DRM — «Видео защищено DRM» (после попытки других форматов); разбор ошибки — d9d7d3c |
| 53 | X | твит с видео | `https://x.com/historyinmemes/status/1790637656616943991` | код 0, mp4 | код 0, 3.7 с — mp4 15.6 с h264/aac 720p · «1080p у ролика нет — качаю в лучшем доступном: 720p» | OK |  |
| 54 | Reddit | v.redd.it | `https://www.reddit.com/r/videos/comments/6rrwyj/that_small_heart_attack/ -f both` | код 0, mp3+mp4 | код 0, 5.4 с — mp4 11.8 с h264/aac 352p; mp3 11.8 с mp3 210 кбит/с · «1080p у ролика нет — качаю в лучшем доступном: 352p» | OK |  |
| 55 | Twitch | клип только в 1080p, просим 480 | `https://clips.twitch.tv/FaintLightGullWholeWheat -q 480` | код 0, mp4, ≥1080p | код 0, 4.8 с — mp4 32.1 с h264/aac 1080p · «480p у ролика нет, меньше 1080p не бывает — сохраняю 1080p» | FIX | d9d7d3c — заметка «480p у ролика нет… сохраняю 1080p» |
| 56 | SoundCloud | трек, просим MP4 (-y → MP3) | `https://soundcloud.com/jaimemf/youtube-dl-test-video-a-y-baw/s-8Pjrp -y` | код 0, mp3 | код 0, 4.2 с — mp3 9.9 с mp3 193 кбит/с · «У этой ссылки нет видео — скачать mp3 (автоматически)» | OK |  |
| 57 | SoundCloud | трек в MP3 320 | `http://soundcloud.com/ethmusic/lostin-powers-she-so-heavy -f mp3 -b 320` | код 0, mp3, 140–146 с | код 0, 9.9 с — mp3 143.2 с mp3 323 кбит/с | OK |  |

## Сценарии вручную

| № | Сценарий | Как проверено | Результат | Статус |
|---|---|---|---|---|
| M1 | Прогресс в настоящем терминале | `.sprint/tty_run.py` (pty 120 колонок), YouTube `-f both -q 360` | живые полосы по задаче, этапы «Получаю информацию → Скачиваю → Склейка → Конвертирую», итог с полными путями (кликабельны в Windows Terminal) | OK |
| M2 | Вывод в пайп (`… \| cat`) | две ссылки, одна плохая | без переносов строк и цветов; этапы — по строке; ошибка с подсказкой; «Готово с ошибками», код 3 | FIX 57d3d24 |
| M3 | Ctrl+C во время загрузки | pty, Ctrl+C на 5-й секунде (Big Buck Bunny 4K) | «Отменено», код 130 за 0,3 с, воркер yt-dlp убит, папка задачи удалена, в хранилище пусто | OK |
| M4 | Ctrl+C во время конвертации | `convert` 40 с 1080p VP9 → MP4, Ctrl+C на 3-й секунде | код 130; ffmpeg остановлен; **копия исходника в `<work>/uploads` оставалась** — теперь убирается | FIX add7724 |
| M5 | Второй Ctrl+C | pty, Ctrl+C дважды | подсказка «Ctrl+C ещё раз — выйти сразу», выход немедленно | FIX 57d3d24 |
| M6 | Ссылка из буфера обмена Windows | `.sprint/clipboard_case.py`: сохранить буфер → подложить текст со ссылкой и `&` → `vydra d -f mp3` → вернуть буфер | «Ссылка из буфера: …&t=3», файл скачан, буфер восстановлен | OK |
| M7 | Интерактивный режим | pty: ссылка → «2» (MP3) → 128 → отрезок `0:02-0:06` → пустая строка | **не выходил по пустой строке: `powershell.exe` (чтение буфера) наследовал терминал и съедал ввод**; теперь все внешние программы — со `stdin=/dev/null`, повторная ссылка не качается дважды | FIX d93067b |
| M8 | «Показать в папке» из CLI и из API | `vydra open пробел`, `POST /api/library/{id}/reveal`, `download --показать`; окна Проводника прочитаны через Shell.Application | Проводник открыт, выделен нужный файл — и на `/mnt/c` (пробелы, запятая, `&`, скобки), и на `\\wsl.localhost\…` | OK |
| M9 | Порт занят Windows-программой | `TcpListener` на 127.0.0.1:8795 со стороны Windows, `vydra ui -p 8795` | `bind()` видит занятость за 2 мс, «Порт 8795 занят — запускаю на 8796» | OK |
| M10 | `vydra ui` / `stop` / `restart` с открытой вкладкой (SSE) | сервер :8797, `curl -N /api/events`, затем `restart` и `stop` | баннер 0,2 с, `/api/health` 0,5 с; restart 6,8 → **3,2 с**, stop — 1,2 с (SSE закрывается по сигналу) | FIX c0653b5 |
| M11 | Повторный `install.sh` при работающем `vydra ui` | `.sprint/install_test.sh` с временным HOME: новая → новая, старая (main) → новая | новая: перезапуск в том же окне (тот же pid, новый `started`); старая: остановлена, поднята в фоне | OK |
| M12 | `-o` в папку с пробелами на `/mnt/c` и в новую вложенную (путь `C:\…`) | см. случаи 27, 29, 30 | создаётся; было «Папка хранилища недоступна: [Errno 2] Папка хранилища недоступна: …» | FIX 75c2ab3 |

## Что нашла матрица

| Сбой | Причина | Исправление | Тест |
|---|---|---|---|
| Прямой эфир (`/@NASA/live`) качался бы бесконечно | yt-dlp пишет эфир, пока тот идёт; круглосуточный — никогда не кончается | `_review`: `is_live` → постоянная ошибка «Это прямой эфир…»; `is_live` в превью; `info` объясняет | `test_live_stream_is_refused_not_recorded_forever` |
| «This live stream recording is not available», «IP address is blocked» — 3 попытки по 5–15 с впустую | неизвестные ошибки считались временными | классификация: постоянные, с понятным текстом (VPN/cookies, «запись недоступна») | `test_error_classification`, `test_upcoming_stream_is_permanent_error` |
| Reel без звука с `-f both`: код 1, хотя MP4 сохранён | MP3-шаг падал ошибкой задачи | `NoAudio`: при «оба» MP4 остаётся, задача успешна с предупреждением | `test_both_formats_for_silent_video_keeps_the_video` |
| `-q 480` у клипа Twitch молча дал 1080p | заметка была только для «качества меньше просимого» | заметка «480p у ролика нет, меньше 1080p не бывает — сохраняю 1080p» | `test_only_bigger_quality_is_a_note` |
| Vimeo: DRM на выбранном формате — сразу ошибка | «drm protected» не считался проблемой формата | пробуем другие форматы; нет — «Видео защищено DRM» | `test_drm_format_falls_back_then_explains` |
| `--папка "YouTube/Тест"`, которой нет, — отказ | консоль требовала готовую папку | создаётся (в терминале — с вопросом), плохое имя объясняется | `test_missing_folder_is_created`, `test_bad_folder_name_is_explained` |
| `-o` в новую вложенную папку на `/mnt/c` — «хранилище недоступно» | `available()` не создаёт хранилище, если нет родителя вне домашней папки | `-o` создаёт папку сам; `C:\…` в WSL — путь Windows | `test_out_into_new_nested_folder` |
| Интерактивный режим не выходил по пустой строке | `powershell.exe`/`ffprobe` наследовали stdin-терминал | `stdin=DEVNULL` у всех внешних программ | `test_external_programs_never_read_the_terminal` |
| После сбоя/Ctrl+C консоль оставляла папку задачи и копию исходника | «прервано — продолжу при перезапуске» имеет смысл только для сервера | консоль (очередь не сохраняется) убирает сразу | `test_console_shutdown_cleans_interrupted_jobs`, `test_failed_conversion_keeps_upload_only_for_the_server` |
| После выхода консоли ffmpeg (постеры) жил сиротой | фоновый поток постеров в короткоживущей команде | `Library(enrich=False)` в консоли | `test_console_library_starts_no_background_ffmpeg` |
| Отрезок 5 с из 10-минутного 4K-ролика — 87 с, 4 с из часовой лекции — 21 с | для отрезка качался весь ролик (диапазоны yt-dlp через ffmpeg считались ненадёжными) | короткий отрезок (≤ 60 с) ролика длиннее 3 мин — куском (`download_ranges`); кусок — последние (end − start) с файла; сбой — откат на весь ролик, повтор — всегда весь ролик. Сверено с резом из целого ролика: звук ±20–50 мс (корреляция 0,98–0,99), кадры PSNR 31–35 дБ. Стало 18 с и 9 с | `test_section_decision`, `test_downloaded_section_is_cut_in_its_own_time`, `test_retry_after_failure_downloads_the_whole_video`, живой `test_real_short_clip_of_long_video_is_downloaded_as_a_piece` |
| Фото-пост TikTok (`/photo/…`) — «ссылка не поддерживается» | `_VALID_URL` yt-dlp знает только `/video/` | `/photo/` → `/video/` для yt-dlp (звук слайдшоу) | `test_tiktok_photo_post_goes_to_ytdlp_as_video` |

## Не проверено и почему
- **Закрытый аккаунт Instagram, приватные видео с доступом** — нужны cookies пользователя; поведение без них проверено
  (случай 36: понятная ошибка), подключение — `vydra cookies` (`test_cookies_command`).
- **Обрыв сети посреди загрузки** — в этой машине не воспроизвести без root; докачка `.part` и повторы покрыты
  `test_queue_survives_kill_and_resumes_partial_data`, `test_transient_errors_are_retried_until_success`.
- **Картинки фото-слайдшоу TikTok** — yt-dlp их не качает, выдра сохраняет звук; сами посты для этого IP закрыты (SITE).
- **macOS** — проверить негде: ветки `open -R`, `pbpaste`, `lsof`, `~/Downloads` покрыты тестами с подменой ОС
  (`tests/test_system.py`, `test_clipboard_readers`, `test_old_server_without_pid_file_is_found_by_lock_holder`).
