"""Cleaning and deduplication of raw reviews. Pure functions.

Cleaning only removes markup, links to user profiles and stray whitespace. It
never corrects spelling, removes stop words or drops words it does not know:
Hinglish must come out as it went in.
"""

import html
import re
import unicodedata

_TAG = re.compile(r"<[^>]{1,200}>")
_PROFILE_LINK = re.compile(
    r"https?://\S*(?:/store/people/|/user/|/users/|/u/|/profile/|/people/|/@)\S*", re.IGNORECASE
)
_WHITESPACE = re.compile(r"\s+")
_WORD = re.compile(r"\w+", re.UNICODE)
_NOT_WORD = re.compile(r"[^\w]+", re.UNICODE)

EMPTY = "empty"
TOO_SHORT = "too_short"
DUPLICATE = "duplicate"


def clean_text(text: str) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    text = _PROFILE_LINK.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def drop_reason(text: str, min_words: int) -> str | None:
    """Why a cleaned review is not kept, or None when it is."""
    words = _WORD.findall(text)
    if not words:
        return EMPTY
    if len(words) < min_words:
        return TOO_SHORT
    return None


def dedup_key(text: str) -> str:
    """Two reviews with the same key say the same thing, whatever the source."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _NOT_WORD.sub(" ", folded).strip()
