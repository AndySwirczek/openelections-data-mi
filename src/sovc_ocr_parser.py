"""Parse a county's image-only SOVC PDF via cached PaddleOCR markdown.

Generalized from the Montmorency 2026 parser (src/montmorency_ocr_parser.py,
which has the full layout description): ES&S "Statement of Votes Cast"
contest-major counting-group tables, OCR'd with PaddleOCR-VL
(src/fetch_paddleocr_md.py, per-page markdown cached in /tmp/paddleocr_md).
Each county needs a COUNTY_CONFIG entry (precinct list, cache directory,
page quirks); add MANUAL_TABLES/NAME_FIXES entries as OCR garbles surface.

Convention (from the committed Wexford/Ontonagon 2026 files): emit candidate
rows (alphabetical, zero rows skipped), a Write-In row only when > 0, and
Ballots Cast = Total Votes + Unresolved Write-In unconditionally. Proposals
keep their printed name without "(Vote for N)"; qualified write-in columns
keep their printed name.

Usage:
    .venv/bin/python src/sovc_ocr_parser.py <County> \
        [--cache DIR] --out <csv>
"""
import argparse
import csv
import html
import re
import sys

HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

# Per-county configuration. summary_pages: pages of countywide turnout
# summary read for verification only; pages: total cached page count;
# text_pages: pages whose tables render as plain text lines (single-column
# Unresolved Write-In tables); manual/name_fixes: OCR corrections.
COUNTY_CONFIG = {
    # 2024 primary (100 pages): 7 precincts, contests p2-100.
    'Oscoda': {
        'cache': '/tmp/paddleocr_md/Oscoda_County_August_Election_Results',
        'out': '2024/counties/20240806__mi__primary__oscoda__precinct.csv',
        'summary_pages': 1,
        'pages': 100,
        'precincts': [
            'Big Creek Township, Precinct 1',
            'Big Creek Township, Precinct 2',
            'Clinton Township, Precinct 1',
            'Comins Township, Precinct 1',
            'Elmer Township, Precinct 1',
            'Greenwood Township, Precinct 1',
            'Mentor Township, Precinct 1',
        ],
        'text_pages': [],
        'manual': {},
        'name_fixes': {},
    },
    # 2024 primary (two PDFs = one 217-page report): 3 pages of countywide
    # turnout, then one contest per 3-page spread (aux table, results table,
    # collapsed Unresolved Write-In table), DEM then REP, then proposals.
    'Otsego': {
        'caches': [
            '/tmp/paddleocr_md/Otsego_MI_August_6_2024_Statement_of_Votes_'
            'Cast_Precinct_by_Precinct_part_1',
            '/tmp/paddleocr_md/Otsego_MI_August_6_2024_Statement_of_Votes_'
            'Cast_Precinct_by_Precinct_part_2',
        ],
        'out': '2024/counties/20240806__mi__primary__otsego__precinct.csv',
        'summary_pages': 3,
        'pages': 217,
        'precincts': [
            'City of Gaylord, Ward 1, Precinct 1',
            'City of Gaylord, Ward 2, Precinct 2',
            'City of Gaylord, Ward 3, Precinct 3',
            'Bagley Township, Precinct 1', 'Bagley Township, Precinct 2',
            'Charlton Township, Precinct 1', 'Chester Township, Precinct 1',
            'Corwith Township, Precinct 1', 'Dover Township, Precinct 1',
            'Elmira Township, Precinct 1', 'Hayes Township, Precinct 1',
            'Livingston Township, Precinct 1',
            'Otsego Lake Township, Precinct 1',
        ],
        'text_pages': [],
        # No-table pages are parsed into blocks and classified (aux / WI /
        # results) automatically.
        'auto_text_blocks': True,
        'precinct_aliases': {
            # p017 OCR renders Charlton as Charles.
            'Charles Township, Precinct 1': 'Charlton Township, Precinct 1',
        },
        'name_fixes': {
            # p097 header garble; p093/p095 spell it correctly.
            'Justin Arnash': 'Justin Amash',
        },
        # OCR-destroyed tables, retyped from the page images:
        # p027: the Clerk (DEM) Unresolved Write-In table collapsed into
        #   scrambled text (Livingston 8 / Otsego Lake 27, county 186);
        # p097: US Senate (REP) lost the O'Donnell column on its data rows;
        # p174: Hayes Township Trustee (REP) lost the label column and
        #   scattered cells (Daly 191/119/17, Kilbourn 182/111/16).
        'manual': {
            27: {'tables': {1: [
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['Livingston Township, Precinct 1', '', ''],
                ['Total', '0', '8'],
                ['Otsego Lake Township, Precinct 1', '', ''],
                ['Total', '0', '27'],
                ['Otsego County Michigan - Total', '0', '186'],
            ]}},
            97: {'tables': {1: [
                ['', 'Justin Amash', "Sherry O'Donnell"],
                ['Precinct', '', ''],
                ['Livingston Township, Precinct 1', '', ''],
                ['', 'Election Day', '54', '64'],
                ['', 'AV Counting Boards', '0', '0'],
                ['', 'Early Voting', '7', '1'],
                ['', 'Total', '61', '65'],
                ['Otsego Lake Township, Precinct 1', '', '', ''],
                ['', 'Election Day', '27', '22'],
                ['', 'AV Counting Boards', '23', '38'],
                ['', 'Early Voting', '3', '0'],
                ['', 'Total', '53', '60'],
                ['Otsego County Michigan - Total', '447', '524'],
                ['Cumulative', '', ''],
                ['Cumulative', '', ''],
                ['', 'Election Day', '0', '0'],
                ['', 'AV Counting Boards', '0', '0'],
                ['', 'Early Voting', '0', '0'],
                ['', 'Total', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '447', '524'],
            ]}},
            174: {'tables': {1: [
                ['Precinct', 'Lisa Daly', 'Brad M. Kilbourn', 'Total Votes'],
                ['County', '', '', ''],
                ['Otsego County Michigan', '', '', ''],
                ['Hayes Township, Precinct 1', '', '', ''],
                ['Election Day', '191', '182', '373'],
                ['AV Counting Boards', '119', '111', '230'],
                ['Early Voting', '17', '16', '33'],
                ['Total', '327', '309', '636'],
                ['Otsego County Michigan - Total', '327', '309', '636'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '', '', ''],
                ['Election Day', '0', '0', '0'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '0', '0', '0'],
                ['Total', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '327', '309', '636'],
            ]}},
            # p191/p193: the REP delegate continuation pages' tables
            # collapsed (label column merged into a rowspan cell, every
            # data cell empty); retyped from the page images.
            191: {'tables': {0: [
                ['Precinct', 'Christine Muzyl-Aude', 'Christi Sortor',
                 'Total Votes', 'Unresolved Write-In'],
                ['County', '', '', '', ''],
                ['Otsego County Michigan', '', '', '', ''],
                ['Bagley Township, Precinct 2', '', '', '', ''],
                ['Election Day', '57', '106', '376', '0'],
                ['AV Counting Boards', '77', '123', '402', '0'],
                ['Early Voting', '1', '7', '16', '0'],
                ['Total', '135', '236', '794', '0'],
                ['Otsego County Michigan - Total', '135', '236', '794', '0'],
                ['Cumulative', '', '', '', ''],
                ['Cumulative', '', '', '', ''],
                ['Election Day', '0', '0', '0', '0'],
                ['AV Counting Boards', '0', '0', '0', '0'],
                ['Early Voting', '0', '0', '0', '0'],
                ['Total', '0', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0', '0'],
                ['County - Total', '135', '236', '794', '0'],
            ]}},
            193: {'tables': {0: [
                ['Precinct', 'Rebecca Latuszek', 'Roger Latuszek',
                 'Total Votes', 'Unresolved Write-In'],
                ['County', '', '', '', ''],
                ['Otsego County Michigan', '', '', '', ''],
                ['Charlton Township, Precinct 1', '', '', '', ''],
                ['Election Day', '137', '155', '457', '2'],
                ['AV Counting Boards', '0', '0', '0', '0'],
                ['Early Voting', '3', '2', '5', '0'],
                ['Total', '140', '157', '462', '2'],
                ['Otsego County Michigan - Total', '140', '157', '462', '2'],
                ['Cumulative', '', '', '', ''],
                ['Cumulative', '', '', '', ''],
                ['Election Day', '0', '0', '0', '0'],
                ['AV Counting Boards', '0', '0', '0', '0'],
                ['Early Voting', '0', '0', '0', '0'],
                ['Total', '0', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0', '0'],
                ['County - Total', '140', '157', '462', '2'],
            ]}},
            # p35/63/67/86/166/216: single-precinct results tables whose
            # label column merged into one cell (candidate names and data
            # collapsed into the header row); retyped from the page images.
            35: {'tables': {1: [
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Otsego County Michigan', '', ''],
                ['City of Gaylord, Ward 2, Precinct 2', '', ''],
                ['Election Day', '0', '14'],
                ['AV Counting Boards', '0', '0'],
                ['Early Voting', '0', '0'],
                ['Total', '0', '14'],
                ['Livingston Township, Precinct 1', '', ''],
                ['Election Day', '0', '4'],
                ['AV Counting Boards', '0', '0'],
                ['Early Voting', '0', '1'],
                ['Total', '0', '5'],
                ['Otsego County Michigan - Total', '0', '19'],
                ['Cumulative', '', ''],
                ['Cumulative', '', ''],
                ['Election Day', '0', '0'],
                ['AV Counting Boards', '0', '0'],
                ['Early Voting', '0', '0'],
                ['Total', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '0', '19'],
            ]}},
            63: {'tables': {1: [
                ['Precinct', 'Rebecca S. House', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Otsego County Michigan', '', '', ''],
                ['Dover Township, Precinct 1', '', '', ''],
                ['Election Day', '24', '24', '0'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '3', '3', '0'],
                ['Total', '27', '27', '0'],
                ['Otsego County Michigan - Total', '27', '27', '0'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '', '', ''],
                ['Election Day', '0', '0', '0'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '0', '0', '0'],
                ['Total', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '27', '27', '0'],
            ]}},
            67: {'tables': {1: [
                ['Precinct', 'Jessica Henke', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Otsego County Michigan', '', '', ''],
                ['Elmira Township, Precinct 1', '', '', ''],
                ['Election Day', '115', '115', '0'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '6', '6', '0'],
                ['Total', '121', '121', '0'],
                ['Otsego County Michigan - Total', '121', '121', '0'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '', '', ''],
                ['Election Day', '0', '0', '0'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '0', '0', '0'],
                ['Total', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '121', '121', '0'],
            ]}},
            86: {'tables': {1: [
                ['Precinct', 'Leah Cygan', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Otsego County Michigan', '', '', ''],
                ['Chester Township, Precinct 1', '', '', ''],
                ['Election Day', '50', '50', '0'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '0', '0', '0'],
                ['Total', '50', '50', '0'],
                ['Otsego County Michigan - Total', '50', '50', '0'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '', '', ''],
                ['Election Day', '0', '0', '0'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '0', '0', '0'],
                ['Total', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '50', '50', '0'],
            ]}},
            166: {'tables': {1: [
                ['Precinct', 'Diane J. Franckowiak', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Otsego County Michigan', '', '', ''],
                ['Elmira Township, Precinct 1', '', '', ''],
                ['Election Day', '303', '303', '2'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '16', '16', '0'],
                ['Total', '319', '319', '2'],
                ['Otsego County Michigan - Total', '319', '319', '2'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '', '', ''],
                ['Election Day', '0', '0', '0'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '0', '0', '0'],
                ['Total', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '319', '319', '2'],
            ]}},
            216: {'tables': {1: [
                ['Precinct', 'Yes', 'No', 'Total Votes'],
                ['County', '', '', ''],
                ['Otsego County Michigan', '', '', ''],
                ['Elmira Township, Precinct 1', '', '', ''],
                ['Election Day', '402', '104', '506'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '21', '5', '26'],
                ['Total', '423', '109', '532'],
                ['Otsego County Michigan - Total', '423', '109', '532'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '', '', ''],
                ['Election Day', '0', '0', '0'],
                ['AV Counting Boards', '0', '0', '0'],
                ['Early Voting', '0', '0', '0'],
                ['Total', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '423', '109', '532'],
            ]}},
        },
        # p208-213: the 4-candidate Judge of Probate contest prints its
        # candidate columns as two interleaved page sets (Cadotte/Delaney on
        # 208+210b+212, Hesselink/Slough on 209+211+213); p210 is a text page
        # with an aux block then the Cadotte/Delaney results block.
        'text_blocks': {
            210: [
                {'kind': 'aux'},
                {'kind': 'results', 'slots': ['Courtney Eugene Cadotte',
                                              'David M. Delaney']},
            ],
        },
        'slot_fixes': [('Courtney Eugene', 'Cadotte',
                        'Courtney Eugene Cadotte')],
        # Title garbles: broken "Township" words, mis-joins, LaTeX debris.
        'title_fixes': [
            # 'Township' with its 'T' eaten by LaTeX debris or a quote
            # ('\ownship', '_i ownership', "Chester's ownership")
            (r"^([A-Za-z]+)(?:['’]s)?[\W_1iλl]*[o0]wn(?:ership|ship)\b",
             r'\1 Township'),
            # 'T' kept but mangled ('To.,nship', 'To-nship', 'To., nship')
            (r"^(\S+)\s*To[\s.,_-]*nship\b", r'\1 Township'),
            (r'^Livingsto[.,]* ', 'Livingston '),
            (r'^Otsego Lane ', 'Otsego Lake '),
            (r'^City of Gay[,.]?lord\b', 'City of Gaylord'),
            (r'^City of Guylord\b', 'City of Gaylord'),
            (r"^Corwith['’]o\b", 'Corwith'),
            (r'uncilmissioner', 'mmissioner'),
            (r'-W_arner', '-Warner'),
        ],
    },
}

COUNTY = None
PRECINCTS = []
MANUAL_TABLES = {}
NAME_FIXES = {}
# cfg precinct_aliases: OCR precinct-name garbles -> canonical label, tried
# as an exact fix inside canon_precinct (Otsego p017 'Charles Township').
PRECINCT_ALIASES = {}

# ---------------------------------------------------------------- markdown -> blocks

TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TR = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
TABLE = re.compile(r'<table.*?</table>', re.S)
TAG = re.compile(r'<[^>]+>')

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
    return int(s.replace(',', '')) if isinstance(s, str) else s


def squash(s):
    """Comparison key that survives OCR space-dropping."""
    return re.sub(r'[\s\W]+', '', s, flags=re.UNICODE).lower()


# Row labels (compared via squash()), built per county. The county's own
# pseudo-row labels ('<county> County Michigan', its - Total variant) join
# the generic ones.
def county_labels(county):
    sq = squash(f'{county} County')
    sqm = squash(f'{county} County Michigan')
    return {
        'county': 'pseudo',
        sq: 'pseudo',
        'michigan': 'pseudo',
        sqm: 'pseudo',
        sqm + 'total': 'county_total',
        'michigantotal': 'county_total',
        'countytotal': 'county_total',
        'cumulative': 'cumulative',
        'cumulativetotal': 'cumulative',
        'electionday': 'skip',
        'avcountingboards': 'skip',
        'earlyvoting': 'skip',
    }


COUNTY_LABELS = county_labels('Montmorency')
GROUP_LABELS = {'total'}
# cfg slot_fixes: (first header cell, second header cell, merged name) for
# candidate names the OCR split across two header cells.
SLOT_FIXES = []
TURNOUT_HEADER = {'registeredvoters', 'voterscast', '%turnout'}
# Header cells that belong to the aux block of a merged table.
AUX_HEADERS = {'', 'precinct', 'timescast', 'registeredvoters', 'registered',
               'voters'}
# Garbled Unresolved Write-In header cells (p080, p094, p098).
WI_GARBAGE = {'≤', '5支', 'unrwm'}
# Counting-group row labels (Otsego 2024 stacks per-group rows per precinct).
GROUP_ROWS = {'electionday', 'avcountingboards', 'earlyvoting'}
# OCR noise rows above a results header (run timestamps).
DATE_CELL = re.compile(r'^\d{1,2}/\d{1,2}/\d{2,4}\b')


def looks_aux(tbl):
    """True for an aux (Times Cast / Registered Voters) table whose header
    was garbled away: counting-group rows present, and the leading non-label
    header cells are all aux words (or gone). A results table with candidate
    names or a Total Votes cell in the header is not aux."""
    groups = tuple(GROUP_ROWS)
    if not any(squash(c).startswith(groups) for r in tbl for c in r):
        return False
    hdr_cells = [c for r in tbl[:3] for c in r[1:] if c.strip()
                 and not is_num(c) and not squash(c).startswith(groups)]
    if not all(squash(c) in AUX_HEADERS or squash(c) in WI_GARBAGE
               for c in hdr_cells):
        return False
    # A genuine aux table carries two value columns (Times Cast + Registered)
    # or an aux header word; single-value zero tables are garbled write-in
    # remnants (Otsego global p153) that must not clobber Times Cast.
    return any(squash(c) in AUX_HEADERS for r in tbl for c in r) \
        or any(sum(1 for c in row if is_num(c)) >= 2 for row in tbl)


MERGED_LABEL = re.compile(r'^(.*\S)\s+(\d[\d,]*)$')


def row_label_parts(row):
    """(label, cell index, merged trailing number) for a data row: the first
    non-empty non-numeric cell. Otsego 2024 puts the label in column 1 and
    sometimes merges the counting-group label with the first data value
    ('Total 14'); the split only fires when the base is a known row label."""
    for i, c in enumerate(row):
        if c and not is_num(c):
            m = MERGED_LABEL.match(c)
            if m and (squash(m.group(1)) in COUNTY_LABELS
                      or squash(m.group(1)) in GROUP_LABELS):
                return m.group(1), i, m.group(2)
            return c, i, None
    return '', -1, None


def canon_precinct(label):
    """Match a row label to a canonical precinct (OCR truncates/splits labels);
    None for anything else or for ambiguous prefixes (Montmorency 1 vs 2)."""
    key = re.sub(r'\s+', ' ', label).strip()
    if not key or len(key) < 12:
        return None
    key = PRECINCT_ALIASES.get(key, key)
    if key in PRECINCTS:
        return key
    matches = [p for p in PRECINCTS if p.startswith(key)]
    return matches[0] if len(matches) == 1 else None


SPLIT_LABEL = re.compile(r'^precinct\s*\d+$')


def merged_precinct(cell):
    """A precinct label merged into a longer header cell ('Precinct
    Livingston Township, Precinct 1'): the precinct whose squashed form ends
    the cell, or None."""
    sq = squash(cell)
    if len(sq) < 14:
        return None
    for p in PRECINCTS:
        ps = squash(p)
        if sq.endswith(ps) and len(sq) > len(ps):
            return p
    return None


def head_is_slotish(head):
    """True if a header row's label-column cell can be a candidate name
    (i.e. it does not look like a precinct or pseudo-row label)."""
    hsq = squash(head)
    return bool(hsq) and hsq not in COUNTY_LABELS and hsq != 'precinct' \
        and not SPLIT_LABEL.match(hsq) \
        and not re.search(r'precinct|county|michigan', hsq)


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

def normalize_title(line):
    """Flatten OCR LaTeX fragments in a title line: unwrap \text{...}-style
    groups, drop math delimiters and sub/superscript markers."""
    line = re.sub(r'^#{1,4}\s*', '', line)
    line = re.sub(r'\\(?:text|textit|textbf|uwave|underline)\{([^{}]*)\}',
                  r'\1', line)
    line = line.replace('\\lambda', ' ')
    line = line.replace('$', ' ')
    # an escaped sub/superscript ('\_{ownship}') would otherwise leave
    # '\ownship', which the leftover-command rule below eats whole
    line = re.sub(r'\\([_^])', r'\1', line)
    line = re.sub(r'[_^]\{([^{}]*)\}', r'\1', line)
    # leftover commands and markers (\mathbf, \uwave, lone backslashes)
    line = re.sub(r'\\[a-zA-Z]+', ' ', line)
    line = line.replace('\\', ' ')
    line = re.sub(r'[{}]', '', line)
    return re.sub(r'\s+', ' ', line).strip()


VOTE_FOR = re.compile(r'v[o0]t[e]?\s*f?[o0]r?[\.,]?', re.I)


def match_title(lines, cfg, where, notes):
    """Find a contest title among a page's leading text lines.

    Returns (title, party, vote_for) or None. Tolerates garbled party tags
    ("(DEₑₗₐₗ)", "(D⊥v1)"), missing vote-for digits, and OCR LaTeX."""
    for line in lines[:3]:
        line = normalize_title(line)
        m = VOTE_FOR.search(line)
        if not m:
            continue
        head, tail = line[:m.start()], line[m.end():]
        vf = re.match(r'\s*\(?\s*(\d+|\.|)?', tail).group(1)
        if vf and vf != '.':
            vote_for = vf
        elif vf == '.' or not vf:
            # "(Vote for .)" / "(Vote for" — single-seat garbles
            vote_for = '1'
            notes.append(f'{where}: vote_for garbled ({tail.strip()!r}) in '
                         f'{head!r}; assumed 1')
        else:
            continue
        party = ''
        pm = None
        for pm in re.finditer(r'\(([^()]*)\)', head):
            sq = re.sub(r'[^demrep]', '', pm.group(1).lower())
            if sq.startswith('d'):
                party = party or 'DEM'
            elif sq.startswith('r'):
                party = party or 'REP'
        if not party:
            bm = re.search(r'\b(DEM|REP)\)?\s*$', head)
            party = bm.group(1) if bm else ''
        if not party:
            # garbled unbalanced tag: '(REF, (Vote for 3) REP' — the party
            # word only survives at the end of the vote-for fragment
            tm = re.search(r'\b(DEM|REP)\)?\s*$', tail)
            party = tm.group(1) if tm else ''
        title = head
        if pm:
            title = title[:pm.start()] + title[pm.end():]
        # an unbalanced tag ('(DEM,') or the '(Vote' opener split across
        # parens survives the paren scan
        title = re.sub(r'\s*\($', '', title)
        title = re.sub(r'\s*\([^()]*\)?[.,]?\s*$', '', title)
        title = re.sub(r'\s+', ' ', title).strip(' ,(')
        for pat, repl in cfg.get('title_fixes', []):
            title = re.sub(pat, repl, title)
        return title, party, vote_for
    return None


def map_office(title):
    """Map a party-stripped contest title to (office, district)."""
    m = re.match(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^State Sena(?:te|tor) (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'State Senate', m.group(1)
    m = re.match(r'^State Representative (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'^Representative in State Legislature (\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'^County Commissioner,? District (\d+)$', title)
    if m:
        return 'County Commissioner', m.group(1)
    m = re.match(r'^(.+?) Precinct Delegates?$', title)
    if m:
        return f'{m.group(1)} Delegate to County Convention', ''
    exact = {'Governor': 'Governor', 'United States Senator': 'U.S. Senate'}
    if title in exact:
        return exact[title], ''
    # County offices: prefix with the county name (2026-series convention).
    if title.startswith('County '):
        return f'{COUNTY} {title}', ''
    return title, ''


# ---------------------------------------------------------------- table parsing

def slot_of(header_cell):
    """Map a header cell to a slot name: 'TV', 'WI', or the candidate name."""
    sq = squash(header_cell)
    if sq.startswith('totalv'):
        return 'TV'  # 'Total Votes' and garbles ('Total Vs', Otsego p37)
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
    slot_cells = []  # (header cell index, slot name) in encounter order
    first_data = len(rows)
    initial = None  # precinct header row(s) above the first numeric row
    pending = ''    # precinct label split across consecutive rows
    for i, row in enumerate(rows):
        if any(is_num(c) for c in row):
            first_data = i
            break
        if row and DATE_CELL.search(row[-1]):
            continue  # OCR noise row above the header (run timestamp)
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
            head = (row[0] if row else '').strip()
            nonempty = [c for c in row if c.strip()]
            sq1 = squash(nonempty[0]) if len(nonempty) == 1 else ''
            if len(nonempty) == 1 and not canon_precinct(nonempty[0]) \
                    and not SPLIT_LABEL.match(sq1) \
                    and sq1 not in COUNTY_LABELS and sq1 != 'precinct' \
                    and sq1 != 'total' \
                    and not re.search(r'precinct|county|michigan', sq1):
                # single-cell candidate header row (Otsego p154): the name
                # occupies the label column of a row of its own
                slot_cells.append((0, slot_of(nonempty[0])))
                continue
            # a header cell can merge the precinct label with column words
            # ('Precinct Livingston Township, Precinct 1', Otsego p119)
            mp = merged_precinct(head)
            if mp and initial is None:
                initial = mp
            pending = (pending + ' ' + head).strip()
        row_has_tvwi = False
        head = (row[0] if row else '').strip()
        for j, cell in enumerate(row[1:], start=1):
            if not cell or squash(cell) in ('precinct', 'registeredvoters',
                                            'registered', 'voters') \
                    or SPLIT_LABEL.match(squash(cell)):
                continue
            slot = slot_of(cell)
            row_has_tvwi = row_has_tvwi or slot in ('TV', 'WI')
            slot_cells.append((j, slot))
        if row_has_tvwi and head_is_slotish(head):
            # candidate name in the label column of a header row that also
            # carries Total Votes / Unresolved Write-In (Otsego global p198)
            slot_cells.append((0, slot_of(head)))
    # Slots in printed column order: sort by header cell index so a name
    # split across two header rows (Otsego global p199: 'Kari Visser-Robel'
    # above 'Stephenie Jacobson') lands in its true column.
    slots = []
    for _, s in sorted(slot_cells, key=lambda t: t[0]):
        if s not in slots:
            slots.append(s)
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
    # cfg slot_fixes: OCR splits one candidate name across two header cells
    # (Otsego p208 'Courtney Eugene' + 'Cadotte').
    fixed = []
    i = 0
    while i < len(slots):
        for a, b, merged in SLOT_FIXES:
            if i + 1 < len(slots) and slots[i] == a and slots[i + 1] == b:
                fixed.append(merged)
                i += 2
                break
        else:
            fixed.append(slots[i])
            i += 1
    slots = fixed
    if slots_override:
        slots = slots_override
    elif not slots:
        # continuation table whose header cell was garbled away (p058, p102,
        # p104): the single data column is Unresolved Write-In
        slots = ['WI']

    def assign(cells):
        vals = {}
        numerics = [c for c in cells if is_num(c)]
        if len(numerics) == len(slots) - 1 and slots[-1] == 'TV':
            # OCR drops the trailing Total Votes cell in some rows (p08);
            # in these tables TV is the sum of the candidate columns.
            numerics = numerics + [sum(num(c) for c in numerics)]
        if len(numerics) > len(slots):
            problems.append(f'{where}: {len(numerics)} numeric cells > {len(slots)} slots')
        for i, slot in enumerate(slots):
            vals[slot] = num(numerics[i]) if i < len(numerics) else 0
        return vals

    data, totals = {}, {}
    precinct = initial  # None once the county/cumulative section starts
    pending = ''
    for row in rows[first_data:]:
        label, idx, extra = row_label_parts(row)
        sq = squash(label)
        # a merged label cell contributes the first data value
        cells = ([extra] if extra is not None else []) + row[idx + 1:]
        if sq in COUNTY_LABELS:
            kind = COUNTY_LABELS[sq]
            if kind == 'skip':
                continue  # counting-group row; precinct stays current
            precinct = None
            pending = ''
            if kind == 'county_total' and any(is_num(c) for c in cells):
                totals.update(assign(cells))
            continue
        if sq in GROUP_LABELS:  # 'Total'
            if precinct:
                data.setdefault(precinct, {}).update(assign(cells))
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
        label, idx, extra = row_label_parts(row)
        sq = squash(label)
        numerics = [c for c in ([extra] if extra is not None else [])
                    + row[idx + 1:] if is_num(c)]
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


# ---------------------------------------------------------------- main parse

def parse_text_blocks(lines, where, problems):
    """Parse a text page that carries one or more column-blocks of the same
    contest stacked vertically (Otsego p210: an aux block, then a
    two-candidate results block). A block ends at a bare 'Precinct' header
    line; within a block, precinct rows carry counting-group rows
    'Election Day|AV Counting Boards|Early Voting|Total <v1> <v2...>'.
    Header words seen before a block's first precinct row are collected so
    a [Total Votes, Unresolved Write-In] block can be recognized.
    Returns [(header kinds set, {precinct: {'ED': [...], 'AV': [...],
    'EV': [...], 'Total': [...]}})]."""
    blocks = []
    cur = {}
    kinds = set()
    precinct = None
    pending = ''
    for line in lines:
        sq = squash(line)
        if sq == 'precinct':
            if cur:
                blocks.append((kinds, cur))
            cur = {}
            kinds = set()
            precinct = None
            pending = ''
            continue
        if precinct is None:
            # column-header words ahead of the first precinct row
            if 'timescast' in sq or re.search(r'register\w* voters', sq):
                kinds.add('aux')
            if 'totalvotes' in sq:
                kinds.add('tv')
            if 'unresolved' in sq or 'writein' in sq:
                kinds.add('wi')
        m = re.match(r'^(Election Day|AV Counting Boards|Early Voting|Total)\s+'
                     r'((?:\d[\d,]*\s*)+)$', line)
        if m and precinct:
            cur.setdefault(precinct, {})[
                {'Election Day': 'ED', 'AV Counting Boards': 'AV',
                 'Early Voting': 'EV', 'Total': 'Total'}[m.group(1)]] = \
                [num(v) for v in m.group(2).split()]
            continue
        matched = canon_precinct(pending + ' ' + line) \
            or canon_precinct(line)
        if not matched:
            # OCR merges the first data value onto the label line
            # ('Bagley Township, Precinct 1 8'); the value duplicates the
            # group row that follows, so strip it and retry.
            m2 = re.match(r'^(.*\S)\s+\d[\d,]*$', line)
            if m2:
                matched = canon_precinct(m2.group(1))
        if matched:
            precinct = matched
            pending = ''
        elif not is_num(line):
            pending = (pending + ' ' + line).strip()
    if cur:
        blocks.append((kinds, cur))
    return blocks


def text_block_kind(block, kinds=frozenset()):
    """'wi' (single write-in column), 'tvwi' (Total Votes + Unresolved
    Write-In), 'aux' (Times Cast / Registered) or 'results' for a parsed
    text block. Results iff every column's Total row equals the sum of its
    counting-group rows; the aux block's Registered column does not sum.
    Single-value blocks are write-in tables."""
    if kinds and kinds <= {'tv', 'wi'}:
        return 'tvwi'
    if kinds == {'aux'}:
        return 'aux'
    if all(len(rows.get('Total') or []) <= 1 for rows in block.values()):
        return 'wi'
    for rows in block.values():
        t = rows.get('Total') or []
        groups = [rows.get(g) or [] for g in ('ED', 'AV', 'EV')]
        for i in range(len(t)):
            parts = [g[i] if i < len(g) else 0 for g in groups]
            if any(parts) and t[i] != sum(parts):
                return 'aux'
    return 'results'


def apply_text_block(current, block, kind, where, problems):
    """Fold one text block into the current contest."""
    if kind == 'aux':
        for prec, rows in block.items():
            vals = rows.get('Total') or []
            if vals:
                current['times_cast'][prec] = vals[0]
        return
    if kind == 'tvwi':
        for prec, rows in block.items():
            vals = rows.get('Total') or []
            if len(vals) == 2:
                if vals[0] or prec not in current['tv']:
                    current['tv'][prec] = vals[0]
                current['wi'][prec] = current['wi'].get(prec, 0) + vals[1]
            elif len(vals) == 1:
                current['wi'][prec] = current['wi'].get(prec, 0) + vals[0]
            elif vals:
                problems.append(f'{where}: {prec} TV/WI block has {vals}')
        return
    if kind == 'wi':
        for prec, rows in block.items():
            vals = rows.get('Total') or []
            if vals:
                current['wi'][prec] = current['wi'].get(prec, 0) + vals[0]
        return
    slots = current['slot_order']
    for prec, rows in block.items():
        vals = rows.get('Total') or []
        if not vals:
            continue
        if not slots:
            current['pending_text'].append((where, prec, vals))
            continue
        if len(vals) != len(slots):
            problems.append(f'{where}: {prec} has {vals} for '
                            f'{len(slots)} slots ({slots})')
        for s, v in zip(slots, vals):
            if s == 'TV':
                if v or prec not in current['tv']:
                    current['tv'][prec] = v
            elif s == 'WI':
                current['wi'][prec] = current['wi'].get(prec, 0) + v
            else:
                cur = current['cands'].setdefault(s, {})
                cur[prec] = cur.get(prec, 0) + v


def build_contests(pages, problems, notes, cfg):
    contests = []
    turnout = {}
    current = None
    text_pages = cfg['text_pages']
    summary_pages = cfg['summary_pages']
    for no in sorted(pages):
        text, tables, lines = parse_page(pages[no])
        if no <= summary_pages:
            # Countywide turnout summary (verification only): parse every
            # table (p001's header line sits outside the table, so a header
            # test would skip it).
            for tbl in tables:
                turnout.update(parse_turnout_table(tbl))
            continue
        title_m = match_title(lines, cfg, f'p{no}', notes)
        if title_m:
            title, party, vote_for = title_m
            if not party and len(lines) > 3 and lines[3] in ('DEM', 'REP'):
                party = lines[3]
            office, district = map_office(title)
            current = {
                'title': title, 'office': office,
                'district': district, 'party': party,
                'vote_for': vote_for, 'pages': [no],
                'cands': {}, 'tv': {}, 'wi': {}, 'totals': {},
                'times_cast': {}, 'tc_total': None,
                'slot_order': [], 'pending_text': [],
            }
            contests.append(current)
        if current is None:
            continue
        current['pages'].append(no)

        tb = cfg.get('text_blocks', {}).get(no)
        if tb:
            # Text page carrying several column-blocks of the current
            # contest (Otsego p210): explicit per-block semantics.
            where = f'p{no} text ({current["title"]})'
            blocks = parse_text_blocks(lines, where, problems)
            if len(blocks) != len(tb):
                problems.append(f'{where}: {len(blocks)} blocks, '
                                f'expected {len(tb)}')
            for spec, (_, block) in zip(tb, blocks):
                if spec['kind'] == 'aux':
                    apply_text_block(current, block, 'aux', where, problems)
                else:
                    slots = spec['slots']
                    for prec, rows in block.items():
                        vals = rows.get('Total') or []
                        if len(vals) != len(slots):
                            problems.append(f'{where}: {prec} has {vals} '
                                            f'for {len(slots)} slots')
                        for s, v in zip(slots, vals):
                            current['cands'].setdefault(s, {})[prec] = v
            continue

        if cfg.get('auto_text_blocks'):
            # The report renders some tables as bare text lines; parse the
            # page's non-table lines into blocks and classify each. A page
            # can carry both a real table and text blocks (Otsego p22: an
            # aux table plus a text write-in block), so this runs alongside
            # the table pass below.
            where = f'p{no} text ({current["title"]})'
            for kinds, block in parse_text_blocks(lines, where, problems):
                kind = text_block_kind(block, kinds)
                if kind == 'tvwi' and any(
                        len(r.get('Total') or []) > 2 for r in block.values()):
                    # a results block that happens to list Total Votes /
                    # Unresolved Write-In among its headers
                    kind = 'results'
                apply_text_block(current, block, kind, where, problems)

        if not tables and no in text_pages:
            # Text-only page: single-column Unresolved Write-In table.
            where = f'p{no} text ({current["title"]})'
            data = parse_text_page(lines, where, problems)
            for precinct, vals in data.items():
                current['wi'][precinct] = current['wi'].get(precinct, 0) + vals['WI']
            continue

        if no in MANUAL_TABLES:
            man = MANUAL_TABLES[no]['tables']
            for t_i in sorted(man):
                # apply clean() so header cells get NAME_FIXES like OCR tables
                rows = [[clean(c) for c in row] for row in man[t_i]]
                if t_i < len(tables):
                    tables[t_i] = rows
                else:
                    # OCR dropped the table entirely (Otsego p27): append it
                    tables.append(rows)

        for t_i, tbl in enumerate(tables):
            if not tbl:
                continue
            parts = split_merged(tbl)
            if parts:
                aux_rows, result_rows = parts
                parse_aux(aux_rows, current, f'p{no} table {t_i} aux', problems)
                tbl = result_rows
            elif any('timescast' in squash(c) for row in tbl for c in row) \
                    or looks_aux(tbl):
                parse_aux(tbl, current, f'p{no} table {t_i} aux', problems)
                continue
            where = f'p{no} table {t_i} ({current["title"]})'
            slots, data, totals = parse_table(tbl, where, problems)
            for s in slots:
                if s not in current['slot_order']:
                    current['slot_order'].append(s)
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
    # text results blocks recorded before the contest's table headers were
    # seen can be mapped now
    for c in contests:
        for where, prec, vals in c['pending_text']:
            if not c['slot_order']:
                problems.append(f'{where}: {prec} {vals} with no known '
                                f'column slots')
                continue
            if len(vals) != len(c['slot_order']):
                problems.append(f'{where}: {prec} has {vals} for '
                                f'{len(c["slot_order"])} slots')
            for s, v in zip(c['slot_order'], vals):
                if s == 'TV':
                    if v or prec not in c['tv']:
                        c['tv'][prec] = v
                elif s == 'WI':
                    c['wi'][prec] = c['wi'].get(prec, 0) + v
                else:
                    cur = c['cands'].setdefault(s, {})
                    cur[prec] = cur.get(prec, 0) + v
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
        # Some contests print no Total Votes column at all (Otsego's
        # two-candidate precinct delegate races); a missing TV cell is only
        # a defect when the report has TV columns elsewhere in the contest.
        has_tv = 'TV' in c['slot_order'] or 'TV' in c['totals']
        # Per-precinct: candidate sum == Total Votes (unresolved write-ins are
        # reported separately and NOT included in Total Votes); TV+WI within
        # Times Cast.
        for p in sorted(precincts):
            csum = sum(c['cands'][cand].get(p, 0) for cand in c['cands'])
            wi = c['wi'].get(p, 0)
            tv = c['tv'].get(p)
            if tv is None:
                if has_tv:
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
            tv = c['tv'].get(p)
            if tv is None:
                # the source prints no Total Votes column for this contest
                # (Otsego's two-candidate precinct delegate races); Ballots
                # Cast is then the votes reported for the contest
                tv = sum(cand.get(p, 0) for cand in c['cands'].values())
            rows.append([COUNTY, p, c['office'], c['district'], c['party'],
                         'Ballots Cast', tv + wi])
    with open(out_path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    return rows


def main():
    global COUNTY, PRECINCTS, MANUAL_TABLES, NAME_FIXES, COUNTY_LABELS, \
        SLOT_FIXES, PRECINCT_ALIASES
    ap = argparse.ArgumentParser()
    ap.add_argument('county', help='COUNTY_CONFIG key')
    ap.add_argument('--cache', default=None)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    if args.county not in COUNTY_CONFIG:
        sys.exit(f'no COUNTY_CONFIG for {args.county!r}')
    cfg = COUNTY_CONFIG[args.county]
    COUNTY = args.county
    PRECINCTS = cfg['precincts']
    MANUAL_TABLES = cfg['manual']
    NAME_FIXES = cfg['name_fixes']
    SLOT_FIXES = cfg.get('slot_fixes', [])
    PRECINCT_ALIASES = cfg.get('precinct_aliases', {})
    COUNTY_LABELS = county_labels(COUNTY)
    cache = args.cache or cfg.get('cache')
    out = args.out or cfg['out']

    import glob
    import os
    pages = {}
    offset = 0
    for cache_dir in cfg.get('caches', [cache]):
        for md_path in sorted(glob.glob(os.path.join(cache_dir, 'p*.md'))):
            no = offset + int(re.search(r'p(\d+)\.md$', md_path).group(1))
            pages[no] = open(md_path).read()
        offset += len(pages)
    if len(pages) != cfg['pages']:
        print(f'expected {cfg["pages"]} pages, got {len(pages)}',
              file=sys.stderr)
        sys.exit(1)

    problems, notes = [], []
    contests, turnout = build_contests(pages, problems, notes, cfg)
    validate(contests, turnout, problems, notes)
    rows = emit(contests, out)

    print(f'Wrote {len(rows)} rows to {out} ({len(contests)} contests)')
    for n in notes:
        print('NOTE:', n)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()