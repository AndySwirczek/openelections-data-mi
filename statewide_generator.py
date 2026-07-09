import argparse
import csv
import glob
import os

OFFICE_WHITELIST = [
    'Straight Party', 'President', 'Governor', 'Secretary of State',
    'Railroad Commissioner', 'State Auditor', 'Auditor General', 'State Treasurer',
    'Commissioner of Agriculture & Commerce', 'Commissioner of Insurance',
    'Attorney General', 'U.S. House', 'State Senate', 'State House', 'U.S. Senate',
    'House of Delegates', 'State Representative', 'Registered Voters',
    'Ballots Cast', 'Ballots Cast Blank', 'Proposal 22-3',
]

OUTPUT_HEADER = [
    'county', 'precinct', 'office', 'district', 'candidate', 'party', 'votes',
    'election_day', 'absentee', 'av_counting_boards', 'early_voting', 'mail',
    'provisional', 'pre_process_absentee',
]

BREAKDOWN_COLS = OUTPUT_HEADER[7:]

# County-specific column mappings for counties whose source files use
# non-standard header names for the vote-method breakdown columns, or split
# a breakdown column (e.g. absentee) across multiple source columns that
# must be summed. Keys are the county token as it appears in the source
# filename (…__mi__general__<county>__precinct.csv).
COUNTY_COLUMN_MAP = {
    'baraga': {
        'election_day': ['election_day'],
        'av_counting_boards': ['av_counting_boards'],
        'early_voting': ['early_votes'],
    },
    'eaton': {
        'election_day': ['election'],
        'early_voting': ['early_voting'],
        'absentee': ['absentee'],
    },
    'grand_traverse': {
        'election_day': ['election_day'],
        'early_voting': ['early'],
        'absentee': ['absentee'],
    },
    'muskegon': {
        'election_day': ['election_day'],
        'absentee': ['absentee'],
        # ev_local/ev_county are a redundant sub-breakdown of early_voting
        # (they sum to it exactly) and are intentionally not used here.
        'early_voting': ['early_voting'],
    },
    'oakland': {
        'election_day': ['election_day'],
        'absentee': ['absentee_local', 'absentee_county'],
        'early_voting': ['early_voting_central', 'early_voting_-_regional'],
    },
}


def county_key_from_path(path):
    # …/<election>__mi__general__<county>__precinct.csv
    return os.path.basename(path).split('__')[3]


def build_breakdown(row, county_key):
    out = {c: '' for c in BREAKDOWN_COLS}
    mapping = COUNTY_COLUMN_MAP.get(county_key)
    if mapping:
        for out_col, source_cols in mapping.items():
            present = [c for c in source_cols if c in row and row[c] != '']
            if not present:
                continue
            if len(present) == 1:
                out[out_col] = row[present[0]]
            else:
                out[out_col] = str(sum(int(row[c]) for c in present))
    else:
        for c in BREAKDOWN_COLS:
            if c in row:
                out[c] = row[c]
    return out


def generate_consolidated_file(year, election):
    county_dir = os.path.join(year, 'counties')
    pattern = os.path.join(county_dir, f'{election}*precinct.csv')
    rows = []
    for path in sorted(glob.glob(pattern)):
        county_key = county_key_from_path(path)
        with open(path) as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row['office'].strip() not in OFFICE_WHITELIST:
                    continue
                breakdown = build_breakdown(row, county_key)
                rows.append({
                    'county': row['county'], 'precinct': row['precinct'],
                    'office': row['office'], 'district': row['district'],
                    'candidate': row['candidate'], 'party': row['party'],
                    'votes': row['votes'], **breakdown,
                })

    # Only keep breakdown columns that at least one row actually populates,
    # so a year with no vote-method detail (e.g. 2020) keeps its narrower
    # historical schema instead of gaining columns that are empty everywhere.
    used_breakdown_cols = [c for c in BREAKDOWN_COLS if any(r[c] for r in rows)]
    header = OUTPUT_HEADER[:7] + used_breakdown_cols

    output_file = os.path.join(year, f'{election}__mi__general__precinct.csv')
    with open(output_file, 'w', newline='') as csv_outfile:
        writer = csv.writer(csv_outfile)
        writer.writerow(header)
        for r in rows:
            writer.writerow([r[c] for c in header])
    return output_file, len(rows)


def parse_args():
    parser = argparse.ArgumentParser(
        description='Regenerate a statewide precinct-level general election CSV from county files.')
    parser.add_argument('year', help='election year directory, e.g. 2024')
    parser.add_argument('election', help='election date in YYYYMMDD format, e.g. 20241105')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    output_file, count = generate_consolidated_file(args.year, args.election)
    print(f'Wrote {count} rows to {output_file}')
