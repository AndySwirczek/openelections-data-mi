"""Parse county "Detailed Results Report" precinct-major PDFs (2026 primary).

Layout (Van Buren County): one report per precinct flowing across pages. Each
page repeats three title lines and the precinct label on line 4; contest
sections then follow one another, each with a title line ending in a party tag
("(DEM)"/"(REP)"), a "Vote For N" line, a wrapped column header
(Election Day / AV Counting Boards / Early Voting / TOTAL), one row per
candidate, and Total Votes Cast / Over Votes / Under Votes / Ballots Cast rows.

Small counts are masked with "**" ("** Protected" footer): protection is
all-or-nothing across the three method columns, and the TOTAL cell is
protected in a few rows. Protected cells are emitted blank (the "Ballots Cast"
row is always printed in full, so only candidate-level detail is blanked).

Usage:
    .venv/bin/python src/detailed_results_pdf_parser.py <source.pdf> \
        --county 'Van Buren' \
        --out 2026/counties/20260804__mi__primary__van_buren__precinct.csv
"""
import argparse
import csv
import re
import sys

import pdfplumber

PARTY_TAG = re.compile(r' \((DEM|REP|LIB|GRN|UST)\)$')
CANDIDATE_PARTY = re.compile(r' \((Democrat|Republican|Libertarian|Green|U.S. Taxpayers)\)$')
ROW = re.compile(r'^(.+?) (\*\*|[\d,]+) (\*\*|[\d,]+) (\*\*|[\d,]+) (\*\*|[\d,]+)$')
VOTE_FOR = re.compile(r'^Vote For (\d+)$')
WRITE_IN = re.compile(r'^Write-?in\b', re.I)
DISTRICT_PATTERNS = [
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$'), 'U.S. House'),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th) District$'), 'State Senate'),
    (re.compile(r'^State Rep (\d+)(?:st|nd|rd|th) District$'), 'State House'),
]
OFFICE_EXACT = {
    'Governor': 'Governor',
    'United States Senator': 'U.S. Senate',
    'Surveyor': 'Surveyor',
    'County Comm': 'County Commissioner',
}
# Breakdown column order matches the county's 2024 file convention.
BREAKDOWN_ORDER = ['election_day', 'early_voting', 'av_counting_boards']
# PDF column order: Election Day, AV Counting Boards, Early Voting, TOTAL.
PDF_METHODS = ['election_day', 'av_counting_boards', 'early_voting']
PAGE_NOISE = re.compile(
    r'^(Statistics|TOTAL\b|Ballots Cast \d|Voter Registration|Voter Turnout|'
    r'AV Counting|Election Day |Early Voting|'
    r'.*County (Detailed Results|August)|August \d{1,2}, \d{4})')
PRECINCT = re.compile(r'.*, (?:Ward \d+, )?Precinct \d+$')


def ordinal(n):
    return f'{n}{"th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")}'


class Contest:
    """One contest section: rows from the title's "Vote For" to "Ballots Cast"."""

    def __init__(self, county, precinct):
        self.county = county
        self.precinct = precinct
        self.title = ''
        self.vote_for = None
        self.rows = []  # (label, ed, av, ev, total) with '**' for protected

    def finish(self, out_rows, problems):
        if self.vote_for is None or not self.title:
            return
        office, district, party = map_office(self.title)
        names, cast, total_cast = {}, None, None
        for label, *cells in self.rows:
            if label in ('Total Votes Cast', 'Over Votes', 'Under Votes'):
                if label == 'Total Votes Cast':
                    total_cast = cells
            elif label == 'Ballots Cast':
                cast = cells
            else:
                names[label] = cells
        if cast is None:
            problems.append(f'{self.precinct} / {self.title}: no Ballots Cast row')
            return
        vals = [None if v == '**' else int(v.replace(',', '')) for v in cast]
        if vals[3] is not None and sum(v for v in vals[:3] if v is not None) != vals[3]:
            problems.append(f'{self.precinct} / {self.title}: Ballots Cast methods '
                            f'{vals[:3]} != {vals[3]}')
        if total_cast is not None and total_cast[3] != '**':
            tv = int(total_cast[3].replace(',', ''))
            visible = sum(int(c[3].replace(',', '')) for c in names.values() if c[3] != '**')
            if visible > tv:
                problems.append(f'{self.precinct} / {self.title}: visible candidate sum '
                                f'{visible} > Total Votes Cast {tv}')
        bc_method = dict(zip(PDF_METHODS, vals[:3]))
        for label, cells in names.items():
            name = 'Write-In' if WRITE_IN.match(label) else label
            name = CANDIDATE_PARTY.sub('', name).strip()
            methods = [None if v == '**' else int(v.replace(',', '')) for v in cells[:3]]
            by_method = dict(zip(PDF_METHODS, methods))
            out_rows.append([self.county, self.precinct, office, district, party, name,
                             '' if cells[3] == '**' else int(cells[3].replace(',', ''))]
                            + [by_method[m] for m in BREAKDOWN_ORDER])
        out_rows.append([self.county, self.precinct, office, district, party,
                         'Ballots Cast', vals[3]] + [bc_method[m] for m in BREAKDOWN_ORDER])


def map_office(title):
    party = ''
    m = PARTY_TAG.search(title)
    if m:
        party = m.group(1)
        title = title[:m.start()]
    # "Delegate Almena Township, Precinct 1" -> jurisdiction-first delegate office.
    m = re.match(r'^Delegate (.+)$', title)
    if m:
        title = f'{m.group(1).strip()} Delegate to County Convention'
    # "County Comm 5" -> the repo's "County Commissioner 5th District" form.
    m = re.match(r'^County Comm (\d+)$', title)
    if m:
        title = f'County Commissioner {ordinal(int(m.group(1)))} District'
    office = OFFICE_EXACT.get(title)
    district = ''
    if office is None:
        for pat, name in DISTRICT_PATTERNS:
            m = pat.match(title)
            if m:
                office, district = name, m.group(1)
                break
        else:
            office = title
    return office, district, party


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    out_rows = []
    problems = []
    contest = None
    precinct = None
    with pdfplumber.open(args.pdf) as pdf:
        pages = [p.extract_text() or '' for p in pdf.pages]
    for page in pages:
        for line in (l.strip() for l in page.splitlines()):
            if not line or line == '** Protected':
                continue
            m = ROW.match(line)
            if m and contest is not None:
                contest.rows.append(m.groups())
                continue
            if VOTE_FOR.match(line):
                if contest is not None and contest.rows:
                    if not any(r[0] == 'Ballots Cast' for r in contest.rows):
                        problems.append(f'{precinct} / {contest.title}: section ends '
                                        f'without Ballots Cast')
                    contest.finish(out_rows, problems)
                if contest is None or contest.rows:
                    contest = Contest(args.county, precinct)
                contest.vote_for = int(line.split()[-1])
                continue
            if PRECINCT.match(line):
                if line != precinct:
                    # A contest section never spans two precincts; it flows
                    # across same-precinct page breaks, so only a new precinct
                    # label ends the section.
                    if contest is not None and contest.rows:
                        if not any(r[0] == 'Ballots Cast' for r in contest.rows):
                            problems.append(f'{precinct} / {contest.title}: section '
                                            f'ends without Ballots Cast at precinct '
                                            f'change')
                        contest.finish(out_rows, problems)
                    precinct = line
                    contest = None
                continue
            if PAGE_NOISE.match(line):
                continue
            # Contest title line (titles are never wrapped in this report).
            if contest is not None and contest.rows:
                if not any(r[0] == 'Ballots Cast' for r in contest.rows):
                    problems.append(f'{precinct} / {contest.title}: section ends '
                                    f'without Ballots Cast')
                contest.finish(out_rows, problems)
            if contest is None or contest.rows:
                contest = Contest(args.county, precinct)
            contest.title = line
    if contest is not None and contest.rows:
        if not any(r[0] == 'Ballots Cast' for r in contest.rows):
            problems.append(f'{precinct} / {contest.title}: section ends without '
                            f'Ballots Cast at end of document')
        contest.finish(out_rows, problems)

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate',
                    'votes'] + BREAKDOWN_ORDER)
        for row in out_rows:
            w.writerow(['' if v is None else v for v in row])
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()