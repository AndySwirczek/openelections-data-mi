"""Regenerate Livingston 2022 general Proposal 22-3 rows from the county SOVC.

Commit 1d6b93e added 22-3 rows for only 66 of the county's 81 precincts
(missing: City of Brighton 1-4, City of Howell 1-3, Brighton Charter Township
1-8). This re-parses the Proposal 22-3 contest pages (306/308/310/312/314/316
1-based? see PAGES) of
openelections-sources-mi/2022/general/Livingston MI Statement-of-Votes-Cast-Report.pdf
and replaces ALL Proposal 22-3 rows in the county precinct CSV.

Page row layout (text-extraction order; wrapped precinct names put the
numbers line between the two name fragments):
  Yes No VotesCast Undervotes Overvotes wir rej unass BallotsAbs
  BallotsPrec TotalBallots Registered Turnout%
"""
import csv
import re
import sys

import pdfplumber

PDF = ('/Users/dwillis/code/openelections-sources-mi/2022/general/'
       'Livingston MI Statement-of-Votes-Cast-Report.pdf')
COUNTY_CSV = '2022/counties/20221108__mi__general__livingston__precinct.csv'
# pdfplumber 0-based indexes of the Proposal 22-3 pages
PAGES = [306, 308, 310, 312, 314, 316]
# expected printed Totals: Yes No Under Over
EXPECTED_TOTALS = (53175, 54410, 2031, 42)

NUMBERS = re.compile(r'^(?P<name>.*?)\s*(?P<nums>(?:\d[\d,]*\s+){9,13}\d[\d,]*)'
                     r'\s+(?P<pct>\d+\.\d+)%$')


def to_int(s):
    return int(s.replace(',', ''))


def split_line(line):
    """'City of Brighton, Precinct 1 759 512 ... 62.60%' ->
    (name tokens, [11 numbers], pct) or None if not a data/total row."""
    toks = line.strip().split()
    if len(toks) < 12 or not toks[-1].endswith('%') or '%' in toks[-1][:-1]:
        return None
    if not re.fullmatch(r'\d+\.\d+', toks[-1][:-1]):
        return None
    nums = [to_int(t) for t in toks[-12:-1]]
    if len(nums) != 11:
        return None
    return toks[:-12], nums, float(toks[-1][:-1])


def parse():
    known = set()
    for r in csv.DictReader(open(COUNTY_CSV)):
        known.add(r['precinct'])

    rows = []   # (precinct, yes, no, under, over)
    totals = None
    for idx in PAGES:
        text = pdfplumber.open(PDF).pages[idx].extract_text() or ''
        lines = text.split('\n')
        # skip everything up to and including the contest title
        start = next(i for i, l in enumerate(lines) if 'Proposal 22-3' in l) + 1
        buffer = []   # pending name fragments
        held = None   # (nums) waiting for the name to complete
        for line in lines[start:]:
            parts = split_line(line)
            if parts is None:
                frag = ' '.join(line.split())
                if frag:
                    buffer.append(frag)
                if held is not None:
                    full = ' '.join(buffer)
                    if full in known:
                        n = held
                        rows.append((full, n[0], n[1], n[3], n[4]))
                        buffer = []
                        held = None
                continue
            name_toks, nums, _pct = parts
            name = ' '.join(buffer + name_toks).strip()
            if name.startswith('Totals'):
                totals = (nums[0], nums[1], nums[3], nums[4])
                continue
            if name in known:
                # Yes No Cast Under Over wir rej unass abs prec total
                rows.append((name, nums[0], nums[1], nums[3], nums[4]))
                buffer = []
                held = None
            else:
                # wrapped name: the rest of the name follows the numbers line
                held = nums
        if buffer or held is not None:
            sys.exit(f'page {idx}: unparsed residue {buffer!r} held={held!r}')
    return rows, totals


def main(apply=False):
    rows, totals = parse()
    print(f'parsed {len(rows)} rows; Totals yes/no/under/over = {totals[:4]}')
    if totals[:4] != EXPECTED_TOTALS:
        sys.exit(f'Totals mismatch: expected {EXPECTED_TOTALS}')
    yes = sum(r[1] for r in rows)
    no = sum(r[2] for r in rows)
    under = sum(r[3] for r in rows)
    over = sum(r[4] for r in rows)
    if (yes, no, under, over) != EXPECTED_TOTALS:
        sys.exit(f'row sums {yes}/{no}/{under}/{over} != printed totals')
    # names must be exactly the known 81 and unique
    names = [r[0] for r in rows]
    assert len(set(names)) == len(names), 'duplicate precinct'
    with open(COUNTY_CSV, newline='') as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        kept = [r for r in reader if r['office'] != 'Proposal 22-3']
    added = []
    for precinct, y, n, u, o in rows:
        for cand, votes in (('Yes', y), ('No', n),
                            ('Under Votes', u), ('Over Votes', o)):
            added.append({'county': 'Livingston', 'precinct': precinct,
                          'office': 'Proposal 22-3', 'district': '',
                          'party': '', 'candidate': cand, 'votes': votes})
    out = kept + added
    print(f'kept {len(kept)} rows, added {len(added)} 22-3 rows '
          f'(file {len(out)} rows)')
    if apply:
        with open(COUNTY_CSV, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(out)
        print('wrote', COUNTY_CSV)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)