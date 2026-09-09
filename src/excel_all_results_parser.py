"""Parse a vendor "All Results" Excel export into a precinct CSV.

Written for files like
openelections-sources-mi/2024/primary/Saginaw County Aug 2024 Primary All Results.xlsx
whose 'Precinct Results' sheet has one row per (precinct, contest, choice)
with a single Total column:

    Precinct | Office Name | Contest ID | Ballot Name | Choice ID | Party | Total

Choice rows are candidates plus 'Ballots Cast', 'Write-in', 'Over Vote Count'
and 'Under Vote Count'. 'Ballots Cast' is repeated per contest and is the
count of ballots that saw that contest (it varies for split precincts and
uncontested races); the emitted precinct total is the top-of-ballot contest's
value (lowest contest ID), which is total ballots cast. The 'Summary Results'
sheet holds countywide totals per choice and validates the parse.

Out-of-county splits (e.g. Isabella's 'City of Clare, Precinct 1 (Out of
County)') report Ballots Cast but leave every choice cell blank in the
'Precinct Results' sheet while the summary includes their votes; those cells
are recovered as the residual (summary total minus all other precincts),
bounded by the precinct's Ballots Cast for the contest.

Usage:
    python src/excel_all_results_parser.py \
        --source '/path/County Aug 2024 Primary All Results.xlsx' \
        --county Saginaw --date 20240806 \
        --out 2024/counties/20240806__mi__primary__saginaw__precinct.csv
"""
import argparse
import csv
import re
import sys

import openpyxl

# Columns of the 'Precinct Results' sheet
PRECINCT, OFFICE_NAME, CONTEST_ID, BALLOT_NAME, PARTY, TOTAL = 0, 1, 2, 3, 5, 6
# Columns of the 'Summary Results' sheet (same but without 'Precinct')
SUM_CONTEST, SUM_BALLOT, SUM_TOTAL = 1, 2, 5

PARTY_MAP = {'Democrat': 'DEM', 'Democratic': 'DEM', 'Republican': 'REP',
             'Libertarian': 'LIB', 'Green': 'GRN', 'U.S. Taxpayers': 'UST',
             'Natural Law': 'NLP', 'Working Class': 'WCP'}

# Rows with these ballot names carry no votes to record ('Over Votes'/
# 'Under Votes' is Lake/Berrien 2024's spelling of the same thing)
SKIP_NAMES = {'Over Vote Count', 'Under Vote Count',
              'Over Votes', 'Under Votes'}

PSEUDO_NAMES = {'Ballots Cast', 'Registered Voters'}

# lowercase office words seen in source titles, keyed by their last word
OFFICE_WORD_FIX = {'trustee': 'Trustee', 'clerk': 'Clerk',
                   'treasurer': 'Treasurer', 'supervisor': 'Supervisor'}

# source title typos -> corrected spellings
TITLE_FIXES = [('Roland Township', 'Rolland Township')]

# precinct label fixes (source style -> repo convention)
PRECINCT_FIXES = [('Sturgis City,', 'City of Sturgis,')]

# ', St. Joseph County Michigan' jurisdiction suffix some titles carry
JURISDICTION_SUFFIX = re.compile(r',\s*St\.? Joseph County Michigan$')
# ' for <County> Michigan' suffix on county offices (Lake 2024)
COUNTY_SUFFIX = re.compile(r'\s+for\s+[\w. ]+ County Michigan$')

# St. Joseph 2024 titles the vendor left generic; keys are the cleaned source
# titles, values the real ballot-item names (county resolutions, township
# filings, and the White Pigeon Township Library board minutes of 8/26/2024,
# which record the library millage passing 585-271)
PROPOSAL_TITLES = {
    'Road Millage Renewal':
        'St. Joseph County Road Millage Renewal Proposal',
    'COA Renewal':
        'St. Joseph County Commission on Aging Millage Renewal Proposal',
    'Township Proposals for Colon Township':
        'Colon Township Library Millage Renewal Proposal',
    'Township Proposals for Fawn River Township':
        'Fawn River Township Fire Protection and Ambulance Special '
        'Assessment Proposal',
    'Township Proposals for Leonidas Township':
        'Leonidas Township Fire and Ambulance Service Millage Renewal '
        'Proposal',
    'Township Proposals for Mottville Township':
        'Mottville Township Road Improvement Millage Proposal',
    'Township Proposals for Sherman Township':
        'Sherman Township Fire Protection and Ambulance Special Assessment '
        'Proposal',
    'Township Proposals for Sturgis Township':
        'Sturgis Township Fire and Ambulance Special Assessment Proposal',
    'Township Proposals for White Pigeon Township':
        'White Pigeon Township Library Millage Renewal Proposal',
    'Local School District Proposals for Centreville Public Schools':
        'Centreville Public Schools Operating Millage Renewal Proposal',
    'Local School District Proposals for Sturgis Public School District':
        'Sturgis Public Schools Operating Millage Increase Proposal',
}

# abbreviations in delegate titles ('Delegate to County Convention for
# COTR P1'); anything else not already a City of/Township gets ' Township'
DELEGATE_ABBREV = {'COTR': 'City of Three Rivers',
                   'Sturgis City': 'City of Sturgis',
                   'Sturgs Twp': 'Sturgis Township'}

# Choice cells the source leaves blank but whose values are known from
# another county's official report (both counties voted on the shared
# contest). {(contest_id, ballot): {precinct: votes}}
MANUAL_CELLS = {
    # Osceola's Pine River Area Schools Bond Proposal: Cherry Grove Township
    # (Wexford County)'s numbers come from the Wexford County SOVC
    # (openelections-sources-mi/2024/primary, parsed into
    # 2024/counties/20240806__mi__primary__wexford__precinct.csv; its
    # Ballots Cast of 7 matches this file's).
    ('212', 'Yes'): {'Cherry Grove Township (Wexford County)': 4},
    ('212', 'No'): {'Cherry Grove Township (Wexford County)': 3},
}

STATE_OFFICES = [
    (re.compile(r'^United States Senator(?: for State)?$'), 'U.S. Senate',
     None),
    (re.compile(r'^(?:Representative in Congress|Rep in Congress)'
                r'(?: (\d+)(?:(?:st|nd|rd|th) )?District)?$'), 'U.S. House',
     1),
    (re.compile(r'^(?:State Representative|State Rep|Representative in '
                r'State Legislature|Rep in State Legislature)'
                r'(?: (\d+)(?:(?:st|nd|rd|th) )?District)?$'), 'State House',
     1),
    (re.compile(r'^(?:State Senator|State Senate) '
                r'(\d+)(?:(?:st|nd|rd|th) )?District$'), 'State Senate', 1),
]

ORDINAL = {1: 'st', 2: 'nd', 3: 'rd'}


def ordinal(n):
    return f'{n}{ORDINAL.get(n % 10 if n % 100 not in (11, 12, 13) else 0, "th")}'


def clean_office(name):
    """Strip the '(DEM)'/'(REP)' suffix and whitespace noise; expand 'Twp.'."""
    name = re.sub(r'\s+', ' ', str(name)).strip()
    contest_party = ''
    # the closing paren is sometimes missing ('Richland Twp. Trustee (DEM')
    m = re.search(r'\s*\((DEM|REP)\s*\)?\s*$', name)
    if m:
        contest_party = m.group(1)
        name = name[:m.start()].rstrip()
    name = JURISDICTION_SUFFIX.sub('', name).strip()
    name = COUNTY_SUFFIX.sub('', name).strip()
    # St. Joseph 2024 puts the party tag mid-title ('County Commissioner
    # for Comm District 7 (DEM), St. Joseph County Michigan'): the suffix
    # removal above can strand a second tag at the end
    m = re.search(r'\s*\((DEM|REP)\s*\)?\s*$', name)
    if m and not contest_party:
        contest_party = m.group(1)
        name = name[:m.start()].rstrip()
    name = re.sub(r'\bTwp\.?', 'Township', name)
    for bad, good in TITLE_FIXES:
        name = name.replace(bad, good)
    name = re.sub(r'\s+', ' ', name).strip()
    # a few source titles lowercase the office word ('James Twp. trustee')
    words = name.rsplit(' ', 1)
    if len(words) == 2 and words[1] in OFFICE_WORD_FIX:
        name = f'{words[0]} {OFFICE_WORD_FIX[words[1]]}'
    return name, contest_party


def expand_delegate_place(name):
    """'White Pigeon P1' -> 'White Pigeon Township, Precinct 1'."""
    m = re.match(r'^(.*?)\s+P(\d+)$', name)
    prec = ''
    if m:
        name, prec = m.group(1), f', Precinct {m.group(2)}'
    name = DELEGATE_ABBREV.get(name, name)
    if not (name.startswith('City of') or name.endswith('Township')):
        name += ' Township'
    return f'{name}{prec}'


def normalize_office(name):
    """Return (office, district, contest_party)."""
    name, contest_party = clean_office(name)
    # 'Representative in State Legislature for Representative in State
    # Legislature 101st District' (Lake 2024): collapse the doubled prefix
    m = re.match(r'^(.+?) for \1 (.+)$', name)
    if m:
        name = f'{m.group(1)} {m.group(2)}'
    # 'Representative in State Legislature District 108' (Delta 2024)
    m = re.match(r'^(Representative in Congress|Representative in '
                 r'State Legislature) District (\d+)$', name)
    if m:
        name = f'{m.group(1)} {ordinal(int(m.group(2)))} District'
    for pat, out_office, dist_group in STATE_OFFICES:
        m = pat.match(name)
        if m:
            has_num = dist_group and m.group(dist_group)
            district = str(int(m.group(dist_group))) if has_num else ''
            return out_office, district, contest_party
    # 'Delegate for Albee Township, Precinct 1' and 'City of Mt. Pleasant,
    # Precinct 2 Delegate' are county-convention delegate contests; the
    # repo's files name them 'X Delegate to County Convention'
    m = re.match(r'^Delegate for (.+)$', name)
    if m:
        return f'{m.group(1)} Delegate to County Convention', '', \
            contest_party
    if name.endswith(' Delegate'):
        # 'X, Precinct 1 Precinct Delegate' (Delta/Lake 2024) drops the
        # doubled 'Precinct' word
        base = re.sub(r', (Precinct \S+) Precinct Delegate$', r', \1', name)
        if base == name:
            base = name[:-len(' Delegate')]
        return f'{base} Delegate to County Convention', '', contest_party
    # 'Delegate to County Convention for White Pigeon P1' (St. Joseph 2024
    # style) keeps its form but with the precinct name expanded
    m = re.match(r'^Delegate to County Convention for (.+)$', name)
    if m:
        return f'Delegate to County Convention for ' \
            f'{expand_delegate_place(m.group(1))}', '', contest_party
    # 'County Commissioner for Comm District 4' (St. Joseph 2024)
    m = re.match(r'^County Commissioner (?:for Comm )?District (\d+)$', name)
    if m:
        return f'County Commissioner {ordinal(int(m.group(1)))} District', \
            '', contest_party
    # 'Township Clerk for Burr Oak Township' -> 'Burr Oak Township Clerk'
    m = re.match(r'^Township (Clerk|Treasurer|Trustee|Supervisor|Constable) '
                 r'for (.+)$', name)
    if m:
        return f'{m.group(2)} {m.group(1)}', '', contest_party
    if name in PROPOSAL_TITLES:
        return PROPOSAL_TITLES[name], '', contest_party
    return name, '', contest_party


def read_summary(wb):
    """Countywide totals from the 'Summary Results' sheet, keyed by contest.

    Returns ({(contest_id, ballot): total}, {(ballot, contest_id): total}).
    """
    ws = wb['Summary Results']
    choices, pseudo = {}, {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        r = list(r) + [None] * 8
        contest_id, ballot, total = r[SUM_CONTEST], r[SUM_BALLOT], r[SUM_TOTAL]
        if contest_id is None or ballot is None:
            continue
        ballot = re.sub(r'\s+', ' ', str(ballot)).strip()
        if ballot in SKIP_NAMES:
            continue
        if ballot in PSEUDO_NAMES:
            pseudo[(ballot, str(contest_id))] = int(total or 0)
        else:
            choices[(str(contest_id), ballot)] = int(total or 0)
    return choices, pseudo


def parse(source, us_house_district=None, state_house_district=None):
    """Return (rows, problems).

    us_house_district / state_house_district fill in the district when the
    source titles the contest just 'Rep in Congress' / 'State Rep' with no
    number (St. Joseph 2024).
    """
    wb = openpyxl.load_workbook(source, data_only=True, read_only=True)
    if 'Precinct Results' not in wb.sheetnames:
        sys.exit(f'no "Precinct Results" sheet in {source}; '
                 f'sheets: {wb.sheetnames}')
    sum_choice, sum_pseudo = read_summary(wb)
    ws = wb['Precinct Results']
    rows = []            # emitted rows, in sheet order
    pseudo_contest = {}  # (ballot, contest) -> {precinct: value}
    bc_pick = {}         # precinct -> (contest_id, Ballots Cast value)
    cells = {}           # (contest_id, ballot) -> {precinct: value or None}
    entry_at = {}        # (contest_id, ballot, precinct) -> index into rows
    problems = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        r = list(r) + [None] * 8
        precinct, office_name, contest_id, ballot, party, total = (
            r[PRECINCT], r[OFFICE_NAME], r[CONTEST_ID], r[BALLOT_NAME],
            r[PARTY], r[TOTAL])
        if precinct is None or office_name is None or ballot is None:
            continue
        ballot = re.sub(r'\s+', ' ', str(ballot)).strip()
        if ballot in SKIP_NAMES:
            continue
        precinct = re.sub(r'\s+', ' ', str(precinct)).strip()
        for bad, good in PRECINCT_FIXES:
            precinct = precinct.replace(bad, good)
        cid = str(contest_id)
        office, district, contest_party = normalize_office(office_name)
        if not district and office == 'U.S. House' and us_house_district:
            district = str(us_house_district)
        if not district and office == 'State House' and state_house_district:
            district = str(state_house_district)
        row_party = PARTY_MAP.get(str(party or '').strip(), '')
        if not row_party and party:
            row_party = str(party).strip().upper()[:3]
        if row_party == '(BL':
            row_party = ''
        if not row_party:
            row_party = contest_party
        if ballot in PSEUDO_NAMES:
            # Pseudo-office: the count goes in votes, candidate stays blank.
            # The sheet repeats the row per contest with a per-contest value,
            # so the repeats are only cross-checked, not emitted (the
            # emitted precinct total is the top-of-ballot contest's value).
            pseudo_contest.setdefault((ballot, cid), {})[precinct] = \
                int(total or 0)
            if ballot == 'Registered Voters':
                rows.append([precinct, ballot, '', '', '', int(total or 0)])
            else:
                try:
                    n = int(contest_id)
                except (TypeError, ValueError):
                    n = 0
                if precinct not in bc_pick or n < bc_pick[precinct][0]:
                    bc_pick[precinct] = (n, int(total or 0))
            continue
        cells.setdefault((cid, ballot), {})[precinct] = \
            None if total is None else int(total)
        entry_at[(cid, ballot, precinct)] = len(rows)
        rows.append([precinct, office, district, row_party,
                     'Write-In' if ballot == 'Write-in' else ballot, total])
    for precinct, (n, votes) in bc_pick.items():
        rows.append([precinct, 'Ballots Cast', '', '', '', votes])
    wb.close()

    # apply values recovered from other counties' reports
    for (cid, ballot), manual in MANUAL_CELLS.items():
        precincts = cells.get((cid, ballot))
        if precincts is None:
            continue
        for precinct, v in manual.items():
            if precincts.get(precinct) is None:
                precincts[precinct] = v
                if (cid, ballot, precinct) in entry_at:
                    rows[entry_at[(cid, ballot, precinct)]][5] = v

    # every choice's precinct sum must equal its summary total
    for (cid, ballot), precincts in sorted(
            cells.items(), key=lambda kv: (int(kv[0][0]), kv[0][1])):
        s = sum_choice.get((cid, ballot))
        known = sum(v for v in precincts.values() if v is not None)
        unknown = [p for p, v in precincts.items() if v is None]
        if not unknown:
            if s != known:
                problems.append(f'contest {cid} {ballot}: precinct sum '
                                f'{known} != summary {s}')
            continue
        # Out-of-county split: recover the blank cells as the residual,
        # bounded by the precinct's Ballots Cast for the contest.
        if len(unknown) > 1:
            problems.append(f'contest {cid} {ballot}: blank cells in '
                            f'{len(unknown)} precincts; left as 0')
            continue
        precinct = unknown[0]
        bc = pseudo_contest.get(('Ballots Cast', cid), {}).get(precinct)
        if s is None:
            problems.append(f'contest {cid} {ballot}: blank cell in '
                            f'{precinct} and no summary total; left as 0')
            continue
        residual = s - known
        if residual < 0 or (bc is not None and residual > bc):
            problems.append(f'contest {cid} {ballot}: residual for '
                            f'{precinct} is {residual} (summary {s}, known '
                            f'{known}, BC {bc}); left as 0')
            continue
        precincts[precinct] = residual
        rows[entry_at[(cid, ballot, precinct)]][5] = residual

    # per-contest Ballots Cast: precinct sums vs summary
    for key in sorted(set(sum_pseudo) | set(pseudo_contest),
                      key=lambda kv: (kv[1], kv[0])):
        ballot, cid = key
        precincts = pseudo_contest.get(key, {})
        known = sum(v for v in precincts.values() if v is not None)
        unknown = [p for p, v in precincts.items() if v is None]
        s = sum_pseudo.get(key)
        if unknown and s is not None and len(unknown) == 1:
            # a lone BC blank behaves like a blank choice cell
            residual = s - known
            if residual >= 0:
                precincts[unknown[0]] = residual
                known += residual
        if s != known:
            problems.append(f'{key}: precinct sum {known} != summary {s}')
    return rows, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--us-house-district', type=int, default=None,
                    help='U.S. House district when source titles omit it')
    ap.add_argument('--state-house-district', type=int, default=None,
                    help='State House district when source titles omit it')
    args = ap.parse_args()
    rows, problems = parse(args.source, args.us_house_district,
                           args.state_house_district)
    for p in problems:
        print('PROBLEM:', p)
    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        for precinct, office, district, party, candidate, votes in rows:
            w.writerow([args.county, precinct, office, district, party,
                        candidate, votes])
    print(f'Wrote {len(rows)} rows to {args.out} '
          f'({len(problems)} problems)')


if __name__ == '__main__':
    main()