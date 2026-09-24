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
