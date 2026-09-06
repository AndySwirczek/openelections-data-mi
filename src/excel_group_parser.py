"""Parse a vendor Excel "Precinct Results by Group" sheet into a precinct CSV.

Written for files like
openelections-sources-mi/2026/primary/Newaygo County Aug 2026 Primary All Results.xlsx
where each contest has one row per (candidate, Group) with Group in
{Election Day, Early Voting, AV Counting Boards}.

Usage:
    python src/excel_group_parser.py \
        --source '/path/County Results.xlsx' --county Newaygo \
        --date 20260804 --out 2026/counties/20260804__mi__primary__newaygo__precinct.csv
"""
import argparse
import csv
import re
import sys

import openpyxl

# Columns of the "Precinct Results by Group" sheet
PRECINCT, OFFICE_NAME, BALLOT_NAME, PARTY, GROUP, TOTAL = 0, 1, 3, 5, 6, 7

PARTY_MAP = {'Democrat': 'DEM', 'Democratic': 'DEM', 'Republican': 'REP', '': ''}

GROUP_COLUMN = {'Election Day': 'election_day', 'Absentee': 'absentee',
                'Early Voting': 'early_voting', 'AV Counting Boards': 'av_counting_boards'}

# Rows with these ballot names are dropped (Over/Under are excluded from results)
SKIP_NAMES = {'Over Votes', 'Under Votes'}

# Statewide offices: (pattern on office name, output office, district group index or None)
STATE_OFFICES = [
    (re.compile(r'^Governor(?: for State)?$'), 'Governor', None),
    (re.compile(r'^United States Senator(?: for State)?$'), 'U.S. Senate', None),
    # "Representative in Congress 2nd District" / "... District 4"
    (re.compile(r'^Representative in Congress (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'U.S. House', 'both'),
    # "State Senator 33rd District" / "State Senate 34th District" / "... District 19"
    (re.compile(r'^(?:State Senator|State Senate) (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'State Senate', 'both'),
    # "Representative in State Legislature 101st District" / "... District 40"
    (re.compile(r'^Representative in State Legislature (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'State House', 'both'),
    (re.compile(r'^State Representative (\d+)(?:st|nd|rd|th) District$'), 'State House', 1),
]

TOWNSHIP_OFFICE = re.compile(r'^(Township \S+) for (.+)$')


def normalize_office(name):
    """Return (office, district, contest_party). Strips the '(DEM)'/'(REP)' suffix
    or 'DEM '/'REP ' prefix some counties put on contest names."""
    contest_party = ''
    m = re.search(r' \((DEM|REP)\)$', name)
    if m:
        contest_party = m.group(1)
        name = name[:m.start()]
    else:
        m = re.match(r'^(DEM|REP) (.+)$', name)
        if m:
            contest_party = m.group(1)
            name = m.group(2)
    office = name
    for pat, out_office, dist_group in STATE_OFFICES:
        m = pat.match(office)
        if m:
            if dist_group is None:
                district = ''
            elif dist_group == 'both':
                district = str(int(next(g for g in m.groups() if g is not None)))
            else:
                district = str(int(m.group(dist_group)))
            return out_office, district, contest_party
    # "Township Clerk for Home Township" -> "Home Township Clerk"
    m = TOWNSHIP_OFFICE.match(office)
    if m:
        office = f'{m.group(2)} {m.group(1)}'
    return office, '', contest_party


def parse(source, sheet):
    wb = openpyxl.load_workbook(source, data_only=True)
    ws = wb[sheet]
    rows = {}          # (precinct, office, district, party, candidate) -> {group: votes}
    precinct_order = {}
    needs_fallback = set()
    for r in ws.iter_rows(min_row=2, values_only=True):
        r = list(r) + [None] * 8
        precinct, office_name, ballot, party, group, total = (
            r[PRECINCT], r[OFFICE_NAME], r[BALLOT_NAME], r[PARTY], r[GROUP], r[TOTAL])
        if precinct is None or office_name is None:
            continue  # blank/trailing row
        precinct = re.sub(r'\s+', ' ', str(precinct)).strip()
        office_name = re.sub(r'\s+', ' ', str(office_name)).strip()
        if ballot in SKIP_NAMES:
            continue
        if group not in GROUP_COLUMN:
            sys.exit(f'Unknown vote-type Group {group!r} for {precinct!r} / {office_name!r}')
        office, district, contest_party = normalize_office(office_name)
        precinct_order.setdefault(precinct, len(precinct_order))
        # Ballots Cast rows have no party of their own; use the contest's party
        row_party = PARTY_MAP.get(party or '', '') or contest_party
        candidate = re.sub(r'\s+', ' ', str(ballot)).strip()
        key = (precinct, office, district, row_party, candidate)
        if total is None:
            # Some vendor exports leave candidate Totals blank in this sheet;
            # the 'Precinct Results' sheet has the correct contest total.
            needs_fallback.add(key)
            continue
        rows.setdefault(key, {})[GROUP_COLUMN[group]] = int(total)
    return rows, precinct_order, needs_fallback


def fallback_totals(wb, sheet='Precinct Results'):
    """Contest totals from the non-grouped sheet: {key: total}."""
    if sheet not in wb.sheetnames:
        return {}
    ws = wb[sheet]
    totals = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        r = list(r) + [None] * 8
        precinct, office_name, ballot, party, total = (
            r[PRECINCT], r[OFFICE_NAME], r[BALLOT_NAME], r[PARTY], r[TOTAL - 1])
        if precinct is None or office_name is None:
            continue
        precinct = re.sub(r'\s+', ' ', str(precinct)).strip()
        office_name = re.sub(r'\s+', ' ', str(office_name)).strip()
        if ballot in SKIP_NAMES:
            continue
        office, district, contest_party = normalize_office(office_name)
        row_party = PARTY_MAP.get(party or '', '') or contest_party
        candidate = re.sub(r'\s+', ' ', str(ballot)).strip()
        totals[(precinct, office, district, row_party, candidate)] = int(total or 0)
    return totals


def write_csv(rows, precinct_order, county, out_path):
    CANON = ['election_day', 'absentee', 'early_voting', 'av_counting_boards']
    # Emit only the breakdown columns this county's sheet actually populates
    BREAKDOWNS = [c for c in CANON if any(c in g for g in rows.values())]
    with open(out_path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate',
                    'votes'] + BREAKDOWNS)
        for (precinct, office, district, party, candidate), groups in sorted(
                rows.items(), key=lambda kv: (precinct_order[kv[0][0]], kv[0][1], kv[0][2], kv[0][3], kv[0][4])):
            if '__fallback__' in groups:
                # Total recovered from the non-grouped sheet; no vote-type breakdown
                w.writerow([county, precinct, office, district, party, candidate,
                            groups['__fallback__']] + [''] * len(BREAKDOWNS))
            else:
                breakdowns = [groups.get(c, 0) for c in BREAKDOWNS]
                w.writerow([county, precinct, office, district, party, candidate,
                            sum(breakdowns)] + breakdowns)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--county', required=True)
    ap.add_argument('--sheet', default='Precinct Results by Group')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    rows, precinct_order, needs_fallback = parse(args.source, args.sheet)
    fb = fallback_totals(openpyxl.load_workbook(args.source, data_only=True, read_only=True))
    missing_fb = [k for k in needs_fallback if k not in fb]
    for k in needs_fallback:
        if k in fb:
            rows[k] = {'__fallback__': fb[k]}
        else:
            rows[k] = {'__fallback__': 0}
    if needs_fallback:
        print(f'WARNING: {len(needs_fallback)} rows had blank Totals in {args.sheet!r}; '
              f'{len(needs_fallback) - len(missing_fb)} recovered from "Precinct Results" sheet')
        for k in missing_fb[:10]:
            print(f'  no fallback for {k}')
    write_csv(rows, precinct_order, args.county, args.out)
    print(f'Wrote {len(rows)} rows to {args.out}')


if __name__ == '__main__':
    main()