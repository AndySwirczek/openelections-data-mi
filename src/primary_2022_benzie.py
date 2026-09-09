"""Parse Benzie County's Aug 2022 primary precinct-results PDF into a
per-county precinct CSV, verified against the certified county-level CENR.

Source (openelections-sources-mi/2022/primary/): 'Benzie MI 8.2.2022 Prec
Results.pdf' — a 7-page text PDF, precincts as columns and candidates as
rows. The rotated column headers extract mangled ('A lm ir a'), so columns
are derived by clustering the x centers of the candidate rows' numbers
(16 columns: 14 election-day precincts + 2 township AVC absentee columns +
TOTAL). AVC values merge into their township, matching the county's 2022
general file (13 precinct labels). Candidate rows end with the row total,
cross-checked against the precinct sum; district-restricted contests
(House 103) only occupy their own columns, so the check still holds.
'Name - D' / 'Name - R - Write In' (variously spaced) supply the party.

Only the contests the CENR certifies are emitted (Governor, U.S. House 1,
State Senate 32, State House 103/104); county/local offices, delegate rows
and proposals are skipped. The CENR's zero-vote write-ins (Adkisson,
Blackburn, McDonell) are absent or all-zero in the source.

Usage: .venv/bin/python src/primary_2022_benzie.py [--apply]
"""
import re
import sys
from collections import defaultdict

import pdfplumber

from primary_2022_common import CENR, one_space, verify, write

COUNTY = 'Benzie'
SOURCE = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
          "Benzie MI 8.2.2022 Prec Results.pdf")

# value columns left to right (x order), AVC merged into townships on emit;
# the last cluster is TOTAL
PRECINCTS = ['Almira', 'Almira-AVC', 'Benzonia', 'Benzonia-AVC', 'Blaine',
             'Colfax', 'Crystal Lake', 'Gilmore', 'Homestead', 'Inland',
             'Joyfield', 'Lake', 'Platte', 'Weldon', 'City of Frankfort']
MERGE = {'Almira-AVC': 'Almira', 'Benzonia-AVC': 'Benzonia'}

# (candidate, party) -> (office, district, party), from the CENR's Benzie rows
BY_CAND = {}
for (office, district, party, cand), _votes in CENR[COUNTY].items():
    BY_CAND[(cand, party)] = (office, district, party)

NUM = re.compile(r'\d[\d,]*')
CAND = re.compile(r'^(.*?)[- ] ?(D|R)(?:\s*-\s*Write\s*In|\s*-Write\s*In)?$',
                  re.I)
CONTESTS = [
    (re.compile(r'^Governor$'), lambda m: ('Governor', '')),
    (re.compile(r'^Rep In Congress - (\d+)(?:st|nd|rd|th)? District$'),
     lambda m: ('U.S. House', m.group(1))),
    (re.compile(r'^State Senator - (\d+)(?:st|nd|rd|th)? District$'),
     lambda m: ('State Senate', m.group(1))),
    (re.compile(r'^Rep in State Legislature ?-+ (\d+)'
                r'(?:st|nd|rd|th)? District$'),
     lambda m: ('State House', m.group(1))),
]


def lines_of(page):
    lines = {}
    for w in page.extract_words(x_tolerance=1.5):
        lines.setdefault(round(w['top']), []).append(w)
    return [(top, sorted(ws, key=lambda w: w['x0']))
            for top, ws in sorted(lines.items())]


def parse():
    rows = []
    skipped = defaultdict(int)
    problems = []
    contest = None
    with pdfplumber.open(SOURCE) as pdf:
        # the complete Governor DEM row defines the 16 value columns
        columns = None
        for page in pdf.pages:
            for top, ws in lines_of(page):
                text = one_space(' '.join(w['text'] for w in ws))
                if re.match(r'^Gretchen Whitmer', text):
                    columns = [round((w['x0'] + w['x1']) / 2)
                               for w in ws if NUM.fullmatch(w['text'])]
                    break
            if columns:
                break
        if columns is None or len(columns) != len(PRECINCTS) + 1:
            problems.append(f'columns from Whitmer row: {columns}')
            return rows, skipped, problems
        for page_no, page in enumerate(pdf.pages, 1):
            for top, ws in lines_of(page):
                text = one_space(' '.join(w['text'] for w in ws))
                if re.match(r'^(STATE|LEGISLATIVE|Poll Book Totals)', text) \
                        or text.startswith('Vote for'):
                    continue
                key = None
                for pat, fn in CONTESTS:
                    m = pat.match(text)
                    if m:
                        key = fn(m)
                        break
                if key:
                    contest = key
                    continue
                nums = [w for w in ws if NUM.fullmatch(w['text'])]
                if not nums:
                    continue
                head = one_space(' '.join(
                    w['text'] for w in ws if w['x1'] <= nums[0]['x0'] + 1))
                m = CAND.match(head)
                if not m or not m.group(1):
                    continue  # turnout rows ('B 6 5 3...', percentages)
                name, party = m.group(1).strip(), m.group(2).upper()
                emit(contest, name, party, ws, columns, page_no, rows,
                     skipped, problems)
    return rows, skipped, problems


def emit(contest, name, party, ws, columns, page_no, rows, skipped,
         problems):
    if contest is None:
        return
    values = defaultdict(int)
    total = None
    for w in ws:
        if not NUM.fullmatch(w['text']):
            continue
        x = round((w['x0'] + w['x1']) / 2)
        ci = min(range(len(columns)), key=lambda i: abs(columns[i] - x))
        if abs(columns[ci] - x) > 12:
            problems.append(f'page {page_no} {name!r}: value at x={x} '
                            f'off-column (nearest {columns[ci]})')
            continue
        if ci == len(columns) - 1:
            total = int(w['text'].replace(',', ''))
        else:
            label = PRECINCTS[ci]
            values[MERGE.get(label, label)] += int(w['text'].replace(',', ''))
    if sum(values.values()) != total:
        problems.append(f'page {page_no} {name!r}: precinct sum '
                        f'{sum(values.values())} != row total {total}')
        return
    key = BY_CAND.get((name, {'D': 'DEM', 'R': 'REP'}[party]))
    if key is None or key[:2] != contest:
        skipped[f'{name} ({party})'] += 1
        return
    for label, v in values.items():
        if v:
            rows.append((label, key[0], key[1], key[2], name, v))


def main(apply=False):
    rows, skipped, problems = parse()
    print(f'{COUNTY}: {len(rows)} rows; {sum(skipped.values())} local rows '
          f'skipped in {len(skipped)} candidates')
    problems += verify(COUNTY, rows, {})
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')
    if apply:
        write(COUNTY, rows)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)