"""Parse Cheboygan County's March 10, 2020 presidential primary from
the Board of Canvassers "Canvass of Votes Cast" PDF (20 pages) in
openelections-sources-mi/2020/presidential_primary/ -- a STATEMENT OF
VOTES - CERTIFICATE OF DETERMINATION form, cached as PaddleOCR
markdown (/tmp/paddleocr_md_mar2020/
Cheboygan_MI_March_2020_Canvass_of_Votes_Cast).  The PDF also has an
embedded (old, noisy) OCR text layer, but the fresh PaddleOCR tables
are clean.

Layout:
- p1 cover/notes with the countywide POLL BOOK total (5,326).
- p3-p4: OFFICIAL COUNTY VOTE TOTALS (Office Code 01000000) -- one
  row per candidate with the votes printed as words AND digits, but
  the digit cell is offset one row down in the OCR table; anchors
  below are read from the word column.  p4-p6 repeat everything for
  the 1st District contest (Office Code 25001000) with identical
  values (the county is entirely in CD 1) -- not parsed separately.
- p8-p15: precinct pages, TWO candidates each (Bennet+Biden,
  Bloomberg+Booker, Buttigieg+Castro, Delaney+Gabbard,
  Klobuchar+Sanders, Sestak+Steyer, Warren+Williamson,
  Yang+Uncommitted); p16 Sanford+Trump, p17 Walsh+Weld, p18
  Uncommitted (header in a div, not in the table).  Each row:
  [precinct, word, digit] per candidate; 22 precincts (19 townships
  each Precinct 1 plus City of Cheboygan Precincts 1-3).
- p19: Pellston Public School District - Sinking Fund Millage
  Proposal -- a YES table and a NO table, each Burt / Hebron / Munro
  Township, Precinct 1 (the special election's three voting
  precincts).

Checks: per-candidate precinct digit sums == the printed countywide
totals (word column, cross-checked against the digit column and the
repeated 1st-District pages); word vs digit agreement on every data
cell; proposal sums == printed totals (Yes 123 / No 88).  The p17
Totals word prints 'Thirteen' but its digit (12), the precinct sums
(12) and the countywide total (12) all agree -- digit wins.  DEM TV
2,938 / REP TV 2,375 = candidate sums 2,936 / 2,373 + 2 invalid
write-ins each (write-ins are not emitted -- March convention).
This source has NO per-precinct Times Cast / Registered Voters data,
so only candidate rows are emitted (plus proposal Yes/No).
"""
import csv
import re

CACHE = '/tmp/paddleocr_md_mar2020/Cheboygan_MI_March_2020_Canvass_of_Votes_Cast'
OUT = ('2020/counties/20200310__mi__primary__president__cheboygan__'
       'precinct.csv')
COUNTY = 'Cheboygan'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

# printed countywide anchors (word column of the p3/p4 totals tables,
# confirmed against the offset digit column and the 1st-District
# repeat); poll book total 5,326
# party-qualified keys -- DEM and REP both have an 'Uncommitted'
ANCHORS = {**{('DEM', n): v for n, v in
              zip(DEM, [3, 1710, 160, 0, 48, 0, 1, 18, 22, 880, 1,
                        5, 55, 0, 2, 31])},
           **{('REP', n): v for n, v in
              zip(REP, [15, 2260, 12, 18, 68])}}

# precinct page -> (party, candidate 1, candidate 2 or None)
PAGES = [(8, 'DEM', DEM[0], DEM[1]), (9, 'DEM', DEM[2], DEM[3]),
         (10, 'DEM', DEM[4], DEM[5]), (11, 'DEM', DEM[6], DEM[7]),
         (12, 'DEM', DEM[8], DEM[9]), (13, 'DEM', DEM[10], DEM[11]),
         (14, 'DEM', DEM[12], DEM[13]), (15, 'DEM', DEM[14], DEM[15]),
         (16, 'REP', REP[0], REP[1]), (17, 'REP', REP[2], REP[3]),
         (18, 'REP', REP[4], None)]

PROPOSAL = 'Pellston Public School District - Sinking Fund Millage Proposal'

CANON = {'Buft Township, Precinct 1': 'Burt Township, Precinct 1'}

ONES = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
        'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
        'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
        'nineteen': 19}
TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60,
        'seventy': 70, 'eighty': 80, 'ninety': 90}

CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TABLE_RE = re.compile(r'<table.*?</table>', re.S)


def words_to_number(s):
    """-> int from a printed words number ('One Hundred Sixty-eight'),
    or None if the text is not a clean number word string."""
    def part(toks):
        small, hundreds = 0, 0
        for t in toks:
            if t in ONES:
                small += ONES[t]
            elif t in TENS:
                small += TENS[t]
            elif t == 'hundred':
                hundreds += (small or 1) * 100
                small = 0
            else:
                return None
        return hundreds + small

    toks = [t.strip('.') for t in re.split(r'[\s-]+', s.lower())
            if t not in ('votes', 'and', '')]
    if not toks:
        return None
    if 'thousand' in toks:
        i = toks.index('thousand')
        left = part(toks[:i])
        right = part(toks[i + 1:])
        return None if left is None or right is None \
            else 1000 * (left or 1) + right
    return part(toks)


def load_tables(page):
    md = open(f'{CACHE}/p{page:03d}.md').read()
    out = []
    for t in TABLE_RE.findall(md):
        rows = [[re.sub(r'<[^>]+>', '', c).replace('\\n', ' ').strip()
                 for c in CELL_RE.findall(r)]
                for r in re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.S)]
        out.append(rows)
    return out


def main():
    problems = []
    PRECINCTS = []
    cand = {}
    for page, party, n1, n2 in PAGES:
        rows = load_tables(page)[0]
        # validate the header (p18 has none -- candidate from the div)
        if rows[0][0] == '':
            hdr = [h.replace('Democratic', '').replace('Republican', '')
                   for h in rows.pop(0)[1:]]
            want = [n for n in (n1, n2) if n]
            got = [h.strip() for h in hdr if h.strip()][:len(want)]
            if got != want:
                problems.append(f'p{page}: header {got} != {want}')
        for r in rows:
            label = r[0].strip()
            if label in ('Totals', 'TOTALS', 'TOTAL') \
                    or label.startswith('Total') \
                    or 'Invalid write-in' in label:
                continue  # totals checked against the countywide anchors
            if not re.search(r'Township|City of Cheboygan', label):
                problems.append(f'p{page}: odd row {label!r}')
                continue
            label = CANON.get(label, label)
            if label not in PRECINCTS:
                PRECINCTS.append(label)
                cand[label] = {}
            nums = [int(v.replace(',', '')) for v in r[1:]
                    if re.fullmatch(r'[\d,]+', v)]
            for i, n in enumerate((n1, n2)):
                if n is None:
                    break
                if i >= len(nums):
                    problems.append(f'p{page}: {label!r} missing digit '
                                    f'for {n}')
                    continue
                v = nums[i]
                key = (party, n)  # DEM and REP both have 'Uncommitted'
                if key in cand[label]:
                    problems.append(f'{party} {n}: duplicate {label!r}')
                cand[label][key] = v
                # word vs digit cross-check (word cell precedes digit)
                w = r[1 + 2 * i]
                wn = words_to_number(w)
                if wn is not None and wn != v:
                    problems.append(f'p{page}: {label!r} {n}: word '
                                    f'{w!r} ({wn}) != digit {v}')
    if problems:
        for p in problems[:20]:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems')
        return
    if len(PRECINCTS) != 22:
        problems.append(f'{len(PRECINCTS)} precincts')

    # per-candidate sums vs printed countywide anchors
    for party, seq in (('DEM', DEM), ('REP', REP)):
        for n in seq:
            s = sum(cand[p][(party, n)] for p in PRECINCTS)
            if s != ANCHORS[(party, n)]:
                problems.append(f'{party} {n}: precinct sum {s} != '
                                f'countywide {ANCHORS[(party, n)]}')
    dem_sum = sum(v for k, v in ANCHORS.items() if k[0] == 'DEM')
    rep_sum = sum(v for k, v in ANCHORS.items() if k[0] == 'REP')
    if dem_sum != 2936 or rep_sum != 2373 or dem_sum + rep_sum != 5309:
        problems.append(f'anchor sums DEM {dem_sum} / REP {rep_sum}')

    # proposal (p19): YES table then NO table
    tables = load_tables(19)
    votes = {}
    for tbl, choice in zip(tables, ('Yes', 'No')):
        for r in tbl:
            label = CANON.get(r[0].strip(), r[0].strip())
            if not re.search(r'Township', label):
                continue  # 'Total number of votes cast NO:' row
            if label not in PRECINCTS:
                problems.append(f'p19: {label!r} not a precinct')
            votes[(label, choice)] = int(r[-1])
    yes = sum(v for (p, c), v in votes.items() if c == 'Yes')
    no = sum(v for (p, c), v in votes.items() if c == 'No')
    if yes != 123 or no != 88:
        problems.append(f'proposal sums Yes {yes} / No {no}')
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
            print(f'{len(problems)} problems; not writing')
        return

    rows_out = []
    for p in sorted(PRECINCTS):
        for party, seq in (('DEM', DEM), ('REP', REP)):
            for n in seq:
                rows_out.append([COUNTY, p, 'President', '', party, n,
                                 cand[p][(party, n)]])
        for choice in ('Yes', 'No'):
            if (p, choice) in votes:
                rows_out.append([COUNTY, p, PROPOSAL, '', '', choice,
                                 votes[(p, choice)]])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(PRECINCTS)} precincts')
    print('county: Biden', ANCHORS[('DEM', 'Joe Biden')], 'Sanders',
          ANCHORS[('DEM', 'Bernie Sanders')], 'Trump',
          ANCHORS[('REP', 'Donald J. Trump')],
          'poll book 5,326; proposal Yes', yes, '/ No', no)


if __name__ == '__main__':
    main()