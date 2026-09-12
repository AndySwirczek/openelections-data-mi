"""Parse Otsego County's March 10, 2020 presidential primary from the
PaddleOCR markdown of the image-only ES&S "Statement of Votes Cast"
PDF (11 pages, cached in /tmp/paddleocr_md_mar2020/
Otsego_MI_20200312112813906).

- p1: countywide voting stats -- 13 precincts, RV 22,074.  The printed
  Cards Cast / Voters Cast county totals (5,108) were HAND-CORRECTED
  to 5,109 on the source page (Livingston's Cards Cast 471 struck and
  rewritten 472 -- 'board adjusted to reflect (1) spoiled ballot ...
  as well as replacement ballot'); the OCR captures both values as a
  fused '5108 5109' cell.  The corrected 5,109 == the per-precinct
  sum, so it is used here.
- p2-p5: DEM President -- per-precinct Times Cast table (p2 t0), then
  candidate tables spanning pages (Bennet + Biden on p2 t1, Bloomberg
  through Klobuchar on p3, Sanders through Uncommitted on p4), then
  Total Votes + Unresolved Write-In on p5.  p6-p7: the same for REP
  (Times Cast, Sanford + Trump, Walsh / Weld / Uncommitted / TV / WI).
- p8-p11: two C.O.O.R. ISD proposals ('Education Mill Prop' Yes 25 /
  No 9, 'Operating Mill Prop' Yes 23 / No 10) voted only by Otsego
  Lake Township, Precinct 1, each with a Times Cast table and an
  Unresolved Write-In table (both 0).  p11's 'Special Education
  Millage Proposal (Restoration of Headlee Reduction)' text is the
  ballot wording on the certification page, not a third contest.
- Times Cast labels wrap with embedded newlines ('City of Gaylord,
  Ward 1,\\nPrecinct 1') -- cells are whitespace-normalized.  The TC
  headers are OCR-mangled ('Times Cast | Registered' and a phantom
  column) so only the first value column is read from them.

Times Cast is the per-party ballots cast (DEM 2,755 / REP 2,353);
Total Votes (DEM 2,753 / REP 2,340) is the candidate sum --
Unresolved Write-In (DEM 0 / REP 6) sits outside it and is not
emitted (March convention).  Source phenomenon: Otsego Lake's DEM +
REP Times Cast (416 + 328 = 744) falls 1 short of its hand-corrected
Cards Cast 745 -- the same blank/unregistered-card shortfall as
Kalkaska; the check is a bound (<=).  Emission per precinct: 16 DEM +
5 REP candidates + Ballots Cast DEM / REP + proposal Yes / No rows.
Checks: per-candidate precinct sums == printed County - Total,
candidate sequence, per-precinct candidates == Total Votes, TV <=
Times Cast, proposal TC == precinct Cards Cast.
"""
import csv
import re

CACHE = '/tmp/paddleocr_md_mar2020/Otsego_MI_20200312112813906'
OUT = '2020/counties/20200310__mi__primary__president__otsego__precinct.csv'
COUNTY = 'Otsego'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

SKIP_ROWS = {'County', 'Otsego County', 'Otsego County - Total',
             'Cumulative', 'Cumulative - Total'}
TOTAL_ROW = 'County - Total'

# (page, table_index, party) for the candidate tables, in page order
CAND_PAGES = [(1, 1, 'DEM'), (2, 0, 'DEM'), (3, 0, 'DEM'), (4, 0, 'DEM'),
              (5, 1, 'REP'), (6, 0, 'REP')]
# (page, table_index) for the per-party Times Cast tables
TC_PAGES = [(1, 0), (5, 0)]
# proposal title -> (stats page, votes page, write-in page)
PROPOSALS = [('C.O.O.R. ISD Education Mill Prop', 'p008', 'p008', 'p009'),
             ('C.O.O.R. ISD Operating Mill Prop', 'p010', 'p010', 'p011')]

CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TABLE_RE = re.compile(r'<table.*?</table>', re.S)


def ival(s):
    s = s.strip()
    return int(s.replace(',', '')) if re.fullmatch(r'[\d,]+', s) else None


def load_page(page):
    """-> list of (header, rows) for every table on the page; cells are
    whitespace-normalized (labels wrap with embedded newlines)."""
    md = open(f'{CACHE}/{page}.md').read()
    out = []
    for t in TABLE_RE.findall(md):
        rows = [[' '.join(c.replace('\\n', ' ').split())
                 for c in CELL_RE.findall(r)]
                for r in re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.S)]
        out.append((rows[0], rows[1:]))
    return out


def names_of(header):
    """-> candidate names from a header row: drop empty cells, merge the
    OCR-split 'Unresolved' + 'Write-In' / 'Registered' + 'Voters' pairs."""
    MERGE = {('Unresolved', 'Write-In'), ('Registered', 'Voters')}
    out, i = [], 0
    while i < len(header):
        h = header[i]
        if not h:
            i += 1
            continue
        if i + 1 < len(header) and (h, header[i + 1]) in MERGE:
            out.append(f'{h} {header[i + 1]}')
            i += 2
            continue
        out.append(h)
        i += 1
    return out


def table_rows(header, rows, stats, problems, where, take=None):
    """-> ({precinct: [vals]}, county_total): numeric cells read
    left-to-right (OCR filler '' / '-' cells dropped)."""
    names = names_of(header[1:])
    data, total = {}, None

    def cells(r):
        vals = [ival(c) for c in r[1:]]
        if any(v is None for v in vals):
            vals = [v for v in vals if v is not None]
        return vals if take is None else vals[:take]

    for r in rows:
        if r[0] in SKIP_ROWS:
            continue
        vals = cells(r)
        if take is not None:
            if not vals:
                problems.append(f'{where}: {r[0]!r} has no values')
                continue
        elif len(vals) != len(names):
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
    # countywide voting stats (p1); county Cards/Voters Cast total was
    # hand-corrected 5,108 -> 5,109 on the source page
    CTY_TOTAL = [22074, 5109, 5109]
    stats = {}
    header, rows = load_page('p001')[0]
    if header[:4] != ['Precinct', 'Registered Voters', 'Cards Cast',
                      'Voters Cast']:
        problems.append(f'p001 header {header}')
    for r in rows:
        if r[0] in SKIP_ROWS or r[0].endswith('- Total'):
            continue
        vals = [ival(v) for v in r[1:4]]
        if any(v is None for v in vals):
            problems.append(f'p001: {r[0]!r} -> {vals}')
        else:
            stats[r[0]] = vals
    if len(stats) != 13:
        problems.append(f'stats: {len(stats)} precincts')
    if problems:
        for p in problems:
            print('PROBLEM:', p)
        return
    pages = [load_page(f'p{n:03d}') for n in range(1, 12)]

    # per-party Times Cast tables (headers OCR-mangled; read col 0)
    tc = {p: [None, None] for p in stats}
    tc_tot = []
    for k, (pi, ti) in enumerate(TC_PAGES):
        header, rows = pages[pi][ti]
        if header[1] != 'Times Cast':
            problems.append(f'p{pi + 1} t{ti}: header {header[:3]}')
        names, data, total = table_rows(header, rows, stats, problems,
                                        f'p{pi + 1} t{ti}', take=1)
        if set(data) != set(stats):
            problems.append(f'p{pi + 1} t{ti}: precinct set differs')
        if names[:1] != ['Times Cast']:
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

    # proposals: single-precinct Times Cast + Yes/No + write-in tables
    props = {}
    for title, tp, vp, wp in PROPOSALS:
        h1, rows1 = pages[int(tp[1:]) - 1][0]
        h2, rows2 = pages[int(vp[1:]) - 1][1]
        h3, rows3 = pages[int(wp[1:]) - 1][0]
        n1, d1, t1 = table_rows(h1, rows1, stats, problems, f'{tp} tc')
        n2, d2, t2 = table_rows(h2, rows2, stats, problems, f'{vp} votes')
        n3, d3, t3 = table_rows(h3, rows3, stats, problems, f'{wp} wi')
        if n1 != ['Times Cast', 'Registered Voters']:
            problems.append(f'{tp}: tc names {n1}')
        if n2 != ['Yes', 'No', 'Total Votes']:
            problems.append(f'{vp}: vote names {n2}')
        if n3 != ['Unresolved Write-In'] or t3[0] != 0:
            problems.append(f'{wp}: write-in names {n3} total {t3}')
        if set(d1) != set(d2) or len(d1) != 1:
            problems.append(f'{title}: precinct sets {set(d1)} vs {set(d2)}')
        props[title] = (d1, d2, t1, t2)

    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    # candidate sequence check
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
    # per-precinct candidates == TV; TV <= TC; DEM+REP TC <= Cards Cast
    for party, seq in (('DEM', DEM), ('REP', REP)):
        for p in sorted(stats):
            s = sum(cand_votes[p][party][n] for n in seq)
            if s != cand_votes[p][party]['Total Votes']:
                problems.append(f'{p}: {party} candidates {s} != Total '
                                f'Votes {cand_votes[p][party]["Total Votes"]}')
            if cand_votes[p][party]['Total Votes'] > tc[p][0 if party ==
                                                           'DEM' else 1]:
                problems.append(f'{p}: {party} Total Votes > Times Cast')
    for p in sorted(stats):
        d, r = tc[p]
        if d + r > stats[p][2]:
            problems.append(f'{p}: DEM TC {d} + REP TC {r} > Cards Cast '
                            f'{stats[p][2]}')
        elif d + r < stats[p][2]:
            print(f'NOTE: {p}: DEM TC {d} + REP TC {r} < Cards Cast '
                  f'{stats[p][2]} (source phenomenon)')
    for title, (d1, d2, t1, t2) in props.items():
        p = list(d1)[0]
        if t1[0] != d1[p][0]:
            problems.append(f'{title}: TC total {t1[0]} != {d1[p][0]}')
        if d2[p][0] + d2[p][1] != d2[p][2]:
            problems.append(f'{title}: Yes+No != Total Votes')
        if d2[p][2] > d1[p][0]:
            problems.append(f'{title}: Total Votes {d2[p][2]} > TC {d1[p][0]}')
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
          'Trump', sum(cand_votes[p]['REP']['Donald J. Trump'] for p in stats),
          'BC', sum(v[2] for v in stats.values()))


if __name__ == '__main__':
    main()