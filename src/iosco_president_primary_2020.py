"""Parse Iosco County's March 10, 2020 presidential primary from the
PaddleOCR markdown of the image-only ES&S "Statement of Votes by
Precinct" PDF (8 pages, cached in /tmp/paddleocr_md_mar2020/
Iosco_MI_March_2020_Statement_of_Votes_by_Precinct_unofficial_).

- p1-p2: countywide voting-stats table (Precinct | Registered Voters |
  Cards Cast | Voters Cast | % Turnout) -- 18 precincts, Registered
  Voters all 0 (no RV data), Cards Cast == Voters Cast 5,189.
- p3-p6: DEM President -- a per-precinct Times Cast table, then
  candidate tables spanning pages (Bennet + Biden on p3, Bloomberg
  through Klobuchar on p4, Sanders through Uncommitted on p5), then
  Total Votes + Unresolved Write-In on p6.  p7-p8: the same for REP
  (Times Cast, Sanford + Trump, then Walsh / Weld / Uncommitted /
  Total Votes / Unresolved Write-In).
- Each table repeats the 'Precinct' header; county rows ('County',
  'Iosco/losco County Michigan', '- Total') and zero 'Cumulative' rows
  are skipped; 'County - Total' rows are the verification anchors.
- OCR truncates some precinct labels ('Alabaster Township, Precinct'
  without ' 1', 'Burleigh Township, Precinct ') -- repaired by prefix-
  matching against the stats table's labels (unique match required).
  'uapidJoe' is Biden's garbled header; 'losco' -> 'Iosco'.

Times Cast is the per-party ballots cast (DEM 2,917 + REP 2,272 ==
Voters Cast 5,189); Total Votes (DEM 2,911 / REP 2,264) is the
candidate sum -- Unresolved Write-In (DEM 0 / REP 1) sits outside it
and is not emitted (March convention).  Emission per precinct: Ballots
Cast DEM / REP rows + 16 DEM + 5 REP candidates.  Checks: per-candidate
precinct sums == printed County - Total, per-precinct DEM TC + REP TC
== Voters Cast, candidate sums <= party Times Cast.
"""
import csv
import re

CACHE = ('/tmp/paddleocr_md_mar2020/'
         'Iosco_MI_March_2020_Statement_of_Votes_by_Precinct_unofficial_')
OUT = '2020/counties/20200310__mi__primary__president__iosco__precinct.csv'
COUNTY = 'Iosco'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

SKIP_ROWS = {'County', 'Iosco County Michigan', 'losco County Michigan',
             'Iosco County Michigan - Total', 'losco County Michigan - Total',
             'Cumulative', 'Cumulative - Total'}
TOTAL_ROW = 'County - Total'
CANON = {'uapidJoe': 'Joe Biden'}

CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TABLE_RE = re.compile(r'<table.*?</table>', re.S)


def ival(s):
    s = s.strip()
    return int(s.replace(',', '')) if re.fullmatch(r'[\d,]+', s) else None


def load_page(page):
    """-> list of (header, rows) for every table on the page."""
    md = open(f'{CACHE}/{page}.md').read()
    out = []
    for t in TABLE_RE.findall(md):
        rows = [[c.strip() for c in CELL_RE.findall(r)]
                for r in re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.S)]
        out.append((rows[0], rows[1:]))
    return out


def table_rows(header, rows, stats, problems, where):
    """-> ({precinct: [vals]}, county_total) with labels repaired."""
    data, total = {}, None
    for r in rows:
        vals = [ival(v) for v in r[1:len(header)]]
        if r[0] in SKIP_ROWS:
            continue
        if r[0] == TOTAL_ROW:
            total = vals
            continue
        label = r[0].strip()
        full = label
        if full not in stats:
            m = [s for s in stats if s.startswith(label.rstrip())]
            if len(m) != 1:
                problems.append(f'{where}: label {label!r} matches {m}')
                continue
            full = m[0]
        if full in data:
            problems.append(f'{where}: duplicate {full!r}')
        data[full] = vals
    return data, total


def main():
    problems = []
    # countywide voting stats (p1-p2)
    stats = {}
    cty = None
    for page in ('p001', 'p002'):
        header, rows = load_page(page)[0]
        for r in rows:
            vals = [ival(v) for v in r[1:5]]
            if r[0] in SKIP_ROWS:
                continue
            if r[0] == TOTAL_ROW:
                cty = vals
                continue
            if r[0] in stats:
                problems.append(f'duplicate stats row {r[0]!r}')
            stats[r[0]] = vals
    if cty is None or len(stats) != 18:
        problems.append(f'stats: {len(stats)} precincts, county row {cty}')
        for p in problems:
            print('PROBLEM:', p)
        return

    pages = [load_page(f'p{n:03d}') for n in range(3, 9)]
    # per-party Times Cast tables (p3 table 0, p7 table 0)
    tc = {p: [None, None] for p in stats}
    tc_tot = []
    for k, (pi, ti) in enumerate([(0, 0), (4, 0)]):
        header, rows = pages[pi][ti]
        if header[1] != 'Times Cast':
            problems.append(f'p{pi + 3} table {ti}: header {header[:3]}')
        data, total = table_rows(header, rows, stats, problems,
                                 f'p{pi + 3} t{ti}')
        tc_tot.append(total)
        for p, vals in data.items():
            tc[p][k] = vals[0]
    # candidate tables in page order: p3 t1, p4, p5, p6, p7 t1, p8
    cand_pages = [(0, 1, 'DEM'), (1, 0, 'DEM'), (2, 0, 'DEM'), (3, 0, 'DEM'),
                  (4, 1, 'REP'), (5, 0, 'REP')]
    headers = {}
    cand_votes = {p: {'DEM': {}, 'REP': {}} for p in stats}
    totals = []
    for pi, ti, party in cand_pages:
        header, rows = pages[pi][ti]
        names = [CANON.get(h, h) for h in header[1:] if h]
        data, total = table_rows(header, rows, stats, problems,
                                 f'p{pi + 3} t{ti}')
        if set(data) != set(stats):
            problems.append(f'p{pi + 3} t{ti}: precinct set differs '
                            f'({len(data)} vs {len(stats)})')
        totals.append(total)
        for p, vals in data.items():
            for i, n in enumerate(names):
                cand_votes[p][party][n] = vals[i]
        if party == 'DEM' and headers.get('DEM') is None:
            headers['DEM'] = names
        elif party == 'REP' and headers.get('REP') is None:
            headers['REP'] = names
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    # each table's candidate names must continue the expected sequence
    dem_seen = DEM + ['Total Votes', 'Unresolved Write-In']
    rep_seen = REP + ['Total Votes', 'Unresolved Write-In']
    pos = {'DEM': 0, 'REP': 0}
    for pi, ti, party in cand_pages:
        names = [CANON.get(h, h) for h in pages[pi][ti][0][1:] if h]
        expected = dem_seen if party == 'DEM' else rep_seen
        e = expected[pos[party]:pos[party] + len(names)]
        if names != e:
            problems.append(f'p{pi + 3} t{ti}: names {names} expected {e}')
        pos[party] += len(names)
    if pos['DEM'] != len(dem_seen) or pos['REP'] != len(rep_seen):
        problems.append(f'candidate sequence incomplete: {pos}')
    if len(tc_tot) != 2 or any(t is None for t in tc_tot):
        problems.append(f'TC county totals {tc_tot}')
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    # per-candidate sums vs County - Total (aligned page by page)
    idx = 0
    for (pi, ti, party), total in zip(cand_pages, totals):
        names = [CANON.get(h, h) for h in pages[pi][ti][0][1:] if h]
        for i, n in enumerate(names):
            s = sum(cand_votes[p][party][n] for p in stats)
            if s != total[i]:
                problems.append(f'{n}: precinct sum {s} != County-Total '
                                f'{total[i]}')
        idx += 1
    # Times Cast sums vs their County - Total rows
    for k, (pi, ti) in enumerate([(0, 0), (4, 0)]):
        s = sum(tc[p][k] for p in stats)
        if s != tc_tot[k][0]:
            problems.append(f'Times Cast p{pi + 3}: {s} != {tc_tot[k][0]}')
    # DEM TC + REP TC == Voters Cast per precinct; candidate sums <= TC
    for p in sorted(stats):
        d, r = tc[p]
        if d + r != stats[p][2]:
            problems.append(f'{p}: DEM TC {d} + REP TC {r} != Voters Cast '
                            f'{stats[p][2]}')
    for party, key in (('DEM', 0), ('REP', 1)):
        names = DEM if party == 'DEM' else REP
        for p in sorted(stats):
            s = sum(cand_votes[p][party][n] for n in names)
            if s > tc[p][key]:
                problems.append(f'{p}: {party} candidates {s} > Times Cast '
                                f'{tc[p][key]}')
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    rows_out = []
    for p in sorted(stats):
        for party, names in (('DEM', DEM), ('REP', REP)):
            for name in names:
                rows_out.append([COUNTY, p, 'President', '', party, name,
                                 cand_votes[p][party][name]])
        d, r = tc[p]
        rows_out.append([COUNTY, p, 'Ballots Cast', '', 'DEM', '', d])
        rows_out.append([COUNTY, p, 'Ballots Cast', '', 'REP', '', r])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    biden = sum(cand_votes[p]['DEM']['Joe Biden'] for p in stats)
    sanders = sum(cand_votes[p]['DEM']['Bernie Sanders']
                  for p in stats)
    trump = sum(cand_votes[p]['REP']['Donald J. Trump']
                for p in stats)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(stats)} precincts')
    print('county: Biden', biden, 'Sanders', sanders, 'Trump', trump,
          'BC', sum(v[2] for v in stats.values()))


if __name__ == '__main__':
    main()