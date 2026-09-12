#!/usr/bin/env python3
"""Parse Livingston County's Mar 2020 presidential primary from
'Livingston MI Statement-of-Votes-3-10-2020.pdf' (17pp Dominion
'Statement of Votes Cast' official election-night report, 87
counting groups, text-extractable).

Same report family as Montcalm's March 2020 canvass, with these
differences:
  - Column blocks carry BOTH absentee and Election Day ballots-cast
    columns: DEM main 13 candidates; DEM continuation 10 values
    (Williamson, Yang, Uncommitted, Votes Cast, Undervotes, Overvotes,
    Absentee BC, Election Day BC, Total BC, Registered Voters); REP
    12 values (5 candidates + the same 7 summary columns); proposals
    9 values (Yes, No, VC, UV, OV, AV BC, ED BC, TBC, RV).  Column
    order verified from the rotated header word x-positions.
  - 87 rows per party section: some precincts are split counting
    groups printed as separate rows with 3-letter suffixes --
    'Iosco Township, Precinct 1 - SBC' and 'Unadilla Township,
    Precinct 1 - SBC' appear in ALL sections; the proposals also
    print 'Conway/Handy ... - WEB' and 'Deerfield ... - LIN' rows.
    Names are emitted verbatim.
  - Proposals: 'Mott Community College Bond Proposition' (Deerfield
    P1/P2 - LIN + Tyrone P1-3) and 'Ingham Intermediate School
    District Special Education Millage Proposal' (Conway P1 - WEB,
    Handy P1/P3 - WEB, Iosco/Unadilla P1 - SBC).  Titles end
    '- Nonpartisan'.

Precinct names wrap across 3 physical lines around the value row
(head at top t, values at t+5.3, tail at t+10.7), so fragments are
assigned to the nearest value row ON THE PAGE by line top (not to
the following row) -- the tail completes its own row's name.  The
rotated candidate headers extract REVERSED; candidates hardcoded.

Verification: per precinct, candidates sum to Votes Cast; VC +
undervotes + overvotes == Total Ballots Cast; Absentee BC + Election
Day BC == Total BC; every column's precinct sums == the printed
countywide Totals rows (DEM main/cont, REP, both proposals); RV sums
to the printed 152,432 and matches across sections; names unique and
identical across all sections.
"""

import csv
import os
import re
import sys

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Livingston MI Statement-of-Votes-3-10-2020.pdf')
OUT = ('2020/counties/20200310__mi__primary__president__'
       'livingston__precinct.csv')
COUNTY = 'Livingston'

DEM_MAIN = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
            'Cory Booker', 'Pete Buttigieg', 'Julian Castro',
            'John Delaney', 'Tulsi Gabbard', 'Amy Klobuchar',
            'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
            'Elizabeth Warren']
DEM_CONT = ['Marianne Williamson', 'Andrew Yang', 'Uncommitted']
DEM_ALL = DEM_MAIN + DEM_CONT
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']
PROPOSALS = ['Mott Community College Bond Proposition',
             'Ingham Intermediate School District Special Education '
             'Millage Proposal']

INT = r'\d[\d,]*'
TURNOUT_RE = re.compile(r'\s+[\d.]+\s*%$')

# rotated-header vocabulary: extracted tokens are each word reversed
# (or, for the upright 'Precinct' label, the word itself)
HEADER_WORDS = {w.lower() for w in (
    'Precinct Michael Bennet Joe Biden Michael R. Bloomberg Cory Booker '
    'Pete Buttigieg Julián Castro John Delaney Tulsi Gabbard Amy '
    'Klobuchar Bernie Sanders Joe Sestak Tom Steyer Elizabeth Warren '
    'Marianne Williamson Andrew Yang Uncommitted Votes Cast Undervotes '
    'Overvotes Ballots Voting Day Election Total Totals Voters Registered '
    'Absentee Percentage Turnout Mark Sanford Donald J. Trump Joe Walsh '
    'Bill Weld Yes No Nonpartisan Proposition').split()}


def ints(s):
    return [int(x.replace(',', '')) for x in s.split()]


def main():
    pdf = pdfplumber.open(SRC)
    problems = []

    rows = {}       # sect -> width -> [(name, values)] in print order
    totals = {}     # sect -> width -> values
    sect = None
    block = '?'     # demmain | demcont | rep | prop

    for page in pdf.pages:
        ptext = page.extract_text() or ''
        if 'nosmailliW' in ptext:
            block = 'demcont'
        elif 'tenneB' in ptext:
            block = 'demmain'
        elif 'drofnaS' in ptext:
            block = 'rep'
        elif 'seY' in ptext:
            block = 'prop'
        else:
            block = '?'
        data = []       # (top, sect, width, namepart, vals)
        frags = []      # (top, text, sect)
        for l in page.extract_text_lines():
            line = l['text'].strip()
            top = l['top']
            if not line:
                continue
            if line.startswith(('Statement of Votes Cast',
                                'Livingston County', 'Registered Voters',
                                'Election Night Reports', '46597 of',
                                'Run Time', 'Run Date')):
                continue
            m = re.match(r'^(.*) - (Democratic|Republican) Party - '
                         r'Vote for not more than 1$', line)
            if m:
                sect = (m.group(1).strip(),
                        'DEM' if 'Democratic' in line else 'REP')
                continue
            m = re.match(r'^(.*) - Nonpartisan$', line)
            if m:
                sect = (m.group(1).strip(), '')
                continue
            core = TURNOUT_RE.sub('', line)
            toks = core.split()
            if not toks:
                continue
            i = len(toks)
            while i > 0 and re.fullmatch(INT, toks[i - 1]):
                i -= 1
            run = len(toks) - i
            if toks[0] == 'Totals':
                if sect is None or run == 0:
                    problems.append(f'stray Totals row: {line!r}')
                    continue
                totals.setdefault(sect, {})[run] = ints(' '.join(toks[1:]))
                continue
            if all(t.lower() in HEADER_WORDS
                   or t[::-1].lower() in HEADER_WORDS for t in toks):
                continue
            width = {('President of the United States', 'DEM'): 13,
                     ('President of the United States', 'REP'): 12,
                     }.get(sect)
            if sect and sect[1] == 'DEM' and block == 'demcont':
                width = 10
            if sect and sect[1] == '':
                width = 9
            if width is None:
                if run:
                    problems.append(f'unmatched line: {line!r}')
                continue
            if run >= width:
                if run > width + 1:
                    problems.append(f'{sect} {block}: unexpected run '
                                    f'{run} (width {width}): {line!r}')
                data.append((top, sect, width,
                             ' '.join(toks[:-width]).strip(),
                             ints(' '.join(toks[-width:]))))
            else:
                frags.append((top, line, sect))

        # wrapped names: assign each fragment to the nearest value row
        # ON THIS PAGE (its own row's value line sits ~5pt away; the
        # next row's head is ~16pt away).  A fragment ABOVE its value
        # row is the name's head; one BELOW is its tail.
        frag_map = {}   # (sect, width, top) -> [(is_head, top, text)]
        for ftop, ftext, fsect in frags:
            cands = [(abs(ftop - dtop), dtop)
                     for dtop, dsect, dwidth, _np, _v in data
                     if dsect == fsect]
            if not cands:
                problems.append(f'unassignable fragment {ftext!r}')
                continue
            dist, dtop = min(cands)
            dwidth = next(w_ for t_, s_, w_, _np, _v in data
                          if t_ == dtop and s_ == fsect)
            frag_map.setdefault((fsect, dwidth, dtop), []).append(
                (ftop < dtop, ftop, ftext))

        for dtop, dsect, dwidth, namepart, vals in data:
            heads, tails = [], []
            for is_head, ftop, ftext in frag_map.pop((dsect, dwidth,
                                                      dtop), []):
                (heads if is_head else tails).append((ftop, ftext))
            heads.sort()
            tails.sort()
            name = ' '.join([t for _, t in heads] + [namepart]
                            + [t for _, t in tails]).strip()
            name = ' '.join(name.split())   # PDF lines carry doubled spaces
            rows.setdefault(dsect, {}).setdefault(dwidth, []) \
                .append((name, vals))

    # ---- structure ------------------------------------------------------
    dem_main = rows.get(('President of the United States', 'DEM'), {}) \
        .get(13, [])
    dem_cont = rows.get(('President of the United States', 'DEM'), {}) \
        .get(10, [])
    rep_rows = rows.get(('President of the United States', 'REP'), {}) \
        .get(12, [])
    names_m = [n for n, _ in dem_main]
    names_c = [n for n, _ in dem_cont]
    names_r = [n for n, _ in rep_rows]
    if len(names_m) != 87:
        problems.append(f'{len(names_m)} DEM main rows, expected 87')
    if len(names_c) != 87:
        problems.append(f'{len(names_c)} DEM cont rows, expected 87')
    if len(names_r) != 87:
        problems.append(f'{len(names_r)} REP rows, expected 87')
    if names_m != names_r:
        problems.append('DEM main names != REP names: '
                        f'{set(names_m) ^ set(names_r)}')
    if names_c != names_r:
        problems.append('DEM cont names != REP names')
    if len(set(names_r)) != len(names_r):
        dupes = sorted(n for n in set(names_r) if names_r.count(n) > 1)
        problems.append(f'duplicate REP names: {dupes}')
    for n in names_r:
        if not n or not re.search(r'(Precinct|Ward|District)', n):
            problems.append(f'suspicious precinct name {n!r}')

    # ---- per-precinct checks -------------------------------------------
    main_by = {n: v for n, v in dem_main}
    cont_by = {n: v for n, v in dem_cont}
    rep_by = {n: v for n, v in rep_rows}
    for n in names_r:
        mvals, cvals, rvals = main_by[n], cont_by[n], rep_by[n]
        cand_sum = sum(mvals) + cvals[0] + cvals[1] + cvals[2]
        if cand_sum != cvals[3]:
            problems.append(f'DEM {n!r}: candidates {cand_sum} != '
                            f'Votes Cast {cvals[3]}')
        if cvals[3] + cvals[4] + cvals[5] != cvals[8] \
                or cvals[6] + cvals[7] != cvals[8]:
            vcuvov = cvals[3] + cvals[4] + cvals[5]
            aved = cvals[6] + cvals[7]
            problems.append(f'DEM {n!r}: VC+UV+OV {vcuvov} or AV+ED '
                            f'{aved} != TBC {cvals[8]}')
        if sum(rvals[:5]) != rvals[5]:
            problems.append(f'REP {n!r}: candidates {sum(rvals[:5])} '
                            f'!= Votes Cast {rvals[5]}')
        if rvals[5] + rvals[6] + rvals[7] != rvals[10] \
                or rvals[8] + rvals[9] != rvals[10]:
            problems.append(f'REP {n!r}: summary columns != TBC '
                            f'{rvals[10]}')
        if rvals[11] != cvals[9]:
            problems.append(f'{n!r}: REP RV {rvals[11]} != DEM RV '
                            f'{cvals[9]}')

    # ---- proposals ------------------------------------------------------
    props = {}
    for title in PROPOSALS:
        widths = rows.get((title, ''), {})
        if 9 not in widths:
            problems.append(f'{title}: no rows')
            continue
        props[title] = {n: v for n, v in widths[9]}
        for n, v in widths[9]:
            if v[0] + v[1] != v[2]:
                problems.append(f'{title} {n!r}: Yes+No {v[0] + v[1]} '
                                f'!= Votes Cast {v[2]}')
            if v[2] + v[3] + v[4] != v[7] or v[5] + v[6] != v[7]:
                problems.append(f'{title} {n!r}: summary != TBC {v[7]}')
            if n not in rep_by:
                problems.append(f'{title}: precinct {n!r} not in the '
                                f'REP table')
            elif v[8] != rep_by[n][11]:
                problems.append(f'{title} {n!r}: RV {v[8]} != REP RV '
                                f'{rep_by[n][11]}')

    # ---- county Totals rows --------------------------------------------
    want_m = totals.get(('President of the United States', 'DEM'), {}) \
        .get(13)
    want_c = totals.get(('President of the United States', 'DEM'), {}) \
        .get(10)
    want_r = totals.get(('President of the United States', 'REP'), {}) \
        .get(12)
    if want_m is None or want_c is None or want_r is None:
        problems.append('missing a county Totals row')
    else:
        cols = [sum(main_by[n][i] for n in names_r) for i in range(13)]
        if cols != want_m:
            problems.append(f'DEM main sums {cols} != Totals {want_m}')
        cols = [sum(cont_by[n][i] for n in names_r) for i in range(10)]
        if cols != want_c:
            problems.append(f'DEM cont sums {cols} != Totals {want_c}')
        cols = [sum(rep_by[n][i] for n in names_r) for i in range(12)]
        if cols != want_r:
            problems.append(f'REP sums {cols} != Totals {want_r}')
    for title, by in props.items():
        want = totals.get((title, ''), {}).get(9)
        if want is None:
            problems.append(f'{title}: no county Totals row')
            continue
        cols = [sum(v[i] for v in by.values()) for i in range(9)]
        if cols != want:
            problems.append(f'{title}: sums {cols} != Totals {want}')
    if len(names_r) == 87:
        rv_sum = sum(rep_by[n][11] for n in names_r)
        if rv_sum != 152432:
            problems.append(f'RV sum {rv_sum} != 152432')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)

    # ---- emission -------------------------------------------------------
    rows_out = []
    for n in names_r:
        rows_out.append([COUNTY, n, 'Registered Voters', '', '', '',
                         rep_by[n][11]])
    for i, cand in enumerate(DEM_MAIN):
        for n in names_r:
            rows_out.append([COUNTY, n, 'President', '', 'DEM', cand,
                             main_by[n][i]])
    for i, cand in enumerate(DEM_CONT):
        for n in names_r:
            rows_out.append([COUNTY, n, 'President', '', 'DEM', cand,
                             cont_by[n][i]])
    for n in names_r:
        rows_out.append([COUNTY, n, 'Ballots Cast', '', 'DEM', '',
                         cont_by[n][3]])
    for i, cand in enumerate(REP):
        for n in names_r:
            rows_out.append([COUNTY, n, 'President', '', 'REP', cand,
                             rep_by[n][i]])
    for n in names_r:
        rows_out.append([COUNTY, n, 'Ballots Cast', '', 'REP', '',
                         rep_by[n][5]])
    for title in PROPOSALS:
        for n, v in props[title].items():
            for cand, val in (('Yes', v[0]), ('No', v[1])):
                rows_out.append([COUNTY, n, title, '', '', cand, val])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(names_r)} precincts')


if __name__ == '__main__':
    main()