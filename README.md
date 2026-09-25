# ▲ vydra

Download videos from YouTube, TikTok, Instagram and ~1800 other sites — no watermarks, straight to MP4 or MP3.
Web UI + console app, runs locally: no ads, no accounts, no cloud.

<p align="center"><img src="docs/screenshots/ui.png" alt="vydra web UI" width="880"></p>

## Install

No admin rights needed. The installer brings Python, FFmpeg and everything else into your user folder.

**Windows** — open PowerShell and paste:

```powershell
irm https://raw.githubusercontent.com/Ihor-Zakharov/vydra/main/install.ps1 | iex
```

**macOS / Linux / WSL** — open a terminal and paste:

```sh
curl -LsSf https://raw.githubusercontent.com/Ihor-Zakharov/vydra/main/install.sh | sh
```

A **vydra** shortcut appears on your desktop (app menu on Linux). Already have [uv](https://docs.astral.sh/uv/)?
`uv tool install git+https://github.com/Ihor-Zakharov/vydra && vydra doctor --fix`.

## Use

Open the shortcut (or run `vydra ui`), paste a link, pick MP4 / MP3 / both, press **Download**.
Files land in `Downloads/VideoDownloader`, sorted by site, with an offline `Кинотеатр.html` player next to them.

## Commands

```sh
vydra                               # interactive mode
vydra ui / vydra stop               # start / stop the web UI (http://localhost:8765)
vydra d '<url>'                     # download video (MP4, up to 1080p)
vydra d '<url>' -f mp3              # audio only
vydra d '<url>' -f both -q 720      # MP4 720p + MP3
vydra d '<url>' --clip 1:00-5:00    # only a fragment
vydra d -f mp3                      # no URL = take it from the clipboard
vydra convert file.mov -f mp4       # convert your own file
vydra list                          # what's in the library
vydra doctor --fix                  # check and repair everything
vydra update                        # update vydra and yt-dlp
```

Put URLs in **single quotes** (`&` in YouTube links breaks bash/zsh). `vydra <command> --help` for details,
`vydra lang ru` for a Russian console.

## Troubleshooting

| Problem | Fix |
|---|---|
| A site stopped working | `vydra update` — sites change, yt-dlp updates weekly |
| "Sign in required" / private or age-restricted video | export `cookies.txt` from your browser, then `vydra cookies path/to/cookies.txt` |
| FFmpeg errors, missing shortcut, anything else | `vydra doctor --fix` |
| UI stuck or outdated after update | `vydra stop`, then `vydra ui` |

Uninstall: `uv tool uninstall vydra` (downloaded files stay).

## Development

```sh
git clone https://github.com/Ihor-Zakharov/vydra && cd vydra
uv run vydra ui        # run from source
uv run pytest -q       # fast tests, no network
```

Download only what you have the rights to. Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [FFmpeg](https://ffmpeg.org).
