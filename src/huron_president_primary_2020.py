"""Parse Huron County's March 10, 2020 presidential primary from the
PaddleOCR markdown of the image-only ES&S "Results per Precinct Huron
Mar 2020 Official" PDF (3 pages, cached in /tmp/paddleocr_md_mar2020/
Huron_MI_Primary).

Contest-major HTML tables: 'President (DEM) (Vote for 1)' spans pages
1-2 (the p2 table is a continuation with no title of its own -- it
carries 10 more precinct rows plus the printed Total row), 'President
(REP) (Vote for 1)' on page 2 (header + 31 precincts + Total), and
three page-3 local proposals (Huron Township, Caseville Public Schools,
Elkton-Pigeon-Bay Port Laker School District) each with a Total row.
Every table is Precinct | candidate... | Write-in, one row per
precinct, values comma-grouped.

No Registered Voters / Ballots Cast data (ES&S Results per Precinct).

Emission: 16 DEM + 5 REP presidential candidates per precinct (Write-in
column not emitted, March convention) + proposal Yes / No rows.
Checks: precinct rows sum to each printed Total row, DEM/REP candidate
sets, and identical precinct sets across the DEM, REP tables.
"""
import csv
import re

CACHE = '/tmp/paddleocr_md_mar2020/Huron_MI_Primary'
OUT = '2020/counties/20200310__mi__primary__president__huron__precinct.csv'
COUNTY = 'Huron'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

CELL_RE = re.compile(r'<td[^>]*>([^<]*)</td>')
TABLE_RE = re.compile(r'<table.*?</table>', re.S)


def ival(s):
    return int(s.strip().replace(',', ''))


def parse_tables():
    """-> list of (title_or_None, header_or_None, rows) in page order.

    The page-2 continuation of the DEM table repeats no header row --
    its first row is data -- so header is None when the first row is a
    precinct rather than 'Precinct'.
    """
    out = []
    for n in range(1, 4):
        md = open(f'{CACHE}/p{n:03d}.md').read()
        text = TABLE_RE.sub('\x00TABLE\x00', md)
        # markdown between tables carries the contest titles
        parts = text.split('\x00TABLE\x00')
        for i, t in enumerate(TABLE_RE.finditer(md)):
            title = parts[i].strip().splitlines()
            title = ' '.join(x for x in title if x.strip()
                             and not x.startswith('#')
                             and not re.fullmatch(r'[\d\-: ]+', x.strip()))
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', t.group(0), re.S)
            cells = [[c.strip() for c in CELL_RE.findall(r)] for r in rows]
            if cells and cells[0][0].lower() == 'precinct':
                out.append((title or None, cells[0], cells[1:]))
            else:
                out.append((title or None, None, cells))
    return out


def main():
    problems = []
    contests = []  # (title, header, {precinct: [votes]}, total_row)
    cur = None
    for title, header, rows in parse_tables():
        if title:
            cur = {'title': title, 'header': header, 'rows': {}, 'total': None}
            contests.append(cur)
        elif cur is None:
            problems.append('table with no preceding contest title')
            continue
        if header is not None:
            if len(header) != len(cur['header']):
                problems.append(f'{cur["title"]}: header width {len(header)} '
                                f'!= {len(cur["header"])}')
        for row in rows:
            if len(row) != len(cur['header']):
                problems.append(f'{cur["title"]}: row {row[:1]} has '
                                f'{len(row)} cells, want {len(cur["header"])}')
                continue
            vals = [ival(v) for v in row[1:]]
            if row[0].lower() == 'total':
                cur['total'] = vals
            else:
                if row[0] in cur['rows']:
                    problems.append(f'{cur["title"]}: duplicate precinct '
                                    f'{row[0]!r}')
                cur['rows'][row[0]] = vals
    if problems:
        for p in problems:
            print('PROBLEM:', p)
        return

    for c in contests:
        if c['total'] is None:
            problems.append(f'{c["title"]}: no Total row')
            continue
        for i, h in enumerate(c['header'][1:]):
            s = sum(v[i] for v in c['rows'].values())
            if s != c['total'][i]:
                problems.append(f'{c["title"]} {h}: precinct sum {s} != '
                                f'Total {c["total"][i]}')
    dem = next(c for c in contests if c['title'].startswith('President (DEM)'))
    rep = next(c for c in contests if c['title'].startswith('President (REP)'))
    for c, want in ((dem, DEM + ['Write-in']), (rep, REP + ['Write-in'])):
        if c['header'][1:] != want:
            problems.append(f'{c["title"]}: headers {c["header"][1:]}')
    for name in (dem, rep):
        if set(name['rows']) != set(dem['rows']):
            problems.append(f'{name["title"]}: precinct set differs from DEM')
    # write-in totals (not emitted, but printed as 0 countywide here)
    for c in (dem, rep):
        if c['total'][-1] != 0:
            problems.append(f'{c["title"]}: write-in Total '
                            f'{c["total"][-1]} != 0')
    props = [c for c in contests if c is not dem and c is not rep]
    for c in props:
        if c['header'][1:] != ['Yes', 'No']:
            problems.append(f'{c["title"]}: headers {c["header"][1:]}')
        for p, v in c['rows'].items():
            if v[0] + v[1] > dem['total'][1] + rep['total'][1]:
                pass  # proposals are jurisdiction-scoped; checked vs Total
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    rows_out = []
    for p in sorted(dem['rows']):
        for name, v in zip(dem['header'][1:-1], dem['rows'][p]):
            rows_out.append([COUNTY, p, 'President', '', 'DEM', name, v])
        for name, v in zip(rep['header'][1:-1], rep['rows'][p]):
            rows_out.append([COUNTY, p, 'President', '', 'REP', name, v])
        for c in props:
            if p in c['rows']:
                title = re.sub(r'\s*\(Vote for 1\)\s*$', '', c['title'])
                for name, v in zip(c['header'][1:], c['rows'][p]):
                    rows_out.append([COUNTY, p, title, '', '', name, v])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(dem["rows"])} precincts')
    print('DEM Total:', dict(zip(dem['header'][1:], dem['total'])))
    print('REP Total:', dict(zip(rep['header'][1:], rep['total'])))


if __name__ == '__main__':
    main()