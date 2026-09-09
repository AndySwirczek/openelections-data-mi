"""Parse Macomb County's 2022 primary Civera-DB contest CSVs into a per-county
precinct CSV, verified against the certified county-level CENR.

Source: 'openelections-sources-mi/2022/primary/Macomb County Aug 2022 Primary
Precinct CSVs/' — one CSV per contest from the Macomb County Elections
Database (macombmi.electionstats.com). Contest files have a 'Precinct' header
row whose columns are '<Candidate>\\n<Party>' (plus 'Total Votes Cast',
'Overvotes/Undervotes' and 'Total Ballots Cast' columns, skipped); 346
precinct rows and a Totals row. Single-precinct files (header starting
'Precinct <name>', e.g. precinct-delegate contests with 'No Nomination') are
local and skipped.

Contest identification: each candidate column is matched by name + party
directly against the CENR county rows (all 97 Macomb candidates are
distinctive); columns that match nothing are skipped (write-in columns are
kept as lumped 'Write-In' rows).

Usage: .venv/bin/python src/primary_2022_macomb.py [--apply]
"""
import csv
import os
import re
import sys
from collections import defaultdict

from primary_2022_common import CENR, one_space, verify, write

SOURCE_DIR = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
              "Macomb County Aug 2022 Primary Precinct CSVs")
PARTY = {'Democratic': 'DEM', 'Republican': 'REP', 'Libertarian': 'LIB',
         'Green': 'GRN', 'Independent': ''}
SKIP_COLS = {'Total Votes Cast', 'Overvotes/Undervotes', 'Total Ballots Cast'}

# source spellings -> CENR (certified) names
CAND_FIX = {'Marvin Cotton Jr.': 'Marvin Cotton Jr'}

# Qualified write-ins the CENR certifies but the Civera DB does not carry at
# all (no write-in columns anywhere in the export): Governor REP James Elmer
# Craig 5,483, Elizabeth Ann Adkisson 0, Justin Paul Blackburn 0.
KNOWN_SOURCE_GAPS = {
    ('Governor', '', 'REP', 'James Elmer Craig'),
    ('Governor', '', 'REP', 'Elizabeth Ann Adkisson'),
    ('Governor', '', 'REP', 'Justin Paul Blackburn'),
}

# county -> (office, district, party, cand) -> votes, restricted to Macomb,
# keyed also by (candidate, party) for column matching
BY_CAND = {}
for (office, district, party, cand), votes in CENR['Macomb'].items():
    BY_CAND[(cand, party)] = (office, district, party)


def parse():
    rows = []
    skipped = defaultdict(int)
    write_ins = defaultdict(int)
    lumped = defaultdict(int)
    unmatched = defaultdict(int)
    for fname in sorted(os.listdir(SOURCE_DIR)):
        if not fname.endswith('.csv'):
            continue
        records = list(csv.reader(open(f'{SOURCE_DIR}/{fname}')))
        if not records or not records[0]:
            continue
        header = records[0]
        if header[0] != 'Precinct':
            # single-precinct local contest (header 'Precinct <place> ...')
            skipped[one_space(header[0])] += 1
            continue
        # match columns first so a write-in column can inherit the contest
        # key identified from its siblings in the same file
        cols = []
        contest_key = None
        for ci, col in enumerate(header[1:], start=1):
            if one_space(col) in SKIP_COLS:
                cols.append((ci, 'skip', None))
                continue
            name, _, party_name = col.partition('\n')
            cand = one_space(name)
            party = PARTY.get(party_name.strip())
            if re.fullmatch(r'write-?in', cand, re.I):
                cols.append((ci, 'write-in', None))
                continue
            key = BY_CAND.get((CAND_FIX.get(cand, cand), party))
            if key is None:
                cols.append((ci, 'unmatched', one_space(col).replace('\n', '/')))
                continue
            cols.append((ci, 'cand', (cand, party)))
            contest_key = key[:3]
        for record in records[1:]:
            if not record:
                continue
            if record[0].strip().lower() == 'totals':
                continue
            precinct = one_space(record[0])
            for ci, kind, val in cols:
                v = record[ci].replace(',', '').strip() if ci < len(record) else ''
                if not v:
                    continue
                if v == '-':
                    v = '0'  # the DB's zero placeholder in some local contests
                votes = int(v)
                if kind == 'skip':
                    continue
                if kind == 'unmatched':
                    unmatched[val] += votes
                elif kind == 'write-in':
                    if contest_key:
                        write_ins[contest_key] += votes
                        lumped[(precinct, *contest_key)] += votes
                else:
                    cand, party = val
                    cand = CAND_FIX.get(cand, cand)
                    office, district, party = BY_CAND[(cand, party)]
                    rows.append((precinct, office, district, party, cand, votes))
    for (precinct, office, district, party), votes in lumped.items():
        if votes:
            rows.append((precinct, office, district, party, 'Write-In', votes))
    n_unmatched = sum(1 for v in unmatched.values() if v)
    if n_unmatched:
        print(f'{n_unmatched} unmatched columns skipped '
              f'(county/local office candidates; {sum(unmatched.values())} votes)')
    return rows, skipped, write_ins


def main(apply=False):
    rows, skipped, write_ins = parse()
    print(f'Macomb: {len(rows)} rows; {sum(skipped.values())} single-precinct '
          f'local files skipped in {len(skipped)} contests')
    problems = []
    for p in verify('Macomb', rows, write_ins):
        if any(str(k) in p for k in KNOWN_SOURCE_GAPS):
            print(f'SOURCE GAP (known, see module docstring): {p}')
        else:
            problems.append(p)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')
    if apply:
        write('Macomb', rows)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)