"""Parse "Results per Precinct" HTML sources into per-county CSVs.

Sources (openelections-sources-mi/2020/primary) share one generator format:
contest-major — an <h2 class="contest">Office (DEM) (Vote for 1)</h2> followed
by a <table class="stattable"> whose first row is the candidate header
("Name &#8211; DEM", "Write-in", "Yes"/"No" for proposals) and whose data rows
are one precinct per row, ending in a <tr class="total"> row used for
validation.

Known counties using this format:
- Oceana MI Aug 4 2020 Primary Results per Precinct.html
- Delta County Aug 4 2020 Primary Results per Precinct.html

Outputs 2020/counties/20200804__mi__primary__<county>__precinct.csv with the
standard header. No turnout columns exist in this source format (like the
Kalamazoo CSV source).
"""
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import normalize_precinct, write_csv  # noqa: E402

SRC = '/Users/dwillis/code/openelections-sources-mi/2020/primary'

TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S | re.I)
TAG = re.compile(r'<[^>]+>')


def cells(tr_html):
    out = []
    for raw in TD.findall(tr_html):
        text = html.unescape(TAG.sub('', raw))
        out.append(' '.join(text.split()))
    return out


def parse_contest_tables(path):
    """[(title, header_cells, [(precinct, [votes...])], [total_votes...])]"""
    text = html.unescape(open(path, encoding='utf-8', errors='replace').read())
    contests = []
    for chunk in text.split('<h2 class="contest">')[1:]:
        title = ' '.join(TAG.sub('', html.unescape(chunk.split('</h2>')[0])).split())
        tbl = chunk[chunk.index('<table'):chunk.index('</table>')]
        header, rows, totals = None, [], None
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, re.S | re.I):
            c = cells(tr)
            if not c:
                continue
            if c[0].strip().lower() == 'precinct':
                header = c
                continue
            if 'class="total"' in tr or c[0].strip().lower() == 'total':
                totals = [v.replace(',', '') for v in c[1:]]
                continue
            if not c[0].strip():
                continue
            rows.append((c[0].strip(), c[1:]))
        contests.append((title, header, rows, totals))
    return contests


def map_contest(title, county):
    """(office, district) for a contest title (party already stripped)."""
    t = ' '.join(title.split())
    m = re.match(r'U\.?\s*S\.?\s+Senator$', t, re.I) or re.match(
        r'United States Senator$', t, re.I)
    if m:
        return 'U.S. Senate', ''
    m = re.match(r'(?:U\.?S\.? |United States )?(?:Rep(?:resentative)? in '
                 r'Congress|Congress)(?:\s+(\d+)(?:st|nd|rd|th)\b.*)?$', t, re.I)
    if m or 'in Congress' in t:
        dm = re.search(r'(\d+)', t)
        return 'U.S. House', dm.group(1) if dm else ''
    m = re.match(r'Rep(?:resentative)? in State Legisl\w* (\d+)', t, re.I)
    if m:
        return 'State House', m.group(1)
    m = re.match(r'County Commissioner (\d+)', t, re.I)
    if m:
        return 'County Commissioner', m.group(1)
    # township-scoped offices: "Supervisor for X Township" /
    # "Township Supervisor for X Township" etc.
    m = re.match(r'(?:Township )?(Supervisor|Clerk|Treasurer|Trustee) '
                 r'for (.+?) Township$', t, re.I)
    if m:
        return m.group(1).title(), ''
    m = re.match(r'Delegate (.+)$', t, re.I)
    if m:
        return 'Precinct Delegate', m.group(1).strip()
    m = re.match(r'Precinct Delegate for (.+)$', t, re.I)
    if m:
        return 'Precinct Delegate', m.group(1).strip()
    if t.startswith('Drain Commissioner for County'):
        return 'Drain Commissioner', ''
    if t.startswith('Surveyor for County'):
        return 'Surveyor', ''
    FIX = {
        'Prosecuting Attorney': 'Prosecuting Attorney',
        'County Prosecuting Attorney': 'Prosecuting Attorney',
        'County Sheriff': 'Sheriff',
        'County Clerk/Register of Deeds': 'County Clerk/Register of Deeds',
        'County Road Commissioner': 'Road Commissioner',
        'County Drain Commissioner': 'Drain Commissioner',
        'County Surveyor': 'Surveyor',
    }
    return FIX.get(t, t), ''


def parse_calhoun():
    """Calhoun DetailedResults.html: per-contest tables with columns
    PRECINCT NAME | # REG. VOTERS | POLL BOOK | % REG VOTERS | candidates...
    and (possibly duplicated) Totals rows used for validation."""
    county = 'Calhoun'
    path = os.path.join(SRC, 'Calhoun MI Aug 4 2020 Primary DetailedResults.html')
    text = html.unescape(open(path, encoding='utf-8', errors='replace').read())
    rows, errors = [], []
    pseudo_seen = {}
    for chunk in re.split(r"<a name='", text)[1:]:
        title = chunk.split("'")[0].strip()
        tbl = chunk[chunk.index('<table'):chunk.index('</table>')]
        trs = re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, re.S | re.I)
        m = re.match(r'^(.*?)\s*\((DEM|REP)\)$', title)
        party = m.group(2) if m else ''
        t = m.group(1).strip() if m else title
        county_prefix = 'Calhoun County '
        if t.startswith(county_prefix):
            t = t[len(county_prefix):]
        office, district = map_contest(t, 'Calhoun')
        header = None
        data = []          # (precinct, [votes...])
        totals_list = []   # each Totals row's candidate values
        dropped_votes = []  # out-of-county rows subtracted from the Totals
        for tr in trs:
            c = cells(tr)
            if not c or not any(x for x in c):
                continue
            if c[0].strip().upper().startswith('PRECINCT'):
                header = c
                continue
            if c[0].strip().lower() == 'totals':
                totals_list.append([v.strip() for v in c[4:]])
                continue
            if re.search(r'\((?:St\. )?[A-Za-z. ]+ Co\.\)$', c[0].strip()):
                # out-of-county precincts (e.g. Athens Area Schools spans
                # St. Joseph/Branch/Kalamazoo counties): not Calhoun precincts;
                # subtract their votes from the contest's printed Totals
                dropped_votes.append([v.strip() for v in c[4:]])
                continue
            row = (c[0].strip(), [v.strip() for v in c[4:]])
            if data and data[-1][0] == row[0]:
                # some contests print the precinct row twice (the copy with a
                # degraded Poll Book value); keep one
                if data[-1][1] == row[1]:
                    continue
                errors.append(f'{title!r}: conflicting duplicate rows for {row[0]}')
                continue
            data.append(row)
            pseudo_seen.setdefault((row[0], 'Registered Voters'), []).append(
                int(c[1].replace(',', '')))
            pseudo_seen.setdefault((row[0], 'Ballots Cast'), []).append(
                int(c[2].replace(',', '')))
        cand_names = ['Write-In' if h.lower() == 'write-in' else h.strip()
                      for h in (header[4:] if header else [])]
        for tv in totals_list:
            for i, name in enumerate(cand_names):
                if i < len(tv) and tv[i]:
                    expected = (int(tv[i].replace(',', ''))
                                - sum(int(v[i].replace(',', '')) for v in dropped_votes
                                      if i < len(v) and v[i].strip()))
                    got = sum(int(votes[i].replace(',', '')) for _, votes in data
                              if i < len(votes) and votes[i].strip())
                    if got != expected:
                        errors.append(f'{title!r} {name!r}: sum {got} != Totals {expected}')
        for precinct, votes in data:
            precinct = normalize_precinct(precinct)
            for i, name in enumerate(cand_names):
                v = votes[i].strip() if i < len(votes) else ''
                if v == '':
                    continue
                rows.append({'county': county, 'precinct': precinct, 'office': office,
                             'district': district, 'party': party, 'candidate': name,
                             'votes': int(v.replace(',', ''))})
    for (precinct, pseudo_name), values in pseudo_seen.items():
        if len(set(values)) > 1:
            print(f'NOTE {precinct}: {pseudo_name} values vary across contests {values}; '
                  f'kept {max(set(values), key=values.count)}')
        rows.append({'county': county, 'precinct': normalize_precinct(precinct),
                     'office': pseudo_name, 'district': '', 'party': '', 'candidate': '',
                     'votes': max(set(values), key=values.count)})
    if errors:
        for e in errors[:20]:
            print('ERROR', e)
        sys.exit(f'Calhoun: {len(errors)} errors')
    write_csv('Calhoun', rows)


def parse_html_per_contest(fname, county):
    path = os.path.join(SRC, fname)
    contests = parse_contest_tables(path)
    rows, errors = [], []
    for title, header, data, totals in contests:
        t = ' '.join(title.split())
        m = re.match(r'^(.*?)\s*\(((?:DEM|REP|LIB|GRN|UST|LIBERTARIAN|'
                     r'GREEN)[A-Za-z ]*)\)\s*\(Vote for \d+\)$', t, re.I)
        party = ''
        if m:
            t, party = m.group(1).strip(), m.group(2).upper()
            if party not in ('DEM', 'REP', 'LIB', 'GRN', 'UST'):
                party = party[:3]
        elif t.endswith(')') and '(Vote for' in t:
            t = re.sub(r'\s*\(Vote for \d+\)$', '', t)
        office, district = map_contest(t, county)
        # candidate columns
        cols = []
        for i, h in enumerate(header[1:] if header else [], start=1):
            name = h.strip()
            if name.lower() == 'write-in':
                name = 'Write-In'
            col_party = party
            m2 = re.match(r'^(.*?)\s*[–-]\s*(DEM|REP|DEMOCRAT|REPUBLICAN)$', name)
            if m2:
                name, col_party = m2.group(1).strip(), {'DEM': 'DEM', 'DEMOCRAT': 'DEM',
                                                        'REP': 'REP', 'REPUBLICAN': 'REP'}[m2.group(2)]
            cols.append((i, name, col_party))
        if totals is not None:
            for i, name, _ in cols:
                if i - 1 < len(totals) and totals[i - 1] != '':
                    got = sum(int(votes[i - 1].strip())
                              for _, votes in data
                              if i - 1 < len(votes) and votes[i - 1].strip())
                    if got != int(totals[i - 1].replace(',', '')):
                        errors.append(f'{title!r} col {name!r}: sum {got} != Total {totals[i - 1]}')
        elif data:
            errors.append(f'{title!r}: no Total row')
        for precinct, votes in data:
            precinct = normalize_precinct(precinct)
            for i, name, col_party in cols:
                v = votes[i - 1].strip() if i - 1 < len(votes) else ''
                if v == '':
                    continue
                rows.append({'county': county, 'precinct': precinct,
                             'office': office, 'district': district,
                             'party': col_party, 'candidate': name,
                             'votes': int(v.replace(',', ''))})
    if errors:
        for e in errors[:20]:
            print('ERROR', e)
        sys.exit(f'{county}: {len(errors)} errors')
    write_csv(county, rows)


def main():
    which = sys.argv[1:] or ['oceana', 'delta']
    if 'oceana' in which:
        parse_html_per_contest('Oceana MI Aug 4 2020 Primary Results per Precinct.html', 'Oceana')
    if 'delta' in which:
        parse_html_per_contest('Delta County Aug 4 2020 Primary Results per Precinct.html', 'Delta')
    if 'calhoun' in which:
        parse_calhoun()


if __name__ == '__main__':
    main()