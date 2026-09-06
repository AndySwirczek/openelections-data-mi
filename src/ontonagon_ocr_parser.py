"""Parse the Ontonagon County Aug 2026 primary SOVC via cached PaddleOCR markdown.

The PDF is an image-only scan (Xerox AltaLink, no text layer), so the page
image is the source of truth. The whole PDF was OCR'd with PaddleOCR-VL
(src/fetch_paddleocr_md.py, per-page markdown cached in /tmp/paddleocr_md) and
that markdown is parsed here.

Page grammar (119 pages, counting-group SOVC):
- p1-3: countywide turnout summary (Registered Voters / Voters Cast / % Turnout
  per precinct, split into Election Day / AV Counting Boards / Early Voting /
  Total rows) — read for verification only.
- Contests: a page whose first text line is "<title> (Vote for N) [DEM|REP]"
  opens a contest. Tables come in page-sized chunks over three page pairs:
  chunk A (county pseudo-rows + first precincts), chunk B (next precincts),
  chunk C (last precinct + Ontonagon County Michigan - Total / Cumulative /
  County - Total rows). Each chunk spans two pages: aux table (Times Cast /
  Registered Voters) + first results columns on page 1, remaining candidate
  columns and/or Total Votes + Unresolved Write-In on page 2. Single-precinct
  contests fit aux + results on one page.
- Every table's rows are grouped: a precinct header row (label, empty cells)
  followed by Election Day / AV Counting Boards / Early Voting / Total rows.
  Only the Total row carries the per-precinct figures used here; the printed
  County - Total row provides county totals for cross-checking.

Convention (from the committed Wexford 2026 file): emit candidate rows
(alphabetical, zero rows skipped), a Write-In row only when > 0, and Ballots
Cast = Total Votes + Unresolved Write-In unconditionally. Proposals keep their
printed name without "(Vote for N)".

Usage:
    .venv/bin/python src/ontonagon_ocr_parser.py \
        [--cache /tmp/paddleocr_md/Ontonagon_County_Aug_2026_Primary_Official_Results] \
        --out 2026/counties/20260804__mi__primary__ontonagon__precinct.csv
"""
import argparse
import csv
import re
import sys

COUNTY = 'Ontonagon'
HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

# Canonical precincts in page-1 order (the output order within each contest).
# The 2024 file also had a Duncan Township (Houghton County) precinct, but the
# 2026 source omits it: the eleven precincts below sum exactly to the printed
# county total of 1,862 ballots.
PRECINCTS = [
    'Bergland Township, Precinct 1',
    'Bohemia Township, Precinct 1',
    'Carp Lake Township, Precinct 1',
    'Greenland Township, Precinct 1',
    'Haight Township, Precinct 1',
    'Interior Township, Precinct 1',
    'Matchwood Township, Precinct 1',
    'McMillan Township, Precinct 1',
    'Ontonagon Township, Precinct 1',
    'Rockland Township, Precinct 1',
    'Stannard Township, Precinct 1',
]

# ---------------------------------------------------------------- markdown -> blocks

TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TR = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
TABLE = re.compile(r'<table.*?</table>', re.S)
TAG = re.compile(r'<[^>]+>')

# OCR name corrections, verified against page images / CENR county file.
NAME_FIXES = {
    # p70/p071 headers drop a K; p072 and CENR spell it Markkanen. Unifying
    # keeps one candidate slot across the contest's pages.
    'Gregory Markanen': 'Gregory Markkanen',
}


def clean(cell):
    text = re.sub(r'\s+', ' ', TAG.sub('', cell).replace('\\n', ' ')).strip()
    return NAME_FIXES.get(text, text)


def parse_page(md):
    """Return (outside_text, tables, text_lines): tables as cell grids, text
    lines as the non-table text one string per line."""
    tables = []
    for tbl in TABLE.findall(md):
        rows = []
        for row in TR.findall(tbl):
            rows.append([clean(c) for c in TD.findall(row)])
        tables.append(rows)
    outside = TABLE.sub('', md)
    outside = re.sub(r'<div[^>]*>|</div>', '', outside)
    outside = TAG.sub('', outside)
    lines = [ln.strip() for ln in outside.splitlines() if ln.strip()]
    return ' '.join(lines), tables, lines


# ---------------------------------------------------------------- value helpers

NUM = re.compile(r'^\d[\d,]*$')


def is_num(s):
    return bool(NUM.match(s))


def num(s):
    return int(s.replace(',', ''))


def squash(s):
    """Comparison key that survives OCR space-dropping ('Ontonagon CountyMichigan')."""
    return re.sub(r'[\s-]+', '', s).lower()


# Row labels (compared via squash()).
COUNTY_LABELS = {
    'county': 'pseudo',
    'ontonagoncountymichigan': 'pseudo',
    'ontonagoncountymichigantotal': 'county_total',
    'countytotal': 'county_total',
    'cumulative': 'cumulative',
    'cumulativetotal': 'cumulative',
    'electionday': 'skip',
    'avcountingboards': 'skip',
    'earlyvoting': 'skip',
}
GROUP_LABELS = {'total'}
TURNOUT_HEADER = {'registeredvoters', 'voterscast', '%turnout'}


def canon_precinct(label):
    """Match a row label to a canonical precinct (OCR truncates labels);
    None for anything else."""
    key = re.sub(r'\s+', ' ', label).strip()
    if key in PRECINCTS:
        return key
    for p in PRECINCTS:
        if p.startswith(key) and len(key) >= 12:
            return p
    return None


# ---------------------------------------------------------------- contest titles

TITLE = re.compile(r'^#{0,2}\s*(.+?)\s*\(Vote for\s+(\d+)\)(?:\s+(DEM|REP))?$')


def map_office(title):
    """Map a party-stripped contest title to (office, district)."""
    m = re.match(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^State Senator (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'State Senate', m.group(1)
    m = re.match(r'^Representative in State Legislature (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'State House', m.group(1)
    exact = {'Governor': 'Governor', 'United States Senator': 'U.S. Senate'}
    if title in exact:
        return exact[title], ''
    m = re.match(r'^County Prosecuting Attorney(.*)$', title)
    if m:
        return f'Ontonagon County Prosecuting Attorney{m.group(1)}', ''
    return title, ''


# ---------------------------------------------------------------- table parsing

def slot_of(header_cell):
    """Map a header cell to a slot name: 'TV', 'WI', or the candidate name."""
    sq = squash(header_cell)
    if sq == 'totalvotes':
        return 'TV'
    if sq.startswith('unresolvedwrite'):
        return 'WI'
    return header_cell


def parse_table(rows, where, problems):
    """Parse one results/write-in table.

    Returns (slots, data, totals): slots in printed column order (candidates
    first, then TV, then WI), data maps precinct -> {slot: votes} from Total
    rows, totals maps slot -> county total from County - Total rows.

    Numeric cells are assigned to slots in printed column order after dropping
    empty/phantom cells; OCR drops zero cells inconsistently, so a dropped
    value surfaces as a validation mismatch rather than a shift."""
    slots = []
    first_data = len(rows)
    initial = None  # precinct header row(s) above the first numeric row
    for i, row in enumerate(rows):
        if any(is_num(c) for c in row):
            first_data = i
            break
        p = canon_precinct(row[0]) if row else None
        if p:
            initial = p
        for cell in row[1:]:
            if not cell or squash(cell) in ('precinct', 'registeredvoters',
                                            'registered', 'voters'):
                continue
            slot = slot_of(cell)
            if slot not in slots:
                slots.append(slot)
    # Printed order is candidates, then Total Votes, then Unresolved Write-In.
    cands = [s for s in slots if s not in ('TV', 'WI')]
    slots = cands + [s for s in ('TV', 'WI') if s in slots]

    def assign(cells):
        vals = {}
        numerics = [c for c in cells if is_num(c)]
        if len(numerics) > len(slots):
            problems.append(f'{where}: {len(numerics)} numeric cells > {len(slots)} slots')
        for i, slot in enumerate(slots):
            vals[slot] = num(numerics[i]) if i < len(numerics) else 0
        return vals

    data, totals = {}, {}
    precinct = initial  # None once the county/cumulative section starts
    for row in rows[first_data:]:
        label = row[0] if row else ''
        sq = squash(label)
        if sq in COUNTY_LABELS:
            kind = COUNTY_LABELS[sq]
            if kind == 'skip':
                continue  # counting-group row; precinct stays current
            precinct = None
            if kind == 'county_total' and any(c for c in row[1:]):
                totals.update(assign(row[1:]))
            continue
        if sq in GROUP_LABELS:  # 'Total'
            if precinct:
                data.setdefault(precinct, {}).update(assign(row[1:]))
            continue
        p = canon_precinct(label)
        if p:
            precinct = p
        # anything else (garbage rows) is ignored
    return slots, data, totals


def parse_turnout_table(rows):
    """Parse a page 1-3 turnout table. Returns {precinct: voters_cast}."""
    turnout = {}
    precinct = None
    for row in rows:
        label = row[0] if row else ''
        sq = squash(label)
        numerics = [c for c in row[1:] if is_num(c)]
        if sq in COUNTY_LABELS:
            kind = COUNTY_LABELS[sq]
            if kind == 'skip':
                continue  # counting-group row; precinct stays current
            # county pseudo-rows and the Cumulative zero block
            precinct = 'COUNTY' if kind in ('pseudo', 'county_total') else None
            continue
        p = canon_precinct(label)
        if p:
            precinct = p
            continue
        if sq == 'total' and precinct and len(numerics) >= 2:
            # columns are Registered Voters, Voters Cast, % Turnout
            if precinct == 'COUNTY':
                turnout['COUNTY'] = num(numerics[1])
            else:
                turnout[precinct] = num(numerics[1])
    return turnout


# ---------------------------------------------------------------- main parse

def build_contests(pages, problems, notes):
    contests = []
    turnout = {}
    current = None
    for no in sorted(pages):
        text, tables, lines = parse_page(pages[no])
        if no <= 3:
            # Countywide turnout summary (verification only).
            for tbl in tables:
                if any(TURNOUT_HEADER & {squash(c) for c in row} for row in tbl[:2]):
                    turnout.update(parse_turnout_table(tbl))
            continue
        title_m = TITLE.match(lines[0]) if lines else None
        if title_m:
            # group(1) ends at "(Vote for N)", so the party parenthetical is
            # its tail: "Governor (DEM)". Strip only a trailing party token.
            title = re.sub(r'\s*\((?:DEM|REP)\)$', '', title_m.group(1))
            office, district = map_office(title)
            current = {
                'title': title_m.group(1), 'office': office,
                'district': district, 'party': title_m.group(3) or '',
                'vote_for': title_m.group(2), 'pages': [no],
                'cands': {}, 'tv': {}, 'wi': {}, 'totals': {},
                'times_cast': {}, 'tc_total': None,
            }
            contests.append(current)
        elif current is None:
            continue
        else:
            current['pages'].append(no)

        for t_i, tbl in enumerate(tables):
            if not tbl:
                continue
            if any('timescast' in squash(c) for row in tbl for c in row):
                # Aux table: per-precinct Times Cast, verification only.
                precinct = None
                for row in tbl:
                    label = row[0] if row else ''
                    sq = squash(label)
                    numerics = [c for c in row[1:] if is_num(c)]
                    if sq in COUNTY_LABELS:
                        if COUNTY_LABELS[sq] == 'skip':
                            continue  # counting-group row; precinct stays current
                        precinct = None
                        if COUNTY_LABELS[sq] == 'county_total' and numerics:
                            current['tc_total'] = num(numerics[0])
                        continue
                    if sq == 'total' and precinct and numerics:
                        current['times_cast'][precinct] = num(numerics[0])
                        continue
                    p = canon_precinct(label)
                    if p:
                        precinct = p
                continue
            where = f'p{no} table {t_i} ({current["title"]})'
            slots, data, totals = parse_table(tbl, where, problems)
            for precinct, vals in data.items():
                for slot, v in vals.items():
                    if slot == 'TV':
                        if precinct in current['tv'] and current['tv'][precinct] != v:
                            problems.append(
                                f'{where}: {precinct} TV {v} != {current["tv"][precinct]}')
                        current['tv'][precinct] = v
                    elif slot == 'WI':
                        current['wi'][precinct] = current['wi'].get(precinct, 0) + v
                    else:
                        current['cands'].setdefault(slot, {})[precinct] = \
                            current['cands'].get(slot, {}).get(precinct, 0) + v
            for slot, v in totals.items():
                current['totals'].setdefault(slot, v)
    return contests, turnout


def validate(contests, turnout, problems, notes):
    for c in contests:
        t = c['title']
        precincts = set(c['tv']) | set(c['wi'])
        for cand in c['cands']:
            precincts |= set(c['cands'][cand])
        if not precincts:
            notes.append(f'{t}: no precinct rows — skipped')
            continue
        # Per-precinct: candidate sum == Total Votes; TV+WI within Times Cast.
        for p in sorted(precincts):
            csum = sum(c['cands'][cand].get(p, 0) for cand in c['cands'])
            tv = c['tv'].get(p)
            if tv is None:
                problems.append(f'{t} / {p}: no Total Votes cell')
            elif csum != tv:
                problems.append(f'{t} / {p}: candidate sum {csum} != TV {tv}')
            tc = c['times_cast'].get(p)
            # Multi-seat contests (Vote for N) can total up to N per voter.
            limit = tc * int(c['vote_for']) if tc is not None else None
            if tv is not None and limit is not None and tv + c['wi'].get(p, 0) > limit:
                problems.append(
                    f'{t} / {p}: TV+WI {tv + c["wi"].get(p, 0)} > Times Cast {tc} x {c["vote_for"]}')
        # County totals from the printed County - Total row.
        for cand in c['cands']:
            tot = sum(c['cands'][cand].values())
            if cand in c['totals'] and c['totals'][cand] != tot:
                problems.append(f'{t} / {cand}: county sum {tot} != printed {c["totals"][cand]}')
        if 'TV' in c['totals']:
            tot = sum(c['tv'].values())
            if c['totals']['TV'] != tot:
                problems.append(f'{t}: TV county sum {tot} != printed {c["totals"]["TV"]}')
        if 'WI' in c['totals']:
            tot = sum(c['wi'].values())
            if c['totals']['WI'] != tot:
                problems.append(f'{t}: WI county sum {tot} != printed {c["totals"]["WI"]}')
        # Aux Times Cast is total ballots in the precinct, so it should equal
        # the turnout summary for every contest's jurisdiction.
        for p, v in c['times_cast'].items():
            if turnout.get(p) != v:
                problems.append(f'{t} / {p}: Times Cast {v} != turnout {turnout.get(p)}')


def emit(contests, out_path):
    rows = []
    for c in contests:
        precincts = set(c['tv']) | set(c['wi'])
        for cand in c['cands']:
            precincts |= set(c['cands'][cand])
        if not precincts:
            continue
        for p in PRECINCTS:
            if p not in precincts:
                continue
            for cand in sorted(c['cands']):
                v = c['cands'][cand].get(p, 0)
                if v:
                    rows.append([COUNTY, p, c['office'], c['district'], c['party'], cand, v])
            wi = c['wi'].get(p, 0)
            if wi:
                rows.append([COUNTY, p, c['office'], c['district'], c['party'], 'Write-In', wi])
            tv = c['tv'].get(p, 0)
            rows.append([COUNTY, p, c['office'], c['district'], c['party'],
                         'Ballots Cast', tv + wi])
    with open(out_path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default='/tmp/paddleocr_md/Ontonagon_County_Aug_2026_Primary_Official_Results')
    ap.add_argument('--out', default='2026/counties/20260804__mi__primary__ontonagon__precinct.csv')
    args = ap.parse_args()

    import glob
    import os
    pages = {}
    for md_path in sorted(glob.glob(os.path.join(args.cache, 'p*.md'))):
        pages[int(re.search(r'p(\d+)\.md$', md_path).group(1))] = open(md_path).read()
    if len(pages) != 119:
        print(f'expected 119 pages, got {len(pages)}', file=sys.stderr)
        sys.exit(1)

    problems, notes = [], []
    contests, turnout = build_contests(pages, problems, notes)
    validate(contests, turnout, problems, notes)
    rows = emit(contests, args.out)

    print(f'Wrote {len(rows)} rows to {args.out} ({len(contests)} contests)')
    for n in notes:
        print('NOTE:', n)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()