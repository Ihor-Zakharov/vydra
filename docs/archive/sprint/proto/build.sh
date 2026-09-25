#!/bin/sh
# Собирает черновики вариантов: общий язык + композиция варианта.
cd "$(dirname "$0")"
for part in *.part.css; do
  name="${part%.part.css}"
  cat _base.css "$part" > "$name.css"
done
ls -1 *.css | grep -v -e '^_' -e '\.part\.css$'
