"""Parse the Montmorency County Aug 2026 primary SOVC via cached PaddleOCR markdown.

The PDF is an image-only scan (converted from TIF by CLS Filer, no text layer),
so the page image is the source of truth. The whole PDF was OCR'd with
PaddleOCR-VL (src/fetch_paddleocr_md.py, per-page markdown cached in
/tmp/paddleocr_md) and that markdown is parsed here.

Page grammar (111 pages, counting-group SOVC):
- p1-3: countywide turnout summary (Registered Voters / Voters Cast / % Turnout
  per precinct, split into Election Day / AV Counting Boards / Early Voting /
  Total rows) — read for verification only. p111 is the canvasser's
  certification (text only, ignored).
- Contests open on a page whose text contains "<title> (Vote for N) [DEM|REP]".
  Tables come in page-sized chunks; this document is messier than Ontonagon's:
  * aux (Times Cast / Registered Voters) and results tables are sometimes
    separate tables, sometimes merged side by side in one <table> (split at the
    first non-aux column after the Registered Voters column);
  * some pages render as plain text lines instead of tables (p005, p007, p019,
    p021, p070, p072, p100, p108) — all of them single-column Unresolved
    Write-In tables, parsed by a line state machine;
  * p009 is a garbled rowspan blob (U.S. Senate DEM Stevens/TV/WI for the first
    five precincts) — transcribed manually in MANUAL_TABLES from the page image;
  * header cells are often truncated ('uosun Benson', 'Mallory McMorr') or
    garbled ('≤', '5 支' for Unresolved Write-In) — see slot_of/NAME_FIXES.
- Every table's rows are grouped: a precinct header row (label, empty cells)
  followed by Election Day / AV Counting Boards / Early Voting / Total rows.
  Only the Total row carries the per-precinct figures used here; the printed
  County - Total row provides county totals for cross-checking. Precinct
  labels sometimes split across two rows ('Montmorency Township,' / 'Precinct 1').

Convention (from the committed Wexford/Ontonagon 2026 files): emit candidate
rows (alphabetical, zero rows skipped), a Write-In row only when > 0, and
Ballots Cast = Total Votes + Unresolved Write-In unconditionally. Proposals
keep their printed name without "(Vote for N)"; qualified write-in columns
keep their printed name.

Usage:
    .venv/bin/python src/montmorency_ocr_parser.py \
        [--cache /tmp/paddleocr_md/Montmorency_County_Aug_2026_Primary_Official_Statement_of_Votes_Cast] \
        --out 2026/counties/20260804__mi__primary__montmorency__precinct.csv
"""
import argparse
import csv
import html
import re
import sys

COUNTY = 'Montmorency'
HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

# Canonical precincts in page-1 order (the output order within each contest).
PRECINCTS = [
    'Albert Township, Precinct 1',
    'Avery Township, Precinct 1',
    'Briley Township, Precinct 1',
    'Hillman Township, Precinct 1',
    'Loud Township, Precinct 1',
    'Montmorency Township, Precinct 1',
    'Montmorency Township, Precinct 2',
    'Rust Township, Precinct 1',
    'Vienna Township, Precinct 1',
]

# ---------------------------------------------------------------- markdown -> blocks

TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TR = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
TABLE = re.compile(r'<table.*?</table>', re.S)
TAG = re.compile(r'<[^>]+>')

# OCR name corrections, verified against page images / CENR county file.
NAME_FIXES = {
    # header truncations / OCR misreads (unifying keeps one candidate slot)
    'uosun Benson': 'Jocelyn Benson',          # p006 header drops 'Jocelyn'
    'Kyie Blomquist': 'Kyle Blomquist',        # p012 header: I for l
    'Haley Steven': 'Haley Stevens',           # p008/p009 header truncation
    'Mallory McMorr': 'Mallory McMorrow',      # p008 header truncation
    'Michele Hoiteng': 'Michele Hoitenga',     # p046 header truncation
    'Matthew DenOtt': 'Matthew DenOtter',      # p042 header truncation
    # p016/p017 header; Manistee p10 rotated header reads Yonkman, CENR agrees
    'Mark W. Yorkman': 'Mark W. Yonkman',
    # p026 rotated header reads Sokoll; OCR rendered the final 'll' as 'l'in'
    "John W. Sokol'in": 'John W. Sokoll',
    # p057 rotated header: OCR dropped the final k
    'Rick Warzywa': 'Rick Warzywak',
    # p063 rotated header truncation
    'Michelle Hamli': 'Michelle Hamlin',
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
    'montmorencycounty': 'pseudo',
    'michigan': 'pseudo',
    'montmorencycountymichigan': 'pseudo',
    'montmorencycountymichigantotal': 'county_total',
    'michigantotal': 'county_total',
    'countytotal': 'county_total',
    'cumulative': 'cumulative',
    'cumulativetotal': 'cumulative',
    'electionday': 'skip',
    'avcountingboards': 'skip',
    'earlyvoting': 'skip',
}
GROUP_LABELS = {'total'}
TURNOUT_HEADER = {'registeredvoters', 'voterscast', '%turnout'}
# Header cells that belong to the aux block of a merged table.
AUX_HEADERS = {'', 'precinct', 'timescast', 'registeredvoters', 'registered',
               'voters'}
# Garbled Unresolved Write-In header cells (p080, p094, p098).
WI_GARBAGE = {'≤', '5支', 'unrwm'}


def canon_precinct(label):
    """Match a row label to a canonical precinct (OCR truncates/splits labels);
    None for anything else or for ambiguous prefixes (Montmorency 1 vs 2)."""
    key = re.sub(r'\s+', ' ', label).strip()
    if not key or len(key) < 12:
        return None
    if key in PRECINCTS:
        return key
    matches = [p for p in PRECINCTS if p.startswith(key)]
    return matches[0] if len(matches) == 1 else None


SPLIT_LABEL = re.compile(r'^precinct\s*\d+$')


def label_candidates(row, pending):
    """Candidate precinct strings for a row: the label may be split across
    rows (pending + col 0) or across cells (col 0 + col 1)."""
    label = row[0] if row else ''
    cands = []
    if pending:
        cands.append((pending + ' ' + label).strip())
    if len(row) > 1 and row[1] and not is_num(row[1]):
        cands.append((label + ' ' + row[1]).strip())
    cands.append(label)
    return cands


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
    # County offices: prefix with the county name (2026-series convention).
    if title.startswith('County '):
        return f'Montmorency {title}', ''
    return title, ''


# ---------------------------------------------------------------- table parsing

def slot_of(header_cell):
    """Map a header cell to a slot name: 'TV', 'WI', or the candidate name."""
    sq = squash(header_cell)
    if sq in ('totalvotes', 'totalvote'):
        return 'TV'
    if 'qualifiedwrite' in sq:
        return header_cell  # named qualified write-in candidate column
    if sq.startswith('unr') or 'write' in sq or sq in WI_GARBAGE:
        return 'WI'
    return header_cell


def split_merged(rows):
    """Split a table that merges the aux (Times Cast / Registered Voters) and
    results sub-tables side by side. Returns (aux_rows, result_rows) or None
    for a pure aux table (no result columns after the aux block)."""
    hdr = [squash(c) for c in rows[0]]
    if 'timescast' not in hdr:
        return None
    last_aux = max((i for i, s in enumerate(hdr)
                    if s in ('registeredvoters', 'registered', 'voters')),
                   default=hdr.index('timescast'))
    split = last_aux + 1
    if split >= len(hdr) or all(squash(c) in AUX_HEADERS for c in rows[0][split:]):
        return None  # pure aux table
    return [r[:split] for r in rows], [r[split:] for r in rows]


def parse_table(rows, where, problems, slots_override=None):
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
    pending = ''    # precinct label split across consecutive rows
    for i, row in enumerate(rows):
        if any(is_num(c) for c in row):
            first_data = i
            break
        if row and squash(row[0]) in COUNTY_LABELS:
            pending = ''  # county pseudo-rows must not pollute a split label
            continue
        for cand in label_candidates(row, pending):
            p = canon_precinct(cand)
            if p:
                initial = p
                pending = ''
                break
        else:
            pending = (pending + ' ' + (row[0] if row else '')).strip()
        for cell in row[1:]:
            if not cell or squash(cell) in ('precinct', 'registeredvoters',
                                            'registered', 'voters') \
                    or SPLIT_LABEL.match(squash(cell)):
                continue
            slot = slot_of(cell)
            if slot not in slots:
                slots.append(slot)
    # Slots stay in printed (encounter) order: this document places Total
    # Votes between candidate columns in some tables (p050), so TV must not
    # be forced to the end.
    # p052 OCR splits 'Brian Heft / Qualified Write In' across two header
    # cells — rejoin a trailing 'Qualified Write In' fragment to its name.
    joined = []
    for s in slots:
        if squash(s) == 'qualifiedwritein' and joined:
            joined[-1] += ' ' + s
        else:
            joined.append(s)
    slots = joined
    if slots_override:
        slots = slots_override
    elif not slots:
        # continuation table whose header cell was garbled away (p058, p102,
        # p104): the single data column is Unresolved Write-In
        slots = ['WI']

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
    pending = ''
    for row in rows[first_data:]:
        label = row[0] if row else ''
        sq = squash(label)
        if sq in COUNTY_LABELS:
            kind = COUNTY_LABELS[sq]
            if kind == 'skip':
                continue  # counting-group row; precinct stays current
            precinct = None
            pending = ''
            if kind == 'county_total' and any(c for c in row[1:]):
                totals.update(assign(row[1:]))
            continue
        if sq in GROUP_LABELS:  # 'Total'
            if precinct:
                data.setdefault(precinct, {}).update(assign(row[1:]))
            continue
        matched = None
        for cand in label_candidates(row, pending):
            p = canon_precinct(cand)
            if p:
                matched = p
                break
        if matched:
            precinct = matched
            pending = ''
        elif label and not is_num(label):
            # maybe the first half of a label split across rows
            pending = (pending + ' ' + label).strip()
        # anything else (garbage rows) is ignored
    return slots, data, totals


def parse_aux(rows, current, where, problems):
    """Parse a pure aux table: per-precinct Times Cast, verification only."""
    precinct = None
    pending = ''
    for row in rows:
        label = row[0] if row else ''
        sq = squash(label)
        numerics = [c for c in row[1:] if is_num(c)]
        if sq in COUNTY_LABELS:
            if COUNTY_LABELS[sq] == 'skip':
                continue  # counting-group row; precinct stays current
            precinct = None
            pending = ''
            if COUNTY_LABELS[sq] == 'county_total' and numerics:
                current['tc_total'] = num(numerics[0])
            continue
        if sq == 'total' and precinct and numerics:
            current['times_cast'][precinct] = num(numerics[0])
            continue
        matched = None
        for cand in label_candidates(row, pending):
            p = canon_precinct(cand)
            if p:
                matched = p
                break
        if matched:
            precinct = matched
            pending = ''
        elif label and not is_num(label):
            pending = (pending + ' ' + label).strip()


def parse_text_page(lines, where, problems):
    """Parse a text-only page (tables rendered as lines): single-column
    Unresolved Write-In tables.

    Returns data maps precinct -> WI votes, using only Total rows. Precinct
    labels may split across lines; data lines look like 'Election Day 0'."""
    data = {}
    precinct = None
    pending = ''
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        sq = squash(line)
        if sq in COUNTY_LABELS and COUNTY_LABELS[sq] != 'skip':
            precinct = None
            pending = ''
            continue
        m = re.match(r'^(Election Day|AV Counting Boards|Early Voting|Total)\s+'
                     r'((?:\d[\d,]*\s*)+)$', line)
        if m:
            if m.group(1) == 'Total' and precinct:
                vals = [num(v) for v in m.group(2).split()]
                if len(vals) != 1:
                    problems.append(f'{where}: {precinct} Total row has {len(vals)} values')
                data.setdefault(precinct, {})['WI'] = vals[0] if vals else 0
            continue
        matched = None
        for cand in (pending + ' ' + line if pending else line, line):
            p = canon_precinct(cand)
            if p:
                matched = p
                break
        if matched:
            precinct = matched
            pending = ''
        elif not is_num(line) and line not in ('Precinct',) and not sq.startswith('unres'):
            pending = (pending + ' ' + line).strip()
    return data


# Manual transcriptions of pages/tables the OCR garbled, read from the
# rendered page images. Page -> {'tables': {table_index: cell-grid rows}}
# replaces just those tables; other tables on the page parse normally.
MANUAL_TABLES = {
    # p009: rowspan blob — U.S. Senate DEM Stevens/TV/WI, first five precincts.
    9: {'tables': {0: [
        ['Precinct', 'Haley Stevens', 'Total Votes', 'Unresolved Write-In'],
        ['County', '', '', ''],
        ['Montmorency County', '', '', ''],
        ['Michigan', '', '', ''],
        ['Albert Township, Precinct 1', '', '', ''],
        ['Election Day', '44', '84', '0'],
        ['AV Counting Boards', '144', '224', '1'],
        ['Early Voting', '2', '3', '0'],
        ['Total', '190', '311', '1'],
        ['Avery Township, Precinct 1', '', '', ''],
        ['Election Day', '31', '56', '0'],
        ['AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '1', '6', '0'],
        ['Total', '32', '62', '0'],
        ['Briley Township, Precinct 1', '', '', ''],
        ['Election Day', '91', '152', '1'],
        ['AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '5', '18', '0'],
        ['Total', '96', '170', '1'],
        ['Hillman Township, Precinct 1', '', '', ''],
        ['Election Day', '96', '169', '0'],
        ['AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '0', '2', '0'],
        ['Total', '96', '171', '0'],
        ['Loud Township, Precinct 1', '', '', ''],
        ['Election Day', '19', '28', '0'],
        ['AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '1', '1', '0'],
        ['Total', '20', '29', '0'],
    ]}},
    # p006 T1: OCR dropped the per-precinct Total Votes cell on every row.
    6: {'tables': {1: [
        ['Precinct', 'uosun Benson', 'Christopher Robert Swanson', 'Total Votes'],
        ['County', '', '', ''],
        ['Montmorency County', '', '', ''],
        ['Michigan', '', '', ''],
        ['Montmorency Township, Precinct 1', '', '', ''],
        ['Election Day', '41', '7', '48'],
        ['AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '3', '3', '3'],
        ['Total', '44', '7', '51'],
        ['Montmorency Township, Precinct 2', '', '', ''],
        ['Election Day', '65', '17', '82'],
        ['AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '0', '0', '0'],
        ['Total', '65', '17', '82'],
        ['Rust Township, Precinct 1', '', '', ''],
        ['Election Day', '32', '7', '39'],
        ['AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '0', '0', '0'],
        ['Total', '32', '7', '39'],
        ['Vienna Township, Precinct 1', '', '', ''],
        ['Election Day', '47', '12', '59'],
        ['AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '0', '1', '1'],
        ['Total', '47', '13', '60'],
        ['Montmorency County Michigan - Total', '803', '157', '960'],
        ['Cumulative', '', '', ''],
        ['Election Day', '0', '0', '0'],
        ['AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '0', '0', '0'],
        ['Total', '0', '0', '0'],
        ['Cumulative - Total', '0', '0', '0'],
        ['County - Total', '803', '157', '960'],
    ]}},
    # p099: merged aux+results table; OCR dropped the No column on every
    # results-side row (the county row fragment kept Yes 321 / No 134).
    99: {'tables': {0: [
        ['', 'Times Cast', 'Registered Voters', 'Precinct', 'Yes', 'No', 'Total Votes'],
        ['County', '', '', 'County', '', '', ''],
        ['Montmorency County', '', '', 'Montmorency County', '', '', ''],
        ['Michigan', '', '', 'Michigan', '', '', ''],
        ['Montmorency Township, Precinct 1', '', '', 'Montmorency Township, Precinct 1', '', '', ''],
        ['Election Day', '238', '616', 'Election Day', '161', '64', '225'],
        ['AV Counting Boards', '0', '616', 'AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '5', '616', 'Early Voting', '4', '1', '5'],
        ['Total', '243', '616', 'Total', '165', '65', '230'],
        ['Montmorency Township, Precinct 2', '', '', 'Montmorency Township, Precinct 2', '', '', ''],
        ['Election Day', '238', '476', 'Election Day', '154', '68', '222'],
        ['AV Counting Boards', '0', '476', 'AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '3', '476', 'Early Voting', '2', '1', '3'],
        ['Total', '241', '476', 'Total', '156', '69', '225'],
        ['Montmorency County Michigan - Total', '484', '1,092', 'Montmorency County Michigan - Total', '321', '134', '455'],
        ['Cumulative', '', '', 'Cumulative', '', '', ''],
        ['Election Day', '0', '0', 'Election Day', '0', '0', '0'],
        ['AV Counting Boards', '0', '0', 'AV Counting Boards', '0', '0', '0'],
        ['Early Voting', '0', '0', 'Early Voting', '0', '0', '0'],
        ['Total', '0', '0', 'Total', '0', '0', '0'],
        ['Cumulative - Total', '0', '0', 'Cumulative - Total', '0', '0', '0'],
        ['County - Total', '484', '1,092', 'County - Total', '321', '134', '455'],
    ]}},
}


# ---------------------------------------------------------------- main parse

def build_contests(pages, problems, notes):
    contests = []
    turnout = {}
    current = None
    for no in sorted(pages):
        text, tables, lines = parse_page(pages[no])
        if no <= 3:
            # Countywide turnout summary (verification only): parse every
            # table (p001's header line sits outside the table, so a header
            # test would skip it).
            for tbl in tables:
                turnout.update(parse_turnout_table(tbl))
            continue
        title_m = next((m for m in (TITLE.match(l) for l in lines[:3]) if m), None)
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
            elif not party and len(lines) > 3 and lines[3] in ('DEM', 'REP'):
                party = lines[3]
            title = t1.strip()
            office, district = map_office(title)
            current = {
                'title': title_m.group(1), 'office': office,
                'district': district, 'party': party,
                'vote_for': title_m.group(2), 'pages': [no],
                'cands': {}, 'tv': {}, 'wi': {}, 'totals': {},
                'times_cast': {}, 'tc_total': None,
            }
            contests.append(current)
        if current is None:
            continue
        current['pages'].append(no)

        if not tables and no in (5, 7, 19, 21, 70, 72, 100, 108):
            # Text-only page: single-column Unresolved Write-In table.
            where = f'p{no} text ({current["title"]})'
            data = parse_text_page(lines, where, problems)
            for precinct, vals in data.items():
                current['wi'][precinct] = current['wi'].get(precinct, 0) + vals['WI']
            continue

        if no in MANUAL_TABLES:
            man = MANUAL_TABLES[no]['tables']
            for t_i, tbl in enumerate(tables):
                if t_i in man:
                    # apply clean() so header cells get NAME_FIXES like OCR tables
                    tables[t_i] = [[clean(c) for c in row] for row in man[t_i]]

        for t_i, tbl in enumerate(tables):
            if not tbl:
                continue
            parts = split_merged(tbl)
            if parts:
                aux_rows, result_rows = parts
                parse_aux(aux_rows, current, f'p{no} table {t_i} aux', problems)
                tbl = result_rows
            elif any('timescast' in squash(c) for row in tbl for c in row):
                parse_aux(tbl, current, f'p{no} table {t_i} aux', problems)
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


def parse_turnout_table(rows):
    """Parse a page 1-3 turnout table. Returns {precinct: voters_cast}."""
    turnout = {}
    precinct = None
    pending = ''
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
        matched = None
        for cand in label_candidates(row, pending):
            p = canon_precinct(cand)
            if p:
                matched = p
                break
        if matched:
            precinct = matched
            pending = ''
            continue
        if sq == 'total' and precinct and len(numerics) >= 2:
            # columns are Registered Voters, Voters Cast, % Turnout
            if precinct == 'COUNTY':
                turnout['COUNTY'] = num(numerics[1])
            else:
                turnout[precinct] = num(numerics[1])
        elif not numerics and not is_num(label):
            pending = (pending + ' ' + label).strip()
    return turnout


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
        # districts, school districts) print only the district residents'
        # counts — so flag only a Times Cast above the precinct turnout.
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
    ap.add_argument('--cache', default='/tmp/paddleocr_md/Montmorency_County_Aug_2026_Primary_Official_Statement_of_Votes_Cast')
    ap.add_argument('--out', default='2026/counties/20260804__mi__primary__montmorency__precinct.csv')
    args = ap.parse_args()

    import glob
    import os
    pages = {}
    for md_path in sorted(glob.glob(os.path.join(args.cache, 'p*.md'))):
        pages[int(re.search(r'p(\d+)\.md$', md_path).group(1))] = open(md_path).read()
    if len(pages) != 111:
        print(f'expected 111 pages, got {len(pages)}', file=sys.stderr)
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