"""Parse Dickinson County's March 10, 2020 presidential primary from the
PaddleOCR markdown of the image-only ES&S "Results per Precinct
Dickinson Mar 2020 Unofficial" PDF (4 pages, cached in
/tmp/paddleocr_md_mar2020/Dickinson_MI_March_2020_Results_per_Precinct_
unofficial_).

Same contest-major format as Huron: President (DEM) on p1 (header + 15
precincts + Total), President (REP) spans p1-p2 (header + 11 precincts
on p1, 4 more + Total on p2, no repeated header), then 15 local
proposals across p2-p4 (the Dickinson-Iron District Health Additional
Millage spans p2-p3; most tables carry a 'Precinct | Yes | No' header,
the p3 continuation does not).

Page 4 also carries two OCR-garbled HAND ANNOTATIONS ('add in
Faithhorn' / 'Faith horn', 'Norway Sinking Millage', Yes 30 / N10 / 46)
-- notes about out-of-county Faithorn Township (Menominee County)
votes; they are not data and are skipped.  The Norway-Vulcan Area
Schools table itself prints 'Faithorn Township, Precinct 1 (OOC)'
0 / 0, which is kept as printed.

OCR misreads 'Joe Sostak' -- canon to 'Joe Sestak'.  Write-in column
not emitted (March convention); its printed Total must be 0.

Emission: 16 DEM + 5 REP presidential candidates per precinct +
proposal Yes / No rows.  No RV / BC data.  Checks: precinct rows sum
to each printed Total row, candidate sets, identical DEM/REP precinct
sets, and every table either fully classified or reported as an
annotation.
"""
import csv
import re

CACHE = ('/tmp/paddleocr_md_mar2020/'
         'Dickinson_MI_March_2020_Results_per_Precinct_unofficial_')
OUT = ('2020/counties/20200310__mi__primary__president__dickinson__'
       'precinct.csv')
COUNTY = 'Dickinson'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

CANON = {'Joe Sostak': 'Joe Sestak'}

# OCR misread of the printed value (verified against the PDF page image)
CORRECTIONS = {
    ('Dickinson County Road Millage Request',
     'City of Iron Mountain, Ward 2, Precinct 2'): {1: 138},
}

CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TABLE_RE = re.compile(r'<table.*?</table>', re.S)
KNOWN = ['City of Iron Mountain, Ward 1, Precinct 1',
         'City of Iron Mountain, Ward 2, Precinct 2',
         'City of Iron Mountain, Ward 3, Precinct 3',
         'City of Kingsford, Precinct 1', 'City of Kingsford, Precinct 2',
         'City of Norway, Precinct 1', 'Breen Township, Precinct 1',
         'Breitung Township, Precinct 1', 'Breitung Township, Precinct 2',
         'Breitung Township, Precinct 3', 'Felch Township, Precinct 1',
         'Norway Township, Precinct 1', 'Sagola Township, Precinct 1',
         'Waucedah Township, Precinct 1', 'West Branch Township, Precinct 1',
         'Faithorn Township, Precinct 1 (OOC)']


def ival(s):
    s = s.strip()
    return int(s.replace(',', '')) if re.fullmatch(r'[\d,]+', s) else None


def parse_tables():
    """-> [(title_or_None, [row_cells...])] in page order."""
    out = []
    for n in range(1, 5):
        md = open(f'{CACHE}/p{n:03d}.md').read()
        text = TABLE_RE.sub('\x00', md)
        parts = text.split('\x00')
        for i, t in enumerate(TABLE_RE.finditer(md)):
            lines = [' '.join(re.sub(r'<[^>]+>', ' ', x).split())
                     for x in parts[i].splitlines()]
            lines = [x for x in lines if x.strip()
                     and not x.startswith('#')
                     and not re.fullmatch(r'[\d\-: ]+', x.strip())]
            title = ' '.join(lines).replace('(Vote for 1)', '').strip()
            rows = [[c.strip() for c in CELL_RE.findall(r)]
                    for r in re.findall(r'<tr[^>]*>(.*?)</tr>', t.group(0),
                                        re.S)]
            out.append((title or None, rows))
    return out


def main():
    problems = []
    notes = []
    contests = []  # (title, header_or_None, {precinct: vals}, total_or_None)
    cur = None
    for title, rows in parse_tables():
        classified = 0
        for row in rows:
            head = row[0]
            if head == 'Precinct':
                cur = {'title': title, 'header': row[1:], 'rows': {},
                       'total': None}
                contests.append(cur)
                classified += 1
                continue
            if head.lower() == 'total':
                if cur is None:
                    problems.append('Total row with no contest')
                    continue
                if cur['total'] is not None:
                    problems.append(f'{cur["title"]}: duplicate Total row')
                cur['total'] = [ival(v) for v in row[1:]]
                classified += 1
                continue
            if head in KNOWN:
                if cur is None:
                    problems.append(f'data row {head!r} with no contest')
                    continue
                vals = [ival(v) for v in row[1:]]
                if head in cur['rows']:
                    problems.append(f'{cur["title"]}: duplicate {head!r}')
                fix = CORRECTIONS.get((cur['title'], head))
                if fix:
                    for i, v in fix.items():
                        vals[i] = v
                cur['rows'][head] = vals
                classified += 1
        if classified == 0:
            notes.append((title, rows))
    for title, rows in notes:
        print('NOTE: unclassified table (source annotation?):', title,
              [' '.join(r) for r in rows][:4])
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    dem = next(c for c in contests if c['title'] == 'President (DEM)')
    rep = next(c for c in contests if c['title'] == 'President (REP)')
    for c, want in ((dem, DEM + ['Write-in']), (rep, REP + ['Write-in'])):
        got = [CANON.get(h, h) for h in c['header']]
        if got != want:
            problems.append(f'{c["title"]}: headers {got}')
    if set(dem['rows']) != set(rep['rows']):
        problems.append('DEM/REP precinct sets differ')
    for c in contests:
        if c['total'] is None:
            problems.append(f'{c["title"]}: no Total row')
            continue
        for i, h in enumerate(c['header']):
            s = sum(v[i] for v in c['rows'].values())
            if s != c['total'][i]:
                problems.append(f'{c["title"]} {h}: precinct sum {s} != '
                                f'Total {c["total"][i]}')
    for c in (dem, rep):
        # write-in votes are nonzero here (DEM 3 / REP 3) but the column
        # is not emitted (March convention); totals are printed above
        print(f'NOTE: {c["title"]} write-in Total: {c["total"][-1]}')
    props = [c for c in contests if c is not dem and c is not rep]
    for c in props:
        got = [CANON.get(h, h) for h in c['header']]
        if got != ['Yes', 'No']:
            problems.append(f'{c["title"]}: headers {got}')
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    rows_out = []
    for p in [k for k in KNOWN if k in dem['rows']]:
        for name, v in zip([CANON.get(h, h) for h in dem['header']][:-1],
                           dem['rows'][p]):
            rows_out.append([COUNTY, p, 'President', '', 'DEM', name, v])
        for name, v in zip(rep['header'][:-1], rep['rows'][p]):
            rows_out.append([COUNTY, p, 'President', '', 'REP', name, v])
        for c in props:
            if p in c['rows']:
                for name, v in zip(c['header'], c['rows'][p]):
                    rows_out.append([COUNTY, p, c['title'], '', '', name, v])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(dem["rows"])} precincts')
    print('DEM Total:', dict(zip([CANON.get(h, h) for h in dem['header']],
                                 dem['total'])))
    print('REP Total:', dict(zip(rep['header'], rep['total'])))


if __name__ == '__main__':
    main()