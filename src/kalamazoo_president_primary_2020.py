"""Parse Kalamazoo County's March 10, 2020 presidential primary from the
saved per-contest HTML pages in openelections-sources-mi/2020/
presidential_primary/"Kalamazoo County Mar 2020 Pres Primary Precinct
Pages" (Kalamazoo County election-site contest pages, one table per
contest: th.precinct + th.vote headers, td.precinct + td.vote rows, a
final 'Totals' row).  contest386 = DEM President, contest1040 = REP
President; the other five files are local proposals (Yes / No).

The pages carry no Ballots Cast / Registered Voters data and the
proposal pages omit out-of-county jurisdictions, so only candidate rows
are emitted.  'Write-in' is a table column but, per the March 2020
convention, write-ins are not emitted; 'Julián Castro' is canon'd to the
unaccented form.  Each contest's Totals row is verified against the sum
of its precinct rows.

Office titles: the presidential pages' heading text
('DEM President of the United States (Democratic Party)') maps to
office 'President' with party DEM / REP; proposals use their heading
verbatim.
"""
import csv
import re
import unicodedata

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Kalamazoo County Mar 2020 Pres Primary '
       'Precinct Pages')
OUT = '2020/counties/20200310__mi__primary__president__kalamazoo__precinct.csv'
COUNTY = 'Kalamazoo'

DEM = ['Michael Bennet', 'Joe Biden', 'Michael R. Bloomberg', 'Cory Booker',
       'Pete Buttigieg', 'Julian Castro', 'John Delaney', 'Tulsi Gabbard',
       'Amy Klobuchar', 'Bernie Sanders', 'Joe Sestak', 'Tom Steyer',
       'Elizabeth Warren', 'Marianne Williamson', 'Andrew Yang',
       'Uncommitted']
REP = ['Mark Sanford', 'Donald J. Trump', 'Joe Walsh', 'Bill Weld',
       'Uncommitted']
CANON = {'Julián Castro': 'Julian Castro'}

# file -> (office title, party, expected candidate list)
CONTESTS = {
    'contest386.html': ('President', 'DEM', DEM),
    'contest1040.html': ('President', 'REP', REP),
    'contest1027.html': ('Glen Oaks Community College Campus Improvements',
                         '', None),
    'contest443.html': ('Central County Transportation Authority', '', None),
    'contest450.html': ('Schoolcraft Township Incorporation as Charter '
                        'Township', '', None),
    'contest457.html': ('Van Buren Intermediate School District', '', None),
    'contest464.html': ('Schoolcraft Community Schools', '', None),
}

CELL_RE = re.compile(
    r'<td class="(?:precinctid|precinct|vote)">([^<]*)</td>')
TH_RE = re.compile(r'<th class="(?:precinct|vote)">([^<]*)</th>')


def cells(row_html):
    return [c.strip() for c in CELL_RE.findall(row_html)]


def main():
    problems = []
    rows = []
    for fname, (office, party, expected) in CONTESTS.items():
        html = open(f'{SRC}/{fname}', encoding='utf-8',
                    errors='replace').read()
        body = html[html.find('<table'):]
        body = body[:body.find('</table>')]
        rows_html = re.findall(r'<tr[^>]*>(.*?)</tr>', body, re.S)
        headers = ([c.strip() for c in TH_RE.findall(rows_html[0])]
                   if rows_html else [])
        if not headers or headers[0] != 'Jurisdiction, Precinct':
            problems.append(f'{fname}: unexpected header {headers[:3]}')
            continue
        choices = [CANON.get(h, h) for h in headers[1:]]
        if expected is not None and choices[:-1] != expected:
            problems.append(f'{fname}: choices {choices} != expected')
        data = {}      # precinct -> {choice: votes}
        totals = None
        for rh in rows_html[1:]:
            c = cells(rh)
            label = c[0]
            vals = [int(v) for v in c[1:]]
            if len(vals) != len(choices):
                problems.append(f'{fname} {label!r}: {len(vals)} values, '
                                f'want {len(choices)}')
                continue
            if label == 'Totals':
                totals = vals
                continue
            if label in data:
                problems.append(f'{fname}: duplicate precinct {label!r}')
            data[label] = vals
        if totals is None:
            problems.append(f'{fname}: no Totals row')
        else:
            for i, name in enumerate(choices):
                s = sum(v[i] for v in data.values())
                if s != totals[i]:
                    problems.append(f'{fname} {name}: precinct sum {s} != '
                                    f'totals {totals[i]}')
        for label in sorted(data):
            vals = data[label]
            for name, v in zip(choices, vals):
                if name == 'Write-in':
                    continue          # March convention: not emitted
                rows.append([COUNTY, label, office, '', party, name, v])
    if problems:
        for p in problems:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return
    out = '2020/counties/20200310__mi__primary__president__kalamazoo__precinct.csv'
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {out}: {len(rows)} rows')


if __name__ == '__main__':
    main()