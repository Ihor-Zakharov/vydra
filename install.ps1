# vydra installer for Windows: irm https://raw.githubusercontent.com/Ihor-Zakharov/vydra/main/install.ps1 | iex
# Works in Windows PowerShell 5.1 and PowerShell 7, with any ExecutionPolicy (irm | iex runs no script files).
# ASCII-only on purpose: PowerShell 5.1 reads BOM-less files as ANSI, and a BOM breaks `iex`.
# Russian messages are \u-escaped; an English gloss is next to each one.
# Re-running upgrades vydra. From a local checkout: $env:VYDRA_REPO = 'C:\path\to\vydra'; .\install.ps1
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$Repo = if ($env:VYDRA_REPO) { $env:VYDRA_REPO } else { 'https://github.com/Ihor-Zakharov/vydra/archive/refs/heads/main.zip' }
$RuName = [regex]::Unescape('\u0432\u044b\u0434\u0440\u0430')  # vydra in Cyrillic
$M = @{}
$M.wait = [regex]::Unescape('\u044d\u0442\u043e \u043c\u043e\u0436\u0435\u0442 \u0437\u0430\u043d\u044f\u0442\u044c \u043f\u0430\u0440\u0443 \u043c\u0438\u043d\u0443\u0442 \u2014 \u043e\u043a\u043d\u043e \u043d\u0435 \u0437\u0430\u0432\u0438\u0441\u043b\u043e')  # this can take a couple of minutes
$M.title = [regex]::Unescape('\u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0430')  # installation
$M.uv = [regex]::Unescape('\u0421\u0442\u0430\u0432\u043b\u044e uv (\u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440 Python \u043e\u0442 Astral)\u2026')  # installing uv
$M.uv_fail = [regex]::Unescape('\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u043e\u0441\u0442\u0430\u0432\u0438\u0442\u044c uv. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0438\u043d\u0442\u0435\u0440\u043d\u0435\u0442 \u0438 \u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0443 \u0435\u0449\u0451 \u0440\u0430\u0437.')  # uv install failed
$M.vydra = [regex]::Unescape('\u0421\u0442\u0430\u0432\u043b\u044e \u0432\u044b\u0434\u0440\u0443 (Python \u043d\u0443\u0436\u043d\u043e\u0439 \u0432\u0435\u0440\u0441\u0438\u0438 uv \u0441\u043a\u0430\u0447\u0430\u0435\u0442 \u0441\u0430\u043c)\u2026')  # installing vydra
$M.vydra_fail = [regex]::Unescape('\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c \u0432\u044b\u0434\u0440\u0443 \u2014 \u0442\u0435\u043a\u0441\u0442 \u043e\u0448\u0438\u0431\u043a\u0438 \u0432\u044b\u0448\u0435.')  # vydra install failed
$M.installed = [regex]::Unescape('\u0432\u044b\u0434\u0440\u0430 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0430')  # vydra installed
$M.check_fail = [regex]::Unescape('\u041a\u043e\u043c\u0430\u043d\u0434\u0430 \u043d\u0435 \u0437\u0430\u043f\u0443\u0441\u043a\u0430\u0435\u0442\u0441\u044f: ')  # command does not start
$M.tools = [regex]::Unescape('\u0421\u0442\u0430\u0432\u043b\u044e FFmpeg \u0438 \u043e\u0441\u0442\u0430\u043b\u044c\u043d\u043e\u0435, \u043f\u0440\u043e\u0432\u0435\u0440\u044f\u044e \u0441\u0438\u0441\u0442\u0435\u043c\u0443\u2026')  # installing FFmpeg etc., running doctor
$M.done = [regex]::Unescape('\u0413\u043e\u0442\u043e\u0432\u043e!')  # done
$M.how = [regex]::Unescape('\u0421\u043a\u043e\u043f\u0438\u0440\u0443\u0439\u0442\u0435 \u0441\u0441\u044b\u043b\u043a\u0443 \u0432 \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u0435 \u0438 \u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u0435:')  # copy a link in the browser and run
$M.how_cmd = [regex]::Unescape('\u0432\u044b\u0434\u0440\u0430 \u0441\u043a\u0430\u0447\u0430\u0442\u044c -\u0444 \u043c\u043f3')  # vydra download -f mp3
$M.how_note = [regex]::Unescape('(\u0441\u0441\u044b\u043b\u043a\u0430 \u0432\u043e\u0437\u044c\u043c\u0451\u0442\u0441\u044f \u0438\u0437 \u0431\u0443\u0444\u0435\u0440\u0430 \u043e\u0431\u043c\u0435\u043d\u0430; \u0438\u043b\u0438 \u0443\u043a\u0430\u0436\u0438\u0442\u0435 \u0435\u0451 \u0432 \u043a\u0430\u0432\u044b\u0447\u043a\u0430\u0445)')  # link comes from the clipboard or quote it
$M.ui = [regex]::Unescape('\u0432\u0435\u0431-\u0438\u043d\u0442\u0435\u0440\u0444\u0435\u0439\u0441')  # web UI
$M.cli = [regex]::Unescape('\u043a\u043e\u043d\u0441\u043e\u043b\u044c \u0438 \u0441\u043f\u0440\u0430\u0432\u043a\u0430')  # console and help
$M.shortcut = [regex]::Unescape('\u0438\u043b\u0438 \u044f\u0440\u043b\u044b\u043a \u00ab\u0412\u044b\u0434\u0440\u0430\u00bb \u043d\u0430 \u0440\u0430\u0431\u043e\u0447\u0435\u043c \u0441\u0442\u043e\u043b\u0435')  # or the desktop shortcut
$M.newwin = [regex]::Unescape('\u041e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u043d\u043e\u0432\u043e\u0435 \u043e\u043a\u043d\u043e PowerShell, \u0447\u0442\u043e\u0431\u044b \u043a\u043e\u043c\u0430\u043d\u0434\u044b \u0437\u0430\u0440\u0430\u0431\u043e\u0442\u0430\u043b\u0438 \u0432\u0435\u0437\u0434\u0435.')  # open a new PowerShell window
$M.open = [regex]::Unescape('\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0432\u044b\u0434\u0440\u0443 \u0441\u0435\u0439\u0447\u0430\u0441? [Y/n]')  # open vydra now?

function Say([string]$Icon, [string]$Color, [string]$Text) {
    Write-Host "  $Icon " -ForegroundColor $Color -NoNewline
    Write-Host $Text
}

function Fail([string]$Text) {
    Say 'x' Red $Text
    exit 1
}

Write-Host ''
Write-Host '  vydra' -ForegroundColor Magenta -NoNewline
Write-Host ('  ' + $M.title) -ForegroundColor DarkGray
Write-Host ''

# 1. uv: installs Python and dependencies into the user profile, no admin rights needed
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say '~' Yellow $M.uv
Write-Host ('    ' + $M.wait) -ForegroundColor DarkGray
    & powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail $M.uv_fail }
}
Say '+' Green (& uv --version)

# 2. vydra itself (re-run = upgrade; --reinstall-package rebuilds a local checkout too)
Say '~' Yellow $M.vydra
Write-Host ('    ' + $M.wait) -ForegroundColor DarkGray
& uv tool install --force --reinstall-package vydra --python 3.13 $Repo
if ($LASTEXITCODE -ne 0) { Fail $M.vydra_fail }
& uv tool update-shell *> $null
$bin = (& uv tool dir --bin)
$env:Path = "$bin;$env:Path"
$version = ''
foreach ($name in @('vydra', $RuName)) {
    $version = & $name --version 2>&1
    if ($LASTEXITCODE -ne 0) { Fail ($M.check_fail + $name) }
}
Say '+' Green ($M.installed + ': ' + ($version | Select-Object -First 1))

# 3. FFmpeg, JS runtime for YouTube, storage folders, desktop shortcut, Tab completion + smart links
Write-Host ''
Say '~' Yellow $M.tools
Write-Host ('    ' + $M.wait) -ForegroundColor DarkGray
& vydra doctor --fix
& vydra completion *> $null

Write-Host ''
Write-Host ('  ' + $M.done + ' ') -ForegroundColor Green -NoNewline
Write-Host $M.how
Write-Host ('    ' + $M.how_cmd) -ForegroundColor Cyan
Write-Host ('    ' + $M.how_note) -ForegroundColor DarkGray
Write-Host ('    ' + $RuName + ' ui') -ForegroundColor Cyan -NoNewline
Write-Host ('      ' + $M.ui) -ForegroundColor DarkGray
Write-Host ('    ' + $RuName + ' --help') -ForegroundColor Cyan -NoNewline
Write-Host ('  ' + $M.cli) -ForegroundColor DarkGray
Write-Host ('    ' + $M.shortcut) -ForegroundColor DarkGray
Write-Host ('  ' + $M.newwin) -ForegroundColor DarkGray
Write-Host ''
if ($env:CI -or $env:VYDRA_NO_LAUNCH -or -not [Environment]::UserInteractive) { exit 0 }
$answer = Read-Host ('  ' + $M.open)
if ($answer -notmatch '^(n|N|\u043d|\u041d)') {
    Start-Process -FilePath (Join-Path $bin 'vydra.exe') -ArgumentList 'ui' -WindowStyle Minimized
}
