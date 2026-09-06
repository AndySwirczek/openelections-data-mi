"""Parse "Statement of Votes Cast" PDFs with rotated candidate columns.

Layout (Livingston County): contest-major, but each contest's candidates are
90-degree-rotated column headers (extract_text mirrors them), followed by one
row per precinct. The header columns are [candidates..., Cast Votes,
Undervotes, Overvotes, Invalid Votes, Unresolved write-in votes, Ballots Cast
Early Voting, Ballots Cast Absentee Voting, Ballots Cast Precinct Voting,
Ballots Cast Total, Registered Voters, Turnout Percentage] — proposal
contests drop Invalid Votes and have Yes/No candidate columns. Precinct
labels wrap to two lines that straddle the vertically-centred numbers row
("Brighton Charter" / numbers / "Township, Precinct 1"), so data rows are
rebuilt from word coordinates: numbers join the nearest column x-band, label
words left of the first column within +/-8pt of the numbers' top. Each contest
ends with a "Totals" county row used for verification. The contest title
repeats on every page of the contest.

Candidate columns carry a single per-precinct total (no per-method split), so
emitted rows are votes-only. "Ballots Cast" rows come from the printed Cast
Votes column (candidate sum). Contests where no candidate ran print no
candidate columns; those pages also carry one extra unnamed column (a
contest-level ballot count, printed after Unresolved write-in votes), so rows
are assigned to columns by x-order with the extra cell tolerated. The report
aggregates ballot-style splits into base precinct labels (Livingston: 80
splits -> 65 precincts; no split suffixes appear).

Usage:
    .venv/bin/python src/rotated_column_sovc_parser.py <source.pdf> \
        --county 'Livingston' \
        --out 2026/counties/20260804__mi__primary__livingston__precinct.csv
"""
import argparse
import collections
import csv
import re
import sys

import pdfplumber

NUM = re.compile(r'^\d[\d,]*$|^[\d.]+%$')
TITLE_PARTY = re.compile(r'^(.*) - (Democratic|Republican|Nonpartisan)'
                         r'(?: - Vote for not more than \d+)?$')
WRITE_IN = re.compile(r'^(Write-?in)\b', re.I)
DISTRICT_PATTERNS = [
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$'),
     'U.S. House'),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th) District$'), 'State Senate'),
    (re.compile(r'^Representative in State Legislature (\d+)(?:st|nd|rd|th) District$'),
     'State House'),
]
OFFICE_EXACT = {'Governor': 'Governor', 'United States Senator': 'U.S. Senate'}
PARTY_CODES = {'Democratic': 'DEM', 'Republican': 'REP'}
# "Office Partial Term Ending ..., <Jurisdiction>" prints jurisdiction-last;
# the repo's convention is jurisdiction-first.
JURISDICTION_LAST = re.compile(r'^(.+ Partial Term Ending [^,]+), (.+)$')
AUX = ('Cast Votes', 'Undervotes', 'Overvotes', 'Invalid Votes',
       'Unresolved write-in votes', 'Early Voting Ballots Cast',
       'Absentee Voting Ballots Cast', 'Precinct Voting Ballots Cast',
       'Total Ballots Cast', 'Registered Voters', 'Turnout Percentage')


def header_cols(page):
    """[(x, text)] rotated column headers, clustered on x0."""
    bands = collections.defaultdict(list)
    for c in page.chars:
        if not c.get('upright'):
            bands[round(c['x0'])].append(c)
    cols = []
    for x in sorted(bands):
        chars = sorted(bands[x], key=lambda c: c['top'])
        cols.append((x, ''.join(c['text'] for c in chars)[::-1]))
    return cols


def page_title(page_text):
    """Title = the line(s) between the 'Run Date ...' banner and the
    'Precinct' column-header line."""
    lines = [l.strip() for l in page_text.splitlines()]
    run_date = next((i for i, l in enumerate(lines) if l.startswith('Run Date')), None)
    if run_date is None:
        return None
    for i in range(run_date + 1, len(lines)):
        if lines[i] == 'Precinct':
            return ' '.join(lines[run_date + 1:i])
    return None


class Contest:
    def __init__(self, title):
        self.title = title
        self.rows = {}   # precinct label -> [cell per column]
        self.totals = None
        self.pages = 0

    def finish(self, county, out_rows, problems):
        m = TITLE_PARTY.match(self.title)
        if not m:
            problems.append(f'untitled/unmapped contest: {self.title!r}')
            return
        office, district, party = map_office(m.group(1))
        party = '' if m.group(2) == 'Nonpartisan' else PARTY_CODES.get(m.group(2), party)
        cand_names = self.cand_names
        for precinct, cells in sorted(self.rows.items()):
            cands = cells[:len(cand_names)]
            cast = cells[len(cand_names)]
            total = sum(int(v.replace(',', '')) for v in cands)
            if total != int(cast.replace(',', '')):
                problems.append(f'{precinct} / {self.title}: candidate sum {total} '
                                f'!= Cast Votes {cast}')
            for name, v in zip(cand_names, cands):
                out_rows.append([county, precinct, office, district, party, name,
                                 int(v.replace(',', ''))])
            out_rows.append([county, precinct, office, district, party, 'Ballots Cast',
                             int(cast.replace(',', ''))])
        # County Totals row must equal the sum of the precinct rows.
        if self.totals is not None:
            for i, name in enumerate(cand_names):
                summed = sum(int(r[i].replace(',', '')) for r in self.rows.values())
                if summed != int(self.totals[i].replace(',', '')):
                    problems.append(f'{self.title}: Totals {name}={self.totals[i]} '
                                    f'!= precinct sum {summed}')
            summed = sum(int(r[len(cand_names)].replace(',', ''))
                         for r in self.rows.values())
            if summed != int(self.totals[len(cand_names)].replace(',', '')):
                problems.append(f'{self.title}: Totals Cast Votes '
                                f'{self.totals[len(cand_names)]} != precinct sum {summed}')


def map_office(title):
    for pat, name in DISTRICT_PATTERNS:
        m = pat.match(title)
        if m:
            return name, m.group(1), ''
    m = JURISDICTION_LAST.match(title)
    if m:
        title = f'{m.group(2)} {m.group(1)}'
    return OFFICE_EXACT.get(title, title), '', ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    out_rows = []
    problems = []
    contest = None
    with pdfplumber.open(args.pdf) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            if 'End of report' in text:
                continue
            cols = header_cols(page)
            title = page_title(text)
            if not cols or not title:
                problems.append(f'page with no column headers or title')
                continue
            if contest is None or contest.title != title:
                contest = Contest(title)
            contest.pages += 1
            contest.cand_names = [
                re.sub(r' \(W\)$', '', t).strip() for x, t in cols
                if t not in AUX]
            words = page.extract_words()
            max_x = cols[-1][0] + 12
            # Label words live left of x=105 (measured max label x1 = 95.9,
            # 'Precinct'); data cells start at x0 >= 124.6. A fixed boundary is
            # required: on candidate-less (empty contest) pages the first data
            # column's single-digit cells sit at x0 ~128, far left of the
            # rotated 'Cast Votes' header band.
            numw = [w for w in words if NUM.match(w['text'])
                    and 105 <= w['x0'] <= max_x]
            bands = collections.defaultdict(list)
            for w in numw:
                bands[round(w['top'])].append(w)
            # merge tops within 2pt
            tops = sorted(bands)
            merged = []
            for t in tops:
                if merged and t - merged[-1][-1] <= 2:
                    merged[-1].append(t)
                else:
                    merged.append([t])
            label_words = [w for w in words if w['x1'] < 105]
            col_x = [x for x, t in cols if t != 'Turnout Percentage']
            for group in merged:
                # Page-header noise rows carry at most 2 numeric cells.
                if len(bands[group[0]]) < 3:
                    continue
                top = sum(group) / len(group)
                cells = bands[group[0]]
                pct_cells = [w for w in cells if w['text'].endswith('%')]
                num_cells = sorted((w for w in cells
                                    if not w['text'].endswith('%')),
                                   key=lambda w: w['x0'])
                if len(pct_cells) != 1:
                    problems.append(f'row at top={top:.0f} has {len(pct_cells)} '
                                    f'percentage cells, expected 1')
                    continue
                # Cells right-align inside fixed column boxes, so x-order is
                # column order. Empty contests print one extra unnamed column
                # (a contest-level ballot count, after Unresolved write-ins);
                # it shifts only the un-emitted aux columns, so the first
                # len(col_x) cells are the columns we need.
                if len(num_cells) not in (len(col_x), len(col_x) + 1):
                    problems.append(f'row at top={top:.0f} has {len(num_cells)} '
                                    f'numeric cells, expected {len(col_x)}')
                    continue
                values = [w['text'] for w in num_cells[:len(col_x)]]
                label = ' '.join(w['text'] for w in sorted(
                    (w for w in label_words if abs(w['top'] - top) <= 8),
                    key=lambda w: (w['top'], w['x0'])))
                if label.startswith('Totals'):
                    contest.totals = values
                else:
                    if label in contest.rows:
                        problems.append(f'duplicate precinct row {label!r} '
                                        f'in {contest.title}')
                    contest.rows[label] = values
            if contest.totals is not None:
                contest.finish(args.county, out_rows, problems)
                contest = None
    if contest is not None:
        problems.append(f'contest {contest.title!r} ends without Totals row')

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate',
                    'votes'])
        for row in out_rows:
            w.writerow(['' if v is None else v for v in row])
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()