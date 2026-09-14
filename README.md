# matchtrack

Resolve free-text song input (a title, optionally with an artist) to a
**MusicBrainz recording MBID** using a *local* SQLite index — with no calls to
the MusicBrainz API at query time.

The matcher uses a two-tier strategy:

1. **Exact path** — normalize the input into MusicBrainz's own
   `combined_lookup` key and check for a direct hit. Clean, well-formed input
   resolves instantly this way.
2. **Fuzzy path (fallback)** — use SQLite FTS5 to retrieve a word-overlap
   candidate shortlist, then re-rank with `rapidfuzz` string similarity. This
   handles typos, partial titles, and word reordering. If a misspelling breaks
   every token, it falls back further to trigram substring retrieval.

## Requirements

```
pip install -r requirements.txt
```

- `rapidfuzz` — fuzzy string similarity re-ranking
- `Unidecode` — ASCII transliteration during normalization

Python 3 standard library covers everything else (`sqlite3`, `csv`, `argparse`,
`logging`, `dataclasses`).

## Setup: build the index

Download the MusicBrainz **canonical** derived dump and extract
`canonical_musicbrainz_data.csv` (~23M rows, several GB):

<https://metabrainz.org/datasets/derived-dumps#canonical>

Then build the local SQLite index (streams the CSV, so it does not load the
whole file into memory):

```
python build_index.py canonical_musicbrainz_data.csv canonical_index.sqlite
```

## Usage

### Resolve a title

```
python match_track.py canonical_index.sqlite "Ripple" -artist "Grateful Dead"
```

Exit codes: `0` = a confident match was found, `2` = no match cleared the
similarity threshold.

Options:

| Arg | Meaning |
|-----|---------|
| `db_path` | Path to the SQLite index built by `build_index.py` |
| `title` | Song title to resolve (positional) |
| `-artist` | Optional artist hint to improve matching |
| `-min_similarity` | Minimum fuzzy similarity 0–100 to accept a fuzzy match (default 60) |
| `-debug` | Enable debug logging |

### Inspect any SQLite database

```
python sqlite_inspect.py canonical_index.sqlite -limit 5
```

Lists each table's columns and a sample of rows. Handy for checking what
`build_index.py` produced.

## Files

| File | Role |
|------|------|
| `build_index.py` | Build the SQLite index from the canonical CSV |
| `match_track.py` | Resolve a title (+ optional artist) to an MBID |
| `sqlite_inspect.py` | Inspect any SQLite DB (tables, fields, sample rows) |
| `normalize.py` | Text-normalization helpers (module, not run directly) |
| `setup_logger.py` | Logging setup helper (module) |
| `model_config.py` | Declares `ANTHROPIC_MODEL` (module) |

## Notes

- The command-line tools write a timestamped `.log` file to
  `/home/vandermude/Dropbox/Projects/Suaditor/Logs/` and mirror INFO-level
  output to the console.
- `setup_logger.py` and `model_config.py` are per-codebase copies. If you change
  either, update the matching copy in `MusicSuaditor/` too (see the note in
  `CLAUDE.md`).
