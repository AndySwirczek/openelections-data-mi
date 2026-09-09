"""Parse the four 2022 primary counties with long-format CSV sources into
per-county precinct CSVs, verified against the certified county-level CENR.

Sources (openelections-sources-mi/2022/primary/):
- Gratiot / Iosco / Isabella: Contest,Candidate,Party,Precinct,Votes; the
  contest name carries a '(DEM)'/'(REP)' suffix (Iosco sometimes without a
  space: 'Rep in Congress 1st District(DEM)'); Isabella calls the office
  'Governor for State'.
- Midland: Contest,Ballot Item ID,Precinct,Candidate/Choice,Party,Total Votes;
  party spelled out ('Republican'/'Democrat') but the contest suffix is there
  too.

Only the four offices the CENR carries for these counties are emitted
(Governor, U.S. House, State Senate, State House); local contests (millages,
county commissioner, township offices, precinct delegates) are skipped.
See src/primary_2022_common.py for the verification rules.

Usage: .venv/bin/python src/primary_2022_csv_counties.py [--apply]
"""
import csv
import re
import sys
from collections import defaultdict

from primary_2022_common import classify, one_space, verify, write

SOURCES = {
    'Gratiot': ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
                "Gratiot County Aug 2022 Primary Precinct Results.csv"),
    'Iosco': ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
              "Iosco County Aug 2022 Primary Precinct Results.csv"),
    'Isabella': ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
                 "Isabella County Aug 2022 Primary Precinct Results.csv"),
    'Midland': ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
                "Midland County Aug 2022 Primary Precinct Results (official "
                "Enhanced Voting portal).csv"),
}


def parse(county, path):
    """[(precinct, office, district, party, candidate, votes)] plus skipped
    contest names and per-contest write-in vote totals."""
    rows = []
    skipped = defaultdict(int)
    write_ins = defaultdict(int)
    lumped = defaultdict(int)
    for r in csv.DictReader(open(path)):
        got = classify(r['Contest'])
        if not got:
            skipped[one_space(r['Contest'])] += 1
            continue
        office, district, party = got
        cand = one_space(r.get('Candidate') or r.get('Candidate/Choice'))
        votes = int(r.get('Votes') or r.get('Total Votes') or 0)
        precinct = one_space(r['Precinct'])
        if re.fullmatch(r'Write-In(?: \(WRITE-IN\))?|Unqualified Write-Ins', cand):
            # the source splits write-ins into Write-In and Unqualified
            # Write-Ins rows; keep one merged 'Write-In' row per precinct
            write_ins[(office, district, party)] += votes
            lumped[(precinct, office, district, party)] += votes
        elif cand.startswith('QW - '):
            # Gratiot names qualified write-ins; the CENR carries them by name
            rows.append((precinct, office, district, party, cand[5:], votes))
        else:
            rows.append((precinct, office, district, party, cand, votes))
    for (precinct, office, district, party), votes in lumped.items():
        if votes:
            rows.append((precinct, office, district, party, 'Write-In', votes))
    return rows, skipped, write_ins


def main(apply=False):
    problems = []
    for county, path in SOURCES.items():
        rows, skipped, write_ins = parse(county, path)
        county_problems = verify(county, rows, write_ins)
        problems += county_problems
        print(f'{county}: {len(rows)} rows; '
              f'{sum(skipped.values())} local rows skipped in {len(skipped)} contests')
        if not county_problems and apply:
            write(county, rows)

    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)