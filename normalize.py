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
# Function words that must not carry a match on their own. Deliberately limited
# to words with no topical content: 'down', 'back', 'love' and the like are left
# out because in a song title they are the subject, not filler.
STOPWORDS = frozenset({
    'the', 'a', 'an', 'and', 'or', 'but', 'nor', 'of', 'in', 'on', 'at', 'to',
    'for', 'with', 'from', 'by', 'as', 'into', 'onto', 'upon', 'is', 'are',
    'was', 'were', 'be', 'been', 'being', 'am', 'it', 'its', 'this', 'that',
    'these', 'those', 'i', 'you', 'he', 'she', 'we', 'they', 'me', 'him',
    'her', 'us', 'them', 'my', 'your', 'his', 'our', 'their', 'not', 'no',
    'so', 'if', 'than', 'then', 'there', 'here', 'what', 'which', 'who',
    'whom', 'when', 'where', 'how', 'why', 'all', 'any', 'some', 'such',
    'own', 'too', 'very', 'just', 'do', 'does', 'did', 'done', 'will',
    'would', 'can', 'could', 'shall', 'should', 'may', 'might', 'must',
    'have', 'has', 'had', 'one', 'two', 'part', 'pt', 'vol', 'feat', 'ft',
    'featuring', 'version', 'remix', 'edit', 'mix', 'original', 'live',
})


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
    'Walzer, op. 39, no. 15' on the strength of 'no' alone, and STOPWORDS are
    dropped for the same reason at full length - 'the' is three characters, so
    the length rule alone let 'Rep the Set' correspond to 'The Dualist'.
    Credits and edition markers ('feat', 'remix', 'live') go too, since they
    say nothing about which recording is meant. A title left with nothing keeps
    all its tokens, since for 'The One' or 'Go' the filler is the only evidence
    there is.
    """
    tokens = tokenize(text)
    strong_tokens = {t for t in tokens if len(t) >= MIN_TOKEN_LENGTH and t not in STOPWORDS}
    return strong_tokens or tokens


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
