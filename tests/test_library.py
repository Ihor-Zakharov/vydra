import json

from conftest import needs_ffmpeg
from vydra.library import LEGACY_CINEMA, SERVICE


def test_layout(library):
    root = library.root
    for platform in ("YouTube", "TikTok", "Instagram", "Другие сайты", "Мои файлы"):
        assert (root / platform / "Видео").is_dir() and (root / platform / "Аудио").is_dir()
    assert not (root / LEGACY_CINEMA).exists()  # офлайн-кинотеатр больше не создаётся
    assert json.loads((root / SERVICE / "library.json").read_text(encoding="utf-8"))["items"] == []
    assert not (root / SERVICE / "library.js").exists() and not (root / SERVICE / "fonts").exists()


def test_legacy_cinema_is_removed_but_user_files_are_kept(settings):
    from vydra.config import Prefs
    from vydra.library import Library
    from vydra.media import Media

    root = settings.default_library
    (root / SERVICE / "fonts").mkdir(parents=True)
    ours = '<script src=".vydra/library.js"></script><script src=".vydra/cinema.js"></script>'
    (root / LEGACY_CINEMA).write_text(ours, encoding="utf-8")
    for name in ("cinema.js", "cinema.css", "library.js"):
        (root / SERVICE / name).write_text("x", encoding="utf-8")
    Library(Prefs(settings), Media(settings)).ensure_layout()
    assert not (root / LEGACY_CINEMA).exists() and not (root / SERVICE / "cinema.js").exists()
    assert not (root / SERVICE / "fonts").exists()

    (root / LEGACY_CINEMA).write_text("<h1>мой собственный файл</h1>", encoding="utf-8")
    Library(Prefs(settings), Media(settings)).ensure_layout()
    assert (root / LEGACY_CINEMA).read_text(encoding="utf-8") == "<h1>мой собственный файл</h1>"


@needs_ffmpeg
def test_add_places_file_by_type_and_platform(library, make_clip):
    item = library.add(make_clip("x.mp4"), platform="tiktok", stem="Танец", ext="mp4", title="Танец",
                       source="https://www.tiktok.com/@nasa/video/1")
    assert item.path == "TikTok/Видео/Танец.mp4"
    assert item.duration and item.width == 320
    assert library.get(item.id).source.startswith("https://www.tiktok.com")


@needs_ffmpeg
def test_scan_picks_up_manual_files_and_forgets_deleted(library, make_clip):
    manual = library.root / "Мои файлы" / "Видео" / "home.mp4"
    manual.write_bytes(make_clip("home.mp4").read_bytes())
    assert library.scan(force=True) == {"added": 1, "removed": 0, "moved": 0}
    item = library.items()[0]
    assert (item["platform"], item["title"]) == ("file", "home")
    manual.unlink()
    assert library.scan(force=True) == {"added": 0, "removed": 1, "moved": 0}


def test_resolve_blocks_traversal(library):
    assert library.resolve("../../etc/passwd") is None
    assert library.resolve(f"{SERVICE}/library.json") is not None


def test_change_root(library, tmp_path):
    new_root = tmp_path / "Кино"
    library.set_root(new_root)
    assert library.root == new_root
    assert (new_root / "TikTok" / "Видео").is_dir()


def test_fresh_machine_without_downloads_folder(settings, tmp_path, monkeypatch):
    """Чистая система: ~/Downloads ещё нет — хранилище всё равно создаётся (а отключённый диск — нет)."""
    import dataclasses

    from vydra.config import Prefs
    from vydra.library import Library, LibraryUnavailable
    from vydra.media import Media

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    fresh = dataclasses.replace(settings, default_library=home / "Downloads" / "VideoDownloader")
    lib = Library(Prefs(fresh), Media(fresh))
    lib.ensure_layout()
    assert (home / "Downloads" / "VideoDownloader" / "YouTube" / "Видео").is_dir()
    Prefs(fresh).set_library_dir(tmp_path / "отключённый диск" / "Кино" / "lib")
    import pytest

    with pytest.raises(LibraryUnavailable):
        lib.ensure_layout()
