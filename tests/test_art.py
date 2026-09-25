"""Заставка интерактивной консоли: размеры, режимы цвета, когда показывается и настройка."""

import dataclasses
import io
import re
import time

import pytest
from rich.console import Console
from typer.testing import CliRunner

from vydra import art, cli
from vydra.config import Prefs, Settings

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.mark.parametrize("mode", ["truecolor", "256", "16", None])
@pytest.mark.parametrize(("cols", "rows"), [(48, 8), (64, 8), (100, 10), (120, 12), (180, 16)])
def test_art_fits_exactly(cols, rows, mode):
    lines = art.render(cols, rows, mode)
    assert len(lines) == rows
    for line in lines:
        assert len(ANSI.sub("", line)) == cols  # ни одна строка не переносится и не короче окна
    if mode is None:
        assert not any("\x1b" in line for line in lines)  # без цвета — ни одного управляющего кода


def test_art_is_fast():
    started = time.perf_counter()
    art.render(180, 16, "truecolor")
    assert time.perf_counter() - started < 0.2  # на обычной машине ~35 мс


@pytest.mark.parametrize(("cols", "rows", "expected"), [
    (120, 40, (116, 10)), (140, 40, (136, 10)), (300, 80, (180, 16)), (100, 20, (97, 8)), (47, 40, None),
    (120, 15, None),
])  # fmt: skip
def test_size_is_a_quarter_of_the_screen(cols, rows, expected):
    assert art.size_for(cols, rows) == expected


def test_enabled_by_default_setting_and_env(monkeypatch):
    monkeypatch.delenv("VYDRA_NO_ART", raising=False)
    assert art.enabled(None) and art.enabled(True) and not art.enabled(False)
    monkeypatch.setenv("VYDRA_NO_ART", "1")
    assert not art.enabled(True)
    monkeypatch.setenv("VYDRA_NO_ART", "0")
    assert art.enabled(None)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("VD_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("VD_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("VD_LIBRARY_DIR", str(tmp_path / "lib"))
    monkeypatch.delenv("VYDRA_NO_ART", raising=False)
    return Settings.from_env()


def terminal(monkeypatch, **kw):
    fake = Console(file=io.StringIO(), force_terminal=True, width=120, height=40, **kw)
    monkeypatch.setattr(cli, "console", fake)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    return out


def test_art_in_a_real_terminal(cfg, monkeypatch):
    out = terminal(monkeypatch, color_system="truecolor")
    assert cli.show_art(cfg)
    assert len(out.getvalue().splitlines()) == 10 and "▀" in out.getvalue() and "38;2;" in out.getvalue()


def test_art_without_color_has_no_escape_codes(cfg, monkeypatch):
    out = terminal(monkeypatch, color_system="truecolor", no_color=True)
    assert cli.show_art(cfg)
    assert "\x1b" not in out.getvalue() and out.getvalue().strip()


def test_no_art_when_not_a_terminal_json_or_disabled(cfg, monkeypatch):
    monkeypatch.setattr(cli, "console", Console(file=io.StringIO(), force_terminal=False))
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert not cli.show_art(cfg)  # пайп
    terminal(monkeypatch, color_system="truecolor")
    cli.json_mode(True)
    try:
        assert not cli.show_art(cfg)  # --json
    finally:
        cli.json_mode(False)
    Prefs(cfg).set_console_art(False)
    assert not cli.show_art(cfg)  # выключена в настройках
    Prefs(cfg).set_console_art(True)
    monkeypatch.setenv("VYDRA_NO_ART", "1")
    assert not cli.show_art(cfg)  # переменная перекрывает настройку
    monkeypatch.delenv("VYDRA_NO_ART")
    monkeypatch.setattr(cli, "console", Console(file=io.StringIO(), force_terminal=True, width=40, height=40))
    assert not cli.show_art(cfg)  # слишком узко


def test_interactive_mode_through_a_pipe_prints_no_art(cfg, monkeypatch):
    monkeypatch.setattr(cli.clipboard, "read", lambda: None)
    result = runner.invoke(cli.app, [], input="\n")
    assert result.exit_code == 0 and "▀" not in result.output and "интерактивный режим" in result.output


def test_settings_command_saves_and_shows(cfg, monkeypatch):
    assert runner.invoke(cli.app, ["настройки", "--заставка", "выкл"]).exit_code == 0
    assert Prefs(cfg).console_art is False
    result = runner.invoke(cli.app, ["settings"])
    assert "выключена" in result.output and "--заставка вкл" in result.output
    assert runner.invoke(cli.app, ["settings", "--art", "on"]).exit_code == 0
    assert Prefs(cfg).console_art is True
    monkeypatch.setenv("VYDRA_NO_ART", "1")
    assert "VYDRA_NO_ART" in runner.invoke(cli.app, ["settings"]).output
    assert runner.invoke(cli.app, ["settings", "--art", "может"]).exit_code == 2


def test_art_setting_does_not_disturb_server_prefs(cfg, tmp_path):
    """prefs.json общий с веб-интерфейсом: заставка не стирает выбранную там папку и наоборот."""
    prefs = Prefs(dataclasses.replace(cfg, fixed_library=None))
    prefs.set_library_dir(tmp_path / "Кино")
    prefs.set_console_art(False)
    assert prefs.library_dir == tmp_path / "Кино" and prefs.console_art is False
    prefs.set_library_dir(tmp_path / "Музыка")
    assert prefs.console_art is False


def test_art_leaves_room_on_the_right_and_fades_at_both_ends():
    """Пользователь: заставка не должна упираться в правый край окна, а горизонт — обрываться (скриншот 25.09)."""
    cols, rows = art.size_for(140, 40)
    assert 140 - cols >= 3
    scene = art.Scene(cols, rows * 2)
    y = int(scene.horizon)
    lum = [max(scene.pixel(x, y)) for x in range(cols)]
    assert max(lum[0], lum[1], lum[-2], lum[-1]) < art.CLEAR  # у самых краёв — фон терминала
    assert lum[cols // 4] > 2 * lum[1] and lum[cols - 8] > lum[-1]  # к краям гаснет плавно, а не обрывается
    assert all(len(ANSI.sub("", line)) == cols for line in art.render(cols, rows, "truecolor"))
