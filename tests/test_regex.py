import pytest

from bot.utils.regex import Platform, detect_link, detect_platform, extract_url, looks_like_url, normalize_url


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc123", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=10s", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RD", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ],
)
def test_youtube_links_normalize(text: str, expected: str) -> None:
    link = detect_link(text)
    assert link is not None
    assert link.platform is Platform.YOUTUBE
    assert link.url == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=xyz", "https://www.instagram.com/reel/C1a2B3c4D5e/"),
        ("https://www.instagram.com/reels/C1a2B3c4D5e/", "https://www.instagram.com/reel/C1a2B3c4D5e/"),
        ("https://instagram.com/p/C1a2B3c4D5e", "https://www.instagram.com/p/C1a2B3c4D5e/"),
        ("https://www.instagram.com/some_user/reel/C1a2B3c4D5e/", "https://www.instagram.com/reel/C1a2B3c4D5e/"),
        ("https://www.instagram.com/tv/C1a2B3c4D5e/", "https://www.instagram.com/tv/C1a2B3c4D5e/"),
    ],
)
def test_instagram_links_normalize(text: str, expected: str) -> None:
    link = detect_link(text)
    assert link is not None
    assert link.platform is Platform.INSTAGRAM
    assert link.url == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.tiktok.com/@user.name/video/7301234567890123456?is_from_webapp=1", "https://www.tiktok.com/@user.name/video/7301234567890123456"),
        ("https://vm.tiktok.com/ZMabc123/", "https://vm.tiktok.com/ZMabc123"),
        ("https://vt.tiktok.com/ZSabc123/", "https://vt.tiktok.com/ZSabc123"),
        ("https://www.tiktok.com/t/ZTabc123/", "https://www.tiktok.com/t/ZTabc123"),
        ("https://m.tiktok.com/v/7301234567890123456.html", "https://m.tiktok.com/v/7301234567890123456"),
    ],
)
def test_tiktok_links_normalize(text: str, expected: str) -> None:
    link = detect_link(text)
    assert link is not None
    assert link.platform is Platform.TIKTOK
    assert link.url == expected


def test_link_embedded_in_text() -> None:
    link = detect_link("mana buni yuklab ber: https://youtu.be/dQw4w9WgXcQ iltimos")
    assert link is not None
    assert link.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert link.original == "https://youtu.be/dQw4w9WgXcQ"


def test_trailing_punctuation_stripped() -> None:
    assert extract_url("ko'r: https://youtu.be/dQw4w9WgXcQ.") == "https://youtu.be/dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "text",
    [
        "Shahzoda - Yetmadi",
        "hello world",
        "",
        "https://example.com/video/123",
        "https://vimeo.com/123456",
        "https://www.youtube.com/@SomeChannel",  # channel page, not media
        "https://www.instagram.com/some_user/",  # profile, not media
    ],
)
def test_non_media_or_unsupported(text: str) -> None:
    assert detect_link(text) is None


def test_looks_like_url() -> None:
    assert looks_like_url("https://vimeo.com/1")
    assert looks_like_url("check www.example.com/x")
    assert not looks_like_url("Shahzoda - Yetmadi")
    assert not looks_like_url("qo'shiq nomi")


def test_detect_platform_hosts() -> None:
    assert detect_platform("https://youtu.be/x") is Platform.YOUTUBE
    assert detect_platform("https://www.instagram.com/p/x/") is Platform.INSTAGRAM
    assert detect_platform("https://vm.tiktok.com/x") is Platform.TIKTOK
    assert detect_platform("https://notyoutube.com/x") is None
    assert detect_platform("https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ") is None


def test_normalize_rejects_garbage() -> None:
    assert normalize_url("https://www.youtube.com/feed/trending", Platform.YOUTUBE) is None
    assert normalize_url("https://www.tiktok.com/@user", Platform.TIKTOK) is None
