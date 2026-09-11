"""Parse the Grand Traverse County Aug 2020 primary "OFFICIAL RESULTS"
Electionware PDF into a precinct CSV.

Source: 'Grand Traverse MI Official Results August 4, 2020
Election_202008130736259876.pdf' (openelections-sources-mi/2020/primary),
62 pages. Pages 1-2 are STATISTICS (Registered Voters / Ballots Cast /
turnout per precinct); pages 2-62 hold contest tables, contest-major, 1-3
side-by-side blocks per page. Each block's header is a title band (wrapped
over up to 3 rows, e.g. 'Ambulance Millage Renewal' / 'Blair Township'),
a 'VOTE FOR n' row, then rotated (bottom-up) candidate-name cells 42pt
wide. Candidate names wrap to multiple vertical lines within a cell;
'Write-in Totals', 'Write-in: Not Assigned' and 'Contest Total' are
fixed columns at the right of each block. Page pairs split the precinct
list and repeat the headers; each block ends with a countywide 'Totals'
row used to validate every column sum.

Layout quirks handled:
- Rotated headers are one-letter elements; strings are rebuilt by x-line
  with a space inserted on bottom-to-top gaps >= 1.4pt (the word gap is a
  constant 1.83pt; glyph heights vary, so top-to-top gaps do not work) and
  lines joined in descending-x order ('Write-in: Not' / 'Assigned').
- 'Write-in Totals' and 'Contest Total' columns are validated but not
  emitted; 'Write-in: Not Assigned' becomes candidate 'Write-in' and
  'Write-in: <name>' columns are emitted under the name (Electionware
  lists them only when qualified).
- Township office titles ('Supervisor Acme', 'Park Commissioner East Bay
  Township') are rewritten to '<Full Township> <Office>' using the
  township spellings from the STATISTICS precinct labels.
- Contest Total = votes cast in the contest (not a candidate sum, since
  undervotes exist), so it is only checked as an upper bound.

Usage: .venv/bin/python src/grand_traverse_2020.py
"""
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csv_2020_primary import write_csv, map_office  # noqa: E402

SRC = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Grand Traverse MI Official Results August 4, 2020 '
       'Election_202008130736259876.pdf')

COUNTY = 'Grand Traverse'


def rows_by_top(page):
    grouped = {}
    for e in page.find_all('text'):
        t = e.extract_text()
        if t is None or not t.strip():
            continue
        key = round(e.top)
        for k in grouped:
            if abs(k - key) <= 2:
                key = k
                break
        grouped.setdefault(key, []).append(
            (e.x0, e.x1, e.top, e.bottom, t.strip()))
    return {top: sorted(v) for top, v in grouped.items()}


def column_cells(page):
    """Deduped header cells (rotated names): [(x0, x1, top, bottom)]."""
    seen = {}
    for r in page.find_all('rect'):
        w, h = r.x1 - r.x0, r.bottom - r.top
        if 38 <= w <= 46 and 45 <= h <= 60:
            key = (round(r.x0, 1), round(r.top, 1))
            seen[key] = (r.x0, r.x1, r.top, r.bottom)
    return sorted(seen.values())


def title_bands(page, cell_top):
    """Title/vote-for band rects just above the rotated cells ->
    {x-extent: top of its topmost band}."""
    exts = {}
    for r in page.find_all('rect'):
        w, h = r.x1 - r.x0, r.bottom - r.top
        if 11 <= h <= 15 and 100 <= w <= 300 and r.x0 >= 150 \
                and cell_top - 70 <= r.bottom <= cell_top + 2:
            key = (round(r.x0, 1), round(r.x1, 1))
            exts[key] = min(exts.get(key, 999), r.top)
    return sorted(exts.items())


def blocks(cells, exts):
    """Split cells into contest blocks at the band boundaries."""
    out = []
    for c in cells:
        center = (c[0] + c[1]) / 2
        for i, ((bx0, bx1), _) in enumerate(exts):
            if bx0 <= center <= bx1:
                if len(out) <= i:
                    out.append([])
                out[i].append(c)
                break
    return [((c[0][0], c[-1][1]), c) for c in out if c]


def vlines(elems):
    """Letters inside a cell -> vertical lines grouped by x0, each joined
    into a string with a space on bottom-to-top word gaps."""
    lines = {}
    for x0, x1, top, bot, t in elems:
        key = round(x0)
        for k in lines:
            if abs(k - key) <= 3:
                key = k
                break
        lines.setdefault(key, []).append((top, bot, t))
    out = []
    for x0 in sorted(lines, reverse=True):   # wrap lines stack right-to-left
        chars = sorted(lines[x0])
        text = ''
        prev_bot = None
        for top, bot, t in chars:
            if prev_bot is not None and top - prev_bot >= 1.4:
                text += ' '
            text += t
            prev_bot = bot
        out.append(text)
    return out


def block_text(page, y0, y1, bx0, bx1):
    """Title text inside an x/y window. Some pages carry two block titles in
    one text element ('DEM Clerk Long Lake Township DEM Clerk Mayfield
    Township'); those are split per character at the block boundary."""
    parts = []
    for e in page.find_all('text'):
        t = e.extract_text()
        if t is None or not t.strip() or not (y0 <= e.top < y1):
            continue
        if e.x0 >= bx0 - 2 and e.x1 <= bx1 + 2:
            parts.append((e.top, e.x0, t.strip()))
        elif e.x0 < bx1 + 2 and e.x1 > bx0 - 2:
            chars = [c for c in e.chars if bx0 <= (c.x0 + c.x1) / 2 <= bx1]
            if chars:
                parts.append((e.top, chars[0].x0,
                              ''.join(c.text for c in chars)))
    parts.sort()
    return ' '.join(t for _, _, t in parts)


def main():
    import natural_pdf
    pdf = natural_pdf.PDF(SRC)
    errors = []
    notes = []

    # ---- township full names from the STATISTICS precinct labels ---------
    townships = set()
    stats = {}      # precinct -> [registered voters, ballots cast]
    for page in pdf.pages[:2]:
        for top, elems in sorted(rows_by_top(page).items()):
            texts = [t for _, _, _, _, t in elems]
            lab = ' '.join(texts)
            nums = [t for t in texts if re.match(r'^\d[\d,]*$', t)]
            if re.search(r'\b(Precinct|Township)\b', lab) and len(nums) == 2:
                name = ' '.join(t for t in texts
                                if not re.match(r'^[\d,%.]+$', t))
                lm = re.match(r'^(.+?), Precinct \d+$', name)
                if lm:
                    townships.add(lm.group(1).strip())
                stats[name] = [int(nums[0].replace(',', '')),
                               int(nums[1].replace(',', ''))]
    short_towns = sorted(
        {re.sub(r'\s+(Charter\s+)?Township$', '', t) for t in townships},
        key=len, reverse=True)
    if len(stats) != 36:
        errors.append(f'statistics rows: {len(stats)} != 36')

    # ---- contest tables --------------------------------------------------
    rows_out = []
    contest_sums = defaultdict(int)   # (contest, party, column) -> parsed sum
    dup_contests = set()              # contests the source prints twice
    for pageno, page in enumerate(pdf.pages, 1):
        rows = rows_by_top(page)
        cells = column_cells(page)
        if not cells:
            continue
        # group cells by header-cell top (one row of blocks per table)
        tables = {}
        for c in cells:
            key = round(c[2])
            for k in tables:
                if abs(k - key) <= 3:
                    key = k
                    break
            tables.setdefault(key, []).append(c)
        for cell_top in sorted(tables):
            if pageno <= 2 and cell_top < 180:
                continue          # STATISTICS header cells on pages 1-2
            tcells = sorted(tables[cell_top])
            exts = title_bands(page, cell_top)
            if not exts:
                exts = [((tcells[0][0], tcells[-1][1]), cell_top - 80)]
            # a (contest, party) printed twice on the same page pair (the
            # source repeats the REP U.S. Senate blocks): keep the first,
            # drop the duplicate
            seen_contests = {}
            max_cbot = max(c[3] for c in tcells)
            # this table's data rows end where the next table's header begins
            next_tops = [t for t in sorted(tables) if t > cell_top]
            row_limit = next_tops[0] - 40 if next_tops else 10 ** 9

            # ---- header blocks: contest titles + rotated-name columns --------
            allcols = []       # {'x0','x1','kind'} across all blocks
            blocks_ok = []     # {'rest','party','idxs'} per block
            for (bx0, bx1), bcells in blocks(tcells, exts):
                band_top = next((t for (b0, b1), t in exts
                                 if b0 <= (bx0 + bx1) / 2 <= b1), cell_top - 80)
                # title: text between the block's topmost band and the cells
                title = block_text(page, band_top, cell_top - 2, bx0, bx1)
                vote_for = re.search(r'VOTE FOR (\d+)', title)
                title = re.sub(r'\s*VOTE FOR \d+\s*', ' ', title).strip()
                if not title:
                    # stepped header pages (e.g. commissioner districts +
                    # township supervisors side by side): this block's title
                    # sits above its VOTE FOR band; page 62's proposal titles
                    # reach up to 5 lines. Keep clear of the page header
                    # (rows 22-48).
                    title = block_text(page, max(band_top - 90, 55), band_top,
                                       bx0, bx1)
                if not title:
                    errors.append(f'page {pageno}: block {bx0}-{bx1} no title')
                    continue
                m = re.match(r'^(DEM|REP)\s+(.*)$', title)
                if m:
                    party, rest = m.group(1), m.group(2).strip()
                else:
                    party, rest = '', title          # proposals
                idxs = []
                for cx0, cx1, ctop, cbot in bcells:
                    letters = [(x0, x1, etop, ebot, t)
                               for top, elems in rows.items()
                               if ctop - 1 <= top <= cbot + 1
                               for x0, x1, etop, ebot, t in elems
                               if cx0 <= (x0 + x1) / 2 <= cx1 and len(t) <= 3]
                    text = ' '.join(vlines(letters))
                    if re.search(r'Write-?in\s*Totals', text, re.I):
                        kind = 'wi_total'
                    elif re.search(r'Not\s*Assigned', text, re.I):
                        kind = 'wi_na'
                    elif re.search(r'Contest\s*Total', text, re.I):
                        kind = 'contest_total'
                    elif re.match(r'^Write-?in[:\s]', text, re.I):
                        kind = 'wi_named:' + re.sub(r'^Write-?in:\s*', '', text,
                                                    flags=re.I).strip()
                    else:
                        kind = 'cand:' + text
                    idxs.append(len(allcols))
                    allcols.append({'x0': cx0, 'x1': cx1, 'kind': kind})
                block = {'rest': rest, 'party': party, 'idxs': idxs,
                         'dup': None}
                if (rest, party) in seen_contests:
                    notes.append(f'page {pageno}: duplicate contest block '
                                 f'{rest!r}; rows validated, first kept')
                    dup_contests.add((rest, party))
                    block['dup'] = seen_contests[(rest, party)]
                else:
                    seen_contests[(rest, party)] = block
                blocks_ok.append(block)

            # ---- data rows below the header cells (one pass per table) -------
            for top, elems in sorted(rows.items()):
                if top <= max_cbot or top >= row_limit:
                    continue
                label = ' '.join(t for x0, x1, _, _, t in elems if x1 <= 160)
                values = {}
                for x0, x1, _, _, t in elems:
                    if not re.match(r'^\d[\d,]*$', t):
                        continue
                    c = (x0 + x1) / 2
                    hit = [i for i, col in enumerate(allcols)
                           if col['x0'] <= c <= col['x1']]
                    if len(hit) != 1:
                        errors.append(f'page {pageno} {label!r}: value {t} '
                                      f'at {c:.0f} hits {len(hit)} columns')
                        continue
                    values[hit[0]] = int(t.replace(',', ''))
                if label == 'Totals':
                    # countywide: validated against the sums accumulated over
                    # this contest's page pair
                    for b in blocks_ok:
                        if (b['rest'], b['party']) in dup_contests:
                            # the duplicated contest's printed Totals row does
                            # not agree with its own precinct rows; verified
                            # against the CENR county file instead
                            continue
                        for i in b['idxs']:
                            col = allcols[i]
                            kind = col['kind']
                            if kind == 'contest_total':
                                continue
                            parsed = contest_sums.get(
                                (b['rest'], b['party'], kind), 0)
                            if i in values and values[i] != parsed:
                                errors.append(
                                    f"page {pageno} {b['rest']!r} {kind}: "
                                    f"Totals {values[i]} != parsed {parsed}")
                            elif i not in values and parsed:
                                errors.append(
                                    f"page {pageno} {b['rest']!r} {kind}: "
                                    f"no Totals value, parsed {parsed}")
                    continue
                if not re.search(r'\b(Precinct|Township)\b', label) \
                        or label not in stats:
                    if label:
                        errors.append(f'page {pageno}: bad row label '
                                      f'{label!r}')
                    continue
                for b in blocks_ok:
                    if b['dup'] is not None:
                        # repeated block: values must match the first copy
                        for j, i in zip(b['dup']['idxs'], b['idxs']):
                            if values.get(i, 0) != values.get(j, 0):
                                errors.append(
                                    f'page {pageno} {label!r} {b["rest"]!r}: '
                                    f'duplicate block value {values.get(i, 0)} '
                                    f'!= {values.get(j, 0)}')
                        continue
                    for i in b['idxs']:
                        kind = allcols[i]['kind']
                        v = values.get(i, 0)
                        contest_sums[(b['rest'], b['party'], kind)] += v
                        if kind in ('wi_total', 'contest_total'):
                            continue
                        if kind == 'wi_na':
                            cand = 'Write-in'
                        elif kind.startswith('cand:'):
                            cand = kind[len('cand:'):]
                        else:
                            cand = kind.split(':', 1)[1]
                        rows_out.append((label, b['rest'], b['party'], cand, v))

    if errors:
        for e in errors[:30]:
            print('ERROR', e)
        sys.exit(f'Grand Traverse: {len(errors)} errors')
    for n in notes:
        print('NOTE', n)

    # ---- township office titles ------------------------------------------
    TWP_OFFICES = {'Supervisor', 'Clerk', 'Treasurer', 'Trustee',
                   'Park Commissioner', 'Constable', 'Drain Commissioner'}
    out = []
    for precinct, title, party, cand, votes in rows_out:
        office, district = map_office(title, '', COUNTY)
        if office == title:
            # township offices: 'Supervisor Acme' -> 'Acme Township Supervisor'
            base = re.sub(r'\s+Township$', '', title)
            for short in short_towns:
                if base.lower().endswith(short.lower()) and \
                        len(base) > len(short):
                    office_word = base[:-len(short)].strip()
                    if office_word in TWP_OFFICES:
                        full = next(t for t in townships
                                    if re.sub(r'\s+(Charter\s+)?Township$',
                                              '', t).lower() == short.lower())
                        office = f'{full} {office_word}'
                    break
        out.append({'county': COUNTY, 'precinct': precinct,
                    'office': office, 'district': district,
                    'party': party, 'candidate': cand, 'votes': votes})

    for precinct, (rv, bc) in sorted(stats.items()):
        out.append({'county': COUNTY, 'precinct': precinct,
                    'office': 'Registered Voters', 'district': '',
                    'party': '', 'candidate': '', 'votes': rv})
        out.append({'county': COUNTY, 'precinct': precinct,
                    'office': 'Ballots Cast', 'district': '',
                    'party': '', 'candidate': '', 'votes': bc})
    write_csv(COUNTY, out)


if __name__ == '__main__':
    main()