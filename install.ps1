# Установка выдры одной командой (PowerShell):
#   irm https://raw.githubusercontent.com/Ihor-Zakharov/vydra/main/install.ps1 | iex
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Repo = if ($env:VYDRA_REPO) { $env:VYDRA_REPO } else { 'git+https://github.com/Ihor-Zakharov/vydra' }

function Say([string]$Icon, [string]$Color, [string]$Text) {
    Write-Host "  $Icon " -ForegroundColor $Color -NoNewline
    Write-Host $Text
}

Write-Host ''
Write-Host '  ▲ выдра' -ForegroundColor Magenta -NoNewline
Write-Host '  установка' -ForegroundColor DarkGray
Write-Host ''

# 1. uv — ставит Python и зависимости, ничего не трогая в системе
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say '~' Yellow 'Ставлю uv (менеджер Python от Astral)...'
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Say '✓' Green (uv --version)

# 2. сама выдра (Python нужной версии uv скачает сам)
Say '~' Yellow 'Ставлю выдру...'
uv tool install --force --python 3.13 $Repo
if ($LASTEXITCODE -ne 0) { Say '✗' Red 'Не удалось установить выдру'; exit 1 }
uv tool update-shell *> $null
$env:Path = "$(uv tool dir --bin);$env:Path"
Say '✓' Green 'выдра установлена'

# 3. FFmpeg, JS-движок для YouTube, папка-хранилище, ярлык на рабочем столе
Write-Host ''
vydra doctor --fix
vydra completion --shell powershell *> $null

Write-Host ''
Write-Host '  Готово! ' -ForegroundColor Green -NoNewline
Write-Host 'Ярлык «Выдра» на рабочем столе, или команды:'
Write-Host '    vydra ui ' -ForegroundColor Cyan -NoNewline; Write-Host '  веб-интерфейс' -ForegroundColor DarkGray
Write-Host '    vydra    ' -ForegroundColor Cyan -NoNewline; Write-Host '  консольная версия' -ForegroundColor DarkGray
Write-Host ''
$answer = Read-Host '  Открыть выдру сейчас? [Y/n]'
if ($answer -notmatch '^(n|н)') { Start-Process -FilePath 'vydra' -ArgumentList 'ui' -WindowStyle Minimized }
