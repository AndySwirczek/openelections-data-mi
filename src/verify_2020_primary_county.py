"""Verify 2020 primary precinct CSVs against the CENR county-level file.

Sums each 2020/counties/20200804__mi__primary__<county>__precinct.csv by
(county, office, district, party, candidate) and compares against
2020/20200804__mi__primary__county.csv for candidates present in BOTH sets.
Candidate names are matched exactly, then on a normalized form, then on
last name + first initial (CENR name forms differ from source exports).
"""
import csv
import glob
import re
import sys
from collections import defaultdict

COUNTY_CSV = '2020/20200804__mi__primary__county.csv'

VERIFIED_OFFICES = {'U.S. Senate', 'U.S. House', 'State House',
                    'Circuit Court Judge', 'District Court Judge',
                    'Probate Court Judge'}


def norm(name):
    return re.sub(r'\s+', ' ', re.sub(r"[.'’,\-]", ' ', name.lower())).strip()


def spacefree(name):
    return norm(name).replace(' ', '')


def spacefree_key(name):
    return ''.join(t for t in norm(name).split() if len(t) > 1)


def tokens(name):
    return {t for t in norm(name).split() if len(t) > 1}


def key_parts(name):
    parts = norm(name).split()
    return (parts[-1], parts[0][0] if parts else '')


def main():
    only = set(sys.argv[1:]) or None
    county_rows = defaultdict(dict)  # (county, office, dist, party) -> {cand: votes}
    with open(COUNTY_CSV, newline='') as fh:
        for r in csv.DictReader(fh):
            if r['office'] not in VERIFIED_OFFICES:
                continue
            key = (r['county'], r['office'], r['district'], r['party'])
            county_rows[key][r['candidate']] = county_rows[key].get(r['candidate'], 0) + int(r['votes'])

    ok = mismatch = 0
    mismatches, cenr_only, precinct_only = [], [], []

    files = sorted(glob.glob('2020/counties/20200804__mi__primary__*__precinct.csv'))
    if only:
        files = [f for f in files if any(c.lower() in f for c in only)]
    for f in files:
        county = re.search(r'__primary__([a-z_]+)__', f).group(1).replace('_', ' ').title()
        sums = defaultdict(int)
        for r0 in csv.DictReader(open(f)):
            r = dict(r0)
            if r['office'] not in VERIFIED_OFFICES:
                continue
            if r['candidate'].endswith(' (W)') or re.sub(r'[^a-z]', '', r['candidate'].lower()) in ('writeins', 'writein'):
                r['candidate'] = 'Write-In'
            if r['votes']:
                sums[(r['office'], r['district'], r['party'], r['candidate'])] += int(r['votes'])

        # Group both sides by contest for matching
        contests = defaultdict(lambda: ({}, {}))  # key -> ({cand: cenr}, {cand: precinct})
        for (c, office, district, party), cand_votes in county_rows.items():
            if spacefree(c) == spacefree(county):
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
            # 3) token-containment fallback
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
            # 5) single-token precinct candidate matching one unused CENR
            #    candidate's last name (hand-made sheets list last names only)
            for pc, pv in list(prec.items()):
                if pc in used or ' ' in pc.strip():
                    continue
                hits = [c for c, v in cenr.items()
                        if c not in used and c.split()[-1].lower() == pc.strip().lower()]
                if len(hits) == 1:
                    cand = hits[0]
                    used.add(pc)
                    used.add(cand)
                    if prec[pc] == cenr[cand]:
                        ok += 1
                    else:
                        mismatch += 1
                        mismatches.append((county, contest, f'{cand} ~ {pc}', cenr[cand], prec[pc]))
            for cand in cenr:
                if cand not in used:
                    cenr_only.append((county, contest, cand, cenr[cand]))
            PSEUDO = ('Ballots Cast', 'Total Votes', 'Registered Voters',
                      'Ballots Cast Blank', 'Over Votes', 'Under Votes',
                      'Blanks', 'No Nomination')
            for cand in prec:
                if cand not in used and cand not in PSEUDO:
                    precinct_only.append((county, contest, cand, prec[cand]))

    # Write-In rows: the precinct files carry raw (unqualified) write-in
    # totals; the CENR carries qualified write-ins under their own names and
    # no row for unqualified write-ins.
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
    print('Counties with CENR rows but no precinct file yet:')
    seen = {re.search(r'__primary__([a-z_]+)__', f).group(1) for f in files}
    for county in sorted({k[0] for k in county_rows}):
        if county.lower().replace(' ', '_') not in seen:
            n = sum(len(v) for k, v in county_rows.items() if k[0] == county)
            print(f'  {county}: {n} candidate rows')
    if mismatches:
        sys.exit(1)


if __name__ == '__main__':
    main()