# vydra installer for Windows: irm https://raw.githubusercontent.com/Ihor-Zakharov/vydra/main/install.ps1 | iex
# Works in Windows PowerShell 5.1 and PowerShell 7, with any ExecutionPolicy (irm | iex runs no script files),
# without admin rights, winget, git or Python: uv brings its own Python, vydra doctor brings FFmpeg and Deno.
# ASCII-only on purpose: PowerShell 5.1 reads BOM-less files as ANSI, and a BOM breaks `iex`.
# Russian messages are \u-escaped; an English gloss is next to each one.
# Re-running upgrades vydra (a running vydra is stopped first and started again after).
# From a local checkout: $env:VYDRA_REPO = 'C:\path\to\vydra'; .\install.ps1
# Test knobs: VYDRA_NO_LAUNCH=1 (no questions, no windows), UV_NO_MODIFY_PATH=1 (do not touch the user PATH).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # PowerShell 5.1 downloads 10x slower while drawing a progress bar
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
# PowerShell 5.1 on older .NET speaks only TLS 1.0 by default; GitHub and astral.sh need TLS 1.2
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072 } catch { }
$VydraSelf = $MyInvocation.MyCommand.Path  # set when run as a file; empty under irm | iex
$Repo = if ($env:VYDRA_REPO) { $env:VYDRA_REPO } else { 'https://github.com/Ihor-Zakharov/vydra/archive/refs/heads/main.zip' }
$MinUv = [version]'0.5.0'
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
$M.stop = [regex]::Unescape('\u041e\u0441\u0442\u0430\u043d\u0430\u0432\u043b\u0438\u0432\u0430\u044e \u0437\u0430\u043f\u0443\u0449\u0435\u043d\u043d\u0443\u044e \u0432\u044b\u0434\u0440\u0443, \u0447\u0442\u043e\u0431\u044b \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u0435\u0451\u2026')  # stopping the running vydra to upgrade it
$M.restart = [regex]::Unescape('\u0417\u0430\u043f\u0443\u0441\u043a\u0430\u044e \u0432\u0435\u0431-\u0438\u043d\u0442\u0435\u0440\u0444\u0435\u0439\u0441 \u0441\u043d\u043e\u0432\u0430, \u043f\u043e\u0440\u0442 ')  # starting the web UI again, port
$M.old_uv = [regex]::Unescape('uv \u0441\u043b\u0438\u0448\u043a\u043e\u043c \u0441\u0442\u0430\u0440\u044b\u0439 \u2014 \u0441\u0442\u0430\u0432\u043b\u044e \u0441\u0432\u0435\u0436\u0438\u0439\u2026')  # uv is too old, installing a fresh one
$M.pyenv = [regex]::Unescape('\u041f\u0435\u0440\u0435\u043c\u0435\u043d\u043d\u044b\u0435 PYTHONHOME/PYTHONPATH \u043c\u0435\u0448\u0430\u044e\u0442 \u043b\u044e\u0431\u043e\u043c\u0443 Python, \u0432 \u0442\u043e\u043c \u0447\u0438\u0441\u043b\u0435 \u0432\u044b\u0434\u0440\u0435. \u0423\u0431\u0435\u0440\u0438\u0442\u0435 \u0438\u0445: \u041f\u0430\u0440\u0430\u043c\u0435\u0442\u0440\u044b \u2192 \u0421\u0438\u0441\u0442\u0435\u043c\u0430 \u2192 \u041e \u0441\u0438\u0441\u0442\u0435\u043c\u0435 \u2192 \u0414\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0435 \u043f\u0430\u0440\u0430\u043c\u0435\u0442\u0440\u044b \u2192 \u041f\u0435\u0440\u0435\u043c\u0435\u043d\u043d\u044b\u0435 \u0441\u0440\u0435\u0434\u044b.')  # PYTHONHOME/PYTHONPATH break every Python; remove them
$M.retry_tls = [regex]::Unescape('\u041d\u0435 \u0432\u044b\u0448\u043b\u043e \u2014 \u043f\u0440\u043e\u0431\u0443\u044e \u0435\u0449\u0451 \u0440\u0430\u0437 \u0441 \u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442\u0430\u043c\u0438 Windows (\u043f\u0440\u043e\u043a\u0441\u0438 \u0438\u043b\u0438 \u0430\u043d\u0442\u0438\u0432\u0438\u0440\u0443\u0441 \u043f\u0440\u043e\u0432\u0435\u0440\u044f\u044e\u0442 HTTPS)\u2026')  # retrying with Windows certificates
$M.locked = [regex]::Unescape('\u0424\u0430\u0439\u043b\u044b \u0432\u044b\u0434\u0440\u044b \u0437\u0430\u043d\u044f\u0442\u044b \u2014 \u0437\u0430\u043a\u0440\u043e\u0439\u0442\u0435 \u043e\u043a\u043d\u0430, \u0433\u0434\u0435 \u043e\u043d\u0430 \u0437\u0430\u043f\u0443\u0449\u0435\u043d\u0430, \u0438 \u043f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0443.')  # vydra files are busy: close its windows and retry

function Say([string]$Icon, [string]$Color, [string]$Text) {
    Write-Host "  $Icon " -ForegroundColor $Color -NoNewline
    Write-Host $Text
}

# Native programs run with ErrorActionPreference=Continue: in Windows PowerShell 5.1 a redirected stderr line
# (uv prints its notes there) is an ErrorRecord, and with 'Stop' it aborts the whole installer.
function Invoke-Shown([string]$Exe, [string[]]$Arguments) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments | Out-Host; return $LASTEXITCODE } catch { Write-Host $_ -ForegroundColor Red; return 1 }
    finally { $ErrorActionPreference = $old }
}

function Invoke-Quiet([string]$Exe, [string[]]$Arguments) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments *> $null; return $LASTEXITCODE } catch { return 1 }
    finally { $ErrorActionPreference = $old }
}

function Invoke-Capture([string]$Exe, [string[]]$Arguments) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = & $Exe @Arguments 2> $null; if ($LASTEXITCODE -ne 0) { return @() }; return @($out) } catch { return @() }
    finally { $ErrorActionPreference = $old }
}

function Get-UvVersion([string]$Exe) {
    $line = @(Invoke-Capture $Exe @('--version')) | Select-Object -First 1
    if ("$line" -match '(\d+)\.(\d+)\.(\d+)') { return [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])" }
    return $null
}

# uv straight from its GitHub release: when astral.sh is blocked or the official installer fails
function Install-UvZip([string]$Dest) {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
    $url = "https://github.com/astral-sh/uv/releases/latest/download/uv-$arch-pc-windows-msvc.zip"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('vydra-uv-' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile (Join-Path $tmp 'uv.zip')
        Expand-Archive -Path (Join-Path $tmp 'uv.zip') -DestinationPath $tmp -Force
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
        foreach ($exe in @('uv.exe', 'uvx.exe', 'uvw.exe')) {
            if (Test-Path -LiteralPath (Join-Path $tmp $exe)) { Copy-Item -LiteralPath (Join-Path $tmp $exe) -Destination $Dest -Force }
        }
        return (Join-Path $Dest 'uv.exe')
    } catch {
        Write-Host "    $_" -ForegroundColor DarkGray
        return $null
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Running vydra (web UI, downloads) locks its files: uv then fails half-way and leaves a broken install.
# Only processes of THIS installation: its tool folder, its commands, or `-m vydra` on uv's own Python.
# Returns the ports of the web UIs that were running, to start them again after the upgrade.
function Stop-Vydra([string]$Uv, [string]$Bin) {
    $roots = @(@(Invoke-Capture $Uv @('tool', 'dir')) + @($Bin) | Where-Object { $_ })
    $pyDir = @(Invoke-Capture $Uv @('python', 'dir')) | Select-Object -First 1
    $names = @('python.exe', 'pythonw.exe', 'vydra.exe', 'dslhf.exe', "$RuName.exe")
    $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $cmd = "$($_.CommandLine)"
        $ours = $false
        foreach ($root in $roots) { if ($cmd.IndexOf("$root\", [StringComparison]::OrdinalIgnoreCase) -ge 0) { $ours = $true } }
        if (-not $ours -and $pyDir -and $cmd -match '\s-m\s+vydra(\.|\s|$)' -and
            "$($_.ExecutablePath)".StartsWith("$pyDir\", [StringComparison]::OrdinalIgnoreCase)) { $ours = $true }
        $_.ProcessId -ne $PID -and ($names -contains $_.Name) -and $ours
    })
    if ($procs.Count -eq 0) { return @() }
    Say '~' Yellow $M.stop
    $ports = @()
    foreach ($p in $procs) {
        $cmd = "$($p.CommandLine)"
        if ($cmd -match '(?i)\s(ui|web)(\s|$)' -and $cmd -notmatch 'vydra\.downloader') {
            $port = if ($cmd -match '(?:--port|-p)[\s=]+(\d+)') { [int]$Matches[1] } else { 8765 }
            if ($ports -notcontains $port) { $ports += $port }
        }
    }
    $exe = Join-Path "$Bin" 'vydra.exe'
    if ($Bin -and (Test-Path -LiteralPath $exe)) { [void](Invoke-Quiet $exe @('stop')) }  # gently: the queue is saved
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 800  # Windows releases file handles a moment after the process is gone
    return $ports
}

function Install-Vydra {
    Write-Host ''
    Write-Host '  vydra' -ForegroundColor Magenta -NoNewline
    Write-Host ('  ' + $M.title) -ForegroundColor DarkGray
    Write-Host ''

    # 0. A foreign Python environment must not leak into vydra's own (conda, venv, pyenv-win, old installers)
    $pyenvWarn = [bool]($env:PYTHONHOME -or $env:PYTHONPATH)
    foreach ($v in @('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV', 'UV_PYTHON',
                     'UV_SYSTEM_PYTHON', 'UV_MANAGED_PYTHON', 'UV_NO_MANAGED_PYTHON')) {
        Remove-Item -Path "Env:$v" -ErrorAction SilentlyContinue
    }
    $env:UV_PYTHON_PREFERENCE = 'only-managed'  # never the Microsoft Store Python, conda or an old system Python
    $env:UV_PYTHON_DOWNLOADS = 'automatic'
    # Corporate proxy configured in Windows settings: uv only reads HTTPS_PROXY
    if (-not ($env:HTTPS_PROXY -or $env:https_proxy)) {
        try {
            $target = [Uri]'https://github.com'
            $proxy = [Net.WebRequest]::GetSystemWebProxy().GetProxy($target)
            if ($proxy -and $proxy.Host -ne $target.Host) { $env:HTTPS_PROXY = $proxy.AbsoluteUri; $env:HTTP_PROXY = $proxy.AbsoluteUri }
        } catch { }
    }

    # 1. uv: installs Python and dependencies into the user profile, no admin rights needed
    $localBin = Join-Path $env:USERPROFILE '.local\bin'
    $env:Path = "$localBin;$env:Path"
    $uvCmd = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $uv = if ($uvCmd) { $uvCmd.Source } else { $null }
    if (-not $uv) {
        Say '~' Yellow $M.uv
        Write-Host ('    ' + $M.wait) -ForegroundColor DarkGray
        $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        if (-not (Test-Path -LiteralPath $ps)) { $ps = 'powershell' }
        $cmd = '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; ' +
               'irm https://astral.sh/uv/install.ps1 | iex'
        [void](Invoke-Shown $ps @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $cmd))
        $env:Path = "$localBin;$env:Path"
        $fresh = Join-Path $localBin 'uv.exe'
        $uv = if (Test-Path -LiteralPath $fresh) { $fresh } else { Install-UvZip $localBin }
    } elseif ((Get-UvVersion $uv) -lt $MinUv) {
        Say '~' Yellow $M.old_uv
        [void](Invoke-Quiet $uv @('self', 'update'))
        if ((Get-UvVersion $uv) -lt $MinUv) {
            # uv from pip, scoop or winget: a private fresh copy next to vydra (the old uv.exe may still be locked)
            $own = Join-Path $env:LOCALAPPDATA 'vydra\uv'
            $uv = Install-UvZip $own
            $env:Path = "$own;$env:Path"
        }
    }
    if (-not $uv -or -not (Get-UvVersion $uv)) { Say 'x' Red $M.uv_fail; return 1 }
    Say '+' Green (@(Invoke-Capture $uv @('--version')) | Select-Object -First 1)

    # 2. vydra itself (re-run = upgrade; --reinstall-package rebuilds a local checkout too)
    $bin = @(Invoke-Capture $uv @('tool', 'dir', '--bin')) | Select-Object -First 1
    $ports = @(Stop-Vydra $uv "$bin")
    Say '~' Yellow $M.vydra
    Write-Host ('    ' + $M.wait) -ForegroundColor DarkGray
    $install = @('tool', 'install', '--force', '--reinstall-package', 'vydra', '--python', '3.13', $Repo)
    $code = Invoke-Shown $uv $install
    if ($code -ne 0) {
        # TLS-inspecting proxy or antivirus: its root certificate is only in the Windows store
        Say '~' Yellow $M.retry_tls
        $env:UV_NATIVE_TLS = '1'; $env:UV_SYSTEM_CERTS = '1'
        [void](Stop-Vydra $uv "$bin")
        $code = Invoke-Shown $uv $install
    }
    if ($code -ne 0) { Say 'x' Red $M.vydra_fail; Say '!' Yellow $M.locked; return 1 }
    if (-not $env:UV_NO_MODIFY_PATH) { [void](Invoke-Quiet $uv @('tool', 'update-shell')) }
    $bin = @(Invoke-Capture $uv @('tool', 'dir', '--bin')) | Select-Object -First 1
    $env:Path = "$bin;$env:Path"
    $vydra = Join-Path $bin 'vydra.exe'
    $version = ''
    foreach ($name in @('vydra', $RuName)) {
        $version = @(Invoke-Capture (Join-Path $bin "$name.exe") @('--version')) | Select-Object -First 1
        if (-not $version) { Say 'x' Red ($M.check_fail + $name); return 1 }
    }
    Say '+' Green ($M.installed + ': ' + $version)
    # remember the source and commit: `vydra update` compares with the latest one
    [void](Invoke-Quiet $vydra @('update', '--record', $Repo))

    # 3. FFmpeg, JS runtime for YouTube, storage folders, desktop shortcut, Tab completion + smart links
    Write-Host ''
    Say '~' Yellow $M.tools
    Write-Host ('    ' + $M.wait) -ForegroundColor DarkGray
    [void](Invoke-Shown $vydra @('doctor', '--fix'))
    [void](Invoke-Quiet $vydra @('completion'))

    # 4. the web UI was running before the upgrade: start it again with the new version
    foreach ($port in $ports) {
        Say '~' Yellow ($M.restart + $port)
        Start-Process -FilePath $vydra -ArgumentList @('ui', '--no-browser', '--port', "$port") -WindowStyle Minimized
    }

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
    if ($pyenvWarn) { Say '!' Yellow $M.pyenv }
    Write-Host ''
    if ($env:CI -or $env:VYDRA_NO_LAUNCH -or -not [Environment]::UserInteractive -or $ports.Count -gt 0) { return 0 }
    $answer = Read-Host ('  ' + $M.open)
    if ($answer -notmatch ('^(n|N|' + [regex]::Unescape('\u043d|\u041d') + ')')) {
        Start-Process -FilePath $vydra -ArgumentList 'ui' -WindowStyle Minimized
    }
    return 0
}

$global:VydraInstallExit = 1
try {
    $global:VydraInstallExit = @(Install-Vydra)[-1]
} catch {
    Say 'x' Red "$_"
}
# Never `exit` under irm | iex: that would close the user's window together with the error message.
if ($VydraSelf) { exit $global:VydraInstallExit }
