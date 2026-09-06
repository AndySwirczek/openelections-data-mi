"""Parse the Luce County Aug 2026 primary SOVC via cached PaddleOCR markdown.

The PDF is an image-only scan (Konica Minolta bizhub, no text layer), so the
page image is the source of truth. The whole PDF was OCR'd with PaddleOCR-VL
(src/fetch_paddleocr_md.py, per-page markdown cached in /tmp/paddleocr_md) and
that markdown is parsed here.

Page grammar (33 pages, one-row-per-precinct SOVC — different from
Montmorency's counting-group layout):
- p1: countywide turnout summary (Registered Voters / Voters Cast / % Turnout
  per precinct) — read for verification only.
- Each contest occupies one page (p2-33): a "<title> (Vote for N) [DEM|REP]"
  heading and one table per contest. Each table merges the aux columns
  (Times Cast / Registered Voters) and the results side by side, separated by
  a second 'Precinct' label column:
      [Precinct, Times Cast, Registered Voters, Precinct,
       <candidate columns...>, Total Votes, Unresolved Write-In]
  One row per precinct; the precinct label appears in both halves. Trailing
  rows: 'Luce County Michigan - Total', a zero Cumulative block, and
  'County - Total' — both totals rows carry the printed county figures used
  for cross-checking.
- Candidate header names are rotated; OCR sometimes truncates them (see
  NAME_FIXES). Proposal contests use Yes / No columns.

Convention (from the committed Wexford/Ontonagon 2026 files): emit candidate
rows (alphabetical, zero rows skipped), a Write-In row only when > 0, and
Ballots Cast = Total Votes + Unresolved Write-In unconditionally. Proposals
keep their printed name without "(Vote for N)".

Usage:
    .venv/bin/python src/luce_ocr_parser.py \
        [--cache /tmp/paddleocr_md/Luce_County_Aug_2026_Primary_Election_Results] \
        --out 2026/counties/20260804__mi__primary__luce__precinct.csv
"""
import argparse
import csv
import html
import re
import sys

COUNTY = 'Luce'
HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

# Canonical precincts in page-1 order (the output order within each contest).
PRECINCTS = [
    'Columbus Township, Precinct 1',
    'Lakefield Township, Precinct 1',
    'McMillan Township, Precinct 1',
    'Pentland Township, Precinct 1',
]

# ---------------------------------------------------------------- markdown -> blocks

TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TR = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
TABLE = re.compile(r'<table.*?</table>', re.S)
TAG = re.compile(r'<[^>]+>')

# OCR name corrections, verified against page images / CENR county file.
NAME_FIXES = {
    # header truncations / OCR misreads (unifying keeps one candidate slot)
}


def clean(cell):
    text = re.sub(r'\s+', ' ', TAG.sub('', html.unescape(cell)).replace('\\n', ' ')).strip()
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
    """Comparison key that survives OCR space-dropping."""
    return re.sub(r'[\s\W]+', '', s, flags=re.UNICODE).lower()


# Row labels (compared via squash()).
COUNTY_LABELS = {
    'county': 'pseudo',
    'lucecounty': 'pseudo',
    'michigan': 'pseudo',
    'lucecountymichigan': 'pseudo',
    'lucecountymichigantotal': 'county_total',
    'michigantotal': 'county_total',
    'countytotal': 'county_total',
    'cumulative': 'cumulative',
    'cumulativetotal': 'cumulative',
}
TURNOUT_HEADER = {'registeredvoters', 'voterscast', '%turnout'}


def canon_precinct(label):
    """Match a row label to a canonical precinct (OCR truncates/splits labels);
    None for anything else."""
    key = re.sub(r'\s+', ' ', label).strip()
    if not key or len(key) < 12:
        return None
    if key in PRECINCTS:
        return key
    matches = [p for p in PRECINCTS if p.startswith(key)]
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------- contest titles

TITLE = re.compile(r'^#{0,2}\s*(.+?)\s*\(Vote for\s+(\d+)\)(?:\s+(DEM|REP))?$')


def map_office(title):
    """Map a party-stripped contest title to (office, district)."""
    m = re.match(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^State Sena(?:te|tor) (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'State Senate', m.group(1)
    m = re.match(r'^Representative in State Legislature (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'State House', m.group(1)
    exact = {'Governor': 'Governor', 'United States Senator': 'U.S. Senate'}
    if title in exact:
        return exact[title], ''
    # County offices: prefix with the county name and drop the trailing
    # "for Luce County" tail ("County Prosecuting Attorney Partial Term
    # Ending 12/31/2028 for Luce County" -> "Luce County Prosecuting
    # Attorney Partial Term Ending 12/31/2028", the Ontonagon convention).
    if title.startswith('County '):
        tail = f' for {COUNTY} County'
        if title.endswith(tail):
            title = title[:-len(tail)]
        return f'{COUNTY} {title}', ''
    return title, ''


def slot_of(header_cell):
    """Map a header cell to a slot name: 'TV', 'WI', or the candidate name."""
    sq = squash(header_cell)
    if sq in ('totalvotes', 'totalvote'):
        return 'TV'
    if 'qualifiedwrite' in sq:
        return header_cell  # named qualified write-in candidate column
    if sq.startswith('unr') or 'write' in sq:
        return 'WI'
    return header_cell


# ---------------------------------------------------------------- table parsing

def result_split(row):
    """Index where a row's results half starts: the repeated precinct/county
    label (the last non-numeric, non-empty cell after column 0). The aux and
    results halves don't align by fixed index — OCR renders 'Registered
    Voters' as two header cells but one data cell — so the label repetition
    is the only reliable split point. Falls back to the header divider."""
    idx = max((i for i, c in enumerate(row) if i and c and not is_num(c)),
              default=None)
    return idx + 1 if idx is not None else None


def result_cells(row):
    """The results-half cells of a row (everything after the repeated label)."""
    split = result_split(row)
    return row[split:] if split is not None else []


def parse_turnout_table(rows):
    """Parse the page-1 turnout table. Returns {precinct: voters_cast}."""
    turnout = {}
    precinct = None
    for row in rows:
        label = row[0] if row else ''
        sq = squash(label)
        numerics = [c for c in row[1:] if is_num(c)]
        if sq in COUNTY_LABELS:
            precinct = 'COUNTY' if COUNTY_LABELS[sq] in ('pseudo', 'county_total') else None
            continue
        matched = canon_precinct(label)
        if matched:
            # unlike Montmorency's counting groups, the figures sit on the
            # label's own row: Registered Voters, Voters Cast, % Turnout
            precinct = matched
            if len(numerics) >= 2:
                turnout[precinct] = num(numerics[1])
            continue
        if precinct == 'COUNTY' and len(numerics) >= 2:
            turnout['COUNTY'] = num(numerics[1])
    return turnout


def parse_results_table(rows, where, problems):
    """Parse one contest table.

    Returns (slots, data, totals, times_cast): slots in printed column order
    (candidates, then TV, then WI), data maps precinct -> {slot: votes},
    totals maps slot -> county total from the County - Total row, times_cast
    maps precinct -> Times Cast (verification only).

    The table layout is [label, TC, RV, label2, <result cells>]; the divider
    is the last 'Precinct' header cell (OCR sometimes merges it into
    'Precinct County' or keeps only one of the two). The rotated candidate
    headers often carry a phantom empty cell after each name — and NOT always
    at the same position in data rows — so result values are the row's
    numeric cells in order mapped onto the non-phantom header slots; a
    dropped numeric cell surfaces as a validation mismatch rather than a
    shift.
    """
    hdr = rows[0]
    sq_hdr = [squash(c) for c in hdr]
    precinct_hdrs = [i for i, s in enumerate(sq_hdr) if s.startswith('precinct')]
    if 'timescast' not in sq_hdr or not precinct_hdrs:
        problems.append(f'{where}: unexpected header {hdr}')
        return None, {}, {}, {}
    div = precinct_hdrs[-1]
    slots = [slot_of(c) for c in hdr[div + 1:] if c]
    if not slots:
        slots = ['WI']
    data, totals, times_cast = {}, {}, {}
    for row in rows[1:]:
        label = row[0] if row else ''
        sq = squash(label)
        if sq in COUNTY_LABELS:
            if COUNTY_LABELS[sq] == 'county_total':
                nums = [num(c) for c in result_cells(row) if is_num(c)]
                if len(nums) > len(slots):
                    nums = nums[-len(slots):]
                for slot, v in zip(slots, nums):
                    totals.setdefault(slot, v)
            continue
        p = canon_precinct(label)
        if not p:
            continue  # Cumulative zero block, garbage rows
        # left half: [label, Times Cast, Registered Voters, ...]
        split = result_split(row)
        aux_nums = [c for c in row[1:split] if is_num(c)]
        if aux_nums:
            times_cast[p] = num(aux_nums[0])
        # right half: numeric cells in order onto the slots
        nums = [num(c) for c in row[split:] if is_num(c)]
        if len(nums) > len(slots):
            # p012/p027: OCR duplicates a zero Total Votes cell; keep the
            # tail, which matches the printed columns (TV 0 / WI 19)
            problems.append(f'{where}: {p}: {len(nums)} numeric cells > {len(slots)} slots')
            nums = nums[-len(slots):]
        for slot, v in zip(slots, nums):
            if slot == 'TV':
                data.setdefault(p, {})['TV'] = v
            elif slot == 'WI':
                data.setdefault(p, {})['WI'] = v
            else:
                data.setdefault(p, {}).setdefault('cands', {})[slot] = v
    return slots, data, totals, times_cast


# ---------------------------------------------------------------- main parse

PAGE_HEADER = re.compile(r'^(Page: \d+ of \d+|\d+/\d+/\d+ [\d:]+ [AP]M)$')


def find_title(lines):
    """Locate the contest title in a page's text lines: the printed heading
    may wrap across two lines, and the page footer/header lines sit around
    it. Returns (match, n_lines_consumed) or None."""
    content = [l for l in lines if not PAGE_HEADER.match(l)]
    for i in range(min(4, len(content))):
        acc = ''
        for span in range(3):
            if i + span >= len(content):
                break
            acc = (acc + ' ' + content[i + span]).strip()
            m = TITLE.match(acc)
            if m:
                return m, span + 1
    return None, 0


def build_contests(pages, problems, notes):
    contests = []
    turnout = {}
    current = None
    for no in sorted(pages):
        text, tables, lines = parse_page(pages[no])
        if no == 1:
            # Countywide turnout summary (verification only).
            for tbl in tables:
                turnout.update(parse_turnout_table(tbl))
            continue
        title_m, consumed = find_title(lines)
        if title_m:
            # group(1) ends at "(Vote for N)" and the party parenthetical is
            # its tail: "Governor (DEM)". The bare party token may also follow
            # on the same line (group 3) or on the next line; handle all three.
            t1 = title_m.group(1)
            party = title_m.group(3) or ''
            pm = re.search(r'\s*\((DEM|REP)\)$', t1)
            if pm:
                party = party or pm.group(1)
                t1 = t1[:pm.start()]
            elif not party:
                content = [l for l in lines if not PAGE_HEADER.match(l)]
                if consumed < len(content) and content[consumed] in ('DEM', 'REP'):
                    party = content[consumed]
            title = t1.strip()
            office, district = map_office(title)
            current = {
                'title': title, 'office': office,
                'district': district, 'party': party,
                'vote_for': title_m.group(2), 'pages': [no],
                'cands': {}, 'tv': {}, 'wi': {}, 'totals': {},
                'times_cast': {},
            }
            contests.append(current)
        if current is None:
            continue
        current['pages'].append(no)
        for t_i, tbl in enumerate(tables):
            if not tbl:
                continue
            where = f'p{no} table {t_i} ({current["title"]})'
            slots, data, totals, times_cast = parse_results_table(tbl, where, problems)
            if slots is None:
                continue
            for p, vals in data.items():
                for slot, v in vals.get('cands', {}).items():
                    current['cands'].setdefault(slot, {})[p] = \
                        current['cands'].get(slot, {}).get(p, 0) + v
                if 'TV' in vals:
                    if p in current['tv'] and current['tv'][p] != vals['TV']:
                        problems.append(
                            f'{where}: {p} TV {vals["TV"]} != {current["tv"][p]}')
                    current['tv'][p] = vals['TV']
                if 'WI' in vals:
                    current['wi'][p] = current['wi'].get(p, 0) + vals['WI']
            for slot, v in totals.items():
                current['totals'].setdefault(slot, v)
            for p, v in times_cast.items():
                current['times_cast'][p] = v
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
        # Per-precinct: candidate sum == Total Votes (unresolved write-ins are
        # reported separately and NOT included in Total Votes); TV+WI within
        # Times Cast.
        for p in sorted(precincts):
            csum = sum(c['cands'][cand].get(p, 0) for cand in c['cands'])
            wi = c['wi'].get(p, 0)
            tv = c['tv'].get(p)
            if tv is None:
                problems.append(f'{t} / {p}: no Total Votes cell')
            elif csum != tv:
                problems.append(f'{t} / {p}: candidate sum {csum} != TV {tv}')
            tc = c['times_cast'].get(p)
            # Multi-seat contests (Vote for N) can total up to N per voter.
            limit = tc * int(c['vote_for']) if tc is not None else None
            if tv is not None and limit is not None and tv + wi > limit:
                problems.append(
                    f'{t} / {p}: TV+WI {tv + wi} > Times Cast {tc} x {c["vote_for"]}')
            tv_turnout = turnout.get(p)
            # Single-precinct delegate contests turn out far fewer TV+WI than
            # the precinct's ballots (unopposed / write-in only), so only flag
            # a total above the precinct's ballot count.
            if tv_turnout is not None and tv + wi > tv_turnout:
                problems.append(
                    f'{t} / {p}: TV+WI {tv + wi} > turnout {tv_turnout}')
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
        # Aux Times Cast is total ballots in the precinct for countywide
        # contests, but district-restricted contests (county commissioner
        # districts, single-precinct delegate/clerk contests) print only the
        # participating residents' counts — so flag only a Times Cast above
        # the precinct turnout.
        for p, v in c['times_cast'].items():
            tv_turnout = turnout.get(p)
            if tv_turnout is not None and v > tv_turnout:
                problems.append(f'{t} / {p}: Times Cast {v} > turnout {tv_turnout}')


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
    ap.add_argument('--cache', default='/tmp/paddleocr_md/Luce_County_Aug_2026_Primary_Election_Results')
    ap.add_argument('--out', default='2026/counties/20260804__mi__primary__luce__precinct.csv')
    args = ap.parse_args()

    import glob
    import os
    pages = {}
    for md_path in sorted(glob.glob(os.path.join(args.cache, 'p*.md'))):
        pages[int(re.search(r'p(\d+)\.md$', md_path).group(1))] = open(md_path).read()
    if len(pages) != 33:
        print(f'expected 33 pages, got {len(pages)}', file=sys.stderr)
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