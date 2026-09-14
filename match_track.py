#!/usr/bin/env python3


"""
match_track.py - resolve free-text song input to a MusicBrainz recording MBID
Resolves a title (optionally plus an artist) to a MusicBrainz recording MBID
using a local SQLite index built by build_index.py, with no MusicBrainz API
calls. Two-tier strategy: first an exact path that normalizes the input into
MusicBrainz's own combined_lookup key and checks for a direct hit; then a
fuzzy fallback that uses FTS5 to retrieve a word-overlap shortlist and
re-ranks it with rapidfuzz string similarity to handle typos, partial
titles, and word reordering. A fuzzy candidate is only accepted if its
recording name shares a word with the requested title, since similarity alone
weighs artist and title together and will otherwise settle for an unrelated
recording by the right artist.
=====================
INPUT ARGS:
db_path             path to the SQLite index built by build_index.py
title               song title to resolve
-artist             optional artist hint to improve matching
-min_similarity     minimum fuzzy similarity (0-100) to accept a fuzzy-path match
-allow_title_drift  accept a fuzzy match whose title shares no word with the requested title
-require_artist     also require the matched artist credit to share a word with the requested artist
-title_coverage     fraction of the requested title that must survive into a fuzzy match
-debug              enable debug logging
"""


import sys
import sqlite3
import argparse
import logging
import setup_logger
from project_config import LOG_DIR
from dataclasses import dataclass
from rapidfuzz import fuzz
from normalize import make_combined_lookup, make_searchable_text, share_token, covers, TITLE_COVERAGE


CANDIDATE_LIMIT = 50


@dataclass
class Match:
    recording_mbid: str
    recording_name: str
    artist_credit_name: str
    release_name: str
    release_mbid: str
    canonical_score: int
    similarity: float
    match_type: str  # 'exact' or 'fuzzy'
    artist_mbids: str = ''  # raw artist MBID list as stored in the index


def main():
    """
    Resolve the requested title (with optional artist hint) to a MusicBrainz
    recording via the local index, log the best match, and exit non-zero if
    nothing clears the similarity threshold.
    """
    args = setup_args()
    logger = logging.getLogger(__name__)
    logger.info(f'Starting MusicBrainz match track')
    best = resolve(args.db_path, args.title, args.artist, args.min_similarity, not args.allow_title_drift, args.require_artist, args.title_coverage)
    if best is None:
        logger.info(f'No confident match found.')
        sys.exit(2)
    logger.info(f'Match type:      {best.match_type}')
    logger.info(f'Recording MBID:  {best.recording_mbid}')
    logger.info(f'Title:           {best.recording_name}')
    logger.info(f'Artist:          {best.artist_credit_name}')
    logger.info(f'Release:         {best.release_name}')
    logger.info(f'Release MBID:    {best.release_mbid}')
    logger.info(f'Similarity:      {best.similarity:.1f}')
    logger.info(f'Canonical score: {best.canonical_score}')


def resolve(db_path: str, title: str, artist: str = None, min_similarity: float = 60.0, require_title: bool = True, require_artist: bool = False, title_coverage: float = TITLE_COVERAGE):
    """
    Resolve a title (with optional artist) to the best Match: try the exact
    combined_lookup path first, then the fuzzy FTS + rapidfuzz path. Return
    None if there are no candidates or nothing acceptable clears
    min_similarity.
    """
    con = sqlite3.connect(db_path)
    try:
        exact = try_exact_match(con, artist, title)
        if exact is not None:
            return exact
        searchable_query = make_searchable_text(artist or '', title)
        candidates = fetch_fuzzy_candidates(con, searchable_query)
        if not candidates:
            return None
        ranked = rerank_fuzzy(candidates, searchable_query)
        return select_best(ranked, title, min_similarity, require_title, artist, require_artist, title_coverage)
    finally:
        con.close()


def select_best(ranked: list, title: str, min_similarity: float, require_title: bool = True, artist: str = None, require_artist: bool = False, title_coverage: float = TITLE_COVERAGE):
    """
    Pick the best acceptable match from the ranked candidates. Similarity on
    its own is a weak filter, because the scorer weighs artist and title
    together: a candidate whose name has nothing to do with the requested
    title still scores in the mid-80s when the artist matches, which is how an
    unrelated recording by the right artist gets accepted. With require_title
    set, walk the ranking in order and take the highest-scoring candidate whose
    recording name actually shares a word with the requested title, so a
    correct match further down can win over a confident wrong one.
    require_artist applies the same test to the artist credit, which closes the
    opposite drift - the right title by an unrelated artist - at the cost of
    rejecting correct matches whose credit is spelled differently. It is
    skipped when the caller supplied no artist, since a test cannot be made of
    something that was never given. Return None when nothing qualifies.
    """
    for candidate in ranked:
        if candidate.similarity < min_similarity:
            break
        if require_title and not covers(title, candidate.recording_name, title_coverage):
            continue
        if require_artist and artist and not share_token(artist, candidate.artist_credit_name):
            continue
        return candidate
    return None


def try_exact_match(con: sqlite3.Connection, artist: str, title: str):
    """
    Look for a direct hit on the combined_lookup key built from the input.
    Return a Match with type 'exact', or None if there is no exact key match.
    """
    lookup_key = make_combined_lookup(artist or '', title)
    if not lookup_key:
        return None
    cursor = con.execute('''
        SELECT recording_mbid, recording_name, artist_credit_name, artist_mbids,
               release_name, release_mbid, score
        FROM recordings
        WHERE combined_lookup = ?
        ORDER BY score DESC
        LIMIT 1
    ''', (lookup_key,))
    row = cursor.fetchone()
    if row is None:
        return None
    recording_mbid, recording_name, artist_credit_name, artist_mbids, release_name, release_mbid, score = row
    return Match(
        recording_mbid=recording_mbid,
        recording_name=recording_name,
        artist_credit_name=artist_credit_name,
        release_name=release_name,
        release_mbid=release_mbid,
        canonical_score=score or 0,
        similarity=100.0,
        match_type='exact',
        artist_mbids=artist_mbids or '',
    )


def fetch_fuzzy_candidates(con: sqlite3.Connection, searchable_query: str, limit: int = CANDIDATE_LIMIT):
    """
    Retrieve a candidate shortlist for fuzzy re-ranking: try FTS5 word-level
    retrieval first, fall back to trigram substring retrieval when a typo
    breaks every token, then return the candidates' detail rows.
    """
    fts_query = fts_query_from_tokens(searchable_query)
    if not fts_query:
        return []
    # Query the FTS table on its own first (mixing bm25() with a JOIN in the
    # same SELECT confuses SQLite's column resolution), then fetch details.
    fts_cursor = con.execute('''
        SELECT rowid FROM recordings_fts
        WHERE recordings_fts MATCH ?
        ORDER BY bm25(recordings_fts)
        LIMIT ?
    ''', (fts_query, limit))
    rowids = [row[0] for row in fts_cursor.fetchall()]
    if not rowids:
        # Word-level retrieval found nothing (likely a typo broke every
        # token); fall back to trigram substring retrieval.
        rowids = fetch_trigram_rowids(con, searchable_query, limit)
    if not rowids:
        return []
    return fetch_candidate_details(con, rowids)


def fts_query_from_tokens(searchable_text: str) -> str:
    """
    Build a permissive FTS5 query where any token may match, to keep recall
    high. Precision is recovered by rapidfuzz re-ranking afterward.
    """
    tokens = searchable_text.split()
    if not tokens:
        return ''
    return ' OR '.join(f'"{t}"' for t in tokens)


def fetch_trigram_rowids(con: sqlite3.Connection, searchable_query: str, limit: int) -> list:
    """
    Retrieve rowids whose text shares contiguous character windows with the
    query, using the trigram FTS index. Deduplicates and caps at limit.
    """
    substrings = make_trigram_probe_substrings(searchable_query)
    if not substrings:
        return []
    seen = set()
    rowids = []
    for substring in substrings:
        cursor = con.execute('''
            SELECT rowid FROM recordings_trigram
            WHERE recordings_trigram MATCH ?
            LIMIT ?
        ''', (substring, limit))
        for (rowid,) in cursor.fetchall():
            if rowid not in seen:
                seen.add(rowid)
                rowids.append(rowid)
        if len(rowids) >= limit:
            break
    return rowids[:limit]


def make_trigram_probe_substrings(text: str, window: int = 5) -> list:
    """
    Break the query into overlapping windows so that even if part of it is
    misspelled, other windows can still land an exact substring hit in the
    trigram index. SQLite's trigram MATCH requires at least 3 characters, so
    this only operates on words long enough to bother.
    """
    compact = text.replace(' ', '')
    if len(compact) < 3:
        return [compact] if compact else []
    return [compact[i:i + window] for i in range(0, len(compact) - 2, window - 2)]


def fetch_candidate_details(con: sqlite3.Connection, rowids: list) -> list:
    """Fetch full detail rows plus searchable_text for the given rowids."""
    placeholders = ','.join('?' for _ in rowids)
    detail_cursor = con.execute(f'''
        SELECT r.recording_mbid, r.recording_name, r.artist_credit_name, r.artist_mbids,
               r.release_name, r.release_mbid, r.score, recordings_fts.searchable_text
        FROM recordings r
        JOIN recordings_fts ON recordings_fts.rowid = r.rowid
        WHERE r.rowid IN ({placeholders})
    ''', rowids)
    return detail_cursor.fetchall()


def rerank_fuzzy(candidates: list, searchable_query: str) -> list:
    """
    Score each candidate by rapidfuzz string similarity to the query, then
    return Match objects sorted by similarity with canonical score as the
    tiebreaker among near-equal matches.
    """
    scored = []
    for (recording_mbid, recording_name, artist_credit_name, artist_mbids,
         release_name, release_mbid, canonical_score, candidate_text) in candidates:
        similarity = fuzz.WRatio(searchable_query, candidate_text or '')
        scored.append(Match(
            recording_mbid=recording_mbid,
            recording_name=recording_name,
            artist_credit_name=artist_credit_name,
            release_name=release_name,
            release_mbid=release_mbid,
            canonical_score=canonical_score or 0,
            similarity=similarity,
            match_type='fuzzy',
            artist_mbids=artist_mbids or '',
        ))
    scored.sort(key=lambda m: (m.similarity, m.canonical_score), reverse=True)
    return scored


def setup_args():
    """Parse command-line arguments, initialize logging, and print all arg values."""
    parser = argparse.ArgumentParser(description='Resolve a song title to a MusicBrainz MBID using a local index.')
    parser.add_argument('db_path', help='Path to the SQLite index built by build_index.py')
    parser.add_argument('title', help='Song title to resolve')
    parser.add_argument('-artist', default=None, help='Optional artist hint to improve matching')
    parser.add_argument('-min_similarity', type=float, default=60.0, help='Minimum fuzzy similarity (0-100) to accept a fuzzy-path match')
    parser.add_argument('-allow_title_drift', action='store_true', help='Accept a fuzzy match whose title shares no word with the requested title')
    parser.add_argument('-require_artist', action='store_true', help='Also require the matched artist credit to share a word with the requested artist')
    parser.add_argument('-title_coverage', type=float, default=TITLE_COVERAGE, help='Fraction of the requested title that must survive into a fuzzy match')
    parser.add_argument('-debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()
    loglevel = 'DEBUG' if args.debug else 'INFO'
    setup_logger.setup_logger('match_track.py', LOG_DIR, loglevel)
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, loglevel))
    console.setFormatter(logging.Formatter('%(asctime)s %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(console)
    print(f'db_path={args.db_path}')
    print(f'title={args.title}')
    print(f'artist={args.artist}')
    print(f'min_similarity={args.min_similarity}')
    print(f'allow_title_drift={args.allow_title_drift}')
    print(f'require_artist={args.require_artist}')
    print(f'title_coverage={args.title_coverage}')
    print(f'debug={args.debug}')
    return args


if __name__ == '__main__':
    main()
