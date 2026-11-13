"""URL detection and normalization for supported platforms."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, urlparse


class Platform(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


# Generic URL grabber (first http(s) URL in the message, or bare domain link).
_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|vm\.|vt\.|music\.)?"
    r"(?:youtube\.com|youtu\.be|instagram\.com|tiktok\.com)/[^\s<>\"']+",
    re.IGNORECASE,
)

_YT_ID = r"[A-Za-z0-9_-]{11}"
_YOUTUBE_PATTERNS = [
    re.compile(rf"(?:youtube\.com|music\.youtube\.com)/watch\?(?:.*&)?v=(?P<id>{_YT_ID})", re.I),
    re.compile(rf"youtu\.be/(?P<id>{_YT_ID})", re.I),
    re.compile(rf"youtube\.com/shorts/(?P<id>{_YT_ID})", re.I),
    re.compile(rf"youtube\.com/(?:embed|v|live)/(?P<id>{_YT_ID})", re.I),
]
_INSTAGRAM_RE = re.compile(
    r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(?P<kind>p|reel|reels|tv)/(?P<id>[A-Za-z0-9_-]+)", re.I
)
_TIKTOK_LONG_RE = re.compile(r"tiktok\.com/@(?P<user>[\w.-]+)/(?:video|photo)/(?P<id>\d+)", re.I)
_TIKTOK_SHORT_RE = re.compile(r"(?:vm|vt)\.tiktok\.com/(?P<id>[A-Za-z0-9]+)|tiktok\.com/t/(?P<id2>[A-Za-z0-9]+)", re.I)
_TIKTOK_MOBILE_RE = re.compile(r"m\.tiktok\.com/v/(?P<id>\d+)", re.I)


@dataclass(frozen=True, slots=True)
class DetectedLink:
    platform: Platform
    url: str  # normalized canonical URL
    original: str


def extract_url(text: str) -> str | None:
    """Return the first supported-looking URL in text, with https:// prefix."""
    m = _URL_RE.search(text or "")
    if not m:
        return None
    url = m.group(0).rstrip(".,;:)!?]")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _host_is(host: str, *domains: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def detect_platform(url: str) -> Platform | None:
    host = (urlparse(url).hostname or "").lower()
    if _host_is(host, "youtube.com", "youtu.be"):
        return Platform.YOUTUBE
    if _host_is(host, "instagram.com"):
        return Platform.INSTAGRAM
    if _host_is(host, "tiktok.com"):
        return Platform.TIKTOK
    return None


def normalize_url(url: str, platform: Platform) -> str | None:
    """Collapse share links / tracking params into one canonical URL per media item.

    Returns None when the URL belongs to a supported host but is not a media link
    (e.g. a channel page), so callers can treat it as unsupported.
    """
    if platform is Platform.YOUTUBE:
        for pat in _YOUTUBE_PATTERNS:
            m = pat.search(url)
            if m:
                return f"https://www.youtube.com/watch?v={m.group('id')}"
        # youtu.be/<id>?t=.. handled above; try query param as last resort
        qs = parse_qs(urlparse(url).query)
        if "v" in qs and re.fullmatch(_YT_ID, qs["v"][0]):
            return f"https://www.youtube.com/watch?v={qs['v'][0]}"
        return None

    if platform is Platform.INSTAGRAM:
        m = _INSTAGRAM_RE.search(url)
        if not m:
            return None
        kind = "reel" if m.group("kind").lower() in ("reel", "reels") else m.group("kind").lower()
        return f"https://www.instagram.com/{kind}/{m.group('id')}/"

    if platform is Platform.TIKTOK:
        m = _TIKTOK_LONG_RE.search(url)
        if m:
            return f"https://www.tiktok.com/@{m.group('user')}/video/{m.group('id')}"
        m = _TIKTOK_MOBILE_RE.search(url)
        if m:
            return f"https://m.tiktok.com/v/{m.group('id')}"
        m = _TIKTOK_SHORT_RE.search(url)
        if m:
            code = m.group("id") or m.group("id2")
            # short links redirect; yt-dlp resolves them. Keep host for cache key.
            host = "vm.tiktok.com" if "vm." in url.lower() else ("vt.tiktok.com" if "vt." in url.lower() else "www.tiktok.com/t")
            return f"https://{host}/{code}"
        return None

    return None


def detect_link(text: str) -> DetectedLink | None:
    """Full pipeline: find URL in text -> platform -> canonical URL."""
    url = extract_url(text)
    if not url:
        return None
    platform = detect_platform(url)
    if platform is None:
        return None
    normalized = normalize_url(url, platform)
    if normalized is None:
        return None
    return DetectedLink(platform=platform, url=normalized, original=url)


def looks_like_url(text: str) -> bool:
    """True if the text contains *any* http(s) URL (used to reject unsupported sites)."""
    return bool(re.search(r"https?://\S+|(?:^|\s)(?:www\.)?[a-z0-9-]+\.[a-z]{2,}/\S*", text or "", re.I))
