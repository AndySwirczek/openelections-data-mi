"""Parse the Alcona County Aug 2020 primary "Copy of Primary Spreadsheet"
PDF into a precinct CSV.

Source: 'Alcona MI Copy-of-Primary-Spreadsheet1.pdf'
(openelections-sources-mi/2020/primary), 6 pages. An Excel-exported
spreadsheet: page 1 is the REP table, page 2 the DEM table + countywide
proposals, each with one row per precinct (Alcona has 12 precincts, one per
township/city) plus a Totals row; pages 3-4 are per-township summaries of the
township offices and township proposals (Position / Party /
Candidate/Millage / # of Votes / Yes / No); pages 5-6 list the elected
precinct delegates (not parsed).

Layout quirks handled:
- The rotated column headers (candidate names, and the office-title band
  above them) read bottom-to-top: chars are read in descending-top order
  with a space where prev_top - cur_bottom >= 1.4pt (letter boxes touch,
  word gaps are ~2.7pt).
- Value cells are right-aligned and the 4-digit RV numbers use wider boxes,
  so values are assigned to columns via midpoints between the Totals row's
  value centers.
- Three header cells span two value columns (Registered Voters + Poll Book
  Totals, the two Road Commissioner candidates, and the two County
  Commissioner District 2 candidates); every column's precinct sum is
  validated against its printed Totals value.
- Pages 3-4 township-office rows give township-level totals; since each
  township is a single precinct they are the precinct results. Township
  proposals are kept as printed with Yes/No candidates. Rows with no votes
  (write-in candidates that did not qualify) are emitted with 0 votes;
  contests with no candidates at all are skipped with a NOTE.

Usage: .venv/bin/python src/alcona_2020.py
"""
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import write_csv  # noqa: E402

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Alcona MI Copy-of-Primary-Spreadsheet1.pdf')

COUNTY = 'Alcona'

# precinct label on pages 1-2 / section header on pages 3-4 -> full name
PRECINCTS = {
    'Alcona': 'Alcona Township, Precinct 1',
    'Caledonia': 'Caledonia Township, Precinct 1',
    'Curtis': 'Curtis Township, Precinct 1',
    'Greenbush': 'Greenbush Township, Precinct 1',
    'Gustin': 'Gustin Township, Precinct 1',
    'Harrisville': 'Harrisville Township, Precinct 1',
    'Hawes': 'Hawes Township, Precinct 1',
    'Haynes': 'Haynes Township, Precinct 1',
    'Mikado': 'Mikado Township, Precinct 1',
    'Millen': 'Millen Township, Precinct 1',
    'Mitchell': 'Mitchell Township, Precinct 1',
    'City Harrisville': 'City of Harrisville, Precinct 1',
}

# data columns in Totals-row order on page 1 (REP)
PAGE1_COLS = [
    ('Registered Voters', '', ''),
    ('Ballots Cast', '', ''),
    ('U.S. Senate', '', 'REP', 'John James'),
    ('U.S. House', '1', 'REP', 'Jack Bergman'),
    ('State House', '106', 'REP', 'Sue Allor'),
    ('Prosecuting Attorney', '', 'REP', 'Thomas Jay Weichel'),
    ('Sheriff', '', 'REP', 'Scott Stephenson'),
    ('Clerk', '', 'REP', 'Stephany Eller'),
    ('Treasurer', '', 'REP', 'Cheryl L. Franks'),
    ('Register of Deeds', '', 'REP', 'Missie A. Cordes'),
    ('Road Commissioner', '', 'REP', 'Harry L. Harvey'),
    ('Road Commissioner', '', 'REP', 'John F. Oliver'),
    ('County Commissioner', '1', 'REP', 'Daniel G. Gauthier'),
    ('County Commissioner', '2', 'REP', 'Craig Johnston'),
    ('County Commissioner', '2', 'REP', 'William Thompson'),
    ('County Commissioner', '3', 'REP', 'Carolyn N. Brummund'),
    ('County Commissioner', '4', 'REP', 'Adam Brege'),
    ('County Commissioner', '5', 'REP', 'John Terry Small'),
]

# page 2 (DEM): the first 14 Totals columns
PAGE2_COLS = [
    ('U.S. Senate', '', 'DEM', 'Gary Peters'),
    ('U.S. House', '1', 'DEM', 'Dana Ferguson'),
    ('U.S. House', '1', 'DEM', "Linda O'Dell"),
    ('State House', '106', 'DEM', 'LeeAnn Johnson'),
    ('Prosecuting Attorney', '', 'DEM', None),
    ('Sheriff', '', 'DEM', None),
    ('Clerk', '', 'DEM', None),
    ('Treasurer', '', 'DEM', None),
    ('Road Commissioner', '', 'DEM', None),
    ('County Commissioner', '1', 'DEM', None),
    ('County Commissioner', '2', 'DEM', None),
    ('County Commissioner', '3', 'DEM', None),
    ('County Commissioner', '4', 'DEM', None),
    ('County Commissioner', '5', 'DEM', 'Kevin K. Boyat, Sr.'),
]

# expected proposal titles on page 2 (order = Yes-column order)
PAGE2_PROPOSALS = [
    'MSUE/4H Millage Renewal',
    'Veterans Millage',
    'Ambulance & EMS Millage',
    'Alcona Community Schools Operating Millage Renewal',
    'Alcona Community Schools Sinking Fund Millage',
    'Fairview Area Schools Operating Millage Renewal',
]

NUM_RE = re.compile(r'^\d[\d,]*$')


def rows_by_top(page):
    grouped = {}
    for e in page.find_all('text'):
        t = e.extract_text()
        if t is None or not t.strip():
            continue
        key = round(e.top)
        for k in grouped:
            if abs(k - key) <= 2:
                key = k
                break
        grouped.setdefault(key, []).append(
            (e.x0, e.x1, e.top, e.bottom, t.strip()))
    return {top: sorted(v) for top, v in grouped.items()}


def read_rotated(chars):
    """Chars [(top, bottom, text)] -> string reading bottom-to-top with a
    space where prev_top - cur_bottom >= 1.4."""
    out, prev_top = '', None
    for top, bot, t in sorted(chars, reverse=True):
        if prev_top is not None and prev_top - bot >= 1.4:
            out += ' '
        out += t
        prev_top = top
    return out


def assign(values, centers):
    """Value boxes -> column indices via midpoints between Totals centers."""
    bounds = [(a + b) / 2 for a, b in zip(centers, centers[1:])]
    out = {}
    for i, (x0, x1, _, _, t) in enumerate(values):
        if not NUM_RE.match(t):
            continue
        c = (x0 + x1) / 2
        hit = None
        if c < bounds[0]:
            hit = 0
        elif c >= bounds[-1]:
            hit = len(centers) - 1
        else:
            for j, b in enumerate(bounds):
                if c < b:
                    hit = j
                    break
        if hit is None or hit in out:
            raise ValueError(f'value {t!r} at {c:.1f} unmapped/duplicated')
        out[hit] = int(t.replace(',', ''))
    return out


def parse_sheets(pdf):
    """Pages 1-2 -> {(precinct, office, district, party, candidate): votes}
    plus per-precinct [registered voters, ballots cast]."""
    errors, notes = [], []
    rows = {}
    pseudo = {}
    for pageno, page in enumerate(pdf.pages[:2], 1):
        rows_by = rows_by_top(page)
        totals_top = next(t for t, elems in sorted(rows_by.items())
                          if any(x0 < 95 and t.strip() == 'Totals'
                                 for x0, x1, _, _, t in elems))
        totals = rows_by[totals_top]
        centers = [(x0 + x1) / 2 for x0, x1, _, _, t in totals
                   if NUM_RE.match(t)]
        n_cand = len(PAGE1_COLS if pageno == 1 else PAGE2_COLS)
        n_prop = 12 if pageno == 2 else 0
        if len(centers) != n_cand + n_prop:
            errors.append(f'page {pageno}: {len(centers)} columns != '
                          f'{n_cand + n_prop}')
            continue
        sums = defaultdict(int)
        for top, elems in sorted(rows_by.items()):
            label = ' '.join(t for x0, x1, _, _, t in elems if x0 < 100)
            if not label:
                continue      # rotated header glyphs / upright page titles
            if label == 'Totals':
                continue
            if label not in PRECINCTS:
                errors.append(f'page {pageno}: bad row label {label!r}')
                continue
            precinct = PRECINCTS[label]
            values = assign(elems, centers)
            cols = PAGE1_COLS if pageno == 1 else PAGE2_COLS
            for i in range(n_cand):
                col = cols[i]
                office, district, party = col[0], col[1], col[2]
                cand = col[3] if len(col) > 3 else ''
                v = values.get(i, 0)
                sums[i] += v
                if office in ('Registered Voters', 'Ballots Cast'):
                    pseudo.setdefault(precinct, [0, 0])[i] = v
                    continue
                if not cand:
                    if v:
                        errors.append(f'page {pageno} {label}: {office} '
                                      f'has no candidate but {v} votes')
                    continue
                key = (precinct, office, district, party, cand)
                if key in rows:
                    errors.append(f'duplicate row {key}')
                rows[key] = v
            for i in range(n_cand, len(centers)):
                sums[i] += values.get(i, 0)
        # Totals-row validation (numeric cells only; index 0 = first column)
        for i, val in enumerate(v for v in totals if NUM_RE.match(v[4])):
            printed = int(val[4].replace(',', ''))
            if sums.get(i, 0) != printed:
                errors.append(f'page {pageno} col {i}: Totals {printed} '
                              f'!= parsed {sums.get(i, 0)}')
        # proposal columns: 6 Yes/No pairs; titles are upright wrapped text
        if pageno == 2:
            prop_titles = proposal_titles(page)
            pairs = list(zip(range(n_cand, len(centers), 2),
                             range(n_cand + 1, len(centers), 2)))
            if len(prop_titles) != len(pairs):
                errors.append(f'page 2: {len(prop_titles)} proposal titles '
                              f'for {len(pairs)} Yes/No pairs')
                prop_titles = PAGE2_PROPOSALS
            for i, ((yes_i, no_i), title) in enumerate(zip(pairs, prop_titles)):
                expected = PAGE2_PROPOSALS[i] if i < len(PAGE2_PROPOSALS) \
                    else None
                if title != expected:
                    notes.append(f'page 2 proposal {i}: title {title!r} vs '
                                 f'expected {expected!r}')
                for top, elems in sorted(rows_by.items()):
                    label = ' '.join(t for x0, x1, _, _, t in elems
                                     if x0 < 100)
                    if not label or label not in PRECINCTS:
                        continue
                    values = assign(elems, centers)
                    precinct = PRECINCTS[label]
                    yes = values.get(yes_i, 0)
                    no = values.get(no_i, 0)
                    for cand, v in (('Yes', yes), ('No', no)):
                        key = (precinct, title, '', '', cand)
                        if key in rows:
                            errors.append(f'duplicate row {key}')
                        rows[key] = v
    return rows, pseudo, errors, notes


def proposal_titles(page):
    """Upright wrapped proposal titles in the x>=540 header zone, clustered
    by x-overlap, joined top-to-bottom."""
    elems = []
    for e in page.find_all('text'):
        t = e.extract_text()
        if t is None or not t.strip() or not (100 <= e.top <= 170) \
                or e.x0 < 540:
            continue
        elems.append((e.x0, e.x1, e.top, t.strip()))
    clusters = []
    for x0, x1, top, t in sorted(elems):
        placed = False
        for cl in clusters:
            if min(x1, cl['x1']) - max(x0, cl['x0']) > 0.5:
                cl['x0'] = min(cl['x0'], x0)
                cl['x1'] = max(cl['x1'], x1)
                cl['parts'].append((top, x0, t))
                placed = True
                break
        if not placed:
            clusters.append({'x0': x0, 'x1': x1, 'parts': [(top, x0, t)]})
    out = []
    for cl in sorted(clusters, key=lambda c: c['x0']):
        text = ' '.join(t for _, _, t in sorted(cl['parts']))
        out.append(text)
    return out


def parse_townships(pdf):
    """Pages 3-4: per-jurisdiction office and proposal summaries. Each of the
    12 jurisdictions has exactly one precinct, so these rows are the precinct
    results. Township offices are emitted as '<Jurisdiction> <Office>'
    (position 'Trustee - Write-In' -> 'Trustee'); township proposals as
    '<millage text> <Jurisdiction>' with Yes/No candidates.

    Rows whose position has no candidate (e.g. Curtis Township Supervisor)
    or no candidates at all (Alderman-Ward 1/3) are skipped with a NOTE;
    qualified write-in rows print no votes and are emitted with 0."""
    errors, notes = [], []
    out = []
    cur = None          # (precinct label key, jurisdiction display name)
    for pageno, page in enumerate(pdf.pages[2:4], 3):
        for top, elems in sorted(rows_by_top(page).items()):
            texts = [t for _, _, _, _, t in elems]
            if 'Position' in texts or 'AUGUST' in texts[0]:
                continue
            if len(texts) == 1 and re.search(r'TOWNSHIP|CITY OF', texts[0]):
                sect = texts[0].strip()
                if sect.startswith('CITY OF '):
                    key = 'City ' + sect[len('CITY OF '):].title()
                    juris = 'City of ' + sect[len('CITY OF '):].title()
                else:
                    key = sect[:-len(' TOWNSHIP')].title()
                    juris = key + ' Township'
                if key not in PRECINCTS:
                    errors.append(f'page {pageno}: bad section {sect!r}')
                    cur = None
                else:
                    cur = (PRECINCTS[key], juris)
                continue
            first = elems[0]
            if first[0] > 60 or cur is None:
                continue
            precinct, juris = cur
            position = first[4]
            nums = [(x0, t) for x0, x1, _, _, t in elems
                    if NUM_RE.match(t)]
            if position == 'Proposal':
                if len(nums) != 2:
                    errors.append(f'page {pageno} {precinct}: proposal row '
                                  f'{texts} has {len(nums)} values')
                    continue
                office = f"{elems[1][4]} {juris}"
                for cand, (x0, t) in zip(('Yes', 'No'), sorted(nums)):
                    out.append((precinct, office, '', '', cand,
                                int(t.replace(',', ''))))
                continue
            party = next(({'Dem': 'DEM', 'Rep': 'REP'}[t]
                          for _, _, _, _, t in elems if t in ('Dem', 'Rep')),
                         '')
            cand = next((t for x0, x1, _, _, t in elems
                         if 230 <= x0 < 400), None)
            if cand is None:
                notes.append(f'{precinct}: {position} has no candidate; '
                             'skipped')
                continue
            office_word = re.split(r'\s+-\s+', position)[0]
            votes = int(nums[0][1].replace(',', '')) if nums else 0
            if not nums:
                notes.append(f'{precinct}: {office_word} {cand} prints no '
                             'votes; emitted 0')
            out.append((precinct, f'{juris} {office_word}', '',
                        party, cand, votes))
    return out, errors, notes


def main():
    import natural_pdf
    pdf = natural_pdf.PDF(SRC)
    errors, notes = [], []

    rows, pseudo, err, note = parse_sheets(pdf)
    errors += err
    notes += note
    trows, terr, tnote = parse_townships(pdf)
    errors += terr
    notes += tnote
    if errors:
        for e in errors[:30]:
            print('ERROR', e)
        sys.exit(f'Alcona: {len(errors)} errors')
    for n in notes:
        print('NOTE', n)

    seen = set()
    out = []
    for (precinct, office, district, party, cand), votes in rows.items():
        seen.add((precinct, office, party, cand))
        out.append({'county': COUNTY, 'precinct': precinct,
                    'office': office, 'district': district,
                    'party': party, 'candidate': cand, 'votes': votes})
    for precinct, office, district, party, cand, votes in trows:
        if (precinct, office, party, cand) in seen:
            errors.append(f'township row duplicates sheet row: '
                          f'{precinct} {office} {cand}')
            continue
        out.append({'county': COUNTY, 'precinct': precinct,
                    'office': office, 'district': district,
                    'party': party, 'candidate': cand, 'votes': votes})
    if errors:
        for e in errors[:30]:
            print('ERROR', e)
        sys.exit(f'Alcona: {len(errors)} errors')
    for precinct, (rv, bc) in sorted(pseudo.items()):
        out.append({'county': COUNTY, 'precinct': precinct,
                    'office': 'Registered Voters', 'district': '',
                    'party': '', 'candidate': '', 'votes': rv})
        out.append({'county': COUNTY, 'precinct': precinct,
                    'office': 'Ballots Cast', 'district': '',
                    'party': '', 'candidate': '', 'votes': bc})
    write_csv(COUNTY, out)


if __name__ == '__main__':
    main()