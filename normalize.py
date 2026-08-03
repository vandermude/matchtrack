"""
normalize.py - text normalization helpers for song matching
Two helpers with different jobs. make_combined_lookup() exactly replicates
MusicBrainz's own combined_lookup key (artist + title concatenated with no
separator, all non-word characters stripped, lowercased, transliterated to
ASCII) for fast exact matching against canonical_musicbrainz_data.csv.
make_searchable_text() instead preserves word boundaries, so its output can
be tokenized by FTS5 for token-level fuzzy retrieval. share_token() builds on
that to answer whether two fields have any word in common, which is how a
match is checked for the title actually surviving into it.
=====================
INPUT ARGS:
(none - this module provides helper functions, it is not run directly)
"""


import re
from unidecode import unidecode
from rapidfuzz import fuzz


_NON_WORD_PATTERN = re.compile(r'[^\w]+')
_NON_WORD_KEEP_SPACE_PATTERN = re.compile(r'[^\w\s]')
_WHITESPACE_PATTERN = re.compile(r'\s+')
TOKEN_MATCH_THRESHOLD = 85
MIN_TOKEN_LENGTH = 3


def share_token(left: str, right: str, threshold: int = TOKEN_MATCH_THRESHOLD) -> bool:
    """
    True when any word of left corresponds to any word of right, tolerating
    small spelling differences so that a repaired misspelling ('Nympalidae'
    for 'Nymphalidae') still counts as corresponding - that is the fuzzy
    matcher doing its job, not a false positive. Exact token equality is
    tested first since it is the common case and costs nothing.
    """
    left_tokens = significant_tokens(left)
    right_tokens = significant_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens & right_tokens:
        return True
    return any(fuzz.ratio(l, r) >= threshold for l in left_tokens for r in right_tokens)


def significant_tokens(text: str) -> set:
    """
    Tokens worth comparing on. Words shorter than MIN_TOKEN_LENGTH are dropped,
    because a title like 'Piano Concerto No. 1' otherwise corresponds to
    'Walzer, op. 39, no. 15' on the strength of 'no' alone. Titles made up
    entirely of short words ('Go', '1979') keep all their tokens, since for
    those the short words are all the evidence there is.
    """
    tokens = tokenize(text)
    long_tokens = {t for t in tokens if len(t) >= MIN_TOKEN_LENGTH}
    return long_tokens or tokens


def tokenize(text: str) -> set:
    """
    Split a field into normalized words, seeing the text the same way
    retrieval and ranking do so the comparison stays consistent with them.
    """
    return set(make_searchable_text('', text or '').split())


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
