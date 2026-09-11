"""Parse Livingston County's Aug 2020 primary 'Statement of Votes Cast'
(openelections-sources-mi/2020/primary/'Livingston MI Statement-of-Votes-
Cast.pdf', 270pp) into 2020/counties/20200804__mi__primary__livingston__
precinct.csv.

Contest-major ES&S layout: one table per candidate set, each listing all 80
precincts with columns [candidates...] Cast Votes Undervotes Overvotes
Invalid Votes, then Absentee/Election-Day/Total Ballots Cast, Registered
Voters and a turnout % (repeated in every table; only the contest-level
Cast Votes group is used).  Candidate headers wrap across 2-3 lines with the
names interleaved between the turnout-column captions, so the whole zone
between the contest title and the 'Precinct' line is token-stripped and
split on wide gaps.  Contest titles carry the party ('- Democratic - Vote
for not more than 1').  Precinct labels wrap around the values row
('Brighton Charter Township,' <values> 'Precinct 1'); a label that ends
with ',' or 'Precinct' continues on the line after the values.  'Totals'
rows close each table and are validated as column sums.  Each (office,
party) contest gets 'Ballots Cast' rows (Cast+Undervotes+Overvotes+Invalid)
per the SOVC convention in the other 2020 files.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import map_office, normalize_precinct, write_csv

TXT = '/tmp/livingston.txt'
PDF = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Livingston MI Statement-of-Votes-Cast.pdf')
COUNTY = 'Livingston'

FURNITURE = re.compile(
    r'^(?:Statement of Votes Cast\b.*Official Results$'
    r'|.*Livingston County, Michigan$'
    r'|Registered Voters$'
    r'|\d+ of \d+ = [\d.]+%$'
    r'|Election Night\b'
    r'|Primary Election, .*$'
    r'|Precincts Reporting$'
    r'|Run Time\b|Run Date\b'
    r'|8/4/2020$'
    r'|Page \d+$)')

TITLE_RE = re.compile(
    r'^(.*?) - (Democratic|Republican|Nonpartisan|Libertarian|Green|UST|'
    r'Libertarian Party|Natural Law|Working Class)'
    r'(?: - Vote for (?:no|not) more than \d+)?$')

# tokens stripped from the candidate-header zone (also the wrapped turnout
# captions); whatever remains, split on 2+ spaces, is candidate names
HEADER_TOKENS = ('Absentee Voting Ballots Cast', 'Election Day Voting '
                 'Ballots Cast', 'Total Ballots Cast', 'Registered Voters',
                 'Turnout Percentage', 'Cast Votes', 'Undervotes',
                 'Overvotes', 'Invalid Votes')

CAND_SPLIT = re.compile(r'\s{2,}')

# a values row: optional label prefix, N numbers (candidates + Cast/Under/
# Over/Invalid + Absentee/ElectionDay/Total/Registered), optional turnout %
# (some wide tables clip it).  At least 7 numbers so label fragments like
# 'Precinct 1' never match.  The label must start with a non-digit so a
# leading value cell is never eaten as the label on label-less rows.
VALUES_LABELLED = re.compile(
    r'^([A-Za-z].*?)[ \t]{2,}((?:[\d,]+[ \t]+){6,})([\d,]+)'
    r'(?:[ \t]+([\d.]+)%[ \t]*)?$')
VALUES_BARE = re.compile(
    r'^[ \t]*((?:[\d,]+[ \t]+){6,})([\d,]+)'
    r'(?:[ \t]+([\d.]+)%[ \t]*)?$')


def parse_values(s):
    """(label, numbers-string, trailing-pct-or-None); the turnout % is not
    part of the numbers string."""
    m = VALUES_LABELLED.match(s)
    if m:
        return m.group(1).strip(), m.group(2) + m.group(3), m.group(4)
    m = VALUES_BARE.match(s)
    if m:
        return '', m.group(1) + m.group(2), m.group(3)
    return None

PARTY_MAP = {'Democratic': 'DEM', 'Republican': 'REP', 'Nonpartisan': '',
             'Libertarian': 'LIB', 'Green': 'GRN', 'UST': 'UST',
             'Libertarian Party': 'LIB', 'Natural Law': 'NL',
             'Working Class': 'WC'}

CC_RE = re.compile(r'^County Commissioner (\d+)[A-Za-z]{0,2} District (.+)$')

# Every candidate header in this PDF is ROTATED 90 degrees; pdftotext folds
# the vertical name strips into horizontal lines in an arbitrary order, so
# candidate order cannot be recovered from the text layer (CENR caught the
# U.S. House 8 / State House 47 permutations).  This pass re-reads the PDF
# with pdfplumber and reconstructs each contest's names in true left-to-right
# column order: one vertical strip of words per x position, read bottom-up
# with each word's letters reversed.
SUMMARY_CAPTIONS = ('Cast Votes', 'Undervotes', 'Overvotes', 'Invalid Votes',
                    'Absentee Voting Ballots Cast',
                    'Election Day Voting Ballots Cast', 'Total Ballots Cast',
                    'Registered Voters', 'Turnout Percentage', 'Precinct')
CAPTION_WORDS = {w[::-1] for cap in SUMMARY_CAPTIONS for w in cap.split()}


def header_orders():
    """{normalized title: [candidate names in column order]}"""
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


def norm_title(s):
    return ' '.join(s.split())


def split_title(title):
    m = TITLE_RE.match(title)
    if not m:
        return None
    office_text = m.group(1).strip().replace(' Of ', ' of ')
    party = PARTY_MAP.get(m.group(2), '')
    district = ''
    cc = CC_RE.match(office_text)
    if cc:
        # term-qualified CC contests keep the full title as the office name
        # (2026 Livingston / Hillsdale 2020 style) with the number as district
        office, district = office_text, cc.group(1)
    else:
        # hand map_office the district fragment so U.S. House / State House
        # numbers come from the title, not the county default
        dm = re.search(r'(\d+)(?:st|nd|rd|th) District$', office_text)
        division = dm.group(0) if dm else ''
        base = re.sub(r'\s*(?:\d+(?:st|nd|rd|th) )?District$', '',
                      office_text).strip()
        office, district = map_office(base, division, COUNTY)
        if not district and dm:
            district = dm.group(1)
    return office, district, party


def parse():
    rows = []
    problems = []
    orders = header_orders()
    contest = None      # (office, district, party)
    title = None
    candidates = []
    in_header_zone = False
    has_invalid = None
    has_registered = None   # wide tables clip Registered Voters + turnout %
    pending_label = ''
    table_sums = None   # per-candidate sums for the current table
    last_table_key = None
    skipped = []        # overflow-table label fragments / turnout-only lines

    def strip_header_tokens(line):
        for tok in HEADER_TOKENS:
            line = line.replace(tok, ' ')
        return [n for n in CAND_SPLIT.split(line.strip()) if n]

    with open(TXT) as fh:
        lines = fh.read().splitlines()

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()
        i += 1
        if not s or FURNITURE.match(s):
            continue
        m = TITLE_RE.match(s)
        if m:
            contest = split_title(s)
            if contest is None:
                problems.append(f'unparsed title: {s!r}')
            title = s
            candidates = []
            pending_label = ''
            in_header_zone = True
            has_invalid = None
            has_registered = None
            continue
        if s == 'Precinct':
            in_header_zone = False
            if contest is not None and candidates:
                # restore the true (left-to-right) candidate order; the text
                # layer scrambles the rotated header strips
                ordered = orders.get(norm_title(title))
                if ordered and set(ordered) == set(candidates):
                    candidates = ordered
                elif ordered:
                    problems.append(
                        f'header order mismatch for {contest}: '
                        f'pdf={ordered} text={candidates}')
            continue
        vm = parse_values(s)
        if vm and contest is not None:
            label, nums, pct = vm
            fields = [int(x.replace(',', '')) for x in nums.split()]
            if label and not label.rstrip().endswith(','):
                # complete inline label; a wrapped fragment follows only when
                # this one ends mid-name ('... Precinct')
                precinct = label
                incomplete = label.endswith('Precinct')
            else:
                precinct = (pending_label + ' ' + label).strip()
                incomplete = precinct.endswith(',') or \
                    precinct.endswith('Precinct')
            if incomplete:
                # the label continues on the next non-furniture line
                j = i
                while j < n and (not lines[j].strip()
                                 or FURNITURE.match(lines[j].strip())):
                    j += 1
                if j < n:
                    frag = lines[j].strip()
                    if (not TITLE_RE.match(frag) and frag != 'Precinct'
                            and parse_values(frag) is None):
                        precinct = (precinct + ' ' + frag).strip()
                        i = j + 1
            pending_label = ''
            precinct = normalize_precinct(precinct)
            if label.lower().startswith('totals'):
                ncand = len(candidates)
                expected = [sum(c) for c in table_sums] if table_sums else []
                if ncand and fields[:ncand] != expected:
                    problems.append(
                        f'Totals mismatch for {contest} {candidates}: '
                        f'{fields[:ncand]} != {expected}')
                if (ncand and len(fields) == ncand + 4 + (
                        4 if has_registered is not False else 3)
                        and fields[ncand] != sum(expected)):
                    problems.append(
                        f'Totals Cast Votes {fields[ncand]} != '
                        f'{sum(expected)} for {contest}')
                table_sums = None
                continue
            if not candidates and has_invalid and len(fields) == 9:
                # candidate-less contests (uncontested Sheriff DEM) print a
                # phantom 0-vote candidate column: drop it
                fields = fields[1:]
            ncontest = 3 if has_invalid is False else 4
            # wide tables clip the Registered Voters column into an overflow
            # table; when the header zone is silent (None) accept either
            nm_options = ((4,) if has_registered is True else
                          (3,) if has_registered is False else (4, 3))
            if not any(len(candidates) == len(fields) - ncontest - nm
                       for nm in nm_options):
                problems.append(
                    f'{len(candidates)} candidates vs {len(fields)} fields '
                    f'for {contest} at {precinct!r}')
                continue
            nmethod = next(nm for nm in nm_options
                           if len(candidates) == len(fields) - ncontest - nm)
            cand_vals = fields[:len(candidates)]
            vals = fields[len(candidates):]
            cast, under, over = vals[0], vals[1], vals[2]
            invalid = vals[3] if ncontest == 4 else 0
            if cast != sum(cand_vals):
                problems.append(
                    f'Cast Votes {cast} != candidate sum {sum(cand_vals)} '
                    f'for {contest} at {precinct!r}')
            if table_sums is None or last_table_key != (contest, tuple(
                    candidates)):
                table_sums = [[v] for v in cand_vals]
                last_table_key = (contest, tuple(candidates))
            else:
                for acc, v in zip(table_sums, cand_vals):
                    acc.append(v)
            office, district, party = contest
            for name, v in zip(candidates, cand_vals):
                rows.append({'county': COUNTY, 'precinct': precinct,
                             'office': office, 'district': district,
                             'party': party, 'candidate': name, 'votes': v})
            rows.append({'county': COUNTY, 'precinct': precinct,
                         'office': office, 'district': district,
                         'party': party, 'candidate': 'Ballots Cast',
                         'votes': cast + under + over + invalid})
            continue
        if contest is not None:
            if in_header_zone:
                if 'Invalid Votes' in s:
                    has_invalid = True
                elif 'Registered Voters' in s:
                    has_registered = True
                elif 'Overvotes' in s and has_invalid is None:
                    # 'Invalid Votes' may wrap onto its own header line and
                    # only upgrade, never downgrade
                    has_invalid = False
                names = strip_header_tokens(s)
                if names:
                    candidates.extend(names)
                continue
            if not any(t in s for t in HEADER_TOKENS):
                # a lone non-header, non-furniture line outside a values row
                # is a precinct-label fragment: keep it for the next values
                # row when one follows; otherwise (overflow tables print only
                # Registered Voters/Turnout % columns, label fragments and
                # bare percentages) drop it
                if i < n and parse_values(lines[i].strip()) is not None:
                    pending_label = s
                else:
                    skipped.append(s)
            continue
        problems.append(f'line outside any contest: {s!r}')
    if skipped:
        print(f'note: dropped {len(skipped)} overflow-table lines '
              f'(turnout-only tables)', file=sys.stderr)
    return rows, problems


def main():
    rows, problems = parse()
    for p in problems:
        print('PROBLEM:', p)
    write_csv(COUNTY, rows)


if __name__ == '__main__':
    main()