"""Convert the 2020 SOS CENR by-county text file to a county-level CSV.

Source: /Users/dwillis/OpenElections/2020STATE_PRIMARY_MI_CENR_BY_COUNTY.txt
Output: 2020/20200804__mi__primary__county.csv

Same layout as the 2022/2024 CENR exports but with an extra StatusCode column
(4), shifting CountyName to col 6 and CandidateVotes to col 15.  Names are
already Title Case; the middle-name column is meaningful (LAST='Szczepkowski,
Jr.' FIRST='Sigmunt' MIDDLE='John').  W-flag rows are named qualified write-in
candidates, kept as ordinary candidates.

Output header matches the existing 2020 general county file
(candidate before party, no breakdown columns).  Wayne HD 4 had two contests —
the 2-year term and a vacancy partial term — mapped to 'State House' and
'State House Partial Term' as in 2020/20201103__mi__general__county.csv.
"""
import csv
import re
import sys

SOURCE = '/Users/dwillis/OpenElections/2020STATE_PRIMARY_MI_CENR_BY_COUNTY.txt'
OUT = '2020/20200804__mi__primary__county.csv'

# Column positions in the CENR file — same 0-based indices as the 2022 export
# (StatusCode is present in both header and data, so the 18-col layout matches)
COUNTY, OFFICE_DESC, PARTY_DESC, LAST, FIRST, MIDDLE, VOTES = 5, 6, 8, 10, 11, 12, 14

COUNTY_FIX = {'GD. TRAVERSE': 'Grand Traverse'}

PARTY_MAP = {
    'DEMOCRATIC': 'DEM',
    'REPUBLICAN': 'REP',
    'NO AFFILIATION': '',
}

ORDINAL = r'(\d+[A-Z]?)\s*(?:ST|ND|RD|TH)?'
OFFICE_PATTERNS = [
    (re.compile(rf'^{ORDINAL} DISTRICT REPRESENTATIVE IN CONGRESS', re.I), 'U.S. House', True),
    (re.compile(rf'^{ORDINAL} DISTRICT REPRESENTATIVE IN STATE LEGISLATURE', re.I),
     'State House', True),
    (re.compile(r'^[A-Z .]+ JUDGE OF PROBATE COURT', re.I), 'Probate Court Judge', False),
    # '67th District - 5th Division Judge of District Court' -> district '67-5'
    (re.compile(rf'^{ORDINAL} DISTRICT - (\w+) DIVISION JUDGE OF DISTRICT COURT', re.I),
     'District Court Judge', 'division'),
    (re.compile(rf'^{ORDINAL} CIRCUIT JUDGE OF CIRCUIT COURT', re.I), 'Circuit Court Judge', True),
    (re.compile(rf'^{ORDINAL} DISTRICT JUDGE OF DISTRICT COURT', re.I), 'District Court Judge', True),
    (re.compile(r'^UNITED STATES SENATOR', re.I), 'U.S. Senate', False),
]

NUM_WORD = {'FIRST': '1', 'SECOND': '2', 'THIRD': '3', 'FOURTH': '4', 'FIFTH': '5',
            'SIXTH': '6', 'SEVENTH': '7', 'EIGHTH': '8', 'NINTH': '9'}

def titlecase_county(name):
    name = COUNTY_FIX.get(name.strip().upper(), name)
    return ' '.join(w.capitalize() for w in name.split())

def clean_desc(desc):
    desc = re.sub(r'\s*\(RECOUNT\)\s*$', '', desc.strip())
    desc = re.sub(r'\s+Files (In|With) \w+( County)?\s*$', '', desc)
    return ' '.join(desc.split())

def division_num(word):
    """'5th' -> '5'; word forms as a fallback."""
    if word.isdigit():
        return word
    digits = re.sub(r'\D', '', word)
    if digits:
        return digits
    return NUM_WORD[word.upper()]

def map_office(desc):
    m = re.match(rf'^{ORDINAL} DISTRICT REPRESENTATIVE IN STATE LEGISLATURE '
                 r'PARTIAL TERM ENDING (\d\d/\d\d/\d{4})', desc, re.I)
    if m:
        return 'State House Partial Term', m.group(1).upper()
    for pat, office, dist in OFFICE_PATTERNS:
        m = pat.match(desc)
        if not m:
            continue
        if dist == 'division':
            return office, f"{m.group(1)}-{division_num(m.group(2))}"
        if dist is True:
            # keep letter suffixes ('2B', '64B' — distinct district courts)
            return office, m.group(1).upper()
        return office, ''
    return None, None

def candidate_name(last, first, middle):
    return ' '.join(p.strip() for p in (first, middle, last) if p.strip())

def convert(source, out):
    with open(source, newline='') as fh:
        rows = [r + [''] * (18 - len(r)) for r in csv.reader(fh, delimiter='\t')]
    data = [r for r in rows[2:] if r[COUNTY]]  # drop header + 'RECORDS: ...' footer

    out_rows = {}
    unmapped = set()
    unknown_party = set()
    raw_total = 0
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
        raw_total += votes
        key = (county, office, district, candidate, party)
        out_rows[key] = out_rows.get(key, 0) + votes

    if unmapped:
        sys.exit(f'{source}: unmapped offices: {unmapped}')
    if unknown_party:
        sys.exit(f'{source}: unknown parties: {unknown_party}')

    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'office', 'district', 'candidate', 'party', 'votes'])
        for (county, office, district, candidate, party), votes in sorted(out_rows.items()):
            w.writerow([county, office, district, candidate, party, votes])

    csv_total = sum(out_rows.values())
    print(f'Wrote {len(out_rows)} rows to {OUT}')
    print(f'raw CENR total {raw_total} == csv total {csv_total}: {raw_total == csv_total}')

if __name__ == '__main__':
    convert(SOURCE, OUT)