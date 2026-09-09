"""Parse Leelanau County's Aug 2022 primary "Statement of Votes Cast" PDF
into a per-county precinct CSV, verified against the certified county-level
CENR.

Layout: contest-major; each contest's candidates are 90-degree-rotated column
headers that extract_text mirrors (decoded by clustering non-upright chars on
x0 and reversing the joined string; a wrapped header spans two adjacent bands
joined with a space, e.g. 'Unresolved' + 'Write-In'). Qualified write-in
candidates print as named columns with a trailing '– W/in' / '– W-in' /
'Write-in' marker; the lumped write-in rest is an 'Unresolved Write-In'
column. Each precinct prints three rows per contest — Election Day, AV
Counting Boards, and their Total — preceded by a wrapping precinct label
('Bingham Township,' / 'Precinct 1'); the emitted votes come from the Total
row (Election Day + AV must sum to it). A contest spans pages until the
'Leelanau County <totals>' row, which cross-checks the precinct sums.

Usage:
    .venv/bin/python src/primary_2022_leelanau.py <source.pdf> \
        --out /tmp/leelanau22.csv
"""
import argparse
import collections
import re
import sys

import pdfplumber

from primary_2022_common import verify, write

COUNTY = 'Leelanau'
KEEP_PREFIX = re.compile(
    r'^(Governor|Representative in Congress|State Senator|'
    r'Representative in State Legislature)\b')
VOTE_FOR = re.compile(r'\(Vote for \d+\)$')
DATA = re.compile(r'^(Election Day|AV Counting Boards|Total)\s+'
                  r'([\d,]+(?:\s+[\d,]+)*)$')
# trailing qualified-write-in markers on named columns
W_IN = re.compile(r'\s*[–-] ?W[-/]?in\b|\s+Write-?in$', re.I)
DISTRICT_PATTERNS = [
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$'),
     'U.S. House'),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th) District$'), 'State Senate'),
    (re.compile(r'^Representative in State Legislature (\d+)(?:st|nd|rd|th) District$'),
     'State House'),
]


def header_cols(page):
    """[(x, name)] rotated candidate columns, wrapped bands joined."""
    bands = collections.defaultdict(list)
    for c in page.chars:
        if not c.get('upright'):
            bands[round(c['x0'])].append(c)
    out = []
    for x in sorted(bands):
        chars = sorted(bands[x], key=lambda c: c['top'])
        t = ''.join(c['text'] for c in chars)[::-1].strip()
        if t:
            out.append((x, t))
    merged = []
    for x, t in out:
        # a wrapped header's bands sit ~11pt apart; separate columns ~40pt+
        if merged and x - merged[-1][0] <= 20:
            merged[-1] = (merged[-1][0], merged[-1][1] + ' ' + t)
        else:
            merged.append((x, t))
    return merged


def map_office(title):
    m = re.search(r'\((DEM|REP)\)', title)
    party = m.group(1) if m else ''
    title = re.sub(r'\s*\((?:DEM|REP)\)\s*', ' ', title).strip()
    title = re.sub(r'\s*\(Vote for \d+\)$', '', title).strip()
    for pat, name in DISTRICT_PATTERNS:
        m = pat.match(title)
        if m:
            return name, m.group(1), party
    return title, '', party


class Contest:
    def __init__(self, title):
        self.title = title
        self.office, self.district, self.party = map_office(title)
        self.columns = []          # [(name, is_unresolved_write_in)]
        self.sums = None           # running column sums vs the county totals row
        self.pending = None        # current precinct: {'ED':.., 'AV':.., 'TOT':..}
        self.pending_label = None

    def flush(self, problems):
        """Emit the current precinct's Total row once all three method rows
        are in."""
        p = self.pending
        if not p:
            return
        prec, label = self.pending_label, self.pending_label
        try:
            if 'TOT' not in p:
                problems.append(f'{label} / {self.title}: no Total row yet')
                return
            ed, av, tot = p.get('ED'), p.get('AV'), p['TOT']
            n = len(self.columns)
            if any(v is None or len(v) != n for v in (ed, av, tot)):
                problems.append(f'{label} / {self.title}: incomplete rows')
                return
            for a, b, t in zip(ed, av, tot):
                if a + b != t:
                    problems.append(f'{label} / {self.title}: '
                                    f'{a} + {b} != {t}')
            if self.sums is None:
                self.sums = [0] * n
            for i, v in enumerate(tot):
                self.sums[i] += v
            for (name, is_wi), v in zip(self.columns, tot):
                if is_wi:
                    write_ins[(self.office, self.district, self.party)] += v
                else:
                    rows.append((prec, self.office, self.district,
                                 self.party, name, v))
        finally:
            self.pending = None
            self.pending_label = None

    def check_totals(self, totals, problems):
        if self.sums is None:
            return
        if len(totals) != len(self.columns):
            problems.append(f'{self.title}: totals row has {len(totals)} '
                            f'values, {len(self.columns)} columns')
            return
        for name, summed, printed in zip(
                [c[0] for c in self.columns], self.sums, totals):
            if summed != printed:
                problems.append(f'{self.title}: {name} precinct sum {summed} '
                                f'!= printed county total {printed}')


rows = []
write_ins = collections.defaultdict(int)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    problems = []
    contest = None      # Contest or None (local contests stop emission)
    label = []          # pending precinct-label lines
    totals_next = False  # 'Leelanau County' seen; numbers-only line is totals

    with pdfplumber.open(args.pdf) as pdf:
        for page in pdf.pages:
            cols = header_cols(page)
            band_raw = {c['text'] for c in page.chars if not c.get('upright')}
            title_acc = []
            for line in (l.strip() for l in
                         (page.extract_text() or '').splitlines()):
                if not line:
                    continue
                # contest titles end at '(Vote for N)'; fragments of a wrapped
                # title accumulate in title_acc
                if VOTE_FOR.search(line):
                    title_acc.append(line)
                    title = ' '.join(title_acc)
                    title_acc = []
                    if contest is not None:
                        contest.flush(problems)
                    if KEEP_PREFIX.match(title):
                        contest = Contest(title)
                        for _, name in cols:
                            is_wi = name == 'Unresolved Write-In'
                            contest.columns.append(
                                (name if is_wi else W_IN.sub('', name).strip(),
                                 is_wi))
                    else:
                        contest = None
                    continue
                if DATA.match(line):
                    if contest is None:
                        continue
                    meth, nums = DATA.match(line).groups()
                    if contest.pending_label is None:
                        contest.pending_label = re.sub(
                            r'\s+,', ',', ' '.join(label).strip())
                        label = []
                    if not contest.pending_label:
                        problems.append(f'{contest.title}: data row with no '
                                        f'precinct label')
                        contest.pending = None
                        continue
                    contest.pending = contest.pending or {}
                    key = {'Election Day': 'ED', 'AV Counting Boards': 'AV',
                           'Total': 'TOT'}[meth]
                    if key in contest.pending:
                        problems.append(f'{contest.pending_label} / '
                                        f'{contest.title}: duplicate {meth} row')
                    contest.pending[key] = [
                        int(v.replace(',', '')) for v in nums.split()]
                    if key == 'TOT':
                        contest.flush(problems)
                    continue
                if line.startswith('Leelanau County'):
                    if contest is not None:
                        contest.flush(problems)
                        rest = line[len('Leelanau County'):].strip()
                        if rest:
                            # totals on the same line: contest ends here
                            contest.check_totals(
                                [int(v.replace(',', ''))
                                 for v in rest.split()], problems)
                            contest = None
                        else:
                            totals_next = True  # totals wrap to the next line
                    continue
                if totals_next and re.match(r'^[\d,]+( [\d,]+)*$', line):
                    totals_next = False
                    if contest is not None:
                        contest.check_totals(
                            [int(v.replace(',', '')) for v in line.split()],
                            problems)
                        contest.sums = None
                        contest = None
                    continue
                totals_next = False
                # noise: party/header lines, mirrored rotated-name fragments,
                # page numbers, turnout-summary number rows
                if (line in ('DEM', 'REP', 'Precinct', 'County',
                             'Michigan - Total', 'Michigan')
                        or line in band_raw or line.isdigit()
                        or re.match(r'^[\d,]+( [\d,]+)*$', line)):
                    continue
                label.append(line)
            # a contest continues onto the next page
        if contest is not None:
            contest.flush(problems)

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