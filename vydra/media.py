"""Конвертация через ffmpeg: MP4 (H.264 + AAC, открывается везде) и MP3 с обложкой."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import system
from .config import Settings

ProgressFn = Callable[[float], None]  # 0..100
Clip = tuple[float, float | None]  # (начало, конец|None) в секундах


class MediaError(Exception):
    """Понятная пользователю ошибка конвертации."""


class IntegrityError(MediaError):
    """Файл получился битым или неполным — имеет смысл повторить попытку."""


class NoAudio(MediaError):
    """В исходнике нет звуковой дорожки: MP3 сделать не из чего."""


class Cancelled(Exception):
    pass


def ffmpeg_threads() -> int:
    """Потоки x264: все ядра на 1080p съедают ~1 ГБ RAM, 8 — почти та же скорость и вдвое меньше памяти."""
    try:
        return max(1, int(os.environ["VD_FFMPEG_THREADS"]))
    except (KeyError, ValueError):
        return min(8, os.cpu_count() or 4)


@dataclass
class Probe:
    duration: float | None
    video: dict | None  # первая «настоящая» видеодорожка, не обложка
    audio: dict | None
    tags: dict | None = None  # теги контейнера (title, comment — туда пишется ссылка на оригинал), ключи в нижнем регистре


class Media:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def ffmpeg(self) -> str:
        if path := self.settings.ffmpeg:
            return path
        raise MediaError("Не найден FFmpeg — запустите «vydra doctor --fix» или «Починить» в интерфейсе")

    @property
    def ffprobe(self) -> str:
        if path := self.settings.ffprobe:
            return path
        raise MediaError("Не найден ffprobe — запустите «vydra doctor --fix» или «Починить» в интерфейсе")

    def probe(self, path: Path) -> Probe:
        try:
            result = subprocess.run(
                [self.ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=120,
                **system.child_flags(),
            )
        except subprocess.TimeoutExpired as exc:
            raise MediaError("ffprobe не ответил за 2 минуты — файл повреждён или диск недоступен") from exc
        if result.returncode != 0:
            raise MediaError("Файл повреждён или это не видео/аудио")
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams", [])
        video = next(
            (s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
            None,
        )
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = _float(data.get("format", {}).get("duration")) or _float((video or audio or {}).get("duration"))
        tags = {str(k).lower(): v for k, v in (data.get("format", {}).get("tags") or {}).items()}
        return Probe(duration=duration, video=video, audio=audio, tags=tags)

    def to_mp4(
        self,
        src: Path,
        dst: Path,
        meta: dict[str, str],
        on_progress: ProgressFn,
        cancel: threading.Event,
        clip: Clip | None = None,
    ) -> None:
        p = self.probe(src)
        if p.video is None:
            raise MediaError("В файле нет видео — выберите MP3")
        cut, length = _clip_args(clip, p.duration)
        args = [*cut, "-i", str(src), "-map", f"0:{p.video['index']}"]
        if p.audio is not None:
            args += ["-map", f"0:{p.audio['index']}"]

        # H.264 8 бит без обрезки копируем без потерь; VP9, AV1, HEVC и отрезки перекодируем
        if clip is None and p.video.get("codec_name") == "h264" and p.video.get("pix_fmt") in ("yuv420p", "yuvj420p"):
            args += ["-c:v", "copy"]
        else:
            args += [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-threads", str(ffmpeg_threads()),
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # libx264 требует чётные стороны
            ]  # fmt: skip
        if p.audio is not None:
            copy_audio = clip is None and p.audio.get("codec_name") == "aac"
            args += ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "192k"]

        args += ["-movflags", "+faststart", *_meta_args(meta), str(dst)]
        self._run(args, length, on_progress, cancel)
        self.validate(dst, "mp4", length, has_audio=p.audio is not None)

    def to_mp3(
        self,
        src: Path,
        dst: Path,
        bitrate: int,
        meta: dict[str, str],
        cover: Path | None,
        on_progress: ProgressFn,
        cancel: threading.Event,
        clip: Clip | None = None,
    ) -> None:
        p = self.probe(src)
        if p.audio is None:
            raise NoAudio("В файле нет звука — MP3 сделать не из чего")
        cut, length = _clip_args(clip, p.duration)
        args = [*cut, "-i", str(src)]
        if cover is not None:
            args += ["-i", str(cover)]
        args += ["-map", f"0:{p.audio['index']}", "-c:a", "libmp3lame", "-b:a", f"{bitrate}k"]
        if cover is not None:
            args += [
                "-map", "1:0", "-c:v", "copy", "-disposition:v:0", "attached_pic",
                "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)",
            ]  # fmt: skip
        args += ["-id3v2_version", "3", *_meta_args(meta), str(dst)]
        self._run(args, length, on_progress, cancel)
        self.validate(dst, "mp3", length)

    def validate(self, path: Path, kind: str, expected: float | None, has_audio: bool = True) -> None:
        """Проверка результата перед тем, как он попадёт в хранилище: дорожки, кодеки, длительность."""
        if not path.is_file() or path.stat().st_size == 0:
            raise IntegrityError("Файл получился пустым")
        try:
            p = self.probe(path)
        except MediaError as exc:
            raise IntegrityError("Результат не читается — файл повреждён") from exc
        if kind == "mp4":
            if not p.video or p.video.get("codec_name") != "h264":
                raise IntegrityError("В MP4 нет видеодорожки H.264")
            if has_audio and (not p.audio or p.audio.get("codec_name") != "aac"):
                raise IntegrityError("В MP4 пропал звук")
        elif not p.audio or p.audio.get("codec_name") != "mp3":
            raise IntegrityError("В MP3 нет звуковой дорожки")
        if expected:
            if not p.duration:
                raise IntegrityError("Не удалось определить длительность результата")
            if abs(p.duration - expected) > max(1.5, expected * 0.02):
                raise IntegrityError(
                    f"Длительность результата {p.duration:.1f} с вместо ожидаемых {expected:.1f} с — файл неполный"
                )

    def make_cover(self, image: Path, dst: Path) -> Path | None:
        """Превью ролика (webp/png/jpg) → JPEG для обложки MP3. Не вышло — просто без обложки."""
        try:
            result = subprocess.run(
                [
                    self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", str(image),
                    "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", str(dst),
                ],  # fmt: skip
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=60,
                **system.child_flags(),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return dst if result.returncode == 0 and dst.is_file() and dst.stat().st_size > 0 else None

    def make_poster(self, src: Path, dst: Path, probe: Probe) -> bool:
        """Постер для хранилища: кадр из видео или встроенная обложка аудио."""
        if probe.video is not None:
            at = min(3.0, (probe.duration or 0) * 0.1)
            args = ["-ss", f"{at:.2f}", "-i", str(src), "-map", f"0:{probe.video['index']}"]
        else:
            args = ["-i", str(src), "-map", "0:v:0?", "-an"]
        result = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args,
             "-frames:v", "1", "-vf", "scale='min(640,iw)':-2", "-q:v", "4", str(dst)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=60,
            **system.child_flags(),
        )  # fmt: skip
        if result.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            return True
        dst.unlink(missing_ok=True)
        return False

    def _run(
        self,
        args: list[str],
        duration: float | None,
        on_progress: ProgressFn,
        cancel: threading.Event,
    ) -> None:
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as stderr:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                encoding="utf-8",
                errors="replace",
                **system.child_flags(),
            )
            # отмена срабатывает, даже если ffmpeg долго молчит (например, ищет ключевой кадр)
            watcher = threading.Thread(target=_kill_on, args=(proc, cancel), daemon=True)
            watcher.start()
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    if cancel.is_set():
                        break
                    key, _, value = line.strip().partition("=")
                    if key == "out_time_us" and duration and value.isdigit():
                        on_progress(min(99.0, int(value) / 1e6 / duration * 100))
                code = proc.wait()
            finally:
                system.kill_tree(proc)
            if cancel.is_set():
                raise Cancelled
            if code != 0:
                stderr.seek(0)
                tail = stderr.read().strip().splitlines()[-3:]
                raise MediaError("Ошибка ffmpeg: " + (" / ".join(tail) or f"код {code}"))
        on_progress(100.0)


def _kill_on(proc: subprocess.Popen, cancel: threading.Event) -> None:
    while proc.poll() is None:
        if cancel.wait(0.3):
            system.kill_tree(proc)
            return


def _clip_args(clip: Clip | None, duration: float | None) -> tuple[list[str], float | None]:
    """-ss/-t перед -i: быстрый и при перекодировании точный поиск. Возвращает и длину результата."""
    if clip is None:
        return [], duration
    start, end = clip
    if duration and start >= duration:
        raise MediaError("Начало отрезка дальше конца видео")
    args = ["-ss", f"{start:.3f}"] if start > 0 else []
    if end is not None:
        args += ["-t", f"{end - start:.3f}"]
    stop = min(end, duration) if end is not None and duration else end or duration
    return args, (stop - start) if stop else None


def _meta_args(meta: dict[str, str]) -> list[str]:
    args: list[str] = []
    for key, value in meta.items():
        if value:
            args += ["-metadata", f"{key}={value}"]
    return args


def _float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
