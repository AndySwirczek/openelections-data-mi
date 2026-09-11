"""Parse the Saginaw 2020 primary 'Results per Precinct Data Report' PDF text
(pdftotext -layout of 'Saginaw MI August 2020 Results per Precinct Data
Report.pdf', 126 pages, 85 precincts) into the per-county precinct CSV.

Format: contest-major tables. Each contest has a title line (optionally
suffixed '(DEM)'/'(REP)'), a header row 'Precinct <candidate cols> Total
Write-ins' (or 'Yes No' for proposals), one row per precinct with a leading
index number, and a 'Total' row of column sums. Tables can span page breaks;
continuation pages repeat the header row with no title. A tail section
(~page 90 on) carries write-in-only tables (sole column 'Total Write-ins') for
contests whose listed candidates drew no votes and that did not appear in the
main sections.

Source typos fixed: 'County Commissionerr', 'James Twp. trustee', 'fi'
ligatures (Blumfield/Jonesfield/Lakefield).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from csv_2020_primary import write_csv

TXT = '/tmp/saginaw.txt'

TITLE_FIXES = (
    ('ﬁ', 'fi'),
    ('ﬂ', 'fl'),
    ('County Commissionerr', 'County Commissioner'),
    ('Twp. trustee', 'Twp. Trustee'),
)

FOOTER_RE = re.compile(r'^\s*\d+/\d+\s*$')
HEADER_RE = re.compile(r'^\s*Precinct\s{2,}(\S.*)$')
DATA_RE = re.compile(r'^\s{0,6}(\d{1,2})\s{2,}(\S.*?)\s{2,}(.+)$')
TOTAL_RE = re.compile(r'^\s*Total\s{2,}(.+)$')


def clean_title(t):
    t = ' '.join(t.split())
    for old, new in TITLE_FIXES:
        t = t.replace(old, new)
    return t


def split_title(t):
    """(office_raw, party) from a title line."""
    m = re.match(r'^(.*?)\s*\((DEM|REP)\)$', t)
    if m:
        return m.group(1).strip(), m.group(2)
    return t, ''


def map_office(office_raw):
    """(office, district, precinct_override) per repo conventions."""
    o = office_raw
    m = re.match(r'Representative in Congress (\d+)(?:st|nd|rd|th) District$', o)
    if m:
        return 'U.S. House', m.group(1), None
    m = re.match(r'State Representative (\d+)(?:st|nd|rd|th) District$', o)
    if m:
        return 'State House', m.group(1), None
    if o == 'United States Senator':
        return 'U.S. Senate', '', None
    m = re.match(r'County Commissioner District (\d+)$', o)
    if m:
        return 'County Commissioner', m.group(1), None
    m = re.match(r'Delegate for (.+)$', o)
    if m:
        return f'{m.group(1)} Delegate to County Convention', '', m.group(1)
    m = re.match(r'(.+?) Twp\. (.+)$', o)
    if m:
        return f'{m.group(1)} Township {m.group(2)}', '', None
    return o, '', None


def parse_candidates(header_rest):
    cols = re.split(r'\s{2,}', header_rest.strip())
    out = []
    for c in cols:
        if re.match(r'Total Write-?ins$', c, re.I) or re.match(r'Write-?ins$', c, re.I):
            out.append('Write-In')
        else:
            out.append(c)
    return out


def parse():
    with open(TXT) as fh:
        lines = fh.read().splitlines()

    # pass 1: locate title lines (a title is a non-footer text line followed,
    # past blanks/page furniture, by a 'Precinct ...' header row)
    def is_page_furniture(line):
        return (not line.strip()) or FOOTER_RE.match(line) or \
            line.strip() == 'Saginaw County'

    title_at = set()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or FOOTER_RE.match(s) or s == 'Saginaw County' or \
                HEADER_RE.match(line) or DATA_RE.match(line) or \
                TOTAL_RE.match(line):
            continue
        j = i + 1
        while j < len(lines) and is_page_furniture(lines[j]):
            j += 1
        if j < len(lines) and HEADER_RE.match(lines[j]):
            title_at.add(i)

    contests = []   # {title, party, candidates, rows: {precinct: [ints]}, total}
    pending_title = None
    cur = None
    for i, line in enumerate(lines):
        if i in title_at:
            pending_title = clean_title(line)
            continue
        hm = HEADER_RE.match(line)
        if hm:
            cands = parse_candidates(hm.group(1))
            if pending_title is not None:
                cur = {'title': pending_title, 'candidates': cands,
                       'rows': {}, 'total': None}
                contests.append(cur)
                pending_title = None
            else:
                if cur is None:
                    sys.exit(f'line {i + 1}: header with no open contest')
                if cur['candidates'] != cands:
                    sys.exit(f'line {i + 1}: continuation header mismatch for '
                             f'{cur["title"]!r}: {cands} != {cur["candidates"]}')
            continue
        if cur is None:
            continue
        dm = DATA_RE.match(line)
        if dm:
            idx, name, rest = dm.groups()
            vals = [int(v.replace(',', '')) for v in re.split(r'\s{2,}', rest.strip())]
            if len(vals) != len(cur['candidates']):
                sys.exit(f'line {i + 1}: {len(vals)} values for '
                         f'{len(cur["candidates"])} candidates in {cur["title"]!r}: {line!r}')
            if name in cur['rows']:
                sys.exit(f'line {i + 1}: duplicate precinct row {name!r} in {cur["title"]!r}')
            cur['rows'][name] = vals
            continue
        tm = TOTAL_RE.match(line)
        if tm:
            vals = [int(v.replace(',', '')) for v in re.split(r'\s{2,}', tm.group(1).strip())]
            if len(vals) != len(cur['candidates']):
                sys.exit(f'line {i + 1}: bad Total row in {cur["title"]!r}')
            cur['total'] = vals

    # validation: every contest's Total row equals the column sums
    errors = []
    for c in contests:
        if c['total'] is None:
            errors.append(f'{c["title"]!r}: no Total row')
            continue
        for k, cand in enumerate(c['candidates']):
            got = sum(v[k] for v in c['rows'].values())
            if got != c['total'][k]:
                errors.append(f'{c["title"]!r} col {cand!r}: sum {got} != Total {c["total"][k]}')

    counts = {len(c['rows']) for c in contests}
    print(f'{len(contests)} contests, precinct-row counts: {sorted(counts)}')

    # build rows
    out = []
    for c in contests:
        office_raw, party = split_title(c['title'])
        office, district, prec_override = map_office(office_raw)
        for prec, vals in c['rows'].items():
            precinct = prec_override or prec
            for cand, votes in zip(c['candidates'], vals):
                out.append({'county': 'Saginaw', 'precinct': precinct,
                            'office': office, 'district': district,
                            'party': party, 'candidate': cand, 'votes': votes})
    write_csv('Saginaw', out)

    titles = sorted({c['title'] for c in contests})
    with open('/tmp/saginaw_titles.txt', 'w') as fh:
        fh.write('\n'.join(titles))
    if errors:
        print(f'{len(errors)} VALIDATION ERRORS:')
        for e in errors[:40]:
            print(' ', e)
        sys.exit(1)
    print('all Total-row validations pass')


if __name__ == '__main__':
    parse()