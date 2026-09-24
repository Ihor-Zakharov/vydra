"""Выбор форматов проверяем самим yt-dlp на синтетических наборах — как у настоящих сайтов."""

from yt_dlp import YoutubeDL

from vydra.downloader import format_spec


def pick(mode: str, quality: str, formats: list[dict]) -> str:
    fmt, sort = format_spec(mode, quality)
    opts = {"format": fmt, "format_sort": sort, "quiet": True, "no_warnings": True, "simulate": True}
    with YoutubeDL(opts) as ydl:
        info = ydl.process_ie_result(
            {"id": "x", "title": "t", "extractor": "test", "extractor_key": "Test",
             "webpage_url": "https://example.com/v", "formats": formats},
            download=False,
        )
    return info["format_id"]


def fmt(fid, *, v="none", a="none", h=None, w=None, note=None, tbr=1000, ext="mp4"):
    return {"format_id": fid, "url": f"https://example.com/{fid}", "ext": ext, "vcodec": v, "acodec": a,
            "height": h, "width": w, "format_note": note, "tbr": tbr, "protocol": "https"}


TIKTOK = [
    fmt("download", v="h264", a="aac", note="watermarked", tbr=5000),
    fmt("h264_720p", v="h264", a="aac", h=1280, w=720, tbr=1500),
    fmt("h265_1080p", v="h265", a="aac", h=1920, w=1080, tbr=2000),
    fmt("audio", a="aac", ext="m4a", tbr=128),
]
YOUTUBE = [
    fmt("137", v="avc1.640028", h=1080, w=1920, tbr=2900),
    fmt("136", v="avc1.4d401f", h=720, w=1280, tbr=1000),
    fmt("313", v="vp9", h=2160, w=3840, tbr=12000, ext="webm"),
    fmt("616", v="vp09.00.40.08", h=1080, w=1920, tbr=5700, note="Premium"),
    fmt("140", a="mp4a.40.2", tbr=128, ext="m4a"),
    fmt("251", a="opus", tbr=160, ext="webm"),
]


def test_tiktok_never_picks_watermarked_version():
    assert pick("mp4", "1080", TIKTOK) == "h264_720p"  # H.264 без знака важнее, чем HEVC 1080p
    assert pick("mp4", "max", TIKTOK) == "h265_1080p"


def test_tiktok_mp3_takes_sound_of_the_video_not_the_music_track():
    assert pick("mp3", "1080", TIKTOK) != "audio"


def test_youtube_prefers_h264_and_aac_up_to_1080p():
    assert pick("mp4", "1080", YOUTUBE) == "137+140"
    assert pick("mp4", "720", YOUTUBE) == "136+140"


def test_youtube_max_takes_best_resolution_but_not_premium():
    assert pick("mp4", "max", YOUTUBE) == "313+251"


def test_vertical_video_quality_is_by_short_side():
    vertical = [fmt("v1080", v="avc1", h=1920, w=1080), fmt("v720", v="avc1", h=1280, w=720), fmt("a", a="mp4a")]
    assert pick("mp4", "720", vertical) == "v720+a"
