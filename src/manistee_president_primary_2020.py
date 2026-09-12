"""Parse the Manistee County Mar 10 2020 presidential primary from the
cached PaddleOCR markdown of the scanned official SOVC.

Source (openelections-sources-mi/2020/presidential_primary):
'Manistee MI 03-10-2020 Presidential Primary Election - Official
Statement of Votes Cast (PDF).pdf', 30 pages, landscape scan with a poor
embedded OCR text layer (garbled words, reversed vertical headers) --
useless for parsing -- so the PDF was re-OCR'd with PaddleOCR-VL
(src/fetch_paddleocr_md.py) and the per-page markdown cached in
/tmp/paddleocr_md.

Structure (contest-major, two-up tables side by side on each page, every
table keyed by precinct with Election Day / AV Counting Boards / Total
sub-rows):
- pp1-3 turnout: RV / Cards Cast / Voters Cast / % Turnout, county total
  on p3 ('Manistee County Michigan - Total' 20,230 / 6,102 / 6,102 /
  30.16%) plus Cumulative all-zero rows; p4 blank.
- pp5-16 DEM 'President of the United States' in THREE runs by precinct
  (run A pp5-8: City of Manistee 1-2, Arcadia, Bear Lake, Brown, Cleon,
  Dickson; run B pp9-12: Filer, Manistee, Maple Grove, Marilla, Norman,
  Onekama, Pleasanton; run C pp13-16: Springdale, Stronach).  Each run =
  TC/RV table + Bennet/Biden table, then a 7-candidate table
  (Bloomberg..Klobuchar), then another 7-candidate table
  (Sanders..Uncommitted), then Total Votes / Unresolved Write-In.
  County totals (TV 3,637, UWI 1) print only on the last run's pages.
- pp17-22 REP: same 3 runs; run table 1 = Sanford/Trump, table 2 =
  Walsh/Weld/Uncommitted/TV/UWI; county TV 2,255 (UWI 4) on p22.
- pp23-29 ONE proposal 'County Proposal - Proposition 1 (Vote for 1)'
  (title prints only on p23; pp25/28 carry no title), Yes/No/TV for the
  3 runs + UWI pages; county totals Yes 3,990 / No 1,878 / TV 5,868 on
  p28 (verified equal to the precinct sums).
- p30 certificate text, skipped.

Only the Total sub-row of each precinct is emitted (the standard
one-votes-column CSV has no ED/AV breakdown); the Election Day +
AV Counting Boards == Total identity is checked for every cell instead.
OCR quirks fixed: p7 'Joe Sesak' -> Joe Sestak; p28 'Total Vote' ->
Total Votes; p11/p20 lost their header row entirely (MANUAL_HEADERS by
page).

Usage:
    .venv/bin/python src/manistee_president_primary_2020.py \
        [--cache /tmp/paddleocr_md/Manistee_MI_...]
"""
import argparse
import csv
import html
import os
import re
import sys

COUNTY = 'Manistee'
OUT = '2020/counties/20200310__mi__primary__president__manistee__precinct.csv'
SRC = os.path.expanduser('~/code/openelections-sources-mi/2020/'
                         'presidential_primary/Manistee MI 03-10-2020 '
                         'Presidential Primary Election - Official '
                         'Statement of Votes Cast (PDF).pdf')

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
       'Cory Booker', 'Pete Buttigieg', 'Julian Castro', 'John Delaney',
       'Tulsi Gabbard', 'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak',
       'Tom Steyer', 'Elizabeth Warren', 'Marianne Williamson',
       'Andrew Yang', 'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']
PROP = 'County Proposal - Proposition 1'

# page -> (section, kind) for every table-bearing page; turnout pages
# hold one table, contest pages hold two (TC/RV + candidates).
PAGES = {}
for p in (1, 2, 3):
    PAGES[p] = ('turnout', 'turnout')
for p in range(5, 17):
    PAGES[p] = ('DEM', 'contest')
for p in range(17, 23):
    PAGES[p] = ('REP', 'contest')
for p in (23, 24, 25, 26, 28, 29):
    PAGES[p] = (PROP, 'contest')

# tables whose header row the OCR lost, by page
MANUAL_HEADERS = {
    11: ['Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
         'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
         'Uncommitted'],
    20: ['Joe Walsh', 'Bill Weld', 'Uncommitted', 'Total Votes',
         'Unresolved Write-In'],
}

SUBROWS = ('Election Day', 'AV Counting Boards', 'Total')
PRECINCT_RE = re.compile(r'^.*, Precinct \d$')
COUNTY_TOTAL_RE = re.compile(r'^(Manistee County Michigan) - Total$')
COUNTY_LABEL_RE = re.compile(r'^Manistee County Michigan,?$')
CANON = {'Joe Sesak': 'Joe Sestak', 'Total Vote': 'Total Votes',
         'Clean Township, Precinct 1': 'Cleon Township, Precinct 1',
         'Manistee County, Michigan - Total':
             'Manistee County Michigan - Total'}
EXPECTED_PRECINCTS = 16
TURNOUT_COLS = ['Registered Voters', 'Cards Cast', 'Voters Cast',
                '% Turnout']
COUNTY_TURNOUT = {'Registered Voters': '20230', 'Cards Cast': '6102',
                  'Voters Cast': '6102'}
COUNTY_PCT = 30.16


def ival(text):
    return int(text.replace(',', ''))


def canon(text):
    text = html.unescape(re.sub(r'<[^>]*>', '', text)).strip()
    return CANON.get(text, text)


def read_tables(cache, pno):
    md = open(os.path.join(cache, f'p{pno:03d}.md')).read()
    out = []
    for tm in re.findall(r'<table.*?</table>', md, re.S):
        rows = []
        for r in re.findall(r'<tr>(.*?)</tr>', tm, re.S):
            rows.append([canon(c) for c in
                         re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)])
        out.append(rows)
    return out


def data_cells(cells):
    """values of a sub-row with the OCR's stray empty cells dropped"""
    return [c for c in cells[1:] if c != '']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default=os.path.join(
        '/tmp/paddleocr_md',
        'Manistee_MI_03_10_2020_Presidential_Primary_Election_Official_'
        'Statement_of_Votes_Cast_PDF_'))
    args = ap.parse_args()

    problems = []
    # data[contest][precinct][column][subrow] = value
    data = {}
    porder = []          # precincts in first-seen order
    ctotals = {}         # (contest, column) -> county total value
    turnout_pct = {}     # precinct -> printed % Turnout (Total row)
    pseudo_block = {}    # col -> {subrow: val} for the Cumulative /
                         # countywide blocks after the real precincts

    def store(contest, precinct, col, sub, val, ctx):
        cur = data.setdefault(contest, {}).setdefault(precinct, {}) \
                  .setdefault(col, {}).get(sub)
        if cur is not None and cur != val:
            problems.append(f'{ctx}: {precinct} {col} {sub} '
                            f'{cur} != {val}')
        data[contest][precinct][col][sub] = val

    for pno in sorted(PAGES):
        section, kind = PAGES[pno]
        for ti, rows in enumerate(read_tables(args.cache, pno)):
            ctx = f'p{pno} t{ti}'
            header = [c for c in rows[0][1:] if c] \
                if rows[0][0] == 'Precinct' else []
            if not header:
                header = MANUAL_HEADERS.get(pno, [])
            if pno in MANUAL_HEADERS:
                header = MANUAL_HEADERS[pno]
            if kind == 'turnout':
                header = TURNOUT_COLS
            precinct = None
            for r in rows[1:]:
                name = r[0]
                if COUNTY_TOTAL_RE.match(name):
                    vals = data_cells(r)
                    for col, v in zip(header, vals):
                        key = (section, col)
                        if key in ctotals and ctotals[key] != v:
                            problems.append(f'{ctx}: county {col} '
                                            f'{ctotals[key]} != {v}')
                        ctotals[key] = v
                    continue
                if name == 'Cumulative' and not data_cells(r):
                    # the Cumulative block's zero sub-rows must not
                    # attach to the last real precinct
                    precinct = 'Cumulative (pseudo)'
                    pseudo_block = {}
                    continue
                if name.startswith('Cumulative'):
                    continue    # 'Cumulative' / 'Cumulative - Total'
                if name == 'County - Total':
                    if not data_cells(r):
                        # label row of the countywide ED/AV/Total block
                        precinct = 'Cumulative (pseudo)'
                        pseudo_block = {}
                        continue
                    # data row == the Michigan - Total values; the
                    # countywide ED/AV/Total sub-rows that may follow
                    # get a fresh block
                    precinct = 'Cumulative (pseudo)'
                    pseudo_block = {}
                    vals = data_cells(r)
                    for col, v in zip(header, vals):
                        key = (section, col)
                        if key in ctotals and ctotals[key] != v:
                            problems.append(f'{ctx}: county {col} '
                                            f'{ctotals[key]} != {v}')
                    continue
                if COUNTY_LABEL_RE.match(name):
                    continue    # county block header, values empty
                if PRECINCT_RE.match(name) and not data_cells(r):
                    precinct = name
                    if precinct not in porder:
                        porder.append(precinct)
                    continue
                if name in SUBROWS:
                    if precinct is None:
                        problems.append(f'{ctx}: values {name} without '
                                        f'precinct')
                        continue
                    vals = data_cells(r)
                    if len(vals) != len(header):
                        problems.append(f'{ctx}: {precinct} {name} '
                                        f'{len(vals)} values vs '
                                        f'{len(header)} columns')
                        continue
                    if precinct == 'Cumulative (pseudo)':
                        # Cumulative zeros and the countywide ED/AV/Total
                        # block: accumulate per block, check identity
                        for col, v in zip(header, vals):
                            pseudo_block.setdefault(col, {})[name] = v
                        continue
                    for col, v in zip(header, vals):
                        store(section, precinct, col, name, v, ctx)
                        if name == 'Total' and col == '% Turnout':
                            turnout_pct[precinct] = v
                    continue
                if name and data_cells(r):
                    problems.append(f'{ctx}: unmatched row {r}')
            # check the trailing pseudo block's ED + AV == Total (the
            # printed county block sometimes lacks its Total row, in
            # which case the county total is the reference)
            for col, subs in pseudo_block.items():
                if col in ('% Turnout', 'Registered Voters'):
                    continue
                ed, av, tot = subs.get('Election Day'), \
                    subs.get('AV Counting Boards'), subs.get('Total')
                if ed is None or av is None:
                    continue
                ref = ival(tot) if tot is not None else \
                    (ival(ctotals[(section, col)])
                     if ctotals.get((section, col)) else None)
                if ref is not None and ival(ed) + ival(av) != ref:
                    problems.append(f'{ctx}: county block {col}: '
                                    f'{ed} + {av} != {ref}')
            pseudo_block = {}
            # the contest-page TC/RV and Yes/No tables share the same
            # column names across sections, so cross-section merges are
            # keyed by section id and cannot collide

    # ---- checks ---------------------------------------------------------
    precincts = porder
    if len(precincts) != EXPECTED_PRECINCTS:
        problems.append(f'{len(precincts)} precincts != '
                        f'{EXPECTED_PRECINCTS}')
    for contest, cols in [('DEM', DEM + ['Total Votes']),
                          ('REP', REP + ['Total Votes']),
                          (PROP, ['Yes', 'No', 'Total Votes']),
                          ('turnout', TURNOUT_COLS)]:
        for precinct in precincts:
            pd = data.get(contest, {}).get(precinct, {})
            for col in cols:
                sub = pd.get(col, {})
                ed, av, tot = sub.get('Election Day'), \
                    sub.get('AV Counting Boards'), sub.get('Total')
                if tot is None:
                    problems.append(f'{contest} {precinct} {col}: '
                                    f'no Total row')
                    continue
                if col in ('% Turnout', 'Registered Voters'):
                    continue    # pcts don't sum; RV repeats per subrow
                if ed is None or av is None:
                    problems.append(f'{contest} {precinct} {col}: '
                                    f'missing ED/AV sub-row')
                elif ival(ed) + ival(av) != ival(tot):
                    problems.append(f'{contest} {precinct} {col}: '
                                    f'ED {ed} + AV {av} != Total {tot}')
    # candidate sums vs Total Votes
    for contest, cands in [('DEM', DEM), ('REP', REP)]:
        for precinct in precincts:
            pd = data[contest][precinct]
            s = sum(ival(pd[c]['Total']) for c in cands)
            tv = ival(pd['Total Votes']['Total'])
            if s != tv:
                problems.append(f'{contest} {precinct}: candidates {s} '
                                f'!= TV {tv}')
    # proposal Yes + No == TV
    for precinct in precincts:
        pd = data[PROP][precinct]
        if ival(pd['Yes']['Total']) + ival(pd['No']['Total']) \
                != ival(pd['Total Votes']['Total']):
            problems.append(f'{PROP} {precinct}: Yes+No != TV')
    # turnout: Cards Cast == Voters Cast, county sums, % Turnout
    t = data['turnout']
    for precinct in precincts:
        pd = t[precinct]
        if pd['Cards Cast']['Total'] != pd['Voters Cast']['Total']:  # strings equal check
            problems.append(f'{precinct}: Cards Cast != Voters Cast')
        want = round(ival(pd['Cards Cast']['Total'])
                     / ival(pd['Registered Voters']['Total']) * 100, 2)
        if abs(want - float(turnout_pct[precinct][:-1])) > 0.005:
            problems.append(f'{precinct}: turnout {want}% != printed '
                            f'{turnout_pct[precinct]}')
    for col, want in COUNTY_TURNOUT.items():
        s = sum(ival(t[p][col]['Total']) for p in precincts)
        if s != int(want):
            problems.append(f'turnout county {col}: {s} != {want}')
        if ctotals.get(('turnout', col)) is None \
                or ival(ctotals[('turnout', col)]) != int(want):
            problems.append(f'turnout county row {col}: '
                            f'{ctotals.get(("turnout", col))} != {want}')
    for contest, col in [(('DEM', 'Total Votes')), (('REP', 'Total Votes'))]:
        s = sum(ival(data[contest][p][col]['Total']) for p in precincts)
        want = ctotals.get((contest, col))
        if want is None:
            problems.append(f'{contest}: no county total for {col}')
        elif s != ival(want):
            problems.append(f'{contest} county {col}: {s} != {want}')
    for col in ('Yes', 'No', 'Total Votes'):
        s = sum(ival(data[PROP][p][col]['Total']) for p in precincts)
        want = ctotals.get((PROP, col))
        if want is None:
            problems.append(f'{PROP}: no county total for {col}')
        elif s != ival(want):
            problems.append(f'{PROP} county {col}: {s} != {want}')
    # DEM TC/RV tables: Times Cast >= Total Votes per precinct
    for p in precincts:
        tc = data['DEM'][p].get('Times Cast', {}).get('Total')
        tv = data['DEM'][p]['Total Votes']['Total']
        if tc is not None and ival(tc) < ival(tv):
            problems.append(f'{p}: DEM Times Cast {tc} < TV {tv}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing {OUT}')
        return

    # ---- emission -------------------------------------------------------
    rows = []
    for p in precincts:
        rows.append([COUNTY, p, 'Registered Voters', '', '', '',
                     ival(data['turnout'][p]['Registered Voters']
                          ['Total'])])
    for contest, cands in [('DEM', DEM), ('REP', REP)]:
        for p in precincts:
            pd = data[contest][p]
            for c in cands:
                rows.append([COUNTY, p, 'President', '', contest, c,
                             ival(pd[c]['Total'])])
            rows.append([COUNTY, p, 'Ballots Cast', '', contest, '',
                         ival(pd['Total Votes']['Total'])])
    for p in precincts:
        pd = data[PROP][p]
        for c in ('Yes', 'No'):
            rows.append([COUNTY, p, PROP, '', '', c,
                         ival(pd[c]['Total'])])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(precincts)} precincts, '
          f'proposal: {PROP}')


if __name__ == '__main__':
    main()