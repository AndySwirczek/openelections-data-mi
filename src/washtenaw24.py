"""Parse Washtenaw County Aug 2024 primary canvass-report HTML into a precinct CSV.

Source layout: one HTML file per contest (canvassreport<N>.html from the
county's election-reporting site).  Each file has a summary header table
(Registered Voters / Ballots Cast countywide), a '<font class="h2">TITLE</font>'
line, a 'Vote For N' line, one results table whose <th> cells after
'PRECINCT NAME' are the candidates ('<name> (DEM)', 'Yes'/'No' for proposals,
'<name> (W)' for qualified write-in candidates, plus 'Rejected write-ins' and
'Unassigned write-ins' columns), one data row per precinct printing one number
per column (no early/absentee/election-day breakdown), and a closing 'Totals'
row whose values double as the source's own printed column totals.

The titles are internal abbreviations; they are expanded with TITLE_OFFICES /
PROPOSAL_OFFICES below (proposal wording per the county's official
"August 6, 2024 State Primary - Official List of Proposals" PDF).

The 2024 files decode as latin-1 despite declaring utf-8 ('Giuffré'), so text
is decoded as utf-8 with a latin-1 fallback.

Conventions follow the same county's 2026 parse (src/precinct_html_parser.py):
rejected + unassigned write-ins merge into one 'Write-In' row, and every
(precinct, contest) also emits a 'Ballots Cast' row equal to the candidate and
write-in sum.

Usage:
    .venv/bin/python src/washtenaw24.py \
        "/Users/dwillis/code/openelections-sources-mi/2024/primary/Washtenaw County Aug 2024 Primary Precinct Reports" \
        --out 2024/counties/20240806__mi__primary__washtenaw__precinct.csv
"""
import argparse
import csv
import glob
import html
import os
import re
import sys

COUNTY = 'Washtenaw'

# title suffix / candidate-tag -> party
TITLE_PARTY = {'Dem': 'DEM', 'Rep': 'REP',
               'DEMOCRATIC': 'DEM', 'REPUBLICAN': 'REP'}
CAND_TAG_RE = re.compile(r'^(.*?)\s*\((DEM|REP|W)\)$')

# abbreviated printed title -> (office, district)
TITLE_OFFICES = {
    'United States Senator': ('U.S. Senate', ''),
    'County Clerk Register': ('County Clerk & Register of Deeds', ''),
    'County Prosecuting Attorney': ('County Prosecuting Attorney', ''),
    'County Sheriff': ('County Sheriff', ''),
    'County Treasurer': ('County Treasurer', ''),
    'County Water Resources Comm': ('County Water Resources Commissioner', ''),
}
DISTRICT_OFFICES = [
    (re.compile(r'^US Congress D(\d+)$'), 'U.S. House'),
    (re.compile(r'^State Rep D(\d+)$'), 'State House'),
    (re.compile(r'^County Commissioner (\d+)$'), 'County Commissioner'),
]
WARD_NUM = {'1': '1st', '2': '2nd', '3': '3rd', '4': '4th', '5': '5th'}
COUNCIL_RE = re.compile(r'^(Ann Arbor|Ypsilanti) Council W(\d+)$')
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
    'Webster': 'Webster Township',
    'York Twp': 'York Township',
    'Ypsilanti Twp': 'Ypsilanti Township',
    # the Webster and York titles omit 'Twp' entirely
    'Webster': 'Webster Township',
    'York': 'York Township',
}
TWP_OFFICE_RE = re.compile(
    r'^(' + '|'.join(re.escape(t) for t in TOWNSHIPS) + r') '
    r'(Clerk|Supervisor|Treasurer|Trustee|Park Comm)$')
TWP_OFFICES = {'Clerk': 'Clerk', 'Supervisor': 'Supervisor',
               'Treasurer': 'Treasurer', 'Trustee': 'Trustee',
               'Park Comm': 'Park Commissioner'}

# abbreviated proposal title -> full wording (per the county's official
# "Official List of Proposals")
PROPOSAL_OFFICES = {
    'Washtenaw County Prop A Roads':
        'Washtenaw County Roads Millage Renewal and Restoration Proposal',
    'Washtenaw County Prop B Conservation':
        'Washtenaw County Conservation District Millage Renewal and '
        'Restoration Proposal',
    'Washtenaw County Prop C Parks':
        'Washtenaw County Parks and Recreation Millage Renewal and '
        'Restoration Proposal',
    'Bridgewater Twp Prop':
        'Bridgewater Township Fire Services Millage Renewal Proposal',
    'Saline Twp Prop':
        'Saline Township Road Maintenance Millage Renewal Proposal',
    'Scio Twp Prop':
        'Scio Township Transportation Millage Renewal and Restoration '
        'Proposal',
    'York Twp Prop':
        'York Township Police and Fire Safety Millage Renewal Proposal',
    'Webster Twp Prop A Roads': 'Webster Township Roads Millage Proposal',
    'Webster Twp Prop B Farmland':
        'Webster Township Farmland and Open Space Preservation Millage '
        'Renewal Proposal',
    'Stockbridge Sch Prop':
        'Stockbridge Community Schools Operating Millage Renewal Proposal',
    'Whitmore Lake Sch Prop': 'Whitmore Lake Public Schools Bond Proposal',
    'Manchester Dist Lib Prop':
        'Manchester District Library Millage Renewal Proposal',
    'Saline City Charter Prop':
        'City of Saline Charter Residency Requirements Amendment Proposal',
}

# the source prints these two qualified write-ins in all caps; the same people
# appear in proper case elsewhere on the ballot
CAPS_NAMES = {'JOHN DUNLAP': 'John Dunlap', 'ROBERT GUYSKY': 'Robert Guysky'}

NUM_RE = re.compile(r'^(\d+)%?$')


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
    if title in PROPOSAL_OFFICES:
        return PROPOSAL_OFFICES[title], ''
    if title in TITLE_OFFICES:
        return TITLE_OFFICES[title]
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
            party = TITLE_PARTY[m.group(1)]
            base = None          # office built per data-row precinct
        elif title in PROPOSAL_OFFICES:
            base, party = title, ''
        else:
            m = re.match(r'^(.*?) (Dem|Rep|DEMOCRATIC|REPUBLICAN)$', title)
            if not m:
                problems.append(f'{os.path.basename(path)}: unmappable title {title!r}')
                continue
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
                    vm = re.match(r'^(\d+)', clean(td))
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
            # column total), matching the county's 2026 parse
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

    # rewrite (W) qualified write-in candidate names
    for r in rows:
        cm = CAND_TAG_RE.match(r[5])
        if cm and cm.group(2) == 'W':
            base_name = CAPS_NAMES.get(cm.group(1), cm.group(1))
            r[5] = f'{base_name} Qualified Write In'

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