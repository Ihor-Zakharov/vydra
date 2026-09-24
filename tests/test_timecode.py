import pytest

from vydra.timecode import clip_label, format_time, parse_clip, parse_range, parse_time


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("90", 90), ("1:30", 90), ("01:02:03", 3723), ("1.5", 1.5), ("1,5", 1.5), ("1m30s", 90),
     ("1м30с", 90), ("2 мин", 120), ("1h", 3600), ("", None), ("конец", None)],
)
def test_parse_time(text, seconds):
    assert parse_time(text) == seconds


@pytest.mark.parametrize("text", ["abc", "1:2:3:4", "5 лет"])
def test_parse_time_rejects_garbage(text):
    with pytest.raises(ValueError, match="Примеры"):
        parse_time(text)


def test_parse_range_variants():
    assert parse_range("1:00-5:00") == (60, 300)
    assert parse_range("1:00 – 5:00") == (60, 300)
    assert parse_range("1:00-") == (60, None)
    assert parse_range("90 до 120") == (90, 120)


def test_clip_must_move_forward():
    with pytest.raises(ValueError, match="позже начала"):
        parse_clip("5:00", "1:00")
    assert parse_clip(None, None) is None
    assert parse_clip(None, "0:30") == (0.0, 30)


def test_format():
    assert format_time(59) == "0:59"
    assert format_time(3723) == "1:02:03"
    assert clip_label((60, None)) == "1:00–конец"
