"""Parse Saginaw County's Aug 2022 primary "Results per Precinct Data
Report" PDF into a per-county precinct CSV, verified against the certified
county-level CENR.

Layout: contest-major ("Election Report" style); each contest is a title
line ending '(DEM)' / '(REP)' (nonpartisan contests carry no party), a
'Precinct <candidates...> Total write-in' header that repeats on every page
the contest spans, one row per precinct ('<row-id> <label ending
"Precinct N"> <values>') and a 'Total' county row that closes the contest
and cross-checks the precinct sums. The last column is the lumped
write-in column. Only the CENR-carried contests (Governor, U.S. House,
State Senate, State House) are emitted; header candidate names are
segmented against the CENR's ordered candidate list.

Usage:
    .venv/bin/python src/primary_2022_saginaw.py <source.pdf> \
        --out /tmp/saginaw22.csv
"""
import argparse
import collections
import re
import sys

import pdfplumber

from primary_2022_common import CENR, verify, write

COUNTY = 'Saginaw'
TITLE = re.compile(r'^(.*\S) \((DEM|REP)\)$')
KEEP = [
    (re.compile(r'^Governor$'), 'Governor'),
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$'),
     'U.S. House'),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th) District$'),
     'State Senate'),
    (re.compile(r'^State Representative (\d+)(?:st|nd|rd|th) District$'),
     'State House'),
    # nonpartisan: 'Judge of Circuit Court 10th Circuit' -> the CENR's
    # 'Circuit Court Judge' with a numbered district
    (re.compile(r'^Judge of (?:the )?(?:\S+ ){0,2}(\d+)(?:st|nd|rd|th)? Circuit$'),
     'Circuit Court Judge'),
]
HEADER = re.compile(r'^Precinct (.*) Total write-in$')
DATA = re.compile(r'^\d+ ')
PREC_END = re.compile(r'Precinct \d+$')


def map_office(title):
    for pat, name in KEEP:
        m = pat.match(title)
        if m:
            return name, (m.group(1) if pat.groups else '')
    return None, ''


def segment_names(tokens, cands):
    """Ordered cover of the header tokens by the CENR candidate names."""
    seqs = []

    def rec(i, used, seq):
        if seqs:
            return
        if i == len(tokens):
            seqs.append(list(seq))
            return
        for ci, name in enumerate(cands):
            if ci in used:
                continue
            nt = name.split()
            if tokens[i:i + len(nt)] == nt:
                seq.append(name)
                rec(i + len(nt), used | {ci}, seq)
                seq.pop()
                if seqs:
                    return

    rec(0, frozenset(), [])
    return seqs[0] if seqs else None


class Contest:
    def __init__(self, title, party):
        self.title, self.party = title, party
        self.office, self.district = map_office(title)
        self.names = None
        self.sums = None
        self.emitted = set()

    def finalize_header(self, text, problems):
        key = (self.office, self.district, self.party)
        cands = [c for (o, d, p, c) in CENR[COUNTY] if (o, d, p) == key]
        tokens = text.split()
        names = segment_names(tokens, cands)
        if names is None:
            problems.append(f'{self.title}: header unmatched: {text!r}')
            return
        if len(names) > len(cands) or len(set(names)) != len(names):
            problems.append(f'{self.title}: {len(names)} columns for '
                            f'{len(cands)} CENR candidates')
        elif len(names) < len(cands):
            # zero-vote qualified write-ins are lumped into the report's
            # 'Total write-in' column rather than given their own columns
            omitted = [c for c in cands if c not in names]
            notes.append(f'{self.title}: {len(omitted)} CENR candidates '
                         f'absent from the header (lumped write-in): '
                         f'{omitted}')
        self.names = names
        self.sums = [0] * (len(names) + 1)  # + lumped write-in column

    def add_row(self, label, values, problems):
        if self.names is None or len(values) != len(self.sums):
            problems.append(f'{label!r} / {self.title}: {len(values)} values '
                            f'for {len(self.sums or [])} columns')
            return
        if label in self.emitted:
            problems.append(f'{label!r} / {self.title}: duplicate row')
        self.emitted.add(label)
        for i, v in enumerate(values):
            self.sums[i] += v
        for name, v in zip(self.names, values):
            rows.append((label, self.office, self.district, self.party,
                         name, v))
        write_ins[(self.office, self.district, self.party)] += values[-1]

    def close(self, totals, problems):
        if len(totals) != len(self.sums):
            problems.append(f'{self.title}: Total row has {len(totals)} '
                            f'values, expected {len(self.sums)}')
            return
        for name, summed, printed in zip(
                self.names + ['Write-In'], self.sums, totals):
            if summed != printed:
                problems.append(f'{self.title}: {name} precinct sum {summed} '
                                f'!= printed county total {printed}')


rows = []
write_ins = collections.defaultdict(int)
notes = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    problems = []
    contest = None

    with pdfplumber.open(args.pdf) as pdf:
        for page in pdf.pages:
            for line in (l.strip() for l in
                         (page.extract_text() or '').splitlines()):
                if not line or re.match(r'^\d+/67$', line):
                    continue
                if line == 'Saginaw County':
                    continue
                m = TITLE.match(line)
                if m and not line.startswith('Precinct'):
                    contest = Contest(m.group(1), m.group(2))
                    continue
                cm = re.match(r'^Judge of (?:the )?(?:\S+ ){0,2}'
                              r'(\d+)(?:st|nd|rd|th)? Circuit$', line)
                if cm:
                    contest = Contest(line, '')
                    continue
                if contest is None:
                    continue
                if contest.office is None:
                    continue  # contest the CENR doesn't carry: skip its rows
                hm = HEADER.match(line)
                if hm and contest.names is None:
                    contest.finalize_header(hm.group(1), problems)
                    continue
                if re.match(r'^Total ', line) or line == 'Total':
                    if contest.office is None or contest.names is None:
                        continue
                    vals = [int(v.replace(',', ''))
                            for v in line.split()[1:]]
                    contest.close(vals, problems)
                    contest = None
                    continue
                if contest.office is None or not DATA.match(line):
                    continue
                toks = line.split()
                # label ends 'Precinct N'; the values follow
                idx = None
                for i in range(len(toks) - 1):
                    if toks[i] == 'Precinct' and toks[i + 1].isdigit():
                        idx = i
                if idx is None:
                    problems.append(f'{line!r}: no Precinct label end')
                    continue
                label = ' '.join(toks[1:idx + 2])
                try:
                    values = [int(t.replace(',', ''))
                              for t in toks[idx + 2:]]
                except ValueError:
                    problems.append(f'{line!r}: non-numeric value')
                    continue
                contest.add_row(label, values, problems)

    for p in notes:
        print('NOTE', p)
    for p in problems:
        print('PROBLEM:', p)
    print(f'{len(rows)} rows; {len(problems)} parser problems')
    blocking = verify(COUNTY, rows, write_ins)
    for p in blocking:
        print('PROBLEM:', p)
    if not problems and not blocking:
        write(COUNTY, rows)


if __name__ == '__main__':
    main()