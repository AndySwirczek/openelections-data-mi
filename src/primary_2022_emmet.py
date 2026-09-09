"""Parse Emmet County's Aug 2022 primary results PDF into a per-county
precinct CSV, verified against the certified county-level CENR.

Source (openelections-sources-mi/2022/primary/): 'Emmet MI Aug-22-Official-
Results-Precinct.pdf' — a 104-page Electionware 'Summary Results Report',
one precinct per page run: a repeated '<precinct>' header, a Statistics
block, then contest blocks — '<party> <office>' title, 'Vote For 1', a
'TOTAL Election Absentee Day' header, one row per candidate whose first
number is the contest-wide total ('Gretchen Whitmer 409 81 328'), a lumped
'Write-In Totals' row itemized into 'Write-In: <name>' detail rows for the
qualified write-ins (plus 'Not Assigned' for the unassigned rest), and
'Total Votes Cast'/'Contest Totals' closers. Only the CENR-carried offices
are emitted (Governor, U.S. House 1, State Senate 37, State House 107 — the
titles carry no district, so it comes from the CENR); county/local offices
and delegate rows are skipped. Every block is cross-checked: candidate and
write-in detail totals + unassigned write-ins == Total Votes Cast.

Usage: .venv/bin/python src/primary_2022_emmet.py [--apply]
"""
import re
import sys
from collections import defaultdict

import pdfplumber

from primary_2022_common import CENR, one_space, verify, write

COUNTY = 'Emmet'
SOURCE = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
          "Emmet MI Aug-22-Official-Results-Precinct.pdf")

# (office, party) -> (office, district, party), from the CENR's Emmet rows
BY_OFFICE = {}
for (office, district, party, _cand), _votes in CENR[COUNTY].items():
    BY_OFFICE[(office, party)] = (office, district, party)

CENR_CANDS = defaultdict(set)
for (office, district, party, cand) in CENR[COUNTY]:
    CENR_CANDS[(office, district, party)].add(cand)

TITLE = re.compile(r'^(DEM|REP|LIB|UST|GRN|NONPARTISAN) '
                   r'(Governor|Representative in Congress|State Senator|'
                   r'Representative in State Legislature)$')

# report office names -> the CENR's
OFFICE = {'Governor': 'Governor',
          'Representative in Congress': 'U.S. House',
          'State Senator': 'State Senate',
          'Representative in State Legislature': 'State House'}
PRECINCT = re.compile(r'^(.+), (?:Ward \d+, )?Precinct \d+$')
PARTY_PREFIX = re.compile(r'^(DEM|REP|LIB|UST|GRN|NONPARTISAN)\b')
NUM = re.compile(r'^\d[\d,]*$')


def parse():
    rows = []
    skipped = defaultdict(int)
    problems = []
    precinct = None
    contest = None          # (office, district, party)
    cand_sums = None        # candidate -> votes in the current block
    cast = None             # printed 'Total Votes Cast'
    wi_total = None         # printed 'Write-In Totals'
    wi_named = 0            # votes on itemized qualified write-ins

    def close_block():
        nonlocal contest, cand_sums, cast, wi_total, wi_named
        if contest is not None:
            if cast is None:
                problems.append(f'{precinct} {contest}: block ends without '
                                f'Total Votes Cast')
            lump = (wi_total or 0) - wi_named
            if lump < 0:
                problems.append(f'{precinct} {contest}: write-in details '
                                f'{wi_named} exceed Write-In Totals '
                                f'{wi_total}')
                lump = 0
            got = sum(cand_sums.values()) + lump
            if cast is not None and got != cast:
                problems.append(f'{precinct} {contest}: candidate sum '
                                f'{got} != Total Votes Cast {cast}')
            if lump:
                cand_sums['Write-In'] += lump
            for cand, v in cand_sums.items():
                if cand != 'Write-In' \
                        and cand not in CENR_CANDS[contest]:
                    problems.append(f'{precinct} {contest}: candidate '
                                    f'{cand!r} not in CENR')
                    continue
                if v:
                    rows.append((precinct, contest[0], contest[1],
                                 contest[2], cand, v))
        contest, cand_sums, cast, wi_total, wi_named = \
            None, None, None, None, 0

    with pdfplumber.open(SOURCE) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or '').splitlines():
                text = one_space(line.strip())
                if not text:
                    continue
                m = TITLE.match(text)
                if m:
                    close_block()
                    party, office = m.groups()
                    contest = BY_OFFICE.get((OFFICE[office], party))
                    cand_sums = defaultdict(int) if contest else None
                    continue
                if PARTY_PREFIX.match(text):
                    continue        # local offices, delegates, trustees
                m = PRECINCT.match(text)
                if m:
                    close_block()
                    precinct = text
                    continue
                if contest is None:
                    continue
                toks = text.split()
                nums = [w for w in toks if NUM.match(w)]
                if not nums:
                    continue
                # a data row is '<name> N1 N2 N3'; footers have non-numeric
                # tokens after the first number ('... 3 of 104')
                if any(not NUM.match(w) for w in toks[toks.index(nums[0]) + 1:]):
                    continue
                head = one_space(' '.join(
                    w for w in toks[:toks.index(nums[0])]))
                v = int(nums[0].replace(',', ''))
                if head == 'Total Votes Cast':
                    cast = v
                elif head == 'Contest Totals':
                    close_block()
                elif head == 'Write-In Totals':
                    wi_total = v
                elif head.startswith('Write-In: '):
                    name = head[len('Write-In: '):]
                    if name == 'Invalid Write-in':
                        skipped[name] += 1
                    else:
                        wi_named += v
                        cand_sums[name] += v
                elif head in ('Not Assigned', 'Overvotes', 'Undervotes',
                              'Vote For'):
                    continue
                else:
                    cand_sums[head] += v
        close_block()
    return rows, skipped, problems


def main(apply=False):
    rows, skipped, problems = parse()
    print(f'{COUNTY}: {len(rows)} rows; {sum(skipped.values())} rows skipped')
    problems += verify(COUNTY, rows, {})
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')
    if apply:
        write(COUNTY, rows)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)