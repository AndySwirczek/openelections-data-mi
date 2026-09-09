"""Parse Kalamazoo County's 2022 primary XLSX into a per-county precinct CSV,
verified against the certified county-level CENR.

Source (openelections-sources-mi/2022/primary/): 'Kalamazoo County Aug 2022
Primary Precinct Results.xlsx', sheet 'Precinct Results' — one row per
(precinct, contest, ballot option): Precinct / Office Name / Contest ID /
Ballot Name / Choice ID / Party / Total. Office names carry a party suffix
with sloppy dashes ('Governor of Michigan - Democratic Party',
'Governor of Michigan - - Republican Party', en-dash variants); the two
nonpartisan judgeships have none ('Judge Of 9th Circuit Court',
'Judge Of 8th District Court', mapped to the CENR's 'Circuit Court Judge 9' /
'District Court Judge 8'). 'Ballots Cast'/'Over Votes'/'Under Votes' rows are
skipped (the repo's files carry no pseudo-office rows); 'Write-in' options are
kept as merged per-precinct 'Write-In' rows. Local contests are skipped.

See src/primary_2022_common.py for the verification rules.

Usage: .venv/bin/python src/primary_2022_kalamazoo.py [--apply]
"""
import re
import sys
from collections import defaultdict

import openpyxl

from primary_2022_common import one_space, verify, write

SOURCE = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
          "Kalamazoo County Aug 2022 Primary Precinct Results.xlsx")
PSEUDO = {'Ballots Cast', 'Over Votes', 'Under Votes'}
PARTY = {'Democratic': 'DEM', 'Republican': 'REP'}

OFFICES = [
    (re.compile(r'Governor of Michigan$'), lambda m: ('Governor', '')),
    (re.compile(r'Representative in Congress (\d+)(?:st|nd|rd|th)? District$'),
     lambda m: ('U.S. House', m.group(1))),
    (re.compile(r'State Senator (\d+)(?:st|nd|rd|th)? District$'),
     lambda m: ('State Senate', m.group(1))),
    (re.compile(r'Representative in State Legislature (\d+)(?:st|nd|rd|th)? District$'),
     lambda m: ('State House', m.group(1))),
    (re.compile(r'Judge Of (\d+)(?:st|nd|rd|th)? Circuit Court$'),
     lambda m: ('Circuit Court Judge', m.group(1))),
    (re.compile(r'Judge Of (\d+)(?:st|nd|rd|th)? District Court$'),
     lambda m: ('District Court Judge', m.group(1))),
]

# CENR candidates where the county XLSX (all 106 precincts present, column sums
# internally consistent) disagrees slightly with the state-certified CENR —
# canvass-vs-certified residuals, kept source-faithful as in the 2022 general
# files (see 2022/DISCREPANCIES_20221108.md for the same pattern)
ACCEPTED_RESIDUALS = {
    ('Circuit Court Judge', '9', '', 'Ken Barnard'): (15294, 15295),
    ('District Court Judge', '8', '', 'Becket Jones'): (13090, 13094),
    ('State House', '42', 'REP', 'Matt Hall'): (7165, 7166),
    ('State Senate', '19', 'REP', 'Tamara Mitchell'): (19830, 19831),
    ('U.S. House', '5', 'DEM', 'Bart Goldberg'): (1175, 1189),
    ('U.S. House', '5', 'REP', 'Tim Walberg'): (1730, 1731),
}


def classify(office_name):
    """(office, district, party-code) for a CENR-carried contest, else None."""
    s = one_space(office_name).replace('–', '-').replace('—', '-')
    m = re.search(r'-\s*(Democratic|Republican)\s+Party\s*$', s)
    party = PARTY[m.group(1)] if m else ''  # judges carry no party
    body = re.sub(r'-\s*(Democratic|Republican)\s+Party\s*$', '', s).strip()
    body = re.sub(r'-+\s*$', '', body).strip()
    for pattern, fn in OFFICES:
        hit = pattern.match(body)
        if hit:
            office, district = fn(hit)
            return office, district, party
    return None


def parse():
    """[(precinct, office, district, party, candidate, votes)] plus skipped
    contest names and per-contest write-in vote totals."""
    wb = openpyxl.load_workbook(SOURCE, read_only=True)
    ws = wb['Precinct Results']
    rows = []
    skipped = defaultdict(int)
    write_ins = defaultdict(int)
    lumped = defaultdict(int)
    for row in ws.iter_rows(min_row=2, values_only=True):
        precinct, office_name, _cid, ballot_name, _choice, party_name, total = row[:7]
        if not precinct or not office_name:
            continue
        got = classify(office_name)
        if not got:
            skipped[one_space(office_name)] += 1
            continue
        office, district, party = got
        cand = one_space(str(ballot_name))
        votes = int(str(total or 0))
        if cand in PSEUDO:
            continue
        if cand == 'Write-in':
            # lumped write-in votes; one merged 'Write-In' row per precinct
            write_ins[(office, district, party)] += votes
            lumped[(one_space(str(precinct)), office, district, party)] += votes
        else:
            rows.append((one_space(str(precinct)), office, district, party, cand, votes))
    for (precinct, office, district, party), votes in lumped.items():
        if votes:
            rows.append((precinct, office, district, party, 'Write-In', votes))
    return rows, skipped, write_ins


def main(apply=False):
    rows, skipped, write_ins = parse()
    print(f'Kalamazoo: {len(rows)} rows; '
          f'{sum(skipped.values())} local rows skipped in {len(skipped)} contests')
    problems = []
    for p in verify('Kalamazoo', rows, write_ins):
        residual = next((k for k, (got, _want) in ACCEPTED_RESIDUALS.items()
                         if str(k) in p and f'parsed {got} !=' in p), None)
        if residual:
            print(f'ACCEPTED residual (source-faithful): {p}')
        else:
            problems.append(p)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')
    if apply:
        write('Kalamazoo', rows)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)