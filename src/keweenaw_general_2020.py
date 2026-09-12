#!/usr/bin/env python3
"""Parse Keweenaw County's Nov 2020 general election from the Bureau of
Elections official survey form ('Keweenaw MI 2020 General.pdf', 8pp).

The form is a grid: township rows (one precinct per township) under
contest columns laid out side by side.  Every contest column carries a
party code on the party line, a candidate name printed as a vertical
char stack (each char shares the column's x; read bottom-up), and the
TOTALS row gives every column's county total.

The office/party/candidate for each value column is hardcoded below
(the form's staggered title rows don't overlap their columns reliably);
the mechanical party tokens and name stacks are still decoded and
cross-checked against the map.  Votes stay fully mechanical.

There is no CENR county-file target (Keweenaw is absent from
2020/20201103__mi__general__county.csv), so verification is internal:
every column's township sums equal its printed TOTALS value, the
page-1 poll-book column sums to the county poll-book total, the
page-8 per-township VOTED column matches the poll book, and the
page-8 REGISTERED column sums to the printed county figure.
"""

import csv
import re
import sys
from collections import defaultdict

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/general/'
       'Keweenaw MI 2020 General.pdf')
OUT = '2020/counties/20201103__mi__general__keweenaw__precinct.csv'
COUNTY = 'Keweenaw'

TOWNSHIPS = {
    'ALLOUEZ': 'Allouez Township, Precinct 1',
    'EAGLEHARBOR': 'Eagle Harbor Township, Precinct 1',
    'GRANT': 'Grant Township, Precinct 1',
    'HOUGHTON': 'Houghton Township, Precinct 1',
    'SHERMAN': 'Sherman Township, Precinct 1',
}

PARTIES = {'D': 'DEM', 'R': 'REP', 'LIB': 'LIB', 'US TAX': 'UST',
           'GREEN': 'GRN', 'NAT LAW': 'NLP', 'WORK CL': 'WCP'}
PAIRS = [('US', 'TAX'), ('NAT', 'LAW'), ('WORK', 'CL')]
SOLO = {'WI', 'NPA', 'NONPARTISAN'}

# Value columns per page: (page index, value x0, office, district, party,
# candidate).  x0 is the left edge of the printed vote numbers.  Party ''
# means the contest is nonpartisan (party blank in the CSV); named
# write-in candidates also carry a blank party.
COLUMN_MAP = [
    # page 1: President + U.S. Senate (col 112 is the POLL BOOK column)
    (0, 160, 'President', '', 'DEM', 'Joseph R. Biden'),
    (0, 205, 'President', '', 'REP', 'Donald J. Trump'),
    (0, 253, 'President', '', 'LIB', 'Jo Jorgensen'),
    (0, 301, 'President', '', 'UST', 'Don Blankenship'),
    (0, 349, 'President', '', 'GRN', 'Howie Hawkins'),
    (0, 394, 'President', '', 'NLP', 'Rocky De La Fuente'),
    (0, 434, 'President', '', '', 'Brian T. Carroll'),
    (0, 479, 'President', '', '', 'Tom Hoefling'),
    (0, 524, 'President', '', '', 'Tara Renee Hunter'),
    (0, 569, 'President', '', '', 'Jade Simmons'),
    (0, 614, 'President', '', '', 'Kasey Wells'),
    (0, 656, 'U.S. Senate', '', 'DEM', 'Gary Peters'),
    (0, 704, 'U.S. Senate', '', 'REP', 'John James'),
    (0, 750, 'U.S. Senate', '', 'UST', 'Valerie L. Willis'),
    (0, 795, 'U.S. Senate', '', 'GRN', 'Marcia Squier'),
    (0, 840, 'U.S. Senate', '', 'NLP', 'Doug Dern'),
    (0, 885, 'U.S. Senate', '', '', 'Robert William Carr'),
    (0, 930, 'U.S. Senate', '', '', 'Leonard Paul Gadzinski'),
    # page 2: U.S. House 1, State House 110, State Board of Education
    (1, 112, 'U.S. House', '1', 'DEM', 'Dana Ferguson'),
    (1, 162, 'U.S. House', '1', 'REP', 'Jack Bergman'),
    (1, 215, 'U.S. House', '1', 'LIB', 'Ben Boren'),
    (1, 262, 'State House', '110', 'DEM', 'Janet Metsa'),
    (1, 312, 'State House', '110', 'REP', 'Gregory Markkanen'),
    (1, 367, 'State House', '110', 'GRN', 'Rick Sauermilch'),
    (1, 412, 'Member of the State Board of Education', '', 'DEM',
     'Ellen Cogen Lipton'),
    (1, 461, 'Member of the State Board of Education', '', 'DEM',
     'Jason Strayhorn'),
    (1, 511, 'Member of the State Board of Education', '', 'REP',
     'Tami Carlone'),
    (1, 561, 'Member of the State Board of Education', '', 'REP',
     'Michelle A. Frederick'),
    (1, 614, 'Member of the State Board of Education', '', 'LIB',
     'Bill Hall'),
    (1, 664, 'Member of the State Board of Education', '', 'LIB',
     'Richard A. Hewer'),
    (1, 714, 'Member of the State Board of Education', '', 'UST',
     'Karen Adams'),
    (1, 764, 'Member of the State Board of Education', '', 'UST',
     'Douglas Levesque'),
    (1, 814, 'Member of the State Board of Education', '', 'WCP',
     'Mary Ann Hering'),
    (1, 864, 'Member of the State Board of Education', '', 'WCP',
     'Hali McEachern'),
    (1, 914, 'Member of the State Board of Education', '', 'GRN',
     'Tom Mair'),
    # page 3: Regent of U-M, Trustee of MSU
    (2, 106, 'Regent of the University of Michigan', '', 'DEM',
     'Mark Bernstein'),
    (2, 150, 'Regent of the University of Michigan', '', 'DEM',
     'Shauna Ryder Diggs'),
    (2, 195, 'Regent of the University of Michigan', '', 'REP',
     'Sarah Hubbard'),
    (2, 239, 'Regent of the University of Michigan', '', 'REP',
     'Carl Meyers'),
    (2, 286, 'Regent of the University of Michigan', '', 'LIB',
     'James L. Hudler'),
    (2, 330, 'Regent of the University of Michigan', '', 'LIB',
     'Eric Larson'),
    (2, 377, 'Regent of the University of Michigan', '', 'UST',
     'Ronald E. Graeser'),
    (2, 419, 'Regent of the University of Michigan', '', 'UST',
     'Crystal Van Sickle'),
    (2, 464, 'Regent of the University of Michigan', '', 'GRN',
     'Michael Mawilai'),
    (2, 508, 'Regent of the University of Michigan', '', 'NLP',
     'Keith Butkovich'),
    (2, 549, 'Trustee of Michigan State University', '', 'DEM',
     'Brian Mosallam'),
    (2, 593, 'Trustee of Michigan State University', '', 'DEM',
     'Rema Ella Vassar'),
    (2, 637, 'Trustee of Michigan State University', '', 'REP',
     "Pat O'Keefe"),
    (2, 682, 'Trustee of Michigan State University', '', 'REP',
     'Tonya Schuitmaker'),
    (2, 729, 'Trustee of Michigan State University', '', 'LIB',
     'Will Tyler White'),
    (2, 773, 'Trustee of Michigan State University', '', 'UST',
     'Janet M. Sanger'),
    (2, 817, 'Trustee of Michigan State University', '', 'UST',
     'John Paul Sanger'),
    (2, 862, 'Trustee of Michigan State University', '', 'GRN',
     'Brandon Hu'),
    (2, 906, 'Trustee of Michigan State University', '', 'GRN',
     'Robin Lea Laurain'),
    (2, 951, 'Trustee of Michigan State University', '', 'NLP',
     'Bridgette Abraham-Guzman'),
    # page 4: Governor of WSU + county offices
    (3, 109, 'Governor of Wayne State University', '', 'DEM',
     'Eva Garza Dewaelsche'),
    (3, 155, 'Governor of Wayne State University', '', 'DEM',
     'Shirley Stancato'),
    (3, 202, 'Governor of Wayne State University', '', 'REP', 'Don Gates'),
    (3, 249, 'Governor of Wayne State University', '', 'REP',
     'Terri Lynn Land'),
    (3, 298, 'Governor of Wayne State University', '', 'LIB', 'Jon Elgas'),
    (3, 345, 'Governor of Wayne State University', '', 'UST',
     'Christine C. Schwartz'),
    (3, 392, 'Governor of Wayne State University', '', 'GRN',
     'Susan Odgers'),
    (3, 441, 'Governor of Wayne State University', '', '',
     'Lloyd Arthur Conway'),
    (3, 480, 'County Prosecuting Attorney', '', 'DEM', 'Charles W. Miller'),
    (3, 525, 'County Sheriff', '', 'REP', 'Curt E. Pennala'),
    (3, 570, 'County Clerk-Register of Deeds', '', 'DEM', 'Julie Carlson'),
    (3, 613, 'County Treasurer', '', 'REP', 'Eric Hermanson'),
    (3, 656, 'County Mine Inspector', '', '', 'John Cima'),
    (3, 699, 'County Road Commissioner', '', 'REP', 'John Glenn Karvonen'),
    (3, 741, 'County Commissioner', '1', 'DEM', 'Donald Piche'),
    (3, 784, 'County Commissioner', '2', 'DEM', 'James Vivian'),
    (3, 827, 'County Commissioner', '3', 'REP', 'Del Rajala'),
    (3, 870, 'County Commissioner', '4', 'DEM', 'Robert G. DeMarois'),
    (3, 912, 'County Commissioner', '5', 'DEM', 'Sandra Gayk'),
    (3, 955, 'County Commissioner', '5', 'REP', 'Randy L. Eckloff'),
    # page 5: Allouez Twp / Ahmeek Village / Eagle Harbor Twp
    (4, 108, 'Allouez Township Supervisor', '', 'REP', 'Mark W. Aho'),
    (4, 150, 'Allouez Township Clerk', '', 'REP', 'Trudi Haataja'),
    (4, 193, 'Allouez Township Treasurer', '', 'REP', 'Julie Newman'),
    (4, 234, 'Allouez Township Trustee', '', 'DEM', 'Daniel Yoder'),
    (4, 276, 'Allouez Township Trustee', '', 'REP', 'Roger Haataja'),
    (4, 318, 'Allouez Township Trustee', '', '', 'John Kaura'),
    (4, 365, 'Ahmeek Village President', '', '', 'John Anton Spagnotti'),
    (4, 415, 'Ahmeek Village Clerk', '', '', 'Amy L. Kumpula'),
    (4, 467, 'Ahmeek Village Treasurer', '', '', 'Coreen M. Balbough'),
    (4, 514, 'Ahmeek Village Trustee', '', '', 'Susan R. Gherna'),
    (4, 556, 'Ahmeek Village Trustee', '', '', 'Dave Maki'),
    (4, 598, 'Ahmeek Village Trustee', '', '', 'Frank D. Spagnotti'),
    (4, 637, 'Eagle Harbor Township Supervisor', '', 'REP',
     'Rich Probst Jr.'),
    (4, 679, 'Eagle Harbor Township Clerk', '', 'DEM', 'Jeane Olson'),
    (4, 721, 'Eagle Harbor Township Treasurer', '', 'REP', 'Patty Asselin'),
    (4, 763, 'Eagle Harbor Township Trustee', '', 'REP', 'Connie Eddy'),
    (4, 805, 'Eagle Harbor Township Trustee', '', '', 'George C. Bailey'),
    (4, 849, 'Eagle Harbor Township Trustee', '', '', 'Jim Boggio'),
    # page 6: Grant Twp (+ its school board) / Houghton Twp / Sherman Twp
    (5, 195, 'Grant Township Supervisor', '', 'REP', 'Ken Stigers'),
    (5, 234, 'Grant Township Clerk', '', 'DEM', 'Greg Mielcarz'),
    (5, 276, 'Grant Township Treasurer', '', '', 'Misty D. Filsinger'),
    (5, 318, 'Grant Township Trustee', '', 'DEM', 'Art Davis'),
    (5, 358, 'Grant Township Trustee', '', 'REP', 'Ned L. Huwatschek'),
    (5, 399, 'Grant Township School District School Board Member', '', '',
     'Christine Musiel'),
    (5, 440, 'Grant Township School District School Board Member', '', '',
     'Carolyn G. Stevens'),
    (5, 481, 'Grant Township School District School Board Member', '', '',
     'Patricia Walters'),
    (5, 529, 'Houghton Township Supervisor', '', 'REP', 'Mel Jones'),
    (5, 573, 'Houghton Township Supervisor', '', '', 'Jim Vivian'),
    (5, 616, 'Houghton Township Clerk', '', 'REP', 'Carol Jones'),
    (5, 655, 'Houghton Township Treasurer', '', 'REP', 'Mary K. Long'),
    (5, 697, 'Houghton Township Trustee', '', 'DEM', 'Marjie Marshall'),
    (5, 738, 'Houghton Township Trustee', '', 'DEM', 'Kathy McEvers'),
    (5, 781, 'Sherman Township Supervisor', '', 'DEM',
     'Rob Middlemis-Brown'),
    (5, 824, 'Sherman Township Clerk', '', 'DEM', 'JT Reno'),
    (5, 866, 'Sherman Township Treasurer', '', 'DEM',
     'Marilyn V. Kastelic'),
    (5, 908, 'Sherman Township Trustee', '', 'DEM', 'Deneen Connell'),
    (5, 950, 'Sherman Township Trustee', '', '', 'Frank Kastelic'),
    # page 7: Supreme Court, Court of Appeals, circuit/district judges,
    #         school boards (nonpartisan)
    (6, 106, 'Justice of Supreme Court', '', '', 'Susan L. Hubbard'),
    (6, 148, 'Justice of Supreme Court', '', '', 'Mary Kelly'),
    (6, 190, 'Justice of Supreme Court', '', '', 'Bridget Mary McCormack'),
    (6, 235, 'Justice of Supreme Court', '', '', 'Kerry Lee Morgan'),
    (6, 277, 'Justice of Supreme Court', '', '', 'Katherine Mary Nepton'),
    (6, 316, 'Justice of Supreme Court', '', '', 'Brock Swartzle'),
    (6, 358, 'Justice of Supreme Court', '', '', 'Elizabeth M. Welch'),
    (6, 400, 'Judge of Court of Appeals Incumbent', '', '',
     'Michael J. Kelly'),
    (6, 441, 'Judge of Court of Appeals Incumbent', '', '',
     'Amy Ronayne Krause'),
    (6, 495, 'Judge of Court of Appeals Non-Incumbent', '', '',
     'Michelle Rick'),
    (6, 562, 'Judge of Circuit Court', '12', '', 'Charles R. Goodman'),
    (6, 628, 'Judge of District Court', '97', '', 'Nicholas J. Daavettila'),
    (6, 685, 'Calumet-Laurium-Keweenaw Schools School Board Member', '', '',
     'Philip D. Halonen'),
    (6, 733, 'Calumet-Laurium-Keweenaw Schools School Board Member', '', '',
     'Jason Wickstrom'),
    (6, 782, 'Calumet-Laurium-Keweenaw Schools School Board Member', '', '',
     'Daniel J. Zubiena'),
    (6, 830, 'Lake Linden-Hubbell Schools School Board Member', '', '',
     'Stacey L. Sedar'),
    (6, 874, 'Lake Linden-Hubbell Schools School Board Member', '', '',
     'Write-In'),
    (6, 917, 'Lake Linden-Hubbell Schools School Board Member Partial '
     'Term', '', '', 'Rob Johnson'),
    # page 8: proposals (Yes/No pairs)
    (7, 119, 'State Proposal 20-1', '', '', 'Yes'),
    (7, 166, 'State Proposal 20-1', '', '', 'No'),
    (7, 212, 'State Proposal 20-2', '', '', 'Yes'),
    (7, 259, 'State Proposal 20-2', '', '', 'No'),
    (7, 306, 'County Police Protection Millage Proposal', '', '', 'Yes'),
    (7, 352, 'County Police Protection Millage Proposal', '', '', 'No'),
    (7, 399, 'Grant Township School Operating Millage Proposal', '', '',
     'Yes'),
    (7, 446, 'Grant Township School Operating Millage Proposal', '', '',
     'No'),
]

# Columns that carry values but are not contests: page 1's POLL BOOK
# column and page 8's PROVISIONAL BALLOTS WITHOUT ID summary columns.
KNOWN_SKIP = {0: [112], 7: [503, 577]}

# Columns whose owning township prints no row cell (withdrawn candidate
# / zero-vote write-in): emit 0 for the given township only.
NO_CELL_ZERO = {(5, 529): 'HOUGHTON', (5, 616): 'HOUGHTON',
                (6, 874): 'SHERMAN'}


def read_stack(chars):
    return ''.join(c['text'] for c in sorted(chars, key=lambda c: -c['top']))


def main():
    problems = []
    warnings = []
    columns = []       # {'page','center','office','district','party','cand'}
    town_rows = {}     # township key -> {(page, col key): votes}
    pollbook = {}
    registered = {}
    voted = {}
    county_reg = None
    county_voted = None

    with pdfplumber.open(SRC) as pdf:
        for pi, page in enumerate(pdf.pages):
            words = page.extract_words()
            tsh = next(w for w in words if w['text'] == 'TOWNSHIPS')
            by_top = defaultdict(list)
            for w in words:
                by_top.setdefault(round(w['top']), []).append(w)

            # --- party line (last line above TOWNSHIPS that is party
            # tokens; absent on the nonpartisan pages 7-8) ------------
            party_top = None
            for t in sorted(by_top):
                if t >= tsh['top'] - 1:
                    break
                toks = [w['text'] for w in sorted(by_top[t],
                                                  key=lambda w: w['x0'])]
                n = sum(tok in PARTIES or tok in SOLO
                        or tok in ('US', 'NAT', 'WORK')
                        for tok in toks)
                if n >= 3 and n * 2 >= len(toks):
                    party_top = t
            if party_top is not None:
                party_words = by_top[party_top]
                right = max(w['x1'] for w in party_words) + 30
            elif pi == 7:
                right = 490   # keep the grid; the turnout summary is x>660
            else:
                right = page.width - 10

            # --- data rows ------------------------------------------
            rows = {}   # township key -> {top: [(cx, votes)]}
            line_vals = {}   # every line's values by top, labels or not
            for t in sorted(by_top):
                if t <= tsh['top'] + 5 or t > tsh['top'] + 175:
                    continue
                row = sorted(by_top[t], key=lambda w: w['x0'])
                # township labels start at x0 20 (pages 1-5, 7-8) or 103
                # (page 6, where the poll-book column is absent)
                label_words = [w for w in row if w['x0'] < 105
                               and re.search(r'[A-Za-z]', w['text'])]
                vals = [w for w in row
                        if w not in label_words and w['x1'] < right
                        and re.fullmatch(r'[\d,]+', w['text'])]
                if not vals:
                    continue
                entries = [(round((w['x0'] + w['x1']) / 2),
                            int(w['text'].replace(',', ''))) for w in vals]
                line_vals[round(t)] = entries
                label = ' '.join(w['text'] for w in label_words)
                label = label.upper().replace(' ', '')
                if label in TOWNSHIPS:
                    rows.setdefault(label, {})[round(t)] = entries

            totals = None
            if 'TOTALS' in {w['text'].upper() for tops in by_top.values()
                            for w in tops if w['x0'] < 105
                            and tsh['top'] + 5 < w['top'] < tsh['top'] + 175}:
                # pages 1-4 label the totals row
                for t in sorted(by_top):
                    if t <= tsh['top'] + 5 or t > tsh['top'] + 175:
                        continue
                    if any(w['text'].upper() == 'TOTALS'
                           for w in by_top[t] if w['x0'] < 105):
                        totals = line_vals.get(round(t))
                        break
            if totals is None:
                # pages 5-8 print the totals row label-less: it is the
                # bottom-most line in the grid window that carries values
                if line_vals:
                    totals = line_vals[max(line_vals)]
            if totals is None:
                problems.append(f'page {pi+1}: no totals row')
                continue

            # --- columns: cluster every printed value's center x ------
            centers = sorted({cx for entries in line_vals.values()
                              for cx, _ in entries})
            cols = []
            for cx in centers:
                if cols and cx - cols[-1]['xs'][-1] <= 14:
                    cols[-1]['xs'].append(cx)
                else:
                    cols.append({'xs': [cx]})
            for c in cols:
                c['center'] = sum(c['xs']) / len(c['xs'])
            nearest = lambda cx: min(  # noqa: E731
                cols, key=lambda c: abs(c['center'] - cx))

            # --- party codes (mechanical, cross-checked vs the map) ---
            if party_top is not None:
                pw = sorted((w for w in party_words if w['x0'] > 100),
                            key=lambda w: w['x0'])
                i = 0
                while i < len(pw):
                    w = pw[i]
                    tok, x0, x1 = w['text'], w['x0'], w['x1']
                    if re.fullmatch(r'[\d,]+', tok):
                        i += 1        # stray number on the party line
                        continue
                    if i + 1 < len(pw):
                        nxt = pw[i + 1]
                        if ((tok, nxt['text']) in PAIRS
                                and nxt['x0'] - w['x1'] < 15):
                            tok, x1 = f'{tok} {nxt["text"]}', nxt['x1']
                            i += 1
                        elif (tok, nxt['text']) == ('NON', 'PARTISAN') \
                                and nxt['x0'] - w['x1'] < 15:
                            # NONPARTISAN marker over a nonpartisan column
                            i += 1
                            i += 1
                            continue
                    i += 1
                    center = (x0 + x1) / 2
                    c = nearest(center)
                    if abs(c['center'] - center) > 20:
                        warnings.append(f'page {pi+1}: party token {tok!r} '
                                        f'@{center:.0f} matches no column')
                        continue
                    if tok in PARTIES:
                        c['party'] = PARTIES[tok]
                    elif tok in SOLO:
                        c['party'] = tok

            # --- candidate name stacks (mechanical, cross-checked) ----
            # On the party-less pages (7-8) the stacks can be tall
            # (a whole name wraps to ~150pt); the titles stop above
            # t~110, well clear of the band's lower edge.  On the other
            # pages the stacks sit below the party line.
            band_top = party_top if party_top is not None \
                else tsh['top'] - 175
            stacks = defaultdict(list)
            for c in page.chars:
                if band_top < c['top'] < tsh['top'] + 10:
                    stacks[round(c['x0'])].append(c)
            xs = sorted(stacks)
            groups = []
            for x in xs:
                # every char of a vertical name shares one x0 (within
                # ~2pt); title-word characters scatter, so a 2pt gap
                # plus a >=5-char minimum separates names from titles
                if groups and x - groups[-1][-1] <= 2:
                    groups[-1].append(x)
                else:
                    groups.append([x])
            for group in groups:
                chars = [c for x in group for c in stacks[x]]
                if sum(ch.isalpha() for ch in
                       ''.join(c['text'] for c in chars)) < 5:
                    continue
                center = sum(group) / len(group) + 5.5
                c = nearest(center)
                if abs(c['center'] - center) > 20:
                    continue  # label stack (POLL BOOK TOTAL / TOTAL ...)
                name = read_stack(chars)
                if name.upper().replace(' ', '') in ('POLLBOOK', 'TOTAL'):
                    continue  # header stacks of the poll-book column
                # a wrapped name adds a second stack to the same column
                c.setdefault('names', []).append((sum(group) / len(group),
                                                  name))

            # --- office / party / candidate from the hardcoded map ----
            for c in cols:
                cx = c['center']
                hit = None
                bd = 1e9
                for entry in COLUMN_MAP:
                    if entry[0] != pi:
                        continue
                    d = abs(cx - entry[1])
                    if d < bd:
                        hit, bd = entry, d
                if hit is None or bd > 25:
                    skip = min((abs(cx - x) for x in
                                KNOWN_SKIP.get(pi, [])), default=1e9)
                    if skip <= 25:
                        continue
                    problems.append(f'page {pi+1}: column {cx:.0f} has no '
                                    f'map entry (nearest {bd:.0f})')
                    continue
                c['office'], c['district'], c['map_party'], c['cand'] = hit[2:]
                c['map_x0'] = hit[1]
                if c['map_party'] and c.get('party') \
                        and c['party'] != c['map_party']:
                    problems.append(
                        f'page {pi+1}: column {cx:.0f} ({c["office"]}) '
                        f'decoded party {c["party"]} != map '
                        f'{c["map_party"]}')
                if c['cand'] in ('Yes', 'No', 'Write-In'):
                    continue
                if 'names' in c:
                    got = ''.join(n for _, n in sorted(c['names']))
                    norm = lambda s: re.sub(r'[^A-Za-z]', '', s).lower()  # noqa: E731
                    if norm(c['cand']) not in norm(got):
                        warnings.append(
                            f'page {pi+1}: column {cx:.0f} ({c["office"]}) '
                            f'decoded name {got!r} vs map {c["cand"]!r}')
                else:
                    warnings.append(f'page {pi+1}: column {cx:.0f} '
                                    f'({c["office"]}) has no decoded name')

            # --- record votes -------------------------------------------
            for label in TOWNSHIPS:
                for t, entries in rows.get(label, {}).items():
                    for cx, v in entries:
                        c = nearest(cx)
                        if abs(c['center'] - cx) > 10:
                            problems.append(f'page {pi+1}: value {v} @'
                                            f'{cx:.0f} matches no column')
                            continue
                        key = (pi, round(c['center']))
                        town_rows.setdefault(label, {})[key] = v

            # poll book column: page 1's first column
            if pi == 0:
                for label in TOWNSHIPS:
                    if label in rows:
                        entries = rows[label][min(rows[label])]
                        if entries:
                            pollbook[label] = entries[0][1]
                if line_vals:
                    pollbook['TOTALS'] = line_vals[max(line_vals)][0][1]

            # page-8 turnout summary (right of the grid: its own labels
            # at x~669, one line below the matching grid row, followed
            # by REGISTERED, VOTED and a percent)
            if pi == 7:
                for t in sorted(by_top):
                    if t < 290:
                        continue
                    row = sorted(by_top[t], key=lambda w: w['x0'])
                    label = ' '.join(w['text'] for w in row
                                     if w['x0'] > 655 and w['x1'] < 758)
                    label = label.upper().replace(' ', '')
                    nums = [w['text'] for w in row if w['x0'] > 750
                            and re.fullmatch(r'[\d,]+', w['text'])]
                    if label in TOWNSHIPS and len(nums) >= 2:
                        registered[label] = \
                            int(nums[0].replace(',', ''))
                        voted[label] = int(nums[1].replace(',', ''))
                    elif label == 'TOTAL' and len(nums) >= 2:
                        county_reg = int(nums[0].replace(',', ''))
                        county_voted = int(nums[1].replace(',', ''))

            # --- verify this page against its TOTALS row ----------------
            for c in cols:
                if 'office' not in c:
                    continue
                want = None
                for cx, v in totals:
                    if abs(c['center'] - cx) <= 10:
                        want = v
                if want is None:
                    problems.append(f'page {pi+1}: column {c["center"]:.0f} '
                                    f'not in totals row')
                    continue
                summed = sum(town_rows.get(lbl, {}).get(
                    (pi, round(c['center'])), 0) for lbl in TOWNSHIPS)
                if summed != want:
                    problems.append(
                        f'{c["office"]} {c["cand"]}: precinct sum '
                        f'{summed} != TOTALS {want}')

            for c in cols:
                c['page'] = pi
            columns.extend(cols)

    # --- emission ---------------------------------------------------------
    print(f'{len(columns)} columns')
    for c in sorted(columns, key=lambda c: (c['page'], c['center'])):
        print(f"  p{c['page']+1} {c['center']:6.0f} "
              f"{c.get('office', '?'):47s} d={c.get('district') or '-':3s} "
              f"{(c.get('map_party') or ''):4s} {c.get('cand', '?')}")

    if registered:
        reg_sum = sum(registered.values())
        print(f'registered: {sorted(registered.items())} sum {reg_sum}')
        if county_reg is not None and reg_sum != county_reg:
            problems.append(f'registered sum {reg_sum} != printed county '
                            f'{county_reg}')
        if county_voted is not None and pollbook.get('TOTALS') is not None \
                and county_voted != pollbook['TOTALS']:
            problems.append(f'page-8 TOTAL VOTED {county_voted} != poll '
                            f'book {pollbook["TOTALS"]}')
    else:
        problems.append('page-8 turnout summary not decoded')
    if pollbook:
        pb_sum = sum(v for k, v in pollbook.items() if k != 'TOTALS')
        print(f'pollbook: {sorted(pollbook.items())} township sum {pb_sum} '
              f'printed {pollbook.get("TOTALS")}')
        if 'TOTALS' in pollbook and pb_sum != pollbook['TOTALS']:
            problems.append(f'pollbook township sum {pb_sum} != printed '
                            f'{pollbook["TOTALS"]}')
        for label in TOWNSHIPS:
            if label in voted and label in pollbook \
                    and voted[label] != pollbook[label]:
                problems.append(f'{label}: page-8 VOTED {voted[label]} != '
                                f'poll book {pollbook[label]}')
    else:
        problems.append('page-1 poll-book column not decoded')

    # district-restricted contests: a township that printed no cell for
    # a column did not vote in that contest — emit only the townships
    # that have a cell (or the NO_CELL_ZERO owners, whose totals row
    # prints a 0 but whose row prints nothing).
    out_rows = []
    for label, precinct in TOWNSHIPS.items():
        if label in registered:
            out_rows.append([COUNTY, precinct, 'Registered Voters', '', '',
                             '', registered[label]])
        pb = pollbook.get(label)
        if pb is not None:
            out_rows.append([COUNTY, precinct, 'Ballots Cast', '', '',
                             '', pb])
        for c in sorted(columns, key=lambda c: (c['page'], c['center'])):
            if 'office' not in c:
                continue
            key = (c['page'], round(c['center']))
            if key in town_rows.get(label, {}):
                v = town_rows[label][key]
            elif NO_CELL_ZERO.get((c['page'], c['map_x0'])) == label:
                v = 0
            else:
                continue
            out_rows.append([COUNTY, precinct, c['office'], c['district'],
                             c['map_party'] or '', c['cand'], v])

    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(out_rows)
    print(f'emitted {len(out_rows)} rows -> {OUT}')

    for wn in warnings:
        print('warning:', wn)
    if problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)


if __name__ == '__main__':
    main()