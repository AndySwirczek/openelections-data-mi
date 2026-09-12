"""Parse Branch County's 2020 primary "Official Results" PDF (image-only).

Source: openelections-sources-mi 2020/primary/Branch County Aug 2020 Primary
Precinct Results.pdf (37pp, image-only; PaddleOCR markdown cache at
/tmp/paddleocr_md/Branch_County_Aug_2020_Primary_Precinct_Results).

Layout: p1 is a countywide turnout table (Precinct / Total Voters / Ballot
Cast / Eligible Electors / Turnout%); every contest then occupies tables of
data rows only (label + votes, NO Total Votes column and no per-contest
turnout).  Tables are HTML blobs whose header row holds a 'Precinct' cell
per block; a table can carry ONE block (most contests) or TWO side-by-side
blocks (U.S. Senate's DEM and REP halves share one table; Gilead's delegate
pair).  Headers come in two orders: candidate LAST names with party tags on
the 'Precinct' row and FIRST names above it (usual), or first names ON the
'Precinct' row with a last-name continuation row below it.  Contest titles
come from an embedded header cell or a <div> line (the OCR interleaves divs
and tables unpredictably, so divs attach by content: delegate divs match the
block covering their precinct, the rest pair with untitled blocks in page
order).  Township pages — where whole contests are write-in-only and carry
no title at all — follow the fixed office sequence [Supervisor, Clerk,
Treasurer, Trustee] x [DEM, REP]; titled tables act as anchors and untitled
ones take the earliest unclaimed slot.  Countywide blocks likewise claim
contests from a fixed ballot-order sequence.  Delegate contests are
identified by their precinct labels; DEM/REP of the same precinct pair up
(one table each), with titles/named-candidate party tags resolving the
ambiguity.

Emission: nonzero candidate rows, a Write-In row where the write-in column
is nonzero, Yes/No rows for proposals, and per-precinct 'Registered Voters'
(Eligible Electors) / 'Ballots Cast' (Total Voters) pseudo-office rows from
the turnout page (Jackson/Wayne 2020 style).  Every contest's printed
'Total' row is validated against the sum of its precinct rows.

Usage:
    .venv/bin/python src/branch_2020.py [--out <csv>]
"""
import argparse
import glob
import html
import re
import sys
from collections import defaultdict

from csv_2020_primary import write_csv

CACHE = ('/tmp/paddleocr_md/'
         'Branch_County_Aug_2020_Primary_Precinct_Results')
COUNTY = 'Branch'

TD = re.compile(r'<td([^>]*)>(.*?)</td>', re.S)
PARTY_SUFFIX = re.compile(r' - (DEM|REP)$')
TITLEISH = re.compile(r'\((?:DEM|REP)\)|\(Vote for|Millage|Proposal|'
                      r'Referendum|Delegate')

COUNTY_SEQ = []  # (office, party) in ballot order
for office in ['United States Senator',
               'Representative in Congress 7th District',
               'Rep in State Legislature 58th District',
               'County Prosecuting Attorney', 'County Sheriff',
               'County Clerk', 'County Treasurer',
               'County Register of Deeds', 'County Drain Commissioner',
               'County Surveyor'] + \
              [f'County Commissioner {o} District'
               for o in ['1st', '2nd', '3rd', '4th', '5th']]:
    for party in ['DEM', 'REP']:
        COUNTY_SEQ.append((office, party))

TWP_OFFICES = ['Supervisor', 'Clerk', 'Treasurer', 'Trustee']

# OCR-fused header rows defeat positional name assembly: full candidate
# names for the block's candidate columns in order, keyed (page, table idx).
MANUAL_HEADERS = {
    (18, 7): ['Rod Hathaway', 'Jerry Haylett'],
    (33, 3): ['Lela L. Ash', 'Jennifer Jinx Wingard'],
    (33, 9): ['Ken Delaney', 'Chancelir Murdock', 'Billie Pollack',
              'Cheryl Stechschulte', 'Gary Stechschulte'],
}
# Garbled contest titles ('Hustee' -> Trustee, 'Yinderhook' -> Kinderhook).
TITLE_FIXES = {'Hustee': 'Trustee', 'Yinderhook': 'Kinderhook'}


def cells(row):
    """[(col, text)] expanding colspan into grid positions."""
    out, col = [], 0
    for m in TD.finditer(row):
        attrs, inner = m.groups()
        cs = 1
        cms = re.search(r'colspan="?(\d+)"?', attrs)
        if cms:
            cs = int(cms.group(1))
        text = html.unescape(re.sub(r'<[^>]+>', '', inner)).strip()
        text = re.sub(r'\s+', ' ', text)
        out.append((col, text))
        col += cs
    return out


def parse_table(table):
    """[block] per 'Precinct' header cell.

    A table can stack several sub-contests vertically (each with its own
    'Precinct' header row, as on p34) — those become separate segments;
    'Precinct' cells sharing one header row split into side-by-side blocks
    by column range."""
    rows = [cells(r) for r in re.findall(r'<tr>(.*?)</tr>', table, re.S)]
    prec_i = [i for i, r in enumerate(rows)
              if any(t == 'Precinct' for _, t in r)]
    if not prec_i:
        return None
    blocks = []
    for si, name_i in enumerate(prec_i):
        nxt = prec_i[si + 1] if si + 1 < len(prec_i) else len(rows)
        zone_start = prec_i[si - 1] if si else -1
        # Header zone: rows between the previous segment's data and this
        # name row hold the titles/first names — walk back over rows with
        # no numerics (data and Total rows always carry numerics).
        hz = name_i
        while hz - 1 > zone_start and not any(
                re.fullmatch(r'[\d,]+', t or '') for _, t in rows[hz - 1]):
            hz -= 1
        # Inverted header layout: the 'Precinct' row carries FIRST names and
        # the row below it carries the last names with party tags.
        cont_i = None
        if (name_i + 1 < nxt
                and not any(re.fullmatch(r'[\d,]+', t or '')
                            for _, t in rows[name_i + 1])
                and any(PARTY_SUFFIX.search(t) for _, t in rows[name_i + 1])):
            cont_i = name_i + 1
        name_row = rows[cont_i] if cont_i is not None else rows[name_i]
        title_rows, first_rows = [], []
        for r in rows[hz:name_i]:
            texts = [t for _, t in r if t and t != '·' and t != 'Total']
            if any(TITLEISH.search(t) for t in texts):
                title_rows.append(r)
            elif texts:
                first_rows.append(r)
        if cont_i is not None:
            # first names sit on the 'Precinct' row itself
            first_rows.append(list(rows[name_i]))
        # 'Precinct' cells always sit on the first-name row; the inverted
        # layout's cont row can carry an empty cell there (p5)
        starts = [c for c, t in rows[name_i] if t == 'Precinct']
        for bi, start in enumerate(starts):
            end = starts[bi + 1] if bi + 1 < len(starts) else 10 ** 6
            roles, name_text = {}, {}
            for c, t in name_row:
                if not (start <= c < end) or c == start or not t or t == '·':
                    continue
                name_text[c] = t
                m = PARTY_SUFFIX.search(t)
                if m:
                    roles[c] = 'C'
                elif t == 'Write-in':
                    roles[c] = 'WI'
                elif t == 'Yes':
                    roles[c] = 'Y'
                elif t == 'No':
                    roles[c] = 'N'
                else:
                    roles[c] = '?'
            title = ' '.join(t for r in title_rows for c, t in r
                             if start <= c < end and t and t != '·')
            firsts = [t for r in first_rows for c, t in r
                      if start <= c < end and t and t != '·'
                      and t != 'Precinct']
            data, totals = [], {}
            for r in rows[(cont_i + 1) if cont_i is not None
                          else name_i + 1:nxt]:
                lab = ' '.join(t for c, t in r if start <= c < end and t
                               and t != '·' and not re.fullmatch(r'[\d,.]+', t))
                vals = {c: int(t.replace(',', '')) for c, t in r
                        if start <= c < end and re.fullmatch(r'[\d,]+', t or '')}
                if not vals:
                    continue  # the next segment's title/header rows
                if lab == 'Total':
                    totals.update(vals)
                elif lab:
                    data.append((lab, vals))
            blocks.append(dict(roles=roles, name_text=name_text, title=title,
                               firsts=firsts, data=data, totals=totals))
    return blocks


def split_title(raw):
    """'X (DEM) (Vote for 1)' -> (X, 'DEM')."""
    t = fix_title(re.sub(r'\s+', ' ', raw).strip())
    party = None
    m = re.search(r'\((DEM|REP)\)', t)
    if m:
        party = m.group(1)
        t = (t[:m.start()] + t[m.end():]).strip()
    t = re.sub(r'\s*\(Vote for \d+\)\s*', ' ', t).strip()
    return t, party


def page_items(path):
    """Ordered [('div', text) | ('table', html)] of a page."""
    text = open(path).read()
    items = []
    for m in re.finditer(r'<div[^>]*>(.*?)</div>|<table.*?</table>', text, re.S):
        if m.group(0).startswith('<div'):
            t = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if t and t != '·':
                items.append(('div', t))
        else:
            items.append(('table', m.group(0)))
    return items


def canon_jur(jur):
    j = re.sub(r'\s+', ' ', jur).strip().replace('BethelTwp', 'Bethel Twp')
    # OCR fuses 'NobleTwp' etc.; split before any Twp token
    j = re.sub(r'(?<=[A-Za-z])Twp\b', ' Twp', j)
    j = re.sub(r'\bTwp\b\.?', 'Township', j)
    return re.sub(r'\s+', ' ', j).strip()


def canon_label(lab, universe):
    """Map a printed label onto the turnout page's canonical form
    ('X Township, Precinct N' / 'City of X, Precinct N')."""
    m = re.match(r'^(.+?),\s*(?:Pct|Precinct)\.?\s*(\d+)$',
                 re.sub(r'\s+', ' ', str(lab)).strip())
    if m:
        target = f'{canon_jur(m.group(1))}, Precinct {m.group(2)}'
        return target if target in universe else None
    # OCR truncation ('Gilead v'): match on the label's first word when it
    # pins down exactly one jurisdiction in the universe.
    w = re.match(r"^[A-Za-z']+", str(lab).strip())
    if w:
        cands = [u for u in universe
                 if u.split(',')[0].split(' ')[0] == w.group(0)]
        if len(cands) == 1:
            return cands[0]
    return None


def fix_title(t):
    """Normalize OCR-garbled title text before interpreting it."""
    for bad, good in TITLE_FIXES.items():
        t = t.replace(bad, good)
    # 'Sherwood Township Hustee (BLM)' — BLM is a misread DEM
    return t.replace('(BLM)', '(DEM)')


def jur_key(jur):
    """Common key for 'X City' / 'City of X' / plain 'X'."""
    return re.sub(r'^(City of )?|(\s+City)$', '', jur)


def twp_of(label):
    return re.match(r'^(.+?), Precinct \d+$', label).group(1)


def map_office(title):
    t = title.strip()
    if t == 'United States Senator':
        return 'U.S. Senate', ''
    m = re.match(r'^Representative in Congress (\d+)(?:st|nd|rd|th) District$', t)
    if m:
        return 'U.S. House', m.group(1)
    m = re.match(r'^Rep in State Legislature (\d+)(?:st|nd|rd|th) District$', t)
    if m:
        return 'State House', m.group(1)
    return t, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='2020/counties/'
                    '20200804__mi__primary__branch__precinct.csv')
    args = ap.parse_args()
    errors = []

    pages = {}
    for path in sorted(glob.glob(CACHE + '/p*.md')):
        pages[int(re.search(r'p(\d+)\.md$', path).group(1))] = path

    # ---- turnout (p1) ----
    universe, turnout = [], {}
    for lab, vals in parse_table(open(pages[1]).read())[0]['data']:
        m = re.match(r'^(.+?),\s*(?:Pct|Precinct)\.?\s*(\d+)$',
                     re.sub(r'\s+', ' ', lab).strip())
        if not m:
            errors.append(f'turnout: unmapped label {lab!r}')
            continue
        lab = f'{canon_jur(m.group(1))}, Precinct {m.group(2)}'
        universe.append(lab)
        turnout[lab] = vals
    if len(universe) != 22:
        errors.append(f'turnout: {len(universe)} precincts, expected 22')

    # ---- walk the contest pages, one record per block ----
    recs = []
    for pno in sorted(pages):
        if pno == 1:
            continue
        items = page_items(pages[pno])
        divs = [t for k, t in items if k == 'div']
        page_blocks = []
        for ti, payload in enumerate(t for k, t in items if k == 'table'):
            blocks = parse_table(payload)
            if blocks is None:
                errors.append(f'p{pno}: table with no Precinct header')
                continue
            for bi, block in enumerate(blocks):
                page_blocks.append(dict(page=pno, ti=ti, bi=bi, block=block))
        # attach divs by content: delegate divs match the block covering
        # their precinct (preferring blocks whose candidate tags carry the
        # div's party); office divs pair with untitled blocks, preferring
        # matching party tags. Unmatched office divs are notes only — the
        # countywide/township sequences infer those identities anyway.
        used = [False] * len(page_blocks)

        def tags_of(rec):
            return {PARTY_SUFFIX.search(t).group(1)
                    for t in rec['block']['name_text'].values()
                    if PARTY_SUFFIX.search(t)}

        for d in divs:
            hit = None
            dp = (re.search(r'\((DEM|REP)\)', d) or [None, None])[1]
            if 'Delegate' in d:
                m = re.match(r'^(.+?),\s*(?:Pct|Precinct)\s*(\d+)\s+Delegate', d)
                if m:
                    want = (jur_key(canon_jur(m.group(1))), m.group(2))
                    cands = []
                    for i, rec in enumerate(page_blocks):
                        if used[i] or rec['block']['title']:
                            continue  # titled delegate tables self-identify
                        labs = [canon_label(lab, universe)
                                for lab, _ in rec['block']['data']]
                        if None in labs or not labs:
                            continue
                        have = {(jur_key(twp_of(l)),
                                 re.search(r'(\d+)$', l).group(1))
                                for l in labs}
                        if have == {want}:
                            cands.append(i)
                    tagged = [i for i in cands if dp in tags_of(page_blocks[i])]
                    hit = (tagged or cands or [None])[0]
            else:
                # A titled block on this page already covering the div's
                # (office, party) makes the div redundant — the title
                # identifies its block; don't let the div steal an untitled
                # block that belongs to a later, div-less contest.
                d_office, d_party = split_title(d)
                taken = any(
                    (lambda o, p: o == d_office and p in (d_party, None))(
                        *split_title(r['block']['title']))
                    for r in page_blocks if r['block']['title'])
                if taken:
                    print(f'NOTE: p{pno} div redundant (sequence covers it): '
                          f'{d!r}')
                    continue
                rest = [i for i, rec in enumerate(page_blocks)
                        if not used[i] and not rec['block']['title']]
                tagged = [i for i in rest
                          if dp and dp in tags_of(page_blocks[i])]
                hit = (tagged or rest or [None])[0]
            if hit is None:
                if 'Delegate' in d:
                    errors.append(f'p{pno}: delegate div unmatched: {d!r}')
                else:
                    print(f'NOTE: p{pno} div redundant (sequence covers it): '
                          f'{d!r}')
            else:
                page_blocks[hit]['div'] = d
                used[hit] = True
        for rec in page_blocks:
            rec.setdefault('div', None)
            recs.append(rec)

    # ---- identity resolution ----
    county_claimed = {}
    twp_claimed = defaultdict(dict)
    for rec in recs:
        block = rec['block']
        if any(r in ('Y', 'N') for r in block['roles'].values()):
            rec['kind'] = 'proposal'
            rec['title'], rec['party'] = split_title(block['title'])
            continue
        title = block['title'] or rec['div'] or ''
        rec['title'] = title
        if 'Delegate' in title:
            rec['kind'] = 'delegate'
            office, rec['party'] = split_title(title)
            rec['office'] = office + ' to County Convention'
            continue
        labels = {canon_label(lab, universe) for lab, _ in block['data']}
        if None in labels or not labels:
            errors.append(f'p{rec["page"]} t{rec["ti"]}: unmapped labels '
                          f'{sorted({l for l, _ in block["data"]})}')
            rec['kind'] = '?'
            continue
        rec['precincts'] = sorted(labels)
        # Delegate section (pp32-35): an untitled single-precinct block is a
        # delegate table whose title OCR dropped (write-in-only ones carry
        # no candidate header at all); the party comes from the DEM/REP
        # pairing below.
        if rec['page'] >= 32 and len(labels) == 1 and not title:
            rec['kind'] = 'delegate'
            rec['office'] = (f'{sorted(labels)[0]} '
                             f'Delegate to County Convention')
            continue
        office, rec['party'] = split_title(title)
        twp_set = {twp_of(p) for p in labels if 'Township' in p}
        if len(twp_set) == 1:
            twp = twp_set.pop()
            rec['kind'] = 'township'
            seq = [(f'{twp} {o}', pt) for pt in ('DEM', 'REP')
                   for o in TWP_OFFICES]
            claimed = twp_claimed[twp]
            m = re.match(rf'^{re.escape(twp)} '
                         rf'(Supervisor|Clerk|Treasurer|Trustee)$', office)
            if m and rec['party']:
                key = (office, rec['party'])
            else:
                key = next(k for k in seq if k not in claimed)
                office, rec['party'] = key
            if office and m is None and office != key[0]:
                errors.append(f'p{rec["page"]} t{rec["ti"]}: bad township '
                              f'office {office!r}')
                rec['kind'] = '?'
                continue
            if key in claimed:
                errors.append(f'p{rec["page"]} t{rec["ti"]}: {twp} slot '
                              f'{key} claimed twice')
            claimed[key] = True
            rec['office'] = office
            continue
        rec['kind'] = 'county'
        if office and rec['party']:
            key = (office, rec['party'])
            if key not in COUNTY_SEQ:
                errors.append(f'p{rec["page"]} t{rec["ti"]}: bad county '
                              f'office {key!r}')
                rec['kind'] = '?'
                continue
        else:
            left = [k for k in COUNTY_SEQ if k not in county_claimed]
            if not left:
                errors.append(f'p{rec["page"]} t{rec["ti"]}: county sequence '
                              f'exhausted (office {office!r})')
                rec['kind'] = '?'
                continue
            key = left[0]
            office, rec['party'] = key
        if key in county_claimed:
            errors.append(f'p{rec["page"]} t{rec["ti"]}: county slot {key} '
                          f'claimed twice')
        county_claimed[key] = True
        rec['office'], rec['district'] = map_office(office)

    missing = [k for k in COUNTY_SEQ if k not in county_claimed]
    if missing:
        errors.append(f'unclaimed countywide contests: {missing}')

    # ---- delegates: pair DEM/REP per precinct ----
    dele = defaultdict(list)
    for rec in recs:
        if rec['kind'] == 'delegate':
            labels = {canon_label(lab, universe) for lab, _ in
                      rec['block']['data']}
            if None in labels or len(labels) != 1:
                errors.append(f'p{rec["page"]} t{rec["ti"]}: delegate labels '
                              f'{labels}')
                continue
            rec['precinct'] = labels.pop()
            dele[rec['precinct']].append(rec)
    for precinct, group in dele.items():
        parties = {r['party'] for r in group if r.get('party')}
        unknown = [r for r in group if not r.get('party')]
        if len(group) != 2:
            errors.append(f'{precinct}: {len(group)} delegate tables '
                          f'(expected 2)')
        elif len(unknown) > 1:
            errors.append(f'{precinct}: {len(unknown)} delegate tables '
                          f'without a party')
        elif unknown:
            rest = ({'DEM', 'REP'} - parties).pop()
            for r in unknown:
                r['party'] = rest

    # ---- candidate name assembly ----
    for rec in recs:
        if rec['kind'] in ('proposal', '?'):
            continue
        block = rec['block']
        cand_cols = sorted(c for c, r in block['roles'].items() if r == 'C')
        manual = MANUAL_HEADERS.get((rec['page'], rec['ti']))
        if manual is not None:
            # OCR-fused header rows also leave candidate columns tagged '?'
            # (e.g. a bare 'REP' tag cell or a wrapped 'Stechschulte -'
            # fragment), so manual headers claim those columns too.
            cols = sorted(c for c, r in block['roles'].items()
                          if r in ('C', '?'))
            if len(manual) != len(cols):
                errors.append(f'p{rec["page"]} t{rec["ti"]}: manual header '
                              f'has {len(manual)} names, '
                              f'{len(cols)} columns')
            rec['cands'] = list(zip(cols, manual))
            continue
        last = {}
        for c in cand_cols:
            t = block['name_text'].get(c, '')
            m = PARTY_SUFFIX.search(t)
            if m:
                rec['party'] = rec['party'] or m.group(1)
                last[c] = t[:m.start()].strip()
            else:
                last[c] = t
                errors.append(f'p{rec["page"]} t{rec["ti"]}: candidate '
                              f'column without a party tag: {t!r}')
        firsts = block['firsts']
        if firsts and len(firsts) == len(cand_cols):
            rec['cands'] = [(c, f'{f} {last[c]}'.strip())
                            for c, f in zip(cand_cols, firsts)]
        else:
            rec['cands'] = [(c, last[c]) for c in cand_cols]
            if firsts:
                errors.append(f'p{rec["page"]} t{rec["ti"]}: {len(firsts)} '
                              f'first-name cells vs {len(cand_cols)} '
                              f'candidate columns (names joined as-is)')

    # ---- validate printed totals ----
    for rec in recs:
        if rec['kind'] == '?':
            continue
        block = rec['block']
        for c, total in sorted(block['totals'].items()):
            s = sum(v.get(c, 0) for _, v in block['data'])
            if s != total:
                errors.append(f'p{rec["page"]} t{rec["ti"]} '
                              f'{rec.get("office")}: col {c} sum {s} != '
                              f'printed Total {total}')

    # ---- emission ----
    rows = []

    def emit(precinct, office, district, party, cand, votes):
        rows.append(dict(county=COUNTY, precinct=precinct, office=office,
                         district=district, party=party, candidate=cand,
                         votes=votes))

    for rec in recs:
        if rec['kind'] == '?':
            continue
        block = rec['block']
        if rec['kind'] == 'proposal':
            office = rec['title']
            for lab, vals in block['data']:
                p = canon_label(lab, universe)
                for c, role in sorted(block['roles'].items()):
                    if role == 'Y':
                        emit(p, office, '', '', 'Yes', vals.get(c, 0))
                    elif role == 'N':
                        emit(p, office, '', '', 'No', vals.get(c, 0))
            continue
        if rec['kind'] == 'delegate':
            for lab, vals in block['data']:
                p = canon_label(lab, universe)
                for c, name in rec.get('cands', []):
                    if vals.get(c):
                        emit(p, rec['office'], '', rec['party'] or '', name,
                             vals[c])
                wi = sum(vals.get(c, 0) for c, r in block['roles'].items()
                         if r == 'WI')
                if wi:
                    emit(p, rec['office'], '', rec['party'] or '',
                         'Write-In', wi)
            continue
        office = rec['office']
        district = rec.get('district', '')
        party = rec['party'] or ''
        for lab, vals in block['data']:
            p = canon_label(lab, universe)
            for c, name in rec.get('cands', []):
                if vals.get(c):
                    emit(p, office, district, party, name, vals[c])
            wi = sum(vals.get(c, 0) for c, r in block['roles'].items()
                     if r == 'WI')
            if wi:
                emit(p, office, district, party, 'Write-In', wi)

    for p in universe:
        emit(p, 'Registered Voters', '', '', '', turnout[p][3])
        emit(p, 'Ballots Cast', '', '', '', turnout[p][1])

    if errors:
        print('ERRORS:')
        for e in errors[:60]:
            print(' ', e)
        print(f'total errors: {len(errors)}')
        sys.exit(1)
    write_csv(COUNTY, rows)
    print(f'{len(rows)} rows, {len(recs)} contest blocks, '
          f'{len(universe)} precincts')


if __name__ == '__main__':
    main()