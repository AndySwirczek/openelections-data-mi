"""Parse Branch County Aug 2024 "Results per Precinct" PDF into a precinct CSV.

The PDF is a stream of contest blocks, each block = a title line
'<office> (DEM|REP) (Vote for N)', a header line starting 'Precinct' listing
runs of '<candidate name> - <PARTY>', one data row per precinct
('<precinct label> <one number per candidate>'), and a closing 'Total' row
with the countywide numbers.  Blocks continue across pages.  Number columns
sit at x >= ~190 while labels end by x ~140, and baselines jitter ~0.3pt
within a row, so lines are grouped with a 1.5pt top tolerance and then
x-sorted (which puts each row's label before its numbers).

Candidate names come BEFORE their '- <PARTY>' tag; words after the final tag
are untagged write-in candidates that fill the remaining number columns in
order ('Tiffanie Vargo', 'Mark Forrester Lucis Shadix').

Known rendering defects (checked against Branch County's enhancedvoting
reporting of the same election, which carries these contests plus write-in
totals):
- a '- REP' tag sometimes prints merged with the next candidate's first name
  ('REPRandy Hollister' = '- REP' + 'Randy Hollister')
- tag/name runs can collide so their glyphs interleave ('RGEaPry' = 'REP' +
  'Gary'); the three affected headers are supplied verbatim in MANUAL_HEADERS
- write-in votes are never reported anywhere in the PDF (write-in-only
  contests print all-zero candidate-less blocks), so they are omitted
- empty contests (no candidates) are skipped
- a trailing 'BRANCH COUNTY POLLBOOK TOTAL' table is not contest data
"""
import csv
import re
import sys

import pdfplumber

PDF = ('/Users/dwillis/code/openelections-sources-mi/2024/primary/'
       'Branch County Aug 2024 Primary Precinct Results.pdf')
OUT = '2024/counties/20240806__mi__primary__branch__precinct.csv'
COUNTY = 'Branch'

# page no (1-based) -> title -> candidate list standing in for the garbled
# header (verified against enhancedvoting's candidate names and vote counts)
MANUAL_HEADERS = {
    29: {'Bronson Township, Precinct 1, Precinct Delegate': [
        'Seth Butters', 'Jesse D. Himebaugh', 'Randy Hollister',
        'John Lindsey', 'Julia Lindsey', 'Mark Forrester', 'Lucis Shadix']},
    30: {'Coldwater Township, Precinct 1, Precinct Delegate': [
        'Ken Delaney', 'Scott Kirkpatrick', 'Chancelir Murdock',
        'Cheryl Stechschulte', 'Gary Stechschulte', 'Jerry VanBlarcom',
        'Letha M. VanBlarcom'],
        'Girard Township, Precinct 1, Precinct Delegate': [
        'Marygrace Nagy', 'Eryika Leigh-Mendelsohn Tucker',
        'Megan Uetrecht']},
}

# title -> (office, district)
OFFICE_MAP = {
    'United States Senator': ('U.S. Senate', ''),
    'County Prosecuting Attorney for Branch County':
        ('County Prosecuting Attorney', ''),
    'County Sheriff for Branch County': ('County Sheriff', ''),
    'County Clerk for Branch County': ('County Clerk', ''),
    'County Treasurer for Branch County': ('County Treasurer', ''),
    'County Register of Deeds for Branch County': ('County Register of Deeds', ''),
    'County Drain Commissioner for Branch County': ('County Drain Commissioner', ''),
    'County Surveyor for Branch County': ('County Surveyor', ''),
}
ORD = {'1st': '1', '2nd': '2', '3rd': '3', '4th': '4', '5th': '5',
       '6th': '6', '7th': '7', '8th': '8', '35th': '35'}
NUM_RE = re.compile(r'^\d+$')
TITLE_RE = re.compile(r'^(.*?)\s*\((DEM|REP)\)\s*\(Vote for \d+\)$')
PROPOSAL_RE = re.compile(r'^(.*?)\s*\(Vote for \d+\)$')
COMMA_PARTY_RE = re.compile(r'^(REP|DEM)([A-Z][A-Za-z.\'\-]*)$')


def lines_of(page):
    """Words grouped into lines (1.5pt top tolerance), x-sorted within."""
    out = []
    for w in sorted(page.extract_words(), key=lambda w: (w['top'], w['x0'])):
        if out and abs(w['top'] - out[-1][0]) < 1.5:
            out[-1][1].append(w)
        else:
            out.append([w['top'], [w]])
    for _, ws in out:
        ws.sort(key=lambda w: w['x0'])
    return out


def parse_header(ws, page_no, title, problems):
    """Header words after the leading 'Precinct' -> (candidates, tail_words).

    Names precede their '- <PARTY>' tag; a merged token like 'REPRandy' ends
    the current name and starts the next one with 'Randy'.  Words after the
    final tag are returned separately as untagged tail candidates.
    """
    fixed = MANUAL_HEADERS.get(page_no, {}).get(title)
    if fixed:
        return list(fixed), []
    tokens = [w['text'] for w in ws]
    if tokens == ['Yes', 'No']:     # proposal header: no party tags at all
        return ['Yes', 'No'], []
    cands, cur, tail = [], [], []
    for t in tokens:
        if t == '-':
            continue
        if t in ('REP', 'DEM'):
            cands.append(' '.join(cur))
            cur = []
            continue
        m = COMMA_PARTY_RE.match(t)
        if m:                       # merged tag + next candidate's first name
            cands.append(' '.join(cur))
            cur = [m.group(2)]
            continue
        cur.append(t.rstrip('-'))   # a tag hyphen can merge into the name
    tail = cur          # words after the final tag: untagged candidates
    return cands, tail


def map_office(title):
    office, district = OFFICE_MAP.get(title, (title, ''))
    m = re.match(r'^County Commissioner (\w+) District$', title)
    if m:
        office, district = 'County Commissioner', ORD[m.group(1)]
    m = re.match(r'^Representative in Congress (\w+) District$', title)
    if m:
        office, district = 'U.S. House', ORD[m.group(1)]
    m = re.match(r'^Representative in State Legislature (\w+) District$', title)
    if m:
        office, district = 'State House', ORD[m.group(1)]
    m = re.match(r'^(.*), Precinct (\d+), Precinct Delegate$', title)
    if m:
        office = (f'{m.group(1)}, Precinct {m.group(2)} '
                  'Delegate to County Convention')
        district = ''
    return office, district


def main():
    problems = []
    rows = []
    cur = None          # dict(title, party, office, district, candidates,
                        #      tail, sums, n_data_rows)
    pdf = pdfplumber.open(PDF)
    done = False
    for pi, page in enumerate(pdf.pages, 1):
        if done:
            break
        for top, ws in lines_of(page):
            texts = [w['text'] for w in ws]
            text = ' '.join(texts)
            if 'POLLBOOK TOTAL' in text:
                done = True
                break
            m = TITLE_RE.match(text)
            if m:
                if cur:
                    problems.append(f'p{pi}: contest {cur["title"]} closed by '
                                    f'title without Total')
                office, district = map_office(m.group(1))
                cur = {'title': m.group(1), 'party': m.group(2),
                       'office': office, 'district': district,
                       'candidates': [], 'tail': [], 'sums': None}
                continue
            m = PROPOSAL_RE.match(text)
            if m and not re.search(r'\((DEM|REP)\)', text):
                if cur:
                    problems.append(f'p{pi}: contest {cur["title"]} closed by '
                                    f'title without Total')
                cur = {'title': m.group(1), 'party': '',
                       'office': m.group(1), 'district': '',
                       'candidates': [], 'tail': [], 'sums': None}
                continue
            if texts[0] == 'Precinct':
                if cur is None:
                    problems.append(f'p{pi}: header without contest')
                    continue
                cur['candidates'], cur['tail'] = parse_header(
                    ws[1:], pi, cur['title'], problems)
                continue
            if texts[0] == 'Total':
                if cur is None:
                    problems.append(f'p{pi}: Total without contest')
                    continue
                nums = [t for t in texts[1:] if NUM_RE.match(t)]
                if not cur['candidates']:
                    # write-in-only contest: PDF prints all-zero columns
                    pass
                elif cur['sums'] is not None:
                    if len(nums) != len(cur['sums']):
                        problems.append(
                            f'p{pi} {cur["title"]}: Total has {len(nums)} '
                            f'numbers, contest has {len(cur["sums"])} columns')
                    else:
                        for n, s in zip(nums, cur['sums']):
                            if int(n) != s:
                                problems.append(
                                    f'p{pi} {cur["title"]}: Total {n} != '
                                    f'parsed sum {s}')
                cur = None
                continue
            nums = [(w['x0'], w['text']) for w in ws
                    if w['x0'] > 160 and NUM_RE.match(w['text'])]
            labels = ' '.join(w['text'] for w in ws if w['x0'] <= 160)
            if not nums:
                if labels and cur is not None and not cur['candidates']:
                    continue    # candidate-less rows can print no number
                if labels and not TITLE_RE.match(text) and \
                        not PROPOSAL_RE.match(text):
                    problems.append(f'p{pi}: unclassified line {text!r}')
                continue
            if cur is None:
                if labels:      # a data row always carries a label; label-less
                    problems.append(    # number lines are report-header noise
                        f'p{pi}: data row {labels!r} without contest')
                continue
            if not cur['candidates']:
                # write-in-only contest: a lone all-zero column, no candidates
                continue
            nums = [n for _, n in nums]
            if cur['tail']:
                need = len(nums) - len(cur['candidates'])
                words = cur['tail']
                if need == 1:
                    cur['candidates'].append(' '.join(words))
                elif need > 1 and len(words) % need == 0:
                    k = len(words) // need
                    cur['candidates'] += [' '.join(words[i:i + k])
                                          for i in range(0, len(words), k)]
                else:
                    problems.append(
                        f'p{pi} {cur["title"]}: {need} tail columns for '
                        f'tail words {words}')
                cur['tail'] = []
            cands = cur['candidates']
            if cur['sums'] is None:
                cur['sums'] = [0] * len(cands)
            if len(nums) != len(cands):
                problems.append(f'p{pi} {cur["title"]}: {labels!r} has '
                                f'{len(nums)} numbers, {len(cands)} '
                                f'candidates')
                continue
            for i, (c, n) in enumerate(zip(cands, nums)):
                cur['sums'][i] += int(n)
                rows.append([COUNTY, labels, cur['office'], cur['district'],
                             cur['party'], c, n])
    if cur:
        problems.append(f'unclosed contest {cur["title"]}')
    with open(OUT, 'w', newline='') as fh:
        wtr = csv.writer(fh)
        wtr.writerow(['county', 'precinct', 'office', 'district', 'party',
                      'candidate', 'votes'])
        wtr.writerows(rows)
    print(f'{len(problems)} problems')
    for p in problems[:40]:
        print('PROBLEM:', p)
    print(f'Wrote {len(rows)} rows to {OUT}')


if __name__ == '__main__':
    sys.exit(main())