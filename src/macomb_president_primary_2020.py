"""Parse Macomb County's March 10, 2020 presidential primary from the
saved election-site contest pages in openelections-sources-mi/2020/
presidential_primary/"Macomb County Mar 2020 Pres Primary Precinct Pages":

- 10-DEM-bd.html / 11-REP-bd.html: "Race Details" tables -- per
  jurisdiction (class="precinctname" group rows, ALL-CAPS names) a
  header pair (candidate COLSPAN=3 headers with '(DEM)'/'(REP)' suffixes,
  then AV / Elec Day / TOTAL), one row per precinct (AVEDTprecinctnum +
  AVEDTprecinctpbvtotal poll-book total + per-choice AVEDTprecinctAVtotal
  / EDtotal / AVEDtotal), and a jurisdiction TOTALS row
  (AVEDTprecinctpbtotal poll-book total, then per-choice
  AVEDttotal / AVEDttotal / ttotal for AV / Elec Day / TOTAL).
- 10-DEM.html / 11-REP.html: countywide summary pages (verification
  anchors for the presidential candidates).
- 12..17-bd.html: local proposals (No / Yes).

Emission per precinct: the DEM / REP presidential candidates' TOTAL
votes, a partyless Ballots Cast row (the poll-book total, which the site
prints identically in every contest section -- it is precinct-wide, not
per party), and Yes / No proposal rows.  No Registered Voters data.
Checks: AV + Elec Day == TOTAL for every cell, precinct sums equal the
jurisdiction TOTALS rows, and presidential sums equal the summary pages'
Countywide Results (DEM Biden 66,068 / REP Trump 77,948).

Jurisdiction names are mapped to the committed Aug 2020 Macomb file's
'Armada Township Precinct 1' style (Title Case, City suffixes,
zero-padded precinct numbers stripped).
"""
import csv
import re

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/'
       'presidential_primary/Macomb County Mar 2020 Pres Primary '
       'Precinct Pages')
OUT = '2020/counties/20200310__mi__primary__president__macomb__precinct.csv'
COUNTY = 'Macomb'

DEM = ['Amy Klobuchar', 'Andrew Yang', 'Bernie Sanders', 'Cory Booker',
       'Elizabeth Warren', 'Joe Biden', 'Joe Sestak', 'John Delaney',
       'Julian Castro', 'Marianne Williamson', 'Michael Bennet',
       'Michael R. Bloomberg', 'Pete Buttigieg', 'Tom Steyer',
       'Tulsi Gabbard', 'Uncommitted']
REP = ['Bill Weld', 'Donald J. Trump', 'Joe Walsh', 'Mark Sanford',
       'Uncommitted']

CITY = {'CENTER LINE': 'Center Line City', 'EASTPOINTE': 'Eastpointe City',
        'FRASER': 'Fraser City',
        'GROSSE POINTE SHORES': 'Village of Grosse Pointe Shores City',
        'MEMPHIS': 'Memphis City', 'MOUNT CLEMENS': 'Mount Clemens City',
        'NEW BALTIMORE': 'New Baltimore City', 'RICHMOND CITY': 'Richmond City',
        'ROSEVILLE': 'Roseville City',
        'ST. CLAIR SHORES': 'St. Clair Shores City',
        'STERLING HEIGHTS': 'Sterling Heights City', 'UTICA': 'Utica City',
        'WARREN': 'Warren City'}

PROPOSALS = {
    '12-bd.html': 'Macomb County Art Institute Authority - Millage Renewal',
    '13-bd.html': 'City of Center Line - Public Safety Millage Renewal',
    '14-bd.html': ('City of Memphis - Capital Improvement/Capital Project '
                   'Millage'),
    '15-bd.html': 'Clinton Township - Proposed Marihuana Ordinance',
    '16-bd.html': 'Macomb Intermediate School District - Millage Proposal',
    '17-bd.html': 'Lakeview Public Schools - Operating Millage Proposal',
}

ROW_RE = re.compile(
    r'<td class="AVEDTprecinctnum">([^<]*)</td>'
    r'\s*(?:<td class="AVEDTprecinctpbvtotal">([^<]*)</td>\s*)?'
    r'((?:\s*<td class="AVEDTprecinct(?:AV|ED|AVED)total">[^<]*</td>)+)'
    r'\s*<td class="outsideborder"></td>')
CELL_RE = re.compile(
    r'<td class="AVEDTprecinct(AV|ED|AVED)total">([^<]*)</td>')
CHOICE_RE = re.compile(r'COLSPAN=3>(.*?)</th>', re.S)
GROUP_RE = re.compile(r'class="precinctname"><b>([^<]*)</b>')
TOT_CELL_RE = re.compile(
    r'<td class="AVEDTprecinct(pb|AVEDt|t)total">([^<]*)</td>')


def ival(s):
    return int(s.replace(',', ''))


def strip_tags(s):
    return ' '.join(x.strip() for x in re.sub(r'<[^>]+>', ' ', s).split()
                    if x.strip())


def norm_jur(name):
    if name in CITY:
        return CITY[name]
    if name.endswith(' TOWNSHIP'):
        return name[:-len(' TOWNSHIP')].title() + ' Township'
    return name.title()


def parse_details(path):
    """-> {jurisdiction: {'choices': [...], 'precincts': {num: (pbv,
    triples)}, 'totals': (pb, per-choice av/ed/tot)}}."""
    html = open(path, encoding='utf-8', errors='replace').read()
    html = re.sub(r'<!--.*?-->', '', html, flags=re.S)
    # 'na.gif' marks a choice cell with no AV count recorded -- 0
    html = re.sub(r'<img src="na\.gif"[^>]*>', '0', html)
    data = {}
    cur = None
    last_choices = None
    for m in re.finditer(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        row = m.group(1)
        g = GROUP_RE.search(row)
        if g:
            cur = g.group(1)
            data.setdefault(cur, {'precincts': {}, 'choices': last_choices})
            continue
        if 'AVEDTcandname2' in row:
            choices = []
            for c in CHOICE_RE.findall(row):
                c = strip_tags(c).replace(' (DEM)', '').replace(' (REP)', '')
                c = {'Julián Castro': 'Julian Castro'}.get(c, c)
                choices.append(c)
            last_choices = choices
            if cur is not None:
                data[cur]['choices'] = choices
            continue
        if 'AVEDTprecinctpbtotal' in row:
            cells = [ival(v) for _, v in
                     re.findall(r'<td class="AVEDTprecinct(pb|AVEDt|t)total">'
                                r'([^<]*)</td>', row)]
            data[cur]['totals'] = cells
            continue
        rm = ROW_RE.search(row)
        if rm:
            num, pbv, blob = rm.groups()
            cells = [(k, ival(v)) for k, v in CELL_RE.findall(blob)]
            triples = []
            for i in range(0, len(cells) - 2, 3):
                if [cells[i][0], cells[i + 1][0], cells[i + 2][0]] != \
                        ['AV', 'ED', 'AVED']:
                    raise ValueError(f'{path}: cell order {cells[i:i+3]}')
                triples.append((cells[i][1], cells[i + 1][1],
                                cells[i + 2][1]))
            if pbv is None:
                raise ValueError(f'{path} {cur}: no poll-book total')
            data[cur]['precincts'][str(int(num))] = (ival(pbv), triples)
    for jur, d in data.items():
        d.setdefault('choices', choices)
    return data


def main():
    problems = []
    parsed = {}
    for label, fname in (('DEM', '10-DEM-bd.html'), ('REP', '11-REP-bd.html')):
        data = parse_details(f'{SRC}/{fname}')
        parsed[label] = data
        jurs = [j for j in data if j != 'TOTALS']
        for jur in jurs:
            d = data[jur]
            prec = d['precincts']
            if not prec or d.get('choices') is None:
                problems.append(f'{label} {jur}: missing precincts/choices')
                continue
            if d['choices'] != (DEM if label == 'DEM' else REP):
                problems.append(f'{label} {jur}: choices {d["choices"]}')
            for num, (pbv, triples) in prec.items():
                for (av, ed, tot) in triples:
                    if av + ed != tot:
                        problems.append(f'{label} {jur} {num}: {av}+{ed} '
                                        f'!= {tot}')
                if len(triples) != len(d['choices']):
                    problems.append(f'{label} {jur} {num}: {len(triples)} '
                                    f'choices, want {len(d["choices"])}')
            tot = d.get('totals')
            if not tot:
                problems.append(f'{label} {jur}: no TOTALS row')
                continue
            pb, rest = tot[0], tot[1:]
            if pb != sum(v[0] for v in prec.values()):
                problems.append(f'{label} {jur}: poll-book sum != TOTALS '
                                f'{pb}')
            if len(rest) != len(d['choices']) * 3:
                problems.append(f'{label} {jur}: TOTALS {len(rest)} cells, '
                                f'want {len(d["choices"]) * 3}')
                continue
            for i in range(len(d['choices'])):
                got = (sum(v[1][i][0] for v in prec.values()),
                       sum(v[1][i][1] for v in prec.values()),
                       sum(v[1][i][2] for v in prec.values()))
                want = (rest[i * 3], rest[i * 3 + 1], rest[i * 3 + 2])
                if got != want:
                    problems.append(f'{label} {jur} {d["choices"][i]}: '
                                    f'precinct sums {got} != TOTALS {want}')
        # countywide summary anchor
        html = open(f'{SRC}/{fname.replace("-bd", "")}',
                    encoding='utf-8', errors='replace').read()
        i = html.find('Countywide Results')
        seg = html[i:html.find('Congressional District', i)]
        pairs = re.findall(
            r'<td class="candname">\s*(.*?)\s*</td>\s*<td class="party">'
            + label + r'</td>\s*<td class="vtotal">([\d,]+)</td>',
            seg, re.S)
        anchors = {strip_tags(n): ival(v) for n, v in pairs}
        if len(anchors) != (16 if label == 'DEM' else 5):
            problems.append(f'{label} summary: {len(anchors)} names {anchors}')
        for name in (DEM if label == 'DEM' else REP):
            idx = (DEM if label == 'DEM' else REP).index(name)
            s = sum(v[1][idx][2] for jur in jurs
                    for v in data[jur]['precincts'].values()
                    if len(v[1]) > idx)
            anchor_name = 'Julián Castro' if name == 'Julian Castro' else name
            if anchor_name not in anchors:
                problems.append(f'{label}: {name!r} missing from summary')
            elif s != anchors[anchor_name]:
                problems.append(f'{label} {name}: precinct sum {s} != '
                                f'countywide {anchors[anchor_name]}')

    keys = {j: {n: v for n, v in parsed['DEM'][j]['precincts'].items()}
            for j in parsed['DEM'] if j != 'TOTALS'}
    keys_rep = {j: set(parsed['REP'][j]['precincts'])
                for j in parsed['REP'] if j != 'TOTALS'}
    for jur in keys:
        if jur not in keys_rep:
            problems.append(f'jurisdiction {jur} missing from REP')
        elif set(keys[jur]) != keys_rep[jur]:
            problems.append(f'{jur}: DEM nums {sorted(keys[jur])} != REP '
                            f'{sorted(keys_rep[jur])}')

    prop_data = {}
    for fname, title in PROPOSALS.items():
        data = parse_details(f'{SRC}/{fname}')
        parsed[title] = data
        for jur, d in data.items():
            if jur == 'TOTALS':
                continue
            if d['choices'][:2] != ['No', 'Yes'] or len(d['choices']) != 2:
                problems.append(f'{title} {jur}: choices {d["choices"]}')
            for num, (pbv, triples) in d['precincts'].items():
                for av, ed, tot in triples:
                    if av + ed != tot:
                        problems.append(f'{title} {jur} {num}: {av}+{ed} '
                                        f'!= {tot}')
            tot = d.get('totals')
            if tot and tot[0] != sum(v[0] for v in
                                     d['precincts'].values()):
                problems.append(f'{title} {jur}: poll-book sum != TOTALS')
        prop_data[title] = data

    if problems:
        for p in problems:
            print('PROBLEM:', p)
        print(f'{len(problems)} problems; not writing')
        return

    rows = []
    for jur in sorted(j for j in parsed['DEM'] if j != 'TOTALS'):
        dd, rr = parsed['DEM'][jur], parsed['REP'][jur]
        for num in sorted(dd['precincts'], key=int):
            label = f'{norm_jur(jur)} Precinct {num}'
            pbv, dem_tri = dd['precincts'][num]
            _, rep_tri = rr['precincts'][num]
            for name, (av, ed, tot) in zip(dd['choices'], dem_tri):
                rows.append([COUNTY, label, 'President', '', 'DEM', name,
                             tot])
            for name, (av, ed, tot) in zip(rr['choices'], rep_tri):
                rows.append([COUNTY, label, 'President', '', 'REP', name,
                             tot])
            rows.append([COUNTY, label, 'Ballots Cast', '', '', '', pbv])
            for title, data in prop_data.items():
                if jur in data and num in data[jur]['precincts']:
                    _, tri = data[jur]['precincts'][num]
                    for name, (av, ed, tot) in zip(data[jur]['choices'],
                                                   tri):
                        rows.append([COUNTY, label, title, '', '', name,
                                     tot])
    with open(OUT, 'w', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes'])
        w.writerows(rows)
    print(f'wrote {OUT}: {len(rows)} rows')


if __name__ == '__main__':
    main()