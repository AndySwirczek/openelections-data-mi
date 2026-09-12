"""Convert the March 2020 presidential primary SOS CENR by-county text
file to a county-level CSV.

Source: /Users/dwillis/OpenElections/2020PRESIDENTIAL_PRIMARY_MI_CENR_BY_COUNTY.txt
Output: 2020/20200310__mi__primary__president__county.csv

Same 18-col layout (and 0-based indices) as the August primary export
converted by cenr_2020_primary.py.  The file prints every county's
President votes twice: once in the statewide contest ('President of
the United States 4 Year Term (1) Position') and once in its
congressional-district contest ('Nth District President of the United
States (Congressional Districts)...'); the 10 counties that span two
CDs print both district contests.  A direct comparison shows each
county's district rows sum EXACTLY to its statewide rows for all 83
counties and all 21 candidates, so only the statewide contest is
emitted (district duplication is not a distinct result).  Z-flag rows
are the two 'Uncommitted' candidates; there are no W (write-in) rows.

Candidate names are LAST/FIRST/MIDDLE -- middle is meaningful ('R.'
Bloomberg, 'J.' Trump); Castro is unaccented in the source.  Output
header matches the other county-level files (candidate before party):
county,office,district,candidate,party,votes, office 'President',
district blank.
"""
import csv
import re
import sys

SOURCE = ('/Users/dwillis/OpenElections/'
          '2020PRESIDENTIAL_PRIMARY_MI_CENR_BY_COUNTY.txt')
OUT = '2020/20200310__mi__primary__president__county.csv'

COUNTY, OFFICE_DESC, PARTY_DESC, LAST, FIRST, MIDDLE, VOTES = 5, 6, 8, 10, 11, 12, 14
STATEWIDE = 'President of the United States 4 Year Term (1) Position'

COUNTY_FIX = {'GD. TRAVERSE': 'Grand Traverse'}

PARTY_MAP = {'DEMOCRATIC': 'DEM', 'REPUBLICAN': 'REP'}


def titlecase_county(name):
    name = COUNTY_FIX.get(name.strip().upper(), name)
    return ' '.join(w.capitalize() for w in name.split())


def candidate_name(last, first, middle):
    return ' '.join(p.strip() for p in (first, middle, last) if p.strip())


def main():
    with open(SOURCE, newline='') as fh:
        rows = [r + [''] * (18 - len(r))
                for r in csv.reader(fh, delimiter='\t')]
    data = [r for r in rows[2:] if r[COUNTY] and
            not r[0].startswith('RECORDS')]

    out_rows = {}
    unmapped = set()
    unknown_party = set()
    raw_total = 0
    # district-scoped rows must exactly mirror the statewide rows
    state = {}
    dist = {}
    for r in data:
        desc = ' '.join(r[OFFICE_DESC].split())
        party_key = ' '.join(r[PARTY_DESC].strip().upper().split())
        votes = int(r[VOTES]) if r[VOTES] else 0
        key = (r[COUNTY], ' '.join(r[PARTY_DESC].split()),
               candidate_name(r[LAST], r[FIRST], r[MIDDLE]))
        target = state if desc == STATEWIDE else dist
        target[key] = target.get(key, 0) + votes
        if desc != STATEWIDE and not re.match(
                r'^\d+(st|nd|rd|th) District President of the United '
                r'States', desc, re.I):
            unmapped.add(desc)
        if party_key not in PARTY_MAP:
            unknown_party.add(r[PARTY_DESC])
        if desc == STATEWIDE:
            county = titlecase_county(r[COUNTY])
            party = PARTY_MAP[party_key]
            raw_total += votes
            out_key = (county, 'President', '', candidate_name(
                r[LAST], r[FIRST], r[MIDDLE]), party)
            out_rows[out_key] = out_rows.get(out_key, 0) + votes
    if unmapped:
        sys.exit(f'{SOURCE}: unmapped offices: {unmapped}')
    if unknown_party:
        sys.exit(f'{SOURCE}: unknown parties: {unknown_party}')
    if set(state) != set(dist):
        sys.exit(f'{SOURCE}: statewide/district key sets differ: '
                 f'{set(state) ^ set(dist)}')
    mismatch = {k for k in state if state[k] != dist[k]}
    if mismatch:
        sys.exit(f'{SOURCE}: district rows do not mirror statewide '
                 f'for {sorted(mismatch)[:10]} ({len(mismatch)} keys)')

    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'office', 'district', 'candidate', 'party',
                    'votes'])
        for key, votes in sorted(out_rows.items()):
            w.writerow([*key, votes])

    csv_total = sum(out_rows.values())
    print(f'Wrote {len(out_rows)} rows to {OUT}')
    print(f'raw CENR statewide total {raw_total} == csv total '
          f'{csv_total}: {raw_total == csv_total}')


if __name__ == '__main__':
    main()