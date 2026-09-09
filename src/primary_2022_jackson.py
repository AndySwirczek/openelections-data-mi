"""Parse Jackson County's Aug 2022 primary "Precinct Results" (Statement of
Votes Cast) PDF into a per-county precinct CSV, verified against the
certified county-level CENR.

Layout: contest-major SOVC where each page carries a registration summary
table (rotated 'Times Cast' / 'Registered Voters' headers) and, alongside or
instead of it, the current contest's table (rotated candidate headers plus
'Total Votes' and a wrapped 'Unresolved Write-In'). A contest's columns are
split into column groups that alternate pages, each group repeating the same
slice of precinct labels, so a precinct's full row is assembled by joining
per label with values keyed by the rotated header text. When the contest
table shares the page with the registration table it sits on the right
(labels x0 >= 390); alone it sits on the left (labels x1 < min(header x) -
20). Row labels wrap around the numbers ('City of Jackson, Ward 1,' /
numbers / 'Precinct 1'). County totals print per column group as
'Jackson County Michigan - Total' / 'County - Total' rows ('Cumulative'
rows repeat them with zeros and are skipped). Only the CENR-carried
partisan contests are emitted ('Rep in Congress', the DEM report's
'Representative in State 46th District' short form included).

Usage:
    .venv/bin/python src/primary_2022_jackson.py <source.pdf> \
        --out /tmp/jackson22.csv
"""
import argparse
import collections
import re
import sys

import pdfplumber

from primary_2022_common import CENR, verify, write

COUNTY = 'Jackson'
TITLE = re.compile(r'^(.*\S) \((?:DEM|REP)\) \(Vote for \d+\)$')
KEEP = [
    (re.compile(r'^Governor$'), 'Governor'),
    (re.compile(r'^Rep(?:resentative)? in Congress (\d+)(?:st|nd|rd|th) District$'),
     'U.S. House'),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th) District$'),
     'State Senate'),
    (re.compile(r'^Representative in State(?: Legislature)? '
                r'(\d+)(?:st|nd|rd|th) District$'), 'State House'),
]
NUM = re.compile(r'^\d[\d,]*$')
TOTAL_KEY = 'Total Votes'
WRITEIN_KEY = 'Unresolved Write-In'
# a qualified write-in candidate's wrapped header ends in 'Write-in'
WRITEIN_CAND = re.compile(r'\s*write-?in$', re.I)
COUNTY_LABEL = re.compile(r'^(?:Jackson County Michigan|County)')


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
        # wrapped headers continue 21-22pt to the right ('Elizabeth A.
        # Ferszt' / 'Write-in'); distinct columns sit >= 29pt apart
        if merged and x - merged[-1][0] <= 25:
            merged[-1][1] += ' ' + t
        else:
            merged.append([x, t])
    return [(x, t) for x, t in merged]


class Contest:
    def __init__(self, title, party):
        self.title, self.party = title, party
        self.office, self.district = map_office(title)
        self.rows = {}        # label -> {header text: value}
        self.totals = {}      # header text -> county total
        self.page_bands = []  # this page's [(x, header text)]

    def page_columns(self, page):
        """This page's contest bands (skipping the registration table's)."""
        self.page_bands = [(x, t) for x, t in bands_for(page)
                           if t not in ('Times Cast', 'Registered Voters')]
        return bool(self.page_bands)

    def add_cells(self, label, values, problems):
        """Merge one row group's values into the label's row (a column
        group's second visit to a label is a duplicate)."""
        cells = self.rows.setdefault(label, {})
        for (x, text), v in zip(self.page_bands, values):
            if text in cells and cells[text] != v:
                problems.append(f'{self.title}: {label!r}: {text} printed '
                                f'{cells[text]} and {v}')
            cells[text] = v

    def finish(self, problems):
        if self.office is None or not self.rows:
            return
        key = (self.office, self.district, self.party)
        cands = [c for (o, d, p, c) in CENR[COUNTY] if (o, d, p) == key]
        cvotes = {c: v for (o, d, p, c), v in CENR[COUNTY].items()
                  if (o, d, p) == key}
        # the report prints 'James Johnson, Jr.' where the CENR carries
        # 'James Johnson Jr.', and wraps 'Tina Bednarski-Lynch' as
        # 'Tina Bednarski- Lynch'
        norm = lambda s: re.sub(r'\s*write-?in$', '',
                                re.sub(r'-\s+', '-', s.replace(',', '')),
                                flags=re.I)
        emitted = 0
        for label, cells in sorted(self.rows.items()):
            by_norm = {norm(t): v for t, v in cells.items()}
            missing = [c for c in cands if norm(c) not in by_norm]
            if any(cvotes[c] for c in missing):
                problems.append(f'{self.title}: {label!r} missing columns '
                                f'{missing}')
                continue
            total = cells.get(TOTAL_KEY)
            # write-in candidates' columns are excluded from the report's
            # Total Votes (their countywide votes are 1 and 10)
            got = sum(v for t, v in cells.items()
                      if norm(t) in {norm(c) for c in cands}
                      and not WRITEIN_CAND.search(t))
            if total is None or got != total:
                problems.append(f'{self.title}: {label!r} candidate sum '
                                f'{got} != Total Votes {total}')
                continue
            for name in cands:
                if norm(name) in by_norm:
                    rows.append((label, self.office, self.district,
                                 self.party, name, by_norm[norm(name)]))
            write_ins[key] += cells.get(WRITEIN_KEY, 0)
            emitted += 1
        if emitted < len(self.rows):
            problems.append(f'{self.title}: {len(self.rows) - emitted} of '
                            f'{len(self.rows)} precinct rows incomplete')
        # county totals must equal the sums of the joined rows (zero-vote
        # qualified write-ins sit only in the lumped Unresolved Write-In
        # column, so their absence from the sums is expected)
        sums = collections.Counter()
        for label, cells in self.rows.items():
            for text, v in cells.items():
                sums[text] += v
        for text, printed in self.totals.items():
            if text in sums and sums[text] != printed:
                msg = (f'{self.title}: {text} precinct sum '
                       f'{sums[text]} != printed county total {printed}')
                # the county-total row allocates resolved write-ins between
                # the write-in candidate columns and 'Unresolved Write-In'
                # differently than the precinct rows do (the precinct sums
                # are the ones that match the certified CENR)
                if WRITEIN_CAND.search(text) or text == WRITEIN_KEY:
                    notes.append('(source write-in allocation) ' + msg)
                else:
                    problems.append(msg)


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

    with pdfplumber.open(args.pdf) as pdf:
        for pageno, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ''
            # a contest title line anywhere on the page starts the contest
            # the page's rows belong to
            for line in (l.strip() for l in text.splitlines()):
                m = TITLE.match(line)
                if m and not line.startswith('Precinct'):
                    if contest is not None:
                        contest.finish(problems)
                    office, _ = map_office(m.group(1))
                    # delegate/proposal sections (proposal titles carry no
                    # party marker) aren't carried by the CENR; skip their
                    # pages rather than let their rows pile up
                    contest = (Contest(m.group(1),
                                       'DEM' if '(DEM)' in line else 'REP')
                               if office is not None else None)
                    break
            if contest is None or not contest.page_columns(page):
                continue
            words = page.extract_words()
            bands = contest.page_bands
            table_left = min(x for x, _ in bands)
            label_min = 390 if any(t == 'Times Cast'
                                   for _, t in bands_for(page)) else 0
            # contest cells: numbers right-aligned in each column; group them
            # into rows by top (a row's numbers sit within ~2pt) and assign
            # the row's values to the page's bands left to right
            cellw = [w for w in words if NUM.match(w['text']) and w['top'] > 40
                     and w['x0'] >= table_left - 40
                     and w['x1'] >= table_left - 5]  # drop label digits
            num_rows = []
            for w in sorted(cellw, key=lambda w: (w['top'], w['x0'])):
                if num_rows and w['top'] - num_rows[-1][0] <= 2.5:
                    num_rows[-1][1].append(w)
                else:
                    num_rows.append([w['top'], [w]])
            # label-zone words attach to the nearest row (fragments sit on
            # the values line and ~11pt below it; rows are ~25pt apart)
            labw = [w for w in words if w['x1'] < table_left - 14
                    and w['x0'] >= label_min and w['top'] > 40]
            labels = {}  # row index -> words
            for w in labw:
                # 12pt: fragments sit on the values line and ~11pt below it;
                # the county-table header words above row 1 sit ~14pt up
                best, bd = None, 12
                for i, (top, _) in enumerate(num_rows):
                    d = abs(w['top'] - top)
                    if d < bd:
                        best, bd = i, d
                if best is not None:
                    labels.setdefault(best, []).append(w)
            for i, (top, g) in enumerate(num_rows):
                if len(g) != len(bands):
                    problems.append(f'{contest.title}: row at top={top} has '
                                    f'{len(g)} cells for {len(bands)} '
                                    f'headers')
                    continue
                values = [int(w['text'].replace(',', ''))
                          for w in sorted(g, key=lambda w: w['x1'])]
                label = ' '.join(w['text'] for w in sorted(
                    labels.get(i, []), key=lambda w: (w['top'], w['x0'])))
                label = re.sub(r'\s+', ' ', label).strip()
                if COUNTY_LABEL.match(label):
                    for (x, t), v in zip(bands, values):
                        if t in contest.totals and contest.totals[t] != v:
                            # the two total rows on a column-group's last
                            # page can disagree by a vote or two ('Jackson
                            # County Michigan - Total' 16,957 vs 'County -
                            # Total' 16,958); the later 'County - Total' row
                            # is the one that matches the CENR
                            notes.append(f'{contest.title}: county total '
                                         f'{t} printed '
                                         f'{contest.totals[t]} and {v}')
                        contest.totals[t] = v
                elif 'Cumulative' in label:
                    continue
                elif label:
                    contest.add_cells(label, values, problems)
                else:
                    problems.append(f'{contest.title}: row at top='
                                    f'{round(top)} has no label')
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