"""Parse the Manistee County Aug 2026 primary SOVC via cached PaddleOCR markdown.

The PDF's embedded text layer is unreliable vendor OCR (wrong digits in labels,
"Springdate"), and the vote digits are embedded as images, so the page image is
the source of truth. The whole PDF was OCR'd with PaddleOCR-VL
(src/fetch_paddleocr_md.py, per-page markdown cached in /tmp/paddleocr_md) and
that markdown is parsed here.

Page grammar (100 pages, Wexford-family SOVC):
- p1-2: countywide turnout summary — read for verification only.
- Contests: a page whose first text line is "<office> (Vote for N) [DEM|REP]"
  opens a contest. Its tables: an aux table (Times Cast / Registered Voters —
  contest-jurisdiction turnout, verification only) and one results table
  (candidate names / Yes / No / Total Votes / Unresolved Write-In headers over
  precinct rows). Two-page contests put remaining candidates and/or Total Votes
  + Unresolved Write-In on page B; "Total Votes" excludes unresolved write-ins.
- Some results tables and several write-in-only pages render as plain text
  lines instead of HTML tables; on a few of those PaddleOCR dropped every digit
  (they are images), so MANUAL_PAGES carries hand-transcribed values read from
  the rendered page images (/tmp/manistee/p*.png).

Convention (from the committed Wexford 2026 file): emit candidate rows
(alphabetical, zero rows skipped), a Write-In row only when > 0, and Ballots
Cast = Total Votes + Unresolved Write-In unconditionally. Proposals keep their
printed name without "(Vote for N)".

Usage:
    .venv/bin/python src/manistee_ocr_parser.py \
        [--cache /tmp/paddleocr_md/Manistee_County_Aug_2026_Primary_Statement_of_Votes_Cast] \
        --out 2026/counties/20260804__mi__primary__manistee__precinct.csv
"""
import argparse
import csv
import re
import sys

COUNTY = 'Manistee'
HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

# Canonical precincts in page-1 order (the output order within each contest).
PRECINCTS = [
    'City of Manistee, Precinct 1',
    'City of Manistee, Precinct 2',
    'Arcadia Township, Precinct 1',
    'Bear Lake Township, Precinct 1',
    'Brown Township, Precinct 1',
    'Cleon Township, Precinct 1',
    'Dickson Township, Precinct 1',
    'Charter Township of Filer, Precinct 1',
    'Manistee Township, Precinct 1',
    'Maple Grove Township, Precinct 1',
    'Marilla Township, Precinct 1',
    'Norman Township, Precinct 1',
    'Onekama Township, Precinct 1',
    'Pleasanton Township, Precinct 1',
    'Springdale Township, Precinct 1',
    'Stronach Township, Precinct 1',
]

# Hand-transcribed from the rendered page images for pages where PaddleOCR
# dropped every digit (they are images in the scan). 'wi'/'tv' keyed by
# precinct, totals for cross-checking.
MANUAL_PAGES = {
    27: {'wi': {'Charter Township of Filer, Precinct 1': 9}, 'total_wi': 9},
    30: {'tv': {'Maple Grove Township, Precinct 1': 0},
         'wi': {'Maple Grove Township, Precinct 1': 41},
         'total_tv': 0, 'total_wi': 41},
    47: {'wi': {'City of Manistee, Precinct 1': 1,
                'City of Manistee, Precinct 2': 1,
                'Springdale Township, Precinct 1': 2}, 'total_wi': 4},
    70: {'tv': {'Onekama Township, Precinct 1': 0},
         'wi': {'Onekama Township, Precinct 1': 18}, 'total_wi': 18},
    72: {'tv': {'Springdale Township, Precinct 1': 0},
         'wi': {'Springdale Township, Precinct 1': 26},
         'total_tv': 0, 'total_wi': 26},
    86: {'wi': {'Brown Township, Precinct 1': 0}, 'total_wi': 0},
    88: {'wi': {'Norman Township, Precinct 1': 0}, 'total_wi': 0},
    90: {'wi': {'Norman Township, Precinct 1': 0}, 'total_wi': 0},
    94: {'wi': {'Cleon Township, Precinct 1': 0, 'Marilla Township, Precinct 1': 0,
                'Pleasanton Township, Precinct 1': 0,
                'Springdale Township, Precinct 1': 0}, 'total_wi': 0},
    96: {'wi': {'Cleon Township, Precinct 1': 0, 'Marilla Township, Precinct 1': 0,
                'Pleasanton Township, Precinct 1': 0,
                'Springdale Township, Precinct 1': 0}, 'total_wi': 0},
    98: {'wi': {'Cleon Township, Precinct 1': 0, 'Marilla Township, Precinct 1': 0},
         'total_wi': 0},
    100: {'wi': {'Arcadia Township, Precinct 1': 0, 'Bear Lake Township, Precinct 1': 0,
                 'Brown Township, Precinct 1': 0, 'Manistee Township, Precinct 1': 0,
                 'Onekama Township, Precinct 1': 0}, 'total_wi': 0},
}

# ---------------------------------------------------------------- markdown -> blocks

TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TR = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
TABLE = re.compile(r'<table.*?</table>', re.S)
TAG = re.compile(r'<[^>]+>')

# OCR name corrections, verified against page images / CENR county file.
NAME_FIXES = {
    'Mark W. Yorkman': 'Mark W. Yonkman',  # p10 rotated header reads Yonkman
}


def clean(cell):
    text = re.sub(r'\s+', ' ', TAG.sub('', cell).replace('\\n', ' ')).strip()
    return NAME_FIXES.get(text, text)


def parse_page(md):
    """Return (outside_text, tables, text_lines): tables as cell grids, text
    lines as the non-table trailing text one string per line."""
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
STRUCTURAL = {
    'precinct', 'county', 'manistee county michigan', 'cumulative',
    'cumulative - total', 'county - total', 'manistee county michigan - total',
    'times cast', 'registered voters', 'registered', 'voters',
}
NOISE = {
    'precinct', 'county', 'manistee county michigan', 'cumulative',
    'cumulative - total', 'county - total', 'total votes', 'unresolved',
    'write-in', 'unresolved write-in',
}


def is_num(s):
    return bool(NUM.match(s))


def num(s):
    return int(s.replace(',', ''))


def canon_precinct(label):
    """Fuzzy-match a row label to a canonical precinct (OCR truncates and
    splits labels); None for totals/markers/noise."""
    key = re.sub(r'\s+', ' ', label).strip()
    low = key.lower()
    if low in ('cumulative', 'county', 'manistee county michigan',
               'manistee county michigan - total', 'county - total',
               'cumulative - total', 'precinct', 'unresolved write-in',
               'total votes'):
        return None
    if key in PRECINCTS:
        return key
    for p in PRECINCTS:
        if p.startswith(key) and len(key) >= 12:
            return p
    return None


def is_total_label(label):
    low = re.sub(r'\s+', ' ', label).strip().lower()
    return ('total' in low or low.startswith('cumulative')
            or low in ('county', 'manistee county michigan'))


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
    m = re.match(r'^Township (Clerk|Trustee) for (.+)$', title)
    if m:
        # group(2) already ends in "Township" — don't double it
        return f'{m.group(2)} {m.group(1)}', ''
    m = re.match(r'^Delegate to County Convention for (.+)$', title)
    if m:
        return f'{m.group(1)} Delegate to County Convention', ''
    return title, ''


# ---------------------------------------------------------------- results tables

def parse_results_table(rows, problems, where):
    """Parse one results table. Returns (slots, data, totals) where slots is
    ['cand', ..., 'TV'?, 'WI'?], data maps precinct -> {slot: votes} for
    precinct rows, totals maps slot -> county total from total rows.

    OCR drops empty (zero) cells inconsistently, so numeric cells are assigned
    to slots in printed column order: candidates first, then Total Votes, then
    Unresolved Write-In."""
    # Header labels: non-structural labels from rows above the first numeric row.
    slots = []
    first_data = len(rows)
    for i, row in enumerate(rows):
        if any(is_num(c) for c in row):
            first_data = i
            break
        for cell in row:
            low = cell.lower()
            if low in ('total votes', 'unresolved', 'write-in',
                       'unresolved write-in'):
                slot = 'TV' if low == 'total votes' else 'WI'
            elif not cell or low in STRUCTURAL:
                continue
            else:
                slot = cell
            if slot not in slots:
                slots.append(slot)
    slots = [('TV' if s == 'TV' else ('WI' if s == 'WI' else s)) for s in slots]
    # Printed column order is candidates left-to-right, then Total Votes, then
    # Unresolved Write-In — even when a multi-row header emits 'Total Votes'
    # before the candidate names.
    cands = [s for s in slots if s not in ('TV', 'WI')]
    slots = cands + [s for s in ('TV', 'WI') if s in slots]

    def assign(numerics):
        vals = {}
        if len(numerics) > len(slots):
            problems.append(f'{where}: {len(numerics)} numeric cells > {len(slots)} slots')
        for i, slot in enumerate(slots):
            vals[slot] = num(numerics[i]) if i < len(numerics) else 0
        return vals

    data, totals = {}, {}
    for row in rows[first_data:]:
        label = row[0] if row else ''
        numerics = [c for c in row[1:] if is_num(c)]
        precinct = canon_precinct(label)
        if precinct:
            data.setdefault(precinct, {}).update(assign(numerics))
        elif is_total_label(label) and numerics:
            totals.update(assign(numerics))
    return slots, data, totals


# ---------------------------------------------------------------- text rows

def parse_text_rows(lines):
    """Parse a page whose results table rendered as plain text (PaddleOCR
    sometimes drops the table markup). Returns (candidates, rows, totals):
    candidate names in printed order, rows as {precinct: [nums]}, totals as
    the 'Manistee County Michigan - Total' numbers (TV and/or WI)."""
    if lines and TITLE.match(lines[0]):
        lines = lines[1:]
    candidates, rows, totals = [], {}, []
    data_started = False
    pending = ''  # split-label continuation ("...Filer," + "Precinct 1")

    def precinct_of(text):
        for p in PRECINCTS:
            if text == p:
                return p, ''
            if text.startswith(p + ' '):
                return p, text[len(p) + 1:]
        return None, ''

    for raw in lines:
        line = re.sub(r'\s+', ' ', raw).strip()
        low = line.lower()
        if not line:
            continue
        m = re.match(r'^manistee county michigan\s*-\s*total(.*)$', low)
        if m:
            nums = [num(n) for n in re.findall(r'[\d,]+', m.group(1))]
            if nums:
                totals = nums
            data_started = True
            pending = ''
            continue
        p, rest = precinct_of(line)
        if p:
            data_started = True
            nums = [num(n) for n in re.findall(r'[\d,]+', rest)]
            if nums:
                rows[p] = nums
            pending = ''
            continue
        if pending:
            trial = pending + ' ' + line
            tp, rest = precinct_of(trial)
            if tp:
                data_started = True
                nums = [num(n) for n in re.findall(r'[\d,]+', rest)]
                if nums:
                    rows[tp] = nums
                pending = ''
                continue
        if low in NOISE:
            data_started = data_started or low in ('total votes', 'unresolved',
                                                   'write-in', 'unresolved write-in')
            pending = ''
            continue
        if not data_started and not re.search(r'\d', line):
            # Candidate names appear as bare lines; a split precinct label is a
            # strict prefix (>=12 chars) of a canonical label.
            if any(q.startswith(line) and len(line) >= 12 for q in PRECINCTS):
                pending = line
                continue
            candidates.append(line)
            continue
        pending = ''
    return candidates, rows, totals


# ---------------------------------------------------------------- main parse

def build_contests(pages, problems, notes):
    contests = []
    turnout = {}
    current = None
    for no in sorted(pages):
        text, tables, lines = parse_page(pages[no])
        if no == 1:
            # Turnout reference: Registered Voters / Voters Cast per precinct.
            for row in tables[0]:
                p = canon_precinct(row[0])
                if p and len(row) >= 3 and is_num(row[2]):
                    turnout[p] = num(row[2])
            continue
        if no == 2:
            continue
        title_m = TITLE.match(lines[0]) if lines else None
        if title_m:
            # group(1) ends at "(Vote for N)", so the party parenthetical is
            # its tail: "Governor (DEM)" / "... (Lake County) (DEM)". Strip
            # only a trailing party token, keeping e.g. "(Lake County)".
            title = re.sub(r'\s*\((?:DEM|REP)\)$', '', title_m.group(1))
            office, district = map_office(title)
            current = {
                'title': title_m.group(1), 'office': office,
                'district': district, 'party': title_m.group(3) or '',
                'vote_for': title_m.group(2), 'pages': [no],
                'cands': {}, 'tv': {}, 'wi': {}, 'totals': {},
                'times_cast': {},
            }
            contests.append(current)
        elif current is None:
            continue
        else:
            current['pages'].append(no)

        for t_i, tbl in enumerate(tables):
            joined = ' '.join(tbl[0]) if tbl else ''
            if 'Times Cast' in joined or any('Times Cast' in c for r in tbl for c in r):
                # Aux table: verification only.
                for row in tbl:
                    p = canon_precinct(row[0])
                    nums = [c for c in row[1:] if is_num(c)]
                    if p and nums:
                        current['times_cast'][p] = num(nums[0])
                continue
            where = f'p{no} table {t_i} ({current["title"]})'
            slots, data, totals = parse_results_table(tbl, problems, where)
            for precinct, vals in data.items():
                for slot, v in vals.items():
                    if slot == 'TV':
                        if precinct in current['tv'] and current['tv'][precinct] != v:
                            problems.append(f'{where}: {precinct} TV {v} != {current["tv"][precinct]}')
                        current['tv'][precinct] = v
                    elif slot == 'WI':
                        current['wi'][precinct] = current['wi'].get(precinct, 0) + v
                    else:
                        current['cands'].setdefault(slot, {})[precinct] = \
                            current['cands'].get(slot, {}).get(precinct, 0) + v
            for slot, v in totals.items():
                current['totals'].setdefault(slot, v)

        # Text-rendered results (candidate tables or write-in-only pages).
        if no in MANUAL_PAGES:
            manual = MANUAL_PAGES[no]
            for precinct, v in manual.get('tv', {}).items():
                current['tv'][precinct] = v
            for precinct, v in manual.get('wi', {}).items():
                current['wi'][precinct] = current['wi'].get(precinct, 0) + v
            if 'total_tv' in manual and 'TV' in current['totals'] and \
                    current['totals']['TV'] != manual['total_tv']:
                problems.append(f'p{no}: manual TV total {manual["total_tv"]} != table {current["totals"]["TV"]}')
            if 'total_wi' in manual:
                current['totals']['WI'] = manual['total_wi']
            notes.append(f'p{no}: used manual transcription {manual}')
        else:
            cands, rows, totals = parse_text_rows(lines)
            if rows:
                if cands:
                    for precinct, nums in rows.items():
                        if len(nums) != len(cands):
                            problems.append(f'p{no}: {precinct} {len(nums)} values for {len(cands)} candidates')
                            continue
                        for name, v in zip(cands, nums):
                            current['cands'].setdefault(name, {})[precinct] = v
                    if len(totals) == len(cands):
                        for name, v in zip(cands, totals):
                            current['totals'][name] = v
                else:
                    # Write-in-only text page (optionally with Total Votes).
                    for precinct, nums in rows.items():
                        if len(nums) == 1:
                            current['wi'][precinct] = current['wi'].get(precinct, 0) + nums[0]
                        else:
                            problems.append(f'p{no}: {precinct} unexpected {len(nums)} values')
                    if len(totals) == 1:
                        current['totals']['WI'] = totals[0]
                    elif len(totals) == 2:
                        current['totals']['TV'] = totals[0]
                        current['totals']['WI'] = totals[1]
    return contests, turnout


def validate(contests, turnout, problems, notes):
    for c in contests:
        t = c['title']
        precincts = set(c['tv']) | set(c['wi'])
        for cand in c['cands']:
            precincts |= set(c['cands'][cand])
        if not precincts:
            notes.append(f'{t}: no precinct rows (cross-county contest) — skipped')
            continue
        # Per-precinct: candidate sum == Total Votes.
        for p in sorted(precincts):
            csum = sum(c['cands'][cand].get(p, 0) for cand in c['cands'])
            tv = c['tv'].get(p)
            if tv is not None and csum != tv:
                problems.append(f'{t} / {p}: candidate sum {csum} != TV {tv}')
            tc = c['times_cast'].get(p)
            # Multi-seat contests (Vote for N) can total up to N per voter.
            limit = tc * int(c['vote_for']) if tc is not None else None
            if tv is not None and limit is not None and tv + c['wi'].get(p, 0) > limit:
                problems.append(f'{t} / {p}: TV+WI {tv + c["wi"].get(p, 0)} > Times Cast {tc} x {c["vote_for"]}')
        # County totals.
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
                # Single-precinct contests: OCR drops the precinct WI cell but
                # the total row is printed; adopt it.
                if len(precincts) == 1:
                    p = next(iter(precincts))
                    notes.append(f'{t}: WI for {p} taken from printed total {c["totals"]["WI"]}')
                    c['wi'][p] = c['totals']['WI']
                else:
                    problems.append(f'{t}: WI county sum {tot} != printed {c["totals"]["WI"]}')
        # Single-precinct TV repair, same OCR-drop case.
        if len(precincts) == 1 and 'TV' in c['totals']:
            p = next(iter(precincts))
            if c['tv'].get(p, 0) != c['totals']['TV'] and not c['cands']:
                notes.append(f'{t}: TV for {p} taken from printed total {c["totals"]["TV"]}')
                c['tv'][p] = c['totals']['TV']
        # Countywide contests: Times Cast should equal the page-1 turnout.
        if c['times_cast'] and sum(c['times_cast'].values()) == sum(turnout.values()):
            for p, v in c['times_cast'].items():
                if turnout.get(p) != v:
                    problems.append(f'{t} / {p}: Times Cast {v} != turnout {turnout.get(p)}')


def emit(contests, county, out_path, problems, notes):
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
                    rows.append([county, p, c['office'], c['district'], c['party'], cand, v])
            wi = c['wi'].get(p, 0)
            if wi:
                rows.append([county, p, c['office'], c['district'], c['party'], 'Write-In', wi])
            tv = c['tv'].get(p, 0)
            rows.append([county, p, c['office'], c['district'], c['party'],
                         'Ballots Cast', tv + wi])
    with open(out_path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default='/tmp/paddleocr_md/Manistee_County_Aug_2026_Primary_Statement_of_Votes_Cast')
    ap.add_argument('--out', default='2026/counties/20260804__mi__primary__manistee__precinct.csv')
    args = ap.parse_args()

    import glob
    import os
    pages = {}
    for md_path in sorted(glob.glob(os.path.join(args.cache, 'p*.md'))):
        pages[int(re.search(r'p(\d+)\.md$', md_path).group(1))] = open(md_path).read()
    if len(pages) != 100:
        print(f'expected 100 pages, got {len(pages)}', file=sys.stderr)
        sys.exit(1)

    problems, notes = [], []
    contests, turnout = build_contests(pages, problems, notes)
    validate(contests, turnout, problems, notes)
    rows = emit(contests, COUNTY, args.out, problems, notes)

    print(f'Wrote {len(rows)} rows to {args.out} ({len(contests)} contests)')
    for n in notes:
        print('NOTE:', n)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()