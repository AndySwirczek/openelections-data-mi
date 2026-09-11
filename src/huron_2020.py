"""Parse Huron County's Aug 2020 primary 'Results per Precinct' report
(openelections-sources-mi/2020/primary/'Huron MI Primary.pdf', 47pp,
image-only) into 2020/counties/20200804__mi__primary__huron__precinct.csv.

Contest-major HTML tables from the PaddleOCR cache
(/tmp/paddleocr_md/Huron_MI_Primary/pNNN.md): one table per candidate set,
header row 'Precinct | <name> - DEM | ... | Write-in', one row per precinct,
a final 'Total' row validated as the column sums.  Page breaks split tables
without repeating the title (continuation tables start directly with data
rows), so tables accumulate into the most recent contest.  Titles carry the
party ('United States Senator (DEM) (Vote for 1)'; proposals have no party
marker and Yes/No columns).  'Township Clerk for X Township' titles are
flipped jurisdiction-first; write-in columns become 'Write-In'.  No turnout
lines exist in the source, so no Ballots Cast pseudo-rows (Ionia/Saginaw
precedent).
"""
import csv
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import map_office, normalize_precinct, write_csv

CACHE = '/tmp/paddleocr_md/Huron_MI_Primary'
COUNTY = 'Huron'

TITLE_RE = re.compile(
    r'^(.*?)\s*\((DEM|REP)\)\s*\(Vote for \d+\)$'
    r'|^(.*)\s*\(Vote for \d+\)$')

ROW_RE = re.compile(r'<tr>(.*?)</tr>')
CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>')


def split_title(raw):
    text = raw.strip()
    m = TITLE_RE.match(text)
    if not m:
        return None
    if m.group(1):
        title, party = m.group(1).strip(), m.group(2)
    else:
        title, party = m.group(3).strip(), ''
    office = title.replace('County Prosecuting Attorney', 'Prosecuting '
                           'Attorney')
    office = office.replace('County Register of Deeds', 'Register of Deeds')
    dm = re.match(r'^Precinct Delegate for (.+)$', office)
    if dm:
        return (f'{dm.group(1)} Delegate to County Convention', '', party)
    m = re.match(r'^Township (\w+) for (.+)$', office)
    if m:
        office = f'{m.group(2)} {m.group(1)}'
    dm = re.search(r'(\d+)(?:st|nd|rd|th) District$', office)
    division = dm.group(0) if dm else ''
    base = re.sub(r'\s*(?:\d+(?:st|nd|rd|th) )?District$', '', office).strip()
    if base == 'Rep in State Legislature':
        base = 'Representative in State Legislature'
    if base == 'Rep in Congress':
        base = 'Representative in Congress'
    return (*map_office(base, division, COUNTY), party)


def parse():
    rows = []
    problems = []
    contest = None      # (office, district, party, candidates)
    sums = []           # per-candidate column sums

    files = sorted(os.listdir(CACHE))
    for fname in files:
        with open(os.path.join(CACHE, fname)) as fh:
            for line in fh:
                stripped = re.sub(r'</?div[^>]*>|^#\s*', '', line.strip())
                stripped = re.sub(r'^#+\s*', '', stripped).strip()
                if not stripped or stripped == '47':
                    continue
                if re.match(r'^Results per Precinct\b|^\d{4}-\d{2}-\d{2} '
                            r'\d{2}:\d{2}:\d{2}$', stripped):
                    continue
                if not stripped.startswith('<table'):
                    st = split_title(stripped)
                    if st:
                        contest = (st[0], st[1], st[2], None)
                        sums = []
                    else:
                        problems.append(f'{fname}: unparsed line '
                                        f'{stripped[:70]!r}')
                    continue
                if contest is None:
                    problems.append(f'{fname}: table without contest')
                    continue
                for tr in ROW_RE.findall(stripped):
                    cells = [html.unescape(c).strip()
                             for c in CELL_RE.findall(tr)]
                    if not cells or not any(cells):
                        # OCR emits spurious all-empty rows at page breaks
                        continue
                    office, district, party, candidates = contest
                    if cells[0] == 'Precinct':
                        candidates = []
                        for c in cells[1:]:
                            c = re.sub(r'\s*-\s*(DEM|REP)$', '', c)
                            candidates.append('Write-In'
                                              if c == 'Write-in' else c)
                        contest = (office, district, party, candidates)
                        sums = [0] * len(candidates)
                        continue
                    if any(not c for c in cells[1:]):
                        # truncated OCR row (e.g. Lincoln Constable p041)
                        problems.append(f'{fname}: truncated row for '
                                        f'{contest[:3]}: {cells!r}')
                        continue
                    values = [int(c.replace(',', '')) for c in cells[1:]]
                    if len(values) != len(sums):
                        problems.append(
                            f'{fname}: {len(values)} values vs '
                            f'{len(sums)} columns for {contest[:3]} at '
                            f'{cells[0]!r}')
                        continue
                    if candidates is None:
                        # data rows for a contest whose Total row already
                        # landed — should not happen
                        problems.append(f'{fname}: data row after Total for '
                                        f'{contest[:3]}: {cells!r}')
                        continue
                    if cells[0] == 'Total':
                        if values != sums:
                            problems.append(
                                f'{fname}: Total row {values} != column '
                                f'sums {sums} for {contest[:3]}')
                        contest = (office, district, party, None)
                        sums = []
                        continue
                    for i, v in enumerate(values):
                        sums[i] += v
                    for name, v in zip(candidates, values):
                        rows.append({
                            'county': COUNTY, 'precinct':
                                normalize_precinct(cells[0]),
                            'office': office, 'district': district,
                            'party': party, 'candidate': name, 'votes': v,
                        })
    return rows, problems


# p041's OCR truncated this table's header ('nis Weber') and both data rows
# ('Lincoln Town', 'To'); page-image check: Dennis Weber 127, write-in 0.
MANUAL_ROWS = {
    ('Lincoln Township Constable', '', 'REP'): [
        {'precinct': 'Lincoln Township Precinct 1', 'candidate':
         'Dennis Weber', 'votes': 127},
        {'precinct': 'Lincoln Township Precinct 1', 'candidate': 'Write-In',
         'votes': 0},
    ],
}


def apply_manual(rows, problems):
    for contest, mrows in MANUAL_ROWS.items():
        before = len(rows)
        rows = [r for r in rows if (r['office'], r['district'],
                                    r['party']) != contest]
        if len(rows) != before:
            problems.append(f'manual rows replaced {before - len(rows)} '
                            f'parsed rows for {contest}')
        for mr in mrows:
            rows.append({'county': COUNTY, 'office': contest[0],
                         'district': contest[1], 'party': contest[2], **mr})
    return rows, problems


def main():
    rows, problems = parse()
    rows, problems = apply_manual(rows, problems)
    for p in problems:
        print('PROBLEM:', p)
    write_csv(COUNTY, rows)


if __name__ == '__main__':
    main()