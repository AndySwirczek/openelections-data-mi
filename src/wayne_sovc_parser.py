"""Parse the Wayne County 2024 primary per-contest ES&S SOVC PDF extracts.

Layout: each PDF covers one office group (state/federal, county, township,
proposals, judicial) as a set of contests. A contest starts with a title line
("<office> (DEM) (Vote for N)"), an optional party line, and page pairs whose
left table is a turnout ("Times Cast" / "Registered Voters") count per
precinct and whose right table is the results: rotated candidate headers
("repraH"/"lliH"/")MED(" -> "Hill Harper (DEM)") plus, on the contest's last
page pair, "Write-In" and "Total Votes" columns. Unlike Lenawee's SOVC, each
precinct prints a single row per candidate — "votes pct%" pairs, no
vote-method breakdown (the county file's breakdown columns stay blank) —
and precinct labels wrap across lines ("Canton Township, Precinct" / "21"),
sometimes with the prefix on the line before the data ("Charter Township of"
/ "<data>" / "Brownstown, Precinct 1"). A stray county-boundary token
("MACOMB") trails the last precinct row of district-wide contests; no
non-Wayne precinct rows are present. "Cumulative" blocks and a
"Michigan - top District -" footer close each table; "County - Total" rows
give the countywide column totals used for cross-checks.

Because candidate columns spill across page pairs, results accumulate per
precinct across pages; the contest's sum-of-candidates == Total Votes check
only closes when the last page pair adds the Total Votes column.

Usage:
    .venv/bin/python src/wayne_sovc_parser.py <sources_dir> \
        --out 2024/counties/20240806__mi__primary__wayne__precinct.csv
"""
import argparse
import csv
import os
import re
import sys

import pdfplumber

# PDFs in ballot order. statsa24 (turnout statistics) and officialaug24_sum
# (countywide summary) carry no precinct results; pdd8624/pdr8624 (precinct
# delegate rosters with candidate addresses) are parsed by
# parse_delegate_rosters().
PDFS = ['sena24.pdf', 'cona24.pdf', 'strepa24.pdf', 'cty24.pdf',
        'coma24.pdf', 'twpsa24.pdf', 'prpa24.pdf', 'cira24.pdf',
        '4ja24_(1).pdf', 'eco.pdf']
DELEGATE_PDFS = {'pdd8624.pdf': 'DEM', 'pdr8624.pdf': 'REP'}

TITLE = re.compile(r'^(.*?)\s*(?:\((DEM|REP)\)\s*)?\(Vote for \d+\)$')
PCT = re.compile(r'^\d+\.\d\d%$')
# The footer prints "Michigan - top District -" (trailing dash); the same
# words also appear without the dash as the label column's stacked header
# ("Precinct / County / Michigan - top District"), which must not close the
# page's data.
FOOTER = re.compile(r'^(Michigan - top District -|Cumulative|County - Total)')
COMPLETE = re.compile(r', .*Precinct \d+[A-Z]?$')
# Wrapped labels whose pieces print in reverse order: the precinct-number
# line ("Precinct 4", or a bare "3") above the jurisdiction line
# ("Van Buren Township,"). Reassembled jurisdiction-first.
NUMBER_FIRST = re.compile(r'^(Precinct (\d+[A-Z]?)|(\d+[A-Z]?)) (.+)$')


def reorder_number_first(label):
    """'Precinct 4 City of Inkster, District 6' ->
    'City of Inkster, District 6 Precinct 4' (and bare-number variants)."""
    m = NUMBER_FIRST.match(label)
    if not m:
        return None
    num, rest = (m.group(2) or m.group(3)), m.group(4).strip()
    if rest.endswith(','):
        return f'{rest[:-1]}, Precinct {num}'
    if rest.endswith('Precinct'):
        return f'{rest} {num}'
    return f'{rest} Precinct {num}'
# County-boundary markers the district-wide report leaves in the table.
COUNTY_TOKENS = {'MACOMB', 'OAKLAND', 'WAYNE', 'MONROE', 'WASHTENAW'}
TC_NAMES = ('Times Cast', 'Voters Registered', 'Registered Voters')
PARTY_TAG = re.compile(r' \((DEM|REP|NON|LIB|GRN|UST|NPA)\)$')
DISTRICT = re.compile(
    r'^Representative in (Congress|State Legislature) for '
    r'(\d+)(?:st|nd|rd|th) District$')


def header_columns(page, x_min, x_max):
    """{x_right: name} from a page's rotated headers within an x range.

    Rotated header text reads bottom-to-top; a name too tall for the column
    wraps onto further vertical lines ~10pt to its right (each wrap segment
    its own rotated line, read left to right, hyphenated at the break:
    'DeArtriss'/'Coleman-'/'Richardson'/'(DEM)'). So first group words into
    vertical lines on shared x0, then merge consecutive lines whose x0 gap is
    small (columns themselves sit 60+ points apart) into one header,
    dehyphenating a line that ends '-'.
    """
    words = [w for w in page.extract_words()
             if not w.get('upright', True)
             and x_min <= w['x0'] < x_max]
    words.sort(key=lambda w: (w['x0'], -w['top']))
    vlines = []  # [x0, words of one vertical line]
    for w in words:
        if vlines and abs(w['x0'] - vlines[-1][0]) < 3:
            vlines[-1][1].append(w)
        else:
            vlines.append([w['x0'], [w]])
    cols = []  # [first-line x0, all words, header text]
    prev_x0 = None
    for x0, group in vlines:
        # Reversed words stacked bottom-to-top read in reverse vertical order.
        text = ' '.join(v['text'][::-1]
                        for v in sorted(group, key=lambda v: -v['top']))
        if cols and prev_x0 is not None and x0 - prev_x0 < 30:
            prev = cols[-1][2]
            sep = '' if prev.endswith('-') else ' '
            cols[-1][2] = prev + sep + text
            cols[-1][1].extend(group)
        else:
            cols.append([x0, list(group), text])
        prev_x0 = x0
    out = {}
    for x0, group, text in cols:
        out[sum(w['x1'] for w in group) / len(group)] = text
    return out


def nearest(columns, xc):
    key = min(columns, key=lambda k: abs(k - xc))
    # Values right-align to their column but rotated headers end short of the
    # value column (up to ~15pt); columns sit 60+ points apart.
    return key if abs(key - xc) < 22 else None


def region_lines(page, x_min, x_max):
    """Visual lines of one table region, each a list of words."""
    all_words = page.extract_words()
    # Data starts below the rotated column headers, whose stacked words can
    # reach several tens of points down (and whose header band differs
    # between PDFs), so the cutoff tracks the deepest rotated word.
    rot = [w['top'] for w in all_words if not w.get('upright', True)]
    limit = max(rot) + 5 if rot else 60
    words = [w for w in all_words
             if w.get('upright', True) and x_min <= w['x0'] < x_max
             and w['top'] > limit]
    lines = {}
    for w in words:
        lines.setdefault(round(w['top'] / 3), []).append(w)
    return [sorted(lines[k], key=lambda w: w['x0']) for k in sorted(lines)]


def map_office(office):
    """Printed title -> (office, district)."""
    dm = DISTRICT.match(office)
    if dm:
        return ('U.S. House' if dm.group(1) == 'Congress' else 'State House'), \
            dm.group(2)
    # "Supervisor for Charter Township of Brownstown" -> jurisdiction-first.
    m = re.match(r'^(\S.+?) for (.+)$', office)
    if m and m.group(1) in ('Supervisor', 'Clerk', 'Treasurer', 'Trustee'):
        return f'{m.group(2)} {m.group(1)}', ''
    return office, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sources_dir')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    problems = []
    out_rows = []
    contest = None

    def close_contest():
        nonlocal contest
        if contest is None:
            return
        # County - Total footers vs per-precinct sums, per column.
        for col, total in contest['county_totals'].items():
            got, ok = 0, True
            for p, cols in contest['results'].items():
                v = cols.get(col, {}).get('votes')
                if v is None:
                    ok = False
                    break
                got += v
            if ok and got != total:
                problems.append(f'{contest["office"]} / {col}: precinct sum '
                                f'{got} != County - Total {total}')
        for col, total in contest['tc_totals'].items():
            idx = 0 if col == 'Times Cast' else 1
            got, ok = 0, True
            for p, tc in contest['tc'].items():
                v = tc.get(idx)
                if v is None:
                    ok = False
                    break
                got += v
            if ok and got != total:
                problems.append(f'{contest["office"]} / {col}: precinct sum '
                                f'{got} != County - Total {total}')
        # Per precinct: candidates + write-ins == Total Votes, and the
        # printed percentages agree with the counts.
        for p, cols in contest['results'].items():
            tv = cols.get('Total Votes', {}).get('votes')
            missing = [c for c, v in cols.items() if 'votes' not in v]
            if missing:
                problems.append(f'{contest["office"]} / {p}: columns missing '
                                f'a row: {missing}')
            if tv is None:
                continue
            cand = sum(v.get('votes', 0) for c, v in cols.items()
                       if c != 'Total Votes')
            if cand != tv:
                problems.append(f'{contest["office"]} / {p}: candidates '
                                f'{cand} != Total Votes {tv}')
            for c, v in cols.items():
                if c == 'Total Votes':
                    continue
                votes, pct = v.get('votes'), v.get('pct')
                if votes is not None and pct is not None and tv:
                    if abs(votes / tv * 100 - pct) > 0.6:
                        problems.append(f'{contest["office"]} / {p} / {c}: '
                                        f'{votes}/{tv} != {pct}%')
            tc = contest['tc'].get(p, {}).get(0)
            if tc is not None and tv is not None and tv > tc \
                    and contest['vote_for'] == 1:
                problems.append(f'{contest["office"]} / {p}: Total Votes '
                                f'{tv} > Times Cast {tc}')
        # Emit rows in the source's precinct order.
        for p in contest['precincts']:
            tc = contest['tc'].get(p, {}).get(0)
            if tc:
                out_rows.append([contest['name'], p, contest['office'],
                                 contest['district'], contest['party'],
                                 'Ballots Cast', tc, '', '', ''])
            for col, v in contest['results'].get(p, {}).items():
                if col == 'Total Votes':
                    continue
                name = 'Write-In' if col.lower() == 'write-in' else col
                # Rotated headers carry a party tag ("Hill Harper (DEM)"),
                # joined in whatever word order the column stack prints it;
                # the contest's party column already carries it.
                tagm = re.search(r'\((DEM|REP|NON|LIB|GRN|UST|NPA)\)', name)
                if tagm:
                    # "NON" headers are nonpartisan contests, whose party
                    # column is blank.
                    tag_party = {'NON': '', 'NPA': ''}.get(tagm.group(1),
                                                           tagm.group(1))
                    if tag_party != contest['party']:
                        problems.append(
                            f'{contest["office"]} / {p} / {name}: header tag '
                            f'{tagm.group(1)} != contest party '
                            f'{contest["party"]!r}')
                    name = re.sub(r'\s+', ' ',
                                  (name[:tagm.start()] + name[tagm.end():])
                                  .strip())
                votes = v.get('votes')
                if not votes:
                    continue  # zero or suppressed counts are not emitted
                out_rows.append([contest['name'], p, contest['office'],
                                 contest['district'], contest['party'], name,
                                 votes, '', '', ''])
        contest = None  # emitted; never emit the same contest twice

    for fname in PDFS:
        path = os.path.join(args.sources_dir, fname)
        close_contest()  # a contest never spans two source PDFs
        with pdfplumber.open(path) as pdf:
            for pno, page in enumerate(pdf.pages, 1):
                tag = f'{fname} p{pno}'
                page_words = page.extract_words()
                # Most pages carry a short header ("Page: N of M") above the
                # title; twpsa24's first page instead has a tall banner
                # ("Election Precinct Report / August 6, 2024 - Primary
                # Election / Wayne County, Michigan / OFFICIAL RESULTS")
                # pushing the title below the usual zone.
                banner = any(w['text'] == 'OFFICIAL' for w in page_words)
                head_limit = 135 if banner else 60
                head_words = [w for w in page_words
                              if w.get('upright', True) and w['top'] < head_limit]
                hlines = {}
                for w in head_words:
                    hlines.setdefault(round(w['top'] / 3), []).append(w)
                head_lines = []
                for k in sorted(hlines):
                    line = ' '.join(w['text'] for w in
                                    sorted(hlines[k], key=lambda w: w['x0']))
                    if (re.match(r'^Page:', line) or line in ('DEM', 'REP',
                                                              'Precinct',
                                                              'County',
                                                              'Michigan - top '
                                                              'District')
                            or re.match(r'^\d/ ?\d+/\d+', line)
                            or line in ('Election Precinct Report',
                                        'OFFICIAL RESULTS')
                            or re.match(r'^(August \d{1,2}, \d{4}|'
                                        r'Wayne County, Michigan$)', line)):
                        continue
                    head_lines.append(line)
                m = re.search(r'(.{3,}?)\s*(?:\((DEM|REP)\)\s*)?'
                              r'\(Vote for (\d+)\)$', ' '.join(head_lines))
                if m:
                    close_contest()
                    office, district = map_office(m.group(1).strip())
                    contest = {'name': 'Wayne', 'office': office,
                               'district': district,
                               'party': m.group(2) or '',
                               'vote_for': int(m.group(3)),
                               'tc': {}, 'results': {}, 'precincts': [],
                               'county_totals': {}, 'tc_totals': {}}
                if contest is None:
                    problems.append(f'{tag}: page before any contest title')
                    continue

                # Split the page's tables: turnout columns vs results columns.
                all_cols = header_columns(page, 0, 1000)
                tc_cols = {k: n for k, n in all_cols.items() if n in TC_NAMES}
                res_cols = {k: n for k, n in all_cols.items()
                            if n not in TC_NAMES}
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
                    pending = []     # label lines awaiting the next data row
                    last = None      # row whose wrapped label may continue

                    def flush(entry):
                        """Store a finished row; its label must be complete."""
                        if entry is None:
                            return
                        if not COMPLETE.search(entry['label'] or ''):
                            problems.append(
                                f'{tag}: row label incomplete: '
                                f'{entry["label"]!r}')
                            return
                        p = entry['label']
                        if p not in contest['precincts']:
                            contest['precincts'].append(p)
                        for val, w in entry['values']:
                            xc = (w['x0'] + w['x1']) / 2
                            key = nearest(table_cols, xc)
                            if key is None:
                                problems.append(
                                    f'{tag} {p!r}: value {w["text"]} at '
                                    f'x={xc:.0f} matches no column')
                                continue
                            col = table_cols[key]
                            if is_tc:
                                idx = 0 if col == 'Times Cast' else 1
                                slot = contest['tc'].setdefault(p, {})
                                if slot.get(idx) not in (None, val):
                                    problems.append(
                                        f'{tag} {p!r}: duplicate {col} '
                                        f'value {val}')
                                slot[idx] = val
                            else:
                                slot = contest['results'].setdefault(
                                    p, {}).setdefault(col, {})
                                slot['votes'] = val
                                if col in entry['pcts']:
                                    slot['pct'] = entry['pcts'][col]

                    skip_rest = False
                    for row in region_lines(page, x_min, x_max):
                        # Strip county-boundary markers from label lines.
                        row = [w for w in row
                               if w['text'] not in COUNTY_TOKENS]
                        if not row:
                            continue
                        text_row = ' '.join(w['text'] for w in row)
                        if text_row in ('Precinct', 'County',
                                        'Precinct County',
                                        'Michigan - top District',
                                        'DEM', 'REP') \
                                or re.match(r'^(Page: \d+ of \d+|'
                                            r'Wayne County, Michigan$|'
                                            r'OFFICIAL RESULTS$)', text_row) \
                                or re.search(r'\(Vote for \d+\)$', text_row):
                            continue  # the label column's header band
                        if FOOTER.match(text_row):
                            if text_row.startswith('County - Total'):
                                for w in row:
                                    if PCT.match(w['text']):
                                        continue
                                    if not (w['text'].replace(',', '')
                                            .isdigit()):
                                        continue
                                    xc = (w['x0'] + w['x1']) / 2
                                    key = nearest(table_cols, xc)
                                    if key is None:
                                        problems.append(
                                            f'{tag}: County - Total value '
                                            f'{w["text"]} matches no column')
                                        continue
                                    val = None if w['text'] == '****' else \
                                        int(w['text'].replace(',', ''))
                                    if is_tc:
                                        contest['tc_totals'][
                                            table_cols[key]] = val
                                    else:
                                        contest['county_totals'][
                                            table_cols[key]] = val
                            elif not text_row.startswith(
                                    'Michigan - top District -'):
                                continue  # Cumulative block
                            skip_rest = True
                            continue
                        if skip_rest:
                            continue
                        # Numeric tokens are values only when they right-align
                        # to a column; a precinct number printed after the
                        # label text (or wrapped to its own line) sits left of
                        # the columns and belongs to the label.
                        val_words = []
                        for w in row:
                            if PCT.match(w['text']):
                                # A pct sits ~40pt right of its count column,
                                # so it is classified positionally (paired
                                # with the count before it), never by x.
                                val_words.append(w)
                                continue
                            if not w['text'].replace(',', '').isdigit():
                                continue
                            xc = (w['x0'] + w['x1']) / 2
                            if nearest(table_cols, xc) is not None:
                                val_words.append(w)
                        label_words = [w for w in row if w not in val_words]
                        label_text = ' '.join(w['text']
                                              for w in label_words).strip()
                        is_data = len(val_words) > 0
                        if not is_data:
                            if last is not None \
                                    and not COMPLETE.search(last['label'] or ''):
                                last['label'] = ((last['label'] + ' '
                                                  if last['label'] else '')
                                                 + label_text)
                                if not COMPLETE.search(last['label']):
                                    # Reverse-order wrap: "Precinct 4" printed
                                    # above the jurisdiction line.
                                    fixed = reorder_number_first(last['label'])
                                    if fixed is not None:
                                        last['label'] = fixed
                                if COMPLETE.search(last['label']):
                                    flush(last)
                                    last = None
                            else:
                                pending.append(label_text)
                            continue
                        flush(last)
                        last = None
                        label = ' '.join(pending + [label_text]).strip()
                        pending = []
                        if not COMPLETE.search(label):
                            fixed = reorder_number_first(label)
                            if fixed is not None:
                                label = fixed
                        # Tokens in x order: each int is one candidate's
                        # count, and a pct token pairs with the int before it
                        # (same candidate cell).
                        values, pcts, last_col = [], {}, None
                        for w in sorted(val_words, key=lambda w: w['x0']):
                            if PCT.match(w['text']):
                                if last_col is not None:
                                    pcts[last_col] = float(w['text'][:-1])
                                continue
                            if w['text'] == '****':
                                values.append((None, w))
                            else:
                                values.append(
                                    (int(w['text'].replace(',', '')), w))
                            last_col = table_cols[nearest(
                                table_cols, (w['x0'] + w['x1']) / 2)]
                        entry = {'label': label, 'values': values,
                                 'pcts': pcts}
                        # A complete label stores immediately; otherwise the
                        # row keeps collecting its label from the lines that
                        # follow and flush() stores it when complete.
                        if COMPLETE.search(label):
                            flush(entry)
                        else:
                            last = entry
                    flush(last)
                    if pending:
                        problems.append(f'{tag}: trailing label '
                                        f'{" ".join(pending)!r}')
        close_contest()

    parse_delegate_rosters(args.sources_dir, out_rows, problems)

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


# --- Precinct delegate rosters ---------------------------------------------
# "OFFICE OF THE WAYNE COUNTY CLERK ... PRECINCT DELEGATES / OFFICIAL
# RESULTS": per jurisdiction, rows "DISTRICT PCT. VOTE-FOR [WRITE-IN] NAME
# ADDRESS CITY STATE ZIP EMAIL PHONE TOTAL-VOTES E|NE". Each row is one
# candidate running in one precinct's delegate race; rows with no candidate
# (a precinct whose slate filed with no contest) carry only the leading
# numbers. The DISTRICT column is the county's internal numbering (it does
# not identify a legislative district), so it stays out of the CSV; the
# jurisdiction maps to the SOVC precinct labels' jurisdiction part, so the
# delegate office uses the same "Jurisdiction, Precinct N" labels.
DELEGATE_ROW = re.compile(
    r'^(?:([A-Z][A-Z .\'&-]+?) +)?(\d+) +(\d+[A-Z]?) +(\d+)(?: (X))?'
    r'(?: +(.+))?$')
DELEGATE_NOISE = re.compile(
    r'^(OFFICE OF THE|OFFICIAL RESULTS|VOTE$|ELECTED/NOT|ELECTED$|'
    r'JURISDICTION DIST|\d+/\d+/\d{4}( E = ELECTED)?$|'
    r'E = ELECTED$|'
    r'\d+:\d+ [AP]M \d+ NE [-=] NOT ELECTED|VOTE WRITE-|VOTES$)')
ADDRESS = re.compile(r' \d{1,5} [A-Z]')
VOTES = re.compile(r'(\d+) (E|NE)$')


def normalize_jurisdiction(label):
    """SOVC jurisdiction -> comparable name ('Charter Township of
    Brownstown' -> 'Brownstown'). Unprefixed labels keep their Township
    suffix out of the key ('Canton Township' -> 'Canton'), matching the
    rosters' 'CANTON TWP.' spelling."""
    label = re.sub(r'^The Village of ', '', label)
    label = re.sub(r'^(City|Charter Township|Township) of ', '', label)
    label = re.sub(r', a Michigan City$', '', label)
    label = re.sub(r' Township$', '', label)
    return label


def parse_delegate_rosters(sources_dir, out_rows, problems):
    # Jurisdiction lookup from the labels the SOVC tables already parsed.
    sovc = []
    precinct_labels = set()
    prec_split = re.compile(
        r'^(.+?), ((?:District \d+ )?Precinct \d+[A-Z]?)$')
    for row in out_rows:
        precinct_labels.add(row[1])
        m = prec_split.match(row[1])
        jur = m.group(1) if m else row[1]
        norm = normalize_jurisdiction(jur)
        if (norm, jur) not in sovc:
            sovc.append((norm, jur))
    for fname, party in sorted(DELEGATE_PDFS.items()):
        with pdfplumber.open(os.path.join(sources_dir, fname)) as pdf:
            jurisdiction = None
            for pno, page in enumerate(pdf.pages, 1):
                for line in (l.strip() for l in
                             (page.extract_text() or '').splitlines()):
                    if not line or DELEGATE_NOISE.match(line):
                        continue
                    m = DELEGATE_ROW.match(line)
                    if not m:
                        problems.append(
                            f'{fname} p{pno}: unrecognized line {line!r}')
                        continue
                    jur_raw, dist, pct, _vf, wi, rest = m.groups()
                    if jur_raw:
                        # A city's own name can end in "City" ("GARDEN
                        # CITY"), so try the raw spelling first and the
                        # suffix-stripped one second.
                        keys = [jur_raw.strip().title()]
                        stripped = (jur_raw.replace(' TWP.', '')
                                    .replace(' TWP', '')
                                    .replace(' CITY', '').strip().title())
                        if stripped != keys[0]:
                            keys.append(stripped)
                        matches = []
                        for key in keys:
                            if jur_raw.endswith('CITY'):
                                matches = [j for n, j in sovc if n == key
                                           and j.startswith('City of ')]
                            elif jur_raw.endswith(('TWP.', 'TWP')):
                                matches = [j for n, j in sovc if n == key
                                           and 'Township' in j]
                            else:
                                matches = [j for n, j in sovc if n == key]
                            if len(matches) == 1:
                                break
                        jur = matches[0] if len(matches) == 1 else None
                        if jur is None:
                            problems.append(f'{fname} p{pno}: jurisdiction '
                                            f'{jur_raw!r} matches no precinct '
                                            f'label')
                            continue
                        jurisdiction = jur
                    if not rest:
                        continue  # precinct row with no candidates listed
                    pct_label = f'Precinct {pct}'
                    if jurisdiction == 'City of Inkster' \
                            and pct.isdigit() and len(pct) == 4:
                        # The roster codes Inkster's precincts as
                        # <district><precinct> ("3002"); the SOVC prints
                        # "District 3 Precinct 2".
                        pct_label = (f'District {pct[0]} '
                                     f'Precinct {int(pct[1:])}')
                    if f'{jurisdiction}, {pct_label}' not in precinct_labels:
                        problems.append(f'{fname} p{pno}: roster precinct '
                                        f'{jurisdiction}, {pct_label} matches '
                                        f'no SOVC precinct label')
                        continue
                    am = ADDRESS.search(rest)
                    vm = VOTES.search(rest)
                    votes = None
                    if vm is not None:
                        votes = int(vm.group(1))
                    elif wi == 'X':
                        # Write-in rows the vendor printed with no trailing
                        # vote total (one with a bare "0" and no E/NE flag):
                        # the count is zero.
                        m2 = re.search(r' (\d+)$', rest)
                        votes = int(m2.group(1)) if m2 else 0
                    if am is None or votes is None:
                        problems.append(f'{fname} p{pno}: unparseable row '
                                        f'{line!r}')
                        continue
                    # "X" marks a write-in candidate, but the roster still
                    # prints their name — keep it (two write-ins in one
                    # precinct can tie, which would collide as bare
                    # "Write-In" rows).
                    name = rest[:am.start()].strip()
                    out_rows.append(['Wayne',
                                     f'{jurisdiction}, {pct_label}',
                                     f'{jurisdiction}, {pct_label} '
                                     'Delegate to County Convention',
                                     '', party,
                                     name,
                                     votes, '', '', ''])


if __name__ == '__main__':
    main()