"""Parse Menominee County's March 10, 2020 presidential primary from the
PaddleOCR markdown of the image-only canvassed-results PDF (12 pages,
cached in /tmp/paddleocr_md_mar2020/
Menominee_MI_presidential_primary_3_10_20_canvassed_results).

- p2: ES&S SOVC voting stats (Precinct | Registered Voters | Voters
  Cast | % Turnout) -- 20 precincts, RV 17,596 / VC 3,972.
- p3: President (DEM) -- one table with Times Cast, Registered Voters,
  the 16 DEM candidates and Total Votes per precinct; county Total row
  is the anchor.  p4: President (REP) -- same layout (header row 0
  carries the names, row 1 the 'Precinct' label header); four City of
  Menominee Ward labels wrap onto a following valueless row, and
  '1 Menominee Township, Precinct 2' carries the wrapped tail of the
  previous label -- fragments are joined / leading digits stripped and
  matched against the stats labels ('Ingalston' -> 'Ingallston').
- p5: two countywide proposals (County Library Millage, County 911 and
  Central Dispatch), all 20 precincts.  p6: Mellen Township proposal
  (title clipped in the source: it begins 'Township Proposal for
  Mellen Township...' with no preceding words on p5) and the
  Norway-Vulcan Area Schools Local School District proposal (Faithorn
  Township, Precinct 1 only -- the same votes Dickinson's hand
  annotation refers to).  p6's Mellen table splits 'Registered' +
  'Voters' across two header cells.
- p7-p11 are the Board of Canvassers' statement/certificate pages;
  their TOTAL rows corroborate the proposal and Total Votes figures.

OCR misread corrected against the rendered page image: Nadeau DEM
Times Cast 81 -> printed 80.  Source phenomenon: printed DEM + REP
Times Cast (3,777) falls short of printed Voters Cast (3,972) in 19
of 20 precincts (diffs 4-33) while Gourley's two TC values (78 + 78)
EXCEED its Voters Cast (78) -- internally inconsistent in the source,
so per-precinct TC-vs-VC is printed as NOTE, never asserted.
Emission per precinct: Registered Voters, Ballots Cast (Voters Cast)
plus Ballots Cast DEM/REP (Times Cast), 16 DEM + 5 REP candidates,
proposal Yes/No rows (proposal Times Cast not emitted; write-in
columns do not exist in this report).
"""
import csv
import re

CACHE = ('/tmp/paddleocr_md_mar2020/'
         'Menominee_MI_presidential_primary_3_10_20_canvassed_results')
OUT = ('2020/counties/20200310__mi__primary__president__menominee__'
       'precinct.csv')
COUNTY = 'Menominee'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

TOTAL_ROW = 'Menominee County Michigan - Total'
COUNTY_ROW = 'Menominee County Michigan'
CANON = {'Michael Bennett': 'Michael Bennet',
         'Michael R Bloomberg': 'Michael R. Bloomberg',
         'Ingalston Township, Precinct 1': 'Ingallston Township, Precinct 1'}
# OCR misreads verified against the rendered PDF page images (none --
# the Nadeau Times Cast 81 read at 150dpi as 80 but is printed 81)
CORRECTIONS = {}

PROPOSALS = {
    'County Library Millage Proposal for Menominee County, Menominee '
    'County Michigan': 'p005t0',
    'County 911 and Central Dispatch for Menominee County, Menominee '
    'County Michigan': 'p005t1',
    'Township Proposal for Mellen Township, Menominee County Michigan':
        'p006t0',  # title clipped in the source
    'Local School District Proposal for Norway-Vulcan Area Schools, '
    'Menominee County Michigan': 'p006t1',
}

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


def names_of(cells):
    """-> column names: drop empties, merge OCR-split pairs."""
    MERGE = {('Unresolved', 'Write-In'), ('Registered', 'Voters')}
    out, i = [], 0
    while i < len(cells):
        c = cells[i].strip()
        if not c:
            i += 1
            continue
        if i + 1 < len(cells) and (c, cells[i + 1].strip()) in MERGE:
            out.append(f'{c} {cells[i + 1].strip()}')
            i += 2
            continue
        out.append(CANON.get(c, c))
        i += 1
    return out


def data_vals(cells):
    """-> numeric values left-to-right (empty filler cells dropped)."""
    return [v for v in (ival(c) for c in cells) if v is not None]


def repair_label(label, stats):
    """-> full precinct label: canon, exact, leading-digit strip,
    prefix."""
    label = label.strip()
    label = CANON.get(label, label)
    if label in stats:
        return label
    stripped = re.sub(r'^\d+\s*', '', label)
    if stripped in stats:
        return stripped
    m = [s for s in stats if s.startswith(stripped.rstrip(' ,'))]
    if len(m) == 1:
        return m[0]
    return None


def main():
    problems = []

    # voting stats (p2)
    stats, cty = {}, None
    header, rows = load_page('p002')[0]
    if header[:3] != ['Precinct', 'Registered Voters', 'Voters Cast']:
        problems.append(f'p002 header {header}')
    for r in rows:
        if r[0] in (COUNTY_ROW,):
            continue
        if r[0] == TOTAL_ROW:
            cty = data_vals(r[1:])
            continue
        vals = data_vals(r[1:])
        if len(vals) != 2:
            problems.append(f'p002: {r[0]!r} -> {vals}')
            continue
        stats[r[0]] = vals
    if cty is None or len(stats) != 20:
        problems.append(f'stats: {len(stats)} precincts, county {cty}')
    if problems:
        for p in problems:
            print('PROBLEM:', p)
        return

    # President (DEM) p3 / President (REP) p4
    pres = {}
    for page, party, seq in (('p003', 'DEM', DEM), ('p004', 'REP', REP)):
        header, rows = load_page(page)[0]
        names = names_of(header[1:])
        if names != ['Times Cast', 'Registered Voters'] + seq + \
                ['Total Votes']:
            problems.append(f'{page}: names {names}')
        cur = {}
        last_full = None  # label of the last entry, for wrap fragments
        for r in rows:
            label = r[0].strip()
            vals = data_vals(r[1:])
            if label in (COUNTY_ROW, '') or label == 'Precinct':
                continue
            if label == TOTAL_ROW:
                pres[party] = (cur, vals, names)
                continue
            if not vals:
                # valueless continuation of the previous row's label
                if last_full is None:
                    problems.append(f'{page}: fragment {label!r} with no '
                                    f'preceding row')
                    continue
                if label in last_full:
                    # wrapped tail already consumed by the prefix repair
                    continue
                full = repair_label(f'{last_full} {label}', stats)
                if full is None:
                    problems.append(f'{page}: cannot join {last_full!r} + '
                                    f'{label!r}')
                    continue
                cur[full] = cur.pop(last_full)
                last_full = full
                continue
            full = repair_label(label, stats)
            if full is None:
                problems.append(f'{page}: unmatched label {label!r}')
                continue
            if full in cur:
                problems.append(f'{page}: duplicate {full!r}')
            fix = CORRECTIONS.get((party, full), {})
            for i, v in fix.items():
                vals[i] = v
            cur[full] = vals
            last_full = full
        if set(cur) != set(stats):
            problems.append(f'{page}: precinct set differs '
                            f'({len(cur)} vs {len(stats)})')
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    # proposals
    props = {}
    for title, key in PROPOSALS.items():
        page, ti = key[:4], int(key[5])
        header, rows = load_page(page)[ti]
        if header[0].strip() == '':  # names on row 0, labels on row 1
            names = names_of(header[1:])
        else:
            names = names_of(header[1:])
        if names != ['Times Cast', 'Registered Voters', 'Yes', 'No',
                     'Total Votes']:
            problems.append(f'{key}: names {names}')
        cur, total = {}, None
        for r in rows:
            label = r[0].strip()
            if label in (COUNTY_ROW, '', 'Precinct'):
                continue
            vals = data_vals(r[1:])
            if label == TOTAL_ROW:
                total = vals
                continue
            full = repair_label(label, stats)
            if full is None:
                problems.append(f'{key}: unmatched label {label!r}')
                continue
            if full in cur:
                problems.append(f'{key}: duplicate {full!r}')
            cur[full] = vals
        props[title] = (cur, total)
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
            print(f'{len(problems)} problems; not writing')
            return

    # --- verification ---
    for party, seq in (('DEM', DEM), ('REP', REP)):
        cur, total, names = pres[party]
        if sum(v[0] for v in cur.values()) != total[0]:
            problems.append(f'{party} TC sum != {total[0]}')
        for i, n in enumerate(seq):
            s = sum(v[2 + i] for v in cur.values())
            if s != total[2 + i]:
                problems.append(f'{party} {n}: sum {s} != {total[2 + i]}')
        for p, v in cur.items():
            s = sum(v[2 + i] for i in range(len(seq)))
            if s != v[-1]:
                problems.append(f'{party} {p}: candidates {s} != TV {v[-1]}')
            if v[-1] > v[0]:
                problems.append(f'{party} {p}: TV {v[-1]} > TC {v[0]}')
        if total[-1] != sum(total[2 + i] for i in range(len(seq))):
            problems.append(f'{party} county TV {total[-1]} != candidate '
                            f'sums')
    for p, (rv, vc) in stats.items():
        d = pres['DEM'][0][p][0] + pres['REP'][0][p][0]
        if d != vc:
            print(f'NOTE: {p}: DEM TC + REP TC = {d} vs Voters Cast {vc} '
                  f'(source phenomenon, not asserted)')
    for title, (cur, total) in props.items():
        for p, v in cur.items():
            if v[2] + v[3] != v[4]:
                problems.append(f'{title} {p}: Yes+No != TV')
            if v[0] != stats[p][1]:
                problems.append(f'{title} {p}: TC {v[0]} != Voters Cast '
                                f'{stats[p][1]}')
        s = [sum(v[i] for v in cur.values()) for i in range(5)]
        if s != total:
            problems.append(f'{title}: precinct sums {s} != Total {total}')
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    rows_out = []
    for p in sorted(stats):
        rows_out.append([COUNTY, p, 'Registered Voters', '', '', '',
                         stats[p][0]])
        rows_out.append([COUNTY, p, 'Ballots Cast', '', '', '', stats[p][1]])
        for party in ('DEM', 'REP'):
            cur, total, names = pres[party]
            rows_out.append([COUNTY, p, 'Ballots Cast', '', party, '',
                             cur[p][0]])
        for party, seq in (('DEM', DEM), ('REP', REP)):
            cur, total, names = pres[party]
            for i, n in enumerate(seq):
                rows_out.append([COUNTY, p, 'President', '', party, n,
                                 cur[p][2 + i]])
        for title, (cur, total) in props.items():
            if p in cur:
                rows_out.append([COUNTY, p, title, '', '', 'Yes', cur[p][2]])
                rows_out.append([COUNTY, p, title, '', '', 'No', cur[p][3]])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(stats)} precincts')
    print('county: Biden', pres['DEM'][1][3], 'Sanders', pres['DEM'][1][11],
          'Trump', pres['REP'][1][3], 'RV', cty[0], 'BC', cty[1])


if __name__ == '__main__':
    main()