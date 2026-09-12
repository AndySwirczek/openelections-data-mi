"""Parse Kent County's 2020 primary "Precinct Results" PDF.

Source: openelections-sources-mi 2020/primary/Kent County Aug 2020 Primary
Precinct Results.pdf — 655pp Electionware-style report with a text layer,
same family as Wayne 2020's Precinct Reports but in one PDF: pp1-16 are a
"Turnout Data by Precinct" section (Registered Voters + Ballots Cast with
Election Day/AVCB/Total rows per precinct), pp17-642 are partisan contest
sections (all REP districts/offices first, then all DEM), and pp643-655 are
nonpartisan contests and millage/school proposals.  Every page's footer
("Kent County Elections <section> <pg> / 655") names its section.

Contest pages: rotated bottom-up header strips (Times Cast, Registered
Voters, candidates..., Write-in [Votes], Total Votes) over precinct blocks of
Election Day / AVCB / Total rows; every cell is printed including zeros, and
each page reprints a "Kent County - Total" row (at the section top, at the
end, or both — Plainfield prints it three times).  Values are right-aligned
within their column, so numbers pair with strips by x1-to-x0 distance.  A
line's Registered Voters cell sometimes sits ~1pt above the method row and
must merge into it.  Turnout-page rows are "pct% RV BC" triples.

School-district sections (Lowell Area Schools, Cedar Springs, AAESA x3,
Wayland S.D.) print only the precincts inside the district with
DISTRICT-RESTRICTED Times Cast/Registered Voters (Ada P3: TC 18 vs 192
countywide) — their TC/RV must not be compared to the turnout section, and
pseudo rows come solely from the turnout section.

Emission (2020 set conventions, Jackson style): candidate rows, Write-In
rows (from the Write-in column, when > 0), Yes/No with blank party on
proposals, plus "Registered Voters" / "Ballots Cast" pseudo rows once per
precinct.  Offices: map_office for federal/state; "County ..." for county
offices; "<jurisdiction> <role>" for township offices (jurisdiction spelled
out from the turnout labels); proposals keep the printed title.
"""
import re
import sys
from collections import OrderedDict, defaultdict

import pdfplumber

from csv_2020_primary import write_csv

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Kent County Aug 2020 Primary Precinct Results.pdf')
COUNTY = 'Kent'
DIGIT = re.compile(r'[\d,]+')
PCT = re.compile(r'[\d.]+%$')
TURNOUT_SECTION = 'Turnout Data by Precinct'
ROW_SPACING = 14.5
# school/ISD sections print district-restricted Times Cast/Registered Voters
DISTRICT_RESTRICTED = ('Lowell Area Schools', 'Cedar Springs Public Schools',
                       'AAESA', 'Wayland S.D.')
HEADER_WORDS = {'Precinct', '%', 'Turnout', 'RegisteredV', 'oters',
                'Ballots', 'Cast'}
AUX_NORMS = ('timescast', 'registeredvoters')
SPECIAL_NORMS = {'writein', 'writeinvotes', 'totalvotes', 'yes', 'no'}
TOWNSHIP_ROLES = ('Supervisor', 'Clerk', 'Treasurer', 'Trustee')


def cluster_tops(items, tol=3.0):
    lines = []
    for w in sorted(items, key=lambda w: (w['top'], w.get('x0', 0))):
        if lines and abs(w['top'] - lines[-1][-1]['top']) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines


def page_lines(pg):
    """Word lines above the footer, each [(top, words sorted by x0)]."""
    words = [w for w in pg.filter(lambda o: o.get('upright', True)).extract_words()
             if w['top'] < pg.height - 32]
    out = []
    for line in cluster_tops(words):
        text = ' '.join(w['text'] for w in sorted(line, key=lambda w: w['x0']))
        if text.startswith('Kent County Elections'):
            continue
        out.append((line[0]['top'], sorted(line, key=lambda w: w['x0'])))
    return out


def rotated_strips(pg):
    """Decode rotated bottom-up header strips into ordered columns."""
    rot = [c for c in pg.chars if not c['upright']]
    strips = []
    for c in sorted(rot, key=lambda c: c['x0']):
        for s in strips:
            if abs(s['x0'] - c['x0']) <= 2:
                s['chars'].append(c)
                break
        else:
            strips.append({'x0': c['x0'], 'chars': [c]})
    for s in strips:
        s['text'] = ''.join(c['text'] for c in
                            sorted(s['chars'], key=lambda c: -c['top'])).strip()
    return strips


def is_aux(text):
    return re.sub(r'[\s-]', '', text).lower().rstrip() in AUX_NORMS


def is_special(text):
    return re.sub(r'[\s-]', '', text).lower().rstrip() in SPECIAL_NORMS


def section_party(name):
    m = re.search(r'\((R|D)\)$', name.strip())
    return {'R': 'REP', 'D': 'DEM'}[m.group(1)] if m else ''


def section_base(name):
    return re.sub(r'\s*\((R|D)\)$', '', name.strip()).strip()


def section_contest(base, jurisdictions, errors):
    """Footer section name -> (office, district) using turnout jurisdictions."""
    b = ' '.join(base.split())
    b = re.sub(r'\((R|D)\)$', '', b).strip()   # party lives in section_party
    if b == 'US Senator':
        return 'U.S. Senate', ''
    m = re.match(r'Rep[.\s]+in Cong[.\s]*(?:\d+)(?:[a-z]{2}\.?)? Dist', b, re.I) \
        or re.match(r'Rep[.\s]* In Cong[.\s]*(\d+)', b, re.I)
    if 'Cong' in b:
        m = re.search(r'(\d+)', b)
        return 'U.S. House', m.group(1) if m else ''
    if b.startswith('State Rep'):
        return 'State House', re.search(r'(\d+)', b).group(1)
    if b == 'Sheriff':
        return 'County Sheriff', ''
    if b == 'Prosecuting Attorney':
        return 'County Prosecuting Attorney', ''
    if b.startswith('ClerkReg'):
        return 'County Clerk/Register of Deeds', ''
    if b == 'County Treasurer':
        return 'County Treasurer', ''
    if b == 'Drain Commissioner':
        return 'County Drain Commissioner', ''
    m = re.match(r'County Comm[.,]*\s*(?:\d+(?:st|nd|rd|th)\.?)? Dist', b, re.I)
    if m:
        return 'County Commissioner', re.search(r'(\d+)', b).group(1)
    m = re.match(r'(.+?)\s+Twp\.?\s+(Supervisor|Clerk|Treasurer|Trustee)$', b)
    if m:
        stem, role = m.groups()
        stem = {'Tytone': 'Tyrone'}.get(stem, stem)
        cands = [j for j in jurisdictions
                 if j.startswith(stem) and 'Township' in j]
        if len(cands) != 1:
            errors.append(f'section {base!r}: township stem {stem!r} matches '
                          f'{cands}')
            return stem + ' Township', role
        return f'{cands[0]} {role}', ''
    return None, ''     # nonpartisan: office comes from the page title


def page_title(lines):
    """Title text from the lines above the 'Precinct' caption (nonpartisan)."""
    cap_top = next((t for t, ws in lines
                    if len(ws) == 1 and ws[0]['text'] == 'Precinct'), None)
    if cap_top is None:
        return None
    norm = lambda s: re.sub(r'[\s,.]+', '', s).lower()
    acc = ''
    for top, ws in lines:
        if top > cap_top - 2:
            break
        text = ' '.join(w['text'] for w in ws)
        if not acc:
            acc = text
            continue
        if norm(acc).startswith(norm(text)):     # the second copy begins
            break
        acc += ' ' + text
    return acc or None


def parse_numbers(ws):
    """[(x1, int)] for the digit words of one line, ordered left to right."""
    out = []
    for w in ws:
        if PCT.match(w['text']):
            continue
        if DIGIT.fullmatch(w['text']):
            out.append((w['x1'], int(w['text'].replace(',', ''))))
    return sorted(out)


def pair_values(nums, vx, ctx, errors):
    """Map (x1, value) pairs onto columns by right-edge position; the
    column value-x1s come from build_columns, so pairing is exact."""
    vals = [None] * len(vx)
    for x1, v in nums:
        best = min(range(len(vx)), key=lambda i: abs(vx[i] - x1))
        if abs(vx[best] - x1) > 5:
            errors.append(f'{ctx}: value {v}@{x1:.0f} matches no column')
            continue
        if vals[best] is not None:
            errors.append(f'{ctx}: two values ({vals[best]}, {v}) map to '
                          f'column @{vx[best]:.0f}')
        vals[best] = v
    return vals


def parse():
    """-> (turnout, sections)

    turnout: OrderedDict label -> {'RV', 'ED', 'AV', 'TV'}
    sections: list of dicts {name, party, office, district, title, pages,
    candidates, cols, rows{label: {method: vals}}, county_totals}
    """
    errors = []
    turnout = OrderedDict()
    sections = []
    cur = None
    acc = TurnoutAcc(turnout, errors)

    with pdfplumber.open(SRC) as pdf:
        for pno, pg in enumerate(pdf.pages):
            lines = page_lines(pg)
            # section name from the footer line
            up_words = pg.filter(lambda o: o.get('upright', True)).extract_words()
            foot_words = [w for w in up_words if w['top'] > pg.height - 40]
            foot_text = ' '.join(w['text'] for w in
                                 sorted(foot_words, key=lambda w: w['x0']))
            m = re.match(r'Kent County Elections (.+?)\s+(\d+) / 655$',
                         foot_text.strip())
            if not m:
                errors.append(f'page {pno+1}: unparseable footer {foot_text!r}')
                continue
            name = ' '.join(m.group(1).split())

            if name == TURNOUT_SECTION:
                cur = None
                acc.feed(pno + 1, lines)
                continue
            if cur is None or cur['name'] != name:
                cur = {'name': name, 'pages': [], 'rows': OrderedDict(),
                       'raw': [], 'county_raw': [], 'county_totals': [],
                       'strips': None, 'candidates': None, 'title': None}
                sections.append(cur)
            cur['pages'].append(pno + 1)
            parse_contest_page(pno + 1, cur, lines, pg, errors)
    for sec in sections:
        finish_section(sec, errors)
    if errors:
        for e in errors[:40]:
            print('PARSE ERROR:', e)
        print(f'total parse errors: {len(errors)}')
        sys.exit(1)
    return turnout, sections, acc.county


class TurnoutAcc:
    """Accumulates the turnout section across pages: a precinct's block can
    straddle a page break (label at the bottom of one page, its Election Day
    row and the rest at the top of the next — those rows carry no label and
    attach to the label carried over from the previous page)."""

    def __init__(self, turnout, errors):
        self.turnout = turnout
        self.errors = errors
        self.last_label = None
        self.county = None        # repeated 'Kent County - Total' row

    def feed(self, pno, lines):
        cur = self.last_label
        after_county = False
        for top, ws in lines:
            wtexts = {w['text'] for w in ws}
            if wtexts <= HEADER_WORDS or wtexts == {'Precinct'}:
                continue
            leftmost = ws[0]['text']
            if leftmost == 'Kent':      # repeated countywide turnout row
                vals = [v for _, v in parse_numbers(ws)]
                if len(vals) == 2:
                    self.county = vals
                else:
                    self.errors.append(f'p{pno}: county turnout row {vals}')
                after_county = True
                continue
            vals = [v for _, v in parse_numbers(ws)]
            if leftmost in ('Election', 'AVCB', 'Total'):
                if after_county and len(vals) == 1:
                    # countywide Election Day/AVCB breakdown (pct + ballots)
                    continue
                after_county = False
                if cur is None:
                    self.errors.append(f'p{pno}: {leftmost} row {vals} with '
                                       f'no precinct label')
                    continue
                self.assign(pno, cur, leftmost, vals)
            else:
                cur = ' '.join(w['text'] for w in ws)
                after_county = False
        self.last_label = cur

    def assign(self, pno, lab, left, vals):
        method = {'Election': 'ED', 'AVCB': 'AV', 'Total': 'TV'}[left]
        e = self.turnout.setdefault(lab, {'RV': None, 'ED': None, 'AV': None,
                                          'TV': None})
        if not vals:
            return
        if len(vals) == 2:
            rv, bc = vals
            if e['RV'] is None:
                e['RV'] = rv
            elif e['RV'] != rv:
                self.errors.append(f'p{pno}: {lab} RV {rv} vs {e["RV"]}')
            e[method] = bc
        else:
            e[method + '_partial'] = vals


def parse_contest_page(pno, sec, lines, pg, errors):
    strips = rotated_strips(pg)
    if sec['strips'] is None:
        sec['strips'] = strips
    elif [s['x0'] for s in strips] != [s['x0'] for s in sec['strips']]:
        errors.append(f'p{pno} [{sec["name"]}]: header strip x0s differ from '
                      f'section start')
    if sec['title'] is None:
        sec['title'] = page_title(lines)

    # Walk the lines in order.  Precinct labels are single lines (wrapped
    # labels continue within ROW_SPACING); Election Day / AVCB / Total value
    # rows attach to the current label, which is carried across page breaks.
    # Value-to-column pairing is deferred to finish_section(), after the
    # county rows have calibrated the columns' offsets.
    cur = sec.get('last_label')
    prev_lab_top = None
    # continuation pages repeat the rotated contest title above the 'Precinct'
    # caption — skip that zone, or its first line is eaten as a precinct
    # label and steals the label carried across the page break
    cap_top = next((top for top, ws in lines
                    if {w['text'] for w in ws} == {'Precinct'}), None)
    for top, ws in lines:
        if cap_top is not None and top < cap_top:
            continue
        wtexts = {w['text'] for w in ws}
        if wtexts <= HEADER_WORDS or wtexts == {'Precinct'}:
            continue
        leftmost = ws[0]['text']
        nums = parse_numbers(ws)
        if leftmost == 'Kent':
            sec['county_raw'].append(nums)
            continue
        if leftmost in ('Election', 'AVCB', 'Total'):
            if cur is None:
                # clipped label (p577 Ada Clerk(D): 'Ada Township,
                # Precinct 1' is absent from the text layer) — park the
                # block under a placeholder and infer the label later
                sec['missing'] = sec.get('missing', 0) + 1
                cur = f'<<missing#{sec["missing"]}>>'
            sec['raw'].append((pno, cur, leftmost, nums))
        else:
            text = ' '.join(w['text'] for w in ws)
            if cur is not None and prev_lab_top is not None \
                    and top - prev_lab_top < ROW_SPACING:
                cur += ' ' + text      # wrapped label continuation
            else:
                cur = text
            prev_lab_top = top
    sec['last_label'] = cur


def build_columns(sec, errors):
    """Define the section's columns from the printed values.

    Values are right-aligned per column, so clustering all value x1s yields
    the columns exactly.  The rotated header strips are then assigned to the
    clusters: each cluster takes >=1 strip, strips stay in order, and a strip
    that begins a column must leave a plausible header offset (value x1 minus
    strip x0: single-line headers ~9-22pt, wrapped candidate names up to
    ~40pt) — a wrong merge like Registered Voters+candidate runs 58+.
    """
    xs = sorted(x1 for nums in sec['county_raw'] for x1, _ in nums)
    xs += sorted(x1 for _, _, _, nums in sec['raw'] for x1, _ in nums)
    xs.sort()
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= 4:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    vx = [sum(c) / len(c) for c in clusters]
    strips = sec['strips']
    k, n = len(vx), len(strips)
    if n < k:
        errors.append(f'[{sec["name"]}]: {k} value columns but only {n} '
                      f'header strips')
        return None
    strip_roles = []
    for st in strips:
        t = re.sub(r'[\s-]+', '', st['text']).lower().strip()
        strip_roles.append({'timescast': 'TC', 'registered': 'RV',
                            'registeredvoters': 'RV', 'voters': 'RV2',
                            'writein': 'WI', 'writeinvotes': 'WI',
                            'totalvotes': 'TV'}.get(t))

    def cols_from_groups(groups):
        cols = []
        for g in groups:
            text = ''
            for idx in g:
                part = strips[idx]['text']
                if text and part[:1].islower():
                    text += part          # word continuation
                else:
                    if text:
                        text += ' '
                    text += part
            cols.append({'x0': strips[g[0]]['x0'], 'x1': vx[len(cols)],
                         'text': re.sub(r'\s+', ' ', text).strip()})
        return cols
    # A column's value right-edge sits 9.4pt right of its rightmost header
    # strip (the glyph height of the rotated line), plus 12.5pt per phantom
    # empty line slot (Lynn Afendoulis reserves 3 line slots for a 2-strip
    # name).  A column closes only where that relation holds; among the
    # feasible strip-to-column partitions the one with the fewest phantom
    # slots must be unique.
    BASE, LINE, TOL, MAXPHANTOM = 9.4, 12.5, 2.5, 2
    memo = {}

    def close_ok(c, lo, hi):
        """phantom count if strips[lo..hi] can be column c, else None"""
        d = vx[c] - strips[hi]['x0']
        j = round((d - BASE) / LINE)
        if 0 <= j <= MAXPHANTOM and abs(d - BASE - LINE * j) <= TOL:
            return j
        return None

    def rec(i, c, lo):
        """(min phantom count, #ways) placing strips[i:] on columns[c:],
        where column c is open holding strips lo..i-1 (lo < i)."""
        if i == n:
            if c != k - 1:
                return None
            j = close_ok(c, lo, i - 1)
            return (j, 1) if j is not None else None
        key = (i, c, lo)
        if key in memo:
            return memo[key]
        best = None

        def merge(cand):
            nonlocal best
            if best is None or cand[0] < best[0]:
                best = cand
            elif cand[0] == best[0]:
                best = (best[0], best[1] + cand[1])

        merge(rec(i + 1, c, lo))             # strip i joins column c
        j = close_ok(c, lo, i - 1)           # close c with lo..i-1
        if j is not None and c + 1 < k:
            r = rec(i + 1, c + 1, i)         # strip i opens column c+1
            if r:
                merge((r[0] + j, r[1]))
        memo[key] = best
        return best

    res = rec(1, 0, 0)

    def order_fallback():
        """Order-based assignment for pages whose header blocks deviate
        from the 9.4/12.5 lattice (Cascade/Alpine Twp. Clerk(D) print the
        Write-in header ~30pt left of its value).  Strips and value
        clusters are both in printed column order, so pair them
        positionally: known headers each form their own group, the
        Registered+Voters wrap pair merges, and candidate-name wrap lines
        group by the 12.5pt line pitch."""
        if any(r in ('YES', 'NO') for r in strip_roles):
            return None
        if strip_roles[0] != 'TC' or strip_roles[-1] != 'TV':
            return None
        groups = [[0]]
        for idx in range(1, n):
            prev, role = groups[-1], strip_roles[idx]
            prev_role = strip_roles[prev[-1]]
            if role and role != 'RV2':
                groups.append([idx])          # known header: own column
            elif prev_role == 'RV' and role == 'RV2':
                prev.append(idx)              # Registered + Voters wrap
            elif (not role and not prev_role
                  and strips[idx]['x0'] - strips[prev[-1]]['x0'] <= 14.5):
                prev.append(idx)              # candidate-name wrap line
            else:
                groups.append([idx])
        if len(groups) != k:
            return None
        flat = [strip_roles[g[0]] for g in groups]
        wi_n = flat.count('WI')
        expect = ['TC', 'RV'] + [None] * (k - 3 - wi_n)
        if wi_n:
            expect.append('WI')
        expect.append('TV')
        if any(r is not None and r != e for r, e in zip(flat, expect)):
            return None
        sec['fallback'] = True
        return cols_from_groups(groups)

    if not res or res[1] != 1:
        cols = order_fallback()
        if cols is not None:
            return cols
        errors.append(f'[{sec["name"]}]: {res[1] if res else 0} '
                      f'strip-to-column assignments feasible')
        return None
    # reconstruct the unique minimal-phantom path
    groups = []
    i, c, lo, remaining = 1, 0, 0, res[0]
    cur = [0]
    while True:
        if c == k - 1 and i == n:
            groups.append(cur)
            break
        r = rec(i + 1, c, lo)
        if r and r[0] == remaining:
            cur.append(i)                    # strip i joins column c
            i += 1
            continue
        j = close_ok(c, lo, i - 1)
        r2 = rec(i + 1, c + 1, i)
        if j is None or not r2 or r2[0] + j != remaining:
            errors.append(f'[{sec["name"]}]: strip assignment path lost')
            return None
        groups.append(cur)
        remaining -= j
        cur = [i]
        lo, c, i = i, c + 1, i + 1
    return cols_from_groups(groups)


def finish_section(sec, errors):
    """Pair raw value rows onto columns and build sec['rows']."""
    cols = build_columns(sec, errors)
    if cols is None:
        return
    sec['cols'] = cols
    vx = [c['x1'] for c in cols]
    for nums in sec['county_raw']:
        sec['county_totals'].append(
            pair_values(nums, vx, f'[{sec["name"]}] county', errors))
    for pno, lab, left, nums in sec['raw']:
        method = {'Election': 'ED', 'AVCB': 'AV', 'Total': 'TV'}[left]
        vals = pair_values(nums, vx, f'p{pno} [{sec["name"]}] {lab!r}', errors)
        sec['rows'].setdefault(lab, {})[method] = vals


def finalize(turnout, sections, errors):
    jurisdictions = []
    for lab, e in turnout.items():
        m = re.match(r'(.*), (?:Ward \d+ )?Precinct \d+$', lab)
        if m and m.group(1) not in jurisdictions:
            jurisdictions.append(m.group(1))

    for sec in sections:
        base = sec['name']
        office, district = section_contest(base, jurisdictions, errors)
        if office is None:
            office, district = sec['title'], ''
            if not office:
                errors.append(f'[{base}]: no page title found')
        sec['office'], sec['district'] = office, district
        sec['party'] = section_party(base)
        sec['restricted'] = any(base.startswith(p) for p in DISTRICT_RESTRICTED)

        # candidates from the columns
        cands = [c['text'] for c in sec['cols']
                 if not is_aux(c['text']) and not is_special(c['text'])]
        sec['candidates'] = cands
        # column roles
        roles = []
        for c in sec['cols']:
            n = re.sub(r'[\s-]', '', c['text']).lower().rstrip()
            if n in ('timescast',):
                roles.append('TC')
            elif n in ('registeredvoters',):
                roles.append('RV')
            elif n.startswith('writein'):
                roles.append('WI')
            elif n == 'totalvotes':
                roles.append('TV')
            elif n in ('yes', 'no'):
                roles.append(n.capitalize())
            else:
                roles.append('C')
        sec['roles'] = roles
        if roles[:2] != ['TC', 'RV'] or roles[-1] != 'TV':
            errors.append(f'[{base}]: unexpected column order {roles} '
                          f'({[c["text"] for c in sec["cols"]]})')

        # complete missing method rows columnwise (Total = ED + AVCB)
        for lab, methods in sec['rows'].items():
            have = [m for m in ('ED', 'AV', 'TV') if m in methods]
            if len(have) == 3:
                continue
            if len(have) < 2:
                errors.append(f'[{base}] {lab!r}: only {have} rows')
                continue
            want = [m for m in ('ED', 'AV', 'TV') if m not in methods][0]
            a, b = ((methods['ED'], methods['AV']) if want == 'TV' else
                    (methods['TV'], methods['AV']) if want == 'ED' else
                    (methods['TV'], methods['ED']))
            if any(v is None for v in a + b):
                errors.append(f'[{base}] {lab!r}: cannot compute {want}')
                continue
            n = len(a)
            full = ([x + y for x, y in zip(a, b)] if want == 'TV' else
                    [x - y for x, y in zip(a, b)])
            methods[want] = full
            print(f'repaired missing {want} row for {lab!r} in [{base}]')

        # short rows: printed numbers are a prefix or suffix of the computed row
        for lab, methods in sec['rows'].items():
            for mkey, vals in list(methods.items()):
                if vals.count(None) == 0:
                    continue
                others = [methods[o] for o in ('ED', 'AV', 'TV')
                          if o != mkey and o in methods
                          and methods[o].count(None) == 0]
                if len(others) < 2:
                    errors.append(f'[{base}] {lab!r} {mkey}: short row '
                                  f'{vals} without two full rows')
                    continue
                want = (others[0] + others[1] if mkey == 'TV'
                        else [x - y for x, y in zip(others[0], others[1])])
                got = [v for v in vals if v is not None]
                if want[:len(got)] == got:
                    full = want
                elif want[-len(got):] == got:
                    full = want
                else:
                    errors.append(f'[{base}] {lab!r} {mkey}: printed {got} '
                                  f'vs computed {want}')
                    continue
                methods[mkey] = full
                print(f'repaired short {mkey} row for {lab!r} in [{base}]')

        # arithmetic: Total Votes == candidates + write-in, columnwise
        for lab, methods in sec['rows'].items():
            for mkey, vals in methods.items():
                tv = vals[sec['roles'].index('TV')]
                cand_sum = sum(v for r, v in zip(sec['roles'], vals)
                               if r in ('C', 'Yes', 'No') and v is not None)
                wi = vals[sec['roles'].index('WI')] if 'WI' in sec['roles'] \
                    else 0
                if None in vals:
                    continue
                if tv != cand_sum + (wi or 0):
                    errors.append(f'[{base}] {lab!r} {mkey}: Total {tv} != '
                                  f'{cand_sum}+{wi}')
        # county totals (repeated prints must agree; clipped cells are None)
        ct = sec['county_totals']
        if not ct:
            errors.append(f'[{base}]: no county-total row')
        else:
            for v in ct[1:]:
                if any(a is not None and b is not None and a != b
                       for a, b in zip(ct[0], v)):
                    errors.append(f'[{base}]: county-total prints differ')
            sums = [0] * len(sec['cols'])
            for lab, methods in sec['rows'].items():
                if 'TV' not in methods:
                    continue
                for i, v in enumerate(methods['TV']):
                    sums[i] += v or 0
            if any(ct0 is not None and s != ct0
                   for s, ct0 in zip(sums, ct[0])):
                errors.append(f'[{base}]: precinct TV sums {sums} != county '
                              f'total {ct[0]}')
    return jurisdictions


def main():
    turnout, sections, county_row = parse()
    errors = []
    jurisdictions = finalize(turnout, sections, errors)

    # ---- turnout completion/validation ----
    stats = OrderedDict()
    for lab, e in turnout.items():
        st = {'RV': e['RV'], 'ED': e['ED'], 'AV': e['AV'], 'TV': e['TV']}
        missing = [k for k, v in st.items() if v is None]
        partial = {k[:-8]: v for k, v in e.items() if k.endswith('_partial')}
        for k in missing:
            if k == 'TV' and st['ED'] is not None and st['AV'] is not None:
                st[k] = st['ED'] + st['AV']
                print(f'turnout: repaired missing TV for {lab!r}')
            elif k == 'ED' and st['TV'] is not None and st['AV'] is not None:
                st[k] = st['TV'] - st['AV']
                print(f'turnout: repaired missing ED for {lab!r}')
            elif k == 'AV' and st['TV'] is not None and st['ED'] is not None:
                st[k] = st['TV'] - st['ED']
                print(f'turnout: repaired missing AV for {lab!r}')
            elif k in partial:
                # clipped row: the printed number is RV or BC — take BC when
                # it completes ED+AV=TV, else it is RV
                if k != 'TV':
                    print(f'turnout: {lab!r} {k} partial {partial}')
                st[k] = None
            else:
                st[k] = None
        if st['RV'] is None:
            errors.append(f'turnout {lab!r}: no RV')
        if st['ED'] is None or st['AV'] is None or st['TV'] is None:
            errors.append(f'turnout {lab!r}: incomplete {st} (partial '
                          f'{partial})')
        elif st['ED'] + st['AV'] != st['TV']:
            errors.append(f'turnout {lab!r}: ED+AV {st["ED"]+st["AV"]} != TV '
                          f'{st["TV"]}')
        stats[lab] = st
    # ---- contest TC consensus vs turnout ----
    # The turnout section double-counted one AVCB batch (Spencer Twp P2:
    # printed AV 396/TV 631 where every contest section prints 198/433, and
    # the per-ballot vote sums only fit 198).  When every contest section
    # agrees on a TC value that contradicts turnout, the sections win.
    deltas = 0
    for lab, st in stats.items():
        for m in ('ED', 'AV'):
            vals = [rows[m][sec['roles'].index('TC')]
                    for sec in sections if not sec['restricted']
                    for rows in [sec['rows'].get(lab)]
                    if rows and m in rows and rows[m].count(None) == 0]
            if len(vals) >= 3 and len(set(vals)) == 1 \
                    and vals[0] != st[m]:
                print(f'turnout: {lab!r} {m} {st[m]} contradicted by all '
                      f'{len(vals)} contest sections ({vals[0]}) — repaired')
                deltas += st[m] - vals[0]
                st[m] = vals[0]
        if st['ED'] is not None and st['AV'] is not None:
            st['TV'] = st['ED'] + st['AV']
    if county_row:
        sums = [sum(st['RV'] for st in stats.values()),
                sum(st['TV'] for st in stats.values())]
        if sums != county_row:
            if sums[0] == county_row[0] \
                    and sums[1] + deltas == county_row[1]:
                print(f'turnout: county ballots {county_row[1]} includes the '
                      f'{deltas} doubled AVCB ballots repaired above')
            else:
                errors.append(f'turnout county sums {sums} != {county_row}')

    # ---- contest precinct labels vs turnout universe ----
    universe = set(stats)
    for sec in sections:
        for lab in list(sec['rows']):
            if lab in universe:
                continue
            if lab.startswith('<<missing#'):
                # clipped label: the section's other rows give the
                # jurisdiction; ED/AV/TC + RV must match the turnout entry
                jurs = {re.match(r'(.*), Precinct \d+$', u).group(1)
                        for u in sec['rows'] if u in universe}
                ti, ri = sec['roles'].index('TC'), sec['roles'].index('RV')
                mkey, vals = next((m, v) for m, v in sec['rows'][lab].items()
                                  if v.count(None) == 0)
                meth = mkey
                cands = [u for u in universe
                         if re.match(r'(.*), Precinct \d+$', u)
                         and re.match(r'(.*), Precinct \d+$', u).group(1)
                         in jurs and u not in sec['rows']
                         and stats[u][meth] == vals[ti]
                         and stats[u]['RV'] == vals[ri]]
                if len(cands) == 1:
                    print(f'relabel {lab!r} -> {cands[0]!r} '
                          f'in [{sec["name"]}] (label clipped in source)')
                    sec['rows'][cands[0]] = sec['rows'].pop(lab)
                    continue
                errors.append(f'[{sec["name"]}]: clipped label {lab!r} '
                              f'({meth}={vals[ti]}, RV={vals[ri]}) matches '
                              f'{cands}')
                continue
            cands = [u for u in universe
                     if u.startswith(lab) and len(u) > len(lab)]
            if len(cands) == 1:
                print(f'relabel {lab!r} -> {cands[0]!r} in [{sec["name"]}]')
                sec['rows'][cands[0]] = sec['rows'].pop(lab)
            else:
                errors.append(f'[{sec["name"]}]: label {lab!r} not in turnout '
                              f'(candidates {cands})')

    # ---- TC/RV columns vs turnout (skip district-restricted sections) ----
    # Byron Twp P4 is split between the 2nd and 3rd congressional districts:
    # each US House section prints its portion, and the portions of one
    # party's sections sum to the whole-precinct turnout.  Those sections are
    # therefore checked by family-sum, everything else strictly.
    leg_secs = [sec for sec in sections if not sec['restricted']
                and re.search(r'rep\W*in\W*cong|state rep', sec['name'],
                              re.I)]
    fam = defaultdict(list)
    for sec in leg_secs:
        ti, ri = sec['roles'].index('TC'), sec['roles'].index('RV')
        kind = 'cong' if 'cong' in sec['name'].lower() else 'sh'
        for lab, methods in sec['rows'].items():
            for mkey, vals in methods.items():
                m = 'TV' if mkey == 'TV' else mkey
                fam[(kind, sec['party'], lab, m)].append(
                    (vals[ti], vals[ri]))
    for (kind, party, lab, m), parts in sorted(fam.items()):
        st = stats.get(lab)
        if not st:
            continue
        bc = st['TV' if m == 'TV' else m]
        tcs = [t for t, _ in parts]
        rvs = [r for _, r in parts]
        mult = len(parts) > 1
        if (sum(tcs) if mult else tcs[0]) != bc:
            errors.append(f'{kind} {party} {lab!r} {m}: TC {tcs} vs turnout '
                          f'{bc}')
        if (sum(rvs) if mult else rvs[0]) != st['RV']:
            errors.append(f'{kind} {party} {lab!r} {m}: RV {rvs} vs turnout '
                          f'{st["RV"]}')
    for sec in sections:
        if sec['restricted'] or sec in leg_secs:
            continue
        ti, ri = sec['roles'].index('TC'), sec['roles'].index('RV')
        for lab, methods in sec['rows'].items():
            st = stats.get(lab)
            if not st:
                continue
            for mkey, vals in methods.items():
                bc = st['TV' if mkey == 'TV' else mkey]
                if vals[ti] != bc:
                    errors.append(f'[{sec["name"]}] {lab!r} {mkey}: Times '
                                  f'Cast {vals[ti]} != turnout {bc}')
                if vals[ri] != st['RV']:
                    errors.append(f'[{sec["name"]}] {lab!r} {mkey}: RV '
                                  f'{vals[ri]} != turnout {st["RV"]}')

    if errors:
        print('ERRORS:')
        for e in errors[:60]:
            print(' ', e)
        print(f'total errors: {len(errors)}')
        sys.exit(1)

    # ---- emission ----
    rows = []
    for lab, st in stats.items():
        rows.append(dict(county=COUNTY, precinct=lab, office='Registered Voters',
                         district='', party='', candidate='', votes=st['RV']))
        rows.append(dict(county=COUNTY, precinct=lab, office='Ballots Cast',
                         district='', party='', candidate='', votes=st['TV']))
    for sec in sections:
        # candidate names sit in role order ('C' roles); Yes/No carry blank
        # party on proposals
        cand_vals = {}
        ci = 0
        for i, r in enumerate(sec['roles']):
            if r == 'C':
                cand_vals[sec['candidates'][ci]] = i
                ci += 1
            elif r in ('Yes', 'No'):
                cand_vals[r] = i     # proposals: blank party
        for lab, methods in sec['rows'].items():
            vals = methods.get('TV')
            if vals is None:
                continue
            for name, i in cand_vals.items():
                rows.append(dict(county=COUNTY, precinct=lab,
                                 office=sec['office'],
                                 district=sec['district'],
                                 party='' if name in ('Yes', 'No')
                                 else sec['party'],
                                 candidate=name, votes=vals[i]))
            if 'WI' in sec['roles']:
                wi = vals[sec['roles'].index('WI')]
                if wi:
                    rows.append(dict(county=COUNTY, precinct=lab,
                                     office=sec['office'],
                                     district=sec['district'],
                                     party=sec['party'],
                                     candidate='Write-In', votes=wi))
    write_csv(COUNTY, rows)
    print(f'{len(sections)} sections; {len(stats)} precincts; {len(rows)} rows')
    for sec in sections:
        n = len(sec['rows'])
        print(f"  [{sec['name']}] {sec['office']} d={sec['district']!r} "
              f"p={sec['party']!r}: {len(sec['candidates'])} cands, {n} precincts")


if __name__ == '__main__':
    main()