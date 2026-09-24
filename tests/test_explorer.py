"""Проводник по хранилищу: папки, переименование, перемещение, удаление, миграция, синхронизация."""

import json
import os
import time

import pytest

from conftest import needs_ffmpeg, wait_for
from vydra.config import Prefs
from vydra.library import (
    LEGACY_CINEMA, SERVICE, Conflict, InvalidName, Library, LibraryUnavailable, NotFound, Protected, validate_name,
    validate_rel,
)  # fmt: skip
from vydra.media import Media


def put(library, rel: str, data: bytes = b"x" * 100) -> None:
    path = library.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def paths(library) -> set[str]:
    library.scan(force=True)
    return {i["path"] for i in library.items()}


def ids_by_name(library) -> dict[str, str]:
    return {i["name"]: i["id"] for i in library.items()}


@pytest.mark.parametrize("bad", ["", "  ", "a/b", "a\\b", "x:y", "CON", "con.txt", "LPT1", "имя.", ".скрытая",
                                 "..", "a\x07b", "я" * 121, "*"])
def test_invalid_names(bad):
    with pytest.raises(InvalidName):
        validate_name(bad)


def test_names_are_trimmed_and_normalized():
    assert validate_name("  Танцы  ") == "Танцы"


@pytest.mark.parametrize("bad", ["../x", "a/../b", ".vydra", "TikTok/.vydra", "a\\b", "a//b", "a/./b", "x\x00"])
def test_invalid_paths(bad):
    with pytest.raises(InvalidName):
        validate_rel(bad)


def test_layout_has_platform_departments(library):
    listing = library.list_dir("")
    names = [f["name"] for f in listing["folders"]]
    assert names[:5] == ["YouTube", "TikTok", "Instagram", "Другие сайты", "Мои файлы"]
    tiktok = next(f for f in listing["folders"] if f["name"] == "TikTok")
    assert tiktok["system"] and tiktok["platform"] == "tiktok"
    sub = library.list_dir("TikTok")
    assert [f["name"] for f in sub["folders"]] == ["Видео", "Аудио"]
    assert all(f["system"] for f in sub["folders"])
    assert [c["path"] for c in sub["breadcrumbs"]] == ["", "TikTok"]
    assert not (library.root / LEGACY_CINEMA).exists()


def test_make_folder_and_collisions(library):
    folder = library.make_folder("TikTok", "Танцы")
    assert folder["path"] == "TikTok/Танцы" and not folder["system"]
    with pytest.raises(Conflict):
        library.make_folder("TikTok", "ТАНЦЫ")  # как в Windows — без учёта регистра
    with pytest.raises(NotFound):
        library.make_folder("Нет такой", "x")
    library.make_folder("", "Музыка для зала")
    assert (library.root / "Музыка для зала").is_dir()


def test_system_folders_are_protected(library):
    for rel in ("TikTok", "TikTok/Видео", "мои файлы/аудио", ""):
        with pytest.raises(Protected):
            library.rename(rel, "x") if rel else library.remove(rel)
        with pytest.raises(Protected):
            library.remove(rel, recursive=True)
    with pytest.raises(Protected):
        library.move(["YouTube"], "TikTok")


def test_rename_file_keeps_extension_and_id(library):
    put(library, "TikTok/Видео/клип.mp4")
    library.scan(force=True)
    before = ids_by_name(library)["клип.mp4"]
    new = library.rename("TikTok/Видео/клип.mp4", "Лучший клип")  # без расширения — допишем .mp4
    assert new == "TikTok/Видео/Лучший клип.mp4"
    assert ids_by_name(library)["Лучший клип.mp4"] == before
    assert paths(library) == {"TikTok/Видео/Лучший клип.mp4"}


def test_rename_folder_updates_index_and_case_only_rename(library):
    library.make_folder("TikTok", "танцы")
    put(library, "TikTok/танцы/a.mp4")
    put(library, "TikTok/танцы/глубже/b.mp3")
    library.scan(force=True)
    ids = ids_by_name(library)
    library.rename("TikTok/танцы", "Танцы")  # только регистр
    assert paths(library) == {"TikTok/Танцы/a.mp4", "TikTok/Танцы/глубже/b.mp3"}
    assert ids_by_name(library) == ids
    with pytest.raises(Conflict):
        library.make_folder("TikTok", "Другое") and library.rename("TikTok/Другое", "танцы")


def test_move_files_and_folders_with_collisions(library):
    put(library, "YouTube/Видео/ролик.mp4", b"1" * 10)
    put(library, "TikTok/Видео/ролик.mp4", b"2" * 20)
    library.make_folder("", "Избранное")
    library.scan(force=True)
    ids = {i["path"]: i["id"] for i in library.items()}
    moved = library.move(["YouTube/Видео/ролик.mp4", "TikTok/Видео/ролик.mp4"], "Избранное")
    assert [m["to"] for m in moved] == ["Избранное/ролик.mp4", "Избранное/ролик (2).mp4"]
    after = {i["path"]: i["id"] for i in library.items()}
    assert after["Избранное/ролик.mp4"] == ids["YouTube/Видео/ролик.mp4"]
    assert after["Избранное/ролик (2).mp4"] == ids["TikTok/Видео/ролик.mp4"]
    library.make_folder("Избранное", "Вложенная")
    with pytest.raises(Conflict):
        library.move(["Избранное"], "Избранное/Вложенная")  # внутрь самой себя
    library.move(["Избранное"], "Мои файлы")
    assert paths(library) == {"Мои файлы/Избранное/ролик.mp4", "Мои файлы/Избранное/ролик (2).mp4"}


def test_remove_needs_confirmation_for_non_empty_folder(library):
    library.make_folder("", "Старьё")
    put(library, "Старьё/a.mp4")
    library.scan(force=True)
    with pytest.raises(Conflict):
        library.remove("Старьё")
    library.remove("Старьё", recursive=True)
    assert not (library.root / "Старьё").exists() and paths(library) == set()
    with pytest.raises(NotFound):
        library.remove("Старьё")


def test_platform_is_metadata_and_survives_moves(library):
    put(library, "Instagram/Видео/reel.mp4")
    library.scan(force=True)
    assert library.items()[0]["platform"] == "instagram"
    library.make_folder("", "Архив")
    library.move(["Instagram/Видео/reel.mp4"], "Архив")
    item = library.items()[0]
    assert item["platform"] == "instagram" and item["folder"] == "Архив"


def test_manual_move_in_explorer_keeps_id(library):
    put(library, "TikTok/Видео/танец.mp4", b"z" * 321)
    library.scan(force=True)
    before = library.items()[0]["id"]
    (library.root / "Мои файлы/Видео").mkdir(parents=True, exist_ok=True)
    os.rename(library.root / "TikTok/Видео/танец.mp4", library.root / "Мои файлы/Видео/танец.mp4")
    library.scan(force=True)
    assert library.items()[0]["id"] == before


# --- миграция старой раскладки ---------------------------------------------------------


def old_layout_library(settings) -> Library:
    root = settings.default_library
    for rel in ("Видео/TikTok/a.mp4", "Видео/Концерты/c.mp4", "Видео/d.mp4", "Аудио/YouTube/b.mp3"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"m" * 50)
    (root / SERVICE).mkdir(parents=True)
    items = [
        {"id": f"id{n}", "path": rel, "type": "video" if rel.endswith("mp4") else "audio", "title": rel,
         "platform": "tiktok", "size": 50, "added": 1}
        for n, rel in enumerate(("Видео/TikTok/a.mp4", "Видео/Концерты/c.mp4", "Видео/d.mp4", "Аудио/YouTube/b.mp3"))
    ]  # fmt: skip
    (root / SERVICE / "library.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    return Library(Prefs(settings), Media(settings))


def test_migration_moves_everything_and_keeps_ids(settings):
    library = old_layout_library(settings)
    library.ensure_layout()
    expected = {
        "id0": "TikTok/Видео/a.mp4",
        "id1": "Мои файлы/Видео/Концерты/c.mp4",
        "id2": "Мои файлы/Видео/d.mp4",
        "id3": "YouTube/Аудио/b.mp3",
    }
    assert {i["id"]: i["path"] for i in library.items()} == expected
    root = library.root
    assert not (root / "Видео").exists() and not (root / "Аудио").exists()
    assert json.loads((root / SERVICE / "layout.json").read_text())["version"] == 2
    library.make_folder("", "Видео")  # после миграции «Видео» в корне — обычная папка пользователя
    put(library, "Видео/своё.mp4")
    library.ensure_layout()
    assert (root / "Видео/своё.mp4").is_file()


def test_interrupted_migration_is_finished_on_next_start(settings):
    library = old_layout_library(settings)
    root = library.root
    (root / "TikTok/Видео").mkdir(parents=True)
    os.rename(root / "Видео/TikTok/a.mp4", root / "TikTok/Видео/a.mp4")  # прошлый запуск успел только это
    library.ensure_layout()
    assert {i["id"]: i["path"] for i in library.items()}["id0"] == "TikTok/Видео/a.mp4"
    assert len(library.items()) == 4


# --- восстановление и недоступность --------------------------------------------------------


def test_corrupted_index_is_restored_from_backup(settings, library):
    put(library, "TikTok/Видео/a.mp4")
    library.scan(force=True)
    put(library, "TikTok/Видео/b.mp4")
    library.scan(force=True)  # теперь в .bak — версия с одним файлом, в library.json — с двумя
    ids = ids_by_name(library)
    (library.root / SERVICE / "library.json").write_text("{битый json", encoding="utf-8")
    fresh = Library(Prefs(settings), Media(settings))
    fresh.scan(force=True)
    assert ids_by_name(fresh)["a.mp4"] == ids["a.mp4"]  # id старых файлов сохранились
    assert set(ids_by_name(fresh)) == {"a.mp4", "b.mp4"}


def test_disconnected_drive_does_not_wipe_index(settings, library):
    put(library, "TikTok/Видео/a.mp4")
    library.scan(force=True)
    root = library.root
    moved = root.with_name("отключено")
    os.rename(root, moved)  # «диск отключили»
    assert library.scan(force=True) == {}
    os.rename(moved, root)
    assert paths(library) == {"TikTok/Видео/a.mp4"}
    Prefs(settings).set_library_dir(settings.work_dir / "нет" / "такого" / "диска")
    with pytest.raises(LibraryUnavailable):
        library.ensure_layout()


def test_stale_partials_are_swept(library):
    put(library, "TikTok/Видео/old.mp4.part")
    put(library, "TikTok/Видео/old.mp4", b"")
    put(library, "TikTok/Видео/fresh.mp4.part")
    old = time.time() - 3600
    for name in ("old.mp4.part", "old.mp4"):
        os.utime(library.root / "TikTok/Видео" / name, (old, old))
    assert library.sweep_partials() == 2
    assert (library.root / "TikTok/Видео/fresh.mp4.part").exists()


def test_watcher_sees_external_changes(library):
    library.start_watching(interval=0.1)
    try:
        rev = library.rev
        time.sleep(0.3)
        put(library, "YouTube/Видео/из проводника.mp4")
        assert wait_for(lambda: "из проводника.mp4" in {i["name"] for i in library.items()}, timeout=5)
        assert library.rev > rev
    finally:
        library.stop_watching()
