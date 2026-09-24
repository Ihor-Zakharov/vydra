"""Смена папки хранилища с переносом: ничего не теряется, чужое не трогается, прерывание не страшно."""

import errno
import os

import pytest

from vydra import library as libmod
from vydra.config import Prefs
from vydra.library import SERVICE, Library
from vydra.media import Media


def put(root, rel, data=b"x" * 64):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def filled(library):
    root = library.root
    put(root, "TikTok/Видео/танец.mp4")
    put(root, "YouTube/Аудио/песня.mp3")
    put(root, "Музыка для зала/трек.mp3")
    put(root, "клип в корне.mp4")
    (root / "Пустая своя").mkdir()
    put(root, "Документы/отчёт.docx", b"doc")  # чужое: папка без медиа
    put(root, "заметки.txt", b"txt")  # чужое: не медиа
    library.scan(force=True)
    return library


def ids(library):
    return {i["path"]: i["id"] for i in library.items()}


def test_move_keeps_ids_layout_and_leaves_foreign_files(filled, tmp_path):
    old_root = filled.root
    before = ids(filled)
    new_root = tmp_path / "Новое хранилище"
    result = filled.move_root(new_root)
    assert filled.root == new_root
    assert ids(filled) == before  # те же пути относительно корня и те же id
    for rel in before:
        assert (new_root / rel).is_file()
    assert (new_root / "Пустая своя").is_dir()
    assert (new_root / "Instagram" / "Видео").is_dir()  # отделы созданы
    assert (old_root / "Документы/отчёт.docx").read_bytes() == b"doc"  # чужое осталось на месте
    assert (old_root / "заметки.txt").is_file()
    assert not (old_root / SERVICE).exists() and not (old_root / "TikTok").exists()
    assert result["bytes"] == 64 * 4


def test_move_merges_into_existing_library_with_conflicts(filled, tmp_path):
    new_root = tmp_path / "другое"
    put(new_root, "TikTok/Видео/танец.mp4", b"other" * 10)
    before = ids(filled)
    filled.move_root(new_root)
    after = {i["path"]: i["id"] for i in filled.items()}
    assert after["TikTok/Видео/танец (2).mp4"] == before["TikTok/Видео/танец.mp4"]
    assert (new_root / "TikTok/Видео/танец.mp4").read_bytes() == b"other" * 10  # чужой файл не перезаписан
    assert len(after) == len(before) + 1


def test_cross_device_copy_verifies_and_keeps_source_on_failure(filled, tmp_path, monkeypatch):
    real_rename = os.rename

    def no_rename(src, dst):
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(libmod.os, "rename", no_rename)
    before = ids(filled)
    filled.move_root(tmp_path / "другой диск")
    monkeypatch.setattr(libmod.os, "rename", real_rename)
    assert ids(filled) == before
    assert not list((tmp_path / "другой диск").rglob("*.part"))


def test_interrupted_move_resumes(filled, tmp_path, monkeypatch):
    old_root = filled.root
    new_root = tmp_path / "новая"
    real = libmod._move_entry
    calls = {"n": 0}

    def flaky(src, dst, moved_file, report):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(errno.EIO, "диск отвалился")
        return real(src, dst, moved_file, report)

    monkeypatch.setattr(libmod, "_move_entry", flaky)
    before = ids(filled)
    with pytest.raises(OSError):
        filled.move_root(new_root)
    assert filled.root == old_root  # переключения не было
    assert filled._idle.is_set()  # сканирование снова работает
    monkeypatch.setattr(libmod, "_move_entry", real)
    filled.move_root(new_root)  # повторный запуск доделывает
    assert filled.root == new_root and ids(filled) == before


@pytest.mark.parametrize("where", ["same", "inside"])
def test_move_refuses_bad_targets(filled, where):
    target = filled.root if where == "same" else filled.root / "TikTok" / "внутри"
    with pytest.raises(ValueError):
        filled.move_root(target)


def test_move_refuses_read_only_target(filled, tmp_path):
    ro = tmp_path / "только чтение"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        with pytest.raises(ValueError, match="нельзя записывать"):
            filled.move_root(ro / "lib")
    finally:
        ro.chmod(0o755)


def test_fixed_library_cannot_be_moved(settings, tmp_path):
    import dataclasses

    fixed = dataclasses.replace(settings, fixed_library=tmp_path / "fixed")
    lib = Library(Prefs(fixed), Media(fixed))
    lib.ensure_layout()
    with pytest.raises(ValueError, match="VD_LIBRARY_DIR"):
        lib.move_root(tmp_path / "другая")
