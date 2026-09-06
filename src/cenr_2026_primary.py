"""Convert the 2026 primary SOS CENR by-county text file to a county-level CSV.

Source: /Users/dwillis/OpenElections/2026STATE_PRIMARY_MI_CENR_BY_COUNTY.txt
Output: 2026/20260804__mi__primary__county.csv
"""
import csv
import re
import sys

SOURCE = '/Users/dwillis/OpenElections/2026STATE_PRIMARY_MI_CENR_BY_COUNTY.txt'
OUT = '2026/20260804__mi__primary__county.csv'

# Column positions in the CENR file
COUNTY, OFFICE_DESC, PARTY_DESC, LAST, FIRST, MIDDLE, VOTES = 5, 6, 8, 10, 11, 12, 14

PARTY_MAP = {
    'DEMOCRATIC': 'DEM',
    'REPUBLICAN': 'REP',
    'NO PARTY AFFILIATION': '',  # nonpartisan (judges)
}

ORDINAL = {'ST': '', 'ND': '', 'RD': '', 'TH': ''}

# (regex on OfficeDescription, office name, needs district)
OFFICE_PATTERNS = [
    (re.compile(r'^(\d+)(?:ST|ND|RD|TH) DISTRICT REPRESENTATIVE IN CONGRESS '), 'U.S. House', True),
    (re.compile(r'^(\d+)(?:ST|ND|RD|TH) DISTRICT STATE SENATOR '), 'State Senate', True),
    (re.compile(r'^(\d+)(?:ST|ND|RD|TH) DISTRICT REPRESENTATIVE IN STATE LEGISLATURE '), 'State House', True),
    (re.compile(r'^(\d+)(?:ST|ND|RD|TH) CIRCUIT COURT JUDGE'), 'Circuit Court Judge', True),
    (re.compile(r'^(\d+)(?:ST|ND|RD|TH) DISTRICT COURT'), 'District Court Judge', True),
    (re.compile(r'^OAKLAND COUNTY PROBATE COURT JUDGE'), 'Probate Court Judge', False),
    (re.compile(r'^GOVERNOR '), 'Governor', False),
    (re.compile(r'^UNITED STATES SENATOR '), 'U.S. Senate', False),
]

def titlecase_county(name):
    return ' '.join(w.capitalize() for w in name.split())

def map_office(desc):
    for pat, office, has_district in OFFICE_PATTERNS:
        m = pat.match(desc)
        if m:
            district = str(int(m.group(1))) if has_district else ''
            # 67th District Court Fourth Division (district code 06704)
            if 'FOURTH DIVISION' in desc:
                district += '-4'
            return office, district
    return None, None

def candidate_name(last, first, middle):
    if last.upper() == 'WRITE-IN':
        return 'Write-In'
    return re.sub(r'\s+', ' ', f'{first} {last}').strip()

def main():
    with open(SOURCE, newline='') as fh:
        rows = [r + [''] * (18 - len(r)) for r in csv.reader(fh, delimiter='\t')]
    data = rows[2:]
    # Footer rows like "RECORDS: 2535 / RESULTS: OFFICIAL"
    data = [r for r in data if r[5]]

    out_rows = {}
    unmapped = set()
    for r in data:
        office, district = map_office(r[OFFICE_DESC])
        if office is None:
            unmapped.add(r[OFFICE_DESC])
            continue
        party = PARTY_MAP[r[PARTY_DESC]]
        candidate = candidate_name(r[LAST], r[FIRST], r[MIDDLE])
        county = titlecase_county(r[COUNTY])
        votes = int(r[VOTES]) if r[VOTES] else 0
        key = (county, office, district, party, candidate)
        # WRITE-IN candidates appear with multiple CandidateIDs per county/party; sum them
        out_rows[key] = out_rows.get(key, 0) + votes

    if unmapped:
        sys.exit(f'Unmapped offices: {unmapped}')

    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'office', 'district', 'party', 'candidate', 'votes'])
        for (county, office, district, party, candidate), votes in sorted(out_rows.items()):
            w.writerow([county, office, district, party, candidate, votes])

    print(f'Wrote {len(out_rows)} rows to {OUT}')

if __name__ == '__main__':
    main()