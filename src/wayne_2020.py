"""Parse Wayne County 2020 primary precinct results.

Sources (openelections-sources-mi/2020/primary/):
  Wayne MI senate_080420.pdf          U.S. Senate, DEM + REP precinct reports
  Wayne MI congress_080420.pdf        U.S. House districts 11-14, DEM + REP
  Wayne MI leg080420.pdf              State House 1-17,19-21,23 + Partial Term 4, DEM + REP
  Wayne MI voting_stats_08042020.pdf  Registered Voters / Ballots Cast per precinct

Each contest is an Electionware "Precinct Report": one contest per report, precinct
blocks of Election Day / AV Counting Board / Total rows, rotated bottom-up candidate
headers ("Gary Peters (DEM)", "Total Votes", "Unresolved Write-In" word-wrapped across
strips), every cell printed including zeros, and a final "County - Total" row.  Some
reports overflow onto pages that repeat no title (leg pages 59-64, 151-156, 198-200);
those are assigned by the district-gap rule in assign_contests().

Emission follows the 2020 method-mode precedent (Monroe/Ingham): header
county,precinct,office,district,party,candidate,votes,election_day,av_counting_boards
with candidate rows plus "Total Votes" and "Write-In" pseudo-candidates, and
"Registered Voters" / "Ballots Cast" office rows (Mason precedent) carrying the
voting-stats breakdowns.
"""
import csv
import re
import sys
from collections import OrderedDict

import pdfplumber

SRC = '/Users/dwillis/code/openelections-sources-mi/2020/primary/'
OUT = '2020/counties/20200804__mi__primary__wayne__precinct.csv'

DIGIT = re.compile(r'[\d,]+')
TITLE_RE = re.compile(r'Senator|Congress for|State Legislature')
HEADER_WORDS = {'Precinct', 'Portion', 'County', 'Registered', 'Voters',
                'Ballots', 'Cast', 'Turnout', '%'}
ROW_SPACING = 14.5

SENATE_DISTRICTS = ['']
CONGRESS_DISTRICTS = ['11', '12', '13', '14']
LEG_DISTRICTS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12',
                 '13', '14', '15', '16', '17', '19', '20', '21', '23']

# leg pp.198-200 (HD13 REP) print neither a contest title nor a rotated header
MANUAL_CANDIDATES = {('State House', '13', False, 'REP'): ['Megan Frump']}

# HD12 ends with two Van Buren precincts that both parties' reports truncate;
# HD21 lists Van Buren 1,2,3,6,7,8,9,10, so HD12's two must be 4 and 5
MANUAL_RUN_FIXES = {
    ('State House', '12', False): {
        'Charter Township of Van': [
            'Charter Township of Van Buren, Precinct 4',
            'Charter Township of Van Buren, Precinct 5'],
        'Charter Township of Van Buren, Precinct': [
            'Charter Township of Van Buren, Precinct 4',
            'Charter Township of Van Buren, Precinct 5'],
    },
}


def canonical_contests(family):
    """Ordered (office, district, partial, party) list as the reports appear."""
    out = []
    if family == 'senate':
        for party in ('DEM', 'REP'):
            out.append(('U.S. Senate', '', False, party))
    elif family == 'congress':
        # all DEM districts first, then all REP (observed page order)
        for party in ('DEM', 'REP'):
            for d in CONGRESS_DISTRICTS:
                out.append(('U.S. House', d, False, party))
    else:
        for party in ('DEM', 'REP'):
            for d in LEG_DISTRICTS:
                out.append(('State House', d, False, party))
                if d == '4':
                    out.append(('State House Partial Term', d, True, party))
    return out


def cluster_tops(items, tol=2.0):
    """Group word/char dicts into lines by their top coordinate."""
    lines = []
    for w in sorted(items, key=lambda w: (w['top'], w.get('x0', 0))):
        if lines and abs(w['top'] - lines[-1][-1]['top']) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines


def is_pagenum(w, page):
    """Page numbers sit alone at the right edge, top or bottom."""
    if w['x0'] > page.width - 90 and w['top'] < 20:
        return True
    if page.width < 700 and w['x0'] > page.width - 85 and w['top'] > page.height - 50:
        return True
    return False


def title_info(page):
    """Return (office, district, partial, party) from the horizontal title line."""
    upright = page.filter(lambda o: o.get('upright', True))
    ws = [w for w in upright.extract_words() if w['top'] < 135 and w['x0'] < 500]
    for line in cluster_tops(ws, tol=3.0):
        text = ' '.join(w['text'] for w in sorted(line, key=lambda w: w['x0']))
        party = 'DEM' if '(Dem)' in text else ('REP' if '(REP)' in text else None)
        if not party or 'Vote for' not in text:
            continue
        if 'United States Senator' in text:
            return ('U.S. Senate', '', False, party)
        m = re.search(r'in Congress for (\d+)(?:st|nd|rd|th) Congressional District', text)
        if m:
            return ('U.S. House', m.group(1), False, party)
        m = re.search(r'State Legislature- Partial Term (\d+)(?:st|nd|rd|th) District', text)
        if m:
            return ('State House Partial Term', m.group(1), True, party)
        m = re.search(r'State Legislature for (?:ONLY )?(\d+)(?:st|nd|rd|th) District', text)
        if m:
            return ('State House', m.group(1), False, party)
    return None


def header_columns(page):
    """Decode rotated bottom-up header strips into ordered columns.

    Chars of one strip share x0; a strip reads bottom-up (chars sorted by
    descending top).  Strips of one column sit ~12.2pt apart (word wrap / party
    tag); distinct columns are far wider apart.
    """
    rot = [c for c in page.chars if not c['upright']]
    strips = []
    for c in sorted(rot, key=lambda c: c['x0']):
        for s in strips:
            if abs(s['x0'] - c['x0']) <= 2:
                s['chars'].append(c)
                break
        else:
            strips.append({'x0': c['x0'], 'chars': [c]})
    for s in strips:
        s['text'] = ''.join(c['text'] for c in sorted(s['chars'], key=lambda c: -c['top'])).strip()
    columns = []
    for s in strips:
        # strips of one column sit 12.2-13.7pt apart (x0 to x0); a party tag
        # strip always ends a candidate column even when the next candidate's
        # name sits closer than that
        prev = columns[-1] if columns else None
        tag = prev and prev['parts'][-1].upper() in ('(DEM)', '(REP)')
        if prev and not tag and s['x0'] - prev['last_x0'] <= 14.5:
            prev['parts'].append(s['text'])
            prev['last_x0'] = s['x0']
        else:
            columns.append({'x0': s['x0'], 'last_x0': s['x0'], 'parts': [s['text']]})
    out = []
    for col in columns:
        text = ''
        for part in col['parts']:
            if text and part[:1].islower():
                text += part          # word continuation, e.g. 'Dudenhoefe' + 'r'
            else:
                if text:
                    text += ' '
                text += part
        out.append({'x0': col['x0'], 'text': re.sub(r'\s+', ' ', text).strip()})
    return out


def norm_header(text):
    return re.sub(r'[\s-]', '', text).lower()


def split_candidate(name):
    """'Gary Peters (DEM)' -> ('Gary Peters', 'DEM')."""
    m = re.search(r'\((DEM|REP)\)$', name)
    if m:
        return name[:m.start()].strip(), m.group(1)
    return name, None


def assign_contests(pages, family):
    """Map each page to its contest using readable titles + district-gap rule."""
    canon = canonical_contests(family)
    key = lambda t: tuple(t)
    readable = [(i, pg['title']) for i, pg in enumerate(pages) if pg['title']]
    # sanity: readable titles must appear in canonical order
    idxs = []
    for _, t in readable:
        if key(t) not in [key(c) for c in canon]:
            raise SystemExit(f'title {t} not in canonical list for {family}')
        idxs.append([key(c) for c in canon].index(key(t)))
    for a, b in zip(idxs, idxs[1:]):
        if b < a:
            raise SystemExit(f'readable titles out of order in {family}: {idxs}')

    page_contest = [None] * len(pages)
    cur = None                    # canonical index of current contest
    run_start = None              # first title-less page since last anchor
    ai = 0                        # next readable anchor
    for i, pg in enumerate(pages):
        if ai < len(readable) and readable[ai][0] == i:
            want = idxs[ai]
            if cur is not None and want > cur + 1:
                if want > cur + 2:
                    raise SystemExit(f'ambiguous title gap at page {i+1} of {family}')
                cur = want - 1    # the run since the last anchor was this contest
            else:
                if want != cur and cur is not None and want != cur + 1:
                    raise SystemExit(f'unexpected title jump at page {i+1} of {family}')
                # run since last anchor belongs to the previous contest
            if run_start is not None:
                for j in range(run_start, i):
                    page_contest[j] = cur
                run_start = None
            cur = want
            page_contest[i] = cur
            ai += 1
        else:
            if run_start is None:
                run_start = i
            if cur is None:
                raise SystemExit(f'title-less page {i+1} before any title in {family}')
            page_contest[i] = cur
    if run_start is not None:
        for j in range(run_start, len(pages)):
            page_contest[j] = cur
    return [canon[c] for c in page_contest]


def parse_report(path, family):
    """Parse one Precinct Report PDF into contests with per-precinct rows."""
    pages = []
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            upright = pg.filter(lambda o: o.get('upright', True))
            words = upright.extract_words()
            values, labels = [], []
            for line in cluster_tops(words):
                line = [w for w in line if not is_pagenum(w, pg)]
                if not line:
                    continue
                wtexts = {w['text'] for w in line}
                digits = [w for w in line if DIGIT.fullmatch(w['text'])
                          or re.fullmatch(r'#+', w['text'])]
                txt = ' '.join(w['text'] for w in sorted(line, key=lambda w: w['x0']))
                if txt in ('DEM', 'REP') or (TITLE_RE.search(txt) and 'Vote for' in txt):
                    continue        # contest title / party marker, not a label
                entry = {'top': line[0]['top'], 'words': line,
                         'height': pg.height, 'width': pg.width}
                # a value row starts with its method label ("Election Day",
                # "AV Counting Board", "Total") or is the "County - Total"
                # footer row; the page header "August 4th, 2020 - Primary
                # Election" also carries digits and the word Election but
                # starts with "August".  Continuation pages without a
                # repeated title (leg HD13 REP pp198-200) put their first
                # block above y=75, so position is not a filter.
                first = min(line, key=lambda w: w['x0'])['text']
                if digits and (wtexts & {'Election', 'AV', 'Total'}) \
                        and first in ('Election', 'AV', 'Total', 'County'):
                    entry['values'] = digits
                    values.append(entry)
                else:
                    labels.append(entry)
            pages.append({'title': title_info(pg), 'cols': header_columns(pg),
                          'values': values, 'labels': labels})

    contests_seq = assign_contests(pages, family)

    contests = OrderedDict()
    for i, (pg, cinfo) in enumerate(zip(pages, contests_seq)):
        if cinfo is None:
            continue
        ckey = cinfo
        contest = contests.setdefault(ckey, {
            'office': cinfo[0], 'district': cinfo[1], 'partial': cinfo[2],
            'party': cinfo[3], 'candidates': None, 'rows': OrderedDict(),
            '_occ': {}, '_short': [], 'county_total': None})
        cols = pg['cols']
        if contest['candidates'] is None:
            if not cols:
                cands = MANUAL_CANDIDATES.get(ckey)
                if cands is None:
                    raise SystemExit(
                        f"page {i+1} of {family}: no header columns and no "
                        f"manual candidates for {ckey}")
                contest['candidates'] = list(cands)
            else:
                if [norm_header(c['text']) for c in cols[-2:]] != \
                        ['totalvotes', 'unresolvedwritein']:
                    raise SystemExit(
                        f"page {i+1} of {family}: unexpected last columns "
                        f"{[c['text'] for c in cols[-2:]]}")
                cands = []
                for c in cols[:-2]:
                    name, hparty = split_candidate(c['text'])
                    if hparty != contest['party']:
                        raise SystemExit(
                            f"page {i+1} of {family}: header party {hparty!r} != "
                            f"contest party {contest['party']!r} for {name!r}")
                    cands.append(name)
                contest['candidates'] = cands
        ncol = len(contest['candidates']) + 2
        if cols and len(cols) != ncol:
            raise SystemExit(
                f"page {i+1} of {family}: {len(cols)} header columns, expected {ncol}")

        # walk the page in top order: labels precede their block; a label with
        # no row after it on the page stays pending (rows spill to next page)
        row_tops = [v['top'] for v in pg['values']]
        label_tops = [e['top'] for e in pg['labels']]
        current_label = contest.get('_last_label')
        stream = sorted(pg['labels'] + pg['values'], key=lambda e: e['top'])
        pending = []            # consecutive parts of one (possibly wrapped) label

        def flush_label():
            # A label is valid if a value row follows its last part close by
            # with no other label line in between, or it dangles at the page
            # bottom (rows spill over); a truncated single-line cut just stays
            # invalid and is repaired later (see finalize).
            nonlocal pending, current_label
            if not pending:
                return
            last = pending[-1]
            text = ' '.join(w['text'] for p in pending
                            for w in sorted(p['words'], key=lambda w: w['x0']))
            # most pages put the first row 14.5pt below the label; a few leg
            # pages (HD12/13 DEM) use 10.8pt, and a handful of blocks (GPP
            # Park, Wayne) leave ~23-30pt of blank space before the first row.
            # The "no label in between" guard keeps wrap part1s invalid when
            # their continuation part sits before the value row.
            near = any(7 <= t - last['top'] <= 30
                       and not any(last['top'] < lt < t for lt in label_tops)
                       for t in row_tops)
            if near or last['top'] > last['height'] - 58:
                current_label = text
            pending = []

        for entry in stream:
            if 'values' not in entry:
                wset = {w['text'] for w in entry['words']}
                if wset <= HEADER_WORDS or 'County' in wset:
                    continue
                if pending and entry['top'] - pending[-1]['top'] >= ROW_SPACING:
                    flush_label()
                pending.append(entry)
                continue
            flush_label()
            wtexts = {w['text'] for w in entry['words']}
            if 'Election' in wtexts:
                method = 'ED'
            elif 'AV' in wtexts:
                method = 'AV'
            elif 'Total' in wtexts:
                method = 'TV'
            else:
                raise SystemExit(f"page {i+1} of {family}: value row without method label "
                                 f"at top {entry['top']}: {sorted(wtexts)}")
            vals = [None if w['text'].strip('#') == '' else
                    int(w['text'].replace(',', ''))
                    for w in sorted(entry['values'], key=lambda w: w['x1'])]
            if 'County' in wtexts:          # county-total row ends the report
                if len(vals) != ncol:
                    raise SystemExit(
                        f"page {i+1} of {family}: clipped county-total row {vals}")
                # '####' = a candidate cell too narrow for its number
                if vals[:-2].count(None) == 1:
                    k = vals[:-2].index(None)
                    vals[k] = vals[-2] - sum(v for v in vals[:-2] if v is not None) \
                        - vals[-1]
                elif any(v is None for v in vals):
                    raise SystemExit(f"page {i+1} of {family}: unresolved '####' cells "
                                     f"in county-total row {vals}")
                contest['county_total'] = vals
                current_label = None
                continue
            if current_label is None:
                # the source can omit a block's label entirely (leg p80, first
                # block of HD12 DEM); mark it and repair in finalize
                contest['_unlabeled'] = contest.get('_unlabeled', 0) + 1
                current_label = f'\x00unlabeled {contest["_unlabeled"]}'
            if any(v is None for v in vals):
                raise SystemExit(f"page {i+1} of {family}: '####' cell in a precinct row "
                                 f"at top {entry['top']}")
            # occurrence index keeps blocks that share a truncated label apart
            key = (current_label, method)
            occ = contest['_occ'].get(key, 0)
            contest['_occ'][key] = occ + 1
            if len(vals) != ncol:
                # page-bottom clipping: the rightmost cells of the last row on
                # a page are not printed; repaired after the walk
                contest['_short'].append((current_label, method, occ, vals))
                continue
            contest['rows'][key + (occ,)] = vals
        flush_label()
        contest['_last_label'] = current_label

    # resolve page-bottom clipped rows: Total = Election Day + AV cellwise,
    # so a clipped ED/AV row is the difference, a clipped TV row the sum
    for contest in contests.values():
        ncol = ncol_of(contest)
        for label, m, occ, vals in contest['_short']:
            if m == 'TV':
                a, b = (contest['rows'].get((label, x, occ)) for x in ('ED', 'AV'))
            else:
                a, b = (contest['rows'].get((label, x, occ))
                        for x in (('TV', 'AV') if m == 'ED' else ('TV', 'ED')))
            if any(v is None or len(v) != ncol for v in (a, b)):
                raise SystemExit(
                    f'clipped {m} row of {label!r}: needed rows missing/incomplete')
            full = [x + y for x, y in zip(a, b)] if m == 'TV' else \
                [x - y for x, y in zip(a, b)]
            if full[:len(vals)] != vals:
                raise SystemExit(
                    f'clipped {m} row of {label!r}: printed {vals} vs '
                    f'computed {full}')
            contest['rows'][(label, m, occ)] = full
        contest['_short'] = []

    # cleanup (validation and truncated-label repair happen in finalize)
    out = []
    for contest in contests.values():
        contest.pop('_occ', None)
        out.append(contest)
    return out


def ncol_of(contest):
    return len(contest['candidates']) + 2


def natural_key(s):
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', s)]


def prefix_matches(frag, universe):
    """Full labels that extend a truncated fragment (cut mid-label)."""
    cands = [L for L in universe
             if L.startswith(frag) and len(L) > len(frag)
             and not L[len(frag)].isdigit()]
    # drop candidates that are themselves truncations of a longer label
    return sorted((c for c in cands
                   if not any(o != c and o.startswith(c)
                              and not o[len(c)].isdigit() for o in cands)),
                  key=natural_key)


def finalize(reports, stats):
    """Repair source-truncated labels, then validate every contest."""
    stats_order = list(stats)
    sidx = {p: i for i, p in enumerate(stats_order)}

    # 1. labels the source cut short enough that two precinct blocks collided
    universe = {p for c in reports for p, _, _ in c['rows']} | set(stats)
    by_key = {(c['office'], c['district'], c['partial'], c['party']): c
              for c in reports}
    ordered = [(c, [p for (p, m, _) in c['rows'] if m == 'TV']) for c in reports]
    for idx, (contest, seq) in enumerate(ordered):
        # first known precinct of a later contest bounds a run at contest end
        after = next((s[0] for _, s in ordered[idx + 1:] if s and s[0] in sidx),
                     None)
        # the same district's other-party contest lists identical precincts
        other = 'REP' if contest['party'] == 'DEM' else 'DEM'
        sib = by_key.get((contest['office'], contest['district'],
                          contest['partial'], other))
        sib_seq = [p for (p, m, _) in sib['rows'] if m == 'TV'] if sib else None
        renames = []            # (fragment, [full label per occurrence])
        i = 0
        while i < len(seq):
            j = i
            while j < len(seq) and seq[j] == seq[i]:
                j += 1
            frag = seq[i]
            if j - i > 1 and not frag.startswith('\x00'):
                nxt = seq[j] if j < len(seq) else after
                cands = prefix_matches(frag, universe)
                if i and seq[i - 1] in sidx:
                    lo = sidx[seq[i - 1]]
                    cands = [c for c in cands if sidx.get(c, -1) > lo]
                if nxt is not None and nxt in sidx:
                    hi = sidx[nxt]
                    cands = [c for c in cands if sidx.get(c, 10 ** 9) < hi]
                if len(cands) != j - i:
                    manual = MANUAL_RUN_FIXES.get(
                        (contest['office'], contest['district'], contest['partial']),
                        {}).get(frag)
                    if manual and len(manual) == j - i:
                        cands = manual
                    # fall back to sibling alignment: same precincts, same order
                    elif sib_seq is not None and len(sib_seq) == len(seq) and all(
                            sib_seq[k].startswith(frag) and sib_seq[k] != frag
                            and sib_seq[k] in stats for k in range(i, j)):
                        cands = [sib_seq[k] for k in range(i, j)]
                    else:
                        raise SystemExit(
                            f'{contest["office"]} {contest["district"]} '
                            f'{contest["party"]}: {j - i} blocks share label '
                            f'{frag!r} between {seq[i - 1] if i else None!r} and '
                            f'{nxt!r}, candidates {cands}')
                renames.append((frag, cands))
            i = j
        for frag, fulls in renames:
            print(f'repaired collision {frag!r} -> {fulls} '
                  f'({contest["office"]} {contest["district"]} {contest["party"]})')
            for k, full in enumerate(fulls):
                for m in ('ED', 'AV', 'TV'):
                    key = (frag, m, k)
                    if key in contest['rows']:
                        contest['rows'][(full, m, 0)] = contest['rows'].pop(key)

    # 1b. blocks whose label the source omitted entirely: precincts run in the
    # same order as voting stats, so a run of unlabeled blocks maps to the
    # stats precincts immediately preceding the next labelled block
    for contest in reports:
        seq = [p for (p, m, _) in contest['rows'] if m == 'TV']
        runs = []
        i = 0
        while i < len(seq):
            if seq[i].startswith('\x00'):
                j = i
                while j < len(seq) and seq[j].startswith('\x00'):
                    j += 1
                runs.append((i, j))
                i = j
            else:
                i += 1
        if not runs:
            continue
        rename = {}
        for i, j in runs:
            nxt = seq[j] if j < len(seq) else None
            prv = seq[i - 1] if i else None
            if nxt is None or nxt not in sidx:
                raise SystemExit(
                    f'{contest["office"]} {contest["district"]} '
                    f'{contest["party"]}: unlabeled run with no labelled '
                    f'precinct after it')
            hi = sidx[nxt]
            if prv is not None and prv in sidx:
                cands = stats_order[sidx[prv] + 1:hi]
            else:
                cands = stats_order[max(0, hi - (j - i)):hi]
            if len(cands) != j - i:
                raise SystemExit(
                    f'{contest["office"]} {contest["district"]} '
                    f'{contest["party"]}: {j - i} unlabeled blocks between '
                    f'{prv!r} and {nxt!r}, candidates {cands}')
            for k, lab in zip(range(i, j), cands):
                rename[seq[k]] = lab
        print(f'repaired unlabeled blocks in {contest["office"]} '
              f'{contest["district"]} {contest["party"]}: {rename}')
        contest['rows'] = OrderedDict(
            ((rename.get(p, p), m, o), v)
            for (p, m, o), v in contest['rows'].items())

    # 2. per-row arithmetic: Total = candidates + unresolved write-ins
    for contest in reports:
        for (prec, method, _), vals in contest['rows'].items():
            total, unres = vals[-2], vals[-1]
            if total != sum(vals[:-2]) + unres:
                raise SystemExit(
                    f'{contest["office"]} {contest["district"]} '
                    f'{contest["party"]}: {prec} {method}: Total {total} != '
                    f'{sum(vals[:-2])}+{unres}')

    # 3. labels still missing from voting stats: prefix-repair via the stats
    #    ordering (both documents follow the same precinct sequence)
    mapping = {}
    for contest in reports:
        seq = [p for (p, m, _) in contest['rows'] if m == 'TV']
        for i, p in enumerate(seq):
            if p in stats or p in mapping:
                continue
            cands = prefix_matches(p, set(stats))
            if len(cands) == 1:
                mapping[p] = cands[0]
                continue
            lo = hi = None
            for j in range(i - 1, -1, -1):
                if seq[j] in sidx:
                    lo = sidx[seq[j]]
                    break
            for j in range(i + 1, len(seq)):
                if seq[j] in sidx:
                    hi = sidx[seq[j]]
                    break
            narrowed = [c for c in cands
                        if (lo is None or sidx[c] > lo)
                        and (hi is None or sidx[c] < hi)]
            if len(narrowed) != 1:
                raise SystemExit(
                    f'{contest["office"]} {contest["district"]} '
                    f'{contest["party"]}: label {p!r} not in voting stats; '
                    f'candidates {narrowed or cands}')
            mapping[p] = narrowed[0]
    if mapping:
        print('repaired truncated labels:', mapping)
        for contest in reports:
            contest['rows'] = OrderedDict(
                ((mapping.get(p, p), m, o), vals)
                for (p, m, o), vals in contest['rows'].items())

    # 4. precinct sums vs the source's own county-total rows
    for contest in reports:
        if not contest['county_total']:
            continue
        sums = [0] * (len(contest['candidates']) + 2)
        for (prec, method, _), vals in contest['rows'].items():
            if method != 'TV':
                continue
            for k, x in enumerate(vals):
                sums[k] += x
        if sums != contest['county_total']:
            raise SystemExit(
                f'{contest["office"]} {contest["district"]} '
                f'{contest["party"]}: precinct sums {sums} != county total '
                f'{contest["county_total"]}')


def parse_voting_stats(path):
    """Registered Voters / Ballots Cast per precinct, with ED/AV breakdown.

    Per precinct: one or two label lines (long names wrap), then Election
    Day / AV Counting Board / Total rows, each carrying the repeated
    Registered Voters figure and the Ballots Cast number.
    """
    stats = OrderedDict()
    county_total = None
    county_breakdown = False   # consuming the ED/AV/Total rows under County-Total
    county_ed_av = []
    cur_label = None           # label of the block currently being read
    pending = []               # label lines seen since the last method row

    def flush():
        nonlocal pending
        text = ' '.join(pending)
        pending = []
        return text

    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            words = pg.extract_words()
            lines = []
            for line in cluster_tops(words):
                line = [w for w in line if not is_pagenum(w, pg)]
                if not line:
                    continue
                lines.append((line[0]['top'], line))
            lines.sort()
            for top, line in lines:
                if top < 90:        # 'Voting Stats / August 4, 2020 / ...' header
                    continue
                wtexts = {w['text'] for w in line}
                if wtexts <= HEADER_WORDS:
                    continue
                digits = [w for w in line if DIGIT.fullmatch(w['text'])]
                if not (wtexts & {'Election', 'AV', 'Total'}) or top <= 75:
                    # label line (may wrap onto the next line)
                    pending.append(' '.join(
                        w['text'] for w in sorted(line, key=lambda w: w['x0'])))
                    continue
                nums = [int(w['text'].replace(',', ''))
                        for w in sorted(digits, key=lambda w: w['x0'])]
                if 'County' in wtexts:      # county-total row
                    if len(nums) != 2:
                        raise SystemExit(f'voting stats: county row {nums}')
                    county_total = nums
                    county_breakdown, county_ed_av = True, []
                    cur_label, pending = None, []
                    continue
                if 'Election' in wtexts:
                    method = 'ED'
                elif 'AV' in wtexts:
                    method = 'AV'
                else:
                    method = 'TV'
                if len(nums) != 2:
                    raise SystemExit(
                        f'voting stats: {len(nums)} numbers on a row at top {top}')
                rv, bc = nums
                if county_breakdown and not pending:
                    # the County-Total block's own Election Day / AV / Total rows
                    if method == 'TV':
                        if bc != county_total[1]:
                            raise SystemExit(
                                f'voting stats: county breakdown {county_ed_av} '
                                f'!= TV {county_total[1]}')
                        county_breakdown = False
                    else:
                        county_ed_av.append(bc)
                    continue
                if pending:
                    if method != 'ED':
                        raise SystemExit(
                            f'voting stats: label parts before a {method} row')
                    cur_label = flush()
                if method == 'ED' and not pending and cur_label is None:
                    raise SystemExit('voting stats: Election Day row without label')
                if cur_label is None:
                    raise SystemExit('voting stats: unlabeled row')
                e = stats.setdefault(
                    cur_label, {'RV': None, 'ED': None, 'AV': None, 'TV': None})
                if e['RV'] is None:
                    e['RV'] = rv
                elif e['RV'] != rv:
                    raise SystemExit(
                        f'voting stats: {cur_label} RV mismatch {e["RV"]} vs {rv}')
                e[method] = bc
            # pending label parts carry across the page break
    for prec, e in stats.items():
        if None in e.values():
            raise SystemExit(f'voting stats: incomplete {prec}: {e}')
        if e['ED'] + e['AV'] != e['TV']:
            raise SystemExit(f'voting stats: {prec} ED+AV {e["ED"]+e["AV"]} != TV {e["TV"]}')
    if county_total:
        s = [sum(e[k] for e in stats.values()) for k in ('RV', 'TV')]
        if s != county_total[:2]:
            raise SystemExit(f'voting stats: sums {s} != county total {county_total[:2]}')
    return stats


def main():
    only = sys.argv[1:] or ['senate', 'congress', 'leg']
    reports = []
    if 'senate' in only:
        reports += parse_report(SRC + 'Wayne MI senate_080420.pdf', 'senate')
    if 'congress' in only:
        reports += parse_report(SRC + 'Wayne MI congress_080420.pdf', 'congress')
    if 'leg' in only:
        reports += parse_report(SRC + 'Wayne MI leg080420.pdf', 'leg')
    stats = parse_voting_stats(SRC + 'Wayne MI voting_stats_08042020.pdf')
    finalize(reports, stats)

    for contest in reports:
        n = len(contest['rows']) // 3
        print(f"{contest['office']} {contest['district']} {contest['party']}: "
              f"{len(contest['candidates'])} candidates, {n} precincts, "
              f"county total {contest['county_total']}")

    # every report precinct should exist in voting stats under the same name
    stat_names = set(stats)
    missing = set()
    for contest in reports:
        for prec in {p for p, _, _ in contest['rows']}:
            if prec not in stat_names:
                missing.add(prec)
    if missing:
        print(f'WARNING: {len(missing)} report precincts not in voting stats:')
        for p in sorted(missing):
            print('   ', p)

    rows = []
    precincts = [p for p in stats]
    seen = set(precincts)
    for contest in reports:
        for p in {pr for pr, _, _ in contest['rows']}:
            if p not in seen:
                seen.add(p)
                precincts.append(p)
    for prec in precincts:
        st = stats.get(prec)
        if st:
            rows.append(['Wayne', prec, 'Registered Voters', '', '', '',
                         st['RV'], '', ''])
            rows.append(['Wayne', prec, 'Ballots Cast', '', '', '',
                         st['TV'], st['ED'], st['AV']])
        for contest in reports:
            methods = {}
            for (p, m, _), vals in contest['rows'].items():
                if p == prec:
                    methods[m] = vals
            if not methods:
                continue
            for k, name in enumerate(contest['candidates']):
                ed, av, tv = methods['ED'][k], methods['AV'][k], methods['TV'][k]
                rows.append(['Wayne', prec, contest['office'], contest['district'],
                             contest['party'], name, tv, ed, av])
            rows.append(['Wayne', prec, contest['office'], contest['district'],
                         contest['party'], 'Total Votes',
                         methods['TV'][-2], methods['ED'][-2], methods['AV'][-2]])
            rows.append(['Wayne', prec, contest['office'], contest['district'],
                         contest['party'], 'Write-In',
                         methods['TV'][-1], methods['ED'][-1], methods['AV'][-1]])

    header = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
              'votes', 'election_day', 'av_counting_boards']
    with open(OUT, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(precincts)} precincts')


if __name__ == '__main__':
    main()