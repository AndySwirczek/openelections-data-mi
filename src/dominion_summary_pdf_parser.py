"""Parse a Dominion "Summary Results Report" precinct-major PDF (Bay 2024
primary) into a precinct CSV.

Layout (openelec­tions-sources-mi/2024/primary/"Bay County Aug 2024 Primary
Precinct Results.pdf", 201 pages): one section per precinct, label on its own
line ("City of Bay City, Ward 1 Precinct 1", "City of Midland, Precinct 2" —
an out-of-county split), a Statistics block with a
"Ballots Cast - Total <total> <election day> <absentee> <early voting>" row,
then contest blocks. Each contest is one line

    <PCODE> <office> -- <Democratic|Republican> <scope>
    Vote For N
    <column header lines>
    <NAME>  <total> <election day> <absentee> <early voting>
    Write-In Totals  <total> <election day> <absentee> <early voting>

where scope is 'State' (U.S. Senate), 'Congressional District N', 'State
Representative District N', 'County Commissioner District NN', 'Bay County'
(county offices), a jurisdiction ('Bangor Township'), or
'<jurisdiction>/<code>' (the per-precinct delegate contests, e.g.
'Democratic Bangor Township/1'). One contest — Bay City's nonpartisan ward
council races — has no party prefix or '--' ('Council Member By Ward City of
Bay City W2'); it is kept verbatim, matching Bay's 2024 general file.

Candidate rows print names in ALL CAPS; they are converted to Title Case
(Mc/O'-aware) to match the county's general file. The per-precinct delegate
office is named from the section's precinct label, matching the
"<precinct> Delegate to County Convention" convention.

Usage:
    .venv/bin/python src/dominion_summary_pdf_parser.py <source.pdf> \
        --county Bay \
        --out 2024/counties/20240806__mi__primary__bay__precinct.csv
"""
import argparse
import csv
import re
import subprocess
import sys

# <office base> -> county office name (tail 'Bay County')
COUNTY_OFFICES = {
    'Clerk': 'County Clerk', 'Sheriff': 'County Sheriff',
    'Treasurer': 'County Treasurer',
    'Prosecuting Attorney': 'County Prosecuting Attorney',
    'Register of Deeds': 'County Register of Deeds',
    'Drain Commissioner': 'County Drain Commissioner',
    'County Executive': 'County Executive',
    'County Road Commissioner': 'County Road Commissioner',
}
ORDINAL = {1: 'st', 2: 'nd', 3: 'rd'}
# candidate names whose caps->title conversion needs a hand (checked against
# the county's general file spellings)
NAME_FIXES = {}

ROW = re.compile(r'^(.+?) {2,}(\d+) +(\d+) +(\d+) +(\d+)$')
WRITE_IN = re.compile(r'^Write-In Totals {2,}(\d+) +(\d+) +(\d+) +(\d+)$')
BALLOTS = re.compile(
    r'^Ballots Cast - Total {2,}([\d,]+) +([\d,]+) +([\d,]+) +([\d,]+)$')
PRECINCT = re.compile(r'^(.+), (Ward \d+ )?Precinct \d+$')
TITLE = re.compile(r'^(DEM|REP) (.+?) -- (.+)$')
NONPARTISAN = re.compile(r'^Council Member By Ward ')
NOISE = re.compile(
    r'^(Summary Results Report.*|24AMIBAY.*|August \d+, \d{4}.*|Bay County|'
    r'Precinct Summary - .*|Statistics.*|Voter Turnout.*|'
    r'TOTAL +Election.*|Day +E +Voting)$')


def ordinal(n):
    return f'{n}{ORDINAL.get(n % 10 if n % 100 not in (11, 12, 13) else 0, "th")}'


def smart_title(name):
    """'MCDONALD RIVET' -> 'McDonald Rivet'; "O'DONNELL" -> "O'Donnell"."""
    if not name.isupper():
        return name
    words = []
    for word in name.split(' '):
        parts = []
        for part in word.split('-'):
            if part in ('II', 'III', 'IV', 'JR', 'SR'):
                parts.append(part.title())
            elif part[:2] == "MC" and len(part) > 2:
                parts.append('Mc' + part[2:].capitalize())
            elif part[:2] == "O'" and len(part) > 2:
                parts.append("O'" + part[2:].capitalize())
            else:
                parts.append(part.capitalize())
        words.append('-'.join(parts))
    return ' '.join(words)


def map_office(base, scope, precinct, problems):
    """Contest title -> (office, district)."""
    scope = re.sub(r'^(?:Democratic|Republican) ', '', scope)
    if scope == 'State' and base == 'United States Senator':
        return 'U.S. Senate', ''
    m = re.match(r'^Congressional District (\d+)$', scope)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^State Representative District (\d+)$', scope)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'^County Commissioner District (\d+)$', scope)
    if m:
        return f'County Commissioner {ordinal(int(m.group(1)))} District', ''
    if scope == 'Bay County':
        if base not in COUNTY_OFFICES:
            problems.append(f'{precinct}: unknown county office {base!r}')
            return base, ''
        return COUNTY_OFFICES[base], ''
    # jurisdiction tail; delegates are named from the section's precinct
    if base == 'Delegate to County Convention':
        m = re.match(r'^(.+)/\d+$', scope)
        jurisdiction = m.group(1) if m else scope
        label_jurisdiction = re.sub(r', (?:Ward \d+ )?Precinct \d+$', '',
                                    precinct)
        if jurisdiction != label_jurisdiction:
            problems.append(f'{precinct}: delegate scope {scope!r} does not '
                            f'match precinct label')
        return f'{precinct} Delegate to County Convention', ''
    if re.search(r' (Township|City)$', scope):
        return f'{scope} {base}', ''
    problems.append(f'{precinct}: unknown contest scope {scope!r} '
                    f'(base {base!r})')
    return base, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    rows = []
    problems = []
    precinct = None
    ballots = None
    contest = None  # (office, district, party) being collected
    contest_rows = None
    prev_line = None  # buffered bare proposal title, waiting for 'Vote For'

    def finish_contest():
        if contest is None:
            return
        if not contest_rows:
            problems.append(f'{precinct} / {contest[0]}: no candidate rows')
        for name, cells in contest_rows:
            total, ed, abs_, ev = cells
            if ed + abs_ + ev != total:
                problems.append(f'{precinct} / {contest[0]} / {name}: methods '
                                f'{ed}+{abs_}+{ev} != total {total}')
            rows.append([args.county, precinct, contest[0], contest[1],
                         contest[2], name, total, ed, abs_, ev])

    # pdftotext -layout keeps the wide column gaps that separate the name
    # from its counts (pdfplumber's extract_text welds them together)
    text = subprocess.run(['pdftotext', '-layout', args.pdf, '-'],
                          capture_output=True, text=True, check=True).stdout
    for line in text.splitlines():
        line = line.strip()
        if True:
            if not line:
                continue
            m = BALLOTS.match(line)
            if m:
                finish_contest()
                contest = None
                total, ed, abs_, ev = (
                    int(v.replace(',', '')) for v in m.groups())
                if ed + abs_ + ev != total:
                    problems.append(f'{precinct}: Ballots Cast methods '
                                    f'{ed}+{abs_}+{ev} != total {total}')
                ballots = (total, ed, abs_, ev)
                rows.append([args.county, precinct, 'Ballots Cast', '', '', '',
                             total, ed, abs_, ev])
                continue
            m = PRECINCT.match(line)
            if m and not ROW.match(line):
                if line == precinct:
                    # Dominion repeats the label at the top of every page of
                    # the section; only a new label starts a precinct
                    continue
                finish_contest()
                contest = None
                prev_line = None
                if ballots is None and precinct is not None:
                    problems.append(f'{precinct}: no Ballots Cast row')
                precinct = line
                ballots = None
                continue
            m = TITLE.match(line)
            if m:
                finish_contest()
                office, district = map_office(m.group(2), m.group(3),
                                              precinct, problems)
                contest = (office, district, m.group(1))
                contest_rows = []
                prev_line = None
                continue
            if NONPARTISAN.match(line):
                finish_contest()
                contest = (line, '', '')
                contest_rows = []
                prev_line = None
                continue
            if re.match(r'^Vote For \d+$', line):
                if prev_line is not None:
                    # bare proposal title ('Fire Department Operating Millage
                    # Bangor Township') announced by its following Vote For
                    if contest is not None:
                        finish_contest()
                    contest = (prev_line, '', '')
                    contest_rows = []
                    prev_line = None
                elif contest is None:
                    problems.append(f'{precinct}: Vote For with no title')
                # else: the Vote For of the contest the title just opened
                continue
            m = WRITE_IN.match(line)
            if m and contest is not None:
                contest_rows.append(('Write-In',
                                     [int(v) for v in m.groups()]))
                continue
            m = ROW.match(line)
            if m and contest is not None and not name_noise(m.group(1)):
                contest_rows.append(
                    (smart_title(m.group(1).strip()),
                     [int(v) for v in m.groups()[1:]]))
                continue
            if NOISE.match(line):
                continue
            # an unmatched, non-noise line while a contest is open ends it
            # (the bare proposal titles that follow a finished contest)
            if contest is not None:
                finish_contest()
                contest = None
            if prev_line is not None:
                problems.append(f'{precinct}: unmatched line {prev_line!r}')
            prev_line = line
    finish_contest()
    if precinct is not None and ballots is None:
        problems.append(f'{precinct}: no Ballots Cast row')

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes', 'election_day', 'absentee',
                    'early_voting'])
        w.writerows(rows)
    print(f'Wrote {len(rows)} rows to {args.out} ({len(problems)} problems)')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(1)


def name_noise(name):
    """Wrapped column-header fragments that look like name rows."""
    return bool(re.match(r'^(TOTAL|Election|ABSENTE|Day|Early|Voting|'
                         r'Statistics|Vote For|Write-In)$', name.strip()))


if __name__ == '__main__':
    main()