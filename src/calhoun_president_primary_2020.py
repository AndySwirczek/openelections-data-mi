"""Parse Calhoun County's March 10, 2020 presidential primary from the
HTML "Calhoun MI March 2020 Detailed Results.htm" in
openelections-sources-mi/2020/presidential_primary/.

Six tables, each PRECINCT NAME | # REG. VOTERS | POLL BOOK |
% REG VOTERS | candidates..., with a 'Totals' row printed BOTH first
and last (the anchor):
- t0 President (REP), t1 President (DEM) -- 58 precincts; POLL BOOK is
  the per-party ballots cast (REP 7,606 / DEM 14,061).  The DEM table
  is UTF-8 read as latin-1 ('JuliÃ¡n Castro'); read as UTF-8.
- t2/t3 City of Battle Creek Charter Amendments 1 and 2 (21 precincts
  -- the city has no Precinct 14 or 16), Yes / No columns.
- t4 Litchfield Community Schools Sinking Fund Millage and t5
  Hillsdale County ISD Proposal, each a single precinct (Homer
  Township, Precinct 1).

Emission per precinct: Registered Voters, Ballots Cast (per-party
POLL BOOK), 16 DEM + 5 REP candidates, proposal Yes/No rows.  The
Write-in columns are not emitted (March convention).  Checks:
candidate sums == Totals, per-precinct candidates sum to no Total
Votes column exists (none printed), proposal Yes+No <= POLL BOOK.
"""
import csv
import re
import html as htmllib

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Calhoun MI March 2020 Detailed Results.htm')
OUT = '2020/counties/20200310__mi__primary__president__calhoun__precinct.csv'
COUNTY = 'Calhoun'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']
CANON = {'Julián Castro': 'Julian Castro'}

PROPOSALS = [('City of Battle Creek Charter Amendment 1', 2),
             ('City of Battle Creek Charter Amendment 2', 3),
             ('Litchfield Community Schools Sinking Fund Millage', 4),
             ('Hillsdale County ISD Proposal', 5)]


def cells(row):
    return [htmllib.unescape(re.sub(r'<[^>]+>', '', c)).strip()
            for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S)]


def main():
    h = open(SRC, encoding='utf-8').read()
    tables = [cells(r) for t in re.findall(r'<table.*?</table>', h, re.S)
              for r in re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.S)]
    # split the flat cell list back into rows via the table boundaries
    bounds = [len(re.findall(r'<tr[^>]*>', t, re.S))
              for t in re.findall(r'<table.*?</table>', h, re.S)]
    out, i = [], 0
    for b in bounds:
        out.append(tables[i:i + b])
        i += b
    problems = []

    def parse(idx):
        rows = out[idx]
        if rows[0][0] != 'PRECINCT NAME':
            problems.append(f't{idx}: header {rows[0]}')
        # Totals rows appear first AND last; keep the last as the anchor
        totals = [r for r in rows[1:] if r[0] == 'Totals']
        if len(totals) != 2 or totals[0] != totals[1]:
            problems.append(f't{idx}: Totals rows {totals}')
        data = {}
        for r in rows[1:]:
            if r[0] == 'Totals':
                continue
            if r[0] in data:
                problems.append(f't{idx}: duplicate {r[0]!r}')
            data[' '.join(r[0].split())] = r
        return rows[0], data, totals[1]

    # president tables: names come from the header, canon-mapped
    pres = {}
    for idx, party, seq in ((0, 'REP', REP), (1, 'DEM', DEM)):
        header, data, total = parse(idx)
        names = [CANON.get(n, n) for n in header[4:]]
        # the source also prints a Write-in column (not emitted)
        if names != seq + ['Write-in']:
            problems.append(f't{idx}: candidates {names}')
            continue
        names = names[:len(seq)]
        if len(data) != 58:
            problems.append(f't{idx}: {len(data)} precincts')
        pres[party] = (data, total, names)
    if problems:
        for p in problems:
            print('PROBLEM:', p)
        return

    for party, seq in (('REP', REP), ('DEM', DEM)):
        data, total, names = pres[party]
        for i, n in enumerate(names):
            s = sum(int(v[4 + i]) for v in data.values())
            if s != int(total[4 + i]):
                problems.append(f'{party} {n}: sum {s} != {total[4 + i]}')
        if sum(int(v[1]) for v in data.values()) != int(total[1]):
            problems.append(f'{party} RV sum != {total[1]}')
        if sum(int(v[2]) for v in data.values()) != int(total[2]):
            problems.append(f'{party} POLL BOOK sum != {total[2]}')
    # RV identical across tables
    d1, _, _ = pres['REP']
    d2, _, _ = pres['DEM']
    if set(d1) != set(d2):
        problems.append('precinct sets differ REP vs DEM')
    for p in d1:
        if d1[p][1] != d2[p][1]:
            problems.append(f'{p}: RV {d1[p][1]} vs {d2[p][1]}')

    props = {}
    for title, idx in PROPOSALS:
        header, data, total = parse(idx)
        if header[4:] != ['Yes', 'No']:
            problems.append(f'{title}: header {header[4:]}')
        for p, v in data.items():
            if int(v[4]) + int(v[5]) > int(v[2]):
                problems.append(f'{title} {p}: Yes+No {int(v[4]) + int(v[5])}'
                                f' > POLL BOOK {v[2]}')
        s = [sum(int(v[i]) for v in data.values()) for i in (1, 2, 4, 5)]
        want = [int(total[i]) for i in (1, 2, 4, 5)]
        if s != want:
            problems.append(f'{title}: sums {s} != Totals {want}')
        props[title] = data
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
            print(f'{len(problems)} problems; not writing')
            return

    rows_out = []
    for p in sorted(d1):
        rv = int(d1[p][1])
        rows_out.append([COUNTY, p, 'Registered Voters', '', '', '', rv])
        for party in ('DEM', 'REP'):
            data, _, _ = pres[party]
            rows_out.append([COUNTY, p, 'Ballots Cast', '', party, '',
                             int(data[p][2])])
        for party, seq in (('DEM', DEM), ('REP', REP)):
            data, _, _ = pres[party]
            for i, n in enumerate(seq):
                rows_out.append([COUNTY, p, 'President', '', party, n,
                                 int(data[p][4 + i])])
        for title, data in props.items():
            if p in data:
                v = data[p]
                rows_out.append([COUNTY, p, title, '', '', 'Yes', int(v[4])])
                rows_out.append([COUNTY, p, title, '', '', 'No', int(v[5])])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(d1)} precincts')
    print('county: Biden', pres['DEM'][1][5], 'Sanders', pres['DEM'][1][13],
          'Trump', pres['REP'][1][5], 'RV', pres['DEM'][1][1])


if __name__ == '__main__':
    main()