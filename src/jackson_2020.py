"""Parse Jackson County's 2020 primary ES&S "Statement of Votes Cast" PDF.

Source: openelections-sources-mi 2020/primary/Jackson MI Primary.pdf (457
pages, 792x612 landscape, text layer).  Each contest prints one or more
tables; a table is a grid whose headers are rotated 90 degrees (each vertical
line reads bottom-to-top, wrapped lines continue at x0+~10.7).  Tables come
in two shapes:

- 2-up pages: left half carries the aux columns (Times Cast, Registered
  Voters), right half the result columns (candidates, Total Votes,
  Unresolved Write-In); the precinct label is printed in both halves and
  each precinct spans up to three physical lines (labels+values, aux
  values, "Precinct N" tails).
- single-width pages (follow-up tables): one label column and the result
  columns; labels, values and tails interleave freely across lines.

A contest whose main table has no Unresolved Write-In column (or, for the
12th District Court judge, no room for all candidates) is followed by
further tables that walk the same precinct list on interleaved pages; the
parser merges a contest's tables by precinct.  "Qualified Write In" strips
never carry values and are excluded from the value mapping.  Precinct
delegate contests are one precinct each.  Nearly every page repeats its
rotated strips, so no header inheritance is needed.  Out-of-County rows
(only the Senior Citizens millage proposal lists any) are parsed for
validation but not emitted.

Digits are values when they start at x >= 140 (left half) / x >= 530 (right
half) or within 3pt of the previous word (never the case for values); label
digits ("Precinct N" tails, inline or on their own line) sit left of that.
Value digits are center-aligned per column, so they cluster by center and
pair in x order with the x-sorted strip groups.  Every precinct row
validates Total Votes == sum(candidates) (Unresolved excluded; printed
totals already include cross-table candidates), every table's per-column
county total validates against its precinct sums, and aux columns must
agree across contests.  Output is the standard 7-column file plus
per-precinct Registered Voters / Ballots Cast pseudo rows (Wayne 2020
precedent); raw Unresolved Write-In counts become candidate rows named
"Write-In" (Kalamazoo 2020 precedent).
"""
import re
import sys

import pdfplumber

from csv_2020_primary import map_office, write_csv

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Jackson MI Primary.pdf')
COUNTY = 'Jackson'

# Column names after removing spaces/hyphens, with their roles.
AUX_COLUMNS = {'TimesCast', 'RegisteredVoters'}
PHANTOM_COLUMNS = {'QualifiedWriteIn'}
CAPTION_COLUMNS = AUX_COLUMNS | PHANTOM_COLUMNS | {
    'TotalVotes', 'UnresolvedWriteIn', 'Yes', 'No', 'DEM', 'REP'}
AUX_NAMES = {'Times Cast', 'Registered Voters'}

DIGIT_RE = re.compile(r'^[\d,]+$')
FOOTER_WORDS = {'Jackson', 'County', 'Michigan', '-', 'Total', 'Cumulative'}
# label/text zone right edges (values start right of these)
LEFT_TEXT_EDGE = 140
RIGHT_TEXT_EDGE = 530


# known column-name phrases that can wrap-merge onto a candidate strip;
# if a strip's word sequence ends with one, it is split back off
SPLIT_PHRASES = ('Qualified Write In', 'Unresolved Write-In', 'Total Votes')


def read_strips(rot_words):
    """Rotated header strips -> [(name, party|None, x0)] in x order."""
    bins = {}
    for w in rot_words:
        bins.setdefault(round(w['x0'] / 3), []).append(w)
    groups = []
    for b in sorted(bins):
        # merge adjacent bins whose x0 gap is <= 14.5: wrap lines sit ~10.7
        # right of their base line, distinct columns are >= 30 apart
        if groups and min(w['x0'] for w in bins[b]) - \
                max(w['x0'] for w in bins[groups[-1][-1]]) <= 14.5:
            groups[-1].append(b)
        else:
            groups.append([b])
    out = []
    for g in groups:
        words = sorted((w for b in g for w in bins[b]),
                       key=lambda w: (w['x0'], -w['top']))
        # reading order: vertical lines left to right, each bottom-to-top
        seq = [w['text'][::-1] for w in words]
        name = ' '.join(seq)
        # split off a trailing known column phrase (its strips sit close
        # enough to the candidate's wrap line to be caught by the merge)
        extra = None
        for phrase in SPLIT_PHRASES:
            n = len(phrase.split())
            if seq[-n:] == phrase.split() and len(seq) > n:
                extra = (phrase, min(w['x0'] for w in words[-n:]))
                words = words[:-n]
                name = ' '.join(seq[:-n])
                break
        party = None
        m = re.search(r'\((DEM|REP)\)$', name)
        if m:
            party = m.group(1)
            name = name[:m.start()].strip()
        if name or party:
            out.append((name, party, min(w['x0'] for w in words)))
        if extra:
            out.append((extra[0], None, extra[1]))
    # a strip that is only a party tag belongs to the previous column
    fixed = []
    for name, party, x0 in out:
        if not name and party and fixed:
            prev = fixed[-1]
            fixed[-1] = (prev[0], party, prev[2])
        elif name or party:
            fixed.append((name, party, x0))
    return fixed


def classify(strips):
    roles = []
    for name, party, x0 in strips:
        key = re.sub(r'[ -]', '', name)
        if key in AUX_COLUMNS:
            kind = 'aux'
        elif key in PHANTOM_COLUMNS:
            kind = 'phantom'
        elif key in CAPTION_COLUMNS:
            kind = 'result'
        else:
            kind = 'candidate'
        roles.append(dict(kind=kind, name=name, party=party, x0=x0))
    return roles


def title_office(title):
    """Strip the party tag and "(Vote for N)" from a contest title."""
    t = re.sub(r'\s*\((?:DEM|REP)\)\s*\(Vote for \d+\)$', '', title)
    t = re.sub(r'\s*\(Vote for \d+\)$', '', t)
    return t.strip()


def map_title(title):
    """Contest title -> (office, district)."""
    o = title_office(title)
    m = re.match(r'Rep in Congress (\d+)(?:st|nd|rd|th) District$', o)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'Rep in State Legislature (\d+)(?:st|nd|rd|th) District$', o)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'District Court Judge (\d+)(?:st|nd|rd|th) District$', o)
    if m:
        return 'District Court Judge', m.group(1)
    m = re.match(r'(.+), Precinct \d+ Delegate$', o)
    if m:
        return 'Precinct Delegate', o
    return map_office(o, '', COUNTY)


def parse_pages(errors):
    """Per page: contest title/party, column roles, data lines."""
    pages = []
    with pdfplumber.open(SRC) as pdf:
        for pno in range(5, len(pdf.pages) + 1):
            pg = pdf.pages[pno - 1]
            words = pg.extract_words()
            upright = [w for w in words if w.get('upright', True)]
            rot = [w for w in words if not w.get('upright', True)]
            title = party = None
            for top0 in (36.3, 46.7):
                row = [w for w in upright if abs(w['top'] - top0) < 1.5]
                text = ' '.join(w['text'] for w in
                                sorted(row, key=lambda w: w['x0']))
                if '(Vote' in text:
                    title = text
                    m = re.search(r'\((DEM|REP)\)', title)
                    if m:
                        party = m.group(1)
                    break
            if title and not party:
                marker = [w['text'] for w in upright
                          if abs(w['top'] - 52.2) < 1.5]
                if marker and marker[0] in ('DEM', 'REP'):
                    party = marker[0]
            captions = sorted(w['top'] for w in upright
                              if w['text'] == 'Precinct' and w['top'] > 60)
            if not captions:
                errors.append(f'page {pno}: no Precinct caption')
                continue
            cap_top = captions[0]
            county_cap = sorted(w['top'] for w in upright
                                if w['text'] == 'County'
                                and cap_top < w['top'] < cap_top + 25)
            data_cut = (county_cap[0] if county_cap else cap_top) + 2
            roles = classify(read_strips(
                [w for w in rot if 30 < w['top'] < cap_top + 6]))
            is_2up = len([w for w in upright if w['text'] == 'Precinct'
                          and abs(w['top'] - cap_top) < 1.5]) > 1
            lines = []
            for w in sorted((w for w in upright if w['top'] > data_cut),
                            key=lambda w: (w['top'], w['x0'])):
                if lines and w['top'] - lines[-1][-1]['top'] < 3:
                    lines[-1].append(w)
                else:
                    lines.append([w])
            pages.append(dict(pno=pno, title=title, party=party,
                              is_2up=is_2up, roles=roles, lines=lines))
    return pages


def split_words(line, is_2up):
    """Line words -> [(half, words)]: one half unless a 2-up page."""
    if not is_2up:
        return [('L', line)]
    return [('L', [w for w in line if w['x0'] < 397]),
            ('R', [w for w in line if w['x0'] >= 397])]


def split_digits(words):
    """Words -> (value digit words, label words).  A digit is label text
    when it starts in the text zone or abuts the previous word; trailing
    commas ("Ward 1,") are always label text."""
    values, labels, prev_x1 = [], [], None
    edge = RIGHT_TEXT_EDGE if is_right_half(words) else LEFT_TEXT_EDGE
    for w in words:
        if DIGIT_RE.match(w['text']):
            if w['text'].endswith(',') or \
                    (prev_x1 is not None and w['x0'] - prev_x1 <= 3) or \
                    w['x0'] < edge:
                labels.append(w)
            else:
                values.append(w)
        else:
            labels.append(w)
        prev_x1 = w['x1']
    return values, labels


def is_right_half(words):
    return bool(words) and words[0]['x0'] >= 397


def cluster_digits(digits):
    """Value digit words -> clusters ([words], right edge) sorted by edge.

    Values are right-aligned per column (x1 stable within ~2pt)."""
    items = sorted(((w['x1'], w) for w in digits), key=lambda t: t[0])
    clusters = []
    for edge, w in items:
        if clusters and edge - clusters[-1][-1][0] <= 6:
            clusters[-1].append((edge, w))
        else:
            clusters.append([(edge, w)])
    return [([w for _, w in c], sum(x for x, _ in c) / len(c))
            for c in clusters]


def parse_page_records(page, errors):
    """One page -> (records, county ({result col: v}, {aux col: v})).

    records are (precinct, {result col: votes}, {aux col: votes})."""
    roles = [r for r in page['roles'] if r['kind'] != 'phantom']
    halves_per_line = [split_words(line, page['is_2up'])
                       for line in page['lines']]
    # first pass: collect value digits for clustering (footer/county rows
    # and the digits-only county-aux line that may follow are excluded)
    value_digits = []
    expect_aux = False
    for (half, words), line in zip(
            [h for hl in halves_per_line for h in hl],
            [line for line in page['lines']
             for _ in split_words(line, page['is_2up'])]):
        texts = [w['text'] for w in line]
        lbl = [t for t in texts if not DIGIT_RE.match(t)]
        if lbl and all(t in FOOTER_WORDS for t in lbl):
            expect_aux = not any(t == 'Cumulative' for t in lbl) and \
                ('Jackson' in lbl or set(lbl) == {'County', '-', 'Total'})
            continue                        # county/footer rows
        if expect_aux and not lbl:
            continue
        expect_aux = False
        values, _ = split_digits(words)
        value_digits += values
    clusters = cluster_digits(value_digits)
    digit_col = {}
    if clusters:
        if len(clusters) != len(roles):
            errors.append(f'page {page["pno"]}: {len(clusters)} value '
                          f'clusters for {len(roles)} columns '
                          f'({[r["name"] for r in roles]})')
            return [], ({}, {})
        paired = [a for a in zip(sorted(roles, key=lambda r: r['x0']),
                                 sorted(clusters, key=lambda c: c[1]))]
        for r, (ws, edge) in paired:
            if abs(edge - r['x0']) > 45:
                errors.append(f'page {page["pno"]}: cluster@{edge:.0f} too '
                              f'far from column {r["name"]} '
                              f'({abs(edge - r["x0"]):.0f}pt)')
                return [], ({}, {})
        digit_col = {id(w): r['name'] for r, (ws, _) in paired for w in ws}

    def nearest_col(w):
        """County/footer digits aren't clustered; map by nearest strip."""
        best, dist = None, 1e9
        for r in roles:
            d = abs(w['x1'] - r['x0'])
            if d < dist:
                best, dist = r['name'], d
        if dist > 45:
            errors.append(f'page {page["pno"]}: county digit {w["text"]}'
                          f'@{w["x0"]:.0f} too far from any column')
            return None
        return best

    records = []
    county = ({}, {})
    expect_aux = False
    last_rec = None             # (values dict, last line top)
    buf = {'L': [], 'R': []}
    pend = {h: ({}, {}) for h in ('L', 'R')}   # (values, aux) per half

    def complete_text(half):
        return ' '.join(w['text'] for w in buf[half])

    for half_groups, line in zip(halves_per_line, page['lines']):
        texts = [w['text'] for w in line]
        lbl = [t for t in texts if not DIGIT_RE.match(t)]
        # footer rows
        if lbl and all(t in FOOTER_WORDS for t in lbl):
            if any(t == 'Cumulative' for t in lbl) or \
                    set(lbl) <= {'Jackson', 'County', 'Michigan', 'Total'} \
                    and '-' not in lbl:
                expect_aux = False
                continue
            if 'Jackson' in lbl or set(lbl) == {'County', '-', 'Total'}:
                for w in line:
                    if DIGIT_RE.match(w['text']):
                        col = digit_col.get(id(w)) or nearest_col(w)
                        if col is None:
                            continue
                        v = int(w['text'].replace(',', ''))
                        target = county[1] if col in AUX_NAMES else county[0]
                        if col in target and target[col] != v:
                            errors.append(f'page {page["pno"]}: conflicting '
                                          f'county {col}: {target[col]} vs {v}')
                        target[col] = v
                expect_aux = True
                continue
        # digits-only line right after a county row: county aux values
        if expect_aux and not lbl:
            cols = [(w, digit_col.get(id(w)) or nearest_col(w)) for w in line
                    if DIGIT_RE.match(w['text'])]
            if all(col in AUX_NAMES for _, col in cols):
                for w, col in cols:
                    county[1][col] = int(w['text'].replace(',', ''))
                expect_aux = False
                continue
        expect_aux = False
        for half, words in half_groups:
            values, labels = split_digits(words)
            line_vals = {}
            for w in values:
                col = digit_col.get(id(w))
                if col is None:
                    errors.append(f'page {page["pno"]}: unmapped digit '
                                  f'{w["text"]}@{w["x0"]:.0f}')
                    continue
                v = int(w['text'].replace(',', ''))
                if col in AUX_NAMES:
                    pend[half][1][col] = v
                else:
                    line_vals[col] = v
            # orphan value digits completing a record flushed earlier
            if not labels and not buf['L'] and not buf['R'] and last_rec \
                    and line[0]['top'] - last_rec[1] <= 16:
                last_rec[0].update(line_vals)
                continue
            for w in labels:
                buf[half].append(w)
            pend[half][0].update(line_vals)
        # completion: label ends with an inline/tailed "Precinct N" or ")"
        text = ' '.join(complete_text('L').split())
        if page['is_2up'] and not re.search(r'Precinct \d+$', text) \
                and not text.rstrip().endswith(')'):
            # left label truncated: fall back to the concatenation
            text = ' '.join((complete_text('L') + ' ' +
                             complete_text('R')).split())
        if re.search(r'Precinct \d+$', text) or text.rstrip().endswith(')'):
            if page['is_2up']:
                text2 = ' '.join(complete_text('R').split())
                if text2 and text2 != text:
                    errors.append(f'page {page["pno"]}: label halves differ: '
                                  f'{text!r} vs {text2!r}')
            vals, aux = {}, {}
            for h in ('L', 'R'):
                vals.update(pend[h][0])
                aux.update(pend[h][1])
            precinct = text
            missing = [r['name'] for r in roles
                       if r['kind'] != 'aux' and r['name'] not in vals]
            missing += [r['name'] for r in roles
                        if r['kind'] == 'aux' and r['name'] not in aux]
            if missing:
                errors.append(f'page {page["pno"]} {precinct!r}: '
                              f'missing values for {missing}')
            records.append((precinct, vals, aux))
            last_rec = (vals, line[-1]['top'])
            buf = {'L': [], 'R': []}
            pend = {h: ({}, {}) for h in ('L', 'R')}
    return records, county


def main():
    errors = []
    warn = []
    pages = parse_pages(errors)
    contests = []
    aux_all = {}                # precinct -> {aux col: votes}
    cur = None
    for page in pages:
        if page['title']:
            cur = dict(title=page['title'], party=page['party'], tables={})
            contests.append(cur)
        if cur is None:
            continue
        records, county = parse_page_records(page, errors)
        key = tuple(sorted(r['name'] for r in page['roles']))
        tbl = cur['tables'].setdefault(key, {})
        cty = cur['tables'].setdefault(key + ('#county',), {})
        for precinct, vals, aux in records:
            if precinct in tbl:
                errors.append(f'page {page["pno"]}: duplicate precinct '
                              f'{precinct!r}')
            tbl[precinct] = vals
            if 'Out of' in precinct:
                continue        # out-of-county names collide with real ones
            counts = aux_all.setdefault(precinct, {})
            for col, v in aux.items():
                counts.setdefault(col, {}).setdefault(v, 0)
                counts[col][v] += 1
        for col, v in county[0].items():
            if col in cty and cty[col] != v:
                errors.append(f'{cur["title"]}: conflicting county {col}: '
                              f'{cty[col]} vs {v}')
            cty[col] = v

    # ---- aux majority vote (a few cells are misprinted in the source) ----
    aux = {}
    for p, cols in sorted(aux_all.items()):
        for col, counts in sorted(cols.items()):
            if len(counts) > 1:
                warn.append(f'{p}: inconsistent {col} across tables: '
                            + ', '.join(f'{v} x{n}'
                                        for v, n in counts.items()))
            aux.setdefault(p, {})[col] = max(
                counts.items(), key=lambda kv: kv[1])[0]

    # ---- validation ----
    for c in contests:
        tbl_keys = [k for k in c['tables'] if '#county' not in k]
        if not tbl_keys:
            errors.append(f'{c["title"]}: no tables')
            continue
        sets = [set(c['tables'][k]) for k in tbl_keys]
        if len(sets) > 1 and any(s != sets[0] for s in sets[1:]):
            errors.append(f'{c["title"]}: table precinct sets differ: '
                          + '/'.join(str(len(s)) for s in sets))
        merged = {}
        for k in tbl_keys:
            for p, vals in c['tables'][k].items():
                m = merged.setdefault(p, {})
                for col, v in vals.items():
                    if col in m and m[col] != v:
                        errors.append(f'{c["title"]} {p}: conflicting '
                                      f'{col}: {m[col]} vs {v}')
                    m[col] = v
        for p, vals in merged.items():
            if 'Total Votes' in vals:
                total = sum(v for col, v in vals.items() if col not in
                            ('Total Votes', 'Unresolved Write-In'))
                if vals['Total Votes'] != total:
                    errors.append(f'{c["title"]} {p}: Total '
                                  f'{vals["Total Votes"]} != sum {total}')
        for k in tbl_keys:
            cty = c['tables'].get(k + ('#county',), {})
            if not cty:
                errors.append(f'{c["title"]} table {k[:2]}: no county total')
                continue
            for col, v in cty.items():
                s = sum(rec.get(col, 0) for rec in c['tables'][k].values())
                if v != s:
                    errors.append(f'{c["title"]} {col}: county total {v} '
                                  f'!= precinct sum {s}')

    # ---- emission ----
    rows = []
    pseudo_emitted = set()
    for c in contests:
        office, district = map_title(c['title'])
        tbl_keys = [k for k in c['tables'] if '#county' not in k]
        merged = {}
        for k in tbl_keys:
            for p, vals in c['tables'][k].items():
                merged.setdefault(p, {}).update(vals)
        for p in sorted(merged):
            if 'Out of' in p:
                continue
            vals = merged[p]
            for col, v in sorted(vals.items()):
                if col in ('Total Votes', 'Unresolved Write-In'):
                    continue
                party = '' if col in ('Yes', 'No') else (c['party'] or '')
                rows.append(dict(county=COUNTY, precinct=p, office=office,
                                 district=district, party=party,
                                 candidate=col, votes=v))
            if 'Unresolved Write-In' in vals:
                rows.append(dict(county=COUNTY, precinct=p, office=office,
                                 district=district, party=c['party'] or '',
                                 candidate='Write-In',
                                 votes=vals['Unresolved Write-In']))
            for col in ('Registered Voters', 'Times Cast'):
                if col in aux.get(p, {}) and p not in pseudo_emitted:
                    name = ('Ballots Cast' if col == 'Times Cast'
                            else 'Registered Voters')
                    rows.append(dict(county=COUNTY, precinct=p, office=name,
                                     district='', party='', candidate='',
                                     votes=aux[p][col]))
        pseudo_emitted.update(p for p in merged if 'Out of' not in p)
    if errors:
        print('ERRORS:')
        for e in errors[:60]:
            print(' ', e)
        print(f'total errors: {len(errors)}')
        sys.exit(1)
    for w in warn[:20]:
        print('WARN:', w)
    print(f'{len(warn)} aux warnings')
    write_csv(COUNTY, rows)
    print(f'{len(rows)} rows, {len(contests)} contests, '
          f'{len(pseudo_emitted)} precincts')


if __name__ == '__main__':
    main()