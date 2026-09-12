#!/usr/bin/env python3
"""Parse Ionia County's Nov 2020 general election from the Dominion
'Precinct Results' report ('Ionia-County-Official-Precinct-Results.pdf',
337pp, text-extractable, precinct-major).

The committed 20201103__mi__general__ionia__precinct.csv (Dec 2022)
carried only the CENR-covered offices (President, U.S. Senate, U.S.
House, State House, Straight Party); the source has ~40 contest types
(judges, State Board, Regents, county offices, township/village/school
offices, proposals).  This parser regenerates the whole file in the
committed file's own format — header county,precinct,office,district,
candidate,party,votes with 'Under Votes'/'Over Votes' rows and named
'(W)' write-ins — so the covered rows reproduce exactly and the
remaining offices are added.

Grammar of the report:
  - Page furniture: 'Precinct Results ...', 'Ionia County, Michigan',
    'Registered Voters', 'NNNNNNNN Ionia County Election', the county
    turnout lines, 'Run Time/Run Date', 'Page N of 337', 'Election Day',
    'Choice Party Voting Total'.
  - Each page repeats its precinct's name below the furniture; the
    precinct's section is a stream of contest blocks:
    contest title (wrapping across up to 3 lines, completed by
    ' - Vote for not more than N' — with the count, the 'Vote for not
    more'/'than N', or a bare title completed by the 'Election Day'
    header as with the proposals), then 'Name PARTY v v% v v%' rows,
    'Name v v% v v%' rows for write-ins, and one-line or wrapped name
    continuations (running mates on President, '(W)' suffixes,
    multi-token surnames like 'James Robert'/'Redford').
  - 'Cast Votes: n ...', 'Undervotes: n ...', 'Overvotes: n ...' close
    each contest.

Verification: sum of a contest's candidate totals == its printed Cast
Votes in every precinct; per (office, district, candidate) sums against
the CENR county file for the offices it covers; the regenerated file's
covered rows are compared for equality against the committed CSV.
"""

import csv
import os
import re
import sys

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/general/'
       'Ionia-County-Official-Precinct-Results.pdf')
OUT = '2020/counties/20201103__mi__general__ionia__precinct.csv'
COUNTY_FILE = '2020/20201103__mi__general__county.csv'
COUNTY = 'Ionia'

PARTY_CODES = {'DEM', 'REP', 'LIB', 'UST', 'GRN', 'NLP', 'WCP', 'NON'}

# President rows keep the presidential candidate only; the source wraps
# running mates onto the name.  Full-name -> candidate.
PRES_MATES = {
    'Joseph R. Biden Kamala D. Harris': 'Joseph R. Biden',
    'Donald J. Trump Michael R. Pence': 'Donald J. Trump',
    'Jo Jorgensen Jeremy Cohen': 'Jo Jorgensen',
    'Don Blankenship William Mohr': 'Don Blankenship',
    'Howie Hawkins Angela Walker': 'Howie Hawkins',
    'Rocky De La Fuente Darcy Richardson': 'Rocky De La Fuente',
}

FURNITURE = [
    re.compile(r'^Precinct Results'),
    re.compile(r'^Official results$'),
    re.compile(r'^Ionia County, Michigan$'),
    re.compile(r'^Registered Voters$'),
    re.compile(r'^\d{8} Ionia County Election$'),
    re.compile(r'^\d+ of \d+ = \d+\.\d+ ?%$'),
    re.compile(r'^General Election$'),
    re.compile(r'^Run Time'),
    re.compile(r'^Run Date'),
    re.compile(r'^Polling Places Reporting$'),
    re.compile(r'^Page \d+ of \d+$'),
    re.compile(r'^Election Day$'),
    re.compile(r'^Choice Party Voting Total$'),
    re.compile(r'^Cast Votes:'),
    re.compile(r'^Undervotes:'),
    re.compile(r'^Overvotes:'),
]

VOTE_RE = re.compile(r'^(.+?) (\d[\d,]*) (\d+\.\d+)% (\d[\d,]*) (\d+\.\d+)%$')


def title_office(title):
    """Contest title -> (office, district)."""
    m = re.match(r'^Representative in Congress (\d+)', title)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^Representative in State Legislature (\d+)', title)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'^County Commissioner (\d+)', title)
    if m:
        return 'County Commissioner', m.group(1)
    if title == 'Straight Party Ticket':
        return 'Straight Party', ''
    if title.startswith('Electors of President'):
        return 'President', ''
    if title == 'United States Senator':
        return 'U.S. Senate', ''
    # the source pluralizes the university boards; other 2020 county
    # files (Keweenaw, Van Buren) use the singular forms
    if title == 'Regents of the University of Michigan':
        return 'Regent of the University of Michigan', ''
    if title == 'Trustees of Michigan State University':
        return 'Trustee of Michigan State University', ''
    if title == 'Governors of Wayne State University':
        return 'Governor of Wayne State University', ''
    return title, ''


def main():
    problems = []

    # committed file: precinct list + rows that must be reproduced
    old_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            os.pardir, OUT)
    old_rows = list(csv.DictReader(open(old_path)))
    precincts = {r['precinct'] for r in old_rows}

    def jurisdiction(prec):
        name = re.sub(r'\s+\d+$', '', prec)
        m = re.match(r'^Township of (.+)$', name)
        if m:
            return f'{m.group(1)} Township'
        return name

    pdf = pdfplumber.open(SRC)
    prec = None
    pending = None
    cur = None            # completed contest title
    contest_cands = []    # [(name, party, votes)] for the open contest
    cast = None
    under = over = None
    last_struct = None    # 'title' | 'cand' | 'votes'
    seq = []              # (prec, office, district, cand, party, votes)
    cast_tot = {}         # (prec, title) -> printed Cast Votes
    cand_tot = {}         # (prec, title) -> summed candidate totals

    def flush_contest():
        nonlocal cur, contest_cands, cast, under, over
        if cur is None:
            contest_cands = []
            cast = under = over = None
            return
        office, district = title_office(cur)
        for name, party, votes in contest_cands:
            seq.append((prec, office, district, name, party, votes))
        if under is not None:
            seq.append((prec, office, district, 'Under Votes', '', under))
        if over is not None:
            seq.append((prec, office, district, 'Over Votes', '', over))
        if cast is not None:
            cast_tot[(prec, cur)] = cast
            cand_tot[(prec, cur)] = sum(v for _n, _p, v in contest_cands)
        cur = None
        contest_cands = []
        cast = under = over = None

    for page in pdf.pages:
        for line in (page.extract_text() or '').split('\n'):
            line = line.strip()
            if not line:
                continue

            m = re.match(r'^Cast Votes: (\d[\d,]*) ', line)
            if m:
                cast = int(m.group(1).replace(',', ''))
                last_struct = 'votes'
                continue
            m = re.match(r'^Undervotes: (\d[\d,]*) ', line)
            if m:
                under = int(m.group(1).replace(',', ''))
                continue
            m = re.match(r'^Overvotes: (\d[\d,]*) ', line)
            if m:
                over = int(m.group(1).replace(',', ''))
                continue
            if line == 'Election Day':
                # completes a bare title with no Vote clause (proposals)
                if pending is not None:
                    cur = pending
                    pending = None
                last_struct = 'title'
                continue
            if any(rx.match(line) for rx in FURNITURE):
                continue
            if line in precincts:
                flush_contest()
                prec = line
                pending = None
                last_struct = None
                continue

            if pending is not None:
                if line == 'Election Day':
                    cur = pending
                    pending = None
                    last_struct = 'title'
                    continue
                pending = f'{pending} {line}'
                m = re.match(r'^(.*) - Vote for not more than \d+$', pending)
                if m:
                    cur = m.group(1)
                    pending = None
                last_struct = 'title'
                continue

            m = re.match(r'^(.*) - Vote for not more than \d+$', line)
            if m:
                flush_contest()
                cur = m.group(1)
                last_struct = 'title'
                continue
            m = re.search(r' - (Vote for not more|Vote for|Vote)$', line)
            if m or line.endswith(' -'):
                flush_contest()
                pending = line
                last_struct = 'title'
                continue
            # 'Election Day' never reaches here with a title open (the
            # pending branch handles it), so no completion needed.

            m = VOTE_RE.match(line)
            if m and cur is not None:
                name, votes = m.group(1), int(m.group(2).replace(',', ''))
                party = ''
                parts = name.rsplit(' ', 1)
                if len(parts) == 2 and parts[1] in PARTY_CODES:
                    name, party = parts[0], parts[1]
                    if party == 'NON':
                        party = ''
                contest_cands.append([name, party, votes])
                last_struct = 'cand'
                continue

            # wrapped candidate-name fragment: only directly after a
            # candidate row, so title fragments never weld onto names
            if cur is not None and contest_cands and last_struct == 'cand':
                contest_cands[-1][0] = \
                    f'{contest_cands[-1][0]} {line}'
                continue

            # otherwise: a bare title fragment (proposals, judges with
            # no 'Vote' clause, multi-line titles)
            flush_contest()
            pending = line
            last_struct = 'title'

    flush_contest()

    # President running mates -> candidate only
    fixed = []
    for prec_n, office, district, name, party, votes in seq:
        if office == 'President' and name in PRES_MATES:
            name = PRES_MATES[name]
        fixed.append((prec_n, office, district, name, party, votes))
    seq = fixed

    # bare township/city titles get their jurisdiction — except the
    # county Clerk/Treasurer, which print with the same bare titles in
    # every precinct (the county Clerk race sits right after Sheriff;
    # the township's block comes later, after Supervisor).  A bare-title
    # CONTEST whose candidate set is identical across all precincts is
    # the county race; the rest are the jurisdiction's own offices.
    bare = {'Supervisor', 'Clerk', 'Treasurer', 'Trustee', 'Council Member'}
    n_prec = len({r[0] for r in seq})
    # group rows into per-(prec, title) contest blocks
    blocks = []
    for i, (prec_n, office, district, name, party, votes) in enumerate(seq):
        if office not in bare:
            continue
        if not blocks or blocks[-1]['title'] != office \
                or blocks[-1]['prec'] != prec_n:
            blocks.append({'prec': prec_n, 'title': office, 'cands': set(),
                           'rows': []})
        blocks[-1]['cands'].add(name)
        blocks[-1]['rows'].append(i)
    county_sig = {}
    for t in bare:
        precs_by_sig = {}
        for b in blocks:
            if b['title'] == t:
                precs_by_sig.setdefault(frozenset(b['cands']),
                                        set()).add(b['prec'])
        for sig, precs in precs_by_sig.items():
            if len(precs) == n_prec:
                county_sig[t] = sig
    rename = set()
    for b in blocks:
        sig = county_sig.get(b['title'])
        if sig is not None and frozenset(b['cands']) == sig:
            rename.update(b['rows'])
    out = []
    for i, (prec_n, office, district, name, party, votes) in enumerate(seq):
        if i in rename:
            office = f'County {office}'
        elif office in bare:
            office = f'{jurisdiction(prec_n)} {office}'
        out.append((prec_n, office, district, name, party, votes))
    seq = out

    print(f'emitted {len(seq)} rows over {len({r[0] for r in seq})} '
          f'precincts, {len({(r[1], r[2]) for r in seq})} offices')

    # --- verification -------------------------------------------------
    bad = 0
    for key, want in sorted(cast_tot.items()):
        got = cand_tot.get(key)
        if got != want:
            bad += 1
            print(f'CAST MISMATCH {key}: candidates {got} != cast {want}')

    # county-file check for the offices it covers
    want = {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, COUNTY_FILE)
    for row in csv.DictReader(open(path)):
        if row['county'] != COUNTY:
            continue
        cand = row['candidate']
        if 'write' in cand.lower() and '(' not in cand:
            cand = 'Write-In'
        # the county file's President rows join candidate and running
        # mate (sometimes without the '/'): keep the candidate only
        if row['office'] == 'President':
            cand = cand.split('/')[0].strip()
            if cand == 'Donald J. TrumpMicheal R. Pence':
                cand = 'Donald J. Trump'
            if cand == 'Rocky DeLaFuente':
                cand = 'Rocky De La Fuente'
        want[(row['office'], row['district'], cand)] = \
            want.get((row['office'], row['district'], cand), 0) + \
            int(row['votes'])
    got = {}
    for _p, office, district, name, _party, votes in seq:
        if name in ('Under Votes', 'Over Votes'):
            continue
        got[(office, district, name)] = \
            got.get((office, district, name), 0) + votes
    mism = 0
    for key in sorted(set(want) | set(got)):
        if key not in want:
            continue    # county file covers only the 5 federal/state offices
        if got.get(key, 0) == want.get(key, 0):
            continue
        mism += 1
        print(f'MISMATCH {key}: parsed {got.get(key, 0)} '
              f'vs county {want.get(key, 0)}')
    print(f'county check: {len(want)} covered keys, {mism} mismatches')

    # covered offices must reproduce the committed rows exactly
    covered = {'Straight Party', 'President', 'U.S. Senate', 'U.S. House',
               'State House'}
    old = {}
    for r in old_rows:
        if r['office'] in covered:
            old[(r['precinct'], r['office'], r['district'], r['candidate'],
                 r['party'])] = int(r['votes'])
    new = {}
    for prec_n, office, district, name, party, votes in seq:
        if office in covered:
            new[(prec_n, office, district, name, party)] = votes
    if old == new:
        print('covered rows reproduce committed CSV exactly '
              f'({len(old)} keys)')
    else:
        only_old = set(old) - set(new)
        only_new = set(new) - set(old)
        diff = [k for k in set(old) & set(new) if old[k] != new[k]]
        bad += 1
        print(f'COVERED DIFF: {len(only_old)} old-only, '
              f'{len(only_new)} new-only, {len(diff)} value diffs')
        for k in list(only_old)[:5]:
            print('  old-only', k, old[k])
        for k in list(only_new)[:5]:
            print('  new-only', k, new[k])
        for k in diff[:5]:
            print('  diff', k, old[k], '->', new[k])

    if bad or mism:
        sys.exit(1)

    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district',
                    'candidate', 'party', 'votes'])
        for prec_n, office, district, name, party, votes in seq:
            w.writerow([COUNTY, prec_n, office, district, name, party,
                        votes])
    print(f'wrote {OUT}')


if __name__ == '__main__':
    main()