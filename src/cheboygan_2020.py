"""Parse the Cheboygan County Aug 2020 primary "Results per Precinct" PDF
via cached PaddleOCR markdown.

Source (openelections-sources-mi/2020/primary):
'Cheboygan MI fs37-1597417036-80286.pdf', 44 pages, image-only (embedded
text layer is garbage), OCR'd with PaddleOCR-VL (src/fetch_paddleocr_md.py,
per-page markdown cached in /tmp/paddleocr_md).

Same Board of Canvassers contest-major style as Iron: each contest is one
HTML table (Precinct column, one column per candidate / Yes-No / Write-in,
a Total row of column sums) preceded by its title line in one of three
shapes ('## Title', '<div>Title</div>', bare line). 22 precincts (19
townships x Precinct 1 + City of Cheboygan P1-3). No turnout columns exist
in this source (no pseudo rows). Delegate tables are single-precinct with a
Total row; tables split across pages continue without a header.

OCR/source quirks handled:
- the printed source itself misspells 'Iverness Township' (Inverness) in
  its proposal titles, 'Mackinac Towsnship' (Mackinaw) once, and
  'Elles Township' (Ellis) in delegate titles; normalized here;
- the 106th-district title doubles as 'Representative in State Legislature
  for Representative in Statel Legislature 106th District' (OCR dropped the
  'gu' in the inner copy);
- the DEM County Clerk and Register of Deeds contest prints no table at all
  (no candidate filed; all-zero), so it emits no rows.

Office mapping (the committed 2020 conventions): 'United States Senator for
State' -> U.S. Senate; 'Representative in Congress 1st District for State'
-> U.S. House 1; 'Representative in State Legislature for Representative in
State Legislature Nth District' -> State House N; 'County Commissioner for
County Commissioner N' -> 'County Commissioner Nth District' (blank
district); 'County Clerk and Register of Deeds for Cheboygan County' ->
'Clerk and Register of Deeds' (the Clinton/Eaton/Kalamazoo 2020 style);
'County X for Cheboygan County' -> bare county office; 'Township X for
<Township>' -> '<Township> X' (covers Constable and Park Commissioner);
'Delegate to the County Convention for <precinct>' -> '<precinct> Delegate
to County Convention' (the Clare style).

Usage:
    .venv/bin/python src/cheboygan_2020.py \
        [--cache /tmp/paddleocr_md/Cheboygan_MI_Results_Per_Precinct]
"""
import argparse
import html
import os
import re
import sys
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import county_districts, write_csv  # noqa: E402

COUNTY = 'Cheboygan'
DEFAULT_CACHE = '/tmp/paddleocr_md/Cheboygan_MI_Results_Per_Precinct'

TITLE_RE = re.compile(
    r'^(?P<name>.+?)\s*(?:\((?P<party>DEM|REP)\))?\s*'
    r'\(Vote for (?P<n>\d+)\)$')
TEXT_FIXES = {}
TITLE_FIXES = {}
MANUAL_HEADERS = {}
MANUAL_TOTALS = {}


def ordinal(n):
    n = int(n)
    if n % 100 in (11, 12, 13):
        return f'{n}th'
    return f'{n}{ {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th") }'


def norm_title(t):
    t = re.sub(r'^#+\s*', '', t.strip())
    t = re.sub(r'^<div[^>]*>', '', t)
    t = re.sub(r'</div>$', '', t).strip()
    t = t.replace('{DEM}', '(DEM)').replace('{REP}', '(REP)')
    t = t.replace('Vote for_', 'Vote for ')
    return re.sub(r'\s+', ' ', t)


def cells(row):
    return [html.unescape(c.strip())
            for c in re.findall(r'>([^<]*)</td>', row)]


def clean_int(c):
    c = c.strip().replace(',', '').replace('*', '').replace('$', '').strip()
    return int(c) if re.fullmatch(r'\d+', c) else None


class Contest:
    def __init__(self, title, party):
        self.title = title
        self.party = party
        self.header = None       # column names after 'Precinct'
        self.rows = []           # [label, [values]]
        self.totals = None       # raw total cells
        self.seen = set()        # (label, tuple(vals)) already read

    def key(self, label, vals):
        return (label, tuple(str(v) for v in vals))

    def add_row(self, label, vals):
        if self.rows and self.rows[-1][0] == label and \
                self.rows[-1][1] == vals:
            return  # OCR printed the row twice inside one table
        if self.totals is not None:
            # duplicate re-read of a table that was already completed
            if self.key(label, vals) not in self.seen:
                raise ValueError(f'row after Total not seen before: '
                                 f'{label} {vals}')
            return
        self.seen.add(self.key(label, vals))
        self.rows.append((label, vals))


def parse_contest(c, problems):
    """Validate one contest against its Total row of column sums."""
    ncols = len(c.totals)
    if c.header is None or len(c.header) != ncols:
        problems.append(f'{c.title!r}: header {c.header} vs {ncols} total '
                        f'columns')
        return None
    # column sums of the clean cells; ambiguous cells collected separately
    sums = [0] * ncols
    ambiguous = []           # (row_idx, col_idx, [candidate ints])
    for ri, (label, vals) in enumerate(c.rows):
        if len(vals) != ncols:
            problems.append(f'{c.title!r} / {label}: {len(vals)} values != '
                            f'{ncols}')
            return None
        for ci, v in enumerate(vals):
            if isinstance(v, list):
                ambiguous.append((ri, ci, v))
            else:
                sums[ci] += v

    def total_ok(colsum, raw):
        if not raw or not raw.strip():
            return True  # missing/empty total cell: nothing to check
        d = raw.replace(',', '').replace('*', '').replace('$', '')
        if re.fullmatch(r'\d+', d) and int(d) == colsum:
            return True
        # glued totals: accept when the column sum appears inside the digits
        return str(colsum) in d.replace(' ', '')

    # brute-force the (rare) ambiguous cells against the Total constraints
    solution = None
    for combo in product(*[a[2] for a in ambiguous]) if ambiguous else [()]:
        trial = list(sums)
        for (ri, ci, _), v in zip(ambiguous, combo):
            trial[ci] += v
        if all(total_ok(trial[k], c.totals[k]) for k in range(ncols)):
            if solution is not None:
                problems.append(f'{c.title!r}: ambiguous cells resolve '
                                f'ambiguously: {ambiguous}')
                return None
            solution = (combo, trial)
    if solution is None:
        problems.append(f'{c.title!r}: no assignment fits the Totals '
                        f'(ambiguous cells {ambiguous}, sums {sums}, '
                        f'totals {c.totals})')
        return None
    combo, sums = solution
    return c.header, c.rows, sums


def embedded_ints(cell):
    return [int(x) for x in re.findall(r'\d+', cell.replace(',', ''))]


def fix_label(label):
    label = label.replace('\\n', ' ')   # OCR writes a wrapped cell line as \n
    label = ' '.join(label.split())
    label = label.replace('Koshler', 'Koehler')
    if label == 'City of Cheboygan,':
        # p022 (HD107 REP): the source cell truncates the label; the three
        # City of Cheboygan rows are Precincts 1-3 in order.
        label = 'City of Cheboygan, Precinct 3'
    return label


def fix_name(name):
    """Normalize misspellings the printed source itself carries."""
    name = ' '.join(name.split())
    name = name.replace('Iverness', 'Inverness')     # source typo (p043)
    name = name.replace('Mackinac Towsnship', 'Mackinaw Township')
    name = name.replace('Mackinac Township', 'Mackinaw Township')
    name = name.replace('Elles Township', 'Ellis Township')
    name = name.replace('Statel Legislature', 'State Legislature')
    return name


def map_office(name, districts, problems):
    name = fix_name(name)
    if name == 'United States Senator for State':
        return 'U.S. Senate', ''
    m = re.match(r'Representative in Congress (\d+)(?:st|nd|rd|th) District '
                 r'for State$', name)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'Representative in State Legislature for '
                 r'Representative in State Legislature '
                 r'(\d+)(?:st|nd|rd|th) District$', name)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'County Commissioner for County Commissioner (\d+)$', name)
    if m:
        return f'County Commissioner {ordinal(m.group(1))} District', ''
    m = re.match(r'Delegate to the County Convention for (.+)$', name)
    if m:
        return f'{fix_label(m.group(1))} Delegate to County Convention', ''
    m = re.match(r'Township (.+?) for (.+ Township)$', name)
    if m:
        return f'{m.group(2)} {m.group(1)}', ''
    if name == 'County Clerk and Register of Deeds for Cheboygan County':
        return 'Clerk and Register of Deeds', ''
    m = re.match(r'County (.+?) for Cheboygan County$', name)
    if m:
        return m.group(1), ''
    return name, ''


def read_table(part, problems, title):
    """Read one <table> chunk into a Contest (header/rows/totals) or None."""
    c = Contest('', '')
    for row in re.findall(r'<tr>(.*?)</tr>', part, flags=re.S):
        cs = cells(row)
        if not any(cs):
            continue  # empty stub row
        if cs[0] == 'Precinct':
            sig = tuple(cs[1:])
            if sig in MANUAL_HEADERS:
                # the OCR dropped this table's title line
                name, party = MANUAL_HEADERS[sig]
                c = Contest(name, party)
            if c.header is None:
                c.header = [fix_label(h) for h in cs[1:]]
            continue
        if cs[0] == 'Total':
            vals = cs[1:]
            if c.totals is not None:
                if vals != c.totals:
                    problems.append(f'{c.title!r}: two Total rows '
                                    f'{c.totals} vs {vals}')
            else:
                c.totals = vals
            continue
        vals = []
        for cell in cs[1:]:
            d = cell.replace(',', '').replace('*', '').replace('$', '')
            if re.fullmatch(r'\d+', d):
                vals.append(int(d))
            else:
                nums = embedded_ints(cell)
                if not nums:
                    nums = [0]
                vals.append(nums if len(nums) > 1 else nums[0])
        try:
            c.add_row(fix_label(cs[0]), vals)
        except ValueError as e:
            problems.append(f'{c.title!r}: {e}')
    return c


def parse(cache):
    problems = []
    events = []   # ('title', name, party) or ('table', Contest)

    for page in sorted(os.listdir(cache)):
        if not re.fullmatch(r'p\d+\.md', page):
            continue
        text = open(os.path.join(cache, page)).read()
        for old, new in TEXT_FIXES.items():
            text = text.replace(old, new)
        for part in re.split(r'(<table.*?</table>)', text, flags=re.S):
            if part.startswith('<table'):
                if not events:
                    continue  # header/log-stamp table before any title
                tc = read_table(part, problems, None)
                if tc is not None and (tc.header or tc.rows or tc.totals):
                    events.append(('table', tc))
                continue
            for line in part.strip().splitlines():
                t = norm_title(line)
                if not t or t.startswith('<img') or '<table' in t:
                    continue
                if t.startswith(('#', '2020', '**', '***', '$', '大')) or \
                        'Results per Precinct' in t:
                    continue
                m = TITLE_RE.match(t)
                if not m:
                    problems.append(f'{page}: unrecognized line {t!r}')
                    continue
                name = ' '.join(m.group('name').rstrip('.,').split())
                party = m.group('party') or ''
                name = TITLE_FIXES.get(fix_name(name), fix_name(name))
                events.append(('title', name, party))

    # ---- association pass -------------------------------------------------
    # The OCR sometimes hoists title lines ahead of their tables (p033 prints
    # two titles back-to-back, then the tables) and sometimes prints a table
    # before its title line (p008). It also re-prints one completed table in
    # full on the next page (the DEM Sheriff write-in table, pp3/p4, whose
    # two copies even disagree — the first copy is the one whose rows sum to
    # its printed Total). Rules:
    # - titles queue up; a table claims one (content-matched when several
    #   are pending: its first precinct label appears in the title);
    # - a header-bearing table while no title is pending attaches to the
    #   still-open contest as a page-break continuation, else becomes an
    #   orphan claimed by the next title line — unless all its labels were
    #   already read for that contest, in which case it is a duplicate
    #   re-print and is dropped.
    from collections import deque
    pending = deque()
    orphan = None            # completed table contest awaiting its title
    current = None           # titled contest still missing its Total
    contests = []

    def match_pending(tc):
        for i, (name, party) in enumerate(pending):
            first = tc.rows[0][0] if tc.rows else ''
            if first and first.split(', Precinct')[0] in name:
                del pending[i]
                return name, party
        return pending.popleft()

    for ev in events:
        kind = ev[0]
        if kind == 'title':
            _, name, party = ev
            if orphan is not None:
                orphan.title, orphan.party = name, party
                contests.append(orphan)
                orphan, current = None, orphan
                continue
            pending.append((name, party))
            continue
        tc = ev[1]
        if tc.header is None:
            # continuation fragment (no header row)
            if current is not None and current.totals is None:
                for label, vals in tc.rows:
                    current.add_row(label, vals)
                if tc.totals is not None:
                    current.totals = tc.totals
            else:
                problems.append(f'{tc.rows[:1]!r}: fragment with no open '
                                f'contest')
            continue
        if pending:
            name, party = match_pending(tc)
            tc.title, tc.party = name, party
            contests.append(tc)
            current = tc
        elif current is not None and current.totals is None:
            # page-break continuation that repeats the header row
            for label, vals in tc.rows:
                current.add_row(label, vals)
            if tc.totals is not None:
                current.totals = tc.totals
        else:
            labels = {lbl for lbl, _ in tc.rows}
            if current is not None and labels and \
                    labels <= {lbl for lbl, _ in current.rows}:
                print(f'NOTE: dropping duplicate re-print of '
                      f'{current.title!r}')
                continue
            orphan = tc
            current = None
    if orphan is not None:
        contests.append(orphan)

    districts = county_districts(COUNTY)
    rows = []
    for c in contests:
        manual = MANUAL_TOTALS.get((c.title, c.party))
        if manual is not None:
            c.totals = [str(v) for v in manual]
        if c.totals is None:
            problems.append(f'{c.title!r}: no Total row')
            continue
        parsed = parse_contest(c, problems)
        if parsed is None:
            continue
        header, crows, _sums = parsed
        office, district = map_office(c.title, districts, problems)
        for label, vals in crows:
            for ci, cand in enumerate(header):
                v = vals[ci]
                v = v[0] if isinstance(v, list) else v
                if cand == 'Write-in':
                    cand = 'Write-In'
                if not v:
                    continue
                rows.append({'county': COUNTY, 'precinct': label,
                             'office': office, 'district': district,
                             'party': c.party, 'candidate': cand,
                             'votes': v})

    for p in problems:
        print('PROBLEM:', p)
    if problems:
        sys.exit(f'{COUNTY}: {len(problems)} problems')
    write_csv(COUNTY, rows)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default=DEFAULT_CACHE)
    parse(ap.parse_args().cache)