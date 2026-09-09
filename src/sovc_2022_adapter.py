"""Filter a sovc_pdf_parser-produced CSV down to the CENR-carried 2022
primary contests, verify it against the certified county-level CENR, and
write the standard per-county precinct CSV.

sovc_pdf_parser emits local offices, proposals and delegates that the 2022
primary CENR doesn't carry (its own verification is the PDF's printed
totals); this adapter keeps Governor / U.S. House / State Senate / State
House, drops pseudo-candidate rows (Ballots Cast etc.), and lumps the
parser's per-precinct 'Write-In' rows into write_ins for verify()'s
qualified-write-in NOTEs.

Usage: .venv/bin/python src/sovc_2022_adapter.py <parsed.csv> <County> [--apply]
"""
import ast
import csv
import re
import sys
from collections import defaultdict

from primary_2022_common import verify, write

KEEP = {'Governor', 'U.S. House', 'State Senate', 'State House'}

# candidates whose county SOVC total differs from the certified CENR for a
# known reason; reported but not blocking (no per-precinct source for the
# difference exists)
TOLERATE = {
    # Lenawee HD34 REP was recounted after this SOVC printed (the CENR's
    # office description carries "(RECOUNT)"): Moore +5, Rank +4 with no
    # per-precinct recount breakdown published
    ('Lenawee', ('State House', '34', 'REP', 'Julie Moore')),
    ('Lenawee', ('State House', '34', 'REP', 'Ryan Rank')),
    ('Lenawee', ('State House', '34', 'REP', 'Dale W. Zorn')),
    # the CENR spells it without the comma
    ('Lenawee', ('State House', '31', 'DEM', 'Glenn R. Morrison Jr')),
    # Montcalm's only per-precinct export is the unofficial 8/3 snapshot and
    # every 'Unresolved write-in votes' row in it prints 0; these certified
    # write-ins were resolved at canvass afterwards, with no per-precinct
    # source for them
    ('Montcalm', ('Governor', '', 'REP', 'James Elmer Craig')),
    ('Montcalm', ('U.S. House', '2', 'REP', 'Jericho Joel Gonzales')),
}
NAME_FIX = {'Glenn R. Morrison, Jr.': 'Glenn R. Morrison Jr'}

# court judgeships print as 'Judge of the 2A District Court ...'; the CENR
# carries them as office 'District Court Judge' with lettered district.
# Muskegon 2022 prints the court name first ('Judge of Circuit Court 14th
# Circuit ...').
JUDGE = re.compile(r'^Judge of (?:the )?(\S+) (District|Circuit) Court')
JUDGE_COURT_FIRST = re.compile(
    r'^Judge of (?:the )?(District|Circuit) Court (\d+)')


def adapt(path, county, apply=False):
    rows = []
    write_ins = defaultdict(int)
    for r in csv.DictReader(open(path)):
        office, district = r['office'], r['district']
        if r['county'] != county:
            continue
        jm = JUDGE.match(office)
        if jm:
            office = f'{jm.group(2)} Court Judge'
            district = jm.group(1)
        else:
            jm = JUDGE_COURT_FIRST.match(office)
            if jm:
                office = f'{jm.group(1)} Court Judge'
                district = jm.group(2)
        if jm is None and office not in KEEP:
            continue
        if r['candidate'] in ('Ballots Cast', 'Ballots Cast Blank',
                              'Registered Voters', 'Total Votes',
                              'Total Votes Cast'):
            continue
        # some reports print curly apostrophes (Sherry O’Donnell)
        cand = r['candidate'].replace('’', "'")
        cand = NAME_FIX.get(cand, cand)
        votes = int(r['votes'] or 0)
        if cand == 'Write-In':
            write_ins[(office, district, r['party'])] += votes
        rows.append((r['precinct'], office, district, r['party'],
                     cand, votes))
    blocking = []
    for p in verify(county, rows, write_ins):
        # verify()'s problem lines carry the (office, district, party,
        # candidate) key either as '<county>: (...): ...' (total mismatches)
        # or after 'CENR candidate missing from source: ' (absentees)
        m = (re.match(r"^%s: \((.*)\): " % re.escape(county), p)
             or re.match(r"^%s: CENR candidate missing from source: \((.*)\) "
                         % re.escape(county), p))
        if m:
            key = ast.literal_eval('(' + m.group(1) + ',)')
            if (county, key) in TOLERATE:
                print('TOLERATED (known source gap):', p)
                continue
        blocking.append(p)
    for p in blocking:
        print('PROBLEM:', p)
    print(f'{county}: {len(rows)} rows; {len(blocking)} problems')
    if not blocking and apply:
        write(county, rows)
    return blocking


if __name__ == '__main__':
    adapt(sys.argv[1], sys.argv[2], apply='--apply' in sys.argv)