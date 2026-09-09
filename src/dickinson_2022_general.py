"""Patch the Dickinson 2022 general precinct CSV from the county per-precinct PDF.

Fixes the same two problems as Allegan in
2022/counties/20221108__mi__general__dickinson__precinct.csv:

1. U.S. House 1 stored Bob Lorinser's per-precinct votes +200 over the source
   (our total 3,768 vs the PDF's printed Total and the CENR both 3,568).
   The contest is rebuilt from the PDF.

2. The file lacked entire offices present in the certified CENR: Court of
   Appeals Judge 4, Circuit Court Judge 41, Justice of Supreme Court, State
   Board of Education, Regent, MSU Trustee, WSU Governor, Proposals 22-1/22-2.
   Those are appended from the same PDF.

Source: openelections-sources-mi/2022/general/'Dickinson MI Official results
November 2022.pdf' (image-only; PaddleOCR markdown cached under
/tmp/paddleocr_md/Dickinson_MI_Nov_2022_Official_results_orient/). OCR drops
most contest titles, so candidates are identified by matching each numeric
column's printed Total against the CENR county totals; unmatched trailing
columns are write-ins. County/local offices absent from the CENR are skipped.

Usage: .venv/bin/python src/dickinson_2022_general.py [--apply]
"""
import csv
import glob
import html
import re
import sys
from collections import defaultdict

CACHE = ('/tmp/paddleocr_md/Dickinson_MI_Nov_2022_Official_results_orient/'
         'Dickinson_MI_Official_results_November_2022')
COUNTY_CSV = '2022/counties/20221108__mi__general__dickinson__precinct.csv'
CENR_CSV = '2022/20221108__mi__general__county.csv'

ROW = re.compile(r'<tr>(.*?)</tr>')
CELL = re.compile(r'<td[^>]*>(.*?)</td>')

# contests to rebuild/append; everything else is verification-only
PATCH_OFFICES = {
    ('U.S. House', '1'),
    ('Court of Appeals Judge', '4'), ('Circuit Court Judge', '41'),
    ('Justice of Supreme Court', ''), ('State Board of Education', ''),
    ('Regent of the University of Michigan', ''),
    ('Trustee of Michigan State University', ''),
    ('Governor of Wayne State University', ''),
    ('Proposal 22-1', ''), ('Proposal 22-2', ''),
}


def clean(text):
    # '**' are OCR bold markers; '\n' inside a cell is a literal two-char
    # escape from the OCR markdown, not a newline
    text = re.sub(r'\\n', ' ', text)
    return re.sub(r'\s+', ' ', re.sub(r'\*\*', '', html.unescape(text))).strip()


def norm(name):
    return re.sub(r'\s+', '', name).lower()


def parse_contests():
    """Return [contest] where contest = {'headers': [str], 'rows': [(label,
    [values])], 'totals': [[values]]} assembled from the OCR tables; a table
    whose first row starts with 'Precinct' begins a contest, otherwise it
    continues the previous one (OCR split tables across pages and dropped most
    titles)."""
    contests = []
    for path in sorted(glob.glob(f'{CACHE}/p*.md')):
        md = open(path).read()
        for piece in re.split(r'(?=<table)', md):
            if not piece.startswith('<table'):
                continue
            rows = ROW.findall(piece)
            cells = [[clean(c) for c in CELL.findall(r)] for r in rows]
            cells = [r for r in cells if any(r)]
            if not cells:
                continue
            if cells[0] and cells[0][0] == 'Precinct':
                header = cells[0][1:]
                contests.append({'header': header, 'rows': [], 'totals': []})
            elif not contests:
                sys.exit(f'{path}: continuation table before any contest')
            for r in cells:
                if r[0].startswith('Total'):
                    contests[-1]['totals'].append([int(v.replace(',', '') or 0)
                                                   for v in r[1:]])
                elif r[0] != 'Precinct':
                    if re.search(r'\([A-Za-z. ]+ County\)$', r[0]):
                        continue  # cross-county tag row (e.g. Menominee) - not ours
                    contests[-1]['rows'].append((r[0], [int(v.replace(',', '') or 0) for v in r[1:]]))
    return contests


def main(apply=False):
    with open(COUNTY_CSV, newline='') as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        file_rows = list(reader)
    known = {norm(r['precinct']): r['precinct'] for r in file_rows}
    cenr = defaultdict(dict)  # (office, district) -> candidate -> (party, votes)
    for r in csv.DictReader(open(CENR_CSV)):
        if r['county'] == 'Dickinson':
            cenr[(r['office'], r['district'])][r['candidate']] = (r['party'], int(r['votes']))
    # file's Straight Party candidates by party code (for the Straight Party
    # contest, which the CENR does not carry)
    sp_by_party = {}
    for r in file_rows:
        if r['office'] == 'Straight Party' and r['party']:
            sp_by_party[r['party']] = r['candidate']

    problems = []
    new_rows = []
    for contest in parse_contests():
        header = contest['header']
        # Straight Party is the one contest not in the CENR: identify it by
        # header cells naming a party ('Democratic Party - DEM', ...) and match
        # columns to the file's candidates by party code
        parties = [re.sub(r'^.* - ', '', h) for h in header]
        if all(p in sp_by_party for p in parties) and parties:
            key = ('Straight Party', '')
            cols_used = {ci: sp_by_party[p] for ci, p in enumerate(parties)}
        else:
            # identify the contest by matching its Total row against each
            # CENR (office, district)'s candidate totals (greedy, left to right)
            if not contest['totals']:
                print(f'SKIP local contest (no totals): header {header}')
                continue
            totals = contest['totals'][0]
            scores = []
            for cand_key, pool in cenr.items():
                rest = dict(pool)
                used = {}
                for ci, total in enumerate(contest['totals'][0]):
                    match = [c for c, (p, v) in rest.items() if v == total]
                    if len(match) == 1:
                        used[ci] = match[0]
                        del rest[match[0]]
                scores.append((len(used), cand_key, used))
            scores.sort(reverse=True)
            # a CENR contest must have its matched columns explain at least
            # half the contest's total votes (a lone spurious 0/1-vote match
            # against a write-in candidate is a local contest)
            total_votes = sum(contest['totals'][0])
            matched_votes = sum(totals[ci] for ci in scores[0][2])
            if (len(scores) > 1 and scores[0][0] == scores[1][0]) or \
                    not scores or matched_votes < total_votes / 2:
                print(f'SKIP local contest: header {header}')
                continue
            key = scores[0][1]
            cols_used = scores[0][2]
        # verify column sums against the printed Total row
        sums = defaultdict(int)
        for label, values in contest['rows']:
            for ci, v in enumerate(values):
                sums[ci] += v
        for ci, total in enumerate(contest['totals'][0]):
            if sums[ci] != total:
                problems.append(f'{key}: column {ci} sum {sums[ci]} != Total {total}')
        if len(contest['totals']) > 1:
            problems.append(f'{key}: {len(contest["totals"])} Total rows')
        # every data row must be a known precinct
        for label, values in contest['rows']:
            if norm(label) not in known:
                problems.append(f'{key}: unknown precinct {label!r}')
        print(f'{key}: {len(contest["rows"])} precincts, '
              f'{len(cols_used)} of {len(contest["header"])} columns matched')

        if key not in PATCH_OFFICES:
            # verification-only: compare per-precinct values with the file
            for ci, cand in cols_used.items():
                file_map = {r['precinct']: int(r['votes']) for r in file_rows
                            if r['office'] == key[0] and r['district'] == key[1]
                            and r['candidate'] == cand}
                for prect, values in contest['rows']:
                    if file_map.get(prect) != values[ci]:
                        problems.append(f'{key} {cand} / {prect}: '
                                        f'PDF {values[ci]} vs file {file_map.get(prect)}')
            continue

        # patched contest: rebuild from the PDF
        for ci, cand in sorted(cols_used.items()):
            party, cenr_votes = cenr[key][cand]
            got = sum(values[ci] for _, values in contest['rows'])
            if got != cenr_votes:
                problems.append(f'{key} {cand}: parsed sum {got} != CENR {cenr_votes}')
            for prect, values in contest['rows']:
                new_rows.append({'county': 'Dickinson', 'precinct': prect,
                                 'office': key[0], 'district': key[1],
                                 'party': party, 'candidate': cand,
                                 'votes': values[ci]})
        # unmatched columns are write-ins; keep the file's existing convention
        write_in = sum(v for _, values in contest['rows']
                       for ci, v in enumerate(values) if ci not in cols_used)
        if write_in and any(r['candidate'] == 'Write-In' and r['office'] == key[0]
                            and r['district'] == key[1] for r in file_rows):
            for prect, values in contest['rows']:
                w = sum(v for ci, v in enumerate(values) if ci not in cols_used)
                new_rows.append({'county': 'Dickinson', 'precinct': prect,
                                 'office': key[0], 'district': key[1],
                                 'party': '', 'candidate': 'Write-In',
                                 'votes': w})

    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not applying')

    kept = [r for r in file_rows if (r['office'], r['district']) not in PATCH_OFFICES]
    out = kept + new_rows
    print(f'kept {len(kept)} rows, patching {len(new_rows)} rows (file {len(out)} rows)')
    if apply:
        with open(COUNTY_CSV, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(out)
        print('wrote', COUNTY_CSV)


if __name__ == '__main__':
    new_rows = []
    main(apply='--apply' in sys.argv)