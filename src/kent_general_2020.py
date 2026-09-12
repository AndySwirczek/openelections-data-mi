#!/usr/bin/env python3
"""Parse Kent County's Nov 2020 general election from the ES&S
'Statement of Votes Cast' ('Kent County, MI precinct-level results.pdf',
283pp, text-extractable).  The PDF covers the 16 federal/state contests
only ('*Kent County Only'); local offices and proposals are not in it.

The committed 20201103__mi__general__kent__precinct.csv carried those
same 5 contest types, but with nonstandard party codes (LBT/GRE) and
short Straight Party candidate names; this parser regenerates the whole
file with full breakdown columns (election_day from the Election Day
row, absentee from the AVCB row) and adds the state-board contests.

Layout:
  - pp 1-19: per-precinct turnout (Registered Voters / Cards Cast with
    Election Day / Absentee / Total rows), ending in the county totals.
  - pp 20-283: contest sections, each starting with an upright title
    line ('President / Vice Pres. (Vote for 1)'); every page of the
    section repeats a rotated header strip (Times Cast, Registered
    Voters, then one cell per candidate; labels read bottom-to-top,
    wrapped lines sit ~10pt to the right of their continuation, cells
    separated by >11pt gaps).  Data rows are 'Election Day <TC> <RV>
    <values...>' / 'AVCB ...' / 'Total ...' under each bold precinct
    name; values are RIGHT-aligned per column, and zero cells print
    except the qualified-write-in columns, which print only when
    nonzero.  A 'Kent County - Total' row prints on every page (running
    totals); the final one is the contest's county total.
  - pdfplumber fragments everything into single chars here, so lines
    are rebuilt from chars (top tolerance 3pt) and tokens from char
    gaps (>2.5pt).

Verification: per precinct Total == Election Day + AVCB for every
column (Times Cast and Registered Voters included); sum over precincts
of each column == the contest's final Kent County - Total row; the
turnout pages' sums == the printed county turnout; per-precinct Times
Cast == Ballots Cast for the countywide contests; CENR county-file
reconciliation for the offices it covers (Straight Party excluded -- see
below), and the emitted rows are compared against the committed CSV.

The CENR county file's seven Kent Straight Party rows are internally
inconsistent: its three values are a permutation of the source's
(e.g. REP: source ED 54,922 / AVC 51,596 / Total 106,518 vs CENR
election_day 51,596 / absentee 106,518 / votes 175,030 = AVC+Total).
The source's own printed county total row matches its precinct sums, so
those rows are parsed as printed; the county-file check expects exactly
the 7 Straight Party mismatches.
"""

import collections
import csv
import os
import re
import sys

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/general/'
       'Kent County, MI precinct-level results.pdf')
OUT = '2020/counties/20201103__mi__general__kent__precinct.csv'
COUNTY_FILE = '2020/20201103__mi__general__county.csv'
COUNTY = 'Kent'

OLD_SP = {  # committed file's Straight Party candidate names by party
    'DEM': 'Democratic', 'REP': 'Republican', 'GRN': 'Green',
    'LIB': 'Libertarian', 'NLP': 'Natural Law', 'UST': 'U.S. Taxpayers',
    'WCP': 'Working Class Party',
}
SP_NAMES = {  # emitted names follow the CENR county file / Ionia style
    'DEM': 'Democratic Party', 'REP': 'Republican Party',
    'LIB': 'Libertarian Party', 'UST': 'US Taxpayers Party',
    'WCP': 'Working Class Party', 'GRN': 'Green Party',
    'NLP': 'Natural Law Party',
}

# title line -> (office, district)
OFFICES = [
    (re.compile(r'^\*?Straight Party\b'), ('Straight Party', '')),
    (re.compile(r'^\*?President / Vice Pres\.'), ('President', '')),
    (re.compile(r'^\*?US Senator'), ('U.S. Senate', '')),
    (re.compile(r'^\*?Rep In Congress, (\d+)[a-z]{2} Dist\.'),
     lambda m: ('U.S. House', m.group(1))),
    (re.compile(r'^\*?State Rep\., (\d+)[a-z]{2} Dist\.'),
     lambda m: ('State House', m.group(1))),
    (re.compile(r'^\*?State Board of Ed\.'),
     ('Member of the State Board of Education', '')),
    (re.compile(r'^\*?U of M Regents'),
     ('Regent of the University of Michigan', '')),
    (re.compile(r'^\*?MSU Trustee'),
     ('Trustee of Michigan State University', '')),
    (re.compile(r'^\*?Wayne State Governors'),
     ('Governor of Wayne State University', '')),
]

DATA_LABELS = {'Election Day', 'AVCB', 'Total', 'Kent County - Total',
               'Absentee'}
TURNOUT_FURNITURE = {'Precinct', 'Registered Voters', 'Cards Cast',
                     '% Turnout', 'Registered  Voters', 'Cards  Cast',
                     '%  Turnout'}
NUM_RE = re.compile(r'^[\d,]+$')


def page_lines(page, y0=0.0, y1=1e9):
    """Upright chars -> [(top, [(token, x0, x1), ...])]."""
    up = [c for c in page.chars if c['matrix'][1] <= c['matrix'][0]
          and y0 <= c['top'] < y1]
    up.sort(key=lambda c: c['top'])
    lines = []
    for c in up:
        if lines and c['top'] - lines[-1][-1]['top'] <= 3:
            lines[-1].append(c)
        else:
            lines.append([c])
    out = []
    for ln in lines:
        ln.sort(key=lambda c: c['x0'])
        toks = [(ln[0]['text'], ln[0]['x0'], ln[0]['x1'])]
        for a, b in zip(ln, ln[1:]):
            if b['x0'] - a['x1'] > 2.5:
                toks.append((b['text'], b['x0'], b['x1']))
            else:
                t, x0, _x1 = toks[-1]
                toks[-1] = (t + b['text'], x0, b['x1'])
        out.append((round(ln[0]['top'], 1), toks))
    return out


def header_cells(page):
    """Rotated header strip -> [(first-line x0, label)] in x order."""
    rot = [c for c in page.chars if c['matrix'][1] > c['matrix'][0]]
    lines = collections.defaultdict(list)
    for c in rot:
        lines[round(c['x0'], 1)].append(c)
    vlines = []
    for x0 in sorted(lines):
        cs = sorted(lines[x0], key=lambda c: -c['top'])
        vlines.append((x0, ''.join(c['text'] for c in cs)))
    cells = []
    for x0, text in vlines:
        if cells and x0 - cells[-1][-1][0] <= 12.5:
            cells[-1].append((x0, text))
        else:
            cells.append([(x0, text)])
    return [(cell[0][0], ' '.join(t for _x, t in cell).strip())
            for cell in cells]


def title_of(page):
    up = [c for c in page.chars if c['matrix'][1] <= c['matrix'][0]
          and c['top'] < 65]
    up.sort(key=lambda c: (round(c['top']), c['x0']))
    t = ''.join(c['text'] for c in up).strip()
    if '(Vote for' not in t:
        return None
    return re.sub(r'\s+', ' ', t)


def office_of(title):
    for rx, off in OFFICES:
        m = rx.match(title)
        if m:
            if callable(off):
                return off(m)
            return off
    raise ValueError(f'unmapped contest title {title!r}')


def clean_candidate(label, office):
    """Header cell label -> (candidate, party)."""
    label = re.sub(r'\s+', ' ', label).strip()
    # wrapped header lines split hyphenated names ('Cheng- Schulting')
    label = re.sub(r'(\w)- (?=\w)', r'\1-', label)
    m = re.match(r'^(.*) \(([A-Z]{2,4})\)$', label)
    if m:
        name, code = m.group(1).strip(), m.group(2)
        if office == 'Straight Party':
            return SP_NAMES[code], code
        return re.sub(r'\s+w/\s+.*$', '', name), code
    m = re.match(r'^(.*) Qualified WI$', label)
    if m:
        return m.group(1).strip(), ''
    if label in ('Write-in', 'Write-in Cumulative'):
        return 'Write-ins', ''
    raise ValueError(f'unparsed candidate label {label!r} ({office})')


def main():
    problems = []
    pdf = pdfplumber.open(SRC)

    # ---- contest segmentation ---------------------------------------
    titles = {}     # page index -> title
    for i, page in enumerate(pdf.pages, 1):
        t = title_of(page)
        if t:
            titles[i] = t
    starts = sorted(titles)
    contests = []
    for n, p0 in enumerate(starts):
        p1 = starts[n + 1] - 1 if n + 1 < len(starts) else len(pdf.pages)
        contests.append((titles[p0], office_of(titles[p0]), p0, p1))
    first_contest = starts[0]

    # ---- front turnout pages ----------------------------------------
    front = {}      # precinct -> {'RV': n, 'ED': n, 'AB': n, 'TOT': n}
    county = {}     # 'RV'/'ED'/'AB'/'TOT' -> count
    cur = None
    for i in range(1, first_contest):
        for top, toks in page_lines(pdf.pages[i - 1]):
            texts = [t for t, _x0, _x1 in toks]
            if not texts:
                continue
            head = texts[0]
            joined = re.sub(r'\s+', ' ', ' '.join(texts)).strip()
            if head == 'Kent County - Total':
                # county row prints RV, cards total, pct inline
                nums = [int(t.replace(',', ''))
                        for t in texts[1:] if NUM_RE.match(t)]
                county['RV'] = nums[0]
                county['TOT'] = nums[1]
                cur = None      # county ED/AB rows follow without RV
                continue
            if head in ('Election Day', 'Absentee', 'Total'):
                vals = [int(t.replace(',', ''))
                        for t in texts[1:] if NUM_RE.match(t)]
                key = {'Election Day': 'ED', 'Absentee': 'AB',
                       'Total': 'TOT'}[head]
                if cur is None:
                    county[key] = vals[0]
                else:
                    front[cur][key] = vals
                continue
            if head in TURNOUT_FURNITURE or head in (
                    'General Election', 'Kent County, Michigan',
                    'November 3, 2020', '*Kent County Only'):
                continue
            if 'Statement of Votes Cast' in joined:
                continue
            name = re.sub(r'\s+', ' ', joined)
            cur = name
            front[cur] = {}

    for name, d in front.items():
        if set(d) != {'ED', 'AB', 'TOT'}:
            problems.append(f'turnout {name!r}: rows {sorted(d)}')
            continue
        if d['ED'][0] != d['AB'][0] or d['ED'][0] != d['TOT'][0]:
            problems.append(f'turnout {name!r}: RV disagrees {d}')
        elif len(d['ED']) != 2 or len(d['AB']) != 2 or len(d['TOT']) != 2:
            problems.append(f'turnout {name!r}: unexpected shape {d}')
        elif d['ED'][1] + d['AB'][1] != d['TOT'][1]:
            problems.append(f'turnout {name!r}: cards {d["ED"][1]}+'
                            f'{d["AB"][1]} != {d["TOT"][1]}')
    county_turnout = county
    ed = sum(d['ED'][1] for d in front.values())
    ab = sum(d['AB'][1] for d in front.values())
    tot = sum(d['TOT'][1] for d in front.values())
    rv = sum(d['ED'][0] for d in front.values())
    if (ed, ab, tot, rv) != (county_turnout['ED'], county_turnout['AB'],
                             county_turnout['TOT'], county_turnout['RV']):
        problems.append(f'turnout sums {ed}/{ab}/{tot}/{rv} vs county '
                        f'{county_turnout}')
    print(f'{len(front)} precincts from turnout pages; county '
          f'RV {county_turnout["RV"]}, cards {county_turnout["TOT"]}')

    # ---- contest pages ----------------------------------------------
    seq = []            # (precinct, office, district, candidate, party,
                        #  votes, ed, ab)
    bc = {}             # precinct -> Ballots Cast from contest TC? no --
                        # from front pages; verify TC == BC here
    # contest titles (minus the '(Vote for N)' clause) repeat as upright
    # lines inside the rotated header band on every page of a section;
    # with the 'Precinct' label rows they are furniture.  Scanning from
    # y0=66 keeps the first precinct block of each Straight Party page
    # (top ~83), which sits inside the band's y range but is upright.
    band_skip = {'Precinct'}
    for t in titles.values():
        band_skip.add(re.sub(r'\s*\(Vote for.*$', '', t)
                      .lstrip('*').strip())

    for title, (office, district), p0, p1 in contests:
        cells = None
        blocks = collections.OrderedDict()   # prec -> {'ED': [...], ...}
        kct = []                             # (page, numeric tokens)
        cur = None
        for pno in range(p0, p1 + 1):
            page = pdf.pages[pno - 1]
            hdr = header_cells(page)
            if cells is None:
                cells = hdr
            elif hdr != cells:
                problems.append(f'{office}: header differs on p{pno}')
            for top, toks in page_lines(page, y0=66):
                texts = [t for t, _x0, _x1 in toks]
                if not texts:
                    continue
                joined = re.sub(r'\s+', ' ',
                                ' '.join(t for t, _x0, _x1 in toks)).strip()
                base = re.sub(r'\s*\(Vote for.*$', '', joined) \
                    .lstrip('*').strip()
                if not joined or base in band_skip:
                    continue
                if texts[0] == 'Kent County - Total':
                    kct.append((pno, [tk for tk in toks[1:]
                                      if NUM_RE.match(tk[0])]))
                    continue
                if texts[0] in ('Election Day', 'AVCB', 'Total'):
                    key = {'Election Day': 'ED', 'AVCB': 'AV',
                           'Total': 'TOT'}[texts[0]]
                    if cur is None:
                        problems.append(
                            f'{office} p{pno}: {key} row with no precinct')
                    else:
                        blocks[cur][key] = [tk for tk in toks[1:]
                                            if NUM_RE.match(tk[0])]
                    continue
                if joined in front:
                    cur = joined
                    blocks.setdefault(cur, {})
                else:
                    problems.append(
                        f'{office} p{pno}: unknown row {texts[:4]}')

        # column model: every contest column prints in every precinct
        # row except the qualified-write-in columns, which print only
        # where nonzero — so clusters over precinct-row value edges with
        # >=20 prints are the real columns, and the rare ones are the
        # write-in columns.  County-total rows use a second tab layout
        # ~3-17pt left of the precinct stops; assign() maps their tokens
        # to the nearest column center.
        edges = []
        for pno in range(p0, p1 + 1):
            for top, toks in page_lines(pdf.pages[pno - 1], y0=66):
                texts = [t for t, _x0, _x1 in toks]
                if not texts:
                    continue
                if texts[0] in ('Election Day', 'AVCB', 'Total'):
                    edges.extend(tk[2] for tk in toks[1:]
                                 if NUM_RE.match(tk[0]))
        edges.sort()
        clusters = []
        for x in edges:
            if clusters and x - clusters[-1][-1] <= 8:
                clusters[-1].append(x)
            else:
                clusters.append([x])
        centers = [sum(c) / len(c) for c in clusters if len(c) >= 20]
        centers += [sum(c) / len(c) for c in clusters if len(c) < 20]

        labels = [t for _x, t in cells]
        # the qualified-write-in columns break out only in the county
        # row, so the header may carry more cells than precinct rows
        # ever print values for
        if not labels or len(labels) < len(centers) or (
                len(labels) > len(centers)
                and not all(t.endswith('Qualified WI')
                            for t in labels[len(centers):])):
            problems.append(f'{office}: {len(labels)} header cells vs '
                            f'{len(centers)} value columns')
            print(f'  {office}: cells={labels}')
            print(f'  {office}: centers={centers}')
            continue
        if labels[0] != 'Times Cast':
            problems.append(f'{office}: first header {labels[:2]}')
        if 'Registered' not in labels[1]:
            problems.append(f'{office}: second header {labels[:2]}')

        def assign(vals_x1):
            out = [None] * len(labels)
            for (t, _x0, x1) in vals_x1:
                j = min(range(len(centers)),
                        key=lambda k: abs(centers[k] - x1))
                if out[j] is not None:
                    problems.append(f'{office}: column {j} double-assigned')
                out[j] = int(t.replace(',', ''))
            return out

        # re-scan pages with x1s kept, fill per-column values
        blocks = collections.OrderedDict()
        cur = None
        for pno in range(p0, p1 + 1):
            for top, toks in page_lines(pdf.pages[pno - 1], y0=66):
                texts = [t for t, _x0, _x1 in toks]
                if not texts:
                    continue
                if texts[0] in ('Election Day', 'AVCB', 'Total'):
                    key = {'Election Day': 'ED', 'AVCB': 'AV',
                           'Total': 'TOT'}[texts[0]]
                    if cur is not None:
                        blocks[cur][key] = assign(
                            [tk for tk in toks[1:] if NUM_RE.match(tk[0])])
                    continue
                name = re.sub(r'\s+', ' ', ' '.join(
                    t for t, _x0, _x1 in toks)).strip()
                if name in front:
                    cur = name
                    blocks.setdefault(cur, {})

        # verification per precinct (blank cell = 0; only the qualified
        # write-in columns leave cells blank)
        for prec, d in blocks.items():
            if set(d) != {'ED', 'AV', 'TOT'}:
                problems.append(f'{office} {prec!r}: rows {sorted(d)}')
                continue
            for j in range(len(labels)):
                if d['TOT'][j] is None:
                    if d['ED'][j] is not None or d['AV'][j] is not None:
                        problems.append(
                            f'{office} {prec!r} col {j} ({labels[j]}): '
                            f'blank total but ED/AV present')
                    continue
                if j == 1:
                    # Registered Voters repeats the same value in all
                    # three rows rather than summing
                    if not (d['ED'][j] == d['AV'][j] == d['TOT'][j]):
                        problems.append(
                            f'{office} {prec!r} col 1: RV rows disagree '
                            f'{d["ED"][j]}/{d["AV"][j]}/{d["TOT"][j]}')
                    continue
                edv = d['ED'][j] or 0
                abv = d['AV'][j] or 0
                if d['TOT'][j] != edv + abv:
                    problems.append(
                        f'{office} {prec!r} col {j} ({labels[j]}): '
                        f'{edv}+{abv} != {d["TOT"][j]}')
        # Times Cast vs the turnout pages' Ballots Cast; the source
        # itself prints one precinct's TC one ballot below the turnout
        # pages (Alpine Township, Precinct 1: 876 vs 877)
        tc_short = 0
        if len(blocks) == len(front):
            for prec, d in blocks.items():
                delta = front[prec]['TOT'][1] - d['TOT'][0]
                if delta == 1:
                    tc_short += 1
                elif delta != 0:
                    problems.append(
                        f'{office} {prec!r}: TC {d["TOT"][0]} vs cards '
                        f'cast {front[prec]["TOT"][1]}')
            if tc_short:
                print(f'  {office}: {tc_short} precinct(s) with TC one '
                      f'ballot below the turnout pages')
        # county totals: the final Kent County - Total row prints every
        # printed column in x order, so map it positionally when it
        # carries all cells, else by nearest column center
        if not kct:
            problems.append(f'{office}: no Kent County - Total row')
            fin = None
        else:
            ftoks = kct[-1][1]
            if len(ftoks) == len(labels):
                fin = [int(t.replace(',', '')) for t, _x0, _x1 in ftoks]
            else:
                fin = assign(ftoks)
            for j in range(len(labels)):
                got = sum((d['TOT'][j] or 0) for d in blocks.values())
                if j == 0 and tc_short and got == (fin[j] or 0) - tc_short:
                    continue    # covered by the TC check above
                if labels[j].endswith('Qualified WI') and got == 0 \
                        and fin[j]:
                    # the qualified-write-in columns break out only in
                    # the county row; precinct rows carry just the
                    # cumulative Write-in column
                    print(f'  {office} {labels[j]}: qualified write-in '
                          f'total {fin[j]} appears only in the county '
                          f'row')
                    continue
                if got != (fin[j] or 0):
                    problems.append(
                        f'{office} col {j} ({labels[j]}): precinct sum '
                        f'{got} != county total {fin[j] or 0}')
        # emit candidate rows (columns 2..)
        for prec, d in blocks.items():
            for j in range(2, len(labels)):
                if d['TOT'][j] is None and d['ED'][j] is None \
                        and d['AV'][j] is None:
                    continue    # qualified-WI column blank in this precinct
                cand, party = clean_candidate(labels[j], office)
                seq.append((prec, office, district, cand, party,
                            d['TOT'][j] or 0, d['ED'][j] or 0,
                            d['AV'][j] or 0))
        print(f'{office} {district or ""}: {len(blocks)} precincts, '
              f'{len(centers)} columns, county total row on '
              f'{len({p for p, _v in kct})} pages')

    # ---- CENR county-file check --------------------------------------
    covered = {'President', 'U.S. Senate', 'U.S. House', 'State House',
               'Straight Party'}
    want = collections.Counter()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, COUNTY_FILE)
    for row in csv.DictReader(open(path)):
        if row['county'] != COUNTY:
            continue
        if row['office'] in ('Registered Voters', 'Ballots Cast',
                             'Ballots Cast Blank'):
            continue    # pseudo-offices are emitted from the turnout pages
        cand = row['candidate']
        if row['office'] == 'President':
            cand = re.sub(r'\s+w/.*$', '', cand)
        want[(row['office'], row['district'], cand)] += int(row['votes'])
    got = collections.Counter()
    for prec, office, district, cand, party, votes, _ed, _ab in seq:
        got[(office, district, cand)] += votes
    mism = 0
    expected_sp_mism = 0
    for key in sorted(set(want) | set(got)):
        if key not in want:
            continue
        if got.get(key, 0) == want[key]:
            continue
        mism += 1
        if key[0] == 'Straight Party':
            expected_sp_mism += 1
            print(f'EXPECTED SP mismatch {key}: parsed {got.get(key, 0)} '
                  f'vs county {want[key]}')
        else:
            problems.append(f'CENR {key}: parsed {got.get(key, 0)} vs '
                            f'county {want[key]}')
    print(f'CENR check: {len(want)} covered keys, {mism} mismatches '
          f'({expected_sp_mism} expected Straight Party)')

    # ---- comparison against the committed file ------------------------
    old_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            os.pardir, OUT)
    old = {}
    for r in csv.DictReader(open(old_path)):
        old[(r['precinct'], r['office'], r['district'], r['candidate'])] = \
            (int(r['votes']), int(r['election_day'] or 0),
             int(r['absentee'] or 0))
    new = {}
    for prec, office, district, cand, party, votes, edv, abv in seq:
        new[(prec, office, district, cand)] = (votes, edv, abv)
    # Straight Party: compare by party code (names/codes were normalized)
    old_sp_by_key = {}
    for r in csv.DictReader(open(old_path)):
        if r['office'] == 'Straight Party':
            old_sp_by_key[(r['precinct'], OLD_SP.get(r['party'], r['party']))] = \
                (int(r['votes']), int(r['election_day'] or 0),
                 int(r['absentee'] or 0))
    new_sp_by_key = {}
    for prec, office, district, cand, party, votes, edv, abv in seq:
        if office == 'Straight Party':
            new_sp_by_key[(prec, party)] = (votes, edv, abv)
    diff = [k for k in set(old_sp_by_key) & set(new_sp_by_key)
            if old_sp_by_key[k] != new_sp_by_key[k]]
    only_old = set(old_sp_by_key) - set(new_sp_by_key)
    only_new = set(new_sp_by_key) - set(old_sp_by_key)
    print(f'Straight Party vs committed: {len(diff)} value diffs, '
          f'{len(only_old)} old-only, {len(only_new)} new-only')
    for k in diff[:8]:
        print('  SP diff', k, old_sp_by_key[k], '->', new_sp_by_key[k])
    # non-SP covered offices must match the committed values exactly
    nondiff = 0
    for k in set(old) & set(new):
        if k[1] == 'Straight Party':
            continue
        if old[k] != new[k]:
            problems.append(f'COMMITTED DIFF {k}: {old[k]} -> {new[k]}')
        else:
            nondiff += 1
    only_old_n = {k for k in old if k[1] != 'Straight Party'} - set(new)
    only_new_n = {k for k in new if k[1] != 'Straight Party'} - set(old)
    print(f'non-SP covered rows: {nondiff} identical; '
          f'{len(only_old_n)} old-only, {len(only_new_n)} new-only '
          f'(expected: new adds Write-In/Qualified WI columns)')
    for k in list(only_old_n)[:8]:
        print('  old-only', k, old[k])

    if problems:
        print(f'\n{len(problems)} PROBLEMS:')
        for p in problems[:60]:
            print(' -', p)
        sys.exit(1)

    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes', 'election_day', 'absentee'])
        for prec in front:
            w.writerow([COUNTY, prec, 'Registered Voters', '', '', '',
                        front[prec]['ED'][0], '', ''])
        for prec in front:
            w.writerow([COUNTY, prec, 'Ballots Cast', '', '', '',
                        front[prec]['TOT'][1], front[prec]['ED'][1],
                        front[prec]['AB'][1]])
        for row in seq:
            w.writerow([COUNTY] + list(row))
    print(f'wrote {OUT}: {len(seq) + 2 * len(front)} rows')


if __name__ == '__main__':
    main()