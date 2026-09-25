"""Консоль по-русски: ключи и значения кириллицей, раскладка, буфер обмена, вопросы, итоги."""

import shutil
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import needs_ffmpeg
from vydra import argv, cli, clipboard, jobs, system
from vydra.argv import normalize, normalize_choice
from vydra.downloader import Downloaded, DownloadFailed

runner = CliRunner()
URL = "https://www.youtube.com/watch?v=UwullClrOuw&t=8140s"


@pytest.mark.parametrize(
    ("args", "expected", "hint"),
    [
        (["скачать", URL, "-ф", "мп3"], ["скачать", URL, "-ф", "mp3"], None),
        (["скачать", URL, "--формат", "оба", "--качество", "720", "--отрезок", "1:00-5:00"],
         ["скачать", URL, "--формат", "both", "--качество", "720", "--отрезок", "1:00-5:00"], None),
        (["d", URL, "-а", "ьз3"], ["d", URL, "-f", "mp3"], "-а ьз3"),  # русская раскладка вместо английской
        (["d", URL, "-a", "vg3"], ["d", URL, "-ф", "mp3"], "-a vg3"),  # английская вместо русской (-ф мп3)
        (["вщцтдщфв", URL, "--ащкьфе", "ищер"], ["download", URL, "--format", "both"], "вщцтдщфв"),
        (["crfxfnm", URL], ["скачать", URL], "crfxfnm"),
        (["в", URL], ["d", URL], "в"),
        ([URL, "-f", "MP3"], ["download", URL, "-f", "mp3"], None),  # ссылка без команды — это скачать
        (["d", URL, "-q", "1080p"], ["d", URL, "-q", "1080"], None),
        (["d", URL, "-к", "ЬФЧ"], ["d", URL, "-к", "max"], "ЬФЧ"),
        (["d", URL, "-б", "320к"], ["d", URL, "-б", "320"], None),
        (["d", URL, "-o", "1:00-5:00"], ["d", URL, "-о", "1:00-5:00"], "-o"),  # латинская o с отрезком
        (["d", URL, "-о", "D:/Кино"], ["d", URL, "-o", "D:/Кино"], "-о"),  # кириллическая о с папкой
        (["d", URL, "--format=ьз4"], ["d", URL, "--format=mp4"], "ьз4"),
        (["list", "-s", "ьз3"], ["list", "-s", "ьз3"], None),  # поиск — свободный текст, не трогаем
        (["d", "--", "-а"], ["d", "--", "-а"], None),
    ],
)
def test_normalize(args, expected, hint):
    result = normalize(args)
    assert result.args == expected
    assert (hint in (result.hint or "")) if hint else result.hint is None


def test_urls_and_paths_are_never_touched():
    weird = ["d", "https://www.youtube.com/watch?v=ьз3&list=ищер", "--папка", "ищер/ьз3", "-с", "ьз3"]
    out = normalize(weird).args
    assert out[1] == weird[1] and out[3] == "ищер/ьз3"


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [("format", "MP3", "mp3"), ("format", "мп4", "mp4"), ("format", "Оба", "both"), ("format", "ищер", "both"),
     ("format", "j,f", "both"), ("format", "звук", "mp3"), ("quality", "4k", "max"), ("quality", "720р", "720"),
     ("quality", "vfrc", "max"), ("bitrate", "192kbps", "192")],
)
def test_choice_values(kind, value, expected):
    assert normalize_choice(kind, value)[0] == expected


def test_every_click_option_name_is_known_to_the_normalizer():
    """Имена ключей в argv.py и в Typer не должны разъезжаться."""
    from typer.main import get_command

    group = get_command(cli.app)
    for name, aliases in argv.COMMAND_ALIASES.items():
        command = group.commands[name]
        click_names = {n for p in command.params for n in getattr(p, "opts", []) if n.startswith("-")}
        click_names -= {"--help", "-h", "--справка", "--помощь"}
        table_names = {n for opt in argv.COMMAND_OPTS[name].values() for n in opt.names}
        assert click_names <= table_names, f"{name}: {click_names - table_names}"
        assert table_names <= click_names, f"{name}: {table_names - click_names}"
        for alias in aliases:
            assert alias in group.commands, alias


@pytest.mark.parametrize(
    ("text", "links"),
    [
        ("смотри: https://youtu.be/abc?t=5, круто!", ["https://youtu.be/abc?t=5"]),
        ("(https://www.tiktok.com/@nasa/video/1)", ["https://www.tiktok.com/@nasa/video/1"]),
        ("«https://www.instagram.com/reel/X/»", ["https://www.instagram.com/reel/X/"]),
        ("youtube.com/watch?v=1&t=2 и https://youtu.be/2 и снова https://youtu.be/2",
         ["youtube.com/watch?v=1&t=2", "https://youtu.be/2"]),
        ("просто текст", []),
        (None, []),
    ],
)
def test_extract_links(text, links):
    assert clipboard.extract_links(text) == links


@pytest.mark.parametrize(
    ("os_name", "which", "expected_cmd"),
    [("mac", {}, ["pbpaste"]), ("linux", {"wl-paste"}, ["wl-paste", "--no-newline"]),
     ("linux", {"xclip"}, ["xclip", "-o", "-selection", "clipboard"]), ("linux", {"xsel"}, ["xsel", "-ob"])],
)
def test_clipboard_readers(monkeypatch, os_name, which, expected_cmd):
    calls = []
    monkeypatch.setattr(system, "OS", os_name)
    monkeypatch.setattr(system, "WINDOWS_LIKE", False)
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name if name in which else None)
    monkeypatch.setattr(system, "run", lambda cmd, timeout=20: calls.append(cmd) or "https://youtu.be/x")
    assert clipboard.read() == "https://youtu.be/x"
    assert calls == [expected_cmd]


def test_clipboard_on_windows_uses_powershell(monkeypatch):
    monkeypatch.setattr(system, "WINDOWS_LIKE", True)
    monkeypatch.setattr(system, "powershell", lambda script, timeout=30, sta=False: "https://youtu.be/ю" if "Get-Clipboard" in script else None)
    assert clipboard.links() == ["https://youtu.be/ю"]


# --- команда скачать целиком (загрузка подменена) -----------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    for var, sub in (("VD_LIBRARY_DIR", "lib"), ("VD_WORK_DIR", "work"), ("VD_CONFIG_DIR", "cfg"), ("VD_TOOLS_DIR", "tools")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setattr(jobs, "BACKOFF", (0.01, 0.01, 0.01))
    return tmp_path


def fake_download(clip_path, calls, ask=False):
    def fake(url, mode, quality, work_dir, **kw):
        calls.append({"url": url, "mode": mode, "quality": quality})
        if ask:
            answer = kw["reporter"].question({
                "id": "q1", "code": "test_choice", "title": "Нет нужного формата", "message": "Есть только 720p",
                "options": [{"id": "take", "label": "Скачать 720p", "primary": True}, {"id": "cancel", "label": "Отменить"}],
                "default": "take",
            })  # fmt: skip
            calls[-1]["answer"] = answer
        dst = work_dir / "src.mp4"
        shutil.copyfile(clip_path, dst)
        return [Downloaded(dst, {"id": "UwullClrOuw", "title": "Ролик", "duration": 3, "webpage_url": url}, None)]

    return fake


@needs_ffmpeg
def test_download_with_cyrillic_flags_prints_full_path(cli_env, make_clip, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), calls))
    result = runner.invoke(cli.app, normalize(["скачать", URL, "-ф", "мп3", "-к", "720"]).args)
    assert result.exit_code == 0, result.output
    assert calls[0]["mode"] == "mp3" and calls[0]["url"] == URL
    assert system.display_path(cli_env / "lib" / "YouTube" / "Аудио") in result.output.replace("\n", "")
    assert "Готово" in result.output


@needs_ffmpeg
def test_no_url_takes_links_from_clipboard(cli_env, make_clip, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), calls))
    monkeypatch.setattr(clipboard, "read_strict", lambda: f"вот: {URL}")
    result = runner.invoke(cli.app, ["скачать", "-ф", "мп3"])
    assert result.exit_code == 0, result.output
    assert "Ссылка из буфера" in result.output and calls[0]["url"] == URL


def test_no_url_and_empty_clipboard_explains_both_ways(cli_env, monkeypatch):
    monkeypatch.setattr(clipboard, "read_strict", lambda: "мой-пароль-123")
    result = runner.invoke(cli.app, ["скачать", "-ф", "мп3"])
    assert result.exit_code == 1
    assert "текст, но не ссылка" in result.output and "кавычках" in result.output
    assert "мой-пароль" not in result.output  # содержимое буфера не печатаем


def test_unavailable_clipboard_is_not_called_empty(cli_env, monkeypatch):
    def broken():
        raise clipboard.Unavailable("PowerShell не ответил за 8 с")

    monkeypatch.setattr(clipboard, "read_strict", broken)
    result = runner.invoke(cli.app, ["скачать"])
    assert result.exit_code == 1
    assert "не удалось" in result.output and "PowerShell не ответил" in result.output


def test_clipboard_timeout_and_failure_are_unavailable(monkeypatch):
    monkeypatch.setattr(system, "WINDOWS_LIKE", True)
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/x/powershell.exe")
    seen = {}
    monkeypatch.setattr(system, "powershell", lambda script, timeout=30, sta=False: seen.update(t=timeout))
    with pytest.raises(clipboard.Unavailable):
        clipboard.read_strict()
    assert seen["t"] <= 10  # команда не ждёт PowerShell дольше нескольких секунд
    assert clipboard.read() is None
    monkeypatch.setattr(system, "powershell", lambda script, timeout=30, sta=False: "")
    assert clipboard.read_strict() == ""  # пусто — это не ошибка


def test_nothing_downloaded_is_a_failure_not_done(cli_env, monkeypatch):
    def fail(*a, **k):
        raise DownloadFailed("YouTube временно отказал в доступе (403)", transient=False)

    monkeypatch.setattr(jobs, "download", fail)
    result = runner.invoke(cli.app, ["d", URL, "-y"])
    assert result.exit_code == 1
    assert "Не удалось скачать" in result.output and "Готово" not in result.output
    assert "403" in result.output


@needs_ffmpeg
def test_question_is_asked_in_terminal(cli_env, make_clip, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), calls, ask=True))
    monkeypatch.setattr(cli, "_auto_accept", lambda yes: yes)  # CliRunner — не TTY, а тут нужен диалог
    result = runner.invoke(cli.app, ["d", URL], input="1\n")
    assert result.exit_code == 0, result.output
    assert "Нет нужного формата" in result.output and calls[0]["answer"] == "take"
    assert "Скачать 720p" in result.output  # заметка о решении под задачей


@needs_ffmpeg
def test_yes_flag_answers_automatically(cli_env, make_clip, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), calls, ask=True))
    result = runner.invoke(cli.app, normalize(["d", URL, "-д"]).args)
    assert result.exit_code == 0, result.output
    assert calls[0]["answer"] == "take" and "автоматически" in result.output


def test_folder_command_shows_departments(cli_env):
    result = runner.invoke(cli.app, ["папка"])
    assert result.exit_code == 0, result.output
    for name in ("YouTube", "TikTok", "Instagram", "Другие сайты", "Мои файлы"):
        assert name in result.output


@needs_ffmpeg
def test_folder_move_via_cli(cli_env, make_clip, monkeypatch):
    monkeypatch.delenv("VD_LIBRARY_DIR")
    from vydra.config import Prefs, Settings

    settings = Settings.from_env()
    Prefs(settings).set_library_dir(cli_env / "старая")
    runner.invoke(cli.app, ["папка"])
    (cli_env / "старая" / "TikTok" / "Видео" / "клип.mp4").write_bytes(make_clip("c.mp4").read_bytes())
    result = runner.invoke(cli.app, ["папка", str(cli_env / "новая"), "--перенести"])
    assert result.exit_code == 0, result.output
    assert (cli_env / "новая" / "TikTok" / "Видео" / "клип.mp4").is_file()
    assert not (cli_env / "старая").exists()


# --- показать в папке ---------------------------------------------------------------------


@needs_ffmpeg
def test_download_show_reveals_the_file(cli_env, make_clip, monkeypatch):
    shown = []
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), []))
    monkeypatch.setattr(system, "reveal", shown.append)
    result = runner.invoke(cli.app, ["d", URL, "-f", "mp3", "--показать"])
    assert result.exit_code == 0, result.output
    assert [p.name for p in shown] == ["Ролик.mp3"] and shown[0].is_file()
    assert "Показываю в папке" in result.output


def test_open_command_reveals_latest_or_match(cli_env, monkeypatch):
    lib = cli_env / "lib"
    runner.invoke(cli.app, ["папка"])  # раскладка хранилища
    (lib / "YouTube" / "Видео" / "Старое.mp4").write_bytes(b"x")
    newer = lib / "TikTok" / "Аудио" / "Новое.mp3"
    newer.write_bytes(b"x")
    import os
    import time

    os.utime(lib / "YouTube" / "Видео" / "Старое.mp4", (time.time() - 100, time.time() - 100))
    shown, played = [], []
    monkeypatch.setattr(system, "reveal", shown.append)
    monkeypatch.setattr(system, "open_path", played.append)
    assert runner.invoke(cli.app, ["показать"]).exit_code == 0
    assert runner.invoke(cli.app, ["open", "стар", "--play"]).exit_code == 0
    assert [p.name for p in shown] == ["Новое.mp3"] and [p.name for p in played] == ["Старое.mp4"]
    result = runner.invoke(cli.app, ["open", "нет-такого"])
    assert result.exit_code == 1 and "нет файла" in result.output


def test_open_command_on_empty_library(cli_env):
    result = runner.invoke(cli.app, ["открыть"])
    assert result.exit_code == 1 and "пусто" in result.output


# --- вывод для скриптов, коды выхода ------------------------------------------------------


def _json_line(output: str) -> dict:
    import json

    lines = [ln for ln in output.splitlines() if ln.startswith("{")]
    assert len(lines) == 1, output
    return json.loads(lines[0])


@needs_ffmpeg
def test_download_json_is_one_line_with_real_paths(cli_env, make_clip, monkeypatch):
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), []))
    result = runner.invoke(cli.app, ["d", URL, "-f", "both", "--json"])
    assert result.exit_code == 0, result.output
    data = _json_line(result.output)
    assert data["ok"] and data["exit_code"] == 0
    assert sorted(f["type"] for f in data["files"]) == ["mp3", "mp4"]
    for f in data["files"]:
        assert Path(f["path"]).is_file() and f["url"] == URL
    assert "Готово" not in result.output  # человеческий вывод молчит
    again = _json_line(runner.invoke(cli.app, ["d", URL, "-f", "both", "--json"]).output)
    assert again["ok"] and all(f["existing"] for f in again["files"])


def test_download_json_reports_failure(cli_env, monkeypatch):
    def broken(*a, **k):
        raise DownloadFailed("Видео приватное — скачать его нельзя.", transient=False)

    monkeypatch.setattr(jobs, "download", broken)
    result = runner.invoke(cli.app, ["d", URL, "--json"])
    assert result.exit_code == 1
    data = _json_line(result.output)
    assert not data["ok"] and data["jobs"][0]["error"].startswith("Видео приватное")


def test_json_error_before_download(cli_env):
    result = runner.invoke(cli.app, ["d", URL, "--clip", "5-1", "--json"])
    assert result.exit_code == 1
    assert _json_line(result.output)["ok"] is False


@needs_ffmpeg
def test_partial_failure_exit_code(cli_env, make_clip, monkeypatch):
    good = fake_download(make_clip("s.mp4"), [])

    def some_fail(url, *a, **k):
        if "bad" in url:
            raise DownloadFailed("Страница не найдена — проверьте ссылку.", transient=False)
        return good(url, *a, **k)

    monkeypatch.setattr(jobs, "download", some_fail)
    result = runner.invoke(cli.app, ["d", URL, "https://example.com/bad", "-f", "mp3"])
    assert result.exit_code == cli.EXIT_PARTIAL, result.output
    assert "Готово с ошибками" in result.output


def test_same_link_twice_is_downloaded_once(cli_env, monkeypatch):
    seen = []
    monkeypatch.setattr(jobs, "download", lambda url, *a, **k: seen.append(url) or [])
    runner.invoke(cli.app, ["d", URL, "https://youtu.be/UwullClrOuw", "-y"])
    assert len(seen) == 1


@pytest.mark.parametrize("link", ["https://www.youtube.com/watch?feature=share", "https://youtube.com/watch"])
def test_link_cut_at_ampersand_is_explained(cli_env, link):
    result = runner.invoke(cli.app, ["d", link])
    assert result.exit_code == 1 and "кавычки" in result.output


def test_list_json(cli_env):
    runner.invoke(cli.app, ["папка"])
    (cli_env / "lib" / "YouTube" / "Видео" / "Клип.mp4").write_bytes(b"x")
    data = _json_line(runner.invoke(cli.app, ["список", "--json"]).output)
    assert data["total"] == 1 and Path(data["items"][0]["abs_path"]).is_file()
    assert runner.invoke(cli.app, ["list", "--type", "кино"]).exit_code == 2


def test_cookies_command(cli_env, tmp_path):
    good = tmp_path / "cookies.txt"
    good.write_text("# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc\n")
    bad = tmp_path / "bad.txt"
    bad.write_text("просто текст")
    assert runner.invoke(cli.app, ["cookies", str(bad)]).exit_code == 1
    result = runner.invoke(cli.app, ["куки", str(good)])
    assert result.exit_code == 0 and "instagram.com" in result.output
    target = cli_env / "cfg" / "cookies.txt"
    assert target.is_file() and (target.stat().st_mode & 0o777) == 0o600
    assert runner.invoke(cli.app, ["cookies", "--remove"]).exit_code == 0 and not target.exists()


def test_cookie_errors_point_to_cookies_command():
    assert "выдра cookies" in cli.error_hint("Сайт просит войти в аккаунт. Добавьте cookies в настройках и повторите.")
    assert cli.error_hint("Не найден FFmpeg — запустите «vydra doctor --fix»") is None  # уже сказано, что делать


@needs_ffmpeg
def test_missing_folder_is_created(cli_env, make_clip, monkeypatch):
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), []))
    result = runner.invoke(cli.app, ["d", URL, "-f", "mp3", "--папка", "TikTok/Танцы"])
    assert result.exit_code == 0, result.output
    assert "создана" in result.output
    assert (cli_env / "lib" / "TikTok" / "Танцы" / "Ролик.mp3").is_file()


def test_bad_folder_name_is_explained(cli_env):
    result = runner.invoke(cli.app, ["d", URL, "--папка", "Мои:видео"])
    assert result.exit_code == 1 and "Нельзя использовать символы" in result.output


@needs_ffmpeg
def test_interactive_mode_skips_what_is_already_downloaded(cli_env, make_clip, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), calls))
    monkeypatch.setattr(clipboard, "read", lambda: None)
    monkeypatch.setattr("vydra.downloader.preview", lambda *a, **k: {"title": "Ролик", "url": URL})
    session = f"{URL}\n2\n192\n\n{URL}\n2\n192\n\n\n"  # дважды одно и то же, пустая строка — выход
    result = runner.invoke(cli.app, [], input=session)
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and "уже в хранилище" in result.output


def test_external_programs_never_read_the_terminal(monkeypatch):
    """Интероп Windows, запущенный с терминалом на stdin, съедал набранные строки (интерактивный режим)."""
    seen = []

    def fake_run(cmd, **kw):
        seen.append(kw.get("stdin"))
        raise OSError("нет")

    monkeypatch.setattr(system.subprocess, "run", fake_run)
    system.run(["clip-reader", "--get"])
    from vydra import media

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    with pytest.raises(OSError):
        media.Media(type("S", (), {"ffprobe": "ffprobe", "ffmpeg": "ffmpeg"})()).probe(Path("x.mp4"))
    assert seen == [system.subprocess.DEVNULL] * 2


@needs_ffmpeg
def test_both_formats_for_silent_video_keeps_the_video(cli_env, make_clip, monkeypatch):
    """Reel без звука с «-f both»: раньше — ошибка задачи и код 1, хотя MP4 сохранён (найдено матрицей)."""
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("silent.mp4", acodec=None), []))
    result = runner.invoke(cli.app, ["d", URL, "-f", "both", "--json"])
    assert result.exit_code == 0, result.output
    data = _json_line(result.output)
    assert [f["type"] for f in data["files"]] == ["mp4"]
    assert "нет звука" in data["jobs"][0]["warning"]
