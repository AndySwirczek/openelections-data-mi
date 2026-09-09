"""Report remaining discrepancies between the 2022 general county precinct
files and the certified county-level CENR file.

Compares per-county sums by (office, district, candidate) from
2022/counties/20221108__mi__general__<county>__precinct.csv against
2022/20221108__mi__general__county.csv (converted from the SOS CENR export by
src/cenr_2022.py) and prints, per county:

- MISSING CONTESTS   — (office, district) contests present in the CENR but with
                       no rows in the county file
- PARTIAL            — contests partially present where CENR candidates are
                       missing from the file (with CENR votes)
- NUMERIC            — candidates in both whose sums differ
- FILE-ONLY          — candidates in the file that the CENR does not carry

Pseudo-candidates (Ballots Cast, Registered Voters, Over/Under Votes, empty
candidate) and the Straight Party office are not compared; the lumped 'Write-In'
rows most files carry (the CENR instead names individual write-in candidates)
show up as FILE-ONLY rows or missing CENR write-in candidates with 0 votes.

The five counties reparsed from source in 2026 (Macomb, Livingston, Emmet,
Allegan, Dickinson — see src/macomb_2022_general.py, src/livingston_2022_proposal.py,
src/emmet_2022_general.py, src/allegan_2022_general.py, src/dickinson_2022_general.py)
match the CENR except for deliberate source-faithful residuals (±1 certified-vs-
canvass and write-in representation) documented in 2022/DISCREPANCIES_20221108.md.

Usage: .venv/bin/python src/verify_2022_general_county.py [--summary]
  --summary  print only the per-class counts and the statewide-impacting diffs
"""
import csv
import glob
import re
import sys
from collections import defaultdict

COUNTY_CSV = '2022/20221108__mi__general__county.csv'
# two county files use single-underscore names (clare, presque_isle), so the
# glob must be looser than *__precinct.csv
GLOB = '2022/counties/20221108__mi__general_*precinct.csv'

PSEUDO_OFFICES = {'Straight Party', 'Registered Voters', 'Ballots Cast',
                  'Ballots Cast Blank'}
PSEUDO_CANDS = {'', 'Write-In', 'Write-in', 'Write-Ins', 'Over Votes', 'Under Votes'}


def county_key(path):
    """county token from '…__mi__general__<token>[_]_precinct.csv'."""
    base = path.split('__general__')[1]
    return re.sub(r'_?_precinct\.csv$', '', base)


def norm_county(name):
    """Normalize either a CENR county name ('Gd. Traverse', 'St. Clair') or a
    filename token ('grand_traverse', 'st_clair') to a comparable form."""
    name = re.sub(r'\bd\.?\b', '', name.replace('_', ' ')).lower()
    name = re.sub(r'[^a-z ]', '', name)
    name = re.sub(r'^gd\s+', 'grand ', name)
    return re.sub(r'\s+', '', name)


def votes(r):
    v = (r['votes'] or '').replace(',', '').strip()
    return int(v) if v else 0


def main(summary=False):
    cenr = defaultdict(dict)
    display = {}
    for r in csv.DictReader(open(COUNTY_CSV)):
        cenr[norm_county(r['county'])][(r['office'], r['district'], r['candidate'])] = votes(r)
        display[norm_county(r['county'])] = r['county']

    n_missing = n_partial = n_numeric = n_file_only = 0
    clean = []
    for path in sorted(glob.glob(GLOB)):
        county = norm_county(county_key(path))
        csum = defaultdict(int)
        od_rows = defaultdict(int)
        for r in csv.DictReader(open(path)):
            csum[(r['office'], r['district'], r['candidate'])] += votes(r)
            od_rows[(r['office'], r['district'])] += 1
        c = cenr.get(county, {})
        cenr_od = {(o, d) for (o, d, _) in c}
        full_missing = sorted(od for od in cenr_od if od not in od_rows)
        partial = []
        numeric = []
        file_only = []
        for (o, d, cand), v in sorted(c.items()):
            if (o, d) in full_missing or cand in PSEUDO_CANDS:
                continue
            if (o, d, cand) not in csum:
                partial.append((o, d, cand, v))
            elif csum[(o, d, cand)] != v:
                numeric.append((o, d, cand, csum[(o, d, cand)], v))
        for (o, d, cand), f in sorted(csum.items()):
            if (o, d, cand) not in c and (o, d) in cenr_od and cand not in PSEUDO_CANDS:
                file_only.append((o, d, cand, f))
        if not (full_missing or partial or numeric or file_only):
            clean.append(county)
            continue
        n_missing += bool(full_missing)
        n_partial += len(partial)
        n_numeric += len(numeric)
        n_file_only += len(file_only)
        if summary:
            continue
        print(f'== {display.get(county, county)}')
        if full_missing:
            print('   MISSING CONTESTS: ' +
                  '; '.join(f'{o}[{d}]' for o, d in full_missing))
        for o, d, cand, v in partial:
            print(f'   PARTIAL {o}[{d}]: {cand} (cenr {v})')
        for o, d, cand, f, v in numeric:
            print(f'   NUMERIC {o}[{d}] {cand}: file {f} cenr {v} ({f - v:+d})')
        for o, d, cand, f in file_only:
            print(f'   FILE-ONLY {o}[{d}] {cand}: file {f}')

    n_counties = len(glob.glob(GLOB))
    print(f'\n{clean and len(clean) or "no"} counties with zero candidate-level diffs: '
          f'{", ".join(clean) if clean else "-"}')
    print(f'counties with missing contests: {n_missing} | '
          f'missing candidates: {n_partial} | numeric diffs: {n_numeric} | '
          f'file-only candidates: {n_file_only} (of {n_counties} county files)')


if __name__ == '__main__':
    main(summary='--summary' in sys.argv)