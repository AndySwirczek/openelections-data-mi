"""Parse county "Precinct Results Report" precinct-major PDFs (2026 primary).

Layout (Hillsdale, Muskegon counties): one report per precinct flowing across
3-8 pages. Every page repeats a header block (report banner, countywide
"Registered Voters"/"Precincts Reporting" turnout lines, run date/time, page
number); the precinct label line carries the precinct's turnout
("<label> 516 of 1,815 registered voters = 28.43%"). Contest sections follow,
each with a title "<office> - <Party> Party - Vote for not more than N"
(wrapped titles continue on the next line; Muskegon prints "Voter for" on one
office), a "Choice Party <method columns> Total" column header, one row per
candidate (count + percentage per method, "(W)" marks write-ins), and a
"Cast Votes:" total row. Muskegon adds "Undervotes:"/"Overvotes:" rows after
Cast Votes. Contests where no candidate ran print a lone zero row instead of
candidates. Method columns vary by county (see COUNTY_CONFIG); Muskegon's
"Early Voting - County" header wraps ("Early Voting -" on its own line). Ottawa
prints a party code after each candidate name ("Jocelyn Benson DEM 145 ..."),
titles without the word "Party", jurisdiction-less delegate/local titles, and
"Invalid Votes:"/"Unresolved write-in votes:" rows.

Usage:
    .venv/bin/python src/precinct_results_pdf_parser.py <source.pdf> \
        --county 'Hillsdale' \
        --out 2026/counties/20260804__mi__primary__hillsdale__precinct.csv
"""
import argparse
import csv
import re
import sys

import pdfplumber

# Candidate row: a name followed by N "count percentage" pairs (last pair is
# the contest total). Cast Votes has the same pair tail.
PAIRS = r'((?: \d[\d,]* [\d.]+%)+)'
ROW = re.compile(r'^(.+?)' + PAIRS + r'$')
CAST = re.compile(r'^Cast Votes:' + PAIRS + r'$')
PAIR = re.compile(r'(\d[\d,]*) [\d.]+%')
# The empty-contest marker: no candidate ran, so the report prints a bare
# count+percentage row with no choice label.
ZERO_ROW = re.compile(r'^(?:(?:\d[\d,]* [\d.]+% )+)\d[\d,]* [\d.]+%$')
PRECINCT = re.compile(r'^(.+ \d+) (\d[\d,]*) of ([\d,]+) registered voters')
TITLE_DONE = re.compile(
    r' - (?:Democratic|Republican|Nonpartisan)(?: Party)? - '
    r'Vote?r? for not more than \d+$')
PAGE_NOISE = re.compile(
    r'^(Precinct Results|Official Results|Cumulative Results|'
    r'Registered Voters|\d+ of [\d,]+ = [\d.]+%|Official Canvassed|'
    r'Precincts Reporting|Election Night|Primary Election|8/4/2026|Run Time|'
    r'Run Date|Choice Party|Early Voting -$|County$|Undervotes:|Overvotes:|'
    r'Invalid Votes:|Unresolved write-in votes:|'
    r'[^ ]* End of report|{county} County,? ?Michigan?$|'
    r'{county} County - Precinct)')
# Delegate-title jurisdiction abbreviations (the title abbreviates what the
# precinct label spells out); used to confirm the title belongs to the
# precinct section it prints in.
TITLE_ABBREV = {'Twp': 'Township', 'Pct': 'Precinct', 'Hts': 'Heights',
                'N': 'North'}
# PDF method column order -> repo breakdown columns. Muskegon's Early Voting
# - County / - Local are the vendor's sub-breakdown of early voting; the
# county file carries both plus their sum (its own 2024 file's shape), and
# statewide_generator's COUNTY_COLUMN_MAP takes 'early_voting' from the sum.
COUNTY_CONFIG = {
    'Hillsdale': {
        'methods': ['early_voting', 'av_counting_boards', 'election_day'],
        'header': ['early_voting', 'av_counting_boards', 'election_day'],
        'delegate_full': False,
    },
    'Muskegon': {
        'methods': ['election_day', 'absentee', 'ev_county', 'ev_local'],
        'header': ['election_day', 'absentee', 'early_voting', 'ev_county',
                   'ev_local'],
        'delegate_full': True,
    },
    'Ottawa': {
        'methods': ['election_day', 'absentee', 'early_voting'],
        'header': ['election_day', 'absentee', 'early_voting'],
        'delegate_full': True,
    },
}
DISTRICT_PATTERNS = [
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$'),
     'U.S. House'),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th) District$'), 'State Senate'),
    (re.compile(r'^Representative in State Legislature (\d+)(?:st|nd|rd|th) District$'),
     'State House'),
]
OFFICE_EXACT = {'Governor': 'Governor', 'United States Senator': 'U.S. Senate'}
PARTY_WORDS = ('Democratic', 'Republican', 'Nonpartisan', 'Non Partisan',
               'Non-Partisan')
PARTY_CODES = {'Democratic': 'DEM', 'Republican': 'REP'}
# Local offices whose printed title lacks the jurisdiction; the precinct's
# jurisdiction is prefixed so townships sharing a title stay distinct
# (jurisdiction-first repo precedent).
NEEDS_JURISDICTION = ('Clerk', 'Trustee', 'Supervisor')


def ordinal(n):
    return f'{n}{"th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")}'


class Contest:
    """One contest section: title line through its Cast Votes row."""

    def __init__(self, county, precinct):
        self.county = county
        self.precinct = precinct
        self.title = ''
        self.rows = []  # (name, method counts..., total)
        self.tags = []  # per-row party tag printed after the name (Ottawa)
        self.cast = None  # (method counts..., total)

    def finish(self, out_rows, problems):
        cfg = COUNTY_CONFIG[self.county]
        if not self.title or self.cast is None:
            if self.title and self.cast is None:
                problems.append(f'{self.precinct} / {self.title}: no Cast Votes row')
            return
        office, district, party = map_office(self.title, self.precinct,
                                             self.county, problems)
        counts = [int(v.replace(',', '')) for v in self.cast]
        methods, total = counts[:-1], counts[-1]
        if sum(methods) != total:
            problems.append(f'{self.precinct} / {self.title}: Cast Votes methods '
                            f'{methods} != {total}')
        cand_total = sum(r[-1] for r in self.rows)
        if cand_total != total:
            problems.append(f'{self.precinct} / {self.title}: candidate sum '
                            f'{cand_total} != Cast Votes {total}')
        for tag, (name, *_), in zip(self.tags, self.rows):
            if tag and tag != party:
                problems.append(f'{self.precinct} / {self.title}: row party tag '
                                f'{tag} != contest party {party!r}')
        for name, *cells in self.rows:
            out_rows.append([self.county, self.precinct, office, district, party,
                             name, cells[-1]]
                            + breakdown_values(cells[:-1], cfg))
        out_rows.append([self.county, self.precinct, office, district, party,
                         'Ballots Cast', total] + breakdown_values(methods, cfg))


def breakdown_values(cells, cfg):
    """Method counts -> values for the county's breakdown header order."""
    d = dict(zip(cfg['methods'], cells))
    out = []
    for col in cfg['header']:
        if col in d:
            out.append(d[col])
        elif col == 'early_voting' and 'ev_county' in d and 'ev_local' in d:
            out.append(d['ev_county'] + d['ev_local'])
        else:
            out.append(None)
    return out


def expand_abbrev(text):
    for a, b in TITLE_ABBREV.items():
        text = re.sub(rf'\b{a}\b', b, text)
    return text


def map_office(title, precinct, county, problems):
    party = ''
    m = re.search(r' - (' + '|'.join(PARTY_WORDS) + r')(?: Party)?(?: - |$)', title)
    if m:
        party = PARTY_CODES.get(m.group(1), '')
        title = title[:m.start()].strip()
    # "(W)" marks a write-in candidate; keep the printed name.
    jurisdiction = re.sub(r', (?:Ward [IVX]+, )?Precinct \d+$', '', precinct)
    if re.search(r'Delegate to County C', title):
        # The title carries an abbreviated jurisdiction; confirm it is this
        # precinct's contest, then name the office from the precinct label.
        m = re.match(r'^(.+?) Pct (\d+) Delegate', title)
        if m:
            expected = f'{expand_abbrev(m.group(1))}, Precinct {m.group(2)}'
            if expected != precinct:
                problems.append(f'{precinct}: delegate title {title!r} does not '
                                f'match precinct ({expected!r})')
        if COUNTY_CONFIG[county]['delegate_full']:
            return f'{precinct} Delegate to County Convention', '', party
        return f'{precinct} Delegate', '', party
    if title.startswith(NEEDS_JURISDICTION):
        title = f'{jurisdiction} {title}'
    for pat, name in DISTRICT_PATTERNS:
        m = pat.match(title)
        if m:
            return name, m.group(1), party
    return OFFICE_EXACT.get(title, title), '', party


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    if args.county not in COUNTY_CONFIG:
        sys.exit(f'no COUNTY_CONFIG for {args.county!r}')

    out_rows = []
    problems = []
    contest = None
    precinct = None
    noise = re.compile(PAGE_NOISE.pattern.format(county=re.escape(args.county)))
    with pdfplumber.open(args.pdf) as pdf:
        pages = [p.extract_text() or '' for p in pdf.pages]
    for page in pages:
        for line in (l.strip() for l in page.splitlines()):
            if not line or noise.match(line) or ZERO_ROW.match(line):
                continue
            m = CAST.match(line)
            if m and contest is not None:
                if contest.cast is not None:
                    problems.append(f'{precinct} / {contest.title}: duplicate Cast Votes')
                contest.cast = PAIR.findall(m.group(1))
                contest.finish(out_rows, problems)
                contest = None
                continue
            m = ROW.match(line)
            if m and contest is not None:
                # Ottawa prints a party code after the candidate name.
                name, tag = m.group(1), ''
                mt = re.search(r' (DEM|REP|LIB|UST|GRN|NPA)$', name)
                if mt:
                    tag = mt.group(1)
                    name = name[:mt.start()]
                name = re.sub(r' \(W\)$', '', name)
                contest.tags.append(tag)
                contest.rows.append(
                    [name] + [int(v.replace(',', ''))
                              for v in PAIR.findall(m.group(2))])
                continue
            m = PRECINCT.match(line)
            if m:
                if m.group(1) != precinct:
                    if contest is not None:
                        contest.finish(out_rows, problems)
                        contest = None
                    precinct = m.group(1)
                continue
            # Contest title line; wrapped titles continue on the next line.
            if contest is not None and not TITLE_DONE.search(contest.title):
                contest.title += ' ' + line
            else:
                if contest is not None:
                    contest.finish(out_rows, problems)
                contest = Contest(args.county, precinct)
                contest.title = line
    if contest is not None:
        contest.finish(out_rows, problems)

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate',
                    'votes'] + COUNTY_CONFIG[args.county]['header'])
        for row in out_rows:
            w.writerow(['' if v is None else v for v in row])
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()