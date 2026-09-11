"""Parse the Ogemaw County Aug 2020 primary "Results by Precinct" PDF.

Source: 'Ogemaw MI August 4 2020 Primary Election Results.pdf'
(openelections-sources-mi/2020/primary), 25 pages, clean text layer.
Contest-major and blocks can span pages, so the whole document is parsed
linearly: each contest is a title line '<Office> (PARTY) (Vote for N)'
(proposals carry no party), then a 'Precinct <candidate names...>' header
row, then one row per precinct ('<label ending in "Precinct N">
<values...>') and a 'Total <values...>' row that validates every column.
There is no turnout block in this report, so no Registered Voters /
Ballots Cast rows are emitted.

Column layout: values are right-aligned with no delimiters between
candidate names in the header, so columns are established from the value
positions of the data rows (k-th value of every row shares a right edge)
and the header words are assigned to the column with the nearest right
edge. extract_words can fuse a candidate name with a following 'Write-in'
('SchultzWrite-in'), so such words are split first. 'Write-in' header
columns become candidate 'Write-In' (emitted only when nonzero).

Office mapping: 'County ' prefixes are dropped (Crawford precedent),
'United States Senator' -> U.S. Senate, 'Representative in Congress Nth
District' -> U.S. House N, 'Rep in State Legislature Nth District' ->
State House N, 'County Commissioner Nth District' -> County Commissioner
N, '<X> Precinct N Delegate' -> Precinct Delegate with '<X>, Precinct N'
as district (Delta convention). Township offices and proposals are kept
as printed (proposals with Yes/No candidates).

Usage: .venv/bin/python src/ogemaw_2020.py
"""
import os
import re
import sys
from collections import defaultdict
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import write_csv  # noqa: E402

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Ogemaw MI August 4 2020 Primary Election Results.pdf')

COUNTY = 'Ogemaw'

CONTEST = re.compile(r'^(.*\S) \((DEM|REP|LIB|UST|GRN|NLP)\) '
                     r'\(Vote for (\d+)\)$')
PROPOSAL = re.compile(r'^(.*\S) \(Vote for (\d+)\)$')
NUM = re.compile(r'^[\d,]+$')


def map_office(title):
    """Contest title (without the party/(Vote for N) suffix) -> (office,
    district)."""
    t = ' '.join(title.split())
    if re.match(r'^United States Senator$', t, re.I):
        return 'U.S. Senate', ''
    m = re.match(r'^Rep(?:resentative)? in Congress (\d+)', t, re.I)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^Rep(?:resentative)? in State Legislature (\d+)', t, re.I)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'^County Commissioner (\d+)', t, re.I)
    if m:
        return 'County Commissioner', m.group(1)
    m = re.match(r'^(.+?) Precinct (\d+) Delegate$', t)
    if m:
        return 'Precinct Delegate', f'{m.group(1)}, Precinct {m.group(2)}'
    if t.startswith('County '):
        t = t[len('County '):]
    return t, ''


def split_writein(w):
    """extract_words fuses a candidate name with a following 'Write-in'
    when kerning is tight ('SchultzWrite-in'); split the text and divide
    the box proportionally by length."""
    m = re.match(r'^(.+?)(Write-?in)$', w['text'], re.I)
    if m:
        frac = len(m.group(1)) / len(w['text'])
        mid = w['x0'] + (w['x1'] - w['x0']) * frac
        return [{'text': m.group(1), 'x0': w['x0'], 'x1': mid},
                {'text': m.group(2), 'x0': mid, 'x1': w['x1']}]
    return [w]


def document_lines(pdf):
    """All lines of the document as sorted word lists, in reading order."""
    out = []
    for page in pdf.pages:
        rows = defaultdict(list)
        for w in page.extract_words():
            key = round(w['top'])
            for k in list(rows):
                if abs(k - key) <= 2:
                    key = k
                    break
            rows.setdefault(key, []).append(w)
        for _, ws in sorted(rows.items()):
            words = []
            for w in sorted(ws, key=lambda w: w['x0']):
                words.extend(split_writein(w))
            out.append(words)
    return out


def main():
    import pdfplumber

    problems = []
    rows = []
    with pdfplumber.open(SRC) as pdf:
        lines = document_lines(pdf)

    i = 0
    while i < len(lines):
        text = ' '.join(w['text'] for w in lines[i])
        m = CONTEST.match(text) or PROPOSAL.match(text)
        if not m or text.startswith('Precinct '):
            i += 1
            continue
        is_contest = bool(CONTEST.match(text))
        party = m.group(2) if is_contest else ''
        office, district = map_office(m.group(1))
        # header row: 'Precinct <names...>'
        if i + 1 >= len(lines) or lines[i + 1][0]['text'] != 'Precinct':
            problems.append(f'{office!r}: no Precinct header row after '
                            f'{text!r}')
            i += 1
            continue
        header = lines[i + 1]
        data_rows = []
        total_row = None
        j = i + 2
        while j < len(lines):
            t2 = ' '.join(w['text'] for w in lines[j])
            if lines[j][0]['text'] == 'Total':
                total_row = lines[j]
                j += 1
                break
            if CONTEST.match(t2) or PROPOSAL.match(t2) or \
                    lines[j][0]['text'] == 'Precinct':
                break
            data_rows.append(lines[j])
            j += 1
        # column count from the value words of each row
        ncols = None
        col_edges = defaultdict(list)
        parsed = []
        for ws2 in data_rows:
            text2 = ' '.join(w['text'] for w in ws2)
            m2 = re.match(r'^(.*Precinct \d) ((?:[\d,]+ ?)+)$', text2)
            if not m2:
                problems.append(f'bad data row {text2!r} in {office!r}')
                continue
            label = re.sub(r'\s+', ' ', m2.group(1)).strip()
            vals = [int(v.replace(',', '')) for v in m2.group(2).split()]
            if ncols is None:
                ncols = len(vals)
            elif len(vals) != ncols:
                problems.append(f'{office} / {label}: {len(vals)} values '
                                f'!= {ncols}')
                continue
            for k, w in enumerate(ws2[-len(vals):]):
                col_edges[k].append(w['x1'])
            parsed.append((label, vals))
        if ncols is None:
            problems.append(f'{office!r}: no data rows')
            i = j
            continue
        edges = [sorted(v)[len(v) // 2] for _, v in sorted(col_edges.items())]
        if len(edges) != ncols:
            problems.append(f'{office!r}: {len(edges)} column edges != '
                            f'{ncols} values')
            i = j
            continue
        # Header words are undelimited candidate names. Enumerate every
        # contiguous partition of the words into ncols non-empty groups and
        # keep the one whose per-group right edge is closest to the
        # value-column right edge: values are right-aligned and each name
        # ends at its column's right edge (nearest-word assignment fails on
        # narrow columns).
        words = header[1:]
        n = len(words)
        best, best_pen = None, None
        if n >= ncols:
            for cuts in combinations(range(1, n), ncols - 1):
                bounds = [0, *cuts, n]
                pen = sum(abs(words[bounds[k + 1] - 1]['x1'] - edges[k])
                          for k in range(ncols))
                if best_pen is None or pen < best_pen:
                    best_pen, best = pen, bounds
        if best is None:
            problems.append(f'{office!r}: {n} header words for {ncols} '
                            f'columns')
            i = j
            continue
        cands = [' '.join(w['text'] for w in words[best[k]:best[k + 1]])
                 for k in range(ncols)]
        cands = [re.sub(r'\s+', ' ', c).strip() for c in cands]
        if any(not c for c in cands):
            problems.append(f'{office!r}: header words '
                            f'{[w["text"] for w in header]} left a column '
                            f'unnamed: {cands}')
            i = j
            continue
        sums = [0] * ncols
        for label, vals in parsed:
            for k, v in enumerate(vals):
                sums[k] += v
                if not v and cands[k].lower() == 'write-in':
                    continue
                rows.append({'county': COUNTY, 'precinct': label,
                             'office': office, 'district': district,
                             'party': party,
                             'candidate': 'Write-In'
                             if cands[k].lower() == 'write-in' else cands[k],
                             'votes': v})
        if total_row is not None:
            tot = [int(w['text'].replace(',', ''))
                   for w in total_row if NUM.match(w['text'])]
            if len(tot) != ncols:
                problems.append(f'{office!r}: Total row has {len(tot)} '
                                f'values != {ncols}')
            else:
                for k in range(ncols):
                    if tot[k] != sums[k]:
                        problems.append(f'{office} col {cands[k]!r}: Total '
                                        f'{tot[k]} != parsed {sums[k]}')
        else:
            problems.append(f'{office!r}: no Total row')
        i = j

    if problems:
        for p in problems[:30]:
            print('PROBLEM:', p)
        sys.exit(f'Ogemaw: {len(problems)} problems')
    write_csv(COUNTY, rows)


if __name__ == '__main__':
    main()