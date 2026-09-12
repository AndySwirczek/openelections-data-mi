"""Parse Roscommon County's March 10, 2020 presidential primary from the
Clarity JSON export in openelections-sources-mi/2020/presidential_primary/
"Roscommon County Mar 2020 Pres Primary Clarity JSON":

- summary.json (gzip): county-level results; each contest record carries
  its title (C), choice list (CH) and countywide votes (V).
- details.json: per contest (K) the precinct list (P) and, per precinct,
  one vote per choice (V[i][j]).  No vote-method split (Total Votes only),
  no Registered Voters / turnout anywhere in the export.

Contest K map: 0006 BALLOTS CAST - DEMOCRATIC PARTY, 0007 REPUBLICAN,
0008 NONPARTISAN, 0009 BLANK, 0010 DEM President, 0011 REP President,
0012/0013 C.O.O.R. ISD proposals (51 precincts across four counties --
keep only Roscommon's), 0014 Roscommon Area Public Schools millage
(8 precincts, two of them in other counties).

Emission follows the committed Bay March file's Ballots Cast convention:
a partyless total Ballots Cast row (= DEM + REP + NPA + Blank), per-party
Ballots Cast rows (DEM / REP / NPA) and a Ballots Cast Blank row, plus the
DEM / REP presidential candidates and the Yes / No proposal rows.  The
export's 'Write-in' choice sits inside the contest's choice list but, per
the March 2020 convention, write-ins are not emitted.

Checks: precinct sets consistent across contests, per-choice precinct
sums equal the summary county votes for the countywide contests, and
candidates + write-in <= ballots cast per precinct per party.
"""
import csv
import gzip
import json
import os

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/presidential_'
       'primary/Roscommon County Mar 2020 Pres Primary Clarity JSON')
OUT = '2020/counties/20200310__mi__primary__president__roscommon__precinct.csv'
COUNTY = 'Roscommon'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

K_DEM_BC, K_REP_BC, K_NP_BC, K_BLANK_BC = '0006', '0007', '0008', '0009'
K_DEM, K_REP = '0010', '0011'
PROPOSALS = ['0012', '0013', '0014']


def norm_label(p):
    """'Au Sable Township, Prec 1' -> 'Au Sable Township, Precinct 1'."""
    if ', Prec ' in p:
        return p.replace(', Prec ', ', Precinct ')
    return p


def main():
    problems = []
    details = json.load(open(os.path.join(SRC, 'details.json')))
    summary = json.load(gzip.open(os.path.join(SRC, 'summary.json')))
    titles = {c['K']: c['C'] for c in summary}
    choices = {c['K']: c['CH'] for c in summary}
    county_votes = {c['K']: c['V'] for c in summary}
    byk = {c['K']: c for c in details['Contests']}

    # the four ballots-cast contests define the canonical precinct set
    precincts = byk[K_DEM_BC]['P']
    for k in (K_REP_BC, K_NP_BC, K_BLANK_BC, K_DEM, K_REP):
        if byk[k]['P'] != precincts:
            problems.append(f'K{k}: precinct list differs from {K_DEM_BC}')

    votes = {}   # (K, precinct) -> [choice votes]
    for c in details['Contests']:
        if len(c['P']) != len(c['V']):
            problems.append(f"K{c['K']}: {len(c['P'])} precincts, "
                            f"{len(c['V'])} vote rows")
            continue
        for p, v in zip(c['P'], c['V']):
            votes[(c['K'], p)] = v
        # countywide contests: per-choice sums must equal the summary
        if c['K'] not in PROPOSALS and len(c['P']) == 14:
            want = county_votes.get(c['K'])
            got = [sum(col) for col in zip(*c['V'])]
            if want is not None and got != want:
                problems.append(f"K{c['K']}: precinct sums {got} != county "
                                f"{want}")
        nch = len(choices[c['K']])
        for p, v in zip(c['P'], c['V']):
            if len(v) != nch:
                problems.append(f"K{c['K']} {p}: {len(v)} choices, want "
                                f'{nch}')

    rows = []
    for p in precincts:
        label = norm_label(p)
        dem_v = votes[(K_DEM, p)]
        rep_v = votes[(K_REP, p)]
        # write-in is the last choice; verify candidate sets line up
        if choices[K_DEM][:-1] != DEM:
            problems.append(f'DEM choices {choices[K_DEM][:-1]} != expected')
        if choices[K_REP][:-1] != REP:
            problems.append(f'REP choices {choices[K_REP][:-1]} != expected')
        dem_bc = votes[(K_DEM_BC, p)][0]
        rep_bc = votes[(K_REP_BC, p)][0]
        np_bc = votes[(K_NP_BC, p)][0]
        blank_bc = votes[(K_BLANK_BC, p)][0]
        if sum(dem_v[:-1]) > dem_bc:
            problems.append(f'{label}: DEM candidates {sum(dem_v[:-1])} > '
                            f'BC {dem_bc}')
        if sum(rep_v[:-1]) > rep_bc:
            problems.append(f'{label}: REP candidates {sum(rep_v[:-1])} > '
                            f'BC {rep_bc}')

        def emit(office, party, candidate, v):
            rows.append([COUNTY, label, office, '', party, candidate, v])

        for name, v in zip(DEM, dem_v[:-1]):
            emit('President', 'DEM', name, v)
        emit('Ballots Cast', 'DEM', '', dem_bc)
        for name, v in zip(REP, rep_v[:-1]):
            emit('President', 'REP', name, v)
        emit('Ballots Cast', 'REP', '', rep_bc)
        emit('Ballots Cast', 'NPA', '', np_bc)
        emit('Ballots Cast Blank', '', '', blank_bc)
        emit('Ballots Cast', '', '', dem_bc + rep_bc + np_bc + blank_bc)
        for k in PROPOSALS:
            if (k, p) in votes:
                for name, v in zip(choices[k], votes[(k, p)]):
                    emit(titles[k], '', name, v)

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing {OUT}')
        return
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(precincts)} precincts')


if __name__ == '__main__':
    main()