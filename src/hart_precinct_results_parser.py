"""Parse a Hart "Official Precinct Results" precinct-major PDF (Genesee 2024
primary) into a precinct CSV.

Layout (openelec­tions-sources-mi/2024/primary/"Genesee County Aug 2024
Primary Precinct Results.pdf", 745 pages, one report per precinct flowing
over ~5 pages): the precinct label line carries the turnout
("Argentine Township, Precinct 1   775 of 2,409 registered voters =
32.17%"), repeated at the top of every page of the section. Each contest is

    <office> - <Democratic|Republican|Nonpartisan> Party
    Choice  Party  Absentee Voting  Early Voting  Election Day Voting  Total
    <name>  <abs> <pct>  <early> <pct>  <ed> <pct>  <total> <pct>
    Cast Votes:  <abs> <pct>  <early> <pct>  <ed> <pct>  <total> <pct>
    Undervotes / Overvotes / Invalid Votes rows (single counts, not emitted)

Contests with no candidates on a ballot style print a bare zero-pct row and
no names; they emit no rows. Proposal Yes/No rows are ordinary choice rows.
Candidate names keep their printed mixed case.

Emissions: one row per choice (votes + election_day, early_voting, absentee
breakdown — the column order of Genesee's 2024 general file), plus
Ballots Cast and Registered Voters pseudo rows from the label (which give
only a total, so their breakdown cells stay blank).

Usage:
    .venv/bin/python src/hart_precinct_results_parser.py <source.pdf> \
        --county Genesee \
        --out 2024/counties/20240806__mi__primary__genesee__precinct.csv
"""
import argparse
import csv
import re
import subprocess
import sys

PRECINCT = re.compile(
    r'^(.+), (Ward \d+ )?Precinct (\d+) (\d[\d,]*) of ([\d,]+) '
    r'registered voters')
TITLE = re.compile(r'^(.*) - (Democratic|Republican|Nonpartisan) Party$')
HEADER = re.compile(r'^Choice +Party ')
CAND = re.compile(
    r'^(.+?) (\d[\d,]*) \d+\.\d+% (\d[\d,]*) \d+\.\d+% '
    r'(\d[\d,]*) \d+\.\d+% (\d[\d,]*) \d+\.\d+%$')
CAST = re.compile(
    r'^Cast Votes: (\d[\d,]*) \d+\.\d+% (\d[\d,]*) \d+\.\d+% '
    r'(\d[\d,]*) \d+\.\d+% (\d[\d,]*) \d+\.\d+%$')
TAIL_NOISE = re.compile(
    r'^(Undervotes:|Overvotes:|Invalid Votes:)|'
    r'^(?:\d[\d,]* \d+\.\d+% ){3}\d[\d,]* \d+\.\d+%$')
PAGE_NOISE = re.compile(
    r'^(Official Precinct Results|Official Results|Genesee County, Michigan|'
    r'Primary Election.*|Run Time|Run Date|8/6/2024|Page \d+|'
    r'Precincts Reporting|'
    r'Party +Absentee.*Voting.*Election.*Day.*Voting.*Total|'
    r'\*+ End of report \*+|'
    r'Registered Voters$|\d+ of [\d,]+ = [\d.]+%$)')
STATE_OFFICES = [
    (re.compile(r'^United States Senator$'), 'U.S. Senate', None),
    (re.compile(r'^Representative in Congress '
                r'(\d+)(?:st|nd|rd|th) District$'), 'U.S. House', 1),
    (re.compile(r'^Representative in State Legislature '
                r'(\d+)(?:st|nd|rd|th) District$'), 'State House', 1),
]
PARTY_CODES = {'Democratic': 'DEM', 'Republican': 'REP', 'Nonpartisan': ''}
# Genesee's general file drops the county prefix from county offices
COUNTY_OFFICES = {
    'Genesee County Sheriff': 'Sheriff',
    'Genesee County Treasurer': 'Treasurer',
    'Genesee County Prosecuting Attorney': 'Prosecuting Attorney',
    'Genesee County Drain Commissioner': 'Drain Commissioner',
    'Genesee County Clerk and Register of Deeds': 'Clerk and Register of '
                                                  'Deeds',
    'Genesee County Surveyor': 'Surveyor',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    rows = []
    problems = []
    precinct = None
    contest = None  # (office, district, party) being collected
    contest_rows = None  # (name, abs, early, ed, total)
    cast = None
    zero_contest = False  # contest printed its bare zero row (no candidates)

    def finish_contest():
        nonlocal contest, contest_rows, cast, zero_contest
        if contest is None:
            return
        office, district, party = contest
        if not contest_rows:
            if not zero_contest:
                problems.append(f'{precinct} / {office}: no candidate rows')
        else:
            if cast is None:
                problems.append(f'{precinct} / {office}: no Cast Votes row')
            else:
                for i, label in enumerate(('absentee', 'early_voting',
                                           'election_day', 'total')):
                    if sum(r[1 + i] for r in contest_rows) != cast[i]:
                        problems.append(
                            f'{precinct} / {office}: {label} candidate sum '
                            f'{sum(r[1 + i] for r in contest_rows)} != Cast '
                            f'Votes {cast[i]}')
            for name, abs_, early, ed, total in contest_rows:
                rows.append([args.county, precinct, office, district, party,
                             name, total, ed, early, abs_])
        contest, contest_rows, cast, zero_contest = None, None, None, False

    text = subprocess.run(['pdftotext', '-layout', args.pdf, '-'],
                          capture_output=True, text=True, check=True).stdout
    for raw in text.splitlines():
        line = re.sub(r'\s+', ' ', raw).strip()
        if not line or PAGE_NOISE.match(line):
            continue
        m = PRECINCT.match(line)
        if m:
            label = (f'{m.group(1)}, {m.group(2) or ""}'
                     f'Precinct {m.group(3)}')
            if label != precinct:
                finish_contest()
                precinct = label
                rows.append([args.county, precinct, 'Ballots Cast', '', '',
                             '', int(m.group(4).replace(',', '')), '', '', ''])
                rows.append([args.county, precinct, 'Registered Voters', '',
                             '', '', int(m.group(5).replace(',', '')),
                             '', '', ''])
            continue
        m = TITLE.match(line)
        if m:
            finish_contest()
            office, district = m.group(1).strip(), ''
            for pat, name, grp in STATE_OFFICES:
                m2 = pat.match(office)
                if m2:
                    office = name
                    district = m2.group(grp) if grp else ''
                    break
            else:
                office = COUNTY_OFFICES.get(office, office)
            if re.match(r'^.* Delegate to County Convention PCT (\d+)$',
                        office):
                m2 = re.match(r'^(.*) Delegate to County Convention PCT '
                              r'(\d+)$', office)
                office = (f'{m2.group(1)}, Precinct {m2.group(2)} Delegate '
                          f'to County Convention')
            contest = (office, district, PARTY_CODES[m.group(2)])
            contest_rows = []
            cast = None
            zero_contest = False
            continue
        if HEADER.match(line):
            continue
        if TAIL_NOISE.match(line):
            if contest is not None and CAND.match(line) is None:
                zero_contest = True
            continue
        m = CAST.match(line)
        if m and contest is not None:
            cast = tuple(int(v.replace(',', '')) for v in m.groups())
            continue
        m = CAND.match(line)
        if m and contest is not None:
            contest_rows.append(
                (m.group(1).strip(), *(int(v.replace(',', ''))
                                       for v in m.groups()[1:])))
            continue
        if contest is not None or precinct is not None:
            problems.append(f'{precinct}: unmatched line {line!r}')
    finish_contest()

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes', 'election_day', 'early_voting',
                    'absentee'])
        w.writerows(rows)
    print(f'Wrote {len(rows)} rows to {args.out} ({len(problems)} problems)')
    for p in problems[:40]:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(1)


if __name__ == '__main__':
    main()