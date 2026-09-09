"""Parse rotated "Statement of Votes Cast" reports printed as side-by-side
column panels (Jackson and St. Clair counties, Aug 2024 primary).

The report is a landscape table drawn rotated 90 degrees on portrait pages,
so a table row runs down the portrait page: word['top'] is the row's nx
position and page_width - word['x1'] is the column's ny position. Each page
holds up to two panels, each a [Precinct label column, ...data columns]
block; a contest's data columns (Times Cast, Registered Voters, one per
candidate, Unresolved Write-In, Total Votes) are spread across its pages,
each printed on every page that carries it.

Data-column headers are drawn as character stacks at a single x0 (reading
top-down: "Times Cast", "Hill Harper"); the Precinct header and all row
text are ordinary rotated words. Numbers right-align within ~6pt of their
column header's ny. Two variants:
- method mode (Jackson): each precinct block prints four rows labeled
  Election Day / AV Counting Boards / Early Voting / Total, with the row
  label in the gutter left of the Precinct column and the precinct label on
  standalone lines above the block;
- precinct mode (St. Clair): one row per precinct with no method rows, and
  wrapped precinct labels straddle the numbers row.

With two panels on a page both print the same precinct labels and gutter
row labels; only the left panel's label copy is kept, using the right
panel's Precinct header ny as the boundary.

Both variants emit per-precinct candidate rows, a Write-In row when the
source prints an Unresolved Write-In column, and a Ballots Cast row from
Times Cast; the Total Votes column, per-block method sums and the county
total row are used for verification. Method mode carries election_day /
av_counting_boards / early_votes breakdown columns (Kent's convention).

Usage:
    .venv/bin/python src/rotated_panel_sovc_parser.py <source.pdf> \
        --county 'Jackson' \
        --out 2024/counties/20240806__mi__primary__jackson__precinct.csv
"""
import argparse
import collections
import csv
import re
import sys

import pdfplumber

TITLE = re.compile(r'^(.+?)((?:\((?:DEM|REP|LIB|GRN|UST|NLP|NPA|WCP)\))?) '
                   r'\(Vote for \d+\)$')
NUM = re.compile(r'^\d[\d,]*$')
AUX = {'TimesCast', 'RegisteredVoters', 'TotalVotes', 'UnresolvedWrite-In'}
METHODS = ('Election Day', 'AV Counting Boards', 'Early Voting', 'Total')
METHOD_KEY = {'Election Day': 'election_day',
              'AV Counting Boards': 'av_counting_boards',
              'Early Voting': 'early_votes', 'Total': 'total'}
PARTY_SUFFIX = re.compile(r'\((?:DEM|REP|LIB|GRN|UST|NLP|NPA|WCP)\)$')
METHOD_WORDS = {'Election', 'Day', 'AV', 'Counting', 'Boards', 'Early',
                'Voting', 'Total'}
PARTIES = {'DEM', 'REP', 'LIB', 'GRN', 'UST', 'NLP', 'NPA', 'WCP'}


def map_office(title):
    """Contest title (party tag stripped) -> (office, district)."""
    if title == 'United States Senator':
        return 'U.S. Senate', ''
    m = re.match(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$',
                 title)
    if m:
        return 'U.S. House', str(int(m.group(1)))
    m = re.match(r'^Representative in State Legislature '
                 r'(\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'State House', str(int(m.group(1)))
    m = re.match(r'^(.+), Precinct \d+ Delegate$', title)
    if m:
        return f'{m.group(1)} Delegate to County Convention', ''
    return title, ''


def rotated_lines(page):
    """[(nx, [(ny, text)...])] with each line's words sorted right-to-left."""
    words = page.extract_words(y_tolerance=1.5)
    width = page.width
    rows = sorted((w['top'], width - w['x1'], w['text']) for w in words)
    lines = []
    for nx, ny, text in rows:
        if lines and nx - lines[-1][0][-1] <= 3:
            lines[-1][0].append(nx)
            lines[-1][1].append((ny, text))
        else:
            lines.append(([nx], [(ny, text)]))
    return [(xs[0], sorted(ws, key=lambda p: -p[0])) for xs, ws in lines]


def _cluster(chars, key, gap):
    """Split chars into clusters where consecutive keys differ <= gap."""
    clusters, cur = [], []
    for c in chars:
        if cur and key(c) - key(cur[-1]) > gap:
            clusters.append(cur)
            cur = []
        cur.append(c)
    if cur:
        clusters.append(cur)
    return clusters


def _stack_words(chars):
    """Vertical stack: read chars top-down, splitting words at capitals.

    Gap alone cannot separate words: wide glyphs ("m", "w") leave gaps up
    to 9pt inside a word while word gaps run as low as 5pt ("Bobbi Jo").
    Every word in these headers starts with a capital letter (or follows
    an initial's period), so split only there.
    """
    chars = sorted(chars, key=lambda c: -c['top'])
    words, cur = [], [chars[0]]
    for c in chars[1:]:
        prev = cur[-1]
        if (c['text'].isupper() and prev['text'].islower()) \
                or prev['text'] == '.':
            words.append(cur)
            cur = [c]
        else:
            cur.append(c)
    words.append(cur)
    return ' '.join(''.join(c['text'] for c in w) for w in words)


def _join_stack_lines(texts):
    """Join wrapped stack lines; a trailing hyphen keeps the next line's
    word attached ("Osborne-" + "Reynolds" -> "Osborne-Reynolds")."""
    out = ''
    for t in texts:
        if out.endswith('-'):
            out += t
        else:
            out += (' ' if out else '') + t
    return out


def header_columns(page):
    """Data-column headers: [(ny_lo, ny_hi, text)] sorted by ny descending.

    Header lines are the report's column-header block; every other line
    (page footer, title, party tag, turnout header, precinct labels, method
    rows, county footers) consists of whole multi-char upright words, while
    a header line either holds single-character words (the vertical-stack
    rendering) or non-upright chars (the mirrored rendering). Column bands
    are char clusters in x; three renderings are in use:
    - vertical stacks at one x0, read top-down ("Times Cast", most pages);
    - wrapped stacks sharing a band ("Registered" over "Voters");
    - mirrored horizontal words (chars drawn right-to-left, upright=False),
      read x0-descending with lines ordered bottom-to-top.
    """
    lines = rotated_lines(page)
    mirrored = [c['top'] for c in page.chars
                if c['text'].strip() and not c['upright']]
    header_nxs = []
    for nx, ws in lines:
        texts = [t for _, t in ws]
        # a lone letter (stack rendering); "-" on footer rows is not one
        if any(len(t) == 1 and t.isalpha() for t in texts) \
                or any(nx - 1 <= t <= nx + 10 for t in mirrored):
            header_nxs.append(nx)

    # Stack chars interleave top-to-top within 3pt, so the whole header
    # block can chain into one line whose start sits ~24pt below the
    # block's tallest chars. Extend each cluster of header lines
    # (consecutive starts <=14 apart) up to just below the next
    # non-header line — on every page that's the footer "County" line or
    # a precinct label, well clear of the header block.
    line_tops = sorted(nx for nx, _ in lines)
    blocks = _cluster(sorted(header_nxs), lambda n: n, 14)

    def in_header(top):
        for block in blocks:
            above = [t for t in line_tops
                     if t > block[-1] and t not in header_nxs]
            hi = (above[0] - 1 if above else block[-1] + 30)
            if block[0] - 1 <= top <= hi:
                return True
        return False

    chars = [c for c in page.chars
             if c['text'].strip() and in_header(c['top'])]
    if not chars:
        return []
    chars.sort(key=lambda c: c['x0'])
    bands = []
    for c in chars:
        # columns sit >=20pt apart and letters inside a band <=3pt; the
        # footer's "County" word sits 8pt right of the Precinct column
        if bands and c['x0'] - max(x['x1'] for x in bands[-1]) < 6:
            bands[-1].append(c)
        else:
            bands.append([c])
    cols = []
    for band in bands:
        ny_lo = page.width - max(c['x1'] for c in band)
        ny_hi = page.width - min(c['x0'] for c in band)
        if not all(c['upright'] for c in band):        # mirrored words
            if max(collections.Counter(round(c['x0'])
                                       for c in band).values()) >= 2:
                # mirrored glyphs stacked vertically at one x ("Karen A.
                # Coffman"): read like an upright stack
                clusters = _cluster(band, lambda c: c['x0'], 3)
                text = _join_stack_lines(_stack_words(cl)
                                         for cl in clusters)
            else:
                lines = _cluster(sorted(band, key=lambda c: -c['top']),
                                 lambda c: -c['top'], 6)
                text = ' '.join(
                    ''.join(c['text'] for c in
                            sorted(line, key=lambda c: -c['x0']))
                    for line in lines)
        elif max(c['x0'] for c in band) - min(c['x0'] for c in band) < 3:
            text = _stack_words(band)                  # single stack
        elif max(collections.Counter(round(c['x0'])
                                     for c in band).values()) < 2:
            # one horizontal word ("Precinct", "Total Votes"): chars run
            # left to right, each at its own x; a stacked header instead
            # puts several chars on the same x
            text = ''.join(c['text'] for c in sorted(band,
                                                     key=lambda c: c['x0']))
        else:                                          # wrapped stacks
            # a wrapped multi-line header ("Alissa Marie" / "Peterson" /
            # "Write-in") stacks chars whose tops interleave, so it has
            # one top-cluster but several x0-clusters; read each stack
            # bottom-to-top and join the lines
            clusters = _cluster(band, lambda c: c['x0'], 3)
            text = _join_stack_lines(_stack_words(cl) for cl in clusters)
        cols.append((ny_lo, ny_hi, text))
    return sorted(cols, key=lambda c: -c[0])


def column_key(text):
    """Column header text -> storage key (aux keys lose their spaces)."""
    text = PARTY_SUFFIX.sub('', text).strip()
    key = text.replace(' ', '')
    # one band's chars drift x as they stack, defeating line clustering;
    # the county summary spells the name (the tag can merge into the
    # band, so look past a trailing "Write-in" too)
    fixed = CANDIDATE_FIXES.get(key)
    if not fixed:
        stripped = re.sub(r'\s*\bWrite-?in\b\s*', ' ', text,
                          flags=re.IGNORECASE).strip()
        if stripped != text:
            fixed = CANDIDATE_FIXES.get(stripped.replace(' ', ''))
    if fixed:
        return fixed
    return _column_key_tail(text, key)


CANDIDATE_FIXES = {
    'AnnaElizabethOsboren-Reynolds': 'Anna Elizabeth Osborne-Reynolds',
    # mirrored-horizontal band read as spaced initials; the candidate list
    # (co.jackson.mi.us DocumentCenter/View/21611) spells it
    'LeeAnnaR.Kloack': 'LeeAnna R. Kloack',
}


def _column_key_tail(text, key):
    if key in AUX or key in ('CastTimes', 'VotesTotal'):
        # mirrored pages wrap headers bottom-to-top ("Cast Times")
        return 'TimesCast' if key == 'CastTimes' else \
            ('TotalVotes' if key == 'VotesTotal' else key)
    collapsed = re.sub(r'(.)\1+', r'\1', key)
    if collapsed.lower() == 'write-in':
        return 'UnresolvedWrite-In'
    if key in ('VotersRegistered', 'RegisteredVoters') or \
            text == 'Voters Registered':
        # mirrored pages read bottom-to-top ("Voters Registered")
        return 'RegisteredVoters'
    if key == 'RegisteredVoters':
        return 'RegisteredVoters'
    # a write-in candidate's name can merge with its "Write-in" tag into
    # one band; the name alone is the candidate
    stripped = re.sub(r'\s*\bWrite-?in\b\s*', ' ', text,
                      flags=re.IGNORECASE).strip()
    if stripped and stripped != text:
        return stripped
    return text


def dedupe_double(words):
    """Both panels print the same label text side by side; drop the copy."""
    h = len(words) // 2
    if h and len(words) % 2 == 0 and words[:h] == words[h:]:
        return words[:h]
    return words


class Contest:
    def __init__(self, title, county, problems):
        self.title = title
        self.county = county
        self.problems = problems
        m = TITLE.match(title)
        self.party = m.group(2)[1:-1] if m.group(2) else ''
        self.office, self.district = map_office(m.group(1).strip())
        self.candidates = []        # candidate names in first-seen order
        self.headers_seen = set()
        self.rows = collections.OrderedDict()   # precinct -> {col: {m: v}}
        self.method_mode = None     # 'method' | 'precinct' | None yet
        self.pending_labels = []    # [(page_no, nx, text)] label lines
        self.block = None           # method mode: {col: {method: val}}
        self.block_methods = []
        self.block_total = False    # block's Total row seen (write-in
        self.last_method = None     # sub-rows may still follow)
        self.current_label = None
        self.last_data_nx = None    # precinct mode: wrapped-label window
        self.county_totals = {}     # col -> set of printed county values

    # -- page -----------------------------------------------------------
    def page(self, page, cols, page_no):
        data_cols = [c for c in cols
                     if column_key(c[2]) not in ('Precinct', 'County',
                                                 'Michigan')]
        for lo, hi, text in data_cols:
            key = column_key(text)
            if key not in self.headers_seen:
                if key not in AUX and key not in self.candidates:
                    self.candidates.append(key)
                self.headers_seen.add(key)

        def band_of(ny):
            for lo, hi, text in data_cols:
                # a drifted candidate column's numbers can sit ~10pt below
                # the band's bottom (its stacked header reaches further
                # right than its numbers do)
                if lo - 12 <= ny <= hi + 3:
                    return column_key(text)
            return None

        in_footer = False    # the per-page footer block after the last block
        self.last_data_nx = None
        for nx, ws in rotated_lines(page):
            assigned = [(band_of(ny), ny, text) for ny, text in ws]
            nums = [(band, text) for band, ny, text in assigned
                    if band is not None and NUM.match(text)]
            # gutter words: row labels, method labels, county footers; a
            # number outside every band is a label fragment ("Precinct 1")
            alpha = [(ny, text) for band, ny, text in assigned
                     if band is None]
            words = [t for _, t in alpha]
            text = ' '.join(words)
            if 'Cumulative' in text or 'Page:' in text:
                continue
            if not nums:
                if not words or 'County' in text or 'Michigan' in text:
                    continue
                # contest titles repeat on continuation pages, sometimes
                # with a number word banded away; header words ("Precinct",
                # "Total") and party tags can strand on their own line
                if '(Vote for' in text or \
                        all(w in ('Precinct', 'Total') for w in words) or \
                        all(re.match(r'^\d+\)$', w) or w in PARTIES
                            for w in words):
                    continue
                if any(len(t) == 1 and not NUM.match(t) for t in
                       (t for _, t in ws)):
                    continue    # garbled column-header line, not a label
                if len(alpha) != len(assigned):
                    # words of this line landed in data-column bands: a
                    # contest-title line, which spans the full page width
                    # (labels live only in the gutter)
                    continue
                tail = ' '.join(dedupe_double(words))
                if self.method_mode == 'precinct' \
                        and self.current_label is not None \
                        and self.last_data_nx is not None \
                        and nx - self.last_data_nx <= 25 \
                        and re.match(r'^(?:Precinct )?\d+[A-Z]?$', tail):
                    # precinct mode: a label's last words wrap onto the next
                    # line below their row (the numbers row can sit between
                    # the label lines), so they extend the row above; the
                    # ~25pt window and the tail shape keep contest-title
                    # lines (which sit beside another contest's rows on
                    # two-panel pages) out
                    self.extend_label(tail)
                    continue
                if self.method_mode == 'precinct':
                    continue    # contest-title fragment or page noise
                # standalone precinct-label line (method mode)
                self.pending_labels.append(
                    (page_no, nx, ' '.join(dedupe_double(words))))
                continue
            if 'Michigan' in text:
                in_footer = True    # "Jackson County Michigan - <totals>"
                continue
            if in_footer:
                if 'County' in text:    # the page's "County - Total" row
                    for band, val in nums:
                        self.county_totals.setdefault(band, set()).add(
                            int(val.replace(',', '')))
                continue
            if 'County' in text:
                for band, val in nums:
                    self.county_totals.setdefault(band, set()).add(
                        int(val.replace(',', '')))
                continue
            if '(Vote for' in text:
                continue    # contest title with a number word banded away
            self.last_data_nx = nx
            # data row; in method mode the gutter holds the method label
            near_label = dedupe_double(words)
            method = None
            for meth in METHODS:
                if meth.replace(' ', '') in text.replace(' ', ''):
                    method = METHOD_KEY[meth]
                    break
            is_sub = False
            if method is None and not words and self.method_mode == 'method' \
                    and self.last_method is not None:
                # write-in candidates print on label-less sub-rows, one per
                # method row, directly below their parent
                method = self.last_method
                is_sub = True
            if self.method_mode is None:
                self.method_mode = 'method' if method else 'precinct'
            label = self.take_label(page_no, nx,
                                    None if method else near_label)
            if self.method_mode == 'method':
                if method is None:
                    self.problems.append(
                        f'{self.title}: data row at nx={nx} without method '
                        f'label ({text!r})')
                    continue
                if label:
                    if self.block is not None:
                        self.close_block()
                    self.current_label = label
                    self.block = collections.OrderedDict()
                    self.block_methods = []
                    self.block_total = False
                if self.block is None:
                    if all(int(v.replace(',', '')) == 0 for _, v in nums):
                        continue    # cumulative zero rows, no precinct
                    self.problems.append(f'{self.title}: method row at '
                                         f'nx={nx} outside a block')
                    continue
                if method in self.block_methods and not is_sub:
                    self.problems.append(f'{self.title} / '
                                         f'{self.current_label}: duplicate '
                                         f'{method} row')
                seen = set()
                for band, val in nums:
                    if band in seen:
                        self.problems.append(
                            f'{self.title} / {self.current_label}: two '
                            f'{band} numbers on one row')
                    seen.add(band)
                    self.block.setdefault(band, {})[method] = \
                        int(val.replace(',', ''))
                self.last_method = method
                if method == 'total':
                    self.block_total = True
                else:
                    self.block_methods.append(method)
            else:
                if label:
                    self.current_label = label
                elif self.current_label is None:
                    self.problems.append(f'{self.title}: row at nx={nx} has '
                                         f'no precinct label')
                    continue
                row = self.rows.setdefault(self.current_label,
                                           collections.OrderedDict())
                for band, val in nums:
                    row.setdefault(band, {})['total'] = \
                        int(val.replace(',', ''))
        if self.block_total:
            self.close_block()    # Total sub-rows ended with the page

    def close_block(self):
        if self.block is None or not self.block_total:
            self.block = None
            self.current_label = None
            return
        self.verify_block()
        row = self.rows.setdefault(self.current_label,
                                   collections.OrderedDict())
        for band, vals in self.block.items():
            row.setdefault(band, {}).update(vals)
        self.block = None
        self.current_label = None
        self.block_total = False

    def extend_label(self, text):
        """Precinct mode: append a wrapped label line to the row above.

        The row's numbers were already recorded under the shorter label, so
        move its dict to the full label (later panels of the same contest
        re-print the same rows, so a full-label row may already exist).
        """
        old = self.current_label
        new = f'{old} {text}'
        self.current_label = new
        if old not in self.rows:
            return
        row = self.rows.pop(old)
        if new in self.rows:
            for band, vals in row.items():
                self.rows[new].setdefault(band, {}).update(vals)
        else:
            self.rows[new] = row

    def take_label(self, page_no, nx, near_text):
        """Pending label lines near this row (or carried from the last page)
        plus, in precinct mode, the label words straddling the numbers row.
        """
        pieces = [(p, x, t) for p, x, t in self.pending_labels
                  if p < page_no or abs(x - nx) <= 40]
        self.pending_labels = [p for p in self.pending_labels
                               if not (p[0] < page_no
                                       or abs(p[1] - nx) <= 40)]
        if near_text:
            pieces.append((page_no, nx, ' '.join(near_text)))
        texts = [text for _, _, text in pieces]
        return ' '.join(texts) if texts else None

    def verify_block(self):
        label = self.current_label
        wi = self.block.get('UnresolvedWrite-In', {})
        for band, vals in self.block.items():
            if band == 'RegisteredVoters':
                continue    # registration total, constant across methods
            missing = [m for m in METHOD_KEY.values() if m not in vals]
            if missing:
                self.problems.append(f'{self.title} / {label}: {band} '
                                     f'missing {missing}')
                continue
            methods = ('election_day', 'av_counting_boards', 'early_votes')
            total = vals['total']
            summed = sum(vals[m] for m in methods)
            if summed == total:
                continue
            # The report is inconsistent for two write-in candidates, and
            # the county's own summary report (SUM_2024_Aug_OpenPrimary)
            # resolves both the same way its Total Votes row does:
            # - Pulaski P1 double-prints an unresolved write-in vote under
            #   Alissa Marie Peterson's Election Day cell; her Total row
            #   (and the county total) keep it only as a write-in, so drop
            #   the duplicated detail value.
            # - Pulaski P1 prints 0 in Anna Elizabeth Osborne-Reynolds's
            #   Total cell while her detail rows (and Total Votes) give
            #   79, so take the total from the method sum.
            dup = [m for m in methods if wi.get(m) == vals[m]
                   and vals[m] == summed - total]
            if dup:
                for m in dup:
                    vals[m] = 0
            else:
                tv = self.block.get('TotalVotes', {}).get('total')
                others = sum(v['total'] for b, v in self.block.items()
                             if b not in ('TotalVotes', 'RegisteredVoters')
                             and b != band and 'total' in v)
                if tv is not None and summed == tv - others:
                    vals['total'] = summed
                else:
                    self.problems.append(
                        f'{self.title} / {label}: {band} methods do not '
                        f'sum to Total ({vals!r})')

    # -- finish ---------------------------------------------------------
    def finish(self, out_rows):
        self.close_block()
        cands = self.candidates
        for precinct, row in self.rows.items():
            for method in ('election_day', 'av_counting_boards',
                           'early_votes', 'total'):
                if not any(method in vals for vals in row.values()):
                    continue
                total = sum(vals[method] for cand, vals in row.items()
                            if cand in cands and method in vals)
                if self.method_mode == 'method' and \
                        'UnresolvedWrite-In' in row and \
                        method in row['UnresolvedWrite-In']:
                    # method mode's Total Votes covers the write-in column
                    # too; precinct mode (St. Clair) prints unresolved
                    # write-ins outside its Total Votes column
                    total += row['UnresolvedWrite-In'][method]
                tv = row.get('TotalVotes', {}).get(method)
                if tv is not None and total != tv:
                    self.problems.append(
                        f'{self.title} / {precinct}: candidate sum {total} '
                        f'!= Total Votes {tv} ({method})')
            self.emit_precinct(precinct, row, out_rows)
        # county totals check (county row may print per page; all copies
        # of a column's total must agree)
        for cand in cands + ['TimesCast']:
            if cand in self.county_totals:
                printed = sorted(self.county_totals[cand])
                summed = sum(r[cand]['total'] for r in self.rows.values()
                             if cand in r and 'total' in r[cand])
                if len(printed) > 1:
                    self.problems.append(f'{self.title}: county totals for '
                                         f'{cand} vary: {printed}')
                elif printed[0] != summed:
                    self.problems.append(
                        f'{self.title}: county total {cand}={printed[0]} != '
                        f'precinct sum {summed}')

    def emit_precinct(self, precinct, row, out_rows):
        breakdown = self.method_mode == 'method'
        for cand in self.candidates:
            self.emit_row(row.get(cand), precinct, cand, breakdown, out_rows)
        if 'UnresolvedWrite-In' in row:
            self.emit_row(row['UnresolvedWrite-In'], precinct, 'Write-In',
                          breakdown, out_rows)
        if 'TimesCast' in row:
            self.emit_row(row['TimesCast'], precinct, 'Ballots Cast',
                          breakdown, out_rows)

    def emit_row(self, vals, precinct, name, breakdown, out_rows):
        if not vals:
            if name != 'Ballots Cast':
                self.problems.append(f'{self.title} / {precinct}: no row '
                                     f'for {name!r}')
            return
        if breakdown:
            missing = [m for m in METHOD_KEY.values() if m not in vals]
            if missing:
                self.problems.append(f'{self.title} / {precinct}: {name} '
                                     f'missing {missing}')
                return
            out_rows.append([self.county, precinct, self.office,
                             self.district, self.party, name, vals['total'],
                             vals['election_day'], vals['av_counting_boards'],
                             vals['early_votes']])
        else:
            out_rows.append([self.county, precinct, self.office,
                             self.district, self.party, name,
                             vals['total']])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    out_rows = []
    problems = []
    contest = None

    with pdfplumber.open(args.pdf) as pdf:
        for page in pdf.pages:
            title = None
            lines_rl = rotated_lines(page)
            for i, (nx, ws) in enumerate(lines_rl):
                line = ' '.join(t for _, t in ws)
                if TITLE.match(line):
                    title = line.strip()
                    break
                # "(Vote for" can wrap, stranding "5)" on the party line
                if line.endswith('(Vote for') and i + 1 < len(lines_rl):
                    nxt = ' '.join(t for _, t in lines_rl[i + 1][1])
                    m = re.match(r'^(\d+\))', nxt)
                    if m:
                        title = f'{line} {m.group(1)}'
                        break
            cols = header_columns(page)
            if title:
                if contest is not None:
                    contest.finish(out_rows)
                contest = Contest(title, args.county, problems)
            if contest is None or not cols:
                continue    # summary pages, or a contest's turnout pages
            contest.page(page, cols, page.page_number)
    if contest is not None:
        contest.finish(out_rows)

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        header = ['county', 'precinct', 'office', 'district', 'party',
                  'candidate', 'votes']
        if any(len(r) > 7 for r in out_rows):
            header += ['election_day', 'av_counting_boards', 'early_votes']
        w.writerow(header)
        w.writerows(out_rows)
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()