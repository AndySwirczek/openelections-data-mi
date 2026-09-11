"""Parse Ionia County's Aug 2020 primary 'Precinct Results / Official results'
report (openelections-sources-mi/2020/primary/'Ionia MI 08042020-Official-
Election-Results-Precinct-Report.pdf', 303pp) into
2020/counties/20200804__mi__primary__ionia__precinct.csv.

This is a precinct-major report (unlike the contest-major ES&S SOVCs): each
precinct's pages list its contests in turn, each with the header
'Choice Party Election Day Total'.  Contest titles carry the party
('- Democratic Party - Vote for not more than 1'; proposals say
'- Nonpartisan Party' and have Yes/No rows).  Michigan counts absentees by
precinct, so the 'Election Day' column already holds the full precinct count
('Total' duplicates it; verified against the CENR: the 31 Gary Peters rows sum
to 3148 and John James to 7828, both exact).  A candidate may carry a '(W)'
write-in marker; seven write-in rows print no numbers at all (0 votes, name
sometimes just '(W)').  Zero-vote contests print no candidate rows, only the
summary lines.  No turnout rows exist in the source, so no Ballots Cast
pseudo-rows are emitted (Saginaw precedent).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import map_office, normalize_precinct, write_csv

TXT = '/tmp/ionia.txt'
COUNTY = 'Ionia'

FURNITURE = re.compile(
    r'^(?:Precinct Results\b.*Official results$'
    r'|.*Ionia County, Michigan$'
    r'|Registered Voters$'
    r'|08042020 STATE PRIMARY ELECTION$'
    r'|Primary Election$'
    r'|Run Time\b|Run Date\b'
    r'|Precincts Reporting$'
    r'|\d+ of \d+ = [\d.]+ ?%$'
    r'|Page \d+ of \d+$)')

PRECINCT_RE = re.compile(
    r'^(?:City of .+|Township of .+), (?:Ward \d+, )?Precinct \d+$')

HEADER_RE = re.compile(r'^Choice\s+Party\s+Election Day\s+Total$')

CAND_RE = re.compile(
    r'^(.+?)\s{2,}(\d+)\s+[\d.]+%\s+(\d+)\s+[\d.]+%\s*$')

# write-in rows print no numbers when the count is 0 ('Hawley (W)', or a bare
# '(W)' when the name itself is unattributed)
WROW_RE = re.compile(r'^(.*?)\s*\(W\)$')

SUMMARY_RE = re.compile(
    r'^(?:Cast Votes|Undervotes|Overvotes|Invalid votes'
    r'|Rejected write-in votes):')

VOTE_FOR_RE = re.compile(r'\s*-\s*Vote for (?:no|not) more than \d+$')


def split_title(title):
    """(office, party, is_proposal) from a joined contest title."""
    party = ''
    for pat, code in ((' - Democratic Party', 'DEM'),
                      (' - Republican Party', 'REP'),
                      (' - Nonpartisan Party', '')):
        if pat in title:
            party = code
            title = title.split(pat)[0]
            break
    office = VOTE_FOR_RE.sub('', title).strip()
    return office, party, party == ''


def jurisdiction(precinct):
    """'Township of Berlin, Precinct 1' -> 'Berlin Township' (for local
    office names; the source prints township offices with bare titles)."""
    m = re.match(r'^(?:City|Township) of (.+?),? (?:Ward \d+, )?Precinct \d+$',
                 precinct)
    if not m:
        return precinct
    name = m.group(1)
    if 'Township' in precinct:
        return name if name.endswith('Township') else f'{name} Township'
    return f'City of {name}'


def parse():
    rows = []
    problems = []
    titles = {}
    precinct = None
    pending = []
    contest = None   # (office, district, party)
    table_sum = 0
    cast_votes = None
    with open(TXT) as fh:
        lines = fh.read().splitlines()
    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not s or FURNITURE.match(s):
            continue
        if PRECINCT_RE.match(s):
            # precinct headers repeat at the top of every page; only reset
            # the Clerk/Treasurer context when the precinct actually changes
            new_precinct = normalize_precinct(s)
            if new_precinct != precinct:
                last_office = None
                last_clerk_county = False
            precinct = new_precinct
            pending = []
            contest = None
            continue
        if HEADER_RE.match(s):
            if not pending:
                problems.append(f'no title before header near {s!r} '
                                f'(precinct={precinct})')
                contest = None
                continue
            title = ' '.join(pending)
            pending = []
            office, party, _ = split_title(title)
            if office == 'Delegate to County Convention':
                office = f'{precinct} Delegate to County Convention'
            elif office in ('Clerk', 'Treasurer'):
                # the county slate (Prosecuting Attorney / Sheriff / Clerk /
                # Treasurer / Register of Deeds) and the township slate
                # (Supervisor / Clerk / Treasurer / Trustee) both print bare
                # 'Clerk' and 'Treasurer'; the county one follows 'Sheriff'
                # and the township one follows 'Supervisor' (or the township
                # Clerk for Treasurer).
                if last_office == 'Sheriff' or (
                        office == 'Treasurer' and last_office in
                        ('Clerk', 'County Clerk') and last_clerk_county):
                    office = f'County {office}'
                else:
                    office = f'{jurisdiction(precinct)} {office}'
                last_clerk_county = office.startswith('County')
            elif office == 'Supervisor':
                office = f'{jurisdiction(precinct)} Supervisor'
            last_office = office
            office, district = map_office(office, '', COUNTY)
            contest = (office, district, party)
            table_sum = 0
            cast_votes = None
            titles[title] = titles.get(title, 0) + 1
            continue
        if SUMMARY_RE.match(s):
            pending = []
            if s.startswith('Cast Votes:') and contest is not None:
                cast_votes = int(re.match(r'[^:]+:\s{2,}(\d+)', s).group(1))
                if cast_votes != table_sum:
                    problems.append(
                        f'Cast Votes {cast_votes} != candidate sum {table_sum}'
                        f' for {contest} in {precinct}')
            continue
        m = CAND_RE.match(s)
        if m:
            pending = []
            if contest is None:
                problems.append(f'candidate row without contest: {s!r}')
                continue
            name, ed, tot = m.group(1).strip(), int(m.group(2)), int(m.group(3))
            if ed != tot:
                problems.append(f'Election Day {ed} != Total {tot}: {s!r}')
            table_sum += ed
            office, district, party = contest
            rows.append({
                'county': COUNTY, 'precinct': precinct,
                'office': office, 'district': district, 'party': party,
                'candidate': name, 'votes': ed,
            })
            continue
        wm = WROW_RE.match(s)
        if wm and wm.group(1) != s:   # a '(W)'-terminated row with no numbers
            pending = []
            if contest is None:
                problems.append(f'write-in row without contest: {s!r}')
                continue
            table_sum += 0
            office, district, party = contest
            rows.append({
                'county': COUNTY, 'precinct': precinct,
                'office': office, 'district': district, 'party': party,
                'candidate': (wm.group(1).strip() + ' (W)'
                              if wm.group(1).strip() else 'Write-In'),
                'votes': 0,
            })
            continue
        pending.append(s)
    if pending:
        problems.append(f'unconsumed title lines: {pending}')
    with open('/tmp/ionia_titles.txt', 'w') as fh:
        for t in sorted(titles):
            fh.write(f'{titles[t]:4d}  {t}\n')
    return rows, problems, titles


def main():
    rows, problems, titles = parse()
    for p in problems:
        print('PROBLEM:', p)
    # per-contest Cast Votes consistency: every (title, precinct) table's
    # candidate sum must equal its printed Cast Votes; the summary lines were
    # consumed above, so instead check each (contest, precinct) group's
    # candidate sum against the countywide source sanity via CENR later.
    write_csv(COUNTY, rows)
    print(f'{len(titles)} distinct titles')


if __name__ == '__main__':
    main()