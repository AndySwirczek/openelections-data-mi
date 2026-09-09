"""Standardize candidate names and party codes in the 2022 general precinct files.

Canonical source: 2022/20221108__mi__general__county.csv (converted from the SOS
CENR by-county export by src/cenr_2022.py). Covers the offices that file carries
(Governor, Attorney General, Secretary of State, U.S. House, State Senate, State
House, Justice of Supreme Court, the four state boards, Court of Appeals/Circuit/
District/Probate judges, Proposals 22-1/22-2/22-3) plus Straight Party and the
write-in pseudo rows of those offices. County/local offices with no canonical
source are left untouched.

Usage:
  .venv/bin/python src/fix_2022_general_precinct.py            # report only
  .venv/bin/python src/fix_2022_general_precinct.py --apply    # rewrite files
"""
import csv
import difflib
import glob
import os
import re
import sys
from collections import Counter, defaultdict

CANON_CSV = '2022/20221108__mi__general__county.csv'
COUNTY_GLOB = '2022/counties/20221108__mi__general__*__precinct.csv'

# ---------------------------------------------------------------- canonical
canon_party = {}        # (office, district, candidate) -> party
canon_by_office = defaultdict(dict)   # office -> {(district, candidate): party}
canon_county_districts = defaultdict(set)  # (county_title, office) -> {district}
for r in csv.DictReader(open(CANON_CSV)):
    key = (r['office'], r['district'], r['candidate'])
    canon_party[key] = r['party']
    canon_by_office[r['office']][(r['district'], r['candidate'])] = r['party']
    canon_county_districts[(r['county'], r['office'])].add(r['district'])

# ---------------------------------------------------------------- office aliases
ORD = r'(\d+)'

def judge_alias(desc):
    """Judge-office descriptions -> canonical (office, district or None)."""
    m = (re.match(rf'Judge of Circuit Court {ORD}(?:ST|ND|RD|TH) Circuit ', desc)
         or re.match(rf'Judge of Circuit Court {ORD}(?:ST|ND|RD|TH) Circuit$', desc))
    if m:
        return 'Circuit Court Judge', m.group(1)
    if desc == 'Judge of Circuit Court':
        return 'Circuit Court Judge', None  # district resolved via candidate
    m = re.match(rf'Judge of Court of Appeals {ORD}(?:ST|ND|RD|TH) District', desc)
    if m:
        return 'Court of Appeals Judge', m.group(1)
    m = re.match(rf'Judge of Court of Appeals {ORD}(?:ST|ND|RD|TH)$', desc)
    if m:
        return 'Court of Appeals Judge', m.group(1)
    m = (re.match(rf'Judge of District Court {ORD}(?:ST|ND|RD|TH) District, '
                  rf'{ORD}(?:ST|ND|RD|TH) Division', desc))
    if m:
        return 'District Court Judge', f'{m.group(1)}-{m.group(2)}'
    m = re.match(rf'Judge of District Court {ORD}(?:ST|ND|RD|TH) District', desc)
    if m:
        return 'District Court Judge', m.group(1)
    if desc.startswith('Judge of Probate Court'):
        return 'Probate Court Judge', ''
    return None, None

OFFICE_ALIAS = {
    'Governor': 'Governor',
    'Attorney General': 'Attorney General',
    'Secretary of State': 'Secretary of State',
    'Secretary Of State': 'Secretary of State',
    'U.S. House': 'U.S. House',
    'State Senate': 'State Senate',
    'State House': 'State House',
    'Justice of Supreme Court': 'Justice of Supreme Court',
    'Justice of the Supreme Court': 'Justice of Supreme Court',
    'Member of the State Board of Education': 'State Board of Education',
    'State Board of Education': 'State Board of Education',
    'Straight Party': 'Straight Party',
    'Regent of the University of Michigan': 'Regent of the University of Michigan',
    'U of M Regent': 'Regent of the University of Michigan',
    'Trustee of Michigan State University': 'Trustee of Michigan State University',
    'MSU Trustee': 'Trustee of Michigan State University',
    'Governor of Wayne State University': 'Governor of Wayne State University',
    'Wayne State Gov': 'Governor of Wayne State University',
    'Proposal 22-1': 'Proposal 22-1', 'Prop 22-1': 'Proposal 22-1',
    'Proposal 22-2': 'Proposal 22-2', 'Prop 22-2': 'Proposal 22-2',
    'Proposal 22-3': 'Proposal 22-3', 'Prop 22-3': 'Proposal 22-3',
}

# ---------------------------------------------------------------- parties
PARTY_NORM = {
    'DEM': 'DEM', 'DEMOCRATIC': 'DEM',
    'REP': 'REP', 'REPUBLICAN': 'REP',
    'LIB': 'LIB', 'LIBERTARIAN': 'LIB',
    'GRN': 'GRN', 'GREEN': 'GRN', 'GRE': 'GRN',
    'UST': 'UST', 'UTP': 'UST', 'U.S. TAXPAYERS': 'UST',
    'NLP': 'NLP', 'NL': 'NLP', 'NAT': 'NLP', 'NAL': 'NLP', 'NTL': 'NLP',
    'NATURAL LAW': 'NLP', 'NATURAL LAW PARTY': 'NLP',
    'WCP': 'WCP', 'WC': 'WCP', 'WORKING CLASS': 'WCP', 'WORKING CLASS PARTY': 'WCP',
    'GP': None,   # Van Buren only — resolved by county special-case below
    'WP': None,   # Van Buren only
    '': '', 'NPA': '',
}
WRITE_IN_PARTIES = {'WRITE IN', 'WRITE-IN', 'WRITE-INN', 'WRITE-INS'}
CODE_TOKENS = {'GRE', 'GRN', 'NLP', 'NL', 'WC', 'WCP', 'UST', 'UTP', 'DEM', 'REP',
               'LIB', 'GP', 'WP', 'NAT', 'NAL', 'NTL'}

STRAIGHT_PARTY_CANDIDATE = {
    'DEM': 'Democratic', 'REP': 'Republican', 'LIB': 'Libertarian', 'GRN': 'Green',
    'UST': 'U.S. Taxpayers', 'NLP': 'Natural Law', 'WCP': 'Working Class',
}
SP_CODES = {'DEM': 'DEM', 'REP': 'REP', 'LIB': 'LIB', 'GRN': 'GRN',
            'UST': 'UST', 'UTP': 'UST', 'NLP': 'NLP', 'WC': 'WCP', 'WCP': 'WCP',
            'NAT': 'NLP'}
STRAIGHT_PARTY_BY_CANDIDATE = {
    'DEMOCRATIC': ('DEM', 'Democratic'), 'DEMOCRATIC PARTY': ('DEM', 'Democratic'),
    'REPUBLICAN': ('REP', 'Republican'), 'REPUBLICAN PARTY': ('REP', 'Republican'),
    'LIBERTARIAN': ('LIB', 'Libertarian'), 'LIBERTARIAN PARTY': ('LIB', 'Libertarian'),
    'GREEN': ('GRN', 'Green'), 'GREEN PARTY': ('GRN', 'Green'),
    'U.S. TAXPAYERS': ('UST', 'U.S. Taxpayers'), 'U.S. TAXPAYERS PARTY': ('UST', 'U.S. Taxpayers'),
    'NATURAL LAW': ('NLP', 'Natural Law'), 'NATURAL LAW PARTY': ('NLP', 'Natural Law'),
    'WORKING CLASS': ('WCP', 'Working Class'), 'WORKING CLASS PARTY': ('WCP', 'Working Class'),
}
# Van Buren mislabels its Green/NLP straight-party columns (ballot order:
# DEM REP LIB UST WCP GRN NLP -> the 6th 'GP' column is Green, 7th 'NL' is NLP)
COUNTY_STRAIGHT_PARTY = {
    # ballot order DEM REP LIB UST WCP GRN NLP: the 6th 'GP' column is Green,
    # the 7th 'NL' column is NLP in both counties
    'Van Buren': {'GP': ('GRN', 'Green'), 'NL': ('NLP', 'Natural Law')},
    'Shiawassee': {'GP': ('GRN', 'Green'), 'NL': ('NLP', 'Natural Law')},
}

# ------------------------------------------------------- pseudo-candidate rows
# Breakdown/pseudo rows that sit under a real office in some county files
# (contest totals, over/under votes, rejected write-ins, ...). Keyed on the
# lowercased candidate; party is always forced blank.
PSEUDO_CANDIDATES = {
    'ballots cast': 'Ballots Cast',
    'cast votes': 'Cast Votes',
    'total': 'Total',
    'unresolved': 'Unresolved',
    'over votes': 'Over Votes', 'overvotes': 'Over Votes',
    'under votes': 'Under Votes', 'undervotes': 'Under Votes',
    'rejected write-ins': 'Rejected Write-Ins',
    'rejected write-in votes': 'Rejected Write-Ins',
    'unassigned write-ins': 'Unassigned Write-Ins',
    'unresolved write-in': 'Unresolved Write-In',
    'unresolved write-in votes': 'Unresolved Write-In',
    'unqualified write-ins': 'Unqualified Write-Ins',
}

# Typos the fuzzy matcher cannot bridge (keyed (office, normalized variant)).
MANUAL_CANDIDATE = {
    ('Governor', 'elizabeth add adkisson'): 'Elizabeth Ann Adkisson',
    ('Governor', 'michael david kelly'): 'Michael David Kelley',
    ('Attorney General', 'joe mchugh jr'): 'Joseph W. McHugh Jr.',
    ('Secretary of State', 'jocelyn benton'): 'Jocelyn Benson',
    ('Secretary of State', 'christina schwartz'): 'Christine C. Schwartz',
    ('State Senate', 'kai w de graaf'): 'Kai W. Degraaf',
    ('State Senate', 'christina gerace'): 'Christine Gerace',
    ('State House', 'lauren pohutsky'): 'Laurie Pohutsky',
    ('State House', 'jenn will'): 'Jenn Hill',
    ('State House', 'kimberly k kennedy barrington'): 'Kimberly Y. Kennedy-Barrington',
    ('State House', 'john m magoola'): 'John J. Magoola',
    ('State House', 'adam wodjan'): 'Adam J. Wojdan',
    ('State House', 'mike corcoran'): 'Mark Corcoran',
    ('State Senate', 'john m damoose'): 'John N. Damoose',
    ('U.S. House', 'jacob kelts'): 'Jake Kelts',
    ('U.S. House', 'daniel t kiddle'): 'Daniel T. Kildee',
}

# ---------------------------------------------------------------- helpers
def to_int(s):
    s = (s or '').replace(',', '').strip()
    return int(s) if s else 0

def norm(s):
    return ' '.join(re.sub(r'[^a-z0-9]', ' ', s.lower()).split())

def clean_write_in(name):
    """Strip write-in markers / party-code prefixes from a candidate string."""
    s = name.strip()
    s = re.sub(r'^qw\s*-\s*', '', s, flags=re.I)
    s = re.sub(r'^write[- ]?ins?\s*[:\-]?\s*', '', s, flags=re.I)
    s = re.sub(r'\s*\(\s*w\s*\)\s*$', '', s, flags=re.I)
    toks = s.split()
    while toks and toks[0].upper() in CODE_TOKENS:
        toks = toks[1:]
    return ' '.join(toks)

def match_candidate(county_title, office, variant, districts):
    """-> (canonical_candidate, district, canonical_party) or (None, None, None)."""
    cands = canon_by_office[office]
    pool = {c: (d, p) for (d, c), p in cands.items()
            if districts is None or d in districts or d == ''}
    raw = variant.strip()
    if raw in pool:
        d, p = pool[raw]
        return raw, d, p
    v = norm(clean_write_in(raw))
    if not v:
        return None, None, None
    manual = MANUAL_CANDIDATE.get((office, v))
    if manual in pool:
        d, p = pool[manual]
        return manual, d, p
    vt = set(v.split())
    best = None
    for c, (d, p) in pool.items():
        ct = norm(c)
        if not ct:
            continue
        cts = set(ct.split())
        if cts == vt:
            score = 0.0
        elif vt >= cts:      # running mate / extra middle tokens
            score = 0.3 + 0.01 * (len(vt) - len(cts))
        elif vt <= cts:      # last name only ('Whitmer', 'Dixon', 'Karamo')
            score = 0.5 + 0.01 * (len(cts) - len(vt))
        else:
            # fuzzy: map each canonical token to a variant token
            unmatched = list(vt)
            dist = 0
            for t in cts:
                near = difflib.get_close_matches(t, unmatched, n=1, cutoff=0.8)
                if near:
                    dist += abs(len(t) - len(near[0]))
                    unmatched.remove(near[0])
                else:
                    dist += 99
            dist += 3 * len(unmatched)
            score = 1.0 + dist
        if best is None or score < best[0]:
            best = (score, c, d, p)
    if best and best[0] <= 0.6:
        return best[1], best[2], best[3]
    if best and 1.0 <= best[0] <= 2.0 + 0.5 * len(vt):
        return best[1], best[2], best[3]
    return None, None, None


def cross_office_match(office, variant, party, district):
    """Exact (candidate, party) match in a different canonical office.

    Catches office-label errors like Clinton's State Senate 28/34 filed under
    'U.S. House'. Returns (office, candidate, district) or None."""
    if not party.strip() or party.strip().upper() not in PARTY_NORM:
        return None
    code = PARTY_NORM[party.strip().upper()]
    v = norm(clean_write_in(variant))
    if not v:
        return None
    hits = [(off, dist, cand) for off, cands in canon_by_office.items() if off != office
            for (dist, cand), p in cands.items() if p == code and norm(cand) == v]
    return hits[0] if len(hits) == 1 else None

# ---------------------------------------------------------------- main
def fix(county_path, apply):
    with open(county_path, newline='') as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        src = list(reader)
    county_title = os.path.basename(county_path).split('__general__')[1].split('__')[0]
    county_title = ' '.join(w.capitalize() for w in county_title.split('_'))
    all_offices = {x['office'] for x in src}
    rows_out = []
    unmatched = Counter()
    fixed = Counter()
    merged = Counter()
    for r in src:
        o, d, p, c = r['office'], r['district'], r['party'], r['candidate']
        office = OFFICE_ALIAS.get(o)
        if office is None and o.startswith('Judge of'):
            office, jdist = judge_alias(o)
            if office and jdist is not None:
                d = jdist
        if office is None:
            # not a covered office; only normalize pseudo-office casing/naming
            if o == 'Registered voters':
                r['office'] = 'Registered Voters'
            elif o in ('Cards Cast', 'Votes Cast') and 'Ballots Cast' not in all_offices:
                r['office'] = 'Ballots Cast'
            rows_out.append(r)
            continue

        if office == 'Straight Party':
            up = ' '.join(p.split()).upper()
            upc = ' '.join(c.split()).upper()
            cn = ' '.join(c.split()).lower()
            if not p.strip() and not c.strip():
                pass
            elif cn in PSEUDO_CANDIDATES:
                p, c = '', PSEUDO_CANDIDATES[cn]
            elif upc in ('OVER VOTES', 'UNDER VOTES'):
                p, c = '', upc.title()
            elif upc in SP_CODES:            # code in the candidate field (swapped cols)
                p, c = SP_CODES[upc], STRAIGHT_PARTY_CANDIDATE[SP_CODES[upc]]
            elif up in COUNTY_STRAIGHT_PARTY.get(county_title, {}):
                p, c = COUNTY_STRAIGHT_PARTY[county_title][up]
            elif up in SP_CODES:
                p = SP_CODES[up]
                c = STRAIGHT_PARTY_CANDIDATE[p]
            elif upc in STRAIGHT_PARTY_BY_CANDIDATE:
                p, c = STRAIGHT_PARTY_BY_CANDIDATE[upc]
            elif up in STRAIGHT_PARTY_BY_CANDIDATE:
                p, c = STRAIGHT_PARTY_BY_CANDIDATE[up]
            elif up in WRITE_IN_PARTIES:
                p, c = '', 'Write-In'
            elif not p.strip() and cn in ('write-in', 'write-ins'):
                p, c = '', 'Write-In'
            else:
                unmatched[(o, d, p, c)] += to_int(r['votes'])
            r['party'], r['candidate'] = p, c
            rows_out.append(r)
            continue

        # covered office: resolve candidate + party against the county file
        districts = canon_county_districts[(county_title, office)] or None
        pu = ' '.join(p.split()).upper()
        cn = ' '.join(c.split()).lower()

        # pseudo rows under a covered office (contest totals, over/under votes, ...)
        if cn in PSEUDO_CANDIDATES:
            r['party'], r['candidate'], r['office'] = '', PSEUDO_CANDIDATES[cn], office
            rows_out.append(r)
            continue

        if o.startswith('Prop'):
            if not c.strip() and pu in ('YES', 'NO'):
                c, p = p, ''
            if c.strip():
                cand, dist, cparty = match_candidate(county_title, office, c, districts)
                if cand is not None:
                    c = cand
                elif cn not in ('yes', 'no'):
                    unmatched[(o, d, p, c)] += to_int(r['votes'])
            else:
                unmatched[(o, d, p, c)] += to_int(r['votes'])
            r['party'], r['candidate'], r['office'] = '', c, office
            rows_out.append(r)
            continue

        cleaned = clean_write_in(c) if c.strip() else ''
        is_write_in_row = pu in WRITE_IN_PARTIES or (not c.strip() and pu == '') \
            or (cleaned == '' and c.strip() != '')
        if is_write_in_row:
            if not cleaned:
                r['party'], r['candidate'] = '', 'Write-In'
            else:
                cand, dist, cparty = match_candidate(county_title, office, cleaned, districts)
                if cand is not None:
                    r['party'], r['candidate'] = cparty, cand
                    if dist not in (None, ''):
                        r['district'] = dist
                else:
                    # named write-in not in the county file: keep the cleaned name
                    r['party'], r['candidate'] = '', cleaned
                    unmatched[(o, d, pu, c)] += to_int(r['votes'])
            r['office'] = office
            rows_out.append(r)
            continue

        cand, dist, cparty = match_candidate(county_title, office, c, districts) if c.strip() \
            else (None, None, None)
        if cand is not None:
            if cparty:
                r['party'] = cparty
            r['candidate'] = cand
            if dist not in (None, '') and d != dist:
                fixed[('district', o, d, dist)] += to_int(r['votes'])
                r['district'] = dist
        elif c.strip():
            x = cross_office_match(office, c, p, d)
            if x:
                office, dist, cand = x
                r['office'], r['candidate'], r['district'] = office, cand, dist
                r['party'] = canon_party.get((office, dist, cand), r['party'])
            else:
                unmatched[(o, d, p, c)] += to_int(r['votes'])
        else:
            # blank candidate, non-write-in party: pseudo row in a covered office
            if pu in ('TOTAL', 'UNRESOLVED'):
                r['party'], r['candidate'] = '', pu.capitalize()
            elif pu in PARTY_NORM or pu in SP_CODES:
                # a party-code row with the candidate name dropped: assign the
                # unique canonical candidate of that party in the county's pool
                code = PARTY_NORM.get(pu) or SP_CODES.get(pu)
                matches = {(dist, cand) for (dist, cand), pp in canon_by_office[office].items()
                           if pp == code and (districts is None or dist in districts or dist == '')}
                if len(matches) == 1:
                    dist, cand = next(iter(matches))
                    r['party'], r['candidate'] = code, cand
                    if dist and d != dist:
                        fixed[('district', o, d, dist)] += to_int(r['votes'])
                        r['district'] = dist
                else:
                    unmatched[(o, d, p, c)] += to_int(r['votes'])
            else:
                unmatched[(o, d, p, c)] += to_int(r['votes'])
        if 'office' not in r or r['office'] != office:
            r['office'] = office
        rows_out.append(r)

    # rows standardized to the same key in one precinct get their votes summed
    seen, order = {}, []
    for r in rows_out:
        key = (r['county'], r['precinct'], r['office'], r['district'],
               r['party'], r['candidate'])
        if key in seen:
            t = seen[key]
            merged[(r['office'], r['candidate'], key[1])] += to_int(r['votes'])
            for f in fieldnames:
                if f in key:
                    continue
                a, b = (t.get(f) or '').replace(',', ''), (r.get(f) or '').replace(',', '')
                try:
                    t[f] = str(int(a) + int(b))
                except ValueError:
                    t[f] = t.get(f) or r.get(f) or ''
        else:
            seen[key] = r
            order.append(r)
    return fieldnames, order, unmatched, fixed, merged

    return rows_out, unmatched, fixed


def main():
    apply = '--apply' in sys.argv
    all_unmatched = Counter()
    all_fixed = Counter()
    all_merged = Counter()
    for path in sorted(glob.glob(COUNTY_GLOB)):
        fieldnames, rows_out, unmatched, fixed, merged = fix(path, apply)
        all_unmatched.update(unmatched)
        all_fixed.update(fixed)
        all_merged.update(merged)
        if apply:
            with open(path, 'w', newline='') as fh:
                w = csv.DictWriter(fh, fieldnames=fieldnames)
                w.writeheader()
                for r in rows_out:
                    w.writerow(r)
    print(f'=== unmatched variants ({sum(all_unmatched.values())} votes):')
    for (o, d, p, c), v in sorted(all_unmatched.items()):
        print(f'  {v:7d}  office={o!r} district={d!r} party={p!r} candidate={c!r}')
    print(f'=== silent fixes: {sum(all_fixed.values())} votes in {len(all_fixed)} keys')
    for k, v in sorted(all_fixed.items()):
        print(f'  {v:7d}  {k}')
    print(f'=== merged duplicate rows: {sum(all_merged.values())} votes in {len(all_merged)} keys')
    for k, v in sorted(all_merged.items()):
        print(f'  {v:7d}  {k}')

if __name__ == '__main__':
    main()