#!/usr/bin/env python3
"""Parse Lapeer County's Mar 2020 presidential primary from
'Lapeer MI Prec31020.pdf' (22pp ES&S 'Statement of Votes Cast',
landscape 792x612, text-extractable, 37 precincts).  Same format as
Barry's (see barry_president_primary_2020.py); differences called out
below.

Layout:
  - Pages 1-2: turnout table (upright headers) -- Registered Voters,
    Cards Cast, Voters Cast, % Turnout per precinct (RV is the FIRST
    data column; there is no Ballots Cast column).
  - Pages 3-10: DEM President contest printed as two runs (precincts
    1-25 on pp3-6, the rest on pp7-10); pp11-14 REP.  Each run carries
    a Times Cast/Registered Voters table, candidate tables, and a
    Total Votes / Unresolved Write-In table.  Tables are side-by-side
    (one 'Precinct' label column per table) and each ends with
    'County - Total', 'Cumulative', 'Cumulative - Total' rows (the
    county totals come ONLY from the 'County - Total' rows; there is
    no 'Barry County Michigan - Total' title-page row).  Columns merge
    by (column name, precinct label); duplicated Times Cast/Registered
    Voters values across the two runs must agree.
  - Pages 15-22: four SCOPED proposals (Brandon School District
    Bonding Proposal, Davison Community Schools Bonding Proposal,
    Marlette Community Schools Operating Millage Renewal, Mott
    Community College Bond Proposition), each a title page (TC/RV +
    Yes/No/Total Votes) plus an Unresolved Write-In page.  Only the
    precincts inside each district print rows.
  - Rotated candidate headers are parsed at CHAR level (chars carry
    upright=False): chars of one vertical line share an x0 and read
    bottom-to-top (descending top), wrap lines sit ~10.7pt to the
    right and read left-to-right.  Candidates are hardcoded and
    cross-checked against the parsed header text.
  - Precinct names wrap head/values/tail; fragments attach to the
    nearest value line within 12pt (heads above, tails below).

Emission: Registered Voters (turnout table), DEM President rows for
the 16 named candidates + Ballots Cast (= the contest's Total Votes),
REP rows for its 5 candidates + Ballots Cast, and Yes/No rows for each
proposal's in-scope precincts.  The 'Unresolved Write-In' column is NOT
emitted: candidates sum exactly to Total Votes with the unresolved
write-ins held outside it (countywide DEM 1, REP 1), so emitting them
would break Ballots Cast == candidate sum; they are printed for the
record.

Verification: per precinct, candidate sum == Total Votes (DEM and REP)
and Yes+No == Total Votes for in-scope proposal precincts; Times Cast
>= Total Votes; every column's precinct sums == the printed county
total row; Registered Voters, Cards Cast and Voters Cast sum to the
printed county row (Cards Cast == Voters Cast everywhere); the
Cumulative rows are zero and 'County - Total' repeats the county total.
"""

import csv
import os
import re
import sys
import unicodedata

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Lapeer MI Prec31020.pdf')
OUT = '2020/counties/20200310__mi__primary__president__lapeer__precinct.csv'
COUNTY = 'Lapeer'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
       'Cory Booker', 'Pete Buttigieg', 'Julian Castro',
       'John Delaney', 'Tulsi Gabbard', 'Amy Klobuchar',
       'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

AUX = {'Times Cast': 'TC', 'Registered Voters': 'RV',
       'Total Votes': 'TV', 'Unresolved Write-In': 'WI'}

TURNOUT_COLS = ['Registered Voters', 'Cards Cast', 'Voters Cast',
                '% Turnout']

COUNTY_LABELS = ('Precinct', 'County', 'Lapeer County Michigan',
                 'Lapeer County', 'Michigan', 'Cumulative',
                 'Registered', 'Voters')


def canon(name):
    """Normalize a parsed header to a canonical column name."""
    name = re.sub(r' \((DEM|REP)\)$', '', name)
    name = unicodedata.normalize('NFC', name)
    name = name.replace('á', 'a')   # 'Julián Castro' -> repo convention
    if name in AUX:
        return AUX[name]
    return name


def is_value(text):
    """A table value word: int, N/A, or a percentage like '23.00%'
    (the turnout table's % Turnout column)."""
    t = text.replace(',', '')
    return t.isdigit() or t == 'N/A' or re.fullmatch(r'\d+\.\d+%', t)


def rotated_chars(page):
    return [c for c in page.chars if not c.get('upright', True)]


def parse_clusters(rot):
    """Group rotated chars into header clusters.  One vertical line =
    chars sharing x0 (read bottom-to-top, descending top); wrap lines
    sit ~10.7pt right and read left-to-right.  Returns [(x_center,
    text)]."""
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
        if clusters and x0 - clusters[-1]['maxx0'] <= 12:
            cl = clusters[-1]
            cl['texts'].append(text)
            cl['cxs'].append(cx)
            cl['maxx0'] = x0
        else:
            clusters.append({'texts': [text], 'cxs': [cx],
                             'maxx0': x0})
    return [(sum(cl['cxs']) / len(cl['cxs']), ' '.join(cl['texts']))
            for cl in clusters]


def parse_table(twords, rot, col_names, problems, ctx, anchor_hint=None):
    """Parse one table.  twords: upright words of the table; rot: its
    rotated chars.  anchor_hint: centers of the upright header phrases
    (turnout pages), used instead of clustering the data words (whose
    label digits 'Precinct 1' would form spurious clusters).  Returns
    (rows, cols) with rows = [(label, {col: value})]."""
    if not twords:
        return [], []
    clusters = parse_clusters(rot)
    nums = [w for w in twords if is_value(w['text'])]
    if not nums:
        return [], []
    centers = sorted({(w['x0'] + w['x1']) / 2 for w in nums})
    if clusters:
        cols = []
        for cx, text in clusters:
            cand = min(centers, key=lambda c: abs(c - cx))
            if abs(cand - cx) > 30:
                problems.append(f'{ctx}: header {text!r} has no data '
                                f'column nearby (nearest {cand:.0f} vs '
                                f'{cx:.0f})')
                continue
            cols.append((cand, canon(text)))
    else:
        # upright-header table (turnout pages): values are RIGHT-
        # aligned, so anchor columns on the words' right edge x1 (a
        # narrow value like '0' drifts left of no one; its center
        # would).  Words left of the header phrases are precinct-label
        # digits ('Precinct 1') and are dropped first.
        if anchor_hint:
            vals = [w for w in nums
                    if w['x1'] > min(anchor_hint) - 10]
        else:
            vals = nums
        swords = sorted(vals, key=lambda w: w['x1'])
        groups = [[swords[0]]]
        for w in swords[1:]:
            if w['x1'] - groups[-1][-1]['x1'] <= 6:
                groups[-1].append(w)
            else:
                groups.append([w])
        vtops = {round(w['top']) for w in vals}
        anchors = [max(w['x1'] for w in g) for g in groups
                   if len(g) >= 0.6 * len(vtops)]
        if col_names is None or len(anchors) != len(col_names):
            problems.append(f'{ctx}: {len(anchors)} data columns != '
                            f'{col_names}')
            return [], []
        cols = [(c, n) for c, n in zip(anchors, col_names)]
    anchors = [c for c, _t in cols]

    # group words into lines by top; value words join their column,
    # the rest is the row label
    lines = {}
    for w in twords:
        lines.setdefault(round(w['top']), []).append(w)
    val_lines = []      # (top, {col_idx: text}, label_text)
    frag_lines = []     # (top, label_text)
    for top in sorted(lines):
        ws = sorted(lines[top], key=lambda w: w['x0'])
        vmap = {}
        labels = []
        for w in ws:
            c = (w['x0'] + w['x1']) / 2
            if is_value(w['text']):
                if clusters:
                    best = min(anchors, key=lambda a: abs(a - c))
                    delta = abs(best - c)
                else:
                    # right-aligned upright columns: match on x1
                    best = min(anchors, key=lambda a: abs(a - w['x1']))
                    delta = abs(best - w['x1'])
                if delta <= 12:
                    ci = anchors.index(best)
                    if ci in vmap:
                        problems.append(f'{ctx}: two values for column '
                                        f'{ci} at top {top}')
                    vmap[ci] = w['text'].replace(',', '')
                    continue
            labels.append(w)
        text = ' '.join(w['text'] for w in labels)
        if vmap:
            val_lines.append((top, vmap, text))
        elif text in COUNTY_LABELS \
                or 'Cards Cast' in text:
            continue    # table sub-headers
        else:
            frag_lines.append((top, text))
    # assemble rows: fragments attach to the nearest value line
    rows = []
    for vtop, vmap, inline in val_lines:
        heads, tails = [], []
        for ftop, ftext in frag_lines:
            if abs(ftop - vtop) <= 12:
                (heads if ftop < vtop else tails).append((ftop, ftext))
        heads.sort()
        tails.sort()
        name = ' '.join([t for _, t in heads] + [inline]
                        + [t for _, t in tails]).strip()
        name = ' '.join(name.split())
        rows.append((name, {cols[ci][1]: v for ci, v in vmap.items()}))
    for ftop, ftext in frag_lines:
        if not any(abs(ftop - v[0]) <= 12 for v in val_lines):
            problems.append(f'{ctx}: unassigned fragment {ftext!r} '
                            f'(top {ftop:.1f})')
    return rows, cols


def page_lines(page):
    """Upright text lines of the page: {top: text}."""
    ups = page.filter(lambda obj: obj.get('upright', True))
    words = ups.extract_words()
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
    (center, text) per phrase ('Cards Cast' -> one phrase)."""
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
    turnout_anchors = []        # centers of the turnout header phrases
    contests = {}               # title -> {'party':, 'groups': [...]}
    contest_order = []
    cur = None                  # ('turnout',) | ('contest', title)

    for page in pdf.pages:
        lines = page_lines(page)
        skip_tops = set()
        label_anchors = []
        for top in sorted(lines):
            text = lines[top][0]
            if re.match(r'^Page: \d+ of \d+', text) \
                    or text.startswith(('Statement of Votes Cast',
                                        'Closed Primary',
                                        'Lapeer County, Michigan',
                                        'March 10, 2020', 'SOVC for:',
                                        'OFFICIAL RESULTS')):
                skip_tops.add(top)
                continue
            m = re.match(r'^(.*) \(Vote for (\d+)\)$', text)
            if m:
                # keep the '(DEM)'/'(REP)' token: the DEM and REP
                # presidential contests share one office name
                title = m.group(1).strip()
                cur = ('contest', title)
                contests.setdefault(title, {'party': None, 'groups': []})
                if title not in contest_order:
                    contest_order.append(title)
                skip_tops.add(top)
                continue
            if text in ('DEM', 'REP'):
                if cur and cur[0] == 'contest':
                    contests[cur[1]]['party'] = text
                skip_tops.add(top)
                continue
            if text in ('Registered', 'Voters') \
                    or ('Cards Cast' in text and 'Precinct' in text):
                cur = ('turnout',)
                for cx, ptext in phrase_centers(lines[top][1]):
                    if ptext in ('Precinct', 'County'):
                        continue
                    if not turnout_anchors \
                            or min(abs(cx - a) for a in turnout_anchors) > 8:
                        turnout_anchors.append(cx)
                skip_tops.add(top)
                continue
            if text in COUNTY_LABELS:
                skip_tops.add(top)
                continue
            if text.replace('Precinct', '').strip() == '' \
                    and 'Precinct' in text:
                # one or more side-by-side tables' 'Precinct' header
                # labels share a line -- each marks a label column
                for w in lines[top][1]:
                    label_anchors.append(w['x0'])
                skip_tops.add(top)
                continue

        if cur is None:
            continue
        rot_all = rotated_chars(page)
        rest = []
        for top in sorted(lines):
            if top in skip_tops:
                continue
            rest.extend(lines[top][1])
        # label columns: 'Precinct' header anchors; the turnout page
        # has no such line, so default to 20
        anchors = sorted(set(round(a) for a in label_anchors)) or [20.0]

        for ai, ax in enumerate(anchors):
            limit = anchors[ai + 1] - 40 if ai + 1 < len(anchors) else 10 ** 9
            twords = [w for w in rest if w['x0'] >= ax - 2
                      and w['x0'] < limit]
            trot = [c for c in rot_all if c['x0'] >= ax - 2
                    and c['x0'] < limit]
            ctx = f'page {page.page_number} table@{ax}'
            names_hint = TURNOUT_COLS if cur[0] == 'turnout' else None
            rows, cols = parse_table(twords, trot, names_hint,
                                     problems, ctx,
                                     anchor_hint=(sorted(turnout_anchors)
                                                  if names_hint else None))
            if not rows:
                continue
            if cur[0] == 'turnout':
                got = [t for _c, t in cols]
                if got != TURNOUT_COLS:
                    problems.append(f'{ctx}: turnout columns {got} != '
                                    f'{TURNOUT_COLS}')
                    continue
                turnout_rows.extend(rows)
            else:
                contests[cur[1]]['groups'].append(rows)

    # ---- merge contest column groups -----------------------------------
    merged = {}     # title -> col -> {label: value}
    ctotals = {}    # title -> col -> value
    for title in contest_order:
        merged[title] = {}
        ctotals[title] = {}
        seen_dup = set()
        for rows in contests[title]['groups']:
            for name, vmap in rows:
                if name == 'Lapeer County Michigan - Total':
                    for c, v in vmap.items():
                        if c in ctotals[title] and ctotals[title][c] != v:
                            problems.append(f'{title}: county total {c} '
                                            f'{ctotals[title][c]} != {v}')
                        ctotals[title][c] = v
                    continue
                if name.startswith('County - Total') \
                        or name.startswith('Cumulative'):
                    for c, v in vmap.items():
                        if name.startswith('Cumulative') and v != '0' \
                                and v != 'N/A':
                            problems.append(f'{title}: {name} {c} '
                                            f'nonzero {v}')
                        elif name.startswith('County - Total') \
                                and c in ctotals[title] \
                                and ctotals[title][c] != v:
                            problems.append(f'{title}: County-Total {c} '
                                            f'{v} != {ctotals[title][c]}')
                        elif name.startswith('County - Total'):
                            ctotals[title][c] = v
                    continue
                if not name or not re.search(r'(Township|City of)', name):
                    problems.append(f'{title}: suspicious row {name!r} '
                                    f'{vmap}')
                    continue
                for c, v in vmap.items():
                    col = merged[title].setdefault(c, {})
                    if c in seen_dup and name in col and col[name] != v:
                        problems.append(f'{title}: {name} {c} '
                                        f'{col[name]} != {v}')
                    col[name] = v
            seen_dup.update({c for _n, vm in rows for c in vm})

    # ---- structure ------------------------------------------------------
    precincts = []
    for name, _v in turnout_rows:
        if name == 'Lapeer County Michigan - Total' \
                or name.startswith(('County - Total', 'Cumulative')):
            continue
        if name not in precincts:
            precincts.append(name)
    if len(precincts) != 37:
        problems.append(f'{len(precincts)} precincts, expected 37')

    dem_title, rep_title = contest_order[0], contest_order[1]
    prop_titles = contest_order[2:]
    if len(prop_titles) != 4:
        problems.append(f'{len(prop_titles)} proposals, expected 4: '
                        f'{prop_titles}')
    if contests[dem_title]['party'] != 'DEM' \
            or contests[rep_title]['party'] != 'REP':
        problems.append(f'party tokens: {contests[dem_title]["party"]} / '
                        f'{contests[rep_title]["party"]}')
    got_dem = [c for c in merged[dem_title] if c not in AUX.values()]
    got_rep = [c for c in merged[rep_title] if c not in AUX.values()]
    if got_dem != DEM:
        problems.append(f'DEM columns {got_dem} != {DEM}')
    if got_rep != REP:
        problems.append(f'REP columns {got_rep} != {REP}')
    for title in (dem_title, rep_title):
        for c in merged[title]:
            missing = [p for p in precincts if p not in merged[title][c]]
            if missing:
                problems.append(f'{title}: column {c} missing precincts '
                                f'{missing}')
    # proposals are scoped: every column must cover the same precinct
    # set, a subset of the county
    prop_pset = {}
    for title in prop_titles:
        psets = [{p for p in merged[title][c]} for c in merged[title]]
        if len({frozenset(s) for s in psets}) != 1:
            problems.append(f'{title}: proposal columns cover different '
                            f'precinct sets')
            prop_pset[title] = set().union(*psets)
        else:
            prop_pset[title] = psets[0]
        if not prop_pset[title] <= set(precincts):
            problems.append(f'{title}: unknown precincts '
                            f'{prop_pset[title] - set(precincts)}')

    # ---- per-precinct checks -------------------------------------------
    dem = merged[dem_title]
    rep = merged[rep_title]
    wi_total = 0
    for p in precincts:
        s = sum(int(dem[c][p]) for c in DEM)
        if s != int(dem['TV'][p]):
            problems.append(f'DEM {p!r}: candidates {s} != TV '
                            f'{dem["TV"][p]}')
        s = sum(int(rep[c][p]) for c in REP)
        if s != int(rep['TV'][p]):
            problems.append(f'REP {p!r}: candidates {s} != TV '
                            f'{rep["TV"][p]}')
        if int(dem['TC'][p]) < int(dem['TV'][p]) \
                or int(rep['TC'][p]) < int(rep['TV'][p]):
            problems.append(f'{p!r}: Times Cast < Total Votes')
        wi_total += int(dem['WI'][p]) + int(rep['WI'][p])
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
            s = sum(int(cols[c][p]) for p in precincts if p in cols[c])
            want = ctotals[title].get(c)
            if want is None:
                problems.append(f'{title}: no county total for {c}')
            elif s != int(want):
                problems.append(f'{title}: {c} sums {s} != county total '
                                f'{want}')
    turnout_by = {n: v for n, v in turnout_rows}
    for c in ('Registered Voters', 'Cards Cast', 'Voters Cast'):
        rv_sum = sum(int(turnout_by[p][c]) for p in precincts)
        want = turnout_by.get('Lapeer County Michigan - Total', {}) \
            .get(c) or turnout_by.get('Lapeer County, Michigan - Total', {}) \
            .get(c)
        if want is None:
            problems.append(f'turnout: no county total for {c}')
        elif rv_sum != int(want):
            problems.append(f'turnout {c} sum {rv_sum} != county {want}')
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
    print(f'unresolved write-ins (not emitted): DEM '
          f'{sum(int(dem["WI"][p]) for p in precincts)}, REP '
          f'{sum(int(rep["WI"][p]) for p in precincts)}')

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
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(precincts)} precincts, '
          f'proposals: {prop_titles}')


if __name__ == '__main__':
    main()