"""Parse a Gratiot/Shiawassee-style Aug 2020 primary "Results per Precinct"
PDF (ES&S contest-major export) into a precinct CSV.

Source (Gratiot): 'Gratiot County Aug 2020 Primary Precinct Results.pdf'
(openelections-sources-mi/2020/primary), 30 pages, clean text layer.
Contest-major: '<Office> (PARTY)' title (proposals carry no party), then a
'Precinct <candidate names...> Total Write-ins' header row ('Total
Write-ins' is the write-in column; the delegate blocks have no candidate
columns), then numbered rows '<n> <label ending in "Precinct N">
<values...>' (values are left-aligned at the column edges) and a
'Total <values...>' row that validates every column. Turnout is countywide
only in the page header, so no per-precinct Registered Voters / Ballots
Cast rows are emitted.

Office mapping: 'United States Senator' -> U.S. Senate, 'Rep in Congress
Nth District' -> U.S. House N, 'Rep in State Legislature Nth District' /
'State Representative N' -> State House N, 'County Commissioner NN' ->
County Commissioner N, 'Delegate for <juris> Precinct N' -> Precinct
Delegate with '<juris>, Precinct N' as district (Delta convention),
'County ' prefixes dropped, 'Twp' expanded to 'Township' and the printed
typo 'Arcada Twp Supervior' normalized to 'Supervisor'. Proposals are kept
as printed with Yes/No candidates; 'Total Write-ins' columns become
candidate 'Write-In' (emitted only when nonzero).

Candidate names are left-aligned at their value-column edge; a word
starting within a few px of an edge begins the next name, other words
continue the current one in reading order (so wrapped fragments like
'Muhammad' / 'Salman Rais' and 'Bernard James' / 'Barnes' rejoin their
names across the header line and page breaks).

Usage:
    .venv/bin/python src/gratiot_2020.py                     # Gratiot
    .venv/bin/python src/gratiot_2020.py <County> <pdf-path> # same format
"""
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import write_csv  # noqa: E402

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Gratiot County Aug 2020 Primary Precinct Results.pdf')

COUNTY = 'Gratiot'

CONTEST = re.compile(r'^(.*\S) \((DEM|REP|LIB|UST|GRN|NLP)\)$')
NUM = re.compile(r'^[\d,]+$')


def undouble(text):
    """Bold header text extracts with every glyph doubled
    ('PPrreecciinncctt'); collapse it back."""
    if len(text) >= 4 and len(text) % 2 == 0 \
            and text == ''.join(c * 2 for c in text[::2]):
        return text[::2]
    return text


def map_office(title):
    """Contest title (without the party suffix) -> (office, district)."""
    t = ' '.join(title.split())
    if re.match(r'^United States Senator$', t, re.I):
        return 'U.S. Senate', ''
    m = re.match(r'^Rep(?:resentative)? in Congress (\d+)', t, re.I)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^Rep(?:resentative)? in State Legislature (\d+)', t, re.I)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'^State Representative (\d+)$', t, re.I)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'^County Commissioner (\d+)$', t, re.I)
    if m:
        return 'County Commissioner', str(int(m.group(1)))
    m = re.match(r'^Delegate for (.+?) Precinct (\d+)$', t)
    if m:
        return 'Precinct Delegate', f'{m.group(1)}, Precinct {m.group(2)}'
    if t.startswith('County '):
        t = t[len('County '):]
    t = re.sub(r'\bTwp\b', 'Township', t)
    t = t.replace('Supervior', 'Supervisor')
    return t, ''


def document_lines(pdf, county):
    """All lines as sorted word lists, in reading order, with page headers
    ('<County> County', 'Results per Precinct'), the report-header block
    and page footers ('1/30') removed."""
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
            text = ' '.join(w['text'] for w in sorted(ws, key=lambda w: w['x0']))
            if text in (f'{county} County', 'Results per Precinct') or \
                    re.match(r'^\d+/\d+$', text) or \
                    re.match(r'^(Precincts fully reported|Last Updated|'
                             r'Election Report|Unofficial Results|Turnout)'
                             r'\b', text):
                continue
            out.append(sorted(ws, key=lambda w: w['x0']))
    return out


def parse(county, src):
    import pdfplumber

    problems = []
    rows = []
    with pdfplumber.open(src) as pdf:
        lines = document_lines(pdf, county)

    i = 0
    while i < len(lines):
        text = ' '.join(w['text'] for w in lines[i])
        m = CONTEST.match(text)
        if not m or text.startswith('Precinct '):
            i += 1
            continue
        party = m.group(2)
        office, district = map_office(m.group(1))
        # The header ('Precinct <names...>') can be split by wrapped
        # candidate-name fragments printed above it ('Muhammad' /
        # 'Salman Rais'); collect every word between the title and the
        # first data row.
        words = []
        data_start = None
        for j in range(i + 1, len(lines)):
            t2 = ' '.join(w['text'] for w in lines[j])
            if CONTEST.match(t2):
                break
            if re.match(r'^\d+ .*Precinct \d', t2):
                data_start = j
                break
            words.extend({'text': undouble(w['text']), 'x0': w['x0'],
                          'top': w['top']}
                         for w in lines[j] if w['text'] != 'Precinct'
                         and undouble(w['text']) != 'Precinct')
        if data_start is None:
            problems.append(f'{office!r}: no header/data rows after '
                            f'{text!r}')
            i += 1
            continue
        # 'Total Write-ins' names the write-in column; wrapped name
        # fragments can sit on either side of it, so remove it wherever it
        # appears
        wi_col = None
        for k in range(len(words) - 1):
            if words[k]['text'] == 'Total' \
                    and words[k + 1]['text'].startswith('Write-ins'):
                del words[k:k + 2]
                wi_col = 'last'    # the final value column
                break
        data_rows = []
        total_row = None
        j = data_start
        while j < len(lines):
            t2 = ' '.join(w['text'] for w in lines[j])
            if CONTEST.match(t2):
                break
            if lines[j][0]['text'] == 'Total':
                total_row = lines[j]
                j += 1
                break
            if lines[j][0]['text'] == 'Precinct':
                # continuation header row on a page break
                j += 1
                continue
            if not any(NUM.match(w['text']) for w in lines[j]):
                # wrapped name fragment repeated at a page break
                j += 1
                continue
            data_rows.append(lines[j])
            j += 1
        ncols = None
        col_x0 = defaultdict(list)
        parsed = []
        for ws2 in data_rows:
            text2 = ' '.join(w['text'] for w in ws2)
            m2 = re.match(r'^\d+ (.*Precinct \d(?:-\d)?) ((?:[\d,]+ ?)+)$',
                          text2)
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
                col_x0[k].append(w['x0'])
            parsed.append((label, vals))
        if ncols is None:
            problems.append(f'{office!r}: no data rows')
            i = j
            continue
        edges = [sorted(v)[len(v) // 2] for _, v in sorted(col_x0.items())]
        if len(edges) != ncols:
            problems.append(f'{office!r}: {len(edges)} column edges != '
                            f'{ncols} values')
            i = j
            continue
        cand_cols = ncols - (1 if wi_col else 0)
        # Candidate names are left-aligned at their value-column edge; a
        # word starting within a few px of an edge begins the next name,
        # other words continue the current one (reading order, so wrapped
        # fragments like 'Bernard James' / 'Barnes' rejoin their name).
        cands = []
        col = None
        if cand_cols:
            cands = [''] * cand_cols
            for w in words:
                near = [k for k in range(cand_cols)
                        if abs(edges[k] - w['x0']) <= 5]
                if near:
                    col = near[0]
                elif col is None:
                    col = 0
                cands[col] = (cands[col] + ' ' + w['text']).strip()
        cands = [re.sub(r'\s+', ' ', c).strip() for c in cands]
        if any(not c for c in cands):
            problems.append(f'{office!r}: empty candidate column: {cands} '
                            f'(edges {edges})')
            i = j
            continue
        sums = [0] * ncols
        for label, vals in parsed:
            for k, v in enumerate(vals):
                sums[k] += v
                is_wi = wi_col and k == ncols - 1
                if not v:
                    continue
                rows.append({'county': county, 'precinct': label,
                             'office': office, 'district': district,
                             'party': party,
                             'candidate': 'Write-In' if is_wi else cands[k],
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
                        name = 'Write-In' if wi_col and k == ncols - 1 \
                            else cands[k]
                        problems.append(f'{office} col {name!r}: Total '
                                        f'{tot[k]} != parsed {sums[k]}')
        else:
            problems.append(f'{office!r}: no Total row')
        i = j

    if problems:
        for p in problems[:30]:
            print('PROBLEM:', p)
        sys.exit(f'{county}: {len(problems)} problems')
    write_csv(county, rows)


if __name__ == '__main__':
    if len(sys.argv) == 3:
        parse(sys.argv[1], sys.argv[2])
    else:
        parse(COUNTY, SRC)