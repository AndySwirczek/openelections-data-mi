"""Parse Washtenaw County Aug 2020 primary canvass-report HTML into a precinct CSV.

Same source layout as the county's Aug 2024 files (src/washtenaw24.py): one
HTML file per contest (canvassreport<N>.html), a '<font class="h2">TITLE</font>'
line, a 'Vote For N' line, one results table whose <th> cells after
'PRECINCT NAME' are the candidates ('<name> (DEM)', 'Yes'/'No' for proposals,
plus 'Rejected write-ins' and 'Unassigned write-ins' columns), one data row per
precinct, and a closing 'Totals' row used for validation.

Differences from 2024: titles carry uppercase party tokens ('United States
Senator DEM'), delegate titles end in DEMOCRATIC/REPUBLICAN with internal
precinct codes (e.g. 'Delegate ANN ARBOR CITY 1001 DEMOCRATIC'), the two
nonpartisan offices ('Judge Circuit Court Non-incumbent', Ann Arbor/Ypsilanti
council wards) and the school proposals need their own mappings, and no (W)
qualified write-in columns exist in 2020 (checked across all 527 files).
Proposal titles are kept as printed.

Usage:
    .venv/bin/python src/washtenaw20.py \
        "/Users/dwillis/code/openelections-sources-mi/2020/primary/Washtenaw County Aug 2020 Primary Precinct Reports" \
        --out 2020/counties/20200804__mi__primary__washtenaw__precinct.csv
"""
import argparse
import csv
import glob
import html
import os
import re
import sys

COUNTY = 'Washtenaw'

TITLE_PARTY = {'DEM': 'DEM', 'REP': 'REP'}
CAND_TAG_RE = re.compile(r'^(.*?)\s*\((DEM|REP|W)\)$')

DISTRICT_OFFICES = [
    (re.compile(r'^Rep in Congress District (\d+)$'), 'U.S. House'),
    (re.compile(r'^Rep in State Leg Dist (\d+)$'), 'State House'),
    (re.compile(r'^County Commissioner (\d+)$'), 'County Commissioner'),
]
TITLE_OFFICES = {
    'United States Senator': ('U.S. Senate', ''),
    'County Clerk Register': ('County Clerk & Register of Deeds', ''),
    'County Prosecuting Attorney': ('County Prosecuting Attorney', ''),
    'County Sheriff': ('County Sheriff', ''),
    'County Treasurer': ('County Treasurer', ''),
    'County Water Res Comm': ('County Water Resources Commissioner', ''),
    'Judge Circuit Court Non-incumbent': ('Circuit Court Judge', ''),
}
WARD_NUM = {'1': '1st', '2': '2nd', '3': '3rd', '4': '4th', '5': '5th'}
COUNCIL_RE = re.compile(r'^(Ann Arbor|Ypsilanti) Council Ward (\d+)$')
DELEGATE_RE = re.compile(r'^Delegate .+ (DEMOCRATIC|REPUBLICAN)$')

# abbreviated township token in titles -> jurisdiction as spelled by the
# source's own precinct labels
TOWNSHIPS = {
    'Ann Arbor Twp': 'Ann Arbor Township',
    'Augusta Twp': 'Augusta Township',
    'Bridgewater Twp': 'Bridgewater Township',
    'Dexter Twp': 'Dexter Township',
    'Freedom Twp': 'Freedom Township',
    'Lima Twp': 'Lima Township',
    'Lodi Twp': 'Lodi Township',
    'Lyndon Twp': 'Lyndon Township',
    'Manchester Twp': 'Manchester Township',
    'Northfield Twp': 'Northfield Township',
    'Pittsfield Twp': 'Pittsfield Charter Township',
    'Salem Twp': 'Salem Township',
    'Saline Twp': 'Saline Township',
    'Scio Twp': 'Scio Township',
    'Sharon Twp': 'Sharon Township',
    'Superior Twp': 'Charter Township of Superior',
    'Sylvan Twp': 'Sylvan Township',
    'Webster Twp': 'Webster Township',
    'York Twp': 'York Township',
    'Ypsilanti Twp': 'Ypsilanti Township',
}
TWP_OFFICE_RE = re.compile(
    r'^(' + '|'.join(re.escape(t) for t in TOWNSHIPS) + r') '
    r'(Clerk|Supervisor|Treasurer|Trustee|Park Comm)$')
TWP_OFFICES = {'Clerk': 'Clerk', 'Supervisor': 'Supervisor',
               'Treasurer': 'Treasurer', 'Trustee': 'Trustee',
               'Park Comm': 'Park Commissioner'}

# proposal titles kept as printed
PROPOSALS = {
    'County Nonmotorized Prop',
    'County Conservation District Millage Proposal',
    'Freedom Twp Road Maint Millage Renewal',
    'Northfield Twp Police Proposal',
    'Saline Twp Renewal of Road Maintenance Millage',
    'Scio Twp Parks and Pathways Millage Proposal',
    'Pinckney Comm Sch Bonding Prop',
    'South Lyon Comm Sch Bonding Prop',
}


def clean(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text))).strip()


def read_text(path):
    data = open(path, 'rb').read()
    try:
        text = data.decode('utf-8')
        if '�' in text:
            raise UnicodeDecodeError('utf-8', b'', 0, 1, 'replacement char')
    except UnicodeDecodeError:
        text = data.decode('latin-1')
    return text


def map_office(title):
    """Printed title (minus party token) -> (office, district) or None."""
    if title in TITLE_OFFICES:
        return TITLE_OFFICES[title]
    if title in PROPOSALS:
        return title, ''
    for pat, office in DISTRICT_OFFICES:
        m = pat.match(title)
        if m:
            return office, m.group(1)
    m = COUNCIL_RE.match(title)
    if m:
        return f'Council Member {WARD_NUM[m.group(2)]} Ward', ''
    m = TWP_OFFICE_RE.match(title)
    if m:
        return f'{TOWNSHIPS[m.group(1)]} {TWP_OFFICES[m.group(2)]}', ''
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('directory')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    rows = []
    problems = []
    files = sorted(glob.glob(os.path.join(args.directory, 'canvassreport*.html')),
                   key=lambda p: int(re.search(r'(\d+)', os.path.basename(p)).group(1)))
    for path in files:
        text = read_text(path)
        m = re.search(r'<font class="h2">(.*?)</font>', text)
        title = clean(m.group(1)) if m else ''
        m = DELEGATE_RE.match(title)
        if m:
            party = TITLE_PARTY[m.group(1)[:3]]
            base = None          # office built per data-row precinct
        else:
            m = re.match(r'^(.*?) (DEM|REP)$', title)
            if not m:
                # proposals (and other nonpartisan contests) have no party token
                base, party = title, ''
            else:
                base, party = m.group(1), TITLE_PARTY[m.group(2)]

        hm = re.search(r'class="headertr">(.*?)</tr>', text, re.S)
        headers = [clean(c) for c in re.findall(r'<th[^>]*>(.*?)</th>', hm.group(1), re.S)]
        if headers[0] != 'PRECINCT NAME':
            problems.append(f'{os.path.basename(path)}: bad header start {headers[0]!r}')
        cands = headers[1:]
        for i, c in enumerate(cands):
            cm = CAND_TAG_RE.match(c)
            if cm and cm.group(2) != 'W':
                cands[i] = cm.group(1)
        is_proposal = cands == ['Yes', 'No']
        if is_proposal:
            party = ''
        office_district = None if base is None else map_office(base)
        if base is not None and office_district is None:
            problems.append(f'{os.path.basename(path)}: unmapped title {title!r}')
            continue

        # data rows and the Totals row
        totals = None
        sums = [0] * len(cands)
        seen = set()
        for chunk in text.split('<tr'):
            tds = re.findall(r'<td[^>]*>(.*?)</td>', chunk, re.S)
            if not tds:
                continue
            label = clean(tds[0])
            if label == 'Totals':
                vals = []
                for td in tds[1:]:
                    vm = re.match(r'^\D*?(\d+)', clean(td))
                    vals.append(int(vm.group(1)) if vm else None)
                totals = vals
                continue
            if label == 'PRECINCT NAME' or not re.match(
                    r'.*, Precinct \d+W?$', label):
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
            for i, n in enumerate(nums):
                sums[i] += n
            if base is None:     # delegate contest: office names the precinct
                office, district = f'{label} Delegate to County Convention', ''
            else:
                office, district = office_district
            # candidate rows; rejected + unassigned write-ins merge into one
            # Write-In row, and Ballots Cast = everything (the source's own
            # per-contest sum), matching the county's 2024 parse
            wi = 0
            for i, name in enumerate(cands):
                if name in ('Rejected write-ins', 'Unassigned write-ins'):
                    wi += nums[i]
                else:
                    rows.append([COUNTY, label, office, district, party,
                                 name, nums[i]])
            if wi:
                rows.append([COUNTY, label, office, district, party,
                             'Write-In', wi])
            rows.append([COUNTY, label, office, district, party,
                         'Ballots Cast', sum(nums)])
        if totals is None:
            problems.append(f'{os.path.basename(path)}: no Totals row')
        else:
            if len(totals) != len(cands):
                problems.append(f'{os.path.basename(path)}: Totals has '
                                f'{len(totals)} values, {len(cands)} columns')
            else:
                for i, (tv, s) in enumerate(zip(totals, sums)):
                    if tv != s:
                        problems.append(f'{os.path.basename(path)} {cands[i]!r}: '
                                        f'Totals {tv} != parsed sum {s}')
        if not seen:
            problems.append(f'{os.path.basename(path)}: no precinct rows')

    # rewrite (W) qualified write-in candidate names (none found in 2020 files)
    for r in rows:
        cm = CAND_TAG_RE.match(r[5])
        if cm and cm.group(2) == 'W':
            r[5] = f'{cm.group(1)} Qualified Write In'

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'{len(problems)} problems')
    for p in problems[:40]:
        print('PROBLEM:', p)
    print(f'Wrote {len(rows)} rows to {args.out}')


if __name__ == '__main__':
    sys.exit(main())