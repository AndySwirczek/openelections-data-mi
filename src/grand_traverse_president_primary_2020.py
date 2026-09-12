#!/usr/bin/env python3
"""Parse Grand Traverse County's Mar 2020 presidential primary from
'Grand Traverse MI Primary Election by Precinct.pdf' (8pp Electionware
Custom Table Report, text-extractable).

The report has three sections:
  - STATISTICS (pages 1-2): one row per precinct,
    'Name  Registered  DEM Ballots Cast  REP Ballots Cast  turnout%'.
    36 base precincts + 17 AVCB rows; each AVCB's Registered count is
    the SUM of its base precincts' (Acme AVCB 3,827 = 2,461 + 1,366).
  - DEM President (pages 2-6): 14 candidate columns on the main pages,
    then continuation pages with 3 more columns (Yang, Uncommitted,
    Write-in) plus each precinct's Total Votes Cast, then a Totals row.
  - REP President (pages 7-8): 6 columns + Total Votes Cast + Totals.

Candidates are emitted in the file convention used by the other March
2020 counties (Alger/Antrim/Benzie): 'Julian Castro' (no accent),
'Michael R. Bloomberg' and 'Marianne Williamson' (the header prints
them surname-first), 'Uncommitted', 'Write-In'.

Ballots Cast rows carry the party (Clinton-file style) and use the
contest Total Votes Cast, not the STATISTICS count: the STATISTICS
ballots-cast counts exceed the contest totals in 25 of 53 rows (DEM 11
rows, countywide 17,507 vs 17,492; REP 14 rows, 8,625 vs 8,598) --
plausibly overvotes/blank ballots that the contest report does not
break out.  The contest totals are what the candidate votes sum to, so
they are emitted; the deltas are printed for the record.

Verification: every precinct's candidate sum == its printed Total Votes
Cast; every candidate's precinct sum == the printed Totals row; the
DEM/REP countywide sums == 17,492 / 8,598; AVCB Registered counts ==
the sum of their base precincts.
"""

import os
import re
import sys

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Grand Traverse MI Primary Election by '
       'Precinct.pdf')
OUT = '2020/counties/20200310__mi__primary__president__grand_traverse__precinct.csv'
COUNTY = 'Grand Traverse'

DEM_MAIN = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
            'Cory Booker', 'Pete Buttigieg', 'Julian Castro',
            'John Delaney', 'Tulsi Gabbard', 'Amy Klobuchar',
            'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
            'Elizabeth Warren', 'Marianne Williamson']
DEM_CONT = ['Andrew Yang', 'Uncommitted', 'Write-In']
DEM_ALL = DEM_MAIN + DEM_CONT
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted', 'Write-In']

NUM = r'[\d,]+'
STATS_RE = re.compile(rf'^(.*\S)\s+({NUM})\s+({NUM})\s+({NUM})\s+([\d.]+)%$')


def ints(s):
    return [int(x.replace(',', '')) for x in s.split()]


def norm_name(name):
    # 'Fife Lake AVCB 0' prints only on the contest pages; the
    # STATISTICS table calls it 'Fife Lake AVCB'
    name = re.sub(r'^(.*AVCB) 0$', r'\1', name)
    return name


def main():
    pdf = pdfplumber.open(SRC)
    stats = {}          # name -> (registered, dem_bc, rep_bc)
    stats_order = []
    section = None      # 'stats' | 'dem' | 'rep'
    dem = {}            # name -> {cand: votes}
    dem_total = {}      # name -> printed Total Votes Cast
    dem_totals_row = None
    dem_cont_totals_row = None
    rep = {}
    rep_total = {}
    rep_totals_row = None
    problems = []

    for page in pdf.pages:
        text = page.extract_text() or ''

        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            if (line.startswith('Presidential Primary')
                    or line == 'Election by Precinct'
                    or line.startswith('March 10, 2020')
                    or line.startswith('Custom Table Report')
                    or line.startswith('Report generated')):
                continue
            # section transitions are line-level: page 2 carries the
            # last STATISTICS rows AND the DEM contest's first rows
            if line == 'STATISTICS':
                section = 'stats'
                continue
            if 'DEM President of the United States' in line:
                section = 'dem'
                continue
            if 'REP President of the United States' in line:
                section = 'rep'
                continue
            if 'President of the United States' in line:
                continue

            if section == 'stats':
                if line in ('Voters', '- Total', 'Registered', 'PARTY',
                            'DEMOCRATIC Ballots', 'Cast', '-',
                            'REPUBLICAN Ballots', 'Total', 'Voter',
                            'Turnout'):
                    continue
                m = STATS_RE.match(line)
                if not m:
                    problems.append(f'stats row not matched: {line!r}')
                    continue
                name = norm_name(m.group(1))
                if name in stats:
                    problems.append(f'duplicate stats row: {name!r}')
                    continue
                stats[name] = (int(m.group(2).replace(',', '')),
                               int(m.group(3).replace(',', '')),
                               int(m.group(4).replace(',', '')))
                stats_order.append(name)
                continue

            # contest sections: precinct names can end in digits
            # ('Precinct 1', 'AVCB2'), so the value run is taken from
            # the RIGHT at the section's known width, not greedily
            m = re.match(rf'^Totals ((?:{NUM} )+{NUM})$', line)
            if m:
                vals = ints(m.group(1))
                if section == 'dem':
                    if len(vals) == len(DEM_MAIN):
                        dem_totals_row = vals
                    else:
                        dem_cont_totals_row = vals
                else:
                    rep_totals_row = vals
                continue
            if line in ('VOTE FOR 1', 'Totals', 'Total Votes Cast') \
                    or re.match(r'^\d+ of \d+ Precincts Reporting$', line):
                continue

            toks = line.split()
            # maximal trailing run of numeric tokens: precinct names
            # end with at most one numeric token ('Precinct 1', 'Blair
            # AVCB 1'), so the run is the values plus 0-or-1
            i = len(toks)
            while i > 0 and re.fullmatch(NUM, toks[i - 1]):
                i -= 1
            run = len(toks) - i
            if section == 'dem':
                if run >= len(DEM_MAIN):
                    width = len(DEM_MAIN)
                elif run >= len(DEM_CONT) + 1:
                    width = len(DEM_CONT) + 1
                else:
                    # wrapped candidate-header fragment
                    continue
                vals = ints(' '.join(toks[-width:]))
                name = norm_name(' '.join(toks[:-width]))
            else:
                if run < len(REP) + 1:
                    continue
                width = len(REP) + 1
                vals = ints(' '.join(toks[-width:]))
                name = norm_name(' '.join(toks[:-width]))

            if section == 'dem':
                if len(vals) == len(DEM_MAIN):
                    for cand, v in zip(DEM_MAIN, vals):
                        dem.setdefault(name, {})[cand] = v
                else:
                    for cand, v in zip(DEM_CONT, vals[:3]):
                        dem.setdefault(name, {})[cand] = v
                    dem_total[name] = vals[3]
            else:
                for cand, v in zip(REP, vals[:6]):
                    rep.setdefault(name, {})[cand] = v
                rep_total[name] = vals[6]

    # ---- structure checks ---------------------------------------------
    n_prec = 36      # '36 of 36 Precincts Reporting'
    n_avcb = 17
    if len(stats) != n_prec + n_avcb:
        problems.append(f'stats rows {len(stats)} != {n_prec + n_avcb}')
    if set(dem) != set(stats):
        problems.append(f'dem precincts {len(dem)} != stats {len(stats)}: '
                        f'{sorted(set(stats) - set(dem))} / '
                        f'{sorted(set(dem) - set(stats))}')
    if set(rep) != set(stats):
        problems.append(f'rep precincts {len(rep)} != stats {len(stats)}')
    for name, cands in dem.items():
        missing = [c for c in DEM_ALL if c not in cands]
        if missing:
            problems.append(f'{name!r} dem missing {missing}')
    for name, cands in rep.items():
        missing = [c for c in REP if c not in cands]
        if missing:
            problems.append(f'{name!r} rep missing {missing}')

    # ---- per-precinct: candidates == Total Votes Cast ------------------
    for name, cands in sorted(dem.items()):
        got = sum(cands[c] for c in DEM_ALL)
        want = dem_total.get(name)
        if got != want:
            problems.append(f'DEM {name!r}: candidates {got} '
                            f'!= total {want}')
    for name, cands in sorted(rep.items()):
        got = sum(cands[c] for c in REP)
        want = rep_total.get(name)
        if got != want:
            problems.append(f'REP {name!r}: candidates {got} '
                            f'!= total {want}')

    # ---- per-candidate: precinct sums == printed Totals ---------------
    if dem_totals_row is None or dem_cont_totals_row is None \
            or rep_totals_row is None:
        problems.append('missing a Totals row')
    else:
        printed = dict(zip(DEM_MAIN, dem_totals_row)) \
            if len(dem_totals_row) == len(DEM_MAIN) else {}
        if len(dem_totals_row) == len(DEM_MAIN) + len(DEM_CONT):
            # a single Totals row spanning both column groups
            printed = dict(zip(DEM_ALL, dem_totals_row))
            dem_cont_totals_row = []
        for cand in DEM_MAIN:
            got = sum(dem[p].get(cand, 0) for p in dem)
            if printed and got != printed[cand]:
                problems.append(f'DEM {cand}: precincts {got} '
                                f'!= totals row {printed[cand]}')
        printed_c = dict(zip(DEM_CONT, dem_cont_totals_row)) \
            if dem_cont_totals_row else {}
        for cand in DEM_CONT:
            got = sum(dem[p].get(cand, 0) for p in dem)
            if cand in printed_c and got != printed_c[cand]:
                problems.append(f'DEM {cand}: precincts {got} '
                                f'!= totals row {printed_c[cand]}')
        for cand, want in zip(REP, rep_totals_row):
            got = sum(rep[p].get(cand, 0) for p in rep)
            if got != want:
                problems.append(f'REP {cand}: precincts {got} '
                                f'!= totals row {want}')

    # ---- stats vs contest totals --------------------------------------
    deltas = []
    rep_deltas = []
    dem_sum = rep_sum = stats_dem_sum = stats_rep_sum = 0
    for name, (rv, dbc, rbc) in stats.items():
        dem_sum += dem_total.get(name, 0)
        rep_sum += rep_total.get(name, 0)
        stats_dem_sum += dbc
        stats_rep_sum += rbc
        if name in dem_total and dbc != dem_total[name]:
            deltas.append((name, dbc, dem_total[name]))
        if name in rep_total and rbc != rep_total[name]:
            rep_deltas.append((name, rbc, rep_total[name]))
    print(f'DEM contest total {dem_sum}; stats sum {stats_dem_sum}; '
          f'{len(deltas)} rows differ: {deltas}')
    print(f'REP contest total {rep_sum}; stats sum {stats_rep_sum}; '
          f'{len(rep_deltas)} rows differ: {rep_deltas}')
    if dem_sum != 17492 or rep_sum != 8598:
        problems.append(f'countywide contest totals {dem_sum}/{rep_sum} '
                        f'!= printed 17492/8598')

    # ---- AVCB Registered Voters == sum of base precincts ---------------
    # an AVCB processes the home precincts' absentee ballots, so its
    # Registered count is the sum of theirs; the mapping below is
    # validated by the check itself
    AVCB_BASES = {
        'Acme Township AVCB': ['Acme Township, Precinct 1',
                               'Acme Township, Precinct 2'],
        'Blair AVCB 1': ['Blair Township, Precinct 1',
                         'Blair Township, Precinct 2'],
        'Blair AVCB 2': ['Blair Township, Precinct 3',
                         'Blair Township, Precinct 4'],
        'East Bay AVCB 1': ['East Bay Charter Township, Precinct 1'],
        'East Bay AVCB 2': ['East Bay Charter Township, Precinct 2'],
        'East Bay AVCB 3': ['East Bay Charter Township, Precinct 3'],
        'East Bay AVCB 4': ['East Bay Charter Township, Precinct 4'],
        'Fife Lake AVCB': ['Fife Lake Township'],
        'Garfield AVCB': ['Garfield Charter Township, Precinct 1',
                          'Garfield Charter Township, Precinct 2',
                          'Garfield Charter Township, Precinct 3',
                          'Garfield Charter Township, Precinct 4',
                          'Garfield Charter Township, Precinct 5',
                          'Garfield Charter Township, Precinct 6'],
        'Green Lake AVCB': ['Green Lake Township, Precinct 1',
                            'Green Lake Township, Precinct 2'],
        'Long Lake AVCB 1': ['Long Lake Township, Precinct 1',
                             'Long Lake Township, Precinct 2'],
        'Long Lake AVCB2': ['Long Lake Township, Precinct 3'],
        'Penninsula AVCB': ['Peninsula Township, Precinct 1',
                            'Peninsula Township, Precinct 2'],
        'Whitewater AVCB': ['Whitewater Township, Precinct 1'],
        'Traverse City AVCB 1': ['City of Traverse City, Precinct 1',
                                 'City of Traverse City, Precinct 3'],
        'Traverse City AVCB 2': ['City of Traverse City, Precinct 7',
                                 'City of Traverse City, Precinct 8'],
        'Traverse City AVCB 3': ['City of Traverse City, Precinct 9',
                                 'City of Traverse City, Precinct 10'],
    }
    # note: the AVCB grouping above is a hypothesis validated below; the
    # ones that fail are reported as informational (absentee counts
    # need not track their home precincts), only RV must sum
    for avcb, bases in AVCB_BASES.items():
        if avcb not in stats:
            problems.append(f'AVCB {avcb!r} not in stats')
            continue
        rv = stats[avcb][0]
        got = sum(stats[b][0] for b in bases if b in stats)
        if got != rv:
            problems.append(f'AVCB {avcb!r}: RV {rv} != base sum {got}')
        # DEM+REP ballots cast: AVCB BC should equal base BC sums too
        # (the AVCB processes the base precincts' absentee ballots)
        for j, label in ((1, 'DEM'), (2, 'REP')):
            bc = stats[avcb][j]
            gotb = sum(stats[b][j] for b in bases if b in stats)
            if gotb != bc:
                print(f'NOTE AVCB {avcb!r} {label} BC {bc} != '
                      f'base sum {gotb}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)

    # ---- emission ------------------------------------------------------
    rows = []
    for name in stats_order:
        rows.append([COUNTY, name, 'Registered Voters', '', '', '',
                     stats[name][0]])
    for cand in DEM_ALL:
        for name in stats_order:
            rows.append([COUNTY, name, 'President', '', 'DEM', cand,
                         dem[name][cand]])
    for name in stats_order:
        rows.append([COUNTY, name, 'Ballots Cast', '', 'DEM', '',
                     dem_total[name]])
    for cand in REP:
        for name in stats_order:
            rows.append([COUNTY, name, 'President', '', 'REP', cand,
                         rep[name][cand]])
    for name in stats_order:
        rows.append([COUNTY, name, 'Ballots Cast', '', 'REP', '',
                     rep_total[name]])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    import csv
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(stats)} precincts')


if __name__ == '__main__':
    main()