"""Parse Cheboygan County's Aug 2022 primary "Official Results" PDF into a
per-county precinct CSV, verified against the certified county-level CENR.

The PDF has three sections: the county canvasser's statement (county totals
only, pages 2-8), a "Results per Precinct" section (pages 9-30) and
"STATEMENT OF VOTES" certificates (pages 31+). Only the Results-per-Precinct
section carries precinct rows: contest-major blocks of a title line, a
'Precinct <candidates...> Write-in' column header, one row per precinct and a
'Total' row cross-checked against the precinct sums.

The text layer is garbled around punctuation — party markers print as
'IDEM!', '!REP)', 'CDEMl' — so the party is read from the punctuation-riddled
title tail. extract_text collapses header column spacing to single spaces
with the same inter-word gap inside and between names, so headers are built
from word coordinates: tokens are first segmented against the CENR's
candidate names (handles single-line headers, with 'Write-in' allowed at any
position — it prints mid-header for U.S. House REP); interleaved wrapped
headers fall back to x-overlap clustering of the header words. Data-row
labels wrap around the numbers ('City of' / <numbers> / 'Precinct 1'), on
either side of a page break, so a row's label is the buffered no-digit
fragments before and after its values, complete once it ends 'Precinct N'.

The Governor REP block's lumped Write-in column (41) sits mid-header and
contains qualified write-in James Elmer Craig's certified 30; the 106th
District title prints 'for Representative 10 6th District'; the report
misspells 'Triston Cole' as 'Tristan Cole'.

Usage:
    .venv/bin/python src/primary_2022_cheboygan.py <source.pdf> \
        --out /tmp/cheboygan22.csv
"""
import argparse
import collections
import re
import sys

import pdfplumber

from primary_2022_common import CENR, verify, write

COUNTY = 'Cheboygan'
# contest-title -> (office, district or None to take from the title's digits)
TITLES = [
    (re.compile(r'^Governor\b'), ('Governor', '')),
    (re.compile(r'^Representative in Congress\s+(.+?)\s+District'),
     ('U.S. House', None)),
    (re.compile(r'^State Senator\s+(.+?)\s+District'), ('State Senate', None)),
    (re.compile(r'^Representative in State Legislature'
                r'(?: for Representative)?\s+(.+?)\s+District'),
     ('State House', None)),
]
NUM = re.compile(r'^(?:\d[\d,]*|\(j)$')
TOTAL = re.compile(r'^Total\s')
# Page 10's Wilmot row and the 106th-District DEM Total row are stamped as a
# full-page image overlay invisible to the text layer; the values were read
# from the rendered image (and the Total matches the canvasser's statement
# on page 5: Fielder 1686).
MANUAL_ROWS = {
    ('State House', '106', 'DEM'): [('Wilmot Township, Precinct 1', [39, 0])],
}
MANUAL_TOTALS = {('State House', '106', 'DEM'): [1686, 2]}
PREC_END = re.compile(r'Precinct \d+$')
NAME_FIX = {'Tristan Cole': 'Triston Cole'}
# report spelling -> CENR spelling, applied at token level before header
# segmentation
TOKEN_FIX = {'Tristan': 'Triston'}


def title_party(title, office_len):
    """Party from the garbled tail after the office name ('IDEM!','!REP)')."""
    tail = title[office_len:]
    if re.search(r'DEM', tail, re.I):
        return 'DEM'
    return 'REP' if re.search(r'REP', tail, re.I) else ''


def parse_num(tok):
    if tok == '(j':
        return 0
    return int(tok.replace(',', ''))


def segment_names(tokens, cands):
    """Ordered cover of the header tokens by CENR candidate names, with one
    'Write-in' token (the lumped column) allowed at any position. Returns
    (names, write_in_index) or None."""
    seqs = []

    def rec(i, used, wi_seen, seq):
        if seqs:
            return
        if i == len(tokens):
            if wi_seen:
                seqs.append(list(seq))
            return
        if tokens[i] == 'Write-in' and not wi_seen:
            seq.append('Write-In')
            rec(i + 1, used, True, seq)
            seq.pop()
            if seqs:
                return
        for ci, name in enumerate(cands):
            if ci in used:
                continue
            nt = name.split()
            if tokens[i:i + len(nt)] == nt:
                seq.append(name)
                rec(i + len(nt), used | {ci}, wi_seen, seq)
                seq.pop()
                if seqs:
                    return

    rec(0, frozenset(), False, [])
    if seqs:
        names = seqs[0]
        return names, names.index('Write-In')
    return None


def cluster_names(words, cands):
    """Fallback for interleaved wrapped headers: x-overlap clustering of the
    header words into columns. Returns (names, write_in_index) or None."""
    clusters = []  # [x0, x1, words]

    def extend(c, w):
        return w['x0'] <= c[1] + 4.5 and w['x1'] >= c[0] - 4.5

    for w in sorted(words, key=lambda w: w['x0']):
        for c in clusters:
            if extend(c, w):
                c[0] = min(c[0], w['x0'])
                c[1] = max(c[1], w['x1'])
                c[2].append(w)
                break
        else:
            clusters.append([w['x0'], w['x1'], [w]])
    clusters.sort(key=lambda c: c[0])
    names = []
    wi = None
    for c in clusters:
        ws = sorted(c[2], key=lambda w: (w['top'], w['x0']))
        name = ' '.join(w['text'] for w in ws)
        if name == 'Write-in':
            wi = len(names)
            names.append('Write-In')
        else:
            names.append(name)
    if wi is None:
        return None
    return names, wi


class Block:
    def __init__(self, office, district, party):
        self.office, self.district, self.party = office, district, party
        self.header_words = []   # words from the block's header lines
        self.names = None        # candidate names in column order
        self.wi_index = None     # column index of the lumped Write-In
        self.sums = None
        self.total = None
        self.deferred = []       # rows awaiting column identification
        self.buffer = []        # pending label fragments
        self.pending = None      # (label_so_far, values) awaiting 'Precinct N'
        self.header_done = False  # finalize_header has run
        self.seen = set()         # precinct labels already held/emitted

    def finalize_header(self, problems):
        words = [w for w in self.header_words
                 if not (w['text'] == 'Precinct' and w['x0'] < 100)
                 and w['x1'] > 130]  # label zone: wrapped data-row labels
        tokens = [TOKEN_FIX.get(w['text'], w['text']) for w in
                  sorted(words, key=lambda w: (w['x0'], w['top']))]
        key = (self.office, self.district, self.party)
        cands = [c for (o, d, p, c) in CENR[COUNTY] if (o, d, p) == key]
        cands = [NAME_FIX.get(c, c) for c in cands]
        seg = segment_names(tokens, cands)
        if seg:
            self.names, self.wi_index = seg
        else:
            cl = cluster_names(words, cands)
            if cl:
                names, wi = cl
                names = [NAME_FIX.get(n, n) for n in names]
                # reject a clustering that produced non-CENR names (the
                # garbled 107th-District header's columns don't align with
                # its values anyway); the Total-value matching below is
                # more reliable there
                if all(n == 'Write-In' or n in cands for n in names):
                    self.names, self.wi_index = names, wi
            if self.names is None:
                # informational: map_by_total identifies the columns by
                # matching each Total against the CENR candidates, and
                # close() then checks every precinct sum against that Total
                notes.append(f'{self.office} {self.district} {self.party}: '
                             f'header unmapped; columns identified from the '
                             f'Total row')
                return
        if len(self.names) != len(cands) + 1:
            problems.append(f'{self.office} {self.district} {self.party}: '
                            f'{len(self.names)} columns for {len(cands)} '
                            f'CENR candidates')
        self.sums = [0] * len(self.names)

    def add_row(self, label, values, problems):
        if self.names is None:
            # columns not yet identified: defer until the Total row maps them
            self.seen.add(label)
            self.deferred.append((label, values))
            return
        self.seen.add(label)
        if self.sums is None or len(values) != len(self.names):
            problems.append(f'{label or "?"} / {self.office}: {len(values)} '
                            f'values for {len(self.names or [])} columns')
            return
        for i, v in enumerate(values):
            self.sums[i] += v
        for i, name in enumerate(self.names):
            if i == self.wi_index:
                continue
            rows.append((label, self.office, self.district, self.party,
                         name, values[i]))

    def defer_line(self, toks, nums):
        """Header unmapped: hold data rows (and their wrapped-label
        fragments) until the Total row maps the columns (map_by_total)."""
        if len(nums) >= 2:
            if toks and toks[-1] != 'Precinct' and 'Precinct' in toks:
                cut = len(toks) - 1 - toks[::-1].index('Precinct')
                pre_toks = toks[:cut + 1]
                vals = [parse_num(t) for t in toks[cut + 1:]] or None
            else:
                pre_toks, vals = [], [parse_num(t) for t in nums]
            if vals:
                label = re.sub(r'\s+', ' ',
                               ' '.join(self.buffer + pre_toks)).strip()
                self.buffer = []
                if PREC_END.search(label):
                    self.seen.add(label)
                    self.deferred.append((label, vals))
                else:
                    self.pending = (label, vals)
            return
        frag = ' '.join(toks)  # 'Precinct N' tails and no-num label lines
        if nums and not PREC_END.search(frag):
            return
        self.buffer.append(frag)
        if self.pending and PREC_END.search(
                self.pending[0] + ' ' + ' '.join(self.buffer)):
            label = re.sub(r'\s+', ' ',
                           (self.pending[0] + ' ' +
                            ' '.join(self.buffer)).strip())
            self.seen.add(label)
            self.deferred.append((label, self.pending[1]))
            self.pending = None
            self.buffer = []

    def map_by_total(self, problems):
        """Identify columns by matching each column's Total against the CENR
        candidate totals (used when the header text can't be segmented or
        clustered)."""
        key = (self.office, self.district, self.party)
        cvals = {c: v for (o, d, p, c), v in CENR[COUNTY].items()
                 if (o, d, p) == key}
        cand_totals = self.total[:-1]
        if len(set(cand_totals)) != len(cand_totals):
            problems.append(f'{self.office} {self.district} {self.party}: '
                            f'duplicate Total values; cannot map columns')
            return False
        names = []
        for v in cand_totals:
            matches = [c for c, cv in cvals.items() if cv == v]
            if len(matches) != 1:
                problems.append(f'{self.office} {self.district} {self.party}: '
                                f'Total {v} matches {len(matches)} CENR '
                                f'candidates')
                return False
            names.append(matches[0])
        self.names = names + ['Write-In']
        self.wi_index = len(names)
        self.sums = [0] * len(self.names)
        for label, values in self.deferred:
            self.add_row(label, values, problems)
        self.deferred = []
        return True

    def close(self, problems):
        if self.total is None:
            if self.names is not None or self.deferred:
                problems.append(f'{self.office} {self.district} {self.party}: '
                                f'no Total row')
            return
        if self.names is None:
            if not self.map_by_total(problems):
                return
        for i, (s, t) in enumerate(zip(self.sums, self.total)):
            if s != t:
                name = self.names[i] if i < len(self.names) else f'col {i}'
                problems.append(f'{self.office} {self.district} '
                                f'{self.party}: {name} precinct sum {s} != '
                                f'Total {t}')
        write_ins[(self.office, self.district, self.party)] += \
            self.total[self.wi_index]


rows = []
write_ins = collections.defaultdict(int)
notes = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    problems = []
    block = None

    with pdfplumber.open(args.pdf) as pdf:
        page_words = [p.extract_words() for p in pdf.pages]
    # stop at the certificates section
    end = len(page_words)
    for i, ws in enumerate(page_words):
        if any(w['text'] == 'STATEMENT' for w in ws):
            end = i
            break

    for ws in page_words[:end]:
        # group words into lines by top (within 3pt), reading left to right
        lines = []
        for w in sorted(ws, key=lambda w: (w['top'], w['x0'])):
            if lines and abs(w['top'] - lines[-1][0][ 'top']) <= 3:
                lines[-1].append(w)
            else:
                lines.append([w])
        for line_words in lines:
            line_words.sort(key=lambda w: w['x0'])
            line = ' '.join(w['text'] for w in line_words)
            if block is not None and re.match(r'^Total\b', line):
                vals = [parse_num(t) for t in line.split()[1:]
                        if NUM.match(t)]
                key = (block.office, block.district, block.party)
                if not vals and key not in MANUAL_TOTALS:
                    pass  # bare 'Total': numbers live elsewhere; keep going
                else:
                    if not vals:
                        vals = MANUAL_TOTALS[key]
                    for label, mvals in MANUAL_ROWS.get(key, []):
                        # inject rows the text layer lost to the image
                        # overlay (skipped if the text carried them)
                        if label not in block.seen:
                            if block.names is None:
                                block.seen.add(label)
                                block.deferred.append((label, mvals))
                            else:
                                block.add_row(label, mvals, problems)
                    block.total = vals
                    block.close(problems)
                    block = None
                    continue
            # contest title? (the garbled tail always carries 'Vote for N')
            matched = None
            for pat, (office, district) in TITLES:
                m = pat.match(line)
                if m and 'Vote' in line:
                    matched = (m, office, district)
                    break
            if matched:
                m, office, district = matched
                party = title_party(line, m.end())
                if district is None:
                    district = re.sub(r'\D', '', m.group(1))
                block = Block(office, district, party)
                continue
            if block is None:
                continue
            toks = line.split()
            nums = [t for t in toks if NUM.match(t)]
            if block.names is None:
                if not block.header_done:
                    # header phase: collect the line's words with
                    # coordinates; the first numeric line ends the header
                    if not (len(nums) >= 2 and not line.startswith('Precinct')):
                        block.header_words.extend(line_words)
                        continue
                    block.finalize_header(problems)
                    block.header_done = True
                    if block.names is None:
                        block.defer_line(toks, nums)
                        continue
                    # fall through: this line is the block's first data row
                else:
                    # header unmapped: keep deferring rows until the Total
                    # row maps the columns
                    block.defer_line(toks, nums)
                    continue
            # data phase
            if len(nums) >= 2:
                # the row's values are the LAST len(names) numeric tokens;
                # a label ending 'Precinct N' contributes one more
                n = len(block.names)
                if len(nums) not in (n, n + 1):
                    problems.append(f'{line!r}: {len(nums)} numeric tokens '
                                    f'for {n} columns')
                    continue
                vals = [parse_num(t) for t in nums[-n:]]
                pre_toks = toks[:len(toks) - n]
                if len(nums) == n + 1:
                    # the extra numeric token is the label's 'Precinct N'
                    if not PREC_END.search(' '.join(pre_toks)):
                        problems.append(f'{line!r}: unexpected extra numeric '
                                        f'token')
                        continue
                pre = ' '.join(pre_toks)
                label_frag = ' '.join(block.buffer + [pre])
                block.buffer = []
                label_frag = re.sub(r'\s+', ' ', label_frag.strip())
                if PREC_END.search(label_frag):
                    block.add_row(label_frag, vals, problems)
                else:
                    block.pending = (label_frag, vals)
                continue
            if len(nums) == 1:
                if PREC_END.search(line):
                    # a wrapped label's 'Precinct N' tail carries one digit
                    block.buffer.append(line)
                    if block.pending:
                        label = re.sub(
                            r'\s+', ' ',
                            (block.pending[0] + ' ' +
                             ' '.join(block.buffer)).strip())
                        if PREC_END.search(label):
                            block.add_row(label, block.pending[1], problems)
                            block.pending = None
                            block.buffer = []
                    continue
                problems.append(f'{line!r}: single numeric token')
                continue
            # no digits: label fragment
            block.buffer.append(line)
            if block.pending and PREC_END.search(' '.join(block.buffer)):
                label = re.sub(r'\s+', ' ',
                               (block.pending[0] + ' ' +
                                ' '.join(block.buffer)).strip())
                block.add_row(label, block.pending[1], problems)
                block.pending = None
                block.buffer = []

    if block is not None:
        block.close(problems)
    for p in notes:
        print('NOTE', p)
    for p in problems:
        print('PROBLEM:', p)
    print(f'{len(rows)} rows; {len(problems)} parser problems')
    blocking = verify(COUNTY, rows, write_ins)
    for p in blocking:
        print('PROBLEM:', p)
    if not problems and not blocking:
        write(COUNTY, rows)


if __name__ == '__main__':
    main()