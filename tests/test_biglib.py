"""Большое хранилище: постраничная выдача, поиск, сортировки, дешёвые итоги, фон без лавины записей,
наблюдатель без полного обхода; ссылка на оригинал из тегов файла; смена папки с переносом из UI и консоли."""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from conftest import needs_ffmpeg, wait_for
from vydra import cli, system
from vydra import library as libmod
from vydra.config import Prefs
from vydra.library import Library
from vydra.main import create_app
from vydra.media import Media, Probe

runner = CliRunner()


def put(root, rel, size=10, mtime=None):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def big(library):
    """120 файлов: 80 видео и 40 аудио в разных папках, разные размеры и даты."""
    root = library.root
    now = time.time()
    for n in range(120):
        kind = "Видео" if n % 3 else "Аудио"
        ext = ".mp4" if kind == "Видео" else ".mp3"
        folder = f"YouTube/{kind}" if n % 2 else f"TikTok/{kind}/Подборка"
        word = "космос" if n % 10 == 0 else "клип"
        put(root, f"{folder}/{word} {n:03d}{ext}", size=100 + n, mtime=now - n * 60)
    library.scan(force=True)
    return library


def test_query_pages_cover_everything_once(big):
    seen, offset = [], 0
    while offset is not None:
        page = big.query(limit=25, offset=offset)
        assert page["total"] == 120 and len(page["items"]) <= 25
        seen += [i["id"] for i in page["items"]]
        offset = page["next_offset"]
    assert len(seen) == len(set(seen)) == 120
    assert seen[0] == big.items()[0]["id"]  # по умолчанию — новые сверху, как весь список


def test_query_search_sort_type_folder(big):
    found = big.query(q="КОСМОС 000")  # все слова, без учёта регистра, по названию и имени файла
    assert found["total"] == 1 and "космос 000" in found["items"][0]["name"]
    assert big.query(q="космос")["total"] == 12
    by_size = big.query(sort="size", limit=3)["items"]
    assert [i["size"] for i in by_size] == [219, 218, 217]
    assert [i["size"] for i in big.query(sort="size", order="asc", limit=2)["items"]] == [100, 101]
    names = [i["title"] for i in big.query(sort="name", limit=120)["items"]]
    assert names == sorted(names, key=str.casefold)
    assert big.query(kind="audio")["total"] == 40
    assert big.query(folder="YouTube/Видео")["total"] == sum(1 for n in range(120) if n % 3 and n % 2)
    with pytest.raises(ValueError):
        big.query(sort="rating")


def test_duration_sort_puts_unknown_last(big):
    items = big._view().ordered
    for n, item in enumerate(items[:5]):
        item["duration"] = float(n)
    big._cache._sorted.clear()
    ordered = big.query(sort="duration", limit=120)["items"]
    assert [i["duration"] for i in ordered[:5]] == [4.0, 3.0, 2.0, 1.0, 0.0] and ordered[-1]["duration"] is None


def test_api_without_params_is_unchanged_and_paged_api_adds_fields(settings, big):
    with TestClient(create_app(settings, watch=False), base_url="http://localhost") as client:
        full = client.get("/api/library").json()
        assert set(full) == {"root", "stats", "items", "rev"} and len(full["items"]) == 120
        assert full["stats"]["count"] == 120 and full["stats"]["videos"] == 80
        assert set(full["items"][0]) >= {"id", "path", "type", "title", "size", "added", "folder", "name", "source"}
        page = client.get("/api/library", params={"limit": 10, "offset": 110, "sort": "size", "order": "asc"}).json()
        assert page["total"] == 120 and len(page["items"]) == 10 and page["next_offset"] is None
        assert page["stats"] == full["stats"] and page["rev"] == full["rev"]
        assert client.get("/api/library", params={"q": "космос", "type": "audio"}).json()["total"] == 4
        assert client.get("/api/library", params={"sort": "rating"}).status_code == 422
        assert client.get("/api/library", params={"limit": 0}).status_code == 422
        stats = client.get("/api/library/stats").json()
        assert stats["stats"] == full["stats"] and "rev" in stats
        folder = client.get("/api/fs", params={"path": "YouTube/Видео"}).json()
        assert "files_total" not in folder  # без параметров — как раньше
        paged = client.get("/api/fs", params={"path": "YouTube/Видео", "limit": 5}).json()
        assert paged["files_total"] == len(folder["files"]) and len(paged["files"]) == 5
        assert paged["folders"] == folder["folders"] and paged["next_offset"] == 5


def test_reads_are_cached_per_revision(big, monkeypatch):
    first = big.full_json()
    assert big.full_json() is first  # тот же ответ, пока хранилище не менялось
    put(big.root, "YouTube/Видео/новое.mp4")
    big.scan(force=True)
    assert big.full_json() is not first and json.loads(big.full_json())["stats"]["count"] == 121


def test_watched_library_does_not_walk_the_disk_on_every_read(big, monkeypatch):
    walks = []
    real = libmod._walk_media
    monkeypatch.setattr(libmod, "_walk_media", lambda root: walks.append(root) or real(root))
    big._watching.set()
    try:
        big._last_scan = 0
        big.items(), big.query(limit=5), big.list_dir("YouTube/Видео"), big.full_json()
    finally:
        big._watching.clear()
    assert walks == []  # сервер: свежесть держит наблюдатель
    big._last_scan = 0
    big.items()
    assert len(walks) == 1  # консоль: сверяется с диском сама


def test_snapshot_rereads_only_changed_folders(big, monkeypatch):
    cache: dict = {}
    first = libmod._snapshot(big.root, {}, cache)
    calls = []
    real = os.scandir
    monkeypatch.setattr(libmod.os, "scandir", lambda path: calls.append(path) or real(path))
    assert libmod._snapshot(big.root, {}, cache) == first and calls == []  # ничего не менялось — не читаем папки
    time.sleep(0.01)
    put(big.root, "TikTok/Видео/Подборка/Новая/клип.mp4")
    second = libmod._snapshot(big.root, {}, cache)
    assert second != first and "d:TikTok/Видео/Подборка/Новая" in second
    assert len(calls) <= 3  # перечитаны только изменившиеся папки


def test_background_work_is_queued_once_and_saved_in_batches(settings, monkeypatch):
    lib = Library(Prefs(settings), Media(settings), enrich=False)
    lib.ensure_layout()
    for n in range(40):
        put(lib.root, f"YouTube/Видео/ролик {n}.mp4")
    lib.scan(force=True)
    lib.scan(force=True)  # повторное сканирование не ставит те же файлы в очередь ещё раз
    assert lib._queue.qsize() == 40
    monkeypatch.setattr(lib.media, "probe", lambda path: Probe(duration=5.0, video=None, audio={}, tags={}))
    monkeypatch.setattr(lib.media, "make_poster", lambda *a: False)
    writes = []
    real = lib._write_index
    monkeypatch.setattr(lib, "_write_index", lambda: writes.append(1) or real())
    while not lib._queue.empty():
        item_id = lib._queue.get()
        lib._queued.discard(item_id)
        lib._attempted.add(item_id)
        lib._enrich(item_id)
    lib._flush_enrichment()
    assert len(writes) == 1  # раньше — запись индекса после каждого файла
    assert all(i["duration"] == 5.0 for i in lib.items())
    lib.scan(force=True)
    assert lib._queue.qsize() == 0  # уже обработанные не возвращаются в очередь


@needs_ffmpeg
def test_source_and_title_recovered_from_file_tags(settings, make_clip):
    """Файл, найденный сканированием (скачан другой копией выдры или до переустановки): ссылка на оригинал
    и название — из тегов, которые выдра пишет при сохранении."""
    import subprocess

    lib = Library(Prefs(settings), Media(settings), enrich=False)
    lib.ensure_layout()
    clip = make_clip("src.mp4")
    target = lib.root / "YouTube" / "Видео" / "ролик.mp4"
    subprocess.run([settings.ffmpeg, "-v", "error", "-y", "-i", str(clip), "-c", "copy", "-metadata",
                    "comment=https://www.youtube.com/watch?v=abcdefghijk", "-metadata", "title=Настоящее название",
                    str(target)], check=True)  # fmt: skip
    lib.scan(force=True)
    [item] = lib.items()
    assert item["source"] is None
    lib._enrich(item["id"])
    lib._flush_enrichment()
    [item] = lib.items()
    assert item["source"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert item["title"] == "Настоящее название" and item["probed"]


# --- смена папки хранилища ------------------------------------------------------------------


@pytest.fixture
def server(settings):
    with TestClient(create_app(settings, watch=False), base_url="http://localhost") as client:
        yield client


def test_ui_flow_switch_then_move_from_previous(server, settings, tmp_path):
    """Интерфейс: сначала меняет папку, потом спрашивает «перенести уже скачанное?» — POST {path, move: true}."""
    old = settings.default_library
    put(old, "YouTube/Видео/клип.mp4")
    put(old, "Документы/чужое.txt")
    server.post("/api/library/rescan")
    new = tmp_path / "Новая папка"
    switched = server.post("/api/settings/library", json={"path": str(new)}).json()
    assert server.get("/api/library").json()["stats"]["count"] == 0
    moved = server.post("/api/settings/library", json={"path": str(new), "move": True})
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["moved"]["bytes"] == 10 and body["library"] == switched["library"]
    assert (new / "YouTube/Видео/клип.mp4").is_file() and not (old / "YouTube/Видео/клип.mp4").exists()
    assert (old / "Документы/чужое.txt").is_file()  # чужое не трогаем
    assert server.get("/api/library").json()["stats"]["count"] == 1
    progress = server.get("/api/settings/library/move").json()
    assert progress["active"] is False and progress["result"]["bytes"] == 10
    again = server.post("/api/settings/library", json={"path": str(new), "move": True})
    assert again.status_code == 400 and "нечего" in again.json()["detail"]


def test_move_to_a_new_folder_directly_and_bad_targets(server, settings, tmp_path):
    put(settings.default_library, "TikTok/Аудио/песня.mp3")
    server.post("/api/library/rescan")
    inside = server.post("/api/settings/library", json={"path": str(settings.default_library / "внутри"), "move": True})
    assert inside.status_code == 400 and "внутри" in inside.json()["detail"]
    target = tmp_path / "Архив"
    ok = server.post("/api/settings/library", json={"path": str(target), "move": True})
    assert ok.status_code == 200 and (target / "TikTok/Аудио/песня.mp3").is_file()
    assert server.get("/api/settings").json()["library"]["path"] == system.display_path(target)


def test_interrupted_copy_is_not_duplicated_on_retry(tmp_path):
    """Обрыв между «скопировал» и «удалил исходник»: повтор не плодит « (2)», а доудаляет исходник."""
    src = put(tmp_path / "old", "a/клип.mp4", size=300)
    dst = put(tmp_path / "new", "a/клип.mp4", size=300)
    moved = []
    libmod._move_entry(src, dst, lambda s, d, n: moved.append(d), lambda name: None)
    assert moved == [dst] and not src.exists() and sorted(p.name for p in dst.parent.iterdir()) == ["клип.mp4"]
    other = put(tmp_path / "old", "a/клип.mp4", size=300)
    other.write_bytes(b"y" * 300)  # тот же размер, другое содержимое — это другой файл
    libmod._move_entry(other, dst, lambda s, d, n: moved.append(d), lambda name: None)
    assert moved[-1].name == "клип (2).mp4"


def test_cli_settings_storage_with_move(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("VD_WORK_DIR", str(settings.work_dir))
    monkeypatch.setenv("VD_CONFIG_DIR", str(settings.config_dir))
    monkeypatch.delenv("VD_LIBRARY_DIR", raising=False)
    old = tmp_path / "старое"
    Prefs(settings).set_library_dir(old)
    put(old, "YouTube/Видео/клип.mp4")
    new = tmp_path / "новое хранилище"
    result = runner.invoke(cli.app, ["настройки", "--папка", str(new), "--перенести"])
    assert result.exit_code == 0, result.output
    assert (new / "YouTube/Видео/клип.mp4").is_file() and Prefs(settings).library_dir == new
    assert runner.invoke(cli.app, ["settings", "--move"]).exit_code == 2
    assert runner.invoke(cli.app, ["settings", "--storage-reset"]).exit_code == 0
    assert Prefs(settings).library_dir == settings.default_library or "VideoDownloader" in str(Prefs(settings).library_dir)


def test_cli_list_pages_and_open_source(settings, big, monkeypatch):
    monkeypatch.setenv("VD_LIBRARY_DIR", str(big.root))
    monkeypatch.setenv("VD_WORK_DIR", str(settings.work_dir))
    monkeypatch.setenv("VD_CONFIG_DIR", str(settings.config_dir))
    data = json.loads(runner.invoke(cli.app, ["list", "--json", "-n", "50", "--страница", "3"]).output)
    assert data["total"] == 120 and data["pages"] == 3 and len(data["items"]) == 20
    result = runner.invoke(cli.app, ["список", "--поиск", "космос", "--сортировка", "размер", "-n", "5"])
    assert result.exit_code == 0 and "из 12" in result.output and "--страница 2" in result.output
    assert runner.invoke(cli.app, ["list", "--страница", "99"]).exit_code == 2
    with big._txn():
        item = next(iter(big._items.values()))
        item.source = "https://www.tiktok.com/@nasa/video/1"
        big._dirty = True
    opened = []
    monkeypatch.setattr(system, "open_url", opened.append)
    result = runner.invoke(cli.app, ["open", item.title, "--оригинал"])
    assert result.exit_code == 0, result.output
    assert opened == ["https://www.tiktok.com/@nasa/video/1"]
    assert cli.short_source("https://www.youtube.com/watch?v=x") == "youtube.com" and cli.short_source(None) == "—"


def test_mac_opens_links_with_open(monkeypatch):
    spawned = []
    monkeypatch.setattr(system, "OS", "mac")
    monkeypatch.setattr(system, "_spawn", lambda cmd, hint=None: spawned.append(cmd))
    system.open_url("https://youtu.be/x")
    assert spawned == [["open", "https://youtu.be/x"]]


def test_server_start_is_fast_on_a_large_library(settings, monkeypatch):
    """Индекс на тысячи файлов строится в фоне: сервер отвечает сразу, API — постранично и быстро."""
    root = settings.default_library
    for n in range(3000):
        put(root, f"YouTube/Видео/{n // 100:02d}/клип {n}.mp4")
    started = time.monotonic()
    with TestClient(create_app(settings, watch=False), base_url="http://localhost") as client:
        assert client.get("/api/health").status_code == 200
        assert time.monotonic() - started < 5
        assert wait_for(lambda: client.get("/api/library/stats").json()["stats"]["count"] == 3000, timeout=30)
        t = time.monotonic()
        assert len(client.get("/api/library", params={"limit": 50, "q": "клип 29"}).json()["items"]) == 50
        assert time.monotonic() - t < 1.5


def test_watcher_rescans_only_changed_folders_and_keeps_ids(big, monkeypatch):
    """Переименовали папку в Проводнике, добавили и удалили файл: сверяются только эти папки, id сохраняются."""
    root = big.root
    cache: dict = {}
    before = libmod._snapshot(root, {}, cache)
    moved_ids = {i["id"] for i in big.query(folder="TikTok/Видео/Подборка")["items"]}
    time.sleep(0.01)
    (root / "TikTok/Видео/Подборка").rename(root / "TikTok/Видео/Сборник")
    put(root, "YouTube/Аудио/новый.mp3")
    next(root.glob("YouTube/Видео/*.mp4")).unlink()
    after = libmod._snapshot(root, {}, cache)
    changed = libmod._changed_dirs(before, after)
    assert {"TikTok/Видео/Подборка", "TikTok/Видео/Сборник", "YouTube/Аудио", "YouTube/Видео"} <= changed
    monkeypatch.setattr(libmod, "_walk_media", lambda root: pytest.fail("полный обход не нужен"))
    result = big.scan(force=True, dirs=changed)
    assert result == {"added": 1, "removed": 1, "moved": len(moved_ids)}
    assert {i["id"] for i in big.query(folder="TikTok/Видео/Сборник")["items"]} == moved_ids
    assert big.stats()["count"] == 120
