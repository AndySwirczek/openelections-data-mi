#!/usr/bin/env python3
"""Parse Oscoda County's Mar 10 2020 presidential primary from
'Oscoda MI 2020-March-Official-Election.pdf' (19pp scan: a Department of
State countywide vote-total form, a certification page, then a 13-page
ES&S 'Statement of Votes Cast' with 7 precincts).

The PDF's embedded text layer is unreliable vendor OCR (dropped digits,
'6B' for 68, 'O'/'U' for 0), so the pages were OCR'd with PaddleOCR-VL
(src/fetch_paddleocr_md.py) and the cached per-page markdown tables are
parsed here (same pattern as the Manistee 2026 parser).  Spot checks
against the rendered page images (/tmp/oscoda_png) confirmed the OCR.

Layout (cache page numbers are 1-based PDF pages):
  p1    cover form; p2-3 countywide state form (DEM+REP candidates);
  p4-5  the same form for the 1st Congressional District; p6 certification.
  p7    turnout: Registered Voters / Cards Cast / Voters Cast / % Turnout.
  p8    DEM aux (Times Cast, Registered Voters) + Bennet, Biden.
  p9    DEM Bloomberg..Klobuchar; p10 Sanders..Uncommitted.
  p11   DEM Total Votes + Unresolved Write-In.
  p12   REP aux + Sanford, Trump; p13 Walsh, Weld, Uncommitted, TV, WI.
  p14   Mio AuSable Schools proposal: aux + Yes/No/Total Votes (5 precincts).
  p15   that proposal's Unresolved Write-In page (all zero).
  p16   C.O.O.R. ISD Proposal 1: aux + Yes/No/TV (all 7 precincts).
  p17   its write-in page (all zero).
  p18   C.O.O.R. ISD Proposal 2: aux + Yes/No/TV; p19 its write-in page.

Candidate tables print value + percentage pairs (pct of Times Cast, not
of Total Votes); Times Cast can exceed Total Votes when a card cast no
presidential vote.  As everywhere in the March 2020 set, 'Unresolved
Write-In' sits OUTSIDE Total Votes (DEM countywide: candidates sum
exactly to TV 744 with 1 unresolved write-in), so write-ins are not
emitted and Ballots Cast = Total Votes.

Known source quirk: none -- Sanford's precinct rows (1,0,1,1,0,0,0) sum
to 3, matching both the report's county row and the certified state
form, and REP Total Votes rows sum to the county row's 1,031.

Verification: per precinct, candidates sum to Total Votes (DEM and REP);
Yes + No == Total Votes for every proposal; every column's precinct sum
== the printed county row; countywide candidates == the state form's
word-number amounts ('received FOUR HUNDRED FORTY SIX votes'); DEM TV
744 + REP TV 1,031 == the state form's 1,775; turnout RV sums to 6,121
and Cards Cast == Voters Cast (1,837 poll-book total).

Usage: .venv/bin/python src/oscoda_president_primary_2020.py
"""

import csv
import html
import os
import re
import sys

CACHE = '/tmp/paddleocr_md/Oscoda_MI_2020_March_Official_Election'
OUT = '2020/counties/20200310__mi__primary__president__oscoda__precinct.csv'
COUNTY = 'Oscoda'

PRECINCTS = [
    'Big Creek Township, Precinct 1', 'Big Creek Township, Precinct 2',
    'Clinton Township, Precinct 1', 'Comins Township, Precinct 1',
    'Elmer Township, Precinct 1', 'Greenwood Township, Precinct 1',
    'Mentor Township, Precinct 1',
]

# candidate order as printed across the DEM/REP tables (ballot order)
DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

NUM_RE = re.compile(r'^\d[\d,]*$')


def parse_tables(txt):
    """All <table>s on a cached markdown page -> rows of cell strings."""
    tables = []
    for tm in re.findall(r'<table[^>]*>(.*?)</table>', txt, re.S):
        rows = []
        for rm in re.findall(r'<tr>(.*?)</tr>', tm, re.S):
            cells = [html.unescape(re.sub(r'<[^>]*>', '', c)).strip()
                     for c in re.findall(r'<td[^>]*>(.*?)</td>', rm, re.S)]
            rows.append(cells)
        tables.append(rows)
    return tables


def read_rows(rows, problems, ctx):
    """Precinct rows of one table: {name: [int values]} + the county row.

    Cells carry value/percentage pairs; percentages (trailing '%') and
    empty padding cells are dropped, leaving one int per data column.
    """
    out = {}
    county = None
    for cells in rows[1:]:      # rows[0] is the table's header
        label = re.sub(r'\s+', ' ', cells[0]).strip()
        # OCR occasionally fuses 'Precinct 1' -> 'Precinct1'
        label = re.sub(r'Precinct(\d)', r'Precinct \1', label)
        vals = [c.replace(',', '') for c in cells[1:]
                if c and not c.endswith('%')]
        if label in ('County', 'Oscoda County, Michigan', 'Cumulative'):
            continue
        if label == 'Oscoda County, Michigan - Total':
            county = [int(v) for v in vals]
            continue
        if label in ('Cumulative - Total', 'County - Total'):
            continue    # repeats of the county row (and zero rows)
        if label not in PRECINCTS:
            problems.append(f'{ctx}: unknown row label {label!r}')
            continue
        if label in out:
            problems.append(f'{ctx}: duplicate row {label!r}')
            continue
        try:
            out[label] = [int(v) for v in vals]
        except ValueError:
            problems.append(f'{ctx}: non-numeric row {cells!r}')
    return out, county


def unfuse_header(tbl, expected_cands, problems, ctx):
    """PaddleOCR sometimes fuses a table's header row into one cell
    ('Precinct Mark Sanford Donald J. Trump'); split it back apart."""
    first = tbl[0]
    if len(first) != 1 or not first[0].startswith('Precinct'):
        return tbl
    rest = first[0][len('Precinct'):].strip()
    names = []
    for c in expected_cands:
        if rest.startswith(c):
            names.append(c)
            rest = rest[len(c):].strip()
    if rest or not names:
        problems.append(f'{ctx}: unfusable header {first!r}')
        return tbl
    return [['Precinct'] + names] + tbl[1:]


def table_kind(header):
    joined = ' '.join(header)
    if '% Turnout' in joined:
        return 'turnout'
    if 'Times Cast' in joined or 'Times' in header[1:2]:
        return 'aux'
    if 'Total Votes' in header and 'Unresolved' in joined:
        return 'tvwi'
    if 'Unresolved' in joined:
        return 'wi'
    return 'cand'


def main():
    problems = []

    def page(n):
        with open(os.path.join(CACHE, f'p{n:03d}.md')) as fh:
            return fh.read()

    # ---- turnout (p7) ---------------------------------------------------
    tables = parse_tables(page(7))
    if len(tables) != 1:
        problems.append(f'p7: {len(tables)} tables')
    turnout, tv_county = read_rows(tables[0], problems, 'p7')
    if set(turnout) != set(PRECINCTS) or len(turnout) != 7:
        problems.append(f'p7: {len(turnout)} precinct rows')
    for n, v in turnout.items():
        if len(v) != 3:
            problems.append(f'p7 {n!r}: {len(v)} values')
            continue
        rv, cards, voters = v
        if cards != voters:
            problems.append(f'p7 {n!r}: Cards Cast {cards} != Voters Cast '
                            f'{voters}')
        pct = round(voters / rv * 100, 2) if rv else 0.0
        printed = next(float(c[:-1]) for c in tables[0][
            [re.sub(r'\s+', ' ', r[0]) for r in tables[0]].index(n)][1:]
            if c.endswith('%'))
        if abs(pct - printed) > 0.005:
            problems.append(f'p7 {n!r}: turnout {pct}% vs printed '
                            f'{printed}%')
    if tv_county != [sum(turnout[n][i] for n in PRECINCTS)
                     for i in range(3)]:
        problems.append(f'p7: precinct sums != county row {tv_county}')
    if tv_county[0] != 6121 or tv_county[1] != 1837:
        problems.append(f'p7: county row {tv_county} != printed 6,121 / '
                        f'1,837')

    # ---- DEM / REP contests (p8-13) -------------------------------------
    cand_vals = {'DEM': {}, 'REP': {}}      # candidate -> {precinct: votes}
    cand_county = {'DEM': {}, 'REP': {}}    # candidate -> printed county row
    tcast = {'DEM': {}, 'REP': {}}
    tv = {'DEM': {}, 'REP': {}}
    wi = {'DEM': {}, 'REP': {}}
    county_tv = {}
    county_wi = {}
    county_tc = {}

    PAGE_SECT = {8: 'DEM', 9: 'DEM', 10: 'DEM', 11: 'DEM',
                 12: 'REP', 13: 'REP'}
    seen_cands = []
    for pg in range(8, 14):
        sect = PAGE_SECT[pg]
        for tbl in parse_tables(page(pg)):
            tbl = unfuse_header(tbl, DEM + REP, problems, f'p{pg}')
            header = [c for c in tbl[0] if c]
            kind = table_kind(header)
            rows, county = read_rows(tbl, problems, f'p{pg}')
            if kind == 'aux':
                for n, v in rows.items():
                    if n in tcast[sect] and tcast[sect][n] != v[:1]:
                        problems.append(f'p{pg}: TC disagrees for {n!r}')
                    tcast[sect][n] = v[:1]
                if county:
                    county_tc.setdefault(sect, []).append(county[0])
            elif kind == 'cand':
                names = header[1:]
                seen_cands += names
                for n, v in rows.items():
                    if len(v) != len(names):
                        problems.append(f'p{pg} {n!r}: {len(v)} values for '
                                        f'{len(names)} candidates')
                        continue
                    for c, val in zip(names, v):
                        cand_vals[sect].setdefault(c, {})[n] = val
                if county:
                    for c, val in zip(names, county):
                        cand_county[sect].setdefault(c, []).append(val)
            elif kind == 'tvwi':
                # p13 prints Walsh/Weld/Uncommitted AND Total Votes +
                # Unresolved Write-In in one table; p11 is TV/WI only
                names = header[1:-2]
                if set(names) - set(DEM + REP):
                    problems.append(f'p{pg}: unexpected tvwi cols {names}')
                    continue
                seen_cands += names
                for n, v in rows.items():
                    if len(v) != len(names) + 2:
                        problems.append(f'p{pg} {n!r}: {len(v)} values for '
                                        f'{len(names)} cands + TV + WI')
                        continue
                    for c, val in zip(names, v):
                        cand_vals[sect].setdefault(c, {})[n] = val
                    tv[sect][n] = v[-2]
                    wi[sect][n] = v[-1]
                if county:
                    for c, val in zip(names, county[:-2]):
                        cand_county[sect].setdefault(c, []).append(val)
                    county_tv[sect] = county[-2]
                    county_wi[sect] = county[-1]
            else:
                problems.append(f'p{pg}: unexpected table {header}')

    if seen_cands != DEM + REP:
        problems.append(f'candidate columns {seen_cands} != expected '
                        f'DEM+REP order')

    # ---- proposals (p14-19) ---------------------------------------------
    prop_titles = []
    prop_vals = {}      # title -> {precinct: (yes, no, tv)}
    prop_county = {}
    prop_tc = {}
    PROP_PAGE = {14: 'Mio Ausable Schools Proposal',
                 16: 'C.O.O.R. ISD Proposal 1',
                 18: 'C.O.O.R. ISD Proposal 2'}
    for pg, title in PROP_PAGE.items():
        ptext = page(pg)
        plain = re.sub(r'<[^>]*>', ' ', ptext)
        plain = re.sub(r'\s+', ' ', plain)
        m = re.search(r'((?:Mio Au?sable Schools Proposal|'
                      r'C\.O\.O\.R\. ISD Proposal \d)\s+for\s+.*?'
                      r'\(Vote for \d+\))', plain)
        if m and re.sub(r'\s+for\s+.*$', '', m.group(1)) != title:
            problems.append(f'p{pg}: proposal title {m.group(1)!r} != '
                            f'{title!r}')
        # titles are missing from the OCR entirely on p16/p18 (verified
        # against the rendered page images); flag only real mismatches
        prop_titles.append(title)
        for tbl in parse_tables(ptext):
            header = [c for c in tbl[0] if c]
            kind = table_kind(header)
            rows, county = read_rows(tbl, problems, f'p{pg}')
            if kind == 'aux':
                for n, v in rows.items():
                    prop_tc.setdefault(title, {})[n] = v
            elif kind == 'cand':
                names = header[1:]
                if names != ['Yes', 'No', 'Total Votes']:
                    problems.append(f'p{pg}: proposal cols {names}')
                    continue
                for n, v in rows.items():
                    if len(v) != 3:
                        problems.append(f'p{pg} {n!r}: {len(v)} values')
                        continue
                    prop_vals.setdefault(title, {})[n] = tuple(v)
                if county:
                    prop_county[title] = county
            elif kind != 'wi':
                problems.append(f'p{pg}: unexpected table {header}')

    # write-in-only pages (p15/p17/p19): all zeros, verification only
    for pg in (15, 17, 19):
        for tbl in parse_tables(page(pg)):
            rows, county = read_rows(tbl, problems, f'p{pg}')
            for n, v in rows.items():
                if any(v):
                    problems.append(f'p{pg} {n!r}: write-ins {v} not zero')
            if county and any(county):
                problems.append(f'p{pg}: county write-ins {county}')

    # ---- verification ---------------------------------------------------
    # state form word-numbers (p2-3): countywide candidates.  Rows may be
    # markdown tables or plain lines, and the digit column is shifted by
    # the OCR, so the printed ENGLISH amount is the value of record.
    state = {}
    for pg in (2, 3):
        plain = re.sub(r'<[^>]*>', ' ', page(pg))
        for m in re.finditer(r'\b(DEM|REP)\s+(.+?)\s+received\s+'
                             r'([A-Za-z ]+?)\s+votes', plain):
            party, name, words = m.groups()
            try:
                state[(party, ' '.join(name.split()))] = wordnum(
                    words.split())
            except (KeyError, ValueError):
                problems.append(f'p{pg}: unreadable amount {m.group(0)!r}')
    total_m = re.search(r'was\s+([A-Za-z ]+?)\s+votes', plain)
    if not total_m or wordnum(total_m.group(1).split()) != 1775:
        problems.append('state form grand total not 1,775')
    EXPECT_STATE = {('DEM', n): 0 for n in
                    ('Bennet, Michael', 'Booker, Cory', 'Castro, Julian',
                     'Delaney, John', 'Sestak, Joe', 'Williamson, Marianne',
                     'Yang, Andrew')}
    for key in EXPECT_STATE:
        state.setdefault(key, 0)
    CANON = {('DEM', 'Bennet, Michael'): 'Michael Bennet',
             ('DEM', 'Biden, Joe'): 'Joe Biden',
             ('DEM', 'Bloomberg, Michael R.'): 'Michael R. Bloomberg',
             ('DEM', 'Booker, Cory'): 'Cory Booker',
             ('DEM', 'Buttigieg, Pete'): 'Pete Buttigieg',
             ('DEM', 'Castro, Julian'): 'Julian Castro',
             ('DEM', 'Delaney, John'): 'John Delaney',
             ('DEM', 'Gabbard, Tulsi'): 'Tulsi Gabbard',
             ('DEM', 'Klobuchar, Amy'): 'Amy Klobuchar',
             ('DEM', 'Sanders, Bernie'): 'Bernie Sanders',
             ('DEM', 'Sestak, Joe'): 'Joe Sestak',
             ('DEM', 'Steyer, Tom'): 'Tom Steyer',
             ('DEM', 'Warren, Elizabeth'): 'Elizabeth Warren',
             ('DEM', 'Williamson, Marianne'): 'Marianne Williamson',
             ('DEM', 'Yang, Andrew'): 'Andrew Yang',
             ('DEM', 'Uncommitted'): 'Uncommitted',
             ('REP', 'Sanford, Mark'): 'Mark Sanford',
             ('REP', 'Trump, Donald J.'): 'Donald J. Trump',
             ('REP', 'Walsh, Joe'): 'Joe Walsh',
             ('REP', 'Weld, Bill'): 'Bill Weld',
             ('REP', 'Uncommitted'): 'Uncommitted'}
    if set(state) != set(CANON):
        problems.append(f'state form rows {sorted(set(state) ^ set(CANON))} '
                        f'differ from expected candidates')

    for sect, cands in (('DEM', DEM), ('REP', REP)):
        for c in cands:
            by = cand_vals[sect].get(c, {})
            if set(by) != set(PRECINCTS):
                problems.append(f'{sect} {c}: {len(by)} precinct rows')
                continue
            col = [by[n] for n in PRECINCTS]
            printed = cand_county[sect].get(c, [])
            if len(printed) == 1 and printed[0] != sum(col):
                problems.append(f'{sect} {c}: precinct sum {sum(col)} '
                                f'!= county row {printed}')
            form_name = next(k[1] for k, vv in CANON.items()
                             if vv == c and k[0] == sect)
            want = state.get((sect, form_name))
            if want is not None and want != sum(col):
                problems.append(f'{sect} {c}: precinct sum {sum(col)} != '
                                f'state form {want}')
        # per-precinct: candidates sum to Total Votes
        for n in PRECINCTS:
            s = sum(cand_vals[sect][c][n] for c in cands)
            if s != tv[sect][n]:
                problems.append(f'{sect} {n!r}: candidates {s} != TV '
                                f'{tv[sect][n]}')
            if n in tcast[sect] and tv[sect][n] > tcast[sect][n][0]:
                problems.append(f'{sect} {n!r}: TV {tv[sect][n]} > Times '
                                f'Cast {tcast[sect][n][0]}')
        sum_tv = sum(tv[sect].values())
        if county_tv.get(sect) not in (None, sum_tv):
            problems.append(f'{sect}: TV sum {sum_tv} != county row '
                            f'{county_tv[sect]}')
        if county_wi.get(sect) not in (None, sum(wi[sect].values())):
            problems.append(f'{sect}: WI sum {sum(wi[sect].values())} != '
                            f'county row {county_wi[sect]}')
        if len(tv[sect]) != 7:
            problems.append(f'{sect}: {len(tv[sect])} TV rows')
        tc_sum = sum(tcast[sect][n][0] for n in PRECINCTS
                     if n in tcast[sect])
        if county_tc.get(sect) and county_tc[sect][0] != tc_sum:
            problems.append(f'{sect}: Times Cast sum {tc_sum} != county '
                            f'row {county_tc[sect]}')
    print(f"unresolved write-ins (not emitted): DEM "
          f"{sum(wi['DEM'].values())}, REP {sum(wi['REP'].values())}")

    for title in prop_titles:
        by = prop_vals.get(title, {})
        if not by:
            problems.append(f'{title}: no rows')
            continue
        for n, (yes, no, tot) in by.items():
            if yes + no != tot:
                problems.append(f'{title} {n!r}: Yes+No {yes + no} != TV '
                                f'{tot}')
            if title in prop_tc and n in prop_tc[title] \
                    and tot > prop_tc[title][n][0]:
                problems.append(f'{title} {n!r}: TV > Times Cast')
            if title in prop_tc and n in prop_tc[title] \
                    and n not in turnout:
                problems.append(f'{title}: unknown precinct {n!r}')
        cols = [sum(v[i] for v in by.values()) for i in range(3)]
        if title in prop_county and prop_county[title] != cols:
            problems.append(f'{title}: sums {cols} != county row '
                            f'{prop_county[title]}')
        if set(by) - set(PRECINCTS):
            problems.append(f'{title}: unknown precincts {set(by) - set(PRECINCTS)}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)

    # ---- emission -------------------------------------------------------
    rows_out = []
    for n in PRECINCTS:
        rows_out.append([COUNTY, n, 'Registered Voters', '', '', '',
                         turnout[n][0]])
    for c in DEM:
        for n in PRECINCTS:
            rows_out.append([COUNTY, n, 'President', '', 'DEM', c,
                             cand_vals['DEM'][c][n]])
    for n in PRECINCTS:
        rows_out.append([COUNTY, n, 'Ballots Cast', '', 'DEM', '',
                         tv['DEM'][n]])
    for c in REP:
        for n in PRECINCTS:
            rows_out.append([COUNTY, n, 'President', '', 'REP', c,
                             cand_vals['REP'][c][n]])
    for n in PRECINCTS:
        rows_out.append([COUNTY, n, 'Ballots Cast', '', 'REP', '',
                         tv['REP'][n]])
    for title in prop_titles:
        for n, (yes, no, _tot) in prop_vals[title].items():
            for cand, val in (('Yes', yes), ('No', no)):
                rows_out.append([COUNTY, n, title, '', '', cand, val])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(PRECINCTS)} precincts, '
          f'proposals: {prop_titles}')


# English number words as printed by the state form
WORDS = {'ZERO': 0, 'ONE': 1, 'TWO': 2, 'THREE': 3, 'FOUR': 4, 'FIVE': 5,
         'SIX': 6, 'SEVEN': 7, 'EIGHT': 8, 'NINE': 9, 'TEN': 10,
         'ELEVEN': 11, 'TWELVE': 12, 'THIRTEEN': 13, 'FOURTEEN': 14,
         'FIFTEEN': 15, 'SIXTEEN': 16, 'SEVENTEEN': 17, 'EIGHTEEN': 18,
         'NINETEEN': 19, 'TWENTY': 20, 'THIRTY': 30, 'FORTY': 40,
         'FIFTY': 50, 'SIXTY': 60, 'SEVENTY': 70, 'EIGHTY': 80,
         'NINETY': 90, 'HUNDRED': 100}


def wordnum(tokens):
    """'ONE THOUSAND SEVEN HUNDRED SEVENTY FIVE' -> 1775."""
    total = cur = 0
    for t in tokens:
        t = t.upper()
        if t == 'THOUSAND':
            total += (cur or 1) * 1000
            cur = 0
        elif t == 'HUNDRED':
            cur = (cur or 1) * 100
        else:
            cur += WORDS[t]
    return total + cur


if __name__ == '__main__':
    main()