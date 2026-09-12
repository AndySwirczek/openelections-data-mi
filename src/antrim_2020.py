"""Parse the Antrim County Aug 2020 primary "Statement of Votes Cast" PDFs
via cached PaddleOCR markdown.

Sources (openelections-sources-mi/2020/primary), image-only:
'Antrim County Aug 2020 Primary Official Results p1-108.pdf' (DEM contests,
plus the US Senate (REP) contest on its last page) and
'Antrim County Aug 2020 Primary Official Results p109-278.pdf' (REP
contests, delegates and proposals; the extract's page N = the original's
page N+108). OCR'd with PaddleOCR-VL (src/fetch_paddleocr_md.py, caches in
/tmp/paddleocr_md); the two caches are concatenated in page order.

Same ES&S SOVC layout as Antrim 2022 (primary_2022_ocr_sovc.py): each
contest is a heading line ('<contest> (DEM) (Vote for 1) DEM') followed by
a turnout-only table (Precinct | Times Cast | Registered Voters — kept and
emitted as 'Ballots Cast' candidate rows under the office, the Houghton
convention) and a results-only table (Precinct | <candidate> ... | Total
Votes | Unresolved Write-In; zero-vote candidates' columns are dropped and
data rows carry a phantom blank percent cell after each candidate). Here
every column maps by NAME, not slot, because the source splits a contest's
columns across consecutive headless tables — the 105th District (REP)
prints Ken Borton / Tony Cutler on one page and Jimmy Schmidt + Total
Votes + Unresolved Write-In on the next; County Sheriff (REP)'s Unresolved
Write-In column and several delegate contests' candidate halves print as
separate tables with no heading. Tables without a pending heading
therefore merge into the open contest by candidate name, and each table's
'Antrim County Michigan - Total' row validates the per-candidate precinct
sums of its own columns. The per-row Total Votes value (which spans every
column group) is kept as a cross-check: it must equal the sum of the
merged row's named candidates.

Local contests are emitted (unlike 2022): 'County X' offices keep the
County prefix, township offices are '<Township> <Office>', delegate
headings '<Twp>, Precinct N Delegate' gain ' to County Convention', and
proposals (no party tag) become offices with Yes/No candidates and a blank
party.

Usage:
    .venv/bin/python src/antrim_2020.py
"""
import glob
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import write_csv  # noqa: E402
from primary_2022_common import one_space  # noqa: E402
from primary_2022_ocr_sovc import (GLUED, INT, PCT, SKIP_TOKEN,  # noqa: E402
                                   WRITEIN_TOKEN, classify_cells,
                                   header_cells, split_row)

COUNTY = 'Antrim'
CACHES = [
    '/tmp/paddleocr_md/Antrim_2020_p1_108/'
    'Antrim_County_Aug_2020_Primary_Official_Results_p1_108',
    '/tmp/paddleocr_md/Antrim_2020_p109_278/'
    'Antrim_County_Aug_2020_Primary_Official_Results_p109_278',
]

TITLE_RE = re.compile(
    r'^(?P<name>.+?)\s*(?:\((?P<party>DEM|REP)\))?\s*\(Vote for (?P<n>\d+)\)$')
# page furniture text lines (page footers, report headers)
SKIP_LINE = re.compile(
    r'Page:\s*\d+\s*of\s*\d+|^\d{1,2}/\d{1,2}/\d{4}\s+\d[\d:]*\s*(AM|PM)?$'
    r'|Statement of Votes Cast|Open Primary|SOVC for|OFFICIAL RESULTS'
    r'|August 04, 2020|Antrim County, Michigan', re.I)
# every Antrim precinct label is a township; everything else is furniture
PRECINCT_LABEL = re.compile(r'Township\s*,\s*Precinct\s+\d+$')
TOTAL_LABEL = re.compile(r'-\s*Total\s*$')


def ordinal(n):
    n = int(n)
    if n % 100 in (11, 12, 13):
        return f'{n}th'
    return f'{n}{ {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th") }'


def cells_of(tr):
    return [html.unescape(one_space(re.sub(r'<[^>]+>', '', c)))
            for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]


def parse_pages(caches):
    """[(kind, payload)] over both caches in page order; same rules as
    primary_2022_ocr_sovc.parse_pages (unterminated tables end at the next
    '<table', text lines are kept as text)."""
    items = []
    header_lines = []

    for cache in caches:
        for path in sorted(glob.glob(os.path.join(cache, 'p*.md'))):
            md = open(path).read()
            pos = 0
            while pos < len(md):
                start = md.find('<table', pos)
                for line in md[pos:start if start >= 0 else len(md)] \
                        .splitlines():
                    text = one_space(re.sub(r'<[^>]+>', '', line).strip())
                    if text:
                        header_lines.append(text)
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
                for l in header_lines:
                    items.append(('text', l))
                header_lines.clear()
                items.append(('table', [cells_of(tr) for tr in
                                        re.findall(r'<tr>(.*?)</tr>', seg,
                                                   re.S)]))
    return items


def norm_title(line):
    t = one_space(re.sub(r'<[^>]+>', '', line.strip()))
    t = re.sub(r'^#+\s*', '', t)
    t = re.sub(r'\s*(?:DEM|REP)\s*$', '', t)
    return t


def map_office(name, problems):
    """(office, district) for a heading name, per the committed 2020
    conventions (County offices keep their County prefix; township offices
    are jurisdiction-first; delegates gain ' to County Convention')."""
    name = one_space(name)
    if name == 'United States Senator':
        return 'U.S. Senate', ''
    m = re.match(r'Rep in Congress (\d+)(?:st|nd|rd|th) District$', name)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'Rep in State Legislature '
                 r'(\d+)(?:st|nd|rd|th) District$', name)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'County Commissioner (\d+)$', name)
    if m:
        return f'County Commissioner {ordinal(m.group(1))} District', ''
    m = re.match(r'(.+?, Precinct \d+) Delegate$', name)
    if m:
        return f'{m.group(1)} Delegate to County Convention', ''
    return name, ''


class Contest:
    def __init__(self, title, party):
        self.title = title
        self.party = party
        self.rows = {}          # label -> [ {name: votes}, writein, tv ]
        self.labels = []        # insertion order
        self.times = {}         # label -> Times Cast from the turnout table
        self.totals = {}        # name -> printed county total
        self.total_writein = 0
        self.tv_total = None    # printed county Total Votes (all groups)
        self.cols = None        # header tokens of the most recent table

    def merge_row(self, label, vals, writein, tv, tag, problems):
        label = fix_label(label)
        if label not in self.rows:
            self.labels.append(label)
            self.rows[label] = [{}, 0, None]
        slot = self.rows[label]
        if vals and vals == slot[0] and writein == slot[1] and \
                (tv == slot[2] or tv is None):
            print(f'NOTE: {tag}: duplicate re-print of {label!r} dropped')
            return
        for name, v in vals.items():
            if name in slot[0] and slot[0][name] != v:
                problems.append(f'{self.title}: {label!r}: conflicting '
                                f'values for {name}: {slot[0][name]} vs {v}')
            slot[0][name] = v
        slot[1] += writein
        if tv is not None:
            if slot[2] is not None and slot[2] != tv:
                problems.append(f'{self.title}: {label!r}: conflicting '
                                f'Total Votes {slot[2]} vs {tv}')
            slot[2] = tv

    def check(self, problems):
        for label in self.labels:
            vals, _w, tv = self.rows[label]
            s = sum(vals.values())
            if tv is not None and tv != s:
                problems.append(f'{self.title}: {label!r}: Total Votes '
                                f'{tv} != candidate sum {s}')
        for name, want in self.totals.items():
            got = sum(self.rows[l][0].get(name, 0) for l in self.labels)
            if got != want:
                problems.append(f'{self.title}: {name}: precinct sum '
                                f'{got} != printed county total {want}')
        if self.total_writein:
            got = sum(self.rows[l][1] for l in self.labels)
            if got != self.total_writein:
                problems.append(f'{self.title}: Write-In: precinct sum '
                                f'{got} != printed county total '
                                f'{self.total_writein}')
        if self.tv_total is not None and \
                self.tv_total != sum(self.totals.values()):
            # the Total Votes column spans every column group of a split
            # contest, so it must equal the sum of the printed per-candidate
            # totals across all of the contest's tables
            problems.append(f'{self.title}: printed Total Votes '
                            f'{self.tv_total} != sum of candidate totals '
                            f'{sum(self.totals.values())}')


def row_values_tv(cells, toks, problems, tag, label):
    """({name: votes}, write-in sum, Total Votes) — percent cells are
    skipped, each integer goes to the next header token; values landing on
    skip tokens or past the header are the row's Total Votes."""
    vals = {}
    writein = 0
    tv = None
    ci = 0
    for cell in cells:
        cell = one_space(cell)
        if not cell or PCT.match(cell):
            continue
        m = GLUED.match(cell)
        num = m.group(1) if m else (cell if INT.match(cell) else None)
        if num is None:
            problems.append(f'{tag}: {label!r}: cell {cell!r} is not a '
                            f'number')
            continue
        v = int(num.replace(',', ''))
        tok = toks[ci] if ci < len(toks) else None
        if tok in (None, SKIP_TOKEN):
            if tv is not None and tv != v:
                problems.append(f'{tag}: {label!r}: two Total Votes '
                                f'values {tv} vs {v}')
            tv = v
        elif tok == WRITEIN_TOKEN:
            writein += v
        else:
            vals[tok] = v
        ci += 1
    return vals, writein, tv


def is_precinct(label):
    return bool(PRECINCT_LABEL.search(one_space(label.replace('\\n', ' '))))


def fix_label(label):
    """normalize a precinct label for emission (OCR prints a space before
    some commas: 'Custer Township , Precinct 1')."""
    label = one_space(label.replace('\\n', ' '))
    return re.sub(r'\s+,', ',', label)


def open_heading(name, party, state):
    """close the open contest and queue the heading for its tables."""
    if state['open'] is not None:
        state['open'].check(state['problems'])
        state['contests'].append(state['open'])
        state['open'] = None
    state['pending'] = (name, party)
    state['pending_times'] = {}


def parse():
    problems = []
    contests = []
    state = {'open': None, 'contests': contests, 'problems': problems,
             'pending': None, 'pending_times': {}, 'orphan': None,
             'orphan_times': {}}

    for kind, payload in parse_pages(CACHES):
        tag = COUNTY
        if kind == 'text':
            t = norm_title(payload)
            m = TITLE_RE.match(t)
            if not m:
                if not SKIP_LINE.search(payload) and not t.startswith('<'):
                    problems.append(f'unrecognized line {t!r}')
                continue
            open_heading(one_space(m.group('name').rstrip('.,')),
                         m.group('party') or '', state)
            continue

        chunk = [r for r in payload if r and any(one_space(c) for c in r)]
        if not chunk:
            continue
        # a contest heading fused into the table's first cell
        if len(chunk[0]) == 1:
            m = TITLE_RE.match(norm_title(chunk[0][0]))
            if m:
                open_heading(one_space(m.group('name').rstrip('.,')),
                             m.group('party') or '', state)
                chunk = chunk[1:]

        head = header_cells(chunk[0], COUNTY)
        hdr_off = 1
        if head is None and len(chunk) > 1 and \
                header_cells(chunk[1], COUNTY) is not None:
            head = header_cells(chunk[1], COUNTY)
            hdr_off = 2

        if head is not None and head[0] == 'turnout':
            times = state['pending_times'] if state['pending'] \
                else state['orphan_times']
            for row in chunk[hdr_off:]:
                label = one_space(row[0].replace('\\n', ' '))
                if is_precinct(label):
                    nums = [int(c.replace(',', '')) for c in row[1:]
                            if INT.match(one_space(c))]
                    if nums:
                        times[label] = nums[0]
            continue

        # results table (candidate columns) or write-in-only spill table
        toks = head[1] if head is not None else None
        if head is None:
            # a headless table: data rows for the open contest, or a
            # spill/continuation whose header the OCR mangled away
            cells0 = chunk[0][1:]
            ctoks = classify_cells(cells0)
            if any(t == WRITEIN_TOKEN for t in ctoks) and not any(
                    re.search(r'\d', one_space(c)) for c in cells0):
                toks = ctoks          # a write-in-only spill table
            elif len(chunk) > 1 and not any(
                    one_space(c) for c in chunk[0]) and not any(
                    re.search(r'\d', one_space(c))
                    for c in chunk[1][1:]):
                toks = classify_cells(chunk[1][1:])   # header on row 2
                hdr_off = 2

        if toks is None:
            if state['open'] is None:
                problems.append(f'{tag}: headless table with no open '
                                f'contest (first row {chunk[0]!r})')
                continue
            for row in chunk:
                label = one_space(row[0].replace('\\n', ' '))
                if not is_precinct(label):
                    continue
                rlabel, cells = split_row(row)
                vals, writein, tv = row_values_tv(
                    cells, state['open'].cols, problems, tag, rlabel)
                state['open'].merge_row(label, vals, writein, tv, tag,
                                        problems)
            continue

        if state['pending'] is not None:
            name, party = state['pending']
            c = Contest(name, party)
            c.times = state['pending_times']
            state['pending'], state['pending_times'] = None, {}
            state['open'] = c
        elif state['open'] is not None:
            c = state['open']
        else:
            # a results table printed ahead of its heading: buffer it for
            # the next heading (Cheboygan-style OCR reordering)
            state['orphan'] = Contest('', '')
            state['orphan'].cols = list(toks)
            oc = state['orphan']
            for row in chunk:
                label = one_space(row[0].replace('\\n', ' '))
                if not is_precinct(label):
                    if TOTAL_LABEL.search(label) and \
                            not label.startswith('Cumulative'):
                        vals, writein, tv = row_values_tv(
                            row[1:], oc.cols, problems, tag, label)
                        if vals or writein or tv is not None:
                            oc.totals.update(vals)
                            if writein:
                                oc.total_writein = writein
                            if tv is not None:
                                oc.tv_total = tv
                    continue
                rlabel, cells = split_row(row)
                vals, writein, tv = row_values_tv(
                    cells, toks, problems, tag, rlabel)
                oc.merge_row(label, vals, writein, tv, tag, problems)
            continue
        c.cols = list(toks)
        tag_c = f'{c.title} ({c.party or "np"})'

        for row in chunk[hdr_off:]:
            label = one_space(row[0].replace('\\n', ' '))
            if not is_precinct(label):
                if TOTAL_LABEL.search(label) and \
                        not label.startswith('Cumulative'):
                    # the same alignment rules as data rows (phantom blank
                    # cells do not consume header slots); the table may
                    # print its total twice ('Antrim County Michigan -
                    # Total' and 'County - Total')
                    vals, writein, tv = row_values_tv(
                        row[1:], c.cols, problems, tag_c, label)
                    if vals or writein or tv is not None:
                        for name, v in vals.items():
                            if name in c.totals and c.totals[name] != v:
                                problems.append(f'{tag_c}: printed totals '
                                                f'disagree on {name}: '
                                                f'{c.totals[name]} vs {v}')
                            c.totals[name] = v
                        if writein:
                            if c.total_writein and \
                                    c.total_writein != writein:
                                problems.append(f'{tag_c}: printed write-in '
                                                f'totals disagree: '
                                                f'{c.total_writein} vs '
                                                f'{writein}')
                            c.total_writein = writein
                        if tv is not None:
                            if c.tv_total is not None and \
                                    c.tv_total != tv:
                                problems.append(f'{tag_c}: printed Total '
                                                f'Votes totals disagree: '
                                                f'{c.tv_total} vs {tv}')
                            c.tv_total = tv
                continue
            rlabel, cells = split_row(row)
            vals, writein, tv = row_values_tv(
                cells, c.cols, problems, tag_c, rlabel)
            c.merge_row(label, vals, writein, tv, tag_c, problems)

    if state['open'] is not None:
        state['open'].check(problems)
        contests.append(state['open'])

    rows = []
    for c in contests:
        office, district = map_office(c.title, problems)
        for label in c.labels:
            vals, writein, _tv = c.rows[label]
            for name, v in vals.items():
                if v:
                    rows.append({'county': COUNTY, 'precinct': label,
                                 'office': office, 'district': district,
                                 'party': c.party, 'candidate': name,
                                 'votes': v})
            if writein:
                rows.append({'county': COUNTY, 'precinct': label,
                             'office': office, 'district': district,
                             'party': c.party, 'candidate': 'Write-In',
                             'votes': writein})
            if c.times.get(label):
                rows.append({'county': COUNTY, 'precinct': label,
                             'office': office, 'district': district,
                             'party': c.party, 'candidate': 'Ballots Cast',
                             'votes': c.times[label]})

    for p in problems:
        print('PROBLEM:', p)
    if problems:
        sys.exit(f'{COUNTY}: {len(problems)} problems')
    write_csv(COUNTY, rows)


if __name__ == '__main__':
    parse()