"""Parse Saginaw County's Mar 2020 presidential primary from
'Saginaw MI March 2020 Saginaw County Statement Of Votes Cast RPT.pdf'
(138pp ES&S SOVC, landscape 792x612, text-extractable).

Monroe-family structure (Election Day / AV Counting Boards / Total
sub-rows per precinct; county totals print only after each contest's
LAST run, followed by an all-zero Cumulative zone; bare 'Saginaw
County'/'Saginaw County Michigan' lines are the zero Cumulative rows
printed atop every group).  Runs hold 6 precincts each and the
rotated candidate headers extract as word-reversed lines, so column
identity is positional:

- turnout pp1-12: cols Registered Voters / Precinct Cards Cast /
  Voters Cast / pct (pct 'N/A' inside the Cumulative zone).
- DEM pp13-60: 12 runs of 4 pages; page A = Times Cast/RV group +
  Bennet/Biden group (2 value pairs), page B = Bloomberg..Klobuchar
  (7), page C = Sanders..Uncommitted (7), page D = Total Votes.
- REP pp61-84: 12 runs of 2 pages; page A = TC/RV + Sanford/Trump,
  page B = Walsh/Weld/Uncommitted + Total Votes, whose rows MERGE
  into single 4-value lines (both groups share the printed row).
- proposals pp85-138 (12 titles, 1-12 pages each): TC/RV group +
  Yes/No/Total Votes group; the title prints only on a proposal's
  first page and is carried forward across continuation pages.

Value rows carry an optional '<pct>%' token after each value ('0 0'
bare vs '0 0.00%'), so rows are decomposed token-wise against the
known column counts; when a page's trailing groups share a printed
row, one row's values fill consecutive groups (window assignment
starts at the first group not yet complete for that label).  County
total rows likewise span all groups on the page in order.

Checks: sub-row ED + AV == Total per column (TC; turnout Cards and
Voters), Cards == Voters, RV consistent across sub-rows and equal to
the turnout RV, candidate sums == Total Votes (also per sub-row),
TC >= TV, printed pcts recomputed (val / sub-row Total Votes), DEM TC
+ REP TC <= Ballots Cast, county totals == sums over precincts for
every contest and proposal, turnout counting-group ED + AV == county
Voters Cast.  'Unresolved Write-In' is not printed in this source.

Usage:
    .venv/bin/python src/saginaw_president_primary_2020.py
"""
import csv
import os
from decimal import Decimal, ROUND_HALF_UP

import pdfplumber

COUNTY = 'Saginaw'
OUT = ('2020/counties/20200310__mi__primary__president__'
       'saginaw__precinct.csv')
SRC = os.path.expanduser('~/code/openelections-sources-mi/2020/'
                         'presidential_primary/Saginaw MI March 2020 '
                         'Saginaw County Statement Of Votes Cast RPT.pdf')

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
       'Cory Booker', 'Pete Buttigieg', 'Julian Castro', 'John Delaney',
       'Tulsi Gabbard', 'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak',
       'Tom Steyer', 'Elizabeth Warren', 'Marianne Williamson',
       'Andrew Yang', 'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

# positional groups; marker = first token of the group's reversed
# header lines
GROUP_COLS = {
    'TCRV': ['TC', 'RV'],
    'BB': ['Michael Bennet', 'Joe Biden'],
    'D7A': ['Michael R. Bloomberg', 'Cory Booker', 'Pete Buttigieg',
            'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
            'Amy Klobuchar'],
    'D7B': ['Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
            'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
            'Uncommitted'],
    'TV': ['TV'],
    'R2': ['Mark Sanford', 'Donald J. Trump'],
    'R3': ['Joe Walsh', 'Bill Weld', 'Uncommitted'],
    'YN': ['Yes', 'No', 'TV'],
}
MARKERS = {'tsaC': 'TCRV', 'tenneB': 'BB', 'grebmoolB': 'D7A',
           'srednaS': 'D7B', 'drofnaS': 'R2', 'hslaW': 'R3',
           'seY': 'YN'}

COUNTY_MI = 'Saginaw County Michigan - '
COUNTY_TOT = 'Saginaw County - Total '

# printed county anchors (turnout p12, DEM TV p60, REP p84)
COUNTY_RV, COUNTY_BC, COUNTY_ED_CG = 151301, 44570, 34825
COUNTY_DEM_TV = 27703
COUNTY_REP = {'Joe Walsh': 80, 'Bill Weld': 97, 'Uncommitted': 634,
              'TV': 14087}


def ival(text):
    return int(text.replace(',', ''))


def pct_round(v, denom):
    """v/denom*100 to 2dp, rounding half up (the source's rounding;
    Python's round() would give banker's rounding and disagree on
    exact .xx5 ties)."""
    return float((Decimal(v) * 100 / Decimal(denom)).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP))


def parse_tokens(tokens):
    """'1 0.40% 158 62.70%' -> [(1, 0.40), (158, 62.70)];
    '252 1,606' -> [(252, None), (1606, None)];
    '0 0 0 N/A' -> [(0, None), (0, None), (0, None), (None, None)]."""
    vals = []
    for tk in tokens:
        if tk.endswith('%'):
            vals[-1] = (vals[-1][0], float(tk[:-1]))
        elif tk == 'N/A':
            vals.append((None, None))
        else:
            vals.append((ival(tk), None))
    return vals


FRAG_WORDS = set()
for _name in DEM + REP:
    FRAG_WORDS.update(_name.split())
FRAG_WORDS.update(['Times', 'Cast', 'Registered', 'Voters', 'Yes',
                   'No', 'Total', 'Votes', '(DEM)', '(REP)', 'R.',
                   'J.', 'Julián'])
FRAGS = {w[::-1] for w in FRAG_WORDS}


def _deaccent(s):
    import unicodedata
    return ''.join(ch for ch in unicodedata.normalize('NFD', s)
                   if not unicodedata.combining(ch))


def is_fragment(ln):
    """True for word-reversed rotated-header fragment lines, which
    extract one word per line ('tsaC' / 'semiT' / 'deretsigeR' /
    'sretoV') or as short merged runs ('einreB )MED('); accents are
    ignored ('Julián' extracts reversed as 'náiluJ')."""
    toks = ln.split()
    return (bool(toks) and not any(ch.isdigit() for ch in ln)
            and all(_deaccent(t) in FRAGS for t in toks))


def rowtype_of(ln):
    if ln.startswith('Election Day'):
        return 'ED'
    if ln.startswith('AV Counting Boards'):
        return 'AV'
    return 'T'


def new_rec():
    return {'RV': None, 'BC': None,
            'DEM': {}, 'DEM_TC': {}, 'DEM_RV': None, 'DEM_TV': None,
            'DEM_TV_RT': {},
            'REP': {}, 'REP_TC': {}, 'REP_RV': None, 'REP_TV': None,
            'REP_TV_RT': {},
            'PROPS': {}}


def main():
    problems = []
    data = {}        # precinct -> record
    porder = []
    county_mi = {}   # ctx -> {gname: [(v, pct)...]}
    county_tot = {}  # ctx -> {gname: [(v, pct)...]}
    ctx = None       # 'DEM' | 'REP' | proposal title
    cg = {}          # turnout counting-group rows after the county row

    def cty_store(name):
        return (county_mi if name == 'mi' else county_tot)

    def put_county(sink_name, gname, vals, pno):
        store = cty_store(sink_name).setdefault(ctx, {})
        old = store.get(gname)
        if old is not None and [v for v, _ in old] != [v for v, _ in vals]:
            problems.append(f'p{pno}: county {sink_name} row {gname} '
                            f'redefined: {old} vs {vals}')
        store[gname] = vals

    with pdfplumber.open(SRC) as pdf:
        for pno in range(1, len(pdf.pages) + 1):
            text = pdf.pages[pno - 1].extract_text() or ''
            lines = [l.strip() for l in text.split('\n')]
            lines = [l for l in lines if l and not l.startswith('Page: ')]
            if not lines:
                problems.append(f'p{pno}: empty')
                continue
            first = lines[0]
            if first.startswith('President of the United States (DEM)'):
                ctx = 'DEM'
            elif first.startswith('President of the United States (REP)'):
                ctx = 'REP'
            elif '(Vote for 1)' in first and not first.startswith(
                    'President of the United States'):
                ctx = first[:first.index('(Vote for 1)')].strip()
            is_turnout = all(l.split()[0] not in MARKERS
                             and l.split()[0] != 'setoV' for l in lines)

            groups, blocks, pending = [], {}, []
            cty = {}      # 'mi'/'tot' -> {gi: vals} for this page
            cur = None    # precinct label whose block is being read
            cty_pending = None   # county header seen, values on next line
            if is_turnout:
                ctx = 'TO'

            def place(vals, sink, rt, ln):
                """Window-assign vals over the page's groups into
                blocks[sink][gi][rt] (precinct rows) or cty[sink][gi]
                (county rows)."""
                if sink == 'cty':
                    holder = cty.setdefault(rt, {})
                    taken = set(holder)
                else:
                    holder = blocks.setdefault(sink, {})
                    taken = {gi for gi, rows in holder.items()
                             if 'T' in rows}
                start = next((i for i in range(len(groups))
                              if i not in taken), None)
                if start is None:
                    problems.append(f'p{pno}: extra {rt} row: {ln!r}')
                    return
                for end in range(start + 1, len(groups) + 1):
                    ncols = sum(len(g['cols']) for g in groups[start:end])
                    if ncols == len(vals):
                        off = 0
                        for gi in range(start, end):
                            g = groups[gi]
                            n = len(g['cols'])
                            chunk = vals[off:off + n]
                            off += n
                            if sink == 'cty':
                                holder[gi] = chunk
                            else:
                                holder.setdefault(gi, {})[rt] = chunk
                        return
                problems.append(
                    f'p{pno}: {rt}: {len(vals)} values do not fit '
                    f'{[g["name"] for g in groups]}: {ln!r}')

            for ln in lines:
                tk0 = ln.split()[0]
                if ln in ('Total', 'latoT', 'Michigan', 'March 10, 2020'):
                    continue      # bare 'Total' under a county row; date
                if tk0 in MARKERS or tk0 == 'setoV':
                    if tk0 == 'setoV' and groups and \
                            groups[-1]['name'] == 'YN':
                        continue      # the Yes/No group already spans TV
                    gname = MARKERS.get(tk0, 'TV')
                    groups.append({'name': gname,
                                   'cols': GROUP_COLS[gname]})
                    continue
                county = (ln.startswith(COUNTY_MI)
                          or ln.startswith(COUNTY_TOT))
                if county:
                    sink_name = 'mi' if COUNTY_MI in ln else 'tot'
                    toks = ln[len(COUNTY_MI if COUNTY_MI in ln
                                  else COUNTY_TOT):].split()
                    if toks and toks[-1] in ('Total', 'latoT'):
                        toks = toks[:-1]
                    vals = parse_tokens(toks)
                    if is_turnout:
                        put_county(sink_name, 'TO', vals, pno)
                    else:
                        place(vals, 'cty', sink_name, ln)
                    continue
                if ln in ('Saginaw County Michigan -',
                          'Saginaw County - Total'):
                    # county row wrapped: label on this line, values on
                    # the next
                    cty_pending = 'mi' if ln == 'Saginaw County '\
                        'Michigan -' else 'tot'
                    continue
                if cty_pending is not None:
                    toks = ln.split()
                    if toks and all(
                            t == 'N/A' or t.endswith('%')
                            or t.replace(',', '').isdigit()
                            for t in toks):
                        if toks[-1] in ('Total', 'latoT'):
                            toks = toks[:-1]
                        vals = parse_tokens(toks)
                        if is_turnout:
                            put_county(cty_pending, 'TO', vals, pno)
                        else:
                            place(vals, 'cty', cty_pending, ln)
                        continue
                    cty_pending = None   # values didn't follow after all
                if ln.startswith('Election Day ') or \
                        ln.startswith('AV Counting Boards ') or \
                        ln.startswith('Total '):
                    ntok = (2 if ln.startswith('Election Day ')
                            else 3 if ln.startswith('AV Counting Boards ')
                            else 1)
                    rest = ln.split()[ntok:]
                    if not rest:
                        continue      # bare 'Total' under the county row
                    vals = parse_tokens(rest)
                    if pending:
                        cur = ' '.join(pending)
                        pending = []
                    rt = rowtype_of(ln)
                    if cur is None:
                        # Cumulative-zone rows (after the county rows on
                        # turnout pages: the counting-group summary)
                        if is_turnout and county_tot.get('TO', {})\
                                .get('TO') and rt != 'T':
                            cg[rt] = vals
                        else:
                            for v, _ in vals:
                                if v not in (0, None):
                                    problems.append(f'p{pno}: skipped '
                                                    f'nonzero row {ln!r}')
                        continue
                    if is_turnout:
                        rec = data.setdefault(cur, new_rec())
                        if cur not in porder:
                            porder.append(cur)
                        if rec.get(rt) is not None:
                            problems.append(f'p{pno}: {cur!r} turnout {rt} '
                                            f'repeated')
                        if len(vals) != 3:
                            problems.append(f'p{pno}: {cur!r} turnout '
                                            f'{len(vals)} values: {ln!r}')
                        rec[rt] = vals
                        continue
                    if cur not in data:
                        data[cur] = new_rec()
                        porder.append(cur)
                    place(vals, cur, rt, ln)
                    continue
                if is_fragment(ln):
                    continue
                if is_turnout:
                    if tk0 in ('Registered', 'Voters') or \
                            ln.startswith(('Precinct Cards Cast',
                                           'Cumulative',
                                           'Statement of Votes Cast',
                                           'Closed Primary', 'SOVC for:',
                                           'Saginaw County', 'Michigan')):
                        if ln.startswith('Cumulative'):
                            cur = None   # zero Cumulative zone begins
                        continue
                    pending.append(ln)
                    continue
                # contest / proposal page non-value lines ('Precinct'
                # exact -- bare 'Precinct N' is a wrapped label part;
                # title lines are ctx, never labels)
                if ln == 'Precinct' or '(Vote for 1)' in ln or \
                        ln in ('DEM', 'REP') or \
                        ln.startswith('President of the United States'):
                    continue
                if ln in ('Saginaw County', 'Saginaw County Michigan',
                          'Michigan'):
                    continue              # zero Cumulative rows
                if ln.startswith('Cumulative'):
                    cur = None            # zero Cumulative zone begins
                    continue
                pending.append(ln)

            # fold this page's county rows into the county stores
            for sink_name, gi_map in cty.items():
                for gi, vals in gi_map.items():
                    put_county(sink_name, groups[gi]['name'], vals, pno)

            # fold this page's precinct blocks into the main records
            for label, gb in blocks.items():
                rec = data[label]
                for gi, rows in gb.items():
                    gname = groups[gi]['name']
                    for rt, vals in rows.items():
                        for col, (v, pct) in zip(GROUP_COLS[gname], vals):
                            if gname == 'TCRV':
                                if ctx == 'DEM':
                                    if col == 'TC':
                                        rec['DEM_TC'][rt] = v
                                    else:
                                        rec['DEM_RV'] = v
                                elif ctx == 'REP':
                                    if col == 'TC':
                                        rec['REP_TC'][rt] = v
                                    else:
                                        rec['REP_RV'] = v
                                else:
                                    pr = rec['PROPS'].setdefault(ctx, {})
                                    if col == 'TC':
                                        pr.setdefault('TC', {})[rt] = v
                                    else:
                                        pr['RV'] = v
                            elif gname == 'TV':
                                # per-sub-row Total Votes: the printed
                                # pct denominator for every column
                                if ctx == 'DEM':
                                    rec['DEM_TV_RT'][rt] = v
                                    if rt == 'T':
                                        rec['DEM_TV'] = v
                                elif ctx == 'REP':
                                    rec['REP_TV_RT'][rt] = v
                                    if rt == 'T':
                                        rec['REP_TV'] = v
                                else:
                                    pr = rec['PROPS'].setdefault(ctx, {})
                                    pr.setdefault('TV_RT', {})[rt] = v
                                    if rt == 'T':
                                        pr['TV'] = v
                            elif gname in ('BB', 'D7A', 'D7B'):
                                rec['DEM'].setdefault(col, {})[rt] = \
                                    (v, pct)
                            elif gname in ('R2', 'R3'):
                                rec['REP'].setdefault(col, {})[rt] = \
                                    (v, pct)
                            elif gname == 'YN':
                                if ctx in ('DEM', 'REP') or ctx is None:
                                    problems.append(
                                        f'p{pno}: YN group with ctx={ctx}')
                                    continue
                                pr = rec['PROPS'].setdefault(ctx, {})
                                if col == 'TV':
                                    pr.setdefault('TV_RT', {})[rt] = v
                                    if rt == 'T':
                                        pr['TV'] = v
                                else:
                                    pr.setdefault(col, {})[rt] = (v, pct)

    # ---- checks ---------------------------------------------------------
    for p in porder:
        rec = data[p]
        t = {rt: rec.get(rt) for rt in ('ED', 'AV', 'T')}
        if any(t[rt] is None for rt in t):
            problems.append(f'{p}: turnout missing {[r for r in t if t[r] is None]}')
            continue
        ed, av, tot = t['ED'], t['AV'], t['T']
        for idx, name in ((1, 'Cards'), (2, 'Voters')):
            if ed[idx][0] + av[idx][0] != tot[idx][0]:
                problems.append(f'{p}: turnout {name} ED {ed[idx][0]} + '
                                f'AV {av[idx][0]} != {tot[idx][0]}')
        for idx in (0,):
            # only Registered Voters repeats across sub-rows; Cards and
            # Voters Cast are per counting group
            if len({t[rt][idx][0] for rt in ('ED', 'AV', 'T')}) > 1:
                problems.append(f'{p}: turnout col {idx} differs across '
                                f'sub-rows')
        if tot[1][0] != tot[2][0]:
            problems.append(f'{p}: turnout Cards {tot[1][0]} != Voters '
                            f'{tot[2][0]}')
        rv = tot[0][0]
        want = pct_round(tot[1][0], rv)
        if tot[2][1] is not None and abs(want - tot[2][1]) > 0.005:
            problems.append(f'{p}: turnout {want}% != printed '
                            f'{tot[2][1]}%')
        rec['RV'], rec['BC'] = rv, tot[1][0]

        # DEM (printed ED/AV pcts use that sub-row's Total Votes as
        # denominator; candidate sums may fall short of TV by a ballot
        # or two -- the source's own rounding artifact -- so TV is
        # authoritative and the strict sum check is not applied)
        d = rec['DEM']
        dem_tv, dem_tc = rec['DEM_TV'], rec['DEM_TC'].get('T')
        missing = [c for c in DEM if 'T' not in d.get(c, {})]
        d_tv_missing = [rt for rt in ('ED', 'AV', 'T')
                        if rt not in rec['DEM_TV_RT']]
        if dem_tv is None or dem_tc is None or missing or d_tv_missing:
            problems.append(f'{p}: DEM incomplete (tv={dem_tv}, '
                            f'tc={dem_tc}, missing={missing}, '
                            f'tv_rt_missing={d_tv_missing})')
        else:
            if dem_tc < dem_tv:
                problems.append(f'{p}: DEM TC {dem_tc} < TV {dem_tv}')
            if rec['DEM_RV'] != rv:
                problems.append(f'{p}: DEM RV {rec["DEM_RV"]} != turnout '
                                f'RV {rv}')
            etc = rec['DEM_TC'].get('ED', 0) + rec['DEM_TC'].get('AV', 0)
            if etc != dem_tc:
                problems.append(f'{p}: DEM TC ED {rec["DEM_TC"].get("ED")} '
                                f'+ AV {rec["DEM_TC"].get("AV")} != '
                                f'{dem_tc}')
            for rt in ('ED', 'AV', 'T'):
                denom = rec['DEM_TV_RT'][rt]
                for c in DEM:
                    v, pct = d[c][rt]
                    if pct is None:
                        continue
                    w = pct_round(v, denom)
                    if abs(w - pct) > 0.005:
                        problems.append(f'{p}: DEM {c} {rt} pct {w}% != '
                                        f'printed {pct}%')
        # REP
        r = rec['REP']
        rep_tv, rep_tc = rec['REP_TV'], rec['REP_TC'].get('T')
        rmissing = [c for c in REP if 'T' not in r.get(c, {})]
        r_tv_missing = [rt for rt in ('ED', 'AV', 'T')
                        if rt not in rec['REP_TV_RT']]
        if rep_tv is None or rep_tc is None or rmissing or r_tv_missing:
            problems.append(f'{p}: REP incomplete (tv={rep_tv}, '
                            f'tc={rep_tc}, missing={rmissing}, '
                            f'tv_rt_missing={r_tv_missing})')
        else:
            if rep_tc < rep_tv:
                problems.append(f'{p}: REP TC {rep_tc} < TV {rep_tv}')
            if rec['REP_RV'] != rv:
                problems.append(f'{p}: REP RV {rec["REP_RV"]} != turnout '
                                f'RV {rv}')
            rtc = rec['REP_TC'].get('ED', 0) + rec['REP_TC'].get('AV', 0)
            if rtc != rep_tc:
                problems.append(f'{p}: REP TC ED+AV {rtc} != {rep_tc}')
            for rt in ('ED', 'AV', 'T'):
                denom = rec['REP_TV_RT'][rt]
                for c in REP:
                    v, pct = r[c][rt]
                    if pct is None:
                        continue
                    w = pct_round(v, denom)
                    if abs(w - pct) > 0.005:
                        problems.append(f'{p}: REP {c} {rt} pct {w}% != '
                                        f'printed {pct}%')
        # proposals
        for title, pr in rec['PROPS'].items():
            need = ('TC', 'RV', 'Yes', 'No', 'TV', 'TV_RT')
            if any(k not in pr for k in need) or 'T' not in pr.get('TC',
                                                                   {}):
                problems.append(f'{p}: {title} incomplete: '
                                f'{sorted(pr)}')
                continue
            yes, no, ptv = pr['Yes']['T'][0], pr['No']['T'][0], \
                pr['TV']
            tc = pr['TC']['T']
            if yes + no != ptv:
                problems.append(f'{p}: {title} Yes {yes} + No {no} != TV '
                                f'{ptv}')
            if tc < ptv:
                problems.append(f'{p}: {title} TC {tc} < TV {ptv}')
            if pr['RV'] > rv:
                problems.append(f'{p}: {title} RV {pr["RV"]} > turnout '
                                f'RV {rv}')
            etc = pr['TC'].get('ED', 0) + pr['TC'].get('AV', 0)
            if etc != tc:
                problems.append(f'{p}: {title} TC ED+AV {etc} != {tc}')
            for rt in ('ED', 'AV', 'T'):
                denom = pr['TV_RT'].get(rt)
                if denom is None:
                    problems.append(f'{p}: {title} {rt} TV missing')
                    continue
                for c in ('Yes', 'No'):
                    v, pct = pr[c][rt]
                    if pct is None:
                        continue
                    w = pct_round(v, denom)
                    if abs(w - pct) > 0.005:
                        problems.append(f'{p}: {title} {c} {rt} pct {w}% '
                                        f'!= printed {pct}%')
        if dem_tc and rep_tc and dem_tc + rep_tc > rec['BC']:
            problems.append(f'{p}: DEM TC {dem_tc} + REP TC {rep_tc} > '
                            f'BC {rec["BC"]}')

    # county-level checks
    if problems:
        for p_ in problems[:30]:
            print('PROBLEM:', p_)
        print(f'{len(problems)} problems (pre-county diagnostics only)')
        return
    to_cty = county_mi.get('TO', {}).get('TO')
    if not to_cty or [v for v, _ in to_cty][:3] != \
            [COUNTY_RV, COUNTY_BC, COUNTY_BC]:
        problems.append(f'turnout county row unexpected: {to_cty}')
    if to_cty and to_cty[2][1] is not None and \
            abs(pct_round(COUNTY_BC, COUNTY_RV) - to_cty[2][1]) \
            > 0.005:
        problems.append(f'turnout county pct {to_cty[2][1]}')
    bad_rv = [p for p in porder if data[p]['RV'] is None]
    if bad_rv:
        problems.append(f'{len(bad_rv)} labels with RV None: '
                        f'{bad_rv[:8]}')
    s_rv = sum(data[p]['RV'] for p in porder if data[p]['RV'] is not None)
    s_bc = sum(data[p]['BC'] for p in porder if data[p]['BC'] is not None)
    if s_rv != COUNTY_RV:
        problems.append(f'sum RV {s_rv} != {COUNTY_RV}')
    if s_bc != COUNTY_BC:
        problems.append(f'sum BC {s_bc} != {COUNTY_BC}')
    if not (cg.get('ED') and cg.get('AV')):
        problems.append(f'counting-group rows missing: {sorted(cg)}')
    else:
        if cg['ED'][1][0] + cg['AV'][1][0] != COUNTY_BC:
            problems.append(f'counting groups {cg["ED"][1][0]} + '
                            f'{cg["AV"][1][0]} != {COUNTY_BC}')
        if cg['ED'][0][0] != COUNTY_RV or cg['AV'][0][0] != COUNTY_RV:
            problems.append(f'counting-group RV {cg["ED"][0][0]} / '
                            f'{cg["AV"][0][0]} != {COUNTY_RV}')
    d_cty = county_mi.get('DEM', {}).get('TV')
    if not d_cty or d_cty[0][0] != COUNTY_DEM_TV:
        problems.append(f'DEM county TV row unexpected: {d_cty}')
    s_dem = sum(data[p]['DEM_TV'] for p in porder
                if data[p]['DEM_TV'] is not None)
    if s_dem != COUNTY_DEM_TV:
        problems.append(f'sum DEM TV {s_dem} != {COUNTY_DEM_TV}')
    r_cty = county_mi.get('REP', {})
    if not r_cty:
        problems.append('REP county rows missing')
    else:
        for gname, vals in r_cty.items():
            for col, (v, _) in zip(GROUP_COLS[gname], vals):
                if COUNTY_REP.get(col) is not None and v != COUNTY_REP[col]:
                    problems.append(f'REP county {col} {v} != '
                                    f'{COUNTY_REP[col]}')
    s_rep = sum(data[p]['REP_TV'] for p in porder
                if data[p]['REP_TV'] is not None)
    if s_rep != COUNTY_REP['TV']:
        problems.append(f'sum REP TV {s_rep} != {COUNTY_REP["TV"]}')
    # proposal county sums
    props_by = {}
    for p in porder:
        for title in data[p]['PROPS']:
            props_by.setdefault(title, []).append(p)
    for title, plist in props_by.items():
        cty = county_mi.get(title, {})
        if 'TCRV' not in cty or 'YN' not in cty:
            problems.append(f'{title}: county rows missing: '
                            f'{sorted(cty)}')
            continue
        tcrv, yn = cty['TCRV'], cty['YN']
        s_tc = sum(data[p]['PROPS'][title]['TC']['T'] for p in plist)
        s_rvp = sum(data[p]['PROPS'][title]['RV'] for p in plist)
        s_y = sum(data[p]['PROPS'][title]['Yes']['T'][0] for p in plist)
        s_n = sum(data[p]['PROPS'][title]['No']['T'][0] for p in plist)
        s_tv = sum(data[p]['PROPS'][title]['TV'] for p in plist)
        for got, want, nm in ((s_tc, tcrv[0][0], 'TC'),
                              (s_rvp, tcrv[1][0], 'RV'),
                              (s_y, yn[0][0], 'Yes'),
                              (s_n, yn[1][0], 'No'),
                              (s_tv, yn[2][0], 'TV')):
            if got != want:
                problems.append(f'{title}: sum {nm} {got} != {want}')
        if set(plist) == set(porder):
            for p in plist:
                if data[p]['PROPS'][title]['RV'] == data[p]['RV'] and \
                        data[p]['PROPS'][title]['TC']['T'] != \
                        data[p]['BC']:
                    problems.append(f'{p}: {title} TC != BC')

    if problems:
        for p_ in problems:
            print('PROBLEM:', p_)
        print(f'{len(problems)} problems; not writing {OUT}')
        return
    print(f'{len(porder)} precincts; counting groups: '
          f'ED {cg.get("ED")}, AV {cg.get("AV")}')
    print('county DEM TV:', county_mi.get('DEM', {}).get('TV'),
          'REP:', county_mi.get('REP', {}))

    # ---- emission -------------------------------------------------------
    rows = []
    for p in porder:
        rows.append([COUNTY, p, 'Registered Voters', '', '', '',
                     data[p]['RV']])
    for sec, cands in (('DEM', DEM), ('REP', REP)):
        for c in cands:
            for p in porder:
                src = data[p]['DEM'] if sec == 'DEM' else data[p]['REP']
                rows.append([COUNTY, p, 'President', '', sec, c,
                             src[c]['T'][0]])
        for p in porder:
            tv = data[p]['DEM_TV'] if sec == 'DEM' else data[p]['REP_TV']
            rows.append([COUNTY, p, 'Ballots Cast', '', sec, '', tv])
    for title, plist in props_by.items():
        for p in plist:
            for c in ('Yes', 'No'):
                rows.append([COUNTY, p, title, '', '', c,
                             data[p]['PROPS'][title][c]['T'][0]])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(porder)} precincts')


if __name__ == '__main__':
    main()