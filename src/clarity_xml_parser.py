"""Parse Clarity "detail.xml" election result files into precinct CSVs.

Uses the openelections `clarify` package (uv dependency, from GitHub) to read
the detail XML report zip produced by Clarity-based county election sites
(e.g. "Oakland County Aug 2026 Primary Precinct Detail XML.zip" containing
detail.xml). Each Contest carries Choice elements whose VoteType children
hold per-precinct votes by vote method, plus contest-level Undervotes /
Overvotes VoteTypes.

Counties differ in how party and vote method appear, so COUNTY_CONFIG keys
by county token:

- Oakland: contest text "Governor - Dem"; methods Absentee - Local/County,
  Early Voting - Regional/Central, Election (SOVC-style breakdown columns).
- Cass: contest text "Governor (DEM)"; locals "Township Clerk ... for
  Jefferson Township"; methods AV Counting Boards / Early Voting / Election Day.
- Eaton: plain contest text with party on the Choice element (contest texts
  repeat for DEM/REP; multi-seat delegate contests share one text and are
  aggregated); locals "Clerk Partial Term Ending ... - Carmel Township";
  methods Absentee / Early Voting / Election (column names match
  COUNTY_COLUMN_MAP['eaton'] in statewide_generator.py).
- Emmet / Roscommon: contest text "DEM Governor"; locals "DEM Clerk Carp
  Lake Township"; methods Absentee / Early Voting / Election Day (Emmet) or
  Total Votes only (Roscommon). District offices print without a number, so
  districts come from the 2026 CENR county file via DISTRICT_FILL.
- Roscommon also has zero-value REGISTERED VOTERS contests, skipped.
- Roscommon 2024: the export carries no ballotsCast / voterTurnout attributes
  on <VoterTurnout> (only totalVoters, which holds registered voters), so the
  XML is patched before clarify parses it, and --registered-voters emits a
  per-precinct Registered Voters row from the VoterTurnout precincts. Contest
  texts are "DEM Supervisor -- Democratic Au Sable Township" with the
  jurisdiction truncated in the suffix, so the 2024 config resolves local
  offices, delegate precincts and county-commissioner districts from the
  precincts each contest covers, and renames proposals to the county's
  Official List of Proposals (roscommoncounty.net DocumentCenter 2601).

Convention notes:
- Undervotes/Overvotes and regVotersCounty rows are dropped; a Ballots Cast
  row (candidate sum) is emitted per contest per precinct.
- Named write-in choices ("Jane Doe (Write-in)", "John Smith - Write-In")
  keep their name with the suffix stripped; "Write-in"/"Unassigned write-ins"
  /"Rejected write-ins" merge into one Write-In row.
- Local office titles are flipped to jurisdiction-first.
- Rows are aggregated by (precinct, office, district, party, candidate),
  which merges the multi-seat delegate contests that share a contest text.

Usage:
    .venv/bin/python src/clarity_xml_parser.py <detail.xml.zip or detail.xml> \
        --county Oakland \
        --out 2026/counties/20260804__mi__primary__oakland__precinct.csv
"""
import argparse
import csv
import re
import sys
import zipfile

from clarify.parser import Parser

BASE_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

COUNTY_CONFIG = {
    'Oakland': {
        'style': 'suffix_dash',
        'methods': {
            'Absentee - Local': 'absentee_local',
            'Absentee - County': 'absentee_county',
            'Early Voting - Regional': 'early_voting_-_regional',
            'Early Voting - Central': 'early_voting_central',
            'Election': 'election_day',
        },
        'breakdown_order': ['absentee_local', 'early_voting_central', 'absentee_county',
                            'early_voting_-_regional', 'election_day'],
    },
    'Cass': {
        'style': 'suffix_tag',
        'methods': {
            'AV Counting Boards': 'absentee',
            'Early Voting': 'early_voting',
            'Election Day': 'election_day',
        },
        'breakdown_order': ['absentee', 'early_voting', 'election_day'],
    },
    'Eaton': {
        'style': 'choice_party',
        'methods': {
            'Absentee': 'absentee',
            'Early Voting': 'early_voting',
            'Election': 'election',
        },
        'breakdown_order': ['election', 'early_voting', 'absentee'],
    },
    # Eaton Aug 2024 primary: same style as 2026, but the bare office words
    # ("Supervisor", "Clerk", ...) stay bare — the county's own general-file
    # convention — and only the delegate contests (all bare "Delegate to
    # County Convention", one per precinct) get their office built from
    # coverage.
    'Eaton 2024': {
        'style': 'choice_party',
        'methods': {
            'Absentee': 'absentee',
            'Early Voting': 'early_voting',
            'Election': 'election',
        },
        'breakdown_order': ['election', 'early_voting', 'absentee'],
        'coverage_delegates': True,
    },
    'Emmet': {
        'style': 'prefix',
        'methods': {
            'Absentee': 'absentee',
            'Early Voting': 'early_voting',
            'Election Day': 'election_day',
        },
        'breakdown_order': ['election_day', 'absentee', 'early_voting'],
        'district_fill': {'U.S. House': '1', 'State Senate': '37', 'State House': '107'},
    },
    'Roscommon': {
        'style': 'prefix',
        'methods': {
            'Total Votes': None,  # no vote-method breakdown in this file
            'regVotersCounty': None,
        },
        'breakdown_order': [],
        'district_fill': {'U.S. House': '1', 'State Senate': '36', 'State House': '105'},
        'bare_townships': True,  # "Supervisor Denton" -> "Denton Township Supervisor"
    },
    # Roscommon 2024: dash-suffix contest texts ("DEM Supervisor -- Democratic
    # Au Sable Township") with the suffix jurisdiction truncated, bare office
    # words whose jurisdiction (or commissioner district) must come from the
    # precincts the contest covers, and truncated proposal titles.
    'Roscommon 2024': {
        'county_name': 'Roscommon',
        'style': 'prefix',
        'methods': {
            'Total Votes': None,
            'regVotersCounty': None,
        },
        'breakdown_order': [],
        'district_fill': {'U.S. House': '1', 'State House': '105'},
        'coverage_jurisdictions': True,
        # County commissioner districts, from the precincts each contest
        # covers (Denton 1 is split between districts 3 and 5).
        'commissioner_districts': {
            frozenset(['Denton Township, Precinct 1', 'Denton Township, Precinct 2']): '5',
            frozenset(['Lake Township, Precinct 1', 'Lyon Township, Precinct 1',
                       'Markey Township, Precinct 1']): '1',
            frozenset(['Gerrish Township, Precinct 1',
                       'Higgins Township, Precinct 1']): '2',
            frozenset(['Au Sable Township, Precinct 1', 'Backus Township, Precinct 1',
                       'Denton Township, Precinct 1',
                       'Richfield Township, Precinct 1']): '3',
            frozenset(['Nester Township, Precinct 1', 'Roscommon Township, Precinct 1',
                       'Roscommon Township, Precinct 2']): '4',
        },
        # Source titles (truncated) -> titles from the county's Official List
        # of Proposals for the August 6, 2024 primary.
        'proposal_titles': {
            'Animal Shelter/Animal Control Program Roscommon County':
                'Roscommon County Animal Shelter/Animal Control Program '
                'Millage Proposal',
            'County Capital Improvement and Maintenance Roscommon Cou':
                'Roscommon County Capital Improvement and Maintenance '
                'Millage Proposal',
            'RCEDC and MSUE/4-H Program Millage Roscommon County':
                'Roscommon Economic Development Committee and MSU '
                'Extension/4-H Program Millage Proposal',
            'Denton Twp Ambulance Services Denton Township':
                'Denton Township Ambulance Services Millage Increase Proposal',
            'Denton Twp Streetlight Millage Denton Township':
                'Denton Township Streetlight Operation and Maintenance '
                'Millage Renewal Proposal',
            'Denton Twp Gen Operating Millage Denton Township':
                'Denton Township Allocated Voted General Operating Millage '
                'Renewal Proposal',
            'Gerrish Township Bond Proposal Gerrish Township':
                'Gerrish Township Bond Proposal',
            'Road Maintenance and Improvement Millage Renewal Markey':
                'Markey Township Road Maintenance and Improvement Millage '
                'Renewal Proposal',
            'Roscommon Twp Fire Dept Operating Roscommon Township':
                'Roscommon Township Fire Department Operating Millage '
                'Renewal Proposal',
        },
    },
    # Macomb: prefix style with numbered districts, XML-style precinct labels
    # kept verbatim ("Armada Twp, Pct 1" — same as its 2024 file), write-in
    # choices printed as "Unofficial Write-in", judge title
    # "Judge of Circuit Court - 16th Circuit Non-Incumbent".
    'Macomb': {
        'style': 'prefix',
        'methods': {
            'Absentee': 'absentee',
            'Early Voting': 'early_voting',
            'Election Day': 'election_day',
        },
        'breakdown_order': ['election_day', 'absentee', 'early_voting'],
    },
}

PARTY_SUFFIX_DASH = {'Dem': 'DEM', 'Rep': 'REP', 'Lib': 'LIB', 'Grn': 'GRN', 'Ust': 'UST'}
# Vote-type-like contest texts that carry no results; skip them.
SKIP_CONTESTS = re.compile(r'^REGISTERED VOTERS', re.I)
WRITE_IN_ROWS = {'unassigned write-ins', 'rejected write-ins', 'write-in',
                 'unofficial write-in'}
NAMED_WRITE_IN = re.compile(r'(?: \((?:Write-in|Write-In)\)| - (?:Write-in|Write-In))$')
DISTRICT_PATTERNS = [
    (re.compile(r'^Representative in Congress (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'U.S. House'),
    (re.compile(r'^State Senator (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'State Senate'),
    (re.compile(r'^Representative in State Legislature (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'State House'),
]
# District offices printed without a district number; fill from CENR.
DISTRICT_OFFICES = {
    'Representative in Congress': 'U.S. House',
    'State Senator': 'State Senate',
    'Representative in State Legislature': 'State House',
}
OFFICE_EXACT = {
    'Governor': 'Governor',
    'United States Senator': 'U.S. Senate',
}
# Judicial contests mapped to the SOS/CENR naming convention used in the
# county-level consolidated file (e.g. "Circuit Court Judge", district "6";
# "District Court Judge" district "67-4").
JUDGE_PATTERNS = [
    # Macomb prints "Judge of Circuit Court - 16th Circuit Non-Incumbent"
    # (dash before the circuit, no "Position" suffix).
    (re.compile(r'^Judge of Circuit Court\s*-?\s*(\d+)(?:st|nd|rd|th) Circuit'
                r'(?: Non-Incumbent(?: Position)?)?$'),
     'Circuit Court Judge'),
    (re.compile(r'^Judge of Probate Court(?: Non-Incumbent Position)?$'),
     'Probate Court Judge'),
    (re.compile(r'^Judge of District Court (\d+)(?:st|nd|rd|th) District'
                r'(?:, (\d+)(?:st|nd|rd|th) Division)?(?: Non-Incumbent Position)?$'),
     'District Court Judge'),
]
# Local office types appearing first in a title ("Clerk Oakland Township ...").
OFFICE_TYPES = ('Supervisor', 'Clerk', 'Treasurer', 'Trustee', 'Constable',
                'Park Commissioner', 'Drain Commissioner', 'Prosecuting Attorney',
                'County Road Commissioner', 'Council Member', 'Mayor', 'President',
                'Judge of Probate')
# Trailing term clause kept in place when flipping office-first titles.
TERM_SUFFIX = re.compile(r'(Term Ending .*)$')
# Bare office words that turn out to be county-wide once the covered
# precincts show the contest is not a single township's (Roscommon 2024).
COUNTY_LOCAL_OFFICES = {
    'Clerk & Register of Deeds': 'County Clerk and Register of Deeds',
    'Treasurer': 'County Treasurer',
    'Sheriff': 'County Sheriff',
    'Prosecuting Attorney': 'County Prosecuting Attorney',
    'Drain Commissioner': 'County Drain Commissioner',
}
ORDINALS = {1: 'st', 2: 'nd', 3: 'rd'}


def ordinal(n):
    return (f'{n}{ORDINALS.get(n % 10 if n % 100 not in (11, 12, 13) else 0, "th")}')


def strip_party(text, style, choice_party=None):
    """Return (office_title, party) for a contest text under a county style."""
    if style == 'suffix_tag':
        m = re.search(r' \((DEM|REP|LIB|GRN|UST)\)$', text)
        if m:
            return text[:m.start()], m.group(1)
    elif style == 'suffix_dash':
        m = re.search(r' - (\w+)$', text)
        if m and m.group(1) in PARTY_SUFFIX_DASH:
            return text[:m.start()], PARTY_SUFFIX_DASH[m.group(1)]
    elif style == 'prefix':
        m = re.match(r'(DEM|REP|LIB|GRN|UST) (.*)$', text)
        if m:
            title = m.group(2)
            # "DEM Supervisor -- Democratic Au Sable Township" (Roscommon
            # 2024): drop the repeated " -- <Party adjective> <jurisdiction>"
            # suffix; the jurisdiction is truncated there anyway.
            m2 = re.match(r'(.*) -- (?:Democratic|Republican|Libertarian|'
                          r'Green|U.S. Taxpayers|Nonpartisan) .*$', title)
            if m2:
                title = m2.group(1).strip()
            return title, m.group(1)
    elif style == 'choice_party':
        return text, choice_party or ''
    return text, ''


def map_office(title, config):
    """Map a party-stripped contest title to (office, district)."""
    for pat, name in JUDGE_PATTERNS:
        m = pat.match(title)
        if m:
            office = name
            groups = m.groups()
            d1 = groups[0] if groups else None
            d2 = groups[1] if len(groups) > 1 else None
            return office, (f'{d1}-{d2}' if d1 and d2 else (d1 or ''))
    for pat, name in DISTRICT_PATTERNS:
        m = pat.match(title)
        if m:
            return name, m.group(1) or m.group(2)
    if title in DISTRICT_OFFICES:
        fill = config.get('district_fill', {})
        return DISTRICT_OFFICES[title], fill.get(DISTRICT_OFFICES[title], '')
    office = OFFICE_EXACT.get(title)
    if office:
        return office, ''
    # "Clerk & Register of Deeds" (Roscommon 2024) must not be flipped
    # office-first; its county/township resolution happens from coverage.
    if title == 'Clerk & Register of Deeds':
        return title, ''

    # "Township Clerk Partial Term Ending ... for Jefferson Township" (Cass)
    m = re.match(r'^(?:Township )?(%s) (Partial Term Ending .*?) for (.+)$' % '|'.join(OFFICE_TYPES), title)
    if m:
        jur = m.group(3).strip()
        return f'{jur} {m.group(1)} {m.group(2)}'.replace('  ', ' '), ''
    # "Clerk Partial Term Ending ... - Carmel Township" (Eaton)
    m = re.match(r'^(%s) (Partial Term Ending .*?) ?- ?(.+)$' % '|'.join(OFFICE_TYPES), title)
    if m:
        jur = re.sub(r'(?<=[a-z])Township', ' Township', m.group(3).strip())
        return f'{jur} {m.group(1)} {m.group(2)}'.replace('  ', ' '), ''
    # "Delegate to County Convention for/‑/  <jurisdiction>" (all styles)
    m = re.match(r'^Delegate to County Convention\s*(?:for|-)?\s*(.+)$', title)
    if m:
        return f'{m.group(1).strip()} Delegate to County Convention', ''
    # "<Office> <Jurisdiction>[ Term Ending ...]" (prefix style, Oakland-style
    # office-first titles): flip to jurisdiction-first, keeping the term clause.
    m = re.match(r'^(%s) (.+)$' % '|'.join(OFFICE_TYPES), title)
    if m:
        jur = m.group(2).strip()
        suffix = ''
        m2 = TERM_SUFFIX.search(jur)
        if m2:
            suffix = ' ' + m2.group(1)
            jur = jur[:m2.start()].strip()
        if config.get('bare_townships') and not re.search(r'(Township|Village|City|County)$', jur):
            jur += ' Township'
        return f'{jur} {m.group(1)}{suffix}'.replace('  ', ' '), ''
    # Anything else (proposals, county-wide offices) stays as printed.
    return title, ''


def map_contest(text, style, choice_party, config):
    """Return (office, district, party) for a contest."""
    if SKIP_CONTESTS.match(text):
        return None
    title, party = strip_party(text, style, choice_party)
    title = re.sub(r'\s+', ' ', title).strip()
    office, district = map_office(title, config)
    return office, district, party


VOTER_TURNOOUT_TAG = re.compile(r'<VoterTurnout\b[^>]*>')


def load_xml(path):
    """Return detail.xml contents, patching exports that lack ballotsCast.

    Roscommon 2024 writes <VoterTurnout totalVoters="23917"> — clarify indexes
    .values()[1] and [2] for ballotsCast and voterTurnout, so add zeros; the
    totalVoters attribute there actually holds registered voters, which the
    precinct rows keep regardless.
    """
    if path.endswith('.zip'):
        with zipfile.ZipFile(path) as archive:
            contents = archive.read('detail.xml').decode()
    else:
        with open(path, encoding='utf8') as fh:
            contents = fh.read()
    m = VOTER_TURNOOUT_TAG.search(contents)
    if m and 'ballotsCast' not in m.group(0):
        contents = (contents[:m.start()] +
                    m.group(0)[:-1] + ' ballotsCast="0" voterTurnout="0.0">' +
                    contents[m.end():])
    return contents


def parse(path, county, registered_voters=False):
    config = COUNTY_CONFIG[county]
    county = config.get('county_name', county)
    style = config['style']
    methods = config['methods']
    breakdown_order = config['breakdown_order']
    parser = Parser()
    parser.parse(load_xml(path))

    out_rows = []
    problems = []
    for contest in parser.contests:
        first_party = next((c.party for c in contest.choices if c.party), None)
        mapped = map_contest(contest.text, style, first_party, config)
        if mapped is None:
            continue
        office, district, party = mapped
        names = {c.text.lower() for c in contest.choices}
        if names <= {'yes', 'no'}:
            party = ''
        # Per-precinct per-choice method breakdown.
        precinct_votes = {}  # precinct -> {choice: {method: votes}}
        for choice in contest.choices:
            for r in choice.results:
                if r.jurisdiction is None:
                    continue
                if r.vote_type not in methods:
                    continue  # e.g. regVotersCounty
                cell = precinct_votes.setdefault(r.jurisdiction.name, {})
                tally = cell.setdefault(choice, {})
                tally[r.vote_type] = tally.get(r.vote_type, 0) + r.votes
        # Roscommon 2024: bare titles whose jurisdiction (or county-
        # commissioner district, or delegate precinct) is only implied by the
        # precincts the contest covers; proposals carry truncated titles.
        if config.get('coverage_jurisdictions') and precinct_votes:
            covered = sorted(precinct_votes)
            title = office
            if title in config.get('proposal_titles', {}):
                office = config['proposal_titles'][title]
            elif title == 'County Commissioner':
                district = config.get('commissioner_districts', {}) \
                    .get(frozenset(covered))
                if district:
                    office = f'County Commissioner {ordinal(int(district))} District'
                else:
                    problems.append(f'{contest.text}: no district for '
                                    f'covered precincts {covered}')
            elif title == 'Delegate to County Convention':
                if len(covered) == 1:
                    office = f'{covered[0]} Delegate to County Convention'
                else:
                    problems.append(f'{contest.text}: delegate contest covers '
                                    f'{len(covered)} precincts: {covered}')
            elif title in OFFICE_TYPES or title in COUNTY_LOCAL_OFFICES:
                townships = {re.split(r', Precinct', p)[0] for p in covered}
                if len(townships) == 1:
                    office = f'{townships.pop()} {title}'
                else:
                    office = COUNTY_LOCAL_OFFICES.get(title, title)
        elif config.get('coverage_delegates') and precinct_votes \
                and office == 'Delegate to County Convention':
            # Eaton 2024: one bare delegate contest per precinct; the office
            # names the precinct it covers.
            covered = sorted(precinct_votes)
            if len(covered) == 1:
                office = f'{covered[0]} Delegate to County Convention'
            else:
                problems.append(f'{contest.text}: delegate contest covers '
                                f'{len(covered)} precincts: {covered}')
        # Delegate contests occasionally carry a wrong precinct label in the
        # contest text (Eaton 2026: all four Grand Ledge contests say
        # "Precinct 1"); when the contest covers a single precinct whose label
        # differs, use that precinct's label in the office.
        if 'Delegate to County Convention' in office and precinct_votes:
            nonzero = [p for p, cell in precinct_votes.items()
                       if any(tally.values() for tally in cell.values())]
            targets = nonzero or list(precinct_votes)
            if len(targets) == 1:
                m = re.search(r'\bPrecinct ?[\w-]+', office)
                if m and m.group(0) not in targets[0]:
                    office = f'{targets[0]} Delegate to County Convention'
        # Undervotes/Overvotes aggregates verify against precinct sums.
        for r in contest.results:
            if r.choice is not None or r.jurisdiction is not None:
                continue
            if r.vote_type not in ('Undervotes', 'Overvotes'):
                continue
            total = sum(rr.votes for rr in contest.results
                        if rr.choice is None and rr.jurisdiction is not None
                        and rr.vote_type == r.vote_type)
            if r.votes != total:
                problems.append(f'{contest.text}: {r.vote_type} aggregate {r.votes} != precinct sum {total}')

        # Choice aggregates verify against the precinct sums.
        for choice in contest.choices:
            agg = sum(r.votes for r in choice.results if r.jurisdiction is None)
            per_prec = sum(sum(tally.values()) for tally in
                           (cell.get(choice, {}) for cell in precinct_votes.values()))
            if agg != per_prec:
                problems.append(f'{contest.text} / {choice.text}: totalVotes {agg} != precinct sum {per_prec}')

        def emit(candidate, tally):
            vals = []
            for col in breakdown_order:
                total = sum(v for vt, v in tally.items() if methods[vt] == col)
                vals.append(total)
            out_rows.append([county, precinct, office, district, party, candidate,
                             sum(tally.values()), *vals])

        for precinct in sorted(precinct_votes):
            cell = precinct_votes[precinct]
            # Named candidates.
            for choice in contest.choices:
                name = choice.text
                if name.lower() in WRITE_IN_ROWS:
                    continue
                m = NAMED_WRITE_IN.search(name)
                if m:
                    name = name[:m.start()]
                emit(name, cell.get(choice, {}))
            # Merged write-ins.
            wi_tally = {}
            for choice in contest.choices:
                if choice.text.lower() in WRITE_IN_ROWS:
                    for vt, v in cell.get(choice, {}).items():
                        wi_tally[vt] = wi_tally.get(vt, 0) + v
            emit('Write-In', wi_tally)
            # Ballots Cast = candidate sum.
            bc_tally = {}
            for choice, tally in cell.items():
                if choice.text.lower() in WRITE_IN_ROWS:
                    continue
                for vt, v in tally.items():
                    bc_tally[vt] = bc_tally.get(vt, 0) + v
            emit('Ballots Cast', bc_tally)

    # Aggregate rows sharing a key (multi-seat delegate contests share a text).
    agg = {}
    order = []
    for row in out_rows:
        key = tuple(row[:6])
        if key not in agg:
            agg[key] = list(row)
            order.append(key)
        else:
            for i in range(6, len(row)):  # votes and breakdown columns
                agg[key][i] += row[i]
    out_rows = [agg[k] for k in order]

    # Cross-check candidate rows against the Choice aggregates.
    if registered_voters:
        rv_rows = [[county, j.name, 'Registered Voters', '', '', '',
                    j.total_voters] for j in parser.result_jurisdictions]
        out_rows = rv_rows + out_rows
    return out_rows, problems, parser


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path', help='detail.xml zip or XML file')
    ap.add_argument('--county', required=True, choices=sorted(COUNTY_CONFIG))
    ap.add_argument('--out', required=True)
    ap.add_argument('--registered-voters', action='store_true',
                    help='emit a per-precinct Registered Voters row '
                         '(from VoterTurnout precincts)')
    args = ap.parse_args()

    out_rows, problems, parser = parse(args.path, args.county,
                                       args.registered_voters)
    config = COUNTY_CONFIG[args.county]
    header = BASE_HEADER + config['breakdown_order']
    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(out_rows)
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()