#!/bin/sh
# Установка выдры одной командой (macOS, Linux, WSL):
#   curl -LsSf https://raw.githubusercontent.com/Ihor-Zakharov/vydra/main/install.sh | sh
# Повторный запуск — обновление. Из локальной копии: VYDRA_REPO=/путь/к/vydra sh install.sh
set -eu
REPO="${VYDRA_REPO:-https://github.com/Ihor-Zakharov/vydra/archive/refs/heads/main.zip}"

say() { printf '  \033[%sm%s\033[0m %s\n' "$1" "$2" "$3"; }
die() { say 31 "✗" "$1"; exit 1; }

fetch() {  # fetch <url> — в stdout, через curl или wget
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$1"
    else
        die "Нужен curl или wget — поставьте один из них и запустите установку ещё раз"
    fi
}

printf '\n  \033[1;35m▲ выдра\033[0m  \033[2mустановка\033[0m\n\n'

# 1. uv — ставит Python и зависимости в папку пользователя, права администратора не нужны
PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export PATH
if ! command -v uv >/dev/null 2>&1; then
    say 33 "~" "Ставлю uv (менеджер Python от Astral)…"
    printf '    \033[2mэто может занять пару минут — терминал не завис\033[0m\n'
    fetch https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || die "Не удалось поставить uv — проверьте интернет и повторите"
    command -v uv >/dev/null 2>&1 || die "uv установился, но не находится в PATH"
fi
say 32 "✓" "$(uv --version)"

# 2. сама выдра (повторный запуск = обновление; --reinstall-package пересобирает и локальную копию)
say 33 "~" "Ставлю выдру (Python нужной версии uv скачает сам)…"
printf '    \033[2mэто может занять пару минут — терминал не завис\033[0m\n'
uv tool install --force --reinstall-package vydra --python 3.13 "$REPO" || die "Не удалось установить выдру — текст ошибки выше"
uv tool update-shell >/dev/null 2>&1 || true
PATH="$(uv tool dir --bin):$PATH"
export PATH
for name in vydra выдра; do
    "$name" --version >/dev/null 2>&1 || die "Команда $name не запускается"
done
say 32 "✓" "$(vydra --version)"

# 3. FFmpeg, JS-движок для YouTube, папки хранилища, ярлык, Tab и умные ссылки
echo
say 33 "~" "Ставлю FFmpeg и остальное, проверяю систему…"
printf '    \033[2mэто может занять пару минут — терминал не завис\033[0m\n'
vydra doctor --fix || true
vydra completion >/dev/null 2>&1 || true
# VYDRA_NO_WINDOWS=1 — не трогать Windows-сторону (проверка установщика с временным HOME)
if [ "${VYDRA_NO_WINDOWS:-0}" != 1 ] && grep -qi microsoft /proc/version 2>/dev/null \
    && command -v powershell.exe >/dev/null 2>&1; then
    vydra bridge || true  # WSL: команды vydra и выдра в PowerShell и cmd Windows
fi
if [ "$(uname -s)" = "Darwin" ]; then
    printf '  \033[2mmacOS может один раз спросить разрешение для ffmpeg — разрешите в «Настройках → Конфиденциальность».\033[0m\n'
fi

# 4. веб-интерфейс уже работает — перезапускаем его новой версией (в том же окне, если умеет)
vydra restart --quiet || say 33 "!" "Перезапустите веб-интерфейс сами: vydra stop, затем vydra ui"

printf '\n  \033[1;32mГотово!\033[0m Скопируйте ссылку в браузере и запустите:\n'
printf '    \033[36mвыдра скачать -ф мп3\033[0m   \033[2m(ссылка возьмётся из буфера обмена; или укажите её в кавычках)\033[0m\n'
printf '    \033[36mвыдра ui\033[0m               \033[2mвеб-интерфейс\033[0m\n'
printf '    \033[36mвыдра --help\033[0m           \033[2mвсе команды; Tab подсказывает\033[0m\n'
printf '  \033[2mОткройте новый терминал, чтобы заработали Tab и кавычки вокруг ссылок.\033[0m\n\n'
