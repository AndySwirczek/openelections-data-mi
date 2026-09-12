"""Parse Kalkaska County's March 10, 2020 presidential primary from the
PaddleOCR markdown of the image-only ES&S "Statement of Votes Cast"
PDF (21 pages, cached in /tmp/paddleocr_md_mar2020/
Kalkaska_MI_March_2020_Statement_of_Votes_Cast).

- p1: countywide voting stats (Precinct | Registered Voters | Cards
  Cast | Voters Cast | % Turnout) -- 13 precincts, Registered Voters
  all 0 (no RV data), Cards Cast == Voters Cast 3,496.
- p2-p5: DEM President -- a per-precinct Times Cast table (p2 t0),
  then candidate tables spanning pages (Bennet + Biden on p2 t1,
  Bloomberg through Klobuchar on p3, Sanders through Uncommitted on
  p4), then Total Votes + Unresolved Write-In on p5.  p6-p7: the same
  for REP (Times Cast, Sanford + Trump on p6, Walsh / Weld /
  Uncommitted / Total Votes / Unresolved Write-In on p7).
- p8-p21: seven local proposals (Clearwater Township Road and Fire
  Equipment, Springfield Township Ambulance / Fire Protection /
  General Operations, C.O.O.R. ISD Special Education and Operating
  Millage), each a single-precinct Times Cast table + Yes / No /
  Total Votes table + an Unresolved Write-In table.
- Every table repeats the 'Precinct' header; 'County',
  'Kalkaska County Michigan [- Total]' and 'Cumulative [- Total]'
  rows are skipped; 'County - Total' rows are the verification
  anchors.
- OCR filler cells (empty or '-') appear at different positions in
  headers and data rows, and split 'Unresolved' + 'Write-In' across
  two header cells -- numeric cells are read left-to-right and
  matched to the non-empty header names in order.  Canon fixes:
  'Tudi Gabbard' -> 'Tulsi Gabbard', 'Tom Steyr' -> 'Tom Steyer',
  'Billiwell' -> 'Bill Weld', 'Donald Trump' -> 'Donald J. Trump'.

Times Cast is the per-party ballots cast (DEM 1,701 / REP 1,749);
Total Votes (DEM 1,696 / REP 1,746) is the candidate sum --
Unresolved Write-In (DEM 1 / REP 0) sits outside it and is not
emitted (March convention).  **Source phenomenon**: in three
precincts (Bear Lake 225 vs 228, Clearwater 636 vs 674, Springfield
222 vs 227) the printed DEM + REP Times Cast sum falls SHORT of the
printed Voters Cast -- 46 ballots countywide counted as Voters Cast
but in neither party's Times Cast; verified against the rendered
page images, so the check is a bound (<=), never an equality.
Emission per precinct: Ballots Cast DEM / REP rows + 16 DEM + 5 REP
candidates + proposal Yes / No rows (proposal Times Cast not
emitted).  Checks: per-candidate precinct sums == printed County -
Total, per-table candidate sequence, Times Cast sums vs their County
- Total, Total Votes <= Times Cast per party.
"""
import csv
import re

CACHE = ('/tmp/paddleocr_md_mar2020/'
         'Kalkaska_MI_March_2020_Statement_of_Votes_Cast')
OUT = '2020/counties/20200310__mi__primary__president__kalkaska__precinct.csv'
COUNTY = 'Kalkaska'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

SKIP_ROWS = {'County', 'Kalkaska County Michigan',
             'Kalkaska County Michigan - Total',
             'Cumulative', 'Cumulative - Total'}
TOTAL_ROW = 'County - Total'
CANON = {'Tudi Gabbard': 'Tulsi Gabbard', 'Tom Steyr': 'Tom Steyer',
         'Billiwell': 'Bill Weld', 'Donald Trump': 'Donald J. Trump'}

# (page, table_index, party) for the candidate tables, in page order
CAND_PAGES = [(1, 1, 'DEM'), (2, 0, 'DEM'), (3, 0, 'DEM'), (4, 0, 'DEM'),
              (5, 1, 'REP'), (6, 0, 'REP')]
# (page, table_index) for the per-party Times Cast tables
TC_PAGES = [(1, 0), (5, 0)]
# proposal title -> (stats page, votes page)
PROPOSALS = [
    ('Clearwater Township Road Millage Proposal for Clearwater Township, '
     'Kalkaska County Michigan', 'p008', 'p008'),
    ('Clearwater Township Fire Equipment Proposal for Clearwater Township, '
     'Kalkaska County Michigan', 'p010', 'p010'),
    ('Springfield Township Ambulance Proposal for Springfield Township, '
     'Kalkaska County Michigan', 'p012', 'p012'),
    ('Springfield Township Fire Protection Proposal for Springfield '
     'Township, Kalkaska County Michigan', 'p014', 'p014'),
    ('Springfield Township General Operations Millage Proposal for '
     'Springfield Township, Kalkaska County Michigan', 'p016', 'p016'),
    ('C.O.O.R. ISD Special Education Proposal for C.O.O.R. ISD, '
     'Kalkaska County Michigan', 'p018', 'p018'),
    ('C.O.O.R. ISD Operating Millage Proposal for C.O.O.R. ISD, '
     'Kalkaska County Michigan', 'p020', 'p020'),
]

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


def names_of(header):
    """-> candidate names from a header row: drop empty cells, merge the
    OCR-split 'Unresolved' + 'Write-In' pair."""
    out, merged = [], False
    for h in header:
        h = h.strip()
        if not h:
            continue
        if merged:
            out[-1] += ' ' + h
            merged = False
            continue
        out.append(CANON.get(h, h))
        if h == 'Unresolved':
            merged = True
    return out


def table_rows(header, rows, stats, problems, where):
    """-> ({precinct: [vals]}, county_total): numeric cells read
    left-to-right (OCR filler '' / '-' cells are dropped, whitespace-
    fused cells like '674 0' are split)."""
    hdr = [c.strip() for c in header]
    if len(hdr) == 1 and 'Times Cast' in hdr[0]:
        names = ['Times Cast', 'Registered Voters']  # fused header row
    else:
        names = names_of(hdr[1:])
    data, total = {}, None

    def cells(r):
        vals = []
        for c in r[1:]:
            toks = c.split()
            if len(toks) > 1:
                vals.extend(ival(t) for t in toks)
            else:
                vals.append(ival(c))
        return [v for v in vals if v is not None]

    for r in rows:
        if r[0] in SKIP_ROWS:
            continue
        vals = cells(r)
        if len(vals) != len(names):
            problems.append(f'{where}: {r[0]!r} has {len(vals)} values '
                            f'for {len(names)} names')
            continue
        if r[0] == TOTAL_ROW:
            total = vals
        elif r[0] in stats:
            if r[0] in data:
                problems.append(f'{where}: duplicate {r[0]!r}')
            data[r[0]] = vals
        else:
            problems.append(f'{where}: unknown label {r[0]!r}')
    return names, data, total


def main():
    problems = []
    # countywide voting stats (p1)
    stats, cty = {}, None
    header, rows = load_page('p001')[0]
    if header[:4] != ['Precinct', 'Registered Voters', 'Cards Cast',
                      'Voters Cast']:
        problems.append(f'p001 header {header}')
    for r in rows:
        if r[0] in SKIP_ROWS:
            continue
        vals = [ival(v) for v in r[1:4]]
        if r[0] == TOTAL_ROW:
            cty = vals
        elif any(v is None for v in vals):
            problems.append(f'p001: {r[0]!r} -> {vals}')
        else:
            stats[r[0]] = vals
    if cty is None or len(stats) != 13:
        problems.append(f'stats: {len(stats)} precincts, county row {cty}')
    if problems:
        for p in problems:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return
    pages = [load_page(f'p{n:03d}') for n in range(1, 22)]

    # per-party Times Cast tables
    tc = {p: [None, None] for p in stats}
    tc_tot = []
    for k, (pi, ti) in enumerate(TC_PAGES):
        header, rows = pages[pi][ti]
        if header[1] != 'Times Cast':
            problems.append(f'p{pi + 1} t{ti}: header {header[:3]}')
        names, data, total = table_rows(header, rows, stats, problems,
                                        f'p{pi + 1} t{ti}')
        if set(data) != set(stats):
            problems.append(f'p{pi + 1} t{ti}: precinct set differs')
        if names != ['Times Cast', 'Registered Voters']:
            problems.append(f'p{pi + 1} t{ti}: names {names}')
        tc_tot.append(total)
        for p, vals in data.items():
            tc[p][k] = vals[0]

    # candidate tables in page order
    cand_votes = {p: {'DEM': {}, 'REP': {}} for p in stats}
    headers, page_names, totals = {}, [], []
    for pi, ti, party in CAND_PAGES:
        header, rows = pages[pi][ti]
        names, data, total = table_rows(header, rows, stats, problems,
                                        f'p{pi + 1} t{ti}')
        if set(data) != set(stats):
            problems.append(f'p{pi + 1} t{ti}: precinct set differs')
        totals.append(total)
        page_names.append((party, names))
        headers[party] = headers.get(party, []) + names
        for p, vals in data.items():
            for n, v in zip(names, vals):
                cand_votes[p][party][n] = v

    # proposals: single-precinct Times Cast + Yes/No tables + write-in
    props = {}
    for title, tp, vp in PROPOSALS:
        h1, rows1 = pages[int(tp[1:]) - 1][0]
        h2, rows2 = pages[int(vp[1:]) - 1][1]
        h3, rows3 = pages[int(vp[1:])][0]  # next page: Unresolved Write-In
        n1, d1, t1 = table_rows(h1, rows1, stats, problems, f'{tp} tc')
        n2, d2, t2 = table_rows(h2, rows2, stats, problems, f'{vp} votes')
        n3, d3, t3 = table_rows(h3, rows3, stats, problems, f'{vp} wi')
        if n1 != ['Times Cast', 'Registered Voters']:
            problems.append(f'{tp}: tc names {n1}')
        if n2 != ['Yes', 'No', 'Total Votes']:
            problems.append(f'{vp}: vote names {n2}')
        if n3 != ['Unresolved Write-In'] or t3[0] != 0:
            problems.append(f'{vp}: write-in names {n3} total {t3}')
        if set(d1) != set(d2) or len(d1) != 1:
            problems.append(f'{title}: precinct sets {set(d1)} vs {set(d2)}')
        props[title] = (d1, d2, t1, t2)

    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    # candidate sequence check: each party's tables concatenate to
    # DEM/REP + Total Votes + Unresolved Write-In
    for party, expected in (('DEM', DEM), ('REP', REP)):
        got = headers[party]
        want = expected + ['Total Votes', 'Unresolved Write-In']
        if got != want:
            problems.append(f'{party} candidate sequence {got} != {want}')
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    # per-candidate sums vs County - Total (aligned table by table)
    for (pi, ti, _), (party, names), total in zip(CAND_PAGES, page_names,
                                                  totals):
        for i, n in enumerate(names):
            s = sum(cand_votes[p][party][n] for p in stats)
            if s != total[i]:
                problems.append(f'{party} {n}: precinct sum {s} != '
                                f'County-Total {total[i]}')
    # Times Cast sums vs their County - Total rows
    for k, (pi, ti) in enumerate(TC_PAGES):
        s = sum(tc[p][k] for p in stats)
        if s != tc_tot[k][0]:
            problems.append(f'Times Cast p{pi + 1}: {s} != {tc_tot[k][0]}')
    # per-precinct candidate sums == Total Votes
    for party, seq in (('DEM', DEM), ('REP', REP)):
        for p in sorted(stats):
            s = sum(cand_votes[p][party][n] for n in seq)
            if s != cand_votes[p][party]['Total Votes']:
                problems.append(f'{p}: {party} candidates {s} != Total '
                                f'Votes {cand_votes[p][party]["Total Votes"]}')
    # Total Votes <= Times Cast per party; DEM TC + REP TC vs Voters Cast
    for party, key in (('DEM', 0), ('REP', 1)):
        for p in sorted(stats):
            if cand_votes[p][party]['Total Votes'] > tc[p][key]:
                problems.append(f'{p}: {party} Total Votes > Times Cast')
    for p in sorted(stats):
        d, r = tc[p]
        if d + r > stats[p][2]:
            problems.append(f'{p}: DEM TC {d} + REP TC {r} > Voters Cast '
                            f'{stats[p][2]}')
        elif d + r < stats[p][2]:
            print(f'NOTE: {p}: DEM TC {d} + REP TC {r} < Voters Cast '
                  f'{stats[p][2]} (source phenomenon; verified against '
                  f'page images)')
    for title, (d1, d2, t1, t2) in props.items():
        p = list(d1)[0]
        if t1[0] != d1[p][0]:
            problems.append(f'{title}: TC {t1[0]} vs {d1[p][0]}')
        if d2[p][0] + d2[p][1] != d2[p][2]:
            problems.append(f'{title}: Yes+No != Total Votes')
        if d2[p][2] > d1[p][0]:
            problems.append(f'{title}: Total Votes {d2[p][2]} > TC {t1[0]}')
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
        for title, (d1, d2, t1, t2) in props.items():
            if p in d2:
                rows_out.append([COUNTY, p, title, '', '', 'Yes', d2[p][0]])
                rows_out.append([COUNTY, p, title, '', '', 'No', d2[p][1]])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(stats)} precincts')
    print('county: Biden',
          sum(cand_votes[p]['DEM']['Joe Biden'] for p in stats),
          'Sanders', sum(cand_votes[p]['DEM']['Bernie Sanders']
                         for p in stats),
          'Trump', sum(cand_votes[p]['REP']['Donald J. Trump']
                       for p in stats),
          'BC', sum(v[2] for v in stats.values()))


if __name__ == '__main__':
    main()