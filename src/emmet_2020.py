"""Parse the Emmet County Aug 2020 primary "Precinct Report Official"
Summary Results Report PDF into a precinct CSV.

Source: 'Emmet County Aug 2020 Primary Precinct Report Official.pdf'
(openelections-sources-mi/2020/primary), 129 pages: 22 precinct sections of
5-7 pages, each section opening with a Statistics block (Registered Voters -
Total, Ballots Cast - Total/-Democratic/-Republican/-Nonpartisan/-CrossOver/
-Blank, Voter Turnout) followed by contest blocks:

    DEM|REP <office>            <- or a bare proposal title
    Vote For N
    TOTAL
    <candidate> <votes>
    Write-In Totals <n>         <- optional
    Not Assigned <n>            <- unattributed write-ins (emitted as
                                   candidate 'Write-In')
    Total Votes Cast <n>

Township offices print as '<Office> <Township>' ('Supervisor Bear Creek
Township' -> 'Bear Creek Township Supervisor'); delegate contests are
'Delegate to County Convention <township>, Precinct N' -> Precinct Delegate
with the township as district; proposals keep their printed title with
Yes/No candidates. Every contest is validated: candidates + Not Assigned
write-ins == Total Votes Cast.

Usage: .venv/bin/python src/emmet_2020.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import write_csv, map_office  # noqa: E402

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Emmet County Aug 2020 Primary Precinct Report Official.pdf')

COUNTY = 'Emmet'

HEADER_NOISE = ('Summary Results Report', 'Pimary Election',
                'August 4, 2020 Emmet County', 'Statistics TOTAL',
                'Precinct Summary -', 'Report generated with')
FOOTER_NOISE = re.compile(r'^(Precinct Summary - .* of 129$'
                          r'|Report generated with Electionware.*)')


def main():
    import pdfplumber

    lines = []
    with pdfplumber.open(SRC) as pdf:
        for page in pdf.pages:
            lines += [l.strip() for l in (page.extract_text() or '')
                      .splitlines() if l.strip()]

    rows = []
    precinct = None
    rv = bc = None
    cur = None        # {'title','party','vote_for','cands':[(name,v)], ...}
    problems = []

    def close(cur, where):
        if cur is None:
            return
        cand_sum = sum(v for _, v in cur['cands'])
        if cand_sum + cur['wi_na'] != cur['total']:
            problems.append(f'{where} {cur["title"]}: candidates+WI '
                            f'{cand_sum + cur["wi_na"]} != Total Votes Cast '
                            f'{cur["total"]}')
        rows.append((cur['precinct'], cur['title'], cur['party'],
                     cur['cands'], cur['wi_na'], cur['total'],
                     cur['vote_for']))

    for line in lines:
        if any(line.startswith(p) for p in HEADER_NOISE) or FOOTER_NOISE.match(line):
            continue
        # section header: the label line just above the Statistics block.
        # Delegate titles end in ', Precinct N' too, so party-prefixed lines
        # are matched first (below) and never reach this branch.
        m = re.match(r'^(.+), (?:Precinct \d+|Ward \d+, Precinct \d+)$',
                     line)
        if m and re.search(r'Township|City of', line) \
                and not re.match(r'^(DEM|REP|LIB|UST|GRN|NLP) ', line):
            close(cur, precinct)
            cur = None
            precinct = line.strip()
            rv = bc = None
            continue
        if precinct is None:
            problems.append(f'stray line outside precinct: {line!r}')
            continue
        m = re.match(r'^Registered Voters - Total ([\d,]+)$', line)
        if m:
            rv = int(m.group(1).replace(',', ''))
            continue
        m = re.match(r'^Ballots Cast - Total ([\d,]+)$', line)
        if m:
            bc = int(m.group(1).replace(',', ''))
            continue
        if re.match(r'^(Registered Voters|Ballots Cast|Voter Turnout)', line):
            continue
        m = re.match(r'^(DEM|REP|LIB|UST|GRN|NLP) (.+)$', line)
        if m:
            close(cur, precinct)
            cur = {'precinct': precinct, 'party': m.group(1),
                   'title': m.group(2).strip(), 'cands': [], 'wi_na': 0,
                   'total': None, 'vote_for': None}
            continue
        if line == 'Vote For 1' or re.match(r'^Vote For \d+$', line):
            if cur is not None:
                cur['vote_for'] = line
            continue
        if line == 'TOTAL':
            continue
        m = re.match(r'^Write-In Totals ([\d,]+)$', line)
        if m:
            if cur is not None:
                cur['wi_total'] = int(m.group(1).replace(',', ''))
                cur['in_wi'] = True    # itemized write-in details follow
            continue
        m = re.match(r'^Not Assigned ([\d,]+)$', line)
        if m:
            if cur is not None:
                cur['wi_na'] = int(m.group(1).replace(',', ''))
            continue
        m = re.match(r'^Total Votes Cast ([\d,]+)$', line)
        if m:
            if cur is None:
                problems.append(f'{precinct}: Total Votes Cast outside '
                                f'contest: {line!r}')
                continue
            cur['total'] = int(m.group(1).replace(',', ''))
            close(cur, precinct)
            cur = None
            continue
        m = re.match(r'^(.+?) ([\d,]+)$', line)
        if m and cur is not None:
            name = re.sub(r'^Write-In:\s*', '', m.group(1).strip())
            cur['cands'].append((name,
                                 int(m.group(2).replace(',', ''))))
            continue
        if cur is not None and cur.get('in_wi'):
            # a write-in name wrapped onto its own line with no votes
            # printed (0 votes; the contest total still checks out)
            cur['cands'].append((line, 0))
            continue
        if cur is None:
            # a bare proposal title opens a new contest
            cur = {'precinct': precinct, 'party': '', 'title': line,
                   'cands': [], 'wi_na': 0, 'total': None,
                   'vote_for': None}
        else:
            problems.append(f'{precinct}: unexpected line in contest '
                            f'{cur["title"]!r}: {line!r}')
    close(cur, precinct)

    if problems:
        for p in problems[:30]:
            print('PROBLEM:', p)
        sys.exit(f'Emmet: {len(problems)} problems')

    out_rows = []
    for precinct_label, title, party, cands, wi_na, total, vote_for in rows:
        # 'Delegate to County Convention <juris>' -> Precinct Delegate with
        # the jurisdiction as district (Delta/Crawford convention)
        m = re.match(r'^Delegate to County Convention (.+)$', title)
        if m:
            office, district = 'Precinct Delegate', m.group(1).strip()
        else:
            office, district = map_office(title, '', COUNTY)
        # township offices print as '<Office> <Township>'
        m = re.match(r'^(Supervisor|Clerk|Treasurer|Trustee|Constable|'
                     r'Drain Commissioner|Park Commissioner) (.+ '
                     r'Township)$', office)
        if m:
            office = f'{m.group(2)} {m.group(1)}'
        for cand, v in cands:
            out_rows.append({'county': COUNTY, 'precinct': precinct_label,
                             'office': office, 'district': district,
                             'party': party, 'candidate': cand,
                             'votes': v})
        if wi_na:
            out_rows.append({'county': COUNTY, 'precinct': precinct_label,
                             'office': office, 'district': district,
                             'party': party, 'candidate': 'Write-In',
                             'votes': wi_na})
    # turnout rows: collected per section (rv/bc reset per precinct)
    turnout = {}
    precinct_label = None
    for line in lines:
        m = re.match(r'^(.+), (?:Precinct \d+|Ward \d+, Precinct \d+)$', line)
        if m and re.search(r'Township|City of', line):
            precinct_label = line.strip()
        m = re.match(r'^Registered Voters - Total ([\d,]+)$', line)
        if m and precinct_label:
            turnout.setdefault(precinct_label, [0, 0])[0] = \
                int(m.group(1).replace(',', ''))
        m = re.match(r'^Ballots Cast - Total ([\d,]+)$', line)
        if m and precinct_label:
            turnout.setdefault(precinct_label, [0, 0])[1] = \
                int(m.group(1).replace(',', ''))
    for precinct_label, (rv, bc) in sorted(turnout.items()):
        out_rows.append({'county': COUNTY, 'precinct': precinct_label,
                         'office': 'Registered Voters', 'district': '',
                         'party': '', 'candidate': '', 'votes': rv})
        out_rows.append({'county': COUNTY, 'precinct': precinct_label,
                         'office': 'Ballots Cast', 'district': '',
                         'party': '', 'candidate': '', 'votes': bc})
    write_csv(COUNTY, out_rows)


if __name__ == '__main__':
    main()