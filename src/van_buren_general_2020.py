#!/usr/bin/env python3
"""Parse Van Buren County's Nov 2020 general election from the
'Van Buren MI Results per Precinct Data report.pdf' (52pp,
text-extractable, contest-major tables).

Grammar of the report:
  - Page furniture: 'Van Buren County' header, 'Results per Precinct'
    cover lines, 'Precincts fully reported: 33 of 33', 'Last Updated',
    the page-1 summary lines, and 'NN/52' page numbers.
  - A block = contest title line, then a header line 'Precinct <cells>'
    (bold headers double every character: 'PPrreecciinncctt'), then
    data rows, then a 'Total <values>' row.  Contests spanning pages
    repeat the header on each continuation page (no title); the Total
    row prints once, wherever the block ends.
  - Value cells are LEFT-aligned at the header cell's x0, so a value's
    x0 identifies its column; per block the anchors are clustered from
    the data values (gap <= 6).
  - Wrapped pages (the wide tables, roughly pages 6-12): the precinct
    name wraps to 3 physical lines around the id line —
    'City of' / '11 Bangor 325 301 ... 1' / 'Precinct 1'.  All values
    still sit on the id line (precinct id at x0 27-35 identifies the
    row), so the surrounding name fragments are ignored and the
    canonical id->name map comes from the unwrapped Straight Party
    block at the end of the report.
  - Multi-line header cells ('Tom'/'Mair', 'Total'/'write-'/'in',
    'Bridgette Abraham-'/'Guzman') are stacked at their column anchor
    one or two lines above/below the header line; they are collected
    from the fragment lines adjacent to the header line and joined in
    y order per column (dehyphenating '-'-terminated fragments).
  - Cells containing 'write' are the write-in column; proposal blocks
    have Yes/No cells; the Straight Party block's cells are party
    names with no write-in column.

Verification: per-block per-column precinct sums == the printed Total
row; per (office, district, candidate) sums against the Van Buren rows
of the CENR county file for the offices it covers (the county file has
no Van Buren rows at all, so that check is vacuous here and the
printed Total row carries the whole load).

Party codes: the source prints no party lines and the CENR county file
has no Van Buren rows, so partisan candidates take hardcoded codes in
PARTY below — the 2020 ballot's own party assignments, cross-checked
against committed county files carrying party for the same offices
(20201103__mi__general__keweenaw__precinct.csv and the statewide
20201103__mi__general__precinct.csv).  Straight Party rows are coded
from the party names the source prints in that block's cells.
"""

import csv
import os
import re
import sys

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/general/'
       'Van Buren MI Results per Precinct Data report.pdf')
OUT = '2020/counties/20201103__mi__general__van_buren__precinct.csv'
COUNTY_FILE = '2020/20201103__mi__general__county.csv'
COUNTY = 'Van Buren'

SP_CODES = {
    'Democratic Party': 'DEM', 'Republican Party': 'REP',
    'Libertarian Party': 'LIB', 'U.S. Taxpayers Party': 'UST',
    'Working Class Party': 'WCP', 'Green Party': 'GRN',
    'Natural Law Party': 'NLP',
}

# The CENR county file has no Van Buren rows, so partisan offices can't
# take their party from it.  The source prints no party lines either;
# these codes are the 2020 general ballot's own party assignments,
# cross-checked against committed county files that carry party for the
# same offices (2020/counties/20201103__mi__general__keweenaw__precinct.csv
# and the statewide 20201103__mi__general__precinct.csv).  Candidate
# spellings are the Van Buren source's verbatim ones ('Carl Meyer',
# 'Brian Mosaliam', 'Mary Anne Hering' differ from other counties).
PARTY = {
    # President
    ('President', 'Joseph R. Biden'): 'DEM',
    ('President', 'Donald J. Trump'): 'REP',
    ('President', 'Jo Jorgensen'): 'LIB',
    ('President', 'Don Blankenship'): 'UST',
    ('President', 'Howie Hawkins'): 'GRN',
    ('President', 'Rocky De La Fuente'): 'NLP',
    # U.S. Senate
    ('U.S. Senate', 'Gary Peters'): 'DEM',
    ('U.S. Senate', 'John James'): 'REP',
    ('U.S. Senate', 'Valerie L. Willis'): 'UST',
    ('U.S. Senate', 'Marcia Squier'): 'GRN',
    ('U.S. Senate', 'Doug Dern'): 'NLP',
    # U.S. House, 6th District
    ('U.S. House', 'Jon Hoadley'): 'DEM',
    ('U.S. House', 'Fred Upton'): 'REP',
    ('U.S. House', 'Jeff DePoy'): 'LIB',
    ('U.S. House', 'John Lawrence'): 'GRN',
    # State House, 66th District
    ('State House', 'Abigail Wheeler'): 'DEM',
    ('State House', 'Beth Griffin'): 'REP',
    # Member of the State Board of Education (2D 2R 2LIB 2UST 2WCP 1GRN)
    ('Member of the State Board of Education', 'Ellen Cogen Lipton'): 'DEM',
    ('Member of the State Board of Education', 'Jason Strayhorn'): 'DEM',
    ('Member of the State Board of Education', 'Tami Carlone'): 'REP',
    ('Member of the State Board of Education', 'Michelle A. Frederick'): 'REP',
    ('Member of the State Board of Education', 'Bill Hall'): 'LIB',
    ('Member of the State Board of Education', 'Richard A. Hewer'): 'LIB',
    ('Member of the State Board of Education', 'Karen Adams'): 'UST',
    ('Member of the State Board of Education', 'Douglas Levesque'): 'UST',
    ('Member of the State Board of Education', 'Mary Anne Hering'): 'WCP',
    ('Member of the State Board of Education', 'Hali McEachern'): 'WCP',
    ('Member of the State Board of Education', 'Tom Mair'): 'GRN',
    # Regent of the University of Michigan
    ('Regent of the University of Michigan', 'Mark Bernstein'): 'DEM',
    ('Regent of the University of Michigan', 'Shauna Ryder Diggs'): 'DEM',
    ('Regent of the University of Michigan', 'Sarah Hubbard'): 'REP',
    ('Regent of the University of Michigan', 'Carl Meyer'): 'REP',
    ('Regent of the University of Michigan', 'James L. Hudler'): 'LIB',
    ('Regent of the University of Michigan', 'Eric Larson'): 'LIB',
    ('Regent of the University of Michigan', 'Ronald E. Graeser'): 'UST',
    ('Regent of the University of Michigan', 'Crystal Van Sickle'): 'UST',
    ('Regent of the University of Michigan', 'Michael Mawilai'): 'GRN',
    ('Regent of the University of Michigan', 'Keith Butkovich'): 'NLP',
    # Trustee of Michigan State University
    ('Trustee of Michigan State University', 'Brian Mosaliam'): 'DEM',
    ('Trustee of Michigan State University', 'Rema Ella Vassar'): 'DEM',
    ('Trustee of Michigan State University', "Pat O'Keefe"): 'REP',
    ('Trustee of Michigan State University', 'Tonya Schuitmaker'): 'REP',
    ('Trustee of Michigan State University', 'Will Tyler White'): 'LIB',
    ('Trustee of Michigan State University', 'Janet M. Sanger'): 'UST',
    ('Trustee of Michigan State University', 'John Paul Sanger'): 'UST',
    ('Trustee of Michigan State University', 'Brandon Hu'): 'GRN',
    ('Trustee of Michigan State University', 'Robin Lea Laurain'): 'GRN',
    ('Trustee of Michigan State University',
     'Bridgette Abraham-Guzman'): 'NLP',
    # Governor of Wayne State University
    ('Governor of Wayne State University', 'Eva Garza Dewaelsche'): 'DEM',
    ('Governor of Wayne State University', 'Shirley Stancato'): 'DEM',
    ('Governor of Wayne State University', 'Don Gates'): 'REP',
    ('Governor of Wayne State University', 'Terri Lynn Land'): 'REP',
    ('Governor of Wayne State University', 'Jon Elgas'): 'LIB',
    ('Governor of Wayne State University', 'Susan Odgers'): 'GRN',
    ('Governor of Wayne State University', 'Christine C. Schwartz'): 'UST',
}

# title -> (office, district); titles not listed pass through verbatim.
OFFICE_MAP = {
    'President/Vice Pres': ('President', ''),
    'United States Senator': ('U.S. Senate', ''),
    'Congress 6th District': ('U.S. House', '6'),
    'State Legislature 66th District': ('State House', '66'),
    'State Board of Education': ('Member of the State Board of Education',
                                 ''),
    'Regent of U of M': ('Regent of the University of Michigan', ''),
    'Trustee MSU': ('Trustee of Michigan State University', ''),
    'Governor Wayne State': ('Governor of Wayne State University', ''),
    'Justice of Supreme Court': ('Justice of Supreme Court', ''),
    'Prosecuting Attorney': ('Prosecuting Attorney', ''),
    'Sheriff': ('Sheriff', ''),
    'Clerk': ('Clerk', ''),
    'Treasurer': ('Treasurer', ''),
    'Register of Deeds': ('Register of Deeds', ''),
    'Drain Commissioner': ('Drain Commissioner', ''),
    'Surveyor': ('Surveyor', ''),
    'Straight Party for Van Buren County Michigan': ('Straight Party', ''),
}
for _i, _n in enumerate('1st 2nd 3rd 4th 5th 6th 7th'.split(), 1):
    OFFICE_MAP[f'County Commissioner {_n} District'] = \
        ('County Commissioner', str(_i))

FURNITURE = [
    re.compile(r'^Van Buren County$'),
    re.compile(r'Results per Precinct'),
    re.compile(r'^Precincts fully reported'),
    re.compile(r'^Last Updated'),
    re.compile(r'^Election Report'),
    re.compile(r'Number of Registered Voters:'),
    re.compile(r'^Unofficial Results'),
    re.compile(r'^Turnout:'),
    re.compile(r'^Ballots Cast:'),
    re.compile(r'^\d+/52$'),
]

ID_RE = re.compile(r'^\d{1,2}$')


def undouble(word):
    """Collapse the doubled characters of a bold header word."""
    if (len(word) >= 2 and len(word) % 2 == 0
            and all(word[i] == word[i + 1] for i in range(0, len(word), 2))):
        return word[::2]
    return word


def get_lines(pdf):
    """Words -> lines (y-grouped), in page then y order."""
    lines = []
    for pno, page in enumerate(pdf.pages):
        words = page.extract_words(x_tolerance=3, y_tolerance=3)
        words.sort(key=lambda w: (w['top'], w['x0']))
        cur = []
        for w in words:
            if cur and abs(w['top'] - cur[0]['top']) > 2.5:
                lines.append(make_line(cur, pno))
                cur = []
            cur.append(w)
        if cur:
            lines.append(make_line(cur, pno))
    return lines


def make_line(words, page):
    return {'page': page, 'top': words[0]['top'], 'x0': words[0]['x0'],
            'words': words,
            'text': ' '.join(w['text'] for w in words)}


def classify(line):
    """furniture | title | header | id | total | frag"""
    text = line['text']
    if any(p.search(text) for p in FURNITURE):
        return 'furniture'
    w0 = line['words'][0]
    if (ID_RE.match(w0['text']) and 27 <= w0['x0'] <= 35
            and 1 <= int(w0['text']) <= 33):
        return 'id'
    if w0['text'] == 'Total' and w0['x0'] < 60:
        return 'total'
    for w in line['words']:
        if undouble(w['text']) == 'Precinct' and 40 <= w['x0'] <= 50:
            if any(x['x0'] >= 85 for x in line['words'][1:]):
                return 'header'
    return 'frag'


def cluster(xs, gap=6):
    xs = sorted(set(round(x, 1) for x in xs))
    groups = [[xs[0]]]
    for x in xs[1:]:
        if x - groups[-1][-1] <= gap:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [sum(g) / len(g) for g in groups]


def nearest(x, centers, tol):
    dists = [abs(c - x) for c in centers]
    i = min(range(len(centers)), key=lambda k: dists[k])
    return (i if dists[i] <= tol else None)


def main():
    problems, warns = [], []

    # county file: party codes + verification targets
    county_want = {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, COUNTY_FILE)
    for row in csv.DictReader(open(path)):
        if row['county'] != COUNTY:
            continue
        cand = row['candidate']
        if cand in ('Registered Voters', 'Ballots Cast'):
            continue
        if 'write' in cand.lower():
            cand = 'Write-In'
        key = (row['office'], row['district'], cand)
        county_want[key] = county_want.get(key, 0) + int(row['votes'])
    county_party = {}
    county_csv = csv.DictReader(open(path))
    for row in county_csv:
        if row['county'] != COUNTY:
            continue
        cand = row['candidate'].split('/')[0].strip()
        if 'write' in cand.lower():
            cand = 'Write-In'
        county_party[(row['office'], row['district'], cand)] = row['party']

    pdf = pdfplumber.open(SRC)
    lines = get_lines(pdf)

    # stream into blocks; every non-furniture line stays on its block
    # so the header's stacked cell fragments can be recovered later.
    blocks = []
    cur = None
    last_struct = None
    for line in lines:
        kind = classify(line)
        if kind == 'furniture':
            continue
        # classify is context-free: an alpha-only line is a block title
        # when it follows a Total row (or starts the stream), else it is
        # a wrapped precinct-name fragment.
        if kind == 'frag' and last_struct in (None, 'total'):
            kind = 'title'
        if kind == 'title':
            if cur is not None:
                blocks.append(cur)
            cur = {'title': line['text'], 'title_line': line,
                   'header': None, 'rows': [], 'total': None,
                   'all_lines': [line]}
            last_struct = 'title'
            continue
        if cur is None:
            problems.append(f'line outside block: {line["text"]!r}')
            continue
        cur['all_lines'].append(line)
        if kind == 'header':
            if cur['header'] is None:
                cur['header'] = line
            last_struct = 'header'
        elif kind == 'id':
            words = line['words']
            pid = int(words[0]['text'])
            vals = [(w['x0'], int(w['text'].replace(',', '')))
                    for w in words[1:]
                    if w['text'].replace(',', '').isdigit()]
            cur['rows'].append({'id': pid, 'page': line['page'],
                                'top': line['top'], 'vals': vals,
                                'words': words})
            last_struct = 'id'
        elif kind == 'total':
            vals = [(w['x0'], int(w['text'].replace(',', '')))
                    for w in line['words'][1:]
                    if w['text'].replace(',', '').isdigit()]
            cur['total'] = vals
            last_struct = 'total'
    if cur is not None:
        blocks.append(cur)

    # Precinct ids are per-block ordinals: the report's full-county
    # blocks list 33 precincts with 'City of South Haven Ward 3
    # Precinct 2' as 17, but the blocks where that tiny split precinct
    # has no row drop it and renumber (32 rows).  So rows are keyed by
    # their printed precinct name; the canonical 33-name map is built
    # from the full-county blocks and is needed only for the wrapped
    # wide-table blocks (State Board/Regent/MSU/WSU), which are all
    # full-county and whose names are split across the wrap lines.
    id_names = {}
    for b in blocks:
        if b['header'] is None or len(b['rows']) != 33:
            continue
        hline = b['header']
        xs = [w['x0'] for w in hline['words']
              if not (undouble(w['text']) == 'Precinct'
                      and 40 <= w['x0'] <= 50)]
        if not xs:
            continue
        boundary = min(xs) - 5
        pids = set()
        for row in b['rows']:
            pids.add(row['id'])
            name = ' '.join(w['text'] for w in row['words'][1:]
                            if w['x0'] < boundary)
            if not re.search(r'Precinct \d+$', name):
                continue  # wrapped page: name split across lines
            if id_names.setdefault(row['id'], name) != name:
                problems.append(f'id {row["id"]}: name conflict '
                                f'{id_names[row["id"]]!r} vs {name!r}')
        if pids != set(range(1, 34)):
            problems.append(f'{b["title"]!r}: ids {sorted(pids)} in a '
                            f'33-row block')
    if len(id_names) != 33:
        problems.append(f'id map has {len(id_names)} precincts, want 33')

    emit = []
    for b in blocks:
        title = b['title']
        if b['header'] is None:
            problems.append(f'{title!r}: no header line')
            continue

        # header cells: the header line plus the alpha fragment lines on
        # its page (stacked cell text sits 1-3 lines above/below it).
        hline = b['header']
        frags = [l for l in b['all_lines']
                 if l['page'] == hline['page'] and l is not hline
                 and l is not b['title_line']
                 and classify(l) == 'frag'
                 and abs(l['top'] - hline['top']) <= 45
                 and all(not w['text'].replace(',', '').isdigit()
                         for w in l['words'])]
        picked = [hline] + frags
        picked.sort(key=lambda l: l['top'])

        # column anchors: every cell is left-aligned at its anchor, so
        # within a picked line a new cell starts where the gap from the
        # previous word is >= 10pt.  Cells never start left of x0 85
        # (the precinct-name column ends before the first anchor on
        # every page; its digits top out at 80.7).
        starts = []
        for l in picked:
            prev_x1 = None
            for w in sorted(l['words'], key=lambda w: w['x0']):
                t = undouble(w['text'])
                if t == 'Precinct' and 40 <= w['x0'] <= 50:
                    prev_x1 = w['x1']
                    continue
                if w['x0'] >= 85 and (prev_x1 is None
                                      or w['x0'] - prev_x1 >= 10):
                    starts.append(w['x0'])
                prev_x1 = w['x1']
        if not starts:
            problems.append(f'{title!r}: no header cell anchors')
            continue
        boundary = min(starts) - 5

        def line_vals(words):
            """Numeric value words: right of the name column, and past
            the precinct number that follows the name's own 'Precinct'
            word (its digit sits left of the anchors on normal pages,
            but the x0 test alone is not enough on wide pages)."""
            idx = max([k for k, w in enumerate(words)
                       if undouble(w['text']) == 'Precinct'] or [-1])
            return [(w['x0'], int(w['text'].replace(',', '')))
                    for k, w in enumerate(words)
                    if k > idx + 1
                    and w['text'].replace(',', '').isdigit()
                    and w['x0'] >= boundary]

        rows_raw = []
        xs = list(starts)
        for row in b['rows']:
            vals = line_vals(row['words'])
            rows_raw.append((row, vals))
            xs += [x for x, _ in vals]
        if b['total']:
            xs += [x for x, _ in b['total']]
        centers = cluster(xs)

        # id -> precinct name from complete (unwrapped) id lines
        full_block = len(b['rows']) == 33

        def row_name(row):
            return ' '.join(w['text'] for w in row['words'][1:]
                            if w['x0'] < boundary)

        if not full_block:
            for row, vals in rows_raw:
                if not re.search(r'Precinct \d+$', row_name(row)):
                    problems.append(f'{title!r} p{row["id"]}: wrapped row '
                                    f'in a {len(b["rows"])}-row block')

        cols = {}
        for l in picked:
            for w in l['words']:
                t = undouble(w['text'])
                if t == 'Precinct' and 40 <= w['x0'] <= 50:
                    continue
                # header cells are left-aligned too: a word belongs to
                # the greatest column anchor <= its x0 (multi-word cell
                # text continues past its anchor).
                i = None
                for j, c in enumerate(centers):
                    if c <= w['x0'] + 0.5:
                        i = j
                    else:
                        break
                if i is None:
                    warns.append(f'{title!r}: header word {w["text"]!r} '
                                 f'@{w["x0"]:.0f} matches no column')
                    continue
                cols.setdefault(i, []).append((l['top'], w['x0'], t))
        cells = []
        for i in range(len(centers)):
            text = ''
            for _top, _x, t in sorted(cols.get(i, [])):
                text = text + t if text.endswith('-') else \
                    (text + ' ' + t).strip()
            cells.append(text)
        wi_list = [i for i, c in enumerate(cells) if 'write' in c.lower()]
        wi = wi_list[0] if len(wi_list) == 1 else None
        if len(wi_list) > 1:
            problems.append(f'{title!r}: multiple write-in cells')

        rows = []
        for row, raw in rows_raw:
            vals = {}
            ok = True
            for x, v in raw:
                i = nearest(x, centers, 5)
                if i is None:
                    problems.append(f'{title!r} p{row["id"]}: value {v} '
                                    f'@{x:.0f} matches no column')
                    ok = False
                    continue
                if i in vals and vals[i] != v:
                    problems.append(f'{title!r} p{row["id"]}: col {i} '
                                    f'has {vals[i]} and {v}')
                vals[i] = v
            if not ok or len(vals) != len(centers):
                problems.append(f'{title!r} p{row["id"]}: '
                                f'{len(vals)}/{len(centers)} cells')
                continue
            name = row_name(row)
            if re.search(r'Precinct \d+$', name):
                prec = name
                if prec not in id_names.values():
                    problems.append(f'{title!r} p{row["id"]}: printed name '
                                    f'{name!r} not in canonical list')
                    continue
            else:
                if not full_block or row['id'] not in id_names:
                    problems.append(f'{title!r} p{row["id"]}: unresolvable '
                                    f'precinct {row["id"]}')
                    continue
                prec = id_names[row['id']]
                part = name.split()
                can = prec.split()
                if not any(can[i:i + len(part)] == part
                           for i in range(len(can) - len(part) + 1)):
                    problems.append(f'{title!r} p{row["id"]}: wrapped name '
                                    f'{name!r} inconsistent with '
                                    f'{prec!r}')
                    continue
            rows.append((prec, vals))

        if b['total'] is None:
            problems.append(f'{title!r}: no Total row')
        else:
            for i in range(len(centers)):
                got = sum(vals.get(i, 0) for _pid, vals in rows)
                want = None
                for x, v in b['total']:
                    if nearest(x, centers, 5) == i:
                        want = v
                        break
                if want is None:
                    problems.append(f'{title!r} col {cells[i]!r}: '
                                    f'no printed Total value')
                elif got != want:
                    problems.append(f'{title!r} col {cells[i]!r}: '
                                    f'sum {got} != Total {want}')

        office, district = OFFICE_MAP.get(title, (title, ''))
        for prec, vals in rows:
            for i in range(len(centers)):
                cand = 'Write-In' if i == wi else cells[i]
                if not cand:
                    problems.append(f'{title!r}: empty candidate cell {i}')
                    continue
                if office == 'Straight Party':
                    party = SP_CODES.get(cand, '')
                elif cand in ('Yes', 'No'):
                    party = ''
                else:
                    party = PARTY.get((office, cand))
                    if party is None:
                        party = county_party.get(
                            (office, district, cand), '')
                emit.append([COUNTY, prec, office, district,
                             party, cand, vals.get(i, 0)])

    # --- verify against the county file --------------------------------
    covered = {}
    for r in emit:
        if r[5] == 'Write-In':
            continue
        key = (r[2], r[3], r[5])
        covered[key] = covered.get(key, 0) + int(r[6])
    bad = 0
    for key in sorted(set(covered) | set(county_want)):
        got = covered.get(key, 0)
        want = county_want.get(key, 0)
        if got == want:
            continue
        if key not in county_want:
            print(f'  info  {key}: parsed {got} (no county-file row)')
            continue
        bad += 1
        print(f'MISMATCH {key}: parsed {got} vs county {want}')

    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(emit)
    print(f'emitted {len(emit)} rows over {len(blocks)} blocks')

    for w_ in warns:
        print('WARN:', w_)
    if bad or problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)


if __name__ == '__main__':
    main()