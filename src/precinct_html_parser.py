"""Parse per-precinct HTML result reports (e.g. Washtenaw County 2026 primary).

Source layout: one HTML file per precinct from the county's election-reporting
site. Each file has a header table (Registered Voters / Ballots Cast / turnout)
and one results table whose columns are Early Votes, Absentee Votes,
Election Day Votes, Total Votes, Percent. Contest rows are `<td colspan=6>`
headers; the HTML carries no party labels, so party is inferred from block
order: a contest title appearing twice means the DEM block comes first and the
REP block second (ballot order); single-occurrence contests with Yes/No
candidates are proposals (party blank), other single-occurrence contests are
assumed DEM with a warning.

Usage:
    .venv/bin/python src/precinct_html_parser.py <dir> \
        --county Washtenaw \
        --out 2026/counties/20260804__mi__primary__washtenaw__precinct.csv
"""
import argparse
import csv
import glob
import html
import os
import re
import warnings

PARTY_BY_INDEX = {0: 'DEM', 1: 'REP'}
WRITE_IN_ROWS = {'rejected write-ins', 'unassigned write-ins'}
NONVOTE_ROWS = {'undervotes', 'overvotes', 'invalid votes'}
DISTRICT_PATTERNS = [
    (re.compile(r'^Representative in Congress (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'U.S. House'),
    (re.compile(r'^State Senator (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'State Senate'),
    (re.compile(r'^Representative in State Legislature (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'State House'),
]
OFFICE_EXACT = {
    'Governor': 'Governor',
    'United States Senator': 'U.S. Senate',
}
# Local offices whose printed title omits the jurisdiction; the jurisdiction
# is derived from the precinct label ("Augusta Township, Precinct 1" ->
# "Augusta Township Clerk Partial Term Ending 11/20/2028").
JURISDICTION_OFFICES = ('Clerk', 'Treasurer', 'Trustee', 'Supervisor', 'Constable',
                        'Park Commissioner', 'Drain Commissioner')
NUMERIC = re.compile(r'^[\d,]+$')


def clean(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', text))).strip()


def parse_file(path):
    """Return (precinct_name, contests) where contests is a list of
    (title, [(name, early, absentee, election_day, total)])."""
    text = open(path, encoding='utf-8', errors='replace').read()
    m = re.search(r'<font class="h2">(.*?)</font>', text)
    precinct = clean(m.group(1)) if m else None

    contests = []  # (title, rows)
    for chunk in text.split('<tr'):
        tds = re.findall(r'<td[^>]*>(.*?)</td>', chunk, re.S)
        if not tds:
            continue
        if 'colspan=6' in chunk[:200] or 'colspan="6"' in chunk[:200]:
            title = re.sub(r'\s+\(.*$', '', clean(tds[0]))
            contests.append((title, []))
            continue
        if len(tds) != 6:
            continue
        name = clean(tds[0])
        try:
            vals = [int(clean(tds[i]).replace(',', '').split('%')[0]) for i in range(1, 5)]
        except ValueError:
            continue
        early, absentee, election_day, total = vals
        if not contests:
            continue  # index pages carry stray rows but no contest headers
        # Invalid/under/over vote rows are unattributed by method in some
        # precinct reports, so only require the sum identity on vote rows.
        if name.lower() not in NONVOTE_ROWS and early + absentee + election_day != total:
            raise ValueError(f'{os.path.basename(path)} {precinct} {name}: '
                             f'{early}+{absentee}+{election_day} != {total}')
        contests[-1][1].append((name, early, absentee, election_day, total))
    return precinct, contests


def jurisdiction_of(precinct):
    return re.split(r', (?:Ward|Precinct)', precinct)[0].strip()


def map_office(title, precinct):
    office = OFFICE_EXACT.get(title)
    district = ''
    if office is None:
        for pat, name in DISTRICT_PATTERNS:
            m = pat.match(title)
            if m:
                office, district = name, m.group(1) or m.group(2)
                break
    if office is None:
        office = title
        if title == 'Delegate to County Convention':
            office = f'{precinct} Delegate to County Convention'
        else:
            for prefix in JURISDICTION_OFFICES:
                if title.startswith(prefix + ' '):
                    office = f'{jurisdiction_of(precinct)} {title}'
                    break
    return office, district


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('directory')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    out_rows = []
    for path in sorted(glob.glob(os.path.join(args.directory, '*.html'))):
        precinct, contests = parse_file(path)
        if not precinct:
            print(f'Skipping {os.path.basename(path)} (no precinct header, e.g. index page)')
            continue
        # Party inference: DEM block first, REP second, on repeated titles.
        counts = {}
        for title, _ in contests:
            counts[title] = counts.get(title, 0) + 1
        seen = {}
        for title, rows in contests:
            idx = seen.get(title, 0)
            seen[title] = idx + 1
            if counts[title] > 2:
                warnings.warn(f'{os.path.basename(path)}: {title} occurs '
                              f'{counts[title]} times; party assignment is a guess')
            party = PARTY_BY_INDEX.get(idx)
            names = {name.lower() for name, *_ in rows
                     if name.lower() not in NONVOTE_ROWS}
            if names and names <= {'yes', 'no'}:
                party = ''
            elif counts[title] == 1:
                # Single occurrence of a non-proposal contest: assume DEM, but
                # flag it since a nonpartisan office would be mislabeled.
                print(f'NOTE single-occurrence contest assumed DEM: '
                      f'{os.path.basename(path)} / {title}')
            office, district = map_office(title, precinct)
            cand_sum = [0, 0, 0, 0]
            emitted_write_in = False
            for name, early, absentee, election_day, total in rows:
                key = name.lower()
                if key in NONVOTE_ROWS:
                    continue
                if key in WRITE_IN_ROWS:
                    cand_sum[0] += early; cand_sum[1] += absentee
                    cand_sum[2] += election_day; cand_sum[3] += total
                    if not emitted_write_in:
                        out_rows.append([args.county, precinct, office, district, party,
                                         'Write-In', total, early, absentee, election_day])
                        emitted_write_in = True
                    else:
                        # merge into the Write-In row just appended
                        w = out_rows[-1]
                        w[6] += total; w[7] += early; w[8] += absentee; w[9] += election_day
                    continue
                cand_sum[0] += early; cand_sum[1] += absentee
                cand_sum[2] += election_day; cand_sum[3] += total
                out_rows.append([args.county, precinct, office, district, party,
                                 name, total, early, absentee, election_day])
            out_rows.append([args.county, precinct, office, district, party,
                             'Ballots Cast', cand_sum[3], cand_sum[0], cand_sum[1], cand_sum[2]])

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes',
                    'early_voting', 'absentee', 'election_day'])
        w.writerows(out_rows)
    print(f'Wrote {len(out_rows)} rows to {args.out}')


if __name__ == '__main__':
    main()