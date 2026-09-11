"""Parse Muskegon County's Aug 2020 primary 'Precinct Results / Official
results' report (openelections-sources-mi/2020/primary/'Muskegon MI Precinct
Results-8-12-2020 07-27-33 PM_202008131128415645.pdf', 365pp) into
2020/counties/20200804__mi__primary__muskegon__precinct.csv.

Precinct-major layout (Ionia's format with a real method split): each
precinct's section repeats its contest list with the header 'Choice Party
Absentee Precinct Total' — three (value, pct) pairs, so 'Total' = Absentee +
Precinct is checked per row and the breakdown goes into the election_day /
av_counting_boards columns (Ingham/Wexford convention).  County offices
print bare titles (Clerk/Treasurer -> 'County Clerk'/'County Treasurer' —
township offices are always jurisdiction-prefixed here, unlike Ionia);
delegates print 'Precinct Delegate, <jur>, Precinct N'; proposals are
'- Nonpartisan Party' with Yes/No rows.  Zero-vote contests print a single
nameless all-zero row (skipped).  Write-in names keep their '(W)' suffix.
No turnout/Total Votes lines exist in the source, so no Ballots Cast
pseudo-rows are emitted (Ionia/Saginaw precedent).
"""
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import map_office, normalize_precinct

TXT = '/tmp/muskegon.txt'
COUNTY = 'Muskegon'

FURNITURE = re.compile(
    r'^(?:Precinct Results\b.*Official Results$'
    r'|.*Muskegon County, Michigan$'
    r'|Registered Voters$'
    r'|[\d,]+ of [\d,]+ = [\d.]+%$'
    r'|Election Night Results\b'
    r'|Primary Election$'
    r'|Precincts Reporting$'
    r'|Run Time\b|Run Date\b'
    r'|8/4/2020$'
    r'|Page \d+$)')

# 'Blue Lake Township   591 of 1,975 registered voters = 29.92%' (with or
# without ', Precinct N' — single-precinct jurisdictions print bare names)
PRECINCT_RE = re.compile(
    r'^(.+?),?(?: Precinct \d+)?\s{2,}[\d,]+ of [\d,]+ registered voters')

# proposals print no 'Vote for' suffix; long titles wrap and are joined
# from the pending lines when the Choice/Party header arrives
TITLE_RE = re.compile(
    r'^(.*?) - (Democratic|Republican|Nonpartisan) Party'
    r'(?: - Vote for not more than \d+)?$')

HEADER_RE = re.compile(
    r'^Choice\s+Party\s+Absentee\s+Precinct\s+Total$')

# name + three (value, pct) pairs: Absentee, Precinct, Total; zero-vote
# contests print a nameless row (leading digit, no label)
CAND_RE = re.compile(
    r'^(?:([A-Za-z].*?)[ \t]{2,})?([\d,]+)\s+[\d.]+%\s+([\d,]+)\s+'
    r'[\d.]+%\s+([\d,]+)\s+[\d.]+%\s*$')

# 'Cast Votes:' lines carry the same three pairs; 'Overvotes:' three bare
SUMMARY_RE = re.compile(r'^(Cast Votes|Overvotes|Undervotes|Invalid votes'
                        r'|Rejected write-in votes):?')

PARTY_MAP = {'Democratic': 'DEM', 'Republican': 'REP', 'Nonpartisan': ''}


def split_title(title):
    party = ''
    for pat, code in ((' - Democratic Party', 'DEM'),
                      (' - Republican Party', 'REP'),
                      (' - Nonpartisan Party', '')):
        if pat in title:
            party = code
            title = title.split(pat)[0]
            break
    office = title.strip().replace('Egeleston', 'Egelston')
    m = re.match(r'^Precinct Delegate, (.+), Precinct (\d+)$', office)
    if m:
        return f'{m.group(1)} Precinct {m.group(2)} Delegate to County ' \
               f'Convention', '', party
    if office in ('Clerk', 'Treasurer'):
        # bare titles are the county slate; township offices are always
        # jurisdiction-prefixed in this source
        office = f'County {office}'
    jm = re.match(r'^Judge of District Court (\d+)(?:st|nd|rd|th)? District',
                  office)
    if jm:
        # CENR naming (office 'District Court Judge', district '60')
        return 'District Court Judge', jm.group(1), party
    dm = re.search(r'(\d+)(?:st|nd|rd|th) District$', office)
    division = dm.group(0) if dm else ''
    base = re.sub(r'\s*(?:\d+(?:st|nd|rd|th) )?District$', '', office).strip()
    return (*map_office(base, division, COUNTY), party)


def parse():
    rows = []
    problems = []
    precinct = None
    contest = None   # (office, district, party)
    pending = []
    table_sum = 0
    cast_votes = None
    with open(TXT) as fh:
        lines = fh.read().splitlines()
    for raw in lines:
        s = raw.strip()
        if not s or FURNITURE.match(s):
            continue
        pm = PRECINCT_RE.match(s)
        if pm:
            # precinct headers repeat at the top of every page; only reset
            # when the precinct actually changes
            new = normalize_precinct(re.sub(
                r'\s+[\d,]+ of [\d,]+ registered.*$', '', s))
            if new != precinct:
                precinct = new
            contest = None
            continue
        if HEADER_RE.match(s):
            if pending:
                joined = ' '.join(pending)
                jm = TITLE_RE.match(joined)
                if jm:
                    office, district, party = split_title(joined)
                    contest = (office, district, party)
                else:
                    problems.append(f'unparsed title: {pending!r} '
                                    f'(precinct={precinct})')
                    contest = None
                pending = []
            elif not contest:
                problems.append(f'header without title near {s!r} '
                                f'(precinct={precinct})')
            table_sum = 0
            cast_votes = None
            continue
        m = TITLE_RE.match(s)
        if m:
            pending = []
            office, district, party = split_title(s)
            contest = (office, district, party)
            table_sum = 0
            cast_votes = None
            continue
        sm = SUMMARY_RE.match(s)
        if sm:
            if sm.group(1) == 'Cast Votes' and contest is not None:
                # values are thousands-comma formatted on these lines
                nums = [int(x.replace(',', ''))
                        for x in re.findall(r'([\d,]+)\s+[\d.]+%', s)]
                cast_votes = nums[2] if len(nums) == 3 else None
                if cast_votes is not None and cast_votes != table_sum:
                    problems.append(
                        f'Cast Votes {cast_votes} != candidate sum '
                        f'{table_sum} for {contest} in {precinct}')
            continue
        cm = CAND_RE.match(s)
        if cm:
            name = (cm.group(1) or '').strip()
            abs_, ed, tot = (int(cm.group(i).replace(',', ''))
                             for i in (2, 3, 4))
            if tot != abs_ + ed:
                problems.append(f'Total {tot} != Absentee {abs_} + Precinct '
                                f'{ed} for {contest} in {precinct}: {s!r}')
            if contest is None:
                problems.append(f'candidate row without contest: {s!r}')
                continue
            if not name:
                # zero-vote contests print one nameless all-zero row
                if abs_ or ed or tot:
                    problems.append(f'nameless non-zero row for {contest} '
                                    f'in {precinct}: {s!r}')
                continue
            table_sum += tot
            office, district, party = contest
            rows.append({
                'county': COUNTY, 'precinct': precinct,
                'office': office, 'district': district, 'party': party,
                'candidate': name, 'votes': tot,
                'election_day': ed, 'av_counting_boards': abs_,
            })
            continue
        # unmatched lines are wrapped title fragments, joined at the header
        pending.append(s)
    return rows, problems


def write_csv(rows):
    os.makedirs('2020/counties', exist_ok=True)
    out = '2020/counties/20200804__mi__primary__muskegon__precinct.csv'
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes', 'election_day',
                    'av_counting_boards'])
        for row in sorted(rows, key=lambda r: (r['precinct'], r['office'],
                                               r['district'], r['party'],
                                               r['candidate'])):
            w.writerow([row['county'], row['precinct'], row['office'],
                        row['district'], row['party'], row['candidate'],
                        row['votes'], row['election_day'],
                        row['av_counting_boards']])
    print(f'{COUNTY}: wrote {len(rows)} rows to {out}')


if __name__ == '__main__':
    rows, problems = parse()
    for p in problems:
        print('PROBLEM:', p)
    write_csv(rows)