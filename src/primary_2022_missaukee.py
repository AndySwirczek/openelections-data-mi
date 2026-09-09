"""Parse Missaukee County's 2022 primary hand-built XLSX into a per-county
precinct CSV, verified against the certified county-level CENR.

Source (openelections-sources-mi/2022/primary/): 'Missaukee County Aug 2022
Primary Official Spreadsheet.xlsx' — a hand-built multi-sheet workbook. Only
the first two sheets carry the four offices the CENR certifies (each township
is a single precinct; bare township names, matching the county's 2022 general
file):

- 'Gov,1st,36th': row 4 holds candidate names (Whitmer; Dixon, Kelley,
  Rebandt, Rinke, Soldano, Write-In Adkisson/Blackburn/Craig; Lorinser,
  Bergman, Write-In McDonell; Sheltrown, Hoitenga), rows 7-21 the townships
  and rows 23-24 Lake City/McBain; col E is the precinct name.
- '105th,County,Twp': col A precinct, cols B-F the State House 105 candidates
  (Wojdan DEM; Borton, McFarlin, Morley, Randall REP).

The remaining sheets (precinct delegates, county/township proposals) are
skipped. Candidates are mapped to the CENR names; zero-vote CENR write-in
candidates keep their named rows. Known source-vs-certified residual: the
source's Write-In McDonell column holds 1 vote in Forest Township while the
CENR certifies David John McDonell at 0 (kept source-faithful).

Usage: .venv/bin/python src/primary_2022_missaukee.py [--apply]
"""
import sys
import openpyxl

from primary_2022_common import verify, write

SOURCE = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
          "Missaukee County Aug 2022 Primary Official Spreadsheet.xlsx")

# sheet 'Gov,1st,36th': column index -> (CENR candidate, party)
GOV_SHEET_COLS = {
    5: ('Gretchen Whitmer', 'DEM'), 6: ('Tudor M. Dixon', 'REP'),
    7: ('Ryan D. Kelley', 'REP'), 8: ('Ralph Rebandt', 'REP'),
    9: ('Kevin Rinke', 'REP'), 10: ('Garrett Soldano', 'REP'),
    11: ('Elizabeth Ann Adkisson', 'REP'), 12: ('Justin Paul Blackburn', 'REP'),
    13: ('James Elmer Craig', 'REP'),
    16: ('Bob Lorinser', 'DEM'), 17: ('Jack Bergman', 'REP'),
    18: ('David John McDonell', 'REP'),
    20: ('Joel A. Sheltrown', 'DEM'), 21: ('Michele Hoitenga', 'REP'),
}
# sheet '105th,County,Twp': column index -> (CENR candidate, party)
HOUSE_SHEET_COLS = {
    1: ('Adam J. Wojdan', 'DEM'), 2: ('Ken Borton', 'REP'),
    3: ('Mark McFarlin', 'REP'), 4: ('Kim Morley', 'REP'),
    5: ('Diane Randall', 'REP'),
}
# canvass-vs-certified residual kept source-faithful (see module docstring)
ACCEPTED_RESIDUALS = {('U.S. House', '1', 'REP', 'David John McDonell'): (1, 0)}


def rows_for(sheet, name_col, cols, office, district):
    """Data rows for one sheet's section; skips blanks, header rows and the
    'Totals' row (its cells are formulas)."""
    out = []
    for row in sheet.iter_rows(min_row=1, values_only=True):
        name = row[name_col]
        if not name or str(name).strip().lower() == 'totals':
            continue
        # skip the candidate/header rows (no numeric votes anywhere)
        if not any(str(row[ci]).replace('.', '').replace('-', '').strip().isdigit()
                   for ci in cols if ci < len(row) and row[ci] is not None):
            continue
        name = str(name).strip()
        for ci, (cand, party) in cols.items():
            v = row[ci] if ci < len(row) else None
            if v in (None, '', ' '):
                continue
            votes = int(float(str(v).strip()))
            out.append((name, office, district, party, cand, votes))
    return out


def parse():
    wb = openpyxl.load_workbook(SOURCE, data_only=True, read_only=True)
    gov = wb['Gov,1st,36th']
    house = wb['105th,County,Twp']
    rows = []
    # the Gov sheet covers three contests (Governor cols F-N, U.S. House 1
    # cols Q-S, State Senate 36 cols U-V); split them by column group
    contest_cols = [
        (4, {ci: (c, p) for ci, (c, p) in GOV_SHEET_COLS.items() if ci <= 13},
         'Governor', ''),
        (4, {ci: (c, p) for ci, (c, p) in GOV_SHEET_COLS.items() if ci in (16, 17, 18)},
         'U.S. House', '1'),
        (4, {ci: (c, p) for ci, (c, p) in GOV_SHEET_COLS.items() if ci in (20, 21)},
         'State Senate', '36'),
    ]
    for name_col, cols, office, district in contest_cols:
        rows += rows_for(gov, name_col, cols, office, district)
    rows += rows_for(house, 0, HOUSE_SHEET_COLS, 'State House', '105')
    return rows


def main(apply=False):
    rows = parse()
    print(f'Missaukee: {len(rows)} rows')
    problems = []
    for p in verify('Missaukee', rows, {}):
        residual = next((k for k, (got, _want) in ACCEPTED_RESIDUALS.items()
                         if str(k) in p and f'parsed {got} !=' in p), None)
        if residual:
            print(f'ACCEPTED residual (source-faithful): {p}')
        else:
            problems.append(p)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')
    if apply:
        write('Missaukee', rows)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)