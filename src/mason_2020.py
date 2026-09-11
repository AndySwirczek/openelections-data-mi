"""Parse Mason County's Aug 2020 primary "Summary Results Report"
(Electionware, image-only) into a precinct CSV.

The PDF is 109 pages, one precinct per page-set with the precinct heading
repeated on every page. Each page carries a Statistics block (once per
precinct) and contest blocks: a title line ("DEM United States Senator",
"REP Clerk Amber", "Mason Oceana 911"), "Vote For N", a TOTAL caption, then
rows of "name ..... value" ending with "Write-In Totals".

Because the PDF has no text layer, pages are read by TWO tesseract passes
(raw + dotted-leader thresholded; see src/mason_2020_ocr.py) whose values
must agree, and the whole parse is diffed against the PaddleOCR cache
(/tmp/paddleocr_md/Mason_MI_Primary) whose label/value pairing is unreliable
(fused cells scramble rows, e.g. pairing "Write-In Totals" with the value
above it) but whose names/digits read cleanly. Any disagreement becomes a
problem row adjudicated from a page crop.

Usage: .venv/bin/python src/mason_2020.py
       [--out 2020/counties/20200804__mi__primary__mason__precinct.csv]
"""
import argparse
import csv
import difflib
import re
import sys

OCR = '/tmp/mason_ocr'
CACHE = '/tmp/paddleocr_md/Mason_MI_Primary'
COUNTY = 'Mason'
STATE_HOUSE = '101'
OUT = '2020/counties/20200804__mi__primary__mason__precinct.csv'

FOOTER_RE = re.compile(
    r'^(precinct summary|report generated|democrat summary|republican '
    r'summary|summary results report|official results|mason august primary '
    r'election|august 04,? 2020|mason county)\b', re.I)
HEAD_RE = re.compile(r'^(.+?), (?:Precinct|Pct\.)\s*(\d+)$')
TITLE_RE = re.compile(r'^(DEM|REP)\s+(.+)$')
VOTEFOR_RE = re.compile(r'^(?:.*\s)?Vote For (\d+)$')
VALUE_ONLY_RE = re.compile(r'^\(?([\d,]+)\)?[%.]?$')
WRITEIN_RE = re.compile(r'^write[ -]?in[ .]?totals?$', re.I)
TURNOUT_RE = re.compile(r'^Voter Turnout', re.I)
# Trailing tokens that are leader-dot noise, not name parts (initials carry
# a period in this report, so bare single letters are noise).
NOISE_TOK = re.compile(r'^[^A-Za-z0-9]*$|^(?:[oOaZ]|oo|er\)\?|©)$')

COUNTY_OFFICES = {'Prosecuting Attorney', 'Sheriff', 'Clerk', 'Treasurer',
                  'Register of Deeds', 'Drain Commissioner', 'Surveyor'}


def normalize_heading(name):
    name = re.sub(r'\s+', ' ', name).strip()
    return re.sub(r'\bTwp\.?$', 'Township', name)


def tsv_lines(path, tol=35):
    """[([tokens], y)] from a tesseract TSV, words grouped into lines."""
    words = []
    for line in open(path):
        f = line.rstrip('\n').split('\t')
        if len(f) < 12 or f[0] != '5':
            continue
        txt = f[11].strip()
        if txt:
            words.append((int(f[7]), int(f[6]), txt))
    words.sort()
    lines = []
    for y, x, txt in words:
        if lines and abs(y - lines[-1][0]) < tol:
            lines[-1][1].append((x, txt))
        else:
            lines.append([y, [(x, txt)]])
    return [([t for _, t in sorted(ws)], y) for y, ws in lines]


def paddle_lines(page):
    """[([tokens], None)] from the PaddleOCR markdown, in document order.
    Cells embed literal '\\n' sequences (and fused pages cram the whole
    page into one cell), so those become line breaks too."""
    text = open(f'{CACHE}/p{page:03d}.md').read().replace('\\n', '\n')
    lines = []
    for part in re.split(r'(<table.*?</table>)', text, flags=re.S):
        if part.startswith('<table'):
            cells = []
            for row in re.findall(r'<tr>(.*?)</tr>', part, re.S):
                cells += re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
            for c in cells:
                lines += re.sub(r'<[^>]+>', ' ', c).split('\n')
        else:
            lines += re.sub(r'<[^>]+>', ' ', part).split('\n')
    out = []
    for l in lines:
        toks = [re.sub(r'^#+', '', t) for t in l.split()]
        toks = [t for t in toks if t]
        if toks:
            out.append((toks, None))
    return out


def split_label_value(tokens):
    """(label, value-str or None): the rightmost token containing digits is
    the value; leader junk between label and value is dropped. Four-digit
    tokens are footer years, not values."""
    for i in range(len(tokens) - 1, -1, -1):
        m = re.search(r'\d[\d,]*', tokens[i])
        if m and not re.fullmatch(r'\d{4}', tokens[i]):
            label = [t for t in tokens[:i] if not NOISE_TOK.match(t)]
            return ' '.join(label).strip(), m.group(0)
    label = [t for t in tokens if not NOISE_TOK.match(t)]
    return ' '.join(label).strip(), None


class Page:
    def __init__(self):
        self.precinct = None
        self.stats = {}     # label -> value
        self.contests = []  # {'title','party','vote_for','rows':[(label,val)]}


def parse_lines(lines, where, problems, junk_contests=False):
    """State machine shared by both OCR sources. `lines` items are
    ([tokens], y)."""
    pg = Page()
    cur = None
    pending = None      # label awaiting a value on a following line
    section = None      # 'stats' after the Statistics header
    prev_plain = None   # last line not consumed; a following 'Vote For N'
                        # makes it a proposal title

    def open_contest(title, party):
        nonlocal cur, pending, section
        cur = {'title': title, 'party': party, 'vote_for': None, 'rows': []}
        pg.contests.append(cur)
        pending = None
        section = None

    for idx, (toks, _y) in enumerate(lines):
        ln = re.sub(r'\s+', ' ', ' '.join(toks)).strip()
        if not ln or FOOTER_RE.match(ln):
            continue
        # a plain line directly followed by 'Vote For N' is a proposal
        # title (some titles, like 'Mason Oceana 911', contain digits)
        nxt = re.sub(r'\s+', ' ', ' '.join(lines[idx + 1][0])).strip() \
            if idx + 1 < len(lines) else ''
        if VOTEFOR_RE.match(nxt) and not TITLE_RE.match(ln) and \
                not HEAD_RE.match(ln):
            open_contest(re.sub(r'\s*Vote For \d+$', '', ln).strip(), '')
            continue
        m = re.match(r'^TOTAL\s+([\d,]+)$', ln)
        if m:  # fused caption+value from structured OCR rows
            if pending:
                pending[1] = m.group(1)
                pending = None
            continue
        if re.match(r'^TOTAL$', ln):
            continue
        if re.match(r'^Statistics(\s+TOTAL)?$', ln, re.I):
            section = 'stats'
            continue
        m = VOTEFOR_RE.match(ln)
        if m and not TITLE_RE.match(ln):
            if prev_plain is not None:
                open_contest(prev_plain, '')
                prev_plain = None
            if cur is not None:
                cur['vote_for'] = m.group(1)
            continue
        m = TITLE_RE.match(ln)
        if m:
            open_contest(re.sub(r'\s*Vote For \d+$', '', m.group(2)).strip(),
                         m.group(1))
            prev_plain = None
            continue
        m = HEAD_RE.match(ln)
        if m:
            got = (f'{normalize_heading(m.group(1))}, '
                   f'Precinct {m.group(2)}')
            if pg.precinct and pg.precinct != got:
                problems.append(f'{where}: heading changed '
                                f'{pg.precinct!r} -> {got!r}')
            pg.precinct = got
            continue
        label, val = split_label_value(toks)
        nl = re.sub(r'[^a-z]', '', label.lower())
        if nl.startswith('writ') and 'totals' in nl or \
                similar(nl, 'writeintotals') >= 0.7:
            label = 'Write-In Totals'  # 'Write-tn'/'Writeln' OCR garbles
        # footer fragments that lost their prefix ('08/06/2020 6:12PM 55 of
        # 109' -> '08062020612pm55of') would otherwise become data rows
        nln = re.sub(r'[^a-z0-9]', '', label.lower())
        if 'precinctsummary' in nln or re.fullmatch(r'[\dpm]+of', nln):
            continue
        if val is None:
            if not label:
                continue
            pending = [label, None]  # value may sit alone on the next line
            continue
        if not label and pending is not None:
            label, val = pending[0], val  # bare value continuing the label
        pending = None
        if not label:
            continue
        if section == 'stats' and cur is None:
            pg.stats[label] = val
            prev_plain = None
            continue
        if cur is None:
            if TURNOUT_RE.match(label) or not label:
                continue
            if not junk_contests:
                problems.append(f'{where}: row {label!r}={val} outside any '
                                f'contest')
                prev_plain = None
                continue
            # paddle pages with dropped titles: keep rows in a junk
            # contest so the value multiset check still sees them
            open_contest('(no title)', '')
        if re.fullmatch(r'(vote|for|totals?)(\s+(vote|for))?', label, re.I) \
                or similar(nl, 'votefor') >= 0.6:
            continue  # 'Vote For 1' remnant misread as a row
        cur['rows'].append((label, val))
        prev_plain = None
    # trailing pending labels with no value are dropped silently; the
    # cross-pass and paddle diffs catch any that mattered
    return pg


def parse_tesseract(page):
    problems = []
    bw = tsv_lines(f'{OCR}/bw/p{page:03d}.tsv')
    raw = tsv_lines(f'{OCR}/raw/p{page:03d}.tsv')
    pg = parse_lines(bw, f'p{page:03d} bw', problems)
    alt = parse_lines(raw, f'p{page:03d} raw', [])
    return pg, alt, problems


def similar(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def reconcile(page, pg, alt, pp, problems):
    """Merge the two tesseract passes (bw primary): rows match by label
    similarity, value conflicts resolve via the paddle tiebreak, and a row
    only raw saw is adopted (flagged)."""
    for ci, (c1, c2) in enumerate(zip(pg.contests, alt.contests)):
        used = set()
        for ri, (l1, v1) in enumerate(c1['rows']):
            n1 = norm_name(l1)
            best, bs = None, 0.0
            for ri2, (l2, v2) in enumerate(c2['rows']):
                if ri2 in used:
                    continue
                s = similar(n1, norm_name(l2))
                if s > bs:
                    best, bs = ri2, s
            if best is None or bs < 0.6:
                continue  # raw lost the row entirely; paddle checks cover
            used.add(best)
            l2, v2 = alt.contests[ci]['rows'][best]
            if v1 is None:
                # bw dropped or garbled the value; raw's label+value read
                # cleanly (e.g. 'Bryan Berghoef AR' vs 'Bryan Berghoef 172')
                c1['rows'][ri] = (l2, v2)
            elif v1 == v2:
                c1.setdefault('confirmed', set()).add(norm_name(l1))
            elif v1 != v2:
                # paddle is the tiebreak: a value both bw and paddle agree
                # on stands even when raw garbled it
                pv = paddle_value(pp, c1['title'], l1)
                if pv == v1:
                    continue
                if pv == v2 and v2 is not None:
                    c1['rows'][ri] = (l1, v2)
                    continue
                problems.append(f"p{page:03d} {c1['title']!r} {l1!r}: "
                                f'bw {v1} != raw {v2}, paddle {pv}')
        for ri2, (l2, v2) in enumerate(c2['rows']):
            if ri2 not in used:
                c1['rows'].append((l2, v2))
                problems.append(f"p{page:03d} {c1['title']!r}: row "
                                f"{l2!r}={v2} only in raw pass")


def paddle_value(pp, title, label):
    """The paddle parse's value for (title, label); titles and labels match
    fuzzily since OCR garbles either side. None when no good match."""
    nt = norm_name(title)
    nl = norm_name(label)
    vals = []
    for c in pp.contests:
        if similar(norm_name(c['title']), nt) < 0.7:
            continue
        for l, v in c['rows']:
            if similar(norm_name(l), nl) >= 0.7:
                vals.append(v)
    uniq = set(vals)
    return vals[0] if len(uniq) == 1 else None
    pg.problems = problems
    return pg


def parse_paddle(page):
    problems = []
    pg = parse_lines(paddle_lines(page), f'p{page:03d} paddle',
                     problems, junk_contests=True)
    pg.problems = problems
    return pg


def norm_name(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def junk_title(t):
    """Paddle sometimes drops contest titles entirely; such contests are
    still usable for value checks once matched by order."""
    nt = norm_name(t)
    return not nt or nt.isdigit()


def diff_contests(page, tp, pp):
    """Cross-check the tesseract consensus against paddle. When every
    contest matches by order/title, rows diff per contest; when paddle
    dropped or garbled titles (its fused-cell pages cram everything into
    one contest), fall back to a page-level multiset of values, which
    still catches any digit misread."""
    problems = []
    j = 0
    ok = True
    pairs = []
    for c1 in tp.contests:
        match = None
        for k in range(j, len(pp.contests)):
            t1, t2 = norm_name(c1['title']), \
                norm_name(pp.contests[k]['title'])
            if t1 == t2 or junk_title(t2) or similar(t1, t2) >= 0.75:
                match = k
                break
        if match is None:
            ok = False
            break
        pairs.append((c1, pp.contests[match]))
        j = match + 1
    leftover = len(pp.contests) - len(pairs)
    if ok and leftover <= sum(1 for c in pp.contests
                              if junk_title(c['title'])):
        for c1, c2 in pairs:
            confirmed = c1.get('confirmed', set())
            r1 = [(norm_name(l), v) for l, v in c1['rows']]
            r2 = [(norm_name(l), v) for l, v in c2['rows']]
            used = set()
            for l1, v1 in r1:
                best, bv, bs = None, None, 0.0
                for i2, (l2, v2) in enumerate(r2):
                    if i2 in used:
                        continue
                    s = similar(l1, l2)
                    if s > bs:
                        best, bv, bs = i2, v2, s
                if best is None or bs < 0.6:
                    if l1 not in confirmed:
                        problems.append(f'p{page:03d} {c1["title"]!r}: '
                                        f'tesseract row {l1!r}={v1} not in '
                                        f'paddle')
                    continue
                used.add(best)
                if bv != v1 and l1 not in confirmed:
                    # both tesseract passes agree; paddle's fused-cell
                    # pairing shifts write-in values by a row, so the
                    # consensus stands unless paddle ever disagrees
                    problems.append(f'p{page:03d} {c1["title"]!r} {l1!r}: '
                                    f'tesseract {v1} != paddle {bv}')
            for i2, (l2, v2) in enumerate(r2):
                if i2 not in used:
                    problems.append(f'p{page:03d} {c1["title"]!r}: paddle '
                                    f'row {l2!r}={v2} not in tesseract')
        return problems
    # page-level value multiset (labels unreliable on such pages)
    import collections
    m1 = collections.Counter(v for c in tp.contests for _, v in c['rows'])
    m2 = collections.Counter(v for c in pp.contests for _, v in c['rows'])
    d1 = m1 - m2
    d2 = m2 - m1
    if +d1 or +d2:
        problems.append(f'p{page:03d}: value multiset differs; tesseract '
                        f'only {dict(+d1)}, paddle only {dict(+d2)}')
    return problems


def map_title(t):
    if re.match(r'^United States Senator$', t, re.I):
        return 'U.S. Senate', ''
    m = re.match(r'^Representative in Congress (\d+)', t, re.I)
    if m:
        return 'U.S. House', m.group(1)
    if re.match(r'^Representative in State Legislature$', t, re.I):
        return 'State House', STATE_HOUSE
    m = re.match(r'^County Commissioner (\d+)', t, re.I)
    if m:
        return 'County Commissioner', m.group(1)
    if t in COUNTY_OFFICES:
        return t, ''
    m = re.match(r'^(Supervisor|Clerk|Treasurer|Trustee)\s+(.+)$', t)
    if m:
        office = f'{m.group(2)} Township {m.group(1)}'
        return TITLE_FIX.get(norm_name(office), office), ''
    if re.match(r'^Delegate to County', t, re.I):
        return 'DELEGATE', ''
    return TITLE_FIX.get(norm_name(t), t), ''  # proposals pass through


_R1 = norm_name('Representative in Congress 1st District')
_R2 = norm_name('Representative in Congress 2nd District')
_H = norm_name('Representative in State Legislature')
_SS = norm_name('United States Senator')
_CC5 = norm_name('County Commissioner 5th District')
_CC6 = norm_name('County Commissioner 6th District')
_CC7 = norm_name('County Commissioner 7th District')

# Visual adjudication results from page crops: (page, norm title, norm
# label) -> (clean label, value) to correct a row, or () to drop it.
MANUAL = {
    # p001 RepCong2 DEM: Berghoef 172 (raw right)
    (1, _R2, 'bryanberghoef'): ('Bryan Berghoef', '172'),
    # p004 Clerk Amber REP WI=3 (crop)
    (4, 'clerkamber', 'writeintotals'): ('Write-In Totals', '3'),
    # p004 Treasurer Amber REP WI=1
    (4, 'treasureramber', 'writeintotals'): ('Write-In Totals', '1'),
    # p006 Trustee Branch DEM WI=15 (bw right)
    (6, 'trusteebranch', 'writeintotals'): ('Write-In Totals', '15'),
    # p007 RepStateLeg REP: Cater 31 (bw 34/raw 3 garbled, paddle 31)
    (7, _H, 'carolyncater'): ('Carolyn Cater', '31'),
    # p007 Sheriff REP WI=0
    (7, 'sheriff', 'writeintotals'): ('Write-In Totals', '0'),
    # p008 Clerk Branch REP: Tenney 193 (bw right)
    (8, 'clerkbranch', 'kimberlytenney'): ('Kimberly Tenney', '193'),
    # p008 Delegate Branch P1 REP: C. D. Robinson 162 (bw right)
    (8, 'delegatetocountyconventionbranchtownshipprecinct1',
     'charlesdrobinson'): ('Charles D. Robinson', '162'),
    # p008 911: No=58 (bw 98 wrong); Yes 173 stands
    (8, 'masonoceana911', 'noae'): ('No', '58'),
    (8, 'masonoceana911', 'yes'): ('Yes', '173'),
    (8, 'masonoceana911', 'no'): ('No', '58'),
    # p010 USSen REP: James 212 (raw right, bw 242 wrong)
    (10, _SS, 'johnjames'): ('John James', '212'),
    # p009 StateLeg DEM: McGill-Rizer 57 (crop; both passes dropped the 5)
    (9, _H, 'bethmcgillrizer'): ('Beth McGill-Rizer', '57'),
    # p011 Sheriff REP WI=2
    (11, 'sheriff', 'writeintotals'): ('Write-In Totals', '2'),
    # p015 CC7 DEM: Ed Miller 25 (raw 'Ed Miller cee' garble)
    (15, _CC7, 'edmillercee'): ('Ed Miller', '25'),
    # p016 Drain Commissioner REP WI=1
    (16, 'draincommissioner', 'writeintotals'): ('Write-In Totals', '1'),
    # p018 USSen DEM: Peters 55 stands; silence paddle-only row
    (18, _SS, 'garypeters'): ('Gary Peters', '55'),
    # p018 RepCong1 DEM: O'Dell 17 (bw right); drop raw's 'O'Dell ee'=7 and
    # paddle's 'linda'=27
    (18, _R1, 'linda'): (),
    (18, _R1, 'lindaodellee'): (),
    (18, _R1, 'lindaodell'): ("Linda O'Dell", '17'),
    # p020 RepStateLeg REP: O'Malley 123; drop the raw-adopted 'Jack'=128
    (20, _H, 'jackomalley'): ("Jack O'Malley", '123'),
    (20, _H, 'jack'): (),
    # p021 Delegate Free Soil P1 REP: WI=13
    (21, 'delegatetocountyconventionfreesoiltownshipprecinct1',
     'writeintotals'): ('Write-In Totals', '13'),
    # p021 911: Yes 123, No 49; 'VES'=188 raw garble -> Yes 123
    (21, 'masonoceana911', 'ves'): ('Yes', '123'),
    # p022 RepCong1 DEM WI=0 (raw 9 wrong)
    (22, _R1, 'writeintotals'): ('Write-In Totals', '0'),
    # p022 USSen DEM: Peters 117 stands
    (22, _SS, 'garypeters'): ('Gary Peters', '117'),
    # p022 ProsAtty DEM WI=13
    (22, 'prosecutingattorney', 'writeintotals'): ('Write-In Totals', '13'),
    # p024 RepStateLeg: 'jack'=27 is the recurring paddle artifact
    (24, _H, 'jack'): (),
    # p026 Grant Road Millage: No=99 (bw right)
    (26, 'grantroadmillage', 'no'): ('No', '99'),
    # p032 RepCong2 DEM: Berghoef 154; drop raw 'Caylee'=4
    (32, _R2, 'bryanberghoef'): ('Bryan Berghoef', '154'),
    (32, _H, 'caylee'): (),
    # p033 Treasurer Hamlin WI=14 (bw right)
    (33, 'treasurerhamlin', 'writeintotals'): ('Write-In Totals', '14'),
    # p036 911: Yes 635, No 143, drop 'NO nnn'=48
    (36, 'masonoceana911', 'nonnn'): (),
    (36, 'masonoceana911', 'vcscee'): ('Yes', '635'),
    (36, 'masonoceana911', 'noeecesecemerstmeeesteete'): ('No', '143'),
    (36, 'masonoceana911', 'yes'): ('Yes', '635'),
    (36, 'masonoceana911', 'no'): ('No', '143'),
    # p036 Hamlin Fire Millage: Yes 666 ('Yes Coens' garble), No 113
    (36, 'hamiinfiremillage', 'yescoens'): ('Yes', '666'),
    (36, 'hamiinfiremillage', 'yes'): ('Yes', '666'),
    (36, 'hamiinfiremillage', 'no'): ('No', '113'),
    # p038 Supervisor Logan WI=9 (paddle right)
    (38, 'supervisorlogan', 'writeintotals'): ('Write-In Totals', '9'),
    # p041 Walkerville Public Schools: drop 'FOS oc ccsnitn'=18
    (41, 'walkervillepublicschools', 'fosocccsnitn'): (),
    # p043 RepStateLeg: 'jack'=27 artifact; ProsAtty DeRouin 73
    (43, _H, 'jack'): (),
    (43, 'prosecutingattorney', 'chadaderouin'): ('Chad A. DeRouin', '73'),
    # p044 Clerk: Cheryl Kelly 255 (paddle right)
    (44, 'clerk', 'cherylkellya'): ('Cheryl Kelly', '255'),
    # p045 911: Yes 343 ('Yes vcore' garble)
    (45, 'masonoceana911', 'yesvcore'): ('Yes', '343'),
    (45, 'masonoceana911', 'yes'): ('Yes', '343'),
    # p047 RepStateLeg: 'jack'=27 artifact; RepCong2 REP Huizenga 171
    (47, _H, 'jack'): (),
    (47, _R2, 'billhuizenga'): ('Bill Huizenga', '171'),
    # p048 911: No=51 ('noccnp' garble)
    (48, 'masonoceana911', 'noccnp'): ('No', '51'),
    (48, 'masonoceana911', 'no'): ('No', '51'),
    # p050 RepCong2 DEM: Berghoef 171 (paddle right)
    (50, _R2, 'bryanberghoef'): ('Bryan Berghoef', '171'),
    # p051 Delegate Ludington P3 DEM: only WI=38 (paddle right)
    (51, 'delegatetocountyconventioncityofludingtonprecinct3',
     'writeintotals'): ('Write-In Totals', '38'),
    # p052 Delegate Ludington P3 REP: McClelland 111
    (52, 'delegatetocountyconventioncityofludingtonprecinct3',
     'lylammcclelland'): ('Lyla M. McClelland', '111'),
    # p055 RepStateLeg: 'jack'=27 artifact
    (55, _H, 'jack'): (),
    # p056 Surveyor: Nordlund 94 (paddle right)
    (56, 'surveyor', 'jamestnordlund'): ('James T. Nordlund', '94'),
    # p059 Surveyor DEM WI=10 (bw right)
    (59, 'surveyor', 'writeintotals'): ('Write-In Totals', '10'),
    # p062 USSen DEM: Peters 106 ('Gary' truncated); McGill-Rizer 73
    (62, _SS, 'gary'): ('Gary Peters', '106'),
    (62, _SS, 'garypeters'): ('Gary Peters', '106'),
    (62, _H, 'bethmcgillrizer7'): ('Beth McGill-Rizer', '73'),
    # p065 911: Yes 221, No 46 (crop); 'NO ne' is a duplicate read of the
    # same row (both garbles read 46) — keep one
    (65, 'masonoceana911', 'nocenaamnueeeee'): ('No', '46'),
    (65, 'masonoceana911', 'none'): (),
    (65, 'masonoceana911', 'no'): ('No', '46'),
    (65, 'ludingtonmasstransportationauthority', 'no'): ('No', '55'),
    # p067 Drain Commissioner DEM WI=0 (paddle right)
    (67, 'draincommissioner', 'writeintotals'): ('Write-In Totals', '0'),
    # p069 Delegate Meade P1: WI=1 (paddle right)
    (69, 'delegatetocountyconventionmeadetownshipprecinct1',
     'writeintotals'): ('Write-In Totals', '1'),
    # p071 USSen DEM: Peters 71 (tesseract 7 wrong)
    (71, _SS, 'garypeters'): ('Gary Peters', '71'),
    # p072 Clerk PM Charter DEM WI=1; Soberalski 66 (bw right)
    (72, 'clerkperemarquettecharter', 'writeintotals'): ('Write-In Totals',
                                                         '1'),
    (72, 'trusteeperemarquettecharter', 'ronaldlsoberalski'):
        ('Ronald L. Soberalski', '66'),
    # p073 RepStateLeg REP WI=1 (paddle right)
    (73, _H, 'writeintotals'): ('Write-In Totals', '1'),
    # p074 Trustee PM Charter REP: Rasmussen 114 (paddle right)
    (74, 'trusteeperemarquettecharter', 'henryerasmussen'):
        ('Henry E. Rasmussen', '114'),
    # p076 Clerk PM Charter DEM WI=12; Surveyor WI=11; James 315
    (76, 'clerkperemarquettecharter', 'writeintotals'): ('Write-In Totals',
                                                         '12'),
    (76, 'surveyor', 'writeintotals'): ('Write-In Totals', '11'),
    (76, _SS, 'johnjames'): ('John James', '315'),
    # p077 RepCong2 REP: Huizenga 311; RepStateLeg Cater 71; Sheriff WI=1
    (77, _R2, 'billhuizenga'): ('Bill Huizenga', '311'),
    (77, _H, 'carolyncater'): ('Carolyn Cater', '71'),
    (77, 'sheriff', 'writeintotals'): ('Write-In Totals', '1'),
    # p078 Clerk PM Charter REP: Enbody 312, WI=0
    (78, 'clerkperemarquettecharter', 'writeintotals'): ('Write-In Totals',
                                                         '0'),
    # p079 911: No=81 ('No.' label, value right)
    (79, 'masonoceana911', 'no'): ('No', '81'),
    # p081 Treasurer Riverton DEM WI=14 (bw right)
    (81, 'treasurerriverton', 'writeintotals'): ('Write-In Totals', '14'),
    # p082 RepStateLeg REP: Cater 24 stands
    (82, _H, 'carolyncater'): ('Carolyn Cater', '24'),
    # p083 Surveyor: Nordlund 155 (bw right), WI=0, drop 'No cine'=81
    (83, 'surveyor', 'jamestnordlund'): ('James T. Nordlund', '155'),
    (83, 'surveyor', 'writeintotals'): ('Write-In Totals', '0'),
    (83, 'masonoceana911', 'nocine'): (),
    # p083 Trustee Riverton: Thurow 177 (tesseract right)
    (83, 'trusteeriverton', 'gregthurow'): ('Greg Thurow', '177'),
    # p085 CC5 DEM WI=3; Drain Commissioner DEM WI=3; ProsAtty WI=0
    (85, _CC5, 'writeintotals'): ('Write-In Totals', '3'),
    (85, 'draincommissioner', 'writeintotals'): ('Write-In Totals', '3'),
    (85, 'prosecutingattorney', 'writeintotals'): ('Write-In Totals', '0'),
    # p088 RepStateLeg DEM: Cary L. Urka 9 (raw 'Caryl. Urka ce' garble)
    (88, _H, 'carylurkace'): ('Cary L. Urka', '9'),
    # p089 CC5 DEM WI=4; USSen REP WI=1; RepStateLeg Cater 4
    (89, _CC5, 'writeintotals'): ('Write-In Totals', '4'),
    (89, _SS, 'writeintotals'): ('Write-In Totals', '1'),
    (89, _H, 'carolyncater'): ('Carolyn Cater', '4'),
    # p090 Register of Deeds REP: Englebrecht 48 (raw label garble)
    (90, 'registerofdeeds', 'dianelenglebrecht'): ('Diane L. Englebrecht',
                                                   '48'),
    # p092 RepCong1 DEM WI=4 (tesseract right)
    (92, _R1, 'writeintotals'): ('Write-In Totals', '4'),
    # p096 911: No=57 ('nocoane' garble)
    (96, 'masonoceana911', 'nocoane'): ('No', '57'),
    (96, 'masonoceana911', 'no'): ('No', '57'),
    # p098 Trustee Sherman DEM WI=1 (paddle right)
    (98, 'trusteesherman', 'writeintotals'): ('Write-In Totals', '1'),
    # p099 StateLeg REP: Cater 17 (crop; both passes dropped the 1)
    (99, _H, 'carolyncateram'): ('Carolyn Cater', '17'),
    # p100 911: No=41 (bw right)
    (100, 'masonoceana911', 'no'): ('No', '41'),
    # p102 Trustee Summit DEM: only WI=9 (raw right)
    (102, 'trusteesummit', 'writeintotals'): ('Write-In Totals', '9'),
    # p103 RepStateLeg REP Cater 48; Clerk Kelly 193; Rohde 183 stands
    (103, _H, 'carolyncaterce'): ('Carolyn Cater', '48'),
    (103, _H, 'carolyncater'): ('Carolyn Cater', '48'),
    (103, 'clerk', 'cherylkelly'): ('Cheryl Kelly', '193'),
    (103, 'draincommissioner', 'danrohde'): ('Dan Rohde', '183'),
    # p104 911: Yes 245 ('Yes ov tt vise' garble), No 36 ('nocncane');
    # drop the raw-adopted 'Yes onus' duplicate
    (104, 'masonoceana911', 'yesovttvise'): ('Yes', '245'),
    (104, 'masonoceana911', 'nocncane'): ('No', '36'),
    (104, 'masonoceana911', 'no'): ('No', '36'),
    (104, 'masonoceana911', 'yes'): ('Yes', '245'),
    (104, 'masonoceana911', 'yesonus'): (),
    # p109 911: Yes 239 (bw right), No 57
    (109, 'masonoceana911', 'yes'): ('Yes', '239'),
    # p017 911: Yes 128, No 32 (crop; 'No sb eocce ton vntanennn' garble)
    (17, 'masonoceana911', 'nosbeoccetonvntanennn'): ('No', '32'),
    # p021 Surveyor: Nordlund 74 (crop; tp 'an' row read 4)
    (21, 'surveyor', 'jamestnordlundan'): ('James T. Nordlund', '74'),
    # p021 911: Yes 123 ('Yes ce ce'), No 49 ('No ne')
    (21, 'masonoceana911', 'yescece'): ('Yes', '123'),
    (21, 'masonoceana911', 'none'): ('No', '49'),
    # p040 911: No 18 ('No 7 oe' label garble, value right)
    (40, 'masonoceana911', 'no7oe'): ('No', '18'),
    # p063 RepStateLeg: O'Malley 126 ('Jack O'Malley 7' label garble)
    (63, _H, 'jackomalley7'): ("Jack O'Malley", '126'),
    # p074 Surveyor: Schulke 41 (crop; tp 4); 911: Yes 172 ('FOS enn')
    (74, 'surveyor', 'johncschulke'): ('John C. Schulke', '41'),
    (74, 'masonoceana911', 'fosenn'): ('Yes', '172'),
    # p075 USSen DEM: Peters 149 (crop; tp 9)
    (75, _SS, 'garypeters'): ('Gary Peters', '149'),
    # p078 Surveyor: Schulke 77 (crop; tp 7); delegate Ingraham 233 (crop)
    (78, 'surveyor', 'johncschulke'): ('John C. Schulke', '77'),
    (78, 'delegatetocountyconventionperemarquettechartertwppct2',
     'cathyingraham'): ('Cathy Ingraham', '233'),
    # p080 USSen/Cong2 DEM: 75 each (paddle right; tp '1' wrong)
    (80, _SS, 'garypeters'): ('Gary Peters', '75'),
    (80, _R2, 'bryanberghoef'): ('Bryan Berghoef', '75'),
    # p100 Drain Commissioner REP WI=1 (crop; tp 4); 911 'Yes...' label
    (100, 'draincommissioner', 'writeintotals'): ('Write-In Totals', '1'),
    (100, 'masonoceana911', 'yes'): ('Yes', '184'),
}

# Rows the tesseract passes missed entirely but crops confirmed:
# (page, norm title) -> [(clean label, value)] appended when absent.
ADDS = {
    # p003 Drain Commissioner REP WI=2
    (3, 'draincommissioner'): [('Write-In Totals', '2')],
    # p005 USSen DEM WI=0 (pp right; tp lost the row)
    (5, _SS): [('Write-In Totals', '0')],
    # p010 Supervisor/Trustee Custer DEM: WI-only blocks per crops
    (10, 'supervisorcuster'): [('Write-In Totals', '4')],
    (10, 'trusteecuster'): [('Write-In Totals', '5')],
    # p006 USSen REP WI=3 (raw-adopted; no-op + silences)
    (6, _SS): [('Write-In Totals', '3')],
    # p007 Drain Commissioner REP WI=1
    (7, 'draincommissioner'): [('Write-In Totals', '1')],
    # p009 USSen DEM WI=0
    (9, _SS): [('Write-In Totals', '0')],
    # p011 Treasurer REP WI=1
    (11, 'treasurer'): [('Write-In Totals', '1')],
    # p015 Delegate Eden P1 DEM WI=1
    (15, 'delegatetocountyconventionedentownshipprecinct1'):
        [('Write-In Totals', '1')],
    # p018 RepCong1 DEM WI=0; USSen DEM WI=0
    (18, _R1): [("Linda O'Dell", '17'), ('Write-In Totals', '0')],
    (18, _SS): [('Gary Peters', '55'), ('Write-In Totals', '0')],
    (20, _H): [('Write-In Totals', '0')],
    (20, 'clerk'): [('Write-In Totals', '1')],
    (20, 'registerofdeeds'): [('Write-In Totals', '0')],
    (20, _SS): [('Write-In Totals', '0')],
    (20, 'prosecutingattorney'): [('Write-In Totals', '0')],
    (21, _CC6): [('Write-In Totals', '1')],
    (29, 'prosecutingattorney'): [('Write-In Totals', '1')],
    (29, 'registerofdeeds'): [('Write-In Totals', '1')],
    (33, 'clerkhamlin'): [('Write-In Totals', '13')],
    (33, 'supervisorhamlin'): [('Write-In Totals', '14')],
    (36, 'delegatetocountyconventionhamlintownshipprecinct2'):
        [('Write-In Totals', '1')],
    (43, 'surveyor'): [('Write-In Totals', '6')],
    (46, 'sheriff'): [('Write-In Totals', '11')],
    (47, _R2): [('Bill Huizenga', '171')],
    (55, _R2): [('Write-In Totals', '1')],
    (58, 'prosecutingattorney'): [('Write-In Totals', '11')],
    (65, 'ludingtonmasstransportationauthority'): [('Yes', '211')],
    (65, 'masonoceana911'): [('Yes', '221')],
    (66, _R1): [('Write-In Totals', '0')],
    (68, 'registerofdeeds'): [('Write-In Totals', '0')],
    (76, 'draincommissioner'): [('Write-In Totals', '11')],
    (76, 'treasurerperemarquettecharter'): [('Write-In Totals', '11')],
    (76, 'delegatetocountyconventionperemarquettechartertwppct2'):
        [('Write-In Totals', '0')],
    (77, 'registerofdeeds'): [('Write-In Totals', '0')],
    # DEM-side blocks tesseract read as 0 rows; single write-in rows per crops
    (10, 'surveyor'): [('Write-In Totals', '4')],
    (19, 'delegatetocountyconventionfreesoiltownshipprecinct1'):
        [('Write-In Totals', '7')],
    (28, 'draincommissioner'): [('Write-In Totals', '7')],
    (28, 'clerkhamlin'): [('Write-In Totals', '7')],
    (72, 'registerofdeeds'): [('Write-In Totals', '3')],
    (75, 'sheriff'): [('Write-In Totals', '11')],
    (75, 'clerk'): [('Write-In Totals', '11')],
    (82, 'treasurer'): [('Write-In Totals', '1')],
    (83, 'surveyor'): [('John C. Schulke', '55')],
    (84, _H): [('Write-In Totals', '0')],
    (86, 'masonoceana911'): [('No', '12')],
    (89, _H): [('Write-In Totals', '0')],
    (92, _H): [('Write-In Totals', '0')],
    (94, 'prosecutingattorney'): [('Write-In Totals', '0')],
    (97, _SS): [('Gary Peters', '61'), ('Write-In Totals', '0')],
    (99, 'clerk'): [('Write-In Totals', '1')],
    (103, 'prosecutingattorney'): [('Write-In Totals', '1')],
    (103, 'registerofdeeds'): [('Write-In Totals', '0')],
    # candidate rows only paddle saw (crops confirmed the values); tp's
    # MANUAL entries for these silenced the problem lines without the row
    (22, _SS): [('Gary Peters', '117')],
    (37, _H): [('Cary L. Urka', '8')],
    (76, _SS): [('John James', '315')],
    (82, _H): [('Carolyn Cater', '24')],
    (104, 'delegatetocountyconventionsummittownshipprecinct1'):
        [('Write-In Totals', '7')],
    (107, 'clerk'): [('Write-In Totals', '1')],
}

# OCR garbles of pass-through (proposal) titles -> correct office text
TITLE_FIX = {
    'drain1commissioner': 'Drain Commissioner',
    'hamiinfiremillage': 'Hamlin Fire Millage',
    'registerofdeeds7': 'Register of Deeds',
    'surveyor': 'Surveyor',  # 'Surveyor ‘' curly-quote garble (p106)
    'legantownshipsupervisor': 'Logan Township Supervisor',
}

# candidate-name garbles (norm form) -> correct name; anything not here is
# caught by the name census
NAME_FIX = {
    'bethmcgillrize': 'Beth McGill-Rizer',
    'bethmcgillrizen': 'Beth McGill-Rizer',
    'bethmcgillrizer': 'Beth McGill-Rizer',
    'carolyncater': 'Carolyn Cater',
    'carolyncateram': 'Carolyn Cater',
    'carolyncaternn': 'Carolyn Cater',
    'cherylkelly': 'Cheryl Kelly',
    'cherylkellya': 'Cheryl Kelly',
    'charteslange': 'Charles Lange',
    'connielanderson': 'Connie L. Anderson',
    'carylurka': 'Cary L. Urka',
    'dianelenglebrecht': 'Diane L. Englebrecht',
    'dianelenglebrechts': 'Diane L. Englebrecht',
    'edmillercee': 'Ed Miller',
    'edmillerseen': 'Ed Miller',
    'edmilter': 'Ed Miller',
    'larryreescoe': 'Larry Rees',
    'lamyalar': 'Larry A. Larr',
    'johnvames': 'John James',
    # write-in label garbles (emit applies NAME_FIX before the write-in
    # check, so these collapse into the Write-In row)
    'writetn': 'Write-In Totals',
    'writeinim': 'Write-In Totals',
    # Yes/No proposal label garbles
    'yes': 'Yes',
    'no': 'No',
    'yes7': 'Yes',
    'yesonus': 'Yes',
    'yesae': 'Yes',
    'yescouse': 'Yes',
    'yesoe': 'Yes',
    'no7': 'No',
    'noty': 'No',
    'none': 'No',
    # township-office candidate garbles
    'jodyhartley': 'Jody Hartley',
    'stevenkhull': 'Steven K. Hull',
    'danrohde': 'Dan Rohde',
    'danrohde7': 'Dan Rohde',
    'danrohdeoe': 'Dan Rohde',
    'danrehdeee': 'Dan Rohde',
    'rogernasho': 'Roger Nash',
    'melissarreister': 'Melissa R. Reister',
    'jimmyleemetzger': 'Jimmy Lee Metzger',
    'geraldableau': 'Gerald A. Bleau',
    'kariekbleau': 'Karie K. Bleau',
    'ronaldlsoberalski': 'Ronald L. Soberalski',
    'andrewrkmetziv': 'Andrew R. Kmetz IV',
    'andrewrkmetz': 'Andrew R. Kmetz IV',
    'andrewrkmetzlv': 'Andrew R. Kmetz IV',
    'andrewrkmetzv': 'Andrew R. Kmetz IV',
    'andrewrkmetzill': 'Andrew R. Kmetz III',
    'andrewrkmetzil': 'Andrew R. Kmetz III',
    'jamestnordlund': 'James T. Nordlund',
    'jamestnordund': 'James T. Nordlund',
    'jamestnordlundae': 'James T. Nordlund',
    'jasonlwolven': 'Jason L. Wolven',
    'chadaderouin': 'Chad A. DeRouin',
    'chadaderouvin': 'Chad A. DeRouin',
    'chadaderovin': 'Chad A. DeRouin',
    'laurenrkreinbrink': 'Lauren R. Kreinbrink',
    'dianelengtebrecht': 'Diane L. Englebrecht',
    'marlynnegulembo': 'Marlynn E. Gulembo',
    'corlisstgulembo': 'Corliss T. Gulembo III',
    'corlisstgulembotl': 'Corliss T. Gulembo III',
    'jeffcormany': 'Jeff Cormany',
    'kimccole': 'Kim C. Cole',
    'kimcole': 'Kim C. Cole',
    'kimcoleee': 'Kim C. Cole',
    'bethmcgilrizer': 'Beth McGill-Rizer',
    'johnschulke': 'John C. Schulke',
    'johncschulke': 'John C. Schulke',
    'patriciaageers': 'Patricia A. Geers',
    'patriciaageersve': 'Patricia A. Geers',
    'danielrstewart': 'Daniel R. Stewart',
    # countywide candidate garbles
    'bryanberghoef': 'Bryan Berghoef',
    'bryanberghcef': 'Bryan Berghoef',
    'bryanberghoefot': 'Bryan Berghoef',
    'bryanberghoefce': 'Bryan Berghoef',
    'danaferguson': 'Dana Ferguson',
    'lindaodell': "Linda O'Dell",
    'lindaodel': "Linda O'Dell",
    'billhuizenga': 'Bill Huizenga',
    'billhuizenga7': 'Bill Huizenga',
    'jackbergman': 'Jack Bergman',
    'jackbergmanso': 'Jack Bergman',
    'dackbergman': 'Jack Bergman',
    'garypeters': 'Gary Peters',
    'garypetes': 'Gary Peters',
    'peters': 'Gary Peters',
    'johnjames': 'John James',
    'johndjames': 'John James',
}


def apply_manual(page, tp):
    """Rewrite/drop rows per MANUAL, then ensure ADDS rows are present."""
    for c in tp.contests:
        nt = norm_name(c['title'])
        keep = []
        for label, value in c['rows']:
            fix = MANUAL.get((page, nt, norm_name(label)))
            if fix is None:
                fix = MANUAL.get((page, '', norm_name(label)))
            if fix is None:
                keep.append((label, value))
            elif fix:  # ('clean label', 'value')
                keep.append(fix)
            # else: drop
        for label, value in ADDS.get((page, nt), []):
            if not any(norm_name(l) == norm_name(label) and v == value
                       for l, v in keep):
                keep.append((label, value))
        # MANUAL rewrites can collide with an existing clean row; keep one
        seen = set()
        dedup = []
        for label, value in keep:
            k = (norm_name(label), value)
            if k not in seen:
                seen.add(k)
                dedup.append((label, value))
        c['rows'] = dedup


# Pages whose value-multiset diffs are crop-verified tesseract-correct:
# paddle lost or scrambled the block (or its parse truncated), so the
# diff reflects pp's failure, not bad tesseract values.
SUPPRESS_MULTISET = {4, 5, 12, 17, 19, 28, 37, 40, 42, 49, 51, 59, 63, 70,
                     72, 74, 75, 80, 84, 87, 100, 102}


def filter_problems(problems):
    """Drop problems for rows that MANUAL/ADDS have adjudicated."""
    out = []
    for p in problems:
        pm = re.match(r'p(\d{3})', p)
        if pm and 'value multiset' in p and \
                int(pm.group(1)) in SUPPRESS_MULTISET:
            continue
        # labels with apostrophes are double-quoted in problem strings
        quotes = [a or b for a, b in re.findall(
            r"'([^']*)'|\"([^\"]*)\"", p)]
        if pm and len(quotes) >= 2:
            page = int(pm.group(1))
            key = (page, norm_name(quotes[0]), norm_name(quotes[1]))
            if key in MANUAL or (page, '', norm_name(quotes[1])) in MANUAL:
                continue
            adds = ADDS.get((page, norm_name(quotes[0])))
            if adds and any(norm_name(l) == norm_name(quotes[1])
                            for l, _ in adds):
                continue
        out.append(p)
    return out


def emit(c, precinct, rows, problems, page):
    office, district = map_title(c['title'])
    if office == 'DELEGATE':
        # delegate titles are per-precinct and frequently OCR-truncated;
        # the page heading is authoritative
        office = f'{precinct} Delegate to County Convention'
        district = ''
    write_in = None
    for label, value in c['rows']:
        label = NAME_FIX.get(norm_name(label), label)
        if WRITEIN_RE.match(label):
            write_in = value
        else:
            rows.append([COUNTY, precinct, office, district, c['party'],
                         label, value])
    if write_in is not None:
        rows.append([COUNTY, precinct, office, district, c['party'],
                     'Write-In', write_in])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--pages', default='')
    args = ap.parse_args()
    pages = ([int(p) for p in args.pages.split(',')] if args.pages
             else range(1, 110))
    rows, problems = [], []
    for page in pages:
        tp, alt, tprobs = parse_tesseract(page)
        pp = parse_paddle(page)
        reconcile(page, tp, alt, pp, tprobs)
        apply_manual(page, tp)
        problems += [f'p{page:03d}: {p}' for p in tprobs]
        problems += [f'p{page:03d}: {p}' for p in pp.problems]
        precinct = tp.precinct or pp.precinct
        if not precinct:
            problems.append(f'p{page:03d}: no precinct heading')
            continue
        if tp.precinct and pp.precinct and tp.precinct != pp.precinct:
            problems.append(f'p{page:03d}: heading bw {tp.precinct!r} != '
                            f'paddle {pp.precinct!r}')
        # Statistics -> pseudo rows (only the first page of each precinct
        # set carries the block); compare only the keys we emit, since
        # paddle mispairs party-split stats rows on fused pages
        for word, canon in (('registered voters', 'Registered Voters'),
                            ('ballots cast', 'Ballots Cast')):
            vals = {}
            for tag, pgx in (('bw', tp), ('paddle', pp)):
                for k, v in pgx.stats.items():
                    nk = k.lower().replace('-', ' ')
                    if word in nk and 'total' in nk:
                        vals.setdefault(tag, v)
            if len(set(vals.values())) > 1:
                problems.append(f'p{page:03d} {canon}: {vals}')
            if 'bw' in vals:
                rows.append([COUNTY, precinct, canon, '', '', '',
                             vals['bw']])

        problems += diff_contests(page, tp, pp)
        for c in tp.contests:
            emit(c, precinct, rows, problems, page)
    # countywide contests must print for the same precinct set in both
    # parties (delegates/proposals/township offices are precinct-scoped)
    import collections
    by_office = collections.defaultdict(lambda: collections.defaultdict(set))
    for r in rows:
        if r[4] in ('DEM', 'REP'):
            by_office[(r[2], r[3])][r[4]].add(r[1])
    for (office, dist), parties in sorted(by_office.items()):
        if len(parties) == 2 and parties['DEM'] != parties['REP']:
            problems.append(
                f'{office} d{dist}: DEM/REP precinct sets differ: '
                f"DEM-only {sorted(parties['DEM'] - parties['REP'])} "
                f"REP-only {sorted(parties['REP'] - parties['DEM'])}")
    problems = filter_problems(problems)
    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'Wrote {len(rows)} rows to {args.out} ({len(problems)} problems)')
    # candidate-name census: the same person should spell identically in
    # every precinct; outliers here are OCR garbles
    import collections
    census = collections.Counter(
        (r[2], r[4], r[5]) for r in rows
        if r[5] not in ('Write-In', '') and r[2] not in
        ('Registered Voters', 'Ballots Cast'))
    for (office, party, name), n in sorted(census.items()):
        print(f'{n:4d}  {office} [{party}] {name}', file=sys.stderr)
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(1)


if __name__ == '__main__':
    main()