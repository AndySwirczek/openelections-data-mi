"""Parse Allegan County's Aug 2022 primary results PDF into a per-county
precinct CSV, verified against the certified county-level CENR.

Source (openelections-sources-mi/2022/primary/): 'Allegan MI Results per
Precinct Offi.pdf' — a 26-page text PDF of contest blocks: a title line
('... (DEM) (Vote for 1)'), a 'Precinct' / wrapped-candidate-names /
'Write-in' header (continuation pages repeat it), one data row per precinct
and a closing 'Total' row — the Branch layout, but blocks span page breaks
and the report right-aligns values under left-aligned names, so header x
positions are poor anchors. Columns are therefore the clusters of the
block's own value x centers (right-aligned numbers cluster tightly), named
left-to-right from the header's candidate clusters when the counts match
(wrapped names merge by x overlap); the printed Total row cross-checks every
column's sum. Titles not in the CENR-carried offices (county/local,
delegates, proposals) are skipped. Zero-vote cells are omitted by the
source. Labels keep the general file's comma style ('Alamo Township,
Precinct 1-O (Kalamazoo)').

Only the contests the CENR certifies are emitted (Governor, U.S. House 4,
State Senate 18/20/31, State House 38/39/42/43/79/86).

Usage: .venv/bin/python src/primary_2022_allegan.py [--apply]
"""
import re
import sys
from collections import defaultdict

import pdfplumber

from primary_2022_common import CENR, one_space, verify, write

COUNTY = 'Allegan'
SOURCE = ("/Users/dwillis/code/openelections-sources-mi/2022/primary/"
          "Allegan MI Results per Precinct Offi.pdf")

# contest title -> (office, district, party)
CONTESTS = [
    (re.compile(r'^Governor \((DEM|REP)\) \(Vote for \d+\)$'),
     lambda m: ('Governor', '', m.group(1))),
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th)? District'
                r' \((DEM|REP)\) \(Vote for \d+\)$'),
     lambda m: ('U.S. House', m.group(1), m.group(2))),
    (re.compile(r'^State Senator (\d+)(?:st|nd|rd|th)? District'
                r' \((DEM|REP)\) \(Vote for \d+\)$'),
     lambda m: ('State Senate', m.group(1), m.group(2))),
    (re.compile(r'^Rep(?:resentative)? in State Legislature'
                r' (\d+)(?:st|nd|rd|th)? District'
                r' \((DEM|REP)\) \(Vote for \d+\)$'),
     lambda m: ('State House', m.group(1), m.group(2))),
]

NUM = re.compile(r'\d[\d,]*')


def lines_of(page):
    lines = {}
    for w in page.extract_words(x_tolerance=1.5):
        lines.setdefault(round(w['top']), []).append(w)
    return [(top, sorted(ws, key=lambda w: w['x0']))
            for top, ws in sorted(lines.items())]


def header_names(header_lines):
    """Candidate clusters [(x0, x1, name)] from the header words. Words on
    one line join by proximity (a name's words sit ~2pt apart; the next
    column ~6pt over); a wrapped fragment then joins the cluster it
    overlaps most — the report stacks wrapped names left-aligned, so real
    x overlap (no slack: 'Write-in' sits ~4pt right of 'Victory') is
    required."""
    line_clusters = []
    for top, ws in header_lines:
        cur = None
        for w in sorted(ws, key=lambda w: w['x0']):
            if w['text'] == 'Precinct':
                continue
            if cur and w['x0'] - cur['x1'] <= 4:
                cur['words'].append(w)
                cur['x1'] = max(cur['x1'], w['x1'])
            else:
                cur = {'top': top, 'x0': w['x0'], 'x1': w['x1'],
                       'words': [w]}
                line_clusters.append(cur)
    clusters = []
    for cl in sorted(line_clusters, key=lambda c: (c['top'], c['x0'])):
        best, best_ov = None, 0
        for t in clusters:
            ov = min(cl['x1'], t['x1']) - max(cl['x0'], t['x0'])
            if ov > best_ov:
                best, best_ov = t, ov
        if best is None or best['top'] == cl['top']:
            clusters.append(cl)
        else:
            best['words'] += cl['words']
            best['x0'] = min(best['x0'], cl['x0'])
            best['x1'] = max(best['x1'], cl['x1'])
    out = []
    for cl in sorted(clusters, key=lambda c: c['x0']):
        name = one_space(' '.join(w['text'] for w in
                                  sorted(cl['words'],
                                         key=lambda w: (w['top'], w['x0']))))
        out.append((cl['x0'], cl['x1'], name))
    return out


# names the report splits across header lines / spells differently from
# the certified CENR
CAND_FIX = {'Kai W. De Graaf': 'Kai W. Degraaf',
            'Kimberly Y. Kennedy- Barrington':
                'Kimberly Y. Kennedy-Barrington',
            'Nancy DeBoer': 'Nancy De Boer'}


def cluster(xs, gap=25):
    """Sorted x centers -> [[cluster centers]] split where neighbors are
    farther than `gap` apart."""
    out = []
    for x in sorted(xs):
        if out and x - out[-1][-1] <= gap:
            out[-1].append(x)
        else:
            out.append([x])
    return [sum(g) / len(g) for g in out]


def parse():
    rows = []
    skipped = defaultdict(int)
    problems = []
    write_ins = defaultdict(int)
    contest = None
    header = []              # header lines awaiting the first data row
    data = []                # buffered (label, [(x_center, value)...])

    def close_block(page_no):
        """cluster value columns, name them, cross-check the Total row"""
        nonlocal contest, header, data
        try:
            if contest is not None and data:
                if not data[-1][0].startswith('Total'):
                    problems.append(f'page {page_no} {contest}: block ends '
                                    f'without a Total row')
                    return
                anchors = cluster([x for _, vals in data
                                   for x, _ in vals])
                printed = cluster([x for x, _ in data[-1][1]])
                if len(anchors) != len(printed):
                    problems.append(f'page {page_no} {contest}: '
                                    f'{len(anchors)} value columns vs '
                                    f'{len(printed)} printed totals')
                    return
                names = header_names(header)
                # an all-zero write-in column prints no values
                if len(anchors) == len(names) - 1 and sum(
                        1 for n in names
                        if re.fullmatch(r'Write-?in', n[2], re.I)) == 1:
                    names = [n for n in names
                             if not re.fullmatch(r'Write-?in', n[2], re.I)]
                if len(anchors) != len(names):
                    # the report sometimes omits the candidate name line
                    # entirely (U.S. House 4 DEM prints only 'Write-in'):
                    # fall back to the CENR's sole candidate
                    cands = [c for (o, d, p, c) in CENR[COUNTY]
                             if (o, d, p) == contest and c != 'Write-In']
                    if len(anchors) == 1 and len(names) == 1 \
                            and re.fullmatch(r'Write-?in', names[0][2], re.I) \
                            and len(cands) == 1:
                        names = [(0, 0, cands[0])]
                    else:
                        problems.append(f'page {page_no} {contest}: '
                                        f'{len(anchors)} value columns vs '
                                        f'header {[(n[2]) for n in names]}')
                        return
                sums = defaultdict(int)
                for label, vals in data[:-1]:
                    for x, v in vals:
                        col = min(range(len(anchors)),
                                  key=lambda i: abs(anchors[i] - x))
                        if abs(anchors[col] - x) > 15:
                            problems.append(f'page {page_no} {label!r}: '
                                            f'value at x={x} off-column')
                            continue
                        sums[col] += v
                for col, (hx0, hx1, name) in enumerate(names):
                    got = sums[col]
                    want = next(v for x, v in data[-1][1]
                                if abs(x - printed[col]) < 5)
                    if got != want:
                        problems.append(f'{contest} {name!r}: precinct sum '
                                        f'{got} != Total {want}')
                        continue
                    if re.fullmatch(r'Write-?in', name, re.I):
                        name = 'Write-In'
                        if got:
                            write_ins[contest] += got
                            for label, vals in data[:-1]:
                                for x, v in vals:
                                    if abs(anchors[col] - x) <= 15 and v:
                                        rows.append((label, contest[0],
                                                     contest[1], contest[2],
                                                     'Write-In', v))
                        continue
                    for label, vals in data[:-1]:
                        for x, v in vals:
                            if abs(anchors[col] - x) <= 15 and v:
                                rows.append((label, contest[0], contest[1],
                                             contest[2],
                                             CAND_FIX.get(name, name), v))
        finally:
            contest, header, data = None, [], []

    with pdfplumber.open(SOURCE) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            for top, ws in lines_of(page):
                text = one_space(' '.join(w['text'] for w in ws))
                nums = [w for w in ws if NUM.fullmatch(w['text'])]
                if 'Vote for' in text:
                    close_block(page_no)
                    for pat, fn in CONTESTS:
                        m = pat.match(text)
                        if m:
                            contest = fn(m)
                            break
                    continue
                if contest is None:
                    continue
                if not nums:
                    if not data:
                        # header lines (first or continuation page)
                        header.append((top, ws))
                    continue
                # the report leaves a wide gap (~50pt) between the label
                # — which itself ends in a number ('... Precinct 1') — and
                # the right-aligned value columns; column x varies by page
                words = sorted(ws, key=lambda w: w['x0'])
                split = 1
                while split < len(words) \
                        and words[split]['x0'] - words[split - 1]['x1'] <= 25:
                    split += 1
                label = one_space(' '.join(w['text'] for w in words[:split]))
                values = [(round((w['x0'] + w['x1']) / 2, 1),
                           int(w['text'].replace(',', '')))
                          for w in words[split:] if NUM.fullmatch(w['text'])]
                data.append(('Total' if label.startswith('Total')
                             else label, values))
        close_block(len(pdf.pages))
    return rows, skipped, write_ins, problems


def main(apply=False):
    rows, skipped, write_ins, problems = parse()
    print(f'{COUNTY}: {len(rows)} rows; {sum(skipped.values())} rows skipped')
    problems += verify(COUNTY, rows, write_ins)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(f'{len(problems)} problems; not writing')
    if apply:
        write(COUNTY, rows)


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)