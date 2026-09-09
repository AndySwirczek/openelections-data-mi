"""Parse Antrim County's 2024 primary "Results per Precinct" report.

Layout (image-only PDF, from cached PaddleOCR markdown): one contest per
block, flowing across pages. A contest starts with a title line
"<office> (DEM)_(Vote for N)" (the underscore is the OCR's join of a line
break; trailing periods and missing spaces occur), then a header row
"Precinct | <candidates...>" (a "Write-in" column is a candidate), then one
row per relevant precinct, then a "Total" footer row whose per-column values
sum the precinct rows. A contest continues across page breaks with no
repeated header; the next title ends it.

No turnout data exists in the report, so the CSV carries only candidate
rows.

Usage:
    .venv/bin/python src/antrim_2024_parser.py \
        [--out 2024/counties/20240806__mi__primary__antrim__precinct.csv]
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sovc_flat_ocr_parser import flatten

CACHE = '/tmp/paddleocr_md/Antrim'
COUNTY = 'Antrim'
HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
          'votes']

TITLE = re.compile(r'^(.+?)\s*(?:\((DEM|REP|LIB|UST|GRN|NLP)\)\s*)?'
                   r'[_\s]*\(Vote for (\d+)\)\.?$')
# OCR sometimes renders the underlined title as LaTeX math.
LATEX_TITLE = re.compile(r'\\underline\{\\text\{(.+?)\}\}')
NOISE = re.compile(r'^(Results per Precinct|2024-08-16|file:///)')


def map_office(title):
    """Printed contest title -> (office, district)."""
    title = title.replace('_', ' ')
    m = re.match(r'^Representative in Congress '
                 r'(\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^Representative in State Legislature '
                 r'(\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'^County Commissioner for County Commissioner '
                 r'(\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'County Commissioner', m.group(1)
    m = re.match(r'^(.+) Precinct Delegate$', title)
    if m:
        return f'{m.group(1)} Delegate to County Convention', ''
    if title == 'United States Senator':
        return 'U.S. Senate', ''
    m = re.match(r'^(County .+?) for Antrim County$', title)
    if m:
        return m.group(1), ''
    return title, ''


def parse(problems):
    """State machine over the cached pages -> list of contest dicts."""
    contests = []
    cur = None

    def close(where):
        nonlocal cur
        if cur is None:
            return
        if not cur['total']:
            problems.append(f'{where}: contest {cur["title"]!r} has no '
                            f'Total row')
        contests.append(cur)
        cur = None

    for name in sorted(f for f in os.listdir(CACHE) if f.endswith('.md')):
        where = name
        for cells in flatten(os.path.join(CACHE, name)):
            if not any(c.strip() for c in cells):
                continue   # phantom empty rows
            line = cells[0]
            if NOISE.match(line):
                continue
            if len(cells) == 1:
                m = LATEX_TITLE.search(line)
                if m:
                    # "$ \underline{\text{Office (DEM) }} $(Vote for 1)."
                    line = m.group(1) + line[m.end():]
                line = line.replace('$', '').strip()
                m = TITLE.match(line)
                if m:
                    close(where)
                    cur = {'title': m.group(1).replace('_', ' ').strip(),
                           'party': m.group(2) or '', 'cands': [],
                           'rows': {}, 'total': []}
                    continue
                problems.append(f'{where}: stray line {line!r}')
                continue
            sq = line.strip().lower()
            if sq == 'precinct':
                if cur is None:
                    problems.append(f'{where}: header outside a contest')
                    continue
                if cur['cands']:
                    problems.append(f'{where}: contest {cur["title"]!r} has '
                                    f'a second header')
                cur['cands'] = ['Write-In' if c.strip() == 'Write-in' else c
                                for c in cells[1:]]
                continue
            if sq == 'total':
                if cur is None:
                    problems.append(f'{where}: Total row outside a contest')
                    continue
                cur['total'] = cells[1:]
                close(where)
                continue
            if cur is None:
                problems.append(f'{where}: data row {cells} outside a '
                                f'contest')
                continue
            prec = line.strip()
            if not prec:
                problems.append(f'{where}: data row with no label {cells}')
                continue
            if prec in cur['rows']:
                problems.append(f'{where}: duplicate precinct row {prec!r} '
                                f'in {cur["title"]!r}')
            vals = ['' if c in ('', '****') else c.replace(',', '')
                    for c in cells[1:]]
            if len(vals) < len(cur['cands']):
                # Trailing phantom cells: pad on the right.
                vals += [''] * (len(cur['cands']) - len(vals))
            elif len(vals) > len(cur['cands']):
                problems.append(f'{where}: {prec!r} has {len(vals)} values '
                                f'for {len(cur["cands"])} columns')
                vals = vals[:len(cur['cands'])]
            cur['rows'][prec] = vals
    close('end')
    return contests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out',
                    default='2024/counties/20240806__mi__primary__'
                            'antrim__precinct.csv')
    args = ap.parse_args()

    problems = []
    contests = parse(problems)
    rows_out = []
    for c in contests:
        office, district = map_office(c['title'])
        if len(c['total']) != len(c['cands']):
            problems.append(f'{c["title"]}: Total {c["total"]} vs '
                            f'{len(c["cands"])} columns')
            continue
        totals = [int(v.replace(',', '')) if v.strip() else None
                  for v in c['total']]
        sums = [0] * len(c['cands'])
        for prec, vals in c['rows'].items():
            for i, cand in enumerate(c['cands']):
                if not cand:
                    continue   # phantom column
                v = int(vals[i]) if vals[i].strip() else 0
                sums[i] += v
                if v:
                    rows_out.append([COUNTY, prec, office, district,
                                     c['party'], cand, v])
        for i, cand in enumerate(c['cands']):
            if not cand:
                continue
            if totals[i] is not None and sums[i] != totals[i]:
                problems.append(f'{c["title"]}: column {cand!r} precinct '
                                f'sum {sums[i]} != Total {totals[i]}')

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows_out)
    print(f'Wrote {len(rows_out)} rows to {args.out} '
          f'({len(problems)} problems)')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)


if __name__ == '__main__':
    main()