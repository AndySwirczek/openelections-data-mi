"""Parse CSV-based 2020 primary precinct sources into per-county CSVs.

Sources (openelections-sources-mi/2020/primary):
- Kalamazoo County Aug 2020 Primary Precinct Results.csv (tidy: contest_id,
  contest, precinct, candidate, votes; contest names prefixed DEM/REP)
- Marquette County Aug 2020 Primary Precinct CSVs/contest_<id>.csv (one
  contest per file, precinct rows with candidate columns "Name\nParty",
  Totals row first; contest list in contest_list.tsv)
- Macomb County Aug 2020 Primary Precinct CSVs/contest_<id>.csv (same layout;
  single-precinct contests have no Totals row; contest list in
  contest_list.tsv)

Outputs 2020/counties/20200804__mi__primary__<county>__precinct.csv with the
standard header (county,precinct,office,district,party,candidate,votes).
Registered Voters / Ballots Cast pseudo-rows are emitted where the source
provides those columns (once per precinct, values checked for consistency).
Every contest file's Totals row is checked against the sum of its precinct
rows.
"""
import csv
import os
import re
import sys

SRC = '/Users/dwillis/code/openelections-sources-mi/2020/primary'
OUTDIR = '2020/counties'
DATE = '20200804'
COUNTY_CSV = '2020/20200804__mi__primary__county.csv'

PARTY_MAP = {
    'Democratic': 'DEM',
    'Republican': 'REP',
    'DEM': 'DEM',
    'REP': 'REP',
    'None/Unknown': '',
    'Non-Partisan': '',
    'Nonpartisan': '',
}


PRECINCT_FIXES = (
    # 'Powell Township, Precinct 2' (comma variant used by some contests)
    (', Precinct', ' Precinct'),
    # 'W. Branch Township Precinct 1' abbreviation variant
    ('W. Branch Township', 'West Branch Township'),
    # Macomb: the ElStats database labels Richmond City Precinct 1 as "1A"
    # in some contests (same ballots-cast value, 837, in both forms)
    ('Richmond City Precinct 1A', 'Richmond City Precinct 1'),
)


def normalize_precinct(name):
    p = ' '.join(str(name).split())
    for old, new in PRECINCT_FIXES:
        p = p.replace(old, new)
    return p


# Pseudo rows reported under an aggregate precinct that is also broken out
# into splits (Ishpeming Township Precinct 1 = 1A + 1B): dropping the
# aggregate's turnout rows keeps the per-precinct sums consistent.
DROP_AGGREGATE_PSEUDO = {'Marquette': {'Ishpeming Township Precinct 1'}}


def county_districts(county):
    """{office: single unique district} from the county-level CENR file."""
    out = {}
    with open(COUNTY_CSV, newline='') as fh:
        for row in csv.DictReader(fh):
            if row['county'] == county and row['district']:
                out.setdefault(row['office'], set()).add(row['district'])
    return {o: sorted(d)[0] for o, d in out.items() if len(d) == 1}


def map_office(office, division, county):
    """Map a source office name (+ optional division text) to repo conventions."""
    o = ' '.join(office.split())
    d = ' '.join(division.split()) if division else ''
    if re.match(r'United States Senator', o, re.I):
        return 'U.S. Senate', ''
    if re.match(r'(?:State )?Representative in Congress', o, re.I):
        m = re.search(r'(\d+)', d)
        if m:
            return 'U.S. House', m.group(1)
        single = county_districts(county).get('U.S. House')
        return 'U.S. House', single if single else ''
    if re.match(r'State Representative (\d+)', o, re.I):
        return 'State House', re.search(r'(\d+)', o).group(1)
    if re.match(r'Representative in State Legislature', o, re.I):
        m = re.search(r'(\d+)', d)
        if m:
            return 'State House', m.group(1)
        single = county_districts(county).get('State House')
        return 'State House', single if single else ''
    m = re.match(r'County Commissioner(?: (\d+)[A-Za-z]{0,2} District)?$', o, re.I)
    if m or o.lower() == 'county commissioner':
        num = m.group(1) if m and m.group(1) else None
        if not num and d:
            dm = re.search(r'(\d+)', d)
            num = dm.group(1) if dm else ''
        return 'County Commissioner', num or ''
    m = re.match(r'Judge of Probate Court', o, re.I)
    if m:
        return 'Probate Court Judge', ''
    m = re.match(r'Precinct Delegate', o, re.I)
    if m:
        return 'Precinct Delegate', re.sub(r'^Precinct ', '', d)
    m = re.match(r'Delegate (.+)', o)
    if m:
        return 'Precinct Delegate', m.group(1).strip()
    return o, ''


def write_csv(county, rows):
    os.makedirs(OUTDIR, exist_ok=True)
    slug = re.sub(r'[^a-z0-9]+', '_', county.lower()).strip('_')
    out = os.path.join(OUTDIR, f'{DATE}__mi__primary__{slug}__precinct.csv')
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes'])
        for row in sorted(rows, key=lambda r: (r['precinct'], r['office'], r['district'],
                                               r['party'], r['candidate'])):
            w.writerow([row['county'], row['precinct'], row['office'], row['district'],
                        row['party'], row['candidate'], row['votes']])
    print(f'{county}: wrote {len(rows)} rows to {out}')


def parse_kalamazoo():
    src = os.path.join(SRC, 'Kalamazoo County Aug 2020 Primary Precinct Results.csv')
    rows = []
    with open(src, newline='') as fh:
        for r in csv.DictReader(fh):
            contest = r['contest'].strip()
            m = re.match(r'^(DEM|REP)\s+(.*)$', contest)
            party, office = (m.group(1), m.group(2).strip()) if m else ('', contest)
            office, district = map_office(office, '', 'Kalamazoo')
            rows.append({
                'county': 'Kalamazoo', 'precinct': r['precinct'].strip(),
                'office': office, 'district': district, 'party': party,
                'candidate': 'Write-In' if r['candidate'] == 'Write-in' else r['candidate'].strip(),
                'votes': int(r['votes'].replace(',', '')),
            })
    write_csv('Kalamazoo', rows)


def contest_party(party_text):
    t = party_text.strip()
    if re.match(r'Democratic', t, re.I):
        return 'DEM'
    if re.match(r'Republican', t, re.I):
        return 'REP'
    return ''


def int_val(cell):
    c = cell.strip().replace(',', '')
    if c in ('', '-'):
        return 0
    return int(c)


def parse_contest_file_county(dirname, county):
    """Parse contest_<id>.csv files with a contest_list.tsv mapping."""
    listing = {}
    with open(os.path.join(SRC, dirname, 'contest_list.tsv')) as fh:
        for line in fh:
            cid, party, office, division = line.rstrip('\n').split('\t')
            listing[cid] = (party, office, division)

    rows = []
    errors = []
    notes = []
    pseudo_seen = {}   # (precinct, pseudo_office) -> votes; inconsistent values error
    row_seen = {}      # (precinct, office, district, party, candidate) -> votes so far
    for fname in sorted(os.listdir(os.path.join(SRC, dirname))):
        m = re.match(r'contest_(\d+)\.csv$', fname)
        if not m:
            continue
        cid = m.group(1)
        if cid not in listing:
            errors.append(f'{fname}: no contest-list entry')
            continue
        party_text, office, division = listing[cid]
        cparty = contest_party(party_text)
        office, district = map_office(office, division, county)
        with open(os.path.join(SRC, dirname, fname), newline='') as fh:
            data = [r for r in csv.reader(fh) if any(c.strip() for c in r)]
        if data[0][0].strip() == 'Precinct':
            # normal layout: header row, optional Totals row, one row per precinct
            header = data[0]
            has_totals = len(data) > 1 and data[1][0].strip().lower() == 'totals'
            data_rows = [r for r in (data[2:] if has_totals else data[1:])
                         if r[0].strip().lower() != 'totals']
        else:
            # single-precinct contest (Precinct Delegates): precinct name is in
            # header col 0 ("Precinct <name>"); the Totals row holds the votes
            precinct = normalize_precinct(data[0][0].strip()[len('Precinct '):])
            header = ['Precinct'] + data[0][1:]
            data_rows = [[precinct] + data[1][1:]]

        candidate_cols = []   # (col_index, name, col_party)
        pseudo = {}           # col_index -> pseudo office name
        for i, h in enumerate(header[1:], start=1):
            if re.match(r'Total Votes Cast|Overvotes/Undervotes|Undervotes/Overvotes', h):
                continue
            if h == 'Write-In':
                candidate_cols.append((i, 'Write-In', ''))
            elif h == 'No Nomination':
                continue
            elif h.startswith('Total Ballots Cast'):
                pseudo[i] = 'Ballots Cast'
            elif h == 'Registered Voters':
                pseudo[i] = 'Registered Voters'
            else:
                parts = h.split('\n')
                col_party = PARTY_MAP.get(parts[1].strip(), '') if len(parts) > 1 else ''
                candidate_cols.append((i, parts[0].strip(), col_party))

        if data[0][0].strip() == 'Precinct' and has_totals:
            totals = [int_val(c) for c in data[1][1:]]
            for i, name, _ in candidate_cols:
                got = sum(int_val(r[i]) for r in data_rows)
                if got != totals[i - 1]:
                    errors.append(f'{fname} col {name!r}: row sum {got} != Totals {totals[i - 1]}')

        for r in data_rows:
            if not any(c.strip() for c in r):
                continue  # trailing blank row
            precinct = normalize_precinct(r[0])
            if not precinct:
                errors.append(f'{fname}: blank precinct row')
                continue
            for i, name, col_party in candidate_cols:
                votes = int_val(r[i])
                key = (precinct, office, district, cparty or col_party, name)
                if key in row_seen:
                    if row_seen[key] == votes:
                        # same contest listed twice in the source database
                        notes.append(f'duplicate contest row dropped: {key} = {votes}')
                        continue
                    votes += row_seen[key]
                    notes.append(f'two source contests share {key}; summed to {votes}')
                row_seen[key] = votes
                rows.append({'county': county, 'precinct': precinct, 'office': office,
                             'district': district, 'party': cparty or col_party,
                             'candidate': name, 'votes': votes})
            for i, pseudo_name in pseudo.items():
                pseudo_seen.setdefault((precinct, pseudo_name), []).append(int_val(r[i]))

    # The per-contest exports disagree slightly on turnout for a few precincts
    # (Ishpeming Township); take the most common reported value.
    disagreements = []
    drops = DROP_AGGREGATE_PSEUDO.get(county, set())
    for (precinct, pseudo_name), values in pseudo_seen.items():
        if precinct in drops:
            continue
        if len(set(values)) > 1:
            disagreements.append((precinct, pseudo_name, values))
        rows.append({'county': county, 'precinct': precinct, 'office': pseudo_name,
                     'district': '', 'party': '', 'candidate': '',
                     'votes': max(set(values), key=values.count)})

    if errors:
        for e in errors[:20]:
            print('ERROR', e)
        sys.exit(f'{county}: {len(errors)} errors')
    for n in notes:
        print('NOTE', n)
    for precinct, pseudo_name, values in disagreements:
        print(f'NOTE {precinct}: {pseudo_name} values vary across contests {values}; '
              f'kept {max(set(values), key=values.count)}')
    write_csv(county, rows)


def main():
    which = sys.argv[1:] or ['kalamazoo', 'marquette', 'macomb']
    if 'kalamazoo' in which:
        parse_kalamazoo()
    if 'marquette' in which:
        parse_contest_file_county('Marquette County Aug 2020 Primary Precinct CSVs', 'Marquette')
    if 'macomb' in which:
        parse_contest_file_county('Macomb County Aug 2020 Primary Precinct CSVs', 'Macomb')

if __name__ == '__main__':
    main()