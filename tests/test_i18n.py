"""Язык консоли: английский по умолчанию, `vydra lang ru|en`, VYDRA_LANG главнее, --json от языка не зависит."""

import ast
import json
import os
import re
import string
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import needs_ffmpeg
from vydra import cli, i18n, jobs
from vydra.i18n_en import EN

ROOT = Path(__file__).resolve().parent.parent
URL = "https://www.youtube.com/watch?v=UwullClrOuw"
runner = CliRunner()


def _cli_keys() -> list[str]:
    """Все русские строки консоли: tr("…"), help/metavar у opt()/arg(), plural(), подсказки _HINTS, докстринги команд."""
    tree = ast.parse((ROOT / "vydra" / "cli.py").read_text(encoding="utf-8"))

    def lit(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    keys, commands = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "tr" and node.args and lit(node.args[0]) is not None:
                keys.append(lit(node.args[0]))
            if node.func.id in ("opt", "arg"):
                keys += [lit(kw.value) for kw in node.keywords if kw.arg in ("help", "metavar") and lit(kw.value)]
            if node.func.id == "plural":
                keys += [lit(a) for a in node.args[1:] if lit(a) is not None]
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "_HINTS" for t in node.targets):
            keys += [lit(e.elts[1]) for e in node.value.elts]
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "COMMANDS" for t in node.targets):
            commands |= {e.elts[0].id for e in node.value.elts}
    keys += [ast.get_docstring(n, clean=False).strip() for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in commands]  # fmt: skip
    return list(dict.fromkeys(keys))


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_every_console_string_has_english():
    keys = _cli_keys()
    assert len(keys) > 300  # сканер действительно видит строки
    missing = [k for k in keys if k not in EN]
    assert not missing, f"нет перевода: {missing[:10]}"
    for key in keys:  # подстановки {port}, {why}… — те же в обоих языках
        assert _fields(EN[key]) == _fields(key), key
    cyrillic = [k for k in keys if re.search("[а-яё]", EN[k], re.I) and not re.search("выдра|Видео|Аудио", EN[k])]
    assert not cyrillic, f"в английском остался русский: {cyrillic[:5]}"


def _run(*args, lang=None, cfg: Path, env_extra=None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "VYDRA_LANG"}
    env |= {"VD_CONFIG_DIR": str(cfg), "COLUMNS": "120", "NO_COLOR": "1", **(env_extra or {})}
    if lang:
        env["VYDRA_LANG"] = lang
    return subprocess.run([sys.executable, "-m", "vydra", *args], env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=60)  # fmt: skip


def _first_line(text: str) -> str:
    return next(ln.strip() for ln in text.splitlines() if ln.strip())


def test_default_is_english_and_help_shows_how_to_switch(tmp_path):
    out = _run("--help", cfg=tmp_path).stdout
    assert _first_line(out).startswith("Language: vydra lang ru | en")  # смена языка — самой верхней строкой
    assert "Usage:" in out and "Downloads videos" in out and "Maintenance" in out
    assert "─ По-русски" not in out and "скачать  " not in out  # русские синонимы в английской справке не шумят
    sub = _run("download", "--help", cfg=tmp_path).stdout
    assert "--format" in sub and "--формат" not in sub and "-ф" not in sub and "--справка" not in sub
    assert "What to save" in sub


def test_russian_help(tmp_path):
    out = _run("--help", lang="ru", cfg=tmp_path).stdout
    assert _first_line(out).startswith("Язык: выдра язык ru | en")
    assert "Как вызвать" in out and "По-русски" in out and "скачать" in out
    assert "--формат" in _run("download", "--help", lang="ru", cfg=tmp_path).stdout


def test_lang_command_saves_choice_and_env_wins(tmp_path):
    shown = _run("lang", cfg=tmp_path)
    assert shown.returncode == 0 and "Console language: English" in shown.stdout and "vydra lang ru" in shown.stdout
    switched = _run("язык", "ру", cfg=tmp_path)  # кириллический синоним и значение
    assert switched.returncode == 0 and "Язык консоли: русский" in switched.stdout
    assert json.loads((tmp_path / "prefs.json").read_text(encoding="utf-8"))["lang"] == "ru"
    assert _first_line(_run("--help", cfg=tmp_path).stdout).startswith("Язык:")  # выбор запомнился
    forced = _run("--help", lang="en", cfg=tmp_path).stdout  # переменная окружения главнее
    assert _first_line(forced).startswith("Language:")
    assert "VYDRA_LANG" in _run("lang", lang="en", cfg=tmp_path).stdout
    for value in ("en", "анг", "англ", "английский", "EN"):
        assert i18n.normalize(value) == "en"
    for value in ("ru", "ру", "рус", "русский"):
        assert i18n.normalize(value) == "ru"
    bad = _run("lang", "de", lang="en", cfg=tmp_path)
    assert bad.returncode == 2 and "Unknown language" in bad.stderr


def test_lang_value_completes_with_tab(tmp_path):
    env = {"_TYPER_COMPLETE_ARGS": "vydra lang ", "_VYDRA_COMPLETE": "complete_zsh"}
    out = _run(cfg=tmp_path, env_extra=env)
    assert out.returncode == 0 and "en" in out.stdout and "ru" in out.stdout, out.stderr


def test_usage_errors_in_english_are_compact(tmp_path):
    bad = _run("download", "-f", "mp5", "x", cfg=tmp_path)
    assert bad.returncode == 2
    assert "Invalid value for --format (-f): 'mp5' is not one of" in bad.stderr and "--формат" not in bad.stderr
    assert "Help: vydra download --help" in bad.stderr
    typo = _run("вщцтдщфв", "x", cfg=tmp_path)  # другая раскладка по-прежнему понимается
    assert "understood “вщцтдщфв” as “download”" in typo.stderr


@pytest.fixture
def english(monkeypatch):
    monkeypatch.setattr(i18n, "LANG", "en")


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    for var, sub in (("VD_LIBRARY_DIR", "lib"), ("VD_WORK_DIR", "work"), ("VD_CONFIG_DIR", "cfg"), ("VD_TOOLS_DIR", "tools")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setattr(jobs, "BACKOFF", (0.01, 0.01, 0.01))
    return tmp_path


def _fake_download(clip_path):
    import shutil

    from vydra.downloader import Downloaded

    def fake(url, mode, quality, work_dir, **kw):
        dst = work_dir / "src.mp4"
        shutil.copyfile(clip_path, dst)
        return [Downloaded(dst, {"id": "UwullClrOuw", "title": "Clip", "duration": 3, "webpage_url": url}, None)]

    return fake


@needs_ffmpeg
def test_download_summary_in_english(cli_env, make_clip, monkeypatch, english):
    monkeypatch.setattr(jobs, "download", _fake_download(make_clip("s.mp4")))
    result = runner.invoke(cli.app, ["d", URL, "-f", "both"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Done! 2 files created" in out and "Library: " in out and "+ video" in out and "+ audio" in out
    body = "\n".join(ln for ln in out.splitlines() if not ln.startswith("▲"))  # баннер собран при импорте (ru)
    assert not re.search("[а-яё]", re.sub(r"Видео|Аудио|\S*[\\/]\S*", "", body), re.I), out  # кроме имён папок


def test_errors_and_hints_in_english(cli_env, monkeypatch, english):
    from vydra.downloader import DownloadFailed

    def private(*a, **k):
        raise DownloadFailed("Видео приватное — скачать его нельзя.", transient=False)

    monkeypatch.setattr(jobs, "download", private)
    result = runner.invoke(cli.app, ["d", URL, "-y"])
    assert result.exit_code == 1
    assert "Couldn't download" in result.output and "The video is private" in result.output
    assert "Check whether the video opens in the browser" in result.output


@needs_ffmpeg
def test_json_does_not_depend_on_language(cli_env, make_clip, monkeypatch):
    monkeypatch.setattr(jobs, "download", _fake_download(make_clip("s.mp4")))

    def run(lang: str) -> dict:
        monkeypatch.setattr(i18n, "LANG", lang)
        lines = [ln for ln in runner.invoke(cli.app, ["d", URL, "-f", "mp3", "--json", "--force"]).output.splitlines()
                 if ln.startswith("{")]  # fmt: skip
        return json.loads(lines[-1])

    def shape(value):
        if isinstance(value, dict):
            return {k: shape(v) for k, v in value.items() if k not in ("path", "display_path", "size")}
        if isinstance(value, list):
            return [shape(v) for v in value]
        return value

    en, ru = run("en"), run("ru")
    assert shape(en) == shape(ru) and en["ok"] and en["exit_code"] == 0
    listed = {lang: (monkeypatch.setattr(i18n, "LANG", lang), runner.invoke(cli.app, ["list", "--json"]).output)[1]
              for lang in ("en", "ru")}  # fmt: skip
    assert json.loads(listed["en"]) == json.loads(listed["ru"])


def test_messages_from_other_modules(english):
    assert i18n.tr_msg("Видео приватное — скачать его нельзя.") == "The video is private — it can't be downloaded."
    assert i18n.tr_msg("Без постера: 1") == "Without a poster: 1"  # шаблон — со строчной, текст — с заглавной
    assert i18n.tr_msg("пропало файлов: 2; без постера: 1") == "missing files: 2; without a poster: 1"
    assert i18n.tr_msg("Совсем новое сообщение") == "Совсем новое сообщение"  # без перевода — как есть
    assert i18n.plural(1, "файл", "файла", "файлов") == "1 file"
    assert i18n.plural(5, "файл", "файла", "файлов") == "5 files"
    assert i18n.tr("Порт {port} и соседние заняты другими программами", port=80).startswith("Port 80")


def test_russian_is_untouched(monkeypatch):
    monkeypatch.setattr(i18n, "LANG", "ru")
    assert i18n.tr_msg("Видео приватное — скачать его нельзя.") == "Видео приватное — скачать его нельзя."
    assert i18n.plural(3, "файл", "файла", "файлов") == "3 файла"
    assert i18n.tr("Готово! ") == "Готово! "
