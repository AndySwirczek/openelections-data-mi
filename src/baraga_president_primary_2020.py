"""Parse Baraga County's March 10, 2020 presidential primary from the
text-layer ES&S "Statement of Votes Cast" PDF (21 pages) in
openelections-sources-mi/2020/presidential_primary/ -- the same
contest-major 2-up SOVC family as Kalkaska/Otsego, but with a real
text layer (pdfplumber, no OCR needed).

- p1: voting stats -- 8 precincts, RV 6,445 / Cards Cast 1,527 /
  Voters Cast 1,527.
- p2-p5: DEM President -- p2 Times Cast + Registered Voters (left
  table) + Bennet/Biden (right), p3 Bloomberg..Klobuchar, p4
  Sanders..Uncommitted, p5 Total Votes + Unresolved Write-In.
  Candidate headers are rotated 90-degrees (extract_words() reverses
  them: 'tenneB leahciM'); columns are mapped by x position to the
  known sequence and verified by per-candidate sums against the
  printed County - Total rows.
- p6-p7: the same for REP (Times Cast/RV + Sanford/Trump; then Walsh /
  Weld / Uncommitted / TV / WI).
- p8-p21: seven single-precinct proposals (Covington x2, L'Anse x2,
  Spurr x3), each a Times Cast + Registered Voters + Yes/No/TV page
  and an Unresolved Write-In page (all 0).

Wrapped precinct labels ('Covington Township,' / 'Precinct 1') split
across three lines with the left-table values landing on the
continuation line -- lines are clustered into rows by a <=8pt top
gap.  Column zones: two-table pages use x<160 label / 160-300 left
values / 300-500 right label (dropped, it repeats the left) / >=500
right values; one-table pages use x<155 label / >=155 values.

Source phenomenon (same as Kalkaska/Otsego): DEM TC + REP TC
(799 + 678 = 1,477) < Voters Cast 1,527 -- short in 3 of 8 precincts
(Covington 91/96, L'Anse 703/735, Spurr 90/103); the check is a bound
(<=) with a NOTE.  Proposal Times Cast == Cards Cast exactly.
Emission per precinct: Registered Voters, Ballots Cast (Voters Cast),
Ballots Cast DEM/REP (Times Cast), 16 DEM + 5 REP candidates,
proposal Yes/No.  Write-in columns are not emitted (March convention;
DEM WI 0 / REP WI 0).  Checks: stats and candidate sums == printed
County - Total rows, per-precinct candidates == Total Votes,
TV <= Times Cast, proposal cross-checks.
"""
import csv
import re

import pdfplumber

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Baraga MI March 2020 Statement of Votes Cast.pdf')
OUT = '2020/counties/20200310__mi__primary__president__baraga__precinct.csv'
COUNTY = 'Baraga'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']

# proposal title -> (Times Cast page, write-in page), 1-based
PROPOSALS = {
    'Covington Twp Recreation Prop': (8, 9),
    'Covington Twp Ambulance & Fire Prop': (10, 11),
    "L'Anse Twp Ambulance Renew Prop": (12, 13),
    "L'Anse Twp Fire Renew Prop": (14, 15),
    'Spurr Twp Garbage Millage Prop': (16, 17),
    'Spurr Twp Road Millage Prop': (18, 19),
    'Spurr Twp Cemetery Millage Prop': (20, 21),
}


def rows_of(page, two=False):
    """-> (label, values) per clustered line-row: label is the
    left-table label text.  Two-table pages (Times Cast + Registered
    Voters on the left, candidates on the right, the right table
    repeating the precinct label) use x zones -- label <155, left
    values 155-300, right label 300-500 (dropped), right values >=500
    -- and left values are emitted before right values regardless of
    line order (wrapped labels put the right-table values on an
    earlier line).  One-table pages read label <155, values >=155 in
    (top, x0) order."""
    words = [w for w in page.extract_words() if not w['text'].endswith('%')]
    lines = {}
    for w in words:
        lines.setdefault(round(w['top']), []).append(w)
    tops = sorted(lines)
    groups, cur = [], [tops[0]]
    for t in tops[1:]:
        if t - cur[-1] <= 8:
            cur.append(t)
        else:
            groups.append(cur)
            cur = [t]
    groups.append(cur)
    out = []
    for g in groups:
        ws = sorted((w for t in g for w in lines[t]),
                    key=lambda w: (w['top'], w['x0']))
        text = ' '.join(w['text'] for w in ws)
        if g[0] < 150 or 'Page:' in text:
            continue
        if not any(re.fullmatch(r'[\d,]+', w['text']) for w in ws):
            continue
        label = ' '.join(w['text'] for w in ws if w['x0'] < 155)
        num = lambda w: re.fullmatch(r'[\d,]+', w['text'])
        if two:
            left = [int(w['text'].replace(',', '')) for w in ws
                    if 155 <= w['x0'] < 300 and num(w)]
            right = [int(w['text'].replace(',', '')) for w in ws
                     if w['x0'] >= 500 and num(w)]
            vals = left + right
        else:
            vals = [int(w['text'].replace(',', '')) for w in ws
                    if w['x0'] >= 155 and num(w)]
        out.append((' '.join(label.split()), vals))
    return out


def main():
    problems = []
    pdf = pdfplumber.open(SRC)
    # two-table pages: president TC pages and proposal TC pages
    TWO = {2, 6} | set(range(8, 22, 2))
    pages = [rows_of(p, two=n in TWO) for n, p in enumerate(pdf.pages, 1)]

    def scan(pno, known=None):
        """-> ({precinct: vals}, county_total) for one page, dropping
        the Cumulative rows; county total = the last non-cumulative
        Total row."""
        data, total = {}, None
        # wrapped labels split into a first fragment (carrying the
        # values) and a valueless tail ('Covington Township,' +
        # 'Precinct 1'): hold the first fragment until the tail joins
        pending = None
        for label, vals in pages[pno - 1]:
            if label.startswith('Cumulative'):
                continue
            if not vals:
                if pending is not None and known is not None \
                        and f'{pending} {label}' in known:
                    data[f'{pending} {label}'] = pending_vals
                    pending = None
                continue
            if 'Total' in label:
                if pending is not None:
                    problems.append(f'p{pno}: unmatched {pending!r}')
                    pending = None
                if not label.startswith('Cumulative'):
                    total = vals
                continue
            if known is None or label in known:
                if pending is not None:
                    problems.append(f'p{pno}: unmatched {pending!r}')
                    pending = None
                if label in data:
                    problems.append(f'p{pno}: duplicate {label!r}')
                data[label] = vals
            elif pending is None:
                pending = label  # first fragment of a wrapped label
                pending_vals = vals
            else:
                problems.append(f'p{pno}: unmatched label {label!r}')
        if pending is not None:
            problems.append(f'p{pno}: unmatched {pending!r}')
        return data, total

    # voting stats (p1): RV, Cards Cast, Voters Cast
    stats, cty = scan(1)
    stats.pop('County', None)
    if len(stats) != 8 or cty != [6445, 1527, 1527]:
        problems.append(f'stats: {len(stats)} precincts, county {cty}')
    for col, want in ((0, 6445), (1, 1527), (2, 1527)):
        s = sum(v[col] for v in stats.values())
        if s != want:
            problems.append(f'stats col {col}: sum {s} != {want}')
    if problems:
        for p in problems:
            print('PROBLEM:', p)
        return

    # president tables: (page, party, value offset, names)
    cand = {p: {'DEM': {}, 'REP': {}} for p in stats}
    tc = {p: [None, None] for p in stats}
    tables = [(2, 'DEM', 2, DEM[:2]), (3, 'DEM', 0, DEM[2:9]),
              (4, 'DEM', 0, DEM[9:]), (6, 'REP', 2, REP[:2]),
              (7, 'REP', 0, REP[2:])]
    for pno, party, skip, names in tables:
        data, total = scan(pno, stats)
        for p, vals in data.items():
            if pno in (2, 6):
                tc[p][0 if party == 'DEM' else 1] = vals[0]
                if vals[1] != stats[p][0]:
                    problems.append(f'p{pno}: {p} RV {vals[1]} != '
                                    f'stats {stats[p][0]}')
            if pno == 7:
                got = vals[:3]
                cand[p][party]['Total Votes'] = vals[3]
            elif pno in (2, 6):
                got = vals[skip:]
            else:
                got = vals
            if len(got) != len(names):
                problems.append(f'p{pno}: {p} got {len(got)} for '
                                f'{len(names)} names')
                continue
            for n, v in zip(names, got):
                cand[p][party][n] = v
        if total is None:
            problems.append(f'p{pno}: county total missing')
            continue
        want = total[skip:] if pno in (2, 6) else total
        if pno == 7:
            want = total[:3]
        for n, v in zip(names, want):
            s = sum(cand[p][party][n] for p in stats)
            if s != v:
                problems.append(f'{party} {n}: sum {s} != County-Total {v}')
    # Total Votes (p5 DEM; REP TV already taken from p7)
    data, total = scan(5, stats)
    for p, vals in data.items():
        if len(vals) != 2:
            problems.append(f'p5: {p} -> {vals}')
            continue
        cand[p]['DEM']['Total Votes'] = vals[0]
    if total is None or len(total) != 2:
        problems.append(f'p5: county total {total}')
    else:
        s = sum(cand[p]['DEM']['Total Votes'] for p in stats)
        if s != total[0]:
            problems.append(f'DEM TV sum {s} != County-Total {total[0]}')
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
            print(f'{len(problems)} problems; not writing')
        return

    # proposals
    props = {}
    for title, (tp, wp) in PROPOSALS.items():
        d, t_tot = scan(tp, stats)
        wi, wi_tot = scan(wp, stats)
        if len(d) != 1 or t_tot != list(d.values())[0]:
            problems.append(f'{title}: tc rows {len(d)} total {t_tot}')
        if wi_tot != [0]:
            problems.append(f'{title}: wi total {wi_tot}')
        props[title] = d
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
            print(f'{len(problems)} problems; not writing')
        return

    # per-precinct checks
    for party, seq in (('DEM', DEM), ('REP', REP)):
        for p in sorted(stats):
            s = sum(cand[p][party][n] for n in seq)
            if s != cand[p][party]['Total Votes']:
                problems.append(f'{party} {p}: candidates {s} != TV '
                                f'{cand[p][party]["Total Votes"]}')
            pi = 0 if party == 'DEM' else 1
            if cand[p][party]['Total Votes'] > tc[p][pi]:
                tv = cand[p][party]['Total Votes']
                problems.append(f'{party} {p}: TV {tv} > TC {tc[p][pi]}')
    for p in sorted(stats):
        d, r = tc[p]
        if d + r > stats[p][2]:
            problems.append(f'{p}: DEM TC {d} + REP TC {r} > Cards Cast '
                            f'{stats[p][2]}')
        elif d + r < stats[p][2]:
            print(f'NOTE: {p}: DEM TC {d} + REP TC {r} < Cards Cast '
                  f'{stats[p][2]} (source phenomenon)')
    for title, d in props.items():
        p = list(d)[0]
        if d[p][2] + d[p][3] != d[p][4]:
            problems.append(f'{title}: Yes+No != TV')
        if d[p][4] > d[p][0]:
            problems.append(f'{title}: TV {d[p][4]} > TC {d[p][0]}')
        if d[p][0] != stats[p][1] or d[p][0] != stats[p][2]:
            problems.append(f'{title}: TC {d[p][0]} != Cards/Voters Cast '
                            f'{stats[p][1]}/{stats[p][2]}')
    if problems:
        for p in problems[:40]:
            print('PROBLEM:', p)
            print(f'{len(problems)} problems; not writing')
            return

    rows_out = []
    for p in sorted(stats):
        rows_out.append([COUNTY, p, 'Registered Voters', '', '', '',
                         stats[p][0]])
        rows_out.append([COUNTY, p, 'Ballots Cast', '', '', '', stats[p][2]])
        for party in ('DEM', 'REP'):
            rows_out.append([COUNTY, p, 'Ballots Cast', '', party, '',
                             tc[p][0 if party == 'DEM' else 1]])
        for party, seq in (('DEM', DEM), ('REP', REP)):
            for n in seq:
                rows_out.append([COUNTY, p, 'President', '', party, n,
                                 cand[p][party][n]])
        for title, d in props.items():
            if p in d:
                rows_out.append([COUNTY, p, title, '', '', 'Yes', d[p][2]])
                rows_out.append([COUNTY, p, title, '', '', 'No', d[p][3]])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows_out)
    print(f'wrote {OUT}: {len(rows_out)} rows, {len(stats)} precincts')
    print('county: Biden',
          sum(cand[p]['DEM']['Joe Biden'] for p in stats),
          'Sanders', sum(cand[p]['DEM']['Bernie Sanders'] for p in stats),
          'Trump', sum(cand[p]['REP']['Donald J. Trump'] for p in stats),
          'BC', sum(v[2] for v in stats.values()))


if __name__ == '__main__':
    main()