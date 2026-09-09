"""Parse Wayne County's Aug 2022 primary "Election Precinct Report" PDFs into a
per-county precinct CSV, verified against the certified county-level CENR.

Sources (openelections-sources-mi/2022/primary/Wayne County Aug 2022 Primary
Precinct Reports/): Governor / State Senate / State Representative 'Election
Precinct Report' PDFs. Each PDF binds a turnout report and the candidate
results report side by side (2-up) or full width; every page is parsed the
same way. Rotated 90-degree column headers (char text read bottom-to-top per
x0 cluster; adjacent clusters within 30pt form one column) name the page's
columns: '<name> (DEM|REP)', 'Write-in' and 'Total Votes' — 'Times Cast' and
'Registered Voters' clusters belong to the left-hand turnout report and are
skipped. Data lines repeat the precinct label ('City of Detroit, Precinct
290', possibly wrapped onto a second line) then three method rows per
precinct: Election Day / AV Counting Board / Total, with one number per
column. 'Wayne County - Total' and 'Wayne - Total' rows close each page-run.

A run is a maximal span of pages with the same column set. Each contest's
results come from up to three interleaved page-streams — a 2-up report for
the top-2 candidates, a full-width report for the remaining candidates with
Write-in and Total Votes columns, and (some contests) a write-in-only
report — all covering the same precinct list, so runs are assigned to
contests by matching their candidate names against the CENR's Wayne rows
(party from the header tag); write-in-only runs attach to the current
contest. Each write-in-carrying report prints the total of its own write-in
column, so the merged write-in sum is checked against the sum of the
distinct printed totals. Only the Total method row is emitted, one row per
candidate plus a lumped 'Write-In' row.

The source set has no U.S. House report (CENR districts 6/12/13) — those
contests are a known source gap. The printed names differ from the CENR for
'Leonard C. Scott, Jr.' (CENR 'Leonard C. Scott Jr.'). The reports are the
county's 8/3/2022 UNOFFICIAL results; parsed sums match each report's own
printed county totals, and the small unofficial-vs-certified residuals
against the CENR are allowlisted in ACCEPTED_RESIDUALS.

Usage: .venv/bin/python src/primary_2022_wayne.py [--apply]
"""
import collections
import re
import sys

import pdfplumber

from primary_2022_common import CENR, one_space, verify, write

COUNTY = 'Wayne'
BASE = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
        "Wayne County Aug 2022 Primary Precinct Reports/")
PDFS = ['Governor - Election Precinct Report.pdf',
        'State Senate - Election Precinct Report.pdf',
        'State Representative - Election Precinct Report.pdf']

CAND_FIX = {'Leonard C. Scott, Jr.': 'Leonard C. Scott Jr.',
            'Marvin Cotton, Jr.': 'Marvin Cotton Jr',
            'Glenn R. Morrison, Jr.': 'Glenn R. Morrison Jr',
            'Tullio Liberati, Jr.': 'Tullio Liberati Jr.',
            'Donovan McKinney': 'Donavan McKinney'}

# (candidate, header party tag) -> (office, district, party)
BY_CAND = {}
for (office, district, party, cand), _votes in CENR[COUNTY].items():
    cand = CAND_FIX.get(cand, cand)
    BY_CAND[(cand, party)] = (office, district, party)

TURNOUT_HEADERS = {'Times Cast', 'Registered Voters'}
METHODS = ('Election Day', 'AV Counting Board', 'Total')

# The PDFs are the county's 8/3/2022 UNOFFICIAL reports; every parsed sum
# matches its own report's printed 'Wayne County - Total' row, but small
# unofficial-vs-certified residuals remain against the CENR (largest: SD5
# Overman -260, HD26 Chisholm -211, SD4 Camilleri -97, Governor Whitmer
# -373). Kept source-faithful; (contest key) -> (parsed, CENR).
ACCEPTED_RESIDUALS = {
    ('Governor', '', 'DEM', 'Gretchen Whitmer'): (189769, 190142),
    ('Governor', '', 'REP', 'Garrett Soldano'): (8769, 8762),
    ('Governor', '', 'REP', 'Kevin Rinke'): (18025, 18024),
    ('Governor', '', 'REP', 'Ralph Rebandt'): (3801, 3796),
    ('Governor', '', 'REP', 'Ryan D. Kelley'): (11183, 11179),
    ('Governor', '', 'REP', 'Tudor M. Dixon'): (40516, 40519),
    ('State House', '1', 'DEM', 'Tyrone Carter'): (4291, 4290),
    ('State House', '1', 'REP', 'Paula M. Campbell'): (401, 402),
    ('State House', '10', 'DEM', 'Toni Mua'): (2459, 2461),
    ('State House', '10', 'REP', 'Mark Corcoran'): (4797, 4795),
    ('State House', '13', 'DEM', 'Myles W. Miller'): (864, 865),
    ('State House', '14', 'DEM', 'Donavan McKinney'): (2091, 2092),
    ('State House', '16', 'DEM', 'Stephanie A. Young'): (11073, 11075),
    ('State House', '2', 'DEM', 'Tullio Liberati Jr.'): (7046, 7065),
    ('State House', '2', 'REP', "Michael Joseph D'Onofrio"): (2280, 2281),
    ('State House', '22', 'DEM', 'Matt Koleszar'): (12190, 12198),
    ('State House', '22', 'REP', 'Cathryn Neracher'): (9800, 9805),
    ('State House', '23', 'REP', 'Richard L. Sharland'): (1501, 1502),
    ('State House', '26', 'DEM', 'Allen Wilson'): (1585, 1626),
    ('State House', '26', 'DEM', 'Dylan Wegela'): (3582, 3614),
    ('State House', '26', 'DEM', 'Stephen Patterson'): (771, 800),
    ('State House', '26', 'DEM', 'Steven Chisholm'): (2337, 2548),
    ('State House', '26', 'REP', 'James C. Townsend'): (3281, 3291),
    ('State House', '27', 'DEM', 'Jaime Churches'): (9412, 9410),
    ('State House', '28', 'DEM', 'Robert Kull'): (6278, 6277),
    ('State House', '28', 'REP', 'Jamie Thompson'): (3333, 3334),
    ('State House', '29', 'REP', 'James DeSana'): (3151, 3149),
    ('State House', '4', 'DEM', 'Gus H. Tarraf'): (1255, 1254),
    ('State House', '4', 'DEM', 'Karen Whitsett'): (3863, 3857),
    ('State House', '4', 'DEM', 'Lori Lynn Turner'): (1883, 1879),
    ('State House', '5', 'DEM', 'Reggie Reg Davis'): (2930, 2928),
    ('State House', '6', 'DEM', 'Myya Jones'): (1390, 1391),
    ('State House', '7', 'DEM', 'Helena Scott'): (4749, 4748),
    ('State House', '8', 'DEM', 'Durrel K. Douglas'): (1483, 1484),
    ('State House', '8', 'DEM', 'Mike McFall'): (1464, 1466),
    ('State House', '9', 'DEM', 'Abraham Aiyash'): (4348, 4349),
    ('State House', '9', 'DEM', 'Abraham Shaw'): (621, 622),
    ('State Senate', '1', 'DEM', 'Brenda K. Sanders'): (4903, 4912),
    ('State Senate', '1', 'DEM', 'Carl J. Schwartz'): (775, 774),
    ('State Senate', '1', 'DEM', 'Erika Geiss'): (6811, 6824),
    ('State Senate', '1', 'DEM', 'Frank Liberati'): (4837, 4842),
    ('State Senate', '1', 'DEM', 'Ricardo R. Moore'): (1672, 1673),
    ('State Senate', '1', 'DEM', 'Shellee M. Brooks'): (2088, 2089),
    ('State Senate', '10', 'DEM', 'Paul Wojno'): (7145, 7149),
    ('State Senate', '13', 'DEM', 'Rosemary K. Bayer'): (8859, 8866),
    ('State Senate', '13', 'REP', 'Brian Williams'): (2643, 2645),
    ('State Senate', '13', 'REP', 'Jason Rhines'): (5754, 5756),
    ('State Senate', '2', 'DEM', 'Maurice Sanders'): (3600, 3599),
    ('State Senate', '2', 'DEM', 'Sylvia Santana'): (15022, 15020),
    ('State Senate', '3', 'DEM', 'Stephanie Chang'): (13963, 13968),
    ('State Senate', '3', 'DEM', 'Toinu Reeves'): (3388, 3390),
    ('State Senate', '4', 'DEM', 'Darrin Camilleri'): (26119, 26216),
    ('State Senate', '4', 'REP', 'Beth Socia'): (3638, 3639),
    ('State Senate', '5', 'DEM', 'Dayna Polehanki'): (19762, 19822),
    ('State Senate', '5', 'DEM', 'Velma Jean Overman'): (6694, 6954),
    ('State Senate', '5', 'REP', 'Emily Bauman'): (6906, 6909),
    ('State Senate', '5', 'REP', 'Jody M. Rice-White'): (4110, 4106),
    ('State Senate', '5', 'REP', 'Leonard C. Scott Jr.'): (5444, 5442),
    ('State Senate', '6', 'DEM', 'Vicki Barnett'): (6759, 6754),
    ('State Senate', '8', 'DEM', 'Mallory McMorrow'): (3991, 3990),
    ('State Senate', '8', 'DEM', 'Marshall Bullock II'): (9255, 9256),
}


def rotated_columns(page, problems, page_no):
    """Results-report columns as [(kind, value)] left to right; kind is
    'cand' (value = (name, party)), 'writein' or 'total'."""
    clusters = collections.defaultdict(list)
    for c in page.chars:
        if not c['upright']:
            clusters[round(c['x0'])].append(c)
    groups, cur = [], []
    for x0 in sorted(clusters):
        if cur and x0 - cur[-1] > 30:
            groups.append(cur)
            cur = []
        cur.append(x0)
    if cur:
        groups.append(cur)
    cols = []
    for g in groups:
        text = one_space(' '.join(
            ''.join(c['text'] for c in sorted(clusters[x0], key=lambda c: -c['top']))
            for x0 in g))
        if text in TURNOUT_HEADERS:
            continue
        m = re.match(r'^(.*?)\s*\((DEM|REP)\)\s*$', text)
        if m:
            name = CAND_FIX.get(m.group(1), m.group(1))
            cols.append(('cand', (name, m.group(2))))
        elif re.fullmatch(r'Write-?in', text, re.I):
            cols.append(('writein', None))
        elif text == 'Total Votes':
            cols.append(('total', None))
        else:
            problems.append(f'page {page_no}: unparsed header {text!r}')
    return cols


def label_x0(page):
    """x0 where the results report's label zone starts (the right-most
    'Precinct' column header on the page). Section-start pages print the
    contest title first, pushing the header row down to ~132."""
    for lo, hi in ((88, 100), (120, 145)):
        xs = [w['x0'] for w in page.extract_words(x_tolerance=1.5)
              if w['upright'] and w['text'] == 'Precinct' and lo <= w['top'] <= hi]
        if xs:
            return max(xs)
    return None


def parse_pdf(path, problems):
    """[(contest_key, precinct, candidate_or_Write-In, votes)]; the printed
    county totals are used to cross-check the accumulated sums."""
    rows = []
    contest = None          # current contest key
    run_cols = None         # current run's column kinds
    run_id = 0
    run_total = None        # current run's printed county totals
    totals_seen = []        # every run's printed totals, checked at close
    cells = {}              # (precinct, run_id) -> {'cand': {name: v}, ...}
    pending_label = None    # label awaiting its method rows
    last_label = None       # last completed label, for boundary recovery

    def close_run():
        nonlocal run_cols, run_id, cells, run_total
        if contest is None:
            problems.append(f'{path}: run with no contest (columns '
                            f'{run_cols})')
        run_cols, run_id = None, run_id + 1
        cells = {}
        run_total = None

    def close_contest():
        nonlocal contest
        if contest is not None:
            sums = collections.defaultdict(int)
            for precinct, cands in contest['precincts'].items():
                for name, votes in sorted(cands.items()):
                    if votes:
                        rows.append((contest['key'], precinct, name, votes))
                        sums[name] += votes
            # each write-in-carrying report prints the total of its own
            # write-in column (a contest's write-ins can be split across a
            # candidate report and a write-in-only report), so the merged
            # sum must equal the sum of the distinct printed totals
            printed_wi = {t['Write-In'] for t in totals_seen
                          if 'Write-In' in t}
            if printed_wi and sum(printed_wi) != sums.get('Write-In', 0):
                problems.append(f'{contest["key"]}: write-in total '
                                f'{sums.get("Write-In", 0)} != printed '
                                f'{sorted(printed_wi)}')
            for printed in totals_seen:
                for name, want in printed.items():
                    # Write-In is checked per run (it may span several)
                    if want and name != 'Write-In' \
                            and sums.get(name, 0) != want:
                        problems.append(f'{contest["key"]}: county total '
                                        f'{name} {sums.get(name, 0)} != '
                                        f'printed {want}')
            contest = None

    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            cols = rotated_columns(page, problems, page_no)
            if not cols:
                continue
            x_off = label_x0(page)
            if x_off is None:
                problems.append(f'page {page_no}: results columns but no '
                                f'Precinct header')
                continue

            sig = tuple(cols)
            if sig != run_cols:
                if run_cols is not None:
                    close_run()
                run_cols = sig
                run_total = None
                cands = [v for kind, v in cols if kind == 'cand']
                if cands:
                    keys = {BY_CAND.get(c) for c in cands}
                    if None in keys or len(keys) != 1:
                        problems.append(f'page {page_no}: candidates {cands} '
                                        f'do not map to one CENR contest')
                        key = None if None in keys else keys.pop()
                    else:
                        key = keys.pop()
                else:
                    key = None
                if key is None:
                    if contest is None:
                        problems.append(f'page {page_no}: write-in-only run '
                                        f'with no current contest')
                elif contest is None or contest['key'] != key:
                    close_contest()
                    contest = {'key': key, 'precincts': {}}
                    totals_seen.clear()

            def line_words():
                lines = collections.defaultdict(list)
                for w in page.extract_words(x_tolerance=1.5):
                    if w['upright'] and w['x0'] >= x_off - 30 and w['top'] >= 40:
                        lines[round(w['top'])].append(w)
                return [(top, sorted(ws, key=lambda w: w['x0']))
                        for top, ws in sorted(lines.items())]

            for top, ws in line_words():
                text = ' '.join(w['text'] for w in ws)
                if text == 'Precinct' or top < 60:
                    continue  # header row / page header + contest title zone
                if text in ('Wayne', 'Wayne County'):
                    continue  # county stub row above the precinct list
                meth = next((m for m in METHODS if text.startswith(m + ' ')
                             or text == m), None)
                nums = [w for w in ws
                        if re.fullmatch(r'\d[\d,]*', w['text'])]
                if text.startswith(('Wayne County - Total', 'Wayne - Total')):
                    values = [int(w['text'].replace(',', '')) for w in nums]
                    if len(values) != len(cols):
                        problems.append(f'page {page_no}: summary row has '
                                        f'{len(values)} values for '
                                        f'{len(cols)} columns')
                    else:
                        total = {}
                        for (kind, val), v in zip(cols, values):
                            if kind == 'cand':
                                total[val[0]] = v
                            elif kind == 'writein' and v:
                                total['Write-In'] = v
                        if run_total and run_total != total:
                            problems.append(f'page {page_no}: conflicting '
                                            f'county totals '
                                            f'{run_total} vs {total}')
                        if not run_total:
                            totals_seen.append(total)
                        run_total = total
                    continue
                if meth:
                    if meth != 'Total':
                        continue  # only the Total row is emitted
                    if pending_label is None:
                        # a precinct's rows can straddle a page/run boundary
                        # without repeating the label; reuse the last one
                        if last_label is None:
                            problems.append(f'page {page_no}: {meth} row with '
                                            f'no precinct label')
                            continue
                        pending_label = last_label
                    label = pending_label
                    pending_label = None
                    last_label = label
                    values = [int(w['text'].replace(',', '')) for w in nums]
                    if len(values) != len(cols):
                        problems.append(f'page {page_no} {label!r}: {meth} '
                                        f'row has {len(values)} values for '
                                        f'{len(cols)} columns')
                        continue
                    key = contest['key'] if contest else None
                    if key is None:
                        problems.append(f'page {page_no}: {label!r} Total row '
                                        f'with no current contest')
                        continue
                    if (label, run_id) in cells:
                        problems.append(f'page {page_no}: duplicate Total '
                                        f'row for {label!r}')
                        continue
                    cells[(label, run_id)] = True
                    store = contest['precincts'].setdefault(label, {})
                    for (kind, val), v in zip(cols, values):
                        if kind == 'cand':
                            name, party = val
                            if party != key[2]:
                                problems.append(f'page {page_no}: candidate '
                                                f'{name!r} party {party} != '
                                                f'contest {key[2]}')
                            if name in store and store[name] != v:
                                problems.append(f'{key} / {label!r}: '
                                                f'conflicting total for '
                                                f'{name}: {store[name]} vs {v}')
                            store[name] = v
                        elif kind == 'writein' and v:
                            if 'Write-In' in store and store['Write-In'] != v:
                                problems.append(f'{key} / {label!r}: '
                                                f'conflicting write-in total')
                            store['Write-In'] = v
                        # the 'Total Votes' column is the whole-contest total
                        # across all candidate runs, not this run's columns
                elif meth is None:
                    # precinct label assembly (labels wrap before or after
                    # ', Precinct N', and may carry 'District N' segments)
                    pending_label = text if pending_label is None \
                        else pending_label + ' ' + text
        close_run()
        close_contest()
    return rows


def parse():
    all_rows = []
    write_ins = collections.defaultdict(int)
    for fname in PDFS:
        problems = []
        rows = parse_pdf(BASE + fname, problems)
        for p in problems:
            print(f'PROBLEM ({fname}):', p, file=sys.stderr)
        for contest, precinct, cand, votes in rows:
            all_rows.append((precinct, contest[0], contest[1], contest[2],
                             cand, votes))
            if cand == 'Write-In':
                write_ins[contest] += votes
        print(f'{fname}: {len(rows)} Total rows')
    return all_rows, write_ins


def main(apply=False):
    rows, write_ins = parse()
    print(f'Wayne: {len(rows)} rows')
    problems = []
    for p in verify(COUNTY, rows, write_ins):
        residual = next((k for k, (got, _want) in ACCEPTED_RESIDUALS.items()
                         if str(k) in p and f'parsed {got} !=' in p), None)
        if residual:
            print(f'ACCEPTED residual (unofficial report; source-faithful): '
                  f'{p}')
        else:
            problems.append(p)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')
    if apply:
        write(COUNTY, rows)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)