"""Parse Wayne County's "Official Precinct Canvass" contest-major PDF (2026 primary).

Layout (Wayne County Aug 2026 Primary Official Precinct by Precinct.pdf):
each contest spans whole pages, the title ('1 Governor (DEM)') repeats at
top~76 of every page including a final summary page. Rotated 90-degree column
headers (read bottom-to-top per x0 cluster) sit at top~128-180: three aux
columns (Registered Voters / Voters Cast / Turnout (%)), then one cluster
group per candidate plus an optional 'Write-ins' column. A candidate's name is
split across adjacent x0 clusters spaced 8pt apart (party fragment + name
fragments); clusters of one name have x0 gaps <= 8, names are separated by
gaps >= 13. Read right-to-left, each name reads 'DEM - <name>' / 'REP - <name>'.

Data rows (top 165-752): every method row repeats the full precinct label
(long labels wrap to a label-only line ~3pt below the numbers line, merged
back into the pending row before it commits; a wrap can straddle a page
break). Five method rows per precinct: Early Voting / Election Day / Absentee
/ Pre-Process (whose 'Absentee' half prints on its own line below) / Total.
Columns right-align: Registered Voters at x1~214 (always 0), Voters Cast at
x1~250, candidate columns at header cluster x0 + 8, trailing-zero cells
omitted. The contest's final page prints countywide summary rows ('Total -
Early Voting', ..., 'Contest Total') in the label zone; they are kept as
verification targets, not emitted.

One contest's candidate table may be SPLIT across two consecutive page-runs
under the same title (State Senate 3rd District DEM: pages 605-623 carry ten
candidate columns, pages 624-642 carry the eleventh candidate + Write-ins).
Every precinct's method rows print in BOTH runs, each run showing only its own
columns (both runs' summary pages re-print the same Times Cast). The parser
merges such runs: the contest's candidate list is the ordered union across
runs, cells are keyed by candidate name, and the per-page header run maps
printed columns to names. A run whose header set was already seen is flagged
as a repeated run rather than merged.

Usage:
    .venv/bin/python src/wayne.py <source.pdf> \
        --out 2026/counties/20260804__mi__primary__wayne__precinct.csv
"""
import argparse
import collections
import csv
import re
import sys

import pdfplumber

NUM = re.compile(r'^\d[\d,]*$')
PARTY_NAME = re.compile(r'^(DEM|REP) - (.+)$')
METHODS = ('Early Voting', 'Election Day', 'Absentee', 'Pre-Process')
INCOMPLETE_LABEL = re.compile(
    r'(\bof$|\bthe$|\ba$|\band$|,$|\bPrecinct$|\bTownship$|\bCity$'
    r'|\bVillage$|\bCouncil$|\bMunicipal$)')


def page_lines(page):
    """Upright words grouped into visual lines (round(top)), top to bottom."""
    words = [w for w in page.extract_words(x_tolerance=1.5) if w['upright']]
    lines = collections.defaultdict(list)
    for w in words:
        lines[round(w['top'])].append(w)
    return [(top, sorted(ws, key=lambda w: w['x0']))
            for top, ws in sorted(lines.items())]


def page_title(page):
    words = [w for w in page.extract_words(x_tolerance=1)
             if w['upright'] and 60 < w['top'] < 100]
    return re.sub(r'^1 ', '', ' '.join(w['text'] for w in words))


def header_columns(page):
    """Rotated header zone -> (x_right, text) cluster groups, candidate/write-in
    columns only (x_right >= 290)."""
    chars = [c for c in page.chars if not c['upright'] and 100 < c['top'] < 184]
    clusters = collections.defaultdict(list)
    for c in chars:
        clusters[round(c['x0'])].append(c)
    ordered = sorted((x0, ''.join(c['text'] for c in sorted(cs, key=lambda c: c['top'])))
                     for x0, cs in clusters.items())
    groups, cur = [], [ordered[0]]
    for cl in ordered[1:]:
        if cl[0] - cur[-1][0] > 10:
            groups.append(cur)
            cur = [cl]
        else:
            cur.append(cl)
    groups.append(cur)
    out = []
    for g in groups:
        text = re.sub(r'\s+', ' ', ''.join(t for _, t in sorted(g, key=lambda c: -c[0])))
        x_right = max(x for x, _ in g)
        if x_right >= 290:
            out.append((x_right, text.strip()))
    return out


def parse_title(title, problems):
    m = re.match(r'^(.+?) \((DEM|REP)\)$', title)
    if not m:
        problems.append(f'unparsed title {title!r}')
        return title, '', ''
    name, party = m.group(1), m.group(2)
    dist = re.search(r' (\d+)(?:st|nd|rd|th) District$', name)
    if name == 'Governor':
        return 'Governor', '', party
    if name == 'United States Senator':
        return 'U.S. Senate', '', party
    if dist and name.startswith('Representative in Congress'):
        return 'U.S. House', dist.group(1), party
    if dist and name.startswith('State Senator'):
        return 'State Senate', dist.group(1), party
    if dist and name.startswith('Representative in State Legislature'):
        return 'State House', dist.group(1), party
    problems.append(f'unmapped office {name!r}')
    return name, '', party


class Contest:
    def __init__(self, title, problems):
        self.title = title
        self.problems = problems
        self.office, self.district, self.party = parse_title(title, problems)
        self.candidates = []   # ordered union across header runs
        self.cand_set = set()
        self.has_writein = False
        self.headers = None    # current header run's (candidate tuple, has_wi)
        self.runs = set()      # header tuples already seen
        self.run_seq = 0       # 1-based id of the current header run
        self.rows = {}         # precinct -> {method -> {cast, cells, runs}}
        self.order = []        # precinct first-seen order
        self.expected = {}     # 'TOTAL'/method -> list of {cast, cells, run}

    def col_names(self):
        return self.candidates + (['Write-In'] if self.has_writein else [])

    def set_headers(self, cols, page_no):
        """Parse this page's candidate columns; return the page's column map
        (printed column position -> global candidate name / 'Write-In')."""
        cands, has_wi = [], False
        for x_right, text in cols:
            m = PARTY_NAME.match(text)
            if m:
                if m.group(1) != self.party:
                    self.problems.append(f'{self.title}: header party '
                                         f'{m.group(1)} != contest {self.party}')
                cands.append(m.group(2).strip())
            elif text == 'Write-ins':
                has_wi = True
            else:
                self.problems.append(f'{self.title}: unparsed header {text!r} '
                                     f'(page {page_no})')
        key = (tuple(cands), has_wi)
        if self.run_seq == 0:
            self.run_seq = 1
        elif self.headers != key:
            if key in self.runs:
                self.problems.append(f'{self.title}: header run repeats on '
                                     f'page {page_no}')
            self.run_seq += 1
        if self.headers != key:
            self.runs.add(key)
            self.headers = key
            for name in cands:
                if name in self.cand_set:
                    self.problems.append(
                        f'{self.title}: candidate {name!r} repeats in a later '
                        f'header run (page {page_no})')
                else:
                    self.cand_set.add(name)
                    self.candidates.append(name)
            if has_wi:
                if self.has_writein:
                    self.problems.append(f'{self.title}: Write-ins column '
                                         f'repeats (page {page_no})')
                self.has_writein = True
        return cands + (['Write-In'] if has_wi else [])

    def add_row(self, precinct, method, cast, cells, run):
        """Merge a method row; cells is {candidate name -> value}. Rows from a
        later header run extend the same precinct's cells."""
        rows = self.rows.setdefault(precinct, {})
        if precinct not in self.order:
            self.order.append(precinct)
        r = rows.setdefault(method, {'cast': None, 'cells': {}, 'runs': set()})
        if run in r['runs']:
            self.problems.append(f'{self.title} / {precinct}: duplicate '
                                 f'{method} row')
        r['runs'].add(run)
        if r['cast'] is None:
            r['cast'] = cast
        elif cast is not None and r['cast'] != cast:
            self.problems.append(f'{self.title} / {precinct}: conflicting '
                                 f'{method} cast {r["cast"]} vs {cast}')
        for name, v in cells.items():
            if name in r['cells'] and r['cells'][name] != v:
                self.problems.append(
                    f'{self.title} / {precinct}: conflicting {method} cell '
                    f'for {name}: {r["cells"][name]} vs {v}')
            r['cells'][name] = v

    def add_summary(self, text, cells, colmap, run, where):
        if text == 'Contest Total':
            key = 'TOTAL'
        else:
            m = re.match(r'^Total - (.+)$', text)
            key = m.group(1) if m else ''
            if key == 'Pre-Process Absentee':
                key = 'Pre-Process'
            if key not in METHODS:
                self.problems.append(f'{self.title}: odd summary row {text!r} '
                                     f'({where})')
                return
        for e in self.expected.get(key, []):
            if e['run'] == run:
                self.problems.append(f'{self.title}: duplicate summary row '
                                     f'for {key} ({where})')
        self.expected.setdefault(key, []).append(
            {'cast': cells[0],
             'cells': {name: v for name, v in zip(colmap, cells[1:])
                       if v is not None},
             'run': run})

    def finish(self, out_rows):
        names = self.col_names()
        if self.rows:
            print(f'{self.title}: {len(self.order)} precincts, '
                  f'{len(self.candidates)} candidates'
                  f'{" + write-ins" if self.has_writein else ""}'
                  f'{f" in {self.run_seq} header runs" if self.run_seq > 1 else ""}')
        sums = {name: 0 for name in names}
        sums_m = {m: {name: 0 for name in names} for m in METHODS}
        cast_sums = {m: 0 for m in METHODS}
        cast_total = 0
        zero_cast = 0
        for precinct in self.order:
            rows = self.rows[precinct]
            total = rows.get('Total')
            if total is None:
                self.problems.append(f'{self.title} / {precinct}: no Total row')
                continue
            cast = total['cast'] or 0
            cast_total += cast
            if not cast:
                zero_cast += 1
                continue
            evc = (rows.get('Early Voting') or {}).get('cast') or 0
            edc = (rows.get('Election Day') or {}).get('cast') or 0
            abc = ((rows.get('Absentee') or {}).get('cast') or 0) + \
                  ((rows.get('Pre-Process') or {}).get('cast') or 0)
            if evc + edc + abc != cast:
                self.problems.append(f'{self.title} / {precinct}: Cast {evc}+'
                                     f'{edc}+{abc} != {cast}')
            for m in METHODS:
                r = rows.get(m)
                if r and r['cast'] is not None:
                    cast_sums[m] += r['cast']
            for name in names:
                tot = total['cells'].get(name, 0)
                sums[name] += tot
                ev = (rows.get('Early Voting') or {})['cells'].get(name, 0)
                ed = (rows.get('Election Day') or {})['cells'].get(name, 0)
                ab = ((rows.get('Absentee') or {})['cells'].get(name, 0)
                      + (rows.get('Pre-Process') or {})['cells'].get(name, 0))
                sums_m['Early Voting'][name] += ev
                sums_m['Election Day'][name] += ed
                sums_m['Absentee'][name] += \
                    (rows.get('Absentee') or {})['cells'].get(name, 0)
                sums_m['Pre-Process'][name] += \
                    (rows.get('Pre-Process') or {})['cells'].get(name, 0)
                if not tot:
                    continue
                if ev + ed + ab != tot:
                    self.problems.append(
                        f'{self.title} / {precinct}: {name} EV {ev}+ED {ed}'
                        f'+AB {ab} != Total {tot}')
                out_rows.append(['Wayne', precinct, self.office, self.district,
                                 self.party, name, tot, ev, ed, ab])
            out_rows.append(['Wayne', precinct, self.office, self.district,
                             self.party, 'Ballots Cast', cast, evc, edc, abc])
        if zero_cast:
            print(f'{self.title}: {zero_cast} precinct-contests with zero cast '
                  f'(not emitted)')
        self.verify(sums, sums_m, cast_sums, cast_total)

    def verify(self, sums, sums_m, cast_sums, cast_total):
        for key in ('TOTAL',) + METHODS:
            entries = self.expected.get(key)
            if not entries:
                if key == 'TOTAL' and self.rows:
                    self.problems.append(
                        f'{self.title}: no Contest Total summary row')
                continue
            label = 'Contest Total' if key == 'TOTAL' else f'Total - {key}'
            for e in entries:
                acc = cast_total if key == 'TOTAL' else cast_sums[key]
                if e['cast'] != acc:
                    self.problems.append(
                        f'{self.title}: {label} cast {e["cast"]} != '
                        f'accumulated {acc}')
                for name, v in e['cells'].items():
                    acc = sums[name] if key == 'TOTAL' else sums_m[key][name]
                    if v != acc:
                        self.problems.append(
                            f'{self.title}: {label} {name} {v} != '
                            f'accumulated {acc}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', required=True)
    ap.add_argument('--pages', help='1-based page range to parse, e.g. 1-5')
    args = ap.parse_args()

    out_rows = []
    problems = []
    contests = []
    contest = None
    pending = None   # method row awaiting possible label-wrap continuation

    def commit():
        nonlocal pending
        if pending is not None:
            if INCOMPLETE_LABEL.search(pending[1]):
                problems.append(f'{contest.title}: incomplete label '
                                f'{pending[1]!r}')
            contest.add_row(pending[1], pending[2], pending[3], pending[4],
                            pending[5])
            pending = None

    pdf = pdfplumber.open(args.pdf)
    pages = list(pdf.pages)
    if args.pages:
        a, b = (int(x) for x in args.pages.split('-'))
        pages = pages[a - 1:b]

    for page_no, page in enumerate(pages, 1):
        title = page_title(page)
        if contest is None or contest.title != title:
            commit()
            if contest is not None:
                contest.finish(out_rows)
            contest = Contest(title, problems)
            contests.append(contest)
        cols = header_columns(page)
        colmap = contest.set_headers(cols, page_no)
        col_pos = {x_right: i for i, (x_right, _) in enumerate(cols)}

        for top, ws in page_lines(page):
            if not 165 <= top < 752:
                continue
            label = ' '.join(w['text'] for w in ws if w['x1'] < 132)
            meth = ' '.join(w['text'] for w in ws if 132 <= w['x0'] < 185)
            nums = [w for w in ws if w['x0'] >= 185 and NUM.match(w['text'])]
            if meth and not nums:
                # 'Absentee' wrap under a Pre-Process row; it may share its
                # line with the row's wrapped label continuation.
                if label:
                    if pending is None:
                        problems.append(f'page {page_no}: wrap line {label!r} '
                                        f'with no pending row')
                    else:
                        pending[1] += ' ' + label
                continue
            if not meth and not nums:
                if label:
                    if pending is None:
                        problems.append(f'page {page_no}: label-only line '
                                        f'{label!r} with no pending row')
                    else:
                        pending[1] += ' ' + label
                continue
            if not meth:
                # summary row: 'Total - Early Voting', 'Contest Total'
                commit()
                cells = [int(w['text'].replace(',', '')) for w in nums]
                if cells[0] != 0:
                    problems.append(f'{title}: summary row {label!r} says '
                                    f'registered voters {cells[0]}')
                contest.add_summary(label, cells[1:], colmap, contest.run_seq,
                                    f'page {page_no}')
                continue
            # method row
            commit()
            cells = {}
            reg = cast = None
            for w in nums:
                v = int(w['text'].replace(',', ''))
                x1 = w['x1']
                if 206 <= x1 <= 222:
                    reg = v
                elif 242 <= x1 <= 258:
                    cast = v
                else:
                    hits = [col_pos[x_right] for x_right, _ in cols
                            if 4 <= x1 - x_right <= 12]
                    if not hits:
                        problems.append(f'page {page_no}: cell {v} at x1 '
                                        f'{x1:.0f} matches no column')
                    elif len(hits) > 1:
                        problems.append(f'page {page_no}: cell {v} at x1 '
                                        f'{x1:.0f} matches two columns')
                    else:
                        name = colmap[hits[0]]
                        if name in cells and cells[name] != v:
                            problems.append(
                                f'page {page_no}: two cells for {name}: '
                                f'{cells[name]} vs {v}')
                        cells[name] = v
            if reg != 0:
                problems.append(f'{title} / {label}: registered voters {reg}')
            if cast is None:
                problems.append(f'{title} / {label}: {meth} row has no '
                                f'Voters Cast cell')
            pending = [page_no, label, meth, cast, cells, contest.run_seq]
    commit()
    if contest is not None:
        contest.finish(out_rows)

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes', 'early_voting', 'election_day',
                    'absentee'])
        w.writerows(out_rows)
    print(f'Wrote {len(out_rows)} rows ({len(contests)} contests, '
          f'{sum(len(c.order) for c in contests)} precinct-contests) to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()