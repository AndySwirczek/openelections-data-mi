"""Parse XLSX-based 2020 primary precinct sources into per-county CSVs.

Sources (openelections-sources-mi/2020/primary):
- Ottawa MI AUG0420_Election_Results.xlsx (tidy: Precinct, Race, Candidate,
  Party, District, Result; party is a DEM/REP suffix on the race name)
- ARENAC_PRIMARY_AUG 20_Official Canvas Results.xlsx (contest-major
  multi-sheet canvass layout)
- Missaukee MI August 2020 Spreadsheet.xlsx (hand-made 'Cong,Leg,Cnty' sheet)

Outputs 2020/counties/20200804__mi__primary__<county>__precinct.csv.
"""
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import map_office, write_csv  # noqa: E402

SRC = '/Users/dwillis/code/openelections-sources-mi/2020/primary'


def parse_ottawa():
    import openpyxl
    wb = openpyxl.load_workbook(os.path.join(SRC, 'Ottawa MI AUG0420_Election_Results.xlsx'),
                                read_only=True)
    rows = list(wb.active.iter_rows(values_only=True))[2:]
    out = []
    for r in rows:
        precinct, race, candidate, party, district, votes = r[:6]
        race = ' '.join(str(race).split())
        if not precinct or not race:
            continue
        if race in ('BALLOTS CAST - TOTAL', 'REGISTERED VOTERS - TOTAL'):
            office = 'Ballots Cast' if race.startswith('BALLOTS') else 'Registered Voters'
            out.append({'county': 'Ottawa', 'precinct': precinct.strip(), 'office': office,
                        'district': '', 'party': '', 'candidate': '', 'votes': int(votes)})
            continue
        m = re.match(r'^(.*?)\s+(DEM|REP)\s*-\s*\d+ Positions?$', race)
        if m:
            office, party = m.group(1).strip(), m.group(2)
        else:
            office, party = race, ''
        office = re.sub(r'\s+', ' ', office)
        office = office.replace('Twp.', 'Township')
        office, dist = map_office(office, '', 'Ottawa')
        if not dist and district:
            dm = re.search(r'(\d+)', str(district))
            dist = dm.group(1) if dm else ''
        out.append({'county': 'Ottawa', 'precinct': precinct.strip(), 'office': office,
                    'district': dist, 'party': party, 'candidate': str(candidate).strip(),
                    'votes': int(votes)})
    write_csv('Ottawa', out)


MISSAUKEE_OFFICES = {
    'US Senator': 'U.S. Senate',
    'Prosecutor': 'Prosecuting Attorney',
    'Sheriff': 'Sheriff',
    'Clerk/Reg': 'County Clerk',
    'Treasurer': 'County Treasurer',
    'Road Comm': 'Road Commissioner',
    'Super': 'Supervisor',
    'Supervisor': 'Supervisor',
    'Clerk': 'Clerk',
    'Treas': 'Treasurer',
    'Trustee': 'Trustee',
    'Trust': 'Trustee',
    'Constable': 'Constable',
    'Road': 'Road',
    'Fire': 'Fire',
    'Operating Millage': 'Operating Millage',
}

PARTY_CODES = {
    'Dem': 'DEM', 'Democratic': 'DEM', 'DEMOCRAT': 'DEM',
    'Rep': 'REP', 'Republican': 'REP', 'REPUBLICAN': 'REP',
}


def _nearest_left(vals, idx):
    for j in range(idx, -1, -1):
        if j < len(vals) and vals[j] not in (None, ''):
            return vals[j]
    return None


def _party_code(p):
    if p is None:
        return ''
    return PARTY_CODES.get(str(p).strip(), '')


def _clean_band(band):
    if band is None:
        return ''
    return ' '.join(str(band).split())


def resolve_office_mi(office_cell, band):
    """(office, district) for a Missaukee column header cell."""
    office = _clean_band(office_cell)
    band = _clean_band(band)
    if band == 'Commissioner Districts':
        m = re.match(r'(\d+)(?:st|nd|rd|th)$', office)
        return 'County Commissioner', (m.group(1) if m else '')
    if office in ('Road', 'Fire', 'Operating Millage'):
        return f'{band} {office} Millage Proposal', ''
    o = MISSAUKEE_OFFICES.get(office, office)
    if o == 'U.S. Senate':
        return 'U.S. Senate', ''
    m = re.match(r'Rep (\d+)(?:st|nd|rd|th) District$', office)
    if m:
        n = int(m.group(1))
        return ('U.S. House', str(n)) if n <= 15 else ('State House', str(n))
    return o, ''


def parse_missaukee():
    import openpyxl
    path = os.path.join(SRC, 'Missaukee MI August 2020 Spreadsheet.xlsx')
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    errors = []
    skipped_votes = []

    def add(precinct, office, district, party, candidate, votes):
        out.append({'county': 'Missaukee', 'precinct': precinct, 'office': office,
                    'district': district, 'party': party, 'candidate': candidate,
                    'votes': int(votes)})

    def fix_precinct(p):
        p = _clean_band(p)
        if p == 'Clam':
            return 'Clam Union'
        return p.replace('W. Branch', 'West Branch')

    # (sheet, band_row, office_row, party_row, cand_row, data_start, precinct_col)
    SPECS = [
        ('Cong,Leg,Cnty', 1, 2, 3, 4, 5, 4),
        ('Comm,Aet,Bl,But', 2, 3, 4, 5, 6, 0),
        ('Cald,Clam,Ent,For', 2, 3, 4, 5, 6, 0),
        ('Hol,Lk,Nor,Pio,Reed,', 2, 3, 4, 5, 6, 0),
        ('Rich,Riv,WB,Proposal', 2, 3, 4, 5, 6, 0),
        ('Proposal Continued', 3, 4, None, 5, 6, 0),
        ('Delegate', 4, None, 5, 6, 8, 0),
    ]
    for sheet, band_row, office_row, party_row, cand_row, data_start, prec_col in SPECS:
        rows = [list(r) for r in wb[sheet].iter_rows(values_only=True)]

        def is_totals(r):
            for c in (0, prec_col):
                if c < len(r) and r[c] and str(r[c]).strip().lower().startswith('total'):
                    return True
            return False

        data = [r for r in rows[data_start:] if not is_totals(r)]
        totals = [r for r in rows[data_start:] if is_totals(r)]

        # candidate columns and their mapping
        cols = []  # (col, office, district, party, candidate)
        for col in range(prec_col + 1, max(len(r) for r in rows)):
            cand = rows[cand_row][col] if col < len(rows[cand_row]) else None
            if cand in (None, ''):
                continue
            if sheet == 'Delegate':
                office, district = 'Precinct Delegate', ''
            else:
                office_cell = _nearest_left(rows[office_row], col) if office_row is not None else None
                band = _clean_band(_nearest_left(rows[band_row], col))
                office, district = resolve_office_mi(office_cell, band)
            party = _party_code(_nearest_left(rows[party_row], col)) if party_row is not None else ''
            cols.append((col, office, district, party, _clean_band(cand)))

        for col, office, district, party, cand in cols:
            got = sum(int(r[col]) for r in data
                      if col < len(r) and isinstance(r[col], (int, float)))
            if totals:
                tot = totals[0][col] if col < len(totals[0]) else None
                if isinstance(tot, (int, float)) and tot != got:
                    errors.append(f'{sheet}: {cand} row sum {got} != Totals {tot}')

        for i, r in enumerate(data):
            precinct = fix_precinct(r[prec_col])
            if not precinct:
                continue
            if r is rows[4] and sheet == 'Cong,Leg,Cnty':
                continue
            for col, office, district, party, cand in cols:
                if col >= len(r):
                    continue
                votes = r[col]
                if not isinstance(votes, (int, float)):
                    if votes not in (None, ''):
                        skipped_votes.append((sheet, precinct, col, votes))
                    continue
                add(precinct, office, district, party, cand, votes)

        if sheet == 'Cong,Leg,Cnty':
            for r in data:
                precinct = fix_precinct(r[4]) if len(r) > 4 else ''
                if not precinct:
                    continue
                if r[1] not in (None, ''):
                    add(precinct, 'Registered Voters', '', '', '', r[1])
                if r[3] not in (None, ''):
                    add(precinct, 'Ballots Cast', '', '', '', r[3])

    for s in skipped_votes:
        print('NOTE skipped unheaded vote cell:', s)
    if errors:
        for e in errors[:20]:
            print('ERROR', e)
        sys.exit(f'Missaukee: {len(errors)} errors')
    write_csv('Missaukee', out)


if __name__ == '__main__':
    which = sys.argv[1:] or ['missaukee']
    if 'ottawa' in which:
        parse_ottawa()
    if 'missaukee' in which:
        parse_missaukee()
