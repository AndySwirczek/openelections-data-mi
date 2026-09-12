"""Parse the Houghton County Mar 10 2020 presidential primary from the
ES&S 'Statement of Votes Cast' PDF (text-extractable).

Source (openelections-sources-mi/2020/presidential_primary):
'Houghton MI 3.10.20 SOVC RESULTS.pdf', 38 pages, landscape.

Structure (contest-major; each table is a run of precinct rows):
- pp1-2 turnout: RV / Cards Cast / Voters Cast / % Turnout for the 29
  in-county precincts plus 5 zero rows for out-of-county shared
  precincts (Ontonagon/Keweenaw), then county rows.
- pp3-10 DEM 'President (DEM) (Vote for 1)' in TWO precinct runs (run 1
  pp3-6: first 21 precincts; run 2 pp7-10: remaining 8), each run =
  TC/RV table + Bennet/Biden table, a Bloomberg..Klobuchar 7-col table,
  a Sanders..Uncommitted 7-col table, then Total Votes /
  Unresolved Write-In.  County totals print only in run 2.
- pp11-14 REP in the same two runs (TC/RV + Sanford/Trump; then
  Walsh/Weld/Uncommitted/TV/UWI); county totals in run 2.
- pp15-38 TWELVE scoped proposals, two pages each (title page: TC/RV
  table + Yes/No/TV table; page 2: UWI table): Duncan Twp Operating
  Millage, Duncan Voted Fire Millage Renewal 1, Duncan Voted Ambulance
  Millage Renewal, Duncan Vote Fire Millage Renewal 2, Portage Road &
  Street Repair Millage, Portage Fire Departments Millage 1, Portage
  Fire Departments Millage 2, Renewal of Quincy Township Operations,
  Renewal of Quincy Township Fire Protection, Stanton Township Fire
  Departments Operational Millage, Village of Calumet Ordinance No. 154
  Referendum, Village of Copper City Proposal.

Parsing is via pdfplumber extract_tables (the PDF is fully ruled), so
two-line precinct labels arrive as single cells.  Rotated headers come
out as reversed fragments ('tsaC|semiT') and are ignored: each page's
column layout is known and hardcoded.  'Unresolved Write-In' sits
outside Total Votes (e.g. Calumet Charter Township, Precinct 2: TV 144
+ UWI 1 = TC 145) -> not emitted; Ballots Cast = Total Votes.

Every table (turnout ones included) opens with an all-empty
'County / Houghton County Michigan' label block, which is skipped; the
county totals print as 'Houghton County Michigan - Total' rows followed
by all-zero Cumulative rows and a 'County - Total' data row that
duplicates the county values (verified).

Usage:
    .venv/bin/python src/houghton_president_primary_2020.py
"""
import csv
import os
import re

import pdfplumber

COUNTY = 'Houghton'
OUT = '2020/counties/20200310__mi__primary__president__houghton__precinct.csv'
SRC = os.path.expanduser('~/code/openelections-sources-mi/2020/'
                         'presidential_primary/Houghton MI 3.10.20 '
                         'SOVC RESULTS.pdf')

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
       'Cory Booker', 'Pete Buttigieg', 'Julian Castro', 'John Delaney',
       'Tulsi Gabbard', 'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak',
       'Tom Steyer', 'Elizabeth Warren', 'Marianne Williamson',
       'Andrew Yang', 'Uncommitted']
DEM_7A = ['Michael R. Bloomberg', 'Cory Booker', 'Pete Buttigieg',
          'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
          'Amy Klobuchar']
DEM_7B = ['Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
          'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
          'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']
TCRV = ['Times Cast', 'Registered Voters']
TVUWI = ['Total Votes', 'Unresolved Write-In']
TURNOUT_COLS = ['Registered Voters', 'Cards Cast', 'Voters Cast',
                '% Turnout']

TABLE_SETTINGS = dict(vertical_strategy='lines', horizontal_strategy='lines')
COUNTY_TOTAL = 'Houghton County Michigan - Total'
HEADER_LABELS = {'County', 'Houghton County Michigan'}
ZERO_LABELS = {'Cumulative', 'Cumulative - Total'}

# page -> [(table index within page, section, columns), ...]; sections
# key the store: 'DEM'/'REP' use column names directly, proposals use
# 'Yes'/'No'/'Total Votes' plus a separate Times Cast/RV store keyed by
# the proposal title.
SEVEN_A = DEM_7A
SEVEN_B = DEM_7B
PROPS = ['Duncan Twp Operating Millage',
         'Duncan Voted Fire Millage Renewal 1',
         'Duncan Voted Ambulance Millage Renewal',
         'Duncan Vote Fire Millage Renewal 2',
         'Portage Road & Street Repair Millage',
         'Portage Fire Departments Millage 1',
         'Portage Fire Departments Millage 2',
         'Renewal of Quincy Township Operations',
         'Renewal of Quincy Township Fire Protection',
         'Stanton Township Fire Departments Operational Millage',
         'Village of Calumet Ordinance No. 154 Referendum',
         'Village of Copper City Proposal']
YNTV = ['Yes', 'No', 'Total Votes']

LAYOUT = {}
LAYOUT[1] = [('turnout', TURNOUT_COLS)]
LAYOUT[2] = [('turnout', TURNOUT_COLS)]
LAYOUT[3] = [('DEM', TCRV), ('DEM', ['Michael Bennet', 'Joe Biden'])]
LAYOUT[7] = [('DEM', TCRV), ('DEM', ['Michael Bennet', 'Joe Biden'])]
for p in (4, 8):
    LAYOUT[p] = [('DEM', SEVEN_A)]
for p in (5, 9):
    LAYOUT[p] = [('DEM', SEVEN_B)]
for p in (6, 10):
    LAYOUT[p] = [('DEM', TVUWI)]
LAYOUT[11] = [('REP', TCRV), ('REP', ['Mark Sanford', 'Donald J. Trump'])]
for p in (12, 14):
    LAYOUT[p] = [('REP', ['Joe Walsh', 'Bill Weld', 'Uncommitted',
                          'Total Votes', 'Unresolved Write-In'])]
LAYOUT[13] = [('REP', TCRV), ('REP', ['Mark Sanford', 'Donald J. Trump'])]
for i, title in enumerate(PROPS):
    LAYOUT[15 + 2 * i] = [(title, TCRV), (title, YNTV)]
    LAYOUT[16 + 2 * i] = [(title, ['Unresolved Write-In'])]

EXPECTED_PRECINCTS = 29
COUNTY_TURNOUT = {'Registered Voters': '23,879', 'Cards Cast': '6,532',
                  'Voters Cast': '6,532'}
COUNTY_PCT = 27.35


def ival(text):
    return int(text.replace(',', ''))


def norm(cell):
    return re.sub(r'\s+', ' ', (cell or '')).strip()


def canon_label(cell):
    """join the two lines of a wrapped precinct label"""
    return norm(cell)


def main():
    problems = []
    data = {}        # section -> precinct -> column -> value
    ctotals = {}     # (section, column) -> county total value
    turnout_pct = {}
    porder = []      # every precinct in the turnout table (34, incl.
                     # the 5 zero out-of-county shared precincts)
    corder = []      # precincts with contest rows (29 in-county)

    with pdfplumber.open(SRC) as pdf:
        for pno, layout in sorted(LAYOUT.items()):
            tables = pdf.pages[pno - 1].extract_tables(TABLE_SETTINGS)
            if len(tables) != len(layout):
                problems.append(f'p{pno}: {len(tables)} tables != '
                                f'{len(layout)} expected')
                continue
            for ti, (section, cols) in enumerate(layout):
                rows = tables[ti]
                for r in rows[1:]:
                    label = canon_label(r[0])
                    vals = [norm(c) for c in r[1:] if norm(c)]
                    if label in HEADER_LABELS:
                        if vals:
                            problems.append(f'p{pno} t{ti}: values on '
                                            f'header row {label!r}')
                        continue
                    if label in ZERO_LABELS:
                        # the all-zero Cumulative block; never emitted
                        # ('N/A' is the zero turnout pct)
                        if any(v not in ('0', 'N/A') for v in vals):
                            problems.append(f'p{pno} t{ti}: {label} '
                                            f'values {vals} not all 0')
                        continue
                    if label == COUNTY_TOTAL:
                        for col, v in zip(cols, vals):
                            key = (section, col)
                            if key in ctotals and ctotals[key] != v:
                                problems.append(f'p{pno} t{ti}: county '
                                                f'{col} {ctotals[key]} '
                                                f'!= {v}')
                            ctotals[key] = v
                        continue
                    if label == 'County - Total':
                        for col, v in zip(cols, vals):
                            want = ctotals.get((section, col))
                            if want is None or want != v:
                                problems.append(f'p{pno} t{ti}: '
                                                f'County - Total {col} '
                                                f'{v} vs county row '
                                                f'{want}')
                        continue
                    # precinct row
                    if len(vals) != len(cols):
                        problems.append(f'p{pno} t{ti}: {label}: '
                                        f'{len(vals)} values vs '
                                        f'{len(cols)} columns')
                        continue
                    if section == 'turnout':
                        if label not in porder:
                            porder.append(label)
                        for col, v in zip(cols, vals):
                            cur = data.setdefault('turnout', {}) \
                                      .setdefault(label, {}) \
                                      .setdefault(col)
                            if cur is not None and cur != v:
                                problems.append(f'p{pno} t{ti}: turnout '
                                                f'{label} {col} '
                                                f'{cur} != {v}')
                            data['turnout'][label][col] = v
                        if vals and cols[-1] == '% Turnout':
                            turnout_pct[label] = vals[-1]
                        continue
                    store = data.setdefault(section, {}) \
                                .setdefault(label, {})
                    if label not in corder:
                        corder.append(label)
                    for col, v in zip(cols, vals):
                        cur = store.setdefault(col)
                        if cur is not None and cur != v:
                            problems.append(f'p{pno} t{ti}: {section} '
                                            f'{label} {col} {cur} != {v}')
                        store[col] = v

    # ---- checks ---------------------------------------------------------
    precincts = corder
    if len(precincts) != EXPECTED_PRECINCTS:
        problems.append(f'{len(precincts)} contest precincts != '
                        f'{EXPECTED_PRECINCTS}')
    # the turnout table lists the same 29 first, then the out-of-county
    # zero rows; make sure the contest set is exactly those 29
    if precincts != porder[:len(precincts)]:
        problems.append('turnout/contest precinct order mismatch')
    for p in porder[len(precincts):]:
        pd = data.get('turnout', {}).get(p, {})
        for col in ('Registered Voters', 'Cards Cast', 'Voters Cast'):
            if pd.get(col) not in (None, '0'):
                problems.append(f'{p}: out-of-county {col} '
                                f'{pd.get(col)!r} != 0')
    # DEM/REP: every column present, candidate sums == TV, TC >= TV
    for contest, cands in [('DEM', DEM), ('REP', REP)]:
        for p in precincts:
            pd = data.get(contest, {}).get(p, {})
            for col in cands + TVUWI:
                if col not in pd:
                    problems.append(f'{contest} {p}: missing {col}')
            if 'Total Votes' in pd:
                s = sum(ival(pd[c]) for c in cands if c in pd)
                tv = ival(pd['Total Votes'])
                if s != tv:
                    problems.append(f'{contest} {p}: candidates {s} '
                                    f'!= TV {tv}')
                tc = pd.get('Times Cast')
                if tc is not None and ival(tc) < tv:
                    problems.append(f'{contest} {p}: Times Cast {tc} '
                                    f'< TV {tv}')
        for col in cands + ['Total Votes']:
            s = sum(ival(data[contest][p][col]) for p in precincts)
            want = ctotals.get((contest, col))
            if want is None:
                problems.append(f'{contest}: no county total for {col}')
            elif s != ival(want):
                problems.append(f'{contest} county {col}: {s} != {want}')
    # proposals: Yes+No == TV; sums == county totals
    for title in PROPS:
        pd_all = data.get(title, {})
        for p, pd in pd_all.items():
            for col in YNTV:
                if col not in pd:
                    problems.append(f'{title} {p}: missing {col}')
            if 'Total Votes' in pd and 'Yes' in pd and 'No' in pd:
                if ival(pd['Yes']) + ival(pd['No']) \
                        != ival(pd['Total Votes']):
                    problems.append(f'{title} {p}: Yes+No != TV')
        for col in YNTV:
            s = sum(ival(data[title][p][col]) for p in pd_all)
            want = ctotals.get((title, col))
            if want is None:
                problems.append(f'{title}: no county total for {col}')
            elif s != ival(want):
                problems.append(f'{title} county {col}: {s} != {want}')
    # turnout: Cards Cast == Voters Cast, % Turnout, county sums
    t = data.get('turnout', {})
    for p, pd in t.items():
        if pd.get('Cards Cast') != pd.get('Voters Cast'):
            problems.append(f'{p}: Cards Cast != Voters Cast')
        pct = turnout_pct.get(p)
        rv = ival(pd['Registered Voters']) if 'Registered Voters' in pd \
            else None
        if rv == 0:
            if pct != 'N/A':
                problems.append(f'{p}: RV 0 but pct {pct!r} != N/A')
        elif pct is not None and rv:
            want = round(ival(pd['Cards Cast']) / rv * 100, 2)
            if abs(want - float(pct[:-1])) > 0.005:
                problems.append(f'{p}: turnout {want}% != printed {pct}')
    for col, want in COUNTY_TURNOUT.items():
        s = sum(ival(t[p][col]) for p in precincts)
        if s != ival(want):
            problems.append(f'turnout county {col}: {s} != {want}')
        got = ctotals.get(('turnout', col))
        if got is None or ival(got) != ival(want):
            problems.append(f'turnout county row {col}: {got} != {want}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing {OUT}')
        return

    # ---- emission -------------------------------------------------------
    rows = []
    for p in precincts:
        rows.append([COUNTY, p, 'Registered Voters', '', '', '',
                     ival(data['turnout'][p]['Registered Voters'])])
    for contest, cands in [('DEM', DEM), ('REP', REP)]:
        for p in precincts:
            pd = data[contest][p]
            for c in cands:
                rows.append([COUNTY, p, 'President', '', contest, c,
                             ival(pd[c])])
            rows.append([COUNTY, p, 'Ballots Cast', '', contest, '',
                         ival(pd['Total Votes'])])
    for title in PROPS:
        for p in sorted(data[title]):
            pd = data[title][p]
            for c in ('Yes', 'No'):
                rows.append([COUNTY, p, title, '', '', c, ival(pd[c])])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(precincts)} precincts, '
          f'{len(PROPS)} proposals')


if __name__ == '__main__':
    main()