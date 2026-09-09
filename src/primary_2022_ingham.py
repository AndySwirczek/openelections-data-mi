"""Parse Ingham County's Aug 2022 primary "OFFICIAL Precinct Results" PDF
into a per-county precinct CSV, verified against the certified
county-level CENR.

Layout: each report page ('Page: N of 81', one report page per contest)
spans several PDF pages, every one repeating the same two-table header:
the registration table ('Times Cast' / 'Registered Voters') on the left
and the contest table on the right. Both tables' candidate headers are
vertical stacks whose rotated letters read bottom-to-top and sit at a
shared x0 per word, so each header word is rebuilt from the chars at its
x0 (top order, reversed) and adjacent word stacks (gaps up to 14pt) merge
into one header band (mid-word wraps like 'Muhamma' / 'd Salman' join
without a space when the next stack starts lowercase). Precinct blocks
print label lines, then 'Election Day' / 'AV Counting Board' / 'Total'
rows; only the 'Total' rows are emitted, and a block may split across PDF
pages. A contest's final PDF page carries an 'Ingham County - Total' row
with the countywide totals. Qualified write-ins print as named
'<name> (Write-In)' columns (excluded from 'Total Votes'); the CENR
carries their names plain.

Usage:
    .venv/bin/python src/primary_2022_ingham.py <source.pdf> \
        --out /tmp/ingham22.csv
"""
import argparse
import collections
import re

import pdfplumber

from primary_2022_common import CENR, verify, write

COUNTY = 'Ingham'
# page 128 (State House 77 REP) prints no '(Vote for 1)' suffix
TITLE = re.compile(r'^(.*\S) \((DEM|REP)\)(?: \(Vote for \d+\))?$')
KEEP = [
    (re.compile(r'^Governor$'), 'Governor'),
    (re.compile(r'^Representative in Congress CD (\d+)$'), 'U.S. House'),
    (re.compile(r'^State Senator District (\d+)$'), 'State Senate'),
    (re.compile(r'^Representative in State Legislature District (\d+)$'),
     'State House'),
]
NUM = re.compile(r'^\d[\d,]*$')
TOTAL_KEY = 'Total Votes'
WRITEIN_BAND = re.compile(r'\s*\((?:Write-? ?In)\)$', re.I)
LUMPED = 'Unresolved Write-In'
# label-zone lines that are page furniture, not precinct labels
JUNK_LINE = re.compile(r'^(?:Page:.*|County$|Ingham County$|Precinct$)')
ROW_TYPES = {'Election Day': 'ED', 'AV Counting Board': 'AV', 'Total': 'T',
             'County - Total': 'CT', 'Ingham County - Total': 'CT'}
LEFT_BANDS = ('Times Cast', 'Registered Voters')
# names the report abbreviates against the CENR
NAME_FIX = {'Ricky Salisbury': 'Ricky John Salisbury'}
# Source-internal discrepancies, confirmed by text-layer extraction: the
# precinct rows of these named write-in columns sum 3 and 1 above the
# source's own printed county-total rows (every individual precinct cell
# matches the source, and every non-write-in column matches exactly; the
# CENR agrees with the printed county totals). The printed precinct rows
# are kept as-is. (Wayne-parser allowlist precedent.)
ALLOWED_WRITEIN = {
    ('Governor', '', 'REP', 'James Elmer Craig'):
        'source precinct rows sum 209 vs printed county total 208 (+1)',
    ('U.S. House', '7', 'REP', 'Jake Hagg'):
        'source precinct rows sum 681 vs printed county total 678 (+3)',
}


def map_office(title):
    for pat, name in KEEP:
        m = pat.match(title)
        if m:
            return name, (m.group(1) if pat.groups else '')
    return None, ''


def one_space(s):
    return re.sub(r'\s+', ' ', s).strip()


def cmp_name(text):
    """CENR comparison form: commas dropped (the report prints
    'Albert L. Kelley, Jr.' where the CENR carries 'Albert L. Kelley
    Jr.')."""
    return re.sub(r'\s*\((?:Write-? ?In|DEM|REP)\)$', '',
                  text.replace(',', ''))


def band_name(text):
    """Candidate name from a header band."""
    name = one_space(cmp_name(text))
    return NAME_FIX.get(name, name)


class Contest:
    def __init__(self, title, party, problems):
        self.title, self.party = title, party
        self.office, self.district = map_office(title)
        self.headers = None   # right-band texts, in column order
        self.rows = {}        # label -> {band text: value}
        self.totals = {}
        self.problems = problems

    def set_headers(self, texts):
        if self.headers is None:
            self.headers = texts
        elif self.headers != texts:
            self.problems.append(f'{self.title}: headers changed: '
                                 f'{texts} != {self.headers}')
            return False
        return True

    def add_block(self, label, cells, problems, pageno):
        if not label:
            problems.append(f'{self.title} page {pageno}: block has no '
                            f'precinct label')
            return
        if label in self.rows:
            if self.rows[label] != cells:
                problems.append(f'{self.title}: {label!r}: rows disagree: '
                                f'{self.rows[label]} vs {cells}')
            return
        self.rows[label] = cells

    def finish(self, problems):
        if self.office is None or not self.headers:
            return
        key = (self.office, self.district, self.party)
        cands = [c for (o, d, p, c) in CENR[COUNTY] if (o, d, p) == key]
        cvotes = {c: v for (o, d, p, c), v in CENR[COUNTY].items()
                  if (o, d, p) == key}
        band_cand = {}
        for h in self.headers:
            if h == TOTAL_KEY:
                continue
            name = band_name(h)
            if not any(cmp_name(c) == name for c in cands):
                problems.append(f'{self.title}: header column {h!r} is not '
                                f'a CENR candidate')
            else:
                band_cand[h] = next(c for c in cands
                                    if cmp_name(c) == name)
        missing = [c for c in cands
                   if c not in {n for n in band_cand.values()}]
        if any(cvotes[c] for c in missing):
            problems.append(f'{self.title}: CENR candidates absent from the '
                            f'header: {missing}')
        for label, cells in self.rows.items():
            total = cells.get(TOTAL_KEY)
            core = sum(v for h, v in cells.items() if h in band_cand
                       and not WRITEIN_BAND.search(h))
            if total is None or (core != total and
                                 core + sum(v for h, v in cells.items()
                                            if h in band_cand
                                            and WRITEIN_BAND.search(h))
                                 != total):
                problems.append(f'{self.title}: {label!r}: candidate sum '
                                f'{core} != Total Votes {total}')
                continue
            for name in cands:
                for h, n in band_cand.items():
                    if n == name:
                        rows.append((label, self.office, self.district,
                                     self.party, name, cells[h]))
            write_ins[key] += cells.get(LUMPED, 0)
        # county totals must equal the sums of the joined rows
        sums = collections.Counter()
        for cells in self.rows.values():
            for h, v in cells.items():
                sums[h] += v
        for h, printed in self.totals.items():
            if sums.get(h, 0) != printed:
                key = None
                if WRITEIN_BAND.search(h) and h in band_cand:
                    key = (self.office, self.district, self.party,
                           band_cand[h])
                if key in ALLOWED_WRITEIN:
                    print(f'NOTE {self.title}: {h} precinct sum '
                          f'{sums.get(h, 0)} vs printed county total '
                          f'{printed} — {ALLOWED_WRITEIN[key]}')
                else:
                    problems.append(f'{self.title}: {h} precinct sum '
                                    f'{sums.get(h, 0)} != printed county '
                                    f'total {printed}')


rows = []
write_ins = collections.defaultdict(int)


def num_rows(words):
    """[(top, [words])] numeric words grouped by top (the two tables' rows
    can sit 1pt apart)."""
    out = []
    for w in sorted((w for w in words if NUM.match(w['text'])
                     and w['top'] >= 40),
                    key=lambda w: (w['top'], w['x0'])):
        if out and w['top'] - out[-1][0] <= 2.5:
            out[-1][1].append(w)
        else:
            out.append([w['top'], [w]])
    return out


def stacks_for(page, ymax):
    """Vertical header stacks: chars sharing an x0, tops increasing, gaps
    up to 8pt (one data-row digit per row is 7pt+ apart, but data digits
    sit at or below the first data row; digits on one row share a top)."""
    groups = collections.defaultdict(list)
    for c in page.chars:
        if 40 < c['top'] < ymax:
            groups[round(c['x0'])].append(c)
    out = []
    for x, cs in groups.items():
        cs.sort(key=lambda c: c['top'])
        run = []
        for c in cs:
            if run and not (0.5 < c['top'] - run[-1]['top'] <= 8):
                if len(run) >= 3:
                    out.append((x, run))
                run = []
            run.append(c)
        if len(run) >= 3:
            out.append((x, run))
    return out


def bands_for(page, ymax):
    """(left_bands, right_bands, stack_bottom) with bands as [(max x1,
    text)] from header stacks. Adjacent word stacks merge when their x0
    gap is at most the page's modal wrap pitch plus 1.5pt: within one
    header cell the wrapped words sit a fixed pitch apart (9pt squeezed,
    13pt roomy), and that modal pitch is always below the smallest
    cross-column gap (11pt on the tightest page)."""
    st = sorted(stacks_for(page, ymax), key=lambda s: s[0])
    stacks = [[x, max(c['x1'] for c in run),
               ''.join(c['text'] for c in run)[::-1].strip(),
               min(c['top'] for c in run), max(c['top'] for c in run)]
              for x, run in st]
    stacks = [s for s in stacks if s[2]]
    # a mid-word wrap can leave a lone letter ('Tsernoglo' / 'u') in its
    # own short column; header-zone leftover chars outside the >=3-char
    # stacks join the merge as short stacks unless they sit on a
    # horizontal line with a neighbour (title and 'Precinct' fragments)
    used = {id(c) for _, run in st for c in run}
    leftover = collections.defaultdict(list)
    for c in page.chars:
        if 40 < c['top'] < ymax and id(c) not in used:
            leftover[round(c['x0'])].append(c)
    shorts = []
    for x, cs in sorted(leftover.items()):
        cs.sort(key=lambda c: c['top'])
        text = ''.join(c['text'] for c in cs)[::-1].strip()
        if text:
            shorts.append([x, max(c['x1'] for c in cs), text,
                           min(c['top'] for c in cs),
                           max(c['top'] for c in cs)])
    gaps = [b[0] - a[0] for a, b in zip(stacks, stacks[1:])]
    mode = (max(sorted(collections.Counter(
                round(g) for g in gaps).items()),
            key=lambda kv: (kv[1], -kv[0]))[0]
            if gaps else 12)
    threshold = mode + 1.5
    for s in shorts:
        if any(t is not s and abs(t[0] - s[0]) <= 4 and
               t[3] <= s[4] and s[3] <= t[4] for t in shorts):
            continue  # part of a horizontal line
        # keep it only when sandwiched between two stacks, both within
        # the merge threshold (the lone 'u' of 'Tsernoglou')
        if (any(s[0] - t[0] <= threshold for t in stacks if t[0] < s[0]) and
                any(t[0] - s[0] <= threshold for t in stacks if t[0] > s[0])):
            stacks.append(s)
    stacks.sort(key=lambda s: s[0])
    out = []
    for x0, edge, text, *_ in stacks:
        if out and x0 - out[-1][0] <= threshold:
            # a mid-word wrap ('Muhamma' / 'd Salman') joins without a
            # space when the previous stack ends in a lowercase letter
            prev = out[-1][2]
            joiner = '' if (prev and prev[-1].isalpha() and text
                            and text[0].islower()) else ' '
            out[-1][0] = x0
            out[-1][1] = max(out[-1][1], edge)
            out[-1][2] += joiner + text
        else:
            out.append([x0, edge, text])
    left, right = [], []
    for x0, edge, text in out:
        text = one_space(text)
        (left if text in LEFT_BANDS else right).append((edge, text))
    bottom = max((c['bottom'] for x, run in st for c in run), default=0)
    return left, right, bottom


def collect_label(words, zone_lo, zone_hi, gap_lo, gap_hi):
    """Precinct label lines between the tables in a vertical window."""
    lines = collections.defaultdict(list)
    for w in words:
        if zone_lo < w['x1'] < zone_hi and gap_lo < w['top'] < gap_hi:
            lines[round(w['top'])].append(w)
    out = []
    for ltop, ls in sorted(lines.items()):
        text = one_space(' '.join(w['text'] for w in sorted(
            ls, key=lambda w: w['x0'])))
        if JUNK_LINE.match(text):
            continue
        out.append(text)
    return ' '.join(out)


def process_page(page, contest, problems, pageno, carry, pending):
    """Parse one page's table into contest rows; `carry` is the open block
    spanning pages."""
    if contest is None:
        return  # registration pages and local contests
    words = page.extract_words()
    nrows = num_rows(words)
    # the header stacks sit above the first numeric row that holds table
    # data (x0 >= 120: the left table's first column)
    data_rows = [r for r in nrows
                 if any(w['x0'] >= 120 for w in r[1])]
    if not data_rows:
        return
    ymax = data_rows[0][0] - 1
    left_bands, right_bands, stack_bottom = bands_for(page, ymax)
    if not right_bands:
        problems.append(f'page {pageno}: no candidate headers found')
        return
    # columns: numeric words clustered by right edge; each header band
    # claims the unclaimed cluster nearest its edge (label digits stay
    # unclaimed)
    numw = sorted((w for w in words if NUM.match(w['text'])
                   and w['top'] >= 40), key=lambda w: w['x1'])
    cols = []
    for w in numw:
        if cols and w['x1'] - cols[-1][0] <= 3:
            cols[-1][1].append(w)
        else:
            cols.append([w['x1'], [w]])
    claimed = {}
    for edge, text in sorted(left_bands + right_bands):
        best, bd = None, 40
        for i, (x1, _) in enumerate(cols):
            if i in claimed:
                continue
            d = abs(x1 - edge)
            if d < bd:
                best, bd = i, d
        if best is None:
            problems.append(f'page {pageno}: no data column for header '
                            f'{text!r}')
            return
        claimed[best] = text
    if contest is not None and not contest.set_headers(
            [t for _, t in sorted((cols[i][0], t)
                                  for i, t in claimed.items()
                                  if t not in LEFT_BANDS)]):
        return
    left_first = min((cols[i][0] for i, t in claimed.items()
                      if t in LEFT_BANDS), default=None)
    left_last = max((cols[i][1][0]['x1'] for i, t in claimed.items()
                     if t in LEFT_BANDS), default=None)
    right_first = min((cols[i][1][0]['x0'] for i, t in claimed.items()
                       if t not in LEFT_BANDS), default=None)
    zone_lo = left_last + 1.5 if left_last is not None else 0
    zone_hi = right_first - 3
    # walk the rows; label lines sit between a block's Total row and its
    # Election Day row, between the two tables
    last_t_top = None
    for ri, (top, g) in enumerate(nrows):
        cells = {}
        for i, text in claimed.items():
            if text in LEFT_BANDS:
                continue
            w = next((w for w in cols[i][1] if abs(w['top'] - top) <= 2.5),
                     None)
            if w is not None:
                cells[text] = int(w['text'].replace(',', ''))
        if not cells:
            continue  # a label line's digits, or page furniture
        if left_first is not None:
            tw = [w for w in words if w['x1'] < left_first - 3
                  and not NUM.match(w['text']) and abs(w['top'] - top) <= 3]
        else:
            tw = [w for w in words if w['x1'] < right_first - 3
                  and not NUM.match(w['text']) and abs(w['top'] - top) <= 3]
        typ = one_space(' '.join(w['text'] for w in sorted(tw, key=lambda
                                                          w: w['x0'])))
        rtype = ROW_TYPES.get(typ)
        if rtype is None:
            problems.append(f'page {pageno}: unknown row label {typ!r}')
            continue
        if rtype == 'CT':
            for text, v in cells.items():
                if text in contest.totals and contest.totals[text] != v:
                    problems.append(f'{contest.title}: county total {text} '
                                    f'printed {contest.totals[text]} and {v}')
                contest.totals[text] = v
            last_t_top = top
            continue
        if rtype == 'ED':
            gap_lo = last_t_top + 3 if last_t_top is not None \
                else stack_bottom + 1
            label = collect_label(words, zone_lo, zone_hi, gap_lo, top - 2)
            # a block whose label printed at the foot of the previous page
            if not label and pending.get('label'):
                label = pending['label']
                pending['label'] = None
            if carry.get('total') is None and carry.get('label') is not None:
                problems.append(f'page {pageno}: block {carry["label"]!r} '
                                f'has no Total row')
            carry['label'] = label
            carry['ed'] = cells
            carry['av'] = None
            carry['total'] = None
        elif rtype == 'AV':
            if carry.get('ed') is None:
                problems.append(f'page {pageno}: AV row with no open block')
                continue
            if carry.get('av') is not None and carry['av'] != cells:
                problems.append(f'page {pageno}: AV rows disagree for '
                                f'{carry["label"]!r}')
            carry['av'] = cells
        else:  # 'T'
            if carry.get('ed') is None:
                problems.append(f'page {pageno}: Total row with no open '
                                f'block')
                continue
            if carry.get('av') is not None:
                for text, v in cells.items():
                    if carry['ed'].get(text, 0) + carry['av'].get(text, 0) \
                            != v:
                        problems.append(f'page {pageno}: '
                                        f'{carry["label"]!r}: {text} ED+AV '
                                        f'{carry["ed"].get(text, 0)}+'
                                        f'{carry["av"].get(text, 0)} != '
                                        f'Total {v}')
            carry['total'] = cells
            contest.add_block(carry['label'], cells, problems, pageno)
            carry['label'] = carry['ed'] = carry['av'] = None
            last_t_top = top
    # label lines printed after the last Total row belong to a block whose
    # rows carry onto the next page
    if last_t_top is not None:
        pending['label'] = collect_label(
            words, zone_lo, zone_hi, last_t_top + 3, 10000) or None
    else:
        pending['label'] = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    problems = []
    contest = None
    carry = {}
    pending = {}

    with pdfplumber.open(args.pdf) as pdf:
        for pageno, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ''
            m = None
            for line in (l.strip() for l in text.splitlines()):
                if re.match(r'^Page:', line):
                    continue
                m = TITLE.match(line)
                if m:
                    break
            if m:
                if contest is not None and (contest.title != m.group(1)
                                            or contest.party != m.group(2)):
                    contest.finish(problems)
                    contest = None
                if contest is None:
                    contest = Contest(m.group(1), m.group(2), problems)
                    pending['label'] = None
            elif contest is not None:
                contest.finish(problems)
                contest = None
            process_page(page, contest, problems, pageno, carry, pending)
    if contest is not None:
        contest.finish(problems)
    if carry.get('total') is None and carry.get('label') is not None:
        problems.append(f'final block {carry["label"]!r} has no Total row')

    for p in problems:
        print('PROBLEM:', p)
    print(f'{len(rows)} rows; {len(problems)} parser problems')
    blocking = verify(COUNTY, rows, write_ins, ALLOWED_WRITEIN)
    for p in blocking:
        print('PROBLEM:', p)
    if not problems and not blocking:
        write(COUNTY, rows)


if __name__ == '__main__':
    main()