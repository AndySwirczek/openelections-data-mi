"""Parse Macomb County's "Summary Results Report" precinct-major PDF (2024).

Layout (Macomb County Aug 2024 Primary Precinct Results.pdf): one report
section per precinct (2175 pages, ~204 precincts). Each page repeats the
header ("Summary Results Report" / "0824 PRIM" / "Macomb County") and a
footer "Precinct Summary - <date> <time> Page N of M". After the precinct
label ("<Juris>, Pct N" — Richmond City splits as "Pct 1A") comes a Statistics
block ("Registered Voters - Total/Democratic/Republican/NONPARTISAN",
"Ballots Cast - Total/.../CrossOver/Blank", "Voter Turnout - Total") with
TOTAL / AVCB / Election Day / Early Voting columns, then contest sections:
title ("DEM <office> ...", or a bare proposal/judge title), "Vote For N", a
garbled column header, one row per candidate (count per method), and trailer
rows "Write-In Totals", "Total Votes Cast", "Overvotes", "Undervotes",
"Contest Totals" (TVC + overvotes + undervotes = Contest Totals per column).

Column order differs from Emmet's Precinct Summary Report (Election Day /
ABSENTEE / Early Voting there, AVCB / Election Day / Early Voting here), and
Macomb's titles keep offices jurisdiction-last ("Clerk Armada Township"),
matching the county's committed 2024 general file.

Usage:
    .venv/bin/python src/macomb24.py <source.pdf> \
        --out 2024/counties/20240806__mi__primary__macomb__precinct.csv
"""
import argparse
import csv
import re
import sys

import pdfplumber

# Data row: a label followed by four counts (TOTAL, AVCB, election day,
# early voting).
ROW = re.compile(r'^(.+?) ((?:\d[\d,]* ){3}\d[\d,]*)$')
# Precinct label; the lookahead keeps party-prefixed delegate titles
# ("DEM Delegate to County Convention <Juris>, Pct 1") out.
PRECINCT = re.compile(
    r'^(?!(?:DEM|REP|LIB|GRN|UST|NLP|NPA|WCP) )(.+), Pct \d+[A-Z]?$')
VOTE_FOR = re.compile(r'^Vote For \d+$')
PARTY_TITLE = re.compile(r'^(DEM|REP|LIB|GRN|UST|NLP|NPA|WCP) (.+)$')
# Trailer labels are contest bookkeeping, not candidates. "Write-In Totals"
# holds ALL write-in votes; emit it as a Write-In candidate.
TRAILERS = ('Total Votes Cast', 'Overvotes', 'Undervotes', 'Contest Totals',
            'Write-In Totals')
NOISE = [
    re.compile(p) for p in (
        r'^Summary Results Report$', r'^\d{4} PRIM$', r'^Macomb County$',
        # 'Statistics' overlaps the TOTAL/AVCB/... column header on every
        # section's first page ("Statistics TOTAL AVCB Election Early").
        r'^Statistics\b', r'^TOTAL\s+AVCB\s+Election\s+Early$',
        r'^Day\s+Voting$', r'^Precinct Summary - \d{1,2}/\d{1,2}/\d{4}',
        r'^Registered Voters - ', r'^Ballots Cast - ',
        r'^Voter Turnout - Total',
    )
]


def ordinal_number(text):
    """'10th' -> '10'."""
    return str(int(re.match(r'^(\d+)', text).group(1)))


def map_office(title, precinct, problems):
    """Contest title (party stripped) -> (office, district)."""
    m = re.match(r'^Delegate to County Convention (.+)$', title)
    if m:
        # Split precincts print the base precinct's delegate race on their
        # ballot (Richmond City Pct 1A lists "Richmond City, Pct 1"), so the
        # printed jurisdiction is authoritative and may differ from the
        # section's precinct label.
        return f'{m.group(1)} Delegate to County Convention', ''
    if title == 'United States Senator Countywide':
        return 'U.S. Senate', ''
    m = re.match(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$',
                 title)
    if m:
        return 'U.S. House', ordinal_number(m.group(1))
    m = re.match(r'^Representative in State Legislature '
                 r'(\d+)(?:st|nd|rd|th) District$', title)
    if m:
        return 'State House', ordinal_number(m.group(1))
    if title == 'Judge of Probate Court Countywide':
        return 'Judge of Probate Court', ''
    # Everything else (county offices, township offices, proposals) keeps
    # the printed title, matching the county's 2024 general file.
    return title, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    out_rows = []
    problems = []
    precinct = None
    # Contest being accumulated: {'title': [lines], 'rows': [(name, 4 ints)]}
    pending_title = []
    contest = None

    def finish_contest():
        nonlocal contest
        if contest is None:
            return
        rows = contest['rows']
        totals = next((r[1] for r in rows if r[0] == 'Contest Totals'), None)
        tvc = next((r[1] for r in rows if r[0] == 'Total Votes Cast'), None)
        over = next((r[1] for r in rows if r[0] == 'Overvotes'), None)
        under = next((r[1] for r in rows if r[0] == 'Undervotes'), None)
        wi = next((r[1] for r in rows if r[0] == 'Write-In Totals'), None)
        named = [r for r in rows if r[0] not in TRAILERS]
        if totals is None:
            problems.append(f'{precinct} / {contest["title"]}: no Contest '
                            f'Totals row')
            contest = None
            return
        vals = [int(v.replace(',', '')) for v in totals.split()]
        # TVC + overvotes + undervotes == Contest Totals, per column.
        if tvc is not None and over is not None and under is not None:
            t = [int(v.replace(',', '')) for v in tvc.split()]
            o = [int(v.replace(',', '')) for v in over.split()]
            u = [int(v.replace(',', '')) for v in under.split()]
            for i in range(4):
                if t[i] + o[i] + u[i] != vals[i]:
                    problems.append(f'{precinct} / {contest["title"]}: TVC '
                                    f'{t[i]} + over {o[i]} + under {u[i]} != '
                                    f'Contest Totals {vals[i]} (col {i})')
        # Candidates + write-ins == Total Votes Cast, per column.
        if tvc is not None:
            t = [int(v.replace(',', '')) for v in tvc.split()]
            cand = [sum(int(r[1].split()[i].replace(',', '')) for r in named)
                    for i in range(4)]
            wi_vals = [int(v.replace(',', '')) for v in wi.split()] \
                if wi is not None else [0, 0, 0, 0]
            for i in range(4):
                if cand[i] + wi_vals[i] != t[i]:
                    problems.append(f'{precinct} / {contest["title"]}: '
                                    f'candidate sum {cand[i]} + write-ins '
                                    f'{wi_vals[i]} != Total Votes Cast {t[i]} '
                                    f'(col {i})')
        title = ' '.join(contest['title'])
        party = ''
        m = PARTY_TITLE.match(title)
        if m:
            party = m.group(1)
            office, district = map_office(m.group(2), precinct, problems)
        else:
            office, district = map_office(title, precinct, problems)
        for name, cells in named:
            counts = [int(v.replace(',', '')) for v in cells.split()]
            out_rows.append([precinct, office, district, party, name,
                             counts[0], counts[2], counts[1], counts[3]])
        if wi is not None:
            out_rows.append([precinct, office, district, party, 'Write-In',
                             int(wi.split()[0].replace(',', '')),
                             int(wi.split()[2].replace(',', '')),
                             int(wi.split()[1].replace(',', '')),
                             int(wi.split()[3].replace(',', ''))])
        # The Contest Totals row's first column is the contest's ballot
        # count; its method split completes the breakdown columns.
        out_rows.append([precinct, office, district, party, 'Ballots Cast',
                         vals[0], vals[2], vals[1], vals[3]])
        contest = None

    with pdfplumber.open(args.pdf) as pdf:
        pages = [p.extract_text() or '' for p in pdf.pages]
    for page in pages:
        for line in (l.strip() for l in page.splitlines()):
            if not line:
                continue
            if any(n.match(line) for n in NOISE):
                continue
            m = PRECINCT.match(line)
            if m:
                finish_contest()
                precinct = line
                pending_title = []
                contest = None
                continue
            if VOTE_FOR.match(line):
                # End of the title block; data rows follow after the
                # (garbled) column header.
                contest = {'title': pending_title, 'rows': []}
                pending_title = []
                continue
            m = ROW.match(line)
            if m and contest is not None:
                contest['rows'].append((m.group(1), m.group(2)))
                if m.group(1) == 'Contest Totals':
                    finish_contest()
                continue
            if contest is None:
                pending_title.append(line)
                continue
            if contest['rows']:
                problems.append(f'{precinct} / {contest["title"]}: unexpected '
                                f'line after rows: {line!r}')
            else:
                pending_title.append(line)
    finish_contest()

    if precinct is None:
        sys.exit('no precinct labels found')

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes', 'election_day', 'absentee',
                    'early_voting'])
        for precinct, office, district, party, name, total, ed, av, ev \
                in out_rows:
            w.writerow(['Macomb', precinct, office, district, party, name,
                        total, ed, av, ev])
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()