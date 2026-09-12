#!/usr/bin/env python3
"""Parse Montcalm County's Mar 2020 presidential primary from
'Montcalm MI March 2020 Unofficial Canvass by Precinct.pdf' (12pp
Dominion 'Canvass Results' report, text-extractable, 29 precincts).

Layout: the rotated candidate headers extract REVERSED (one word
piece per line, 'tenneB'/'nediB eoJ'/...); the candidate lists are
hardcoded.  Each party's contest prints as two column blocks with a
19+10 precinct split (the countywide Totals row is on the LAST page
of each block, covering all 29 precincts):
  - DEM main pages 1,3: 13 candidate columns.
  - DEM continuation pages 2,4: Marianne Williamson, Andrew Yang,
    Uncommitted, Votes Cast, Undervotes, Overvotes, Election Day
    Ballots Cast, Total Ballots Cast, Registered Voters, turnout%.
  - REP pages 5-6: 5 candidates + the same 7 summary columns
    (11 numeric values).
  - Four proposals (Library Millage, Law Enforcement Millage,
    Marijuana Establishments [Sidney P1 only], Gratiot-Isabella RESD
    [Crystal/Ferris/Richland P1, all zero]): Yes, No, Votes Cast,
    Undervotes, Overvotes, Election Day BC, Total BC, RV (8 values).
    The RESD section's RV column prints 6/11/9 (26 total), NOT the
    precincts' registered voters -- an internal Dominion count of
    affected voters; it is not cross-checked.

Precinct names wrap across 3 physical lines interleaved with the bare
value row ('Eureka Charter Township,' / values / 'Precinct 1'), with
the same geometry as Livingston's report: the head line sits at top
t, the value line at t+5.3 and the tail at t+10.7 (row pitch 15.1),
so each fragment is assigned to the NEAREST value row on its page by
line top (a fragment above its row is the name's head, below is its
tail).  Values are the LAST width tokens of the value row.

No write-in column exists in this report (DEM candidates 13 +
Williamson + Yang + Uncommitted; REP 4 + Uncommitted).  Ballots Cast
is emitted from the contest's Votes Cast column (= candidate sum),
consistent with the other March 2020 counties; Total Ballots Cast
additionally includes over/undervotes and is verified, not emitted.

Verification: per precinct, candidates sum to Votes Cast (all four
contest types); VC + undervotes + overvotes == Election Day BC ==
Total Ballots Cast; the turnout% == Total BC / RV; every column's
precinct sum == the printed countywide Totals row; RV sums to 45,151
and matches across sections.
"""

import csv
import os
import re
import sys

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Montcalm MI March 2020 Unofficial Canvass '
       'by Precinct.pdf')
OUT = '2020/counties/20200310__mi__primary__president__montcalm__precinct.csv'
COUNTY = 'Montcalm'

DEM_MAIN = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
            'Cory Booker', 'Pete Buttigieg', 'Julian Castro',
            'John Delaney', 'Tulsi Gabbard', 'Amy Klobuchar',
            'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
            'Elizabeth Warren']
DEM_CONT = ['Marianne Williamson', 'Andrew Yang', 'Uncommitted']
DEM_ALL = DEM_MAIN + DEM_CONT
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

INT = r'\d[\d,]*'
TURNOUT_RE = re.compile(r'\s+[\d.]+\s*%$')


def ints(s):
    return [int(x.replace(',', '')) for x in s.split()]


def main():
    pdf = pdfplumber.open(SRC)
    problems = []

    # rows per contest section, split by numeric width: DEM 13 (main
    # candidates) vs 9 (continuation); REP 11; proposals 8
    rows = {}       # sect -> width -> [(name, values)] in print order
    totals = {}     # sect -> width -> values (countywide Totals row)
    sect = None

    for page in pdf.pages:
        ptext = page.extract_text() or ''
        # each page's rotated header identifies its column block
        # (DEM main vs continuation share the section)
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
            if line.startswith(('Canvass Results', 'Montcalm County',
                                'March 10, 2020', '12825 of', 'Run Time',
                                'Run Date', 'Registered Voters')):
                continue
            m = re.match(r'^(.*) - (Democratic|Republican) Party - '
                         r'Vote for not more than 1$', line)
            if m:
                sect = (m.group(1).strip(),
                        'DEM' if 'Democratic' in line else 'REP')
                continue
            m = re.match(r'^(.*) - No Party Declaration$', line)
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

            # rotated-header words: each token is a word reversed (or,
            # for the upright 'Precinct' label, the word itself)
            if all(t.lower() in HEADER_WORDS
                   or t[::-1].lower() in HEADER_WORDS for t in toks):
                continue

            width = {('President of the United States', 'DEM'): 13,
                     ('President of the United States', 'REP'): 11,
                     }.get(sect)
            if sect and sect[1] == 'DEM' and block == 'demcont':
                width = 9
            if sect and sect[1] == '':
                width = 8
            if width is None:
                if run:
                    problems.append(f'unmatched line: {line!r}')
                continue

            if run < width:
                # a wrapped-name fragment: head line above its row's
                # value line, or the previous row's 'Precinct N' tail
                frags.append((top, line, sect))
                continue
            if run > width + 1:
                problems.append(f'{sect} {block}: unexpected numeric '
                                f'run {run} (width {width}): {line!r}')
            data.append((top, sect, width,
                         ' '.join(toks[:-width]).strip(),
                         ints(' '.join(toks[-width:]))))

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
        .get(9, [])
    rep_rows = rows.get(('President of the United States', 'REP'), {}) \
        .get(11, [])
    if len(dem_main) != 29:
        problems.append(f'{len(dem_main)} DEM main rows, expected 29')
    if len(dem_cont) != 29:
        problems.append(f'{len(dem_cont)} DEM cont rows, expected 29')
    if len(rep_rows) != 29:
        problems.append(f'{len(rep_rows)} REP rows, expected 29')
    names_m = [n for n, _ in dem_main]
    names_c = [n for n, _ in dem_cont]
    names_r = [n for n, _ in rep_rows]
    if names_m != names_r:
        problems.append(f'DEM main names != REP names:\n  {names_m}\n  '
                        f'{names_r}')
    if names_c != names_r:
        problems.append(f'DEM cont names != REP names')
    # wrapped names must reassemble to the precincts' names as printed
    # in Montcalm's Nov 2020 general file
    EXPECTED = [
        'Belvidere Township, Precinct 1', 'Bloomer Township, Precinct 1',
        'Bushnell Township, Precinct 1', 'Cato Township, Precinct 1',
        'Crystal Township, Precinct 1', 'Day Township, Precinct 1',
        'Douglass Township, Precinct 1',
        'Eureka Charter Township, Precinct 1',
        'Eureka Charter Township, Precinct 2',
        'Evergreen Township, Precinct 1', 'Evergreen Township, Precinct 2',
        'Fairplain Township, Precinct 1', 'Ferris Township, Precinct 1',
        'Home Township, Precinct 1', 'Maple Valley Township, Precinct 1',
        'Montcalm Township, Precinct 1', 'Pierson Township, Precinct 1',
        'Pine Township, Precinct 1', 'Reynolds Township, Precinct 1',
        'Reynolds Township, Precinct 2', 'Richland Township, Precinct 1',
        'Sidney Township, Precinct 1', 'Winfield Township, Precinct 1',
        'City of Carson City, Precinct 1', 'City of Greenville, Precinct 1',
        'City of Greenville, Precinct 2', 'City of Greenville, Precinct 3',
        'City of Greenville, Precinct 4', 'City of Stanton, Precinct 1',
    ]
    if names_r != EXPECTED:
        problems.append(f'names != Nov 2020 file names:\n  {names_r}')

    # ---- per-precinct checks -------------------------------------------
    main_by = {n: v for n, v in dem_main}
    cont_by = {n: v for n, v in dem_cont}
    rep_by = {n: v for n, v in rep_rows}
    for n in names_r:
        mvals = main_by[n]
        cvals = cont_by[n]
        cand_sum = sum(mvals) + cvals[0] + cvals[1] + cvals[2]
        if cand_sum != cvals[3]:
            problems.append(f'DEM {n!r}: candidates {cand_sum} != '
                            f'Votes Cast {cvals[3]}')
        if cvals[3] + cvals[4] + cvals[5] != cvals[6] \
                or cvals[6] != cvals[7]:
            problems.append(f'DEM {n!r}: VC {cvals[3]} + UV {cvals[4]} '
                            f'+ OV {cvals[5]} != EDBC {cvals[6]} / '
                            f'TBC {cvals[7]}')
        rvals = rep_by[n]
        if sum(rvals[:5]) != rvals[5]:
            problems.append(f'REP {n!r}: candidates {sum(rvals[:5])} '
                            f'!= Votes Cast {rvals[5]}')
        if rvals[5] + rvals[6] + rvals[7] != rvals[8] \
                or rvals[8] != rvals[9]:
            problems.append(f'REP {n!r}: VC+UV+OV != EDBC/TBC')
        if rvals[10] != cvals[8]:
            problems.append(f'{n!r}: REP RV {rvals[10]} != DEM RV '
                            f'{cvals[8]}')
        pct = cvals[7] / cvals[8] * 100 if cvals[8] else 0.0
        # turnout% is printed only on the continuation/REP pages, not
        # stored per row here -- checked via county Totals instead

    # ---- proposals ------------------------------------------------------
    prop_order = []
    props = {}      # sect -> {name: (yes, no, vc, uv, ov, edbc, tbc, rv)}
    for sect, widths in rows.items():
        if sect[1] != '' or 8 not in widths:
            continue
        prop_order.append(sect[0])
        props[sect[0]] = {n: v for n, v in widths[8]}
        if len(widths[8]) not in (1, 3, 29):
            problems.append(f'{sect[0]}: {len(widths[8])} rows')
        for n, v in widths[8]:
            if v[0] + v[1] != v[2]:
                problems.append(f'{sect[0]} {n!r}: Yes+No {v[0] + v[1]} '
                                f'!= Votes Cast {v[2]}')
            if v[2] + v[3] + v[4] != v[5] or v[5] != v[6]:
                problems.append(f'{sect[0]} {n!r}: VC+UV+OV != EDBC/TBC')
            if n not in rep_by:
                problems.append(f'{sect[0]}: precinct {n!r} not in the '
                                f'REP table')
            elif v[7] != rep_by[n][10] \
                    and not sect[0].startswith('Gratiot-Isabella'):
                problems.append(f'{sect[0]} {n!r}: RV {v[7]} != REP RV '
                                f'{rep_by[n][10]}')

    # ---- county Totals rows --------------------------------------------
    tot_dem_main = totals.get(('President of the United States', 'DEM'), {})
    tot_dem_cont = tot_dem_main.get(9)
    tot_dem_main = tot_dem_main.get(13)
    tot_rep = totals.get(('President of the United States', 'REP'), {}) \
        .get(11)
    if tot_dem_main is None or tot_dem_cont is None or tot_rep is None:
        problems.append('missing a county Totals row')
    else:
        cols = [sum(main_by[n][i] for n in names_r) for i in range(13)]
        if cols != tot_dem_main:
            problems.append(f'DEM main sums {cols} != Totals '
                            f'{tot_dem_main}')
        cols = [sum(cont_by[n][i] for n in names_r) for i in range(9)]
        if cols != tot_dem_cont:
            problems.append(f'DEM cont sums {cols} != Totals '
                            f'{tot_dem_cont}')
        cols = [sum(rep_by[n][i] for n in names_r) for i in range(11)]
        if cols != tot_rep:
            problems.append(f'REP sums {cols} != Totals {tot_rep}')

    for title, by in props.items():
        want = totals.get((title, ''), {}).get(8)
        if want is None:
            problems.append(f'{title}: no county Totals row')
            continue
        cols = [sum(v[i] for v in by.values()) for i in range(8)]
        if cols != want:
            problems.append(f'{title}: sums {cols} != Totals {want}')

    # RV countywide == 45151 (printed '12825 of 45151')
    rv_sum = sum(rep_by[n][10] for n in names_r) if len(rep_rows) == 29 \
        else None
    if rv_sum is not None and rv_sum != 45151:
        problems.append(f'RV sum {rv_sum} != 45151')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)

    # ---- emission -------------------------------------------------------
    rows_out = []
    for n in names_r:
        rows_out.append([COUNTY, n, 'Registered Voters', '', '', '',
                         rep_by[n][10]])
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
    for title in prop_order:
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
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(names_r)} precincts, '
          f'proposals: {prop_order}')


# rotated-header vocabulary: the extracted tokens are each word
# reversed, so check tokens against the reversed word set
HEADER_WORDS = {w.lower() for w in (
    'Precinct Michael Bennet Joe Biden Michael R. Bloomberg Cory Booker '
    'Pete Buttigieg Julián Castro John Delaney Tulsi Gabbard Amy '
    'Klobuchar Bernie Sanders Joe Sestak Tom Steyer Elizabeth Warren '
    'Marianne Williamson Andrew Yang Uncommitted Votes Cast Undervotes '
    'Overvotes Ballots Voting Day Election Total Totals Voters Registered '
    'Percentage Turnout Mark Sanford Donald J. Trump Joe Walsh Bill Weld '
    'Yes No').split()}


if __name__ == '__main__':
    main()