"""Parse the Genesee County Mar 10 2020 presidential primary from the
Electionware 'Canvass Results' PDF (text-extractable).

Source (openelections-sources-mi/2020/presidential_primary):
'Genessee MI Canvass Results-3-16-2020 16-26-49 PM.pdf', 60 pages,
landscape (note the source spelling 'Genessee').

Structure (contest-major, one contest per page):
- pp1-24 DEM President, printed in page PAIRS: odd pages carry 13
  candidate columns (Bennet..Warren), the following even page carries
  Marianne Williamson / Andrew Yang / Uncommitted + the tail columns
  (Cast Votes, Undervotes, Overvotes, Rejected write-in votes,
  Unresolved write-in votes, Election Day Ballots Cast, Total Ballots
  Cast, Registered Voters).  A 'Totals' row ends the last tail page.
- pp25-36 REP President, 13-column layout on every page (Sanford,
  Trump, Walsh, Weld, Uncommitted + the same tail columns).
- pp37-60 seven extra ballot contests, 2 candidate columns + tail:
  'Representative in State Legislature 34th District Partial Term
  Ending 1/1/2021 - Nonpartisan' (Cynthia R. Neeley / Adam Ford) and
  six Yes/No proposals (Davison Community Schools Bonding, Flint
  Community Schools Bonding, Flint Community Schools Sinking Fund
  Millage, Millington Community Schools Operating Millage I & II, Mott
  Community College Bond Proposition).  Scoped: each contest prints
  only its own precincts, so its Totals row RV is a partial sum.

Layout details:
- Rotated headers extract as individually REVERSED lines ('tenneB' /
  'leahciM' = Michael Bennet) -- ignored; the page's column set is
  derived from the contest title + row value count (13 no-pct = DEM
  candidates, 11 = DEM tail, 13 = REP, 10 = extra contest; the last
  numeric token of pct-bearing rows is the printed Percent).
- Two-line wrapped precinct labels put the number AFTER the value row
  ('Argentine Township, Precinct' / '2 265 1 ...' / '1'); single-token
  lines are therefore label continuations of the row above.
- Cast Votes = sum of the candidate votes (write-ins excluded);
  Election Day Ballots Cast == Total Ballots Cast == Cast Votes +
  Undervotes + Overvotes + Rejected write-in votes;
  Percent = Total Ballots Cast / RV.  Rejected/Unresolved write-in
  votes sit outside Cast Votes and are not emitted.
- The 34th District contest title prints only on its first page;
  pp38-39 are continuations (title carried forward).

Usage:
    .venv/bin/python src/genesee_president_primary_2020.py
"""
import csv
import os
import re

import pdfplumber

COUNTY = 'Genesee'
OUT = '2020/counties/20200310__mi__primary__president__genesee__precinct.csv'
SRC = os.path.expanduser('~/code/openelections-sources-mi/2020/'
                         'presidential_primary/Genessee MI Canvass '
                         'Results-3-16-2020 16-26-49 PM.pdf')

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
       'Cory Booker', 'Pete Buttigieg', 'Julian Castro', 'John Delaney',
       'Tulsi Gabbard', 'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak',
       'Tom Steyer', 'Elizabeth Warren', 'Marianne Williamson',
       'Andrew Yang', 'Uncommitted']
DEM_13 = DEM[:13]
DEM_TAIL_CANDS = ['Marianne Williamson', 'Andrew Yang', 'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']
TAIL = ['Cast Votes', 'Undervotes', 'Overvotes',
        'Rejected write-in votes', 'Unresolved write-in votes',
        'Election Day Ballots Cast', 'Total Ballots Cast',
        'Registered Voters']
HD34 = ('Representative in State Legislature 34th District Partial '
        'Term Ending 1/1/2021')
HD34_CANDS = ['Cynthia R. Neeley', 'Adam Ford']
DEM_PAGES = range(1, 25)
REP_PAGES = range(25, 37)
EXTRA_PAGES = range(37, 61)
NUM_RE = re.compile(r'\d[\d,]*(?:\.\d+)?')
EXPECTED_PRECINCTS = 216    # the page footers read '216 of 8 = 2,700.00 %'
COUNTY_RV = '333981'
# value counts (excluding the trailing Percent) per page kind
KIND_K = {'DEM': 11, 'REP': 13, 'extra': 10}


def ival(text):
    return int(text.replace(',', ''))


def main():
    problems = []
    data = {}       # contest -> precinct -> column -> value
    totals = {}     # contest -> column -> printed Totals value
    porder = []
    last = None       # (title, contest) carried across continuation pages

    def store(contest, precinct, col, val, ctx):
        cur = data.setdefault(contest, {}).setdefault(precinct, {}) \
                  .get(col)
        if cur is not None and cur != val:
            problems.append(f'{ctx}: {contest} {precinct} {col} '
                            f'{cur} != {val}')
        data[contest][precinct][col] = val

    with pdfplumber.open(SRC) as pdf:
        for pno in list(DEM_PAGES) + list(REP_PAGES) + list(EXTRA_PAGES):
            text = pdf.pages[pno - 1].extract_text() or ''
            lines = text.split('\n')
            # contest title line
            title = None
            tidx = None
            for i, ln in enumerate(lines):
                if ln.startswith('President of the United States'):
                    title = 'DEM' if 'Democratic' in ln else 'REP'
                    tidx = i
                    break
                if ' - Nonpartisan' in ln:
                    # the 34th District line continues with
                    # '- Vote for not more than 1'
                    title = ln[:ln.index(' - Nonpartisan')].strip()
                    tidx = i
                    break
            if title is None:
                # continuation page (e.g. pp38-39 of the 34th District
                # contest): carry the previous page's contest forward
                if last is None:
                    problems.append(f'p{pno}: no contest title')
                    continue
                title, contest = last
                tidx = -1          # no title line: use the whole page
            else:
                contest = title
                last = (title, contest)
            # ---- build raw rows (label, values) with wrap handling
            pending = None       # label-only line
            raw = []
            for ln in lines[tidx + 1:]:
                s = ln.strip()
                if not s or s.startswith(('Run Time', 'Run Date',
                                          'Page ')):
                    continue
                toks = [(m.start(), m.group()) for m in
                        NUM_RE.finditer(s)]
                if not toks:
                    pending = s.rstrip(',')
                    continue
                if len(toks) == 1 and not pending:
                    # wrapped label continuation: precinct number after
                    # the value row above
                    if not raw:
                        problems.append(f'p{pno}: stray number {s!r}')
                        continue
                    raw[-1][0].append(s)
                    continue
                has_pct = '.' in toks[-1][1]
                if title == 'DEM' and not has_pct:
                    k, vals = 13, [t[1] for t in toks[-13:]]
                else:
                    k = KIND_K[title if title in KIND_K else 'extra']
                    if not has_pct:
                        problems.append(f'p{pno}: {title} row without '
                                        f'pct: {s[:50]!r}')
                        continue
                    vals = [t[1] for t in toks[-(k + 1):-1]]
                vstart = toks[len(toks) - len(vals)
                              - (1 if has_pct else 0)][0]
                text_label = s[:vstart].strip().rstrip(',')
                parts = ([pending] if pending else []) + \
                    ([text_label] if text_label else [])
                pending = None
                raw.append([parts, vals])
            for parts, vals in raw:
                label = ' '.join(parts).strip().rstrip(',')
                if label == 'Totals':
                    # column set follows the VALUE COUNT: the last DEM
                    # candidate page (p23) carries its own 13-value
                    # Totals row (candidate county totals) while tail
                    # pages print the 11-value Totals
                    if title == 'DEM' and len(vals) == 13:
                        cols = DEM_13
                    elif title == 'DEM' and len(vals) == 11:
                        cols = DEM_TAIL_CANDS + TAIL
                    elif title == 'REP' and len(vals) == 13:
                        cols = REP + TAIL
                    elif len(vals) == 10:
                        cols = (HD34_CANDS if contest.startswith(
                            'Representative in State Legislature')
                            else ['Yes', 'No']) + TAIL
                    else:
                        problems.append(f'p{pno}: {contest} Totals row '
                                        f'with {len(vals)} values')
                        continue
                    for col, v in zip(cols, vals):
                        cur = totals.setdefault(contest, {}).get(col)
                        if cur is not None and cur != v:
                            problems.append(f'p{pno}: {contest} Totals '
                                            f'{col} {cur} != {v}')
                        totals[contest][col] = v
                    continue
                if title == 'DEM' and len(vals) == 13:
                    cols = DEM_13
                elif title == 'DEM' and len(vals) == 11:
                    cols = DEM_TAIL_CANDS + TAIL
                elif title == 'REP' and len(vals) == 13:
                    cols = REP + TAIL
                elif len(vals) == 10:
                    cols = (HD34_CANDS if contest.startswith(
                        'Representative in State Legislature')
                        else ['Yes', 'No']) + TAIL
                else:
                    problems.append(f'p{pno}: {len(vals)} values for '
                                    f'{title} row {label!r}')
                    continue
                if label not in porder:
                    porder.append(label)
                for col, v in zip(cols, vals):
                    store(contest, label, col, v, f'p{pno}')

    # ---- checks ---------------------------------------------------------
    if len(porder) != EXPECTED_PRECINCTS:
        problems.append(f'{len(porder)} precincts != {EXPECTED_PRECINCTS}')
    # every precinct in both contests, with a full column set
    for contest, cands in (('DEM', DEM), ('REP', REP)):
        for p in porder:
            pd = data.get(contest, {}).get(p, {})
            missing = [c for c in cands if c not in pd]
            if missing:
                problems.append(f'{contest} {p}: missing {missing[:4]}')
                continue
            s = sum(ival(pd[c]) for c in cands)
            if s != ival(pd['Cast Votes']):
                problems.append(f'{contest} {p}: candidates {s} != Cast '
                                f'Votes {pd["Cast Votes"]}')
            cv, uv, ov = (ival(pd['Cast Votes']), ival(pd['Undervotes']),
                          ival(pd['Overvotes']))
            rej = ival(pd['Rejected write-in votes'])
            edbc, tbc = ival(pd['Election Day Ballots Cast']), \
                ival(pd['Total Ballots Cast'])
            if cv + uv + ov + rej != edbc:
                problems.append(f'{contest} {p}: CV {cv} + UV {uv} + '
                                f'OV {ov} + Rej {rej} != ED BC {edbc}')
            if edbc != tbc:
                problems.append(f'{contest} {p}: ED BC {edbc} != Total '
                                f'BC {tbc}')
            if pd['Registered Voters'] != \
                    data['DEM' if contest == 'REP' else 'REP'][p][
                        'Registered Voters']:
                problems.append(f'{p}: RV differs between DEM and REP')
        for col in cands + TAIL:
            s = sum(ival(data[contest].get(p, {}).get(col, '0'))
                    for p in porder)
            want = totals.get(contest, {}).get(col)
            if want is None:
                problems.append(f'{contest}: no Totals for {col}')
            elif s != ival(want):
                problems.append(f'{contest} county {col}: {s} != {want}')
    if totals.get('DEM', {}).get('Registered Voters') != COUNTY_RV:
        problems.append(f'DEM Totals RV '
                        f'{totals.get("DEM", {}).get("Registered Voters")} '
                        f'!= {COUNTY_RV}')
    # extra contests: candidates sum == Cast Votes; Totals rows match
    for contest, cd in data.items():
        if contest in ('DEM', 'REP'):
            continue
        cands = HD34_CANDS if contest.startswith(
            'Representative in State Legislature') else ['Yes', 'No']
        for p, pd in cd.items():
            s = sum(ival(pd[c]) for c in cands if c in pd)
            if s != ival(pd['Cast Votes']):
                problems.append(f'{contest} {p}: candidates {s} != Cast '
                                f'Votes {pd["Cast Votes"]}')
            cv, uv, ov = (ival(pd['Cast Votes']), ival(pd['Undervotes']),
                          ival(pd['Overvotes']))
            if cv + uv + ov + ival(pd['Rejected write-in votes']) != \
                    ival(pd['Election Day Ballots Cast']):
                problems.append(f'{contest} {p}: CV+UV+OV+Rej != ED BC')
        for col in TAIL:
            s = sum(ival(pd[col]) for pd in cd.values() if col in pd)
            want = totals.get(contest, {}).get(col)
            if want is not None and s != ival(want):
                problems.append(f'{contest} county {col}: {s} != {want}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing {OUT}')
        return

    # ---- emission -------------------------------------------------------
    rows = []
    for p in porder:
        rows.append([COUNTY, p, 'Registered Voters', '', '', '',
                     ival(data['DEM'][p]['Registered Voters'])])
    for contest, cands in (('DEM', DEM), ('REP', REP)):
        for p in porder:
            pd = data[contest][p]
            for c in cands:
                rows.append([COUNTY, p, 'President', '', contest, c,
                             ival(pd[c])])
            rows.append([COUNTY, p, 'Ballots Cast', '', contest, '',
                         ival(pd['Total Ballots Cast'])])
    for contest, pd_all in data.items():
        if contest in ('DEM', 'REP'):
            continue
        cands = HD34_CANDS if contest.startswith(
            'Representative in State Legislature') else ['Yes', 'No']
        for p in sorted(pd_all):
            for c in cands:
                rows.append([COUNTY, p, contest, '', '', c,
                             ival(pd_all[p][c])])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(porder)} precincts, '
          f'extra contests: {[c for c in data if c not in ("DEM", "REP")]}')


if __name__ == '__main__':
    main()