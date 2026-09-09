"""Parse Crawford County's Aug 2022 primary precinct-results PDF into a
per-county precinct CSV, verified against the certified county-level CENR.

Source (openelections-sources-mi/2022/primary/): 'Crawford County Aug 2022
Primary Precinct Results.pdf' — a 6-page text PDF, precincts as columns and
candidates as rows. The first data line carries the column ids ('101a 101b
201 302 304 305 306 307 401 501 601 701 BLT Total'); values are missing for
 precincts with no votes, so numbers are assigned to columns by x position.
Candidate rows end with a party letter (D/R, WI for write-ins) and the last
number is the row total (cross-checked against the precinct sum). Page 6 is
a legend expanding the ids; labels follow the county's 2022 general file,
with 101b as the (RAPS) half of Beaver Creek Township and 501 as the (CASD)
Maple Forest precinct (its (GAPS) half is a separate 502 not in this report).

Only the contests the CENR certifies are emitted (Governor, U.S. House 1,
State Senate 36, State House 105); county/local offices, delegate rows and
proposals are skipped. The CENR's zero-vote write-in candidates (Adkisson,
Craig, Blackburn, McDonell) are absent from the source.

Usage: .venv/bin/python src/primary_2022_crawford.py [--apply]
"""
import re
import sys
from collections import defaultdict

import pdfplumber

from primary_2022_common import CENR, one_space, verify, write

COUNTY = 'Crawford'
SOURCE = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
          "Crawford County Aug 2022 Primary Precinct Results.pdf")

# (candidate, party) -> (office, district, party), from the CENR's Crawford
# rows, keyed also by the source spellings
BY_CAND = {}
for (office, district, party, cand), _votes in CENR[COUNTY].items():
    BY_CAND[(cand, party)] = (office, district, party)

# column id from the header line -> precinct label (2022 general convention)
PRECINCTS = {'101a': 'Beaver Creek Twsp (CASD)',
             '101b': 'Beaver Creek Twsp (RAPS)',
             '201': 'Frederic Twsp',
             '302': 'Grayling Twsp - 302',
             '304': 'Grayling Twsp - 304',
             '305': 'Grayling Twsp - 305',
             '306': 'Grayling Twsp - 306',
             '307': 'Grayling Twsp - 307',
             '401': 'Lovells Twsp',
             '501': 'Maple Forest Twsp (CASD)',
             '601': 'South Branch Twsp',
             '701': 'Grayling City',
             'BLT': 'Bear Lake Twsp (CASD)'}

# source spellings -> CENR (certified) names
CAND_FIX = {'Tudor M Dixon': 'Tudor M. Dixon',
            'Ryan D Kelley': 'Ryan D. Kelley',
            'Joel A Sheltrown': 'Joel A. Sheltrown',
            'Adam J Wojdan': 'Adam J. Wojdan'}

PARTY = {'D': 'DEM', 'R': 'REP'}


def contest_of(text):
    """(office, district) for a contest title line, else None."""
    s = one_space(text)
    m = re.match(r'^Governor$', s)
    if m:
        return ('Governor', '')
    m = re.match(r'^Congress (\d+)(?:st|nd|rd|th)? Dist', s)
    if m:
        return ('U.S. House', m.group(1))
    m = re.match(r'^State Senate (\d+)', s)
    if m:
        return ('State Senate', m.group(1))
    m = re.match(r'^State Rep (\d+)', s)
    if m:
        return ('State House', m.group(1))
    return None


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
    with pdfplumber.open(SOURCE) as pdf:
        columns = None            # x centers -> (id, kind): precinct/total
        contest = None
        for page_no, page in enumerate(pdf.pages, 1):
            for top, ws in lines_of(page):
                text = one_space(' '.join(w['text'] for w in ws))
                nums = [w for w in ws if re.fullmatch(r'\d[\d,]*', w['text'])]
                if columns is None:
                    m = re.match(r'^Remember to Refresh (.*)$', text)
                    if not m:
                        continue
                    columns = {}
                    for w in ws:
                        x = (w['x0'] + w['x1']) / 2
                        if w['text'] in PRECINCTS:
                            columns[x] = (PRECINCTS[w['text']], 'precinct')
                        elif w['text'] == 'Total':
                            columns[x] = ('Total', 'total')
                    if len(columns) != len(PRECINCTS) + 1:
                        problems.append(f'page {page_no}: header columns '
                                        f'{sorted(columns)}')
                    continue
                key = contest_of(text)
                if key:
                    contest = key
                    continue
                if text in ('State', 'Congressional', 'Legislative', 'County',
                            'Proposals', 'Abbreviations', '0') \
                        or not nums:
                    continue  # group headers, district/legend lines, footers
                if not re.match(r'^(Total Registered Voters|Ballots Cast|'
                                r'Percentage)', text):
                    cand_row(contest, ws, columns, page_no, rows, skipped,
                            problems)
    return rows, skipped, problems


def cand_row(contest, ws, columns, page_no, rows, skipped, problems):
    """One candidate row: name, party letter, one number per column with a
    value, row total last."""
    name_parts, party = [], None
    values = []
    for w in ws:
        if re.fullmatch(r'\d[\d,]*', w['text']):
            x = (w['x0'] + w['x1']) / 2
            col = min(columns, key=lambda cx: abs(cx - x))
            values.append((columns[col], int(w['text'].replace(',', ''))))
        elif w['text'] in PARTY or w['text'] == 'WI':
            party = w['text']
            name_parts.append(w['text'])  # keep order; name cut below
        else:
            name_parts.append(w['text'])
    if party is None or not values:
        skipped[one_space(' '.join(name_parts))] += 1
        return
    name = one_space(' '.join(name_parts[:-1]))  # drop the party token
    if contest is None:
        skipped[f'{name} ({party})'] += 1
        return
    office, district = contest
    votes = {label: v for (label, kind), v in values if kind == 'precinct'}
    total = next((v for (label, kind), v in values if kind == 'total'), None)
    if total is not None and sum(votes.values()) != total:
        problems.append(f'page {page_no} {name!r}: precinct sum '
                        f'{sum(votes.values())} != row total {total}')
    cand = CAND_FIX.get(name, name)
    key = BY_CAND.get((cand, PARTY.get(party, '')))
    if key is None or (office, district) != key[:2]:
        skipped[f'{name} ({party})'] += 1
        return
    for label, v in votes.items():
        if v:
            rows.append((label, office, district, key[2], cand, v))


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