#!/usr/bin/env python3
"""Parse Leelanau County's Mar 2020 presidential primary from
'Leelanau MI March 2020 Statement of Votes Cast.pdf' (6pp ES&S
Statement of Votes Cast, text-extractable, contest-major).

Layout:
  - Page 1: turnout per precinct as method rows (Election Day / AV
    Counting Boards / Total) over Registered Voters, Cards Cast,
    Voters Cast, % Turnout; ends with the county Total row.
  - Pages 2-3: DEM President; pages 4-5: REP President; page 6: the
    Suttons Bay-Bingham fire millage proposal (Yes/No, Bingham P1 and
    Suttons Bay P1 only).  Candidate headers are rotated-90 cells
    (their extracted text reads reversed); the column order is Times
    Cast, Registered Voters, candidates in ballot order, Total Votes,
    Unresolved Write-In.  Method rows repeat the precinct's Registered
    Voters and add a Times Cast column.
  - Contest rows only cover the affected precincts (the proposal has
    just 2; county Total rows likewise).

Emission: Registered Voters once per precinct (page-1 Total row), DEM
and REP President rows per candidate using the Total method row,
per-party Ballots Cast from the contest's Total Votes column, then the
proposal's Yes/No rows.  'Unresolved Write-In' becomes 'Write-In'.
Precinct names stay verbatim ('Elmwood Twp, Precinct 1').

Verification: per precinct and contest, the candidates sum to the
printed Total Votes (Unresolved Write-In is NOT part of it); Total
method rows equal Election Day + AV Counting Boards columnwise except
the repeated Registered Voters column; the county Total row reproduces
the precinct sums; the turnout page's county row reproduces the
precinct sums.
"""

import csv
import os
import re
import sys

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Leelanau MI March 2020 Statement of Votes '
       'Cast.pdf')
OUT = '2020/counties/20200310__mi__primary__president__leelanau__precinct.csv'
COUNTY = 'Leelanau'

NUM = r'[\d,]+'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
       'Cory Booker', 'Pete Buttigieg', 'Julian Castro',
       'John Delaney', 'Tulsi Gabbard', 'Amy Klobuchar',
       'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

TURNOUT_RE = re.compile(
    rf'^(Election Day|AV Counting Boards|Total) ({NUM}) ({NUM}) ({NUM}) '
    rf'([\d.]+)%$')
METHOD_RE = re.compile(r'^(Election Day|AV Counting Boards|Total) (.+)$')
COUNTY_TOTAL_RE = re.compile(r'^Leelanau County Michigan - Total (.+)$')

FURNITURE = {
    'Statement of Votes Cast', 'Closed Primary', 'Leelanau County, Michigan',
    '3/10/2020', 'OFFICIAL RESULTS', 'Registered',
    'Precinct Cards Cast Voters Cast % Turnout', 'Voters',
}


def ints(s):
    return [int(x.replace(',', '')) for x in s.split()]


def main():
    pdf = pdfplumber.open(SRC)
    problems = []
    turnout_order = []
    turnout = {}    # prec -> method -> (rv, cards, voters)
    contests = {}   # section -> prec -> method -> [values]
    cur_prec = None
    section = 'turnout'     # 'turnout' | ('President', party) | prop title

    for page in pdf.pages:
        for raw in (page.extract_text() or '').split('\n'):
            line = raw.strip()
            if not line or line in FURNITURE \
                    or re.match(r'^Page \d+ of \d+$', line) \
                    or re.match(r'^OVC for: ', line) \
                    or re.fullmatch(r'[a-z]', line):
                continue
            m = re.match(r'^President of the United States.* (DEM|REP)$',
                         line)
            if m:
                section = ('President', m.group(1))
                contests.setdefault(section, {})
                continue
            m = re.match(r'^(.*) \(Vote for \d+\)$', line)
            if m and 'President of the United States' not in line:
                section = m.group(1).strip()
                contests.setdefault(section, {})
                continue

            cm = COUNTY_TOTAL_RE.match(line)
            if cm:
                continue    # county rows are collected in the 2nd pass
            tm = TURNOUT_RE.match(line)
            mm = METHOD_RE.match(line)
            if not tm and not mm:
                # a bare precinct-name line opens the next block; the
                # reversed candidate headers are long digit-less garbage
                if len(line) > 60 and ',' not in line:
                    continue
                cur_prec = line
                continue

            if tm and section == 'turnout':
                vals = (int(tm.group(2).replace(',', '')),
                        int(tm.group(3).replace(',', '')),
                        int(tm.group(4).replace(',', '')))
                if cur_prec not in turnout:
                    turnout_order.append(cur_prec)
                turnout.setdefault(cur_prec, {})[tm.group(1)] = vals
                continue
            if tm:
                continue    # stray turnout-shaped line inside a contest
            if mm:
                vals = ints(mm.group(2))
                contests.setdefault(section, {}) \
                        .setdefault(cur_prec, {})[mm.group(1)] = vals
                continue
            problems.append(f'unmatched line: {line!r}')

    county_total = None

    # ---- verification ---------------------------------------------------
    # collect the printed county Total rows per section first
    county_rows = {}
    sect = 'turnout'
    for page in pdf.pages:
        for raw in (page.extract_text() or '').split('\n'):
            line = raw.strip()
            m = re.match(r'^President of the United States.* (DEM|REP)$',
                         line)
            if m:
                sect = ('President', m.group(1))
                continue
            m = re.match(r'^(.*) \(Vote for \d+\)$', line)
            if m and 'President of the United States' not in line:
                sect = m.group(1).strip()
                continue
            m = COUNTY_TOTAL_RE.match(line)
            if m:
                vals = m.group(1).split()
                if sect == 'turnout':
                    vals = vals[:3]     # drop the trailing % Turnout
                county_rows[sect] = ints(' '.join(vals))

    # turnout page: Total == Election Day + AV Counting Boards (cards,
    # voters); Registered Voters repeats across the method rows
    sum_rv = sum_voters = 0
    for prec in turnout_order:
        rows = turnout[prec]
        if set(rows) < {'Election Day', 'Total'}:
            problems.append(f'{prec!r}: turnout methods {sorted(rows)}')
            continue
        if rows['Election Day'][0] != rows['Total'][0]:
            problems.append(f'{prec!r}: RV differs between method rows')
        if 'AV Counting Boards' in rows:
            if rows['Election Day'][0] != rows['AV Counting Boards'][0]:
                problems.append(f'{prec!r}: AV RV differs')
            for j in (1, 2):
                if rows['Total'][j] != rows['Election Day'][j] \
                        + rows['AV Counting Boards'][j]:
                    problems.append(
                        f'{prec!r}: turnout Total {rows["Total"][j]} '
                        f'!= ED+AV {rows["Election Day"][j]} + '
                        f'{rows["AV Counting Boards"][j]}')
        else:
            if rows['Total'] != rows['Election Day']:
                problems.append(f'{prec!r}: turnout Total != Election Day')
        sum_rv += rows['Total'][0]
        sum_voters += rows['Total'][2]
    if county_rows.get('turnout'):
        want = county_rows['turnout']
        if sum_rv != want[0] or sum_voters != want[2]:
            problems.append(f'turnout county row {want} vs precinct '
                            f'sums RV {sum_rv} voters {sum_voters}')

    # contests: candidates sum to Total Votes; Total == ED + AV
    # (contest pages abbreviate 'Elmwood Twp,'/'Glen Arbor Twp,'/
    # 'Suttons Bay Twp,' where page 1 and the county's other files
    # spell 'Township,' -- alias the keys onto the turnout names)
    for sect, precs in contests.items():
        for prec in list(precs):
            if prec in turnout:
                continue
            alias = re.sub(r'\bTwp,', 'Township,', prec)
            if alias in turnout:
                precs[alias] = precs.pop(prec)
            else:
                problems.append(f'{sect}: precinct {prec!r} not on the '
                                f'turnout page')
    contest_cols = {('President', 'DEM'): DEM, ('President', 'REP'): REP,
                    'Suttons Bay-Bingham Fire Mill Increase Prop':
                        ['Yes', 'No']}
    contest_county = {}
    for sect, precs in contests.items():
        cands = contest_cols[sect]
        for prec, rows in sorted(precs.items()):
            for method, vals in rows.items():
                if len(vals) != len(cands) + 4:
                    problems.append(f'{sect} {prec!r} {method}: '
                                    f'{len(vals)} values, expected '
                                    f'{len(cands) + 4}')
            if 'Total' not in rows:
                problems.append(f'{sect} {prec!r}: no Total row')
                continue
            tot = rows['Total']
            got = sum(tot[2:2 + len(cands)])
            if got != tot[-2]:
                problems.append(f'{sect} {prec!r}: candidates {got} '
                                f'!= Total Votes {tot[-2]}')
            if 'AV Counting Boards' in rows:
                av = rows['AV Counting Boards']
                for j in range(len(tot)):
                    if j == 1:
                        if av[j] != tot[j]:
                            problems.append(
                                f'{sect} {prec!r}: AV RV {av[j]} '
                                f'!= Total RV {tot[j]}')
                        continue
                    if av[j] + rows['Election Day'][j] != tot[j]:
                        problems.append(
                            f'{sect} {prec!r} col {j}: Total {tot[j]} '
                            f'!= ED {rows["Election Day"][j]} + AV {av[j]}')
            else:
                if rows['Election Day'][1:] != tot[1:]:
                    problems.append(f'{sect} {prec!r}: Total != Election '
                                    f'Day without an AV row')

    # county Total rows reproduce the precinct sums per contest
    sums = {}
    for sect, precs in contests.items():
        cands = contest_cols[sect]
        n = len(cands) + 4
        acc = [0] * n
        for prec, rows in precs.items():
            for j, v in enumerate(rows['Total']):
                acc[j] += v
        sums[sect] = acc
        want = county_rows.get(sect)
        if want is None:
            problems.append(f'{sect}: no county Total row found')
        elif want != acc:
            problems.append(f'{sect}: county row {want} != precinct '
                            f'sums {acc}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)

    # ---- emission -------------------------------------------------------
    rows = []
    for prec in turnout_order:
        rows.append([COUNTY, prec, 'Registered Voters', '', '', '',
                     turnout[prec]['Total'][0]])
    for sect, party, cands in ((('President', 'DEM'), 'DEM', DEM),
                               (('President', 'REP'), 'REP', REP)):
        precs = contests[sect]
        order = [p for p in turnout_order if p in precs]
        for i, cand in enumerate(cands):
            for prec in order:
                rows.append([COUNTY, prec, 'President', '', party, cand,
                             precs[prec]['Total'][2 + i]])
        for prec in order:
            rows.append([COUNTY, prec, 'President', '', party, 'Write-In',
                         precs[prec]['Total'][-1]])
        for prec in order:
            rows.append([COUNTY, prec, 'Ballots Cast', '', party, '',
                         precs[prec]['Total'][-2]])
    prop = 'Suttons Bay-Bingham Fire Mill Increase Prop'
    for i, cand in enumerate(('Yes', 'No')):
        for prec in contests[prop]:
            rows.append([COUNTY, prec, prop, '', '', cand,
                         contests[prop][prec]['Total'][2 + i]])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(turnout_order)} precincts')


if __name__ == '__main__':
    main()