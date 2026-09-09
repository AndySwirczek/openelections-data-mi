"""Parse the two 2022 primary counties with HTML sources into per-county
precinct CSVs, verified against the certified county-level CENR.

Sources (openelections-sources-mi/2022/primary/):
- Calhoun 'Detailed Results': an <hN> contest heading ('Governor (DEM)',
  'Senate 17th District (DEM)', 'State Legislature Rep 44th District (DEM)')
  followed by a table whose header row is PRECINCT NAME / # REG. VOTERS /
  POLL BOOK / % REG VOTERS / <candidate columns>, then a Totals row and one
  row per precinct. The 'Invaild Write-in' (sic) column holds write-in votes.
- Delta 'Precinct Results': an <hN> heading ('Governor for State (DEM) (Vote
  for 1)') followed by a table with a Precinct header and 'Gretchen Whitmer -
  DEM' / 'Write-in' candidate columns, one row per precinct, no totals row.

Only the four offices the CENR carries are emitted; local contests are
skipped. See src/primary_2022_common.py for the verification rules.

Usage: .venv/bin/python src/primary_2022_html_counties.py [--apply]
"""
import html as html_mod
import re
import sys
from collections import defaultdict

from primary_2022_common import classify, one_space, verify, write

SOURCES = {
    'Calhoun': ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
                "Calhoun County Aug 2022 Primary Detailed Results.html"),
    'Delta': ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
              "Delta County Aug 2022 Primary Precinct Results.html"),
}
ROW = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
CELL = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
HEAD = re.compile(r'<h\d[^>]*>(.*?)</h\d>|<table.*?</table>', re.S)
# NOTE: use finditer; with findall the table branch (no capture group) comes
# back as an empty string
META_HEADERS = {'precinct name', '# reg. voters', 'poll book', '% reg voters'}


def text(piece):
    """Cell/heading text: strip tags, unescape entities, collapse spaces."""
    return one_space(html_mod.unescape(re.sub(r'<[^>]+>', '', piece)))


def cand_key(name):
    """Candidate column header -> CENR candidate name: drop Delta's ' - DEM'
    party suffix and Calhoun's '(Write In)' qualified-write-in tag."""
    return re.sub(r'\s*\(Write[-\s]?In\)$', '',
                  re.sub(r'\s+-\s+(DEM|REP)$', '', name), flags=re.I)


def parse(county, path):
    """[(precinct, office, district, party, candidate, votes)] plus skipped
    contest names and per-contest write-in vote totals."""
    raw = open(path, encoding='utf-8', errors='replace').read()
    rows = []
    skipped = defaultdict(int)
    write_ins = defaultdict(int)
    lumped = defaultdict(int)
    problems = []
    heading = None
    for mo in HEAD.finditer(raw):
        heading_text, table = mo.group(1), mo.group(0)
        if heading_text is not None:
            heading = text(heading_text)  # contest heading
            continue
        if heading is None:
            continue
        got = classify(heading)
        if not got:
            skipped[one_space(heading)] += 1
            heading = None
            continue
        office, district, party = got
        header = None
        totals = None
        for tr in ROW.findall(table):
            cells = [text(c) for c in CELL.findall(tr)]
            cells = [c for c in cells if c != ''] if cells and cells[0] == '' else cells
            if not cells:
                continue
            first = cells[0].lower()
            if first in META_HEADERS or first == 'precinct':
                header = cells
                continue
            if first in ('totals', 'total'):
                totals = cells
                continue
            if header is None or not cells[0]:
                continue
            for ci, cand in enumerate(header):
                if ci >= len(cells) or not cand or cand.lower() in META_HEADERS \
                        or cand.lower() == 'precinct':
                    continue
                v = cells[ci].replace(',', '').strip()
                if not v:
                    continue
                votes = int(v)
                if re.fullmatch(r'(?:invaild )?write-?in', cand, re.I):
                    # lumped write-in column ('Write-in' / Calhoun's
                    # 'Invaild Write-in'); merge to one 'Write-In' row
                    write_ins[(office, district, party)] += votes
                    lumped[(cells[0], office, district, party)] += votes
                else:
                    rows.append((cells[0], office, district, party,
                                 cand_key(cand), votes))
        # verify column sums against the printed Totals row (Calhoun only)
        if totals:
            sums = defaultdict(int)
            for precinct, o, d, p, cand, votes in rows:
                if (o, d, p) == (office, district, party):
                    sums[cand] += votes
            sums[f'write-in'] = write_ins[(office, district, party)]
            for ci, name in enumerate(header):
                if name.lower() in META_HEADERS or not name or ci >= len(totals):
                    continue
                want = totals[ci].replace(',', '').strip()
                if not re.fullmatch(r'\d+', want):
                    continue
                key = 'write-in' \
                    if re.fullmatch(r'(?:invaild )?write-?in', name, re.I) \
                    else cand_key(name)
                if int(want) != sums.get(key, 0):
                    problems.append(f'{county} {office}[{district}] {name}: '
                                    f'column sum {sums.get(key, 0)} != printed Total {want}')
        heading = None
    for (precinct, office, district, party), votes in lumped.items():
        if votes:
            rows.append((precinct, office, district, party, 'Write-In', votes))
    return rows, skipped, write_ins, problems


def main(apply=False):
    problems = []
    for county, path in SOURCES.items():
        rows, skipped, write_ins, county_problems = parse(county, path)
        county_problems += verify(county, rows, write_ins)
        problems += county_problems
        print(f'{county}: {len(rows)} rows; '
              f'{sum(skipped.values())} local rows skipped in {len(skipped)} contests')
        if not county_problems and apply:
            write(county, rows)

    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)