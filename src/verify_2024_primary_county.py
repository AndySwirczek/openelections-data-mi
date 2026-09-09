"""Verify 2024 primary precinct CSVs against the CENR county-level file.

Sums each 2024/counties/20240806__mi__primary__<county>__precinct.csv by
(county, office, district, party, candidate) and compares against
2024/20240806__mi__primary__county.csv for candidates present in BOTH sets.
Candidate names are matched exactly, then on a normalized form, then on
last name + first initial (CENR name forms differ from source PDFs).
"""
import csv
import glob
import re
import sys
from collections import defaultdict

COUNTY_CSV = '2024/20240806__mi__primary__county.csv'
# Counties whose CENR rows have no precinct counterpart (no precinct file, or
# the source was countywide-only for these offices) — reported, not compared.
SKIP_COUNTIES = {
    'Allegan': 'countywide-only source',
    'Alpena': 'no 2024 primary precinct file (Feb presidential primary only)',
    'Cheboygan': 'countywide-only source',
    'Lapeer': 'countywide-only source',
    'Menominee': 'countywide-only source',
    'Mason': 'source countywide for all statewide/county offices',
    'Oakland': 'no precinct file (JSON unusable)',
    'Ottawa': 'no precinct file (source was May special election)',
}

PARTY_MAP = {'D': 'DEM', 'R': 'REP'}


def norm(name):
    return re.sub(r'\s+', ' ', re.sub(r"[.'’,\-]", ' ', name.lower())).strip()


def spacefree(name):
    return norm(name).replace(' ', '')


def spacefree_key(name):
    """Space-free norm ignoring single-letter tokens (middle initials),
    so 'Robert E. Kelly-Mc Farland' == 'Robert Kelly-McFarland'."""
    return ''.join(t for t in norm(name).split() if len(t) > 1)


def tokens(name):
    return {t for t in norm(name).split() if len(t) > 1}


def key_parts(name):
    """(last name, first initial) of a normalized name."""
    parts = norm(name).split()
    return (parts[-1], parts[0][0] if parts else '')


def fix_office(office, district):
    """Map precinct-file office variants onto the county file's names."""
    m = re.match(r'^State Representative (\d+)(?:ST|ND|RD|TH)$', office)
    if m:
        return 'State House', m.group(1)
    return office, district


def main():
    county_rows = defaultdict(dict)  # (county, office, dist, party) -> {cand: votes}
    with open(COUNTY_CSV, newline='') as fh:
        for r in csv.DictReader(fh):
            key = (r['county'], r['office'], r['district'], r['party'])
            county_rows[key][r['candidate']] = county_rows[key].get(r['candidate'], 0) + int(r['votes'])

    ok = mismatch = 0
    mismatches, cenr_only, precinct_only = [], [], []

    files = sorted(glob.glob('2024/counties/20240806__mi__primary__*__precinct.csv'))
    for f in files:
        county = re.search(r'__primary__([a-z_]+)__', f).group(1).replace('_', ' ').title()
        sums = defaultdict(int)
        for r0 in csv.DictReader(open(f)):
            r = dict(r0)
            # Qualified write-ins print under their own names with a ' (W)'
            # suffix; CENR records them as 'Write-In'.
            if r['candidate'].endswith(' (W)') or re.sub(r'[^a-z]', '', r['candidate'].lower()) in ('writeins', 'writein'):
                r['candidate'] = 'Write-In'
            office, district = fix_office(r['office'], r['district'])
            party = PARTY_MAP.get(r['party'], r['party'])
            if r['office'] not in ('U.S. Senate', 'U.S. House', 'State House',
                                   'District Court Judge', 'Circuit Court Judge',
                                   'Probate Court Judge', 'State Representative 92nd',
                                   'State Representative 93rd'):
                continue
            # Van Buren-style protected cells emit blank votes
            if r['votes']:
                sums[(office, district, party, r['candidate'])] += int(r['votes'])

        # Group both sides by contest for matching
        contests = defaultdict(lambda: ({}, {}))  # key -> ({cand: cenr}, {cand: precinct})
        for (c, office, district, party), cand_votes in county_rows.items():
            if c == county:
                for cand, v in cand_votes.items():
                    contests[(office, district, party)][0][cand] = v
        for (office, district, party, cand), v in sums.items():
            contests[(office, district, party)][1][cand] = v

        for contest, (cenr, prec) in sorted(contests.items()):
            if not cenr or not prec:
                continue
            used = set()
            # 1) exact match
            for cand, v in cenr.items():
                if cand in prec:
                    used.add(cand)
                    if prec[cand] == v:
                        ok += 1
                    else:
                        mismatch += 1
                        mismatches.append((county, contest, cand, v, prec[cand]))
            # 2) normalized-name fallback (also space-free, for 'Mc Clain')
            prec_norm = {norm(c): c for c in prec}
            prec_free = {spacefree(c): c for c in prec}
            prec_free_key = {spacefree_key(c): c for c in prec}
            for cand, v in cenr.items():
                if cand in used:
                    continue
                pc = (prec_norm.get(norm(cand)) or prec_free.get(spacefree(cand))
                      or prec_free_key.get(spacefree_key(cand)))
                if pc and pc not in used:
                    used.add(pc)
                    used.add(cand)
                    if prec[pc] == v:
                        ok += 1
                    else:
                        mismatch += 1
                        mismatches.append((county, contest, f'{cand} ~ {pc}', v, prec[pc]))
            # 3) token-containment fallback (Wayne's 'Last First M.' order;
            #    both CENR first and last names appear in the precinct name)
            for cand, v in cenr.items():
                if cand in used:
                    continue
                tt = tokens(cand)
                if len(tt) < 2:
                    continue
                hits = [pc for pc in prec if pc not in used and tokens(pc) >= tt]
                if len(hits) == 1:
                    pc = hits[0]
                    used.add(pc)
                    used.add(cand)
                    if prec[pc] == v:
                        ok += 1
                    else:
                        mismatch += 1
                        mismatches.append((county, contest, f'{cand} ~ {pc}', v, prec[pc]))
            # 4) last name + first initial fallback (unambiguous only)
            for cand, v in cenr.items():
                if cand in used:
                    continue
                kk = key_parts(cand)
                hits = [pc for pc in prec if pc not in used and key_parts(pc) == kk]
                if len(hits) == 1:
                    pc = hits[0]
                    used.add(pc)
                    used.add(cand)
                    if prec[pc] == v:
                        ok += 1
                    else:
                        mismatch += 1
                        mismatches.append((county, contest, f'{cand} ~ {pc}', v, prec[pc]))
            for cand in cenr:
                if cand not in used:
                    cenr_only.append((county, contest, cand, cenr[cand]))
            PSEUDO = ('Ballots Cast', 'Total Votes', 'Registered Voters',
                      'Ballots Cast Blank', 'Over Votes', 'Under Votes',
                      'Blanks')
            for cand in prec:
                if cand not in used and cand not in PSEUDO:
                    precinct_only.append((county, contest, cand, prec[cand]))

    # Write-In rows: the precinct files carry raw (unqualified) write-in
    # totals; CENR carries only the official (qualified) figure, often 0.
    numeric = [m for m in mismatches if m[2].split(' ~ ')[0] != 'Write-In'
               and not m[2].endswith(' ~ Write-In')]
    writein = [m for m in mismatches if m not in numeric]

    print(f'Candidates in both sets: {ok} matched, {mismatch} MISMATCHED '
          f'({len(writein)} write-in rows, {len(numeric)} numeric)')
    print()
    for row in numeric:
        print('MISMATCH:', row)
    print()
    print(f'Write-In rows (raw vs official; expected diff): {len(writein)}')
    for row in writein:
        print('  ', row)
    print()
    print(f'CENR-only candidates ({len(cenr_only)}):')
    for row in cenr_only:
        print('  ', row)
    print()
    print(f'Precinct-only candidates ({len(precinct_only)}):')
    for row in precinct_only:
        print('  ', row)
    print()
    print('Counties with no comparable precinct file:')
    for c, why in sorted(SKIP_COUNTIES.items()):
        print(f'  {c}: {why}')
    if mismatches:
        sys.exit(1)


if __name__ == '__main__':
    main()