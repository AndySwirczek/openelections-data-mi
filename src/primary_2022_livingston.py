"""Parse Livingston County's Aug 2022 primary "Statement of Votes Cast
Official Results Report" PDF into a per-county precinct CSV, verified against
the certified county-level CENR.

The rotated-column layout (see rotated_column_sovc_parser.py, its 2026
sibling) with the 2022 report's differences:
- titles print '<office> - <Democratic|Republican|Nonpartisan> Party - Vote
  for not more than N', on the line right after the 'Run Date' banner (the
  'Precinct' column-header line sits above the banner, not below it);
- the aux columns are [Cast Votes, Undervotes, Overvotes, Invalid Votes,
  Rejected write-in votes, Absentee Voting Ballots Cast, Election Day Voting
  Ballots Cast, Total Ballots Cast, Registered Voters, Turnout Percentage]
  (no Early Voting, no Unresolved write-ins);
- wrapped labels run wider (single-line 'Deerfield Township, Precinct 1'
  ends at x=125.2), so the label/data boundary moves from x=105 to x=130
  (data cells start at x0=160).

Usage:
    .venv/bin/python src/primary_2022_livingston.py <source.pdf> \
        --county 'Livingston' --out /tmp/livingston22.csv
"""
import argparse
import collections
import csv
import re
import sys

import pdfplumber

NUM = re.compile(r'^\d[\d,]*$|^[\d.]+%$')
TITLE_PARTY = re.compile(r'^(.*) - (Democratic|Republican|Nonpartisan)'
                         r'(?: Party)?(?: - Vote for not more than \d+)?$')
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
       'Rejected write-in votes', 'Absentee Voting Ballots Cast',
       'Election Day Voting Ballots Cast', 'Total Ballots Cast',
       'Registered Voters', 'Turnout Percentage')


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
    'Precinct' column-header line. The 2022 report prints the title on the
    line right after 'Run Date' with the 'Precinct' header line above the
    banner, so a title also stands alone there."""
    lines = [l.strip() for l in page_text.splitlines()]
    run_date = next((i for i, l in enumerate(lines) if l.startswith('Run Date')), None)
    if run_date is None:
        return None
    nxt = lines[run_date + 1] if run_date + 1 < len(lines) else ''
    if nxt and nxt != 'Precinct':
        return nxt
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
        self.cand_names = []

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
    else:
        # 2024 titles print township offices jurisdiction-last with a comma
        # ('Clerk, Brighton Charter Township'); the repo's convention is
        # jurisdiction-first ('Brighton Charter Township Clerk').
        m = re.match(r'^(Clerk|Treasurer|Supervisor|Trustee), (.+)$', title)
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
            header_texts = [t for _, t in cols]
            # A contest with more candidates than fit beside the aux columns
            # continues onto a second page that carries only 'Total Ballots
            # Cast' / 'Registered Voters' / 'Turnout Percentage' (Genoa and
            # Marion township Trustee races); its rows extend the first
            # page's. Such a page has no candidate columns and no
            # 'Cast Votes' (which empty contests do print).
            continuation = ('Cast Votes' not in header_texts
                            and all(t in AUX for t in header_texts))
            if contest is None or contest.title != title:
                contest = Contest(title)
            contest.pages += 1
            if not continuation:
                contest.cand_names = [
                    re.sub(r' \(W\)$', '', t).strip() for x, t in cols
                    if t not in AUX]
            words = page.extract_words()
            # +20 (not +12): a wide contest's last Precinct Voting cell can
            # sit 12.2pt right of its header band (Genoa Precinct 8's '62').
            max_x = cols[-1][0] + 20
            # Label words live left of x=124 (2022's widest label,
            # 'Township,', ends at x=115.2; data cells start at x0=160).
            # 'Precinct'); data cells start at x0 >= 124.6. A fixed boundary is
            # required: on candidate-less (empty contest) pages the first data
            # column's single-digit cells sit at x0 ~128, far left of the
            # rotated 'Cast Votes' header band.
            numw = [w for w in words if NUM.match(w['text'])
                    and 130 <= w['x0'] <= max_x]
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
            label_words = [w for w in words if w['x1'] < 130]
            # Data rows start below the 'Precinct' column header; the
            # registration banner's '47293 of 167529 = 28.23%' row sits above
            # it and matches a continuation page's 2-numbers-plus-% shape.
            prec_top = min((w['top'] for w in words
                            if w['text'] == 'Precinct' and w['x1'] < 130),
                           default=0)
            col_x = [x for x, t in cols if t != 'Turnout Percentage']
            for group in merged:
                if group[0] < prec_top:
                    continue
                # Page-header noise rows carry at most 2 numeric cells.
                if len(bands[group[0]]) < 3:
                    continue
                top = sum(group) / len(group)
                cells = bands[group[0]]
                pct_cells = [w for w in cells if w['text'].endswith('%')]
                num_cells = sorted((w for w in cells
                                    if not w['text'].endswith('%')),
                                   key=lambda w: w['x0'])
                # A wide contest's first page drops the Turnout Percentage
                # column (and its header), so its rows print no % cell.
                if len(pct_cells) != (0 if 'Turnout Percentage'
                                      not in header_texts else 1):
                    problems.append(f'row at top={top:.0f} has {len(pct_cells)} '
                                    f'percentage cells, expected '
                                    f'{1 if "Turnout Percentage" in header_texts else 0}')
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
                    if continuation and contest.totals is not None:
                        contest.totals += values
                    else:
                        contest.totals = values
                elif continuation:
                    # The second page's three cells (Total Ballots Cast,
                    # Registered Voters) extend the same precinct's first-page
                    # row; its label must already be present.
                    if label not in contest.rows:
                        problems.append(f'continuation row {label!r} has no '
                                        f'first-page row in {contest.title}')
                    else:
                        contest.rows[label] += values
                else:
                    if label in contest.rows:
                        problems.append(f'duplicate precinct row {label!r} '
                                        f'in {contest.title}')
                    contest.rows[label] = values
            # A truncated (wide-contest) page's Totals row only starts the
            # accumulated totals; the contest finishes on the continuation
            # page that carries the full column set.
            if (contest.totals is not None
                    and 'Turnout Percentage' in header_texts):
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