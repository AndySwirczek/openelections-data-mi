"""Parse Alcona County's Aug 2022 primary spreadsheet PDF into a per-county
precinct CSV, verified against the certified county-level CENR.

Source (openelections-sources-mi/2022/primary/): 'Alcona MI 2022-Primary-
Spreadsheetofficalfinal.pdf' — a hand-built 5-page landscape sheet, one page
per party ballot (REPUBLICIAN [sic] / DEMOCRATIC), precincts as rows and
candidates as rotated columns whose text extracts reversed. Columns are
identified by reversing each rotated header and matching the candidate name
+ page party against the CENR's Alcona rows ('Geyer W. Balog', 'Cam
Cavitt'...), so the sheet's own contest-label bands (which misplace HD106's
Larry Hull under Road Comm) are never consulted; unmapped columns (turnout,
local offices, delegates) drop out. Data rows are 'Precinct N1 N2 ...' with
numbers assigned to columns by x position; the printed Totals row cross-
checks every mapped column's accumulated sum. 'City Harrisville' becomes
'City of Harrisville' per the county's 2022 general file.

Only the contests the CENR certifies are emitted (Governor, U.S. House 1,
State Senate 36, State House 106). The CENR's zero-vote write-ins (Adkisson,
Blackburn, McDonell) are carried as '/ write-in' columns.

Usage: .venv/bin/python src/primary_2022_alcona.py [--apply]
"""
import re
import sys
from collections import defaultdict

import pdfplumber

from primary_2022_common import CENR, one_space, verify, write

COUNTY = 'Alcona'
SOURCE = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
          "Alcona MI 2022-Primary-Spreadsheetofficalfinal.pdf")

# (candidate, party) -> (office, district, party), from the CENR's Alcona rows
BY_CAND = {}
for (office, district, party, cand), _votes in CENR[COUNTY].items():
    BY_CAND[(cand, party)] = (office, district, party)

NUM = re.compile(r'\d[\d,]*')


def lines_of(page):
    """Words grouped into lines; the hand-built sheet puts some precinct
    names and their numbers 1pt apart, so tops within 3pt are one line."""
    words = sorted(page.extract_words(x_tolerance=1.5),
                   key=lambda w: (w['top'], w['x0']))
    lines = []
    for w in words:
        if lines and w['top'] - lines[-1][0] <= 3:
            lines[-1][1].append(w)
        else:
            lines.append((w['top'], [w]))
    return [(top, sorted(ws, key=lambda w: w['x0'])) for top, ws in lines]


def parse():
    rows = []
    skipped = defaultdict(int)
    problems = []
    sums = defaultdict(int)   # (page, column name) -> accumulated votes
    printed = {}              # (page, column name) -> Totals row value
    with pdfplumber.open(SOURCE) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            party = None
            # rotated column headers: reversed char streams, one per x
            columns = {}
            rot = defaultdict(list)
            for c in page.chars:
                if not c['upright']:
                    rot[round(c['x0'])].append(c)
            for x0, cs in rot.items():
                text = ''.join(c['text'] for c in sorted(cs, key=lambda c: c['top']))[::-1]
                name = one_space(re.sub(r'/\s*write-?in\s*$', '', text, flags=re.I))
                columns[x0] = name
            # party banner (upright, near the page top)
            for top, ws in lines_of(page):
                text = one_space(' '.join(w['text'] for w in ws))
                if 'PARTY' in text.upper():
                    party = 'REP' if 'REPUB' in text.upper() else 'DEM'
                    break
            if party is None:
                continue  # delegate/proposal pages
            mapped = {}   # column name -> (office, district, party, cand)
            for x0, name in columns.items():
                key = BY_CAND.get((name, party))
                if key:
                    mapped[x0] = key
                else:
                    skipped[f'{name} ({party})'] += 1
            for top, ws in lines_of(page):
                text = one_space(' '.join(w['text'] for w in ws))
                nums = [w for w in ws if NUM.fullmatch(w['text'])]
                if not nums:
                    continue
                if not mapped:
                    continue  # page has no CENR-carried columns
                label = one_space(' '.join(
                    w['text'] for w in ws if w['x1'] <= nums[0]['x0'] + 1))
                if label == 'Totals':
                    for w in nums:
                        x = round((w['x0'] + w['x1']) / 2)
                        col = min(mapped, key=lambda cx: abs(cx - x))
                        if abs(col - x) < 12:
                            printed[(page_no, columns[col])] = \
                                int(w['text'].replace(',', ''))
                    continue
                for w in nums:
                    x = round((w['x0'] + w['x1']) / 2)
                    col = min(mapped, key=lambda cx: abs(cx - x))
                    if abs(col - x) >= 12:
                        continue  # turnout / local columns
                    v = int(w['text'].replace(',', ''))
                    office, district, p = mapped[col]
                    cand = columns[col]
                    sums[(page_no, cand)] += v
                    if v:
                        rows.append((label, office, district, p, cand, v))
    for (page_no, cand), want in printed.items():
        if sums.get((page_no, cand), 0) != want:
            problems.append(f'page {page_no} {cand!r}: precinct sum '
                            f'{sums.get((page_no, cand), 0)} != Totals {want}')
    return rows, skipped, problems


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