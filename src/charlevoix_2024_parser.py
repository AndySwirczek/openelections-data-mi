"""Parse the Charlevoix County 2024 primary per-jurisdiction summary PDFs.

The county publishes one "Election Summary Report" PDF per jurisdiction
(each township/city is a single precinct, plus two countywide Early Voting
precincts). Each report lists the jurisdiction's contests one per block:

    <title> (DEM) (Vote for 1)      <- office contests carry a party tag
    DEM                             <- party line
    Total
    Times Cast 652 / 1,603 40.67%
    Candidate Party Total
    Hill Harper 10
    Total Votes 250
    Total
    Unresolved Write-In 0

Proposals have no party line and may wrap the title across two lines
("Charlevoix County Recycling Millage" / "(Vote for 1)"). Contests with no
candidates print only Total Votes (0) and Unresolved Write-In. Files without
a text layer are read from cached PaddleOCR markdown (src/fetch_paddleocr_md.py),
whose tables flatten into the same line grammar.

The countywide summary ("Election Charlevoix Aug 2024.pdf", All Counting
Groups) is parsed for cross-checks only: every contest's candidate/write-in/
Times Cast totals across all 22 jurisdiction files must match it.

Precinct labels reuse the committed 2024 general file's (official precinct
numbers). Early-voting center votes mix jurisdictions and are emitted under
pseudo-precincts named by the source.

Usage:
    .venv/bin/python src/charlevoix_2024_parser.py <sources_dir> \
        --out 2024/counties/20240806__mi__primary__charlevoix__precinct.csv
"""
import argparse
import csv
import html
import os
import re
import sys

import pdfplumber

CACHE = '/tmp/paddleocr_md'
COUNTY = 'Charlevoix'
HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
          'votes']

# filename -> precinct label (from the committed 2024 general file).
PRECINCTS = {
    'Bay Twp Aug 2024.pdf': 'Bay Township, Precinct 1',
    'Boyne Valley Twp Aug 2024.pdf': 'Boyne Valley Township, Precinct 2',
    'Chandler Twp Aug 2024.pdf': 'Chandler Township, Precinct 3',
    'Charlevoix Twp Aug 2024.pdf': 'Charlevoix Township, Precinct 4',
    'Evangeline Twp Aug 2024.pdf': 'Evangeline Township, Precinct 5',
    'Eveline Twp Aug 2024.pdf': 'Eveline Township, Precinct 6',
    'Hayes Twp Aug 2024.pdf': 'Hayes Township, Precinct 7',
    'Hudson Twp Aug 2024.pdf': 'Hudson Township, Precinct 8',
    'Marion Twp Aug 2024.pdf': 'Marion Township, Precinct 9',
    'Melrose Twp Aug 2024.pdf': 'Melrose Township, Precinct 10',
    'Norwood Twp Aug 2024.pdf': 'Norwood Township, Precinct 11',
    'Peaine Twp Aug 2024.pdf': 'Peaine Township, Precinct 12',
    'St. James Twp Aug 2024.pdf': 'St. James Township, Precinct 13',
    'South Arm Twp Aug 2024.pdf': 'South Arm Township, Precinct 14',
    'Wilson Twp Aug 2024.pdf': 'Wilson Township, Precinct 15',
    'City of Boyne City Aug 2024.pdf': 'City of Boyne City, Precinct 16',
    'City of Charlevoix Ward 1 Aug 2024.pdf':
        'City of Charlevoix, Ward 1, Precinct 18',
    'City of Charlevoix Ward 2 Aug 2024.pdf':
        'City of Charlevoix, Ward 2, Precinct 19',
    'City of Charlevoix Ward 3 Aug 2024.pdf':
        'City of Charlevoix, Ward 3, Precinct 20',
    'City of East Jordan Aug 2024.pdf': 'City of East Jordan, Precinct 21',
    'Early Voting Precinct 1 Aug 2024.pdf': 'Early Voting Precinct 1',
    'Early Voting Precinct 2 Aug 2024.pdf': 'Early Voting Precinct 2',
}
SUMMARY = 'Election Charlevoix Aug 2024.pdf'

TITLE_TAG = re.compile(r'^(.*?) \((\w+)\) \((Vote for \d+)\)$')
# OCR sometimes merges the following party line into the title's div.
TITLE_TAG_OCR = re.compile(r'^(.*?) \((\w+)\) \((Vote for \d+)\) (\w+)$')
VOTE_FOR = re.compile(r'\(Vote for \d+\)$')
TIMES = re.compile(r'^Times Cast ([\d,]+) / ([\d,]+)(?: \d+\.\d+%)?$')
# District offices (title -> office, district group).
DISTRICT = re.compile(r'^Representative in (Congress|State Legislature) '
                      r'(\d+)(?:st|nd|rd|th) District$')
OFFICE_EXACT = {'United States Senator': 'U.S. Senate'}
PARTY_LINES = {'DEM', 'REP', 'LIB', 'UST', 'GRN', 'NLP', 'WCP', 'NPA', 'PRO'}
NOISE = re.compile(r'^(Page: \d+ of \d+|Election Summary Report|Open Primary'
                   r'|Charlevoix County, Michigan|August 06, 2024|Summary for:'
                   r'|Groups|Voters Cast:|Candidate Party Total'
                   # OCR splits the column header into fragments.
                   r'|Party Total|Candidate Total|\.? ?Total ?\.?$'
                   r'|Ward \d+ ICP, All Counting Groups)')
# OCR garbles of the Unresolved Write-In line.
GARBLES = [
    (re.compile(r'^Unresolved Write-In Total$'), 'Unresolved Write-In 0'),
    (re.compile(r'^Unresolved Writ\w+ (\d[\d,]*)$'), r'Unresolved Write-In \1'),
    (re.compile(r'^Harassed Write In (\d[\d,]*)$'), r'Unresolved Write-In \1'),
]


def native_lines(path):
    """Lines of a PDF with a text layer."""
    with pdfplumber.open(path) as pdf:
        return [l.strip() for page in pdf.pages
                for l in (page.extract_text() or '').splitlines() if l.strip()]


def ocr_lines(path):
    """Lines of an image-only PDF from cached per-page PaddleOCR markdown.

    PaddleOCR-VL renders tables as single-line HTML (<table border=1 ...>)
    and titles as <div> lines. Table rows flatten into the same line grammar
    (cells joined with spaces: 'Times Cast | 74 / 256 | 28.91%' ->
    'Times Cast 74 / 256 28.91%'); divs and headings are tag-stripped.
    """
    # fetch_paddleocr_md.py names caches with the same sanitize.
    stem = re.sub(r'[^A-Za-z0-9]+', '_',
                  os.path.splitext(os.path.basename(path))[0])
    cache_dir = os.path.join(CACHE, stem)
    pages = sorted(f for f in os.listdir(cache_dir) if f.endswith('.md')) \
        if os.path.isdir(cache_dir) else []
    if not pages:
        return None
    lines = []
    for f in pages:
        for raw in open(os.path.join(cache_dir, f)):
            line = raw.strip().lstrip('#').strip()
            if not line or line.startswith('```') or line == '---':
                continue
            if '<table' in line:
                for table in re.findall(r'<table.*?</table>', line, re.S):
                    for row in re.findall(r'<tr.*?</tr>', table, re.S):
                        cells = [html.unescape(
                                     re.sub(r'<[^>]+>', '', c)).strip()
                                 for c in re.findall(r'<td[^>]*>(.*?)</td>',
                                                     row, re.S)]
                        line = ' '.join(c for c in cells if c)
                        if line:
                            lines.append(line)
                continue
            line = re.sub(r'<[^>]+>', '', line)
            line = html.unescape(line).strip()
            if line:
                lines.append(line)
    return lines


def parse(lines, problems, where):
    """State machine over one report's lines -> {title: contest dict}."""
    contests = {}
    cur = None       # {'title','party','ballots','registered','rows','wi'}
    pending = []     # wrapped title lines

    def start(title, tag, vote_for):
        # OCR reorders ", Precinct N, Ward M" in delegate titles.
        title = re.sub(r', Precinct (\d+), Ward (\d+)',
                       lambda mm: f', Ward {mm.group(2)}, '
                                  f'Precinct {mm.group(1)}', title)
        # Delegate contests print once per party under the same title.
        key = f'{title}|{tag}'
        if key in contests:
            problems.append(f'{where}: duplicate contest {title!r} ({tag})')
        contests[key] = {'key': key, 'title': title, 'tag': tag,
                         'vote_for': vote_for,
                         'party': tag if tag in PARTY_LINES else '',
                         'ballots': None, 'registered': None,
                         'rows': [], 'total': None, 'wi': None}
        return contests[key]

    for raw_line in lines:
        line = raw_line
        for pat, repl in GARBLES:
            line = pat.sub(repl, line)
        # OCR sometimes prints a bare "Unresolved Write-In" with the count
        # dropped; the county-summary cross-check verifies the value.
        if line == 'Unresolved Write-In':
            if cur is not None:
                if cur['wi'] is None:
                    cur['wi'] = 0
                contests[cur['key']] = cur
                cur = None
                pending = []
            continue
        if NOISE.match(line):
            pending = []
            continue
        # OCR drops the "Times Cast" label cell from the row.
        m = re.match(r'^([\d,]+) / ([\d,]+)(?: \d+\.\d+%)?$', line)
        if m and cur is not None:
            cur['ballots'] = int(m.group(1).replace(',', ''))
            cur['registered'] = int(m.group(2).replace(',', ''))
            continue
        if line in PARTY_LINES and cur is not None:
            # The party line repeats the title tag; flag a mismatch.
            if cur['party'] and cur['party'] != line:
                problems.append(f'{where}: contest {cur["title"]!r} party '
                                f'line {line} != title tag {cur["party"]}')
            cur['party'] = cur['party'] or line
            continue
        m = TIMES.match(line)
        if m:
            if cur is not None:
                # Countywide header turnout lines print outside any contest.
                cur['ballots'] = int(m.group(1).replace(',', ''))
                cur['registered'] = int(m.group(2).replace(',', ''))
            continue
        m = re.match(r'^Total Votes ([\d,]+)$', line)
        if m and cur is not None:
            cur['total'] = int(m.group(1).replace(',', ''))
            continue
        m = re.match(r'^Unresolved Write-?In ([\d,]+)$', line, re.I)
        if m and cur is not None:
            cur['wi'] = int(m.group(1).replace(',', ''))
            contests[cur['key']] = cur
            cur = None
            pending = []
            continue
        if line == 'Total':
            continue
        m = re.match(r'^(.+?) ([\d,]+)$', line)
        if m and cur is not None:
            # Named write-in candidates print as "<name> WRITE-IN <votes>"
            # and are counted in Total Votes.
            cur['rows'].append((m.group(1).strip(),
                                int(m.group(2).replace(',', ''))))
            pending = []
            continue
        # Title lines: a "(Vote for N)" token ends the title.
        m = TITLE_TAG.match(line)
        if not m:
            m = TITLE_TAG_OCR.match(line)
        if m:
            if cur is not None:
                problems.append(f'{where}: contest {cur["title"]!r} never '
                                f'closed')
            if pending:
                problems.append(f'{where}: unwrapped title lines {pending}')
            cur = start(m.group(1).strip(), m.group(2), m.group(3))
            pending = []
            continue
        if VOTE_FOR.search(line) or re.match(r'^for \d+\)$', line):
            if cur is not None:
                problems.append(f'{where}: contest {cur["title"]!r} never '
                                f'closed')
            if re.match(r'^for \d+\)$', line):
                # "(Vote" ended the previous line and "for 1)" wrapped; the
                # token is kept in vote_for, not in the title.
                head = re.sub(r'\s*\(Vote$', '', ' '.join(pending)).strip()
                title, vote_for = head, '(Vote for 1)'
            else:
                title = VOTE_FOR.sub('', ' '.join(pending + [line])).strip()
                vote_for = VOTE_FOR.search(line).group(0)
            cur = start(title, '', vote_for)
            pending = []
            continue
        if cur is None:
            # Stray OCR digits between contests must not join the next title.
            if re.match(r'^\d[\d,]*$', line):
                continue
            pending.append(line)
        else:
            problems.append(f'{where}: unexpected line in contest '
                            f'{cur["title"]!r}: {line!r}')
            pending = []
    if cur is not None:
        problems.append(f'{where}: contest {cur["title"]!r} never closed')
    if pending:
        problems.append(f'{where}: trailing title lines {pending}')
    return contests


def label_tokens(label):
    """'City of Charlevoix, Ward 2, Precinct 19' -> (jurisdiction, tokens)."""
    parts = [s.strip() for s in label.split(',')]
    toks = {}
    for s in parts[1:]:
        m = re.match(r'^(Precinct|Ward) (\d+)$', s)
        if m:
            toks[m.group(1)] = int(m.group(2))
    return parts[0], toks


JUR_LABELS = {}      # jurisdiction -> [labels]
for _label in PRECINCTS.values():
    JUR_LABELS.setdefault(label_tokens(_label)[0], []).append(_label)


def attribute(title, file_precinct, problems, where):
    """Precinct label for a contest titled `title` in `file_precinct`'s file.

    The early-voting reports cover every jurisdiction, and their
    township-specific contests (delegates, township offices) print the
    owning jurisdiction in the title — those votes belong to that
    jurisdiction's precinct, not the early-voting pseudo-precinct.
    """
    m = re.match(r'^(.+?) Delegate$', title)
    if m:
        jur, toks = label_tokens(m.group(1))
        for label in JUR_LABELS.get(jur, []):
            if label_tokens(label)[1] == toks:
                return label
        problems.append(f'{where}: delegate title {title!r} matches no '
                        f'precinct')
        return file_precinct
    # A jurisdiction-prefixed office ("<jurisdiction> <office>").
    for jur in sorted(JUR_LABELS, key=len, reverse=True):
        if title.startswith(jur + ' '):
            labels = JUR_LABELS[jur]
            if len(labels) == 1:
                return labels[0]
            mt = re.search(r'\bWard (\d+)\b', title)
            if mt:
                want = [l for l in labels
                        if label_tokens(l)[1].get('Ward') == int(mt.group(1))]
                if len(want) == 1:
                    return want[0]
            problems.append(f'{where}: {title!r} matches no single precinct '
                            f'of {jur}')
            return file_precinct
    return file_precinct


def map_office(title, problems):
    office, district = title, ''
    m = DISTRICT.match(title)
    if m:
        office = 'U.S. House' if m.group(1) == 'Congress' else 'State House'
        district = m.group(2)
    elif title in OFFICE_EXACT:
        office = OFFICE_EXACT[title]
    return office, district


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sources_dir')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    problems = []
    out_rows = []
    all_contests = {}   # title|tag -> [(precinct, contest dict)]
    files = []
    for root, _, names in os.walk(args.sources_dir):
        for name in sorted(names):
            if name in PRECINCTS or name == SUMMARY:
                files.append(os.path.join(root, name))
    if not any(f.endswith(SUMMARY) for f in files):
        sys.exit(f'{SUMMARY} not found under {args.sources_dir}')

    for path in files:
        name = os.path.basename(path)
        lines = native_lines(path)
        if not lines:
            lines = ocr_lines(path) or []
            if not lines:
                problems.append(f'{name}: no text and no OCR cache')
                continue
        contests = parse(lines, problems, name)
        for title, c in contests.items():
            all_contests.setdefault(title, []).append(
                (PRECINCTS.get(name, name), c))

    summary = {}
    for title, entries in all_contests.items():
        for prec, c in entries:
            if prec == SUMMARY:
                summary[title] = c

    # Cross-check every contest against the countywide summary.
    for title in sorted(all_contests):
        entries = all_contests[title]
        parts = [(p, c) for p, c in entries if p != SUMMARY]
        s = summary.get(title)
        for field in ('total', 'wi', 'ballots'):
            if s is None:
                problems.append(f'{title}: no county summary entry')
                break
            got = sum(c[field] for _, c in parts
                      if c[field] is not None)
            missing = [p for p, c in parts if c[field] is None]
            if missing:
                problems.append(f'{title}: missing {field} in {missing}')
            if len(missing) < len(parts) and got != s[field]:
                problems.append(f'{title}: files sum {field} {got} != '
                                f'county summary {s[field]}')
        # Per-file internal checks.
        for p, c in parts:
            if c['wi'] is None or c['total'] is None or c['ballots'] is None:
                problems.append(f'{title} / {p}: incomplete contest '
                                f'{c}')
                continue
            # Named rows (including "<name> WRITE-IN") sum to Total Votes;
            # Unresolved Write-In is counted outside it.
            cand = sum(v for _, v in c['rows'])
            if cand != c['total']:
                problems.append(f'{title} / {p}: candidates {cand} != '
                                f'Total Votes {c["total"]}')
            if c['ballots'] is not None and c['total'] is not None \
                    and c['vote_for'] == '(Vote for 1)' \
                    and c['total'] > c['ballots']:
                problems.append(f'{title} / {p}: Total Votes {c["total"]} > '
                                f'Times Cast {c["ballots"]}')

    # Aggregate votes across files (a precinct's candidate total can come
    # from its own report plus both early-voting reports).
    votes_agg = {}   # (precinct, office, district, party, candidate) -> votes
    bc_rows = {}     # (precinct, office, district, party, contest) -> ballots
    for title in sorted(all_contests):
        for prec, c in all_contests[title]:
            if prec == SUMMARY:
                continue
            label = attribute(c['title'], prec, problems, prec)
            office, district = map_office(c['title'], problems)
            for name, v in c['rows']:
                # "X WRITE-IN" rows are qualified write-in candidates; keep
                # the printed name without the marker.
                name = re.sub(r'\s*WRITE-?IN\s*$', '', name, flags=re.I)
                if not v:
                    continue  # zero rows are not emitted
                key = (label, office, district, c['party'], name)
                votes_agg[key] = votes_agg.get(key, 0) + v
            if c['wi']:
                key = (label, office, district, c['party'], 'Write-In')
                votes_agg[key] = votes_agg.get(key, 0) + c['wi']
            if c['ballots']:
                # Times Cast is per file (each report's own counting groups).
                key = (prec, office, district, c['party'], title)
                bc_rows[key] = c['ballots']
    for (prec, office, district, party, name), votes in sorted(votes_agg.items()):
        out_rows.append([COUNTY, prec, office, district, party, name, votes])
    for (prec, office, district, party, title), ballots in sorted(bc_rows.items()):
        out_rows.append([COUNTY, prec, office, district, party,
                         'Ballots Cast', ballots])

    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(out_rows)
    print(f'Wrote {len(out_rows)} rows to {args.out}')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()