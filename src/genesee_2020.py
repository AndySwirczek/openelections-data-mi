"""Parse Genesee County's Aug 2020 primary 'Canvass Report'
(openelections-sources-mi/2020/primary/'Genesee MI Canvass Report-8-18-2020
15-41-59 PM.pdf', 454pp text layer) into
2020/counties/20200804__mi__primary__genesee__precinct.csv.

Contest-major ES&S layout: one table per contest, precincts as rows, columns
[candidates...] Cast Votes Undervotes Overvotes Invalid Votes Rejected
write-in votes Unresolved write-in votes Election Day Voting Ballots Cast
Total Ballots Cast Registered Voters Turnout Percentage.  Candidate headers
are rotated 90 degrees and pdftotext folds the vertical strips into lines in
arbitrary order, so candidate names AND their column order come from a
pdfplumber pass (one strip per x position, read bottom-up with each word's
letters reversed — Livingston 2020's header_orders; the strip order IS the
value-column order).  Contest titles carry the party ('- Democratic Party -
Vote for not more than 1').  Precinct labels wrap around the values row:
'Argentine Township, Precinct' <values> '1' and 'Grand Blanc Township,'
<values> 'Precinct 15'.  Uncontested township tables print NO candidate
columns (Cast Votes 0) and contribute no rows.  'Totals' rows close each
table and are validated as column sums.  Ballots Cast / Registered Voters
pseudo-office rows are emitted once per precinct from the Total Ballots Cast
/ Registered Voters columns, which repeat identically in every contest.
"""
import os
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import map_office, normalize_precinct, write_csv

PDF = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Genesee MI Canvass Report-8-18-2020 15-41-59 PM.pdf')
TXT = '/tmp/genesee.txt'
COUNTY = 'Genesee'

FURNITURE = re.compile(
    r'^(?:Canvass Report\b.*Official results$'
    r'|Genesee County, Michigan$'
    r'|Registered Voters$'
    r'|\d+ of \d+ = [\d,.]+ ?%$'
    r'|Election Day\b'
    r'|Primary Election, .*$'
    r'|Precincts Reporting$'
    r'|Run Time\b|Run Date\b'
    r'|8/4/2020$'
    r'|Page \d+ of \d+$)')

TITLE_RE = re.compile(
    r'^(.*?) - (Democratic|Republican|Nonpartisan|Libertarian|Green|UST) '
    r'Party(?: - Vote for (?:no|not) more than \d+)?$')

PARTY_MAP = {'Democratic': 'DEM', 'Republican': 'REP', 'Nonpartisan': '',
             'Libertarian': 'LIB', 'Green': 'GRN', 'UST': 'UST'}

# a values row: optional label prefix, >=7 numbers, optional trailing turnout
# pct (printed as '34.51 %' with a space before the percent sign)
VALUES_LABELLED = re.compile(
    r'^([A-Za-z].*?)[ \t]{2,}((?:[\d,]+[ \t]+){6,})([\d,]+)'
    r'(?:[ \t]+([\d.]+) ?%[ \t]*)?$')
VALUES_BARE = re.compile(
    r'^[ \t]*((?:[\d,]+[ \t]+){6,})([\d,]+)'
    r'(?:[ \t]+([\d.]+) ?%[ \t]*)?$')
# overflow-table rows for wide contests, printed after the main Totals.
# Flint Twp style clips Registered Voters (label + 1 number + turnout %);
# Mt. Morris style clips Registered Voters AND Total Ballots Cast (2 numbers
# + %).  Bare forms occur when the precinct label wraps around the values.
OVERFLOW_L2 = re.compile(
    r'^([A-Za-z].*?)[ \t]{2,}([\d,]+)[ \t]+([\d,]+)[ \t]+(\d+\.\d+) ?%$')
OVERFLOW_B2 = re.compile(r'^([\d,]+)[ \t]+([\d,]+)[ \t]+(\d+\.\d+) ?%$')
OVERFLOW_L1 = re.compile(r'^([A-Za-z].*?)[ \t]{2,}([\d,]+)[ \t]+(\d+\.\d+) ?%$')
OVERFLOW_B1 = re.compile(r'^([\d,]+)[ \t]+(\d+\.\d+) ?%$')


def overflow_numbers(s):
    """(label, [n1(, n2)]) for an overflow-table row, else None.

    2-number patterns are tried first: the lazy 1-number label could
    otherwise skip the first number of a 2-number row.
    """
    for rx, grouped in ((OVERFLOW_L2, True), (OVERFLOW_B2, False),
                        (OVERFLOW_L1, True), (OVERFLOW_B1, False)):
        m = rx.match(s)
        if m:
            # labelled forms lead with the precinct-label group; the last
            # group is always the turnout percentage
            gs = m.groups()[1:] if grouped else m.groups()
            return (m.group(1).strip() if grouped else ''), [
                int(g.replace(',', '')) for g in gs[:-1]]
    return None


# turnout-only overflow row (the main table kept Registered Voters):
# 'Genesee Township, Precinct 1   22.50 %'
PCT_ONLY_ROW = re.compile(r'^(?:[A-Za-z].*?[ \t]{2,})?[\d.]+ ?%$')

# rotated-header captions stripped from the candidate strips (reversed forms)
CAPTIONS = ('Cast Votes', 'Undervotes', 'Overvotes', 'Invalid Votes',
            'Rejected write-in votes', 'Unresolved write-in votes',
            'Election Day Voting Ballots Cast', 'Total Ballots Cast',
            'Registered Voters', 'Turnout Percentage', 'Precinct')
CAPTION_WORDS = {w[::-1] for cap in CAPTIONS for w in cap.split()}

# summary columns after the candidates: Cast, Under, Over, Invalid,
# Rejected, Unresolved, ED Ballots, Total Ballots, Registered
NSUMMARY = 9

JUDGE_RE = re.compile(
    r'^Judge of (Circuit|District|Probate) Court (.+?) '
    r'(?:Non-)?Incumbent Position$')


def parse_values(s):
    """(label, numbers-string, trailing-pct-or-None)."""
    m = VALUES_LABELLED.match(s)
    if m:
        return m.group(1).strip(), m.group(2) + m.group(3), m.group(4)
    m = VALUES_BARE.match(s)
    if m:
        return '', m.group(1) + m.group(2), m.group(3)
    return None


def split_title(title):
    m = TITLE_RE.match(title)
    if not m:
        return None
    office_text = ' '.join(m.group(1).split())
    party = PARTY_MAP[m.group(2)]
    j = JUDGE_RE.match(office_text)
    if j:
        court, dist = j.group(1), j.group(2)
        # '7th Circuit' / '67-5A District': drop the court word, then any
        # ordinal suffix ('7th' -> 7, '67-5A' -> 67-5)
        num = re.sub(r'[A-Za-z]+$', '', dist.split()[0])
        if court == 'Circuit':
            return 'Circuit Court Judge', num, party
        if court == 'District':
            return 'District Court Judge', num, party
        return 'Probate Court Judge', '', party
    cm = re.match(r'County Commissioner (\d+)[A-Za-z]{0,2} District$',
                  office_text)
    if cm:
        return 'County Commissioner', cm.group(1), party
    dm = re.search(r'(\d+)(?:st|nd|rd|th) District$', office_text)
    division = dm.group(0) if dm else ''
    base = re.sub(r'\s*(?:\d+(?:st|nd|rd|th) )?District$', '',
                  office_text).strip()
    office, district = map_office(base, division, COUNTY)
    if not district and dm:
        district = dm.group(1)
    return office, district, party


def header_orders():
    """{normalized title: [candidate names in true left-to-right order]}"""
    import pdfplumber
    orders = {}
    with pdfplumber.open(PDF) as pdf:
        for page in pdf.pages:
            words = page.extract_words() or []
            lines = {}
            for w in words:
                lines.setdefault(round(w['top']), []).append(w)
            for t in sorted(lines):
                txt = ' '.join(w['text'] for w in sorted(
                    lines[t], key=lambda w: w['x0'])).strip()
                if not TITLE_RE.match(txt):
                    continue
                precs = [t2 for t2 in sorted(lines) if t2 > t and any(
                    w['text'] == 'Precinct' and w['x0'] < 25
                    for w in lines[t2])]
                if not precs:
                    continue
                zone = [w for t2 in lines for w in lines[t2]
                        if t + 2 < t2 < precs[0] - 1]
                bins = {}
                for w in zone:
                    bins.setdefault(round(w['x0'] / 3), []).append(w)
                names = []
                for b in sorted(bins):
                    ws = sorted(bins[b], key=lambda w: -w['top'])
                    if {w['text'] for w in ws} <= CAPTION_WORDS:
                        continue
                    names.append((bins[b][0]['x0'],
                                  ' '.join(w['text'][::-1] for w in ws)))
                if names:
                    key = ' '.join(txt.split())
                    orders.setdefault(key, [n for _, n in sorted(names)])
    return orders


def parse():
    if not os.path.exists(TXT):
        subprocess.run(['pdftotext', '-layout', PDF, TXT], check=True)
    with open(TXT) as fh:
        lines = fh.read().splitlines()

    orders = header_orders()
    rows = []
    problems = []
    pseudo = {}         # precinct -> {'Ballots Cast': n, 'Registered Voters': n}
    contest = None      # dict with office/district/party/candidates/sums
    contest_precincts = {}
    seen_titles = set()  # keys whose main table closed with a Totals row
    in_zone = False
    pending_label = ''
    i, n = 0, len(lines)

    def merge_pseudo(cont):
        # only countywide contests carry the precinct-wide Ballots Cast /
        # Registered Voters figures; partial-electorate tables (school
        # district proposals printed only for the split precinct's
        # district residents) report smaller numbers
        odp = (cont['office'], cont['district'], cont['party'])
        if len(contest_precincts.get(odp, ())) <= 100:
            return
        for pname, vals in cont['cp'].items():
            for prec, v in vals.items():
                got = pseudo.setdefault(prec, {})
                if got.setdefault(pname, v) != v:
                    problems.append(
                        f'{pname} {got[pname]} != {v} for {prec!r} in '
                        f'{cont["title"]!r}')

    def open_contest(title):
        nonlocal contest, in_zone, pending_label
        if contest is not None and contest['title'] != title \
                and not contest.get('overflow'):
            problems.append(
                f'contest {contest["title"]!r} closed without Totals at '
                f'line {i}')
        if contest is not None and contest['title'] == title:
            in_zone = True
            pending_label = ''
            return
        key = ' '.join(title.split())
        parsed = split_title(title)
        if contest is None and key in seen_titles:
            # a re-print after the table's own Totals is the wide-contest
            # overflow table (Total Ballots Cast / Registered Voters /
            # Turnout Percentage)
            contest = {'title': title, 'key': key, 'overflow': True,
                       'ovsums': None, 'odp': parsed}
            in_zone = True
            pending_label = ''
            return
        if parsed is None:
            problems.append(f'unparsed title: {title!r}')
            contest = None
        else:
            office, district, party = parsed
            contest = {'title': title, 'office': office, 'district': district,
                       'party': party, 'cands': orders.get(key, []),
                       'sums': None, 'nsum': None, 'key': key, 'cp': {}}
        in_zone = True
        pending_label = ''

    while i < n:
        raw = lines[i]
        s = raw.strip()
        i += 1
        if not s or FURNITURE.match(s):
            continue
        if TITLE_RE.match(s):
            open_contest(s)
            continue
        if contest is None:
            problems.append(f'line outside any contest: {s!r}')
            continue
        if in_zone:
            if re.match(r'^Precinct(?:\s+[A-Za-z][A-Za-z .,]*)?$', s):
                in_zone = False
            elif any(ch.isdigit() for ch in s):
                # caption lines are noise, but a digit-bearing line here is
                # something misread
                problems.append(f'unexpected header-zone line in '
                                f'{contest["title"]!r}: {s!r}')
            continue
        vm = parse_values(s)
        if vm is None:
            # wide contests clip their last summary columns into an overflow
            # table re-printed after the main Totals: 'Flint Township,
            # Precinct 1   1821  46.07 %' (Registered Voters) or Mt. Morris
            # style '216  993  21.75 %' (Total Ballots Cast + Registered)
            if contest is not None and contest.get('overflow'):
                ov = overflow_numbers(s)
                if ov is not None:
                    label, nums = ov
                    if label.lower().startswith('totals'):
                        if contest['ovsums'] is not None \
                                and nums != contest['ovsums']:
                            problems.append(
                                f'overflow Totals {nums} != accumulated '
                                f'{contest["ovsums"]} for '
                                f'{contest["title"]!r}')
                        contest['ovsums'] = None
                    elif contest['ovsums'] is None:
                        contest['ovsums'] = list(nums)
                    elif len(nums) != len(contest['ovsums']):
                        problems.append(
                            f'{len(nums)} overflow fields vs '
                            f'{len(contest["ovsums"])} for '
                            f'{contest["title"]!r}')
                    else:
                        contest['ovsums'] = [a + v for a, v in
                                             zip(contest['ovsums'], nums)]
                    continue
                if PCT_ONLY_ROW.match(s) or not any(ch.isdigit() for ch in s) \
                        or re.match(r'^\d+$', s):
                    # bare precinct-label fragments of wrapped overflow rows
                    continue
                if PCT_ONLY_ROW.match(s):
                    # turnout-only overflow: nothing to sum or validate
                    continue
            # a lone line just before a values row is a wrapped label prefix
            if i < n and parse_values(lines[i].strip()) is not None:
                pending_label = s
            else:
                problems.append(f'ignored line in {contest["title"]!r}: {s!r}')
            continue
        label, nums, _pct = vm
        fields = [int(x.replace(',', '')) for x in nums.split()]
        ncand = len(contest['cands'])
        nsum = contest.get('nsum')
        if nsum is None:
            # wide contests clip the Registered Voters column into an
            # overflow table: 9 summary columns shrink to 8 (Mt. Morris
            # trustee style even clips Total Ballots Cast too, leaving 7)
            nsum = next((k for k in (9, 8, 7) if len(fields) == ncand + k),
                        None)
            if nsum is None:
                problems.append(f'{len(fields)} fields vs {ncand} candidates '
                                f'for {contest["title"]!r} at {s[:60]!r}')
                contest = None
                continue
            contest['nsum'] = nsum
        elif len(fields) != ncand + nsum:
            problems.append(f'{len(fields)} fields vs {ncand} candidates + '
                            f'{nsum} for {contest["title"]!r} at {s[:60]!r}')
            contest = None
            continue
        if label.lower().startswith('totals'):
            expected = ([sum(c) for c in contest['sums']]
                        if contest['sums'] else [0] * (ncand + nsum))
            if fields != expected:
                problems.append(f'Totals mismatch for {contest["title"]!r} '
                                f'{contest["cands"]}: {fields} != '
                                f'{expected}')
            merge_pseudo(contest)
            contest['sums'] = None
            seen_titles.add(contest['key'])
            contest = None
            continue
        if label and not label.rstrip().endswith(','):
            precinct, incomplete = label, label.endswith('Precinct')
        else:
            precinct = (pending_label + ' ' + label).strip()
            incomplete = precinct.endswith(',') or \
                precinct.endswith('Precinct')
        if incomplete:
            # the label completes on the next non-furniture line
            j = i
            while j < n and (not lines[j].strip()
                             or FURNITURE.match(lines[j].strip())):
                j += 1
            if j < n:
                frag = lines[j].strip()
                if (not TITLE_RE.match(frag)
                        and not re.match(r'^Precinct(?:\s+[A-Za-z]+.*)?$',
                                         frag)
                        and parse_values(frag) is None):
                    precinct = (precinct + ' ' + frag).strip()
                    i = j + 1
        pending_label = ''
        if precinct.endswith(',') or precinct.endswith('Precinct'):
            problems.append(f'unresolved precinct label {precinct!r} in '
                            f'{contest["title"]!r}')
            continue
        precinct = normalize_precinct(precinct)
        cand_vals = fields[:ncand]
        cast, under, over = fields[ncand], fields[ncand + 1], fields[ncand + 2]
        if cast != sum(cand_vals):
            problems.append(f'Cast Votes {cast} != candidate sum '
                            f'{sum(cand_vals)} for {contest["title"]!r} at '
                            f'{precinct!r}')
        if contest['sums'] is None:
            contest['sums'] = [[v] for v in fields]
        else:
            for acc, v in zip(contest['sums'], fields):
                acc.append(v)
        contest_precincts.setdefault(
            (contest['office'], contest['district'], contest['party']),
            set()).add(precinct)
        office, district, party = (contest['office'], contest['district'],
                                   contest['party'])
        if ncand == 0:
            # uncontested tables print no candidate columns; the source
            # carries no attributable votes (Cast Votes is 0)
            if cast:
                problems.append(f'zero-candidate contest with Cast Votes '
                                f'{cast} for {contest["title"]!r} at '
                                f'{precinct!r}')
        else:
            for name, v in zip(contest['cands'], cand_vals):
                rows.append({'county': COUNTY, 'precinct': precinct,
                             'office': office, 'district': district,
                             'party': party, 'candidate': name, 'votes': v})
        cp = contest['cp']
        if nsum >= 8:
            tb = fields[ncand + 7]
            got = cp.setdefault('Ballots Cast', {})
            if got.setdefault(precinct, tb) != tb:
                problems.append(f'Ballots Cast {got[precinct]} != {tb} for '
                                f'{precinct!r} in {contest["title"]!r}')
        if nsum == 9:
            rv = fields[ncand + 8]
            got = cp.setdefault('Registered Voters', {})
            if got.setdefault(precinct, rv) != rv:
                problems.append(f'Registered Voters {got[precinct]} != {rv} '
                                f'for {precinct!r} in {contest["title"]!r}')
    if contest is not None:
        problems.append(f'contest {contest["title"]!r} never closed')

    # every countywide contest must cover the same precinct set
    wide = {k: v for k, v in contest_precincts.items() if len(v) > 100}
    if wide:
        master = max(wide.values(), key=len)
        for k, v in sorted(wide.items()):
            if v != master:
                problems.append(f'precinct set differs for {k}: '
                                f'{sorted(master - v)[:5]} missing, '
                                f'{sorted(v - master)[:5]} extra')
        print(f'countywide contests cover {len(master)} precincts')
    for precinct, got in sorted(pseudo.items()):
        for pname, v in got.items():
            rows.append({'county': COUNTY, 'precinct': precinct,
                         'office': pname, 'district': '', 'party': '',
                         'candidate': '', 'votes': v})
    print(f'{len(contest_precincts)} contests, {len(pseudo)} precincts')
    return rows, problems


def main():
    rows, problems = parse()
    for p in problems:
        print('PROBLEM:', p)
    if problems:
        sys.exit(f'{len(problems)} problems')
    write_csv(COUNTY, rows)


if __name__ == '__main__':
    main()