"""Convert the 2022 SOS CENR by-county text files to county-level CSVs.

Sources: /Users/dwillis/OpenElections/2022STATE_PRIMARY_MI_CENR_BY_COUNTY.txt
         /Users/dwillis/OpenElections/2022STATE_GENERAL_MI_CENR_BY_COUNTY.txt
Outputs: 2022/20220802__mi__primary__county.csv
         2022/20221108__mi__general__county.csv

Unlike the 2024 CENR export, 2022 names are already Title Case; the middle-name
column is meaningful (e.g. LAST='Hunt Jr.' FIRST='Eugene' MIDDLE='Rosell').
"""
import csv
import re
import sys

PRIMARY = ('/Users/dwillis/OpenElections/2022STATE_PRIMARY_MI_CENR_BY_COUNTY.txt',
           '2022/20220802__mi__primary__county.csv')
GENERAL = ('/Users/dwillis/OpenElections/2022STATE_GENERAL_MI_CENR_BY_COUNTY.txt',
           '2022/20221108__mi__general__county.csv')

# Column positions in the CENR file
COUNTY, OFFICE_DESC, PARTY_DESC, LAST, FIRST, MIDDLE, VOTES = 5, 6, 8, 10, 11, 12, 14

PARTY_MAP = {
    'DEMOCRATIC': 'DEM',
    'REPUBLICAN': 'REP',
    'NO AFFILIATION': '',  # nonpartisan (judges, proposals)
    'NO PARTY AFFILIATION': '',
    'NON PARTISAN': '',
    'LIBERTARIAN': 'LIB',
    'GREEN': 'GRN',
    'US TAXPAYERS': 'UST',
    'NATURAL LAW': 'NLP',
    'WORKING CLASS': 'WCP',
}

# (regex on cleaned OfficeDescription, office name, has numeric district)
# Descriptions are cleaned first: ' (RECOUNT)' and ' Files In/With <X> County'
# tails stripped, case-folded for matching.
ORDINAL = r'(\d+[A-Z]?)\s*(?:ST|ND|RD|TH)?'
OFFICE_PATTERNS = [
    (re.compile(rf'^{ORDINAL} DISTRICT REPRESENTATIVE IN CONGRESS', re.I), 'U.S. House', True),
    (re.compile(rf'^{ORDINAL} DISTRICT REPRESENTATIVE IN STATE LEGISLATURE', re.I), 'State House', True),
    (re.compile(rf'^{ORDINAL} DISTRICT STATE SENATOR', re.I), 'State Senate', True),
    (re.compile(rf'^{ORDINAL} CIRCUIT JUDGE OF CIRCUIT COURT', re.I), 'Circuit Court Judge', True),
    (re.compile(rf'^{ORDINAL} DISTRICT JUDGE OF COURT OF APPEALS', re.I), 'Court of Appeals Judge', True),
    # '52nd District - 4th Division Judge of District Court' -> district '52-4'
    (re.compile(rf'^{ORDINAL} DISTRICT - (\w+) DIVISION JUDGE OF DISTRICT COURT', re.I),
     'District Court Judge', 'division'),
    (re.compile(rf'^{ORDINAL} DISTRICT JUDGE OF DISTRICT COURT', re.I), 'District Court Judge', True),
    (re.compile(r'^[A-Z .]+ JUDGE OF PROBATE COURT', re.I), 'Probate Court Judge', False),
    (re.compile(r'^GOVERNOR 4 YEAR TERM', re.I), 'Governor', False),
    (re.compile(r'^ATTORNEY GENERAL 4 YEAR TERM', re.I), 'Attorney General', False),
    (re.compile(r'^SECRETARY OF STATE 4 YEAR TERM', re.I), 'Secretary of State', False),
    (re.compile(r'^JUSTICE OF SUPREME COURT', re.I), 'Justice of Supreme Court', False),
    (re.compile(r'^MEMBER OF THE STATE BOARD OF EDUCATION', re.I), 'State Board of Education', False),
    (re.compile(r'^REGENT OF THE UNIVERSITY OF MICHIGAN', re.I), 'Regent of the University of Michigan', False),
    (re.compile(r'^TRUSTEE OF MICHIGAN STATE UNIVERSITY', re.I), 'Trustee of Michigan State University', False),
    (re.compile(r'^GOVERNOR OF WAYNE STATE UNIVERSITY', re.I), 'Governor of Wayne State University', False),
    (re.compile(r'^STATE PROPOSAL - (\d+-\d+):', re.I), 'Proposal', 'proposal'),
]

NUM_WORD = {'FIRST': '1', 'SECOND': '2', 'THIRD': '3', 'FOURTH': '4', 'FIFTH': '5',
            'SIXTH': '6', 'SEVENTH': '7', 'EIGHTH': '8', 'NINTH': '9'}

def division_num(word):
    """'4th' -> '4'; word forms as a fallback."""
    if word.isdigit():
        return word
    digits = re.sub(r'\D', '', word)
    if digits:
        return digits
    return NUM_WORD[word.upper()]

def titlecase_county(name):
    return ' '.join(w.capitalize() for w in name.split())

def clean_desc(desc):
    desc = re.sub(r'\s*\(RECOUNT\)\s*$', '', desc.strip())
    desc = re.sub(r'\s+Files (In|With) \w+( County)?\s*$', '', desc)
    return ' '.join(desc.split())

def map_office(desc):
    for pat, office, dist in OFFICE_PATTERNS:
        m = pat.match(desc)
        if not m:
            continue
        if dist == 'division':
            return office, f"{m.group(1)}-{division_num(m.group(2))}"
        if dist == 'proposal':
            return f'Proposal {m.group(1)}', ''
        if dist is True:
            # keep letter suffixes ('14A', '54B' — distinct district courts)
            return office, m.group(1).upper()
        return office, ''
    return None, None

def candidate_name(last, first, middle):
    return ' '.join(p for p in (first.strip(), middle.strip(), last.strip()) if p)

def convert(source, out):
    with open(source, newline='') as fh:
        rows = [r + [''] * (18 - len(r)) for r in csv.reader(fh, delimiter='\t')]
    data = [r for r in rows[2:] if r[5]]  # drop header + 'RECORDS: ...' footer

    out_rows = {}
    unmapped = set()
    unknown_party = set()
    for r in data:
        desc = clean_desc(r[OFFICE_DESC])
        office, district = map_office(desc)
        if office is None:
            unmapped.add(r[OFFICE_DESC])
            continue
        party_key = ' '.join(r[PARTY_DESC].strip().upper().split())
        party = PARTY_MAP.get(party_key)
        if party is None:
            unknown_party.add(r[PARTY_DESC])
            continue
        candidate = candidate_name(r[LAST], r[FIRST], r[MIDDLE])
        county = titlecase_county(r[COUNTY])
        votes = int(r[VOTES]) if r[VOTES] else 0
        key = (county, office, district, party, candidate)
        out_rows[key] = out_rows.get(key, 0) + votes

    if unmapped:
        sys.exit(f'{source}: unmapped offices: {unmapped}')
    if unknown_party:
        sys.exit(f'{source}: unknown parties: {unknown_party}')

    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'office', 'district', 'party', 'candidate', 'votes'])
        for (county, office, district, party, candidate), votes in sorted(out_rows.items()):
            w.writerow([county, office, district, party, candidate, votes])

    print(f'Wrote {len(out_rows)} rows to {out}')

def main():
    convert(*PRIMARY)
    convert(*GENERAL)

if __name__ == '__main__':
    main()