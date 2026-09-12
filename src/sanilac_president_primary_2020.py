#!/usr/bin/env python3
"""Parse Sanilac County's Mar 2020 presidential primary from
'Sanilac MI StatementOfVotesCastRPT.pdf' (24pp ES&S 'Statement of
Votes Cast', landscape 792x612, text-extractable).

Layout (differs from Barry/Lapeer):
  - Pages 1-2: turnout table, upright headers -- Registered Voters,
    Cards Cast, Voters Cast, % Turnout (RV first, no Ballots Cast).
    The header words sit on separate lines ('Registered', 'Cards
    Cast', 'Precinct Voters Cast % Turnout', 'Voters'); values are
    RIGHT-ALIGNED and each precinct's row is split over two lines
    1pt apart (RV + Cards Cast above, label + Voters Cast + % Turnout
    below -- or all four on one line), so rows are clustered with a
    3pt top tolerance and columns anchored on the values' right edge
    x1 (phrase centers sit ~18pt left of the values).
  - Pages 3-10: DEM President contest printed as FOUR column groups
    (pp3-4 Bennet/Booker, pp5-6 Buttigieg/Klobuchar, pp7-8
    Sanders/Williamson, pp9-10 Yang/Uncommitted/Total Votes); pp11-14
    REP (Sanford..Weld then Uncommitted/Total Votes).  Every column
    group re-prints Times Cast and Registered Voters, so duplicated
    TC/RV values across groups must agree.  Each page prints TWO
    side-by-side tables whose rows interleave with 1-3pt offsets:
    left = label + Times Cast + Registered Voters (RV on its own
    line above), right = label + one value/percentage pair per
    candidate (or a bare Total Votes value).  The tables are parsed
    separately (sliced at x=255: the left RV values end <=248.3, the
    right labels start >=257.6) and rows merge by precinct label.
  - Pages 15-24: seven proposals (County Recycling/Library/Med
    Control Millage span two pages each; Forester/Fremont/Marion TWP
    and Moore Twp. millages fit one page), same two-table structure
    with Yes/No value+pct pairs and a bare Total Votes.
  - Rotated headers are parsed at char level: chars of one vertical
    line share an x0 and read bottom-to-top; wrap lines sit 12.2-12.3
    pt to the right (cluster tolerance 13, wider than Barry's 12).
    Names print with a '(DEM)'/'(REP)' suffix and 'Uncommitted' is
    clipped to 'Uncommitt' + 'ed' (canon() re-joins by ignoring
    spaces).  'Julián Castro' arrives accented (canon() strips it).
  - Word extraction uses x_tolerance=2: the left RV value's last
    digit can sit 2.9pt from the right table's first label word
    ('1,676City') and the default tolerance of 3 fuses them.
  - 'DEM' / 'REP' print as their own line, followed by a
    '**** - Insufficient Turnout to Protect Voter Privacy' banner
    line (present on proposal pages too) -- both are skipped.

Emission: Registered Voters, 16 DEM candidates + Ballots Cast (= the
contest's Total Votes), 5 REP candidates + Ballots Cast, and Yes/No
rows for each proposal's in-scope precincts.  There is NO Unresolved
Write-In column anywhere in this report.

Verification: per precinct, candidate sum == Total Votes (DEM and
REP); Yes+No == Total Votes; Times Cast >= Total Votes; every
percentage pair equals round(value / Times Cast * 100, 2); every
column's precinct sums == the printed county-total row; turnout
RV/Cards Cast/Voters Cast sums == the printed county row and Cards
Cast == Voters Cast everywhere; Cumulative rows are zero.
"""

import csv
import os
import re
import sys
import unicodedata

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Sanilac MI StatementOfVotesCastRPT.pdf')
OUT = ('2020/counties/20200310__mi__primary__president'
       '__sanilac__precinct.csv')
COUNTY = 'Sanilac'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
       'Cory Booker', 'Pete Buttigieg', 'Julian Castro',
       'John Delaney', 'Tulsi Gabbard', 'Amy Klobuchar',
       'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

AUX = {'Times Cast': 'TC', 'Registered Voters': 'RV',
       'Total Votes': 'TV'}

TURNOUT_COLS = ['Registered Voters', 'Cards Cast', 'Voters Cast',
                '% Turnout']

X_SPLIT = 255          # left table | right table boundary
COUNTY_LABELS = ('Precinct', 'County', 'Sanilac County, Michigan',
                 'Sanilac County,', 'Michigan', 'Cumulative',
                 'Registered', 'Voters')

EXPECTED_PRECINCTS = 31


def canon(name):
    """Normalize a parsed header to a canonical column name."""
    name = re.sub(r' \((DEM|REP)\)$', '', name)
    name = unicodedata.normalize('NFC', name)
    name = name.replace('á', 'a')   # 'Julián Castro' -> repo convention
    if name in AUX:
        return AUX[name]
    if name.replace(' ', '') in ('Uncommitted', 'Uncommittted'):
        return 'Uncommitted'
    return name


def is_value(text):
    """A table value word: int or a percentage like '23.00%'."""
    t = text.replace(',', '')
    return t.isdigit() or re.fullmatch(r'\d+\.\d+%', t)


def is_pct(text):
    return bool(re.fullmatch(r'\d+\.\d+%', text))


def rotated_chars(page):
    return [c for c in page.chars if not c.get('upright', True)]


def parse_clusters(rot):
    """Group rotated chars into header clusters (wrap lines sit
    ~12.3pt right).  One vertical line = chars sharing an x0, read
    bottom-to-top.  Returns [(x_center, text)] sorted by x."""
    lines = {}
    for c in rot:
        lines.setdefault(round(c['x0'], 1), []).append(c)
    vlines = []
    for x0 in sorted(lines):
        cs = sorted(lines[x0], key=lambda c: -c['top'])
        text = ''.join(c['text'] for c in cs).strip()
        if text:
            cx = sum((c['x0'] + c['x1']) / 2 for c in cs) / len(cs)
            vlines.append((x0, cx, text))
    clusters = []
    for x0, cx, text in vlines:
        if clusters and x0 - clusters[-1]['maxx0'] <= 13:
            cl = clusters[-1]
            cl['texts'].append(text)
            cl['cxs'].append(cx)
            cl['maxx0'] = x0
        else:
            clusters.append({'texts': [text], 'cxs': [cx],
                             'maxx0': x0})
    out = [(sum(cl['cxs']) / len(cl['cxs']), ' '.join(cl['texts']))
           for cl in clusters]
    out.sort()
    return out


def top_clusters(words, tol=3):
    """Group words into rows by top (the two interleaved tables and a
    row's own two lines sit 1-3pt apart)."""
    groups = []
    for w in sorted(words, key=lambda w: w['top']):
        if groups and w['top'] - groups[-1]['top0'] <= tol:
            groups[-1]['words'].append(w)
            groups[-1]['top0'] = max(groups[-1]['top0'], w['top'])
        else:
            groups.append({'top0': w['top'], 'words': [w]})
    return groups


def col_anchors(vals, tol=6):
    """Cluster value words by right edge x1 (values are right-aligned
    per column); returns [(x1_anchor, x_center)] per cluster sorted
    by x1."""
    swords = sorted(vals, key=lambda w: w['x1'])
    groups = [[swords[0]]]
    for w in swords[1:]:
        if w['x1'] - groups[-1][-1]['x1'] <= tol:
            groups[-1].append(w)
        else:
            groups.append([w])
    return [(max(w['x1'] for w in g),
             sum((w['x0'] + w['x1']) / 2 for w in g) / len(g))
            for g in groups]


def assemble_rows(twords, cols, problems, ctx, pct_ok):
    """Shared row assembly: cluster words into rows by top, assign
    value words to columns by right edge x1 (delta <= 8), keep pct
    words per column if pct_ok, the rest is the row label.  Each
    wrapped-label fragment attaches to exactly one row -- preferring
    the row ABOVE it (a label tail like 'Precinct' / '1' printed on
    the next line) over the row it sits above (a label head).
    Returns rows = [(name, {col: value}, {col: pct})]."""
    rows = []
    frag_lines = []
    for cl in top_clusters(twords):
        vmap = {}
        pcmap = {}
        labels = []
        for w in sorted(cl['words'], key=lambda w: w['x0']):
            if is_value(w['text']):
                if pct_ok and is_pct(w['text']):
                    # attach to the nearest value column to the LEFT
                    best = None
                    for ci, (x1, _n) in enumerate(cols):
                        if x1 <= w['x0'] and (best is None
                                              or x1 > cols[best][0]):
                            best = ci
                    if best is not None \
                            and w['x0'] - cols[best][0] <= 60:
                        pcmap[best] = w['text']
                    continue
                best = min(range(len(cols)),
                           key=lambda ci: abs(cols[ci][0] - w['x1']))
                if abs(cols[best][0] - w['x1']) <= 8:
                    if best in vmap:
                        problems.append(f'{ctx}: two values for column '
                                        f'{cols[best][1]} at top '
                                        f'{cl["top0"]}')
                    vmap[best] = w['text'].replace(',', '')
                    continue
            labels.append(w)
        text = ' '.join(w['text'] for w in labels)
        text = ' '.join(text.split())
        if vmap:
            rows.append((cl['top0'], text, vmap,
                         {cols[ci][1]: p for ci, p in pcmap.items()}))
        elif text and text not in COUNTY_LABELS:
            frag_lines.append((cl['top0'], text))
    heads = [[] for _ in rows]
    tails = [[] for _ in rows]
    for ftop, ftext in frag_lines:
        tcand = [(ftop - r[0], i) for i, r in enumerate(rows)
                 if 0 <= ftop - r[0] <= 13.5]
        if tcand:
            tails[min(tcand)[1]].append((ftop, ftext))
            continue
        hcand = [(r[0] - ftop, i) for i, r in enumerate(rows)
                 if 0 < r[0] - ftop <= 8]
        if hcand:
            heads[min(hcand)[1]].append((ftop, ftext))
            continue
        problems.append(f'{ctx}: unassigned fragment {ftext!r} '
                        f'(top {ftop:.1f})')
    out = []
    for i, (vtop, inline, vmap, pcmap) in enumerate(rows):
        hs = [t for _, t in sorted(heads[i])]
        ts = [t for _, t in sorted(tails[i])]
        name = ' '.join(hs + [inline] + ts).strip()
        name = ' '.join(name.split())
        out.append((name, {cols[ci][1]: v for ci, v in vmap.items()},
                    pcmap))
    return out


def parse_turnout(words, problems, ctx):
    """Turnout pages: upright headers, 4 right-aligned columns in
    x order (RV, Cards Cast, Voters Cast, % Turnout)."""
    nums = [w for w in words if is_value(w['text'])]
    if not nums:
        return [], {}
    anchors = col_anchors(nums)
    big = [(x1, cx) for x1, cx in anchors
           if sum(1 for w in nums if abs(w['x1'] - x1) <= 3) >= 8]
    if len(big) != len(TURNOUT_COLS):
        problems.append(f'{ctx}: {len(big)} turnout columns '
                        f'({anchors}) != {len(TURNOUT_COLS)}')
        return [], {}
    cols = [(x1, n) for (x1, _cx), n in zip(big, TURNOUT_COLS)]
    return [(n, v) for n, v, _p in
            assemble_rows(words, cols, problems, ctx, pct_ok=False)], {}


def parse_slice(words, headers, problems, ctx):
    """One side-by-side table slice.  headers: rotated header clusters
    [(x_center, text)] -- continuation pages carry none, so the
    caller passes the ones from the group's first page.  Each header
    is paired with its nearest value column (within 45pt); leftover
    value clusters are precinct-label digits and are dropped.
    Returns (rows, col_names, pcts); rows = [(label, {col: value})]."""
    if not words:
        return [], [], {}
    vals = [w for w in words if is_value(w['text'])
            and not is_pct(w['text'])]
    if not vals:
        return [], [], {}
    vcols = col_anchors(vals)
    cols = []
    used = set()
    for cx, text in sorted(headers):
        cand = [i for i in range(len(vcols)) if i not in used]
        if not cand:
            problems.append(f'{ctx}: header {text!r} has no free '
                            f'value column')
            continue
        best = min(cand, key=lambda i: abs(vcols[i][0] - cx))
        if abs(vcols[best][0] - cx) > 45:
            problems.append(f'{ctx}: header {text!r} ({cx:.0f}) has '
                            f'no value column nearby (nearest '
                            f'{vcols[best][0]:.0f})')
            continue
        used.add(best)
        cols.append((vcols[best][0], canon(text)))
    if not cols:
        return [], [], {}
    cols.sort()
    rows = assemble_rows(words, cols, problems, ctx, pct_ok=True)
    return rows, [n for _x1, n in cols], None


def page_lines(page):
    """Upright text lines of the page: {top: (text, words)}
    (x_tolerance=2)."""
    ups = page.filter(lambda obj: obj.get('upright', True))
    words = ups.extract_words(x_tolerance=2)
    lines = {}
    for w in words:
        lines.setdefault(round(w['top']), []).append(w)
    out = {}
    for top, ws in lines.items():
        ws = sorted(ws, key=lambda w: w['x0'])
        out[top] = (' '.join(w['text'] for w in ws), ws)
    return out


def phrase_centers(words):
    """Group a line's words into phrases by x-gap; return
    (center, text) per phrase."""
    ws = sorted(words, key=lambda w: w['x0'])
    groups = [[ws[0]]]
    for w in ws[1:]:
        if w['x0'] - groups[-1][-1]['x1'] <= 6:
            groups[-1].append(w)
        else:
            groups.append([w])
    return [(sum((w['x0'] + w['x1']) / 2 for w in g) / len(g),
             ' '.join(w['text'] for w in g)) for g in groups]


def main():
    pdf = pdfplumber.open(SRC)
    problems = []

    turnout_rows = []           # [(label, {col: value})]
    contests = {}               # title -> {'party':, 'rows': [...]}
    contest_order = []
    cur = None                  # ('turnout',) | ('contest', title)

    for page in pdf.pages:
        lines = page_lines(page)
        skip_tops = set()
        for top in sorted(lines):
            text = lines[top][0]
            if re.match(r'^Page: \d+ of \d+', text) \
                    or text.startswith(('Statement of Votes Cast',
                                        'Closed Primary',
                                        'March 10, 2020', 'SOVC for:',
                                        'OFFICIAL RESULTS',
                                        'UNOFFICIAL')) \
                    or text == 'Sanilac County, Michigan':
                skip_tops.add(top)
                continue
            m = re.match(r'^(.*?) ?\(Vote for( \d+)?\)?$', text)
            if m:
                title = m.group(1).strip()
                cur = ('contest', title)
                contests.setdefault(title, {'party': None, 'rows': []})
                if title not in contest_order:
                    contest_order.append(title)
                skip_tops.add(top)
                continue
            if text.startswith('****') \
                    or 'Insufficient Turnout' in text:
                skip_tops.add(top)
                continue
            if text in ('DEM', 'REP'):
                if cur and cur[0] == 'contest':
                    contests[cur[1]]['party'] = text
                skip_tops.add(top)
                continue
            if 'Voters Cast' in text \
                    or text in ('Registered', 'Voters', 'Cards Cast'):
                # turnout header words sit on several lines
                cur = ('turnout',)
                skip_tops.add(top)
                continue
            if not re.search(r'\d', text) \
                    and set(text.split()) <= {'Sanilac', 'County,',
                                              'Michigan', 'Precinct',
                                              'County', 'Cumulative',
                                              'Registered', 'Voters',
                                              'Cast', 'Cards', '%',
                                              'Turnout'}:
                # table sub-headers ('Precinct Precinct',
                # 'Sanilac County, Michigan Sanilac County, Michigan',
                # 'Cumulative Cumulative', the stacked turnout header
                # fragments) share lines across the two tables
                skip_tops.add(top)
                continue

        if cur is None:
            continue
        rest = []
        for top in sorted(lines):
            if top in skip_tops:
                continue
            rest.extend(lines[top][1])

        if cur[0] == 'turnout':
            rows, _pcts = parse_turnout(rest, problems,
                                        f'page {page.page_number} '
                                        f'turnout')
            turnout_rows.extend(rows)
            continue
        # contest/proposal page: two interleaved tables; continuation
        # pages carry no rotated headers, so reuse the ones parsed on
        # the contest's first page
        rot_all = rotated_chars(page)
        left = [w for w in rest if w['x0'] < X_SPLIT]
        right = [w for w in rest if w['x0'] >= X_SPLIT]
        lrot = [c for c in rot_all if c['x0'] < X_SPLIT]
        rrot = [c for c in rot_all if c['x0'] >= X_SPLIT]
        title = cur[1]
        ctx = f'page {page.page_number}'
        lheads = parse_clusters(lrot)
        rheads = parse_clusters(rrot)
        if lheads:
            contests[title]['lheads'] = lheads
        if rheads:
            contests[title]['rheads'] = rheads
        lrows, lcols, _ = parse_slice(
            left, lheads or contests[title].get('lheads', []),
            problems, ctx + ' left')
        rrows, rcols, _ = parse_slice(
            right, rheads or contests[title].get('rheads', []),
            problems, ctx + ' right')
        if lrows and lcols != ['TC', 'RV']:
            problems.append(f'{ctx}: left columns {lcols} != '
                            f'["TC", "RV"]')
        # merge the two tables' rows: when one table has no label
        # column (the 5- and 3-candidate pages), pair the rows
        # positionally; otherwise merge by precinct label
        unl_r = bool(rrows) and all(n == '' for n, _v, _p in rrows)
        unl_l = bool(lrows) and all(n == '' for n, _v, _p in lrows)
        page_rows = []
        if unl_r or unl_l:
            lab, unl = (lrows, rrows) if unl_r else (rrows, lrows)
            if len(lab) != len(unl):
                problems.append(f'{ctx}: {len(unl)} unlabeled rows vs '
                                f'{len(lab)} labeled rows')
            for i, (name, vals, pc) in enumerate(lab):
                vals = dict(vals)
                pc = dict(pc)
                if i < len(unl):
                    _n, rvals, rpc = unl[i]
                    for c, v in rvals.items():
                        old = vals.get(c)
                        if old is not None and old != v:
                            problems.append(f'{ctx}: {name} {c} '
                                            f'{old} != {v}')
                        vals[c] = v
                    pc.update(rpc)
                page_rows.append((name, vals, pc))
        else:
            by_name = {}
            for side_rows in (lrows, rrows):
                for name, vals, pc in side_rows:
                    if name in by_name:
                        vals_old, pc_old = by_name[name]
                        for c, v in vals.items():
                            old = vals_old.get(c)
                            if old is not None and old != v:
                                problems.append(f'{ctx}: {name} {c} '
                                                f'{old} != {v}')
                            vals_old[c] = v
                        pc_old.update(pc)
                    else:
                        by_name[name] = [dict(vals), dict(pc)]
            page_rows = [(n, v, p) for n, (v, p) in by_name.items()]
        contests[title]['rows'].extend((n, v) for n, v, _p in page_rows)
        contests[title].setdefault('pcts', {})
        for name, _v, pc in page_rows:
            if pc:
                contests[title]['pcts'].setdefault(name, {}).update(pc)

    # ---- merge contests -------------------------------------------------
    merged = {}     # title -> col -> {label: value}
    pct_merged = {}  # title -> {(label, col): pct}
    ctotals = {}    # title -> col -> value
    for title in contest_order:
        merged[title] = {}
        ctotals[title] = {}
        pct_merged[title] = {}
        seen_dup = set()
        for name, vals in contests[title]['rows']:
            if name == 'Sanilac County, Michigan - Total' \
                    or name.startswith('County - Total'):
                for c, v in vals.items():
                    if c in ctotals[title] and ctotals[title][c] != v:
                        problems.append(f'{title}: county total {c} '
                                        f'{ctotals[title][c]} != {v}')
                    ctotals[title][c] = v
                continue
            if name.startswith('Cumulative'):
                for c, v in vals.items():
                    if v not in ('0', 'N/A'):
                        problems.append(f'{title}: {name} {c} nonzero '
                                        f'{v}')
                continue
            if not name or not re.search(r'(Township|City of|Twp)',
                                         name):
                problems.append(f'{title}: suspicious row {name!r} '
                                f'{vals}')
                continue
            for c, v in vals.items():
                col = merged[title].setdefault(c, {})
                if c in seen_dup and name in col and col[name] != v:
                    problems.append(f'{title}: {name} {c} '
                                    f'{col[name]} != {v}')
                col[name] = v
            seen_dup.update(set(vals))
        for name, pc in contests[title].get('pcts', {}).items():
            for col, p in pc.items():
                pct_merged[title][(name, col)] = p

    # ---- structure ------------------------------------------------------
    precincts = []
    for name, _v in turnout_rows:
        if name == 'Sanilac County, Michigan - Total' \
                or name.startswith(('County - Total', 'Cumulative')):
            continue
        if name not in precincts:
            precincts.append(name)
    if len(precincts) != EXPECTED_PRECINCTS:
        problems.append(f'{len(precincts)} precincts, expected '
                        f'{EXPECTED_PRECINCTS}: {precincts}')

    dem_title, rep_title = contest_order[0], contest_order[1]
    prop_titles = contest_order[2:]
    if len(prop_titles) != 7:
        problems.append(f'{len(prop_titles)} proposals, expected 7: '
                        f'{prop_titles}')
    if contests[dem_title]['party'] != 'DEM' \
            or contests[rep_title]['party'] != 'REP':
        problems.append(f'party tokens: {contests[dem_title]["party"]} '
                        f'/ {contests[rep_title]["party"]}')
    got_dem = [c for c in merged[dem_title] if c not in AUX.values()]
    got_rep = [c for c in merged[rep_title] if c not in AUX.values()]
    if got_dem != DEM:
        problems.append(f'DEM columns {got_dem} != {DEM}')
    if got_rep != REP:
        problems.append(f'REP columns {got_rep} != {REP}')
    for title in (dem_title, rep_title):
        for c in merged[title]:
            missing = [p for p in precincts
                       if p not in merged[title][c]]
            if missing:
                problems.append(f'{title}: column {c} missing '
                                f'precincts {missing}')
    # proposals: every column covers the same (scoped) precinct set
    prop_pset = {}
    for title in prop_titles:
        psets = [{p for p in merged[title][c]} for c in merged[title]]
        if len({frozenset(s) for s in psets}) != 1:
            problems.append(f'{title}: proposal columns cover '
                            f'different precinct sets')
            prop_pset[title] = set().union(*psets)
        else:
            prop_pset[title] = psets[0]
        if not prop_pset[title] <= set(precincts):
            problems.append(f'{title}: unknown precincts '
                            f'{prop_pset[title] - set(precincts)}')

    # ---- per-precinct checks -------------------------------------------
    dem = merged[dem_title]
    rep = merged[rep_title]
    # 3 precincts print Total Votes 1 higher than the candidate sum
    # (every candidate's printed pct confirms the printed value, e.g.
    # Lexington Biden 269/476 = 56.51%; county rows agree with the
    # precinct rows) — overvotes/unresolved counted in TV, keep as
    # printed.
    overvote = {('DEM', 'Lexington Township, Precinct 1'),
                ('REP', 'City of Croswell, Precinct 1'),
                ('REP', 'Speaker Township, Precinct 1')}
    for p in precincts:
        s = sum(int(dem[c][p]) for c in DEM)
        if s != int(dem['TV'][p]) and ('DEM', p) not in overvote:
            problems.append(f'DEM {p!r}: candidates {s} != TV '
                            f'{dem["TV"][p]}')
        s = sum(int(rep[c][p]) for c in REP)
        if s != int(rep['TV'][p]) and ('REP', p) not in overvote:
            problems.append(f'REP {p!r}: candidates {s} != TV '
                            f'{rep["TV"][p]}')
        if int(dem['TC'][p]) < int(dem['TV'][p]) \
                or int(rep['TC'][p]) < int(rep['TV'][p]):
            problems.append(f'{p!r}: Times Cast < Total Votes')
    for title in (dem_title, rep_title) + tuple(prop_titles):
        for (name, col), ptext in sorted(pct_merged[title].items()):
            if col in ('TC', 'RV') or name.startswith('Cumulative'):
                continue
            # the printed pct is v / contest Total Votes (NOT Times
            # Cast — verified: Yes 107/131 = 81.68% with TV 131, TC 136)
            tv = merged[title].get('TV', {}).get(name)
            v = merged[title].get(col, {}).get(name)
            if tv is None or v is None:
                continue    # county-total rows carry pcts too
            if int(tv) == 0:
                want = 0.0
            else:
                want = round(int(v) / int(tv) * 100, 2)
            if abs(want - float(ptext[:-1])) > 0.005:
                problems.append(f'{title} {name!r} {col}: '
                                f'{v}/{tv} = {want}% != printed '
                                f'{ptext}')
    for title in prop_titles:
        prop = merged[title]
        for p in sorted(prop_pset[title]):
            y, n = int(prop['Yes'][p]), int(prop['No'][p])
            if y + n != int(prop['TV'][p]):
                problems.append(f'{title} {p!r}: Yes+No {y + n} != TV '
                                f'{prop["TV"][p]}')

    # ---- county totals --------------------------------------------------
    for title, names in [(dem_title, DEM), (rep_title, REP)] \
            + [(t, ['Yes', 'No']) for t in prop_titles]:
        cols = merged[title]
        for c in names:
            s = sum(int(cols[c][p]) for p in precincts
                    if p in cols[c])
            want = ctotals[title].get(c)
            if want is None:
                problems.append(f'{title}: no county total for {c}')
            elif s != int(want):
                problems.append(f'{title}: {c} sums {s} != county '
                                f'total {want}')
    turnout_by = {n: v for n, v in turnout_rows}
    county_row = turnout_by.get('Sanilac County, Michigan - Total') \
        or turnout_by.get('County - Total')
    for c in ('Registered Voters', 'Cards Cast', 'Voters Cast'):
        s = sum(int(turnout_by[p][c]) for p in precincts)
        want = county_row.get(c) if county_row else None
        if want is None:
            problems.append(f'turnout: no county total for {c}')
        elif s != int(want):
            problems.append(f'turnout {c} sum {s} != county {want}')
    for p in precincts:
        if turnout_by[p]['Cards Cast'] != turnout_by[p]['Voters Cast']:
            problems.append(f'{p!r}: Cards Cast != Voters Cast')
        pct = round(int(turnout_by[p]['Voters Cast'])
                    / int(turnout_by[p]['Registered Voters']) * 100, 2)
        if abs(pct - float(turnout_by[p]['% Turnout'][:-1])) > 0.005:
            problems.append(f'{p!r}: turnout {pct}% != printed '
                            f'{turnout_by[p]["% Turnout"]}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)

    # ---- emission -------------------------------------------------------
    rows_out = []
    for p in precincts:
        rows_out.append([COUNTY, p, 'Registered Voters', '', '', '',
                         turnout_by[p]['Registered Voters']])
    for c in DEM:
        for p in precincts:
            rows_out.append([COUNTY, p, 'President', '', 'DEM', c,
                             dem[c][p]])
    for p in precincts:
        rows_out.append([COUNTY, p, 'Ballots Cast', '', 'DEM', '',
                         dem['TV'][p]])
    for c in REP:
        for p in precincts:
            rows_out.append([COUNTY, p, 'President', '', 'REP', c,
                             rep[c][p]])
    for p in precincts:
        rows_out.append([COUNTY, p, 'Ballots Cast', '', 'REP', '',
                         rep['TV'][p]])
    for title in prop_titles:
        prop = merged[title]
        for p in sorted(prop_pset[title]):
            for cand, v in (('Yes', prop['Yes'][p]),
                            ('No', prop['No'][p])):
                rows_out.append([COUNTY, p, title, '', '', cand, v])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(precincts)} '
          f'precincts, proposals: {prop_titles}')


if __name__ == '__main__':
    main()