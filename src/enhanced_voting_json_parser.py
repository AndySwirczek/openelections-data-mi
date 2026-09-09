"""Parse an Enhanced Voting results JSON (enhancedvoting.com ENR) into a
precinct CSV.

Written for openelec­tions-sources-mi/2024/primary/"Leelanau County Aug 2024
Primary Precinct Results (enhancedvoting JSON).json", downloaded from
https://app.enhancedvoting.com/results/public/leelanau-county-mi/elections/
August2024Primary. Shape:

    {"source": ..., "election": ..., "contests": [
        {"contest": "United States Senator (DEM)", "ballotItemId": ...,
         "breakdownResults": [
             {"precinct": {"name": [{"text": "Bingham Township, Precinct 1"}]},
              "voteTotal": 416,
              "ballotOptions": [{"name": [{"text": "Hill Harper"}],
                                 "voteCount": 40,
                                 "party": {"abbreviation": "DEM"},
                                 "groupResults": [{"groupName": ...,
                                                   "voteCount": 0}, ...],
                                 "isWriteIn": ..., "isQualifiedWriteIn": ...}
                                ]}]}]}

Each ballotOption's groupResults sum to its voteCount and the options sum to
the precinct's voteTotal; both are verified. Unqualified write-in slots print
the literal name 'Write-in' and are emitted as 'Write-In'; qualified write-ins
carry the candidate's real name and keep it.

Office titles carry the party tag as a '(DEM)' suffix (proposals carry none)
and county offices repeat the county ("County Sheriff for Leelanau County");
the suffix is stripped and the party moved to the party column, matching
Leelanau's 2024 general file (which also fixes the column order used here:
election_day, av_counting_boards, early_voting).

Usage:
    .venv/bin/python src/enhanced_voting_json_parser.py <source.json> \
        --county Leelanau \
        --out 2024/counties/20240806__mi__primary__leelanau__precinct.csv
"""
import argparse
import csv
import json
import re
import sys

# groupResults order emitted (Leelanau's general file column order)
GROUPS = ['Election Day', 'AV Counting Boards', 'Early Voting']
HEADER = ['election_day', 'av_counting_boards', 'early_voting']

STATE_OFFICES = [
    (re.compile(r'^United States Senator$'), 'U.S. Senate', ''),
    (re.compile(r'^Representative in Congress '
                r'(\d+)(?:st|nd|rd|th)(?: +)?District$'),
     'U.S. House', 1),
    (re.compile(r'^Representative in State Legislature '
                r'(\d+)(?:st|nd|rd|th)(?: +)?District$'), 'State House', 1),
]
# 'County Sheriff for Leelanau County' -> 'County Sheriff' (Leelanau's 2024
# general file drops the county suffix)
COUNTY_SUFFIX = re.compile(r'\s+for\s+[\w. ]+ County$')


def text(field):
    """Localized fields are [{'text': ...}]; the contest name is a plain str."""
    raw = field[0]['text'] if isinstance(field, list) else field
    return re.sub(r'\s+', ' ', raw).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('json')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    with open(args.json) as fh:
        data = json.load(fh)

    rows = []
    problems = []
    for contest in data['contests']:
        title = text(contest['contest'])
        party = ''
        m = re.search(r'\s*\((DEM|REP|LIB|UST|GRN)\)\s*$', title)
        if m:
            party, title = m.group(1), title[:m.start()].rstrip()
        office, district = title, ''
        m = COUNTY_SUFFIX.search(office)
        if m:
            office = office[:m.start()].rstrip()
        for pat, name, grp in STATE_OFFICES:
            m2 = pat.match(office)
            if m2:
                office = name
                district = m2.group(grp) if grp else ''
                break
        for br in contest['breakdownResults']:
            precinct = text(br['precinct']['name'])
            total = 0
            for opt in br['ballotOptions']:
                name = text(opt['name'])
                if name == 'Write-in':
                    name = 'Write-In'
                row_party = (opt['party'] or {}).get('abbreviation') or party
                if row_party != party and not opt['isWriteIn']:
                    problems.append(f'{precinct} / {title}: {name} party '
                                    f'{row_party!r} != contest party {party!r}')
                groups = {text(g['groupName']): g['voteCount']
                          for g in opt['groupResults']}
                if set(groups) - set(GROUPS):
                    problems.append(f'{precinct} / {title}: unknown groups '
                                    f'{sorted(groups)}')
                if sum(groups.values()) != opt['voteCount']:
                    problems.append(f'{precinct} / {title}: {name} groups '
                                    f'{sum(groups.values())} != '
                                    f"{opt['voteCount']}")
                total += opt['voteCount']
                row = [args.county, precinct, office, district, row_party,
                       name, opt['voteCount']]
                # breakdown columns precede votes in Leelanau's file shape
                row = row[:6] + [groups.get(g) for g in GROUPS] + [row[6]]
                rows.append(row)
            if total != br['voteTotal']:
                problems.append(f'{precinct} / {title}: options sum {total} '
                                f"!= voteTotal {br['voteTotal']}")

    # No Ballots Cast rows: the source has no turnout section, and its
    # per-contest voteTotal is the sum of candidate votes only (verified
    # above), so ballots cast cannot be derived without dropping the
    # contest's undervotes.

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate'] + HEADER + ['votes'])
        w.writerows(rows)
    print(f'Wrote {len(rows)} rows to {args.out} ({len(problems)} problems)')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(1)


if __name__ == '__main__':
    main()