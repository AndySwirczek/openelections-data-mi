"""Parse Gladwin County's 2020 primary canvass/summary PDF (OCR markdown).

Source: openelections-sources-mi 2020/primary/Gladwin County Aug 2020 Primary
Canvass and Election Summary Report.pdf (66pp, image-only; PaddleOCR markdown
cache in /tmp/paddleocr_md/Gladwin_County_Aug_2020_Primary_Canvass_and_
Election_Summary_Report).  Pages 1-14 are the Board of Canvassers
certification; p15 onward is an ES&S "Election Summary Report" whose tables
are COUNTYWIDE (or district/township) AGGREGATES — "Precincts Reported: 0 of
N" gives the contest's precinct scope, and per-precinct numbers exist only
where the scope is 1 (Mason 2020 situation).  Gladwin's 18 precincts are 15
single-precinct townships + City of Gladwin 1-2 + City of Beaverton 1, so the
emittable contests are the township offices, the per-precinct delegate races
and the 11 township proposals; U.S. Senate/House, State House 97th, county
offices and County Commissioner districts (scopes 3-5/18) are aggregates and
are skipped — the file therefore has no federal/state rows (CENR comparison
is empty by design, same as Mason/Alger).

Each contest block: title line (contains "(Vote for"; party from an inline
(DEM)/(REP) tag or a following standalone DEM/REP word/line), a "Precincts
Reported: 0 of N" line, then one HTML table whose cells read [label, value]:
Times Cast, candidate rows (name/party/total), Total Votes, Unresolved
Write-In.  Total Votes EXCLUDES unresolved write-ins, so per contest Total
Votes == sum(candidates) validates, and raw Unresolved becomes a "Write-In"
row (Alger 2020 precedent).  Offices: "<Township> Township <Role>"
(jurisdiction-first, Alger style); delegates "<jur>, Precinct N Delegate to
County Convention" (Alger/Mason style); proposals keep the printed title as
office with Yes/No and blank party, mapped to the township named by their
first word (all 11 match the certification list on pp2-7).  Ballots Cast
pseudo rows come from Times Cast (no Registered Voters exists in this
format); precincts are single-precinct so no RV column is needed.
"""
import glob
import html
import re
import sys
from collections import defaultdict

from csv_2020_primary import write_csv

CACHE = ('/tmp/paddleocr_md/'
         'Gladwin_County_Aug_2020_Primary_Canvass_and_Election_Summary_Report')
COUNTY = 'Gladwin'

TD_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
SCOPE_RE = re.compile(r'Precincts Reported: 0 of (\d+)')


def cells(line):
    out = []
    for td in TD_RE.findall(line):
        text = html.unescape(re.sub(r'<[^>]+>', '', td))
        out.append(re.sub(r'\s+', ' ', text).strip())
    return out


def parse_contests(errors):
    """(page, title, party, scope, times_cast, cands, total, unresolved)"""
    contests = []
    for path in sorted(glob.glob(CACHE + '/p0*.md')):
        pno = int(re.search(r'p(\d+)\.md$', path).group(1))
        lines = [l.strip() for l in open(path) if l.strip()]
        i = 0
        while i < len(lines):
            line = lines[i]
            clean = line.lstrip('#').strip()
            if '(Vote for' not in clean:
                i += 1
                continue
            title = clean
            m = re.search(r'\((DEM|REP)\)', title)
            party = m.group(1) if m else None
            title = re.sub(r'\s*\((?:DEM|REP)\)', '', title).strip()
            # trailing standalone party marker in the title line (OCR fuses
            # the party marker onto the same line even with an inline tag)
            tm = re.search(r'\b(DEM|REP)$', title)
            if tm:
                party = tm.group(1)
                title = ' '.join(title.split()[:-1])
            title = re.sub(r'\s*\(Vote for \d+\)\s*$', '', title).strip()
            scope = None
            j = i + 1
            while j < len(lines) and scope is None:
                if '(Vote for' in lines[j]:
                    break
                sm = SCOPE_RE.search(lines[j])
                if sm:
                    scope = int(sm.group(1))
                elif party is None and lines[j] in ('DEM', 'REP'):
                    party = lines[j]
                j += 1
            table = None
            while j < len(lines) and table is None:
                if '(Vote for' in lines[j] and scope is not None:
                    break
                if lines[j].startswith('<table'):
                    table = lines[j]
                    while not table.rstrip().endswith('</table>'):
                        j += 1
                        table += lines[j]
                j += 1
            if scope is None or table is None:
                errors.append(f'page {pno}: contest {title!r} missing '
                              f'{"scope" if scope is None else "table"}')
                i = j
                continue
            contests.append(parse_table(pno, title, party, scope, table,
                                        errors))
            i = j
    return contests


def cellnum(c):
    """Cell value: plain digits, or a fused '448 / 0' / '200 / 0 N/A'."""
    c = c.replace('N/A', ' ').strip()
    m = re.match(r'^([\d,]+)(?:\s*/\s*[\d,]+)?$', c)
    return int(m.group(1).replace(',', '')) if m else None


def parse_table(pno, title, party, scope, table, errors):
    times_cast = total = unresolved = None
    cands = []
    for row in table.split('<tr>')[1:]:
        cs = [c for c in cells(row) if c]
        if not cs:
            continue
        # OCR fuses label/value/headers differently per row: classify each
        # cell as numeric-or-not, join the non-numeric ones into the label
        nums = [n for n in (cellnum(c) for c in cs) if n is not None]
        label = ' '.join(c for c in cs if cellnum(c) is None
                         and c not in ('N/A', 'Party', 'Total', 'Candidate'))
        if not nums:
            continue  # header rows ('Candidate Party Total', bare 'Total')
        if 'Times Cast' in label:
            times_cast = nums[0]
        elif 'Total Votes' in label:
            total = nums[0]
        elif 'Unresolved' in label:
            unresolved = nums[0]
        elif label:
            cands.append((label, nums[0]))
        else:
            errors.append(f'page {pno} {title!r}: unlabeled value row {cs}')
    return dict(page=pno, title=title, party=party, scope=scope,
                times_cast=times_cast, cands=cands, total=total,
                unresolved=unresolved)


def contest_key(c):
    """Contest -> (precinct, office) for emittable (scope-1) contests."""
    title = c['title']
    m = re.match(r'Precinct Delegate for (.+), Precinct (\d+)$', title)
    if m:
        jur, n = m.groups()
        return (f'{jur}, Precinct {n}',
                f'{jur}, Precinct {n} Delegate to County Convention')
    m = re.match(r'Township (Supervisor|Clerk|Treasurer|Trustee|Constable) '
                 r'for (.+)$', title)
    if m:
        role, twp = m.groups()
        return (f'{twp}, Precinct 1', f'{twp} {role}')
    # proposal: first word names the township
    twp = title.split()[0]
    return (f'{twp} Township, Precinct 1', title)


def main():
    errors = []
    contests = parse_contests(errors)
    emit = [c for c in contests if c['scope'] == 1]
    skipped = defaultdict(int)
    for c in contests:
        if c['scope'] != 1:
            skipped[c['scope']] += 1

    # ---- validation ----
    aux = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for c in emit:
        if c['total'] is None:
            errors.append(f'page {c["page"]} {c["title"]}: no Total Votes')
        s = sum(v for _, v in c['cands'])
        if c['total'] != s:
            errors.append(f'page {c["page"]} {c["title"]}: Total '
                          f'{c["total"]} != candidate sum {s}')
        if c['times_cast'] is None:
            errors.append(f'page {c["page"]} {c["title"]}: no Times Cast')
        precinct, office = contest_key(c)
        aux[precinct]['Ballots Cast'][c['times_cast']] += 1
        if c['party'] is None and {n for n, _ in c['cands']} != {'Yes', 'No'}:
            errors.append(f'page {c["page"]} {c["title"]}: no party')
    for c in emit:
        if {n for n, _ in c['cands']} == {'Yes', 'No'}:
            precinct, _ = contest_key(c)
            yn = {name: v for name, v in c['cands']}
            if set(yn) != {'Yes', 'No'}:
                errors.append(f'page {c["page"]} {c["title"]}: proposal '
                              f'candidates {sorted(yn)}')
            elif sum(yn.values()) != c['total']:
                errors.append(f'page {c["page"]} {c["title"]}: Yes+No '
                              f'{sum(yn.values())} != Total {c["total"]}')
    for precinct, cols in sorted(aux.items()):
        for col, counts in cols.items():
            if len(counts) > 1:
                errors.append(f'{precinct}: inconsistent {col}: {dict(counts)}')

    # ---- emission ----
    rows = []
    precincts = {}
    for c in emit:
        precinct, office = contest_key(c)
        party = c['party'] or ''
        for name, v in c['cands']:
            rows.append(dict(county=COUNTY, precinct=precinct, office=office,
                             district='', party='' if name in ('Yes', 'No')
                             else party, candidate=name, votes=v))
        if c['unresolved']:
            rows.append(dict(county=COUNTY, precinct=precinct, office=office,
                             district='', party=party, candidate='Write-In',
                             votes=c['unresolved']))
        precincts[precinct] = True
    for precinct in sorted(precincts):
        counts = aux[precinct]['Ballots Cast']
        rows.append(dict(county=COUNTY, precinct=precinct,
                         office='Ballots Cast', district='', party='',
                         candidate='',
                         votes=max(counts.items(), key=lambda kv: kv[1])[0]))

    if errors:
        print('ERRORS:')
        for e in errors[:40]:
            print(' ', e)
        print(f'total errors: {len(errors)}')
        sys.exit(1)
    print(f'skipped aggregate contests by scope: {dict(skipped)}')
    write_csv(COUNTY, rows)
    print(f'{len(rows)} rows, {len(emit)} contests, '
          f'{len(precincts)} precincts')


if __name__ == '__main__':
    main()