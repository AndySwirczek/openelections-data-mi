"""Render the Mason 2020 primary PDF and run two tesseract passes per page
(raw + dotted-leader thresholded), caching TSVs under /tmp/mason_ocr/.

The report's dotted leaders read as junk chars and occasionally swallow or
split values, so no single pass is trustworthy; the parser pairs each pass's
lines by y and requires consensus, falling back to the PaddleOCR cache and
finally to visual crops.

Usage: .venv/bin/python src/mason_2020_ocr.py
"""
import glob
import os
import subprocess
import sys

from PIL import Image

PDF = ('/Users/dwillis/code/openelections-sources-mi/2020/primary/'
       'Mason MI Primary.pdf')
OUT = '/tmp/mason_ocr'
TESS = '/opt/homebrew/bin/tesseract'


def run(page):
    png = f'{OUT}/raw/p{page:03d}.png'
    if not os.path.exists(png):
        subprocess.run(['pdftoppm', '-png', '-r', '300', '-f', str(page),
                        '-l', str(page), PDF, f'{OUT}/raw/p'], check=True)
        # pdftoppm names the file from the page range, not the zero-padded
        # number we asked for; normalize.
        for f in glob.glob(f'{OUT}/raw/p-*.png'):
            os.rename(f, png)
    bw = f'{OUT}/bw/p{page:03d}.png'
    if not os.path.exists(bw):
        im = Image.open(png).convert('L')
        im.point(lambda p: 0 if p < 100 else 255).save(bw)
    for tag, src in (('raw', png), ('bw', bw)):
        tsv = f'{OUT}/{tag}/p{page:03d}'
        if not os.path.exists(tsv + '.tsv') or \
                os.path.getsize(tsv + '.tsv') < 1000:
            # tesseract/leptonica only opens images by RELATIVE path in this
            # environment, so chdir into the file's directory
            cwd = os.getcwd()
            os.chdir(f'{OUT}/{tag}')
            try:
                subprocess.run([TESS, f'p{page:03d}.png', f'p{page:03d}',
                                'tsv'], check=True, capture_output=True)
            finally:
                os.chdir(cwd)


def main():
    for d in ('raw', 'bw'):
        os.makedirs(f'{OUT}/{d}', exist_ok=True)
    first, last = 1, 109
    if len(sys.argv) > 2:
        first, last = int(sys.argv[1]), int(sys.argv[2])
    for page in range(first, last + 1):
        run(page)
        print('page', page, flush=True)


if __name__ == '__main__':
    main()