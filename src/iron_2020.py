"""Parse the Iron County Aug 2020 primary "Results per Precinct" PDF via
cached PaddleOCR markdown.

Source (openelections-sources-mi/2020/primary):
'Iron MI 8-4-2020-Results-Per-Precinct.pdf', 16 pages, image-only (no text
layer), OCR'd with PaddleOCR-VL (src/fetch_paddleocr_md.py, per-page markdown
cached in /tmp/paddleocr_md).

The report is contest-major, in the same Board of Canvassers style as Clare:
each contest is one HTML table (Precinct column, one column per candidate /
Yes-No, a Total row of column sums) preceded by its title line in one of three
shapes ('## Title', '<div>Title</div>', bare line). 13 precincts. No turnout
columns exist in this source (no pseudo rows). Delegate tables are
single-precinct (each precinct votes only its own delegates) with a Total row.

OCR artifacts handled:
- tables split across pages (continuation has no header and ends with Total);
- empty stub rows/tables under a title whose data is on the next page;
- 'Township Supervisor for Mansfield Township (DEM)' table mangled across the
  p004/p005 break ('Manstield' / '-1' / 'To $^{+}$') — fixed via TEXT_FIXES,
  values confirmed against the rendered page image (Dryjanski 37, wi 0);
- the source prints 'City of Gaastra' but the OCR reads 'Gastra' every time;
  'County CLerk' and 'Precicnt 1' (delegate titles) are similar OCR garbles.

Office mapping: 'United States Senator for State Senate' -> U.S. Senate;
'Representative in Congress 1st District for State Rep' -> U.S. House 1;
'Representative in State Legislature 110th District for State' -> State House
110; 'County X for Iron County' -> bare county office (combined Clerk/Register
-> 'Clerk and Register of Deeds', the Clinton/Eaton/Kalamazoo 2020 style);
'County Commissioner for County Commissioner District N' -> County
Commissioner N; 'Township X for <Township>' -> '<Township> X'; 'Delegate to
County Convention for <precinct>' -> '<precinct> Delegate to County
Convention' (the Clare style).

Usage:
    .venv/bin/python src/iron_2020.py \
        [--cache /tmp/paddleocr_md/Iron_MI_8_4_2020_Results_Per_Precinct]
"""
import argparse
import html
import os
import re
import sys
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import county_districts, write_csv  # noqa: E402

COUNTY = 'Iron'
DEFAULT_CACHE = '/tmp/paddleocr_md/Iron_MI_8_4_2020_Results_Per_Precinct'

TITLE_RE = re.compile(
    r'^(?P<name>.+?)\s*(?:\((?P<party>DEM|REP)\))?\s*'
    r'\(Vote for (?P<n>\d+)\)$')
# 'Township Supervisor for Mansfield Township (DEM)': the table straddles the
# p004/p005 break and the OCR mangled its data row and Total row. Fixed on the
# raw page text before parsing; values confirmed against the page image.
TEXT_FIXES = {
    '>Manstield<': '>Mansfield Township, Precinct 1<',
    '>-1<': '>37<',
    '>To $ ^{+} $<': '>Total<',
}
TITLE_FIXES = {}
# 'County Commissioner for County Commissioner District 1 (DEM)': its title
# line sat at the very bottom of p003 (under the Road Commissioner table) and
# the OCR dropped it, leaving Patti A. Peretto's table orphaned on p004.
MANUAL_HEADERS = {
    ('Patti A. Peretto', 'Write-in'):
        ('County Commissioner for County Commissioner District 1', 'DEM'),
}
# 'County CLerk / Register of Deeds for Iron County' (DEM): its Total row
# ('Total | 80') is the first line of p003 in the source but the OCR dropped
# the fragment entirely; 80 = the sum of the 13 write-in values.
MANUAL_TOTALS = {
    ('County CLerk / Register of Deeds for Iron County', 'DEM'): [80],
}


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
    label = ' '.join(label.split())
    label = label.replace('Gastra', 'Gaastra')      # OCR drops an 'a'
    label = label.replace('Precicnt', 'Precinct')
    return label


def map_office(name, districts, problems):
    name = name.replace('Precicnt', 'Precinct')
    m = re.match(r'Representative in State Legislature '
                 r'(\d+)(?:st|nd|rd|th) District for State$', name)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'Representative in Congress (\d+)(?:st|nd|rd|th) District '
                 r'for State Rep$', name)
    if m:
        return 'U.S. House', m.group(1)
    if name == 'United States Senator for State Senate':
        return 'U.S. Senate', ''
    m = re.match(r'County Commissioner for County Commissioner District '
                 r'(\d+)$', name)
    if m:
        return 'County Commissioner', m.group(1)
    m = re.match(r'Delegate to County Convention for (.+)$', name)
    if m:
        return f'{fix_label(m.group(1))} Delegate to County Convention', ''
    m = re.match(r'Township (.+?) for (.+ Township)$', name)
    if m:
        return f'{m.group(2)} {m.group(1)}', ''
    if name == 'County CLerk / Register of Deeds for Iron County':
        return 'Clerk and Register of Deeds', ''
    m = re.match(r'County (.+?) for Iron County$', name)
    if m:
        return m.group(1), ''
    return name, ''


def parse(cache):
    problems = []
    contests = []
    current = None

    def close(c):
        if c is not None:
            contests.append(c)

    for page in sorted(os.listdir(cache)):
        if not re.fullmatch(r'p\d+\.md', page):
            continue
        text = open(os.path.join(cache, page)).read()
        for old, new in TEXT_FIXES.items():
            text = text.replace(old, new)
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
                            current.header = [fix_label(h) for h in cs[1:]]
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
                    for cell in cs[1:]:
                        d = cell.replace(',', '').replace('*', '') \
                                .replace('$', '')
                        if re.fullmatch(r'\d+', d):
                            vals.append(int(d))
                        else:
                            nums = embedded_ints(cell)
                            if not nums:
                                nums = [0]
                            vals.append(nums if len(nums) > 1 else nums[0])
                    try:
                        current.add_row(fix_label(cs[0]), vals)
                    except ValueError as e:
                        problems.append(f'{current.title!r}: {e}')
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
                name = TITLE_FIXES.get(name, name)
                close(current)
                current = Contest(name, party)
    close(current)

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