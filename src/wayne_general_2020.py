"""Parse Wayne County Nov 2020 general "Statement of Votes Report" PDFs into
a per-county precinct CSV, then verify against 2020/20201103__mi__general__county.csv.

Sources (openelections-sources-mi/2020/general/), one contest group each:
  Wayne MI statementofvotescastnov20str.pdf   Straight Party
  Wayne MI statementnov20prez.pdf             President/Vice-President
  Wayne MI statementussennov20.pdf            U.S. Senate
  Wayne MI statementnov20cong.pdf             U.S. House 11-14
  Wayne MI statement_strepnov20.pdf           State House 1-17,19-23 + Partial Term 4
  Wayne MI nov20_votestats.pdf                turnout-only (cross-check)

Each PDF starts with a turnout section (per precinct: Election Day /
AV Counting Board / Total rows of Registered Voters, Cards Cast, Voters
Cast, % Turnout; the county's own total block ends it), then one contest
section per title line. Contest sections are 2-up: a "type A" page (Times
Cast column + the first 2 candidate columns, precinct label printed in both
halves) alternates with a "type B" page (the remaining candidate columns +
Total Votes + Unresolved Write-In, label printed once). Candidate headers
are rotated; pdftotext -layout de-rotates them, so columns are keyed by
token x-offset and values are assigned ORDINALLY (Electionware prints every
cell) with a nearest-anchor fallback. Labels wrap in lockstep across both
halves of type-A pages; each contest section opens with two stray
label-only 'Wayne County' lines; the county total row is printed twice with
the values ON the label line (Total-row values only, no ED/AV breakdown).

Validation: per contest/precinct/method, candidates + Unresolved Write-In
== Total Votes; Times Cast (type A) == the turnout section's Voters Cast;
Total == Election Day + AV per candidate; the printed Wayne County - Total
row == the precinct sums; the six turnout extracts must agree; and every
(office, district, candidate) — votes, election_day, absentee — is verified
against the county file.

Usage:
    .venv/bin/python src/wayne_general_2020.py
"""
import csv
import os
import re
import subprocess
import sys
from collections import defaultdict

SRC = '/Users/dwillis/code/openelections-sources-mi/2020/general/'
COUNTY = 'Wayne'
OUT = '2020/counties/20201103__mi__general__wayne__precinct.csv'
COUNTY_CSV = '2020/20201103__mi__general__county.csv'

STATEMENT_SLUGS = ('str', 'prez', 'ussen', 'cong', 'strep')
PDFS = {
    'str': 'Wayne MI statementofvotescastnov20str.pdf',
    'prez': 'Wayne MI statementnov20prez.pdf',
    'ussen': 'Wayne MI statementussennov20.pdf',
    'cong': 'Wayne MI statementnov20cong.pdf',
    'strep': 'Wayne MI statement_strepnov20.pdf',
    'votestats': 'Wayne MI nov20_votestats.pdf',
}

TITLE_MAP = [
    (re.compile(r'^President/Vice-President \(Vote for \d+\)$'),
     ('President', '')),
    (re.compile(r'^Straight Party \(Vote for \d+\)$'), ('Straight Party', '')),
    (re.compile(r'^United States Senator \(Vote for \d+\)$'),
     ('U.S. Senate', '')),
    (re.compile(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District'
                r'(?: \(Wayne County pcts only\))? \(Vote for \d+\)$'),
     ('U.S. House', None)),
    (re.compile(r'^Representative in State Legislature '
                r'(\d+)(?:st|nd|rd|th) District'
                r'(?: Partial Term Ending \S+)?'
                r'(?: \(Wayne County pcts only\))? \(Vote for \d+\)$'),
     ('State House', None)),
]

# the president header wraps mid-word: 'Blankenship/Willia' + 'm Mohr'
NAME_FIXES = [('Willia m', 'William')]
# the statement prints the Democratic Party's straight-party name without
# the 'ic'; the county file (and every other county) uses 'Democratic Party'
STRAIGHT_PARTY_NAMES = {'Democrat Party': 'Democratic Party'}

PARTY_CODE_RX = r'(?:DEM|REP|LIB|UST|GRN|NLP|WCP)'
INT_RX = re.compile(r'[\d,]+')
DUP_HALF_SPLIT = re.compile(r'\s{2,}')
# value columns are stored under their printed header names; the Times Cast
# value lives under a synthetic key
TIMES_KEY = '__times__'
TOTAL_KEY = 'Total Votes'
WRITEIN_KEY = 'Unresolved Write-In'

problems = []


def get_text(slug):
    txt = f'/tmp/wayne_general_{slug}.txt'
    if not os.path.exists(txt):
        subprocess.run(['pdftotext', '-layout', SRC + PDFS[slug], txt],
                       check=True)
    return [pg.split('\n') for pg in open(txt).read().split('\f')]


def title_of(plines):
    """(office, district, title_line) if a contest title heads this page."""
    for line in plines[:8]:
        s = ' '.join(line.split())
        for rx, (office, dist) in TITLE_MAP:
            m = rx.match(s)
            if m:
                if dist is None:
                    dist = m.group(1)
                    if office == 'State House' and 'Partial Term' in s:
                        office = 'State House Partial Term'
                return office, dist, line
    return None


def is_int(tok):
    return bool(INT_RX.fullmatch(tok))


def ints(text):
    return [(m.start(), m.end(), m.group())
            for m in re.finditer(r'[\d,]+', text)]


def parse_turnout(slug):
    """label -> {rv, tv, ed, av, cards}; county -> same shape."""
    stats, county = {}, {}
    pending = []
    label = None

    def flush():
        lab = re.sub(r'\s+', ' ', ' '.join(pending)).strip()
        pending.clear()
        return lab

    for no, plines in enumerate(get_text(slug)):
        if title_of(plines):
            break                      # turnout section ends at first contest
        plines = [l for l in plines if l.strip()]
        for li, line in enumerate(plines):
            if slug == 'votestats' and li == len(plines) - 1:
                # this PDF's footer page number (== the 1-based page no)
                # merges into the page's last line
                toks = line.split()
                while toks and toks[-1] == str(no + 1):
                    toks.pop()
                line = ' '.join(toks)
                if not line.strip():
                    continue
            s = ' '.join(line.split())
            if not s or s.startswith('Page:') or s == 'Wayne County':
                continue
            if ('% Turnout' in s or 'Cards Cast' in s or
                    s in ('Registered', 'Voters', 'Precinct') or
                    'Statement of Votes' in s or 'November 3, 2020' in s or
                    'OFFICIAL RESULTS' in s or s == 'Wayne County, Michigan'):
                continue
            county_row = s.startswith('Wayne County - Total')
            if county_row:
                pending.clear()
                s = 'Total ' + s.split('Wayne County - Total', 1)[1]
                label = 'COUNTY'
            elif not (re.search(r'Election Day|AV Counting Board', s) or
                      s.startswith('Total')):
                pending.append(s)
                continue
            elif pending:
                label = flush()
            nums = [t for t in s.split() if is_int(t)]
            if len(nums) not in (3, 4):
                problems.append(f'{slug} turnout p{no}: {len(nums)} numbers '
                                f'for {label!r}: {s!r}')
                continue
            reg, cards, voters = (int(n.replace(',', '')) for n in nums[:3])
            entry = county if label == 'COUNTY' else stats.setdefault(label, {})
            if s.startswith('Election Day'):
                entry['ed'] = voters
            elif s.startswith('AV Counting Board'):
                entry['av'] = voters
            else:
                entry['tv'] = voters
                entry['rv'] = reg
                entry['cards'] = cards
    for lab, e in list(stats.items()) + [('COUNTY', county)]:
        if None in (e.get('tv'), e.get('ed'), e.get('av'), e.get('rv')):
            problems.append(f'{slug} turnout: incomplete {lab!r}: {e}')
        elif e['ed'] + e['av'] != e['tv']:
            problems.append(f'{slug} turnout {lab!r}: ED {e["ed"]} + AV '
                            f'{e["av"]} != Total {e["tv"]}')
    return stats, county


def header_columns(header_lines, pno):
    """Cluster header-zone tokens into columns [(x, name, kind, party)].

    Rotated headers de-rotate to one word per column at the column's x:
    words of one header sit on the same line ~1 space apart (join on a
    token gap <= 2), while continuation words of wrapped headers repeat on
    later lines at the same x (join on |x - cluster x| <= 8).
    """
    toks = []
    for li, line in enumerate(header_lines):
        s = line.strip()
        if not s or s.startswith('Page:'):
            continue
        for m in re.finditer(r'\S+', line):
            if m.group() == 'Precinct':
                continue
            toks.append((li, m.start(), m.end(), m.group()))
    toks.sort(key=lambda t: (t[0], t[1]))
    cols = []
    for li, x, end, tok in toks:
        best = None
        for c in cols:
            if li == c['last_line']:
                d = x - c['last_end']          # gap since the last word
                near = d <= 2
            else:
                d = abs(x - c['x'])
                near = d <= 8
            if near and (best is None or d < best[0]):
                best = (d, c)
        if best is None:
            best = (0, {'x': x, 'toks': [], 'last_line': li,
                        'last_end': end})
            cols.append(best[1])
        c = best[1]
        c['toks'].append((li, x, tok))
        c['last_line'], c['last_end'] = li, end
    out = []
    for c in sorted(cols, key=lambda c: c['x']):
        text = ' '.join(t for _li, _x, t in sorted(c['toks']))
        party = ''
        m = re.search(r'\((DEM|REP|LIB|UST|GRN|NLP|WCP)\)', text)
        if m:
            party = m.group(1)
            text = (text[:m.start()] + text[m.end():]).strip()
        text = re.sub(r'\s+', ' ', text).strip()
        if re.fullmatch(r'Times Cast', text):
            kind = 'times'
        elif re.fullmatch(r'Total(\s*Votes?)?', text):
            kind = 'total'
        elif re.fullmatch(r'Unresolved\s*Write-?In', text):
            kind = 'writein'
        else:
            kind = 'cand'      # partyless candidates print no (PARTY) code
        out.append({'x': c['x'], 'name': text, 'kind': kind, 'party': party})
    return out


def assign(vals, cols, ctx):
    """Map parsed ints (as (start, end, text)) onto columns, ordinally
    (nearest-x fallback)."""
    named = [c['name'] for c in cols]
    if len(vals) == len(cols):
        return {n: int(v[2].replace(',', '')) for n, v in zip(named, vals)}
    out, used = {}, set()
    for start, end, v in vals:
        center = (start + end) / 2
        order = sorted(range(len(cols)),
                       key=lambda i: abs(center - cols[i]['x']))
        for i in order:
            if i not in used:
                used.add(i)
                out[cols[i]['name']] = int(v.replace(',', ''))
                break
    problems.append(f'{ctx}: {len(vals)} values for {len(cols)} columns '
                    f'({named}); assigned {out}')
    return out


def parse_contests(slug, turnout):
    """One entry per contest:
    {office, district, cands: [(name, party)...], rows: {label: {method:
    {cand: v..., '__times__': t, '__total__': tv, '__writein__': w}}},
    county: {col: v}}"""
    contests = []
    cur = None
    types_seen = {}
    # the label persists across method rows AND across page breaks: the last
    # block of a page can spill its remaining method rows onto the next page
    # (strep printed pages 422-424 do), so pending/label live outside the
    # page loop
    pending = []
    label = None
    for no, plines in enumerate(get_text(slug)):
        t = title_of(plines)
        if t:
            office, dist, title_line = t
            cur = {'office': office, 'district': dist, 'cands': [],
                   'rows': defaultdict(dict), 'county': {}}
            contests.append(cur)
            types_seen = {}
            pending = []
            label = None
            plines = [l for i, l in enumerate(plines) if l is not title_line]
        if cur is None:
            continue                 # still in the turnout section

        lines = [l for l in plines
                 if l.strip() and not l.strip().startswith('Page:')]
        if not lines:
            continue                 # trailing empty page

        def is_method(s):
            return bool(re.search(r'Election Day|AV Counting Board', s) or
                        (s.startswith('Total ') and
                         not s.startswith('Total Votes')))

        m_idx = [i for i, l in enumerate(lines) if is_method(' '.join(l.split()))]
        # header zone ends at the last Precinct-marker line (no digits, so a
        # wrapped 'Precinct 1' label line doesn't qualify); a page with no
        # method rows can still carry the section's county totals
        # (strep printed pages 189-190 do)
        hdr_end = None
        for i in range(m_idx[0] if m_idx else len(lines)):
            s = ' '.join(lines[i].split())
            if s.startswith('Precinct') and not re.search(r'\d', s):
                hdr_end = i
        if hdr_end is None:
            if not m_idx:
                continue             # no data rows at all on this page
            problems.append(f'{slug} p{no}: no Precinct header line')
            continue
        cols = header_columns(lines[:hdr_end + 1], no)
        page_type = 'A' if any(c['kind'] == 'times' for c in cols) else 'B'
        cands = [(c['name'], c['party']) for c in cols if c['kind'] == 'cand']
        prev = types_seen.get(page_type)
        if prev is not None and prev != cands:
            problems.append(f'{slug} p{no}: type {page_type} candidate '
                            f'columns differ from earlier pages: {cands} '
                            f'vs {prev}')
        types_seen.setdefault(page_type, cands)
        # keep the union of candidate columns, ordered A then B
        have = {n for n, _p in cur['cands']}
        for n, p in cands:
            if n not in have:
                cur['cands'].append((n, p))
                have.add(n)

        val_cols = [c for c in cols if c['kind'] != 'times']
        n_method = 0
        n_county = 0
        for line in lines[hdr_end + 1:]:
            s = ' '.join(line.split())
            if s.startswith('Wayne County - Total'):
                # county total printed twice, values ON the label line
                n_county += 1
                parts = s.split('Wayne County - Total')
                if len(parts) == 3:      # type A: left times, right values
                    left = ints(parts[1])
                    if len(left) != 1:
                        problems.append(f'{slug} p{no}: county row {s!r}')
                        continue
                    cur['county']['__times__'] = \
                        int(left[0][2].replace(',', ''))
                    cur['county'].update(assign(ints(parts[2]), val_cols,
                                                f'{slug} p{no} county'))
                else:
                    cur['county'].update(
                        assign(ints(parts[1]), val_cols, f'{slug} p{no} '
                               f'county'))
                continue
            if is_method(s):
                n_method += 1
                if pending:
                    label = re.sub(r'\s+', ' ', ' '.join(pending)).strip()
                    pending.clear()
                if not label:
                    problems.append(f'{slug} p{no}: method row without '
                                    f'label: {s!r}')
                    continue
                occ = list(re.finditer(
                    r'Election Day|AV Counting Board|\bTotal\b', s))
                if page_type == 'A':
                    if len(occ) != 2:
                        problems.append(f'{slug} p{no}: {len(occ)} method '
                                        f'markers (type A): {s!r}')
                        continue
                    left = ints(s[occ[0].end():occ[1].start()])
                    right = ints(s[occ[1].end():])
                    if len(left) != 1:
                        problems.append(f'{slug} p{no}: Times Cast values '
                                        f'{left} for {label!r}')
                        continue
                    vals = assign(right, val_cols, f'{slug} p{no} {label!r}')
                    vals['__times__'] = int(left[0][2].replace(',', ''))
                else:
                    vals = assign(ints(s[occ[0].end():]), val_cols,
                                  f'{slug} p{no} {label!r}')
                method = ('ED' if 'Election Day' in s else
                          'AV' if 'AV Counting Board' in s else 'TV')
                row = cur['rows'][label].setdefault(method, {})
                for k, v in vals.items():
                    if k in row and row[k] != v:
                        problems.append(f'{slug} p{no}: {label!r}/{method} '
                                        f'{k} reprinted {row[k]} vs {v}')
                    row[k] = v
            else:
                # label line(s); type A prints both halves, take the left
                if page_type == 'A':
                    parts = [p.strip() for p in
                             DUP_HALF_SPLIT.split(line.rstrip())]
                    part = next((p for p in parts if p), '')
                else:
                    part = s
                if part and part != 'Wayne County':
                    pending.append(part)
        if not n_method and not n_county:
            problems.append(f'{slug} p{no}: page with no method rows')
    return contests


def validate_contest(c, turnout, slug, split_labels):
    """Per-row and per-contest consistency checks."""
    for label, methods in c['rows'].items():
        if label not in turnout:
            problems.append(f'{slug} {c["office"]} {c["district"]}: precinct '
                            f'{label!r} not in turnout')
            continue
        t = turnout[label]
        for method in ('ED', 'AV', 'TV'):
            row = methods.get(method)
            if row is None:
                problems.append(f'{slug} {c["office"]} {c["district"]} '
                                f'{label!r}: missing {method} row')
                continue
            named = sum(v for k, v in row.items()
                        if k not in (TIMES_KEY, TOTAL_KEY, WRITEIN_KEY))
            total = row.get(TOTAL_KEY, 0)
            writein = row.get(WRITEIN_KEY, 0)
            if named + writein != total:
                problems.append(f'{slug} {c["office"]} {c["district"]} '
                                f'{label!r}/{method}: candidates {named} + '
                                f'write-in {writein} != total {total}')
            if (TIMES_KEY in row and row[TIMES_KEY] != t[method.lower()] and
                    label not in split_labels):
                problems.append(f'{slug} {c["office"]} {c["district"]} '
                                f'{label!r}/{method}: Times Cast '
                                f'{row[TIMES_KEY]} != turnout voters '
                                f'{t[method.lower()]}')
        if not all(m in methods for m in ('ED', 'AV', 'TV')):
            continue
        for key in set().union(*(set(m) for m in methods.values())):
            if not key.startswith('__'):
                ed = methods['ED'].get(key, 0)
                av = methods['AV'].get(key, 0)
                tv = methods['TV'].get(key, 0)
                if ed + av != tv:
                    problems.append(f'{slug} {c["office"]} {c["district"]} '
                                    f'{label!r} {key}: ED {ed} + AV {av} '
                                    f'!= TV {tv}')
    # county total row == sums over precincts (Total-row values)
    if c['county']:
        sums = defaultdict(int)
        times = 0
        for label, methods in c['rows'].items():
            tv = methods.get('TV')
            if not tv:
                continue
            times += tv.get(TIMES_KEY, 0)
            for k, v in tv.items():
                sums[k] += v
        for k, v in c['county'].items():
            want = times if k == TIMES_KEY else sums.get(k, 0)
            if v != want:
                problems.append(f'{slug} {c["office"]} {c["district"]}: '
                                f'county {k} {v} != precinct sum {want}')


def emit(turnout, contests):
    rows = []
    for label, e in turnout.items():
        if None in (e.get('rv'), e.get('tv'), e.get('ed'), e.get('av')):
            continue            # already flagged as incomplete
        rows.append([COUNTY, label, 'Registered Voters', '', '', '',
                     str(e['rv']), '', ''])
        rows.append([COUNTY, label, 'Ballots Cast', '', '', '',
                     str(e['tv']), str(e['ed']), str(e['av'])])
    for c in contests:
        office, dist = c['office'], c['district']
        for label, methods in c['rows'].items():
            tv = methods.get('TV')
            if not tv:
                continue         # incomplete rows already flagged
            for name in [k for k in tv if k not in (TIMES_KEY, TOTAL_KEY)]:
                total = tv[name]
                if total <= 0:
                    continue
                ed = methods['ED'].get(name, 0)
                av = methods['AV'].get(name, 0)
                if name == WRITEIN_KEY:
                    cand, party = WRITEIN_KEY, ''
                else:
                    cand = name
                    for old, new in NAME_FIXES:
                        cand = cand.replace(old, new)
                    if office == 'President':
                        cand = cand.replace('/', ' ')
                    if office == 'Straight Party':
                        cand = STRAIGHT_PARTY_NAMES.get(cand, cand)
                    party = next((p for n, p in c['cands'] if n == name), '')
                rows.append([COUNTY, label, office, dist, cand, party,
                             str(total), str(ed), str(av)])
    return rows


def verify(rows):
    """Compare (votes, election_day, absentee) sums against the county file."""
    want = {}
    for row in csv.DictReader(open(COUNTY_CSV)):
        if row['county'] == COUNTY:
            want[(row['office'], row['district'], row['candidate'])] = (
                int(row['votes']), int(row['election_day'] or 0),
                int(row['absentee'] or 0))
    got = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        if r[2] in ('Registered Voters', 'Ballots Cast'):
            continue
        g = got[(r[2], r[3], r[4])]
        g[0] += int(r[6])
        g[1] += int(r[7] or 0)
        g[2] += int(r[8] or 0)
    for k, v in want.items():
        g = got.get(k)
        if g is None:
            if v != (0, 0, 0):
                problems.append(f'county row never parsed: {k}: {v}')
        elif tuple(g) != v:
            problems.append(f'county mismatch {k}: file {v} vs parsed '
                            f'{tuple(g)}')
    for k in got:
        if k not in want:
            problems.append(f'parsed row not in county file: {k}')


def main():
    turnouts = {}
    for slug in STATEMENT_SLUGS + ('votestats',):
        stats, county = parse_turnout(slug)
        turnouts[slug] = (stats, county)
    base = turnouts['str']
    for slug in ('prez', 'ussen', 'cong', 'strep', 'votestats'):
        stats, county = turnouts[slug]
        for label, e in stats.items():
            b = base[0].get(label)
            if b is None:
                problems.append(f'{slug}: precinct {label!r} not in str '
                                f'turnout')
            elif e != b:
                problems.append(f'{slug}: turnout {label!r} differs: {e} vs '
                                f'{b}')
        for label in base[0]:
            if label not in stats:
                problems.append(f'{slug}: missing turnout for {label!r}')
        if county != base[1]:
            problems.append(f'{slug}: county turnout differs: {county} vs '
                            f'{base[1]}')

    contests = []
    for slug in STATEMENT_SLUGS:
        cs = parse_contests(slug, turnouts[slug][0])
        # a precinct split across two legislative districts prints its Times
        # Cast in BOTH district sections, each carrying only that ballot
        # style's ballots; the parts sum to the turnout voters (Westland P24
        # in State House 11+16, Canton P29 in State House 20+21). Detect
        # such labels so their per-contest Times Cast isn't flagged against
        # the full turnout.
        times_map = defaultdict(lambda: defaultdict(list))
        for c in cs:
            for label, methods in c['rows'].items():
                for m in ('ED', 'AV', 'TV'):
                    row = methods.get(m)
                    if row and TIMES_KEY in row:
                        times_map[label][m].append(row[TIMES_KEY])
        split_labels = set()
        for label, tm in times_map.items():
            t = turnouts[slug][0].get(label)
            if t is None:
                continue
            if all(len(tm.get(m, [])) > 1 and sum(tm[m]) == t[m.lower()]
                   for m in ('ED', 'AV', 'TV')):
                split_labels.add(label)
        if split_labels:
            print(f'NOTE: {slug}: split-precinct Times Cast accepted for '
                  f'{sorted(split_labels)}')
        for c in cs:
            validate_contest(c, turnouts[slug][0], slug, split_labels)
        contests += cs

    rows = emit(base[0], contests)
    verify(rows)
    for p in problems:
        print('PROBLEM:', p)
    if problems:
        sys.exit(f'{COUNTY}: {len(problems)} problems')
    rows.sort(key=lambda r: (r[1], r[2], r[3], r[5], r[4]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['county', 'precinct', 'office', 'district', 'candidate',
                    'party', 'votes', 'election_day', 'absentee'])
        w.writerows(rows)
    precs = sorted({r[1] for r in rows})
    print(f'{OUT}: {len(rows)} rows, {len(precs)} precincts, '
          f'{len({(r[2], r[3]) for r in rows})} (office, district) pairs, '
          f'{len(contests)} contests, verified against {COUNTY_CSV}')


if __name__ == '__main__':
    main()