"""Parse the Benzie County 2024 primary wide precinct matrix PDF.

Layout: a contest-major grid whose columns are each township's vote methods
("Almira ED", "Almira EV", "Almira AVCB", ...). Each township is a single
precinct, so a column group is one precinct's election-day / early-voting /
absentee count. Two early-voting columns combine two jurisdictions ("Lake &
Platte EV", "Frankfort & Gilmore EV"): in township-level contests the value
belongs to the contest's own jurisdiction (ballot styles separate them), but
in countywide contests it mixes both precincts, so it is emitted under a
pseudo-precinct named after the column. A "TOTAL" column carries the Benzie
sum and Grand Traverse / Manistee / Wexford / Grand Total columns carry
neighboring counties' votes in shared districts; both are validated against
but not emitted.

The candidate header is letter-spaced garbage in the text layer, so column
geometry comes from clustering header characters by x (page 1 only); every
numeric cell is assigned to a column by its right edge (numbers are
right-aligned, so right edges are stable across digit widths).

Usage:
    .venv/bin/python src/benzie_matrix_parser.py <source.pdf> \
        --out 2024/counties/20240806__mi__primary__benzie__precinct.csv
"""
import argparse
import csv
import re
import sys

import pdfplumber

# Column label -> (precinct, method). Shared early-voting columns map to
# their own pseudo-precincts.
COLUMNS = {
    'Almira ED': ('Almira Township, Precinct 1', 'election_day'),
    'Almira EV': ('Almira Township, Precinct 1', 'early_voting'),
    'Almira AVCB': ('Almira Township, Precinct 1', 'absentee'),
    'Benzonia ED': ('Benzonia Township, Precinct 1', 'election_day'),
    'Benzonia EV': ('Benzonia Township, Precinct 1', 'early_voting'),
    'Benzonia AVCB': ('Benzonia Township, Precinct 1', 'absentee'),
    'Blaine ED': ('Blaine Township, Precinct 1', 'election_day'),
    'Blaine EV': ('Blaine Township, Precinct 1', 'early_voting'),
    'Colfax ED': ('Colfax Township, Precinct 1', 'election_day'),
    'Colfax EV': ('Colfax Township, Precinct 1', 'early_voting'),
    'Crystal Lake ED': ('Crystal Lake Township, Precinct 1', 'election_day'),
    'Crystal Lake EV': ('Crystal Lake Township, Precinct 1', 'early_voting'),
    'Crystal Lake AVCB': ('Crystal Lake Township, Precinct 1', 'absentee'),
    'Gilmore ED': ('Gilmore Township, Precinct 1', 'election_day'),
    'Homestead ED': ('Homestead Township, Precinct 1', 'election_day'),
    'Homestead EV': ('Homestead Township, Precinct 1', 'early_voting'),
    'Inland ED': ('Inland Township, Precinct 1', 'election_day'),
    'Inland EV': ('Inland Township, Precinct 1', 'early_voting'),
    'Joyfield ED': ('Joyfield Township, Precinct 1', 'election_day'),
    'Joyfield EV': ('Joyfield Township, Precinct 1', 'early_voting'),
    'Lake ED': ('Lake Township, Precinct 1', 'election_day'),
    'Lake & Platte EV': ('Lake & Platte EV', 'early_voting'),
    'Platte ED': ('Platte Township, Precinct 1', 'election_day'),
    'Weldon ED': ('Weldon Township, Precinct 1', 'election_day'),
    'Weldon EV': ('Weldon Township, Precinct 1', 'early_voting'),
    'City of Frankfort ED': ('City of Frankfort, Precinct 1', 'election_day'),
    'Frankfort & Gilmore EV': ('Frankfort & Gilmore EV', 'early_voting'),
}
# Townships whose early votes land in a shared column.
SHARED = {'Lake & Platte EV': ('Lake Township', 'Platte Township'),
          'Frankfort & Gilmore EV': ('City of Frankfort', 'Gilmore Township')}
COUNTY_OFFICES = {'United States Senator': 'U.S. Senate',
                  'Prosecuting Attorney': 'Prosecuting Attorney',
                  'Sheriff': 'Sheriff', 'Clerk': 'Clerk',
                  'Treasurer': 'Treasurer', 'Register of Deeds':
                  'Register of Deeds',
                  'County Road Commission': 'County Road Commission',
                  'Drain Commissioner': 'Drain Commissioner',
                  'Surveyor': 'Surveyor'}
TERM = re.compile(r'^(\d+ Year Term - )?Vote for \d+$')
# Proposal detail lines ("1.4 mills - 5 yrs", "$3.00 monthly - 5 yrs").
DESC = re.compile(r'^\$?\d| mills| yrs|monthly', re.I)
ROW = re.compile(r'^(.+?) ((?:\d+ )+)\d+$')


def cluster(values, gap):
    """Cluster ascending scalars, splitting where the gap exceeds `gap`."""
    out, cur = [], [values[0]]
    for v in values[1:]:
        if v - cur[-1] > gap:
            out.append(cur)
            cur = [v]
        else:
            cur.append(v)
    out.append(cur)
    return out


def header_labels(page):
    """(text, right edge) for each column header on page 1."""
    chars = sorted((c for c in page.chars if 45 < c['top'] < 136),
                   key=lambda c: c['x0'])
    labels = []
    cur = [chars[0]]
    for c in chars[1:]:
        if c['x0'] - cur[-1]['x0'] > 6.5:
            labels.append(cur)
            cur = [c]
        else:
            cur.append(c)
    labels.append(cur)
    out = []
    for g in labels:
        text = re.sub(r'\s+', ' ',
                      ''.join(c['text'] for c in sorted(g, key=lambda c:
                                                        c['x0']))).strip()
        out.append((text, max(c['x1'] for c in g)))
    # The letter-spaced "TOTAL" clusters one letter at a time.
    merged = []
    for text, x1 in out:
        if merged and len(merged[-1][0]) <= 2 and len(text) <= 2:
            merged[-1] = (merged[-1][0] + text, x1)
        else:
            merged.append((text, x1))
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    problems = []
    out_rows = []

    with pdfplumber.open(args.pdf) as pdf:
        page_words = [p.extract_words(x_tolerance=1.5) for p in pdf.pages]
    labels = header_labels(pdf.pages[0])
    texts = [t for t, _ in labels]
    if texts[:len(COLUMNS)] != list(COLUMNS):
        sys.exit(f'header columns {texts} do not start with the expected '
                 f'{len(COLUMNS)} data columns')

    # Numeric grid: cluster every numeric cell's right edge across the doc
    # (left-margin numerics from term/district/proposal-detail lines are not
    # columns). Grid columns pair one-to-one with header labels, nearest
    # right edge first; a label with no data (Wexford) simply goes unused.
    edges = sorted(w['x1'] for words in page_words for w in words
                   if w['text'].isdigit() and (w['x0'] + w['x1']) / 2 > 178)
    grid = [sum(g) / len(g) for g in cluster(edges, 6)]
    rights = [x for _, x in labels]
    # Align x-sorted labels to x-sorted grid columns monotonically (small
    # DP; a label with no data column, like Wexford, stays unassigned).
    n, m = len(grid), len(rights)
    INF = float('inf')
    # dp[j][i]: min cost to label clusters i.. with labels j..; rows are
    # labels (m+1), columns are clusters (n+1). Leaving a label unassigned
    # carries a penalty so clusters are claimed whenever plausible (a data
    # column with no values, like Wexford, is the only real skip).
    SKIP_PENALTY = 100
    dp = [[INF] * (n + 1) for _ in range(m + 1)]
    take = [[None] * (n + 1) for _ in range(m + 1)]
    for i in range(n + 1):
        dp[m][i] = 0  # no labels left: leftover clusters get none
    for j in range(m - 1, -1, -1):
        for i in range(n - 1, -1, -1):
            best, act = dp[j][i + 1], ('skip-cluster', i + 1)
            if dp[j + 1][i] + SKIP_PENALTY < best:
                best, act = dp[j + 1][i] + SKIP_PENALTY, ('skip-label', i)
            c = abs(grid[i] - rights[j]) + dp[j + 1][i + 1]
            if c < best:
                best, act = c, ('assign', i + 1)
            dp[j][i], take[j][i] = best, act
    col_map = [None] * n
    j = i = 0
    while j < m and i < n:
        act, ni = take[j][i]
        if act == 'assign':
            col_map[i] = texts[j]
            j += 1
        elif act == 'skip-label':
            j += 1
            continue
        i = ni
    got = [t for t in col_map if t in COLUMNS]
    if sorted(got) != sorted(COLUMNS):
        sys.exit(f'numeric grid {col_map} does not cover the data columns')

    # Contest state persists across pages; a non-numeric line always starts
    # a new contest or section.
    section = 'county'   # county | township | proposals
    office = None
    district = ''
    township = None      # active header inside the TOWNSHIP section
    prop_township = None  # active header inside Proposals

    for pno, words in enumerate(page_words):
        lines = {}
        for w in words:
            if pno == 0 and w['top'] < 137:
                continue  # the garbled column-header band
            lines.setdefault(round(w['top'] / 2), []).append(w)
        for key in sorted(lines):
            row = sorted(lines[key], key=lambda w: w['x0'])
            text = ' '.join(w['text'] for w in row)
            nums = [(w['text'], w['x1']) for w in row
                    if w['text'].isdigit() and (w['x0'] + w['x1']) / 2 > 180]
            if not nums:
                if TERM.match(text):
                    continue
                if text == 'TOWNSHIP':
                    section, township, prop_township = 'township', None, None
                    continue
                if text == 'Proposals':
                    section, prop_township = 'proposals', None
                    continue
                if section == 'township' and (
                        text.endswith(' Township')
                        or text.startswith('City of ')):
                    township = text
                    continue
                if section == 'proposals':
                    if text in ('COUNTY', 'Schools', 'Library'):
                        prop_township = None
                    elif text.endswith(' Township'):
                        prop_township = text
                    elif DESC.search(text):
                        pass
                    else:
                        office = (f'{prop_township} {text}' if prop_township
                                  else text)
                    continue
                if text == 'County':
                    continue
                m = re.match(r'^Rep (?:In|in) (Congress|State Legislature)'
                             r' .{0,3}? (\d+)(?:st|nd|rd|th) District$', text)
                if m:
                    office = 'U.S. House' if m.group(1) == 'Congress' \
                        else 'State House'
                    district = m.group(2)
                    continue
                if re.match(r'^\w+(st|nd|rd|th) District$', text):
                    district = text
                    continue
                if text in ('Delegate', 'Delegates', 'Delegate:',
                            'Delegates:'):
                    office = 'Delegate to County Convention'
                    continue
                if text.endswith(':'):
                    office = text[:-1]
                    continue
                if text == 'County Commissioner':
                    office, district = 'County Commissioner', ''
                    continue
                if text in COUNTY_OFFICES:
                    office, district = COUNTY_OFFICES[text], ''
                    continue
                problems.append(f'page {pno + 1}: unexpected line {text!r}')
                continue

            m = ROW.match(text)
            name = m.group(1).strip() if m else text.rsplit(' ', len(nums))[0]
            if name == 'Poll Book Totals' or TERM.match(text):
                continue
            cells, total, cross = {}, None, {}
            for val, x1 in nums:
                gi = min(range(len(grid)), key=lambda i: abs(grid[i] - x1))
                if abs(grid[gi] - x1) > 14:
                    problems.append(f'page {pno + 1} {name!r}: value {val} '
                                    f'at x1={x1:.0f} matches no column')
                    continue
                cl = col_map[gi]
                if cl in COLUMNS:
                    cells[cl] = int(val)
                elif cl == 'TOTAL':
                    total = int(val)
                elif cl is not None:
                    cross[cl] = int(val)
                else:
                    problems.append(f'page {pno + 1} {name!r}: unmapped '
                                    f'column at x1={x1:.0f} (value {val})')
            if not cells:
                continue
            if all(v == 0 for v in cells.values()):
                continue
            if total is not None and sum(cells.values()) != total:
                problems.append(f'page {pno + 1} {name!r}: columns '
                                f'{sum(cells.values())} != TOTAL {total}')
            if 'Grand Total' in cross and total is not None:
                if total + sum(v for k, v in cross.items()
                               if k != 'Grand Total') != cross['Grand Total']:
                    problems.append(f'page {pno + 1} {name!r}: Benzie '
                                    f'{total} + cross-county != Grand Total')

            # Party / candidate from the name suffix.
            party = ''
            m = re.match(r'^(.*?) - ([DR])$', name)
            if m:
                name = m.group(1).strip()
                party = 'DEM' if m.group(2) == 'D' else 'REP'
            else:
                m = re.match(r'^(.*?) - ([DR])?(?:- )?Write ?[Ii]n$', name)
                if m:
                    # "X - Write In", or "X - R- Write In" with the party.
                    name = 'Write-In'
                    if m.group(2):
                        party = 'DEM' if m.group(2) == 'D' else 'REP'
            # Resolve the office for this row.
            if section == 'township':
                if office == 'Delegate to County Convention':
                    ofc = f'{township}, Precinct 1 Delegate to County ' \
                          'Convention'
                else:
                    ofc = f'{township} {office}'
                dist = ''
                own = [c for c in cells
                       if COLUMNS[c][0] == f'{township}, Precinct 1']
                shared = [c for c in cells if c in SHARED
                          and township in SHARED[c]]
                if set(own + shared) != set(cells):
                    problems.append(f'page {pno + 1} {name!r}: township '
                                    f'cells outside {township}: '
                                    f'{sorted(set(cells) - set(own + shared))}')
            elif office == 'County Commissioner':
                ofc, dist = f'County Commissioner {district}'.strip(), ''
            elif office in ('U.S. House', 'State House'):
                ofc, dist = office, district
            else:
                ofc, dist = office, district
            use = own + shared if section == 'township' else list(cells)
            # One row per precinct: votes = method sum, breakdown = methods.
            by_prec = {}
            for cl in use:
                prec, method = COLUMNS[cl]
                d = by_prec.setdefault(prec, {})
                d[method] = d.get(method, 0) + cells[cl]
            for prec, methods in by_prec.items():
                vals = [methods.get(m, '') for m in
                        ('election_day', 'absentee', 'early_voting')]
                out_rows.append(['Benzie', prec, ofc, dist, party, name,
                                 sum(v for v in methods.values()), *vals])

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes', 'election_day', 'absentee',
                    'early_voting'])
        w.writerows(out_rows)
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()