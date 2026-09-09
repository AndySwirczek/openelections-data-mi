"""Parse Sanilac County's Aug 2022 primary "Results by Precinct" PDF
(MICanvassReport5_0 HTML print) into a per-county precinct CSV, verified
against the certified county-level CENR.

Layout: the report prints each precinct's counting boards as separate
sections (labels ending in a letter-suffixed code, 'City of Brown City,
Precinct 1A') followed by the combined precinct section ('City of Brown
City, Precinct 1') whose rows equal the sum of the boards — the board
sections are skipped and only the combined sections are emitted. Every
contest block opens with a marker line whose right column (x0 >= 248)
reads 'Precinct: <label>' — either complete ('Precinct: X, Precinct 1
Party: Democrat' on one line) or split over two lines ('Precinct: X,
Precinct' then '<code or tail> Party: <party>', possibly with an empty
right). Contest titles share those marker lines, wrapped ('Rep. in
Congress 9 for Congress (DEM) - Vote not' / 'more than: 1'; some lose
their head words in the text layer). A precinct's first block on a page
carries the page-top header '<label> Total % Election Day AV Counting
Boards' plus the REGISTERED VOTERS / BALLOTS CAST / VOTER TURNOUT block.
Data rows read '<name> <votes> [pct] <election day> <AV>', and 'Write-in'
rows are the lumped write-in column.

Only the CENR-carried partisan contests are emitted; the Write-in rows
feed verify()'s qualified-write-in NOTEs.

Usage:
    .venv/bin/python src/primary_2022_sanilac.py <source.pdf> \
        --out /tmp/sanilac22.csv
"""
import argparse
import collections
import re
import sys

import pdfplumber

from primary_2022_common import CENR, verify, write

COUNTY = 'Sanilac'
KEEP = [
    (re.compile(r'^Governor\b'), 'Governor'),
    (re.compile(r'^Rep\.? in Congress (\d+)'), 'U.S. House'),
    (re.compile(r'^State Senator (\d+)'), 'State Senate'),
    (re.compile(r'^House Rep\.? (\d+)'), 'State House'),
]
VOTES_X = (248, 274)     # votes column
LEFT_X = 245             # left (title/name) column ends here
TURNOUT = re.compile(r'^(?:REGISTERED VOTERS|BALLOTS CAST|VOTER TURNOUT)')
CONT = re.compile(r'^(?:(?:not )?more )?than: \d+$|^\d+$')
BOARD = re.compile(r'^\d+[A-Z]$')   # letter-suffixed counting-board code
NUM = re.compile(r'^\d[\d,]*$')


def map_office(title):
    for pat, name in KEEP:
        m = pat.match(title)
        if m:
            return name, (m.group(1) if pat.groups else '')
    return None, ''


# a block whose title head words were lost from the text layer: its title
# is only the wrapped tail ('more than: 1', 'Vote not more than: 1', '1')
FRAG = re.compile(r'^(?:Vote )?(?:(?:not )?more )?than: \d+$|^\d+$')
# the Aug 2022 ballot order for the CENR-carried offices
BALLOT_ORDER = ['Governor', 'U.S. House', 'State Senate', 'State House']


def title_starts(left):
    return bool(re.search(r'\((?:DEM|REP)\)', left) or 'Vote' in left)


def page_lines(page):
    """[(left, right)] left = x0 < 245 words, right = the rest."""
    lines = []
    for w in sorted(page.extract_words(), key=lambda w: (w['top'], w['x0'])):
        if lines and w['top'] - lines[-1][0] <= 3:
            lines[-1][1].append(w)
        else:
            lines.append([w['top'], [w]])
    out = []
    for top, g in lines:
        left = ' '.join(w['text'] for w in g if w['x0'] < LEFT_X)
        right = [w for w in g if w['x0'] >= 248]
        out.append((top, re.sub(r'\s+', ' ', left).strip(), right))
    return out


rows = []
write_ins = collections.defaultdict(int)
notes = []
problems = []


class Block:
    """One contest block: a marker's precinct label, its title and its
    data rows. Board sections (letter-coded labels) set skip."""

    def __init__(self, label, title, party=None):
        self.label = label
        self.title = title
        self.party = party        # DEM/REP from the marker's Party: word
        self.rows = []
        self.done_title = False
        self.await_tail = False   # a split marker's second line is pending
        self.skip = False

    def emit(self):
        """Emit a CENR-carried block's rows (title-fragment blocks are
        resolved by ballot-order position afterwards)."""
        if not self.title or self.skip:
            return
        office, district = map_office(self.title)
        if office is None:
            return  # local office / proposal / delegate: not CENR-carried
        if not re.search(r'\d', self.label):
            problems.append(f'{self.label!r} ({self.title[:40]}): precinct '
                            f'label has no precinct number')
            return
        party = re.search(r'\((DEM|REP)\)', self.title)
        party = party.group(1) if party else (self.party or '')
        self.emit_key((office, district, party))

    def emit_key(self, key):
        for name, votes in self.rows:
            if name == 'Write-in':
                write_ins[key] += votes
            else:
                rows.append((self.label, key[0], key[1], key[2], name,
                             votes))


def close(block, blocks):
    if block is not None:
        blocks.append(block)


def finish_label(block, pageno, page_header_label, resolved, problems):
    """Complete a split marker's label and decide board-section skip."""
    label = block.label
    if not re.search(r'\d+[A-Z]?$', label):
        # the numberless form: adopt the page-top header's label when its
        # base matches, else the unique recent label with the same base
        new = None
        if page_header_label:
            base = re.sub(r'\s*\d+[A-Z]?$', '', page_header_label).strip()
            if base == label.strip():
                new = page_header_label
        if new is None:
            cands = {l for l in resolved
                     if re.sub(r'\s*\d+[A-Z]?$', '', l).strip()
                     == label.strip()}
            if len(cands) == 1:
                new = cands.pop()
        if new is not None:
            label = block.label = new
    final = label.split()[-1] if label.split() else ''
    if BOARD.match(final):
        block.skip = True   # counting-board section: the combined precinct
    else:                   # section carries the CENR's totals
        resolved.append(label)
        if len(resolved) > 40:
            del resolved[:20]


def resolve_fragments(blocks):
    """Blocks whose title head was lost (title is only a wrapped tail like
    'more than: 1'): between their same-label, same-party CENR-carried
    neighbours, the ballot order names the missing contest."""
    order = {o: i for i, o in enumerate(BALLOT_ORDER)}
    for i, b in enumerate(blocks):
        if b.party not in ('DEM', 'REP') or b.skip \
                or not FRAG.match(b.title or ''):
            continue
        prev = next_ = None
        for j in range(i - 1, -1, -1):
            o, d = map_office(blocks[j].title or '')
            if blocks[j].label == b.label and blocks[j].party == b.party \
                    and o is not None:
                prev = (o, d)
                break
        for j in range(i + 1, len(blocks)):
            o, d = map_office(blocks[j].title or '')
            if blocks[j].label == b.label and blocks[j].party == b.party \
                    and o is not None:
                next_ = (o, d)
                break
        if prev is None or next_ is None \
                or prev[0] not in order or next_[0] not in order \
                or order[prev[0]] >= order[next_[0]]:
            continue  # local contest / proposal fragments: leave them
        missing = [o for o in BALLOT_ORDER[order[prev[0]] + 1:order[next_[0]]]
                   if o != prev[0]]
        if len(missing) != 1:
            continue
        office = missing[0]
        districts = {k[1] for k in CENR[COUNTY]
                     if k[0] == office and k[2] == b.party}
        if len(districts) != 1:
            problems.append(f'{b.label} ({b.title!r}): fragment between '
                            f'{prev[0]} and {next_[0]} but {office} has '
                            f'districts {sorted(districts)}')
            continue
        b.emit_key((office, districts.pop(), b.party))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    blocks = []
    block = None
    page_header_label = None
    pending_frag = None  # a wrapped title's floating head fragment
    resolved = []        # recent non-board precinct labels

    def marker_party(right):
        for i, w in enumerate(right):
            if w['text'] == 'Party:' and i + 1 < len(right):
                return {'Democrat': 'DEM', 'Republican': 'REP'}.get(
                    right[i + 1]['text'])
        return None

    with pdfplumber.open(args.pdf) as pdf:
        for pageno, page in enumerate(pdf.pages, 1):
            lines = page_lines(page)
            page_header_label = None
            for li, (top, left, right) in enumerate(lines):
                if left.startswith('file:///'):
                    continue
                # page-top precinct header: a label plus the 'Total %'
                # column headers on the same line (an empty left here is a
                # previous block's continuation 'Total %' header line)
                if right and right[0]['text'] == 'Total' and left:
                    close(block, blocks)
                    block = None
                    page_header_label = left
                    continue
                if TURNOUT.match(left):
                    continue  # turnout block: not contest data
                votes = [w for w in right
                         if VOTES_X[0] <= w['x0'] <= VOTES_X[1]
                         and NUM.match(w['text'])]
                # contest-block marker in the right column
                pw = None
                for w in right:
                    if w['text'] == 'Precinct:':
                        pw = w
                        break
                if pw is not None:
                    close(block, blocks)
                    # label words after 'Precinct:', stopping at 'Party:'
                    part = []
                    for w in right:
                        if w['x0'] <= pw['x1']:
                            continue
                        if w['text'] == 'Party:':
                            break
                        part.append(w['text'])
                    label = ' '.join(part)
                    party = marker_party(right)
                    block = Block(label, ((pending_frag or '') + ' '
                                          + left).strip(), party)
                    pending_frag = None
                    if 'Party:' in [w['text'] for w in right]:
                        finish_label(block, pageno, page_header_label,
                                     resolved, problems)
                    else:
                        # split marker: '<code> Party: X' (or nothing) on
                        # the next line completes label, title and party
                        block.await_tail = True
                    continue
                if block is not None and block.await_tail and (
                        any(w['text'] == 'Party:' for w in right)
                        or not right
                        or (len(right) == 1
                            and re.match(r'^\d+[A-Z]?$', right[0]['text']))):
                    block.await_tail = False
                    tail = []
                    for w in right:
                        if w['text'] == 'Party:':
                            break
                        tail.append(w['text'])
                    if tail:
                        block.label = (block.label + ' ' + ' '.join(tail))
                    block.party = block.party or marker_party(right)
                    finish_label(block, pageno, page_header_label,
                                 resolved, problems)
                    if left and not block.done_title:
                        block.title = (block.title + ' ' + left).strip()
                    continue
                if block is not None and block.await_tail:
                    # the marker's second line never printed as such (a
                    # data row or header follows); give up on the tail
                    block.await_tail = False
                    finish_label(block, pageno, page_header_label,
                                 resolved, problems)
                if block is None:
                    if votes:
                        problems.append(f'page {pageno}: row {left!r} with '
                                        f'no open block')
                    elif left and (title_starts(left) or CONT.match(left)):
                        # a wrapped title's floating head precedes its
                        # marker line
                        pending_frag = (pending_frag + ' ' + left).strip()
                    continue
                if votes:
                    if not block.title:
                        problems.append(f'page {pageno}: row {left!r} before '
                                        f'the contest title')
                        continue
                    name = left
                    if not name:
                        problems.append(f'page {pageno}: votes '
                                        f'{votes[0]["text"]} with no row name')
                        continue
                    block.done_title = True
                    block.rows.append(
                        (name, int(votes[0]['text'].replace(',', ''))))
                elif left and not block.done_title and (
                        title_starts(left) or CONT.match(left)):
                    # the marker line's title continuation, or a whole
                    # title printed below the marker (its head words may
                    # be lost from the text layer)
                    block.title = (block.title + ' ' + left).strip()
            # rows carry onto the next page; the block stays open
    close(block, blocks)
    for b in blocks:
        b.emit()
    resolve_fragments(blocks)

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