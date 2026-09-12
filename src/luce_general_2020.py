#!/usr/bin/env python3
"""Parse Luce County's Nov 2020 general election from 'Luce MI Summary.pdf'.

The source is a 41-page image-only scan of an ES&S "Election Summary
Report", parsed via the PaddleOCR markdown cache at
/tmp/paddleocr_md/Luce_MI_Summary (build with
`.venv/bin/python src/fetch_paddleocr_md.py <sources>/2020/general/'Luce MI Summary.pdf'`).

Grammar: the report is precinct-major — one section per precinct headed
'Summary for: All Contests, <Township>, Precinct N, ... All Counting
Groups', followed by a turnout table (Elector Group/Counting Group rows
for Election Day and Total), a 'Ballots Cast: N' line, then one
'Title (Vote for N)' heading (## or a centered <div>) plus one results
table per contest.  Result rows are candidate | party | votes | percent
(the party column sometimes collapses into a colspan=2 candidate cell,
and Times Cast rows may carry a stray '--').  Every table closes with a
'Total Votes' row.

Each precinct reports its counting groups combined ("All Counting
Groups"); the turnout table shows Election Day ballots == Total ballots
for all four precincts (the ICP absentee counting group counted zero),
so every vote is emitted under election_day with absentee 0.
"""

import csv
import glob
import html
import re
import sys

CACHE = '/tmp/paddleocr_md/Luce_MI_Summary'
OUT = '2020/counties/20201103__mi__general__luce__precinct.csv'
COUNTY = 'Luce'

PARTY_CODES = {'DEM', 'REP', 'LIB', 'UST', 'WCP', 'GRN', 'NLP'}

TIMES_KEY = '__times__'
TOTAL_KEY = 'Total Votes'
WRITEIN_KEY = 'Write-in'

# p031's State Proposal 20-1 table lost the Yes/No vote cells to OCR;
# the printed percentages and Total Votes recover them exactly
# (0.8466 * 1154 = 977.0, 0.1534 * 1154 = 177.0, 977 + 177 = 1154)
MANUAL_RESULTS = {
    ('State Proposal 20-1', 'McMillan Township, Precinct 1'):
        {'Yes': 977, 'No': 177},
}

# p039's two Court of Appeals headings were lost to OCR; the tables are
# identifiable by their first candidate (confirmed against p018/p028/p029)
FIRST_CAND_TITLE = {
    'Michael J. Kelly': 'Judge of Court of Appeals, 4th District - '
                        'Incumbent Position (Vote for 2)',
    'Michelle Rick': 'Judge of Court of Appeals, 4th District - '
                     'Non-Incumbent Position (Vote for 1)',
}


def ordinal(n):
    return {1: '1st', 2: '2nd', 3: '3rd'}.get(n, f'{n}th')


def map_office(title):
    """Map a printed contest title to (office, district)."""
    t = re.sub(r'\s*\(Vote for \d+\)\s*$', '', title.strip())
    m = re.match(r'^Representative in Congress,\s*(\d+)(?:st|nd|rd|th) District', t)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^State Representative,\s*(\d+)(?:st|nd|rd|th) District', t)
    if m:
        return 'State House', m.group(1)
    if t.startswith('President'):
        return 'President', ''
    if t == 'United States Senator':
        return 'U.S. Senate', ''
    m = re.match(r'^County Commissioner,\s*District (\d+)$', t)
    if m:
        return f'County Commissioner {ordinal(int(m.group(1)))} District', ''
    m = re.match(r'^State Proposal 20-(\d+)$', t)
    if m:
        return f'State Proposal 20-{m.group(1)}', ''
    m = re.match(r'^Township (Supervisor|Clerk|Treasurer|Trustee)'
                 r' for (.+?)(?:, Partial Term)?$', t)
    if m:
        return f'{m.group(2)} {m.group(1)}', ''
    m = re.match(r'^Village (.+?) for Village of (.+?)(?:, Partial Term)?$', t)
    if m:
        office = f'Village of {m.group(2)} Village {m.group(1)}'
        if 'Partial Term' in title:
            office += ' Partial'
        return office, ''
    m = re.match(r'^School Board for (.+)$', t)
    if m:
        return f'{m.group(1)} School Board Member', ''
    # county offices, boards of education/universities, judgeships keep
    # their printed title, minus the commas Lapeer-style office names lack
    t = t.replace(', 4th District', ' 4th District')
    t = t.replace(', 11th District', ' 11th District')
    t = t.replace(', 92nd District', ' 92nd District')
    t = t.replace(' - Incumbent Position', ' Incumbent')
    t = t.replace(' - Non-Incumbent Position', ' - Non Incumbent')
    return t, ''


def map_candidate(name, office):
    name = ' '.join(name.split())
    if name.lower() == 'write-in':
        return 'Write-Ins'
    if office == 'President':
        return name.split(' / ')[0]
    return name


def parse_events(md):
    """Yield ('summary', label) / ('title', text) / ('table', rows)
    / ('bc', n) events in document order."""
    for line in md.split('\n'):
        s = line.strip()
        if not s:
            continue
        if s.startswith('<table'):
            yield 'table', s
            continue
        m = re.match(r'^#{0,2}\s*(.+?\(Vote for \d+\))\s*$', s)
        if m:
            yield 'title', m.group(1)
            continue
        m = re.match(r'^<div[^>]*>(.+?)</div>$', s)
        if m:
            inner = m.group(1)
            if '(Vote for ' in inner:
                yield 'title', inner
            continue
        m = re.match(r'^Summary for:\s*All Contests,\s*(.+?),\s*Precinct (\d+)', s)
        if m:
            yield 'summary', f'{m.group(1)}, Precinct {m.group(2)}'
            continue
        m = re.match(r'^Ballots Cast:\s*([\d,]+)$', s)
        if m:
            yield 'bc', int(m.group(1).replace(',', ''))


def cells_of(row_html):
    """One <tr> as a list of (colspan, text)."""
    out = []
    for cm in re.finditer(r'<td([^>]*)>(.*?)</td>', row_html):
        attrs, text = cm.group(1), cm.group(2)
        col = re.search(r'colspan="?(\d+)', attrs)
        out.append((int(col.group(1)) if col else 1,
                    ' '.join(html.unescape(text).split())))
    return out


def expand(row):
    """Expand colspans of a single-cell run into plain strings."""
    out = []
    for col, text in row:
        out.extend([text] + [''] * (col - 1))
    return out


def is_pct(s):
    return s.endswith('%') or s in ('—', '--')


def parse_result_table(table_html, precinct, office, turnout,
                       problems, page_no):
    """Return {candidate: votes} for one contest table."""
    results = {}
    total_votes = None
    if (office, precinct) in MANUAL_RESULTS:
        return dict(MANUAL_RESULTS[(office, precinct)])
    for rm in re.finditer(r'<tr>(.*?)</tr>', table_html):
        row = cells_of(rm.group(1))
        texts = [t for _c, t in row]
        if not any(texts):
            continue
        flat = ' '.join(texts)
        if 'Times Cast' in texts:
            m = re.search(r'([\d,]+)\s*/\s*([\d,]+)', flat)
            if m and precinct in turnout:
                ballots = int(m.group(1).replace(',', ''))
                reg = int(m.group(2).replace(',', ''))
                ed, reg0 = turnout[precinct]
                if ballots != ed and not office.startswith(
                        ('County Commissioner', 'Village of')):
                    # district-restricted and village contests print a
                    # subset of the precinct's ballots
                    problems.append(f'p{page_no}: {precinct} {office!r} '
                                    f'Times Cast {ballots} != Ballots '
                                    f'Cast {ed}')
                if reg != reg0 and not office.startswith(
                        ('County Commissioner', 'Village of')):
                    problems.append(f'p{page_no}: {precinct} {office!r} '
                                    f'Times Cast registered {reg} != '
                                    f'{reg0}')
            continue
        if 'Elector Group' in texts:
            continue
        if texts[0] == 'Candidate' and 'Party' in texts:
            continue
        if re.search(r'\bTotal\b', flat) and not any(
                re.search(r'\d', t) for t in texts if '%' not in t
                and not is_pct(t)):
            continue  # bare Total header/footer rows
        m = re.search(r'Total Votes', flat)
        if m:
            nums = [t for t in texts[1:]
                    if re.fullmatch(r'[\d,]+', t or '')]
            if len(nums) == 1:
                total_votes = int(nums[0].replace(',', ''))
                continue
        # data row: candidate, [party], votes, percent — stray digits
        # occasionally OCR into the party column (p039 Krause '1'), so
        # the votes cell is the numeric immediately before the percent;
        # when OCR dropped the percent cell (p022 President), a single
        # leftover numeric is the votes
        exp = expand(row)
        cand = exp[0]
        rest = [t for t in exp[1:] if t]
        pct_i = next((i for i, t in enumerate(rest) if t.endswith('%')),
                     None)
        votes = None
        party = rest[0] if rest and rest[0] in PARTY_CODES else ''
        core = [t for t in rest if t != party]
        if pct_i:
            if re.fullmatch(r'[\d,]+', rest[pct_i - 1] or ''):
                votes = rest[pct_i - 1]
        elif len(core) == 1 and re.fullmatch(r'[\d,]+', core[0] or ''):
            votes = core[0]
        if not cand or votes is None:
            problems.append(f'p{page_no}: odd row in {office!r} '
                            f'({precinct}): {texts}')
            continue
        results[cand] = int(votes.replace(',', ''))
        if party and cand != TOTAL_KEY:
            parties.setdefault((office, cand), party)
    if total_votes is not None:
        got = sum(results.values())
        if got != total_votes:
            problems.append(f'p{page_no}: {precinct} {office!r} candidate '
                            f'sum {got} != Total Votes {total_votes}')
    return results


def parse_turnout_table(table_html, page_no, problems):
    """Election Day / Total rows -> (election_day_ballots, registered)."""
    ed = reg = None
    for rm in re.finditer(r'<tr>(.*?)</tr>', table_html):
        texts = [t for _c, t in cells_of(rm.group(1))]
        if 'Election Day' in texts:
            nums = [t for t in texts if re.fullmatch(r'[\d,]+', t or '')]
            if nums:
                ed = int(nums[0].replace(',', ''))
        if texts and texts[0] == '' and 'Total' in texts[1:2] + ['']:
            nums = [t for t in texts if re.fullmatch(r'[\d,]+', t or '')]
            if len(nums) >= 3:
                reg = int(nums[-1].replace(',', ''))
    if ed is None or reg is None:
        problems.append(f'p{page_no}: incomplete turnout table')
    return ed, reg


def main():
    problems = []
    rows = []            # (precinct, office, district, candidate, party, votes)
    turnout = {}         # precinct -> (ed, reg)
    precinct = None
    office = None
    seen_contests = {}   # precinct -> list of offices

    pages = sorted(glob.glob(f'{CACHE}/p*.md'),
                   key=lambda p: int(re.search(r'p(\d+)', p).group(1)))
    for path in pages:
        page_no = int(re.search(r'p(\d+)', path).group(1))
        md = open(path).read()
        for kind, payload in parse_events(md):
            if kind == 'summary':
                precinct = payload
                seen_contests[precinct] = []
            elif kind == 'title':
                office, district = map_office(payload)
            elif kind == 'bc':
                pass  # validated against the turnout table below
            elif kind == 'table':
                for name, title in FIRST_CAND_TITLE.items():
                    if name in payload:
                        office, district = map_office(title)
                texts = re.findall(r'<td[^>]*>(.*?)</td>', payload)
                texts = [' '.join(html.unescape(t).split()) for t in texts]
                if 'Elector Group' in texts:
                    ed, reg = parse_turnout_table(payload, page_no, problems)
                    turnout[precinct] = (ed, reg)
                    continue
                results = parse_result_table(payload, precinct, office,
                                             turnout, problems, page_no)
                for cand, votes in results.items():
                    key = (office, cand)
                    rows.append((precinct, office, district,
                                 map_candidate(cand, office),
                                 parties.get(key, ''), votes))
                seen_contests[precinct].append(office)

    # --- validation ------------------------------------------------------
    if len(turnout) != 4:
        problems.append(f'expected 4 precincts, saw {len(turnout)}')

    write_csv(rows, turnout)
    verify(rows)
    if problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)


parties = {}   # (office, printed candidate) -> party code, filled by main


def write_csv(rows, turnout):
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district',
                    'candidate', 'party', 'votes', 'election_day',
                    'absentee'])
        per_precinct = {}
        for r in rows:
            per_precinct.setdefault(r[0], []).append(r)
        for precinct, prows in per_precinct.items():
            ed, reg = turnout[precinct]
            w.writerow([COUNTY, precinct, 'Registered Voters', '', '',
                        '', reg, '', ''])
            w.writerow([COUNTY, precinct, 'Ballots Cast', '', '', '',
                        ed, ed, 0])
            for (_p, office, district, cand, party, votes) in prows:
                w.writerow([COUNTY, precinct, office, district, cand,
                            party, votes, votes, 0])


def verify(rows):
    """Parsed sums vs the county file's 30 LUCE rows."""
    county_file = '2020/20201103__mi__general__county.csv'
    covered = {'Straight Party', 'President', 'U.S. Senate', 'U.S. House',
               'State House'}
    got = {}
    for _p, office, district, cand, party, votes in rows:
        if office in covered:
            key = (office, district, cand)
            got[key] = got.get(key, 0) + votes
    want = {}
    for row in csv.DictReader(open(county_file)):
        if row['county'] != COUNTY:
            continue
        cand = row['candidate']
        if cand in ('Registered Voters', 'Ballots Cast'):
            continue
        if 'write' in cand.lower():
            cand = 'Write-Ins'
        key = (row['office'], row['district'], cand)
        want[key] = want.get(key, 0) + int(row['votes'])
    bad = 0
    for key in sorted(set(got) | set(want)):
        if got.get(key, 0) != want.get(key, 0):
            bad += 1
            print(f'  MISMATCH {key}: parsed {got.get(key, 0)} '
                  f'vs county {want.get(key, 0)}')
    print(f'verify: {len(got)} covered office keys, {bad} mismatches')


if __name__ == '__main__':
    main()