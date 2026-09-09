"""Parse county "Precinct Summary Report" precinct-major PDFs (2024 primary).

Layout (Emmet County): one report section per precinct flowing across pages.
Each page repeats a header ("Precinct Summary Report OFFICIAL RESULTS",
"August 2024 Primary", "August 6, 2024 <County>"), the precinct label, and a
footer "Precinct Summary - <date> <time> Page N of M". After the label comes
a Statistics block ("Registered Voters - Total", "Ballots Cast - Total" with
TOTAL / Election Day / ABSENTEE / Early Voting columns, plus party turnout
rows). Contest sections follow: title lines ("DEM Supervisor D Bear Creek
Township", or a bare proposal name), "Vote For N", a wrapped column header
("TOTAL ElectionABSENTE" / "Day E Voting"), one row per candidate
(count + TOTAL + 3 method counts), then "Write-In Totals" (all write-in
votes), "Not Assigned" (unassigned subset), "Total Votes Cast", "Overvotes",
"Undervotes", and "Contest Totals" (ballots participating; TVC + overvotes +
undervotes = contest totals per column). Contests with no candidate print
only the write-in/trailer rows.

Usage:
    .venv/bin/python src/precinct_summary_pdf_parser.py <source.pdf> \
        --county 'Emmet' --date 20240806 \
        --out 2024/counties/20240806__mi__primary__emmet__precinct.csv
"""
import argparse
import csv
import re
import sys

import pdfplumber

# Data row: a label followed by four counts (TOTAL, election day, absentee,
# early voting).
ROW = re.compile(r'^(.+?) ((?:\d[\d,]* ){3}\d[\d,]*)$')
PRECINCT = re.compile(r'^(.+), (Ward \d+ Precinct \d+|Precinct \d+)$')
VOTE_FOR = re.compile(r'^Vote For \d+$')
# Trailer labels are contest bookkeeping, not candidates. "Write-In Totals"
# holds ALL write-in votes (named + unassigned); emit it as a Write-In
# candidate when no named write-in row is present.
TRAILERS = ('Total Votes Cast', 'Overvotes', 'Undervotes', 'Contest Totals',
            'Write-In Totals', 'Not Assigned')
NOISE = [
    re.compile(p) for p in (
        r'^Precinct Summary Report OFFICIAL RESULTS$',
        r'^Precinct Summary - \d{1,2}/\d{1,2}/\d{4}',
        r'^August 2024 Primary$',
        r'^\w+ \d{1,2}, \d{4} \w+ County$',
        r'^TOTAL ElectionABSENTE', r'^Day E Voting$', r'^Statistics',
        r'^(Registered Voters|Ballots Cast|Voter Turnout)',
    )
]
# Title grammar: "<PARTY> <office> <D|R> <detail>" (detail = State /
# Congressional District N / county / township), or a bare proposal title.
TITLE = re.compile(r'^(DEM|REP|LIB|GRN|UST|NPA|NLP|WCP|NPN) '
                   r'(.+?) ([DR]) (.+)$')
ORDINAL = {'1': '1st', '2': '2nd', '3': '3rd'}


def ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 20:
        suf = 'th'
    else:
        suf = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suf}'


def fix_title(line):
    # The text layer embeds literal "\n" escapes in some proposal titles
    # ("Mackinaw City Public Schools\nBond Proposal ...").
    return line.replace('\\n', ' ')


def map_office(party, office, detail, precinct, problems):
    """Title parts -> (office, district)."""
    district = ''
    if office == 'United States Senator' and detail == 'State':
        return 'U.S. Senate', ''
    if office == 'Representative in Congress':
        m = re.match(r'^Congressional District (\d+)$', detail)
        if m:
            return 'U.S. House', m.group(1)
    if office == 'Representative in State Legislature':
        m = re.match(r'^State Representative District (\d+)$', detail)
        if m:
            return 'State House', m.group(1)
    if re.search(r'Delegate to County C', office):
        # Detail is "<jurisdiction>/<precinct>"; confirm it matches the
        # precinct label, then use the same full-label office convention as
        # Muskegon/Ottawa.
        m = re.match(r'^(.+)/(\d+)$', detail)
        if m:
            jur, code = m.groups()
            if 'Ward' in precinct and len(code) == 4:
                # Petoskey codes its wards: /1001 = Ward 1, Precinct 1.
                expected = (f'{jur}, Ward {int(code[0])} '
                            f'Precinct {int(code[1:])}')
            else:
                expected = f'{jur}, Precinct {int(code)}'
            if expected != precinct:
                problems.append(f'{precinct}: delegate detail {detail!r} does '
                                f'not match precinct ({expected!r})')
        else:
            problems.append(f'{precinct}: unparseable delegate detail {detail!r}')
        return f'{precinct} Delegate to County Convention', ''
    if office == 'County Commissioner':
        m = re.match(r'^County Commissioner District (\d+)$', detail)
        if m:
            return f'County Commissioner {ordinal(m.group(1))} District', ''
        return f'County Commissioner {detail}', ''
    if detail == 'Emmet County':
        # Countywide office; keep the printed office name.
        return office, ''
    # Township (or city) office: jurisdiction-first repo convention.
    return f'{detail} {office}', ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--county', required=True)
    ap.add_argument('--date', default='20240806')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    out_rows = []
    problems = []
    precinct = None
    registered = None
    ballots_cast = None
    # Contest being accumulated: {'title': [lines], 'rows': [(name, 4 ints)]}
    pending_title = []
    contest = None

    def start_contest():
        nonlocal pending_title, contest
        if contest is not None:
            problems.append(f'{precinct} / {contest["title"]}: no Contest Totals row')
        if pending_title:
            contest = {'title': ' '.join(pending_title), 'rows': []}
            pending_title = []
        elif contest is None:
            contest = None

    def finish_contest():
        nonlocal contest
        if contest is None:
            return
        totals = next((r[1] for r in contest['rows']
                       if r[0] == 'Contest Totals'), None)
        tvc = next((r[1] for r in contest['rows']
                    if r[0] == 'Total Votes Cast'), None)
        over = next((r[1] for r in contest['rows'] if r[0] == 'Overvotes'), None)
        under = next((r[1] for r in contest['rows'] if r[0] == 'Undervotes'), None)
        wi = next((r[1] for r in contest['rows'] if r[0] == 'Write-In Totals'),
                  None)
        named = [r for r in contest['rows'] if r[0] not in TRAILERS]
        # "Write-In: <Name>" rows are named write-in candidates; they are
        # already included in "Write-In Totals".
        wi_named = [r for r in named if r[0].startswith('Write-In: ')]
        named = [r for r in named if not r[0].startswith('Write-In: ')]
        if totals is None:
            problems.append(f'{precinct} / {contest["title"]}: no Contest '
                            f'Totals row')
            contest = None
            return
        vals = [int(v.replace(',', '')) for v in totals.split()]
        total, methods = vals[0], vals[1:]
        # TVC + overvotes + undervotes == contest totals, per column.
        if tvc is not None and over is not None and under is not None:
            t = [int(v.replace(',', '')) for v in tvc.split()]
            o = [int(v.replace(',', '')) for v in over.split()]
            u = [int(v.replace(',', '')) for v in under.split()]
            for i in range(4):
                if t[i] + o[i] + u[i] != vals[i]:
                    problems.append(f'{precinct} / {contest["title"]}: TVC '
                                    f'{t[i]} + over {o[i]} + under {u[i]} != '
                                    f'Contest Totals {vals[i]} (col {i})')
        # Candidates + write-ins == Total Votes Cast, TOTAL column.
        if tvc is not None:
            t = int(tvc.split()[0].replace(',', ''))
            cand = sum(int(r[1].split()[0].replace(',', '')) for r in named)
            wi_total = int(wi.split()[0].replace(',', '')) if wi is not None else 0
            if cand + wi_total != t:
                problems.append(f'{precinct} / {contest["title"]}: candidate '
                                f'sum {cand} + write-ins {wi_total} != Total '
                                f'Votes Cast {t}')
        title = contest['title']
        party = ''
        district = ''
        m = TITLE.match(title)
        if m:
            party = {'DEM': 'DEM', 'REP': 'REP', 'LIB': 'LIB', 'GRN': 'GRN',
                     'UST': 'UST', 'NPA': 'NPA', 'NLP': 'NLP', 'WCP': 'WCP',
                     'NPN': 'NPN'}.get(m.group(1), m.group(1))
            office, district = map_office(party, m.group(2), m.group(4),
                                          precinct, problems)
        else:
            office = title
        for name, cells in named:
            counts = [int(v.replace(',', '')) for v in cells.split()]
            out_rows.append([args.county.title(), precinct, office, district,
                             party, name, counts[0]] + counts[1:])
        for name, cells in wi_named:
            counts = [int(v.replace(',', '')) for v in cells.split()]
            out_rows.append([args.county.title(), precinct, office, district,
                             party, name[len('Write-In: '):], counts[0]]
                            + counts[1:])
        # Unassigned write-in remainder (Write-In Totals minus the named
        # write-in rows) becomes the Write-In candidate row.
        if wi is not None:
            wi_total = int(wi.split()[0].replace(',', ''))
            remainder = wi_total - sum(int(c[1].split()[0].replace(',', ''))
                                       for c in wi_named)
            if remainder > 0:
                # The remainder's method split is unknown; leave the
                # breakdown blank.
                out_rows.append([args.county.title(), precinct, office,
                                 district, party, 'Write-In', remainder,
                                 '', '', ''])
        out_rows.append([args.county.title(), precinct, office, district,
                         party, 'Ballots Cast', total] + methods)
        contest = None

    with pdfplumber.open(args.pdf) as pdf:
        pages = [p.extract_text() or '' for p in pdf.pages]
    for page in pages:
        for line in (l.strip() for l in page.splitlines()):
            if not line:
                continue
            if any(n.match(line) for n in NOISE):
                # Capture the precinct-level statistics pseudo-rows.
                m = re.match(r'^Registered Voters - Total (\d[\d,]*)$', line)
                if m:
                    registered = m.group(1)
                m = re.match(r'^Ballots Cast - Total (\d[\d,]*) '
                             r'((?:\d[\d,]* ){2}\d[\d,]*)$', line)
                if m:
                    ballots_cast = [m.group(1)] + m.group(2).split()
                continue
            m = PRECINCT.match(line)
            if m:
                finish_contest()
                start_contest()
                precinct = line
                registered = ballots_cast = None
                continue
            if VOTE_FOR.match(line):
                # End of the title block; data rows follow after the
                # (garbled) column header.
                contest = {'title': ' '.join(pending_title), 'rows': []}
                pending_title = []
                continue
            m = ROW.match(line)
            if m and contest is not None:
                contest['rows'].append((m.group(1), m.group(2)))
                if m.group(1) == 'Contest Totals':
                    finish_contest()
                continue
            if contest is None:
                pending_title.append(fix_title(line))
                continue
            if contest['rows']:
                problems.append(f'{precinct} / {contest["title"]}: unexpected '
                                f'line after rows: {line!r}')
            else:
                pending_title.append(fix_title(line))
    finish_contest()
    start_contest()

    if precinct is None:
        sys.exit('no precinct labels found')

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes', 'election_day', 'absentee',
                    'early_voting'])
        for r in out_rows:
            w.writerow(r)
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()