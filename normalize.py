"""
normalize.py - text normalization helpers for song matching
Two helpers with different jobs. make_combined_lookup() exactly replicates
MusicBrainz's own combined_lookup key (artist + title concatenated with no
separator, all non-word characters stripped, lowercased, transliterated to
ASCII) for fast exact matching against canonical_musicbrainz_data.csv.
make_searchable_text() instead preserves word boundaries, so its output can
be tokenized by FTS5 for token-level fuzzy retrieval.
=====================
INPUT ARGS:
(none - this module provides helper functions, it is not run directly)
"""


import re
from unidecode import unidecode


_NON_WORD_PATTERN = re.compile(r'[^\w]+')
_NON_WORD_KEEP_SPACE_PATTERN = re.compile(r'[^\w\s]')
_WHITESPACE_PATTERN = re.compile(r'\s+')


def make_combined_lookup(artist_credit_name: str, recording_name: str) -> str:
    """
    Exact replica of MusicBrainz's combined_lookup key. No separator
    between artist and title, all whitespace/punctuation removed.
    """
    raw = (artist_credit_name or '') + (recording_name or '')
    stripped_lowered = _NON_WORD_PATTERN.sub('', raw).lower()
    return unidecode(stripped_lowered)


def make_searchable_text(artist_credit_name: str, recording_name: str) -> str:
    """
    Word-boundary-preserving normalization for FTS5 indexing and fuzzy
    re-ranking. Keeps single spaces between words.
    """
    raw = f'{recording_name or ""} {artist_credit_name or ""}'
    ascii_text = unidecode(raw)
    no_punct = _NON_WORD_KEEP_SPACE_PATTERN.sub('', ascii_text)
    collapsed = _WHITESPACE_PATTERN.sub(' ', no_punct).strip()
    return collapsed.lower()
