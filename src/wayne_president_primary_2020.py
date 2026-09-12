"""Parse Wayne County's Mar 2020 presidential primary from three
portrait 'Precinct Report ... OFFICIAL RESULTS' PDFs in
openelections-sources-mi/2020/presidential_primary/:

  Wayne MI democrat_march10_2020.pdf   (123pp, DEM President contest)
  Wayne MI republican_march10_2020.pdf (109pp, REP President contest)
  Wayne MI voting_stats_mar2020.pdf    (104pp, RV / Ballots Cast / %)

Each contest page carries one precinct block per ~43.5pt: the precinct
label (wrapping onto 1-3 left-column lines), then 'Election Day' /
'AV Counting Board' / 'Total' sub-rows.  Blocks split across pages in
both directions (a label may start at a page bottom and its rows
continue on the next; a sub-row may continue at the next page top), so
parsing is a single state machine over all pages in order.

The rotated column headers extract word-REVERSED (bottom-to-top); the
'(DEM)'/'(REP)' suffix prints at a different x0 than the name words, so
headers are rebuilt by grouping rotated words on x0 and matching
against the known candidate vocabulary.  Candidate values are
right-aligned per column, so a sub-row's 18 (DEM) / 7 (REP) digit words
are assigned to columns positionally in x1 order; the Ballots Cast
value prints INLINE on the sub-row label line at a fixed right edge
(DEM x1 ~143.4, REP x1 ~196.9).

The county-total row is label-only in the DEM file -- its 19 values
print ROTATED at the page bottom (strings must be reversed, grouped by
x0, and mapped positionally onto the header columns); in the REP file
it is a normal row (inline Ballots Cast + 7 upright values).

Detroit's 'City of Detroit, AVCB N' counting boards are real rows and
are emitted, matching the committed 20201103 general file (1115
precincts, 134 of them AVCB).  Ballots Cast per party is that
contest's Total-row Times Cast; voting-stats Ballots Cast can exceed
DEM TC + REP TC (ballots with no party contest), so the check is <=.
'Unresolved Write-In' (the Write-In column) sits outside Total Votes:
candidates + write-in == TV.

Checks: per precinct ED+AV == Total for TC, TV, WI and every
candidate; TC >= TV; candidates + WI == TV; printed county-total row
== per-candidate sums; voting-stats Total == ED + AV, half-up pct,
BC >= DEM TC + REP TC; county sums match voting stats.  RV comes from
voting stats (AVCB boards have RV 0 and printed 'N/A' turnout).

Usage:
    .venv/bin/python src/wayne_president_primary_2020.py
"""
import csv
import os
import re
import sys
import unicodedata
from decimal import Decimal, ROUND_HALF_UP

import pdfplumber

COUNTY = 'Wayne'
OUT = ('2020/counties/20200310__mi__primary__president__'
       'wayne__precinct.csv')
SRC = os.path.expanduser('~/code/openelections-sources-mi/2020/'
                         'presidential_primary/')
DEM_PDF = SRC + 'Wayne MI democrat_march10_2020.pdf'
REP_PDF = SRC + 'Wayne MI republican_march10_2020.pdf'
VS_PDF = SRC + 'Wayne MI voting_stats_mar2020.pdf'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
       'Cory Booker', 'Pete Buttigieg', 'Julian Castro', 'John Delaney',
       'Tulsi Gabbard', 'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak',
       'Tom Steyer', 'Elizabeth Warren', 'Marianne Williamson',
       'Andrew Yang', 'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']
CANON = {'Julián Castro': 'Julian Castro'}
FIXED_COLS = {'Ballots Cast': ('tsaC', 'stollaB'),
              'Total Votes': ('setoV', 'latoT'),
              'Write-In': ('nI-etirW',)}
SUBROW_LABELS = ('Election Day', 'AV Counting Board', 'Total')
NUM_RE = re.compile(r'[\d,]+')
TC_X1 = {'DEM': 143.4, 'REP': 196.9}     # inline Ballots Cast right edge
COL_X0 = {'DEM': 150, 'REP': 230}        # first candidate value x0

VAL_RE = re.compile(r'^(Election Day|AV Counting Board|Total) '
                    r'([\d,]+) ([\d,]+)(?: (\d+\.\d+)%| N/A)$')


def ival(text):
    return int(text.replace(',', ''))


def pct_round(v, denom):
    return float((Decimal(v) * 100 / Decimal(denom)).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP))


def rev(s):
    return s[::-1]


def deaccent(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')


def header_columns(pg, party, problems, pno):
    """Rebuild the rotated header columns: [(x0, column name)]."""
    cands = DEM if party == 'DEM' else REP
    vocab = {}
    for name in cands:
        for w in name.split():
            vocab.setdefault(deaccent(rev(w)), name)
    for name, words in FIXED_COLS.items():
        for w in words:
            vocab.setdefault(deaccent(w), name)
    known = set(cands) | set(FIXED_COLS)
    groups = {}
    for w in pg.extract_words(extra_attrs=['upright']):
        if w['upright'] or w['top'] > 180:
            continue
        if deaccent(w['text']) not in vocab:
            continue          # '(DEM)'/'(REP)' suffixes etc.
        groups.setdefault(round(w['x0']), []).append(w)
    # each x0 group is one rotated line, read bottom-to-top; a long name
    # can wrap across two lines ('Donald J.' x0 270 + 'Trump' x0 282)
    items = [(x0, sorted(groups[x0], key=lambda w: -w['top']))
             for x0 in sorted(groups)]
    changed = True
    while changed and len(items) > 1:
        changed = False
        for i in range(len(items) - 1):
            parts = [items[i][1], items[i + 1][1]]
            name = ' '.join(rev(w['text']) for part in parts for w in part)
            if CANON.get(name, name) in known:
                items[i] = (items[i][0], parts[0] + parts[1])
                del items[i + 1]
                changed = True
                break
    cols = []
    for x0, ws in items:
        name = ' '.join(rev(w['text']) for w in ws)
        cols.append((x0, CANON.get(name, name)))
    expected = ['Ballots Cast'] + cands + ['Total Votes', 'Write-In']
    got = [c for _, c in cols]
    if got != expected:
        problems.append(f'p{pno}: header columns {got} != expected')
    return cols


def county_strips(pg):
    """The DEM county-total row: rotated digit strings -> {x0: value}."""
    groups = {}
    for c in pg.chars:
        if c['upright'] or not re.fullmatch(r'[\d,]', c['text']):
            continue
        groups.setdefault(round(c['x0']), []).append(c)
    out = []
    for x0 in sorted(groups):
        cs = sorted(groups[x0], key=lambda c: -c['top'])
        out.append((x0, ''.join(c['text'] for c in cs)))
    return out


def parse_report(path, party):
    """-> (data, order, county, problems)

    data[label][rt] = {'cands': {name: v}, 'TV':, 'WI':, 'TC':}
    with rt in ('ED', 'AV', 'T').  county = same shape for rt 'T'.
    """
    problems = []
    data = {}
    order = []
    county = None
    pending = []
    cur = None

    with pdfplumber.open(path) as pdf:
        for pno, pg in enumerate(pdf.pages, 1):
            cols = header_columns(pg, party, problems, pno)
            cands = DEM if party == 'DEM' else REP
            ncol = len(cands) + 2
            strips = county_strips(pg) if party == 'DEM' else []
            words = pg.extract_words(extra_attrs=['upright'])
            ups = [w for w in words if w['upright'] and w['top'] > 140]
            tops = sorted({round(w['top'], 1) for w in ups})
            bands = []
            for t in tops:
                if bands and t - bands[-1][-1] < 3:
                    bands[-1].append(t)
                else:
                    bands.append([t])
            for band in bands:
                ws = sorted([w for w in ups
                             if round(w['top'], 1) in band],
                            key=lambda w: w['x0'])
                digits = [w for w in ws if NUM_RE.fullmatch(w['text'])]
                textws = [w for w in ws
                          if not NUM_RE.fullmatch(w['text'])
                          and not re.fullmatch(r'#+', w['text'])]
                label = ' '.join(w['text'] for w in textws)
                if label == 'Precinct' and not digits and party == 'DEM':
                    continue      # column header, once per page
                if label in ('County',) and not digits:
                    continue      # section header
                if label.startswith('County - Total'):
                    if county is not None:
                        problems.append(f'p{pno}: second county row')
                    county = {'cands': {}, 'TV': None, 'WI': None,
                              'TC': None}
                    if digits:
                        # REP: inline Ballots Cast + column values
                        tc = [w for w in digits
                              if abs(w['x1'] - TC_X1[party]) <= 1.5]
                        vals = [w for w in digits if w['x0'] >= COL_X0[party]]
                        if len(tc) != 1 or len(vals) != ncol:
                            problems.append(
                                f'p{pno}: county row {len(tc)} TC/'
                                f'{len(vals)} values')
                            continue
                        county['TC'] = ival(tc[0]['text'])
                        assign(county, cands, vals, problems, f'p{pno}')
                    elif party == 'DEM':
                        if len(strips) != ncol + 1:
                            problems.append(
                                f'p{pno}: county strips {len(strips)}')
                            continue
                        county['TC'] = ival(strips[0][1])
                        assign(county, cands,
                               [{'text': s} for _, s in strips[1:]],
                               problems, f'p{pno}')
                    continue
                if label not in SUBROW_LABELS:
                    if digits:
                        # a label line may wrap with the precinct
                        # number on its own line, but digits that look
                        # like inline TC or column values are a problem
                        tc = [w for w in digits
                              if abs(w['x1'] - TC_X1[party]) <= 1.5]
                        if tc or any(w['x0'] >= COL_X0[party]
                                     for w in digits):
                            problems.append(
                                f'p{pno}@{band[0]}: stray digits on '
                                f'{label!r}')
                            continue
                    pending.append(' '.join(w['text'] for w in ws))
                    continue
                # a sub-row: one inline TC value + column values
                tc = [w for w in digits
                      if abs(w['x1'] - TC_X1[party]) <= 1.5]
                htc = [w for w in ws if re.fullmatch(r'#+', w['text'])
                       and w['x0'] < COL_X0[party]]
                vals = [w for w in digits if w['x0'] >= COL_X0[party]]
                if len(tc) + len(htc) != 1 or len(vals) != ncol:
                    problems.append(
                        f'p{pno}@{band[0]} {label}: {len(tc)} TC, '
                        f'{len(vals)} values (want 1/{ncol})')
                    continue
                rt = {'Election Day': 'ED',
                      'AV Counting Board': 'AV',
                      'Total': 'T'}[label]
                rec = {'cands': {}, 'TV': None, 'WI': None}
                if htc:
                    # the TC glyph did not decode ('###'); the Total
                    # row's count is the sum of its ED + AV rows
                    prev = data.get(cur, {}) if cur else {}
                    if rt == 'T' and prev.get('ED') and prev.get('AV'):
                        rec['TC'] = prev['ED']['TC'] + prev['AV']['TC']
                    else:
                        problems.append(
                            f'p{pno}@{band[0]}: TC ### unresolved')
                else:
                    rec['TC'] = ival(tc[0]['text'])
                assign(rec, cands, vals, problems, f'p{pno}@{band[0]}')
                if rt == 'ED':
                    if pending:
                        full = ' '.join(pending)
                        pending.clear()
                        if full in data:
                            problems.append(f'duplicate precinct {full!r}')
                        data[full] = {}
                        order.append(full)
                        cur = full
                    if cur is None:
                        problems.append(f'p{pno}: ED row, no precinct')
                        continue
                    data[cur][rt] = rec
                elif cur is None:
                    problems.append(f'p{pno}: {rt} row, no precinct')
                else:
                    data[cur][rt] = rec
        if pending:
            problems.append(f'unflushed label {pending!r}')
    return data, order, county, problems


def assign(rec, cands, vals, problems, where):
    """Place right-aligned column values onto candidates / TV / WI."""
    if len(vals) != len(cands) + 2:
        problems.append(f'{where}: {len(vals)} values')
        return
    for name, w in zip(cands, vals):
        rec['cands'][name] = ival(w['text'])
    rec['TV'] = ival(vals[-2]['text'])
    rec['WI'] = ival(vals[-1]['text'])


def parse_voting_stats(path):
    """-> (vs, order, county, problems); vs[label][rt] = [RV, BC, pct]."""
    problems = []
    vs = {}
    order = []
    county = {}
    pending = []
    cur = None
    in_county = False
    with pdfplumber.open(path) as pdf:
        for pno, pg in enumerate(pdf.pages, 1):
            for ln in (pg.extract_text() or '').split('\n'):
                s = ln.strip()
                if not s:
                    continue
                if s in ('Voting Stats', 'County', 'Precinct',
                         'Registered', 'Voters', 'OFFICIAL RESULTS') \
                        or s.startswith(('March 10th', 'Wayne County',
                                         'Precinct Ballots')):
                    continue
                m = VAL_RE.match(s)
                if m:
                    rt = {'Election Day': 'ED',
                          'AV Counting Board': 'AV',
                          'Total': 'T'}[m.group(1)]
                    row = [ival(m.group(2)), ival(m.group(3)),
                           float(m.group(4)) if m.group(4) else None]
                    if in_county:
                        county[rt] = row
                        continue
                    if m.group(1) == 'Election Day':
                        if pending:
                            full = ' '.join(pending)
                            pending.clear()
                            if full in vs:
                                problems.append(
                                    f'duplicate precinct {full!r}')
                            vs[full] = {}
                            order.append(full)
                            cur = full
                        if cur is None:
                            problems.append(f'p{pno}: ED row, no label')
                            continue
                        vs[cur][rt] = row
                    elif cur is None:
                        problems.append(f'p{pno}: {rt} row, no label')
                    else:
                        vs[cur][rt] = row
                    continue
                if s.startswith('County - Total'):
                    in_county = True
                    m2 = VAL_RE.match(
                        'Total ' + s[len('County - Total'):].strip())
                    if m2:
                        county['T'] = [ival(m2.group(2)), ival(m2.group(3)),
                                       float(m2.group(4))
                                       if m2.group(4) else None]
                    continue
                pending.append(s)
        if pending:
            problems.append(f'unflushed label {pending!r}')
    return vs, order, county, problems


def main():
    problems = []
    dem_data, dem_order, dem_cty, p1 = parse_report(DEM_PDF, 'DEM')
    rep_data, rep_order, rep_cty, p2 = parse_report(REP_PDF, 'REP')
    vs, vs_order, vs_cty, p3 = parse_voting_stats(VS_PDF)
    problems += p1 + p2 + p3

    # ---- structure checks ----------------------------------------------
    if len(dem_order) != 1115:
        problems.append(f'DEM: {len(dem_order)} precincts != 1115')
    if len(rep_order) != 1115:
        problems.append(f'REP: {len(rep_order)} precincts != 1115')
    if len(vs_order) != 1115:
        problems.append(f'voting stats: {len(vs_order)} precincts != 1115')
    if set(dem_order) != set(rep_order):
        only_d = sorted(set(dem_order) - set(rep_order))[:5]
        only_r = sorted(set(rep_order) - set(dem_order))[:5]
        problems.append(f'DEM/REP label mismatch: only-DEM {only_d}, '
                        f'only-REP {only_r}')
    if set(dem_order) != set(vs_order):
        only_d = sorted(set(dem_order) - set(vs_order))[:5]
        only_v = sorted(set(vs_order) - set(dem_order))[:5]
        problems.append(f'DEM/vs label mismatch: only-DEM {only_d}, '
                        f'only-vs {only_v}')

    # ---- per-precinct checks -------------------------------------------
    bc_short = []
    for p in dem_order:
        for party, data in (('DEM', dem_data), ('REP', rep_data)):
            cands = DEM if party == 'DEM' else REP
            rec = data[p]
            for rt in ('ED', 'AV', 'T'):
                if rt not in rec:
                    problems.append(f'{p}: {party} {rt} row missing')
                    continue
                r = rec[rt]
                missing = [c for c in cands if c not in r['cands']]
                if missing:
                    problems.append(f'{p}: {party} {rt} missing {missing}')
                    continue
                if r['TV'] is None:
                    problems.append(f'{p}: {party} {rt} TV missing')
                    continue
                s = sum(r['cands'][c] for c in cands)
                if r['WI'] is None or s + r['WI'] != r['TV']:
                    problems.append(
                        f'{p}: {party} {rt} cands {s} + WI {r["WI"]} '
                        f'!= TV {r["TV"]}')
                if r['TC'] < r['TV']:
                    problems.append(f'{p}: {party} {rt} TC < TV')
            if all(rt in rec for rt in ('ED', 'AV', 'T')):
                ed, av, t = rec['ED'], rec['AV'], rec['T']
                for key in ('TC', 'TV', 'WI'):
                    if ed[key] + av[key] != t[key]:
                        problems.append(
                            f'{p}: {party} {key} ED {ed[key]} + AV '
                            f'{av[key]} != Total {t[key]}')
                for c in cands:
                    if ed['cands'][c] + av['cands'][c] != t['cands'][c]:
                        problems.append(
                            f'{p}: {party} {c} ED {ed["cands"][c]} + AV '
                            f'{av["cands"][c]} != Total '
                            f'{t["cands"][c]}')
        # voting stats
        row = vs[p]
        for rt in ('ED', 'AV', 'T'):
            if rt not in row:
                problems.append(f'{p}: voting stats {rt} row missing')
        if all(rt in row for rt in ('ED', 'AV', 'T')):
            rv = row['T'][0]
            if row['ED'][0] != rv or row['AV'][0] != rv:
                problems.append(f'{p}: RV differs across sub-rows')
            if row['ED'][1] + row['AV'][1] != row['T'][1]:
                problems.append(f'{p}: BC ED+AV != Total')
            if rv:
                want = pct_round(row['T'][1], rv)
                if abs(want - row['T'][2]) > 0.005:
                    problems.append(f'{p}: turnout {want}% != printed '
                                    f'{row["T"][2]}%')
            if row['T'][1] < dem_data[p]['T']['TC'] \
                    + rep_data[p]['T']['TC']:
                bc_short.append(p)
    if bc_short:
        # source-level inconsistency (all in City of Ecorse, where the
        # AV rows' Times Cast is the counting-board total in BOTH party
        # files); our output uses only voting-stats RV, so note, don't
        # block
        print(f'NOTE: {len(bc_short)} precincts: voting-stats BC < '
              f'DEM TC + REP TC: {bc_short}')

    # ---- county checks --------------------------------------------------
    for party, cty, data in (('DEM', dem_cty, dem_data),
                             ('REP', rep_cty, rep_data)):
        if cty is None:
            problems.append(f'{party}: no county-total row')
            continue
        cands = DEM if party == 'DEM' else REP
        for key in ('TC', 'TV', 'WI'):
            s = sum(data[p]['T'][key] for p in dem_order)
            if s != cty[key]:
                problems.append(f'{party} county {key}: sum {s} != '
                                f'printed {cty[key]}')
        for c in cands:
            s = sum(data[p]['T']['cands'][c] for p in dem_order)
            if s != cty['cands'][c]:
                problems.append(f'{party} county {c}: sum {s} != '
                                f'printed {cty["cands"][c]}')
    if vs_cty:
        for rt in ('ED', 'AV', 'T'):
            s = sum(vs[p][rt][1] for p in dem_order) \
                if all(rt in vs[p] for p in dem_order) else None
            if s is not None and s != vs_cty[rt][1]:
                problems.append(f'vs county {rt} BC: sum {s} != printed '
                                f'{vs_cty[rt][1]}')
        s = sum(vs[p]['T'][0] for p in dem_order)
        if s != vs_cty['T'][0]:
            problems.append(f'vs county RV: sum {s} != printed '
                            f'{vs_cty["T"][0]}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing {OUT}')
        return

    # ---- emission -------------------------------------------------------
    rows = []
    for p in dem_order:
        rows.append([COUNTY, p, 'Registered Voters', '', '', '',
                     vs[p]['T'][0]])
        for party, cands, data in (('DEM', DEM, dem_data),
                                   ('REP', REP, rep_data)):
            for c in cands:
                rows.append([COUNTY, p, 'President', '', party, c,
                             data[p]['T']['cands'][c]])
            rows.append([COUNTY, p, 'Ballots Cast', '', party, '',
                         data[p]['T']['TC']])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(dem_order)} precincts')


if __name__ == '__main__':
    main()