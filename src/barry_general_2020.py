#!/usr/bin/env python3
"""Parse Barry County's Nov 2020 general election from the ES&S
contest-major SOVC ('Barry MI StatementOfVotesCastRPT(1).pdf', 253pp)
via src/sovc_pdf_parser.py, with the post-passes the generic parser
leaves open:

  - office renames the 2020 general files use (Straight Party,
    President with running mates cut at the '/');
  - party codes: minor-party candidates carry a '(NLP)'-style tag in
    the SOVC column headers (and the parser leaves the tag on the
    candidate name) — strip it into the party column; the remaining
    codes come from the county file's own rows.

Verification: per (office, district, first-half candidate) sums against
the 22 Barry rows of 2020/20201103__mi__general__county.csv, plus the
parser's internal county-total checks (precinct sums == printed
'County - Total' for every column, including Times Cast).  The source's
turnout data is broken (the SOVC prints Registered Voters 0 everywhere
and '% Turnout N/A'), so no Registered Voters rows are emitted; each
office carries its parser-default 'Ballots Cast' row (Total Votes +
write-ins) as in the committed Barry primary file.
"""

import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sovc_pdf_parser as sovc  # noqa: E402

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/general/'
       'Barry MI StatementOfVotesCastRPT(1).pdf')
OUT = '2020/counties/20201103__mi__general__barry__precinct.csv'
COUNTY_FILE = '2020/20201103__mi__general__county.csv'
COUNTY = 'Barry'

SP_CODES = {
    'Democratic Party': 'DEM', 'Republican Party': 'REP',
    'Libertarian Party': 'LIB', 'U.S. Taxpayers Party': 'UST',
    'Working Class Party': 'WCP', 'Green Party': 'GRN',
    'Natural Law Party': 'NLP',
}
TAG = re.compile(r'\s*\((?:DEM|REP|LIB|UST|WCP|GRN|NLP|NPA)\)$')


def split_party(name):
    """Strip a trailing '(NLP)' tag; returns (name, code or None)."""
    m = TAG.search(name)
    if not m:
        return name, None
    code = m.group(0).strip(' ()')
    return name[:m.start()].strip(), (code if code != 'NPA' else '')


def main():
    problems = []
    out_rows = []
    sovc.run(SRC, COUNTY, out_rows, problems)

    # candidate -> party codes the county file itself carries.
    county_parties = {}
    county_rows = []
    for row in csv.DictReader(open(COUNTY_FILE)):
        if row['county'] != COUNTY:
            continue
        county_rows.append(row)
        key = (row['office'], row['district'],
               row['candidate'].split('/')[0].strip())
        county_parties[key] = row['party']

    # office -> per-candidate votes, with the party column filled.
    rows = []
    for county, precinct, office, district, _party, cand, votes in out_rows:
        if office == 'Straight Party Ticket':
            office = 'Straight Party'
        elif office == 'President/Vice-President of the United States':
            office = 'President'
        cand, code = split_party(cand)
        if office == 'President':
            cand = cand.split('/')[0].strip()
        if office == 'Straight Party':
            party = SP_CODES.get(cand, '')
        elif code is not None:
            party = code
        else:
            party = county_parties.get(
                (office, district, cand), '')
        rows.append([county, precinct, office, district, party, cand, votes])

    # --- verify against the county file -------------------------------
    covered = {'Straight Party', 'President', 'U.S. Senate', 'U.S. House',
               'State House'}
    got = {}
    for _county, _prec, office, district, _party, cand, votes in rows:
        if office in covered and cand != 'Ballots Cast':
            got[(office, district, cand)] = \
                got.get((office, district, cand), 0) + int(votes)
    want = {}
    for row in county_rows:
        if row['candidate'] in ('Registered Voters', 'Ballots Cast'):
            continue
        cand = row['candidate']
        if 'write' in cand.lower():
            cand = 'Write-In'
        key = (row['office'], row['district'],
               cand.split('/')[0].strip())
        want[key] = want.get(key, 0) + int(row['votes'])
    bad = 0
    for key in sorted(set(got) | set(want)):
        if got.get(key, 0) == want.get(key, 0):
            continue
        if key not in want and key[2] == 'Write-In':
            # CENR doesn't list the covered offices' write-ins for Barry;
            # the SOVC's own County - Total rows verify them instead.
            print(f'  info {key}: parsed write-in {got.get(key, 0)} '
                  f'(no county-file row)')
            continue
        bad += 1
        print(f'MISMATCH {key}: parsed {got.get(key, 0)} '
              f'vs county {want.get(key, 0)}')
    print(f'verify: {len(got)} covered keys vs {len(want)} county rows, '
          f'{bad} mismatches')

    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'emitted {len(rows)} rows')

    if bad or problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)


if __name__ == '__main__':
    main()