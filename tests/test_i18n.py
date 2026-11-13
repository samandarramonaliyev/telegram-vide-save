from bot.utils.i18n import LANG_NAMES, SUPPORTED, I18n


def test_placeholder_named_lang_does_not_clash() -> None:
    i18n = I18n("uz")
    for code in SUPPORTED:
        text = i18n(code, "lang_set", lang=LANG_NAMES[code])
        assert LANG_NAMES[code] in text


def test_fallback_to_default_language() -> None:
    i18n = I18n("uz")
    assert i18n("xx", "btn_audio") == i18n("uz", "btn_audio")
    assert i18n("uz", "missing_key") == "missing_key"


def test_all_locales_have_same_keys() -> None:
    i18n = I18n("uz")
    base = set(i18n._data["uz"])
    for code in SUPPORTED:
        assert set(i18n._data[code]) == base, code
