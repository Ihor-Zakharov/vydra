from typer.testing import CliRunner

from conftest import needs_ffmpeg
from vydra.cli import app

runner = CliRunner()


def test_help_lists_russian_aliases():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "скачать" in result.output and "доктор" in result.output


def test_bad_clip_is_explained():
    result = runner.invoke(app, ["d", "https://youtu.be/x", "--clip", "5-1"])
    assert result.exit_code == 1


@needs_ffmpeg
def test_convert_into_custom_folder(make_clip, tmp_path, monkeypatch):
    monkeypatch.setenv("VD_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("VD_CONFIG_DIR", str(tmp_path / "cfg"))
    out = tmp_path / "Архив"
    result = runner.invoke(app, ["конвертировать", str(make_clip("song.mp4")), "-f", "mp3", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "Мои файлы" / "Аудио" / "song.mp3").is_file()


def test_port_checks_do_not_connect():
    """В WSL (mirrored + брандмауэр) connect к закрытому порту висит минутами — проверки обязаны быть мгновенными."""
    import socket
    import time

    from vydra.cli import _alive, _port_busy

    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        busy = srv.getsockname()[1]
        assert _port_busy(busy)
    started = time.monotonic()
    assert not _port_busy(busy)  # сокет закрыт — порт свободен
    assert not _alive(busy)
    assert time.monotonic() - started < 0.5


def test_usage_errors_are_russian_and_point_to_help():
    result = runner.invoke(app, ["d", "https://youtu.be/x", "-f", "mp5"])
    assert result.exit_code == 2
    assert "такого варианта нет" in result.output and "mp3" in result.output and "--help" in result.output
    assert "Invalid value" not in result.output


def test_usage_error_translations():
    from vydra.cli import translate_usage_error as t

    assert t("No such command 'скачатб'.") == "Нет команды «скачатб» — может быть, «скачать»?"
    assert t("Invalid value for '--port' / '-p' / '--порт': 70000 is not in the range 1<=x<=65535.") == (
        "Неверное значение --port (-p): 70000 — можно от 1 до 65535"
    )
    assert t("Missing argument 'files'.") == "Не хватает аргумента: файлы"
    assert t("Got unexpected extra argument(s) (x y)") == "Лишнее в команде: x y"
    assert t("Option '-f' requires an argument.") == "Ключу -f нужно значение"
    assert t("что-то новое") == "что-то новое"
