"""Parse county "Statement of Votes Cast" PDFs with rotated headers (2026 primary).

Contest-major tables across pages, one contest per title page; non-title
continuation pages belong to the previous contest. Column headers are rotated
90 degrees and read bottom-to-top; multi-line names wrap into adjacent
vertical lines, and qualified write-in candidates end in a "Qualified Write
In" line. Wide contests split their candidate columns across continuation
pages. Trailing "County - Total" rows verify the per-precinct sums.

Vendor variants handled:
- Marquette/Iron: left "Times Cast" table (plus a "Registered Voters" column
  in Iron; both ignored) + right candidate table, one row per precinct.
- Alger: a single candidate table whose vote columns are interleaved with
  per-column percentage columns (ignored); no Times Cast table.
- Baraga: per-method rows ("Election Day" / "AV Counting Boards" /
  "Early Voting" / "Total") per precinct; the "Times Cast" methods become the
  Ballots Cast breakdown, and an "Unresolved Write-In" column becomes the
  Write-In row. Output gains election_day / av_counting_boards / early_votes
  breakdown columns (Baraga's 2024 header tokens).
- Mecosta: per-method rows like Baraga, but the table is right-shifted, so
  the label column is cut per line (a label ends before the row's first data
  cell) instead of at a fixed x; continuation pages repeat no rotated
  headers, so a contest's header anchors carry across pages. Local offices
  (delegates, Clerk/Treasurer/Trustee) print one precinct per page under a
  shared title, so a header-bearing page that follows a county-total row
  starts a new contest. Titles carry a bare party token ("Clerk DEM (Vote
  for 1)") and no district numbers.
- Wexford (and Barry/Houghton's cross-county labels): Kent-style side-by-side
  tables (duplicated label column at x~399, aux cells sharing the row top or
  printing on their own line). Each contest spans 4 pages: title page (aux +
  candidate tables), an "Unresolved Write-In" table page, then two pages
  re-printing each table with only Cumulative / Cumulative - Total /
  County - Total rows. Contests with no candidate columns print a Total
  Votes + Unresolved Write-In table instead (all votes are write-ins). Some
  contests spill rows onto extra pages (the county total moves with them).
  Cross-county precinct labels append a standalone "(Manistee County)"-style
  line to a completed label.

Usage:
    .venv/bin/python src/sovc_pdf_parser.py <source.pdf> \
        --county Marquette \
        --out 2026/counties/20260804__mi__primary__marquette__precinct.csv
"""
import argparse
import csv
import re
import sys

import pdfplumber

PARTY_TAG = re.compile(r' \((DEM|REP|LIB|GRN|UST)\)$')
NUMERIC = re.compile(r'^[\d,]+$')
PERCENT = re.compile(r'^[\d.]+%$')
DISTRICT_PATTERNS = [
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$'), 'U.S. House'),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th) District$'), 'State Senate'),
    (re.compile(r'^Representative in State Legislature (\d+)(?:st|nd|rd|th) District$'), 'State House'),
    # Gogebic embeds the district in the office text instead of plain
    # "State Senator Nth District".
    (re.compile(r'^State Senate (\d+)(?:st|nd|rd|th) District$'), 'State Senate'),
    (re.compile(r'^State House (\d+)(?:st|nd|rd|th) District$'), 'State House'),
    # Oceana abbreviates and, for State House, puts the district first.
    (re.compile(r'^Rep in Congress (\d+)(?:st|nd|rd|th) Dist$'), 'U.S. House'),
    (re.compile(r'^State Senator for (\d+)(?:st|nd|rd|th) Dist$'), 'State Senate'),
    (re.compile(r'^(\d+)(?:st|nd|rd|th) Dist Repr? in State Legislature$'), 'State House'),
    # Ingham puts "District" before the plain number.
    (re.compile(r'^Representative in Congress District (\d+)$'), 'U.S. House'),
    (re.compile(r'^State Senator District (\d+)$'), 'State Senate'),
    (re.compile(r'^Representative in State Legislature District (\d+)$'), 'State House'),
]
OFFICE_EXACT = {
    'Governor': 'Governor',
    'Governor for State': 'Governor',
    'United States Senator': 'U.S. Senate',
}
JUDGE_PATTERNS = [
    (re.compile(r'^Judge of District Court (\d+)(?:st|nd|rd|th) District'
                r'(?:, (\d+)(?:st|nd|rd|th) Division)?(?: Non-Incumbent(?: Position)?)?$'),
     'District Court Judge'),
]
WRITE_IN_TAG = re.compile(r'Qualified Write[- ]?In$', re.I)
AUX_HEADERS = ('Times Cast', 'Registered Voters', 'Undervotes', 'Overvotes')
METHOD_LABELS = ('Election Day', 'AV Counting Boards', 'Early Voting', 'Total')
# Breakdown column tokens match Baraga's own 2024 header (COUNTY_COLUMN_MAP
# maps early_votes -> early_voting for the statewide file).
METHOD_COLS = {'Election Day': 'election_day', 'AV Counting Boards': 'av_counting_boards',
               'Early Voting': 'early_votes'}
# Turnout summary pages that must not be mistaken for contests.
SUMMARY_TITLE = re.compile(r'^(?:Official )?Statement of Votes Cast$|^Registered$')
# Method mode: a label-only row is a precinct label only if it names a
# jurisdiction; other fragments are rotated-header remnants (e.g. 'r ( T').
PLAUSIBLE_LABEL = re.compile(r'(Precinct|Township|City|Village|County|Ward|Commission)')
# Local offices printed as "<Office> ... for <Jurisdiction>" with the
# jurisdiction's Township suffix omitted (Cass-style); flipped to
# jurisdiction-first. "Nauganee" is the source's misspelling of Negaunee.
LOCAL_PREFIX = ('Township Clerk', 'Township Treasurer', 'Township Trustee',
                'Township Supervisor', 'Township Community Center Board of Directors')
# Local offices whose title omits the jurisdiction entirely (Mecosta prints
# "Clerk DEM (Vote for 1)" for each township's contest); the contest's single
# precinct supplies it, jurisdiction-first.
BARE_LOCAL_PREFIX = ('Clerk', 'Treasurer', 'Trustee', 'Supervisor')
# District offices whose titles carry no district number; filled per county
# (Mecosta, from the SOS CENR by-county file).
COUNTY_DISTRICTS = {
    'Mecosta': {'Rep in Congress': ('U.S. House', '2'),
                'State Senator': ('State Senate', '34'),
                'Rep in State Legislature': ('State House', '100')},
}


def ordinal(n):
    return f'{n}{"th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")}'


def flip_title(title):
    m = re.search(r' for (.+)$', title)
    if m and title.startswith(LOCAL_PREFIX):
        jur = m.group(1).strip()
        office = title[:m.start()].strip()
        if not re.search(r'(Township|Village|City|County)$', jur):
            jur += ' Township'
        jur = jur.replace('Nauganee', 'Negaunee')
        # "<Jur> Township Township Trustee ..." -> "<Jur> Township Trustee ..."
        if jur.endswith('Township') and office.startswith('Township '):
            office = office[len('Township '):]
        return f'{jur} {office}'
    return title


def map_office(title, county=None):
    title = re.sub(r' \*+ - Insufficient Turnout to Protect Voter Privacy$', '', title)
    title = re.sub(r' \(Vote for \d+\)$', '', title)
    party = ''
    m = PARTY_TAG.search(title)
    if m:
        party = m.group(1)
        title = title[:m.start()]
    if not party:
        # Mecosta-style titles carry a bare party token ("Clerk DEM").
        m = re.search(r' (DEM|REP|LIB|GRN|UST)$', title)
        if m:
            party = m.group(1)
            title = title[:m.start()]
    # "Delegate(s) to County Convention for <jurisdiction>" -> jurisdiction-first.
    m = re.match(r'^Delegates? to County Convention for (.+)$', title)
    if m:
        title = (f'{m.group(1).strip()} Delegate to County Convention'
                 .replace('Nauganee', 'Negaunee').replace('Au Train', 'AuTrain'))
    # "County Commissioner District 5" -> the repo's "Nth District" form.
    m = re.match(r'^County Commissioner (?:District|Dist) (\d+)(.*)$', title)
    if m:
        title = f'County Commissioner {ordinal(int(m.group(1)))} District{m.group(2)}'
    office = OFFICE_EXACT.get(title)
    district = ''
    if office is None:
        fixed = COUNTY_DISTRICTS.get(county, {}).get(title)
        if fixed:
            office, district = fixed
    if office is None:
        for pat, name in JUDGE_PATTERNS:
            m = pat.match(title)
            if m:
                office = name
                district = m.group(1) + (f'-{m.group(2)}' if m.group(2) else '')
                break
        else:
            for pat, name in DISTRICT_PATTERNS:
                m = pat.match(title)
                if m:
                    office, district = name, m.group(1)
                    break
            else:
                office = flip_title(title)
    return office, district, party


def page_lines(page, tol=4):
    """Upright words grouped into visual lines; rotated header words excluded."""
    lines = {}
    for w in page.extract_words(x_tolerance=2):
        if not w['upright']:
            continue  # rotated candidate-name headers
        placed = False
        for t in list(lines):
            if abs(t - w['top']) < tol:
                lines[t].append(w)
                placed = True
                break
        if not placed:
            lines[round(w['top'])] = [w]
    return [sorted(v, key=lambda w: w['x0']) for t, v in sorted(lines.items())]


def rotated_blocks(page):
    """Group rotated chars into header blocks; return [(text, rightmost_x)].

    Each vertical text line of a rotated header has one x; the lines of one
    name are adjacent (gap < 20; "Total Votes" sits 30+pt left of the first
    candidate line), so cluster x values and read each line bottom-to-top.
    rightmost_x is the rightmost vertical line, which sits just left of that
    column's right-aligned data cells.

    Long names wrap mid-word onto the next vertical line ('Christophe' +
    'r Robert'): such a continuation starts lowercase and is welded on
    without a space. 'Total Votes' is always its own column even where
    Ingham prints it <20pt from 'Unresolved Write-In'.
    """
    chars = [c for c in page.chars if abs(c['matrix'][1]) > 0.05]
    xs = sorted({round(c['x0']) for c in chars})
    groups = []
    for x in xs:
        if groups and x - groups[-1][-1] < 20:
            groups[-1].append(x)
        else:
            groups.append([x])
    blocks = []
    for group in groups:
        parts = []
        for x in group:
            cs = sorted([c for c in chars if round(c['x0']) == x], key=lambda c: -c['top'])
            text = re.sub(r'\s+', ' ', ''.join(c['text'] for c in cs)).strip()
            if text:
                parts.append((x, text))
        if parts and parts[0][1] == 'Total Votes' and len(parts) > 1:
            blocks.append(('Total Votes', parts[0][0]))
            parts = parts[1:]
        text = ''
        for _, part in parts:
            if text and text[-1].isalpha() and part[:1].islower():
                text += part  # mid-word wrap continuation
            else:
                text += ' ' + part
        blocks.append((text.strip(), group[-1]))
    return [(re.sub(r'(?<=[A-Za-z])- (?=[A-Za-z])', '-', t), x) for t, x in blocks]


def data_rows(page, carried=None):
    """Return (main, aux, rows, fresh) for the page's tables.

    main/aux are [(header text, anchor x)] lists. Data columns are anchored
    on the rotated header blocks: each column's cells are right-aligned
    within ~10pt of the block's rightmost vertical line, so anchors work
    across vendors (Marquette's 557/647/737, Alger's 286/376/466, Baraga's
    write-in pages at 178/268). Aux blocks ('Times Cast', 'Registered
    Voters') get separate aux columns that the Contest turns into the
    Ballots Cast breakdown. Percentage columns are ignored. Every data line
    (including label-only continuations) becomes a row
    [label, main_cells, aux_cells]; the Contest merges continuations.

    fresh is True when the page carries its own rotated headers. When it
    doesn't and the caller carried a contest's anchors (Mecosta's
    continuation pages repeat no headers), the carried anchors are reused.
    """
    lines = page_lines(page)
    blocks = rotated_blocks(page)
    fresh = bool(blocks)
    if fresh:
        main, aux = [], []
        for text, x in blocks:
            (aux if text in AUX_HEADERS else main).append((text, x))
    else:
        main, aux = carried if carried else ([], [])
    def match(anchors, x1, tol=24):
        for i, (_, ax) in enumerate(anchors):
            if abs(x1 - ax) <= tol:
                return i
        return None

    # Data zone starts at the 'Precinct ...' header line (Marquette/Iron:
    # 'Precinct County <County> County Michigan' — no digits, so wrapped
    # "Precinct 1" label continuations never match). Vendors without that
    # header line (Alger splits 'Precinct' into 'P recinct') start at the
    # first line that actually holds a column cell. Continuation pages that
    # reuse carried anchors (Mecosta) start at the page top: the first data
    # line can be a precinct label with no cells.
    header_i = next((i for i, line in enumerate(lines)
                     if line[0]['text'] == 'Precinct'
                     and not any(NUMERIC.match(w['text']) for w in line)), None)
    cell_i = None
    if header_i is None:
        cell_i = next((i for i, line in enumerate(lines)
                       if any(NUMERIC.match(w['text'])
                              and match(main, w['x1']) is not None for w in line)), None)
    if header_i is not None:
        start = header_i + 1
    elif cell_i is not None and not fresh and carried:
        start = 0
    else:
        start = cell_i or 0
    data_lines = lines[start:]

    def label_of(right, left, cut=None):
        if right:
            return ' '.join(w['text'] for w in right)
        words = [w['text'] for w in left]
        # Some vendors print the row label twice side by side (Alger's copies
        # start at x0 = 20 and 128); drop the dupe before any x-based filter,
        # since the second copy's first word can sit inside it.
        for half in range(len(words) // 2, 0, -1):
            if len(words) % 2 == 0 and ' '.join(words[:half]) == ' '.join(words[half:]):
                return ' '.join(words[:half])
        if cut is not None:
            # The label column ends before the row's first data cell; some
            # vendors (Mecosta) shift the whole table rightward, so a fixed
            # x cutoff would mangle every label.
            kept = [w for w in left if w['x1'] < cut] or left
        else:
            kept = [w for w in left if w['x0'] < 135] or left
        return ' '.join(w['text'] for w in kept)

    rows = []  # [label, {main col: votes}, {aux col: votes}] for all data lines
    built = []  # [label, cells, aux_cells, cut, left words]
    for line in data_lines:
        cells, aux_cells, right, left = {}, {}, [], []
        # The label column ends at the row's first data cell — but only the
        # aux cells (Times Cast / Registered Voters) mark it: Oceana prints
        # two tables side by side per page, and the main cells' left edge
        # would keep the right table's duplicate label words. Lines matched
        # on main cells only fall back to the fixed cutoff in label_of.
        cut = None
        for w in line:
            if PERCENT.match(w['text']) or w['text'] == '****':
                continue  # percentage columns; insufficient-turnout masks
                continue
            if NUMERIC.match(w['text']):
                i = match(main, w['x1'])
                if i is not None:
                    cells[i] = int(w['text'].replace(',', ''))
                    continue
                i = match(aux, w['x1'])
                if i is not None:
                    aux_cells[i] = int(w['text'].replace(',', ''))
                    cut = w['x0'] if cut is None else min(cut, w['x0'])
                    continue
                # Some vendors shift a table rightward, leaving cell edges
                # >24pt from the header line (Ingham's small contests). Only
                # words right of the leftmost main anchor qualify — a wrapped
                # label's digit sits left of every column and must stay a
                # label word.
                if main and w['x1'] > main[0][1]:
                    i = match(main, w['x1'], 34)
                    if i is not None:
                        cells[i] = int(w['text'].replace(',', ''))
                        continue
                    i = match(aux, w['x1'], 34)
                    if i is not None:
                        aux_cells[i] = int(w['text'].replace(',', ''))
                        cut = w['x0'] if cut is None else min(cut, w['x0'])
                        continue
            if w['x0'] > 390:
                right.append(w)
            else:
                left.append(w)
        built.append([label_of(right, left, cut), cells, aux_cells, cut, left])
    # A label-only line (no matched cells, cut None) borrows the label-column
    # boundary of the next cell line: a long precinct label's trailing words
    # sit right of the fixed fallback cutoff (Mecosta's right-shifted tables)
    # and would otherwise be dropped.
    next_cut = None
    for row in reversed(built):
        if row[1] or row[2]:
            next_cut = row[3]
        elif row[3] is None and next_cut is not None:
            row[0] = label_of([], row[4], next_cut)
    return main, aux, [[r[0], r[1], r[2]] for r in built], fresh


class Contest:
    def __init__(self, county, title):
        self.county = county
        self.title = title
        self.method_mode = False
        self.votes = {}  # precinct mode: precinct -> {column name: votes}
        # method mode: precinct -> {candidate -> {method: votes}}
        self.method_votes = {}
        # 'Cumulative - Total' adjustments folded into the printed county
        # total but not attributable to a precinct; subtracted in checks.
        self.county_cumulative = {}
        self.aux_votes = {}  # method mode: precinct -> {aux name: {method: votes}}
        self.county_totals = {}  # column name -> printed county total
        self.county_aux = {}  # method mode: aux name -> printed county total
        # Method mode: set once a county row passes; zero-filled Cumulative
        # method rows after it are re-prints (possibly on the next page).
        self.after_county = False
        # Method mode: current precinct label, carried across pages.
        self.precinct = None
        # [(header text, anchor x)] for the contest's main and aux columns,
        # carried across pages whose tables repeat no rotated headers
        # (Mecosta's continuation pages).
        self.anchors = None

    @staticmethod
    def _merge_continuations(county, rows, method_mode):
        """Attach label-only continuation lines to the preceding row.

        Method mode: only the county row's wrapped "Total" merges — any
        other label-only line starts a new precinct block.
        Precinct mode: vendors print the turnout table (Times Cast /
        Registered Voters) and the candidate table as separate line sets
        (Oceana), so one precinct can span several same-label rows, and
        long labels wrap mid-phrase onto the next line ('Claybanks
        Township, Precinct' + '1', 'Oceana County Michigan -' + 'Total').
        Build blocks: an incomplete label opens a pending block; wrap
        continuations ('Precinct N', a bare digit, the county row's
        'Total') complete it; complete labels stand alone (same-label
        rows re-merge later by precinct key).
        """
        if method_mode:
            # Per-method rows ('Election Day' / 'AV Counting Boards' / ...).
            # Ingham wraps long precinct labels onto the next line, and the
            # wrap fragment carries the Registered Voters cell.
            wrap = re.compile(r'^(Precinct )?\d+$')
            merged = []
            for label, cells, aux_cells in rows:
                if merged and not cells:
                    if aux_cells and not label:
                        merged[-1][2].update(aux_cells)  # aux-only continuation
                        continue
                    if label == 'Total' and 'County' in merged[-1][0]:
                        merged[-1][0] += ' ' + label  # county row's wrapped '- Total'
                        continue
                    if (merged[-1][0].endswith((',', 'Precinct', 'Township'))
                            and wrap.match(label)):
                        merged[-1][0] += ' ' + label
                        merged[-1][2].update(aux_cells)
                        continue
                merged.append([label, cells, aux_cells])
            return merged
        wrap = re.compile(r'^(Precinct )?\d+$')
        merged = []
        pending = None

        def flush():
            nonlocal pending
            if pending is not None:
                merged.append(pending)
                pending = None

        for label, cells, aux_cells in rows:
            if not cells and not aux_cells:
                if (label.startswith('Cumulative')
                        or label in ('County', 'Michigan')
                        or re.match(r'^\w+ County$', label)):
                    continue  # header remnants / summary labels
                if pending is not None:
                    if not pending[0].endswith(label):
                        pending[0] += ' ' + label
                elif (merged and label.strip(' -') == 'Total'
                        and 'County' in merged[-1][0]
                        and not merged[-1][0].endswith('Total')):
                    merged[-1][0] += ' ' + label  # county row's wrapped '- Total'
                elif (merged and merged[-1][0].count('(') > merged[-1][0].count(')')
                        and label.endswith(')')
                        and not merged[-1][0].endswith(label)):
                    # Kent appends the closing county to a cross-county
                    # precinct label ('Boston Township 1 (Ionia' + 'County)')
                    # on its own line.
                    merged[-1][0] += ' ' + label
                elif (merged and wrap.match(label)
                        and not merged[-1][0].endswith(label)):
                    merged[-1][0] += ' ' + label  # label wrap on its own line
                elif (merged and re.fullmatch(r'\([A-Za-z ]+ County\)', label)
                        and merged[-1][1]
                        and not merged[-1][0].endswith(')')
                        and re.search(r'\d$', merged[-1][0])):
                    # Wexford appends a whole-county suffix to a completed
                    # cross-county precinct label ('Cleon Township,
                    # Precinct 1' + '(Manistee County)'); the unbalanced-paren
                    # branch above only covers labels split mid-paren.
                    merged[-1][0] += ' ' + label
                elif label:
                    flush()
                    pending = [label, {}, {}]
                continue
            if pending is not None:
                if not label and not cells:
                    # Turnout-table line of the pending block; keep the
                    # block open for its candidate-table line.
                    for i, v in aux_cells.items():
                        pending[2].setdefault(i, v)
                    continue
                if wrap.match(label) or (label == 'Total'
                                         and 'County' in pending[0]):
                    pending[0] += ' ' + label
                    for i, v in cells.items():
                        pending[1].setdefault(i, v)
                    for i, v in aux_cells.items():
                        pending[2].setdefault(i, v)
                    flush()
                    continue
                if not cells:
                    # Turnout-table line. Its label may be another wrap
                    # fragment carrying the turnout cells (Houghton's
                    # 'Precinct 1 (Keweenaw'), the block's complete label
                    # (Oceana's left table), or the next precinct's own
                    # turnout line once this block is complete.
                    if wrap.match(label) or (label.startswith('Precinct ')
                                             and not pending[0].endswith(label)):
                        pending[0] += ' ' + label
                        for i, v in aux_cells.items():
                            pending[2].setdefault(i, v)
                        continue
                    if not pending[1]:
                        pending[0] = label
                        for i, v in aux_cells.items():
                            pending[2].setdefault(i, v)
                        continue
                    flush()
                    merged.append([label, cells, aux_cells])
                    continue
                flush()
                if label.endswith((',', 'Precinct', 'Township', 'Ward', 'Charter')):
                    pending = [label, dict(cells), dict(aux_cells)]
                else:
                    merged.append([label, cells, aux_cells])
                continue
            if not label and aux_cells and merged:
                # Aux-only continuation of the previous block (Marquette's
                # turnout cell sits on its own line).
                merged[-1][2].update(aux_cells)
                continue
            if wrap.match(label):
                # Turnout-table line holding the wrapped final digit of
                # the previous block's label.
                if merged and not merged[-1][0].endswith(label):
                    merged[-1][0] += ' ' + label
                    for i, v in cells.items():
                        merged[-1][1].setdefault(i, v)
                    for i, v in aux_cells.items():
                        merged[-1][2].setdefault(i, v)
                continue
            if label.endswith((',', 'Precinct', 'Township', 'Ward', 'Charter')):
                pending = [label, dict(cells), dict(aux_cells)]
                continue
            merged.append([label, cells, aux_cells])
        flush()
        return merged

    def add_page(self, page, problems):
        main_anchors, aux_anchors, rows, fresh = data_rows(page, self.anchors)
        if fresh:
            self.anchors = (main_anchors, aux_anchors)
        names = [WRITE_IN_TAG.sub('', PARTY_TAG.sub('', h)).strip()
                 for h, _ in main_anchors]
        aux_names = [WRITE_IN_TAG.sub('', PARTY_TAG.sub('', h)).strip()
                     for h, _ in aux_anchors]
        # Detect the page's mode before merging: a method-mode page merged
        # with precinct-mode rules welds the county remnant onto the next
        # precinct label. 'Total' is deliberately excluded — precinct-mode
        # write-in pages also carry bare 'Total' label rows (Oceana).
        self.method_mode = self.method_mode or any(
            cells and label in METHOD_LABELS[:3] for label, cells, _ in rows)
        rows = self._merge_continuations(self.county, rows, self.method_mode)
        if self.method_mode:
            self._add_method_page(names, aux_names, rows, problems)
        else:
            self._add_precinct_page(names, rows, problems)

    def _add_precinct_page(self, names, rows, problems):
        if rows and any(row[1] for row in rows):
            width = max(max(row[1]) + 1 for row in rows if row[1])
            # Trailing headers can legitimately carry no cells (Ingham prints
            # an empty 'Unresolved Write-In' column on some contests).
            if width > len(names):
                problems.append(f'{self.title}: {len(names)} headers {names} for '
                                f'columns {sorted({i for row in rows for i in row[1]})}')
        for label, cells, _ in rows:
            if not cells:
                continue
            if 'Cumulative' in label and 'Total' in label:
                # Post-precinct adjustments folded into the county total
                # (Houghton's final total is precincts + Cumulative 1/1/2).
                for i, v in cells.items():
                    if i < len(names):
                        self.county_cumulative[names[i]] = v
                continue
            if label.startswith('Cumulative'):
                continue
            if 'County' in label and 'Total' in label:
                for i, v in cells.items():
                    if i < len(names):
                        self.county_totals[names[i]] = v
                continue
            votes = self.votes.setdefault(label, {})
            for i, v in cells.items():
                if i >= len(names):
                    problems.append(f'{self.title} / {label}: cell {i} has no header')
                    continue
                votes[names[i]] = v
            missing = [names[i] for i in range(len(names)) if i not in cells]
            if missing:
                problems.append(f'{self.title} / {label}: missing cells {missing}')

    @staticmethod
    def _candidate_total(mv):
        """A candidate's total: the printed 'Total' row, else method sum."""
        total = mv.get('Total')
        return total if total is not None else sum(
            mv.get(m, 0) for m in METHOD_COLS)

    @staticmethod
    def _unresolved(mv):
        """A candidate whose total cannot be read from the report.

        True when the printed 'Total' row is masked and at least one method
        cell is masked too, so the total is not derivable (or the row was
        masked in full). An empty mv means every cell was masked.
        """
        return mv.get('Total') is None and (
            not mv or any(m not in mv for m in METHOD_COLS))

    def _add_method_page(self, names, aux_names, rows, problems):
        # precinct survives page breaks: a label wrap can straddle pages and
        # this contest keeps receiving pages while its title repeats.
        precinct = self.precinct
        for label, cells, aux_cells in rows:
            if aux_cells and not cells and not label and self.county_totals:
                # Aux-only continuation of the county total row.
                for i, v in aux_cells.items():
                    self.county_aux[aux_names[i]] = v
                continue
            if not cells and not aux_cells:
                # Skip header-block remnants ('County' / '<County> County' /
                # 'Michigan') and Cumulative labels; anything else is a
                # precinct label starting a new block.
                if (not label.startswith('Cumulative')
                        and label not in ('County', 'Michigan')
                        and not re.match(r'^\w+ County\b', label)):
                    # Wrapped labels print on two lines; the continuation is
                    # a "Precinct N"/bare "N" fragment following an
                    # incomplete label.
                    if (re.match(r'^(Precinct )?\d+$', label) and precinct
                            and precinct.endswith((',', 'Precinct', 'Township'))):
                        precinct = f'{precinct} {label}'
                    elif PLAUSIBLE_LABEL.search(label):
                        precinct = label
                        self.after_county = False
                    # else: rotated-header remnant (e.g. 'r ( T'); ignore
                continue
            if (not cells and not label.startswith('Cumulative')
                    and label not in ('County', 'Michigan')
                    and not re.match(r'^\w+ County\b', label)
                    and PLAUSIBLE_LABEL.search(label)):
                # Label line carrying only the Registered Voters cell
                # (Ingham's wrap fragments); the precinct's method rows
                # follow.
                precinct = label
                self.after_county = False
                continue
            if label in METHOD_LABELS:
                if self.after_county:
                    continue  # Cumulative block: zero-filled re-print
                if precinct is None:
                    problems.append(f'{self.title}: {label} row with no precinct')
                    continue
                mv = self.method_votes.setdefault(precinct, {})
                av = self.aux_votes.setdefault(precinct, {})
                for i, v in cells.items():
                    mv.setdefault(names[i], {})[label] = v
                for i, v in aux_cells.items():
                    av.setdefault(aux_names[i], {})[label] = v
            elif 'County' in label and not label.startswith('Cumulative'):
                self.after_county = True
                if 'Total' in label or label.endswith('-'):
                    for i, v in cells.items():
                        if i < len(names):
                            # First county row wins: Mecosta's delegate pages
                            # zero qualified write-in columns on the later
                            # 'County - Total' row while the earlier '<County>
                            # Michigan - Total' row reports them correctly.
                            self.county_totals.setdefault(names[i], v)
                    for i, v in aux_cells.items():
                        if i < len(aux_names):
                            self.county_aux.setdefault('__county__', {})[aux_names[i]] = v
            else:
                if not label.startswith('Cumulative'):
                    problems.append(f'{self.title}: unexpected row {label!r} {cells}')
                    precinct = label
                    self.precinct = precinct
                    self.after_county = False

        self.precinct = precinct

    def finish(self, out_rows, problems):
        office, district, party = map_office(self.title, self.county)
        precincts = sorted(self.method_votes if self.method_mode else self.votes)
        if len(precincts) == 1:
            # Mecosta's local offices print one precinct per page under a
            # jurisdiction-less title; the repo convention is
            # jurisdiction-first, and delegate offices carry the full
            # precinct label (Marquette/Baraga precedent).
            label = precincts[0]
            if office == 'Delegate to County Convention':
                office = f'{label} Delegate to County Convention'
            elif office.startswith(BARE_LOCAL_PREFIX + LOCAL_PREFIX):
                jur = re.sub(r', (?:Ward [IVX]+, )?Precinct \d+$', '', label)
                if office.startswith('Township ') and jur.endswith('Township'):
                    office = f'{jur} {office[len("Township "):]}'
                else:
                    office = f'{jur} {office}'
        if self.method_mode:
            self._finish_method(out_rows, problems, office, district, party)
        else:
            self._finish_precinct(out_rows, problems, office, district, party)

    def _finish_precinct(self, out_rows, problems, office, district, party):
        # Iron prints unresolved write-ins in their own column, outside
        # 'Total Votes'; they count as write-in votes and ballots cast.
        cand_names = sorted({n for v in self.votes.values() for n in v}
                            - {'Total Votes', 'Unresolved Write-In'})
        no_total = []
        for precinct in sorted(self.votes):
            votes = self.votes[precinct]
            total = votes.get('Total Votes')
            unresolved = votes.get('Unresolved Write-In', 0)
            cand_sum = sum(votes.get(n, 0) for n in cand_names)
            for n in cand_names:
                if votes.get(n):
                    out_rows.append([self.county, precinct, office, district, party, n,
                                     votes[n]])
            if total is None:
                no_total.append(precinct)
                continue
            wi = total - cand_sum + unresolved
            if wi > 0:
                out_rows.append([self.county, precinct, office, district, party,
                                 'Write-In', wi])
            elif wi < 0:
                problems.append(f'{self.title} / {precinct}: candidate sum {cand_sum} '
                                f'> Total Votes {total}')
            out_rows.append([self.county, precinct, office, district, party,
                             'Ballots Cast', total + unresolved])
        if no_total:
            problems.append(f'{self.title}: precincts with no Total Votes column: '
                            f'{no_total}')
        # Verify per-column sums against the printed County - Total row
        # (final totals can include 'Cumulative' adjustments that no
        # precinct row accounts for).
        for name, expected in sorted(self.county_totals.items()):
            summed = sum(v.get(name, 0) for v in self.votes.values())
            adj = expected - self.county_cumulative.get(name, 0)
            if summed != adj:
                problems.append(f'{self.title} / {name}: precinct sum {summed} != '
                                f'County - Total {expected}')

    def _finish_method(self, out_rows, problems, office, district, party):
        cand_names = sorted({n for mv in self.method_votes.values() for n in mv})
        ballots_total = self.county_aux.get('__county__', {}).get('Times Cast')
        for precinct in sorted(self.method_votes):
            methods = dict(self.aux_votes.get(precinct, {}).get('Times Cast', {}))
            ballots = methods.get('Total')
            if ballots is None and methods:
                ballots = sum(methods.values())  # masked 'Total' row
            if ballots is None:
                problems.append(f'{self.title} / {precinct}: no Times Cast total')
            for n in cand_names:
                mv = self.method_votes[precinct].get(n, {})
                known = {m: v for m, v in mv.items() if m in METHOD_COLS}
                masked = [m for m in METHOD_COLS if m not in known]
                total = mv.get('Total')
                if total is None and masked:
                    # Masked method cells with no printed total: the
                    # candidate's total is unknowable from this report, so
                    # emit blank votes and blanks for the masked methods
                    # (Van Buren's protected-cell precedent); the county
                    # check bounds the missing votes instead.
                    row = [self.county, precinct, office, district, party,
                           'Write-In' if n == 'Unresolved Write-In' else n, None]
                    row += [known.get(m) for m in METHOD_COLS]
                    out_rows.append(row)
                    continue
                # mv also holds the printed 'Total' method row; summing every
                # method would double-count it. 'Total' is the candidate's
                # printed total (the three methods are its breakdown).
                if total is None:
                    total = sum(mv.get(m, 0) for m in METHOD_COLS)
                if not total:
                    continue
                # '****' masks small counts per method row; the missing
                # breakdown is total minus the printed methods. With two or
                # more masked methods the split is not derivable; the
                # candidate total is still printed, so emit blanks for the
                # masked methods.
                if len(masked) == 1:
                    known[masked[0]] = total - sum(known.values())
                    breakdown = known
                elif not masked:
                    breakdown = known
                else:
                    breakdown = known
                row = [self.county, precinct, office, district, party,
                       'Write-In' if n == 'Unresolved Write-In' else n, total]
                row += [breakdown.get(m) for m in METHOD_COLS]
                out_rows.append(row)
            if ballots is not None:
                known = {m: v for m, v in methods.items() if m in METHOD_COLS}
                masked = [m for m in METHOD_COLS if m not in known]
                if len(masked) == 1:
                    known[masked[0]] = ballots - sum(known.values())
                    breakdown = known
                elif not masked:
                    breakdown = known
                else:
                    # Two or more methods masked; ballots is printed, emit blanks.
                    breakdown = known
                row = [self.county, precinct, office, district, party, 'Ballots Cast',
                       ballots]
                row += [breakdown.get(m) for m in METHOD_COLS]
                out_rows.append(row)
        # Verify per-candidate sums and the ballots total (County - Total
        # can include 'Cumulative' adjustments; 'Times Cast' doesn't).
        # Precincts with masked totals leave an unattributable residual in
        # the county total; it must be non-negative and no larger than the
        # masked precincts' combined Times Cast.
        residuals = {}
        for name, expected in sorted(self.county_totals.items()):
            unresolved = [p for p in self.method_votes
                          if self._unresolved(self.method_votes[p].get(name, {}))]
            summed = sum(self._candidate_total(self.method_votes[p].get(name, {}))
                         for p in self.method_votes if p not in unresolved)
            adj = expected - self.county_cumulative.get(name, 0)
            if not unresolved:
                if summed != adj:
                    problems.append(f'{self.title} / {name}: precinct sum {summed} != '
                                    f'County - Total {expected}')
                residuals[name] = 0
                continue
            residual = adj - summed
            residuals[name] = residual
            ballots = 0
            for p in unresolved:
                methods = self.aux_votes.get(p, {}).get('Times Cast', {})
                ballots += methods.get('Total', sum(
                    v for m, v in methods.items() if m in METHOD_COLS))
            if not 0 <= residual <= ballots:
                problems.append(f'{self.title} / {name}: masked precincts '
                                f'{unresolved} leave residual {residual} outside '
                                f'0..{ballots}')
        if 'Total Votes' in residuals and any(residuals.values()):
            others = sum(v for k, v in residuals.items() if k != 'Total Votes')
            if residuals['Total Votes'] != others:
                problems.append(f'{self.title}: Total Votes residual '
                                f'{residuals["Total Votes"]} != sum of candidate '
                                f'residuals {others}')
        if ballots_total is not None:
            summed = 0
            for p in self.method_votes:
                methods = self.aux_votes.get(p, {}).get('Times Cast', {})
                summed += methods.get('Total', sum(
                    v for m, v in methods.items() if m in METHOD_COLS))
            if summed != ballots_total:
                problems.append(f'{self.title} / Times Cast: precinct sum {summed} != '
                                f'County - Total {ballots_total}')


def page_title(page):
    # Titles can wrap ("... Proposal" / "(Vote for 1)" or "... (Vote" /
    # "for 1)"): join zone lines until parentheses balance. Rotated-header
    # fragments ('DEM') only follow lines that already end in ')' — the
    # title itself ends in ')' so the join breaks before reaching them.
    # Calhoun prints the title just below the page header (top ~27).
    parts = []
    for line in page_lines(page, tol=5):
        top = round(line[0]['top'])
        if not 15 < top < 62:
            continue
        text = ' '.join(w['text'] for w in line)
        if text.startswith('Page:'):
            continue
        parts.append(text)
        if text.endswith(')'):
            break
    # A header-less continuation page (Mecosta) starts its data at the page
    # top, so precinct labels and data lines land in the title zone; such a
    # page repeats no rotated headers, and its zone content is data unless
    # it closes with ')'. Title pages repeat the rotated headers, so they
    # keep the join-until-')' behavior (some titles end in an insufficient-
    # turnout suffix instead of ')').
    if parts and not rotated_blocks(page) and not parts[-1].endswith(')'):
        return None
    return ' '.join(parts) if parts else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    out_rows = []
    problems = []
    contest = None
    has_methods = False
    with pdfplumber.open(args.pdf) as pdf:
        for page in pdf.pages:
            title = page_title(page)
            if title is not None and SUMMARY_TITLE.match(title):
                if contest is not None:
                    contest.finish(out_rows, problems)
                    contest = None
                continue  # turnout summary pages
            if title is not None:
                # Calhoun-style vendors repeat the contest title on every
                # page, including totals-only continuation pages; a repeated
                # title continues the current contest. Exception: Mecosta
                # prints one precinct per page for local contests that share
                # a title, each page with its own rotated headers — a
                # header-bearing page after a county-total row starts a new
                # contest (regular multi-page contests carry their headers
                # only on the first page, and totals-only continuations
                # carry no headers).
                if (contest is not None and title == contest.title
                        and contest.method_mode and contest.county_totals
                        and rotated_blocks(page)):
                    contest.finish(out_rows, problems)
                    has_methods = True
                    contest = Contest(args.county, title)
                elif contest is not None and title != contest.title:
                    contest.finish(out_rows, problems)
                    has_methods = has_methods or contest.method_mode
                if contest is None or title != contest.title:
                    contest = Contest(args.county, title)
            elif contest is None:
                continue  # turnout summary pages
            contest.add_page(page, problems)
        if contest is not None:
            contest.finish(out_rows, problems)
            has_methods = has_methods or contest.method_mode

    header = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
    if has_methods:
        header += list(METHOD_COLS.values())
    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for row in out_rows:
            if has_methods and len(row) == 7:  # precinct-mode rows in a mixed file
                row = row + ['', '', '']
            w.writerow(row)
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()