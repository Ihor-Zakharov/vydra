import threading

import pytest

from conftest import needs_ffmpeg
from vydra.media import Cancelled, Media, MediaError

pytestmark = needs_ffmpeg


def codecs(media: Media, path):
    p = media.probe(path)
    return (p.video or {}).get("codec_name"), (p.audio or {}).get("codec_name"), p.duration


def test_vp9_opus_becomes_h264_aac(settings, make_clip, tmp_path):
    media = Media(settings)
    src = make_clip("in.webm", vcodec="libvpx-vp9", acodec="libopus", size="321x241")  # нечётные стороны
    out = tmp_path / "out.mp4"
    progress = []
    media.to_mp4(src, out, {"title": "Тест"}, progress.append, threading.Event())
    video, audio, duration = codecs(media, out)
    assert (video, audio) == ("h264", "aac")
    assert duration == pytest.approx(3, abs=0.2)
    assert progress[-1] == 100


def test_mp3_with_cover_and_tags(settings, make_clip, tmp_path):
    media = Media(settings)
    src = make_clip("in.mp4")
    cover = media.make_cover(make_clip("thumb.png", seconds=0.04, acodec=None), tmp_path / "cover.jpg")
    out = tmp_path / "out.mp3"
    media.to_mp3(src, out, 192, {"title": "Песня", "artist": "NASA"}, cover, lambda _: None, threading.Event())
    probe = media.probe(out)
    assert probe.audio["codec_name"] == "mp3"
    assert probe.video is None  # обложка — attached_pic, а не видеодорожка


def test_clip_is_frame_accurate(settings, make_clip, tmp_path):
    media = Media(settings)
    src = make_clip("long.mp4", seconds=10)
    out = tmp_path / "cut.mp4"
    media.to_mp4(src, out, {}, lambda _: None, threading.Event(), clip=(2.0, 5.5))
    assert media.probe(out).duration == pytest.approx(3.5, abs=0.1)


def test_clip_beyond_end_is_rejected(settings, make_clip, tmp_path):
    with pytest.raises(MediaError, match="дальше конца"):
        Media(settings).to_mp4(make_clip("a.mp4"), tmp_path / "x.mp4", {}, lambda _: None, threading.Event(),
                               clip=(30, None))


def test_mp3_from_silent_video_explains_why(settings, make_clip, tmp_path):
    with pytest.raises(MediaError, match="нет звука"):
        Media(settings).to_mp3(make_clip("mute.mp4", acodec=None), tmp_path / "x.mp3", 192, {}, None,
                               lambda _: None, threading.Event())


def test_cancel_stops_ffmpeg(settings, make_clip, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        Media(settings).to_mp4(make_clip("a.webm", vcodec="libvpx-vp9", acodec="libopus"), tmp_path / "x.mp4", {},
                               lambda _: None, cancel)
