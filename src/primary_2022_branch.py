"""Parse Branch County's Aug 2022 primary results PDF into a per-county
precinct CSV, verified against the certified county-level CENR.

Source (openelections-sources-mi/2022/primary/): 'Branch MI Aug-22-Election-
Results.pdf' — a 12-page text PDF, one contest block per section: a title
line ('... (DEM) (Vote for 1)'), a two-line header whose candidate names may
wrap ('Andrew' over 'Watkins') plus a 'Write-in' column, one data row per
precinct and a closing Total row. Header columns are built by clustering the
header words' x centers across the header lines; each data row's numbers map
to the nearest column, and the Total row cross-checks every column's sum.

Only the contests the CENR certifies are emitted (Governor, U.S. House 5,
State Senate 17, State House 35); county/local offices are skipped. The
lumped 'Write-in' columns become 'Write-In' rows (the CENR carries no
Branch write-in rows). The CENR's zero-vote candidates the source omits
(Adkisson, Blackburn, Trouten, Ferszt) pass as zero-vote omissions.
Precinct labels drop the comma before 'Precinct' to match the county's
2022 general file ('Algansee Township Precinct 1').

Usage: .venv/bin/python src/primary_2022_branch.py [--apply]
"""
import re
import sys
from collections import defaultdict

import pdfplumber

from primary_2022_common import CENR, one_space, verify, write

COUNTY = 'Branch'
SOURCE = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
          "Branch MI Aug-22-Election-Results.pdf")

# contest title -> (office, district, party)
CONTESTS = [
    (re.compile(r'^Governor \((DEM|REP)\) \(Vote for \d+\)$'),
     lambda m: ('Governor', '', m.group(1))),
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th)? District'
                r' \((DEM|REP)\) \(Vote for \d+\)$'),
     lambda m: ('U.S. House', m.group(1), m.group(2))),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th)? District'
                r' \((DEM|REP)\) \(Vote for \d+\)$'),
     lambda m: ('State Senate', m.group(1), m.group(2))),
    (re.compile(r'^Rep in State Legislature (\d+)(?:st|nd|rd|th)? District'
                r' \((DEM|REP)\) \(Vote for \d+\)$'),
     lambda m: ('State House', m.group(1), m.group(2))),
]

NUM = re.compile(r'\d[\d,]*')


def lines_of(page):
    lines = {}
    for w in page.extract_words(x_tolerance=1.5):
        lines.setdefault(round(w['top']), []).append(w)
    return [(top, sorted(ws, key=lambda w: w['x0']))
            for top, ws in sorted(lines.items())]


def header_columns(header_lines):
    """[(x_center, kind, value)] from the header words between a contest
    title and the first data row; wrapped names merge by x overlap."""
    words = [w for _, ws in header_lines for w in ws
             if w['text'] != 'Precinct']
    clusters = []
    for w in sorted(words, key=lambda w: (w['top'], w['x0'])):
        for cl in clusters:
            # wrapped fragments overlap the name's x range
            if w['x0'] <= cl['x1'] + 5 and w['x1'] >= cl['x0'] - 5:
                cl['words'].append(w)
                cl['x0'] = min(cl['x0'], w['x0'])
                cl['x1'] = max(cl['x1'], w['x1'])
                break
        else:
            clusters.append({'x0': w['x0'], 'x1': w['x1'], 'words': [w]})
    cols = []
    for cl in sorted(clusters, key=lambda c: c['x0']):
        name = one_space(' '.join(w['text'] for w in
                                  sorted(cl['words'],
                                         key=lambda w: (w['top'], w['x0']))))
        if re.fullmatch(r'Write-?in', name, re.I):
            cols.append(((cl['x0'] + cl['x1']) / 2, 'writein', None))
        else:
            cols.append(((cl['x0'] + cl['x1']) / 2, 'cand', name))
    return cols


def parse():
    rows = []
    skipped = defaultdict(int)
    problems = []
    write_ins = defaultdict(int)
    with pdfplumber.open(SOURCE) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            contest = None
            header = None            # header lines awaiting the first data row
            columns = None           # built from the header once data starts
            col_sums = None
            def close_block():
                if col_sums is None or columns is None:
                    return
                for col in columns:
                    got = col_sums.get(col[0], 0)
                    printed = col_sums.get(('printed', col[0]))
                    if printed is not None and got != printed:
                        problems.append(
                            f'page {page_no} {contest} '
                            f'{col[2] or "Write-In"!r}: precinct sum {got} '
                            f'!= Total {printed}')
            for top, ws in lines_of(page):
                text = one_space(' '.join(w['text'] for w in ws))
                nums = [w for w in ws if NUM.fullmatch(w['text'])]
                key = None
                for pat, fn in CONTESTS:
                    m = pat.match(text)
                    if m:
                        key = fn(m)
                        break
                if key:
                    close_block()
                    contest, header, columns, col_sums = key, [], None, None
                    continue
                if contest is None:
                    continue
                if not nums:
                    if columns is None:
                        header.append((top, ws))
                    continue
                if columns is None:
                    columns = header_columns(header)
                    col_sums = defaultdict(int)
                # split the row at the first value column: the label may
                # itself end in a number ('... Precinct 1')
                boundary = columns[0][0] - 15
                label = one_space(' '.join(
                    w['text'] for w in ws
                    if (w['x0'] + w['x1']) / 2 < boundary))
                values = [w for w in nums
                          if (w['x0'] + w['x1']) / 2 >= boundary]
                if label == 'Total':
                    for w in values:
                        x = (w['x0'] + w['x1']) / 2
                        col = min(columns, key=lambda c: abs(c[0] - x))
                        col_sums[('printed', col[0])] = \
                            int(w['text'].replace(',', ''))
                    close_block()
                    contest = None
                    continue
                for w in values:
                    x = (w['x0'] + w['x1']) / 2
                    col = min(columns, key=lambda c: abs(c[0] - x))
                    if abs(col[0] - x) > 15:
                        problems.append(f'page {page_no} {label!r}: value '
                                        f'{w["text"]!r} off-column')
                        continue
                    v = int(w['text'].replace(',', ''))
                    col_sums[col[0]] += v
                    if not v:
                        continue
                    label = label.replace(', Precinct', ' Precinct')
                    if col[1] == 'writein':
                        rows.append((label, contest[0], contest[1],
                                     contest[2], 'Write-In', v))
                        write_ins[contest] += v
                    else:
                        rows.append((label, contest[0], contest[1],
                                     contest[2], col[2], v))
            close_block()
    return rows, skipped, write_ins, problems


def main(apply=False):
    rows, skipped, write_ins, problems = parse()
    print(f'{COUNTY}: {len(rows)} rows; {sum(skipped.values())} rows skipped')
    problems += verify(COUNTY, rows, write_ins)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')
    if apply:
        write(COUNTY, rows)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)