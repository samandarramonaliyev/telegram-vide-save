"""Async wrappers around ffmpeg / ffprobe (never block the event loop)."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

MIN_VIDEO_KBPS = 200  # below this a re-encode is unwatchable -> give up
AUDIO_KBPS_FOR_COMPRESS = 96


class FFmpegError(RuntimeError):
    pass


@dataclass(slots=True)
class MediaProbe:
    duration: float
    width: int | None
    height: int | None
    size: int


def ffmpeg_available() -> bool:
    return shutil.which(FFMPEG) is not None and shutil.which(FFPROBE) is not None


async def _run(*args: str, timeout: float = 1800) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FFmpegError(f"{args[0]} timed out after {timeout}s")
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def probe(path: Path) -> MediaProbe:
    code, out = await _run(
        FFPROBE,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
        timeout=60,
    )
    if code != 0:
        raise FFmpegError(f"ffprobe failed ({code}): {out[-500:]}")
    data = json.loads(out)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    width = height = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            width, height = s.get("width"), s.get("height")
            break
    return MediaProbe(duration=duration, width=width, height=height, size=path.stat().st_size)


def compute_video_bitrate_kbps(target_bytes: int, duration: float, audio_kbps: int = AUDIO_KBPS_FOR_COMPRESS) -> int:
    """Total bitrate that fits target_bytes, minus audio, with a safety margin."""
    if duration <= 0:
        return 0
    total_kbps = (target_bytes * 8 / duration) / 1000 * 0.93  # container overhead margin
    return int(total_kbps - audio_kbps)


async def compress_video(src: Path, dst: Path, target_bytes: int, duration: float | None = None, max_height: int = 720) -> Path:
    """Re-encode src so the result fits under target_bytes. Raises FFmpegError if impossible."""
    if duration is None or duration <= 0:
        duration = (await probe(src)).duration
    v_kbps = compute_video_bitrate_kbps(target_bytes, duration)
    if v_kbps < MIN_VIDEO_KBPS:
        raise FFmpegError(f"target bitrate {v_kbps} kbps too low for {duration:.0f}s video")

    # Lower resolution when the bitrate budget is tight.
    if v_kbps < 600:
        max_height = min(max_height, 480)
    if v_kbps < 350:
        max_height = min(max_height, 360)

    vf = f"scale=-2:'min({max_height},ih)'"
    code, out = await _run(
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main", "-pix_fmt", "yuv420p",
        "-b:v", f"{v_kbps}k", "-maxrate", f"{int(v_kbps * 1.1)}k", "-bufsize", f"{v_kbps * 2}k",
        "-vf", vf,
        "-c:a", "aac", "-b:a", f"{AUDIO_KBPS_FOR_COMPRESS}k", "-ac", "2",
        "-movflags", "+faststart",
        str(dst),
    )
    if code != 0 or not dst.exists():
        raise FFmpegError(f"ffmpeg compress failed ({code}): {out[-800:]}")
    log.info("compressed %s -> %s (%d kbps, <=%dp)", src.name, dst.name, v_kbps, max_height)
    return dst


async def make_thumbnail(src: Path, dst: Path, size: int = 320) -> Path | None:
    """Produce a JPEG <= size px on the longest side (Telegram thumbnail rules). Works for images and videos."""
    vf = f"scale='if(gt(iw,ih),{size},-2)':'if(gt(iw,ih),-2,{size})'"
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if src.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov"):
        args += ["-ss", "1", "-frames:v", "1"]
    args += ["-vf", vf, "-q:v", "4", str(dst)]
    code, out = await _run(*args, timeout=120)
    if code != 0 or not dst.exists():
        log.warning("thumbnail failed: %s", out[-300:])
        return None
    if dst.stat().st_size > 200 * 1024:  # Telegram: thumb <= 200 KB
        code, _ = await _run(FFMPEG, "-y", "-loglevel", "error", "-i", str(dst), "-q:v", "10", str(dst.with_suffix(".small.jpg")))
        if code == 0:
            dst.with_suffix(".small.jpg").replace(dst)
    return dst
