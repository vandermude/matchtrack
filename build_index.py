#!/usr/bin/env python3


"""
build_index.py - build a local SQLite song-matching index from MusicBrainz
Loads MusicBrainz's canonical_musicbrainz_data.csv into a local SQLite
database for free-text song matching, without ever calling the MusicBrainz
API. Download the canonical derived dump from
https://metabrainz.org/datasets/derived-dumps#canonical and extract the CSV
(~23M rows). This script streams the file rather than loading it into memory,
building a plain table for exact combined_lookup lookups plus an FTS5 table
and a trigram table over space-preserving normalized text for fuzzy retrieval.
=====================
INPUT ARGS:
csv_path   path to canonical_musicbrainz_data.csv (the canonical dump)
db_path    path to the SQLite index file to create
-debug     enable debug logging
"""


import csv
import sqlite3
import time
import argparse
import logging
import setup_logger
from normalize import make_searchable_text


BATCH_SIZE = 5000
LOG_DIR = '/home/vandermude/Dropbox/Projects/Suaditor/Logs'


def main():
    """Build the local SQLite index from the canonical MusicBrainz CSV."""
    args = setup_args()
    build_index(args.csv_path, args.db_path)


def build_index(csv_path: str, db_path: str):
    """
    Stream the canonical CSV row by row into the SQLite index, inserting in
    batches of BATCH_SIZE and logging progress every 100k rows, then optimize
    the FTS index.
    """
    logger = logging.getLogger(__name__)
    con = sqlite3.connect(db_path)
    create_schema(con)
    batch = []
    row_count = 0
    start = time.time()
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                continue  # header row
            artist_credit_name = row[3]
            release_mbid = row[4]
            release_name = row[5]
            recording_mbid = row[6]
            recording_name = row[7]
            combined_lookup = row[8]
            try:
                score = int(row[9])
            except (IndexError, ValueError):
                score = 0
            searchable_text = make_searchable_text(artist_credit_name, recording_name)
            batch.append((
                i,
                artist_credit_name,
                row[2],  # artist_mbids
                release_mbid,
                release_name,
                recording_mbid,
                recording_name,
                combined_lookup,
                score,
                searchable_text,
            ))
            if len(batch) >= BATCH_SIZE:
                insert_batch(con, batch)
                con.commit()
                row_count += len(batch)
                batch = []
                if row_count % 100000 == 0:
                    elapsed = time.time() - start
                    logger.info(f'{row_count:,} rows indexed ({elapsed:.1f}s)')
    if batch:
        insert_batch(con, batch)
        con.commit()
        row_count += len(batch)
    con.execute('INSERT INTO recordings_fts(recordings_fts) VALUES(\'optimize\')')
    con.commit()
    con.close()
    elapsed = time.time() - start
    logger.info(f'Done. {row_count:,} rows indexed in {elapsed:.1f}s -> {db_path}')


def create_schema(con: sqlite3.Connection):
    """Drop and recreate the recordings table plus its FTS5 and trigram indexes."""
    con.executescript('''
        DROP TABLE IF EXISTS recordings;
        CREATE TABLE recordings (
            rowid               INTEGER PRIMARY KEY,
            artist_credit_name  TEXT,
            artist_mbids        TEXT,
            release_mbid        TEXT,
            release_name        TEXT,
            recording_mbid      TEXT,
            recording_name      TEXT,
            combined_lookup     TEXT,
            score               INTEGER
        );
        CREATE INDEX idx_combined_lookup ON recordings(combined_lookup);
        DROP TABLE IF EXISTS recordings_fts;
        CREATE VIRTUAL TABLE recordings_fts USING fts5(
            searchable_text
        );
        DROP TABLE IF EXISTS recordings_trigram;
        CREATE VIRTUAL TABLE recordings_trigram USING fts5(
            searchable_text,
            tokenize='trigram'
        );
    ''')


def insert_batch(con: sqlite3.Connection, batch: list):
    """Insert one batch of rows into the recordings, FTS, and trigram tables."""
    con.executemany('''
        INSERT INTO recordings (
            rowid, artist_credit_name, artist_mbids, release_mbid,
            release_name, recording_mbid, recording_name,
            combined_lookup, score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', [row[:8] + (row[8],) for row in batch])
    con.executemany('''
        INSERT INTO recordings_fts (rowid, searchable_text) VALUES (?, ?)
    ''', [(row[0], row[9]) for row in batch])
    con.executemany('''
        INSERT INTO recordings_trigram (rowid, searchable_text) VALUES (?, ?)
    ''', [(row[0], row[9]) for row in batch])


def setup_args():
    """Parse command-line arguments, initialize logging, and print all arg values."""
    parser = argparse.ArgumentParser(description='Build a local SQLite index from the canonical MusicBrainz CSV.')
    parser.add_argument('csv_path', help='Path to canonical_musicbrainz_data.csv')
    parser.add_argument('db_path', help='Path to the SQLite index to create')
    parser.add_argument('-debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()
    loglevel = 'DEBUG' if args.debug else 'INFO'
    setup_logger.setup_logger('build_index.py', LOG_DIR, loglevel)
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, loglevel))
    console.setFormatter(logging.Formatter('%(asctime)s %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(console)
    print(f'csv_path={args.csv_path}')
    print(f'db_path={args.db_path}')
    print(f'debug={args.debug}')
    return args


if __name__ == '__main__':
    main()
