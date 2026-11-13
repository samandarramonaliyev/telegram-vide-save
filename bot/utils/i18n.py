"""Tiny JSON-based i18n helper."""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
SUPPORTED = ("uz", "ru", "en")
LANG_NAMES = {"uz": "🇺🇿 O'zbekcha", "ru": "🇷🇺 Русский", "en": "🇬🇧 English"}


class I18n:
    def __init__(self, default: str = "uz") -> None:
        self.default = default if default in SUPPORTED else "uz"
        self._data: dict[str, dict[str, str]] = {}
        for lang in SUPPORTED:
            path = LOCALES_DIR / f"{lang}.json"
            try:
                self._data[lang] = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                log.warning("locale file missing: %s", path)
                self._data[lang] = {}

    def t(self, lang_code: str | None, key: str, /, **kwargs: object) -> str:
        """Positional-only lang/key so placeholders named `lang` or `key` can be passed as kwargs."""
        code = lang_code if lang_code in SUPPORTED else self.default
        text = self._data.get(code, {}).get(key) or self._data[self.default].get(key) or key
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError):
                return text
        return text

    def __call__(self, lang_code: str | None, key: str, /, **kwargs: object) -> str:
        return self.t(lang_code, key, **kwargs)
