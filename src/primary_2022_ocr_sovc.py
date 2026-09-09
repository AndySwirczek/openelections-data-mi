"""Shared parser for 2022 primary counties whose PaddleOCR markdown is the
two-table SOVC layout: a heading line ('<contest> (DEM) (Vote for 1) DEM')
followed by ONE table carrying a left turnout half (Precinct | Times Cast |
Registered Voters) and a right results half (Precinct | <candidate> | ...
| Total Votes). Per-candidate columns are a (votes, %) pair the OCR header
usually collapses to just the name, so data cells are read by pattern, not
position: percent cells are skipped, each integer goes to the next candidate
in header order, and the trailing Total Votes integer (beyond the candidate
count) is ignored. Zero-vote candidates' columns are dropped from the source
table entirely.

Gogebic quirks: candidate votes and their percent print glued in one cell
('74 64.35%'); wrapped precinct labels split across two OCR rows (candidate
values on the first row, Times Cast/Registered on the second), which are
merged. Contest tables continue onto later pages without re-printing the
header. Every contest's printed County - Total per candidate must equal the
sum of its precinct rows, candidate names must exist in the certified
county-level CENR, and the parsed sums are verified against that CENR via
primary_2022_common.verify().

Antrim variant: each contest prints a turnout-only table (Precinct | Times
Cast | Registered Voters, no candidates) that is skipped, followed by a
results-only table (Precinct | <candidate> ... | Total Votes | Unresolved
Write-In) whose precinct label is the first cell. OCR mangles the header
words heavily ('Times Cost', 'Predict', 'Unresolved White-in'), so table
classification runs on prefixes ('Time*', 'Registered*') rather than
exact tokens, and a results table is any table with candidate columns
and no turnout columns — even when OCR drops the Total Votes/Write-In
headers entirely (surplus integers then fall past the candidate count
and are ignored). Turnout-only and zero-candidate tables never disturb
the pending heading or the open contest.

Usage:
    .venv/bin/python src/primary_2022_ocr_sovc.py <County> [--write]
"""
import argparse
import collections
import glob
import html
import re

from primary_2022_common import CENR, classify, one_space, verify, write

CACHE = '/tmp/paddleocr_md'
COUNTIES = {
    'Antrim': 'Antrim_MI_Official_Election_Results_August_2_2022',
    'Charlevoix': 'Charlevoix_County_Aug_2022_Statement_of_Votes_Cast',
    'Gogebic': 'Gogebic_MI_PDF_Statement_of_Votes_Cast',
    'Kalkaska': 'Kalkaska_MI_Statement_of_Votes_Cast_August_2_2022',
    'Montmorency': 'Montmorency_County_Aug_2022_Primary_Statement_of_Votes_Cast',
    'Otsego': 'Otsego_MI_August_2_2022_Statement_of_Votes_Cast_PDF_',
    'Schoolcraft': 'Schoolcraft_MI_Aug_2022_Statement_of_Votes_Cast',
}
# OCR-garbled candidate names in table headers
NAME_FIX = {
    'Antrim': {
        'Gretchen Whitner': 'Gretchen Whitmer',
        'Ralph Randell': 'Ralph Rebandt',
        'Kevin Riste': 'Kevin Rinke',
        'Gianet Soldano': 'Garrett Soldano',
        'George Rawlin': 'George Ranville',
        'William Hinds': 'William Hindle',
        'John N. Damocne': 'John N. Damoose',
        'Bob Lornier': 'Bob Lorinser',
        'Adam J. Woydan': 'Adam J. Wojdan',
        'Ken Morley': 'Kim Morley',
        'Mark McArlin': 'Mark McFarlin',
    },
    'Gogebic': {'Gregory Markanen': 'Gregory Markkanen'},
    'Charlevoix': {'Parker Fairbair': 'Parker Fairbairn'},
    'Kalkaska': {'Michele Höitenga': 'Michele Hoitenga'},
    'Otsego': {'Joel A. Sheltown': 'Joel A. Sheltrown'},
}
# CENR candidates the printed report omits entirely (verified against the
# printed rows), accepted as NOTEs rather than problems
ALLOWED = {
    'Antrim': {
        ('Governor', '', 'REP', 'James Elmer Craig'):
            'his column is absent from the printed report, whose Total '
            '5,446 excludes his 32 certified votes',
    },
}
PCT = re.compile(r'^\d+(?:\.\d+)?%$|^N/A$')
INT = re.compile(r'^\d[\d,]*$')
GLUED = re.compile(r'^(\d[\d,]*)\s+\d+(?:\.\d+)?%$')
TOTAL_LABEL = re.compile(r'\bTotal\s*$', re.I)
# write-in headers OCR badly ('Unresolved White-in', 'Unreached Write-in');
# a bare 'Wri' prefix would also catch candidates like Whitmer
WRITEIN_COL = re.compile(r'Unres|Write|Writs|Writing', re.I)
TURNOUT_START = ('Time', 'Registered')
TURNOUT_EXACT = {'Cards Cast', 'Voters Cast', '% Turnout'}
# words that mark a contest heading (candidate names carry a party tag too,
# but never one of these)
HEADING_WORD = re.compile(
    r'Governor|Senator|Representative|District|Commissioner|Delegate'
    r'|Supervisor|Trustee|Millage|Proposal|Judge|Clerk|Treasurer'
    r'|Attorney|Secretary of State', re.I)


def heading_key(text):
    """contest key for a heading-shaped text, False if it is not a heading
    (a candidate name like 'Randy Bishop (DEM)' also carries a party tag),
    None for a local contest's heading (which classifies to no CENR key)"""
    if not re.search(r'\((DEM|REP)\)', text) or not HEADING_WORD.search(text):
        return False
    return classify(re.sub(r'\s+(?:DEM|REP)\s*$', '', text))
WRITEIN_TOKEN = '<write-in>'
SKIP_TOKEN = '<skip>'
# per-county options: Montmorency prints each precinct as three rows
# (label with no values, then 'Election Day' and 'AV Counting Boards'
# sub-rows that must be summed, then a per-precinct 'Total' check row)
OPTS = {
    'Montmorency': {'method_rows': True,
                    'header_fixes': True,
                    'county_header_skip': True,
                    'orphans': True},
    # Charlevoix's page-group results tables split a contest's candidates
    # across column groups, and the OCR drops several groups' headers (and
    # their headings) wholesale; the group's continuation table carries the
    # names, so buffer the headless tables for it. Unlike Montmorency's
    # fragments, an orphan may only be absorbed by a table of the same
    # contest (its buffered tag), and its first row is data, not a header
    # remnant.
    'Charlevoix': {'orphans': True, 'strict_orphan_key': True,
                   'orphan_row0_data': True, 'blank_cell_consumes': True},
    # Kalkaska's Governor (REP) table prints a phantom empty column in its
    # header (and a matching pad in each data row); blank-free headers'
    # data pads are colspan padding that must be skipped
    'Kalkaska': {'blank_cell_consumes': True},
    # Otsego prints each precinct as a label row plus Election Day /
    # AV Counting Boards sub-rows and a per-precinct Total check row
    'Otsego': {'method_rows': True},
}
METHOD_RE = re.compile(r'Election Day|AV Counting Boards|Absentee', re.I)
# Montmorency: the Governor (REP) page-34 table prints each write-in
# candidate as one column whose label is two lines ('James Elmer Craig' /
# 'Qualified Write-In'); the OCR split those labels into separate header
# cells and inserted two phantom zero columns, so the classified header
# tokens mis-map the data columns. Keyed on (contest key, raw header
# tokens) -> replacement tokens, verified against the printed per-precinct
# Total Votes (which exclude Unresolved but include all named candidates).
HEADER_OVERRIDE = {
    'Montmorency': {
        ('Governor', '', 'REP'): {
            ('Ralph Rebandt', 'Kevin Rinke', 'Garrett Soldano', SKIP_TOKEN,
             'Elizabeth Ann Adkisson', WRITEIN_TOKEN, 'Justin Paul Blackburn',
             WRITEIN_TOKEN, 'James Elmer Craig', WRITEIN_TOKEN):
                ['Ralph Rebandt', 'Kevin Rinke', 'Garrett Soldano', SKIP_TOKEN,
                 'Elizabeth Ann Adkisson', SKIP_TOKEN, 'Justin Paul Blackburn',
                 'James Elmer Craig', SKIP_TOKEN, WRITEIN_TOKEN],
        },
    },
}
# Kalkaska: the OCR fused a results table's header cells into its first
# cell. Keyed on the fused text; splitting restores the normal header path
# (whose candidate-based inference recovers the contest key).
FUSED_HEADERS = {
    'Kalkaska': {
        'Precinct Adam J. Wojdan': ['Precinct', 'Adam J. Wojdan'],
        'Precinct Ken Borton Mark McFarlin':
            ['Precinct', 'Ken Borton', 'Mark McFarlin'],
    },
}
# Kalkaska: the OCR dropped the header of Governor (REP) qualified write-in
# James Elmer Craig's column entirely, leaving a single-'Precinct' spill
# table. Identified by its printed county total (9 = CENR Craig); the
# synthesized header routes it through candidate-based inference.
SPILL_TABLES = {
    'Kalkaska': {9: ['Precinct', 'James Elmer Craig']},
}
# OCR-garbled precinct labels in emitted rows
LABEL_FIX = [
    (re.compile(r'\bEvery Township\b'), 'Avery Township'),
    (re.compile(r'\bMontmoreny\b'), 'Montmorency'),
    (re.compile(r'\bPeane Township\b'), 'Peaine Township'),
]
# Charlevoix: the OCR fused consecutive precinct rows of a results table
# into single cells ('City of Charlevoix, Ward 1, 146 146 4' + 'Precinct 18'
# + ...), dropping the value cells into the label. Keyed on the row's
# joined text; each entry is verified against the printed per-precinct
# Total Votes of the sibling column-group table (which carries all of the
# contest's candidates) and the printed County - Total.
FUSED_ROWS = {
    'Charlevoix': {
        'City of Charlevoix, Ward 1, 146 146 4 Precinct 18 City of '
        'Charlevoix, Ward 2, 119 119 1 Precinct 19 City of Charlevoix, '
        'Ward 3, 157 157 4 Precinct 20': [
            ['City of Charlevoix, Ward 1, Precinct 18', '146', '146', '4'],
            ['City of Charlevoix, Ward 2, Precinct 19', '119', '119', '1'],
            ['City of Charlevoix, Ward 3, Precinct 20', '157', '157', '4'],
        ],
        'City of East Jordan, Precinct 263 263 3':
            [['City of East Jordan, Precinct 21', '263', '263', '3']],
        'City of Charlevoix, Ward 1, 111 111 0 Precinct 18 City of '
        'Charlevoix, Ward 2, 94 94 0 Precinct 19 City of Charlevoix, '
        'Ward 3, 69 69 0 Precinct 20': [
            ['City of Charlevoix, Ward 1, Precinct 18', '111', '111', '0'],
            ['City of Charlevoix, Ward 2, Precinct 19', '94', '94', '0'],
            ['City of Charlevoix, Ward 3, Precinct 20', '69', '69', '0'],
        ],
        'City of Charlevoix, Ward 1, 17 48 Precinct 18 City of Charlevoix, '
        'Ward 2, 4 43 Precinct 19 City of Charlevoix, Ward 3, 12 35 '
        'Precinct 20 City of East Jordan, Precinct 14 89 21': [
            ['City of Charlevoix, Ward 1, Precinct 18', '17', '48'],
            ['City of Charlevoix, Ward 2, Precinct 19', '4', '43'],
            ['City of Charlevoix, Ward 3, Precinct 20', '12', '35'],
            ['City of East Jordan, Precinct 21', '14', '89'],
        ],
        'Boyne Valley Township, 28 56 Precinct 2 Chandler Township, '
        'Precinct 2 16 3 Charlevoix Township, Precinct 22 113 4 '
        'Evangeline Township, 13 50 Precinct 5': [
            ['Boyne Valley Township, Precinct 2', '28', '56'],
            ['Chandler Township, Precinct 3', '2', '16'],
            ['Charlevoix Township, Precinct 4', '22', '113'],
            ['Evangeline Township, Precinct 5', '13', '50'],
        ],
        'Norwood Township, Precinct 11 56':
            [['Norwood Township, Precinct 11', '11', '56']],
    },
}


def fix_label(label):
    for rx, repl in LABEL_FIX:
        label = rx.sub(repl, label)
    return label


def header_remnant(row):
    """a results-table header row the OCR left without candidate names
    ('Precinct | County | Total Votes | Unresolved Write-In') — the table
    below it belongs to the open contest but its header is unusable."""
    text = one_space(' '.join(row))
    if re.search(r'\d', text):
        return False
    toks = classify_cells(row)
    return all(t in (WRITEIN_TOKEN, SKIP_TOKEN) for t in toks) and \
        any(re.match(r'Pr\w*ct', c) or c == 'County' or 'Total' in c
            for c in row)


def fix_header_toks(county, toks):
    """Montmorency header repairs: merge consecutive write-in cells the OCR
    split into two ('Unresolved' + 'Write-In'), and merge consecutive name
    cells whose concatenation is a CENR candidate ('David John' +
    'McDonell' wrapping inside the header cell)."""
    cenr_names = {k[3] for k in CENR[county]}
    merged = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in (WRITEIN_TOKEN, SKIP_TOKEN):
            merged.append(t)
            i += 1
            continue
        name = t
        i += 1
        while i < len(toks) and toks[i] not in (WRITEIN_TOKEN, SKIP_TOKEN) \
                and one_space(f'{name} {toks[i]}') in cenr_names:
            name = one_space(f'{name} {toks[i]}')
            i += 1
        merged.append(name)
    out = []
    for t in merged:
        if t == WRITEIN_TOKEN and out and out[-1] == WRITEIN_TOKEN:
            continue
        out.append(t)
    return out


def turnout_shape(chunk):
    """every precinct group's method sub-rows carry exactly two integers
    with an identical second integer (Times Cast / Registered Voters) —
    a turnout table the OCR stripped its header from, not results."""
    groups = {}   # precinct group -> second integers of its method rows
    group = None
    n_method = 0
    for row in chunk:
        if not row:
            continue
        if len(row) == 1:
            lab = one_space(row[0].replace('\\n', ' '))
            if re.search(r'\d', lab) and re.search(r'[A-Za-z]', lab):
                group = lab
            continue
        joined = one_space(' '.join(row))
        if re.match(r'Cumulative', joined, re.I):
            continue
        nums = [one_space(c) for c in row if INT.match(one_space(c))]
        if not re.search(r'[A-Za-z]', joined):
            if nums and len(nums) != 2:
                return False   # a numeric results row
            continue   # furniture (empty or numeric-only row)
        if METHOD_RE.search(joined) or re.search(r'\bTotal\b', joined, re.I):
            if group is None:
                return False
            if len(nums) != 2:
                return False   # a results row (candidate votes)
            groups.setdefault(group, []).append(nums[1])
            n_method += 1
        elif not nums:
            continue   # county / 'Michigan' furniture with a mangled label
        else:
            return False   # a real results row
    return n_method >= 2 and groups and \
        all(len(set(v)) == 1 for v in groups.values())


def cells_of(tr):
    return [html.unescape(one_space(re.sub(r'<[^>]+>', '', c)))
            for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]


# a bare contest-table row the OCR emitted without <table> markup:
# 'City of Boyne City, Precinct 16 192 64' — the label may itself end in
# the precinct number, so anchor on it when present
DATA_ROW = re.compile(
    r'^(?:.+?\b[Pp]recinct\s+\d+|.+?\b[Ww]ard\s+\d+)'
    r'\s+((?:\d{1,3}(?:,\d{3})*\s+)+\d{1,3}(?:,\d{3})*)$')


def parse_pages(cache):
    """[(kind, payload)] in page order; a table may be unterminated (OCR
    dropped the closing tag) — it then ends at the next '<table' (or the
    page end), and the text lines between tables are kept as text. The OCR
    sometimes drops a table's markup entirely, leaving bare rows; runs of
    such rows are synthesized into tables, with the preceding non-numeric
    lines (county-furniture lines excepted) as the header row."""
    items = []
    header_lines = []     # text lines awaiting the plain-text table they head
    run = None            # rows of a plain-text table being built

    def flush_run():
        nonlocal run
        if run is not None:
            hdr = [l for l in header_lines if not re.search(r'\bCounty$', l)]
            items.append(('table', [hdr] + run if hdr else run))
            header_lines.clear()
            run = None

    for path in sorted(glob.glob(f'{CACHE}/{cache}/p*.md')):
        md = open(path).read()
        pos = 0
        while pos < len(md):
            start = md.find('<table', pos)
            for line in md[pos:start if start >= 0 else len(md)].splitlines():
                text = one_space(re.sub(r'<[^>]+>', '', line).strip())
                if not text:
                    continue
                m = DATA_ROW.match(text)
                if m:
                    if run is None:
                        run = []   # the buffered text lines head this run
                    label = re.sub(r'\s+' + re.escape(m.group(1)) + r'$',
                                   '', text)
                    run.append([label] + m.group(1).split())
                else:
                    if run is not None:
                        flush_run()   # run ended; later lines head the next one
                    header_lines.append(text)
            flush_run()
            if start < 0:
                for l in header_lines:
                    items.append(('text', l))
                header_lines.clear()
                break
            nxt = md.find('<table', start + 1)
            end = md.find('</table>', start)
            if end < 0 or (nxt >= 0 and end > nxt):
                pos = nxt if nxt >= 0 else len(md)
                seg = md[start:pos]
            else:
                pos = end + len('</table>')
                seg = md[start:pos]
            # text buffered before a real table is furniture / a heading —
            # emit it before the table so a heading precedes what it heads
            for l in header_lines:
                items.append(('text', l))
            header_lines.clear()
            items.append(('table',
                          [cells_of(tr) for tr in
                           re.findall(r'<tr>(.*?)</tr>', seg, re.S)]))
    return items


def is_turnout_cell(cell):
    # OCR mangles the turnout headers freely ('Times Cost', 'Registered
    # Visitors', a whole 'Precinct Times Cast Registered Voters' row in
    # one cell), so match on word fragments, not exact tokens
    return cell.startswith(TURNOUT_START) or cell in TURNOUT_EXACT \
        or re.search(r'\bTimes?\b|\bRegistered\b|Cards?\s*Cast'
                     r'|Voters?\s*Cast|Turnout', cell, re.I)


def classify_cells(cells):
    """candidate-column tokens: names, WRITEIN_TOKEN, or SKIP_TOKEN for
    the trailing total / turnout / unnamed percent columns. Candidate
    names may carry a trailing party tag ('Jim Schmidt (DEM)') that the
    heading-based classify doesn't see."""
    toks = []
    for cell in cells:
        cell = one_space(cell.replace('\\n', ' '))
        cell = re.sub(r'\s*\((?:DEM|REP)\)\s*$', '', cell)
        if not cell or cell.startswith('Total') or is_turnout_cell(cell) \
                or cell in ('County', 'Precinct', 'Candidate', 'Voters',
                            'Predict'):
            toks.append(SKIP_TOKEN)
        elif WRITEIN_COL.search(cell):
            toks.append(WRITEIN_TOKEN)
        else:
            toks.append(cell)
    return toks


def header_cells(row, county):
    """(kind, [candidate tokens]) for a contest-table header row, or None.
    kind 'combined' is a turnout half + results half table (the right
    results half starts at the last 'Precinct' header cell); kind 'results'
    is a results-only table whose precinct label is the first cell; kind
    'turnout' is a turnout-only table (no candidates — skipped). None means
    no header: the rows belong to the open contest (a totals/write-in-only
    spill table)."""
    cells = [one_space(c) for c in row]
    if OPTS.get(county, {}).get('county_header_skip'):
        # Montmorency prints a phantom 'County' header cell with no data
        # column under it (verified: the numbers line up only once it is
        # dropped); turnout headers keep theirs harmlessly
        cells = [c for c in cells if c != 'County']
    if any(is_turnout_cell(c) for c in cells):
        right_idx = max((i for i, c in enumerate(cells) if c == 'Precinct'),
                        default=-1)
        if right_idx >= 0:
            toks = classify_cells(cells[right_idx + 1:])
            if any(t not in (WRITEIN_TOKEN, SKIP_TOKEN) for t in toks):
                return 'combined', toks
        return 'turnout', []
    toks = classify_cells(cells[1:])
    if any(t not in (WRITEIN_TOKEN, SKIP_TOKEN) for t in toks):
        if any(re.search(r'\d', t) for t in toks):
            return None   # a name never carries digits; these are fused
                          # data rows the OCR printed as one header row
        return 'results', toks
    return None


def split_row(row):
    """(right label, candidate cells) — the right results half starts at the
    LAST non-numeric, non-empty cell: the header's 'Registered Voters' can
    print split across two cells while data rows merge them, so header
    column indices do not align with data rows. In Montmorency's layout a
    per-method cell ('Election Day', bare 'Total') sits between the
    precinct label and the numbers; it belongs to the label region, so
    step over it (and drop it from the value cells)."""
    def is_num(c):
        return not one_space(c) or INT.match(c) or PCT.match(c) \
            or GLUED.match(c)

    i = len(row) - 1
    while i > 0 and is_num(row[i]):
        i -= 1
    dropped = []
    while i > 0 and (METHOD_RE.search(row[i])
                     or re.fullmatch(r'Total', one_space(row[i]), re.I)):
        dropped.append(i)
        i -= 1
        while i > 0 and is_num(row[i]):
            i -= 1
    return one_space(row[i].replace('\\n', ' ')), \
        [c for j, c in enumerate(row[i + 1:], i + 1) if j not in dropped]


def county_total_value(chunk):
    """the printed county total of a spill table: the integers on the first
    '... - Total' row ('Kalkaska County Michigan - Total', 'County - Total')"""
    for row in chunk:
        if row and re.search(r'-\s*Total\s*$', one_space(row[0]), re.I):
            ints = [int(c.replace(',', '')) for c in row[1:] if INT.match(c)]
            if ints:
                return max(ints)
    return None


def row_values(cells, cands, key, label, problems, blank_consumes=False):
    """([votes per candidate], write-in sum) by walking the cells after the
    right precinct label: percent cells are skipped, each integer or
    glued 'votes percent' cell yields one vote count. With blank_consumes
    (Charlevoix's phantom header columns), an empty cell consumes a
    candidate slot without contributing a value."""
    vals = [None] * len(cands)
    writein = 0
    ci = 0
    for cell in cells:
        cell = one_space(cell)
        if not cell:
            if blank_consumes and ci < len(cands):
                ci += 1
            continue
        if PCT.match(cell):
            continue
        m = GLUED.match(cell)
        num = m.group(1) if m else (cell if INT.match(cell) else None)
        if num is None:
            if key is not None:
                problems.append(f'{key}: {label!r}: cell {cell!r} is not '
                                f'a number')
            continue
        v = int(num.replace(',', ''))
        if ci < len(cands):
            if cands[ci] == WRITEIN_TOKEN:
                writein += v
            elif cands[ci] != SKIP_TOKEN:
                vals[ci] = v
        # integers beyond the candidate count are Total Votes columns
        ci += 1
    return vals, writein


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('county')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--dump', action='store_true')
    args = ap.parse_args()
    county = args.county

    problems = []
    notes = []
    rows = []
    write_ins = collections.defaultdict(int)
    open_contest = None
    pending_key = None
    block = None            # [label, values, writein] of a wrapped-label
                            # precinct row still absorbing its tail rows
    orphans = []            # (pos, chunk, pending key) — headerless tables
                            # buffered for the next headed results table of
                            # the same contest (Montmorency/Charlevoix
                            # 'orphans' option)
    used_spills = set()     # SPILL_TABLES entries already consumed
    carry = collections.defaultdict(collections.Counter)   # key -> candidate
                            # votes already emitted from earlier instances of
                            # the same contest (the source splits contests
                            # across page groups, each with its own tables)

    def finish(contest):
        if contest is None or contest.finished:
            return
        contest.finished = True
        if contest.key is None or not contest.cands:
            return
        if not contest.rows:
            problems.append(f'{contest.key}: contest has no precinct rows')
            return
        sums = collections.Counter()
        for _, values, w in contest.rows:
            for name, v in zip(contest.cands, values):
                if v is not None and name not in (WRITEIN_TOKEN, SKIP_TOKEN):
                    sums[name] += v
            sums[WRITEIN_TOKEN] += w
        seen = {}
        for total in contest.totals:
            for name, v in total.items():
                if name in seen and seen[name] != v:
                    problems.append(f'{contest.key}: printed Total rows '
                                    f'disagree on {name}: {seen[name]} vs {v}')
                seen[name] = v
        # the source splits a contest across page groups; the last group's
        # printed County - Total covers every group's tables, so compare it
        # against this instance's sums plus what earlier instances emitted
        prev = carry[contest.key]
        for i, name in enumerate(contest.cands):
            if name in (WRITEIN_TOKEN, SKIP_TOKEN):
                continue
            if name in seen:
                want, got = seen[name], sums[name] + prev.get(name, 0)
                if got != want:
                    if prev.get(name):
                        problems.append(f'{contest.key}: {name} precinct sum '
                                        f'{sums[name]} + earlier tables '
                                        f'{prev[name]} != printed Total {want}')
                    else:
                        problems.append(f'{contest.key}: {name} precinct sum '
                                        f'{sums[name]} != printed Total {want}')
        for name, v in sums.items():
            if name != WRITEIN_TOKEN:
                carry[contest.key][name] += v
        key = contest.key
        if args.dump:
            for label, values, w in contest.rows:
                print('ROW', key, label, values, w)
            print('TOT', key, contest.totals)
        for label, values, w in contest.rows:
            for name, v in zip(contest.cands, values):
                if v is not None and name not in (WRITEIN_TOKEN, SKIP_TOKEN):
                    rows.append((label, key[0], key[1], key[2], name, v))
            if w:
                write_ins[key] += w

    def close_contest():
        """finish the open contest, reporting any unabsorbed wrapped row"""
        nonlocal open_contest, block
        if block is not None and open_contest is not None \
                and open_contest.key is not None:
            problems.append(f'{open_contest.key}: unresolved wrapped row '
                            f'{block[0]!r}')
        block = None
        finish(open_contest)
        open_contest = None

    for pos, (kind, payload) in enumerate(parse_pages(COUNTIES[county])):
        if kind == 'text':
            text = one_space(payload)
            text = re.sub(r'\s+(?:DEM|REP)\s*$', '', text)
            key = classify(text)
            if key or re.search(r'\((DEM|REP)\)', text):
                # a contest heading (local ones classify to None)
                close_contest()
                pending_key = key
            continue
        chunk = payload
        if not chunk:
            continue
        blank_flag = False
        htext = one_space(chunk[0][0]) if chunk[0] else ''
        hk = heading_key(htext)
        if hk is not False:
            # the OCR fused the contest heading into the table's first
            # header cell: close the page group's previous contest and
            # queue this heading for the tables that follow
            close_contest()
            pending_key = hk
        head = header_cells(chunk[0], county)
        hdr_off = 1
        if head is None:
            fused = FUSED_HEADERS.get(county)
            if fused and chunk[0] and chunk[0][0] in fused:
                # the OCR fused the header cells into one; split them back
                chunk[0] = fused[chunk[0][0]]
                head = header_cells(chunk[0], county)
            elif len(chunk[0]) == 1 and chunk[0][0] == 'Precinct':
                # a spill table whose candidate header the OCR dropped
                # entirely, leaving a lone 'Precinct' cell; identify it by
                # its printed county total (a named 'Total Votes' /
                # 'Unresolved Write-In' spill table keeps its header here
                # and is handled by the headless path instead)
                spill = SPILL_TABLES.get(county)
                if spill:
                    total = county_total_value(chunk)
                    cells = spill.get(total)
                    if cells and (total, tuple(map(tuple, cells))) \
                            not in used_spills:
                        used_spills.add((total, tuple(map(tuple, cells))))
                        notes.append(f'{county}: spill table (county total '
                                     f'{total}) given header {cells}')
                        chunk = [cells] + chunk
                        head = header_cells(cells, county)
        if head is None and len(chunk) > 1 \
                and not any(one_space(c) for c in chunk[0]) \
                and header_cells(chunk[1], county) is not None:
            # the OCR printed the header on the table's second row, leaving
            # an all-empty first row (Montmorency's HD 106 DEM table)
            head = header_cells(chunk[1], county)
            hdr_off = 2
        if head is not None:
            kind, cands = head
            if kind == 'turnout':
                continue   # a turnout-only table; keep the pending heading
            if not any(re.search(r'\d', one_space(r[0]))
                       for r in chunk[hdr_off:] if r and r[0]):
                # no precinct row at all: a Cumulative-only furniture table
                # (the all-zero page-group leftovers, Otsego), which would
                # otherwise open an empty contest and pollute the totals
                continue
            # a phantom empty column is real only when the header carries it
            # (Charlevoix [154]); blank data cells under a blank-free header
            # are colspan padding (Hindle/Ranville's Wilson row) and must be
            # skipped, not consumed as candidate slots
            blank_flag = OPTS.get(county, {}).get('blank_cell_consumes', False) \
                and any(not one_space(c) for c in chunk[hdr_off - 1])
            cands = [NAME_FIX.get(county, {}).get(c, c) for c in cands]
            if OPTS.get(county, {}).get('header_fixes'):
                # repair split header cells before matching candidates
                # against the CENR (a wrapped name defeats the match)
                cands = fix_header_toks(county, cands)
            key = pending_key
            pending_key = None
            if key is None:
                names = {c for c in cands if c not in (WRITEIN_TOKEN,
                                                       SKIP_TOKEN)}
                contests = {}
                for (o, d, p, c) in CENR[county]:
                    contests.setdefault((o, d, p), {})[c] = \
                        CENR[county][(o, d, p, c)]
                # OCR drops candidate header cells wholesale (Antrim's
                # Governor (REP) prints in two column-halves), so match on
                # the printed names alone; several hits mean ambiguity
                hits = {k for k, kv in contests.items() if names <= set(kv)}
                if len(hits) == 1:
                    key = hits.pop()
                    notes.append(f'{county}: heading lost; contest inferred '
                                 f'from header candidates: {key}')
            ovr = HEADER_OVERRIDE.get(county, {}).get(key, {}) \
                .get(tuple(cands)) if key else None
            if ovr is not None:
                cands = list(ovr)
            repeat = (open_contest is not None and key is not None
                      and open_contest.key == key
                      and open_contest.cands == cands)
            if not repeat:
                # a repeat header means more rows of the open table; a
                # same-contest table with different columns (the source
                # splits candidates across pages) starts a second contest
                close_contest()
                contest = Contest(key)
                contest.cands = cands
                if key is not None:
                    cenr_cands = {k[3] for k in CENR[county] if k[:3] == key}
                    for name in set(cands):
                        if name in (WRITEIN_TOKEN, SKIP_TOKEN):
                            continue
                        if name not in cenr_cands:
                            problems.append(f'{key}: header candidate '
                                            f'{name!r} is not a CENR '
                                            f'candidate')
                open_contest = contest
            data = chunk[hdr_off:]
            if OPTS.get(county, {}).get('orphans') and orphans:
                # headerless tables buffered earlier: flush any whose
                # precinct labels are disjoint from this table's into it
                # (a contest's page-1 tables print before their header
                # survives OCR, or arrive before the pending heading)
                new_labels = {one_space(r[0].replace('\\n', ' ')) for r in data
                              if r and re.search(r'\d', r[0])}
                keep = []
                strict_key = OPTS.get(county, {}).get('strict_orphan_key')
                row0_data = OPTS.get(county, {}).get('orphan_row0_data')
                for opos, ochunk, otag in orphans:
                    olabels, ok = set(), True
                    for orow in ochunk[0 if row0_data else 1:]:
                        if not orow:
                            continue
                        lab = one_space(orow[0].replace('\\n', ' '))
                        if re.search(r'\d', lab) and re.search(r'[A-Za-z]', lab):
                            olabels.add(lab)
                        if len(orow) >= 2:
                            ci = 0
                            for cell in split_row(orow)[1]:
                                cell = one_space(cell)
                                if not cell or PCT.match(cell):
                                    continue
                                if INT.match(cell):
                                    if ci >= len(cands):
                                        ok = False
                                        break
                                    ci += 1
                    # a contest's page-group tables sit a few stream items
                    # apart (turnout table + page marker between), so only a
                    # nearby headed table of the same contest may absorb the
                    # orphan — and its first page group holds more precincts
                    # than the second
                    if ok and olabels and pos - opos <= 8 \
                            and (otag == key if strict_key
                                 else otag in (None, key)) \
                            and not (olabels & new_labels) \
                            and len(olabels) >= len(new_labels):
                        notes.append(f'{county}: flushed {len(ochunk) - 1} '
                                     f'orphan rows into {key}')
                        data = ochunk[0 if row0_data else 1:] + data
                    else:
                        keep.append((opos, ochunk, otag))
                orphans = keep
        else:
            lead = one_space(chunk[0][0])
            if OPTS.get(county, {}).get('orphans'):
                flat = one_space(' '.join(c for r in chunk for c in r))
                has_results = 'Total Votes' in flat or WRITEIN_COL.search(flat)
                if not has_results and \
                        any(is_turnout_cell(c) for r in chunk for c in r):
                    continue   # a turnout table whose header OCR mangled away
                if not has_results and turnout_shape(chunk):
                    continue   # same, recognizable from its row shape alone
                if header_remnant(chunk[0]):
                    orphans.append((pos, chunk, pending_key))
                    continue   # for the next headed table
            if not lead or lead in ('Candidate', 'Yes', 'No') \
                    or open_contest is None:
                # a countywide summary table, or a table with no open contest
                if OPTS.get(county, {}).get('orphans') and open_contest is None \
                        and lead and lead not in ('Candidate', 'Yes', 'No'):
                    orphans.append((pos, chunk, pending_key))
                    notes.append(f'{county}: buffered headless table with no '
                                 f'open contest (first cell {lead!r})')
                continue
            data = chunk
        fused = FUSED_ROWS.get(county)
        if fused:
            # the OCR fused consecutive precinct rows into single cells;
            # expand them back (entries verified against the sibling
            # column-group table's printed Total Votes)
            expanded = []
            for row in data:
                expanded.extend(fused.get(one_space(' '.join(row)), [row]))
            data = expanded
        for row in data:
            if len(row) == 1 and OPTS.get(county, {}).get('method_rows'):
                # a lone precinct-label cell (a wrapped or OCR-split label):
                # grow the open label-only block, or start a new one
                lab = one_space(row[0].replace('\\n', ' '))
                if not lab or INT.match(lab) or not re.search(r'\d', lab) \
                        or not re.search(r'[A-Za-z]', lab):
                    continue
                if block is not None and \
                        not any(v is not None for v in block[1]) and not block[2]:
                    block[0] = one_space(f'{block[0]} {lab}')
                else:
                    if block is not None and re.search(r'\d', block[0]):
                        open_contest.rows.append(tuple(block))
                    block = [fix_label(lab),
                             [None] * len(open_contest.cands), 0]
                continue
            if len(row) < 2:
                continue
            rlabel, cells = split_row(row)
            if not rlabel:
                continue   # no label cell found (all-numeric misparse)
            label = one_space(row[0].replace('\\n', ' '))
            if re.match(r'Cumulative', rlabel, re.I) or \
                    re.match(r'Cumulative', label, re.I):
                block = None
                continue   # source furniture (always zero)
            values, w = row_values(cells, open_contest.cands, open_contest.key,
                                   rlabel, problems, blank_consumes=blank_flag)
            has_values = any(v is not None for v in values) or w
            digitless = not re.search(r'\d', rlabel)
            method = OPTS.get(county, {}).get('method_rows')
            if method and has_values and re.fullmatch(r'Total', rlabel, re.I) \
                    and block is not None:
                # a per-precinct Total check row: it must equal the sum of
                # the block's Election Day / AV sub-rows (muted for local
                # contests, whose rows are discarded anyway)
                if open_contest.key is not None:
                    for i, name in enumerate(open_contest.cands):
                        if name in (WRITEIN_TOKEN, SKIP_TOKEN):
                            continue
                        if (block[1][i] or 0) != (values[i] or 0):
                            problems.append(
                                f'{open_contest.key}: {block[0]!r}: printed '
                                f'Total {values[i]} != row sum {block[1][i]}')
                    if block[2] != w:
                        problems.append(f'{open_contest.key}: {block[0]!r}: '
                                        f'printed Total write-in {w} != '
                                        f'row sum {block[2]}')
                if re.search(r'\d', block[0]):
                    open_contest.rows.append(tuple(block))
                block = None
                continue
            if method and block is not None and has_values \
                    and METHOD_RE.search(rlabel):
                # an Election Day / AV counting-board sub-row of the open
                # precinct: sum it into the block
                block[1] = [None if a is None and b is None
                            else (a or 0) + (b or 0)
                            for a, b in zip(block[1], values)]
                block[2] += w
                continue
            if method and block is not None and not has_values \
                    and any(v is not None for v in block[1]) \
                    and re.search(r'\d', rlabel):
                # a fresh precinct label row: flush the completed block
                open_contest.rows.append(tuple(block))
                block = [fix_label(rlabel), values, w]
                continue
            if block is None:
                if INT.match(rlabel):
                    continue   # an all-numeric row: no label cell at all
                if digitless and not has_values:
                    continue   # a furniture row (County / Michigan / Precinct)
                block = [fix_label(rlabel), values, w]
                if method and not has_values:
                    # a precinct label row in method mode: wait for its
                    # Election Day / AV sub-rows rather than appending a
                    # zero-valued row
                    continue
            elif not has_values:
                # a continuation row of a wrapped label: the block's label
                # grows, its values come from whichever row carries them
                block[0] = one_space(f'{block[0]} {rlabel}')
            else:
                # a continuation row carrying the write-in / Total Votes
                # spill cells: join it, adopting only what the block lacks
                block[0] = one_space(f'{block[0]} {rlabel}')
                if not any(v is not None for v in block[1]) and not block[2]:
                    block[1], block[2] = values, w
                values, w = block[1], block[2]
            if TOTAL_LABEL.search(block[0]):
                # a totals row (its label may split across OCR rows)
                open_contest.totals.append(
                    {n: v for n, v in zip(open_contest.cands, block[1])
                     if v is not None and n not in (WRITEIN_TOKEN, SKIP_TOKEN)})
                if block[2]:
                    open_contest.totals[-1][WRITEIN_TOKEN] = block[2]
                block = None
                continue
            if re.search(r'\d', block[0]):
                open_contest.rows.append(tuple(block))
                block = None
    close_contest()
    for opos, ochunk, _otag in orphans:
        notes.append(f'{county}: dropped {len(ochunk) - 1} unflushed orphan '
                     f'rows (buffered at stream item {opos})')

    for p in problems:
        print('PROBLEM:', p)
    for n in notes:
        print('NOTE:', n)
    print(f'{len(rows)} rows; {len(problems)} parser problems')
    blocking = verify(county, rows, write_ins,
                      allowed=ALLOWED.get(county))
    # OCR can drop whole tables; every CENR contest with votes must be
    # present (an absent contest is a parser/OCR gap, not a source gap)
    emitted = {(r[1], r[2], r[3]) for r in rows}
    for key, kv in CENR[county].items():
        if kv and key[:3] not in emitted:
            blocking.append(f'{county}: CENR contest {key[:3]} absent from '
                            f'parsed rows ({kv} votes)')
    for p in blocking:
        print('PROBLEM:', p)
    if not problems and not blocking:
        if args.write:
            write(county, rows)
        else:
            print('(dry run: pass --write to write the CSV)')


class Contest:
    def __init__(self, key):
        self.key = key
        self.cands = []        # per numeric column: name / <write-in> / <skip>
        self.times_idx = 1
        self.right_idx = 3
        self.rows = []         # (label, [votes per candidate], writein)
        self.totals = []
        self.finished = False


if __name__ == '__main__':
    main()