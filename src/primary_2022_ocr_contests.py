"""Shared parser for 2022 primary counties whose PaddleOCR markdown is
contest-major HTML tables: a heading line ('<contest> (DEM) (Vote for 1)')
followed by a table whose header row reads 'Precinct | <candidate> | ...
| Write-in', then one row per precinct and a 'Total' row. The contest's
table may split across OCR pages; the header row prints only once, so
continuation chunks are header-less and inherit the contest's columns
(chunk width must match), and a chunk's 'Total' row can arrive as a
separate single-row table on the next page. Every contest-level total
row must equal the sum of the contest's precinct rows, candidate names
must exist in the certified county-level CENR, and the parsed sums are
verified against that CENR via primary_2022_common.verify().

Usage:
    .venv/bin/python src/primary_2022_ocr_contests.py <County> \
        [--write]
"""
import argparse
import collections
import glob
import html
import re

from primary_2022_common import CENR, classify, one_space, verify, write

CACHE = '/tmp/paddleocr_md'
COUNTIES = {
    'Antrim': 'Antrim',
    'Clare': 'Clare_County_Aug_2022_Primary_Precinct_Results',
    'Dickinson': 'Dickinson_MI_aug_02_2022_official',
    'Iron': 'Iron_MI_Official_Results_Per_Precinct_08_02_22',
    'Mecosta': 'Mecosta_MI_Aug_2022_Statement_of_Votes_Cast',
    'Tuscola': 'Tuscola_MI_August_2022_Results_Per_Precinct_as_Certified',
}
# pseudo-office columns in table headers, not candidates
SKIP_COLS = {'Times Cast', 'Registered Voters', 'Total Votes', 'Cards Cast',
             'Voters Cast', '% Turnout'}
WRITEIN_COL = re.compile(r'write.?in', re.I)
TOTAL_LABEL = re.compile(r'\bTotal\s*$', re.I)
PARTY_TAIL = re.compile(r'\s*[-–]\s*(?:DEM|REP)\s*$|\s*\((?:DEM|REP)\)\s*$',
                        re.I)
NUM = re.compile(r'^\d[\d,]*$')
WRITEIN_TOKEN = '<write-in>'
SKIP_TOKEN = '<skip>'
# OCR-garbled rows and totals, keyed by (office, district, party); row
# values were read off the source PDF images (the scan's cell borders
# OCR as extra leading 'I' on labels and trailing '1' on numbers)
MANUAL = {
    'Tuscola': {
        ('State Senate', '25', 'REP'): {
            'rows': {'Wells Township, Precinct 1':
                     ({'Daniel V. Lauwers': 203}, 4)},
            'total': {'Daniel V. Lauwers': 2785, WRITEIN_TOKEN: 31},
        },
    },
}
# OCR-garbled candidate names in table headers
NAME_FIX = {
    'Mecosta': {'Roger Huck': 'Roger Hauck'},
}


def cells_of(tr):
    return [html.unescape(one_space(re.sub(r'<[^>]+>', '', c)))
            for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]


def clean_cand(name):
    """CENR comparison form of a header cell (a wrapped name can leave a
    trailing hyphen; write-in names can carry LaTeX-junk asterisks)."""
    name = re.sub(r'\$[^$]*\$', '', name)
    return one_space(PARTY_TAIL.sub('', name).strip().rstrip('- '))


def clean_heading(text, county):
    """The heading may print the county name after the party tag ('State
    Senator 25th District (REP), Tuscola County (Vote for 1)') or repeat
    the party as a bare token ('Governor (DEM) (Vote for 1) DEM'), both
    of which break the classify() party match."""
    text = re.sub(r'\s+(?:DEM|REP)\s*$', '', text)
    return one_space(re.sub(rf'[.,]?\s*{county}\s+County\s*', ' ', text,
                            flags=re.I))


def parse_pages(cache):
    """[(kind, payload)] in page order; a table may be unterminated (OCR
    dropped the closing tag) — it then ends at the next '<table' (or the
    page end), and the text lines between tables are kept as text."""
    items = []
    for path in sorted(glob.glob(f'{CACHE}/{cache}/p*.md')):
        md = open(path).read()
        pos = 0
        while pos < len(md):
            start = md.find('<table', pos)
            for line in md[pos:start if start >= 0 else len(md)].splitlines():
                text = one_space(re.sub(r'<[^>]+>', '', line).strip())
                if text:
                    items.append(('text', text))
            if start < 0:
                break
            nxt = md.find('<table', start + 1)
            end = md.find('</table>', start)
            if end < 0 or (nxt >= 0 and end > nxt):
                pos = nxt if nxt >= 0 else len(md)
                seg = md[start:pos]
            else:
                pos = end + len('</table>')
                seg = md[start:pos]
            items.append(('table',
                          [cells_of(tr) for tr in
                           re.findall(r'<tr>(.*?)</tr>', seg, re.S)]))
    return items


class Contest:
    def __init__(self, key):
        self.key = key
        self.headers = {}      # numeric-column index -> header token
        self.cands = {}        # numeric-column index -> candidate name
        self.rows = []         # (label, {name: votes}, {writein: votes})
        self.totals = []
        self.finished = False


def row_cells(row, headers, key, label, problems):
    """(candidate votes, write-in votes) from a data row."""
    cand, writein = {}, 0
    for i, token in sorted(headers.items()):
        if 1 <= i < len(row) and NUM.match(row[i] or ''):
            v = int(row[i].replace(',', ''))
            if token == WRITEIN_TOKEN:
                writein += v
            elif token != SKIP_TOKEN:
                cand[token] = v
        elif 1 <= i < len(row) and (row[i] or '').strip() \
                and token not in (SKIP_TOKEN, WRITEIN_TOKEN) \
                and key is not None:
            problems.append(f'{key}: {label!r}: {token} cell '
                            f'{row[i]!r} is not a number')
    return cand, writein


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('county')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    county = args.county

    problems = []
    notes = []
    rows = []
    write_ins = collections.defaultdict(int)
    open_contest = None
    pending_key = None      # classify() of the heading before a table

    def finish(contest):
        if contest is None or contest.finished:
            return
        contest.finished = True
        if contest.key is None or not contest.headers:
            return
        if not contest.rows:
            problems.append(f'{contest.key}: contest has no precinct rows')
            return
        manual = MANUAL.get(county, {}).get(contest.key, {})
        for ri, (label, cells, w) in enumerate(contest.rows):
            for mlabel, (mcells, mw) in manual.get('rows', {}).items():
                if label.endswith(mlabel):
                    contest.rows[ri] = (mlabel, dict(mcells), mw)
                    break
        sums = collections.Counter()
        for _, cells, w in contest.rows:
            for name, v in cells.items():
                sums[name] += v
            sums[WRITEIN_TOKEN] += w
        totals = contest.totals
        if manual.get('total'):
            totals = [dict(manual['total'])]
        if totals:
            for total in totals:
                for name, v in total.items():
                    if sums.get(name, 0) != v:
                        problems.append(f'{contest.key}: {name} precinct sum '
                                        f'{sums.get(name, 0)} != printed '
                                        f'Total {v}')
            if len({tuple(sorted(t.items())) for t in totals}) > 1:
                problems.append(f'{contest.key}: printed Total rows disagree')
        key = contest.key
        for label, cells, w in contest.rows:
            for name, v in cells.items():
                rows.append((label, key[0], key[1], key[2], name, v))
            if w:
                write_ins[key] += w

    for kind, payload in parse_pages(COUNTIES[county]):
        if kind == 'text':
            key = classify(clean_heading(payload, county))
            if key:
                finish(open_contest)
                open_contest = None
                pending_key = key
            continue
        chunk = payload
        if not chunk:
            continue
        first = chunk[0]
        # header row: starts 'Precinct' with no numeric cells after it
        if len(first) > 1 and one_space(first[0]) == 'Precinct' and \
                not any(NUM.match(c) for c in first[1:]):
            headers, cands = {}, {}
            for i, cell in enumerate(first[1:], 1):
                name = clean_cand(cell)
                name = NAME_FIX.get(county, {}).get(name, name)
                if WRITEIN_COL.search(name):
                    headers[i] = WRITEIN_TOKEN
                elif name in SKIP_COLS:
                    headers[i] = SKIP_TOKEN
                else:
                    headers[i] = name
                    cands[i] = name
            key = pending_key
            pending_key = None
            if key is None:
                # heading lost or unclassified: the candidate set may
                # name a unique CENR contest
                names = set(cands.values())
                contests = {}
                for (o, d, p, c) in CENR[county]:
                    contests.setdefault((o, d, p), {})[c] = \
                        CENR[county][(o, d, p, c)]
                hits = {k for k, kv in contests.items()
                        if names <= set(kv)
                        and {c for c, v in kv.items() if v} <= names}
                if len(hits) == 1:
                    key = hits.pop()
                    notes.append(f'{county}: heading lost; contest inferred '
                                 f'from header candidates: {key}')
                # else: a local contest's heading never classifies; its
                # table (and its continuation chunks) attach to a dummy
                # contest with key None and are skipped
            if open_contest is not None:
                if key is not None and open_contest.key == key:
                    # the same contest's header repeated on a later page
                    for i, name in cands.items():
                        if open_contest.cands.get(i) != name:
                            problems.append(f'{key}: headers disagree: '
                                            f'{name!r} vs '
                                            f'{open_contest.cands.get(i)!r}')
                    open_contest.cands.update(cands)
                    continue
                finish(open_contest)
                open_contest = None
            contest = Contest(key)
            contest.headers = headers
            contest.cands = dict(cands)
            if key is not None:
                # every candidate must exist in the CENR for this contest
                cenr_cands = {k[3] for k in CENR[county] if k[:3] == key}
                for name in set(cands.values()):
                    if name not in cenr_cands:
                        problems.append(f'{key}: header candidate {name!r} '
                                        f'is not a CENR candidate')
            open_contest = contest
            data = chunk[1:]
        else:
            lead = one_space(chunk[0][0])
            if not lead or lead in ('Times Cast', 'Candidate', 'Yes', 'No'):
                # a countywide summary table ('Candidate | Party | Total',
                # proposal 'Yes'/'No' tallies), not precinct data
                continue
            # continuation chunk of the open contest
            if open_contest is None:
                if not any(NUM.match(c) for row in chunk for c in row[1:]):
                    continue
                problems.append(f'rows with no open contest: {chunk[0][:1]}')
                continue
            data = chunk
        if not any(NUM.match(c) for row in data for c in row[1:]):
            continue
        headers = open_contest.headers
        width = max(headers) + 1 if headers else 0
        for row in data:
            if len(row) > width:
                if open_contest.key is not None:
                    problems.append(f'{open_contest.key}: row {row[:1]!r} '
                                    f'has {len(row)} cells, expected {width}')
                continue
            row = (list(row) + [''] * width)[:width]
            label = one_space(row[0])
            if re.match(r'Cumulative', label, re.I):
                continue   # source furniture (always zero)
            if TOTAL_LABEL.search(label):
                total, _ = row_cells(row, headers, open_contest.key, label,
                                     problems)
                open_contest.totals.append(total)
                continue
            cells, writein = row_cells(row, headers, open_contest.key, label,
                                       problems)
            if not cells and not writein:
                continue   # a registration or summary row
            if not re.search(r'\d', label):
                # only a problem for an emitted contest (OCR LaTeX-mangles
                # some labels; local contests are skipped anyway)
                if open_contest.key is not None:
                    problems.append(f'{open_contest.key}: label {label!r} '
                                    f'has no precinct number')
                continue
            open_contest.rows.append((label, cells, writein))
    finish(open_contest)

    for p in problems:
        print('PROBLEM:', p)
    for n in notes:
        print('NOTE:', n)
    print(f'{len(rows)} rows; {len(problems)} parser problems')
    blocking = verify(county, rows, write_ins)
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


if __name__ == '__main__':
    main()