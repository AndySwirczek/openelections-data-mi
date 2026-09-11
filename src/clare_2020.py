"""Parse the Clare County Aug 2020 primary "Official Results" PDF via cached
PaddleOCR markdown.

Source (openelections-sources-mi/2020/primary): 'Clare MI precinct report.pdf',
32 pages, image-only (no text layer), OCR'd with PaddleOCR-VL
(src/fetch_paddleocr_md.py, per-page markdown cached in /tmp/paddleocr_md).

The report is contest-major: each contest is one HTML table (Precinct column,
one column per candidate / Yes-No, a Total row) preceded by its title line.
The City of Clare straddles Clare and Isabella counties, so the report also
carries Isabella County contests — those report only the few Isabella-side
voters of 'City of Clare, Precinct 1' and are EXCLUDED here (they belong in
Isabella County's file); every exclusion is printed as a NOTE.

OCR artifacts handled:
- tables duplicated wholesale after their Total row (data rows + Total repeat);
- tables split across pages (continuation has no header and ends with Total);
- empty stub rows/tables under a title whose data is on the next page;
- Total cells with glued digits ('4456 4,455', '41214,122', '****221') —
  accepted when the column sum appears inside the cell's digits;
- garbage data cells ('$ ^{{***}} $71 70', '147 146') resolved against the
  column Total, else listed as problems for a MANUAL_CELLS entry.

Convention: party from the title ((DEM)/(REP)); 'Write-in' columns become
candidate 'Write-In' (Kalamazoo precedent); declared write-ins print as bare
names with no '- party' suffix and keep their name; zero-vote rows are
skipped; no turnout columns exist in this source (no pseudo rows).

Usage:
    .venv/bin/python src/clare_2020.py \
        [--cache /tmp/paddleocr_md/Clare_MI_precinct_report]
"""
import argparse
import os
import re
import sys
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import county_districts, write_csv  # noqa: E402

COUNTY = 'Clare'
DEFAULT_CACHE = '/tmp/paddleocr_md/Clare_MI_precinct_report'

TITLE_RE = re.compile(
    r'^(?P<name>.+?)\s*(?:\((?P<party>DEM|REP)\))?\s*'
    r'\(Vote for (?P<n>\d+)\)$')
# contests that belong to Isabella County's file, not Clare's
EXCLUDE = ('Isabella', 'Renewal of Fixed Operational Millage')
# Dual-printed recount cells: the Board of Canvassers' determined value is
# printed bold BEFORE the original value ('***71 70', '147 146', '137 135');
# OCR reads both numbers into one cell. Resolved by hand from the rendered
# page images: {(contest title, precinct label, column header): value}
MANUAL_CELLS = {
    ('County Commissioner District 6', 'Greenwood Township, Precinct 1',
     'David A. Hoefling - REP'): 71,
    ('Road Commissioner 4 year term for Clare County',
     'Hamilton Township, Precinct 1', 'Bill Simpson - REP'): 147,
    ('Hamilton Township Trustee', 'Hamilton Township, Precinct 1',
     'Michael lutzi - REP'): 147,
    ('Clare County Central Dispatch Proposition for 9-1-1 System Funding',
     'Greenwood Township, Precinct 1', 'No'): 137,
    ('Clare County Animal Control Services Millage Proposal',
     'Greenwood Township, Precinct 1', 'Yes'): 204,
    ('Clare County Animal Control Services Millage Proposal',
     'Greenwood Township, Precinct 1', 'No'): 144,
}
# Total rows the OCR mangled by splitting the dual-printed cell ('***221 220'
# / '2171 2,172') into extra cells and shifting the rest; values confirmed
# against the page images and the column sums.
MANUAL_TOTALS = {
    'County Commissioner District 6': [223, 221, 104, 169, 3],
    'Road Commissioner 4 year term for Clare County': [2027, 2171, 32],
}
# 'Franklin Township Trustee (REP) (Vote for 2)' — its title line was dropped
# by the OCR; the table is recognized by its header row.
MANUAL_HEADERS = {
    ('Jacqueline Ecklin - REP', 'Write-in'):
        ('Franklin Township Trustee', 'REP'),
}
TITLE_FIXES = {
    'Lindoln Township Clerk': 'Lincoln Township Clerk',
    'Surrey Township Supervisors': 'Surrey Township Supervisor',
    # OCR read 'Precinct 1. Delegate' / 'Precinct 1, Delegate'
}


def norm_title(t):
    t = re.sub(r'^#+\s*', '', t.strip())
    t = re.sub(r'^<div[^>]*>', '', t)
    t = re.sub(r'</div>$', '', t).strip()
    t = t.replace('{DEM}', '(DEM)').replace('{REP}', '(REP)')
    t = t.replace('Vote for_', 'Vote for ')
    return re.sub(r'\s+', ' ', t)


def cells(row):
    return [c.strip() for c in re.findall(r'>([^<]*)</td>', row)]


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
    """Validate one contest, resolving garbage cells against column sums."""
    manual = MANUAL_TOTALS.get(c.title)
    if manual is not None:
        return c.header, c.rows, manual
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
        # glued dual-printed totals ('41214,122' = '4,121' + OCR echo):
        # accept when the column sum appears inside the cell's digits
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


def parse(cache):
    problems = []
    notes = []
    contests = []
    current = None

    def close(c):
        if c is not None:
            contests.append(c)

    for page in sorted(os.listdir(cache)):
        if not re.fullmatch(r'p\d+\.md', page):
            continue
        text = open(os.path.join(cache, page)).read()
        for part in re.split(r'(<table.*?</table>)', text, flags=re.S):
            if part.startswith('<table'):
                if current is None:
                    continue  # header/log-stamp table before any title
                for row in re.findall(r'<tr>(.*?)</tr>', part, flags=re.S):
                    cs = cells(row)
                    if not any(cs):
                        continue  # empty stub row
                    if cs[0] == 'Precinct':
                        sig = tuple(cs[1:])
                        if sig in MANUAL_HEADERS:
                            # the OCR dropped this table's title line
                            close(current)
                            name, party = MANUAL_HEADERS[sig]
                            current = Contest(name, party)
                        if current.header is None:
                            current.header = cs[1:]
                        continue
                    if cs[0] == 'Total':
                        vals = cs[1:]
                        if current.totals is not None:
                            if vals != current.totals:
                                problems.append(
                                    f'{current.title!r}: two Total rows '
                                    f'{current.totals} vs {vals}')
                        else:
                            current.totals = vals
                        continue
                    vals = []
                    for ci, cell in enumerate(cs[1:]):
                        d = cell.replace(',', '').replace('*', '') \
                                .replace('$', '')
                        if re.fullmatch(r'\d+', d):
                            vals.append(int(d))
                        else:
                            nums = embedded_ints(cell)
                            if not nums:
                                nums = [0]
                            vals.append(nums if len(nums) > 1 else nums[0])
                        # dual-printed recount cells: the canvassers'
                        # determined value, read by hand from the page image
                        hdr = current.header[ci] if current.header else None
                        manual = MANUAL_CELLS.get(
                            (current.title, ' '.join(cs[0].split()), hdr))
                        if manual is not None:
                            vals[-1] = manual
                    try:
                        current.add_row(' '.join(cs[0].split()), vals)
                    except ValueError as e:
                        problems.append(f'{current.title!r}: {e}')
                continue
            for line in part.strip().splitlines():
                t = norm_title(line)
                if not t or t.startswith('<img') or '<table' in t:
                    continue
                if t.startswith(('#', '2020-', '**', '***', '$', '大')) or \
                        'Official Results' in t:
                    continue
                m = TITLE_RE.match(t)
                if not m:
                    problems.append(f'{page}: unrecognized line {t!r}')
                    continue
                name = ' '.join(m.group('name').rstrip('.,').split())
                party = m.group('party') or ''
                if any(x in name for x in EXCLUDE):
                    notes.append(f'excluded (Isabella County contest): '
                                 f'{name}')
                    current = None
                    continue
                name = TITLE_FIXES.get(name, name)
                close(current)
                current = Contest(name, party)
    close(current)

    districts = county_districts(COUNTY)
    rows = []
    for c in contests:
        if c.totals is None:
            problems.append(f'{c.title!r}: no Total row')
            continue
        parsed = parse_contest(c, problems)
        if parsed is None:
            continue
        header, crows, _sums = parsed
        # office / district
        name = c.title
        district = ''
        m = re.match(r'Representative in State Legislature '
                     r'(\d+)(?:st|nd|rd|th) District$', name)
        if m:
            office, district = 'State House', m.group(1)
        elif name == 'Representative in Congress':
            office, district = 'U.S. House', districts.get('U.S. House', '')
        elif name == 'United States Senator':
            office = 'U.S. Senate'
        elif m := re.match(r'County Commissioner District (\d+)$', name):
            office, district = 'County Commissioner', m.group(1)
        elif name.endswith(', Delegate'):
            office = f'{name[:-len(", Delegate")]} ' \
                     f'Delegate to County Convention'
        else:
            office = re.sub(r' for Clare County$', '', name)
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

    for n in notes:
        print('NOTE', n)
    for p in problems:
        print('PROBLEM:', p)
    if problems:
        sys.exit(f'{COUNTY}: {len(problems)} problems')
    write_csv(COUNTY, rows)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default=DEFAULT_CACHE)
    parse(ap.parse_args().cache)