"""Internationalization helpers for the Streamlit frontend."""

from typing import Any

import streamlit as st

from frontend.i18n.translations import TRANSLATIONS


def get_lang() -> str:
    """Return the current language code ('en' or 'zh')."""
    return st.session_state.get("language", "en")


def t(key: str, **kwargs: Any) -> str:
    """Look up a translation by dot-notation key.

    Falls back to English text, then to the raw key itself.
    Supports ``str.format`` placeholders: ``t("key", count=5)``.
    """
    lang = get_lang()
    text = TRANSLATIONS.get(lang, {}).get(key)
    if text is None:
        text = TRANSLATIONS.get("en", {}).get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text
