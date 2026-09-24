import json

from conftest import needs_ffmpeg
from vydra.library import CINEMA, SERVICE


def test_layout_and_cinema(library):
    root = library.root
    for platform in ("YouTube", "TikTok", "Instagram", "Другие сайты", "Мои файлы"):
        assert (root / platform / "Видео").is_dir() and (root / platform / "Аудио").is_dir()
    assert (root / CINEMA).is_file()
    assert (root / SERVICE / "fonts" / "fonts.css").is_file()
    text = (root / SERVICE / "library.js").read_text(encoding="utf-8")
    assert text.startswith("window.VYDRA_LIBRARY = ")
    assert json.loads(text.removeprefix("window.VYDRA_LIBRARY = ").rstrip(";\n"))["items"] == []


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
    assert library.resolve(CINEMA) is not None


def test_change_root(library, tmp_path):
    new_root = tmp_path / "Кино"
    library.set_root(new_root)
    assert library.root == new_root
    assert (new_root / CINEMA).is_file()
