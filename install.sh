#!/bin/sh
# Установка выдры одной командой (macOS, Linux, WSL):
#   curl -LsSf https://raw.githubusercontent.com/Ihor-Zakharov/vydra/main/install.sh | sh
set -eu
REPO="${VYDRA_REPO:-git+https://github.com/Ihor-Zakharov/vydra}"

say() { printf '  \033[%sm%s\033[0m %s\n' "$1" "$2" "$3"; }

printf '\n  \033[1;35m▲ выдра\033[0m  \033[2mустановка\033[0m\n\n'

# 1. uv — ставит Python и зависимости, ничего не трогая в системе
if ! command -v uv >/dev/null 2>&1; then
    say 33 "~" "Ставлю uv (менеджер Python от Astral)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi
say 32 "✓" "$(uv --version)"

# 2. сама выдра (Python нужной версии uv скачает сам)
say 33 "~" "Ставлю выдру..."
uv tool install --force --python 3.13 "$REPO"
uv tool update-shell >/dev/null 2>&1 || true
PATH="$(uv tool dir --bin):$PATH"
export PATH
say 32 "✓" "выдра установлена"

# 3. FFmpeg, JS-движок для YouTube, папка-хранилище, ярлык
echo
vydra doctor --fix || true
vydra completion >/dev/null 2>&1 || true

printf '\n  \033[1;32mГотово!\033[0m Откройте новый терминал и запускайте:\n'
printf '    \033[36mvydra ui\033[0m   \033[2mвеб-интерфейс\033[0m\n'
printf '    \033[36mvydra\033[0m      \033[2mконсольная версия (Tab — подсказки команд)\033[0m\n\n'
