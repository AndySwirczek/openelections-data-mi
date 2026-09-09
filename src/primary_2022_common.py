"""Shared helpers for the 2022 primary county parsers (CSV, JSON, HTML, ...).

Classifies contest names from the various sources into the CENR's
(office, district, party) triples, verifies parsed per-precinct rows against
the certified county-level CENR (2022/20220802__mi__primary__county.csv), and
writes the standard per-county precinct CSVs.

Verification rules:
- every CENR (office, district, party, candidate) must be matched exactly,
  except zero-vote candidates the source omits entirely;
- a CENR candidate absent from the source but covered by the contest's lumped
  write-in total is a NOTE (qualified write-in buried in a lumped column);
- an `allowed` candidate absent from the source entirely (verified against
  the printed rows) is a NOTE, not a problem;
- a contest entirely absent from the source is a WARNING (source gap), not a
  blocking problem;
- lumped 'Write-In' rows are kept (the CENR carries no write-in rows for most
  counties) and show up as file-only extras, which is fine.

Usage: import from the per-format parser scripts.
"""
import csv
import re
import sys
from collections import defaultdict

CENR_CSV = '2022/20220802__mi__primary__county.csv'
OUT_DIR = '2022/counties'
HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
OFFICE_RANK = {'Governor': 0, 'U.S. House': 1, 'State Senate': 2, 'State House': 3,
               'Circuit Court Judge': 4, 'District Court Judge': 5}

CENR = defaultdict(dict)  # county -> (office, district, party, cand) -> votes
for _r in csv.DictReader(open(CENR_CSV)):
    CENR[_r['county']][( _r['office'], _r['district'], _r['party'], _r['candidate'])] = \
        int(_r['votes'].replace(',', '') or 0)


def one_space(text):
    return re.sub(r'\s+', ' ', text).strip()


def classify(contest):
    """(office, district, party-code) for a CENR-carried contest, else None.

    Handles the naming variants seen in the sources: 'Governor'/'Governor for
    State', 'Rep|Representative in Congress 2nd District'/'in Congress
    District 2'/'District for Rep in State Legislature 101st District',
    Calhoun's 'Senate 17th District'/'State Legislature Rep 44th District',
    Delta's trailing '(Vote for 1)', with a '(DEM)'/'(REP)' party tag."""
    s = one_space(contest)
    s = re.sub(r'\s*\(Vote for \d+\)\s*$', '', s)
    m = re.search(r'\((DEM|REP)\)\s*$', s)
    if not m:
        return None
    party = m.group(1)
    body = s[:m.start()].strip()
    if re.search(r'Governor', body, re.I):
        return ('Governor', '', party)
    dm = re.search(r'(\d+)(?:st|nd|rd|th)?\s*$', re.sub(r'\s+District\s*$', '', body))
    if dm:
        district = dm.group(1)
        head = body[:dm.start()].strip()
    else:
        # OCR sometimes moves the district to the front of the heading
        # ('106 th State Rep In Legislature (DEM)')
        dm = re.match(r'(\d+)(?:st|nd|rd|th)?\s+', body)
        if not dm:
            return None
        district = dm.group(1)
        head = body[dm.end():].strip()
    if re.search(r'Congress', head, re.I):
        return ('U.S. House', district, party)
    if re.search(r'Senat', head, re.I):  # 'State Senator' / Calhoun's 'Senate'
        return ('State Senate', district, party)
    if re.search(r'Legislature|State Representative', head, re.I):
        return ('State House', district, party)
    return None


def verify(county, rows, write_ins, allowed=None):
    """Print NOTE/WARNING lines and return a list of blocking problems.

    `allowed` maps (office, district, party, candidate) to a reason for
    known, source-internal discrepancies the parser has verified against
    the printed precinct rows (they become NOTEs, not problems)."""
    problems = []
    sums = defaultdict(int)
    for precinct, office, district, party, cand, votes in rows:
        sums[(office, district, party, cand)] += votes
    for key, want in CENR[county].items():
        got = sums.get(key)
        if want == 0 and got in (None, 0):
            continue  # zero-vote candidate (omitted or carried at 0)
        if got is None or got == 0:
            # either the candidate is absent or the source carries them at 0;
            # a qualified write-in the CENR names but the source only counts
            # inside its lumped 'Write-In' column can't be split out
            contest_present = any(k[0] == key[0] and k[1] == key[1] for k in sums)
            if not contest_present:
                print(f'WARNING {county}: contest {key[0]}[{key[1]}] absent from '
                      f'source (CENR {key[2]} {key[3]} {want}) — source gap')
            elif allowed and key in allowed:
                print(f'NOTE {county}: {key[2:]} ({want}) absent from source '
                      f'— {allowed[key]}')
            elif want and write_ins.get(key[:3], 0) >= want:
                print(f'NOTE {county}: {key[2:]} ({want}) is inside the lumped '
                      f'Write-In row ({write_ins[key[:3]]}); not emitted separately')
            else:
                problems.append(f'{county}: CENR candidate missing from source: '
                                f'{key} ({want})')
        elif got != want:
            if allowed and key in allowed:
                print(f'NOTE {county}: {key}: parsed {got} != CENR {want} '
                      f'— {allowed[key]}')
            else:
                problems.append(f'{county}: {key}: parsed {got} != CENR {want}')
    for key in sums:
        if key not in CENR[county] and key[3] != 'Write-In':
            problems.append(f'{county}: candidate not in CENR: {key} ({sums[key]})')
    return problems


def write(county, rows):
    # districts are numeric except lettered court districts ('14A'), which
    # sort after their base number
    def dist_key(district):
        m = re.match(r'(\d+)', district or '')
        return (int(m.group(1)) if m else 0, district or '')
    out = sorted(rows, key=lambda r: (r[0], OFFICE_RANK[r[1]], dist_key(r[2]), r[3], r[4]))
    out_path = f'{OUT_DIR}/20220802__mi__primary__{county.lower()}__precinct.csv'
    with open(out_path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for precinct, office, district, party, cand, votes in out:
            w.writerow([county, precinct, office, district, party, cand, votes])
    print('wrote', out_path)