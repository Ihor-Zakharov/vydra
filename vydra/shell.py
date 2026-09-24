"""Интеграция с терминалом: автодополнение по Tab для `vydra` и `выдра` и «умные ссылки».

Умные ссылки: когда вы жмёте Enter в строке, которая начинается с vydra/выдра, ссылки
без кавычек сами берутся в одинарные кавычки. Иначе «&» из адреса YouTube ломает команду:
bash уводит её в фон и теряет `-f mp3`, PowerShell отказывается разбирать строку.

bash — `bind -x` + макрос на Enter, zsh — обёртка виджета accept-line, fish — `bind \\r`,
PowerShell — обработчик Enter в PSReadLine. Чужие команды не трогаются никогда.

Всё ставится одной командой `vydra completion` (её вызывают установщики) и снимается
`vydra completion --uninstall`. В rc-файлах — один помеченный блок, который подключает
скрипт из папки настроек: обновления выдры меняют скрипт, а не ваши rc-файлы.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import system

PROGRAMS = ("vydra", "выдра")
COMPLETE_VAR = "_VYDRA_COMPLETE"
BEGIN = "# >>> vydra >>>"
END = "# <<< vydra <<<"
SHELLS = ("bash", "zsh", "fish", "powershell")

_FIRST_WORD = re.compile(r"^\s*(?:\S*[\\/])?(?:vydra|выдра)(?:\.exe|\.ps1|\.cmd)?(?:\s|$)", re.IGNORECASE)
_HTTP = re.compile(r"^https?://", re.IGNORECASE)
_BARE = re.compile(r"^(?:(?:www|m|vm|vt|music)\.)?(?:youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/", re.I)
_SPECIAL = set("&;|<>()$`*?#!{}[]~")


# --- эталон правил (скрипты оболочек повторяют его один в один, тесты это сверяют) --------


def fix_token(token: str, escape: str = "\\") -> str:
    if not token or any(ch in token for ch in "'\"" + escape):
        return token  # уже в кавычках или экранировано вручную — не трогаем
    if _HTTP.match(token) or (_BARE.match(token) and any(ch in _SPECIAL for ch in token)):
        return f"'{token}'"
    return token


def quote_links(line: str, flavor: str = "posix") -> str:
    """Строка после «умных ссылок». flavor: posix (bash, zsh, fish) или powershell."""
    if not _FIRST_WORD.match(line):
        return line
    escape = "`" if flavor == "powershell" else "\\"
    out: list[str] = []
    token = ""
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            token += ch
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
            token += ch
        elif ch == escape:
            token += ch
            if i + 1 < len(line):
                i += 1
                token += line[i]
        elif ch in " \t":
            out.append(fix_token(token, escape) + ch)
            token = ""
        else:
            token += ch
        i += 1
    out.append(token if quote else fix_token(token, escape))  # незакрытая кавычка — хвост как есть
    return "".join(out)


# --- скрипты -----------------------------------------------------------------------------

BASH = r"""# выдра: автодополнение по Tab и умные ссылки (vydra completion --uninstall — убрать)
_vydra_completion() {
    local IFS=$'\n'
    COMPREPLY=( $( env COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD=$COMP_CWORD __COMPLETE_VAR__=complete_bash "$1" 2>/dev/null ) )
    return 0
}
complete -o default -F _vydra_completion vydra выдра

__vydra_fix_token() {
    local t=$1
    __vydra_tok=$t
    [[ -z $t ]] && return 0
    case $t in *"'"*|*'"'*|*'\'*) return 0 ;; esac
    local re_http='^[Hh][Tt][Tt][Pp][Ss]?://'
    local re_bare='^((www|m|vm|vt|music)\.)?(youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/'
    if [[ $t =~ $re_http ]]; then
        __vydra_tok="'$t'"
    elif [[ $t =~ $re_bare ]]; then
        case $t in *[\&\;\|\<\>\(\)\$\`\*\?\#\!\{\}\[\]\~]*) __vydra_tok="'$t'" ;; esac
    fi
    return 0
}

__vydra_quote_line() {
    local line=$1 re_first='^[[:space:]]*([^[:space:]]*[\\/])?([Vv][Yy][Dd][Rr][Aa]|выдра|ВЫДРА|Выдра)(\.exe|\.ps1|\.cmd)?([[:space:]]|$)'
    __vydra_line=$line
    [[ $line =~ $re_first ]] || return 0
    local out="" tok="" q="" ch i n=${#line}
    for (( i = 0; i < n; i++ )); do
        ch=${line:i:1}
        if [[ -n $q ]]; then
            tok+=$ch
            [[ $ch == "$q" ]] && q=""
        elif [[ $ch == "'" || $ch == '"' ]]; then
            q=$ch
            tok+=$ch
        elif [[ $ch == '\' ]]; then
            tok+=$ch
            if (( i + 1 < n )); then
                (( i++ ))
                tok+=${line:i:1}
            fi
        elif [[ $ch == ' ' || $ch == $'\t' ]]; then
            __vydra_fix_token "$tok"
            out+=$__vydra_tok$ch
            tok=""
        else
            tok+=$ch
        fi
    done
    if [[ -n $q ]]; then
        out+=$tok
    else
        __vydra_fix_token "$tok"
        out+=$__vydra_tok
    fi
    __vydra_line=$out
}

__vydra_readline_hook() {
    __vydra_quote_line "$READLINE_LINE"
    if [[ $__vydra_line != "$READLINE_LINE" ]]; then
        READLINE_LINE=$__vydra_line
        READLINE_POINT=${#READLINE_LINE}
    fi
}

if [[ $- == *i* ]] && (( BASH_VERSINFO[0] >= 4 )); then
    for __vydra_map in emacs vi-insert; do
        bind -m "$__vydra_map" -x '"\C-x\C-]": __vydra_readline_hook'
        bind -m "$__vydra_map" '"\C-m": "\C-x\C-]\C-j"'
    done
    unset __vydra_map
fi
"""

ZSH = r"""# выдра: автодополнение по Tab и умные ссылки (vydra completion --uninstall — убрать)
_vydra_completion() {
    eval $(env _TYPER_COMPLETE_ARGS="${words[1,$CURRENT]}" __COMPLETE_VAR__=complete_zsh ${words[1]} 2>/dev/null)
}
if (( ! $+functions[compdef] )); then
    autoload -Uz compinit && compinit -i
fi
compdef _vydra_completion vydra выдра

__vydra_fix_token() {
    local t=$1
    REPLY=$t
    [[ -z $t ]] && return 0
    [[ $t == *[\'\"\\]* ]] && return 0
    if [[ $t =~ '^[Hh][Tt][Tt][Pp][Ss]?://' ]]; then
        REPLY="'$t'"
    elif [[ $t =~ '^((www|m|vm|vt|music)\.)?(youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/' ]]; then
        [[ $t == *[\&\;\|\<\>\(\)\$\`\*\?\#\!\{\}\[\]\~]* ]] && REPLY="'$t'"
    fi
    return 0
}

__vydra_quote_line() {
    emulate -L zsh
    local line=$1
    REPLY=$line
    [[ $line =~ '^[[:space:]]*([^[:space:]]*[\\/])?([Vv][Yy][Dd][Rr][Aa]|выдра|ВЫДРА|Выдра)(\.exe|\.ps1|\.cmd)?([[:space:]]|$)' ]] || return 0
    local out="" tok="" q="" ch
    integer i=1 n=${#line}
    while (( i <= n )); do
        ch=${line[i]}
        if [[ -n $q ]]; then
            tok+=$ch
            [[ $ch == $q ]] && q=""
        elif [[ $ch == "'" || $ch == '"' ]]; then
            q=$ch
            tok+=$ch
        elif [[ $ch == '\' ]]; then
            tok+=$ch
            if (( i < n )); then
                (( i++ ))
                tok+=${line[i]}
            fi
        elif [[ $ch == ' ' || $ch == $'\t' ]]; then
            __vydra_fix_token "$tok"
            out+=$REPLY$ch
            tok=""
        else
            tok+=$ch
        fi
        (( i++ ))
    done
    if [[ -n $q ]]; then
        out+=$tok
    else
        __vydra_fix_token "$tok"
        out+=$REPLY
    fi
    REPLY=$out
}

if [[ -o interactive ]] && [[ ${widgets[accept-line]} != user:__vydra_accept_line ]]; then
    if [[ ${widgets[accept-line]} == user:* ]]; then
        zle -A accept-line __vydra_orig_accept_line
    else
        zle -A .accept-line __vydra_orig_accept_line
    fi
    __vydra_accept_line() {
        __vydra_quote_line "$BUFFER"
        [[ $REPLY != "$BUFFER" ]] && BUFFER=$REPLY && CURSOR=${#BUFFER}
        zle __vydra_orig_accept_line -- "$@"
    }
    zle -N accept-line __vydra_accept_line
fi
"""

FISH = r"""# выдра: автодополнение по Tab и умные ссылки (vydra completion --uninstall — убрать)
for __vydra_prog in vydra выдра
    complete --command $__vydra_prog --no-files --arguments "(env __COMPLETE_VAR__=complete_fish _TYPER_COMPLETE_FISH_ACTION=get-args _TYPER_COMPLETE_ARGS=(commandline -cp) $__vydra_prog)" --condition "env __COMPLETE_VAR__=complete_fish _TYPER_COMPLETE_FISH_ACTION=is-args _TYPER_COMPLETE_ARGS=(commandline -cp) $__vydra_prog"
end
set -e __vydra_prog

function __vydra_fix_token
    set -l t $argv[1]
    if test -z "$t"; or string match -q -r '[\'"\\\\]' -- $t
        printf '%s' $t
        return
    end
    if string match -q -r -i '^https?://' -- $t
        printf "'%s'" $t
    else if string match -q -r -i '^((www|m|vm|vt|music)\.)?(youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/' -- $t; and string match -q -r '[&;|<>()$`*?#!{}\[\]~]' -- $t
        printf "'%s'" $t
    else
        printf '%s' $t
    end
end

function __vydra_quote_line
    set -l line $argv[1]
    if not string match -q -r -i '^\s*(\S*[\\\\/])?(vydra|выдра)(\.exe|\.ps1|\.cmd)?(\s|$)' -- $line
        printf '%s' $line
        return
    end
    set -l out ''
    set -l tok ''
    set -l q ''
    set -l chars (string split '' -- $line)
    set -l n (count $chars)
    set -l i 1
    while test $i -le $n
        set -l ch $chars[$i]
        if test -n "$q"
            set tok "$tok$ch"
            test "$ch" = "$q"; and set q ''
        else if test "$ch" = "'" -o "$ch" = '"'
            set q $ch
            set tok "$tok$ch"
        else if test "$ch" = '\\'
            set tok "$tok$ch"
            if test $i -lt $n
                set i (math $i + 1)
                set tok "$tok$chars[$i]"
            end
        else if test "$ch" = ' ' -o "$ch" = \t
            set out "$out"(__vydra_fix_token "$tok")"$ch"
            set tok ''
        else
            set tok "$tok$ch"
        end
        set i (math $i + 1)
    end
    if test -n "$q"
        set out "$out$tok"
    else
        set out "$out"(__vydra_fix_token "$tok")
    end
    printf '%s' $out
end

function __vydra_accept_line
    set -l line (commandline)
    set -l fixed (__vydra_quote_line "$line")
    if test "$fixed" != "$line"
        commandline -r -- $fixed
    end
    commandline -f execute
end

if status is-interactive
    bind \r __vydra_accept_line
    bind -M insert \r __vydra_accept_line 2>/dev/null
end
"""

# Только ASCII: Windows PowerShell 5.1 читает файлы без BOM в кодировке ANSI.
POWERSHELL = r"""# vydra: Tab completion and smart links (vydra completion --uninstall to remove)
$__vydraRu = -join [char[]](0x432, 0x44B, 0x434, 0x440, 0x430)
$__vydraNames = @('vydra', $__vydraRu)

Register-ArgumentCompleter -Native -CommandName $__vydraNames -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $vars = '__COMPLETE_VAR__', '_TYPER_COMPLETE_ARGS', '_TYPER_COMPLETE_WORD_TO_COMPLETE'
    $oldWslEnv = $env:WSLENV
    $env:WSLENV = (@($vars) + @($oldWslEnv -split ':' | Where-Object { $_ })) -join ':'
    Set-Item -Path "env:__COMPLETE_VAR__" -Value 'complete_powershell'
    $env:_TYPER_COMPLETE_ARGS = $commandAst.ToString()
    $env:_TYPER_COMPLETE_WORD_TO_COMPLETE = $wordToComplete
    try {
        & $commandAst.CommandElements[0].Value | ForEach-Object {
            $parts = $_ -split ':::'
            $help = if ($parts.Count -gt 1 -and $parts[1]) { $parts[1] } else { $parts[0] }
            [System.Management.Automation.CompletionResult]::new($parts[0], $parts[0], 'ParameterValue', $help)
        }
    } finally {
        foreach ($v in $vars) { Remove-Item -Path "env:$v" -ErrorAction SilentlyContinue }
        $env:WSLENV = $oldWslEnv
    }
}

function global:__VydraFixToken([string]$t) {
    if (-not $t -or $t.IndexOfAny([char[]]"'`"``") -ge 0) { return $t }
    if ($t -match '^(?i)https?://') { return "'" + $t + "'" }
    if ($t -match '^(?i)((www|m|vm|vt|music)\.)?(youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/' -and
        $t.IndexOfAny([char[]]'&;|<>()$`*?#!{}[]~') -ge 0) { return "'" + $t + "'" }
    return $t
}

function global:__VydraQuoteLine([string]$line) {
    $pattern = '^\s*(\S*[\\/])?(vydra|' + $__vydraRu + ')(\.exe|\.ps1|\.cmd)?(\s|$)'
    if ($line -notmatch ('(?i)' + $pattern)) { return $line }
    $out = New-Object System.Text.StringBuilder
    $tok = ''
    $q = ''
    $i = 0
    while ($i -lt $line.Length) {
        $ch = [string]$line[$i]
        if ($q) {
            $tok += $ch
            if ($ch -ceq $q) { $q = '' }
        } elseif ($ch -eq "'" -or $ch -eq '"') {
            $q = $ch
            $tok += $ch
        } elseif ($ch -eq '`') {
            $tok += $ch
            if ($i + 1 -lt $line.Length) { $i++; $tok += [string]$line[$i] }
        } elseif ($ch -eq ' ' -or $ch -eq "`t") {
            [void]$out.Append((__VydraFixToken $tok) + $ch)
            $tok = ''
        } else {
            $tok += $ch
        }
        $i++
    }
    if ($q) { [void]$out.Append($tok) } else { [void]$out.Append((__VydraFixToken $tok)) }
    return $out.ToString()
}

if (Get-Module -Name PSReadLine) {
    Set-PSReadLineKeyHandler -Key Enter -BriefDescription VydraAcceptLine -Description 'vydra: quote links, then accept the line' -ScriptBlock {
        $line = $null
        $cursor = $null
        [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
        $fixed = __VydraQuoteLine $line
        if ($fixed -cne $line) {
            [Microsoft.PowerShell.PSConsoleReadLine]::Replace(0, $line.Length, $fixed)
        }
        [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
    }
}
"""

SCRIPTS = {"bash": BASH, "zsh": ZSH, "fish": FISH, "powershell": POWERSHELL}
EXTENSIONS = {"bash": "bash", "zsh": "zsh", "fish": "fish", "powershell": "ps1"}


def script(shell: str) -> str:
    return SCRIPTS[shell].replace("__COMPLETE_VAR__", COMPLETE_VAR)


# --- установка ---------------------------------------------------------------------------


@dataclass
class Target:
    shell: str
    script: Path  # куда кладём скрипт
    rc: Path | None  # rc/profile с блоком-подключением (fish подключает conf.d сам)


def detect_shell() -> str | None:
    if system.OS == "windows":
        return "powershell"
    name = Path(os.environ.get("SHELL", "")).name
    return name if name in ("bash", "zsh", "fish") else ("bash" if shutil.which("bash") else None)


def targets(config_dir: Path, shells: list[str] | None = None) -> list[Target]:
    home = Path.home()
    folder = config_dir / "shell"
    wanted = shells or [s for s in SHELLS if _present(s)]
    result = []
    for shell in wanted:
        if shell == "bash":
            result.append(Target("bash", folder / "vydra.bash", home / ".bashrc"))
        elif shell == "zsh":
            zdot = Path(os.environ.get("ZDOTDIR", home))
            result.append(Target("zsh", folder / "vydra.zsh", zdot / ".zshrc"))
        elif shell == "fish":
            result.append(Target("fish", home / ".config/fish/conf.d/vydra.fish", None))
        elif shell == "powershell" and system.OS == "windows":
            for profile in powershell_profiles():
                result.append(Target("powershell", folder / "vydra.ps1", profile))
    return result


def _present(shell: str) -> bool:
    if shell == "powershell":
        return system.OS == "windows"
    if shell == "bash":
        return bool(shutil.which("bash")) and system.OS != "windows"
    return bool(shutil.which(shell)) or (Path.home() / f".{shell}rc").exists()


def powershell_profiles() -> list[Path]:
    """Профили CurrentUserAllHosts для Windows PowerShell 5.1 и PowerShell 7 (если стоит)."""
    profiles = []
    for exe in ("powershell.exe", "pwsh.exe", "pwsh"):
        if not shutil.which(exe):
            continue
        out = system.run([exe, "-NoProfile", "-NonInteractive", "-Command",
                          "[Console]::Out.Write($PROFILE.CurrentUserAllHosts)"], timeout=30)  # fmt: skip
        if out:
            path = system.from_windows(out) if system.OS == "wsl" else Path(out)
            if path and path not in profiles:
                profiles.append(path)
    return profiles


def block(target: Target) -> str:
    path = system.to_windows(target.script) if target.shell == "powershell" and system.OS == "wsl" else str(target.script)
    if target.shell == "powershell":
        literal = str(path).replace("'", "''")
        body = f"if (Test-Path -LiteralPath '{literal}') {{ . '{literal}' }}"
    else:
        body = f'[ -f "{path}" ] && . "{path}"'
    return f"{BEGIN}\n{body}\n{END}\n"


def install(config_dir: Path, shells: list[str] | None = None) -> list[Target]:
    done = []
    for target in targets(config_dir, shells):
        target.script.parent.mkdir(parents=True, exist_ok=True)
        text = script(target.shell)
        if target.shell == "powershell":
            target.script.write_bytes(text.replace("\n", "\r\n").encode("ascii"))
        else:
            target.script.write_text(text, encoding="utf-8", newline="\n")
        if target.rc is not None:
            _write_block(target.rc, block(target), powershell=target.shell == "powershell")
            if target.shell == "bash":
                _drop_legacy_typer_line(target.rc)
        done.append(target)
    return done


def uninstall(config_dir: Path) -> list[Path]:
    removed = []
    for target in targets(config_dir, list(SHELLS)) + targets(config_dir, ["powershell"]):
        if target.rc is not None and _remove_block(target.rc):
            removed.append(target.rc)
        if target.script.exists():
            target.script.unlink()
            removed.append(target.script)
    return removed


def installed(config_dir: Path) -> dict[str, bool]:
    status = {}
    for target in targets(config_dir):
        ok = target.script.is_file() and target.script.read_bytes().replace(b"\r\n", b"\n") == script(
            target.shell
        ).encode("utf-8")
        if ok and target.rc is not None:
            ok = BEGIN in _read(target.rc)
        status[target.shell] = status.get(target.shell, True) and ok
    return status


def _read(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def _write_block(rc: Path, text: str, powershell: bool = False) -> None:
    """Добавляет или обновляет помеченный блок, сохраняя кодировку файла (в т.ч. UTF-16 профилей PS)."""
    rc.parent.mkdir(parents=True, exist_ok=True)
    raw = rc.read_bytes() if rc.exists() else b""
    encoding, bom = "utf-8", b""
    if raw.startswith(b"\xff\xfe"):
        encoding, bom = "utf-16-le", b"\xff\xfe"
    elif raw.startswith(b"\xef\xbb\xbf"):
        bom = b"\xef\xbb\xbf"
    content = raw[len(bom):].decode(encoding, errors="replace")
    newline = "\r\n" if powershell or "\r\n" in content else "\n"
    body = text.replace("\n", newline)
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\r?\n?", re.S)
    if pattern.search(content):
        content = pattern.sub(lambda _: body, content, count=1)
    else:
        if content and not content.endswith(("\n", "\r\n")):
            content += newline
        content += (newline if content else "") + body
    tmp = rc.with_name(rc.name + ".vydra-tmp")
    tmp.write_bytes(bom + content.encode(encoding))
    os.replace(tmp, rc)


def _remove_block(rc: Path) -> bool:
    if not rc.exists():
        return False
    raw = rc.read_bytes()
    encoding, bom = ("utf-16-le", b"\xff\xfe") if raw.startswith(b"\xff\xfe") else ("utf-8", b"")
    if raw.startswith(b"\xef\xbb\xbf"):
        bom = b"\xef\xbb\xbf"
    content = raw[len(bom):].decode(encoding, errors="replace")
    pattern = re.compile(r"(\r?\n)?" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\r?\n?", re.S)
    new = pattern.sub("", content, count=1)
    if new == content:
        return False
    rc.write_bytes(bom + new.encode(encoding))
    return True


def _drop_legacy_typer_line(rc: Path) -> None:
    """Первые версии ставили автодополнение средствами Typer: строка source ~/.bash_completions/vydra.sh."""
    legacy = Path.home() / ".bash_completions" / "vydra.sh"
    content = _read(rc)
    line = f"source '{legacy}'"
    if line in content:
        rc.write_text(content.replace(f"\n{line}", "").replace(line, ""), encoding="utf-8")
    legacy.unlink(missing_ok=True)
