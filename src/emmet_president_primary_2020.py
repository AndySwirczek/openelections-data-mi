"""Parse the Emmet County Mar 10 2020 presidential primary from the
Electionware 'Summary Results Report' PDF (text-extractable).

Source (openelections-sources-mi/2020/presidential_primary):
'Emmet MI March 2020 Precinct Results (unofficial).pdf', 47 pages,
portrait.

Structure (precinct-major, one precinct per page):
- pp1-3 Election Summary (countywide totals) -- used as cross-check
  constants only.
- pp4-47: 44 'Precinct Summary' pages = 22 precincts x 2 pages.  Page A
  = Statistics (RV / Ballots Cast by party, TOTAL + Election Day +
  Absentee columns) + 'DEM President of the United States'; page B =
  'REP President of the United States' + any local proposals
  (Petoskey City Initiative + Petoskey City Referendum on the 4
  Petoskey ward pages; Pellston Public Schools Pellston School District
  on 8 township pages).

Only the TOTAL column is emitted (the CSV has a single votes column).
Electionware 'Write-In Totals' sits outside 'Total Votes Cast' and is
not emitted.  'Ballots Cast - <PARTY>' == that contest's 'Contest
Totals' (checked), and the Statistics 'Ballots Cast - Total' == DEM +
REP per precinct (NPA/Blank rows carry the small unattributed
absentee-group remainder countywide).

Usage:
    .venv/bin/python src/emmet_president_primary_2020.py
"""
import csv
import os
import re

import pdfplumber

COUNTY = 'Emmet'
OUT = '2020/counties/20200310__mi__primary__president__emmet__precinct.csv'
SRC = os.path.expanduser('~/code/openelections-sources-mi/2020/'
                         'presidential_primary/Emmet MI March 2020 '
                         'Precinct Results (unofficial).pdf')

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
       'Cory Booker', 'Pete Buttigieg', 'Julian Castro', 'John Delaney',
       'Tulsi Gabbard', 'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak',
       'Tom Steyer', 'Elizabeth Warren', 'Marianne Williamson',
       'Andrew Yang', 'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']
CANON = {'Julián Castro': 'Julian Castro'}

COUNTY_STATS = {'Registered Voters - Total': '29,448',
                'Ballots Cast - Total': '9,310',
                'Ballots Cast - DEMOCRATIC PARTY': '5,732',
                'Ballots Cast - REPUBLICAN PARTY': '3,443'}
FIRST_PAGE = 4     # first Precinct Summary page (1-based)
EXPECTED_PRECINCTS = 22
STAT_KEYS = list(COUNTY_STATS)
NPA_KEY = 'Ballots Cast - NONPARTISAN'
TURNOUT_KEY = 'Voter Turnout - Total'
VAL3_RE = re.compile(r'^(.+?) (\d[\d,]* [\d,]* [\d,]*)$')
PARTY_STATS_KEY = {'DEM': 'Ballots Cast - DEMOCRATIC PARTY',
                   'REP': 'Ballots Cast - REPUBLICAN PARTY'}


def ival(text):
    return int(text.replace(',', ''))


def main():
    problems = []
    data = {}       # precinct -> {'stats': {key: v}, 'DEM': {cand: v},
                    #              'DEM_TVC','DEM_CT','REP': ...,
                    #              'props': {title: {Yes,No,TVC,CT}}}
    porder = []

    with pdfplumber.open(SRC) as pdf:
        for pno in range(FIRST_PAGE, len(pdf.pages) + 1):
            text = pdf.pages[pno - 1].extract_text() or ''
            lines = text.split('\n')
            # precinct label = first line before Statistics/contest headers
            label = None
            for ln in lines:
                if ln.startswith(('Summary Results', 'Presidential Primary',
                                  'March 10, 2020')):
                    continue
                if ln.startswith(('Statistics', 'DEM President',
                                  'REP President')):
                    break
                if ln.strip():
                    label = ln.strip()
                    break
            if label is None:
                problems.append(f'p{pno}: no precinct label')
                continue
            rec = data.setdefault(label, {})
            if label not in porder:
                porder.append(label)

            # Statistics block (present only on the A page)
            if any(ln.startswith('Statistics') for ln in lines):
                stats = rec.setdefault('stats', {})
                for key in STAT_KEYS + [NPA_KEY, TURNOUT_KEY]:
                    m = [ln for ln in lines if ln.startswith(key)]
                    if not m:
                        problems.append(f'p{pno}: {key} missing')
                        continue
                    if key == TURNOUT_KEY:
                        pm = re.search(r'(\d+(?:\.\d+)?)%', m[0])
                        nums = [pm.group(1)] if pm else []
                    else:
                        nums = re.findall(r'[\d,]+', m[0][len(key):])
                    if not nums:
                        problems.append(f'p{pno}: {key} no value')
                    else:
                        stats[key] = nums[0]

            # sections: DEM/REP contests + proposals.  A proposal title
            # is any line immediately followed by 'Vote For 1' that is
            # not a contest header.
            sec = None                 # 'DEM' | 'REP' | proposal title
            marks = []
            for i, ln in enumerate(lines):
                if ln.startswith('DEM President'):
                    sec = 'DEM'
                    marks.append((i, 'DEM'))
                    continue
                if ln.startswith('REP President'):
                    sec = 'REP'
                    marks.append((i, 'REP'))
                    continue
                if i + 1 < len(lines) and lines[i + 1] == 'Vote For 1' \
                        and not ln.startswith(('DEM President',
                                               'REP President')):
                    sec = ln.strip()
                    rec.setdefault('props', {}).setdefault(sec, {})
                    marks.append((i, sec))
                    continue
                m = VAL3_RE.match(ln.strip())
                if not m:
                    continue
                name = CANON.get(m.group(1).strip(), m.group(1).strip())
                vals = m.group(2).split()
                if sec in ('DEM', 'REP') and name in \
                        (DEM if sec == 'DEM' else REP):
                    rec.setdefault(sec, {})[name] = vals[0]
            marks.append((len(lines), None))
            for (start, kind), (end, _) in zip(marks, marks[1:]):
                body = '\n'.join(lines[start:end])
                if kind in ('DEM', 'REP'):
                    for key, pat in (
                            (f'{kind}_WI', r'^Write-In Totals ([\d,]+)'),
                            (f'{kind}_TVC', r'^Total Votes Cast ([\d,]+)'),
                            (f'{kind}_CT', r'^Contest Totals ([\d,]+)')):
                        m2 = re.search(pat, body, re.M)
                        if m2:
                            rec[key] = m2.group(1)
                        else:
                            problems.append(f'p{pno}: {key} missing')
                else:
                    pr = rec['props'][kind]
                    for k, pat in (('Yes', r'^Yes ([\d,]+)'),
                                   ('No', r'^No ([\d,]+)'),
                                   ('TVC', r'^Total Votes Cast ([\d,]+)'),
                                   ('CT', r'^Contest Totals ([\d,]+)')):
                        m2 = re.search(pat, body, re.M)
                        if m2:
                            pr[k] = m2.group(1)
                        else:
                            problems.append(f'p{pno}: {kind} {k} missing')

    # ---- checks ---------------------------------------------------------
    if len(porder) != EXPECTED_PRECINCTS:
        problems.append(f'{len(porder)} precincts != {EXPECTED_PRECINCTS}')
    for p in porder:
        rec = data[p]
        for sec, cands in (('DEM', DEM), ('REP', REP)):
            pd = rec.get(sec, {})
            missing = [c for c in cands if c not in pd]
            if missing:
                problems.append(f'{p}: {sec} missing {missing}')
                continue
            # Write-In Totals are INCLUDED in Total Votes Cast here
            s = sum(ival(pd[c]) for c in cands) + \
                ival(rec.get(f'{sec}_WI', '0'))
            tvc = rec.get(f'{sec}_TVC')
            if tvc is None:
                problems.append(f'{p}: {sec} TVC missing')
            elif s != ival(tvc):
                problems.append(f'{p}: {sec} candidates+WI {s} != '
                                f'TVC {tvc}')
            bc = rec['stats'].get(PARTY_STATS_KEY[sec])
            ct = rec.get(f'{sec}_CT')
            if bc and ct and bc != ct:
                problems.append(f'{p}: {sec} BC stats {bc} != Contest '
                                f'Totals {ct}')
        st = rec['stats']
        dem_bc, rep_bc = st.get(PARTY_STATS_KEY['DEM']), \
            st.get(PARTY_STATS_KEY['REP'])
        npa, tot = st.get(NPA_KEY), st.get('Ballots Cast - Total')
        # Blank ballots are NOT part of Ballots Cast - Total; NPA is
        if None not in (dem_bc, rep_bc, npa, tot) \
                and ival(dem_bc) + ival(rep_bc) + ival(npa) != ival(tot):
            problems.append(f'{p}: BC DEM {dem_bc} + REP {rep_bc} + NPA '
                            f'{npa} != Total {tot}')
        if tot is not None and st.get(TURNOUT_KEY):
            want = round(ival(tot) / ival(st['Registered Voters - Total'])
                         * 100, 2)
            got = float(st[TURNOUT_KEY].rstrip('%'))
            if abs(want - got) > 0.005:
                problems.append(f'{p}: turnout {want}% != printed '
                                f'{st[TURNOUT_KEY]}')
        for t, pr in rec.get('props', {}).items():
            if 'Yes' in pr and 'No' in pr and 'TVC' in pr:
                if ival(pr['Yes']) + ival(pr['No']) != ival(pr['TVC']):
                    problems.append(f'{p}: {t} Yes+No != TVC')
    # county-wide: sums of precinct stats == printed county statistics
    for key, want in COUNTY_STATS.items():
        vals = [data[p]['stats'].get(key) for p in porder]
        if any(v is None for v in vals):
            problems.append(f'{key}: missing on some pages')
            continue
        s = sum(ival(v) for v in vals)
        if s != ival(want):
            problems.append(f'{key} county sum {s} != {want}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing {OUT}')
        return

    # ---- emission -------------------------------------------------------
    rows = []
    for p in porder:
        rec = data[p]
        rows.append([COUNTY, p, 'Registered Voters', '', '', '',
                     ival(rec['stats']['Registered Voters - Total'])])
        for sec, cands in (('DEM', DEM), ('REP', REP)):
            pd = rec[sec]
            for c in cands:
                rows.append([COUNTY, p, 'President', '', sec, c,
                             ival(pd[c])])
            rows.append([COUNTY, p, 'Ballots Cast', '', sec, '',
                         ival(rec[f'{sec}_CT'])])
        for t, pr in rec.get('props', {}).items():
            for c in ('Yes', 'No'):
                rows.append([COUNTY, p, t, '', '', c, ival(pr[c])])

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