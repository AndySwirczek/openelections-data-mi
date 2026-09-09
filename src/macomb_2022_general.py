"""Patch the Macomb 2022 general precinct CSV from the Macomb Elections Database.

Fixes two problems in 2022/counties/20221108__mi__general__macomb__precinct.csv:

1. Commit 1d6b93e appended Proposal 22-3 rows from the Macomb Elections Database
   that included the 16 township/city AGGREGATE rows (e.g. 'ARMADA TOWNSHIP'
   alongside 'ARMADA TOWNSHIP 1'), double-counting +48,564 Yes / +35,906 No.
   Those aggregate rows are deleted, and the 143 single-digit precinct names
   (the append dropped the zero-padding) are re-padded to match every other
   office in the file.

2. The file was missing entire offices present in the certified CENR: Court of
   Appeals / Circuit / District / Probate judges, Justice of Supreme Court,
   State Board of Education, Regents, MSU Trustees, WSU Governor, and Proposals
   22-1/22-2. Those are appended from the per-contest CSVs downloaded from
   macombmi.elstats2.civera.com (saved in openelections-sources-mi under
   2022/general/Macomb County Nov 2022 General Precinct CSVs/).

Source files: <srcdir>/c<contestId>.csv — one row per precinct, candidate
columns headed '"Name\\nParty"', plus Total Votes Cast / Overvotes-Undervotes /
Total Ballots Cast, and a Totals row first.
"""
import csv
import re
import sys

COUNTY_CSV = '2022/counties/20221108__mi__general__macomb__precinct.csv'
SRC_DIR = ('/Users/dwillis/code/openelections-sources-mi/2022/general/'
           'Macomb County Nov 2022 General Precinct CSVs')

# contest/ballot-question id -> (office, district)
CONTEST_OFFICES = {
    '645': ('Court of Appeals Judge', '2'),
    '646': ('Court of Appeals Judge', '2'),
    '647': ('Circuit Court Judge', '16'),
    '648': ('Circuit Court Judge', '16'),
    '666': ('District Court Judge', '37'),
    '667': ('District Court Judge', '39'),
    '668': ('District Court Judge', '41A'),
    '669': ('District Court Judge', '41B'),
    '670': ('Governor of Wayne State University', ''),
    '671': ('Probate Court Judge', ''),
    '674': ('Regent of the University of Michigan', ''),
    '675': ('State Board of Education', ''),
    '676': ('Justice of Supreme Court', ''),
    '746': ('Trustee of Michigan State University', ''),
    '128': ('Proposal 22-1', ''),
    '129': ('Proposal 22-2', ''),
}

PARTY_MAP = {
    'democratic': 'DEM', 'republican': 'REP', 'libertarian': 'LIB',
    'green': 'GRN', 'u.s. taxpayers': 'UST', 'us taxpayers': 'UST',
    'natural law': 'NLP', 'working class': 'WCP', 'none/unknown': '',
    'nonpartisan': '', 'no party affiliation': '',
}


def db_precinct(name, existing_bases):
    """'Armada Township, Precinct 1' -> 'ARMADA TOWNSHIP 01' (file convention).

    The file keeps the township suffix but is inconsistent for cities
    ('RICHMOND CITY' keeps it, 'CENTER LINE' drops it), so try both.
    """
    m = re.match(r'^(.*),\s*Precinct\s+(\d+[A-Z]?)$', name)
    if not m:
        return None
    db_base, num = m.group(1), m.group(2)
    # 'Richmond City, Precinct 1A' is the same precinct as 'Precinct 1' (same
    # 1,567 ballots; the two ids never co-occur in one contest) — fold to 1
    if re.fullmatch(r'\d+[A-Z]', num):
        num = num[:-1]
    base = re.sub(r'^Village of ', '', db_base).strip().upper()
    for cand in (base, base[:-5].strip() if base.endswith(' CITY') else None):
        if cand and cand in existing_bases:
            return f'{cand} {int(num):02d}' if num.isdigit() else f'{cand} {num}'
    sys.exit(f'unmapped precinct base {db_base!r}')


def parse_source(path):
    """Yield (precinct, [(candidate, party, votes)...], ballots_cast) per data row."""
    rows = list(csv.reader(open(path)))
    header = rows[0]
    cand_cols = []  # (col index, candidate, party)
    ballots_col = None
    for i, h in enumerate(header[1:], 1):
        if h in ('Total Ballots Cast', 'Overvotes/Undervotes', 'Total Votes Cast'):
            if h == 'Total Ballots Cast':
                ballots_col = i
            continue
        parts = h.split('\n')
        cand_cols.append((i, ' '.join(parts[0].split()),
                          ' '.join(parts[1].lower().split()) if len(parts) > 1 else ''))
    for row in rows[1:]:
        if not row or row[0] == 'Totals':
            continue
        cands = [(cand, PARTY_MAP.get(party, party), row[i].replace(',', ''))
                 for i, cand, party in cand_cols]
        bc = row[ballots_col].replace(',', '') if ballots_col else ''
        yield row[0], cands, bc


def main(apply=False):
    with open(COUNTY_CSV, newline='') as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        rows = list(reader)
    known = {r['precinct'] for r in rows}
    known_bases = {re.match(r'^(.*) \S*\d+[A-Z]?$', p).group(1) for p in known
                   if re.match(r'^(.*) \S*\d+[A-Z]?$', p)}

    # 1. drop the 16 aggregate 22-3 rows (name has no trailing digit), and any
    # rows for the offices this script appends (makes reruns idempotent)
    patch_offices = set(CONTEST_OFFICES.values())
    kept = []
    dropped = 0
    for r in rows:
        if r['office'] == 'Proposal 22-3' and not re.search(r'\d$', r['precinct']):
            dropped += 1
            continue
        if (r['office'], r['district']) in patch_offices:
            continue
        kept.append(r)
    # 2. re-pad the unpadded 22-3 precinct names
    renamed = 0
    for r in kept:
        p = r['precinct']
        if r['office'] == 'Proposal 22-3' and re.search(r'(?<!\d)\d$', p):
            base, num = p.rsplit(' ', 1)
            r['precinct'] = f'{base} {int(num):02d}'
            renamed += 1

    # 3. append the missing offices from the database CSVs
    added = []
    ballots_keys = {(r['precinct'], r['office'], r['district']) for r in kept}
    for contest, (office, district) in sorted(CONTEST_OFFICES.items()):
        path = f'{SRC_DIR}/c{contest}.csv'
        for db_prect, cands, ballots in parse_source(path):
            precinct = db_precinct(db_prect, known_bases)
            if precinct not in known:
                sys.exit(f'c{contest}: unmapped precinct {db_prect!r}')
            for cand, party, votes in cands:
                if votes:
                    added.append({'county': 'Macomb', 'precinct': precinct,
                                  'office': office, 'district': district,
                                  'candidate': cand, 'party': party,
                                  'absentee': '', 'election_day': '', 'votes': votes})
            if ballots and (precinct, office, district) not in ballots_keys:
                # 645/646 and 647/648 are two term-types for the SAME seat —
                # Total Ballots Cast is identical, so only keep one row
                ballots_keys.add((precinct, office, district))
                added.append({'county': 'Macomb', 'precinct': precinct,
                              'office': office, 'district': district,
                              'candidate': 'Ballots Cast', 'party': '',
                              'absentee': '', 'election_day': '', 'votes': ballots})

    out = kept + added
    print(f'dropped {dropped} aggregate rows, renamed {renamed}, added {len(added)} rows')
    if apply:
        with open(COUNTY_CSV, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(out)
        print(f'wrote {len(out)} rows to {COUNTY_CSV}')


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)