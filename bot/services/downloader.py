"""yt-dlp based media downloader. All blocking work runs in a thread executor."""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yt_dlp

from bot.config import Settings
from bot.services import ffmpeg as ff
from bot.utils.regex import Platform, detect_platform

log = logging.getLogger(__name__)

ProgressCallback = Callable[["ProgressEvent"], Awaitable[None]]

QUALITIES = (360, 720, 1080)


class ErrorKind(StrEnum):
    PRIVATE = "private"
    REMOVED = "removed"
    GEO_BLOCKED = "geo_blocked"
    AGE_RESTRICTED = "age_restricted"
    LOGIN_REQUIRED = "login_required"
    LIVE = "live"
    TOO_LARGE = "too_large"
    TOO_LONG = "too_long"
    UNSUPPORTED = "unsupported"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class MediaDownloadError(Exception):
    def __init__(self, kind: ErrorKind, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass(slots=True)
class ProgressEvent:
    stage: str  # downloading | processing | uploading
    percent: int | None = None
    downloaded: int | None = None
    total: int | None = None


@dataclass(slots=True)
class SearchResult:
    video_id: str
    title: str
    duration: float | None
    uploader: str | None
    url: str


@dataclass(slots=True)
class DownloadResult:
    path: Path
    job_dir: Path
    title: str
    duration: float | None
    performer: str | None
    thumbnail: Path | None
    width: int | None = None
    height: int | None = None
    size: int = 0
    compressed: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def cleanup(self) -> None:
        shutil.rmtree(self.job_dir, ignore_errors=True)


def classify_error(message: str) -> ErrorKind:
    """Map yt-dlp's free-text errors to a small set of user-facing categories."""
    m = (message or "").lower()
    checks: list[tuple[tuple[str, ...], ErrorKind]] = [
        (("is a live", "live event", "live stream", "premieres in"), ErrorKind.LIVE),
        (("available in your country", "in your country", "geo-restricted", "geo restricted", "not available from your location", "geoblock", "geo-blocked"), ErrorKind.GEO_BLOCKED),
        (("age-restricted", "age restricted", "confirm your age", "inappropriate for some users", "sign in to confirm your age"), ErrorKind.AGE_RESTRICTED),
        (("private video", "this video is private", "private account", "this account is private"), ErrorKind.PRIVATE),
        (("sign in to confirm", "login required", "log in", "requested content is not available, rate-limit", "use --cookies", "cookies", "not a bot", "empty media response", "rate-limit reached", "http error 401", "http error 403", "checkpoint_required"), ErrorKind.LOGIN_REQUIRED),
        (("video unavailable", "has been removed", "no longer available", "does not exist", "http error 404", "not found", "unable to extract", "no video could be found", "this post is unavailable", "copyright"), ErrorKind.REMOVED),
        (("file is larger than max-filesize", "exceeds maximum"), ErrorKind.TOO_LARGE),
        (("unsupported url", "no video formats found", "requested format is not available", "unsupported"), ErrorKind.UNSUPPORTED),
        (("timed out", "timeout"), ErrorKind.TIMEOUT),
    ]
    for needles, kind in checks:
        if any(n in m for n in needles):
            return kind
    return ErrorKind.UNKNOWN


def video_format_selector(platform: Platform | None, quality: int | None, max_bytes: int) -> str:
    """Build a yt-dlp format string that prefers mp4 and fits size when the size is known."""
    size_mb = max(1, max_bytes // (1024 * 1024))
    if platform is Platform.TIKTOK:
        # TikTok formats carry "watermarked" in format_note; prefer the clean ones.
        return (
            f"b[ext=mp4][format_note!*=watermark][filesize<?{size_mb}M]/"
            "b[ext=mp4][format_note!*=watermark]/"
            "bv*[format_note!*=watermark]+ba/b[ext=mp4]/b"
        )
    if platform is Platform.INSTAGRAM:
        return f"b[ext=mp4][filesize<?{size_mb}M]/b[ext=mp4]/bv*+ba/b"

    h = quality or 720
    return (
        f"bv*[height<={h}][ext=mp4][filesize_approx<?{size_mb}M]+ba[ext=m4a]/"
        f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/"
        f"bv*[height<={h}]+ba/"
        f"b[height<={h}][ext=mp4]/b[height<={h}]/b"
    )


class Downloader:
    def __init__(self, settings: Settings, semaphore: asyncio.Semaphore | None = None) -> None:
        self.settings = settings
        self.semaphore = semaphore or asyncio.Semaphore(settings.max_concurrent_downloads)
        self.download_dir = settings.download_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ opts
    def _base_opts(self) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "nocheckcertificate": False,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "concurrent_fragment_downloads": 4,
            "logger": _YtdlpLogger(),
            "noprogress": True,
            "color": "never",
            "geo_bypass": True,
        }
        if self.settings.cookies_file and Path(self.settings.cookies_file).exists():
            opts["cookiefile"] = str(self.settings.cookies_file)
        if self.settings.proxy:
            opts["proxy"] = self.settings.proxy
        if self.settings.yt_player_clients:
            opts["extractor_args"] = {
                "youtube": {"player_client": self.settings.yt_player_clients.split(",")}
            }
        return opts

    def _new_job_dir(self) -> Path:
        d = self.download_dir / uuid.uuid4().hex
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --------------------------------------------------------------- helpers
    async def _run_ydl(self, opts: dict[str, Any], url: str, download: bool) -> dict[str, Any]:
        def _work() -> dict[str, Any]:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=download)
                return ydl.sanitize_info(info) if info else {}

        try:
            info = await asyncio.wait_for(
                asyncio.to_thread(_work), timeout=self.settings.download_timeout_seconds
            )
        except asyncio.TimeoutError as e:
            raise MediaDownloadError(ErrorKind.TIMEOUT, "yt-dlp timed out") from e
        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            kind = classify_error(msg)
            log.warning("yt-dlp error [%s] for %s: %s", kind, url, msg)
            raise MediaDownloadError(kind, msg) from e
        except MediaDownloadError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("unexpected yt-dlp failure for %s", url)
            raise MediaDownloadError(classify_error(str(e)), str(e)) from e
        if not info:
            raise MediaDownloadError(ErrorKind.UNKNOWN, "empty info")
        return info

    @staticmethod
    def _progress_hook(loop: asyncio.AbstractEventLoop, cb: ProgressCallback | None, min_interval: float = 2.0):  # type: ignore[no-untyped-def]
        last = {"t": 0.0, "pct": -1}

        def hook(d: dict[str, Any]) -> None:
            if cb is None:
                return
            status = d.get("status")
            if status == "finished":
                asyncio.run_coroutine_threadsafe(cb(ProgressEvent("processing", 100)), loop)
                return
            if status != "downloading":
                return
            now = time.monotonic()
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            pct = int(downloaded * 100 / total) if total else None
            if now - last["t"] < min_interval and pct != 100:
                return
            if pct is not None and pct == last["pct"]:
                return
            last["t"], last["pct"] = now, pct if pct is not None else -1
            asyncio.run_coroutine_threadsafe(
                cb(ProgressEvent("downloading", pct, downloaded, total)), loop
            )

        return hook

    @staticmethod
    def _result_path(info: dict[str, Any], job_dir: Path, exts: tuple[str, ...]) -> Path:
        rd = info.get("requested_downloads") or []
        for item in rd:
            fp = item.get("filepath")
            if fp and Path(fp).exists():
                return Path(fp)
        for p in sorted(job_dir.iterdir(), key=lambda p: p.stat().st_size, reverse=True):
            if p.suffix.lower() in exts:
                return p
        raise MediaDownloadError(ErrorKind.UNKNOWN, "downloaded file not found")

    @staticmethod
    def _find_thumbnail(job_dir: Path) -> Path | None:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            for p in job_dir.glob(f"*{ext}"):
                return p
        return None

    def _check_duration(self, info: dict[str, Any]) -> None:
        dur = info.get("duration")
        if dur and dur > self.settings.max_duration_minutes * 60:
            raise MediaDownloadError(ErrorKind.TOO_LONG, f"duration {dur}s")
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise MediaDownloadError(ErrorKind.LIVE, "live stream")

    # -------------------------------------------------------------- public
    async def extract_info(self, url: str) -> dict[str, Any]:
        opts = self._base_opts() | {"skip_download": True}
        return await self._run_ydl(opts, url, download=False)

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        opts = self._base_opts() | {"extract_flat": "in_playlist", "skip_download": True}
        info = await self._run_ydl(opts, f"ytsearch{limit}:{query}", download=False)
        results: list[SearchResult] = []
        for e in info.get("entries") or []:
            if not e or not e.get("id"):
                continue
            results.append(
                SearchResult(
                    video_id=e["id"],
                    title=e.get("title") or e["id"],
                    duration=e.get("duration"),
                    uploader=e.get("uploader") or e.get("channel"),
                    url=e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
                )
            )
        return results

    async def download_video(
        self,
        url: str,
        quality: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        platform = detect_platform(url)
        max_bytes = self.settings.max_file_size_bytes
        job_dir = self._new_job_dir()
        loop = asyncio.get_running_loop()
        opts = self._base_opts() | {
            "format": video_format_selector(platform, quality, max_bytes),
            "merge_output_format": "mp4",
            "outtmpl": str(job_dir / "%(id)s.%(ext)s"),
            "writethumbnail": True,
            "progress_hooks": [self._progress_hook(loop, progress)],
            # allow a bigger intermediate file; we can re-encode it down later
            "max_filesize": max_bytes * 4,
            "postprocessors": [
                {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            ],
        }
        try:
            async with self.semaphore:
                info = await self._run_ydl(opts, url, download=True)
                self._check_duration(info)
                path = self._result_path(info, job_dir, (".mp4", ".mkv", ".webm", ".mov"))
                result = await self._finalize_video(path, info, job_dir, max_bytes, progress)
                return result
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

    async def _finalize_video(
        self,
        path: Path,
        info: dict[str, Any],
        job_dir: Path,
        max_bytes: int,
        progress: ProgressCallback | None,
    ) -> DownloadResult:
        size = path.stat().st_size
        compressed = False
        duration = info.get("duration")
        width, height = info.get("width"), info.get("height")

        if size > max_bytes:
            if progress:
                await progress(ProgressEvent("processing"))
            if not ff.ffmpeg_available():
                raise MediaDownloadError(ErrorKind.TOO_LARGE, f"{size} bytes, ffmpeg missing")
            target = int(max_bytes * 0.95)
            dst = path.with_name(path.stem + "_small.mp4")
            try:
                await ff.compress_video(path, dst, target, duration)
            except ff.FFmpegError as e:
                raise MediaDownloadError(ErrorKind.TOO_LARGE, str(e)) from e
            path.unlink(missing_ok=True)
            path = dst
            size = path.stat().st_size
            compressed = True
            if size > max_bytes:
                raise MediaDownloadError(ErrorKind.TOO_LARGE, f"still {size} bytes after compress")
            try:
                pr = await ff.probe(path)
                width, height, duration = pr.width, pr.height, pr.duration or duration
            except ff.FFmpegError:
                pass

        thumb_src = self._find_thumbnail(job_dir)
        thumb: Path | None = None
        if ff.ffmpeg_available():
            thumb = await ff.make_thumbnail(thumb_src or path, job_dir / "thumb.jpg")

        return DownloadResult(
            path=path,
            job_dir=job_dir,
            title=info.get("title") or info.get("id") or "video",
            duration=duration,
            performer=info.get("uploader") or info.get("channel"),
            thumbnail=thumb,
            width=width,
            height=height,
            size=size,
            compressed=compressed,
            extra={"id": info.get("id"), "webpage_url": info.get("webpage_url")},
        )

    async def download_audio(self, url: str, progress: ProgressCallback | None = None) -> DownloadResult:
        max_bytes = self.settings.max_file_size_bytes
        job_dir = self._new_job_dir()
        loop = asyncio.get_running_loop()

        async def _pick_bitrate() -> int:
            # Fit the MP3 under the size cap: bitrate = size*8/duration (90% margin), min 48k.
            try:
                info = await self.extract_info(url)
            except MediaDownloadError:
                return self.settings.audio_bitrate_kbps
            self._check_duration(info)
            dur = info.get("duration") or 0
            if not dur:
                return self.settings.audio_bitrate_kbps
            fit = int(max_bytes * 8 / dur / 1000 * 0.9)
            if fit < 48:
                raise MediaDownloadError(ErrorKind.TOO_LARGE, f"audio of {dur}s cannot fit {max_bytes} bytes")
            return min(self.settings.audio_bitrate_kbps, fit)

        try:
            async with self.semaphore:
                bitrate = await _pick_bitrate()
                opts = self._base_opts() | {
                    "format": "ba[ext=m4a]/ba/b",
                    "outtmpl": str(job_dir / "%(id)s.%(ext)s"),
                    "writethumbnail": True,
                    "progress_hooks": [self._progress_hook(loop, progress)],
                    "max_filesize": max_bytes * 2,
                    "postprocessors": [
                        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(bitrate)},
                        {"key": "FFmpegMetadata", "add_metadata": True},
                        {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                        {"key": "EmbedThumbnail", "already_have_thumbnail": True},
                    ],
                }
                info = await self._run_ydl(opts, url, download=True)
                path = self._result_path(info, job_dir, (".mp3", ".m4a", ".opus", ".webm"))
                size = path.stat().st_size
                if size > max_bytes:
                    raise MediaDownloadError(ErrorKind.TOO_LARGE, f"audio {size} bytes")
                thumb = None
                thumb_src = self._find_thumbnail(job_dir)
                if thumb_src and ff.ffmpeg_available():
                    thumb = await ff.make_thumbnail(thumb_src, job_dir / "thumb.jpg")
                title, performer = split_title(info)
                return DownloadResult(
                    path=path,
                    job_dir=job_dir,
                    title=title,
                    duration=info.get("duration"),
                    performer=performer,
                    thumbnail=thumb,
                    size=size,
                    extra={"id": info.get("id"), "bitrate": bitrate},
                )
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise


def split_title(info: dict[str, Any]) -> tuple[str, str | None]:
    """Derive (title, performer) from yt-dlp info: prefer track/artist tags, then 'Artist - Title'."""
    track = info.get("track")
    artist = info.get("artist") or info.get("creator")
    if track:
        return str(track), (str(artist) if artist else None)
    title = str(info.get("title") or info.get("id") or "audio")
    uploader = info.get("uploader") or info.get("channel")
    for sep in (" - ", " – ", " — ", " | "):
        if sep in title:
            left, right = title.split(sep, 1)
            if 0 < len(left.strip()) <= 60:
                return right.strip(), left.strip()
    if uploader:
        uploader = str(uploader).removesuffix(" - Topic").strip()
    return title, uploader or None


class _YtdlpLogger:
    """Route yt-dlp log output into the stdlib logger (debug level for noise)."""

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug] "):
            return
        log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp warning: %s", msg)

    def error(self, msg: str) -> None:
        log.debug("yt-dlp error: %s", msg)
