#!/usr/bin/env python3


"""
match_track.py - resolve free-text song input to a MusicBrainz recording MBID
Resolves a title (optionally plus an artist) to a MusicBrainz recording MBID
using a local SQLite index built by build_index.py, with no MusicBrainz API
calls. Two-tier strategy: first an exact path that normalizes the input into
MusicBrainz's own combined_lookup key and checks for a direct hit; then a
fuzzy fallback that uses FTS5 to retrieve a word-overlap shortlist and
re-ranks it with rapidfuzz string similarity to handle typos, partial
titles, and word reordering.
=====================
INPUT ARGS:
db_path          path to the SQLite index built by build_index.py
title            song title to resolve
-artist          optional artist hint to improve matching
-min_similarity  minimum fuzzy similarity (0-100) to accept a fuzzy-path match
-debug           enable debug logging
"""


import sys
import sqlite3
import argparse
import logging
import setup_logger
from dataclasses import dataclass
from rapidfuzz import fuzz
from normalize import make_combined_lookup, make_searchable_text


CANDIDATE_LIMIT = 50
LOG_DIR = '/home/vandermude/Dropbox/Projects/Suaditor/Logs'


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


def main():
    """
    Resolve the requested title (with optional artist hint) to a MusicBrainz
    recording via the local index, log the best match, and exit non-zero if
    nothing clears the similarity threshold.
    """
    args = setup_args()
    logger = logging.getLogger(__name__)
    best = resolve(args.db_path, args.title, args.artist, args.min_similarity)
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


def resolve(db_path: str, title: str, artist: str = None, min_similarity: float = 60.0):
    """
    Resolve a title (with optional artist) to the best Match: try the exact
    combined_lookup path first, then the fuzzy FTS + rapidfuzz path. Return
    None if there are no candidates or the best fuzzy match is below
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
        if not ranked:
            return None
        best = ranked[0]
        if best.similarity < min_similarity:
            return None
        return best
    finally:
        con.close()


def try_exact_match(con: sqlite3.Connection, artist: str, title: str):
    """
    Look for a direct hit on the combined_lookup key built from the input.
    Return a Match with type 'exact', or None if there is no exact key match.
    """
    lookup_key = make_combined_lookup(artist or '', title)
    if not lookup_key:
        return None
    cursor = con.execute('''
        SELECT recording_mbid, recording_name, artist_credit_name,
               release_name, release_mbid, score
        FROM recordings
        WHERE combined_lookup = ?
        ORDER BY score DESC
        LIMIT 1
    ''', (lookup_key,))
    row = cursor.fetchone()
    if row is None:
        return None
    recording_mbid, recording_name, artist_credit_name, release_name, release_mbid, score = row
    return Match(
        recording_mbid=recording_mbid,
        recording_name=recording_name,
        artist_credit_name=artist_credit_name,
        release_name=release_name,
        release_mbid=release_mbid,
        canonical_score=score or 0,
        similarity=100.0,
        match_type='exact',
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
        SELECT r.recording_mbid, r.recording_name, r.artist_credit_name,
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
    for (recording_mbid, recording_name, artist_credit_name,
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
    print(f'debug={args.debug}')
    return args


if __name__ == '__main__':
    main()
