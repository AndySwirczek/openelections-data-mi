"""Parse the St. Clair County Aug 2020 primary "STATEMENT OF VOTES (Results by
Precinct)" PDF into a precinct CSV.

Source: 'St. Clair MI 2020_Aug_Fed-State-County.pdf'
(openelections-sources-mi/2020/primary), 12 pages of precinct-major tables:
pages 1-2 DEM federal/state/county offices (with Reg'd Voters / Poll Book /
Turnout columns), pages 3-4 DEM county offices + commissioners, pages 5-6 REP
federal/state, pages 7-8 REP state rep 83 + county offices, pages 9-10 REP
commissioners, pages 11-12 county proposals. Each page pair splits the same
precinct list; every page repeats its contest header.

Layout quirks handled:
- Candidate names and office titles are stacked over 2-3 header rows; parts
  are merged by x-overlap (e.g. 'Chares'/'Richard'/'Armstrong II').
- Contest groups are the office-title columns; each candidate value column is
  right-aligned, so columns are recovered by clustering value x1 positions.
- '-AV' rows are the absentee counts for the base precinct: votes are merged
  into the base precinct, Ballots Cast = election-day + absentee poll book.
- 'No Cand.' columns (DEM sheriff/clerk, some commissioner districts) and
  group 'Total' columns are validated but not emitted.
- The 'Chares Richard Armstrong II' typo is corrected to the certified name.

Usage: .venv/bin/python src/stclair_2020.py
"""
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import write_csv  # noqa: E402

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'St. Clair MI 2020_Aug_Fed-State-County.pdf')

PSEUDO_PARTS = {"Reg'd", 'Voters', 'Poll', 'Book', 'Totals', 'Voter',
                'Turn-', 'Out', 'OPTIONS:', 'Turn-Out'}
NOCAND_RE = re.compile(r'^No\.?\s*Cand\.?$')
NUM_RE = re.compile(r'^\d{1,3}(?:,\d{3})*$')


def page_rows(page):
    """[(top, [(x0, x1, text)...]), ...] grouped by top (+/- 2)."""
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
        grouped.setdefault(key, []).append((e.x0, e.x1, t.strip()))
    return [(top, sorted(v)) for top, v in sorted(grouped.items())]


def left_text(row):
    return ' '.join(t for x0, x1, t in row if x0 < 130)


def merge_parts(parts):
    """Merge stacked header text parts into (text, center) by x-overlap."""
    groups = []
    for top, x0, x1, t in parts:
        placed = False
        for g in groups:
            if any(min(x1, gx1) - max(x0, gx0) > 0.5 for gx0, gx1, _ in g):
                g.append((x0, x1, t))
                placed = True
                break
        if not placed:
            groups.append([(x0, x1, t)])
    out = []
    for g in groups:
        text = ''
        for _, _, t in g:          # names wrapped as 'Vanden-'/'bossche'
            if text.endswith('-'):
                text = text[:-1] + t
            else:
                text = (text + ' ' + t).strip()
        center = sum((x0 + x1) / 2 for x0, x1, _ in g) / len(g)
        out.append((text, center))
    return out


OFFICE_MAP = [
    (re.compile(r'^U\.S\. Senator$'), ('U.S. Senate', '')),
    (re.compile(r'^Rep\.? in Congress (\d+)'), ('U.S. House', None)),
    (re.compile(r'^State Rep\.? (\d+)'), ('State House', None)),
    (re.compile(r'^Co Comm Dist (\d+)$'), ('County Commissioner', None)),
    (re.compile(r'^County Pros Atty$'), ('Prosecuting Attorney', '')),
    (re.compile(r'^County Sherff$'), ('Sheriff', '')),
    (re.compile(r'^County Sheriff$'), ('Sheriff', '')),
    (re.compile(r'^County Clerk/ROD$'), ('County Clerk/Register of Deeds', '')),
    (re.compile(r'^County Treasurer$'), ('County Treasurer', '')),
    (re.compile(r'^County Drain Comm$'), ('Drain Commissioner', '')),
    (re.compile(r'^County Surveyor$'), ('Surveyor', '')),
]

CAND_FIX = {'Chares Richard Armstrong II': 'Charles Richard Armstrong II'}
NAME_FIX = {'Wendlng': 'Wendlng'}  # printed as-is; no certified reference


def main():
    import natural_pdf
    pdf = natural_pdf.PDF(SRC)
    rows_out = []
    errors = []
    precinct_pseudo = {}   # precinct -> [registered_voters, poll_book_sum]

    for page in pdf.pages:
        rows = page_rows(page)
        contest_i = next(i for i, (_, r) in enumerate(rows)
                         if any(t == 'CONTEST' for _, _, t in r))
        first_data_i = None
        for i in range(contest_i + 1, len(rows)):
            lt = left_text(rows[i][1])
            if 'OPTIONS' in lt or not lt:
                continue
            if re.search(r'\b(Pct|Twp|City|Village|Township)\b', lt) and \
                    not lt.startswith('COUNTY'):
                first_data_i = i
                break
        if first_data_i is None:
            errors.append('page with no data rows')
            continue

        # ---- office-title groups (rows from ~88 down to the CONTEST row;
        # the page-title rows higher up are excluded so they can't bridge
        # adjacent office columns) --
        contest_top = rows[contest_i][0]
        office_parts = [(top, x0, x1, t)
                        for top, elems in rows[:contest_i + 1]
                        if top >= contest_top - 15
                        for x0, x1, t in elems
                        if x0 >= 130 and t != 'COUNTY PROPOSALS']
        is_proposals = any(t == 'COUNTY PROPOSALS'
                           for _, elems in rows for _, _, t in elems)
        offices = merge_parts(office_parts)
        # proposals: title continuation rows sit below the CONTEST row
        cand_zone = []
        title_parts = []
        for top, elems in rows[contest_i + 1:first_data_i]:
            texts = [t for _, _, t in elems]
            if is_proposals and not any(re.match(r'^(YES|NO|Total)$', t)
                                        for t in texts):
                title_parts.extend((top, x0, x1, t) for x0, x1, t in elems
                                   if x0 >= 130)
                continue
            cand_zone.append((top, elems))
        if title_parts:
            offices = merge_parts(office_parts + title_parts)
        offices = [(t, c) for t, c in offices
                   if t not in ('CONTEST',) and not NOCAND_RE.match(t)]

        # map each office title to (office, district)
        groups = []
        for title, center in offices:
            m = re.match(r'^(Proposition \d+.*)$', title)
            if m:
                office, district = m.group(1).strip(), ''
            else:
                office, district = None, ''
                for pat, o in OFFICE_MAP:
                    mm = pat.match(title)
                    if mm:
                        office = o[0]
                        district = mm.group(1) if o[1] is None else o[1]
                        break
                if office is None:
                    errors.append(f'unmapped office title {title!r}')
                    continue
            groups.append({'title': title, 'center': center,
                           'office': office, 'district': str(district),
                           'party': '', 'cands': []})

        # ---- party row: DEM/REP elements -> nearest office group ----------
        for top, elems in cand_zone:
            for x0, x1, t in elems:
                if t in ('DEM', 'REP'):
                    g = min(groups, key=lambda g: abs(g['center'] - (x0 + x1) / 2))
                    g['party'] = t

        # ---- candidate/pseudo columns -------------------------------------
        cand_parts = []
        for top, elems in cand_zone:
            for x0, x1, t in elems:
                if t in ('DEM', 'REP'):
                    continue
                if x0 < 130:
                    continue
                if x1 < 226 and t in PSEUDO_PARTS:
                    continue
                cand_parts.append((top, x0, x1, t))
        cols = merge_parts(cand_parts)

        # ---- column grid from the PDF's own separator rects ---------------
        # tall thin rects are vertical rules: ones starting above the CONTEST
        # row bound contest groups, the rest only bound candidate columns
        thin = [r for r in page.find_all('rect')
                if r.x1 - r.x0 <= 2 and r.bottom - r.top > 300]
        group_bounds = sorted({round(r.x0, 1) for r in thin
                               if r.top <= contest_top + 1})
        if not group_bounds:
            # even pages of a pair repeat the previous page's contests with
            # identical x0s, but their group rules start below the header
            group_bounds = prev_group_bounds
        prev_group_bounds = group_bounds
        col_bounds = sorted({round(r.x0, 1) for r in thin})
        matched = []
        for g in groups:
            b = next(((b0, b1) for b0, b1 in zip(group_bounds, group_bounds[1:])
                      if b0 < g['center'] < b1), None)
            if b is None and group_bounds and g['center'] > group_bounds[-1]:
                # last group on the stepped commissioner pages: its left rule
                # is tier-1 but its candidate/Total columns start lower, and
                # no further group follows, so absorb everything to the right
                b = (group_bounds[-1], col_bounds[-1])
            if b is None:
                errors.append(f"office {g['title']!r} outside grid groups")
                continue
            g['bounds'] = b
            matched.append(g)
        groups = matched

        def interval_of(center):
            for i in range(len(col_bounds) - 1):
                if col_bounds[i] < center < col_bounds[i + 1]:
                    return i
            return None

        def group_of_interval(i):
            c = (col_bounds[i] + col_bounds[i + 1]) / 2
            for g in groups:
                b0, b1 = g['bounds']
                if b0 <= c <= b1:
                    return g
            return None

        # (idx, text, pseudo, center, group)
        colnames = []
        for text, center in cols:
            idx = interval_of(center)
            if idx is None:
                errors.append(f'name {text!r} at {center} not mapped')
                continue
            pseudo = None
            tl = text.lower()
            if tl.startswith("reg'd"):
                pseudo = 'rv'
            elif tl.startswith('poll book'):
                pseudo = 'pb'
            elif tl.startswith('voter turn'):
                pseudo = 'pct'
            g = None if pseudo else group_of_interval(idx)
            if pseudo is None and g is None:
                errors.append(f'candidate column {text!r} at {center:.1f} '
                              'has no contest group')
            colnames.append((idx, text, pseudo, center,
                             g))

        # ---- data rows -----------------------------------------------------
        for _, elems in rows[first_data_i:]:
            label = left_text(elems)
            if not re.search(r'\b(Pct|Twp|City|Village|Township)\b', label) \
                    or label.startswith('COUNTY'):
                if 'OPTION TOTALS' in label:
                    continue
                if label and not re.match(r'^\d+$', label):
                    errors.append(f'unexpected row label {label!r}')
                continue
            m = re.match(r'^(.*?)-AV$', label)
            base_name, is_av = (m.group(1), True) if m else (label, False)
            precinct = re.sub(r'\s+Pct\s+', ' Precinct ', base_name).strip()
            values = {}
            for x0, x1, t in elems:
                if x1 < 130 or not NUM_RE.match(t):
                    continue
                idx = interval_of((x0 + x1) / 2)
                if idx is None:
                    errors.append(f'{label}: value {t} at x1 {x1} unmapped')
                    continue
                values[idx] = int(t.replace(',', ''))
            for idx, text, pseudo, center, g in colnames:
                if pseudo == 'rv':
                    if is_av:
                        errors.append(f'{label}: registered voters on AV row')
                    else:
                        precinct_pseudo.setdefault(
                            precinct, [0, 0])[0] = values.get(idx, 0)
                elif pseudo == 'pb':
                    precinct_pseudo.setdefault(precinct, [0, 0])[1] += \
                        values.get(idx, 0)
            # votes per (precinct, group, candidate)
            for idx, text, pseudo, center, g in colnames:
                if pseudo is not None or g is None or idx not in values:
                    continue
                if NOCAND_RE.match(text) or text == 'Total':
                    continue
                name = CAND_FIX.get(text, text)
                rows_out.append({'precinct': precinct, 'office': g['office'],
                                 'district': g['district'], 'party': g['party'],
                                 'candidate': name, 'votes': values[idx],
                                 '_g': g['title'], '_n': 1})
            # Total validation per row
            for idx, text, pseudo, center, g in colnames:
                if pseudo is None and text == 'Total' and g is not None \
                        and idx in values:
                    s = sum(values[idx2] for idx2, text2, p2, _, g2 in colnames
                            if p2 is None and text2 != 'Total'
                            and g2 is g and idx2 in values)
                    if values[idx] != s:
                        errors.append(f"{label} {g['title']}: Total "
                                      f"{values[idx]} != candidate sum {s}")

    if errors:
        for e in errors[:30]:
            print('ERROR', e)
        sys.exit(f'St. Clair: {len(errors)} errors')

    # ---- merge ED+AV duplicate rows (same precinct/office/candidate) ------
    merged = {}
    for r in rows_out:
        key = (r['precinct'], r['office'], r['district'], r['party'],
               r['candidate'])
        if key in merged:
            merged[key]['votes'] += r['votes']
            merged[key]['_n'] += 1
        else:
            merged[key] = r
    dupes = [k for k, r in merged.items() if r['_n'] > 2]
    if dupes:
        for k in dupes[:5]:
            print('ERROR', 'more than ED+AV rows for', k)
        sys.exit('duplicate rows beyond ED+AV')

    out = []
    for r in merged.values():
        out.append({'county': 'St. Clair', 'precinct': r['precinct'],
                    'office': r['office'], 'district': r['district'],
                    'party': r['party'], 'candidate': r['candidate'],
                    'votes': r['votes']})
    for precinct, (rv, pb) in sorted(precinct_pseudo.items()):
        out.append({'county': 'St. Clair', 'precinct': precinct,
                    'office': 'Registered Voters', 'district': '', 'party': '',
                    'candidate': '', 'votes': rv})
        out.append({'county': 'St. Clair', 'precinct': precinct,
                    'office': 'Ballots Cast', 'district': '', 'party': '',
                    'candidate': '', 'votes': pb})
    write_csv('St. Clair', out)


if __name__ == '__main__':
    main()