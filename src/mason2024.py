"""Parse Mason County's Aug 2024 primary Summary Results Report (Hart, image
only, read via the PaddleOCR cache /tmp/paddleocr_md/MasonCounty/pNNN.md) into
a precinct CSV.

The report is a countywide summary: every statewide and county office prints
one TOTAL-only table with no precinct breakdown. The only per-precinct data
is the delegate-to-county-convention contests (one per precinct, 24 of them)
and the township offices / township millage proposals of the 13 single-
precinct townships (Hamlin and Pere Marquette Charter have two precincts and
print one township-total table each, so their tables are excluded). The CSV
therefore covers only those contests.

Usage:
    .venv/bin/python src/mason2024.py \
        --out 2024/counties/20240806__mi__primary__mason__precinct.csv
"""
import argparse
import csv
import html
import re
import sys

CACHE = '/tmp/paddleocr_md/MasonCounty'
COUNTY = 'Mason'
FOOTER = {'Write-In Totals', 'Not Assigned', 'Total Votes Cast', 'Overvotes',
          'Undervotes', 'Contest Totals', 'Precincts Reporting'}
# Tables whose title line PaddleOCR dropped or mangled, hand-read from the
# page renders (pdftoppm -f N -l N -r 100): p004's first table sits between
# the Surveyor and County Commissioner District 02 tables; p047's third
# table follows the Summit Township trustee table.
MANUAL_TITLES = {('p004', 1): 'DEM County Commissioner -- Democratic '
                               'County Commissioner District 01',
                 ('p047', 2): 'REP Trustee -- Republican Victory Township '
                              'Vote For 2'}
# Countywide tables (no precinct breakdown): statewide/county offices, the
# per-district county commissioner races, and the countywide senior citizen
# millage.
COUNTYWIDE_TAIL = ('Mason County', 'State', 'Congressional District 2',
                   'County Commissioner District', 'Mason County')
# Townships whose township-total tables are also one precinct's totals.
SINGLE_PRECINCT = {
    'Amber', 'Branch', 'Custer', 'Eden', 'Free Soil', 'Grant', 'Logan',
    'Meade', 'Riverton', 'Sheridan', 'Sherman', 'Summit', 'Victory'}
PROPOSALS = {'Road Improvement Millage Renewal Grant Township',
             'Fire Millage Renewal Logan Township',
             'Sheridan Township Road Millage Sheridan Township',
             'Sherman Township Renewal Sherman Township'}


def titlecase(name):
    """'SHERRY O'DONNELL' -> "Sherry O'Donnell"; keeps initials and the
    Mc/Mac capital, Roman-numeral suffixes."""
    out = []
    for w in name.split():
        if w.upper() in ('II', 'III', 'IV', 'JR', 'SR'):
            out.append(w.upper())
        elif len(w) == 1:
            out.append(w.upper())
        elif re.match(r'^[Mm]c[A-Za-z]', w):
            out.append('Mc' + w[2].upper() + w[3:].lower())
        elif "'" in w:
            out.append("'".join(p.capitalize() for p in w.split("'")))
        else:
            out.append(w.capitalize())
    return ' '.join(out)


def tables():
    """Yield ((page, index), title, [(label, value), ...]) in report order."""
    import glob
    for f in sorted(glob.glob(f'{CACHE}/p*.md')):
        page = f.rsplit('/', 1)[1][:-3]
        text = open(f).read()
        title = None
        idx = 0
        for part in re.split(r'(<table.*?</table>)', text, flags=re.S):
            if part.startswith('<table'):
                key = (page, idx)
                idx += 1
                if page == 'p001' and idx == 1:
                    continue  # Statistics turnout table
                rows = re.findall(r'<tr>(.*?)</tr>', part, flags=re.S)
                data = []
                for i, row in enumerate(rows):
                    cells = [html.unescape(c).strip() for c in
                             re.findall(r'<td[^>]*>([^<]*)</td>', row)]
                    if i == 0:
                        continue  # header row ('Vote For 1' / TOTAL)
                    if len(cells) != 2 or not re.fullmatch(
                            r'[\d,]+', cells[1]):
                        continue
                    data.append((cells[0], int(cells[1].replace(',', ''))))
                yield key, MANUAL_TITLES.get(key, title), data
            else:
                m = re.findall(r'^## (.+)$', part, flags=re.M)
                if m:
                    title = m[-1].strip()
                    continue
                m = re.findall(r'<div[^>]*>([^<]+)</div>', part)
                if m:
                    title = m[-1].strip()


def run(out_rows, problems):
    for key, title, data in tables():
        if not title or title == '01':
            problems.append(f'{key}: no title')
            continue
        title = re.sub(r' Vote For \d+$', '', title)
        m = re.match(r'^(DEM|REP) (.+) -{1,2} (?:Democratic|Republican) '
                     r'(.+)$', title)
        if m:
            party, office, jur = m.groups()
        elif title in PROPOSALS:
            party, office, jur = '', title, title
        elif ((title.startswith(('DEM ', 'REP '))
               and (title.endswith(COUNTYWIDE_TAIL)
                    or re.search(r'County Commissioner District \d+$',
                                 title)))
                or title == 'Senior Citizen Millage Proposal Renewal '
                            'Mason County'):
            continue  # countywide table, no precinct breakdown
        else:
            problems.append(f'{key}: unmapped title {title!r}')
            continue

        write_in = 0
        candidates = []
        tvc = over = under = contest_total = None
        for label, value in data:
            if label == 'Write-In Totals':
                write_in = value
            elif label in FOOTER:
                if label == 'Total Votes Cast':
                    tvc = value
                elif label == 'Overvotes':
                    over = value
                elif label == 'Undervotes':
                    under = value
                elif label == 'Contest Totals':
                    contest_total = value
            else:
                candidates.append((label, value))

        # Printed cross-checks: the candidates plus write-ins make up the
        # Total Votes Cast, and TVC + overvotes + undervotes is the Contest
        # Totals row.
        summed = sum(v for _, v in candidates) + write_in
        if tvc is None or summed != tvc:
            problems.append(f'{key} {title!r}: candidate sum {summed} != '
                            f'Total Votes Cast {tvc}')
        if contest_total is not None and tvc + over + under != contest_total:
            problems.append(f'{key} {title!r}: {tvc}+{over}+{under} != '
                            f'Contest Totals {contest_total}')

        dm = re.match(r'^(Supervisor|Clerk|Treasurer|Trustee)$', office)
        if dm:
            # Township office; the township-total table is one precinct's
            # totals only for the single-precinct townships.
            base = re.sub(r' Charter Township$', ' Township', jur)
            if base.removesuffix(' Township') not in SINGLE_PRECINCT:
                continue
            precinct = f'{base} Precinct 1'
            office = f'{office} {jur}'
        elif office == 'Delegate to County Convention':
            # '<jur>/<n>' labels; Ludington's are ward-coded ('/1001' is
            # Ward 1 Precinct 1).
            m2 = re.match(r'^(City of Ludington)/(\d+)$', jur)
            if m2:
                n = int(m2.group(2)) // 1000
            else:
                m2 = re.match(r'^(.+)/(\d+)$', jur)
                n = int(m2.group(2))
            # The general file shortens Pere Marquette Charter Township's
            # precinct labels ('Pere Marquette Charter Twp Prec 1').
            precinct = f'{m2.group(1)} Precinct {n}'
            if m2.group(1) == 'Pere Marquette Charter Township':
                precinct = f'Pere Marquette Charter Twp Prec {n}'
            office = (f'{m2.group(1)}, Precinct {n} '
                      f'Delegate to County Convention')
        elif party == '' and title in PROPOSALS:
            base = ('Grant', 'Logan', 'Sheridan', 'Sherman')
            jur_t = next(t for t in base if t in title)
            precinct = f'{jur_t} Township Precinct 1'
        else:
            continue  # countywide table, no precinct breakdown

        for label, value in candidates:
            out_rows.append([COUNTY, precinct, office, '', party,
                             titlecase(label), value])
        if write_in:
            out_rows.append([COUNTY, precinct, office, '', party,
                             'Write-In', write_in])
        out_rows.append([COUNTY, precinct, office, '', party,
                         'Ballots Cast', tvc])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    out_rows, problems = [], []
    run(out_rows, problems)
    with open(args.out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(out_rows)
    print(f'Wrote {len(out_rows)} rows to {args.out} '
          f'({len(problems)} problems)')
    for p in problems[:40]:
        print('PROBLEM:', p, file=sys.stderr)
    if problems:
        sys.exit(1)


if __name__ == '__main__':
    main()