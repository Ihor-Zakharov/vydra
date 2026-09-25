<p align="center">
  <img src="vydra/static/icons/icon-192.png" alt="" width="96">
</p>

<h1 align="center">V Y D R A</h1>

<p align="center">
  <b>Paste a link — take the video.</b><br>
  YouTube · TikTok · Instagram · ~1800 more sites<br>
  <sub>MP4 or MP3 · no watermarks · runs on your machine · no ads, no accounts, no cloud</sub>
</p>

<p align="center">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-000?style=for-the-badge&logo=windows&logoColor=white">
  <img alt="macOS" src="https://img.shields.io/badge/macOS-000?style=for-the-badge&logo=apple&logoColor=white">
  <img alt="Linux" src="https://img.shields.io/badge/Linux%20%C2%B7%20WSL-000?style=for-the-badge&logo=linux&logoColor=white">
  <img alt="Python 3.13" src="https://img.shields.io/badge/Python-3.13-000?style=for-the-badge&logo=python&logoColor=white">
</p>

<p align="center">
  <img src="docs/screenshots/ui.png" alt="vydra" width="900">
</p>

<table>
<tr>
<td width="33%" valign="top"><b>Clean files</b><br><sub>TikTok without the watermark. MP4 as H.264 + AAC that plays anywhere, MP3 with cover art and tags.</sub></td>
<td width="33%" valign="top"><b>Preview & trim</b><br><sub>Title, author, length and qualities right after pasting. Cut 1:00–5:00 with a slider, frame-accurate.</sub></td>
<td width="33%" valign="top"><b>Library & offline cinema</b><br><sub>Sorted into <code>Видео/&lt;site&gt;</code> and <code>Аудио/&lt;site&gt;</code>. <code>Кинотеатр.html</code> plays everything offline, even without vydra.</sub></td>
</tr>
<tr>
<td valign="top"><b>Converter</b><br><sub>Drop any video or audio file — get MP4 or MP3, whole or a fragment.</sub></td>
<td valign="top"><b>Console app</b><br><sub>Download plans, live progress bars, Tab completion, <code>--json</code> for scripts.</sub></td>
<td valign="top"><b>Self-repair</b><br><sub><code>vydra doctor --fix</code> fetches FFmpeg, updates yt-dlp, rebuilds the library, recreates the shortcut.</sub></td>
</tr>
</table>

---

### `01` &nbsp;/&nbsp; INSTALL

No admin rights. The installer brings Python, FFmpeg and the rest into your user folder.

**Windows** — PowerShell:

```powershell
irm https://raw.githubusercontent.com/Ihor-Zakharov/vydra/main/install.ps1 | iex
```

**macOS · Linux · WSL** — terminal:

```sh
curl -LsSf https://raw.githubusercontent.com/Ihor-Zakharov/vydra/main/install.sh | sh
```

A **vydra** shortcut lands on your desktop (app menu on Linux).
Have [uv](https://docs.astral.sh/uv/) already? `uv tool install git+https://github.com/Ihor-Zakharov/vydra && vydra doctor --fix`

### `02` &nbsp;/&nbsp; USE

Open the shortcut (or `vydra ui`) → paste a link → **Download**.
Files go to `Downloads/VideoDownloader`, sorted by site, with an offline player `Кинотеатр.html` beside them.

### `03` &nbsp;/&nbsp; CONSOLE

```sh
vydra                               # interactive mode
vydra ui  |  vydra stop             # web UI on http://localhost:8765
vydra d '<url>'                     # video, MP4 up to 1080p
vydra d '<url>' -f mp3              # audio only
vydra d '<url>' -f both -q 720      # MP4 720p + MP3
vydra d '<url>' --clip 1:00-5:00    # just a fragment
vydra d -f mp3                      # no URL — takes it from the clipboard
vydra convert file.mov -f mp4       # convert your own file
vydra list                          # what's in the library
vydra doctor --fix                  # check and repair everything
vydra update                        # update vydra and yt-dlp
```

Quote URLs with **single quotes** — `&` in YouTube links breaks bash/zsh.
`vydra <command> --help` for the rest · `vydra lang ru` for a Russian console.

### `04` &nbsp;/&nbsp; WHEN SOMETHING BREAKS

| | |
|---|---|
| A site stopped downloading | `vydra update` — sites change, yt-dlp ships fixes weekly |
| "Sign in required", private or age-gated video | export `cookies.txt` from your browser → `vydra cookies path/to/cookies.txt` |
| FFmpeg errors, missing shortcut, anything else | `vydra doctor --fix` |
| UI stuck or stale after an update | `vydra stop`, then `vydra ui` |

| Port 8765 busy | vydra picks the next free one; or `vydra ui --port 9000` |

Uninstall: `uv tool uninstall vydra` — your downloads stay.

### `05` &nbsp;/&nbsp; DEVELOP

```sh
git clone https://github.com/Ihor-Zakharov/vydra && cd vydra
uv run vydra ui        # run from source
uv run pytest -q       # fast tests, no network
uv run pytest -m live  # real downloads
```

```
vydra/
  cli.py         console app (Typer + Rich)
  main.py        HTTP API (FastAPI) + web UI
  jobs.py        queue: download → convert → file into the library
  downloader.py  yt-dlp in a subprocess, watermark-free format picking
  media.py       ffmpeg: MP4, MP3 with cover, frame-accurate clips
  library.py     library layout, index, posters, offline cinema
  health.py      doctor: checks and repairs
  system.py      OS specifics: Windows, WSL, macOS, Linux
  static/        web UI (no CDN, everything local)
```

Project state and hand-off notes: [`HANDOFF.md`](HANDOFF.md).

---

<p align="center"><sub>Download only what you have the rights to. Built on <a href="https://github.com/yt-dlp/yt-dlp">yt-dlp</a> and <a href="https://ffmpeg.org">FFmpeg</a>.</sub></p>
