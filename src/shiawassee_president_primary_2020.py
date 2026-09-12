"""Parse Shiawassee County's Mar 2020 presidential primary from
'Shiawasse MI Primary.pdf' (70pp Electionware 'Summary Results Report',
portrait, text-extractable; note the source's 'Shiawasse' spelling).

Structure (precinct-major, 2 pages per precinct, 35 precincts):
each odd page carries the precinct header (name, 'Precincts
Reported', 'Registered Voters: <cast> of <RV> (<pct>%)',
'Ballots Cast: <n>'), then the DEM President contest and the REP
President contest (Times Cast line, Candidate/Party/Total rows, Total
Votes, Unresolved Write-In).  The even page of each set is empty, or
holds that precinct's share of any local proposals (one block per
proposal: title, Times Cast, Yes/No, Total Votes, Unresolved
Write-In).  'Precincts Reported: N of M' on proposal blocks counts the
whole contest's jurisdictions, not this page's scope -- the numbers
are per precinct (e.g. Durand 1: TC 319 == BC 319, Durand 2: 375).

Ballots Cast can exceed DEM TC + REP TC (blank/undervoted ballots
unattributed to either party contest -- only in jurisdictions with
city proposals: Durand, Owosso, New Haven; checked as <=, not ==).
Ballots Cast per party is emitted as that contest's Times Cast.
'Unresolved Write-In' sits outside Total Votes and is not emitted.

Checks: candidate sum == Total Votes per contest; Times Cast >= Total
Votes; Times Cast denominator == RV; printed pcts == computed; DEM TC
+ REP TC <= Ballots Cast; proposal Yes+No == TV and TC == BC.

Usage:
    .venv/bin/python src/shiawassee_president_primary_2020.py
"""
import csv
import os
import re
import sys

import pdfplumber

COUNTY = 'Shiawassee'
OUT = ('2020/counties/20200310__mi__primary__president__'
       'shiawassee__precinct.csv')
SRC = os.path.expanduser('~/code/openelections-sources-mi/2020/'
                         'presidential_primary/Shiawasse MI Primary.pdf')

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
       'Cory Booker', 'Pete Buttigieg', 'Julian Castro', 'John Delaney',
       'Tulsi Gabbard', 'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak',
       'Tom Steyer', 'Elizabeth Warren', 'Marianne Williamson',
       'Andrew Yang', 'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']
CANON = {'Julián Castro': 'Julian Castro'}

EXPECTED_PRECINCTS = 35
VAL_RE = re.compile(r'^(.+?) (\d[\d,]*)$')


def ival(text):
    return int(text.replace(',', ''))


def main():
    problems = []
    data = {}       # precinct -> {'RV':, 'BC':, 'DEM': {cand: v},
                    #              'DEM_TC':, 'DEM_TV':, ...,
                    #              'props': {title: {...}}}
    porder = []
    wi_county = {'DEM': 0, 'REP': 0}

    def parse_contest(rec, label, pno, lines, start, end):
        sec = None
        for ln in lines[start:end]:
            s = ln.strip()
            if s.startswith('President of the United States'):
                sec = 'DEM' if '(DEM)' in s else 'REP'
                rec.setdefault(sec, {})
                continue
            m = re.match(r'Times Cast (\d[\d,]*) / (\d[\d,]*) '
                         r'(\d+\.\d+)%$', s)
            if m and sec is not None:
                rec[f'{sec}_TC'] = ival(m.group(1))
                rec[f'{sec}_TC_of'] = ival(m.group(2))
                rec[f'{sec}_TC_pct'] = float(m.group(3))
                continue
            m = VAL_RE.match(s)
            if not m or sec is None:
                continue
            name = CANON.get(m.group(1).strip(), m.group(1).strip())
            cands = DEM if sec == 'DEM' else REP
            if name in cands:
                rec[sec][name] = ival(m.group(2))
            elif name == 'Total Votes':
                rec[f'{sec}_TV'] = ival(m.group(2))
            elif name == 'Unresolved Write-In':
                rec[f'{sec}_WI'] = ival(m.group(2))
        for sec in ('DEM', 'REP'):
            for key in (f'{sec}_TC', f'{sec}_TV', f'{sec}_WI'):
                if key not in rec:
                    problems.append(f'{label}: {key} missing')

    with pdfplumber.open(SRC) as pdf:
        for pno in range(1, len(pdf.pages) + 1):
            text = pdf.pages[pno - 1].extract_text() or ''
            lines = text.split('\n')
            first = next((ln.strip() for ln in lines
                          if not ln.startswith('Page:')), '')
            if pno % 2 == 1:
                label = first
                if not label or '(Vote for' in label:
                    problems.append(f'p{pno}: no precinct label')
                    continue
                rec = data.setdefault(label, {})
                porder.append(label)
                m = re.search(r'Registered Voters: (\d[\d,]*) of '
                              r'(\d[\d,]*) \((\d+\.\d+)%\)', text)
                if m:
                    rec['cast'] = ival(m.group(1))
                    rec['RV'] = ival(m.group(2))
                    rec['pct'] = float(m.group(3))
                else:
                    problems.append(f'{label}: Registered Voters line '
                                    f'missing')
                m = re.search(r'Ballots Cast: (\d[\d,]*)', text)
                if m:
                    rec['BC'] = ival(m.group(1))
                else:
                    problems.append(f'{label}: Ballots Cast line missing')
                # proposals never print on the precinct page itself
                parse_contest(rec, label, pno, lines, 0, len(lines))
            else:
                if not first:
                    continue      # empty page 2
                rec = data.setdefault(porder[-1], {})
                label = porder[-1]
                # one proposal block per '(Vote for 1)' title
                marks = [i for i, ln in enumerate(lines)
                         if '(Vote for' in ln]
                marks.append(len(lines))
                for a, b in zip(marks, marks[1:]):
                    title = lines[a].strip()
                    title = title[:title.index('(Vote for')].strip()
                    pr = rec.setdefault('props', {}).setdefault(title, {})
                    body = '\n'.join(lines[a:b])
                    m = re.search(r'Times Cast (\d[\d,]*) / (\d[\d,]*) '
                                  r'(\d+\.\d+)%', body)
                    if m:
                        pr['TC'] = ival(m.group(1))
                        pr['TC_of'] = ival(m.group(2))
                    else:
                        problems.append(f'{label}: {title} Times Cast '
                                        f'missing')
                    for k, pat in (('Yes', r'^Yes (\d[\d,]*)$'),
                                   ('No', r'^No (\d[\d,]*)$'),
                                   ('TV', r'^Total Votes (\d[\d,]*)$'),
                                   ('WI', r'^Unresolved Write-In '
                                          r'(\d[\d,]*)$')):
                        m2 = re.search(pat, body, re.M)
                        if m2:
                            pr[k] = ival(m2.group(1))
                        else:
                            problems.append(f'{label}: {title} {k} '
                                            f'missing')

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
            s = sum(pd[c] for c in cands)
            if s != rec[f'{sec}_TV']:
                problems.append(f'{p}: {sec} candidates {s} != TV '
                                f'{rec[f"{sec}_TV"]}')
            if rec[f'{sec}_TC'] < rec[f'{sec}_TV']:
                problems.append(f'{p}: {sec} TC < TV')
            if rec[f'{sec}_TC_of'] != rec['RV']:
                problems.append(f'{p}: {sec} TC denominator '
                                f'{rec[f"{sec}_TC_of"]} != RV {rec["RV"]}')
            want = round(rec[f'{sec}_TC'] / rec['RV'] * 100, 2)
            if abs(want - rec[f'{sec}_TC_pct']) > 0.005:
                problems.append(f'{p}: {sec} turnout {want}% != printed '
                                f'{rec[f"{sec}_TC_pct"]}%')
            wi_county[sec] += rec[f'{sec}_WI']
        if rec['DEM_TC'] + rec['REP_TC'] > rec['BC']:
            problems.append(f'{p}: DEM TC + REP TC > BC')
        if rec.get('pct') is not None:
            want = round(rec['cast'] / rec['RV'] * 100, 2)
            if abs(want - rec['pct']) > 0.005:
                problems.append(f'{p}: turnout {want}% != printed '
                                f'{rec["pct"]}%')
        for title, pr in rec.get('props', {}).items():
            if pr['Yes'] + pr['No'] != pr['TV']:
                problems.append(f'{p}: {title} Yes+No != TV')
            if pr['TC'] < pr['TV']:
                problems.append(f'{p}: {title} TC < TV')
            # city proposals span the whole precinct; school-district
            # proposals are district-scoped (TC denominator < RV)
            if pr['TC_of'] == rec['RV'] and pr['TC'] != rec['BC']:
                problems.append(f'{p}: {title} TC {pr["TC"]} != BC '
                                f'{rec["BC"]}')
            if pr['TC_of'] > rec['RV']:
                problems.append(f'{p}: {title} TC denominator '
                                f'{pr["TC_of"]} > RV {rec["RV"]}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing {OUT}')
        return
    print('unresolved write-ins (not emitted):',
          {k: v for k, v in wi_county.items()})

    # ---- emission -------------------------------------------------------
    rows = []
    for p in porder:
        rec = data[p]
        rows.append([COUNTY, p, 'Registered Voters', '', '', '',
                     rec['RV']])
        for sec, cands in (('DEM', DEM), ('REP', REP)):
            for c in cands:
                rows.append([COUNTY, p, 'President', '', sec, c,
                             rec[sec][c]])
            rows.append([COUNTY, p, 'Ballots Cast', '', sec, '',
                         rec[f'{sec}_TC']])
        for title, pr in rec.get('props', {}).items():
            for c in ('Yes', 'No'):
                rows.append([COUNTY, p, title, '', '', c, pr[c]])
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