"""Parse Mason County's March 10, 2020 presidential primary from the
PaddleOCR markdown of the image-only Clarity "Mason MI March 10, 2020
Election consolidated.pdf" (4 pages, cached in
/tmp/paddleocr_md_mar2020/Mason_MI_March_10_2020_Election_consolidated).

- p1 STATISTICS table (2 garbled header rows + 25 precincts + Totals):
  label | Registered Voters - Total | three zero columns | RV again |
  Ballots Cast - Total | BC DEM | BC REP | BC NPA | BC Blank | turnout
  columns.  The printed Ballots Cast total EXCLUDES Blank (Bay/Roscommon
  convention); Blank is its own column.
- p2 DEM President table: candidates Michael Bennett ... Marianne
  Williamson (14); p3 continues with Andrew Yang / Uncommitted /
  Write-in Totals; each table has its own printed Totals row.  OCR gives
  'Michael Bennett' -- canon to 'Michael Bennet'.
- p4 REP President + the Free Soil Township proposal fused into one
  table: Mark Sanford..Uncommitted + Write-in Totals + Yes + No (only
  Free Soil Township, Precinct 1 has proposal values).

The printed BC DEM / BC REP columns exceed the candidate sums in 7 of
25 precincts by 8 votes countywide (blank presidential ballots with no
candidate row) -- bound, don't equate.  The candidate rows themselves
verify exactly against their printed Totals rows.

Emission per precinct (Roscommon convention): Ballots Cast DEM / REP /
NPA, Ballots Cast Blank, partyless Ballots Cast total, Registered
Voters, 16 DEM + 5 REP candidates (Write-in Totals not emitted, March
convention), and the Free Soil proposal Yes / No.
"""
import csv
import re

CACHE = ('/tmp/paddleocr_md_mar2020/'
         'Mason_MI_March_10_2020_Election_consolidated')
OUT = '2020/counties/20200310__mi__primary__president__mason__precinct.csv'
COUNTY = 'Mason'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TABLE_RE = re.compile(r'<table.*?</table>', re.S)
PROP = 'Free Soil Township Proposal'


def ival(s):
    s = s.strip()
    return int(s.replace(',', '')) if re.fullmatch(r'[\d,]+', s) else None


def load_table(page, hdr_row, ncols, label='contest'):
    """-> ({precinct: [vals]}, totals) for the page's single table."""
    md = open(f'{CACHE}/{page}.md').read()
    t = TABLE_RE.findall(md)[0]
    rows = [[c.strip() for c in CELL_RE.findall(r)]
            for r in re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.S)]
    data, tot = {}, None
    for r in rows[hdr_row + 1:]:
        vals = [ival(x) for x in r[1:ncols + 1]]
        if r[0].lower() == 'totals':
            tot = vals
        else:
            if r[0] in data:
                raise ValueError(f'{page}: duplicate precinct {r[0]!r}')
            data[r[0]] = vals
    if tot is None:
        raise ValueError(f'{page}: no Totals row')
    return data, tot


def main():
    problems = []
    stats, stot = load_table('p001', 1, 10, 'stats')
    dem1, d1t = load_table('p002', 2, 14)
    dem2, d2t = load_table('p003', 2, 3)
    rep, rt = load_table('p004', 2, 8)
    if not (set(stats) == set(dem1) == set(dem2) == set(rep)):
        problems.append(f'precinct sets differ: stats {len(stats)} '
                        f'dem1 {len(dem1)} dem2 {len(dem2)} rep {len(rep)}')

    # per-candidate sums vs printed Totals
    for data, tot, names in ((dem1, d1t, DEM[:14]), (dem2, d2t, DEM[14:]),
                             (rep, rt, REP)):
        if len(tot) < len(names):
            problems.append(f'totals {tot} short for {names}')
            continue
        for i, n in enumerate(names):
            s = sum(v[i] for v in data.values())
            if s != tot[i]:
                problems.append(f'{n}: precinct sum {s} != Totals {tot[i]}')

    # stats: BC total excludes Blank; DEM/REP candidate sums <= BC party
    for p, v in stats.items():
        if v[5] != v[6] + v[7] + v[8]:
            problems.append(f'{p}: BC total {v[5]} != DEM+REP+NPA '
                            f'{v[6] + v[7] + v[8]}')
        dem_sum = sum(dem1[p]) + sum(dem2[p])
        if dem_sum > v[6]:
            problems.append(f'{p}: DEM candidates {dem_sum} > BC DEM {v[6]}')
        rep_sum = sum(rep[p][:6])
        if rep_sum > v[7]:
            problems.append(f'{p}: REP candidates {rep_sum} > BC REP {v[7]}')
    for name, i in (('BC total', 5), ('BC DEM', 6), ('BC REP', 7),
                    ('BC NPA', 8), ('BC Blank', 9), ('RV', 0)):
        s = sum(v[i] for v in stats.values())
        if s != stot[i]:
            problems.append(f'{name}: precinct sum {s} != Totals {stot[i]}')
    for p, v in rep.items():
        if any(v[6:8]) and p != 'Free Soil Township, Precinct 1':
            problems.append(f'{p}: proposal values in a non-Free-Soil '
                            f'precinct {v[6:8]}')
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    rows_out = []
    for p in sorted(stats):
        v = stats[p]
        rows_out.append([COUNTY, p, 'Registered Voters', '', '', '', v[0]])
        rows_out.append([COUNTY, p, 'Ballots Cast', '', 'DEM', '', v[6]])
        rows_out.append([COUNTY, p, 'Ballots Cast', '', 'REP', '', v[7]])
        rows_out.append([COUNTY, p, 'Ballots Cast', '', 'NPA', '', v[8]])
        rows_out.append([COUNTY, p, 'Ballots Cast Blank', '', '', '', v[9]])
        rows_out.append([COUNTY, p, 'Ballots Cast', '', '', '', v[5]])
        for name in DEM:
            votes = (dem1[p][DEM[:14].index(name)]
                     if name in DEM[:14] else
                     dem2[p][DEM[14:].index(name)])
            rows_out.append([COUNTY, p, 'President', '', 'DEM', name, votes])
        for name in REP:
            rows_out.append([COUNTY, p, 'President', '', 'REP', name,
                             rep[p][REP.index(name)]])
        if rep[p][6] is not None or rep[p][7] is not None:
            rows_out.append([COUNTY, p, PROP, '', '', 'Yes', rep[p][6]])
            rows_out.append([COUNTY, p, PROP, '', '', 'No', rep[p][7]])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(stats)} precincts')
    print('county: RV', stot[0], 'BC', stot[5], 'DEM', stot[6],
          'REP', stot[7], 'NPA', stot[8], 'Blank', stot[9])
    print('Biden', d1t[1], 'Sanders', d1t[9], 'Trump', rt[1])


if __name__ == '__main__':
    main()