#!/usr/bin/env python3
"""Parse Ogemaw County's Mar 2020 presidential primary from
'Ogemaw MI March 2020 Primary Results (unofficial).pdf' (2pp, 17
precincts, text-extractable).

UNOFFICIAL results (report stamped 2020-03-10 22:10:43) - no certified
precinct-level source exists for Ogemaw.  Four contests:
  - President DEM: 17 columns (14 candidates + Yang + Uncommitted +
    Write-in; the rotated header's word x-positions show Uncommitted
    LEFT of Write-in, checked below); precinct names wrap across 3
    physical lines INTERLEAVED with the bare-numeric value row, and
    the county Total row's first cell is fused ('31,183' = Bennet 3 +
    Biden 1,183).  A wrap line can merge onto the value row
    ('Township, 0 104 ...').
  - President REP: 6 columns (Sanford, Trump, Walsh, Weld,
    Uncommitted, Write-in); clean single-line rows.
  - C.O.O.R. ISD Proposal I Special Education / Proposal II Operating
    Millage: Yes/No rows for 16 precincts -- Richland Township,
    Precinct 1 has NO row in either proposal table, and the printed
    county totals reconcile exactly with the 16 printed rows, so it
    is emitted as printed.

No Registered Voters / Ballots Cast columns exist in this report, so
only candidate rows are emitted.

The DEM table's wrapped names are not reconstructed: its 17 value rows
print in the same order as the REP table's clean rows, so rows are
assigned by position; the interleaved name fragments are verified
token-wise in aggregate against the 17 expected names.  The column
order's last two entries (Uncommitted before Write-in) are verified
against the rotated header's word x-positions and the printed county
Total row.

Verification: every contest's precinct values sum to the printed
Total row (DEM via the fused first cell).
"""

import csv
import os
import re
import sys

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Ogemaw MI March 2020 Primary Results '
       '(unofficial).pdf')
OUT = '2020/counties/20200310__mi__primary__president__ogemaw__precinct.csv'
COUNTY = 'Ogemaw'

DEM_HEAD = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg',
            'Cory Booker', 'Pete Buttigieg', 'Julian Castro',
            'John Delaney', 'Tulsi Gabbard', 'Amy Klobuchar',
            'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
            'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang']
REP_HEAD = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld']

INT = r'\d[\d,]*'
PROPOSALS = ['C.O.O.R. ISD Proposal I Special Education',
             'C.O.O.R. ISD Proposal II Operating Millage']

# wrapped table-header words; skipped so they never pollute the DEM
# name-fragment buffers ('Precinct 1' tail lines are NOT filtered --
# they complete the wrapped names)
HEADER_TOKENS = {'Bennet', 'Biden', 'Bloomberg', 'Booker', 'Buttigieg',
                 'Castro', 'Delaney', 'Gabbard', 'Klobuchar', 'Sanders',
                 'Sestak', 'Steyer', 'Warren', 'Williamson', 'Yang',
                 'Write-', 'Write-in', 'Writein', 'Uncommitted',
                 'Sanford', 'TrumpJoe', 'Walsh', 'Weld', 'Yes', 'No'}


def ints(s):
    return [int(x.replace(',', '')) for x in s.split()]


def split_row(line, n, name_digits):
    """Split a line into (leading-name, last-n-int-values).  The name
    may be empty (bare DEM value row) or a wrap fragment ('Township,').
    Single-line rows carry the precinct's trailing digit ('Precinct
    1'), so name_digits=True allows a longer run with digits in the
    name; the DEM table's wrapped value rows never do."""
    toks = line.split()
    i = len(toks)
    while i > 0 and re.fullmatch(INT, toks[i - 1]):
        i -= 1
    run = len(toks) - i
    if run < n or (not name_digits and run != n):
        return None
    vals = ints(' '.join(toks[-n:]))
    name = ' '.join(toks[:-n]).strip()
    if not name_digits and re.search(r'\d', name):
        return None
    return name, vals


def main():
    pdf = pdfplumber.open(SRC)
    problems = []
    rep_rows = []       # (name, values[6]) in print order
    dem_rows = []       # values[17] in print order
    dem_frag_text = ''  # every DEM-section name fragment, raw, in order
    frag = []           # name fragments accumulated since the last row
    prop_rows = {}      # title -> [(name, yes, no)]
    totals_raw = {}     # sect -> raw Total-row text (DEM cell fused)
    sect = None

    for page in pdf.pages:
        for raw in (page.extract_text() or '').split('\n'):
            line = raw.strip()
            if not line:
                continue
            if line.startswith('UNOFFICIAL RESULTS') \
                    or re.match(r'^\d+ OF \d+ REPORTING$', line,
                                re.IGNORECASE) \
                    or re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$',
                                line):
                continue
            m = re.match(r'^President of the United States \((DEM|REP)\)',
                         line)
            if m:
                sect = ('President', m.group(1))
                frag = []
                continue
            m = re.match(r'^(.*) \(Vote for \d+\)$', line)
            if m and not line.startswith('President of the United States'):
                sect = m.group(1).strip()
                if sect not in PROPOSALS:
                    problems.append(f'unexpected contest {sect!r}')
                    sect = None
                frag = []
                continue
            if any(tok in HEADER_TOKENS for tok in line.split()):
                continue    # table-header line
            if line.startswith('Total '):
                totals_raw[sect] = line[6:]
                continue

            width = {('President', 'DEM'): 17, ('President', 'REP'): 6,
                     PROPOSALS[0]: 2, PROPOSALS[1]: 2}.get(sect)
            if width is None:
                continue
            got = split_row(line, width,
                            name_digits=sect != ('President', 'DEM'))
            if got is None:
                frag.append(line)
                if sect == ('President', 'DEM'):
                    dem_frag_text += line
                continue
            name, vals = got
            if sect == ('President', 'DEM'):
                if name:
                    frag.append(name)
                    dem_frag_text += name
                dem_rows.append(vals)
                frag = []
            elif sect == ('President', 'REP'):
                if not name:
                    problems.append(f'REP row without a name: {line!r}')
                rep_rows.append((name, vals))
                if frag:
                    problems.append(f'REP stray fragments {frag}')
                    frag = []
            else:
                if not name:
                    problems.append(f'{sect} row without a name: {line!r}')
                prop_rows.setdefault(sect, []).append((name, vals[0],
                                                       vals[1]))
                if frag:
                    problems.append(f'{sect} stray fragments {frag}')
                    frag = []

    if frag:
        problems.append(f'trailing fragments {frag}')

    # ---- structure checks ----------------------------------------------
    names = [n for n, _ in rep_rows]
    if len(names) != 17:
        problems.append(f'{len(names)} REP rows, expected 17')
    if len(dem_rows) != 17:
        problems.append(f'{len(dem_rows)} DEM rows, expected 17')
    # Richland Township, Precinct 1 has no row in either proposal
    # table (verified against the word positions: it appears on page 1
    # only in the REP presidential table); the printed proposal totals
    # reconcile with the 16 printed rows
    prop_names = [n for n in names if n != 'Richland Township, '
                  'Precinct 1']
    for title in PROPOSALS:
        got_names = [n for n, _y, _n in prop_rows.get(title, [])]
        if got_names != prop_names:
            problems.append(f'{title}: rows {got_names} != expected '
                            f'{prop_names}')

    # the interleaved DEM name fragments (row i's head + row i-1's
    # tail share a gap) must jointly reassemble into the 17 names
    all_frag = dem_frag_text.replace(' ', '').replace(',', '')
    all_names = ''.join(names).replace(' ', '').replace(',', '')
    if all_frag != all_names:
        problems.append(f'DEM fragments {all_frag!r} != names '
                        f'{all_names!r}')

    # ---- candidate order (rotated-header word x-positions) -------------
    # the DEM header cells are rotated; pdfplumber still reports each
    # label's x0, which is the column order.  Require Uncommitted LEFT
    # of Write-in in both the DEM band (anchored on 'Bennet') and the
    # REP band (anchored on 'Sanford').
    def header_order(anchor):
        for page in pdf.pages:
            words = page.extract_words()
            anchors = [w for w in words if w['text'] == anchor]
            for a in anchors:
                band = [w for w in words
                        if abs(w['top'] - a['top']) < 10]
                uc = [w['x0'] for w in band if w['text'] == 'Uncommitted']
                wi = [w['x0'] for w in band
                      if w['text'].startswith('Write-')]
                if uc and wi:
                    return uc[0], wi[0]
        return None, None

    dem_uc_x, dem_wi_x = header_order('Bennet')
    rep_uc_x, rep_wi_x = header_order('Sanford')
    if dem_uc_x is None or dem_wi_x is None:
        problems.append('DEM header: Uncommitted/Write-in x not found')
    elif dem_uc_x >= dem_wi_x:
        problems.append(f'DEM header: Uncommitted x {dem_uc_x} not left '
                        f'of Write-in x {dem_wi_x}')
    if rep_uc_x is None or rep_wi_x is None:
        problems.append('REP header: Uncommitted/Write-in x not found')
    elif rep_uc_x >= rep_wi_x:
        problems.append(f'REP header: Uncommitted x {rep_uc_x} not left '
                        f'of Write-in x {rep_wi_x}')
    dem_cands = DEM_HEAD + ['Uncommitted', 'Write-In']
    rep_cands = REP_HEAD + ['Uncommitted', 'Write-In']

    # ---- totals ---------------------------------------------------------
    want_d = totals_raw.get(('President', 'DEM'))
    if want_d is None:
        problems.append('DEM: no Total row')
    else:
        cols = [sum(r[i] for r in dem_rows) for i in range(17)]
        # the printed Total's first cell is fused ('31,183' = Bennet 3
        # fused with Biden 1,183): one token for cols 0+1
        toks = want_d.split()
        tok = toks[0]
        fused = str(cols[0]) + f'{cols[1]:,}'
        if tok != fused:
            problems.append(f'DEM: fused cell {tok!r} != {fused!r} '
                            f'(Bennet {cols[0]} + Biden {cols[1]})')
        elif ints(' '.join(toks[1:])) != cols[2:]:
            problems.append(f'DEM: sums cols 2+ {cols[2:]} != Total '
                            f'{toks[1:]}')
        # last two tokens: Uncommitted then Write-in
        if [cols[15], cols[16]] != [int(toks[-2].replace(',', '')),
                                    int(toks[-1].replace(',', ''))]:
            problems.append(f'DEM: Uncommitted/Write-in sums '
                            f'[{cols[15]}, {cols[16]}] != Total last '
                            f'two {toks[-2:]}')

    want_r = totals_raw.get(('President', 'REP'))
    if want_r is None:
        problems.append('REP: no Total row')
    else:
        cols = [sum(r[1][i] for r in rep_rows) for i in range(6)]
        if cols != ints(want_r):
            problems.append(f'REP: sums {cols} != Total {want_r}')

    for title in PROPOSALS:
        raw = totals_raw.get(title)
        want = ints(raw) if raw is not None else None
        got = [sum(y for _n, y, _v in prop_rows.get(title, [])),
               sum(no for _n, _y, no in prop_rows.get(title, []))]
        if want is None:
            problems.append(f'{title}: no Total row')
        elif got != want:
            problems.append(f'{title}: sums {got} != Total {want}')

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        sys.exit(1)

    # ---- emission -------------------------------------------------------
    rows = []
    for i, cand in enumerate(dem_cands):
        for j, name in enumerate(names):
            rows.append([COUNTY, name, 'President', '', 'DEM', cand,
                         dem_rows[j][i]])
    for i, cand in enumerate(rep_cands):
        for j, (name, vals) in enumerate(rep_rows):
            rows.append([COUNTY, name, 'President', '', 'REP', cand,
                         vals[i]])
    for title in PROPOSALS:
        for name, yes, no in prop_rows[title]:
            for cand, v in (('Yes', yes), ('No', no)):
                rows.append([COUNTY, name, title, '', '', cand, v])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, OUT)
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows, {len(names)} precincts, '
          f'DEM last columns: {dem_cands[-2:]}')


if __name__ == '__main__':
    main()