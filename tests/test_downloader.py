"""Downloader service tests with yt-dlp fully mocked (no network, no ffmpeg)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yt_dlp

from bot.config import Settings
from bot.services import downloader as dl
from bot.services.downloader import (
    Downloader,
    ErrorKind,
    MediaDownloadError,
    ProgressEvent,
    classify_error,
    split_title,
    video_format_selector,
)
from bot.utils.regex import Platform


# --------------------------------------------------------------------------- fixtures
class FakeYDL:
    """Mimics yt_dlp.YoutubeDL: records opts, returns canned info, creates fake files."""

    info: dict[str, Any] = {}
    error: Exception | None = None
    last_opts: dict[str, Any] = {}
    file_size: int = 1024
    calls: list[tuple[str, bool]] = []

    def __init__(self, opts: dict[str, Any]) -> None:
        FakeYDL.last_opts = opts
        self.opts = opts

    def __enter__(self) -> "FakeYDL":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def sanitize_info(self, info: dict[str, Any]) -> dict[str, Any]:
        return info

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        FakeYDL.calls.append((url, download))
        if FakeYDL.error:
            raise FakeYDL.error
        info = dict(FakeYDL.info)
        if download and "outtmpl" in self.opts:
            is_audio = any(pp.get("key") == "FFmpegExtractAudio" for pp in self.opts.get("postprocessors", []))
            ext = "mp3" if is_audio else "mp4"
            path = Path(self.opts["outtmpl"].replace("%(id)s", info.get("id", "x")).replace("%(ext)s", ext))
            path.write_bytes(b"0" * FakeYDL.file_size)
            info["requested_downloads"] = [{"filepath": str(path)}]
            for hook in self.opts.get("progress_hooks", []):
                hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
                hook({"status": "finished"})
        return info


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(bot_token="123:TEST", download_dir=tmp_path / "dl", _env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def fake_ydl(monkeypatch: pytest.MonkeyPatch) -> type[FakeYDL]:
    FakeYDL.info = {"id": "abc", "title": "Artist - Song", "duration": 120, "uploader": "Artist", "width": 1280, "height": 720}
    FakeYDL.error = None
    FakeYDL.file_size = 1024
    FakeYDL.calls = []
    monkeypatch.setattr(dl.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(dl.ff, "ffmpeg_available", lambda: False)
    return FakeYDL


# --------------------------------------------------------------------------- pure functions
@pytest.mark.parametrize(
    "msg,kind",
    [
        ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", ErrorKind.PRIVATE),
        ("ERROR: Video unavailable", ErrorKind.REMOVED),
        ("ERROR: The uploader has not made this video available in your country", ErrorKind.GEO_BLOCKED),
        ("ERROR: Sign in to confirm your age. This video may be inappropriate for some users.", ErrorKind.AGE_RESTRICTED),
        ("ERROR: [Instagram] Requested content is not available, rate-limit reached or login required", ErrorKind.LOGIN_REQUIRED),
        ("ERROR: [youtube] Sign in to confirm you're not a bot", ErrorKind.LOGIN_REQUIRED),
        ("ERROR: This live event will begin in 2 hours", ErrorKind.LIVE),
        ("ERROR: Unsupported URL: https://example.com", ErrorKind.UNSUPPORTED),
        ("File is larger than max-filesize (300000000 bytes > 200000000 bytes)", ErrorKind.TOO_LARGE),
        ("something completely different", ErrorKind.UNKNOWN),
    ],
)
def test_classify_error(msg: str, kind: ErrorKind) -> None:
    assert classify_error(msg) is kind


def test_video_format_selector_youtube_quality() -> None:
    sel = video_format_selector(Platform.YOUTUBE, 360, 50 * 1024 * 1024)
    assert "height<=360" in sel
    assert "filesize_approx<?50M" in sel
    assert sel.endswith("/b")


def test_video_format_selector_tiktok_no_watermark() -> None:
    sel = video_format_selector(Platform.TIKTOK, None, 50 * 1024 * 1024)
    assert "format_note!*=watermark" in sel


def test_split_title() -> None:
    assert split_title({"title": "Shahzoda - Yetmadi"}) == ("Yetmadi", "Shahzoda")
    assert split_title({"track": "Yetmadi", "artist": "Shahzoda", "title": "x"}) == ("Yetmadi", "Shahzoda")
    assert split_title({"title": "Just a title", "uploader": "Chan - Topic"}) == ("Just a title", "Chan")


# --------------------------------------------------------------------------- async service
@pytest.mark.asyncio
async def test_download_video_success(settings: Settings, fake_ydl: type[FakeYDL]) -> None:
    d = Downloader(settings)
    events: list[ProgressEvent] = []

    async def progress(ev: ProgressEvent) -> None:
        events.append(ev)

    res = await d.download_video("https://www.youtube.com/watch?v=abc", 720, progress)
    await asyncio.sleep(0)  # let threadsafe callbacks land
    try:
        assert res.path.exists()
        assert res.title == "Artist - Song"
        assert res.width == 1280 and res.height == 720
        assert res.size == 1024
        assert not res.compressed
        assert "height<=720" in fake_ydl.last_opts["format"]
        assert fake_ydl.last_opts["merge_output_format"] == "mp4"
        assert fake_ydl.last_opts["noplaylist"] is True
        assert events and events[0].stage == "downloading" and events[0].percent == 50
    finally:
        res.cleanup()
    assert not res.job_dir.exists()


@pytest.mark.asyncio
async def test_download_audio_sets_mp3_postprocessor_and_metadata(settings: Settings, fake_ydl: type[FakeYDL]) -> None:
    d = Downloader(settings)
    res = await d.download_audio("https://www.youtube.com/watch?v=abc")
    try:
        assert res.path.suffix == ".mp3"
        assert res.title == "Song"
        assert res.performer == "Artist"
        keys = [pp["key"] for pp in fake_ydl.last_opts["postprocessors"]]
        assert "FFmpegExtractAudio" in keys and "FFmpegMetadata" in keys
        # extract_info (no download) then the real download
        assert fake_ydl.calls[0][1] is False and fake_ydl.calls[-1][1] is True
    finally:
        res.cleanup()


@pytest.mark.asyncio
async def test_download_audio_bitrate_fits_limit(settings: Settings, fake_ydl: type[FakeYDL]) -> None:
    fake_ydl.info = {**fake_ydl.info, "duration": 3600}  # 1 hour -> 192k would exceed 50 MB
    d = Downloader(settings)
    res = await d.download_audio("https://www.youtube.com/watch?v=abc")
    try:
        bitrate = int(fake_ydl.last_opts["postprocessors"][0]["preferredquality"])
        assert 48 <= bitrate < 192
        assert bitrate * 1000 / 8 * 3600 <= settings.max_file_size_bytes
    finally:
        res.cleanup()


@pytest.mark.asyncio
async def test_download_audio_too_long_for_limit(settings: Settings, fake_ydl: type[FakeYDL]) -> None:
    fake_ydl.info = {**fake_ydl.info, "duration": 10 * 3600}
    settings.max_duration_minutes = 100000
    d = Downloader(settings)
    with pytest.raises(MediaDownloadError) as ei:
        await d.download_audio("https://www.youtube.com/watch?v=abc")
    assert ei.value.kind is ErrorKind.TOO_LARGE


@pytest.mark.asyncio
async def test_download_video_too_large_without_ffmpeg(settings: Settings, fake_ydl: type[FakeYDL]) -> None:
    fake_ydl.file_size = settings.max_file_size_bytes + 1
    d = Downloader(settings)
    with pytest.raises(MediaDownloadError) as ei:
        await d.download_video("https://www.youtube.com/watch?v=abc", 1080)
    assert ei.value.kind is ErrorKind.TOO_LARGE
    assert not any(settings.download_dir.iterdir()), "job dir must be cleaned on failure"


@pytest.mark.asyncio
async def test_download_video_compresses_when_too_large(settings: Settings, fake_ydl: type[FakeYDL], monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ydl.file_size = settings.max_file_size_bytes + 1
    monkeypatch.setattr(dl.ff, "ffmpeg_available", lambda: True)

    async def fake_compress(src: Path, dst: Path, target: int, duration: float | None = None, max_height: int = 720) -> Path:
        dst.write_bytes(b"1" * 100)
        return dst

    async def fake_probe(path: Path) -> dl.ff.MediaProbe:
        return dl.ff.MediaProbe(duration=120, width=640, height=360, size=100)

    async def fake_thumb(src: Path, dst: Path, size: int = 320) -> Path | None:
        return None

    monkeypatch.setattr(dl.ff, "compress_video", fake_compress)
    monkeypatch.setattr(dl.ff, "probe", fake_probe)
    monkeypatch.setattr(dl.ff, "make_thumbnail", fake_thumb)

    d = Downloader(settings)
    res = await d.download_video("https://www.youtube.com/watch?v=abc", 1080)
    try:
        assert res.compressed
        assert res.size == 100
        assert res.height == 360
    finally:
        res.cleanup()


@pytest.mark.asyncio
async def test_private_video_maps_to_error_kind(settings: Settings, fake_ydl: type[FakeYDL]) -> None:
    fake_ydl.error = yt_dlp.utils.DownloadError("ERROR: [youtube] abc: Private video")
    d = Downloader(settings)
    with pytest.raises(MediaDownloadError) as ei:
        await d.download_video("https://www.youtube.com/watch?v=abc")
    assert ei.value.kind is ErrorKind.PRIVATE
    assert not any(settings.download_dir.iterdir())


@pytest.mark.asyncio
async def test_too_long_video_rejected(settings: Settings, fake_ydl: type[FakeYDL]) -> None:
    fake_ydl.info = {**fake_ydl.info, "duration": settings.max_duration_minutes * 60 + 1}
    d = Downloader(settings)
    with pytest.raises(MediaDownloadError) as ei:
        await d.download_video("https://www.youtube.com/watch?v=abc")
    assert ei.value.kind is ErrorKind.TOO_LONG


@pytest.mark.asyncio
async def test_search_parses_entries(settings: Settings, fake_ydl: type[FakeYDL]) -> None:
    fake_ydl.info = {
        "entries": [
            {"id": "id1", "title": "Shahzoda - Yetmadi", "duration": 215, "uploader": "Shahzoda", "url": "https://www.youtube.com/watch?v=id1"},
            {"id": "id2", "title": "Cover", "duration": None, "channel": "Someone"},
            None,
        ]
    }
    d = Downloader(settings)
    results = await d.search("Shahzoda Yetmadi", limit=5)
    assert fake_ydl.calls[0][0] == "ytsearch5:Shahzoda Yetmadi"
    assert fake_ydl.last_opts["extract_flat"] == "in_playlist"
    assert len(results) == 2
    assert results[0].title == "Shahzoda - Yetmadi" and results[0].duration == 215
    assert results[1].url == "https://www.youtube.com/watch?v=id2"
    assert results[1].uploader == "Someone"


@pytest.mark.asyncio
async def test_cookies_and_proxy_passed_to_ytdlp(settings: Settings, fake_ydl: type[FakeYDL], tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    settings.cookies_file = cookies
    settings.proxy = "socks5://127.0.0.1:1080"
    settings.yt_player_clients = "android,web"
    d = Downloader(settings)
    await d.extract_info("https://www.youtube.com/watch?v=abc")
    assert fake_ydl.last_opts["cookiefile"] == str(cookies)
    assert fake_ydl.last_opts["proxy"] == "socks5://127.0.0.1:1080"
    assert fake_ydl.last_opts["extractor_args"] == {"youtube": {"player_client": ["android", "web"]}}


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency(settings: Settings, fake_ydl: type[FakeYDL], monkeypatch: pytest.MonkeyPatch) -> None:
    settings.max_concurrent_downloads = 2
    d = Downloader(settings)
    active = {"now": 0, "max": 0}

    async def slow_run(opts: dict[str, Any], url: str, download: bool) -> dict[str, Any]:
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.02)
        active["now"] -= 1
        job_dir = Path(opts["outtmpl"]).parent
        p = job_dir / "abc.mp4"
        p.write_bytes(b"0")
        return {"id": "abc", "title": "t", "duration": 1, "requested_downloads": [{"filepath": str(p)}]}

    monkeypatch.setattr(d, "_run_ydl", slow_run)
    results = await asyncio.gather(*(d.download_video("https://youtu.be/abc") for _ in range(6)))
    for r in results:
        r.cleanup()
    assert active["max"] == 2
