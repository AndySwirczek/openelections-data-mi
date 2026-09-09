"""Convert county-export contest-major CSVs (Washtenaw-style Aug 2024 primary).

Source layout (identical for Midland, Monroe and Newaygo Aug 2024):
`Contest,Candidate,Party,Precinct,Votes` — one row per contest candidate per
precinct, including zero-vote 'Write-in' rows; proposals have a blank party
and Yes/No candidates; no Ballots Cast or Registered Voters rows.

Notes:
- Midland's file carries 34 Bay County contest rows for City of Midland,
  Precinct 2B (the city straddles the county line); they are kept as-is.
- Newaygo prints the Senate contest as 'United States Senator for State'.
- Monroe's export prints both Whiteford Agricultural Schools bond questions
  under the identical title 'Whiteford Schools - Whiteford Agricultural
  School District of the Counties of Monroe and Lenawee Bond Proposal';
  they are renamed to the ballot's own numbered wordings (as printed in
  Lenawee County's export for the same district). Blocks are identified by
  contest-major row order (a precinct repeating within the title).
- A few cross-county proposal rows ship with blank votes in the counties' own
  exports (Midland: Jasper Township Precinct 1 Shepherd bond; Isabella:
  Deerfield/Denver Precinct 1). Those rows are kept with an empty votes cell,
  matching Isabella's committed CSV.

Usage:
    .venv/bin/python src/county_csv_2024.py <src.csv> --county Midland \
        --out 2024/counties/20240806__mi__primary__midland__precinct.csv
"""
import argparse
import csv
import re
import sys

ORD = {'1st': '1', '2nd': '2', '3rd': '3', '4th': '4', '5th': '5',
       '6th': '6', '7th': '7', '8th': '8', '9th': '9'}
TITLE_RE = re.compile(r'^(.*?)\s*\((DEM|REP)\)$')
CONGRESS_RE = re.compile(r'^Representative in Congress (.+?) District$')
STATEHOUSE_RE = re.compile(r'^Representative in State Legislature '
                           r'(\d+)(?:st|nd|rd|th) District$')
COMMISSIONER_RE = re.compile(
    r'^County Commissioner (?:for County Commissioner )?(.+?) District$')
DELEGATE_RE = re.compile(r'^(.*), Precinct \S+ Delegate$')

# (county, printed title) -> wordings for the contest's blocks in ballot
# order, when the export reuses one title for several ballot questions
BLOCK_TITLES = {
    ('Monroe', 'Whiteford Schools - Whiteford Agricultural School District '
               'of the Counties of Monroe and Lenawee Bond Proposal'): [
        'I. Whiteford Agricultural School District of the Counties of '
        'Monroe and Lenawee Bond Proposal',
        'II. Whiteford Agricultural School District of the Counties of '
        'Monroe and Lenawee Bond Proposal',
    ],
}


def map_office(title):
    """Contest title (party suffix stripped) -> (office, district)."""
    if title in ('United States Senator', 'United States Senator for State'):
        return 'U.S. Senate', ''
    m = CONGRESS_RE.match(title)
    if m:
        d = ORD.get(m.group(1), m.group(1))
        return 'U.S. House', d
    m = STATEHOUSE_RE.match(title)
    if m:
        return 'State House', m.group(1)
    m = COMMISSIONER_RE.match(title)
    if m:
        d = ORD.get(m.group(1), m.group(1))
        return 'County Commissioner', d
    if DELEGATE_RE.match(title):
        return f'{title} to County Convention', ''
    return title, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    rows_in = list(csv.reader(open(args.src)))[1:]
    out = []
    problems = []
    blanks = []
    seen = set()
    # occurrence index per (title, precinct, candidate): the export is
    # contest-major, so all rows of an earlier block (Monroe's first,
    # identically-titled Whiteford bond question) precede the later block's
    # rows; a key seen a second time belongs to block 1
    key_count = {}
    block_of = []
    for r in rows_in:
        if len(r) != 5:
            block_of.append(None)
            continue
        title = TITLE_RE.match(r[0])
        title = (title.group(1) if title else r[0]).strip()
        key = (title, r[3], r[1])
        block_of.append(key_count.get(key, 0))
        key_count[key] = key_count.get(key, 0) + 1
    renames = {}
    for (county, title), names in BLOCK_TITLES.items():
        if county == args.county:
            renames[title] = names

    for r, block in zip(rows_in, block_of):
        if len(r) != 5:
            problems.append(f'bad row: {r}')
            continue
        contest, cand, party, precinct, votes = (f.strip() for f in r)
        if votes == '':
            blanks.append(r)
        elif not votes.isdigit():
            problems.append(f'non-numeric votes: {r}')
            continue
        m = TITLE_RE.match(contest)
        title = m.group(1) if m else contest
        # a few Monroe delegate titles omit the space after the comma
        title = title.replace(',Precinct', ', Precinct')
        if title in renames:
            if block >= len(renames[title]):
                problems.append(f'unexpected block {block} for {title!r}')
            else:
                title = renames[title][block]
        office, district = map_office(title)
        if cand == 'Write-in':
            cand = 'Write-In'
        key = (precinct, office, district, party, cand)
        if key in seen:
            problems.append(f'duplicate row: {key}')
            continue
        seen.add(key)
        out.append([args.county, precinct, office, district, party, cand, votes])

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(out)
    print(f'{len(problems)} problems')
    for p in problems[:20]:
        print('PROBLEM:', p)
    if blanks:
        print(f'{len(blanks)} rows with blank votes (kept, source defect):')
        for b in blanks:
            print('  BLANK:', b)
    print(f'Wrote {len(out)} rows to {args.out}')


if __name__ == '__main__':
    sys.exit(main())