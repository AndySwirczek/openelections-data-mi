"""Parse Washtenaw County's Aug 2022 primary canvass-report HTML files into a
per-county precinct CSV, verified against the certified county-level CENR.

Source: 'openelections-sources-mi/2022/primary/Washtenaw County Aug 2022
Primary Precinct Reports/' — one HTML file per contest (canvassreport<N>.html
from the county's election-reporting site), the same layout as the county's
Aug 2024 reports (see src/washtenaw24.py): a '<font class="h2">TITLE</font>'
line, a results table whose <th> cells after 'PRECINCT NAME' are the
candidates ('<name> (DEM)', '<name> (Write-in)' for qualified write-ins, plus
'Rejected write-ins' and 'Unassigned write-ins' columns), one data row per
precinct, and a closing 'Totals' row. Some titles carry the party instead of
the candidate headers ('US Cong DEM', 'Judge of 14A District Court').

Only the contests the CENR certifies are emitted (Governor, U.S. House 6,
State Senate 14/15, State House 23/31/32/33/46/47/48, District Court Judge
14A); local contests (county commissioners, mayors, councils, township
offices, proposals) are skipped. Rejected + unassigned write-ins merge into
one lumped 'Write-In' row per precinct, as in the other 2022 primary counties.

Usage: .venv/bin/python src/primary_2022_washtenaw.py [--apply]
"""
import glob
import html
import os
import re
import sys
from collections import defaultdict

from primary_2022_common import CENR, one_space, verify, write

COUNTY = 'Washtenaw'
SOURCE_DIR = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
              "Washtenaw County Aug 2022 Primary Precinct Reports")
TITLE_PARTY = {'DEM': 'DEM', 'REP': 'REP'}

# printed title -> (office, district, party) for CENR-carried contests
TITLE_RE = [
    (re.compile(r'^Governor (DEM|REP)$'),
     lambda m: ('Governor', '', TITLE_PARTY[m.group(1)])),
    # all of Washtenaw's US House precincts are in district 6 this primary
    (re.compile(r'^US Cong (DEM|REP)$'),
     lambda m: ('U.S. House', '6', TITLE_PARTY[m.group(1)])),
    (re.compile(r'^State Senate (\d+) (DEM|REP)$'),
     lambda m: ('State Senate', m.group(1), TITLE_PARTY[m.group(2)])),
    (re.compile(r'^State Representative (\d+) (DEM|REP)$'),
     lambda m: ('State House', m.group(1), TITLE_PARTY[m.group(2)])),
    (re.compile(r'^Judge of (\d+A?) District Court$'),
     lambda m: ('District Court Judge', m.group(1), '')),
]

# source spellings -> CENR (certified) names
CAND_FIX = {
    'Glenn R. Morrison Jr.': 'Glenn R. Morrison Jr',
    'Jimmie Wilson Jr.': 'Jimmie Wilson, Jr.',
    'Roderick Casey Sr.': 'Roderick Casey, Sr.',
}

WRITE_IN_TAG = re.compile(r'^(.*?)\s*\((Write-in|W)\)$', re.I)
PARTY_TAG = re.compile(r'^(.*?)\s*\((DEM|REP)\)$')


def cand_name(name):
    """Column header -> candidate: strip '(DEM)'/'(REP)' party tags and
    '(Write-in)' qualified-write-in tags, then apply CAND_FIX spellings."""
    for pat in (WRITE_IN_TAG, PARTY_TAG):
        m = pat.match(name)
        if m:
            name = m.group(1)
            break
    return CAND_FIX.get(name, name)


def clean(text):
    return one_space(html.unescape(re.sub(r'<[^>]+>', ' ', text)))


def read_text(path):
    data = open(path, 'rb').read()
    try:
        text = data.decode('utf-8')
        if '�' in text:
            raise UnicodeDecodeError('utf-8', b'', 0, 1, 'replacement char')
    except UnicodeDecodeError:
        text = data.decode('latin-1')
    return text


def parse():
    rows = []
    skipped = defaultdict(int)
    write_ins = defaultdict(int)
    lumped = defaultdict(int)
    problems = []
    files = sorted(glob.glob(os.path.join(SOURCE_DIR, 'canvassreport*.html')),
                   key=lambda p: int(re.search(r'(\d+)', os.path.basename(p)).group(1)))
    for path in files:
        text = read_text(path)
        m = re.search(r'<font class="h2">(.*?)</font>', text)
        title = clean(m.group(1)) if m else ''
        mapped = None
        for pat, fn in TITLE_RE:
            tm = pat.match(title)
            if tm:
                mapped = fn(tm)
                break
        if mapped is None:
            skipped[title] += 1
            continue
        office, district, party = mapped

        hm = re.search(r'class="headertr">(.*?)</tr>', text, re.S)
        headers = [clean(c) for c in re.findall(r'<th[^>]*>(.*?)</th>', hm.group(1), re.S)]
        if headers[0] != 'PRECINCT NAME':
            problems.append(f'{os.path.basename(path)}: bad header start {headers[0]!r}')
            continue
        cands = headers[1:]

        totals = None
        sums = defaultdict(int)
        seen = set()
        for chunk in text.split('<tr'):
            tds = re.findall(r'<td[^>]*>(.*?)</td>', chunk, re.S)
            if not tds:
                continue
            label = clean(tds[0])
            if label == 'Totals':
                totals = [clean(td) for td in tds[1:]]
                continue
            if label == 'PRECINCT NAME' or not re.match(r'.*, Precinct \d+W?$', label):
                continue
            if len(tds) != len(cands) + 1:
                problems.append(f'{os.path.basename(path)} {label!r}: '
                                f'{len(tds) - 1} values for {len(cands)} columns')
                continue
            try:
                nums = [int(clean(td)) for td in tds[1:]]
            except ValueError:
                problems.append(f'{os.path.basename(path)} {label!r}: non-numeric row')
                continue
            if label in seen:
                problems.append(f'{os.path.basename(path)}: duplicate precinct {label!r}')
                continue
            seen.add(label)
            for i, name in enumerate(cands):
                if name in ('Rejected write-ins', 'Unassigned write-ins'):
                    continue
                wm = WRITE_IN_TAG.match(name)
                cand = cand_name(name)
                rows.append((label, office, district, party, cand, nums[i]))
                sums[(party, cand)] += nums[i]
            wi = sum(nums[i] for i, name in enumerate(cands)
                     if name in ('Rejected write-ins', 'Unassigned write-ins'))
            if wi:
                write_ins[(office, district, party)] += wi
                lumped[(label, office, district, party)] += wi
        # check the printed Totals row against our column sums
        if totals:
            for i, name in enumerate(cands):
                want = totals[i] if i < len(totals) else ''
                wm = re.match(r'^(\d+)', want)
                if not wm:
                    continue
                if name in ('Rejected write-ins', 'Unassigned write-ins'):
                    continue
                pm = PARTY_TAG.match(name)
                key = (pm.group(2) if pm else party, cand_name(name))
                if int(wm.group(1)) != sums.get(key, 0):
                    problems.append(f'{os.path.basename(path)} {name!r}: Totals '
                                    f'{wm.group(1)} != parsed sum {sums.get(key, 0)}')
        else:
            problems.append(f'{os.path.basename(path)}: no Totals row')
        if not seen:
            problems.append(f'{os.path.basename(path)}: no precinct rows')
    for (precinct, office, district, party), votes in lumped.items():
        if votes:
            rows.append((precinct, office, district, party, 'Write-In', votes))
    return rows, skipped, write_ins, problems


def main(apply=False):
    rows, skipped, write_ins, problems = parse()
    print(f'{COUNTY}: {len(rows)} rows; '
          f'{sum(skipped.values())} local rows skipped in {len(skipped)} contests')
    problems += verify(COUNTY, rows, write_ins)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')
    if apply:
        write(COUNTY, rows)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)