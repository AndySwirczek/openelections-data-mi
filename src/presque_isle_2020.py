"""Parse the Presque Isle County Aug 2020 primary "Statement of Votes Cast"
PDF into a precinct CSV.

Source: 'Presque Isle MI August 4, 2020 Election Results.pdf'
(openelections-sources-mi/2020/primary), 58 pages, clean text layer.
Page 1 is the turnout table ('<jurisdiction> <registered> <cast> <%>');
pages 2-58 hold 93 contest-major tables, one or two per page:

    <title lines>
    Vote for N
    Democrat Republican        <- party spans (proposals: 'YES NO')
    <rotated candidate names>  <- 90-degree headers, read bottom-to-top,
                                  each line stored glyph-reversed
    <jurisdiction> <values...> <- right-aligned at each column's edge
    Total <values...>          <- validates every column ('Michigan -
                                  Total' on pages where the county-total
                                  footer follows the table)

Columns sit on a fixed ~50px pitch; a column's values are right-aligned
at rotated-header x0 + 12.6 (proposal YES/NO headers: at the header's own
right edge). Zero cells are simply not printed -- except a party's empty
leftmost slot, which prints a 0 with no header column (dropped; a nonzero
unmatched value is an error). Each rotated column is one candidate; a
name wraps over several vertical lines that rejoin bottom-to-top
('nosugreF'/'anaD' -> 'Dana Ferguson'). On the last contest of a page
that runs to the bottom, the county-total footer overlaps the final data
row and pdfplumber interleaves the two texts char-by-char
('BPereasrqinugee' = 'Presque' + 'Bearinger'); the noise subsequence
('Presque Isle County') is removed at char level and the remainder must
be a known jurisdiction.

Titles: 'United States Senator for State' -> U.S. Senate,
'Representative in Congress 1st District for State' -> U.S. House 1,
'Rep in State Legislature 106th District for State' -> State House 106,
'County Commissioner (for) District N' -> County Commissioner N,
'Delegate to the County Convention for <Township>' -> Precinct Delegate
with the township as district, '<Office> for <Township>' ->
'<Township> <Office>', the 'County ' prefix dropped and the ' for State'
suffix stripped. Proposal tables ('YES NO' header) keep their printed
title with Yes/No candidates and a blank party. Write-in columns
('Write-In' / '(Write-In)') become candidate 'Write-In' (emitted only
when nonzero).

Usage: .venv/bin/python src/presque_isle_2020.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import write_csv  # noqa: E402

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Presque Isle MI August 4, 2020 Election Results.pdf')

COUNTY = 'Presque Isle'

NOISE = {'Presque Isle County, Michigan', 'August 4, 2020'}
FOOTER_NOISE = 'PresqueIsleCounty'
VOTE_FOR = re.compile(r'^Vote for (\d+)$')
NUM = re.compile(r'^[\d,]+$')
LABEL_X1 = 250.0      # jurisdiction labels end left of the value columns
COL_X0 = 260.0        # value words and rotated headers start right of here
EDGE_OFFSET = 12.6    # value right edge = rotated-header x0 + this


def unrotate(w):
    """A rotated (upright=False) word extracts with its glyphs in reverse
    order ('nosugreF'); reverse it back."""
    return w['text'][::-1]


def map_office(title):
    """Contest title -> (office, district)."""
    t = ' '.join(title.split())
    t = re.sub(r' for State$', '', t)
    if re.match(r'^United States Senator$', t, re.I):
        return 'U.S. Senate', ''
    m = re.match(r'^Rep(?:resentative)? in Congress '
                 r'(\d+)(?:st|nd|rd|th)? District$', t, re.I)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^Rep(?:resentative)? in State Legislature '
                 r'(\d+)(?:st|nd|rd|th)? District$', t, re.I)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'^County Commissioner (?:for )?District (\d+)$', t, re.I)
    if m:
        return 'County Commissioner', str(int(m.group(1)))
    m = re.match(r'^Delegate to the County Convention for (.+)$', t, re.I)
    if m:
        return 'Precinct Delegate', m.group(1).strip()
    m = re.match(r'^(.+?) for (.+ (?:Township|City))$', t)
    if m:
        office, jur = m.group(1), m.group(2)
        # 'Township Clerk for Allis Township' -> 'Allis Township Clerk'
        if office.startswith('Township ') and jur.endswith('Township'):
            office = office[len('Township '):]
        return f'{jur} {office}', ''
    if t.startswith('County '):
        t = t[len('County '):]
    return t, ''


def page_lines(page):
    """Horizontal lines of a page as {'top', 'text', 'words'}, plus the
    rotated words separately."""
    hor, rot = [], []
    for w in page.extract_words(extra_attrs=['upright']):
        (hor if w.get('upright', True) else rot).append(w)
    rows = []
    for w in sorted(hor, key=lambda w: (w['top'], w['x0'])):
        key = round(w['top'])
        for r in rows:
            if abs(r['top'] - key) <= 2:
                r['words'].append(w)
                break
        else:
            rows.append({'top': key, 'words': [w]})
    lines = []
    for r in rows:
        ws = sorted(r['words'], key=lambda w: w['x0'])
        lines.append({'top': r['top'],
                      'text': ' '.join(w['text'] for w in ws),
                      'words': ws})
    return lines, rot


def build_columns(party_line, rot, top_bound, problems, where, title):
    """Candidate columns for one contest: rotated headers above the first
    data row (or the YES/NO header words of a proposal)."""
    pw = {w['text']: w for w in party_line['words']}
    if 'YES' in pw and 'NO' in pw:
        cols = []
        for name, w in (('Yes', pw['YES']), ('No', pw['NO'])):
            cols.append({'name': name, 'writein': False,
                         'edge': w['x1'], 'party': ''})
        return cols
    dem_span = (pw['Democrat']['x0'], pw['Democrat']['x1']) \
        if 'Democrat' in pw else (0, -1)
    rep_span = (pw['Republican']['x0'], pw['Republican']['x1']) \
        if 'Republican' in pw else (0, -1)
    cols = []
    for w in sorted(rot, key=lambda w: (w['x0'], w['top'])):
        if not (party_line['top'] < w['top'] < top_bound) or w['x0'] < COL_X0:
            continue
        for c in cols:
            if abs(c['x0'] - w['x0']) <= 5:
                c['lines'].append(w)
                break
        else:
            cols.append({'x0': w['x0'], 'lines': [w]})
    for c in cols:
        parts = [unrotate(w) for w in sorted(c['lines'],
                                             key=lambda w: -w['top'])]
        stripped = [re.sub(r'^\((.+)\)$', r'\1', p) for p in parts]
        c['writein'] = all(p == 'Write-In' for p in stripped)
        c['name'] = 'Write-In' if c['writein'] else ' '.join(parts)
        c['edge'] = c['x0'] + EDGE_OFFSET
        mid = c['x0'] + 6
        c['party'] = 'DEM' if dem_span[0] <= mid <= dem_span[1] else 'REP'
    if not cols:
        problems.append(f'{where} {title!r}: no candidate columns')
        return None
    if any(not c['name'] for c in cols):
        problems.append(f'{where} {title!r}: empty candidate column in '
                        f'{[c["name"] for c in cols]}')
        return None
    return cols


def assign_values(title, label, cols, words, problems, where):
    """A row's value words -> {column index: votes} (zero cells are not
    printed; unmatched zeros belong to headerless empty slots)."""
    by_col = {}
    for w in words:
        if w['x0'] < COL_X0 or not NUM.match(w['text']):
            continue
        k = min(range(len(cols)), key=lambda i: abs(cols[i]['edge'] - w['x1']))
        if abs(cols[k]['edge'] - w['x1']) > 8:
            if int(w['text'].replace(',', '')):
                problems.append(f'{where} {title!r} / {label}: value '
                                f'{w["text"]!r} at x1={w["x1"]:.0f} matches '
                                f'no column edge')
            continue
        if k in by_col:
            problems.append(f'{where} {title!r} / {label}: two values for '
                            f'column {k}')
        by_col[k] = int(w['text'].replace(',', ''))
    return by_col


def deinterleave(chars, known):
    """The county-total footer overlaps the last data row of a full page
    and pdfplumber interleaves the two texts char-by-char. The two runs
    sit at slightly different tops (~1.5px apart), so cluster the chars
    by top and take the run whose text is a known jurisdiction."""
    groups = []
    for c in sorted(chars, key=lambda c: c['top']):
        for g in groups:
            if abs(g['top'] - c['top']) <= 0.7:
                g['chars'].append(c)
                break
        else:
            groups.append({'top': c['top'], 'chars': [c]})
    for g in groups:
        label = ''.join(c['text'] for c in
                        sorted(g['chars'], key=lambda c: c['x0'])).strip()
        label = re.sub(r'\s+', ' ', label)
        if label in known:
            return label
    return None


def main():
    import pdfplumber

    problems = []
    rows = []
    turnout = {}
    known = set()

    with pdfplumber.open(SRC) as pdf:
        # page 1: turnout table
        for line in (pdf.pages[0].extract_text() or '').splitlines():
            m = re.match(r'^(.+?) ([\d,]+) ([\d,]+) [\d.]+$', line.strip())
            if m and m.group(1) != 'Presque Isle County Michigan - Total':
                turnout[m.group(1).strip()] = (
                    int(m.group(2).replace(',', '')),
                    int(m.group(3).replace(',', '')))
        known = set(turnout)
        contests = []
        for page in pdf.pages[1:]:
            lines, rot = page_lines(page)
            i = 0
            while i < len(lines):
                line = lines[i]
                text = line['text']
                if text in NOISE or VOTE_FOR.match(text):
                    i += 1
                    continue
                # a title: consecutive lines up to the 'Vote for N' line
                j = i + 1
                title_lines = [text]
                while j < len(lines) and not VOTE_FOR.match(lines[j]['text']):
                    if lines[j]['text'] not in NOISE:
                        title_lines.append(lines[j]['text'])
                    j += 1
                if j >= len(lines) or not VOTE_FOR.match(lines[j]['text']):
                    problems.append(f'page {page.page_number}: no Vote for '
                                    f'after title {title_lines!r}')
                    i = j
                    continue
                vote_for = int(VOTE_FOR.match(lines[j]['text']).group(1))
                title = ' '.join(title_lines)
                if j + 1 >= len(lines):
                    problems.append(f'page {page.page_number}: no party '
                                    f'header after {title!r}')
                    i = j
                    continue
                party_line = lines[j + 1]
                # rows until one whose label contains 'Total'
                data_rows = []
                total_row = None
                k = j + 2
                while k < len(lines):
                    label_ws = [w for w in lines[k]['words']
                                if w['x1'] < LABEL_X1]
                    if any(w['text'] == 'Total' for w in label_ws):
                        total_row = lines[k]
                        k += 1
                        break
                    data_rows.append(lines[k])
                    k += 1
                if total_row is None:
                    problems.append(f'page {page.page_number}: no Total row '
                                    f'for {title!r}')
                # the rotated headers sit between the party line and the
                # first data row (later contests' headers are lower on the
                # page and must not leak into this one)
                top_bound = min((l['top'] for l in data_rows + [total_row]
                                 if l is not None and
                                 l['top'] > party_line['top']),
                                default=10 ** 6)
                cols = build_columns(party_line, rot, top_bound, problems,
                                     f'page {page.page_number}', title)
                where = f'page {page.page_number}'
                parsed = []
                for l in data_rows + ([total_row] if total_row else []):
                    label_ws = [w for w in l['words'] if w['x1'] < LABEL_X1]
                    if any(w['text'] == 'Total' for w in label_ws):
                        label = 'Total'    # 'Total' / 'Michigan - Total'
                    else:
                        label = ' '.join(w['text'] for w in label_ws)
                        if label not in known:
                            # footer interleave: split the overlapped runs
                            cs = [c for c in page.chars
                                  if c['x1'] < LABEL_X1
                                  and abs(c['top'] - l['top']) <= 3]
                            fixed = deinterleave(cs, known) if cs else None
                            if fixed:
                                label = fixed
                            else:
                                problems.append(f'{where} {title!r}: unknown '
                                                f'jurisdiction label '
                                                f'{label!r}')
                    parsed.append((label, assign_values(
                        title, label, cols, l['words'], problems, where)))
                if cols is not None:
                    contests.append({'title': title, 'cols': cols,
                                     'parsed': parsed})
                i = k

    # validate + emit
    for c in contests:
        sums = {}
        for label, by_col in c['parsed'][:-1]:
            for k, v in by_col.items():
                sums[k] = sums.get(k, 0) + v
        tot = c['parsed'][-1][1]
        for k in range(len(c['cols'])):
            if tot.get(k, 0) != sums.get(k, 0):
                problems.append(f'{c["title"]} / {c["cols"][k]["name"]!r}: '
                                f'Total {tot.get(k, 0)} != parsed '
                                f'{sums.get(k, 0)}')
        for label, by_col in c['parsed'][:-1]:
            for k, col in enumerate(c['cols']):
                v = by_col.get(k, 0)
                if not v:
                    continue
                rows.append({'county': COUNTY, 'precinct': label,
                             'office': c['title'],
                             'district': '',
                             'party': col['party'],
                             'candidate': col['name'], 'votes': v})
    for r in rows:
        office, district = map_office(r['office'])
        r['office'] = office
        r['district'] = district
    for jur, (rv, bc) in sorted(turnout.items()):
        rows.append({'county': COUNTY, 'precinct': jur,
                     'office': 'Registered Voters', 'district': '',
                     'party': '', 'candidate': '', 'votes': rv})
        rows.append({'county': COUNTY, 'precinct': jur,
                     'office': 'Ballots Cast', 'district': '',
                     'party': '', 'candidate': '', 'votes': bc})

    if problems:
        for p in problems[:30]:
            print('PROBLEM:', p)
        sys.exit(f'Presque Isle: {len(problems)} problems')
    write_csv(COUNTY, rows)


if __name__ == '__main__':
    main()