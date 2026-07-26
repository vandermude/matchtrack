#!/usr/bin/env python3


"""
sqlite_inspect.py - inspect an SQLite database
Lists every user table in a SQLite database along with its column
definitions (type, and primary-key / not-null / default flags) and a sample
of its first rows, truncating blobs and long values so the output stays
readable.
=====================
INPUT ARGS:
db_path   path to the SQLite database file
-limit    number of sample rows to show per table (default: 10)
-debug    enable debug logging
"""


import sqlite3
import argparse
import logging
import setup_logger


DEFAULT_LIMIT = 10
MAX_VALUE_LEN = 60
LOG_DIR = '/home/vandermude/Dropbox/Projects/Suaditor/Logs'


def main():
    """
    Open the given SQLite database and log, for each user table, its column
    definitions and a sample of its first rows.
    """
    args = setup_args()
    logger = logging.getLogger(__name__)
    con = sqlite3.connect(args.db_path)
    cur = con.cursor()
    cur.execute('''SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name''')
    tables = [row[0] for row in cur.fetchall()]
    if not tables:
        logger.info(f'No tables found.')
        con.close()
        return
    for table in tables:
        cur.execute(f'PRAGMA table_info("{table}")')
        columns = cur.fetchall()
        col_names = [c[1] for c in columns]
        logger.info(f'{"=" * 60}')
        logger.info(f'Table: {table}')
        logger.info(f'{"-" * 60}')
        for cid, name, ctype, notnull, default, pk in columns:
            flags = []
            if pk:
                flags.append('PRIMARY KEY')
            if notnull:
                flags.append('NOT NULL')
            if default is not None:
                flags.append(f'DEFAULT {default}')
            flag_str = f'  [{", ".join(flags)}]' if flags else ''
            logger.info(f'  {name}: {ctype or "ANY"}{flag_str}')
        cur.execute(f'SELECT * FROM "{table}" LIMIT ?', (args.limit,))
        rows = cur.fetchall()
        logger.info(f'First {len(rows)} row(s):')
        if rows:
            logger.info(f'  {" | ".join(col_names)}')
            for row in rows:
                logger.info(f'  {" | ".join(format_value(v) for v in row)}')
        else:
            logger.info(f'  (empty table)')
    con.close()
    logger.info(f'Done')


def format_value(value):
    """Render a cell value readably, truncating blobs and long strings."""
    if value is None:
        return 'NULL'
    if isinstance(value, bytes):
        try:
            text = value.decode('utf-8')
            kind = 'blob/utf8'
        except UnicodeDecodeError:
            text = value.hex()
            kind = 'blob/hex'
        if len(text) > MAX_VALUE_LEN:
            text = text[:MAX_VALUE_LEN] + '...'
        return f'<{kind}, {len(value)} bytes: {text}>'
    text = str(value)
    if len(text) > MAX_VALUE_LEN:
        return f'{text[:MAX_VALUE_LEN]}... ({len(text)} chars)'
    return text


def setup_args():
    """Parse command-line arguments, initialize logging, and print all arg values."""
    parser = argparse.ArgumentParser(description='Inspect an SQLite database: tables, fields, and sample rows.')
    parser.add_argument('db_path', help='Path to the SQLite database file')
    parser.add_argument('-limit', type=int, default=DEFAULT_LIMIT, help='Sample rows to show per table')
    parser.add_argument('-debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()
    loglevel = 'DEBUG' if args.debug else 'INFO'
    setup_logger.setup_logger('sqlite_inspect.py', LOG_DIR, loglevel)
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, loglevel))
    console.setFormatter(logging.Formatter('%(asctime)s %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(console)
    print(f'db_path={args.db_path}')
    print(f'limit={args.limit}')
    print(f'debug={args.debug}')
    return args


if __name__ == '__main__':
    main()
