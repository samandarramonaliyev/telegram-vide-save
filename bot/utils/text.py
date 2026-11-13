"""Small text helpers."""
from __future__ import annotations

from html import escape


def fmt_duration(seconds: float | int | None) -> str:
    if not seconds:
        return "--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_size(num_bytes: int | float | None) -> str:
    if not num_bytes:
        return "?"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def h(text: str | None) -> str:
    """HTML-escape for parse_mode=HTML."""
    return escape(text or "", quote=False)
