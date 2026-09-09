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
# Montcalm 2022 prints the unresolved write-in counts without percentages
# ('Unresolved write-in votes: 0 0': Election Day + Total)
UNRESOLVED = re.compile(r'^Unresolved write-in votes:((?: \d[\d,]*)+)$')
PAIR = re.compile(r'(\d[\d,]*) [\d.]+%')
# The empty-contest marker: no candidate ran, so the report prints a bare
# count+percentage row with no choice label.
ZERO_ROW = re.compile(r'^(?:(?:\d[\d,]* [\d.]+% )+)\d[\d,]* [\d.]+%$')
# Muskegon 2024 reports split precincts as "Precinct 1 - A" / "- B" (each a
# full report), so the label may end in a " - <letter>" suffix.
PRECINCT = re.compile(
    # Ionia 2024 writes split precincts without spaces ("Danby 1-A").
    r'^(.+ \d+(?: ?- ?[A-Z])?) (\d[\d,]*) of ([\d,]+) registered voters')
TITLE_DONE = re.compile(
    r' - (?:Democratic|Republican|Nonpartisan)(?: Party)? - '
    r'Vote?r? for not more than \d+$')
PAGE_NOISE = re.compile(
    r'^(Precinct Results.*|Official Results|Official Election Results.*|'
    r'Unofficial Results|Cumulative Results|'
    r'Registered Voters|\d+ of [\d,]+ = [\d.]+%|Official Canvassed|'
    r'Precincts Reporting|Polling Places.*|Election Night|Primary Election|'
    r'8/4/2026|'
    r'Run Time.*|Run Date.*|Choice Party|Early Voting -$|County$|'
    r'Undervotes:.*|Overvotes:.*|Invalid Votes:.*|'
    r'Unresolved write-in votes:.*|Rejected write-in votes:.*|'
    # Clinton 2024 repeats the election date in the page header.
    r'\d{{1,2}}/\d{{1,2}}/\d{{4}}$|\w+ \d{{1,2}}, \d{{4}} .*Election|'
    r'.*Election.*\d{{1,2}}, 20\d\d|'
    r'[^ ]* End of report|{county} County,? ?Michigan?$|'
    r'{county} County - Precinct|'
    # Ionia 2024's combined PDF prefixes each page with a dated banner, and
    # wraps the method header of its combined "Election Day & Absentee
    # Ballots" column.
    r'\d{{8}} (?:Canvass|Precinct|Cumulative) Report.*|\d{{8}} (?:Primary|General) Election|'
    r'Election Day &$|Choice Party Early Voting|Absentee Ballots( Total)?$|'
    r'Early Voting$)')
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
    # 2024 primary: jurisdiction-less delegate titles; keep the same
    # full-label convention as Muskegon/Ottawa. The report prints a
    # countywide contest block and then a township block, and both carry a
    # bare "Treasurer - <Party>" title; the township one is jurisdiction-
    # prefixed so the two stay distinct.
    'Clinton': {
        'methods': ['election_day', 'absentee', 'early_voting'],
        'header': ['election_day', 'absentee', 'early_voting'],
        'delegate_full': True,
        'township_block': True,
    },
    # 2024 primary (the file lives in the Charlevoix sources folder but is
    # Hillsdale's report): methods print Early Voting / AVCB / Election Day.
    'Hillsdale 2024': {
        'methods': ['early_voting', 'av_counting_boards', 'election_day'],
        'header': ['early_voting', 'av_counting_boards', 'election_day'],
        'delegate_full': False,
        'township_block': True,
    },
    # 2024 primary: same report as Muskegon but the early-voting columns
    # print Local before County (2026 prints County before Local).
    'Muskegon 2024': {
        'methods': ['election_day', 'absentee', 'ev_local', 'ev_county'],
        'header': ['election_day', 'absentee', 'early_voting', 'ev_county',
                   'ev_local'],
        'delegate_full': True,
    },
    # 2024 primary: bare county titles plus a township block repeating
    # "Clerk"/"Treasurer"; early voting prints before election day.
    'Montcalm': {
        'methods': ['early_voting', 'election_day'],
        'header': ['early_voting', 'election_day'],
        'delegate_full': True,
        'township_block': True,
    },
    # 2024 primary (one combined PDF that also carries a contest-major
    # canvass report; use --pages to parse only the precinct section): the
    # method columns are "Early Voting" and a combined "Election Day &
    # Absentee Ballots" the source does not split, so the combined count is
    # carried in 'election_day' and 'absentee' stays blank.
    'Ionia': {
        'methods': ['election_day', 'early_voting'],
        'header': ['election_day', 'absentee', 'early_voting'],
        'delegate_full': True,
        'township_block': True,
    },
    # 2022 primary: one "Election Day Voting" method column plus Total; the
    # empty 'header' keeps the votes-only shape the 2022 county files use.
    'Montcalm 2022': {
        'methods': ['election_day'],
        'header': [],
        'delegate_full': True,
        'unresolved_writeins': True,
    },
    # 2022 primary: methods print Precinct (election day) / Absentee.
    'Muskegon 2022': {
        'methods': ['election_day', 'absentee'],
        'header': [],
        'delegate_full': True,
        'unresolved_writeins': True,
    },
}
DISTRICT_PATTERNS = [
    # 'In' is title-cased in Hillsdale's 2022 report
    (re.compile(r'^Representative [Ii]n Congress (\d+)(?:st|nd|rd|th) District$'),
     'U.S. House'),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th) District$'), 'State Senate'),
    (re.compile(r'^Representative [Ii]n State Legislature (\d+)(?:st|nd|rd|th) District$'),
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

    def __init__(self, county, precinct, cfg):
        self.county = county
        self.cfg = cfg
        self.precinct = precinct
        self.title = ''
        self.rows = []  # (name, method counts..., total)
        self.tags = []  # per-row party tag printed after the name (Ottawa)
        self.cast = None  # (method counts..., total)
        self.writein = None  # 'Unresolved write-in votes' total (Montcalm 2022)
        self.township = False  # township-block contest (Clinton)

    def finish(self, out_rows, problems):
        cfg = self.cfg
        if not self.title or self.cast is None:
            if self.title and self.cast is None:
                problems.append(f'{self.precinct} / {self.title}: no Cast Votes row')
            return
        office, district, party = map_office(self.title, self.precinct,
                                             self.cfg, problems,
                                             self.township)
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
        # the unresolved write-in total sits outside Cast Votes; carried as a
        # lumped 'Write-In' row (qualified write-ins can't be split out)
        if self.writein:
            out_rows.append([self.county, self.precinct, office, district,
                             party, 'Write-In', self.writein])
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


def map_office(title, precinct, cfg, problems, township=False):
    party = ''
    m = re.search(r' - (' + '|'.join(PARTY_WORDS) + r')(?: Party)?(?: - |$)', title)
    if m:
        party = PARTY_CODES.get(m.group(1), '')
        tail = title[m.end():].strip()
        title = title[:m.start()].strip()
        # A wrapped proposal title resumes after the party marker
        # ("...Capital - Nonpartisan Party - Expenses Millage Proposal");
        # only a trailing "Vote for not more than N" is not part of the name.
        if tail and not re.match(r'Vote?r? for not more than \d+$', tail):
            title = f'{title} {tail}'.strip()
    # "(W)" marks a write-in candidate; keep the printed name.
    jurisdiction = re.sub(r', (?:Ward [IVX]+, )?Precinct \d+$', '', precinct)
    # Ionia 2024 writes precinct numbers without a comma ("Boston 1",
    # "Lyons 2-B"); a jurisdiction never ends in its precinct number.
    jurisdiction = re.sub(r' \d+(?: ?- ?[A-Z])?$', '', jurisdiction)
    if township and title == 'Treasurer':
        # Township block: same bare title as the county Treasurer contest.
        title = f'{jurisdiction} {title}'
    if re.search(r'Delegate to (?:the )?County C', title):
        # The title carries an abbreviated jurisdiction; confirm it is this
        # precinct's contest, then name the office from the precinct label.
        m = re.match(r'^(.+?) Pct (\d+) Delegate', title)
        if m:
            expected = f'{expand_abbrev(m.group(1))}, Precinct {m.group(2)}'
            if expected != precinct:
                problems.append(f'{precinct}: delegate title {title!r} does not '
                                f'match precinct ({expected!r})')
        if cfg['delegate_full']:
            return f'{precinct} Delegate to County Convention', '', party
        return f'{precinct} Delegate', '', party
    if title.startswith(NEEDS_JURISDICTION) and (township or
                                                 not cfg.get('township_block')):
        # Counties with a township block print bare titles for both the
        # county and the township contest ("Clerk", "Treasurer"); only the
        # township one takes the jurisdiction.
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
    ap.add_argument('--config', default=None,
                    help='COUNTY_CONFIG key when it differs from --county '
                         '(e.g. a second election year for the same county)')
    ap.add_argument('--pages', default=None,
                    help='1-based page range "start-end" (end exclusive) to '
                         'parse, when the PDF bundles several reports')
    args = ap.parse_args()
    cfg_key = args.config or args.county
    if cfg_key not in COUNTY_CONFIG:
        sys.exit(f'no COUNTY_CONFIG for {cfg_key!r}')

    out_rows = []
    problems = []
    contest = None
    precinct = None
    township = False  # inside the precinct's township contest block (Clinton)
    noise = re.compile(PAGE_NOISE.pattern.format(county=re.escape(args.county)))
    with pdfplumber.open(args.pdf) as pdf:
        # x_tolerance 2: Clinton 2024's text layer omits inter-word spaces,
        # and the default tolerance 3 welds every word together.
        pages = [p.extract_text(x_tolerance=2) or '' for p in pdf.pages]
    if args.pages:
        start, end = (int(v) for v in args.pages.split('-'))
        pages = pages[start - 1:end - 1]
    for page in pages:
        for line in (l.strip() for l in page.splitlines()):
            # capture the unresolved-write-in total before the noise filter
            # drops the line (Montcalm 2022)
            if contest is not None and COUNTY_CONFIG[cfg_key].get(
                    'unresolved_writeins'):
                mu = UNRESOLVED.match(line)
                if mu:
                    if contest.writein is not None:
                        problems.append(f'{precinct} / {contest.title}: '
                                        f'duplicate Unresolved write-in votes')
                    contest.writein = int(mu.group(1).split()[-1]
                                          .replace(',', ''))
                    continue
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
                    township = False
                continue
            # Contest title line; wrapped titles continue on the next line.
            if contest is not None and not TITLE_DONE.search(contest.title):
                contest.title += ' ' + line
            else:
                if contest is not None:
                    contest.finish(out_rows, problems)
                # Township-only offices announce the start of the township
                # block; later bare "Treasurer" titles there are the
                # township's, not the county's.
                if COUNTY_CONFIG[cfg_key].get('township_block') and re.match(
                        r'^(Supervisor|Trustee|Delegate to County Convention)\b',
                        line):
                    township = True
                contest = Contest(args.county, precinct,
                                  COUNTY_CONFIG[cfg_key])
                contest.township = township
                contest.title = line
    if contest is not None:
        contest.finish(out_rows, problems)

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate',
                    'votes'] + COUNTY_CONFIG[cfg_key]['header'])
        for row in out_rows:
            w.writerow(['' if v is None else v for v in row])
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()