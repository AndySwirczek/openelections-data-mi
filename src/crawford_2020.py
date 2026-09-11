"""Parse the Crawford County Aug 2020 primary hand-made PDF into a precinct CSV.

Source: 'Crawford MI election082011-FINAL.pdf' (openelections-sources-mi/2020/primary)
— an Excel-exported, contest-major sheet with one value per precinct column.
Columns are precinct codes with a legend on the last page:

    101 Beaver Creek Twp   201 Frederic Twp   302/304/305/306 Grayling Twp
    401 Lovells Twp        501 Maple Forest Twp   601 South Branch Twp
    701 Grayling City      BLT Bear Lake Twp (CASD district only)

Quirks handled:
- '*' cells mark which precincts a township-scoped contest applies to; the
  values themselves are typed into those columns (used as-is).
- Delegate contests span two lines ('Delegates to the' / 'County Convention
  (N)'); emitted as Precinct Delegate with the township as district.
- Rows whose only number is a Total of 0 (unopposed 5th Commissioner district,
  an unnamed 'R' row for South Branch delegates) produce no vote rows.
- Some rows have inconsistent Totals (Carol Raybuck 352 vs printed Total 0,
  Brandon Gabriel 177 with no Total, Lovells Supervisor office row Total 0);
  candidate-row sums are validated and mismatches reported.

Usage: .venv/bin/python src/crawford_2020.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import write_csv  # noqa: E402

SRC = '/Users/dwillis/code/openelections-sources-mi/2020/primary/Crawford MI election082011-FINAL.pdf'

# code -> precinct name (Grayling Township precinct numbers from the code)
COL_NAMES = {
    '101': 'Beaver Creek Township Precinct 1',
    '201': 'Frederic Township Precinct 1',
    '302': 'Grayling Township Precinct 2',
    '304': 'Grayling Township Precinct 4',
    '305': 'Grayling Township Precinct 5',
    '306': 'Grayling Township Precinct 6',
    '401': 'Lovells Township Precinct 1',
    '501': 'Maple Forest Township Precinct 1',
    '601': 'South Branch Township Precinct 1',
    '701': 'City of Grayling Precinct 1',
    'BLT': 'Bear Lake Township Precinct 1',
}

TOWNSHIP_RE = re.compile(
    r'^(Beaver Creek|Frederic|Grayling|Lovells|Maple Forest|South Branch) '
    r'Township$|^(Grayling City)$|^Maple Forest$')
SECTION_RE = re.compile(
    r'^(Congressional|Legislative|County|County Commissioner|County Proposals|'
    r'Township Proposals|Abbreviations)$')

OFFICE_NAMES = {
    'Prosecuting Attorney': 'Prosecuting Attorney',
    'Clerk/Register of Deeds': 'County Clerk/Register of Deeds',
    'Treasurer': 'County Treasurer',
    'Road Commissioner': 'Road Commissioner',
    'Treasuer': 'Treasurer',                    # sheet typo
}


def rows_from_pdf(path):
    import natural_pdf
    pdf = natural_pdf.PDF(path)
    rows = []
    for page in pdf.pages:
        grouped = {}
        for e in page.find_all('text'):
            t = e.extract_text()
            if t is None or t.strip() == '':
                continue
            key = round(e.top)
            for k in grouped:
                if abs(k - key) <= 2:
                    key = k
                    break
            grouped.setdefault(key, []).append((e.x0, e.x1, t.strip()))
        for top in sorted(grouped):
            rows.append(sorted(grouped[top]))
    return rows


def main():
    rows = rows_from_pdf(SRC)

    # column centers from the header row ('101' ... 'Total')
    centers = {}
    for row in rows:
        texts = [t for _, _, t in row]
        if '101' in texts and 'Total' in texts:
            for x0, x1, t in row:
                if re.match(r'^(101|201|302|304|305|306|401|501|601|701|BLT|Total)$', t):
                    centers[t] = (x0 + x1) / 2
            break
    if len(centers) != 12:
        sys.exit(f'header row not found: {centers}')

    def col_of(x0, x1):
        c = (x0 + x1) / 2
        best = min(centers, key=lambda k: abs(centers[k] - c))
        if abs(centers[best] - c) > 14:
            return None
        return best

    out = []
    notes = []
    errors = []
    township = None        # current township context
    pending_delegate = False
    office = None          # (office, district, party)
    in_offices = False     # inside a township office block
    proposal_mode = False

    def emit(precinct, o, d, party, cand, votes, cell):
        out.append({'county': 'Crawford', 'precinct': precinct, 'office': o,
                    'district': d, 'party': party, 'candidate': cand,
                    'votes': votes, '_cell': cell})

    for row in rows:
        left = ' '.join(t for x0, x1, t in row if x1 < 210)
        party = ''
        for x0, x1, t in row:
            if 210 <= x0 <= 221 and t.lower() in ('d', 'r'):
                party = t.upper()
        cells = {}
        for x0, x1, t in row:
            c = col_of(x0, x1)
            if c:
                cells[c] = t

        if left == '' and not party and all(v == '*' for v in cells.values() if v != ''):
            continue                      # asterisk-only applicability row
        if not left and not party and not cells:
            continue

        # pseudo rows
        if left.startswith('Total Registered Voters'):
            for c, v in cells.items():
                if c != 'Total':
                    emit(COL_NAMES[c], 'Registered Voters', '', '', '', int(v), None)
            continue
        if left == 'Ballots Cast':
            for c, v in cells.items():
                if c != 'Total':
                    emit(COL_NAMES[c], 'Ballots Cast', '', '', '', int(v), None)
            continue
        if left.startswith('Percentage Voted') or SECTION_RE.match(left):
            continue
        if left.startswith('Remember to Refresh') or left.startswith('Tuesday August'):
            continue

        # context rows
        m = TOWNSHIP_RE.match(left)
        if m:
            township = 'Grayling City' if m.group(2) else f'{m.group(1)} Township'
            in_offices = False
            proposal_mode = False
            continue
        if left in ('County Proposals', 'Township Proposals'):
            proposal_mode = True
            township = None
            office = None
            continue
        if left == 'Delegates to the':
            pending_delegate = True
            continue
        if pending_delegate and left.startswith('County Convention'):
            office = ('Precinct Delegate', township or '', '')
            in_offices = True
            pending_delegate = False
            continue

        m = re.match(r'^(\d+)(?:st|nd|rd|th) District(?: \(vote for \d+\))?$', left)
        if m:
            office = ('County Commissioner', m.group(1), '')
            continue

        m = re.match(r'^(.*?)(?: \((?:vote for )?(\d+)\))?$', left)
        # office rows end in '(vote for N)' or '(N)'; candidate rows carry a party
        m2 = re.match(r'^(.*?)\s*\((?:vote for )?(\d+)\)$', left)
        bare_office = left in ('United States Senator', 'Congress 1st Dist')
        if left.startswith('(vote for'):
            continue                          # continuation of a bare office line
        if (m2 or bare_office) and not party:
            title = m2.group(1).strip() if m2 else left
            if title in ('District',):       # stray
                continue
            title = OFFICE_NAMES.get(title, title)
            if title == 'United States Senator':
                office = ('U.S. Senate', '', '')
            elif title == 'Congress 1st Dist':
                office = ('U.S. House', '1', '')
            elif title == 'State Rep 103rd District':
                office = ('State House', '103', '')
            elif proposal_mode:
                office = (title, township or '', '')
            elif township:
                office = (title, '', '')
            else:
                office = (title, '', '')
            in_offices = True
            continue

        if left == '' and not party and len(cells) == 1 and 'Total' in cells:
            continue                          # stray Total-only row (e.g. 5th Dist 0)

        # candidate row
        if party and left:
            if not office:
                errors.append(f'candidate row without office: {left!r}')
                continue
            if cells.get('Total') is not None:
                expected = sum(int(v) for c, v in cells.items()
                               if c != 'Total' and re.match(r'^\d+$', v))
                total = int(cells['Total'])
                if total != expected:
                    if total == 0:
                        notes.append(f'{office[0]} {left}: values {expected} but '
                                     f'printed Total {total}; keeping values')
                    elif any(total == expected - int(v)
                             for c, v in cells.items()
                             if c != 'Total' and re.match(r'^\d+$', v)):
                        # printed Total equals the sum minus one column: that
                        # value is extra (the sheet's own Total formula says the
                        # certified sum); e.g. Stefanko's spurious Beaver Creek 183
                        bad = [c for c, v in cells.items()
                               if c != 'Total' and re.match(r'^\d+$', v)
                               and total == expected - int(v)][0]
                        notes.append(f'{office[0]} {left}: dropped {COL_NAMES[bad]} '
                                     f'value {cells[bad]} (printed Total {total} = '
                                     f'sum without it; matches certified)')
                        del cells[bad]
                    else:
                        errors.append(f'{office[0]} {left}: sum {expected} != Total {total}')
            o, d, op = office
            p = op or {'D': 'DEM', 'R': 'REP'}.get(party, party)
            emitted = 0
            for c, v in sorted(cells.items()):
                if c == 'Total' or not re.match(r'^\d+$', v):
                    continue
                emit(COL_NAMES[c], o, d, p, left, int(v), c)
                emitted += 1
            if not emitted:
                notes.append(f'{office[0]} {left}: no per-precinct values '
                             f'(Total {cells.get("Total")})')
            continue

        if left and not party and not cells:
            continue                          # stray text (e.g. 'Abbreviations' items)

    # ---- validation against printed countywide totals ----
    from collections import defaultdict
    sums = defaultdict(int)
    for r in out:
        if r['office'] in ('Registered Voters', 'Ballots Cast'):
            sums[r['office']] += r['votes']
    if sums['Registered Voters'] != 11450:
        errors.append(f'Registered Voters sum {sums["Registered Voters"]} != 11450')
    if sums['Ballots Cast'] != 4375:
        errors.append(f'Ballots Cast sum {sums["Ballots Cast"]} != 4375')

    if errors:
        for e in errors[:20]:
            print('ERROR', e)
        sys.exit(f'Crawford: {len(errors)} errors')
    for n in notes:
        print('NOTE', n)
    clean = [{k: v for k, v in r.items() if not k.startswith('_')} for r in out]
    write_csv('Crawford', clean)


if __name__ == '__main__':
    main()