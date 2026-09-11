"""Parse the Montcalm Aug 2020 primary "Canvass Results Report" PDF.

Source (openelections-sources-mi/2020/primary): 'Montcalm MI Canvass
Results-8-12-2020 12-47-35 PM.pdf', 302 pages, clean text layer,
contest-major. Each contest is a table flowing over 1+ pages (one contest
starts per page; the title repeats on every page); page 302 is a closing
page. Every page repeats the header band (banner, countywide turnout, Run
Date/Time, page number) above the title. Data rows carry the per-precinct
values right-aligned under the rotated column headers: one column per
candidate, then the tail columns Cast Votes / Undervotes / Overvotes /
Invalid Votes / Election Day Voting Ballots Cast / Total Ballots Cast /
Registered Voters / Turnout Percentage (proposals print no Invalid Votes
column; contests with no qualified candidates print an unnamed leftmost
zero column). Each contest ends with a 'Totals' row on its last page and
every column's row sum must equal it. Long labels wrap around the value
row ('Douglass Township, Precinct' / values / '1').

Rotated headers store each word glyph-reversed with `upright=False`;
grouping a column's words by x0, sorting by top descending and reversing
each word gives the name ('ynohtnA' / '.D' / 'gieF' -> 'Anthony D. Feig').

Office mapping: bare Clerk/Treasurer/Supervisor/Trustee/Constable titles
are township offices prefixed with the rows' jurisdiction; the one contest
of those whose rows span jurisdictions is the county office ('County
Clerk', 'County Treasurer'). 'Delegate to County Convention' contests are
per-precinct: office '<precinct> Delegate to County Convention' (the
Clinton 2020 convention). Pseudo rows follow the Presque Isle convention:
one 'Registered Voters' and one 'Ballots Cast' row per precinct (from the
Total Ballots Cast column, checked for consistency across contests).

Usage:
    .venv/bin/python src/montcalm_2020.py [pdf-path]
"""
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import write_csv  # noqa: E402

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Montcalm MI Canvass Results-8-12-2020 12-47-35 PM.pdf')
COUNTY = 'Montcalm'

TITLE = re.compile(
    r'^(.*\S) - (Democratic|Republican|Nonpartisan) Party'
    r'(?: - Vote for not more than \d+)?$')
NUM = re.compile(r'^\d[\d,]*(?:\.\d+)?%?$')
# value cells are right-aligned under their headers; a wrapped label
# fragment never reaches x1 140 while every value column ends past 170
VALUE_X1 = 140
TAIL_NAMES = {'Cast Votes', 'Undervotes', 'Overvotes', 'Invalid Votes',
              'Election Day Voting Ballots Cast', 'Total Ballots Cast',
              'Registered Voters', 'Turnout Percentage'}
LOCAL_OFFICES = ('Clerk', 'Treasurer', 'Supervisor', 'Trustee', 'Constable')
PARTY_CODES = {'Democratic': 'DEM', 'Republican': 'REP'}


def rot_name(ws):
    """Rotated header words (same x0) -> column name."""
    ws = sorted(ws, key=lambda w: w['top'], reverse=True)
    return ' '.join(w['text'][::-1] for w in ws)


def rot_columns(rot):
    """Rotated word groups (by x0) -> ordered header names. A wrapped
    candidate name prints its continuation on a second rotated line ~10px
    to the right ('Kathy Zamarran-Brennan' / '(W)'); merge it into the main
    column with its tokens appended after the main tokens."""
    groups = sorted(rot.items())  # (rounded x0, [words])
    merged = []  # [main_words, wrap_words]
    for _, ws in groups:
        if merged and ws[0]['x0'] - merged[-1][0][0]['x0'] <= 12:
            merged[-1][1].extend(ws)
        else:
            merged.append((list(ws), []))
    names = []
    for main_ws, wrap_ws in merged:
        nm = rot_name(main_ws)
        if wrap_ws:
            nm = f'{nm} {rot_name(wrap_ws)}'
        names.append(nm)
    return names


def map_office(title):
    t = ' '.join(title.split())
    if re.match(r'United States? Senator$', t, re.I):
        return 'U.S. Senate', ''
    m = re.match(r'Representative in Congress (\d+)(?:st|nd|rd|th) District$',
                 t, re.I)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'Representative in State Legislature '
                 r'(\d+)(?:st|nd|rd|th) District$', t, re.I)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'County Commissioner (\d+)(?:st|nd|rd|th) District$', t, re.I)
    if m:
        return 'County Commissioner', m.group(1)
    return t, ''


def jurisdiction(label):
    return re.sub(r'[ ,]*Precinct \d+$', '', label).strip()


def val_int(text):
    return int(float(text.replace(',', '').replace('%', '')))


class Contest:
    def __init__(self, title, party):
        self.title = title
        self.party = party
        self.page_headers = []  # (nvalues, [(x0, name), ...]) per page
        self.rows = []          # (precinct label, [values])
        self.totals = None

    def column_names(self, ncols):
        """{value column index -> header name}, majority across pages."""
        names = {}
        for k in range(ncols):
            variants = Counter()
            for nvalues, heads in self.page_headers:
                off = nvalues - len(heads)  # 0, or 1 with an unnamed col 0
                if off not in (0, 1) or not 0 <= k - off < len(heads):
                    continue
                variants[heads[k - off]] += 1
            names[k] = variants.most_common(1)[0][0] if variants else None
        return names


def close(c, rows, registered, ballots, problems):
    if c is None:
        return
    if c.totals is None:
        problems.append(f'{c.title!r}: no Totals row')
        return
    ncols = len(c.totals)
    if not c.rows:
        problems.append(f'{c.title!r}: no data rows')
        return
    for label, vals in c.rows:
        if len(vals) != ncols:
            problems.append(f'{c.title!r} / {label}: {len(vals)} values '
                            f'!= {ncols}')
            return
    names = c.column_names(ncols)
    sums = [0] * ncols
    for _, vals in c.rows:
        for k, v in enumerate(vals):
            sums[k] += v
    for k, nm in names.items():
        # a contest with no qualified candidate prints an all-zero unnamed
        # leftmost column; only a nonzero unnamed column is a problem
        if nm is None and sums[k]:
            problems.append(f'{c.title!r}: unnamed column {k} has nonzero '
                            f'total {sums[k]}')
    for k in range(ncols):
        if names.get(k) == 'Turnout Percentage':
            continue  # percentages, not counts
        if sums[k] != c.totals[k]:
            problems.append(f'{c.title!r} col {names.get(k) or k!r}: row sum '
                            f'{sums[k]} != Totals {c.totals[k]}')
    # office resolution
    is_local = c.title in LOCAL_OFFICES
    is_delegate = 'Delegate to County Convention' in c.title
    office = district = None
    if is_local:
        js = {jurisdiction(label) for label, _ in c.rows}
        if len(js) == 1:
            office = f'{js.pop()} {c.title}'
        elif len(js) > 1 and c.title in ('Clerk', 'Treasurer'):
            office = f'County {c.title}'  # rows span the county
        else:
            problems.append(f'{c.title!r}: rows span jurisdictions '
                            f'{sorted(js)[:4]}')
            office = c.title
    elif not is_delegate:
        office, district = map_office(c.title)
    reg_k = next((k for k, nm in names.items()
                  if nm == 'Registered Voters'), None)
    tot_k = next((k for k, nm in names.items()
                  if nm == 'Total Ballots Cast'), None)
    distinct_js = len({jurisdiction(l) for l, _ in c.rows})
    for label, vals in c.rows:
        district_row = ''
        if is_local:
            office_row = f'{jurisdiction(label)} {c.title}' \
                if distinct_js == 1 else office
        elif is_delegate:
            office_row = f'{label} Delegate to County Convention'
        else:
            office_row, district_row = office, district
        for k, nm in names.items():
            v = vals[k]
            if nm is None:
                if v:
                    problems.append(f'{c.title!r} / {label}: unnamed column '
                                    f'{k} has {v} votes')
                continue
            if nm in TAIL_NAMES:
                continue
            nm = re.sub(r' \(W\)$', '', nm)  # qualified write-in candidate
            if not v:
                continue
            rows.append({'county': COUNTY, 'precinct': label,
                         'office': office_row, 'district': district_row,
                         'party': c.party, 'candidate': nm, 'votes': v})
        if reg_k is not None:
            registered[label].append(vals[reg_k])
        if tot_k is not None:
            ballots[label].append(vals[tot_k])


def parse(src):
    import pdfplumber

    problems = []
    rows = []
    registered = defaultdict(list)
    ballots = defaultdict(list)
    contest = None

    with pdfplumber.open(src) as pdf:
        for page in pdf.pages:
            words = page.extract_words(extra_attrs=['upright'])
            lines = defaultdict(list)
            for w in words:
                if w['upright']:
                    lines.setdefault(round(w['top']), []).append(w)
            merged = []
            for t in sorted(lines):
                if merged and t - merged[-1][-1] <= 2:
                    merged[-1].append(t)
                else:
                    merged.append([t])
            page_lines = []
            for ks in merged:
                ws = [w for k in ks
                      for w in sorted(lines[k], key=lambda w: w['x0'])]
                page_lines.append((ks[0], ws))
            rot = defaultdict(list)
            for w in words:
                if not w['upright']:
                    rot[round(w['x0'] / 4) * 4].append(w)
            heads = rot_columns(rot)

            title_top = title = party = None
            for top, ws in page_lines:
                m = TITLE.match(' '.join(w['text'] for w in ws))
                if m:
                    title_top, title = top, m.group(1)
                    party = PARTY_CODES.get(m.group(2), '')
                    break
            if title_top is None:
                if page.page_number != 302:
                    problems.append(f'page {page.page_number}: no title')
                continue

            recorded_headers = False
            # pre-analyse lines: a wrapped label prints one part on a
            # value-less line 5px from its value row — above ('Douglass
            # Township, Precinct' / values) or below (values / '1')
            analysed = []
            for top, ws in page_lines:
                if top < title_top:
                    continue  # header band
                text = ' '.join(w['text'] for w in ws)
                values = [w for w in ws
                          if NUM.match(w['text']) and w['x1'] >= VALUE_X1]
                analysed.append((top, text, values, ws))
            pending_label = None
            for idx, (top, text, values, ws) in enumerate(analysed):
                if TITLE.match(text):
                    if contest is not None and contest.title == title \
                            and contest.totals is None:
                        continue  # repeated title on a continuation page
                    if contest is not None and contest.totals is None:
                        problems.append(f'{contest.title!r}: superseded by '
                                        f'{title!r} before its Totals row')
                    close(contest, rows, registered, ballots, problems)
                    contest = Contest(title, party)
                    pending_label = None
                    continue
                if text == 'Precinct':
                    continue
                if text == 'Totals' or text.startswith('Totals '):
                    if contest is None:
                        problems.append(f'page {page.page_number}: Totals '
                                        f'outside a contest')
                        continue
                    if contest.totals is not None:
                        problems.append(f'{contest.title!r}: duplicate '
                                        f'Totals row')
                    contest.totals = [val_int(w['text']) for w in values]
                    pending_label = None
                    continue
                if values:
                    if contest is None:
                        problems.append(f'page {page.page_number}: data row '
                                        f'outside a contest: {text!r}')
                        continue
                    label = ' '.join(w['text'] for w in ws
                                     if not (NUM.match(w['text'])
                                             and w['x1'] >= VALUE_X1))
                    if pending_label is not None:
                        label = f'{pending_label} {label}'.strip()
                        pending_label = None
                    vals = [val_int(w['text']) for w in values]
                    if not recorded_headers:
                        contest.page_headers.append((len(vals), heads))
                        recorded_headers = True
                    contest.rows.append((label, vals))
                elif contest is not None:
                    # value-less line: a label fragment. It belongs to the
                    # value row it sits 5px from — below the previous one
                    # (suffix) or above the next one (prefix).
                    prev_val = next((j for j in range(idx - 1, -1, -1)
                                     if analysed[j][2]), None)
                    next_val = next((j for j in range(idx + 1, len(analysed))
                                     if analysed[j][2]), None)
                    if next_val is not None and \
                            analysed[next_val][0] - top <= 8:
                        pending_label = text if pending_label is None \
                            else f'{pending_label} {text}'
                    elif prev_val is not None and contest.rows and \
                            top - analysed[prev_val][0] <= 8:
                        prev_label, prev_vals = contest.rows[-1]
                        contest.rows[-1] = (f'{prev_label} {text}', prev_vals)
                    else:
                        problems.append(f'page {page.page_number}: stray '
                                        f'line {text!r}')
                else:
                    problems.append(f'page {page.page_number}: stray line '
                                    f'{text!r}')
    close(contest, rows, registered, ballots, problems)

    # Scoped contests (school-district and township millage proposals)
    # report only the part of a precinct inside their jurisdiction, so the
    # per-contest turnout varies; the countywide contests carry the true
    # precinct values, i.e. the maximum.
    for label, values in registered.items():
        if len(set(values)) > 1:
            print(f'NOTE {label}: Registered Voters varies across contests '
                  f'{sorted(set(values))}; kept max')
        rows.append({'county': COUNTY, 'precinct': label,
                     'office': 'Registered Voters', 'district': '',
                     'party': '', 'candidate': '', 'votes': max(values)})
    for label, values in ballots.items():
        if len(set(values)) > 1:
            print(f'NOTE {label}: Ballots Cast varies across contests '
                  f'{sorted(set(values))}; kept max')
        rows.append({'county': COUNTY, 'precinct': label,
                     'office': 'Ballots Cast', 'district': '', 'party': '',
                     'candidate': '', 'votes': max(values)})

    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
        sys.exit(f'{COUNTY}: {len(problems)} problems')
    write_csv(COUNTY, rows)


if __name__ == '__main__':
    parse(SRC if len(sys.argv) < 2 else sys.argv[1])