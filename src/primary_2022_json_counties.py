"""Parse the three 2022 primary counties with Enhanced Voting portal JSON
sources into per-county precinct CSVs, verified against the certified
county-level CENR.

Sources (openelections-sources-mi/2022/primary/): Berrien, Cass and Newaygo
JSON exports with ballotItems (one per contest/proposal, name like 'Governor
(DEM)' / 'Representative in Congress District 4 (DEM)') each carrying
summaryResults (countywide) and breakdownResults (per precinct, ballotOptions
per candidate). Berrien and Cass wrap the election in an 'election' key;
Newaygo's export lists ballotItems at the top level.

Lumped 'Write-In' ballot options (isWriteIn=True, no qualified write-ins named
in any of the three) are kept as merged 'Write-In' rows. Only the four offices
the CENR carries are emitted; local contests are skipped. Known source gap:
Newaygo's export omits State Senate 33 entirely (see verify output).

Usage: .venv/bin/python src/primary_2022_json_counties.py [--apply]
"""
import json
import sys
from collections import defaultdict

from primary_2022_common import classify, one_space, verify, write

SOURCES = {
    'Berrien': ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
                "Berrien County Aug 2022 Primary Precinct Results.json"),
    'Cass': ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
             "Cass County Aug 2022 Primary Precinct Results.json"),
    'Newaygo': ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
                "Newaygo County Aug 2022 Primary Precinct Results.json"),
}

# source typos: portal name -> CENR (certified) name
CAND_FIX = {('Berrien', 'Scott Rex Star'): 'Scott Rex Starr'}


def ballot_items(data):
    """Both export shapes carry the contest list in 'ballotItems'."""
    return data['ballotItems'] if isinstance(data, dict) else data


def parse(county, path):
    """[(precinct, office, district, party, candidate, votes)] plus skipped
    contest names and per-contest write-in vote totals."""
    rows = []
    skipped = defaultdict(int)
    write_ins = defaultdict(int)
    lumped = defaultdict(int)
    data = json.load(open(path))
    for item in ballot_items(data):
        got = classify(item['name'][0]['text'])
        if not got:
            skipped[one_space(item['name'][0]['text'])] += 1
            continue
        office, district, party = got
        for br in item.get('breakdownResults') or []:
            precinct = one_space(br['precinct']['name'][0]['text'])
            for opt in br['ballotOptions']:
                cand = one_space(opt['name'][0]['text'])
                votes = opt['voteCount'] or 0
                if opt.get('isWriteIn') or cand == 'Unqualified Write-Ins':
                    # lumped write-in votes the CENR does not carry; keep one
                    # merged 'Write-In' row per precinct
                    write_ins[(office, district, party)] += votes
                    lumped[(precinct, office, district, party)] += votes
                else:
                    if cand.startswith('QW - '):
                        # the portal names qualified write-ins; the CENR
                        # carries them by name
                        cand = cand[5:]
                    rows.append((precinct, office, district, party,
                                 CAND_FIX.get((county, cand), cand), votes))
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