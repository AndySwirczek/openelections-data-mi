"""Parse the Charlevoix County 2020 primary per-jurisdiction summary PDFs.

Same report family as the 2024 primary (see src/charlevoix_2024_parser.py):
one "Election Summary Report" PDF per jurisdiction (each township/city is a
single precinct; the City of Charlevoix wards are Precincts 18-20), contest
blocks of title / party / Times Cast / candidate rows / Total Votes /
Unresolved Write-In. The 2020 reports are scans with a garbled embedded text
layer ('Igl' for 151, '3-13' for 313, dropped values), so every file is read
from cached PaddleOCR markdown (src/fetch_paddleocr_md.py) — the text layer
is never trusted.

2020-specific differences from the 2024 reports:
- Page-1 header lines carry the turnout: 'Registered Voters: 563 of 1,546'
  (563 ballots cast of 1,546 registered) and 'Ballots Cast: 563'; every
  contest's Times Cast repeats them.
- Contest titles are '<Office> for <Jurisdiction>' ('Supervisor for Wilson
  Township'); county offices drop the ' for Charlevoix County' suffix,
  township/city offices become '<Jurisdiction> <Office>', delegates become
  '<precinct label> Delegate' (2024-file convention), and 'County
  Commissioner for Co Comm 3rd District' becomes County Commissioner 3.
- The 'Charlevoix County Aug 2020 Primary Election Summary (countywide,
  summary-only).pdf' report repeats EVERY contest: once aggregated over all
  counting groups (countywide offices, county proposals) and once per
  jurisdiction (township offices, township millages, delegates). Its
  per-jurisdiction blocks are merged into the same-named file contest as a
  second OCR reading — where the file's value is missing it is filled from
  the summary, and disagreements are reported.

The source set is missing the Boyne Township and Charlevoix Township
reports. Their township-specific contests are recovered from the summary's
per-jurisdiction blocks (each is a single precinct); their votes in the
countywide contests are only present inside the county aggregates and
cannot be recovered per candidate, so countywide contests stay short by
those two townships' votes (each remaining diff is printed as a NOTE).

Usage:
    .venv/bin/python src/charlevoix_2020.py
"""
import os
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from charlevoix_2024_parser import HEADER, PARTY_LINES, parse  # noqa: E402
from csv_2020_primary import write_csv  # noqa: E402

SRC = '/Users/dwillis/code/openelections-sources-mi/2020/primary'

# source PDF basename -> precinct label (committed 2020 general-file labels)
PRECINCTS = {
    'Charlevoix County Aug 2020 Primary Bay Twp.pdf': 'Bay Township',
    'Charlevoix County Aug 2020 Primary Boyne City.pdf': 'City of Boyne City',
    'Charlevoix County Aug 2020 Primary Boyne Valley Twp.pdf':
        'Boyne Valley Township',
    'Charlevoix County Aug 2020 Primary Chandler Twp.pdf':
        'Chandler Township',
    'Charlevoix County Aug 2020 Primary City of Charlevoix Ward 1.pdf':
        'City of Charlevoix, Precinct 18',
    'Charlevoix County Aug 2020 Primary City of Charlevoix Ward 2.pdf':
        'City of Charlevoix, Precinct 19',
    'Charlevoix County Aug 2020 Primary City of Charlevoix Ward 3.pdf':
        'City of Charlevoix, Precinct 20',
    'Charlevoix County Aug 2020 Primary East Jordan.pdf':
        'City of East Jordan',
    'Charlevoix County Aug 2020 Primary Evangeline Twp.pdf':
        'Evangeline Township',
    'Charlevoix County Aug 2020 Primary Eveline Twp.pdf': 'Eveline Township',
    'Charlevoix County Aug 2020 Primary Hayes Twp.pdf': 'Hayes Township',
    'Charlevoix County Aug 2020 Primary Hudson Twp.pdf': 'Hudson Township',
    'Charlevoix County Aug 2020 Primary Marion Twp.pdf': 'Marion Township',
    'Charlevoix County Aug 2020 Primary Melrose Twp.pdf': 'Melrose Township',
    'Charlevoix County Aug 2020 Primary Norwood Twp.pdf': 'Norwood Township',
    'Charlevoix County Aug 2020 Primary Peaine Twp.pdf': 'Peaine Township',
    'Charlevoix County Aug 2020 Primary South Arm Twp.pdf':
        'South Arm Township',
    'Charlevoix County Aug 2020 Primary St. James Twp.pdf':
        'St. James Township',
    'Charlevoix County Aug 2020 Primary Wilson Twp.pdf': 'Wilson Township',
}
SUMMARY = ('Charlevoix County Aug 2020 Primary Election Summary '
           '(countywide, summary-only).pdf')

# Unresolved Write-In values where the file report and the summary block
# disagree: both OCR readings garble the same small print, so each was
# adjudicated by hand from the rendered page image of the file report
# (pdftoppm -r 150). {(title, tag, precinct): confirmed value}
MANUAL_WI = {
    ('Supervisor for Hayes Township', 'DEM', 'Hayes Township'): 16,
    ('Supervisor for Hayes Township', 'REP', 'Hayes Township'): 3,
    ('Supervisor for Marion Township', 'REP', 'Marion Township'): 3,
    ('Clerk for Norwood Township', 'DEM', 'Norwood Township'): 1,
    ('Clerk for Norwood Township', 'REP', 'Norwood Township'): 10,
    ('Clerk for Boyne Valley Township', 'DEM', 'Boyne Valley Township'): 10,
    ('Clerk for Evangeline Township', 'DEM', 'Evangeline Township'): 7,
    ('Treasurer for Eveline Township', 'DEM', 'Eveline Township'): 9,
    # delegate write-ins, same adjudication (page images of the file reports)
    ('Delegate for City of Boyne City, Precinct 16', 'REP',
     'City of Boyne City'): 59,
    ('Delegate for City of Charlevoix, Ward 1, Precinct 18', 'REP',
     'City of Charlevoix, Precinct 18'): 21,
    ('Delegate for City of Charlevoix, Ward 2, Precinct 19', 'DEM',
     'City of Charlevoix, Precinct 19'): 1,
    ('Delegate for Evangeline Township, Precinct 5', 'DEM',
     'Evangeline Township'): 6,
    ('Delegate for Hudson Township, Precinct 8', 'DEM',
     'Hudson Township'): 5,
    ('Delegate for Peaine Township, Precinct 12', 'REP',
     'Peaine Township'): 6,
    ('Delegate for Wilson Township, Precinct 15', 'REP',
     'Wilson Township'): 24,
}

# Contests whose block the OCR destroyed in BOTH the file report and the
# countywide summary (no second reading exists). Values read by hand from
# the rendered page image / the exploded OCR line, cross-checked against the
# countywide aggregate where one exists
# ({(title, tag, precinct): {'ballots', 'rows', 'total', 'wi'}}).
MANUAL_CONTESTS = {
    # Norwood Twp p6-7: the county Clerk/Surveyor (REP) titles were dropped
    # by the OCR; the tables follow the Sheriff / Drain Commissioner blocks.
    ('Clerk for Charlevoix County', 'REP', 'Norwood Township'):
        {'ballots': 298, 'rows': [('Julia A. Drost', 148)],
         'total': 148, 'wi': 1},
    ('Surveyor for Charlevoix County', 'REP', 'Norwood Township'):
        {'ballots': 298, 'rows': [('Lawrence Rowen Feindt', 139)],
         'total': 139, 'wi': 1},
    # Hayes Twp: the U.S. House (REP) block lost its title to latex glue.
    ('Representative in Congress 1st District for Charlevoix County',
     'REP', 'Hayes Township'):
        {'ballots': 727, 'rows': [('Jack Bergman', 408)],
         'total': 408, 'wi': 2},
    # Exploded proposal blocks (title after the data line); Yes+No equals
    # the Total and the countywide aggregate (64 / 482) confirms both.
    ('Chandler Township Fire Millage and Emergency Medical Services '
     'Proposal', '', 'Chandler Township'):
        {'ballots': 76, 'rows': [('Yes', 52), ('No', 12)],
         'total': 64, 'wi': 0},
    ('Marion Township Road Millage Proposal', '', 'Marion Township'):
        {'ballots': 533, 'rows': [('Yes', 383), ('No', 99)],
         'total': 482, 'wi': 0},
}

COUNTY = 'Charlevoix'

# Page-1 header lines (also the NOISE set for the contest state machine).
REGISTERED = re.compile(r'^Registered Voters:? [\d,]+ of ([\d,]+)')
BALLOTS = re.compile(r'^Ballots Cast:? ([\d,]+)$')
NOISE = re.compile(
    r'^(?:Page[:;]? \d+ of \d+|Election Summary Report|Open Primary'
    r'|Charlevoix County, Michigan|August 0\d, 2020|Summary for:'
    r'|Precincts Reported|Voters Cast:|Registered Voters|Ballots Cast'
    r'|Candid[a-z]+[;: ]*(?:Party.*)?Total|Party Total'
    # 2020 OCR fragments: table-header remnants and garbled
    # 'Precincts Reported' lines
    r'|Times Cast( Party)? Total$|Candidate Party$|Times Cast$'
    r'|\w+s? Reported|Total /$|[^\d]*Total$|\d\.? ?Total$'
    # a 'Total Votes' label whose number the OCR never produced (prep only
    # rejoins a bare number on the NEXT line), a stray 'Candidate' header
    # cell and a stray percentage column
    r'|Total Votes$|Candidate$|\d+\.\d+%$'
    r')')


def prep(lines):
    """Normalize the 2020 OCR line variants into the 2024 line grammar.

    - pipe-delimited cells ('Total Votes | 0 |') and latex \\quad glue are
      flattened;
    - a Times Cast row keeps only its 'N / M' turnout (the label may carry
      junk: 'Times Cast Total 365 / 1,246 29.29%');
    - a bare 'Total Votes' / 'Unresolved Write-In' whose number landed on
      the next line is rejoined to it (exploded tables put every cell on
      its own line), as is 'Unresolved Write-In Total' followed by its
      number;
    - 'Unresolved Write-In' garbles ('n', '. Total') read as 0 — the
      countywide-summary cross-check validates the value.
    """
    out = []
    i = 0
    while i < len(lines):
        line = re.sub(r'\s*\|\s*', ' ', lines[i])
        line = re.sub(r'\\quad\b', ' ', line)
        line = re.sub(r'\s+', ' ', line).strip()
        if line == 'DFM':
            line = 'DEM'      # OCR garble of the party line
        if line.startswith('Times Cast'):
            m = re.search(r'(\d[\d,]*)\s*/\s*(\d[\d,]*)', line)
            line = f'{m.group(1)} / {m.group(2)}' if m else 'Times Cast'
        if line == 'Unresolved Write-In n' or \
                line == 'Unresolved Write-In . Total':
            line = 'Unresolved Write-In 0'
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ''
        if line in ('Total Votes', 'Unresolved Write-In') and \
                re.fullmatch(r'[\d,]+', nxt):
            line = f'{line} {nxt}'
            i += 1
        elif line == 'Unresolved Write-In Total':
            line = f'Unresolved Write-In {nxt}' if re.fullmatch(r'[\d,]+', nxt) \
                else 'Unresolved Write-In 0'
            if re.fullmatch(r'[\d,]+', nxt):
                i += 1
        out.append(line)
        i += 1
    return out


def ocr_lines(path):
    """Lines of a PDF from cached per-page PaddleOCR markdown (required —
    the 2020 reports' embedded text layer is garbage)."""
    import html
    stem = re.sub(r'[^A-Za-z0-9]+', '_',
                  os.path.splitext(os.path.basename(path))[0])
    cache_dir = os.path.join('/tmp/paddleocr_md', stem)
    pages = sorted(f for f in os.listdir(cache_dir) if f.endswith('.md')) \
        if os.path.isdir(cache_dir) else []
    if not pages:
        return None
    lines = []
    for f in pages:
        for raw in open(os.path.join(cache_dir, f)):
            line = raw.strip().lstrip('#').strip()
            if not line or line.startswith('```') or line == '---':
                continue
            if '<table' in line:
                for table in re.findall(r'<table.*?</table>', line, re.S):
                    for row in re.findall(r'<tr.*?</tr>', table, re.S):
                        cells = [html.unescape(
                                     re.sub(r'<[^>]+>', '', c)).strip()
                                 for c in re.findall(r'<td[^>]*>(.*?)</td>',
                                                     row, re.S)]
                        line = ' '.join(c for c in cells if c)
                        if line:
                            lines.append(line)
                continue
            line = re.sub(r'<[^>]+>', '', line)
            line = html.unescape(line).strip()
            if line:
                lines.append(line)
    return lines


KNOWN_JURS = {'Charlevoix County', 'City of Boyne City', 'City of East Jordan',
              'City of Charlevoix', 'Charlevoix Township', 'Boyne Township'} | \
    {t for t in PRECINCTS.values() if t.endswith('Township')}


def split_title(title, problems, where):
    """'<Office> for <Jurisdiction>' -> (office_part, jurisdiction, party).

    Party is a fallback tag salvaged from a trailing '(DEM)'/'(REP)' the OCR
    left inside the title (the '(Vote for N)' token was on its own line, so
    the 2024 state machine kept the tag in the title).

    Proposals have no ' for <jurisdiction>' part, and the county-
    commissioner titles read 'County Commissioner for Co Comm 3rd District'.
    OCR garbles of the word 'for' ('Sherifff or ...') are cleaned first;
    delegate titles carry a ', Precinct N' suffix that is not part of the
    jurisdiction, and 'United States Senator for State' has no county."""
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'^Sherif+\w*\s+or\b', 'Sheriff for', title)
    tag = ''
    m = re.search(r' \((DEM|REP)\)$', title)
    if m:
        tag = m.group(1)
        title = title[:m.start()]
    if title.startswith('Delegate'):
        return 'Delegate', '', tag
    if re.match(r'^County Commissioner (?:for )?Co ?Comm', title) \
            or 'Proposal' in title:
        return title, '', tag
    pos = title.rfind(' for ')
    if pos < 0:
        return title, '', tag          # proposals
    jur = title[pos + len(' for '):].strip()
    jur = re.sub(r',? Precinct \d+$', '', jur)
    if jur in ('State', 'Charlevoix County') or jur in KNOWN_JURS:
        return title[:pos].strip(), jur, tag
    problems.append(f'{where}: title {title!r} has unknown jurisdiction '
                    f'{jur!r}')
    return title, '', tag


def block_precinct(title, problems, where):
    """Precinct label owning a summary block titled `title`, or None for a
    county-level block (county offices, 'for State', county proposals)."""
    # delegate titles: split_title's Delegate early-return drops the
    # jurisdiction, so parse it from the raw title. A delegate contest is
    # voted on only by its own precinct('s jurisdiction), so the block is
    # that precinct's second OCR reading like any township-office block.
    m = re.match(r'^Delegate for (.+?), (?:Ward (\d+), )?Precinct (\d+)$',
                 title)
    if m:
        if m.group(1) == 'City of Charlevoix':
            return f'City of Charlevoix, Precinct {m.group(3)}'
        return m.group(1)
    office_part, jur, _ = split_title(title, problems, where)
    if jur in ('Charlevoix County', 'State', ''):
        return None
    if jur != 'City of Charlevoix':
        return jur
    m = re.search(r', Ward (\d+)', title)
    if not m:
        return 'City of Charlevoix'
    return f'City of Charlevoix, Precinct {17 + int(m.group(1))}'


DISTRICT = re.compile(r'^Representative in (Congress|State Legislature) '
                      r'(\d+)(?:st|nd|rd|th|ist|nd|rd|th)? District$')
OFFICE_EXACT = {'United States Senator': 'U.S. Senate'}
# OCR merges '1st' into 'ist' and drops the space in 'Co Comm 3rd District'.
COMM = re.compile(r'^County Commissioner (?:for )?Co ?Comm\.? '
                  r'?(\d+)(?:st|nd|rd|th|ist)? District$')


def map_office(office_part, problems, where):
    office, district = office_part, ''
    m = COMM.match(office_part)
    if m:
        return 'County Commissioner', m.group(1)
    m = DISTRICT.match(office_part)
    if m:
        return ('U.S. House' if m.group(1) == 'Congress' else 'State House'), \
            m.group(2)
    if office_part in OFFICE_EXACT:
        return OFFICE_EXACT[office_part], ''
    if office_part == 'Delegate':
        return 'Precinct Delegate', ''
    return office, ''


def clean_contests(contests, name, problems):
    """Post-parse fixups shared by the files and the summary."""
    # 'Unresolved Write-In 0' can become a bogus contest title when it
    # arrives after the real contest already closed.
    contests = {k: c for k, c in contests.items()
                if not k.startswith('Unresolved Write-In')}
    out = {}
    for k, c in contests.items():
        # an exploded proposal block (title lost by the OCR) lands as one
        # giant pseudo-title; the summary's per-jurisdiction block
        # recovers its data
        if re.match(r'^[\d,]+ / [\d,]+ Yes \d+ No \d+ Total Votes \d+', c['title']):
            problems.append(f'{name}: dropped exploded proposal block '
                            f'{c["title"]!r}')
            continue
        _, _, tag = split_title(c['title'], [], name)
        if not c['party'] and tag:
            c['party'] = tag
        if c['title'].endswith((' (DEM)', ' (REP)')):
            # keep the summary cross-check keys identical to the
            # summary's own clean titles
            c['title'] = re.sub(r' \((?:DEM|REP)\)$', '', c['title'])
        out[k] = c
    return out


def read_report(path, problems, where):
    """OCR-parse one PDF -> ({(title|tag): contest}, (registered, ballots)).

    Every contest in one of these reports repeats the page-1 Times Cast
    (each file is a single jurisdiction), so a contest whose Times Cast row
    was lost by the OCR takes the header turnout."""
    lines = ocr_lines(path)
    if lines is None:
        problems.append(f'{where}: no OCR cache')
        return {}, (None, None)
    rv = bc = None
    for line in lines:
        m = REGISTERED.match(line)
        if m and rv is None:
            rv = int(m.group(1).replace(',', ''))
        m = BALLOTS.match(line)
        if m:
            bc = int(m.group(1).replace(',', ''))
    # prep first: it rejoins numbers to their labels ('Unresolved Write-In
    # Total' + '13'), which the NOISE filter would otherwise tear apart
    lines = prep(lines)
    lines = [l for l in lines
             if not (NOISE.match(l) or REGISTERED.match(l)
                     or BALLOTS.match(l))]
    before = len(problems)
    contests = clean_contests(parse(lines, problems, where), where,
                              problems)
    # 'unwrapped title lines' = a contest whose title the OCR dropped: the
    # data lines are orphaned, but the countywide summary block (or a
    # MANUAL_CONTESTS entry) carries the same contest, so report as a NOTE.
    # Same for the companions of an exploded proposal block ('never closed',
    # 'unexpected line') whose values MANUAL_CONTESTS hand-enters.
    tail = problems[before:]
    del problems[before:]
    for p in tail:
        if ': unwrapped title lines ' in p or \
                re.search(r': (contest|unexpected line in contest) '
                          r"'[\d,]+ / [\d,]+ Yes \d+ No \d+ Total Votes", p) \
                or 'dropped exploded proposal block' in p:
            print(f'NOTE {p}', file=sys.stderr)
        else:
            problems.append(p)
    for c in contests.values():
        if c['ballots'] is None and bc is not None:
            print(f'NOTE {where}: {c["title"]}|{c["tag"]}: Times Cast lost '
                  f'by OCR, using header turnout', file=sys.stderr)
            c['ballots'], c['registered'] = bc, rv
    return contests, (rv, bc)


def main():
    problems = []
    all_contests = {}      # title -> [(precinct, contest dict)]
    turnout = {}           # precinct -> (registered, ballots)

    for name in PRECINCTS:
        path = os.path.join(SRC, name)
        name_ = os.path.basename(path)
        contests, (rv, bc) = read_report(path, problems, name_)
        if rv is None or bc is None:
            problems.append(f'{name_}: header turnout not found '
                            f'(rv={rv}, bc={bc})')
        else:
            turnout[PRECINCTS[name_]] = (rv, bc)
        for key, c in contests.items():
            all_contests.setdefault(c['title'], []).append(
                (PRECINCTS[name_], c))

    # contests neither source read cleanly (see MANUAL_CONTESTS)
    for (title, tag, prec), vals in MANUAL_CONTESTS.items():
        c = {'key': f'{title}|{tag}', 'title': title, 'tag': tag,
             'vote_for': '(Vote for 1)', 'party': tag,
             'ballots': vals['ballots'], 'registered': None,
             'rows': vals['rows'], 'total': vals['total'],
             'wi': vals['wi']}
        all_contests.setdefault(title, []).append((prec, c))
        print(f'NOTE {title}|{tag} / {prec}: hand-entered '
              f'(both OCR readings unusable)', file=sys.stderr)

    # The countywide summary: per-jurisdiction blocks are a second OCR
    # reading of each file's contests; county-level blocks cross-check the
    # aggregates.
    s_contests, _ = read_report(os.path.join(SRC, SUMMARY), problems,
                                SUMMARY)
    per_jur, county_level = {}, {}
    for c in s_contests.values():
        prec = block_precinct(c['title'], problems, SUMMARY)
        if prec is None:
            county_level.setdefault(c['title'], []).append(c)
        else:
            per_jur.setdefault((c['title'], c['tag']), {})[prec] = c

    # merge the summary's second reading into the file contests
    def rows_match(a, b):
        """Same candidate rows modulo one OCR misspelling (both sources
        garble names independently; the page images showed e.g. 'Cortright'
        vs 'Corright')."""
        return len(a) == len(b) and all(
            va == vb and SequenceMatcher(None, na.casefold(),
                                         nb.casefold()).ratio() >= 0.8
            for (na, va), (nb, vb) in zip(a, b))

    consumed = set()
    for title, entries in all_contests.items():
        for prec, c in entries:
            s = per_jur.get((title, c['tag']), {}).get(prec)
            if s is None:
                continue
            consumed.add((title, c['tag'], prec))
            for f in ('total', 'wi', 'ballots'):
                if f == 'wi' and (title, c['tag'], prec) in MANUAL_WI:
                    # hand-confirmed from the page image; skip the conflict
                    c['wi'] = MANUAL_WI[(title, c['tag'], prec)]
                    continue
                if c[f] is None and s[f] is not None:
                    c[f] = s[f]
                elif c[f] is not None and s[f] is not None \
                        and c[f] != s[f]:
                    problems.append(f'{title}|{c["tag"]} / {prec}: file '
                                    f'{f} {c[f]} != summary {s[f]}')
            if not c['rows'] and s['rows']:
                c['rows'] = s['rows']
            elif c['rows'] and s['rows'] and c['rows'] != s['rows'] \
                    and not rows_match(c['rows'], s['rows']):
                problems.append(f'{title}|{c["tag"]} / {prec}: candidate '
                                f'rows differ: file {c["rows"]} vs summary '
                                f'{s["rows"]}')

    # Boyne Township and Charlevoix Township have no report in the source
    # set; their township-specific contests come from the summary blocks.
    # The same fallback recovers a file contest whose block the OCR
    # garbled beyond repair (the exploded proposal blocks).
    for (title, tag), by_prec in sorted(per_jur.items()):
        for prec, s in list(by_prec.items()):
            if (title, tag, prec) in consumed:
                continue
            file_has = any(p == prec and c['tag'] == tag
                           for p, c in all_contests.get(title, []))
            if not file_has:
                consumed.add((title, tag, prec))
                all_contests.setdefault(title, []).append((prec, s))
                print(f'NOTE {title}|{tag} / {prec}: recovered from the '
                      f'summary block (no usable file contest)',
                      file=sys.stderr)
                if prec not in turnout and s['ballots'] is not None:
                    turnout[prec] = (s['registered'], s['ballots'])
            else:
                problems.append(f'{title}|{tag} / {prec}: summary block '
                                f'not merged (file contest exists)')

    # county-level cross-checks: the aggregate covers all 20 jurisdictions,
    # so the residual is Boyne + Charlevoix Township (no reports); report
    # it as a NOTE, not an error.
    for title in sorted(county_level):
        for s in county_level[title]:
            parts = [(p, c) for p, c in all_contests.get(title, [])
                     if c['tag'] == s['tag']]
            for field in ('total', 'wi', 'ballots'):
                if s[field] is None:
                    continue
                got = sum(c[field] for _, c in parts
                          if c[field] is not None)
                missing = [p for p, c in parts if c[field] is None]
                if missing:
                    problems.append(f'{title}|{s["tag"]}: missing {field} '
                                    f'in {missing}')
                if not missing and got != s[field]:
                    print(f'NOTE {title}|{s["tag"]}: files sum {field} '
                          f'{got} vs county {s[field]} (diff '
                          f'{s[field] - got}: Boyne/Charlevoix Township '
                          f'+ residual OCR error)', file=sys.stderr)
    # per-file internal checks
    for title in sorted(all_contests):
        for prec, c in all_contests[title]:
            if c['wi'] is None or c['total'] is None or c['ballots'] is None:
                problems.append(f'{title}|{c["tag"]} / {prec}: incomplete '
                                f'contest {c}')
                continue
            cand = sum(v for _, v in c['rows'])
            if cand != c['total']:
                problems.append(f'{title}|{c["tag"]} / {prec}: candidates '
                                f'{cand} != Total Votes {c["total"]}')
            if c['vote_for'] in ('(Vote for 1)', 'Vote for 1') and \
                    c['total'] > c['ballots']:
                problems.append(f'{title}|{c["tag"]} / {prec}: Total Votes '
                                f'{c["total"]} > Times Cast {c["ballots"]}')

    votes_agg = {}
    for title in sorted(all_contests):
        for prec, c in all_contests[title]:
            office_part, jur, _tag = split_title(c['title'], problems, prec)
            office, district = map_office(office_part, problems, prec)
            if office == 'Precinct Delegate':
                # 2024-file convention: '<precinct label> Delegate'
                office, district = f'{prec} Delegate', ''
            if jur not in ('Charlevoix County', 'State', '') \
                    and not office_part.startswith('County Commissioner'):
                office = f'{jur} {office}'
            for name_, v in c['rows']:
                name_ = re.sub(r'\s*WRITE-?IN\s*$', '', name_, flags=re.I)
                if not v:
                    continue
                key = (prec, office, district, c['party'], name_)
                votes_agg[key] = votes_agg.get(key, 0) + v
            if c['wi']:
                key = (prec, office, district, c['party'], 'Write-In')
                votes_agg[key] = votes_agg.get(key, 0) + c['wi']
    rows = [[COUNTY, prec, office, district, party, name_, votes]
            for (prec, office, district, party, name_), votes in
            sorted(votes_agg.items())]
    for prec, (rv, bc) in sorted(turnout.items()):
        rows.append([COUNTY, prec, 'Registered Voters', '', '', '', rv])
        rows.append([COUNTY, prec, 'Ballots Cast', '', '', '', bc])

    write_csv(COUNTY, [dict(zip(HEADER, r)) for r in rows])
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)
    print(f'{len(problems)} problems', file=sys.stderr)


if __name__ == '__main__':
    main()