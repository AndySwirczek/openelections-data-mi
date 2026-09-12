"""Parse Washtenaw County's March 10, 2020 presidential primary from the
150 per-precinct HTML reports in openelections-sources-mi/2020/
presidential_primary/"Washtenaw County Mar 2020 Pres Primary Precinct
Reports" (precinctreport<N>.html, N non-contiguous; every file is one
precinct: 'Official Election Results' header with the label in
<font class="h2">, a Registered Voters / Ballots Cast / Voter Turnout
table, then contest blocks each titled '<name> ( Vote For 1 )' with rows
candidate | IN-PRECINCT | ABSENTEE | TOTAL | PERCENT).

Each file carries the REP President block FIRST, then the DEM President
block, then any local proposals (Washtenaw Community College millage in
all 150 precincts; Plymouth-Canton bond, Northville sinking fund and
Ingham ISD proposals in a few).  Party is taken from the candidate
names; 'Rejected write-ins' / 'Unassigned write-ins' rows are all zero
countywide and are not emitted (March convention); 'Juli&aacute;n
Castro' is canon'd to the unaccented form.

Emission per precinct: Registered Voters row, partyless Ballots Cast
row, the DEM / REP presidential candidates' TOTAL votes, and Yes / No
proposal rows.

Checks: TOTAL == IN-PRECINCT + ABSENTEE for every candidate, each
printed PERCENT equals the half-even-rounded vote / contest sum, the DEM
contest total + REP contest total <= Ballots Cast (the header Ballots
Cast exceeds the contest sums in 144 of 150 precincts -- blank
presidential ballots with no row of their own), proposal sums <= Ballots
Cast, and the printed turnout percentage equals the half-even-rounded
Ballots Cast / RV.
"""
import csv
import glob
import html as htmllib
import os
import re
from decimal import Decimal, ROUND_HALF_EVEN

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Washtenaw County Mar 2020 Pres Primary '
       'Precinct Reports')
OUT = '2020/counties/20200310__mi__primary__president__washtenaw__precinct.csv'
COUNTY = 'Washtenaw'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
TITLE_RE = re.compile(r'class="h2">([^<]+)</font>')
CONTEST_RE = re.compile(r'<b>([^<]+?)\s*\(\s*Vote For 1\s*\)</b>', re.S)


def ival(s):
    s = s.replace(',', '').strip()
    return int(s) if re.fullmatch(r'[\d]+', s) else None


def pct_round(v, denom):
    if not denom:
        return None
    q = (Decimal(100 * v) / Decimal(denom)).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_EVEN)
    return float(q)


def parse_file(path):
    """-> dict(label, rv, bc, turnout, contests=[{'title', 'rows'}])."""
    html = open(path, encoding='utf-8', errors='replace').read()
    html = html.replace('&nbsp;', ' ').replace('&aacute;', 'á')
    label = TITLE_RE.search(html).group(1).strip()
    m = re.search(r'Registered Voters:.*?([\d,]+).*?Ballots Cast:.*?'
                  r'([\d,]+).*?Voter Turnout:.*?([\d.]+)%', html, re.S)
    rv, bc, turnout = ival(m.group(1)), ival(m.group(2)), float(m.group(3))
    contests = []
    cur = None
    # candidate rows are malformed (they end with a stray '<tr>' instead
    # of '</tr>'), so split on '<tr' boundaries rather than matching
    # '</tr>'
    for seg in re.split(r'<tr[^>]*>', html)[1:]:
        seg = seg.split('<tr')[0]
        # the title rows split '( <font>Vote For 1</font> )' across
        # tags, so classify on the tag-stripped row text
        text = ' '.join(re.sub(r'<[^>]+>', ' ', seg).split())
        cm = re.search(r'(.+?) \( Vote For 1 \)', text)
        if cm:
            cur = {'title': cm.group(1).strip(), 'rows': [], 'pcts': {}}
            contests.append(cur)
            continue
        cells = [' '.join(re.sub(r'<[^>]+>', ' ', c).split())
                 for c in CELL_RE.findall(seg)]
        if len(cells) < 4 or not cells[0] or cur is None:
            continue
        name = ' '.join(cells[0].split())
        name = {'Julián Castro': 'Julian Castro'}.get(name, name)
        ip, av, tot = ival(cells[1]), ival(cells[2]), ival(cells[3])
        if ip is not None:
            pct = None
            pm = re.fullmatch(r'([\d.]+)%', cells[4]) if len(cells) > 4 else None
            if pm:
                pct = float(pm.group(1))
            cur['pcts'][name] = pct
            cur['rows'].append((name, ip, av, tot))
    return {'label': label, 'rv': rv, 'bc': bc, 'turnout': turnout,
            'contests': contests}


def main():
    problems = []
    files = sorted(glob.glob(f'{SRC}/precinctreport*.html'))
    rows = []
    seen = set()
    for f in files:
        rec = parse_file(f)
        label = rec['label']
        if label in seen:
            problems.append(f'duplicate label {label!r} ({f})')
        seen.add(label)
        if rec['turnout'] is not None and rec['rv']:
            want = pct_round(rec['bc'], rec['rv'])
            if abs(want - rec['turnout']) > 0.005:
                problems.append(f'{label}: turnout {want}% != printed '
                                f'{rec["turnout"]}%')
        dem = rep = None
        for c in rec['contests']:
            names = [r[0] for r in c['rows']]
            if 'Michael Bennet' in names:
                dem = c
            elif 'Mark Sanford' in names:
                rep = c
        if dem is None or rep is None:
            problems.append(f'{label}: president blocks not found')
            continue
        contest_total = {}
        for party, cont, cands in (('DEM', dem, DEM), ('REP', rep, REP)):
            data = {r[0]: r for r in cont['rows'] if 'write-ins' not in r[0]}
            if set(data) != set(cands):
                problems.append(f'{label} {party}: candidates {sorted(data)}')
                continue
            tot = 0
            for name in cands:
                _, ip, av, total = data[name]
                if ip + av != total:
                    problems.append(f'{label} {party} {name}: {ip}+{av} '
                                    f'!= {total}')
                tot += total
            contest_total[party] = tot
            # the printed PERCENT denominator is the contest total
            # INCLUDING the write-in rows (half-even); it independently
            # validates every row's total
            wi = sum(r[3] for r in cont['rows'] if 'write-ins' in r[0])
            for name in cands:
                got = float(cont['pcts'][name])
                want = pct_round(data[name][3], tot + wi)
                if want is not None and abs(got - want) > 0.005:
                    problems.append(f'{label} {party} {name}: pct {want}% '
                                    f'!= printed {got}%')
        # the header Ballots Cast exceeds DEM+REP contest totals in 144
        # of 150 precincts (blank/unaccounted presidential ballots with
        # no row of their own in the source) -- only bound, don't equate
        if contest_total['DEM'] + contest_total['REP'] > rec['bc']:
            problems.append(f'{label}: contest totals '
                            f'{contest_total["DEM"]}+{contest_total["REP"]} '
                            f'> BC {rec["bc"]}')
        for c in rec['contests']:
            if c is dem or c is rep:
                continue
            vals = {r[0]: r[3] for r in c['rows']}
            if set(vals) != {'Yes', 'No'}:
                problems.append(f'{label} {c["title"]}: rows {sorted(vals)}')
                continue
            if vals['Yes'] + vals['No'] > rec['bc']:
                problems.append(f'{label} {c["title"]}: Yes+No '
                                f'{vals["Yes"] + vals["No"]} > BC {rec["bc"]}')

        def emit(office, party, cand, v):
            rows.append([COUNTY, label, office, '', party, cand, v])

        emit('Registered Voters', '', '', rec['rv'])
        emit('Ballots Cast', '', '', rec['bc'])
        for name in DEM:
            emit('President', 'DEM', name,
                 {r[0]: r[3] for r in dem['rows']
                  if 'write-ins' not in r[0]}[name])
        for name in REP:
            emit('President', 'REP', name,
                 {r[0]: r[3] for r in rep['rows']
                  if 'write-ins' not in r[0]}[name])
        for c in rec['contests']:
            if c is dem or c is rep:
                continue
            title = ' '.join(c['title'].replace('\n', ' ').split())
            for r in c['rows']:
                emit(title, '', r[0], r[3])
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(seen)} precincts')


if __name__ == '__main__':
    main()