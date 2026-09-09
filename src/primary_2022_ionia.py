"""Parse Ionia County's Aug 2022 primary "Official Results" (official
canvass) PDF into a per-county precinct CSV, verified against the certified
county-level CENR.

Layout: contest-major; every page of a contest repeats its title line
('<office> - <Party> Party - Vote for not more than N', wrapped after
'not more') and a rotated column-header band. Narrow contests fit one page
('City of Belding 1 77 77 0 0 1 0 0 288 288 1,719 16.75%'); wide contests
split across page pairs, the left page carrying the candidate columns plus
Cast Votes .. Unresolved write-in votes and the right page repeating the
same precinct labels with In Person/Absentee Ballots Cast .. Turnout
Percentage. A 'Totals' row on the last page closes the contest; Cast Votes
counts every candidate column (qualified write-ins print as '<name> (W)').

Only the CENR-carried partisan contests are emitted; the report's
Unresolved write-in votes are passed to verify() as the lumped write-in
total.

Usage:
    .venv/bin/python src/primary_2022_ionia.py <source.pdf> \
        --out /tmp/ionia22.csv
"""
import argparse
import collections
import re
import sys

import pdfplumber

from primary_2022_common import CENR, verify, write

COUNTY = 'Ionia'
TITLE = re.compile(r'^(.*\S) - (Democratic|Republican) Party - '
                   r'Vote for not more than \d+$')
TITLE_WRAP = re.compile(r'^(.*\S) - (Democratic|Republican) Party - '
                        r'Vote for not more$')
KEEP = [
    (re.compile(r'^Governor$'), 'Governor'),
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$'),
     'U.S. House'),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th) District$'),
     'State Senate'),
    (re.compile(r'^Representative in State Legislature '
                r'(\d+)(?:st|nd|rd|th) District$'), 'State House'),
]
# the fixed (non-candidate) bands, in print order; a page's candidate
# columns precede them
FIXED_TAIL = ['Cast Votes', 'Undervotes', 'Overvotes', 'Invalid Votes',
              'Rejected write-in votes', 'Unresolved write-in votes',
              'In Person/Absentee Ballots Cast', 'Total Ballots Cast',
              'Registered Voters', 'Turnout Percentage']
NUM = re.compile(r'^\d[\d,]*$')
PCT = re.compile(r'^[\d.]+%$')
WRITEIN_CAND = re.compile(r' \((?:W|DEM|REP)\)$')
# the report prints the CENR's 'Kai W. Degraaf' with a space
NAME_FIX = {'Kai W. De Graaf': 'Kai W. Degraaf'}


def map_office(title):
    for pat, name in KEEP:
        m = pat.match(title)
        if m:
            return name, (m.group(1) if pat.groups else '')
    return None, ''


def bands_for(page):
    """[(x, text)] rotated header bands, wrapped bands joined."""
    raw = collections.defaultdict(list)
    for c in page.chars:
        if not c.get('upright'):
            raw[round(c['x0'])].append(c)
    out = []
    for x in sorted(raw):
        chars = sorted(raw[x], key=lambda c: c['top'])
        t = ''.join(c['text'] for c in chars)[::-1].strip()
        if t:
            out.append([x, t])
    merged = []
    for x, t in out:
        if merged and x - merged[-1][0] <= 25:
            merged[-1][1] += ' ' + t
        else:
            merged.append([x, t])
    # the Turnout Percentage band holds a float percentage, not a vote
    # count; drop it and the trailing pct token with it
    return [(x, t) for x, t in merged if t != 'Turnout Percentage']


class Contest:
    def __init__(self, title, party):
        self.title, self.party = title, party
        self.office, self.district = map_office(title)
        self.rows = {}    # label -> {band text: value}
        self.totals = {}  # band text -> county total
        self.closed = False

    def add_cells(self, label, bands, values, problems):
        if label == 'Totals':
            cells = self.totals
        else:
            cells = self.rows.setdefault(label, {})
        for (x, text), v in zip(bands, values):
            if text in cells and cells[text] != v:
                problems.append(f'{self.title}: {label!r}: {text} printed '
                                f'{cells[text]} and {v}')
            cells[text] = v

    def finish(self, problems):
        """Called at the Totals row and at the next title / end of file."""
        self.closed = True
        if self.office is None:
            return
        if not self.totals:
            problems.append(f'{self.title}: no Totals row')
            return
        # every column's precinct sums must match the printed Totals
        sums = collections.Counter()
        for cells in self.rows.values():
            for text, v in cells.items():
                sums[text] += v
        for text, printed in self.totals.items():
            if text in sums and sums[text] != printed:
                problems.append(f'{self.title}: {text} precinct sum '
                                f'{sums[text]} != printed county total '
                                f'{printed}')
        # Cast Votes counts every candidate column
        cands = [v for t, v in self.totals.items() if t not in FIXED_TAIL]
        if cands and sum(cands) != self.totals.get('Cast Votes'):
            problems.append(f'{self.title}: candidate total {sum(cands)} != '
                            f"Cast Votes {self.totals.get('Cast Votes')}")
        # emit against the CENR
        key = (self.office, self.district, self.party)
        cands = [c for (o, d, p, c) in CENR[COUNTY] if (o, d, p) == key]
        cvotes = {c: v for (o, d, p, c), v in CENR[COUNTY].items()
                  if (o, d, p) == key}
        # qualified write-ins print as '<name> (W)'
        norm = lambda s: NAME_FIX.get(s, re.sub(r' \((?:W|DEM|REP)\)$', '', s))
        by_norm_all = {norm(t): t for t in self.totals if t not in FIXED_TAIL}
        missing = [c for c in cands if norm(c) not in by_norm_all]
        if any(cvotes[c] for c in missing):
            problems.append(f'{self.title}: CENR candidates absent from the '
                            f'header: {missing}')
        for label, cells in sorted(self.rows.items()):
            by_norm = {norm(t): v for t, v in cells.items()}
            for name in cands:
                if norm(name) in by_norm:
                    rows.append((label, self.office, self.district,
                                 self.party, name, by_norm[norm(name)]))
        write_ins[key] += self.totals.get('Unresolved write-in votes', 0)


rows = []
write_ins = collections.defaultdict(int)
notes = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    problems = []
    contest = None
    pending_title = None

    with pdfplumber.open(args.pdf) as pdf:
        for pageno, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ''
            # the bundled countywide-summary and precinct-major reports that
            # follow the precinct SOVC print 'Choice Party' column headers
            if 'Choice Party' in text:
                continue
            if '*** End of report ***' in text:
                break
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            i = 0
            while i < len(lines):
                line = lines[i]
                i += 1
                # a wrapped title resumes on the next line ('than 1')
                if pending_title is not None:
                    line = pending_title + ' ' + line
                    pending_title = None
                m = TITLE_WRAP.match(line)
                if m and i < len(lines):
                    pending_title = line
                    continue
                m = TITLE.match(line)
                if m:
                    title = m.group(1)
                    party = m.group(2)[:3].upper()
                    if contest is not None and contest.title == title \
                            and contest.party == party:
                        continue  # the title repeats on every contest page
                    if contest is not None:
                        contest.finish(problems)
                    office, _ = map_office(title)
                    contest = Contest(title, party) if office else None
                    continue
                if line.endswith(' - Ballot Question'):
                    # a party-less proposal title: not a CENR contest, but
                    # it must still close the open contest
                    if contest is not None:
                        contest.finish(problems)
                    contest = None
                    continue
                if contest is None:
                    continue
                bands = bands_for(page)
                if not bands:
                    continue
                # data/Totals row: <label> <one numeric token per band>
                # (pages carrying the Turnout Percentage band append a pct
                # token after the values)
                toks = line.split()
                n = len(bands)
                if PCT.match(toks[-1]):
                    toks = toks[:-1]
                if len(toks) <= n:
                    continue
                vals = toks[-n:]
                if not all(NUM.match(v) for v in vals):
                    continue  # page-header noise ('13456 of 47872 = 28.11%')
                label = ' '.join(toks[:-n])
                if not label:
                    problems.append(f'page {pageno}: row {line!r} has no '
                                    f'label')
                    continue
                values = [int(v.replace(',', '')) for v in vals]
                contest.add_cells(label, bands, values, problems)
    if contest is not None:
        contest.finish(problems)

    for p in notes:
        print('NOTE', p)
    for p in problems:
        print('PROBLEM:', p)
    print(f'{len(rows)} rows; {len(problems)} parser problems')
    blocking = verify(COUNTY, rows, write_ins)
    for p in blocking:
        print('PROBLEM:', p)
    if not problems and not blocking:
        write(COUNTY, rows)


if __name__ == '__main__':
    main()