"""Crop contest blocks from the Mason 2020 renders for visual adjudication.

Reads PROBLEM lines (stdin or file arg), extracts (page, contest title),
locates the title line in the tesseract TSVs, and crops the whole contest
block (title line to the next title/statistics/footer line) from the 300dpi
render into /tmp/mason_crops/. Reading the block rather than a single row
also covers paddle-only rows the tesseract passes never saw.

Usage: .venv/bin/python src/mason_crops.py /tmp/mp.txt [page ...]
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mason_2020 import OCR, norm_name, similar, tsv_lines, FOOTER_RE, TITLE_RE

OUT = '/tmp/mason_crops'
STATS_RE = re.compile(r'^Statistics(\s|$)', re.I)


def find_band(lines, title):
    """(y0, y1) of the contest block whose title line best matches."""
    nt = norm_name(title)
    best_i, best_s = None, 0.0
    for i, (toks, y) in enumerate(lines):
        ln = ' '.join(toks)
        s = similar(norm_name(re.sub(r'^(DEM|REP)\s+', '', ln,
                                     flags=re.I)[:40]), nt)
        if s > best_s:
            best_i, best_s = i, s
    if best_i is None or best_s < 0.5:
        return None
    y0 = lines[best_i][1]
    y1 = None
    for toks, y in lines[best_i + 1:]:
        ln = ' '.join(toks)
        if TITLE_RE.match(ln) or STATS_RE.match(ln) or FOOTER_RE.match(ln):
            y1 = y - 6
            break
    return (max(0, y0 - 10), y1)


def crop(page, title):
    for tag in ('bw', 'raw'):
        lines = tsv_lines(f'{OCR}/{tag}/p{page:03d}.tsv')
        band = find_band(lines, title)
        if band:
            from PIL import Image
            im = Image.open(f'{OCR}/raw/p{page:03d}.png')
            y0, y1 = band
            if y1 is None:
                y1 = min(im.height, y0 + 700)
            slug = re.sub(r'[^A-Za-z0-9]+', '_', title)[:40]
            path = f'{OUT}/p{page:03d}_{slug}.png'
            im.crop((0, y0, im.width, y1)).save(path)
            return path
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    text = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    only_pages = {int(p) for p in sys.argv[2:]} or None
    seen = set()
    for line in text.splitlines():
        m = re.match(r'PROBLEM: p(\d{3})', line)
        if not m:
            continue
        page = int(m.group(1))
        if only_pages and page not in only_pages:
            continue
        quotes = re.findall(r"'([^']*)'", line)
        title = quotes[0] if quotes else ''
        if not title or 'value multiset' in line:
            continue
        key = (page, norm_name(title))
        if key in seen:
            continue
        seen.add(key)
        got = crop(page, title)
        print(page, title, '->', got)


if __name__ == '__main__':
    main()