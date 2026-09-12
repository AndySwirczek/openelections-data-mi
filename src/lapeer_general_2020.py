"""Parse Lapeer County Nov 2020 general "Statement of Votes Cast" PDF into
a per-county precinct CSV, then verify against 2020/20201103__mi__general__county.csv.

Source (openelections-sources-mi/2020/general/):
  Lapeer MI Precinct.pdf   533-page ES&S statement, contest-major

Page grammar:
- Pages 1-6: a turnout section (per precinct Election Day / AV Counting
  Boards / Total rows of Registered Voters, Cards Cast, Voters Cast, %
  Turnout), ending in the county's own block ('Lapeer County Michigan -'
  ... 'County - Total' + Election Day / AV Counting Boards rows).
- Then ~163 contest sections, one per '(Vote for N)' title line. A
  section prints A/B page pairs covering the precinct list (label in both
  halves on type-A pages, once on type-B): type A carries Times Cast +
  Registered Voters (2 left values per method row) plus the next batch of
  candidate columns; type B carries further candidates plus Total Votes /
  Unresolved Write-In. Headers are horizontal (left-aligned wrap at the
  column x) and candidate columns carry (PARTY) codes; qualified write-in
  candidates wrap as '<Name>' + 'Qualified Write In'. Total Votes includes
  the qualified write-in columns but EXCLUDES Unresolved Write-In. Each
  section's final A and final B pages close with the county block: skip
  from the 'Lapeer County Michigan -' line until 'County - Total', whose
  values sit ON the line (type A: left times+registered, right values).

Validation: per precinct/method candidates (incl. qualified write-ins)
== Total Votes; ED + AV == TV per column; Times Cast == the turnout
section's Voters Cast and Registered == its Registered Voters; the
county row == the precinct sums; and every (office, district, candidate)
in the county file is verified on votes (the county file has no
breakdown columns; its President 'Write-in' aggregates the unresolved
and qualified write-ins).

Usage:
    .venv/bin/python src/lapeer_general_2020.py
"""
import csv
import os
import re
import subprocess
import sys
from collections import defaultdict

SRC = '/Users/dwillis/code/openelections-sources-mi/2020/general/'
PDF = 'Lapeer MI Precinct.pdf'
COUNTY = 'Lapeer'
OUT = '2020/counties/20201103__mi__general__lapeer__precinct.csv'
COUNTY_CSV = '2020/20201103__mi__general__county.csv'

TITLE_MAP = [
    (re.compile(r'^Straight Party \(Vote for \d+\)$'), ('Straight Party', '')),
    (re.compile(r'^President/Vice President \(Vote for \d+\)$'),
     ('President', '')),
    (re.compile(r'^United States Senator \(Vote for \d+\)$'),
     ('U.S. Senate', '')),
    (re.compile(r'^Rep in Congress (\d+)(?:st|nd|rd|th) District'
                r' \(Vote for \d+\)$'), ('U.S. House', None)),
    (re.compile(r'^Rep in State Legislature (\d+)(?:st|nd|rd|th) District'
                r' \(Vote for \d+\)$'), ('State House', None)),
]

# the president header wraps mid-word: 'Blankenship/Willia' + 'm Mohr'
NAME_FIXES = [('Willia m', 'William')]
TITLE_FIXES = [('Commisioner', 'Commissioner')]

INT_RX = re.compile(r'[\d,]+')
# value columns are stored under their printed header names; the Times Cast
# and Registered Voters values live under synthetic keys
TIMES_KEY = '__times__'
REG_KEY = '__reg__'
TOTAL_KEY = 'Total Votes'
WRITEIN_KEY = 'Unresolved Write-In'
# stray label fragments printed around the header of every page
STRAYS = {'County', 'Lapeer County', 'Michigan', 'Lapeer County Michigan'}
COUNTY_BLOCK = 'Lapeer County Michigan -'
COUNTY_TOTAL = 'County - Total'

problems = []


def get_text():
    txt = '/tmp/lapeer_precinct.txt'
    if not os.path.exists(txt):
        subprocess.run(['pdftotext', '-layout', SRC + PDF, txt], check=True)
    return [pg.split('\n') for pg in open(txt).read().split('\f')]


def title_of(plines):
    """(title, line) if a contest title heads this page."""
    for line in plines[:8]:
        s = ' '.join(line.split())
        if re.search(r'\(Vote for \d+\)$', s) and not s.startswith('Page:'):
            return s, line
    return None


def is_int(tok):
    return bool(INT_RX.fullmatch(tok))


def ints(text):
    return [(m.start(), m.end(), m.group())
            for m in re.finditer(r'[\d,]+', text)]


def ordinal(n):
    n = int(n)
    if n % 100 in (11, 12, 13):
        return f'{n}th'
    return f'{n}{ {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th") }'


def map_office(title):
    """(office, district) for a contest title."""
    for rx, (office, dist) in TITLE_MAP:
        m = rx.match(title)
        if m:
            if dist is None:
                dist = m.group(1)
            return office, dist
    base = re.sub(r'\s*\(Vote for \d+\)$', '', title)
    for old, new in TITLE_FIXES:
        base = base.replace(old, new)
    # '<office> for <jurisdiction>' prints jurisdiction-last; the repo
    # convention (Saginaw etc.) is jurisdiction-first
    m = re.match(r'^(.*) for (.+)$', base)
    if m:
        office, juris = m.group(1).strip(), m.group(2).strip()
        if office == 'District Library Board Member':
            base = f'{juris} Board Member'
        elif office == 'Township Library Board Member':
            base = (f'{juris} Library Board Member'
                    if juris.endswith(' Township')
                    else f'{juris} Township Library Board Member')
        elif office == 'School Board Member':
            base = f'{juris} School Board Member'
        elif office == 'School Board Member Partial':
            base = f'{juris} School Board Member Partial'
        else:
            base = f'{juris} {office}'
    return base, ''


def parse_turnout():
    """label -> {rv, tv, ed, av}; county -> same shape."""
    stats, county = {}, {}
    pending, label, skip, started = [], None, False, False

    def flush():
        nonlocal pending
        lab = re.sub(r'\s+', ' ', ' '.join(pending)).strip()
        pending = []
        return lab

    for no, plines in enumerate(get_text()):
        if title_of(plines):
            break                      # turnout section ends at first contest
        for line in plines:
            s = ' '.join(line.split())
            if not s or s.startswith('Page:'):
                continue
            if not started:
                if 'Cards Cast' in s:
                    started = True
                continue
            if COUNTY_BLOCK in s:      # county block: skip to County - Total
                skip = True
                pending.clear()
                label = 'COUNTY'
                continue
            if skip:
                if COUNTY_TOTAL not in s:
                    continue
                skip = False
                label = 'COUNTY'
                # fall through with the row rewritten as a Total row
                s = 'Total ' + s.split(COUNTY_TOTAL, 1)[1]
            if s in STRAYS or s == 'Precinct' or s in ('Registered', 'Voters') \
                    or 'Cards Cast' in s or 'Voters Cast' in s or '% Turnout' in s:
                continue
            if re.search(r'Election Day|AV Counting Board', s) or \
                    s.startswith('Total'):
                if COUNTY_TOTAL in s:
                    label = 'COUNTY'
                    s = 'Total ' + s.split(COUNTY_TOTAL, 1)[1]
                elif pending:
                    label = flush()
                if not label:
                    problems.append(f'turnout p{no}: method row without '
                                    f'label: {s!r}')
                    continue
                nums = [t for t in s.split() if is_int(t)]
                if len(nums) != 3:
                    problems.append(f'turnout p{no}: {len(nums)} numbers for '
                                    f'{label!r}: {s!r}')
                    continue
                reg, cards, voters = (int(n.replace(',', '')) for n in nums)
                entry = county if label == 'COUNTY' else \
                    stats.setdefault(label, {})
                if s.startswith('Election Day'):
                    entry['ed'] = voters
                elif s.startswith('AV Counting Board'):
                    entry['av'] = voters
                else:
                    entry['tv'] = voters
                    entry['rv'] = reg
                    entry['cards'] = cards
            else:
                pending.append(s)
    for lab, e in list(stats.items()) + [('COUNTY', county)]:
        if None in (e.get('tv'), e.get('ed'), e.get('av'), e.get('rv')):
            problems.append(f'turnout: incomplete {lab!r}: {e}')
        elif e['ed'] + e['av'] != e['tv']:
            problems.append(f'turnout {lab!r}: ED {e["ed"]} + AV {e["av"]} '
                            f'!= Total {e["tv"]}')
    for key in ('rv', 'tv', 'ed', 'av'):
        tot = sum(e.get(key, 0) for e in stats.values())
        if county.get(key, 0) != tot:
            problems.append(f'turnout: county {key} {county.get(key)} != '
                            f'precinct sum {tot}')
    return stats, county


def header_columns(header_lines, pno):
    """Cluster header-zone tokens into columns [(x, name, kind, party)].

    Headers are horizontal, wrapped left-aligned at the column x: words of
    one header sit on the same line (join on a token gap <= 2), while
    continuation words of wrapped headers repeat on later lines at the same
    x (join on |x - cluster x| <= 8), tokens joined in (line, x) order.
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
        elif re.fullmatch(r'Registered(\s*Voters?)?', text):
            kind = 'reg'
        elif re.fullmatch(r'Total(\s*Votes?)?', text):
            kind = 'total'
        elif re.fullmatch(r'Unresolved\s*Write-?In', text):
            kind = 'writein'
        else:
            kind = 'cand'
            m = re.match(r'^(.*) Qualified Write In$', text)
            if m:
                text = f'{m.group(1).strip()} (W)'
            elif 'Qualified Write In' in text:
                problems.append(f'p{pno}: unmerged qualified write-in '
                                f'header {text!r}')
        out.append({'x': c['x'], 'name': text, 'kind': kind, 'party': party})
    return out


def assign(vals, cols, ctx):
    """Map parsed ints (as (start, end, text)) onto columns, ordinally
    (nearest-x fallback)."""
    named = [c['name'] for c in cols]
    if len(vals) == len(cols):
        return {c['name']: int(v[2].replace(',', ''))
                for c, v in zip(cols, vals)}
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


def parse_contests(turnout):
    """One entry per contest:
    {title, cands: [(name, party)...], rows: {label: {method: {cand: v...,
    '__times__': t, '__reg__': r, '__total__': tv, '__writein__': w}}},
    county: {col: v}}"""
    contests = []
    cur = None
    types_seen = {}
    # the label persists across method rows AND across page breaks, so
    # pending/label live outside the page loop
    pending = []
    label = None
    # county-block state: from the 'Lapeer County Michigan -' line the
    # block's Total/Cumulative/zero rows are skipped until 'County - Total'
    # — but the block can spill across pages (its County - Total row lands
    # on a continuation page), and the section's own data rows can sit
    # between the block's start and its County - Total row
    county_block = False
    seen_cumulative = False
    block_vals = False          # the block's wrapped value lines (directly
                                # after the block-start line)
    for no, plines in enumerate(get_text()):
        t = title_of(plines)
        if t:
            cur = {'title': t[0], 'cands': [], 'rows': defaultdict(dict),
                   'county': {}}
            contests.append(cur)
            types_seen = {}
            pending, label = [], None
            county_block, seen_cumulative = False, False
            block_vals = False
            plines = [l for l in plines if l is not t[1]]
        if cur is None:
            continue                 # still in the turnout section

        lines = [l for l in plines
                 if l.strip() and not l.strip().startswith('Page:')]
        if not lines:
            continue                 # blank separator page

        def is_method(s):
            return bool(re.search(r'Election Day|AV Counting Board', s) or
                        (s.startswith('Total') and
                         not s.startswith('Total Votes')))

        m_idx = [i for i, l in enumerate(lines) if is_method(' '.join(l.split()))]
        # header zone ends at the last Precinct-marker line (no digits, so a
        # wrapped 'Precinct 1' label line doesn't qualify)
        hdr_end = None
        for i in range(m_idx[0] if m_idx else len(lines)):
            s = ' '.join(lines[i].split())
            if s.startswith('Precinct') and not re.search(r'\d', s):
                hdr_end = i
        if hdr_end is None:
            if m_idx:
                problems.append(f'p{no}: no Precinct header line')
                continue
            hdr_end = -1             # county-only continuation page
        cols = header_columns(lines[:hdr_end + 1], no)
        page_type = 'A' if any(c['kind'] == 'times' for c in cols) else 'B'
        left_cols = [c for c in cols if c['kind'] in ('times', 'reg')]
        for c in left_cols:
            # the Times Cast / Registered Voters values live under
            # synthetic keys so they don't count as candidates
            c['name'] = TIMES_KEY if c['kind'] == 'times' else REG_KEY
        right_cols = [c for c in cols
                      if c['kind'] in ('cand', 'total', 'writein')]
        cands = [(c['name'], c['party']) for c in cols if c['kind'] == 'cand']
        # B pages come in two flavors: bulk candidate pages and the tail
        # page carrying Total Votes / Unresolved Write-In
        kind = (page_type, tuple(c['kind'] for c in right_cols))
        prev = types_seen.get(kind)
        if prev is not None and prev != cands:
            problems.append(f'p{no}: type {page_type} candidate columns '
                            f'differ from earlier pages: {cands} vs {prev}')
        types_seen.setdefault(kind, cands)
        # keep the union of candidate columns, ordered as printed
        have = {n for n, _p in cur['cands']}
        for n, p in cands:
            if n not in have:
                cur['cands'].append((n, p))
                have.add(n)

        # a county-continuation page repeats the header and carries only the
        # all-zero Cumulative rows plus the County - Total reprints
        cum_zone = False
        for line in lines[hdr_end + 1:]:
            s = ' '.join(line.split())
            if s.startswith('Cumulative'):
                cum_zone = True
                break
            if COUNTY_TOTAL in s:
                break
            if is_method(s) and any(int(v.replace(',', ''))
                                    for _a, _b, v in ints(s)):
                break

        n_method = 0
        n_county = 0
        for line in lines[hdr_end + 1:]:
            s = ' '.join(line.split())
            if county_block:
                # from 'Lapeer County Michigan -' to 'County - Total': the
                # block's own Total/Cumulative/zero rows and wrapped value
                # lines are skipped; a method row with any NONZERO value
                # means real data resumed (the block can spill across page
                # breaks, and its Cumulative rows may precede the next
                # page's data), and the block can carry lettered data-label
                # lines in between
                if COUNTY_TOTAL in s:
                    county_block, seen_cumulative = False, False
                elif not re.search(r'[A-Za-z]', s) and block_vals:
                    continue             # block's wrapped value lines
                elif s.startswith('Cumulative'):
                    seen_cumulative = True
                    block_vals = False
                    continue
                elif is_method(s):
                    block_vals = False
                    vs = [int(v.replace(',', ''))
                          for _a, _b, v in ints(s)]
                    if not vs or (seen_cumulative and not any(vs)):
                        continue         # the block's own rows
                    county_block, seen_cumulative = False, False
                else:
                    block_vals = False   # lettered line: values are done
            if COUNTY_TOTAL in s:
                n_county += 1
                pending.clear()          # drop any header-wrapped strays
                parts = s.split(COUNTY_TOTAL)
                vals = {}
                if len(parts) == 3:      # type A: left times, reg
                    left = ints(parts[1])
                    if len(left) != 2:
                        problems.append(f'p{no}: county left values '
                                        f'{left}')
                    else:
                        vals[TIMES_KEY] = int(left[0][2].replace(',', ''))
                        vals[REG_KEY] = int(left[1][2].replace(',', ''))
                    vals.update(assign(ints(parts[2]), right_cols,
                                       f'p{no} county'))
                elif len(parts) == 2:
                    vals.update(assign(ints(parts[1]), right_cols,
                                       f'p{no} county'))
                else:
                    problems.append(f'p{no}: county row {s!r}')
                for k, v in vals.items():
                    if k in cur['county'] and cur['county'][k] != v:
                        problems.append(f'p{no}: county {k} reprinted '
                                        f'{cur["county"][k]} vs {v}')
                    cur['county'][k] = v
                continue
            if COUNTY_BLOCK in s:
                county_block, seen_cumulative = True, False
                block_vals = True
                pending.clear()
                continue
            if s.startswith('Cumulative'):
                continue
            if cum_zone and is_method(s):
                # the continuation page's all-zero Cumulative rows: not data
                if any(int(v.replace(',', '')) for _a, _b, v in ints(s)):
                    problems.append(f'p{no}: nonzero method row in the '
                                    f'cumulative zone: {s!r}')
                continue
            if is_method(s):
                n_method += 1
                if pending:
                    label = re.sub(r'\s+', ' ', ' '.join(pending)).strip()
                    pending.clear()
                if not label:
                    problems.append(f'p{no}: method row without label: {s!r}')
                    continue
                occ = list(re.finditer(
                    r'Election Day|AV Counting Boards|\bTotal\b', s))
                if page_type == 'A':
                    if len(occ) != 2:
                        problems.append(f'p{no}: {len(occ)} method markers '
                                        f'(type A): {s!r}')
                        continue
                    left = assign(ints(s[occ[0].end():occ[1].start()]),
                                  left_cols, f'p{no} {label!r} left')
                    vals = assign(ints(s[occ[1].end():]), right_cols,
                                  f'p{no} {label!r}')
                    vals.update(left)
                else:
                    vals = assign(ints(s[occ[0].end():]), right_cols,
                                  f'p{no} {label!r}')
                method = ('ED' if occ[0].group().startswith('Election') else
                          'AV' if occ[0].group().startswith('AV') else 'TV')
                row = cur['rows'][label].setdefault(method, {})
                for k, v in vals.items():
                    if k in row and row[k] != v:
                        problems.append(f'p{no}: {label!r}/{method} {k} '
                                        f'reprinted {row[k]} vs {v}')
                    row[k] = v
            else:
                # label line(s); type A prints both halves, take the left
                if page_type == 'A':
                    parts = [p.strip() for p in re.split(r'\s{2,}',
                                                         line.rstrip())]
                    part = next((p for p in parts if p), '')
                else:
                    part = s
                if part and part not in STRAYS:
                    pending.append(part)
        if not n_method and not n_county and hdr_end >= 0:
            problems.append(f'p{no}: page with no method rows')
    return contests


def validate_contest(c, turnout):
    """Per-row and per-contest consistency checks.

    Subset contests (village/school/college/township offices) print only
    the precincts with eligible voters, so no coverage check is possible;
    a lost section would show up as the county row != the precinct sums.
    """
    for label, methods in c['rows'].items():
        if label not in turnout:
            problems.append(f'{c["title"]}: precinct {label!r} not in turnout')
            continue
        t = turnout[label]
        for method in ('ED', 'AV', 'TV'):
            row = methods.get(method)
            if row is None:
                problems.append(f'{c["title"]} {label!r}: missing {method} '
                                f'row')
                continue
            named = sum(v for k, v in row.items()
                        if k not in (TIMES_KEY, REG_KEY, TOTAL_KEY,
                                     WRITEIN_KEY))
            total = row.get(TOTAL_KEY, 0)
            writein = row.get(WRITEIN_KEY, 0)
            # Total Votes counts the named candidates INCLUDING qualified
            # write-ins but EXCLUDES the Unresolved Write-In column
            if named != total:
                problems.append(f'{c["title"]} {label!r}/{method}: '
                                f'candidates {named} != total {total} '
                                f'(unresolved write-in {writein})')
            if TIMES_KEY in row and row[TIMES_KEY] > t[method.lower()]:
                problems.append(f'{c["title"]} {label!r}/{method}: Times '
                                f'Cast {row[TIMES_KEY]} > turnout voters '
                                f'{t[method.lower()]}')
            if REG_KEY in row and row[REG_KEY] > t['rv']:
                problems.append(f'{c["title"]} {label!r}/{method}: '
                                f'Registered {row[REG_KEY]} > turnout '
                                f'{t["rv"]}')
        for key in set().union(*(set(m) for m in methods.values())):
            if key == REG_KEY:
                continue
            ed = methods['ED'].get(key, 0)
            av = methods['AV'].get(key, 0)
            tv = methods['TV'].get(key, 0)
            if ed + av != tv:
                problems.append(f'{c["title"]} {label!r} {key}: ED {ed} + '
                                f'AV {av} != TV {tv}')
    # county total row == sums over precincts (Total-row values)
    if c['county']:
        sums = defaultdict(int)
        for label, methods in c['rows'].items():
            tv = methods.get('TV')
            if not tv:
                continue
            for k, v in tv.items():
                sums[k] += v
        for k, v in c['county'].items():
            if v != sums.get(k, 0):
                problems.append(f'{c["title"]}: county {k} {v} != precinct '
                                f'sum {sums.get(k, 0)}')


def emit(turnout, contests):
    rows = []
    for label, e in turnout.items():
        rows.append([COUNTY, label, 'Registered Voters', '', '', '',
                     str(e['rv']), '', ''])
        rows.append([COUNTY, label, 'Ballots Cast', '', '', '',
                     str(e['tv']), str(e['ed']), str(e['av'])])
    for c in contests:
        office, dist = map_office(c['title'])
        for label, methods in c['rows'].items():
            tv = methods.get('TV')
            if not tv:
                continue         # incomplete rows already flagged
            for name in [k for k in tv
                         if k not in (TIMES_KEY, REG_KEY, TOTAL_KEY)]:
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
                        cand = cand.split('/')[0].strip()
                    party = next((p for n, p in c['cands'] if n == name), '')
                rows.append([COUNTY, label, office, dist, cand, party,
                             str(total), str(ed), str(av)])
    return rows


def verify(rows):
    """Compare votes against the county file (it has no breakdown columns)."""
    want = {}
    for row in csv.DictReader(open(COUNTY_CSV)):
        if row['county'] == COUNTY:
            want[(row['office'], row['district'], row['candidate'])] = \
                int(row['votes'])
    got = defaultdict(int)
    for r in rows:
        if r[2] in ('Registered Voters', 'Ballots Cast'):
            continue
        got[(r[2], r[3], r[4])] += int(r[6])
        # the county file's President 'Write-in' aggregates the unresolved
        # AND qualified write-ins
        if r[2] == 'President' and r[4].endswith(' (W)'):
            got[('President', '', WRITEIN_KEY)] += int(r[6])

    def county_key(office, district, cand):
        # 'Write-in'/'WRITE-IN' rows alias the parsed Unresolved Write-In
        if 'write' in cand.lower() and not cand.endswith(' (W)'):
            return (office, district, WRITEIN_KEY)
        return (office, district, cand)

    for k, v in want.items():
        g = got.get(county_key(*k), 0)
        if g != v:
            problems.append(f'county mismatch {k}: file {v} vs parsed {g}')
    covered = {k[0] for k in want}
    for k in got:
        if k[0] in covered and k not in want:
            problems.append(f'parsed row not in county file: {k}')


def main():
    turnout, county = parse_turnout()
    contests = parse_contests(turnout)
    for c in contests:
        validate_contest(c, turnout)
    rows = emit(turnout, contests)
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