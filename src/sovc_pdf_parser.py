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
    # 2024 sources prefix "State" ("State Representative in State
    # Legislature 99th District").
    (re.compile(r'^State Representative in State Legislature (\d+)(?:st|nd|rd|th) District$'),
     'State House'),
    # Gogebic embeds the district in the office text instead of plain
    # "State Senator Nth District".
    (re.compile(r'^State Senate (\d+)(?:st|nd|rd|th) District$'), 'State Senate'),
    (re.compile(r'^State House (\d+)(?:st|nd|rd|th) District$'), 'State House'),
    # Oceana abbreviates and, for State House, puts the district first.
    # Lenawee 2020 spells "District" out in full.
    (re.compile(r'^Rep in Congress (\d+)(?:st|nd|rd|th) Dist(?:rict)?$'), 'U.S. House'),
    (re.compile(r'^State Senator for (\d+)(?:st|nd|rd|th) Dist(?:rict)?$'), 'State Senate'),
    (re.compile(r'^Rep in State Legislature (\d+)(?:st|nd|rd|th) Dist(?:rict)?$'), 'State House'),
    (re.compile(r'^(\d+)(?:st|nd|rd|th) Dist Repr? in State Legislature$'), 'State House'),
    # Ingham puts "District" before the plain number.
    (re.compile(r'^Representative in Congress District (\d+)$'), 'U.S. House'),
    (re.compile(r'^State Senator District (\d+)$'), 'State Senate'),
    (re.compile(r'^Representative in State Legislature District (\d+)$'), 'State House'),
    # Baraga 2020 omits the 'District' word on the federal/State House
    # titles entirely ('Representative in Congress 1st').
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th)$'), 'U.S. House'),
    (re.compile(r'^Representative in State Legislature (\d+)(?:st|nd|rd|th)$'),
     'State House'),
]
OFFICE_EXACT = {
    'Governor': 'Governor',
    'Governor for State': 'Governor',
    'United States Senator': 'U.S. Senate',
    'United States Senator for State': 'U.S. Senate',
    'US Senator': 'U.S. Senate',
}
# County office titles each county's own general file uses (Oceana 2024
# drops the 'County' prefix; its Prosecuting Attorney keeps it).
COUNTY_OFFICES = {
    'Oceana': {'County Clerk': 'Clerk', 'County Sheriff': 'Sheriff',
               'County Treasurer': 'Treasurer',
               'County Register of Deeds': 'Register of Deeds',
               'County Drain Commissioner': 'Drain Commissioner',
               'County Road Commissioner': 'Road Commissioner',
               'County Surveyor': 'Surveyor',
               'County Pros Attorney': 'County Prosecuting Attorney'},
    # Gladwin 2024's primary titles carry a 'for <County>' tail its general
    # file drops.
    'Gladwin': {'County Clerk for Gladwin County': 'County Clerk',
                'County Sheriff for Gladwin County': 'County Sheriff',
                'County Treasurer for Gladwin County': 'County Treasurer',
                'County Register of Deeds for Gladwin County':
                    'County Register of Deeds',
                'County Drain Commissioner for Gladwin County':
                    'County Drain Commissioner',
                'County Road Commissioner for Gladwin County':
                    'County Road Commissioner',
                'Prosecuting Attorney for Gladwin County':
                    'County Prosecuting Attorney'},
    # Alger 2024's primary titles carry a 'for Alger County' tail its
    # general file drops.
    'Alger': {'County Sheriff for Alger County': 'County Sheriff',
              'County Treasurer for Alger County': 'County Treasurer',
              'County Road Commissioner for Alger County':
                  'County Road Commissioner',
              'County Clerk and Register of Deeds for Alger County':
                  'County Clerk and Register of Deeds',
              'County Prosecuting Attorney for Alger County':
                  'County Prosecuting Attorney'},
}
JUDGE_PATTERNS = [
    (re.compile(r'^Judge of District Court (\d+)(?:st|nd|rd|th) District'
                r'(?:, (\d+)(?:st|nd|rd|th) Division)?(?: Non-Incumbent(?: Position)?)?$'),
     'District Court Judge'),
]
WRITE_IN_TAG = re.compile(r'Qualified Write[- ]?In$', re.I)
PARTY_TOKEN = re.compile(r'(?:DEM|REP|LIB|GRN|UST)')
# Header-remnant lines that sit in the wider title zone on Gladwin 2024's
# title-less continuation pages ('District District' at ~94).
REMNANT_TITLE = re.compile(r'(?:District|Precinct|County|Michigan)'
                           r'(?: (?:District|Precinct|County|Michigan))*')
AUX_HEADERS = ('Times Cast', 'Registered Voters', 'Undervotes', 'Overvotes')
METHOD_LABELS = ('Election Day', 'AV Counting Boards', 'AV Counting Board',
                 'Early Voting', 'Total')
# Breakdown column tokens match Baraga's own 2024 header (COUNTY_COLUMN_MAP
# maps early_votes -> early_voting for the statewide file). Ingham's 2020
# file prints the singular 'AV Counting Board' — normalized to the plural
# when method rows are read.
METHOD_COLS = {'Election Day': 'election_day', 'AV Counting Boards': 'av_counting_boards',
               'Early Voting': 'early_votes'}
# Turnout summary pages that must not be mistaken for contests.
SUMMARY_TITLE = re.compile(r'^(?:Official )?Statement of Votes Cast$|^Registered$')
# Oscoda 2020: the tally line's label fragment carrying the precinct number
# and the row's '- Total' suffix ('1 - Total', '- Total', 'Precinct 1 - Total',
# bare 'Total' after a dash-terminated label).
FRAG_TOTAL = re.compile(r'^(?:(Precinct) )?(?:(\d+) )?(?:- )?Total$')
# Method mode: a label-only row is a precinct label only if it names a
# jurisdiction; other fragments are rotated-header remnants (e.g. 'r ( T').
PLAUSIBLE_LABEL = re.compile(r'(Precinct|Township|City|Village|County|Ward|Commission)')
# Label wrap fragment when the first line ends with 'Ward' (Kent's
# 'East Grand Rapids City, Ward' + '1, Precinct 1').
WARD_FRAG = re.compile(r'^\d+, Precinct \d+$')
# Local offices printed as "<Office> ... for <Jurisdiction>" with the
# jurisdiction's Township suffix omitted (Cass-style); flipped to
# jurisdiction-first. "Nauganee" is the source's misspelling of Negaunee.
LOCAL_PREFIX = ('Township Clerk', 'Township Treasurer', 'Township Trustee',
                'Township Supervisor', 'Township Constable',
                'Township Community Center Board of Directors')
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


def map_office(title, county=None, map_offices=False):
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
        else:
            # Gladwin 2024's write-in-only titles carry a mid-title party
            # tag and county tail ('Township Supervisor for Secord Township
            # (DEM), Gladwin County Michigan').
            m = re.search(r' \((?:DEM|REP|LIB|GRN|UST)\), .+$', title)
            if m:
                title = title[:m.start()]
    # "Delegate(s) to County Convention for <jurisdiction>" -> jurisdiction-first.
    # Baraga 2020 prints 'Delegate to the County Convention for ...'.
    m = re.match(r'^Delegates? to (?:the )?County Convention for (.+)$', title)
    if m:
        title = (f'{m.group(1).strip()} Delegate to County Convention'
                 .replace('Nauganee', 'Negaunee').replace('Au Train', 'AuTrain'))
    # Chippewa 2020's delegate titles ("Precinct Delegate for <jur>").
    m = re.match(r'^Precinct Delegate for (.+)$', title)
    if m:
        title = f'{m.group(1).strip()} Delegate to County Convention'
    title = re.sub(r'\bTwp\.?\b', 'Township', title)
    # "<X>, Precinct N Precinct Delegate" (Marquette 2024) -> the same form;
    # its titles misspell two jurisdictions the precinct labels get right.
    m = re.match(r'^(.+), (Precinct \d+) Precinct Delegate$', title)
    if m:
        title = f'{m.group(1)}, {m.group(2)} Delegate to County Convention'
    title = title.replace('Nauganee', 'Negaunee').replace('Turnin', 'Turin')
    # Chippewa 2020 misspells three delegate jurisdictions (the precinct
    # labels spell them correctly) and one proposal ('Milage').
    title = (title.replace('Rubyard', 'Rudyard')
                  .replace('Drummond Township, Precinct',
                           'Drummond Island Township, Precinct')
                  .replace('Trout Township', 'Trout Lake Township')
                  .replace('Milage', 'Millage'))
    # Baraga 2020: the Senate title carries a garbled 'for State Senate'
    # tail, the DEM delegate title of a single-precinct township omits the
    # precinct (Arvon is the only such jurisdiction), and the source
    # misspells Covington in the turnout labels (the contest titles spell
    # it right) and prints 'LAnse' without the apostrophe in DEM titles.
    title = title.replace('United States Senator for State Senate',
                          'United States Senator')
    if county == 'Baraga':
        title = title.replace('LAnse', "L'Anse")
        m = re.match(r'^(.+) Delegate to County Convention$', title)
        if m and ', Precinct ' not in title:
            title = f'{m.group(1)}, Precinct 1 Delegate to County Convention'
    # Houghton 2020 county-office titles carry a ' for Houghton County'
    # tail, the Commissioner title doubles the office name, and proposal
    # titles end with ', Houghton County Michigan'.
    if county == 'Houghton':
        title = re.sub(r', Houghton County Michigan$', '', title)
        title = re.sub(r' for Houghton County$', '', title)
        m = re.match(r'^County Commissioner for County Commissioner '
                     r'District (\d+)$', title)
        if m:
            title = f'County Commissioner {ordinal(int(m.group(1)))} District'
    # "County Commissioner District 5" -> the repo's "Nth District" form.
    m = re.match(r'^County Commissioner,? (?:for )?(?:District|Dist) (\d+)(.*)$', title)
    if m:
        title = f'County Commissioner {ordinal(int(m.group(1)))} District{m.group(2)}'
    # Baraga 2020 prints the Commissioner ordinal with no 'District' word,
    # and its DEM 3rd-district title is just 'County 3rd District'.
    m = re.match(r'^County Commissioner (\d+)(?:st|nd|rd|th)$', title)
    if m:
        title = f'County Commissioner {ordinal(int(m.group(1)))} District'
    m = re.match(r'^County (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        title = f'County Commissioner {ordinal(int(m.group(1)))} District'
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
    if map_offices and county in COUNTY_OFFICES and office in COUNTY_OFFICES[county]:
        office = COUNTY_OFFICES[county][office]
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
    # Chippewa 2024 writes its rotated headers with a tiny text matrix
    # ((0, 0.02) vs upright (0.02, 0)), so the old abs(m1) > 0.05 scale
    # test missed them; compare the matrix components instead.
    chars = [c for c in page.chars
             if abs(c['matrix'][1]) > abs(c['matrix'][0])]
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


def precinct_header_spans(line):
    """(x0, x1) of every 'Precinct' header word in a line, joining the
    split 'P recinct' form some vendors print (Alger, Leelanau)."""
    spans = []
    i = 0
    while i < len(line):
        w = line[i]
        if w['text'] == 'Precinct':
            spans.append((w['x0'], w['x1']))
            i += 1
        elif w['text'] == 'P' and i + 1 < len(line) \
                and line[i + 1]['text'] == 'recinct':
            spans.append((w['x0'], line[i + 1]['x1']))
            i += 2
        else:
            i += 1
    return spans


def _regroup(words, tol=4):
    """page_lines' grouping applied to a filtered word stream."""
    lines = {}
    for w in words:
        placed = False
        for t in list(lines):
            if abs(t - w['top']) < tol:
                lines[t].append(w)
                placed = True
                break
        if not placed:
            lines[round(w['top'])] = [w]
    return [sorted(v, key=lambda w: w['x0']) for t, v in sorted(lines.items())]


def data_rows(page, carried=None, dup_carried=None):
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
    def match(anchors, x1, tol=24, directional=False):
        for i, (_, ax) in enumerate(anchors):
            if abs(x1 - ax) <= tol and (
                    not directional or x1 >= ax - 10):
                # Data cells right-align at (or within ~10pt of) the header
                # block's rightmost line. A label word further left — Ingham
                # 2020's 2-up millage pages put the label's trailing
                # 'Precinct 8' digit 23pt left of the 'Yes' anchor — is
                # never a cell.
                return i
        return None

    # Some vendors print every table row's label twice, side by side
    # (Leelanau 2020): a pure duplicate label column, not a second table
    # (Ingham's 2-up pages carry different precincts). The copies drift
    # vertically, so grouping them into shared lines garbles labels; when
    # the right copy is self-contained (no aux columns sit between the
    # copies, and the left copy holds no value cells), rebuild the lines
    # from the right copy's words alone.
    dup_cut = None
    from_header = False
    for i, line in enumerate(lines):
        spans = precinct_header_spans(line)
        if len(spans) > 1 and not any(NUMERIC.match(w['text']) for w in line):
            dup_cut = spans[-1][0] - 2
            from_header = True
            break
    if dup_cut is None and dup_carried is not None:
        # A continuation page carries no header line to detect from;
        # reuse the contest's cut.
        dup_cut = dup_carried
    rebuilt = False
    if dup_cut is not None and not aux:
        pairs = 0
        left_cells = False
        for line in lines:
            left = ' '.join(w['text'] for w in line if w['x0'] < dup_cut)
            right = ' '.join(w['text'] for w in line if w['x0'] >= dup_cut)
            if left and left == right:
                pairs += 1
            left_cells = left_cells or any(
                NUMERIC.match(w['text']) and match(main, w['x1']) is not None
                for w in line if w['x0'] < dup_cut)
        if (pairs >= 2 or not from_header) and not left_cells:
            lines = _regroup([w for line in lines
                              for w in line if w['x0'] >= dup_cut])
            rebuilt = True

    # Data zone starts at the 'Precinct ...' header line (Marquette/Iron:
    # 'Precinct County <County> County Michigan' — no digits, so wrapped
    # "Precinct 1" label continuations never match). Vendors without that
    # header line (Alger splits 'Precinct' into 'P recinct') start at the
    # first line that actually holds a column cell. Continuation pages that
    # reuse carried anchors (Mecosta) start at the page top: the first data
    # line can be a precinct label with no cells.
    header_i = next((i for i, line in enumerate(lines)
                     if (line[0]['text'] == 'Precinct'
                         or (line[0]['text'] == 'P' and len(line) > 1
                             and line[1]['text'] == 'recinct'))
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

    # 2-up layouts print the right table's own 'Precinct' header; that
    # column's left edge — not the fixed 390 cutoff — separates the two
    # tables' label words. Ingham 2020's single-precinct local contests sit
    # far enough right ('Election' at x0 379, 'Day' at 417) that 390 splits
    # 'Election Day' between the zones and turns 'Day' into the label.
    right_cut = 390
    if rebuilt:
        # Only the right copy's words remain; keep every label word.
        right_cut = dup_cut
    elif header_i is not None:
        # Exact 'Precinct' words only: the split 'P recinct' form belongs to
        # duplicate-label-column pages (Oceana 2026), where the right copy is
        # a second print of the SAME label column — aux columns sit left of
        # it and the rebuild above never fires — so cutting at it turns the
        # right copy's labels into row labels and garbles the merge.
        pwords = [w for w in lines[header_i] if w['text'] == 'Precinct']
        if len(pwords) > 1:
            right_cut = pwords[-1]['x0'] - 2

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

    def monotone_assign(main, cands):
        """Min-cost order-preserving assignment of candidate cells (x1, value)
        onto the anchor columns; None unless counts match exactly.

        Schoolcraft 2024's cells drift right of their header lines far enough
        that a cell can sit closer to the NEXT column's anchor, so
        first-match-within-tol drops or overwrites it. When a row carries one
        cell per column, the only sensible reading is the order-preserving
        one, and min total |x1 - anchor| resolves the drift.
        """
        if len(cands) != len(main) or len(main) < 2:
            return None
        n = len(cands)
        INF = float('inf')
        # dp[i][j]: min cost assigning the first i candidates to the first j
        # anchors, candidate i-1 on anchor j-1.
        dp = [[INF] * (len(main) + 1) for _ in range(n + 1)]
        dp[0][0] = 0
        for i in range(1, n + 1):
            x1, _ = cands[i - 1]
            for j in range(i, len(main) + 1):
                prev = dp[i - 1][j - 1]
                if prev < INF:
                    dp[i][j] = prev + abs(x1 - main[j - 1][1])
        if dp[n][len(main)] == INF:
            return None
        out, j = {}, len(main)
        for i in range(n, 0, -1):
            out[j - 1] = cands[i - 1][1]
            j -= 1
        return out

    for line in data_lines:
        cells, aux_cells, right, left = {}, {}, [], []
        # The label column ends at the row's first data cell — but only the
        # aux cells (Times Cast / Registered Voters) mark it: Oceana prints
        # two tables side by side per page, and the main cells' left edge
        # would keep the right table's duplicate label words. Lines matched
        # on main cells only fall back to the fixed cutoff in label_of.
        cut = None
        main_cands = []  # (x1, value) for every main-zone numeric word
        for w in line:
            if PERCENT.match(w['text']) or w['text'] == '****':
                continue  # percentage columns; insufficient-turnout masks
            if NUMERIC.match(w['text']):
                i = match(main, w['x1'], directional=True)
                if i is not None:
                    cells[i] = int(w['text'].replace(',', ''))
                    main_cands.append((w['x1'], cells[i]))
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
                        main_cands.append((w['x1'], cells[i]))
                        continue
                    i = match(aux, w['x1'], 34)
                    if i is not None:
                        aux_cells[i] = int(w['text'].replace(',', ''))
                        cut = w['x0'] if cut is None else min(cut, w['x0'])
                        continue
            if w['x0'] > right_cut:
                right.append(w)
            else:
                left.append(w)
        fixed = monotone_assign(main, main_cands)
        if fixed is not None and fixed != cells:
            cells = fixed
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
    return (main, aux, [[r[0], r[1], r[2]] for r in built], fresh,
            dup_cut if rebuilt else None)


class Contest:
    def __init__(self, county, title, map_offices=False):
        self.county = county
        self.title = title
        self.map_offices = map_offices
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
        # Duplicate-label-column mode (Leelanau 2020): the right copy's
        # left edge, carried from the page that detected it.
        self.dup_cut = None
        # Precinct mode, no-'Total Votes' reports (Marquette 2024): the aux
        # Times Cast table per contest provides ballots cast. Recorded like
        # the main cells so _finish_precinct can use them and verify the
        # printed county total.
        self.saw_total_votes = False
        self.aux = {}  # precinct -> {aux column name: votes}
        self.aux_county = {}  # aux column name -> printed county total
        self.aux_cumulative = {}
        # Precinct mode: Gladwin-style ballot-style section ('City' /
        # 'Township') the current rows fall under.
        self.section = None

    @staticmethod
    def _merge_continuations(county, rows, method_mode, no_main=False):
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
                    # Leelanau 2020's narrow label columns wrap precinct
                    # names across three label-only lines ('Bingham' /
                    # 'Township,' / 'Precinct 1'); a bare first word joins
                    # the plausible continuation that follows.
                    if (merged[-1][0] and not merged[-1][1]
                            and not merged[-1][2]
                            # A title-case multi-char word: rotated-header
                            # remnants like Ingham 2026's stray 'R' also sit
                            # label-only ahead of a wrapped precinct label,
                            # and welding them on garbles the precinct.
                            and re.fullmatch(r'[A-Z][a-z]+', merged[-1][0])
                            and merged[-1][0] not in ('County', 'Michigan')
                            and merged[-1][0] not in METHOD_LABELS
                            and not merged[-1][0].startswith('Cumulative')
                            and label.endswith(',')
                            and not label.startswith('Cumulative')
                            and not re.match(r'^\w+ County\b', label)):
                        merged[-1][0] += ' ' + label
                        continue
                    if (merged[-1][0].endswith((',', 'Precinct', 'Township',
                                               'Charter'))
                            and (wrap.match(label)
                                 # Ingham 2020 wraps 'Meridian Charter' +
                                 # 'Township, Precinct 7'.
                                 or re.match(r'^\w+, Precinct \d+$', label))):
                        merged[-1][0] += ' ' + label
                        merged[-1][2].update(aux_cells)
                        continue
                    if (merged[-1][0].endswith('Ward')
                            and WARD_FRAG.match(label)):
                        # Kent wraps after 'Ward' ('East Grand Rapids City,
                        # Ward' + '1, Precinct 1') where Grand Rapids' wider
                        # first line keeps 'Ward 1,' on it.
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
                # Oscoda 2020's 2-up tables split each precinct's label so
                # that the tally line's trailing fragment ('1 - Total',
                # '- Total', 'Precinct 1 - Total') or the bare 'Total' after
                # a dash-terminated label ('Elmer Township, Precinct 1 -')
                # lands on its own line. Weld the fragment onto the open
                # block (else the last merged row) and strip the ' - Total'
                # it carries.
                m = FRAG_TOTAL.match(label)
                if m:
                    target = pending if pending is not None else (
                        merged[-1] if merged else None)
                    if (target is not None and 'Total' not in target[0]
                            and 'County' not in target[0]
                            and 'Michigan' not in target[0]
                            and 'Cumulative' not in target[0]
                            and (m.group(1) or m.group(2)
                                 # a bare 'Total' only welds onto a
                                 # dash-terminated label; '- Total' always does
                                 or (label != 'Total'
                                     or target[0].rstrip().endswith('-')))):
                        add = ''
                        if m.group(1) and not target[0].rstrip().endswith('Precinct'):
                            add += ' Precinct'
                        if m.group(2) and not target[0].rstrip().endswith(m.group(2)):
                            add += ' ' + m.group(2)
                        target[0] = re.sub(r'\s*- Total$', '',
                                           target[0] + add + ' - Total')
                        target[0] = target[0].rstrip(' -')
                        if pending is not None:
                            flush()
                        continue
                if (label.startswith('Cumulative')
                        or label in ('County', 'Michigan')
                        or re.match(r'^\w+ County$', label)):
                    continue  # header remnants / summary labels
                if label in ('City', 'Township', 'Village'):
                    # Gladwin 2024's ballot-style section headers: label-only
                    # lines that must stand alone so _add_precinct_page sees
                    # them (its precinct rows below carry the cells).
                    flush()
                    merged.append([label, {}, {}])
                    continue
                if pending is not None:
                    if not pending[0].endswith(label):
                        # Marquette 2024's single-precinct contests print no
                        # candidate columns at all; the precinct's wrapped
                        # label plus its Times Cast aux cell is already a
                        # complete row, and the county-total label line that
                        # follows must not weld onto it. A '(X County)'
                        # fragment is Wexford's cross-county suffix instead —
                        # it still welds.
                        if (pending[2] and not pending[1] and 'County' in label
                                and not re.fullmatch(r'\([A-Za-z ]+ County\)',
                                                     label)):
                            flush()
                            pending = [label, {}, {}]
                        elif (no_main and pending[2] and not pending[1]
                                and not wrap.match(label)
                                and not any(t in label for t in (
                                    'County', 'Cumulative', 'Total'))):
                            # Candidate-free contest: this line is the next
                            # precinct's own turnout row, not a continuation.
                            flush()
                            pending = [label, {}, {}]
                        else:
                            pending[0] += ' ' + label
                elif (merged and label.strip(' -') in ('Total',
                                                       'Michigan - Total')
                        and 'County' in merged[-1][0]
                        and not merged[-1][0].endswith('Total')):
                    # county row's wrapped '- Total' (Oceana 2026) or
                    # 'Michigan - Total' tail (Oceana 2024)
                    merged[-1][0] += ' ' + label
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
                elif (merged
                        and re.fullmatch(r'(?:Township|Village|City|Charter), '
                                         r'Precinct \d+', label)
                        and not merged[-1][0].endswith(('Total', ')'))
                        and not merged[-1][0][-1:].isdigit()):
                    # Alger 2024 wraps mid-jurisdiction ('Grand Island' +
                    # 'Township, Precinct 1') where the first line already
                    # carries the row's cells; the fragment is a label-only
                    # line.
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
                                         and 'County' in pending[0]) or (
                        pending[0].endswith('Ward')
                        and WARD_FRAG.match(label)):
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
                    # A complete second turnout row (Marquette 2024's
                    # candidate-free contests print one Times Cast row per
                    # precinct) is its own block, not a relabel of the
                    # pending one; only county/summary label lines replace
                    # the pending label.
                    if (not pending[1]
                            and (not pending[2] or any(
                                t in label for t in ('County', 'Cumulative',
                                                     'Total')))):
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
            if wrap.match(label) or (merged
                                     and merged[-1][0].endswith('Ward')
                                     and WARD_FRAG.match(label)):
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
                if (merged and merged[-1][1] and not merged[-1][2]
                        and not merged[-1][0][-1].isdigit()
                        and not merged[-1][0].endswith(')')):
                    # Oceana 2024's deep wrap: the precinct's main cells
                    # print on the label's first line ('Benona'), its Times
                    # Cast/Registered cells on the middle fragment
                    # ('Township,'), and 'Precinct 1' completes the label.
                    merged[-1][0] += ' ' + label
                    merged[-1][2].update(aux_cells)
                    continue
                pending = [label, dict(cells), dict(aux_cells)]
                continue
            merged.append([label, cells, aux_cells])
        flush()
        return merged

    @staticmethod
    def _col_name(h):
        # A candidate's own "Qualified Write In" line is a suffix of the
        # welded header (2026); stripped, it leaves the candidate name. Some
        # vendors (Marquette 2024) print it as its OWN column holding the
        # contest's write-in votes — that header reduces to nothing under
        # the strip, so map it to the merged write-in row name instead.
        if WRITE_IN_TAG.fullmatch(h.strip()):
            return 'Write-In'
        return WRITE_IN_TAG.sub('', PARTY_TAG.sub('', h)).strip()

    def add_page(self, page, problems):
        main_anchors, aux_anchors, rows, fresh, dup_cut = data_rows(
            page, self.anchors, self.dup_cut)
        if fresh:
            self.anchors = (main_anchors, aux_anchors)
        if dup_cut is not None:
            self.dup_cut = dup_cut
        names = [self._col_name(h) for h, _ in main_anchors]
        aux_names = [self._col_name(h) for h, _ in aux_anchors]
        # Detect the page's mode before merging: a method-mode page merged
        # with precinct-mode rules welds the county remnant onto the next
        # precinct label. 'Total' is deliberately excluded — precinct-mode
        # write-in pages also carry bare 'Total' label rows (Oceana).
        self.method_mode = self.method_mode or any(
            cells and label in METHOD_LABELS[:3] for label, cells, _ in rows)
        rows = self._merge_continuations(self.county, rows, self.method_mode,
                                         no_main=not main_anchors)
        if self.method_mode:
            self._add_method_page(names, aux_names, rows, problems)
        else:
            self._add_precinct_page(names, aux_names, rows, problems)

    def _add_precinct_page(self, names, aux_names, rows, problems):
        self.saw_total_votes = self.saw_total_votes or 'Total Votes' in names
        if rows and any(row[1] for row in rows):
            width = max(max(row[1]) + 1 for row in rows if row[1])
            # Trailing headers can legitimately carry no cells (Ingham prints
            # an empty 'Unresolved Write-In' column on some contests).
            if width > len(names):
                problems.append(f'{self.title}: {len(names)} headers {names} for '
                                f'columns {sorted({i for row in rows for i in row[1]})}')
        for label, cells, aux_cells in rows:
            if not cells and not aux_cells:
                # Gladwin 2024 groups precincts under bare section headers
                # ('City' / 'Township'); its City section re-uses the
                # Unincorporated label for an aggregate row covering every
                # non-city ballot.
                if label in ('City', 'Township', 'Village'):
                    self.section = label
                continue
            if 'Cumulative' in label and 'Total' in label:
                # Post-precinct adjustments folded into the county total
                # (Houghton's final total is precincts + Cumulative 1/1/2).
                for i, v in cells.items():
                    if i < len(names):
                        self.county_cumulative[names[i]] = v
                for i, v in aux_cells.items():
                    if i < len(aux_names):
                        self.aux_cumulative[aux_names[i]] = v
                continue
            if label.startswith('Cumulative'):
                continue
            if (('County' in label or 'State' in label or 'Michigan' in label
                    or label.startswith('Precincts'))
                    and 'Total' in label):
                # Crawford 2024's continuation pages label the county total
                # row "State - Total"; Oscoda 2020's summary pages label it
                # "Precincts - Total".
                for i, v in cells.items():
                    if i < len(names):
                        self.county_totals[names[i]] = v
                for i, v in aux_cells.items():
                    if i < len(aux_names):
                        self.aux_county[aux_names[i]] = v
                continue
            if label.endswith(' - Total'):
                # Gladwin 2024 prints every precinct row as '<Jurisdiction>
                # - Total' (with the bare jurisdiction as a label-only line
                # above it); section subtotals are the bare section word
                # ('Township - Total', 'City - Total'). Each section also
                # prints an 'Unincorporated - Total' row aggregating the
                # OTHER section's real precincts (the City section's
                # aggregates the townships, 6,063; the Township section's
                # aggregates the two cities, 183+676=859) — Gladwin has no
                # Unincorporated precinct at all. The section is deliberately
                # NOT reset at a subtotal: a contest spanning several
                # candidate-table pages repeats each section's rows, and the
                # continuation pages carry no bare section header.
                if re.fullmatch(r'(?:Township|City|Village) - Total', label):
                    continue
                if label == 'Unincorporated - Total' and self.section:
                    continue
                label = label[:-len(' - Total')]
            votes = self.votes.setdefault(label, {})
            for i, v in cells.items():
                if i >= len(names):
                    problems.append(f'{self.title} / {label}: cell {i} has no header')
                    continue
                votes[names[i]] = v
            for i, v in aux_cells.items():
                if i < len(aux_names):
                    self.aux.setdefault(label, {}).setdefault(
                        aux_names[i], v)
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
            if label == 'AV Counting Board':   # Ingham 2020's singular form
                label = 'AV Counting Boards'
            else:
                # A drifted duplicate label column (Leelanau 2020's 2-up
                # tables print every row label twice, side by side) can
                # split a method label across lines ('AV Counting' /
                # 'Boards'); a word-prefix of exactly one method name is
                # that method.
                cands = {('AV Counting Boards' if m == 'AV Counting Board'
                          else m)
                         for m in METHOD_LABELS
                         if label and m.startswith(label + ' ')}
                if len(cands) == 1:
                    label = cands.pop()
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
                    # incomplete label (Gogebic's suffixes: "1A", "2B").
                    if (re.match(r'^(Precinct )?\d+[A-Z]?$', label) and precinct
                            and precinct.endswith((',', 'Precinct', 'Township'))):
                        precinct = f'{precinct} {label}'
                    elif (precinct and precinct.endswith('Ward')
                            and WARD_FRAG.match(label)):
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
                    # Baraga 2024's single-precinct contests put the precinct
                    # in the title ("Baraga Township, Precinct 3 Precinct
                    # Delegate") and print no label row.
                    t = re.sub(r' \((?:DEM|REP|LIB|GRN|UST)\)$', '', self.title)
                    t = re.sub(r' \(Vote for \d+\)$', '', t)
                    m = re.match(r'^(.+?, (?:Ward [\dIVX]+, )?Precinct \d+)\b', t)
                    if m:
                        precinct = m.group(1)
                        self.after_county = False
                    else:
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
                if (not label and cells and self.after_county):
                    # Continuation of a wrapped county-total label: Gogebic
                    # 2024 splits "Gogebic County Michigan -" / "Total" and
                    # carries the remaining cells on the tail line.
                    for i, v in cells.items():
                        if i < len(names):
                            self.county_totals.setdefault(names[i], v)
                    for i, v in aux_cells.items():
                        if i < len(aux_names):
                            self.county_aux.setdefault('__county__', {})[aux_names[i]] = v
                    continue
                if not label.startswith('Cumulative'):
                    problems.append(f'{self.title}: unexpected row {label!r} {cells}')
                    precinct = label
                    self.precinct = precinct
                    self.after_county = False

        self.precinct = precinct

    def finish(self, out_rows, problems):
        office, district, party = map_office(self.title, self.county,
                                             self.map_offices)
        precincts = sorted(self.method_votes if self.method_mode else self.votes)
        if len(precincts) == 1:
            # Mecosta's local offices print one precinct per page under a
            # jurisdiction-less title; the repo convention is
            # jurisdiction-first, and a jurisdiction-less delegate office
            # carries the full precinct label (Marquette/Baraga precedent).
            # Titles that already name their jurisdiction stay as printed —
            # Osceola prints 'City of Evart Delegate to County Convention'.
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
        # Wexford 2024 also prints a 'Write-in' column holding ALL write-in
        # votes (qualified write-in candidates + unresolved), which must not
        # be added to the candidates: Total Votes covers the qualified
        # write-in candidates but not the unresolved ones, so
        # wi = total - candidate_sum + unresolved reconstructs the row.
        cand_names = sorted({n for v in self.votes.values() for n in v}
                            - {'Total Votes', 'Unresolved Write-In', 'Write-in'})
        # Marquette 2024 style: no 'Total Votes' column anywhere; the aux
        # Times Cast table is the ballots cast, and any write-in votes sit in
        # a 'Write-In' column already emitted among the candidates.
        no_total_votes = not self.saw_total_votes
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
            if total is None and no_total_votes:
                ballots = self.aux.get(precinct, {}).get('Times Cast')
                if ballots is None:
                    no_total.append(precinct)
                    continue
                out_rows.append([self.county, precinct, office, district, party,
                                 'Ballots Cast', ballots])
                continue
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
        # The no-'Total Votes' reports print their Times Cast county total in
        # the aux table's own County - Total row; verify it too.
        for name, expected in sorted(self.aux_county.items()):
            summed = sum(v.get(name, 0) for v in self.aux.values())
            adj = expected - self.aux_cumulative.get(name, 0)
            if summed != adj:
                problems.append(f'{self.title} / {name}: precinct sum {summed} != '
                                f'County - Total {expected}')

    def _finish_method(self, out_rows, problems, office, district, party):
        cand_names = sorted({n for mv in self.method_votes.values() for n in mv})
        ballots_total = self.county_aux.get('__county__', {}).get('Times Cast')
        # Reports without Times Cast columns (Leelanau 2020) carry no
        # ballots data at all — only complain when the report prints them
        # and a precinct's row went missing. (aux_votes gains an empty
        # dict per precinct as method rows are read.)
        has_aux = any(self.aux_votes.values())
        for precinct in sorted(self.method_votes):
            methods = dict(self.aux_votes.get(precinct, {}).get('Times Cast', {}))
            ballots = methods.get('Total')
            if ballots is None and methods:
                ballots = sum(methods.values())  # masked 'Total' row
            if ballots is None and has_aux:
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
    # Calhoun prints the title just below the page header (top ~27); Alger
    # 2024's proposal pages sit the title at ~71 and delegate titles wrap
    # 'for 1)' to ~75, so a title-less primary zone falls back to the
    # wider 15-100 zone (Baraga-style continuation pages start their data
    # at ~94, and their 'Precinct ...' header lines are skipped anyway).
    lines = page_lines(page, tol=5)

    def zone_lines(lo, hi, incl_hi=False):
        for line in lines:
            top = round(line[0]['top'])
            if lo < top < hi or (incl_hi and top == hi):
                text = ' '.join(w['text'] for w in line)
                if (text.startswith('Page:')
                        or (text.startswith('Precinct') and ')' not in text)
                        or REMNANT_TITLE.fullmatch(text)
                        or re.match(r'^\w+ County$', text)):
                    # 'Precinct ...' lines are Baraga-style table headers —
                    # but Chippewa 2020's delegate titles also START with
                    # 'Precinct' ('Precinct Delegate for ... (Vote for 2)'),
                    # so only paren-less lines are skipped.
                    continue
                yield text

    parts = []

    def collect(texts):
        for text in texts:
            if PARTY_TOKEN.fullmatch(text):
                return False  # truncated title ended; the party line follows
            parts.append(text)
            if text.endswith(')'):
                return False
        return True  # title still open

    # Ingham 2020 prints some titles at exactly top 62 (its 2-up local
    # contests sit lower than the countywide ones), so the primary zone's
    # upper bound is inclusive.
    if collect(zone_lines(15, 62, incl_hi=True)):
        # Only extend into the wider zone while the title is open (a
        # wrapped tail or a title the primary zone missed entirely):
        # lower zone lines on title pages are table data ('Precinct ...'
        # headers at ~94 on Baraga-style pages), never title.
        collect(zone_lines(62, 100))
    # A header-less continuation page (Mecosta) starts its data at the page
    # top, so precinct labels and data lines land in the title zone; such a
    # page repeats no rotated headers, and its zone content is data unless
    # it closes with ')'. Title pages repeat the rotated headers, so they
    # keep the join-until-')' behavior (some titles end in an insufficient-
    # turnout suffix instead of ')').
    if parts and not rotated_blocks(page) and not parts[-1].endswith(')'):
        return None
    return ' '.join(parts) if parts else None


def run(pdf_path, county, out_rows, problems, chunk=300,
        map_offices=False):
    """Parse the whole PDF, keeping only one contest's state at a time.

    Thousand-page SOVCs (Kent 2024: 3495 pages) exhaust memory if every
    parsed page stays cached, so pages are processed in reopenable chunks:
    contest state (the current Contest and has_methods) carries across chunk
    boundaries — a contest straddling a boundary simply continues on the
    chunk's first page — while pdfplumber's page objects are freed.
    """
    contest = None
    has_methods = False
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
    for start in range(0, page_count, chunk):
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[start:start + chunk]:
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
                        contest = Contest(county, title, map_offices=map_offices)
                    elif contest is not None and title != contest.title:
                        contest.finish(out_rows, problems)
                        has_methods = has_methods or contest.method_mode
                    if contest is None or title != contest.title:
                        contest = Contest(county, title, map_offices=map_offices)
                elif contest is None:
                    continue  # turnout summary pages
                contest.add_page(page, problems)
                page.flush_cache()  # don't hold every page's objects in memory
                page.close()
    if contest is not None:
        contest.finish(out_rows, problems)
        has_methods = has_methods or contest.method_mode
    return has_methods


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--map-offices', action='store_true',
                    help='apply COUNTY_OFFICES renames (2024 files: match '
                         'each county\'s own general-file spellings)')
    args = ap.parse_args()

    out_rows = []
    problems = []
    has_methods = run(args.pdf, args.county, out_rows, problems,
                      map_offices=args.map_offices)

    # Baraga 2020's turnout table (and every contest page's label column)
    # misspells Covington as 'Covongton'; the contest titles spell it right.
    if args.county == 'Baraga':
        for row in out_rows:
            row[1] = row[1].replace('Covongton', 'Covington')

    # Houghton 2020 also prints the Baraga Area Schools proposal for
    # Portage Twp P4 — those rows already live in Baraga's committed file
    # (labeled '... (Houghton County)'); drop them here so the statewide
    # merge doesn't double-count the 65 ballots.
    if args.county == 'Houghton':
        out_rows = [r for r in out_rows
                    if not (r[1] == 'Portage Township, Precinct 4'
                            and 'Baraga Area Schools' in r[2])]

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