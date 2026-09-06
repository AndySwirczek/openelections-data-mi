"""Parse county "Precinct-District Results by Contest Table" PDFs (2026 primary).

These PDFs (produced by county clerks' ES&S-style report writers) have one table
per contest per party: a contest header at the top of each page, diagonal
60-degree candidate-name column headers, one row per precinct, and trailing
columns Over Votes / Under Votes / Total Registered / Total Votes Cast.
Continuation pages repeat the contest header and end with a "Contest Total" row.

Usage:
    .venv/bin/python src/contest_table_pdf_parser.py <source.pdf> \
        --county Shiawassee \
        --out 2026/counties/20260804__mi__primary__shiawassee__precinct.csv
"""
import argparse
import csv
import re

import pdfplumber

# Slope of the 60-degree rotated headers: x0 + (a/b)*top is constant along a
# text line for matrix [a, b, ...] with a=0.5, b=0.87.
SLOPE = 0.5 / 0.87

NUMERIC = re.compile(r'^[\d,]+$')
PARTY_TAG = re.compile(r' \((DEM|REP|LIB|GRN|UST|NPA|NP)\)$')
DISTRICT_PATTERNS = [
    (re.compile(r'^Representative in Congress (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'U.S. House'),
    (re.compile(r'^State Senator (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'State Senate'),
    (re.compile(r'^Representative in State Legislature (?:(\d+)(?:st|nd|rd|th) District|District (\d+))$'), 'State House'),
]
OFFICE_EXACT = {
    'Governor': 'Governor',
    'United States Senator': 'U.S. Senate',
}


PARTY_PREFIX = re.compile(r'^(DEM|REP|LIB|GRN|UST|NPA|NP) ')
# Header forms like "Treasurer Kalamazoo Charter Township" or "Delegate to
# County Convention Alamo Township, Precinct 1" put the office type first;
# other counties put the jurisdiction first, so flip them for consistency.
OFFICE_TYPE_PREFIXES = (
    'Delegate to County Convention',
    'Delegate',
    'Clerk',
    'Treasurer',
    'Trustee',
    'Supervisor',
    'Constable',
    'Park Commissioner',
)


def flip_office_type(title):
    for prefix in OFFICE_TYPE_PREFIXES:
        if title.startswith(prefix + ' '):
            return title[len(prefix) + 1:] + ' ' + prefix
    return title


def map_office(title):
    party = ''
    m = PARTY_TAG.search(title)
    if m:
        party = m.group(1)
        title = title[: m.start()]
    m = PARTY_PREFIX.match(title)
    if m:
        party = m.group(1)
        title = title[m.end():]
    district = ''
    office = OFFICE_EXACT.get(title)
    if office is None:
        for pat, name in DISTRICT_PATTERNS:
            m = pat.match(title)
            if m:
                office, district = name, m.group(1) or m.group(2)
                break
        else:
            office = flip_office_type(title)
    return office, district, party


def group_lines(words, tol=4):
    """Group words into visual lines by top coordinate."""
    lines = []
    for w in sorted(words, key=lambda w: (w['top'], w['x0'])):
        for line in lines:
            if abs(line[0]['top'] - w['top']) <= tol:
                line.append(w)
                break
        else:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w['x0'])
    lines.sort(key=lambda l: l[0]['top'])
    return lines


def parse_page(page):
    """Return (contest_header_text, data_lines, contest_total_line, rotated_chars)."""
    words = page.extract_words(x_tolerance=2)
    lines = group_lines(words)
    header = None
    contest_total = None
    data_lines = []
    for line in lines:
        top = line[0]['top']
        text = ' '.join(w['text'] for w in line)
        if top < 100 or top > 545:
            continue  # page title / footer
        if top < 245:
            if header is None:
                header = text
            continue  # contest header, Vote For N, diagonal/party header rows
        if text.startswith('Contest Total'):
            contest_total = line
            continue
        data_lines.append(line)
    rotated = [
        c for c in page.chars
        if 128 < c['top'] < 248 and (abs(c['matrix'][1]) > 0.05 or abs(c['matrix'][0] - 1) > 0.05)
    ]
    return header, data_lines, contest_total, rotated


def detect_columns(x0s):
    """Cluster numeric cell x0 positions into column base positions."""
    columns = []
    for x in sorted(x0s):
        if not columns or x - columns[-1] > 20:
            columns.append(x)
    return columns


def parse_rows(data_lines, columns):
    """Split each visual line into (precinct, values) using column x positions.

    Out-of-county precinct rows continue on a second line holding only a
    parenthesized county name (e.g. "(Livingston)") with no numbers; merge
    those into the preceding precinct label.
    """
    rows = []
    for line in data_lines:
        precinct_words = []
        values = [None] * len(columns)
        for w in line:
            if NUMERIC.match(w['text']):
                for i, cx in enumerate(columns):
                    if abs(w['x0'] - cx) <= 8:
                        values[i] = int(w['text'].replace(',', ''))
                        break
                else:
                    precinct_words.append(w)
            else:
                precinct_words.append(w)
        if all(v is None for v in values):
            label = ' '.join(w['text'] for w in precinct_words)
            if rows:
                rows[-1] = (f'{rows[-1][0]} {label}', rows[-1][1])
            continue
        if any(v is None for v in values):
            raise ValueError(f'Missing cell in row: {precinct_words} {values}')
        rows.append((' '.join(w['text'] for w in precinct_words), values))
    return rows


def parse_headers(rotated_chars, columns):
    """Reconstruct the diagonal candidate names, one per column."""
    # The PDF draws each glyph twice at the same position; dedupe first.
    seen = set()
    unique = []
    for c in rotated_chars:
        key = (round(c['x0']), round(c['top']), c['text'])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    names = []
    for i, cx in enumerate(columns):
        chars = [
            c for c in unique
            if cx - 7 <= c['x0'] and (i == len(columns) - 1 or c['x0'] < columns[i + 1] - 7)
        ]
        lines = []  # [p, chars]
        for c in sorted(chars, key=lambda c: c['x0'] + SLOPE * c['top']):
            p = c['x0'] + SLOPE * c['top']
            if lines and abs(lines[-1][0] - p) <= 3:
                lines[-1][1].append(c)
            else:
                lines.append([p, [c]])
        text = ' '.join(
            re.sub(r'\s+', ' ', ''.join(ch['text'] for ch in sorted(cl, key=lambda ch: -ch['top']))).strip()
            for _, cl in lines
        )
        names.append(text)
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--county', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    contests = {}
    order = []
    with pdfplumber.open(args.pdf) as pdf:
        for page in pdf.pages:
            header, data_lines, contest_total, rotated = parse_page(page)
            if header is None or not data_lines:
                continue
            if header not in contests:
                contests[header] = {'rows': [], 'columns': None, 'total': None, 'rotated': [], 'pending': []}
                order.append(header)
            c = contests[header]
            c['rotated'].extend(rotated)
            if contest_total is not None:
                # Anchor the column positions on the Contest Total row, whose
                # numbers are guaranteed to be cells (unlike "Precinct 1" labels).
                total_values = [w for w in contest_total if NUMERIC.match(w['text'])]
                cols = detect_columns([w['x0'] for w in total_values])
                c['columns'] = cols
                c['total'] = [int(w['text'].replace(',', '')) for w in total_values]
                c['rows'].extend(parse_rows(c['pending'] + data_lines, cols))
                c['pending'] = []
            elif c['columns'] is not None:
                c['rows'].extend(parse_rows(data_lines, c['columns']))
            else:
                c['pending'].extend(data_lines)

    out_rows = []
    problems = []
    for header in order:
        c = contests[header]
        office, district, party = map_office(header)
        candidates = parse_headers(c['rotated'], c['columns'])
        ncols = len(c['columns'])
        if ncols != len(candidates):
            problems.append(f'{header}: {len(candidates)} names for {ncols} columns: {candidates}')
            continue
        name = lambda i: 'Write-In' if candidates[i].lower() == 'write-in' else candidates[i]
        # Validate column sums against the printed Contest Total row.
        if c['total']:
            sums = [sum(v[i] for _, v in c['rows']) for i in range(ncols)]
            if sums != c['total']:
                problems.append(f'{header}: column sums {sums} != Contest Total {c["total"]}')
        # Trailing 4 columns: Over Votes, Under Votes, Total Registered, Total Votes Cast.
        for precinct, values in c['rows']:
            cand_sum = sum(values[:ncols - 4])
            cast = values[ncols - 1] - values[ncols - 4] - values[ncols - 3]
            if cast != cand_sum:
                problems.append(f'{header} / {precinct}: candidate sum {cand_sum} != cast {cast}')
            for i in range(ncols - 4):
                out_rows.append([None, precinct, office, district, party, name(i), values[i]])
            out_rows.append([None, precinct, office, district, party, 'Ballots Cast', cast])
        if args.verbose:
            print(f'{header}: {len(c["rows"])} precincts, candidates {candidates}')
        print(f'{header}: {len(c["rows"])} rows, candidates {candidates}', file=sys.stderr)

    for row in out_rows:
        row[0] = args.county

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes'])
        w.writerows(out_rows)
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)


if __name__ == '__main__':
    import sys
    main()