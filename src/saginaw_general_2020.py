"""Parse the Saginaw County Nov 2020 general "Canvass Report" HTML into a
per-county precinct CSV, then verify against 2020/20201103__mi__general__county.csv.

Source (openelections-sources-mi/2020/general):
'Saginaw MI Canvass Report.html' — a "PREC REPORT-GROUP DETAIL" canvass
report, precinct-major: for each precinct a turnout table (REGISTERED
VOTERS - TOTAL / BALLOTS CAST - TOTAL / VOTER TURNOUT - TOTAL) followed by
one title table (<strong>Contest</strong> | Precinct: <strong>label</strong>
| Party: <strong>NA</strong>) + one results table per contest. Results rows
are <candidate, Total, %, Election Day, AV Counting Boards>; per-row
Total == Election Day + AV Counting Boards everywhere (AV Counting Boards
= the county's absentee counting boards, emitted as the `absentee`
breakdown column).

Two structural rules learned from reconciling against the county file:
- **School/village split variants**: a precinct whose ballot is split by
  school district (or village) prints one entry per ballot style, labeled
  '<precinct> BR' / 'CL' / 'FM' / 'VL' ... Each variant carries the FULL
  contest set with the style's SUBSET of the precinct's votes (e.g. Birch
  Run Township P1 president 1491 (BR) + 13 (CL) = 1504 (base)); the base
  label carries every contest restricted per contest (a school contest
  under the base label equals the matching variant's value). Only the base
  labels (the 85 that have a turnout table) are emitted — variant rows
  would double-count.
- **Duplicated precinct blocks**: 'Brady Township, Precinct 1' and
  'Tittabawassee Township, Precinct 1' each print their whole block twice
  with DIFFERENT values (alternative prints, like Cheboygan's duplicated
  Sheriff table). Keeping the LATER copy reconciles all 39 county-level
  (office, district, candidate) totals exactly; keeping the earlier copy
  does not.

Source typos fixed in office names: 'Circit' (Circuit), 'Ovid-Elsi'
(Ovid-Elsie), 'Improvemnet' (Improvement), 'Merill' (Merrill),
'Opperations' (Operations), a lowercase 'trustee' in 'James Twp. trustee'.

Usage:
    .venv/bin/python src/saginaw_general_2020.py
"""
import csv
import html
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/general/'
       'Saginaw MI Canvass Report.html')
COUNTY = 'Saginaw'
OUT = '2020/counties/20201103__mi__general__saginaw__precinct.csv'
COUNTY_CSV = '2020/20201103__mi__general__county.csv'

PARTY_CODE = {
    'Democratic Party': 'DEM', 'Republican Party': 'REP',
    'Libertarian Party': 'LIB', 'U.S. Taxpayers Party': 'UST',
    'Working Class Party': 'WCP', 'Green Party': 'GRN',
    'Natural Law Party': 'NLP',
}
# party of each candidate the county file tracks (from the certified lists
# confirmed by the neighboring counties' rows in the 2020 general county file)
CANDIDATE_PARTY = {
    ('President', 'Joseph R. Biden'): 'DEM',
    ('President', 'Donald J. Trump'): 'REP',
    ('President', 'Jo Jorgensen'): 'LIB',
    ('President', 'Don Blankenship'): 'UST',
    ('President', 'Howie Hawkins'): 'GRN',
    ('President', 'Rocky De La Fuente'): 'NLP',
    ('U.S. Senate', 'Gary Peters'): 'DEM',
    ('U.S. Senate', 'John James'): 'REP',
    ('U.S. Senate', 'Valerie L. Willis'): 'UST',
    ('U.S. Senate', 'Marcia Squier'): 'GRN',
    ('U.S. Senate', 'Doug Dern'): 'NLP',
    ('U.S. House|4', 'Jerry Hilliard'): 'DEM',
    ('U.S. House|4', 'John Moolenaar'): 'REP',
    ('U.S. House|4', 'David Canny'): 'LIB',
    ('U.S. House|4', 'Amy Slepr'): 'GRN',
    ('U.S. House|5', 'Daniel T. Kildee'): 'DEM',
    ('U.S. House|5', 'Tim Kelly'): 'REP',
    ('U.S. House|5', 'James Harris'): 'LIB',
    ('U.S. House|5', 'Kathy Goodwin'): 'WCP',
    ('State House|85', 'Andrea Kelly Garrison'): 'DEM',
    ('State House|85', 'Ben Frederick'): 'REP',
    ('State House|94', 'Demond L. Tibbs'): 'DEM',
    ('State House|94', 'Rodney Wakeman'): 'REP',
    ('State House|95', "Amos O'Neal"): 'DEM',
    ('State House|95', 'Charlotte DeMaet'): 'REP',
}

TITLE_FIXES = [
    ('Circit', 'Circuit'), ('Ovid-Elsi ', 'Ovid-Elsie '),
    ('Improvemnet', 'Improvement'), ('Merill', 'Merrill'),
    ('Opperations', 'Operations'),
]


def cells(row_html):
    return [re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', c))).strip()
            for c in re.findall(r'<td[^>]*>(.*?)</td>', row_html, flags=re.S)]


def ordinal(n):
    n = int(n)
    if n % 100 in (11, 12, 13):
        return f'{n}th'
    return f'{n}{ {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th") }'


def fix_title(name):
    for old, new in TITLE_FIXES:
        name = name.replace(old, new)
    # expand the source's 'Twp.' office titles: 'Albee Twp. Clerk' ->
    # 'Albee Township Clerk'; 'James Twp. trustee' -> 'James Township Trustee'
    m = re.match(r'^(.*?\S) Twp\. (\S)(.*)$', name)
    if m:
        name = f'{m.group(1)} Township {m.group(2).upper()}{m.group(3)}'
    return name


def map_office(name):
    """(office, district) for a contest title."""
    if name == 'Straight Party Ticket':
        return 'Straight Party', ''
    if name == 'President and Vice-President of the United States':
        return 'President', ''
    if name == 'United States Senator':
        return 'U.S. Senate', ''
    m = re.match(r'^Representative in Congress (\d+)th District$', name)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^State Representative (\d+)th District$', name)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'^County Commissioner District (\d+)$', name)
    if m:
        return f'County Commissioner {ordinal(m.group(1))} District', ''
    return name, ''


def parse(problems):
    src = open(SRC, encoding='utf-8', errors='replace').read()
    body = src[src.find('<body>'):]
    parts = re.split(r'(<table[^>]*>.*?</table>)', body, flags=re.S)

    contests = []          # {'name', 'prec', 'rows': [5 cells]}
    turnout = {}           # label -> (registered, ballots)
    contest = None
    for p in parts:
        if not p.startswith('<table'):
            continue
        rows = re.findall(r'<tr>(.*?)</tr>', p, flags=re.S)
        if re.search(r'REGISTERED VOTERS', p):
            strong = re.findall(r'<strong>([^<]*)</strong>', p)
            label = re.sub(r'\s+', ' ', strong[0]).strip()
            cs = [cells(r) for r in rows[1:]]
            rv = cs[0][1] if cs and cs[0][1] else None
            bc = cs[1][1] if len(cs) > 1 and cs[1][1] else None
            turnout[label] = (rv, bc)
            contest = None
            continue
        if re.match(r'<table class="title"', p):
            strong = re.findall(r'<strong>([^<]*)</strong>', p)
            if len(strong) < 2:
                problems.append(f'title table with {len(strong)} strong '
                                f'cells: {strong}')
                contest = None
                continue
            contest = {'name': fix_title(re.sub(r'\s+', ' ', strong[0]).strip()),
                       'prec': re.sub(r'\s+', ' ', strong[1]).strip(),
                       'rows': []}
            contests.append(contest)
            continue
        if contest is None:
            problems.append(f'results table with no open contest: {p[:120]!r}')
            continue
        for r in rows[1:]:
            cs = cells(r)
            if len(cs) == 5 and cs[0]:
                contest['rows'].append(cs)
            elif any(cs):
                problems.append(f'odd row in {contest["name"]!r} / '
                                f'{contest["prec"]!r}: {cs}')

    # base precincts = the ones with a turnout table ('Unspecified' is an
    # empty trailing stub); split variants (school/village ballot styles)
    # have no turnout table and are not emitted
    base = {lab for lab in turnout if lab != 'Unspecified'}
    n_prec = len(base)

    # later occurrence of a duplicated (precinct, contest) block wins
    last = {}
    for c in contests:
        if c['prec'] in base:
            last[(c['prec'], c['name'])] = c
    dups = {k[0] for k, n in
            Counter((c['prec'], c['name']) for c in contests
                    if c['prec'] in base).items() if n > 1}
    if dups:
        print(f'NOTE: duplicated blocks kept from the later copy for '
              f'{sorted(dups)}')

    # every contest must also appear under the base labels
    var_only = {c['name'] for c in contests if c['prec'] not in turnout} - \
        {c['name'] for c in contests if c['prec'] in base}
    if var_only:
        problems.append(f'contests only under variant labels: {sorted(var_only)}')

    rows = []
    for (prec, cname), c in sorted(last.items()):
        office, district = map_office(c['name'])
        for r in c['rows']:
            cand, tot, _pct, ed, av = r
            tot, ed, av = int(tot), int(ed), int(av)
            if tot != ed + av:
                problems.append(f'{prec!r} / {cname!r} / {cand!r}: Total '
                                f'{tot} != Election Day {ed} + AV {av}')
            if not tot:
                continue
            if cname == 'Straight Party Ticket':
                rows.append([COUNTY, prec, 'Straight Party', '', cand,
                             PARTY_CODE.get(cand, ''), str(tot), str(ed),
                             str(av)])
                if cand not in PARTY_CODE:
                    problems.append(f'unknown straight party name {cand!r}')
                continue
            key = f'{office}|{district}' if office in (
                'U.S. House', 'State House') else office
            party = CANDIDATE_PARTY.get((key, cand), '')
            if cand == 'Write-in':
                cand = 'Write-Ins'
            rows.append([COUNTY, prec, office, district, cand, party,
                         str(tot), str(ed), str(av)])

    for lab in sorted(base):
        rv, bc = turnout[lab]
        if not rv or not bc:
            problems.append(f'{lab!r}: incomplete turnout table '
                            f'(rv={rv!r}, bc={bc!r})')
            continue
        rows.append([COUNTY, lab, 'Registered Voters', '', '', '', rv, '', ''])
        rows.append([COUNTY, lab, 'Ballots Cast', '', '', '', bc, '', ''])
    return rows


def verify(rows, problems):
    """Aggregate the emitted rows and compare against the county file."""
    want = {}
    for row in csv.DictReader(open(COUNTY_CSV)):
        if row['county'] == COUNTY:
            want[(row['office'], row['district'], row['candidate'])] = \
                int(row['votes'])

    # alias: county file joins presidential running mates with '/'
    def county_key(office, district, cand):
        if office == 'President':
            for k in want:
                if k[0] == 'President' and \
                        k[2].split('/')[0].strip() == cand:
                    return k
        if 'Write' in cand:
            for k in want:
                if (k[0], k[1]) == (office, district) and \
                        'write' in k[2].lower():
                    return k
        return (office, district, cand)

    got = defaultdict(int)
    for r in rows:
        if r[2] in ('Registered Voters', 'Ballots Cast'):
            continue
        got[county_key(r[2], r[3], r[4])] += int(r[6])
    for k, v in want.items():
        if got.get(k, 0) != v:
            problems.append(f'county mismatch {k}: file {v} vs parsed '
                            f'{got.get(k, 0)}')


def main():
    problems = []
    rows = parse(problems)
    verify(rows, problems)
    for p in problems:
        print('PROBLEM:', p)
    if problems:
        sys.exit(f'{COUNTY}: {len(problems)} problems')
    rows.sort(key=lambda r: (r[1], r[2], r[3], r[5], r[4]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['county', 'precinct', 'office', 'district', 'candidate',
                    'party', 'votes', 'election_day', 'absentee'])
        w.writerows(rows)
    precs = sorted({r[1] for r in rows})
    print(f'{OUT}: {len(rows)} rows, {len(precs)} precincts, '
          f'{len({(r[2], r[3]) for r in rows})} (office, district) pairs, '
          f'verified against {COUNTY_CSV}')


if __name__ == '__main__':
    main()