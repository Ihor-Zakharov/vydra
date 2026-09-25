from vydra.naming import safe_stem, save_unique, title_for


def test_windows_forbidden_characters():
    assert safe_stem('Q&A: "Artemis" <live> | 1/2?*') == "Q&A - 'Artemis' (live) - 1-2"


def test_reserved_names_and_trailing_dots():
    assert safe_stem("CON") == "_CON"
    assert safe_stem("title...  ") == "title"
    assert safe_stem("   ") == "video"


def test_hashtags_newlines_and_length():
    stem = safe_stem("Танцы в космосе #nasa #space\nвторая строка " + "очень " * 40)
    assert "#" not in stem and "\n" not in stem
    assert len(stem) <= 100
    assert not stem.endswith(" ")


def test_title_for_instagram_generic_title_uses_caption():
    assert title_for({"title": "Video by nasa", "description": "Around the Moon and back.\nmore"}) == (
        "Around the Moon and back."
    )


def test_title_for_tiktok_truncated_title_uses_full_caption():
    info = {"title": "Turns out you can dance in space! Microgravity just unlocked so...",
            "description": "Turns out you can dance in space! Microgravity just unlocked some moves #nasa"}
    assert title_for(info).endswith("some moves #nasa")


def test_save_unique_never_overwrites(tmp_path):
    folder = tmp_path / "out"
    names = []
    for n in range(3):
        src = tmp_path / f"src{n}.mp4"
        src.write_bytes(b"x" * (n + 1))
        names.append(save_unique(src, folder, "clip", "mp4").name)
    assert names == ["clip.mp4", "clip (2).mp4", "clip (3).mp4"]
    assert (folder / "clip (3).mp4").read_bytes() == b"xxx"
    assert not list(folder.glob("*.part"))


def test_hard_exit_removes_copies_in_flight(tmp_path, monkeypatch):
    """Второй Ctrl+C во время копирования в хранилище: пустая заготовка и .part не остаются в папке."""
    import threading

    from vydra import naming

    src = tmp_path / "src.mp4"
    src.write_bytes(b"x" * 1024)
    started, release = threading.Event(), threading.Event()

    def slow_copy(a, b):
        open(b, "wb").write(b"x" * 10)
        started.set()
        release.wait(5)

    monkeypatch.setattr(naming.shutil, "copyfile", slow_copy)
    folder = tmp_path / "Видео"
    def save():
        try:
            naming.save_unique(src, folder, "Ролик", "mp4")
        except OSError:
            pass  # .part убран из-под копирования — так и задумано

    worker = threading.Thread(target=save, daemon=True)
    worker.start()
    assert started.wait(5)
    naming.abandon_in_flight()
    assert sorted(p.name for p in folder.iterdir()) == []
    release.set()
    worker.join(5)
    assert not naming.IN_FLIGHT
