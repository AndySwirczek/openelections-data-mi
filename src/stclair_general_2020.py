#!/usr/bin/env python3
"""Parse St. Clair County's Nov 2020 general election from the VR Systems
per-ballot-item JSON exports.

Source: openelections-sources-mi/2020/general/'St. Clair County Nov 2020
General Precinct Results'/*.json — 173 files, one per ballot item, each
carrying `ballotItemWithBreakdown` with summaryResults.ballotOptions
(countywide) and breakdownResults[] (per-precinct).  The precinct list
at top level is the emission order; each file's `ballotItems` list gives
the ballot order of all 173 contests.

There is no CENR county-file verification target (St. Clair is absent
from 2020/20201103__mi__general__county.csv), so verification is:
  - internal: per contest, per-precinct option sums == the precinct's
    voteTotal and contest-wide sums == summaryResults voteCounts;
  - external: named-candidate totals vs the Bureau of Elections
    'Official County Vote Totals' form (OCR cache at
    /tmp/paddleocr_md/St_Clair_MI_Nov_2020_Official_County_Vote_Totals),
    which also supplies the party codes for the 19 statewide contests
    (the JSONs carry none — party is null everywhere).

Known source discrepancy: the VR Systems totals (an unofficial tally
re-exported 2025-01-09) differ from the certified canvass for 30 named
candidates by ±1-2 votes — most visibly Donald J. Trump President
59,186 vs 59,185 — and for Don Gates (Governor of Wayne State
University) by +330 (48,474 vs 48,144).  The precinct file emits the
JSON values, which are internally consistent throughout; the certified
values are printed by the parser's check as information only.
"""

import csv
import glob
import html
import json
import re
import sys

SRC_DIR = ('/Users/dwillis/code/openelections-sources-mi/2020/general/'
           'St. Clair County Nov 2020 General Precinct Results')
FORM_CACHE = ('/tmp/paddleocr_md/'
              'St_Clair_MI_Nov_2020_Official_County_Vote_Totals')
OUT = '2020/counties/20201103__mi__general__st_clair__precinct.csv'
COUNTY = 'St. Clair'

SP_CODES = {
    'Democratic Party': 'DEM', 'Republican Party': 'REP',
    'Libertarian Party': 'LIB', 'U.S. Taxpayers Party': 'UST',
    'Working Class Party': 'WCP', 'Green Party': 'GRN',
    'Natural Law Party': 'NLP',
}
FORM_PARTIES = {'DEM', 'REP', 'LIB', 'UST', 'WCP', 'GRN', 'NLP', 'NPA'}

WORD_NUMS = {
    'ZERO': 0, 'ONE': 1, 'TWO': 2, 'THREE': 3, 'FOUR': 4, 'FIVE': 5,
    'SIX': 6, 'SEVEN': 7, 'EIGHT': 8, 'NINE': 9, 'TEN': 10,
    'ELEVEN': 11, 'TWELVE': 12, 'THIRTEEN': 13, 'FOURTEEN': 14,
    'FIFTEEN': 15, 'SIXTEEN': 16, 'SEVENTEEN': 17, 'EIGHTEEN': 18,
    'NINETEEN': 19, 'TWENTY': 20, 'THIRTY': 30, 'FORTY': 40,
    'FIFTY': 50, 'SIXTY': 60, 'SEVENTY': 70, 'EIGHTY': 80,
    'NINETY': 90,
}
SCALES = {'HUNDRED': 100, 'THOUSAND': 1000}


def words_to_int(text):
    """'FIFTY NINE THOUSAND ONE HUNDRED EIGHTY FIVE' -> 59185."""
    total, current = 0, 0
    for word in re.findall(r'[A-Z]+', text.upper()):
        if word in WORD_NUMS:
            current += WORD_NUMS[word]
        elif word in SCALES:
            if word == 'HUNDRED':
                current = (current or 1) * 100
            else:
                total += (current or 1) * SCALES[word]
                current = 0
    return total + current


def txt(field):
    return ' '.join((field or [{}])[0].get('text', '').split())


def name_key(full):
    """Token-set key matching form 'Last, First M.' against JSON
    'First M. Last' (single-letter initials dropped, running mates cut
    at the '/', Yes/No kept verbatim)."""
    full = re.split(r'\s*/\s*', full.strip())[0]
    if full.lower() in ('yes', 'no'):
        return (full.lower(),)
    toks = [t for t in re.split(r'[\s,.]+', full)
            if len(t) > 1 and re.search(r'[A-Za-z]', t)]
    return tuple(sorted(t.lower() for t in toks)) or (full.lower(),)


def parse_form():
    """Official County Vote Totals form -> {json contest: {candidate:
    (party, votes)}} via the OCR cache."""
    offices = {}   # form title -> [(party, name, votes)]
    pages = sorted(glob.glob(f'{FORM_CACHE}/p*.md'),
                   key=lambda p: int(re.search(r'p(\d+)', p).group(1)))
    for path in pages:
        md = open(path).read()
        m = re.search(r'Office Code: ([A-Za-z0-9]+)\)?:?\s*\n(.+)', md)
        if not m:
            continue
        title = re.sub(r'^#+\s*', '', ' '.join(m.group(2).split()))
        tm = re.search(r'<table.*?</table>', md, re.S)
        if not tm:
            continue
        rows = []
        for rm in re.finditer(r'<tr>(.*?)</tr>', tm.group(0)):
            cells = [' '.join(html.unescape(c).split())
                     for c in re.findall(r'<td[^>]*>(.*?)</td>', rm.group(1))]
            if cells:
                rows.append(cells)
        entries = []
        for i, cells in enumerate(rows):
            joined_all = ' '.join(cells)
            if any('Office Code' in c or 'total number of votes' in c.lower()
                   or 'Enter the sum' in c or c.lower().startswith('was ')
                   for c in cells):
                continue
            # [party, name, received(+words), 'votes.', numeric]
            party = cells[0] if cells and cells[0] in FORM_PARTIES else ''
            name = cells[1] if len(cells) > 1 and re.search(
                r'[A-Za-z]', cells[1] or '') and 'received' not in cells[1] \
                else (cells[0] if cells and not party and
                      re.search(r'[A-Za-z]', cells[0] or '')
                      and 'received' not in cells[0] else '')
            if not name or 'received' in name:
                continue
            if not (re.match(r"^[A-Za-z][A-Za-z .'-]+, [A-Za-z]", name)
                    or name.lower() in ('yes', 'no')):
                continue  # stray total/annotation rows
            joined = ' '.join(cells)
            wm = re.search(r'([A-Z][A-Z ]+)(?:\s*votes\.?)', joined)
            votes = words_to_int(wm.group(1)) if wm else None
            if votes is None:
                votes = _next_numeric(rows, i)
            is_wi = bool(re.match(r'^Write-?In\s+', name, flags=re.I))
            name = re.sub(r'^Write-?In\s+', '', name, flags=re.I)
            entries.append((party, name, votes, is_wi))
        offices[title] = entries
    return offices


def _next_numeric(rows, i):
    for cells in rows[i + 1:i + 3]:
        for c in reversed(cells):
            if re.fullmatch(r'[\d,]+', c or ''):
                return int(c.replace(',', ''))
    return None


def form_to_contest(title):
    """Form office title -> JSON contest name (or None to skip)."""
    t = title.strip()
    m = re.match(r'^President of the United States', t)
    if m:
        return 'President/Vice-President of the United States'
    if t.startswith('United States Senator'):
        return 'United States Senator'
    m = re.match(r'^(\d+)(?:st|nd|rd|th) District Representative in '
                 r'Congress', t)
    if m:
        return f'Rep in Congress {m.group(1)}th District'
    m = re.match(r'^(\d+)(?:st|nd|rd|th) District Representative in State '
                 r'Legislature', t)
    if m:
        return f'Rep in State Legislature {m.group(1)}th District'
    m = re.match(r'^(\d+)(?:st|nd|rd|th) District Judge of Court of '
                 r'Appeals (Incumbent|Non-Incumbent)', t)
    if m:
        return f'Judge of Court of Appeals {m.group(1)}th District ' \
               f'{m.group(2)}'
    m = re.match(r'^(\d+)(?:st|nd|rd|th) Circuit Judge of Circuit Court '
                 r'(Non-Incumbent)', t)
    if m:
        return f'Judge of Circuit Court {m.group(1)}st Circuit {m.group(2)}'
    m = re.match(r'^(\d+)(?:st|nd|rd|th) District Judge of District Court '
                 r'Incumbent', t)
    if m:
        return f'Judge of District Court {m.group(1)}nd District Incumbent'
    if 'Judge of Probate' in t:
        return 'Judge of Probate Court Incumbent'
    m = re.match(r'^State Proposal - 20-(\d+)', t)
    if m:
        return f'State Proposal 20-{m.group(1)}'
    m = re.match(r'^(Member of the State Board of Education|Regent of the '
                 r'University of Michigan|Trustee of Michigan State '
                 r'University|Governor of Wayne State University|Justice '
                 r'of Supreme Court)', t)
    if m:
        return m.group(1)
    return None


def map_office(contest):
    """JSON contest name -> (office, district)."""
    if contest == 'Straight Party Ticket':
        return 'Straight Party', ''
    if contest == 'President/Vice-President of the United States':
        return 'President', ''
    if contest == 'United States Senator':
        return 'U.S. Senate', ''
    m = re.match(r'^Rep in Congress (\d+)(?:st|nd|rd|th) District$', contest)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^Rep in State Legislature (\d+)(?:st|nd|rd|th) District$',
                 contest)
    if m:
        return 'State House', m.group(1)
    return contest, ''


def map_candidate(name, office):
    if office == 'President':
        return name.split('/')[0].strip()
    return ' '.join(name.split())


def precinct_label(name):
    m = re.match(r'^(.+?) (Precinct \d+(?: \(Out of County\))?)$', name)
    return f'{m.group(1)}, {m.group(2)}' if m else name


def main():
    problems = []
    files = sorted(glob.glob(f'{SRC_DIR}/*.json'))
    form = parse_form()

    # ballot order + per-contest data
    base = json.load(open(files[0]))
    order = [txt(b['name']) for b in base['ballotItems']]
    precincts = [txt(p['name']) for p in base['precincts']]
    contests = {}      # name -> ballot item with breakdown
    for f in files:
        j = json.load(open(f))
        bi = j['ballotItemWithBreakdown']
        name = txt(bi['name'])
        if name in contests:
            problems.append(f'duplicate contest file: {name}')
        contests[name] = bi
    if set(contests) != set(order):
        problems.append('ballotItems vs contest files mismatch: '
                        f'{set(order) ^ set(contests)}')

    # party map from the certified form: contest -> candidate -> party
    parties = {}
    for title, entries in form.items():
        contest = form_to_contest(title)
        if contest is None:
            problems.append(f'unmapped form office: {title!r}')
            continue
        parties.setdefault(contest, {})
        for party, name, votes, is_wi in entries:
            if is_wi or name.lower() in ('yes', 'no'):
                continue
            parties[contest][name_key(name)] = \
                party if party in FORM_PARTIES - {'NPA'} else ''

    # --- verification ----------------------------------------------------
    print('== internal checks')
    for name, bi in contests.items():
        opts = {o['name'][0]['text']: o['voteCount']
                for o in bi['summaryResults']['ballotOptions']}
        got = {}
        for br in bi['breakdownResults']:
            prec = txt(br['precinct']['name'])
            s = 0
            for o in br['ballotOptions']:
                got[o['name'][0]['text']] = \
                    got.get(o['name'][0]['text'], 0) + o['voteCount']
                s += o['voteCount']
            if s != br['voteTotal']:
                problems.append(f'{name}: {prec} options sum {s} != '
                                f'voteTotal {br["voteTotal"]}')
        for cand, want in opts.items():
            if got.get(cand, 0) != want:
                problems.append(f'{name}: {cand} precinct sums '
                                f'{got.get(cand, 0)} != summary {want}')

    print('== certified-form checks (named candidates; the VR Systems '
          'export carries small unofficial-vs-certified deltas)')
    for title, entries in form.items():
        contest = form_to_contest(title)
        if contest is None or contest not in contests:
            continue
        bi = contests[contest]
        opts = {name_key(o['name'][0]['text']): o['voteCount']
                for o in bi['summaryResults']['ballotOptions']}
        for party, name, votes, is_wi in entries:
            if votes is None or is_wi:
                continue  # JSON aggregates unqualified write-ins
            match = name_key(name)
            if match not in opts:
                problems.append(f'{contest}: form candidate {name!r} not '
                                f'found in JSON options '
                                f'{[txt(o["name"]) for o in bi["summaryResults"]["ballotOptions"]]}')
                continue
            if opts[match] != votes:
                print(f'   diff {contest}: {match}: JSON {opts[match]} '
                      f'vs certified {votes}')
            del opts[match]
    # --- emission ---------------------------------------------------------
    rows = []
    for prec in precincts:
        label = precinct_label(prec)
        for contest in order:
            bi = contests[contest]
            office, district = map_office(contest)
            br = next((b for b in bi['breakdownResults']
                       if txt(b['precinct']['name']) == prec), None)
            if br is None:
                continue  # contest not on this precinct's ballot
            for o in bi['summaryResults']['ballotOptions']:
                name = txt(o['name'])
                cand = map_candidate(name, office)
                bv = next((b for b in br['ballotOptions']
                           if txt(b['name']) == name), None)
                votes = bv['voteCount'] if bv else 0
                if contest == 'Straight Party Ticket':
                    party = SP_CODES.get(name, '')
                else:
                    party = parties.get(contest, {}).get(name_key(cand), '')
                rows.append([COUNTY, label, office, district, party,
                             cand, votes])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)

    print(f'emitted {len(rows)} rows, {len(precincts)} precincts, '
          f'{len(contests)} contests')
    if problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)


if __name__ == '__main__':
    main()