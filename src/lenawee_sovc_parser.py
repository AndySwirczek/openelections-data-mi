"""Parse the Lenawee County 2024 primary precinct-by-precinct SOVC PDF.

Layout: front matter (pages 1-8) is a countywide turnout summary; each contest
then occupies a run of pages. A contest starts with a title line
("<office> (DEM) (Vote for N)") and a party line; its tables repeat per
precinct chunk, each chunk on a page pair — a left table ("Times Cast" and
"Registered Voters" columns by counting group) and, beside or after it, a
results table whose rotated column headers are the candidates plus "Total
Votes" and "Unresolved Write-In" columns (too-wide columns spill onto their
own page). Rows are "<precinct label>" then "Election Day / AV Counting
Boards / Early Voting / Total" with one value per column. Values below the
privacy threshold print as "****" (kept as blanks); "Total" rows are never
suppressed. "Cumulative" / "County - Total" footer rows close each table and
give the countywide column totals used for cross-checks.

Column names come from the rotated headers, which extract as reversed words
stacked bottom-to-top ("repraH"/"lliH" -> "Hill Harper"); values are
right-aligned, so each value is assigned to the nearest header x-center.

Usage:
    .venv/bin/python src/lenawee_sovc_parser.py <source.pdf> \
        --out 2024/counties/20240806__mi__primary__lenawee__precinct.csv
"""
import argparse
import csv
import re
import sys

import pdfplumber

TITLE = re.compile(r'^(.*?)\s*(?:\((DEM|REP)\)\s*)?\(Vote for \d+\)$')
WRITEINS = ('Unresolved Write-In', 'Write-In Unresolved')
METHODS = {'Election Day': 'election_day',
           'AV Counting Boards': 'absentee',
           'Early Voting': 'early_voting',
           'Total': 'total'}
ROW = re.compile(r'^(Election Day|AV Counting Boards|Early Voting|Total)\s*(.*)$')
FOOTER = re.compile(r'^(Lenawee County Michigan|Cumulative|County - Total)')
DISTRICT = re.compile(r'^(Representative in Congress|'
                      r'Representative in State Legislature) '
                      r'(\d+)(?:st|nd|rd|th) District$')
# Header names of the per-precinct turnout table ("Times Cast" and
# "Registered Voters" columns); every other header belongs to a results
# table. Results tables sometimes print without a turnout table beside them
# (spilled columns), starting near x=0, so the table regions are derived per
# page from where each kind's headers sit rather than fixed.
TC_NAMES = ('Times Cast', 'Voters Registered', 'Registered Voters')


def header_columns(page, x_min, x_max):
    """{x_right: name} from a page's rotated headers within an x range.

    Rotated header words and their column's values both right-align, but the
    rotated word's right edge wobbles by a few points ("devlosernU"/"nI-etirW"
    differ by ~11), so cluster on x1 with a matching tolerance.
    """
    words = [w for w in page.extract_words()
             if not w.get('upright', True)
             and x_min <= w['x0'] < x_max]
    cols = {}
    for w in words:
        key = min(cols, key=lambda k: abs(k - w['x1'])) if cols else None
        if key is not None and abs(key - w['x1']) < 12:
            cols[key].append(w)
        else:
            cols[w['x1']] = [w]
    out = {}
    for x1, group in cols.items():
        # Re-key on the mean right edge (the wobble averages out).
        xc = sum(w['x1'] for w in group) / len(group)
        # Reversed words stacked bottom-to-top read in reverse vertical order.
        group.sort(key=lambda w: -w['top'])
        out[xc] = ' '.join(w['text'][::-1] for w in group)
    return out


def nearest(columns, xc):
    key = min(columns, key=lambda k: abs(k - xc))
    # Values right-align to their column but rotated headers end short of the
    # value column (up to ~15pt); columns sit 70+ points apart.
    return key if abs(key - xc) < 22 else None


def region_lines(page, x_min, x_max):
    """Visual lines of one table region, each a list of words."""
    words = [w for w in page.extract_words()
             if w.get('upright', True) and x_min <= w['x0'] < x_max
             and w['top'] > 100]  # below the title/notes band
    lines = {}
    for w in words:
        lines.setdefault(round(w['top'] / 3), []).append(w)
    return [sorted(lines[k], key=lambda w: w['x0']) for k in sorted(lines)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    problems = []
    out_rows = []

    contest = None      # active contest accumulator

    def close_contest():
        if contest is None:
            return
        # Cross-checks against the tables' own County - Total footers.
        for col, total in contest['county_totals'].items():
            if col in ('Times Cast', 'Registered Voters'):
                continue
            got, ok = 0, True
            for p, cols in contest['results'].items():
                v = cols.get(col, {}).get('total')
                if v is None:
                    ok = False
                    break
                got += v
            if ok and got != total:
                problems.append(f'{contest["office"]} / {col}: precinct sum '
                                f'{got} != County - Total {total}')
        for col in ('Times Cast', 'Registered Voters'):
            total = contest['county_totals'].get(col)
            if total is None:
                continue
            got, ok = 0, True
            for p, tc in contest['tc'].items():
                v = tc.get('total', [None, None])[0 if col == 'Times Cast'
                                                   else 1]
                if v is None:
                    ok = False
                    break
                got += v
            if ok and got != total:
                problems.append(f'{contest["office"]} / {col}: precinct sum '
                                f'{got} != County - Total {total}')
        for p, cols in contest['results'].items():
            missing = [k for k, v in cols.items() if 'total' not in v]
            if missing:
                problems.append(f'{contest["office"]} / {p}: columns missing '
                                f'Total row: {missing}')
            cand = sum(v.get('total', 0) for k, v in cols.items()
                       if k not in ('Total Votes',) + WRITEINS)
            tv = cols.get('Total Votes', {}).get('total')
            if tv is not None and cand != tv:
                problems.append(f'{contest["office"]} / {p}: candidates '
                                f'{cand} != Total Votes {tv}')
            tc = contest['tc'].get(p, {}).get('total', [None])[0]
            if (tc is not None and tv is not None and tv > tc
                    and contest['vote_for'] == 1):
                problems.append(f'{contest["office"]} / {p}: Total Votes '
                                f'{tv} > Times Cast {tc}')
        # Emit rows, in the source's precinct order.
        for p in contest['precincts']:
            tc = contest['tc'].get(p, {})
            total = tc.get('total', [None])[0]
            if total:
                vals = [tc.get(m, [None])[0]
                        for m in ('election_day', 'absentee', 'early_voting')]
                out_rows.append([contest['name'], p, contest['office'],
                                 contest['district'], contest['party'],
                                 'Ballots Cast', total]
                                + ['' if v is None else v for v in vals])
            for col, methods in contest['results'].get(p, {}).items():
                if col == 'Total Votes':
                    continue
                name = 'Write-In' if col in WRITEINS else col
                votes = methods.get('total')
                if not votes:
                    continue  # zero or suppressed totals are not emitted
                vals = [methods.get(m) for m in
                        ('election_day', 'absentee', 'early_voting')]
                out_rows.append([contest['name'], p, contest['office'],
                                 contest['district'], contest['party'], name,
                                 votes]
                                + ['' if v is None else v for v in vals])

    with pdfplumber.open(args.pdf) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            # Title zone: the lines above the table headers. Titles can wrap
            # ("II. Whiteford ... Bond Proposal" / "(Vote for 1)"), so join
            # the non-noise lines and match at the end.
            head_words = [w for w in page.extract_words()
                          if w.get('upright', True) and w['top'] < 100]
            hlines = {}
            for w in head_words:
                hlines.setdefault(round(w['top'] / 3), []).append(w)
            head_lines = []
            for k in sorted(hlines):
                line = ' '.join(w['text'] for w in
                                sorted(hlines[k], key=lambda w: w['x0'])).strip()
                if (re.match(r'^Page:', line) or line in ('DEM', 'REP')
                        or 'Insufficient Turnout' in line
                        or line.endswith(('Voter Privacy', 'Privacy'))
                        or re.match(r'^\d/ ?\d+/\d+ ?\d+', line)):
                    continue
                head_lines.append(line)
            title = None
            m = re.search(r'(.{3,}?)\s*(?:\((DEM|REP)\)\s*)?'
                          r'\(Vote for (\d+)\)$', ' '.join(head_lines))
            if m:
                title = m
            if title:
                close_contest()
                office = title.group(1).strip()
                office = re.sub(r'\s+Precinct Delegate$',
                                ' Delegate to County Convention', office)
                district = ''
                dm = DISTRICT.match(office)
                if dm:
                    office = ('U.S. House' if dm.group(1).endswith('Congress')
                              else 'State House')
                    district = dm.group(2)
                elif office == 'United States Senator':
                    office = 'U.S. Senate'
                contest = {'name': 'Lenawee', 'office': office,
                           'district': district, 'party': title.group(2) or '',
                           'vote_for': int(title.group(3)),
                           'tc': {}, 'results': {}, 'precincts': [],
                           'county_totals': {}, 'cur': {}}
            if contest is None:
                continue  # front matter

            # Split the page's tables: turnout ("Times Cast") columns vs
            # results columns. When both kinds are present the boundary is
            # midway between them; a results table printed without a turnout
            # table gets the whole page.
            all_cols = header_columns(page, 0, 1000)
            tc_cols = {k: n for k, n in all_cols.items() if n in TC_NAMES}
            res_cols = {k: n for k, n in all_cols.items() if n not in TC_NAMES}
            regions = []
            if tc_cols and res_cols:
                boundary = (max(tc_cols) + min(res_cols)) / 2
                regions = [('tc', 0, boundary), ('results', boundary, 1000)]
            elif tc_cols:
                regions = [('tc', 0, 1000)]
            elif res_cols:
                regions = [('results', 0, 1000)]
            for kind, x_min, x_max in regions:
                cols = header_columns(page, x_min, x_max)
                if not cols:
                    continue
                is_tc = kind == 'tc'
                table_cols = {k: ('Times Cast' if n == 'Times Cast'
                                  else 'Registered Voters')
                              if is_tc else n
                              for k, n in cols.items()}
                # Data rows; the current precinct persists across rows
                # (and pages) until the next label fragment.
                label = []
                side = kind
                skip_rest = False  # after the footer, only County - Total
                for row in region_lines(page, x_min, x_max):
                    text_row = ' '.join(w['text'] for w in row).strip()
                    m = ROW.match(text_row)
                    num_words = [w for w in row
                                 if w['text'] == '****'
                                 or w['text'].replace(',', '').isdigit()]
                    label_words = [w for w in row if w not in num_words]
                    label_text = ' '.join(w['text'] for w in label_words)
                    if text_row == 'Total' or FOOTER.match(text_row):
                        if text_row.startswith('County - Total'):
                            for w in num_words:
                                xc = (w['x0'] + w['x1']) / 2
                                key = nearest(table_cols, xc)
                                if key is None:
                                    problems.append(f'page {pno}: County - '
                                                    f'Total value {w["text"]} '
                                                    f'matches no column')
                                    continue
                                val = None if w['text'] == '****' else \
                                    int(w['text'].replace(',', ''))
                                contest['county_totals'][table_cols[key]] = val
                        elif text_row.startswith('Lenawee County Michigan -') \
                                or text_row == 'Cumulative':
                            # End of the chunk's real data; everything after
                            # is the Cumulative block (except County - Total).
                            skip_rest = True
                        label = []
                        continue
                    if skip_rest:
                        continue
                    if not m:
                        label.append(text_row)
                        continue
                    method = METHODS[m.group(1)]
                    lm = ROW.match(label_text) if label_text else None
                    if lm and not lm.group(2).strip():
                        label_text = ''  # bare method keyword, not a label
                    if label or label_text:
                        label_text = ' '.join(label + [label_text]).strip()
                        label = []
                    precinct = label_text
                    if not precinct:
                        precinct = contest['cur'].get(side)
                        if not precinct:
                            problems.append(f'page {pno}: row {text_row!r} '
                                            f'has no precinct label')
                            continue
                    contest['cur'][side] = precinct
                    if precinct not in contest['precincts']:
                        contest['precincts'].append(precinct)
                    if num_words and all(w['text'] == '****'
                                         for w in num_words):
                        # Suppressed rows are suppressed in full; every
                        # column's value for this method is unknown.
                        for col in set(table_cols.values()):
                            if is_tc:
                                contest['tc'].setdefault(precinct, {}) \
                                    .setdefault(method, [None, None])
                            else:
                                contest['results'].setdefault(precinct, {}) \
                                    .setdefault(col, {}) \
                                    .setdefault(method, None)
                        continue
                    for w in num_words:
                        xc = (w['x0'] + w['x1']) / 2
                        key = nearest(table_cols, xc)
                        if key is None:
                            problems.append(f'page {pno} {precinct!r}: value '
                                            f'{w["text"]} at x={xc:.0f} '
                                            f'matches no column')
                            continue
                        col = table_cols[key]
                        val = None if w['text'] == '****' \
                            else int(w['text'].replace(',', ''))
                        if is_tc:
                            methods = contest['tc'].setdefault(precinct, {}) \
                                .setdefault(method, [None, None])
                            methods[0 if col == 'Times Cast' else 1] = val
                        else:
                            contest['results'].setdefault(precinct, {}) \
                                .setdefault(col, {})[method] = val
                if label:
                    problems.append(f'page {pno}: trailing label '
                                    f'{" ".join(label)!r}')

    close_contest()

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes', 'election_day', 'absentee',
                    'early_voting'])
        w.writerows(out_rows)
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()