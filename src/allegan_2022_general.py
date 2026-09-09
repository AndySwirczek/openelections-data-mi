"""Patch the Allegan 2022 general precinct CSV from the county per-precinct PDF.

Fixes two problems in 2022/counties/20221108__mi__general__allegan__precinct.csv:

1. State House 39/42/43 stored only the FIRST candidate column of
   'Allegan MI Results per Precinct Offi.pdf' (the DEM candidate's votes) under
   BOTH candidates (Wendzel/Hall/Smit each got their DEM opponent's total).
   Those three contests are rebuilt with per-precinct votes for both candidates.

2. The file lacked entire offices present in the certified CENR: Justice of
   Supreme Court, State Board of Education, Regent, MSU Trustee, WSU Governor,
   Court of Appeals Judge 3, Circuit Court Judge 48, District Court Judge 58,
   and Proposals 22-1/22-2. Those are appended from the same PDF.

PDF layout (text extraction): a contest-title line ending '(Vote for N)',
then wrapped candidate-header lines (not parsed - candidates are identified by
matching each numeric column's printed Total against the CENR county totals),
then one row per precinct. Wrapped precinct names put the numbers line between
the name fragments ('City of' / 'Allegan,' / NUMBERS / 'Precinct' / '1-ALG');
cross-county rows carry a trailing '(Ottawa)' tag on the numbers line.

Candidate columns are matched left-to-right against the CENR candidate pool;
the rightmost unmatched column (a Write-in column) is skipped, which also keeps
the file consistent with CENR (no write-in rows for these contests).

Usage: .venv/bin/python src/allegan_2022_general.py [--apply]
"""
import csv
import re
import sys
from collections import defaultdict

import pdfplumber

PDF = ('/Users/dwillis/code/openelections-sources-mi/2022/general/'
       'Allegan MI Results per Precinct Offi.pdf')
COUNTY_CSV = '2022/counties/20221108__mi__general__allegan__precinct.csv'
CENR_CSV = '2022/20221108__mi__general__county.csv'

NUMERIC = re.compile(r'^[\d,]+$')
TITLE = re.compile(r'\(Vote for \d+\)$')

EXACT_TITLES = {
    'Straight Party (Vote for 1)': ('Straight Party', ''),
    'Governor/ Lieutenant Governor (Vote for 1)': ('Governor', ''),
    'Secretary of State (Vote for 1)': ('Secretary of State', ''),
    'Attorney General (Vote for 1)': ('Attorney General', ''),
    'Member of the State Board of Education (Vote for 2)': ('State Board of Education', ''),
    'Regent of the University of Michigan (Vote for 2)': ('Regent of the University of Michigan', ''),
    'Trustee of Michigan State University (Vote for 2)': ('Trustee of Michigan State University', ''),
    'Governor of Wayne State University (Vote for 2)': ('Governor of Wayne State University', ''),
    'Justice of Supreme Court (Vote for 2)': ('Justice of Supreme Court', ''),
    'State Proposal 22-1 (Vote for 1)': ('Proposal 22-1', ''),
    'State Proposal 22-2 (Vote for 1)': ('Proposal 22-2', ''),
    'State Proposal 22-3 (Vote for 1)': ('Proposal 22-3', ''),
}
ORDINAL = r'(\d+)(?:st|nd|rd|th)'
PATTERN_TITLES = [
    (re.compile(rf'^Representative in Congress {ORDINAL} District \(Vote for \d+\)$'), 'U.S. House'),
    (re.compile(rf'^State Senator {ORDINAL} District \(Vote for \d+\)$'), 'State Senate'),
    (re.compile(rf'^Representative in State Legislature {ORDINAL} District \(Vote for \d+\)$'), 'State House'),
    (re.compile(rf'^Judge of Court of Appeals {ORDINAL} District .* \(Vote for \d+\)$'), 'Court of Appeals Judge'),
    (re.compile(rf'^Judge of Circuit Court {ORDINAL} Circuit .* \(Vote for \d+\)$'), 'Circuit Court Judge'),
    (re.compile(rf'^Judge of District Court {ORDINAL} District .* \(Vote for \d+\)$'), 'District Court Judge'),
]

# Contests to rebuild/append; everything else is verification-only.
PATCH_OFFICES = {
    ('State House', '39'), ('State House', '42'), ('State House', '43'),
    ('State Board of Education', ''), ('Regent of the University of Michigan', ''),
    ('Trustee of Michigan State University', ''), ('Governor of Wayne State University', ''),
    ('Justice of Supreme Court', ''), ('Court of Appeals Judge', '3'),
    ('Circuit Court Judge', '48'), ('District Court Judge', '58'),
    ('Proposal 22-1', ''), ('Proposal 22-2', ''),
}


def map_title(title):
    if title in EXACT_TITLES:
        return EXACT_TITLES[title]
    for pat, office in PATTERN_TITLES:
        m = pat.match(title)
        if m:
            return office, m.group(1)
    return None


def get_lines(page):
    """Extract words, group into lines, drop the page header/footer."""
    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    lines = []
    for w in sorted(words, key=lambda w: (w['top'], w['x0'])):
        for line in lines:
            if abs(line[0]['top'] - w['top']) <= 2.5:
                line.append(w)
                break
        else:
            lines.append([w])
    out = []
    for line in lines:
        line.sort(key=lambda w: w['x0'])
        text = ' '.join(w['text'] for w in line)
        if text.startswith('Results per Precinct') or re.match(r'^\d+ of \d+ ', text):
            continue
        out.append((text, line))
    return out


def parse_pdf(known):
    """Return {(office, district): [block, ...]} where each block is
    {'title': str, 'rows': [(label, {col: votes}), ...], 'total': [values...],
    'ncols': int}. known maps norm(precinct) -> canonical file precinct name."""

    def resolve(fragments):
        """Longest suffix of the fragment list matching a known precinct
        (skips candidate-header junk accumulated ahead of the first row)."""
        for k in range(len(fragments)):
            hit = known.get(norm(' '.join(fragments[k:])))
            if hit is not None:
                return hit
        return None

    blocks = []  # (title, [line, ...])
    with pdfplumber.open(PDF) as pdf:
        stream = []
        for page in pdf.pages:
            stream.extend(get_lines(page))
    for text, line in stream:
        if TITLE.search(text) and text.split()[0] != 'Total':
            blocks.append((text, []))
        elif blocks:
            blocks[-1][1].append((text, line))
        # else: cover-page text before the first contest title - ignored

    contests = {}
    for title, lines in blocks:
        key = map_title(title)
        if key is None:
            continue  # local office - not in scope
        # Columns anchored on the Total row (numbers are right-aligned, so
        # cluster on the right edge x1).
        total = None
        data = []
        for text, line in lines:
            first = line[0]['text']
            nums = [w for w in line if NUMERIC.match(w['text'])]
            if first == 'Total' and len(nums) == len(line) - 1:
                total = nums
            else:
                data.append((text, line, nums))  # data rows and label fragments
        if total is None:
            sys.exit(f'{title}: no Total row')
        cols = []
        for w in sorted(total, key=lambda w: w['x1']):
            if not cols or w['x1'] - cols[-1] > 8:
                cols.append(w['x1'])
        total_vals = [int(w['text'].replace(',', '')) for w in total]

        rows = []
        buf = []   # pending label fragments
        held = None  # (nums) waiting for its label to complete
        seen = set()
        for text, line, nums in data:
            values, extra = [], []
            for w in line:
                if NUMERIC.match(w['text']):
                    for i, cx in enumerate(cols):
                        if abs(w['x1'] - cx) <= 6:
                            values.append((i, int(w['text'].replace(',', ''))))
                            break
                    else:
                        extra.append(w['text'])
                else:
                    extra.append(w['text'])
            if not values:
                buf.append(text.strip())
                if held is not None:
                    label = resolve(buf)
                    if label is not None:
                        if label in seen:
                            sys.exit(f'{title}: duplicate precinct {label!r}')
                        rows.append((label, held))
                        buf, held = [], None
                continue
            # try to resolve the label from the fragments seen since the last
            # numbers line (longest suffix first, to skip header junk)
            label = resolve(buf + extra)
            if held is not None and label is None:
                sys.exit(f'{title}: unresolved label before next numbers row {buf!r} {extra!r}')
            if held is not None:
                if label in seen:
                    sys.exit(f'{title}: duplicate precinct {label!r}')
                seen.add(label)
                rows.append((label, held))
                buf, held = [], None
                continue
            if label is None:
                held = dict(values)
                buf = buf + extra  # fragments after the numbers complete it
            else:
                if label in seen:
                    sys.exit(f'{title}: duplicate precinct {label!r}')
                seen.add(label)
                rows.append((label, dict(values)))
                buf = []
        if held is not None or buf:
            sys.exit(f'{title}: unparsed residue held={held!r} buf={buf!r}')

        contests.setdefault(key, []).append(
            {'title': title, 'rows': rows, 'total': total_vals, 'ncols': len(cols)})
    return contests


def norm(name):
    return re.sub(r'\s+', '', name).lower()


def main(apply=False):
    with open(COUNTY_CSV, newline='') as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        file_rows = list(reader)
    # The file historically carries BOTH spellings of the same precincts
    # ('Precinct 1- ALG' from the original parse, 'Precinct 1-ALG' from the
    # 22-3 append) - for some precincts Straight Party / U.S. House votes are
    # literally split across the two. Collapse to the PDF's unspaced form.
    for r in file_rows:
        r['precinct'] = re.sub(r'-\s+', '-', r['precinct'])
    seen_keys = set()
    for r in file_rows:
        key = (r['precinct'], r['office'], r['district'], r['candidate'])
        if key in seen_keys:
            sys.exit(f'duplicate row after name normalization: {key}')
        seen_keys.add(key)
    known = {norm(r['precinct']): r['precinct'] for r in file_rows}
    if len(known) != len({r['precinct'] for r in file_rows}):
        sys.exit('ambiguous precinct normalization')
    cenr = defaultdict(dict)  # (office, district) -> candidate -> (party, votes)
    for r in csv.DictReader(open(CENR_CSV)):
        if r['county'] == 'Allegan':
            cenr[(r['office'], r['district'])][r['candidate']] = (r['party'], int(r['votes']))

    contests = parse_pdf(known)

    new_rows = []
    problems = []
    for key, blocks in sorted(contests.items()):
        office, district = key
        pool = dict(cenr.get(key, {}))  # candidate -> (party, votes)
        if not pool:
            # e.g. Straight Party: not in the CENR - match columns against the
            # file's own per-candidate sums instead
            sums = defaultdict(int)
            parties = {}
            for r in file_rows:
                if r['office'] == office and r['district'] == district:
                    sums[r['candidate']] += int(r['votes'])
                    parties[r['candidate']] = r['party']
            pool = {cand: (parties[cand], total) for cand, total in sums.items()}
        # per-candidate per-precinct votes accumulated across blocks
        cand_prects = defaultdict(dict)  # candidate -> precinct -> votes
        for bi, block in enumerate(blocks):
            cols_used = {}
            unmatched = []
            for ci, total in enumerate(block['total']):
                match = [c for c, (p, v) in pool.items() if v == total and c not in cols_used.values()]
                if len(match) == 1:
                    cols_used[ci] = match[0]
                else:
                    unmatched.append(total)
            for cand in cols_used.values():
                if cand not in pool:
                    problems.append(f'{key} block {bi}: candidate {cand!r} reused across blocks')
                del pool[cand]
            if unmatched:
                # write-in / trailing columns - all columns after the first
                # unmatched one must also be unmatched (write-in is last)
                tail = block['total'][block['total'].index(unmatched[0]):]
                if tail != unmatched:
                    problems.append(f'{key} block {bi}: unmatched totals mid-row {unmatched} in {block["total"]}')
            for label, values in block['rows']:
                for ci, v in values.items():
                    cand = cols_used.get(ci)
                    if cand is not None:
                        cand_prects[cand][label] = cand_prects[cand].get(label, 0) + v
            # verify column sums == printed Total
            sums = {}
            for label, values in block['rows']:
                for ci, v in values.items():
                    sums[ci] = sums.get(ci, 0) + v
            for ci, total in enumerate(block['total']):
                if sums.get(ci, 0) != total:
                    problems.append(f'{key} block {bi}: column {ci} sum {sums.get(ci, 0)} != Total {total}')
            print(f'{block["title"]}: {len(block["rows"])} precincts, '
                  f'{len(cols_used)} of {block["ncols"]} columns matched')

        if key not in PATCH_OFFICES:
            # verification-only: compare per-precinct values with the file
            if not cand_prects:
                problems.append(f'{key}: no candidates matched')
                continue
            for cand, prects in sorted(cand_prects.items()):
                party, cenr_votes = pool.get(cand, (None, None))
                got = sum(prects.values())
                if cenr_votes is not None and got != cenr_votes:
                    problems.append(f'{key} {cand}: parsed sum {got} != CENR {cenr_votes}')
                file_map = {r['precinct']: int(r['votes']) for r in file_rows
                            if r['office'] == office and r['district'] == district
                            and r['candidate'] == cand}
                for prect, v in prects.items():
                    if file_map.get(prect) != v:
                        problems.append(f'{key} {cand} / {prect}: parsed {v} vs file {file_map.get(prect)}')
                for prect in file_map:
                    if prect not in prects:
                        problems.append(f'{key} {cand} / {prect}: in file, not parsed from PDF')
            continue

        # patched contest: emit rows for every matched candidate
        for cand, (party, cenr_votes) in cenr.get(key, {}).items():
            prects = cand_prects.get(cand)
            if prects is None:
                if cenr_votes == 0:
                    continue  # 0-vote write-in candidates have no PDF column
                problems.append(f'{key}: CENR candidate {cand!r} ({cenr_votes}) not matched from PDF')
                continue
            got = sum(prects.values())
            if got != cenr_votes:
                problems.append(f'{key} {cand}: parsed sum {got} != CENR {cenr_votes}')
            for prect in sorted(prects):
                new_rows.append({'county': 'Allegan', 'precinct': prect,
                                 'office': office, 'district': district,
                                 'party': party, 'candidate': cand,
                                 'election_day': '', 'absentee': '',
                                 'votes': prects[prect]})

    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not applying')

    # rebuild: drop file rows for patched contests, append the new rows
    kept = [r for r in file_rows
            if (r['office'], r['district']) not in PATCH_OFFICES]
    out = kept + new_rows
    print(f'kept {len(kept)} rows, patching {len(new_rows)} rows '
          f'(file {len(out)} rows)')
    if apply:
        with open(COUNTY_CSV, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(out)
        print('wrote', COUNTY_CSV)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)