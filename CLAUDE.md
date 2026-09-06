# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Pre-processed Michigan election results (1998–2024) for OpenElections. It is a **data repo, not an application**: the deliverables are CSVs organized by year, and the Python in `src/` consists of small parsing scripts used to produce them. Raw source files (PDFs, XML, tab-delimited SOVC exports) are **not** in this repo — they live in the sibling repo `openelections-sources-mi` (locally `/Users/dwillis/code/openelections-sources-mi`, organized as `<year>/<primary|general>/<County> ...pdf`). Parser scripts hardcode paths into that repo.

## Environment

uv-managed, Python 3.12 (see `pyproject.toml`, `.python-version`): `uv sync`, then run scripts with `.venv/bin/python`. Older scripts (`clarity_parser.py`, `midland.py`, `macomb.py`) were written against a legacy Pipfile (3.9, tabula-py, bs4, clarify) that is kept but not the current toolchain.

## Data file conventions

Filenames: `{date}__mi__{election}__{county}__precinct.csv` (double underscores; election token is `general`, `primary`, `special__general`, or `primary__president`; county lowercase with underscores). Per-county precinct CSVs go in `<year>/counties/` (e.g. `2024/counties/20241105__mi__general__grand_traverse__precinct.csv`) and are then merged into the consolidated statewide file at the year root by `statewide_generator.py`.

Standard per-county header: `county,precinct,office,district,party,candidate,votes`
- `county` is Title Case with spaces (`Grand Traverse`) and must match the filename's county token.
- `party` is blank for general-election candidates; in primary files it's a code (`DEM`, `REP`, `LIB`, `UST`, `GRN`, ...).
- `district` is blank except for `U.S. House`, `State Senate`, `State House` (integer).
- Pseudo-offices (`Registered Voters`, `Ballots Cast`, `Ballots Cast Blank`) have blank `candidate` and a count in `votes`.
- Precinct `9999` is a statistical pseudo-precinct (negative votes allowed there in verification).

Breakdown columns (`election_day`, `absentee`, `early_voting`, ...) vary by year and by county; `statewide_generator.py`'s `COUNTY_COLUMN_MAP` normalizes them. Note the **generated statewide files put `candidate` before `party`**, unlike per-county files.

## Validation (the "tests")

There is no in-repo test suite. CI checks out `openelections/openelections-data-tests` and runs four tests: `duplicate_entries`, `file_format`, `missing_values`, `vote_breakdown_totals`. Run them locally from the sibling clone:

```
python3 /Users/dwillis/code/openelections-data-tests/run_tests.py file_format /Users/dwillis/code/openelections-data-mi
python3 /Users/dwillis/code/openelections-data-tests/run_tests.py duplicate_entries /Users/dwillis/code/openelections-data-mi --files 2024/counties/20240806__mi__primary__kalkaska__precinct.csv
```

`src/verifier.py` is a lighter pre-commit check on specific CSVs: `python src/verifier.py <csv...> [--mutePrimaryPartiesError --muteMissingPartyError --muteXForDistrictError --singleError]`. It validates columns, county-vs-filename match, office whitelist (`validOffices` — keep in sync with `OFFICE_WHITELIST` in `statewide_generator.py`), district presence for legislative offices, non-negative integer votes, and row uniqueness.

## Key scripts

- **`statewide_generator.py`** (repo root) — the only parameterized, reusable script. Usage: `python statewide_generator.py <year> <YYYYMMDD>`. Globs `<year>/counties/<election>*precinct.csv`, filters offices to `OFFICE_WHITELIST`, normalizes breakdown columns via `COUNTY_COLUMN_MAP`, writes the statewide `<year>/<election>__mi__general__precinct.csv`. Re-run after adding/changing county files.
- **`src/election_reporting.py`** — current workhorse for tabula-exported TSVs, but **not parameterized**: module-level `county` and `OFFICES` constants are edited in place per county before each run (reads from `~/Downloads/`). Pattern: configure → run → commit CSV → reconfigure for next county.
- **`src/parser.py`** — parses SOS tab files (`{year}name.txt`, `{year}vote.txt`, etc.) into statewide precinct CSVs. CLI: `--stateDirPath <dir> --date YYYYMMDD --outDirPath <dir>`.
- **`src/clarity_parser.py`**, `src/macomb.py`, `src/washtenaw.py`, `src/clinton_style.py`, `src/midland.py` — one-off parsers for specific counties/elections (Clarity XML downloads, BeautifulSoup scraping, tabula PDFs). Copy/adapt rather than expecting them to generalize; they are not parameterized.

## Workflow for adding a county

Work happens on feature branches, one commit per county (see `git log`: "Add <County> 2024 primary precinct results"), with PR merges. Good practice from recent commits:
- Reference the source file path from `openelections-sources-mi` in the commit message.
- Verify parsed per-precinct sums against the source's own printed county/contest totals before committing.
- Fix CI failures (`duplicate_entries`, `vote_breakdown_totals`) in follow-up commits when present in older data.
- `src/` also accumulates uncommitted working artifacts (`detail.xml`, stray output CSVs) that are never committed — don't commit them.