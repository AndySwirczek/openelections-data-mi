"""Parse image-only ES&S SOVC PDFs whose contest pages are flat tables.

Layout (Luce, Oscoda 2024 primary): page 1 is a countywide turnout summary;
each contest then occupies one page (or a page pair) with two tables: an
auxiliary turnout table (Precinct / Times Cast / Registered Voters) and a
results table (Precinct / candidates... / Total Votes [, Unresolved
Write-In]). Luce merges both tables into one wide table whose data rows
repeat the precinct label between the two halves. Rows are
"<precinct label> | <values>"; "<County> County Michigan - Total" and
"County - Total" footer rows give the countywide column totals (used for
cross-checks); "Cumulative" rows are a zero block to skip. Zero-candidate
contests print only a Total Votes column; a contest with more candidates
than fit on one page splits its results table across pages (the Total Votes
column appears only on the last page).

No per-method breakdown exists in these files, so the CSV carries the plain
seven-column header; Times Cast becomes the 'Ballots Cast' row. OCR garbles
(split header cells, wrapped labels, merged rows) are handled by per-county
NAME_FIXES and MANUAL_TABLES (hand-transcribed page rows).

Usage:
    .venv/bin/python src/sovc_flat_ocr_parser.py <County> [--out <csv>]
"""
import argparse
import csv
import html
import os
import re
import sys

CACHE = '/tmp/paddleocr_md'
HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
          'votes']

# Montmorency 2020: every countywide contest reprints the same
# Times Cast / Registered Voters table; manualized pages copy it.
_MONT_AUX9 = [
    ['Precinct', 'Times Cast', 'Registered Voters'],
    ['County', '', ''],
    ['Montmorency County Michigan', '', ''],
    ['Albert Township, Precinct 1', '903', '2,170'],
    ['Avery Township, Precinct 1', '210', '569'],
    ['Briley Township, Precinct 1', '644', '1,715'],
    ['Hillman Township, Precinct 1', '659', '1,637'],
    ['Loud Township, Precinct 1', '107', '243'],
    ['Montmorency Township, Precinct 1', '257', '530'],
    ['Montmorency Township, Precinct 2', '231', '441'],
    ['Rust Township, Precinct 1', '183', '422'],
    ['Vienna Township, Precinct 1', '208', '476'],
    ['Montmorency County Michigan - Total', '3,402', '8,203'],
    ['County - Total', '3,402', '8,203'],
]


def mont_aux9():
    """Fresh copy of the countywide aux table (manual rows are consumed)."""
    return [row[:] for row in _MONT_AUX9]


COUNTY_CONFIG = {
    'Oscoda': {
        'cache': 'Oscoda_County_August_Election_Results',
        'pages': 100,
        'out': '2024/counties/20240806__mi__primary__oscoda__precinct.csv',
        'precincts': [
            'Big Creek Township, Precinct 1', 'Big Creek Township, Precinct 2',
            'Clinton Township, Precinct 1', 'Comins Township, Precinct 1',
            'Elmer Township, Precinct 1', 'Greenwood Township, Precinct 1',
            'Mentor Township, Precinct 1',
        ],
        'name_fixes': {},
        # Pages whose tables OCR mangled beyond rule-based repair, hand-
        # transcribed from the PDF (values cross-checked against the
        # printed county footers). Delegate contests with no candidate
        # rows print only Total Votes: no Democrat filed, so every vote
        # is a write-in (the report prints names for filed candidates).
        'manual': {
            3: [   # per-precinct values lost; transcribed from the page image
                ['Representative in Congress 1st District (DEM) (Vote for 1) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Big Creek Township, Precinct 1', '347', '1,142'],
                ['Big Creek Township, Precinct 2', '451', '1,544'],
                ['Clinton Township, Precinct 1', '172', '452'],
                ['Comins Township, Precinct 1', '574', '1,627'],
                ['Elmer Township, Precinct 1', '255', '719'],
                ['Greenwood Township, Precinct 1', '432', '1,223'],
                ['Mentor Township, Precinct 1', '389', '1,099'],
                ['Oscoda County Michigan - Total', '2,620', '7,806'],
                ['Precinct', 'Callie Barr', 'Bob Lorinser', 'Total Votes'],
                ['Big Creek Township, Precinct 1', '38', '16', '54'],
                ['Big Creek Township, Precinct 2', '37', '18', '55'],
                ['Clinton Township, Precinct 1', '11', '5', '16'],
                ['Comins Township, Precinct 1', '52', '25', '77'],
                ['Elmer Township, Precinct 1', '19', '8', '27'],
                ['Greenwood Township, Precinct 1', '68', '32', '100'],
                ['Mentor Township, Precinct 1', '38', '21', '59'],
                ['Oscoda County Michigan - Total', '263', '125', '388'],
                ['County - Total', '263', '125', '388'],
            ],
            7: [   # values merged into labels; transcribed from the page image
                ['County Sheriff (DEM) (Vote for 1) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Big Creek Township, Precinct 1', '347', '1,142'],
                ['Big Creek Township, Precinct 2', '451', '1,544'],
                ['Clinton Township, Precinct 1', '172', '452'],
                ['Comins Township, Precinct 1', '574', '1,627'],
                ['Elmer Township, Precinct 1', '255', '719'],
                ['Greenwood Township, Precinct 1', '432', '1,223'],
                ['Mentor Township, Precinct 1', '389', '1,099'],
                ['Oscoda County Michigan - Total', '2,620', '7,806'],
                ['Precinct', 'Total Votes'],
                ['Big Creek Township, Precinct 1', '6'],
                ['Big Creek Township, Precinct 2', '2'],
                ['Clinton Township, Precinct 1', '0'],
                ['Comins Township, Precinct 1', '17'],
                ['Elmer Township, Precinct 1', '4'],
                ['Greenwood Township, Precinct 1', '9'],
                ['Mentor Township, Precinct 1', '7'],
                ['Oscoda County Michigan - Total', '45'],
                ['County - Total', '45'],
            ],
            10: [   # transcribed from the page image
                ['County Road Commissioner (DEM) (Vote for 1) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Big Creek Township, Precinct 1', '347', '1,142'],
                ['Big Creek Township, Precinct 2', '451', '1,544'],
                ['Clinton Township, Precinct 1', '172', '452'],
                ['Comins Township, Precinct 1', '574', '1,627'],
                ['Elmer Township, Precinct 1', '255', '719'],
                ['Greenwood Township, Precinct 1', '432', '1,223'],
                ['Mentor Township, Precinct 1', '389', '1,099'],
                ['Oscoda County Michigan - Total', '2,620', '7,806'],
                ['Precinct', 'Total Votes'],
                ['Big Creek Township, Precinct 1', '4'],
                ['Big Creek Township, Precinct 2', '2'],
                ['Clinton Township, Precinct 1', '0'],
                ['Comins Township, Precinct 1', '12'],
                ['Elmer Township, Precinct 1', '4'],
                ['Greenwood Township, Precinct 1', '7'],
                ['Mentor Township, Precinct 1', '6'],
                ['Oscoda County Michigan - Total', '35'],
                ['County - Total', '35'],
            ],
            17: [   # per-precinct values lost by OCR; from the page image
                ['Big Creek Township Clerk (DEM) (Vote for 1) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Big Creek Township, Precinct 1', '347', '1,142'],
                ['Big Creek Township, Precinct 2', '451', '1,544'],
                ['Oscoda County Michigan - Total', '798', '2,686'],
                ['Precinct', 'Total Votes'],
                ['Big Creek Township, Precinct 1', '5'],
                ['Big Creek Township, Precinct 2', '2'],
                ['Oscoda County Michigan - Total', '7'],
                ['County - Total', '7'],
            ],
            23: [   # "Precinct 10" = precinct 1 + value 0 (footer total 0)
                ['Clinton Township Trustee (DEM) (Vote for 2) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Clinton Township, Precinct 1', '172', '452'],
                ['Oscoda County Michigan - Total', '172', '452'],
                ['Precinct', 'Total Votes'],
                ['Clinton Township, Precinct 1', '0'],
                ['Oscoda County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            33: [
                ['Greenwood Township Clerk (DEM) (Vote for 1) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Greenwood Township, Precinct 1', '432', '1,223'],
                ['Oscoda County Michigan - Total', '432', '1,223'],
                ['Precinct', 'Total Votes'],
                ['Greenwood Township, Precinct 1', '9'],
                ['Oscoda County Michigan - Total', '9'],
                ['County - Total', '9'],
            ],
            41: [
                ['Big Creek Township, Precinct 2 Delegate (DEM) (Vote for 2) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Big Creek Township, Precinct 2', '451', '1,544'],
                ['Oscoda County Michigan - Total', '451', '1,544'],
                ['Precinct', 'Total Votes'],
                ['Big Creek Township, Precinct 2', '3'],
                ['Oscoda County Michigan - Total', '3'],
                ['County - Total', '3'],
            ],
            43: [
                ['Comins Township, Precinct 1 Delegate (DEM) (Vote for 2) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Comins Township, Precinct 1', '574', '1,627'],
                ['Oscoda County Michigan - Total', '574', '1,627'],
                ['Precinct', 'Total Votes'],
                ['Comins Township, Precinct 1', '20'],
                ['Oscoda County Michigan - Total', '20'],
                ['County - Total', '20'],
            ],
            63: [   # transcribed from the page image
                ['County Commissioner 5th District (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Big Creek Township, Precinct 1', '150', '514'],
                ['Big Creek Township, Precinct 2', '16', '51'],
                ['Comins Township, Precinct 1', '45', '136'],
                ['Mentor Township, Precinct 1', '389', '1,099'],
                ['Oscoda County Michigan - Total', '600', '1,800'],
                ['Precinct', 'Total Votes'],
                ['Big Creek Township, Precinct 1', '9'],
                ['Big Creek Township, Precinct 2', '3'],
                ['Comins Township, Precinct 1', '4'],
                ['Mentor Township, Precinct 1', '22'],
                ['Oscoda County Michigan - Total', '38'],
                ['County - Total', '38'],
            ],
            77: [
                ['Elmer Township Clerk (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Elmer Township, Precinct 1', '255', '719'],
                ['Oscoda County Michigan - Total', '255', '719'],
                ['Precinct', 'Michelle Hoffman', 'Total Votes'],
                ['Elmer Township, Precinct 1', '165', '168'],
                ['Oscoda County Michigan - Total', '165', '168'],
                ['County - Total', '165', '168'],
            ],
            45: [
                ['Greenwood Township, Precinct 1 Delegate (DEM) (Vote for 2) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Greenwood Township, Precinct 1', '432', '1,223'],
                ['Oscoda County Michigan - Total', '432', '1,223'],
                ['Precinct', 'Total Votes'],
                ['Greenwood Township, Precinct 1', '10'],
                ['Oscoda County Michigan - Total', '10'],
                ['County - Total', '10'],
            ],
            99: [
                ['Mentor Township Fire Protection Millage Proposal (Vote for 1)'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Mentor Township, Precinct 1', '389', '1,099'],
                ['Oscoda County Michigan - Total', '389', '1,099'],
                ['Precinct', 'Yes', 'No', 'Total Votes'],
                ['Mentor Township, Precinct 1', '247', '110', '357'],
                ['Oscoda County Michigan - Total', '247', '110', '357'],
                ['County - Total', '247', '110', '357'],
            ],
        },
    },
    'Luce': {
        'cache': 'Luce_County_August_2024_Official_Result_County_WIde',
        'pages': 67,
        'out': '2024/counties/20240806__mi__primary__luce__precinct.csv',
        'precincts': [
            'Columbus Township, Precinct 1', 'Lakefield Township, Precinct 1',
            'McMillan Township, Precinct 1', 'Pentland Township, Precinct 1',
        ],
        'name_fixes': {
            'Luca County Proposition A': 'Luce County Proposition A',
            'Lucie County Proposition B': 'Luce County Proposition B',
        },
        'manual': {},
    },
    # Image-only 206-page SOVC (PaddleOCR); separate aux + results tables
    # per contest page (Oscoda 2024 style), 19 single/multi-precinct
    # jurisdictions. Titles abbreviate "Rep in ..." and repeat the party
    # tag after (Vote for N).
    'Iosco': {
        'cache': 'Iosco_County_Aug_2020_Primary_Statement_of_Votes',
        'pages': 206,
        'out': '2020/counties/20200804__mi__primary__iosco__precinct.csv',
        'precincts': [
            'Alabaster Township, Precinct 1', 'AuSable Township, Precinct 1',
            'Baldwin Township, Precinct 1', 'Burleigh Township, Precinct 1',
            'Grant Township, Precinct 1', 'Oscoda Township, Precinct 1',
            'Oscoda Township, Precinct 2', 'Oscoda Township, Precinct 3',
            'Oscoda Township, Precinct 4', 'Plainfield Township, Precinct 1',
            'Plainfield Township, Precinct 2', 'Reno Township, Precinct 1',
            'Sherman Township, Precinct 1', 'Tawas Township, Precinct 1',
            'Wilber Township, Precinct 1', 'City of East Tawas, Precinct 1',
            'City of Tawas City, Precinct 1', 'City of Whittemore, '
            'Precinct 1',
        ],
        'title_sub': [
            (r'^(.+), Precinct (\d+) Delegate$',
             r'\1 Precinct \2 Delegate to County Convention'),
        ],
        # The footer's county name OCRs as "losco" on most pages.
        'precinct_fixes': {
            'losco county michigan': 'iosco county michigan',
            # p122's aux row: 'Burleigh To  nship, Prec  nct 1' (the LaTeX
            # \text{w}/\text{i} fragments flatten away)
            'tonshipprecnct': 'townshipprecinct',
        },
        # p092: the results footer's Unresolved Write-In prints "24"
        # (County - Total and the precinct sum agree; render-verified);
        # the OCR reads the Total row as "241".
        'footer_overrides': {
            92: {'results:unresolved': 24},
        },
        # p130: OCR shifted the Unresolved Write-In column up one precinct
        # (P1 prints 43, read as 28); render-verified.
        'manual': {
            130: [
                ['Oscoda Township Supervisor (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Iosco County Michigan', '', ''],
                ['Oscoda Township, Precinct 1', '747', '2,020'],
                ['Oscoda Township, Precinct 2', '337', '953'],
                ['Oscoda Township, Precinct 3', '566', '1,534'],
                ['Oscoda Township, Precinct 4', '471', '1,651'],
                ['Iosco County Michigan - Total', '2,121', '6,158'],
                ['Cumulative', '', ''],
                ['Cumulative', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '2,121', '6,158'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Iosco County Michigan', '', ''],
                ['Oscoda Township, Precinct 1', '0', '43'],
                ['Oscoda Township, Precinct 2', '0', '28'],
                ['Oscoda Township, Precinct 3', '0', '28'],
                ['Oscoda Township, Precinct 4', '0', '25'],
                ['Iosco County Michigan - Total', '0', '124'],
                ['Cumulative', '', ''],
                ['Cumulative', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '0', '124'],
            ],
        },
    },
    # Image-only 206-page SOVC (PaddleOCR); Iosco's layout, 13
    # jurisdictions.
    'Kalkaska': {
        'cache': 'Kalkaska_County_Aug_2020_Primary_Statement_of_Votes_Cast',
        'pages': 206,
        'out': '2020/counties/20200804__mi__primary__kalkaska__precinct.csv',
        'precincts': [
            'Bear Lake Township, Precinct 1', 'Blue Lake Township, Precinct 1',
            'Boardman Township, Precinct 1', 'Clearwater Township, Precinct 1',
            'Coldsprings Township, Precinct 1', 'Excelsior Township, '
            'Precinct 1',
            'Garfield Township, Precinct 1', 'Kalkaska Township, Precinct 1',
            'Kalkaska Township, Precinct 2', 'Oliver Township, Precinct 1',
            'Orange Township, Precinct 1', 'Rapid River Township, Precinct 1',
            'Springfield Township, Precinct 1',
        ],
        'manual': {},
        # PaddleOCR dropped or garbled these contest titles; the printed
        # titles were hand-read from page renders.  Pages 95/98/125/142
        # are column-spill continuations of the previous page's contest
        # (a third candidate plus the Total Votes/Unresolved columns
        # printed on their own page: e.g. Comm 2nd REP Coldsprings
        # 199+35+120=354), so they repeat the parent contest's title.
        'page_titles': {
            18: 'County Commissioner 6th District (DEM) (Vote for 1) DEM',
            40: 'Excelsior Township Supervisor (DEM) (Vote for 1) DEM',
            46: 'Garfield Township Treasurer (DEM) (Vote for 1) DEM',
            55: 'Oliver Township Trustee (DEM) (Vote for 2) DEM',
            64: 'Springfield Township Supervisor (DEM) (Vote for 1) DEM',
            68: 'Bear Lake Township, Precinct 1 Delegate (DEM) (Vote for 3)'
                ' DEM',
            80: 'Springfield Township, Precinct 1 Delegate (DEM) (Vote for 4)'
                ' DEM',
            83: 'Rep in State Legislature 103rd District (REP) (Vote for 1)'
                ' REP',
            92: 'County Commissioner 1st District (REP) (Vote for 1) REP',
            95: 'County Commissioner 2nd District (REP) (Vote for 1) REP',
            98: 'County Commissioner 4th District (REP) (Vote for 1) REP',
            125: 'Clearwater Township Trustee (REP) (Vote for 2) REP',
            129: 'Coldsprings Township Trustee (REP) (Vote for 2) REP',
            131: 'Excelsior Township Supervisor (REP) (Vote for 1) REP',
            134: 'Excelsior Township Trustee (REP) (Vote for 2) REP',
            140: 'Garfield Township Treasurer (REP) (Vote for 1) REP',
            155: 'Orange Township Treasurer (REP) (Vote for 1) REP',
            158: 'Rapid River Township Supervisor (REP) (Vote for 1) REP',
            163: 'Springfield Township Supervisor (REP) (Vote for 1) REP',
            169: 'Boardman Township, Precinct 1 Delegate (REP) (Vote for 4)'
                 ' REP',
            170: 'Boardman Township, Precinct 1 Delegate (REP) (Vote for 4)'
                 ' REP',
            173: 'Coldsprings Township, Precinct 1 Delegate (REP) (Vote for 4)'
                 ' REP',
            174: 'Excelsior Township, Precinct 1 Delegate (REP) (Vote for 2)'
                 ' REP',
            175: 'Garfield Township, Precinct 1 Delegate (REP) (Vote for 2)'
                 ' REP',
            181: 'Rapid River Township, Precinct 1 Delegate (REP) (Vote for 3)'
                 ' REP',
        },
        'manual': {
            # p158's results header fused into one cell and PaddleOCR
            # dropped the Total Votes/Unresolved Write-In captions
            # entirely; page-image check: Williams 184, Total 184,
            # unresolved 2.
            158: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Kalkaska County Michigan', '', ''],
                ['Rapid River Township, Precinct 1', '316', '1,118'],
                ['Kalkaska County Michigan - Total', '316', '1,118'],
                ['Cumulative', '', ''],
                ['Cumulative', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '316', '1,118'],
                ['Precinct', 'Terry Williams (REP)', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Kalkaska County Michigan', '', '', ''],
                ['Rapid River Township, Precinct 1', '184', '184', '2'],
                ['Kalkaska County Michigan - Total', '184', '184', '2'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '184', '184', '2'],
            ],
        },
    },
    # One PDF; flat SOVC like Luce (contest per page section, aux +
    # results tables), nine single-precinct townships.
    'Montmorency': {
        # The orient OCR variant is used: the plain one drops ~90 contest
        # titles and garbles several aux-table headers.
        'cache': 'MontmorencyCountyResults_orient',
        'pages': 169,
        'out': '2024/counties/20240806__mi__primary__montmorency__precinct.csv',
        'precincts': [
            'Albert Township, Precinct 1', 'Avery Township, Precinct 1',
            'Briley Township, Precinct 1', 'Hillman Township, Precinct 1',
            'Loud Township, Precinct 1', 'Montmorency Township, Precinct 1',
            'Montmorency Township, Precinct 2', 'Rust Township, Precinct 1',
            'Vienna Township, Precinct 1',
        ],
        'name_fixes': {},
        # PaddleOCR misreads 'Avery' as 'Every' and 'Montmorency' as
        # 'Montmoreny' in places.
        'precinct_fixes': {'Every Township': 'Avery Township',
                           'Montmoreny': 'Montmorency'},
        # The orient OCR reduces this contest's title to a bare
        # '(Vote for 1)'; the full title is hand-read from the PDF page.
        'page_titles': {
            157: 'Montmorency Township Millage Renewal and Increase for '
                 'Fire Department Operation and Equipment (Vote for 1)',
        },
        # The unresolved write-in continuation tables lost their header
        # labels to the scan (all values are zero); hand-typed here.
        'manual': {
            # p070: OCR printed a stray '3' on the county label row and
            # garbled the '- Total' to 0; the precinct values are clean.
            70: [
                ['Precinct', 'Unresolved Write-In'],
                ['Montmorency County Michigan', ''],
                ['Albert Township, Precinct 1', '0'],
                ['Avery Township, Precinct 1', '0'],
                ['Briley Township, Precinct 1', '0'],
                ['Hillman Township, Precinct 1', '1'],
                ['Loud Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 1', '1'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Rust Township, Precinct 1', '2'],
                ['Vienna Township, Precinct 1', '7'],
                ['Montmorency County Michigan - Total', '11'],
                ['County - Total', '11'],
            ],
            91: [
                ['Precinct', 'Unresolved Write-In'],
                ['Montmorency County Michigan', ''],
                ['Avery Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            144: [
                ['Precinct', 'Unresolved Write-In'],
                ['Montmorency County Michigan', ''],
                ['Albert Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            148: [
                ['Precinct', 'Unresolved Write-In'],
                ['Montmorency County Michigan', ''],
                ['Briley Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            150: [
                ['Precinct', 'Unresolved Write-In'],
                ['Montmorency County Michigan', ''],
                ['Hillman Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            152: [
                ['Precinct', 'Unresolved Write-In'],
                ['Montmorency County Michigan', ''],
                ['Hillman Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            154: [
                ['Precinct', 'Unresolved Write-In'],
                ['Montmorency County Michigan', ''],
                ['Loud Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            158: [
                ['Precinct', 'Unresolved Write-In'],
                ['Montmorency County Michigan', ''],
                ['Montmorency Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            168: [
                ['Precinct', 'Unresolved Write-In'],
                ['Montmorency County Michigan', ''],
                ['Vienna Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
        },
    },
    # Three PDFs (file 1 = DEM contests, file 2 = REP, file 3 = REP
    # delegates/proposals), each page a contest section; the orient OCR
    # variant is used because the plain one drops most contest titles.
    'Dickinson': {
        'caches': [
            'Statement_of_Vote_Cast_Official_Dickinson_County_1_orient',
            'Statement_of_Vote_Cast_Official_Dickinson_County_2_orient',
            'Statement_of_Vote_Cast_Official_Dickinson_County_3_orient',
        ],
        'pages': 173,
        'out': '2024/counties/20240806__mi__primary__dickinson__precinct.csv',
        'precincts': [
            'City of Iron Mountain, Ward 1, Precinct 1',
            'City of Iron Mountain, Ward 2, Precinct 2',
            'City of Iron Mountain, Ward 3, Precinct 3',
            'City of Kingsford, Precinct 1', 'City of Kingsford, Precinct 2',
            'City of Norway, Precinct 1', 'Breen Township, Precinct 1',
            'Breitung Township, Precinct 1', 'Breitung Township, Precinct 2',
            'Breitung Township, Precinct 3', 'Felch Township, Precinct 1',
            'Norway Township, Precinct 1', 'Sagola Township, Precinct 1',
            'Waucedah Township, Precinct 1',
            'West Branch Township, Precinct 1',
        ],
        'name_fixes': {
            'Agola Township, Precinct 1 Delegate to County Convention':
                'Sagola Township, Precinct 1 Delegate to County Convention',
            'West Branch T wnship, Precinct 1 Delegate to County Convention':
                'West Branch Township, Precinct 1 Delegate to County '
                'Convention',
        },
        # two tables interleaved into one, or values lost outright) --
        # transcribed from the page images (some render rotated).
        'manual': {
            17: [   # results row label wrapped, Total Votes column lost
                ['County Commissioner 3rd District (DEM) (Vote for 1) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '411', '2,316'],
                ['Breitung Township, Precinct 3', '693', '2,505'],
                ['Dickinson County Michigan - Total', '1,104', '4,821'],
                ['County - Total', '1,104', '4,821'],
                ['Precinct', 'Sandi Lefebvre', 'Total Votes',
                 'Unresolved Write-In'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '118', '118',
                 '0'],
                ['Breitung Township, Precinct 3', '150', '150', '1'],
                ['Dickinson County Michigan - Total', '268', '268', '1'],
                ['County - Total', '268', '268', '1'],
            ],
            34: [   # two tables interleaved row by row in one merged table
                ['Norway Township Treasurer (DEM) (Vote for 1) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Norway Township, Precinct 1', '388', '1,296'],
                ['Dickinson County Michigan - Total', '388', '1,296'],
                ['County - Total', '388', '1,296'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['Norway Township, Precinct 1', '0', '8'],
                ['Dickinson County Michigan - Total', '0', '8'],
                ['County - Total', '0', '8'],
            ],
            41: [   # results table collapsed to bare labels, values lost
                ['Waucedah Township Clerk (DEM) (Vote for 1) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Waucedah Township, Precinct 1', '218', '806'],
                ['Dickinson County Michigan - Total', '218', '806'],
                ['County - Total', '218', '806'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['Waucedah Township, Precinct 1', '0', '5'],
                ['Dickinson County Michigan - Total', '0', '5'],
                ['County - Total', '0', '5'],
            ],
            51: [   # aux+results tables merged; 'Precinct County' headers
                ['City of Kingsford, Precinct 1 Delegate to County '
                 'Convention (DEM) (Vote for 5)'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['City of Kingsford, Precinct 1', '489', '1,855'],
                ['Dickinson County Michigan - Total', '489', '1,855'],
                ['County - Total', '489', '1,855'],
                ['Precinct', 'Janice K. Miller', 'Mark C. Miller'],
                ['City of Kingsford, Precinct 1', '132', '134'],
            ],
            53: [
                ['City of Kingsford, Precinct 2 Delegate to County '
                 'Convention (DEM) (Vote for 5)'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['City of Kingsford, Precinct 2', '494', '2,600'],
                ['Dickinson County Michigan - Total', '494', '2,600'],
                ['County - Total', '494', '2,600'],
                ['Precinct', 'Dennis Baldinelli', 'Lola Johnson',
                 'Total Votes'],
                ['City of Kingsford, Precinct 2', '128', '158', '286'],
            ],
            54: [   # continuation: values displaced, transcribed from image
                ['Precinct', 'Unresolved Write-In'],
                ['City of Kingsford, Precinct 2', '2'],
                ['Dickinson County Michigan - Total', '2'],
                ['County - Total', '2'],
            ],
            55: [
                ['City of Norway, Precinct 1 Delegate to County '
                 'Convention (DEM) (Vote for 5)'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['City of Norway, Precinct 1', '572', '2,709'],
                ['Dickinson County Michigan - Total', '572', '2,709'],
                ['County - Total', '572', '2,709'],
                ['Precinct', 'Total Votes', 'Mari Negro Qualified Write In',
                 'Leslie Negro Qualified Write In'],
                ['City of Norway, Precinct 1', '9', '3', '3'],
                ['Dickinson County Michigan - Total', '9', '3', '3'],
                ['County - Total', '9', '3', '3'],
            ],
            60: [   # results table labels fused into one cell
                ['Breitung Township, Precinct 2 Delegate to County '
                 'Convention (DEM) (Vote for 3) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Breitung Township, Precinct 2', '407', '1,289'],
                ['Dickinson County Michigan - Total', '407', '1,289'],
                ['County - Total', '407', '1,289'],
                ['Precinct', 'Lynne H. Wilson', 'Total Votes',
                 'Unresolved Write-In'],
                ['Breitung Township, Precinct 2', '100', '100', '2'],
                ['Dickinson County Michigan - Total', '100', '100', '2'],
                ['County - Total', '100', '100', '2'],
            ],
            62: [   # continuation page, image-only and rotated; OCR lost
                    # all values (BTP3 Unresolved Write-In = 7)
                ['Precinct', 'Unresolved Write-In'],
                ['Breitung Township, Precinct 3', '7'],
                ['Dickinson County Michigan - Total', '7'],
                ['County - Total', '7'],
            ],
            # File 2 (global page = 65 + local). The 4-candidate US Senate
            # table splits across pages 69/70; page 69 carries the aux
            # table plus the Amash/O'Donnell half, whose rows collapsed
            # into text lines the parser mis-keys (image-verified).
            69: [
                ['United States Senator (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '411', '2,316'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '343', '2,315'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '396', '2,237'],
                ['City of Kingsford, Precinct 1', '489', '1,855'],
                ['City of Kingsford, Precinct 2', '494', '2,600'],
                ['City of Norway, Precinct 1', '572', '2,709'],
                ['Breen Township, Precinct 1', '113', '410'],
                ['Breitung Township, Precinct 1', '487', '1,622'],
                ['Breitung Township, Precinct 2', '407', '1,289'],
                ['Breitung Township, Precinct 3', '693', '2,505'],
                ['Felch Township, Precinct 1', '170', '652'],
                ['Norway Township, Precinct 1', '388', '1,296'],
                ['Sagola Township, Precinct 1', '342', '1,040'],
                ['Waucedah Township, Precinct 1', '218', '806'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '5,550', '23,704'],
                ['County - Total', '5,550', '23,704'],
                ['Precinct', 'Justin Amash', "Sherry O'Donnell"],
                ['City of Iron Mountain, Ward 1, Precinct 1', '19', '24'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '29', '24'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '15', '22'],
                ['City of Kingsford, Precinct 1', '18', '30'],
                ['City of Kingsford, Precinct 2', '48', '47'],
                ['City of Norway, Precinct 1', '22', '44'],
                ['Breen Township, Precinct 1', '10', '6'],
                ['Breitung Township, Precinct 1', '25', '54'],
                ['Breitung Township, Precinct 2', '30', '40'],
                ['Breitung Township, Precinct 3', '34', '74'],
                ['Felch Township, Precinct 1', '8', '6'],
                ['Norway Township, Precinct 1', '13', '35'],
                ['Sagola Township, Precinct 1', '29', '27'],
                ['Waucedah Township, Precinct 1', '10', '24'],
                ['West Branch Township, Precinct 1', '3', '4'],
                ['Dickinson County Michigan - Total', '313', '461'],
                ['County - Total', '313', '461'],
            ],
            72: [   # Bergman results continuation, rotated (unres = 6 total)
                ['Precinct', 'Unresolved Write-In'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '1'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '0'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '0'],
                ['City of Kingsford, Precinct 1', '0'],
                ['City of Kingsford, Precinct 2', '1'],
                ['City of Norway, Precinct 1', '0'],
                ['Breen Township, Precinct 1', '1'],
                ['Breitung Township, Precinct 1', '2'],
                ['Breitung Township, Precinct 2', '0'],
                ['Breitung Township, Precinct 3', '0'],
                ['Felch Township, Precinct 1', '0'],
                ['Norway Township, Precinct 1', '0'],
                ['Sagola Township, Precinct 1', '1'],
                ['Waucedah Township, Precinct 1', '0'],
                ['West Branch Township, Precinct 1', '0'],
                ['Dickinson County Michigan - Total', '6'],
                ['County - Total', '6'],
            ],
            77: [   # both tables garbled (rotated page); image-transcribed
                ['County Sheriff for Dickinson County (REP) (Vote for 1) '
                 'REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '411', '2,316'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '343', '2,315'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '396', '2,237'],
                ['City of Kingsford, Precinct 1', '489', '1,855'],
                ['City of Kingsford, Precinct 2', '494', '2,600'],
                ['City of Norway, Precinct 1', '572', '2,709'],
                ['Breen Township, Precinct 1', '113', '410'],
                ['Breitung Township, Precinct 1', '487', '1,622'],
                ['Breitung Township, Precinct 2', '407', '1,289'],
                ['Breitung Township, Precinct 3', '693', '2,505'],
                ['Felch Township, Precinct 1', '170', '652'],
                ['Norway Township, Precinct 1', '388', '1,296'],
                ['Sagola Township, Precinct 1', '342', '1,040'],
                ['Waucedah Township, Precinct 1', '218', '806'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '5,550', '23,704'],
                ['County - Total', '5,550', '23,704'],
                ['Precinct', 'Aaron M. Rochon', 'Total Votes',
                 'Unresolved Write-In'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '205', '205',
                 '1'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '167', '167',
                 '0'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '195', '195',
                 '1'],
                ['City of Kingsford, Precinct 1', '247', '247', '1'],
                ['City of Kingsford, Precinct 2', '260', '260', '0'],
                ['City of Norway, Precinct 1', '238', '238', '1'],
                ['Breen Township, Precinct 1', '84', '84', '0'],
                ['Breitung Township, Precinct 1', '287', '287', '1'],
                ['Breitung Township, Precinct 2', '219', '219', '1'],
                ['Breitung Township, Precinct 3', '393', '393', '5'],
                ['Felch Township, Precinct 1', '121', '121', '0'],
                ['Norway Township, Precinct 1', '205', '205', '1'],
                ['Sagola Township, Precinct 1', '207', '207', '0'],
                ['Waucedah Township, Precinct 1', '128', '128', '0'],
                ['West Branch Township, Precinct 1', '20', '20', '0'],
                ['Dickinson County Michigan - Total', '2,976', '2,976', '12'],
                ['County - Total', '2,976', '2,976', '12'],
            ],
            84: [   # results labels wrapped across rows; values lost
                ['County Commissioner 2nd District (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '343', '2,315'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '396', '2,237'],
                ['Dickinson County Michigan - Total', '739', '4,552'],
                ['County - Total', '739', '4,552'],
                ['Precinct', 'Ann Martin', 'Kevin Sullivan', 'Total Votes'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '74', '120',
                 '194'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '79', '146',
                 '225'],
                ['Dickinson County Michigan - Total', '153', '266', '419'],
                ['County - Total', '153', '266', '419'],
            ],
            85: [   # continuation: wrapped labels again, image-transcribed
                ['Precinct', 'Unresolved Write-In'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '0'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '0'],
                ['Dickinson County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            87: [   # CC 3rd District unres continuation, rotated
                ['Precinct', 'Unresolved Write-In'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '1'],
                ['Breitung Township, Precinct 3', '0'],
                ['Dickinson County Michigan - Total', '1'],
                ['County - Total', '1'],
            ],
            89: [   # CC 4th District unres continuation
                ['Precinct', 'Unresolved Write-In'],
                ['Breen Township, Precinct 1', '0'],
                ['Breitung Township, Precinct 1', '1'],
                ['Breitung Township, Precinct 2', '1'],
                ['Felch Township, Precinct 1', '0'],
                ['Sagola Township, Precinct 1', '0'],
                ['West Branch Township, Precinct 1', '0'],
                ['Dickinson County Michigan - Total', '2'],
                ['County - Total', '2'],
            ],
            88: [   # results labels fused into text lines, values lost
                ['County Commissioner 4th District (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Breen Township, Precinct 1', '113', '410'],
                ['Breitung Township, Precinct 1', '487', '1,622'],
                ['Breitung Township, Precinct 2', '407', '1,289'],
                ['Felch Township, Precinct 1', '170', '652'],
                ['Sagola Township, Precinct 1', '342', '1,040'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '1,546', '5,065'],
                ['County - Total', '1,546', '5,065'],
                ['Precinct', 'Peter Swanson', 'Henry A. Wender',
                 'Total Votes'],
                ['Breen Township, Precinct 1', '54', '32', '86'],
                ['Breitung Township, Precinct 1', '170', '155', '325'],
                ['Breitung Township, Precinct 2', '179', '81', '260'],
                ['Felch Township, Precinct 1', '40', '92', '132'],
                ['Sagola Township, Precinct 1', '141', '100', '241'],
                ['West Branch Township, Precinct 1', '4', '17', '21'],
                ['Dickinson County Michigan - Total', '588', '477', '1,065'],
                ['County - Total', '588', '477', '1,065'],
            ],
            115: [  # precinct unres cell blank in source (footer 0)
                ['Sagola Township Treasurer (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Sagola Township, Precinct 1', '342', '1,040'],
                ['Dickinson County Michigan - Total', '342', '1,040'],
                ['County - Total', '342', '1,040'],
                ['Precinct', 'Robin Begarowicz', 'Total Votes',
                 'Unresolved Write-In'],
                ['Sagola Township, Precinct 1', '200', '200', '0'],
                ['Dickinson County Michigan - Total', '200', '200', '0'],
                ['County - Total', '200', '200', '0'],
            ],
            108: [  # Norway Township Clerk unres continuation, colspan rows
                ['Precinct', 'Unresolved Write-In'],
                ['Norway Township, Precinct 1', '0'],
                ['Dickinson County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            118: [  # results values lost (image-only page)
                ['Waucedah Township Supervisor (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Waucedah Township, Precinct 1', '218', '806'],
                ['Dickinson County Michigan - Total', '218', '806'],
                ['County - Total', '218', '806'],
                ['Precinct', 'Louis A. Sturm', 'Total Votes',
                 'Unresolved Write-In'],
                ['Waucedah Township, Precinct 1', '113', '113', '2'],
                ['Dickinson County Michigan - Total', '113', '113', '2'],
                ['County - Total', '113', '113', '2'],
            ],
            122: [
                ['West Branch Township Supervisor (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '27', '52'],
                ['County - Total', '27', '52'],
                ['Precinct', 'Penny S. Skogman', 'Total Votes',
                 'Unresolved Write-In'],
                ['West Branch Township, Precinct 1', '20', '20', '0'],
                ['Dickinson County Michigan - Total', '20', '20', '0'],
                ['County - Total', '20', '20', '0'],
            ],
            123: [
                ['West Branch Township Clerk (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '27', '52'],
                ['County - Total', '27', '52'],
                ['Precinct', 'Lisa M. Jacobsen', 'Total Votes',
                 'Unresolved Write-In'],
                ['West Branch Township, Precinct 1', '19', '19', '0'],
                ['Dickinson County Michigan - Total', '19', '19', '0'],
                ['County - Total', '19', '19', '0'],
            ],
            45: [  # image-only page, no candidates on the ballot
                ['West Branch Township Clerk (DEM) (Vote for 1) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '27', '52'],
                ['County - Total', '27', '52'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['West Branch Township, Precinct 1', '0', '1'],
                ['Dickinson County Michigan - Total', '0', '1'],
                ['County - Total', '0', '1'],
            ],
            46: [
                ['West Branch Township Treasurer (DEM) (Vote for 1) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '27', '52'],
                ['County - Total', '27', '52'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['West Branch Township, Precinct 1', '0', '1'],
                ['Dickinson County Michigan - Total', '0', '1'],
                ['County - Total', '0', '1'],
            ],
            47: [
                ['West Branch Township Trustee (DEM) (Vote for 2) DEM'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '27', '52'],
                ['County - Total', '27', '52'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['West Branch Township, Precinct 1', '0', '3'],
                ['Dickinson County Michigan - Total', '0', '3'],
                ['County - Total', '0', '3'],
            ],
            124: [  # image-only page (rotated)
                ['West Branch Township Treasurer (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '27', '52'],
                ['County - Total', '27', '52'],
                ['Precinct', 'Kim M. Oman', 'Total Votes',
                 'Unresolved Write-In'],
                ['West Branch Township, Precinct 1', '20', '20', '0'],
                ['Dickinson County Michigan - Total', '20', '20', '0'],
                ['County - Total', '20', '20', '0'],
            ],
            125: [  # image-only page, no candidates on the ballot
                ['West Branch Township Trustee (REP) (Vote for 2) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '27', '52'],
                ['County - Total', '27', '52'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['West Branch Township, Precinct 1', '0', '13'],
                ['Dickinson County Michigan - Total', '0', '13'],
                ['County - Total', '0', '13'],
            ],
            132: [  # delegate table values lost (rotated page)
                ['City of Kingsford, Precinct 1 Delegate to County '
                 'Convention (REP) (Vote for 8) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['City of Kingsford, Precinct 1', '489', '1,855'],
                ['Dickinson County Michigan - Total', '489', '1,855'],
                ['County - Total', '489', '1,855'],
                ['Precinct', 'Jeff Gurchinoff', 'Barbara Hicks'],
                ['City of Kingsford, Precinct 1', '143', '163'],
                ['Dickinson County Michigan - Total', '143', '163'],
                ['County - Total', '143', '163'],
            ],
            136: [
                ['City of Norway, Precinct 1 Delegate to County '
                 'Convention (REP) (Vote for 9) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['City of Norway, Precinct 1', '572', '2,709'],
                ['Dickinson County Michigan - Total', '572', '2,709'],
                ['County - Total', '572', '2,709'],
                ['Precinct', 'Andrea Burcar', 'Victoria Jakel'],
                ['City of Norway, Precinct 1', '141', '216'],
                ['Dickinson County Michigan - Total', '141', '216'],
                ['County - Total', '141', '216'],
            ],
            139: [
                ['Breitung Township, Precinct 1 Delegate to County '
                 'Convention (REP) (Vote for 9) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Breitung Township, Precinct 1', '487', '1,622'],
                ['Dickinson County Michigan - Total', '487', '1,622'],
                ['County - Total', '487', '1,622'],
                ['Precinct', 'Brian H. Anderson', 'Bev Diamond'],
                ['Breitung Township, Precinct 1', '172', '191'],
                ['Dickinson County Michigan - Total', '172', '191'],
                ['County - Total', '172', '191'],
            ],
            # File 3 (global page = 140 + local). Collapsed unres-only
            # continuation tables: as bare text lines their "<label> 0"
            # rows overwrite the main page's Total Votes (image-verified).
            147: [  # merged grid staggers aux and results rows; unfixable
                ['Norway Township, Precinct 1 Delegate to County '
                 'Convention (REP) (Vote for 7) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Norway Township, Precinct 1', '388', '1,296'],
                ['Dickinson County Michigan - Total', '388', '1,296'],
                ['County - Total', '388', '1,296'],
                ['Precinct', 'Diane Gregg', 'Pete Schlitt'],
                ['Norway Township, Precinct 1', '146', '152'],
                ['Dickinson County Michigan - Total', '146', '152'],
                ['County - Total', '146', '152'],
            ],
            151: [  # phantom empty column drops the Unresolved value
                ['West Branch Township, Precinct 1 Delegate to County '
                 'Convention (REP) (Vote for 1) REP'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['West Branch Township, Precinct 1', '27', '52'],
                ['Dickinson County Michigan - Total', '27', '52'],
                ['County - Total', '27', '52'],
                ['Precinct', 'Total Votes',
                 'Amanda Viane Qualified Write In', 'Unresolved Write-In'],
                ['West Branch Township, Precinct 1', '0', '0', '1'],
                ['Dickinson County Michigan - Total', '0', '0', '1'],
                ['County - Total', '0', '0', '1'],
            ],
            153: [  # 911 Millage continuation
                ['Precinct', 'Unresolved Write-In'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '0'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '0'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '0'],
                ['City of Kingsford, Precinct 1', '0'],
                ['City of Kingsford, Precinct 2', '0'],
                ['City of Norway, Precinct 1', '0'],
                ['Breen Township, Precinct 1', '0'],
                ['Breitung Township, Precinct 1', '0'],
                ['Breitung Township, Precinct 2', '0'],
                ['Breitung Township, Precinct 3', '0'],
                ['Felch Township, Precinct 1', '0'],
                ['Norway Township, Precinct 1', '0'],
                ['Sagola Township, Precinct 1', '0'],
                ['Waucedah Township, Precinct 1', '0'],
                ['West Branch Township, Precinct 1', '0'],
                ['Dickinson County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            155: [  # Library Millage continuation
                ['Precinct', 'Unresolved Write-In'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '0'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '0'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '0'],
                ['City of Kingsford, Precinct 1', '0'],
                ['City of Kingsford, Precinct 2', '0'],
                ['City of Norway, Precinct 1', '0'],
                ['Breen Township, Precinct 1', '0'],
                ['Breitung Township, Precinct 1', '0'],
                ['Breitung Township, Precinct 2', '0'],
                ['Breitung Township, Precinct 3', '0'],
                ['Felch Township, Precinct 1', '0'],
                ['Norway Township, Precinct 1', '0'],
                ['Sagola Township, Precinct 1', '0'],
                ['Waucedah Township, Precinct 1', '0'],
                ['West Branch Township, Precinct 1', '0'],
                ['Dickinson County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            156: [  # aux header row fused with the W1P1 label row
                ['City of Iron Mountain Proposed Amendment to Section 4.10 '
                 'of the Charter of the City of Iron Mountain (Vote for 1)'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '411', '2,316'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '343', '2,315'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '396', '2,237'],
                ['Dickinson County Michigan - Total', '1,150', '6,868'],
                ['County - Total', '1,150', '6,868'],
                ['Precinct', 'Yes', 'No', 'Total Votes'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '190', '211',
                 '401'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '164', '171',
                 '335'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '189', '183',
                 '372'],
                ['Dickinson County Michigan - Total', '543', '565', '1,108'],
                ['County - Total', '543', '565', '1,108'],
            ],
            157: [  # Iron Mountain charter amendment continuation
                ['Precinct', 'Unresolved Write-In'],
                ['City of Iron Mountain, Ward 1, Precinct 1', '0'],
                ['City of Iron Mountain, Ward 2, Precinct 2', '0'],
                ['City of Iron Mountain, Ward 3, Precinct 3', '0'],
                ['Dickinson County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            163: [  # Felch Township Road Millage continuation, values lost
                ['Precinct', 'Unresolved Write-In'],
                ['Felch Township, Precinct 1', '0'],
                ['Dickinson County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            166: [  # aux footer fused with a Cumulative row; rows jumbled
                ['Sagola Township Proposal #2 Ambulance Protection '
                 '(Vote for 1)'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Sagola Township, Precinct 1', '342', '1,040'],
                ['Dickinson County Michigan - Total', '342', '1,040'],
                ['County - Total', '342', '1,040'],
                ['Precinct', 'Yes', 'No', 'Total Votes'],
                ['Sagola Township, Precinct 1', '279', '53', '332'],
                ['Dickinson County Michigan - Total', '279', '53', '332'],
                ['County - Total', '279', '53', '332'],
            ],
            167: [  # Sagola Proposal #2 unres continuation
                ['Precinct', 'Unresolved Write-In'],
                ['Sagola Township, Precinct 1', '0'],
                ['Dickinson County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            169: [  # Sagola Proposal #3 unres continuation, values lost
                ['Precinct', 'Unresolved Write-In'],
                ['Sagola Township, Precinct 1', '0'],
                ['Dickinson County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            170: [  # Total Votes column lost in OCR (Yes 118, No 81)
                ['Waucedah Township Millage Proposal for Road Construction '
                 'this is a Renewal (Vote for 1)'],
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['Waucedah Township, Precinct 1', '218', '806'],
                ['Dickinson County Michigan - Total', '218', '806'],
                ['County - Total', '218', '806'],
                ['Precinct', 'Yes', 'No', 'Total Votes'],
                ['Waucedah Township, Precinct 1', '118', '81', '199'],
                ['Dickinson County Michigan - Total', '118', '81', '199'],
                ['County - Total', '118', '81', '199'],
            ],
        },
    },
    # One PDF, several contests per page; each contest is one wide table
    # (candidate columns + Write-in) with no aux turnout tables, closed by a
    # bare 'Total' county footer. The orient OCR variant is used because the
    # plain one drops the contest titles.
    'Huron': {
        'cache': 'Huron_MI_Official_Results_Per_Precinct_orient',
        'pages': 39,
        'total_footer': True,
        'out': '2024/counties/20240806__mi__primary__huron__precinct.csv',
        'precincts': [
            'City of Bad Axe, Precinct 1', 'City of Caseville, Precinct 1',
            'City of Harbor Beach, Precinct 1', 'Bingham Township, Precinct 1',
            'Bloomfield Township, Precinct 1',
            'Brookfield Township, Precinct 1',
            'Caseville Township, Precinct 1',
            'Chandler Township, Precinct 1', 'Colfax Township, Precinct 1',
            'Dwight Township, Precinct 1', 'Fairhaven Township, Precinct 1',
            'Gore Township, Precinct 1', 'Grant Township, Precinct 1',
            'Hume Township, Precinct 1', 'Huron Township, Precinct 1',
            'Lake Township, Precinct 1', 'Lincoln Township, Precinct 1',
            'McKinley Township, Precinct 1', 'Meade Township, Precinct 1',
            'Oliver Township, Precinct 1', 'Paris Township, Precinct 1',
            'Pointe Aux Barques Township, Precinct 1',
            'Port Austin Township, Precinct 1',
            'Rubicon Township, Precinct 1', 'Sand Beach Township, Precinct 1',
            'Sebewaing Township, Precinct 1', 'Sheridan Township, Precinct 1',
            'Sherman Township, Precinct 1', 'Sigel Township, Precinct 1',
            'Verona Township, Precinct 1', 'Winsor Township, Precinct 1',
            # The Ubly Community Schools millage spans into Sanilac County;
            # the source prints those precincts with the county suffix.
            'Argyle Township (Sanilac County)',
            'Austin Township (Sanilac County)',
            'Delaware Township (Sanilac County)',
            'Greenleaf Township (Sanilac County)',
            'Minden Township (Sanilac County)',
        ],
        'name_fixes': {},
        'precinct_fixes': {'Sebewang Township': 'Sebewaing Township'},
        'manual': {},
    },
    # One PDF per party. Each contest is one merged table whose columns are
    # Times Cast | Registered Voters | candidates | Total Votes, and every
    # precinct is a label row followed by counting-board sub-rows (Election
    # Day / AV Counting Boards / Early Voting / Total) of which only 'Total'
    # carries the per-precinct values; 'board_rows' collapses them. The two
    # leading pages are a precinct turnout table in the same sub-row shape.
    'Mecosta': {
        'caches': ['Aug_2024_Statement_of_Votes_DEM_Mecosta',
                   'Aug_2024_Statement_of_Votes_REP_Mecosta'],
        'pages': 262,
        'board_rows': True,
        'title_sub': [
            # The Congress and State Legislature titles print no district
            # number; Mecosta is in the 2nd Congressional and 102nd House
            # districts.
            (r'^Representative in Congress$',
             'Representative in Congress 2nd District'),
            (r'^Representative in State Legislature$',
             'Representative in State Legislature 102nd District'),
            # Township offices print office-first ('Supervisor Aetna
            # Township'); the repo's convention is '<unit> <office>'.
            (r'^(Supervisor|Clerk|Treasurer|Trustee) (.+)$', r'\2 \1'),
            # Delegates print the party inside the district text ('...
            # for Aetna Township, DEM Precinct 1'); the comma before the
            # party can double up ('Township,, Precinct 1'), and other
            # townships print no comma at all ('Township Precinct 1').
            (r' (DEM|REP) Precinct', r', Precinct'),
            (r',, Precinct', r', Precinct'),
            (r'^(Delegate to County Convention for .+[^,]) Precinct (\d+)$',
             r'\1, Precinct \2'),
        ],
        'out': '2024/counties/20240806__mi__primary__mecosta__precinct.csv',
        # PaddleOCR dropped these contest titles outright (all in the REP
        # document); the titles were hand-read from page renders. Keys are
        # global page numbers — the DEM cache runs 123 pages, so REP page N
        # is global 123 + N.
        'page_titles': {
            155: 'County Commissioner District 2 (REP) (Vote for 1)',
            156: 'County Commissioner District 3 (REP) (Vote for 1)',
            159: 'County Commissioner District 6 (REP) (Vote for 1)',
            183: 'Treasurer Colfax Township (REP) (Vote for 1)',
            199: 'Trustee Grant Township (REP) (Vote for 2)',
            204: 'Trustee Green Charter Township (REP) (Vote for 4)',
            205: 'Delegate to County Convention for Green Charter Township, '
                 'Precinct 1 (REP) (Vote for 10)',
            219: 'Trustee Mecosta Township (REP) (Vote for 2)',
            220: 'Delegate to County Convention for Mecosta Township, '
                 'Precinct 1 (REP) (Vote for 8)',
            229: 'Trustee Morton Township (REP) (Vote for 2)',
            235: 'Trustee Sheridan Township (REP) (Vote for 2)',
            239: 'Treasurer Wheatland Township (REP) (Vote for 1)',
        },
        'precincts': [
            'Aetna Township, Precinct 1', 'Austin Township, Precinct 1',
            'Big Rapids Charter Township, Precinct 1',
            'City of Big Rapids, Precinct 1', 'City of Big Rapids, Precinct 2',
            'City of Big Rapids, Precinct 3',
            'Chippewa Township, Precinct 1', 'Colfax Township, Precinct 1',
            'Deerfield Township, Precinct 1', 'Fork Township, Precinct 1',
            'Grant Township, Precinct 1', 'Green Charter Township, Precinct 1',
            'Hinton Township, Precinct 1', 'Martiny Township, Precinct 1',
            'Mecosta Township, Precinct 1', 'Millbrook Township, Precinct 1',
            'Morton Township, Precinct 1', 'Morton Township, Precinct 2',
            'Sheridan Township, Precinct 1', 'Wheatland Township, Precinct 1',
        ],
        # The US Senate table's first page (global 124 = REP page 1) has
        # interleaved blank columns whose Total Votes values OCR dropped
        # entirely; the page is replaced wholesale with rows hand-read
        # from the PDF render (totals verified to sum to the printed
        # county footer of 6,023). Grant Township's label is kept so its
        # Total row on the next page still finds its precinct.
        'manual': {
            124: [
                ['United States Senator (REP) (Vote for 1)'],
                ['Precinct', 'Times Cast', 'Registered Voters',
                 'Justin Amash (REP)', "Sherry O'Donnell (REP)",
                 'Sandy Pensler (REP)', 'Mike Rogers (REP)', 'Total Votes'],
                ['Aetna Township, Precinct 1', '422', '1,801', '89', '40',
                 '24', '164', '317'],
                ['Austin Township, Precinct 1', '326', '1,305', '40', '47',
                 '18', '112', '217'],
                ['Big Rapids Charter Township, Precinct 1', '1,074', '3,188',
                 '135', '121', '66', '359', '681'],
                ['Chippewa Township, Precinct 1', '387', '1,069', '47', '32',
                 '29', '163', '271'],
                ['Colfax Township, Precinct 1', '624', '1,765', '82', '70',
                 '38', '241', '431'],
                ['Deerfield Township, Precinct 1', '296', '1,351', '63', '29',
                 '12', '105', '209'],
                ['Fork Township, Precinct 1', '319', '1,367', '19', '22',
                 '27', '132', '200'],
                ['Grant Township, Precinct 1', ''],
            ],
        },
    },
    # Image-only 167-page SOVC; the Iosco layout — each contest page holds
    # an aux table (Times Cast / Registered Voters) followed by the results
    # table, and most spill pages re-print only the aux half.
    'Ontonagon': {
        'cache': 'Ontonagon_MI_August_4_2020_Primary_Election_Results',
        'pages': 167,
        'out': '2020/counties/20200804__mi__primary__ontonagon__precinct.csv',
        # Pages whose title line the OCR dropped entirely (hand-read from
        # the pdftoppm renders). p006/141/152/153 are COLUMN-SPLIT
        # continuation pages (the last candidate column(s) + Total Votes
        # print on the next page): repeat the parent title so they merge
        # back into the contest opened on the previous page.
        'page_titles': {
            45: 'Township Treasurer for Interior Township (DEM)'
                ' (Vote for 1) DEM',
            48: 'Township Treasurer for Ontonagon Township (DEM)'
                ' (Vote for 1) DEM',
            67: 'Township Park Commissioner for Ontonagon Township (DEM)'
                ' (Vote for 7) DEM',
            74: 'Delegate to the County Convention for Haight Township,'
                ' Precinct 1 (DEM) (Vote for 1) DEM',
            96: 'Township Supervisor for Carp Lake Township (REP)'
                ' (Vote for 1) REP',
            6: 'Representative in State Legislature 110th District (DEM)'
               ' (Vote for 1) DEM',
            82: 'Representative in Congress 1st District (REP)'
                ' (Vote for 1) REP',
            98: 'Township Supervisor for Haight Township (REP)'
                ' (Vote for 1) REP',
            104: 'Township Supervisor for Stannard Township (REP)'
                 ' (Vote for 1) REP',
            109: 'Township Clerk for Haight Township (REP) (Vote for 1) REP',
            113: 'Township Clerk for Ontonagon Township (REP)'
                 ' (Vote for 1) REP',
            120: 'Township Treasurer for Haight Township (REP)'
                 ' (Vote for 1) REP',
            121: 'Township Treasurer for Interior Township (REP)'
                 ' (Vote for 1) REP',
            141: 'Township Park Commissioner for Ontonagon Township (REP)'
                 ' (Vote for 7) REP',
            152: 'Delegate to the County Convention for Ontonagon Township,'
                 ' Precinct 1 (REP) (Vote for 9) REP',
            153: 'Delegate to the County Convention for Ontonagon Township,'
                 ' Precinct 1 (REP) (Vote for 9) REP',
        },
        'precinct_fixes': {'Ortonagon': 'Ontonagon',
                           'Roddand': 'Rockland'},
        # CENR spells the 110th District representative Markkanen.
        'cand_fixes': {'Gregory Markanen': 'Gregory Markkanen'},
        # Pages hand-transcribed from the pdftoppm renders. p004 and
        # p159 are unresolved-write-in spill pages whose rotated/fused
        # headers the OCR lost (p159 printed as one fused text line whose
        # mis-split precinct numbers then polluted the open proposal
        # contest); p045/048/067/096/104 are single-precinct contests
        # whose results header fused into one cell (p045/048/104) or
        # lost its Unresolved Write-In caption (p096); p074's spill table
        # OCR'd as a junk ['1','1','1'] row.
        'manual': {
            4: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Ontonagon County Michigan', ''],
                ['Bergland Township, Precinct 1', '0'],
                ['Bohemia Township, Precinct 1', '0'],
                ['Bohemia Township, Precinct 2', '0'],
                ['Carp Lake Township, Precinct 1', '1'],
                ['Greenland Township, Precinct 1', '2'],
                ['Haight Township, Precinct 1', '0'],
                ['Interior Township, Precinct 1', '0'],
                ['Matchwood Township, Precinct 1', '0'],
                ['McMillan Township, Precinct 1', '0'],
                ['Ontonagon Township, Precinct 1', '2'],
                ['Rockland Township, Precinct 1', '1'],
                ['Stannard Township, Precinct 1', '0'],
                ['Ontonagon County Michigan - Total', '6'],
                ['Cumulative', ''],
                ['Cumulative', '0'],
                ['Cumulative - Total', '0'],
                ['County - Total', '6'],
            ],
            45: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Ontonagon County Michigan', '', ''],
                ['Interior Township, Precinct 1', '87', '289'],
                ['Ontonagon County Michigan - Total', '87', '289'],
                ['Cumulative', '', ''],
                ['Cumulative', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '87', '289'],
                ['Precinct', 'Chelsea J. Nurmi (DEM)', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Ontonagon County Michigan', '', '', ''],
                ['Interior Township, Precinct 1', '24', '24', '1'],
                ['Ontonagon County Michigan - Total', '24', '24', '1'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '24', '24', '1'],
            ],
            48: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Ontonagon County Michigan', '', ''],
                ['Ontonagon Township, Precinct 1', '790', '2,184'],
                ['Ontonagon County Michigan - Total', '790', '2,184'],
                ['Cumulative', '', ''],
                ['Cumulative', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '790', '2,184'],
                ['Precinct', 'Penny L. Saari (DEM)', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Ontonagon County Michigan', '', '', ''],
                ['Ontonagon Township, Precinct 1', '400', '400', '0'],
                ['Ontonagon County Michigan - Total', '400', '400', '0'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '400', '400', '0'],
            ],
            67: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Ontonagon County Michigan', '', ''],
                ['Ontonagon Township, Precinct 1', '790', '2,184'],
                ['Ontonagon County Michigan - Total', '790', '2,184'],
                ['Cumulative', '', ''],
                ['Cumulative', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '790', '2,184'],
                ['Precinct', 'Jason Clinesmith (DEM)',
                 'John P. LaSota (DEM)', 'Total Votes'],
                ['County', '', '', ''],
                ['Ontonagon County Michigan', '', '', ''],
                ['Ontonagon Township, Precinct 1', '353', '282', '635'],
                ['Ontonagon County Michigan - Total', '353', '282', '635'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '353', '282', '635'],
            ],
            74: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Ontonagon County Michigan', '', ''],
                ['Haight Township, Precinct 1', '58', '180'],
                ['Ontonagon County Michigan - Total', '58', '180'],
                ['Cumulative', '', ''],
                ['Cumulative', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '58', '180'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Ontonagon County Michigan', '', ''],
                ['Haight Township, Precinct 1', '0', '2'],
                ['Ontonagon County Michigan - Total', '0', '2'],
                ['Cumulative', '', ''],
                ['Cumulative', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '0', '2'],
            ],
            96: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Ontonagon County Michigan', '', ''],
                ['Carp Lake Township, Precinct 1', '212', '564'],
                ['Ontonagon County Michigan - Total', '212', '564'],
                ['Cumulative', '', ''],
                ['Cumulative', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '212', '564'],
                ['Precinct', 'Homer Colclasure (REP)', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Ontonagon County Michigan', '', '', ''],
                ['Carp Lake Township, Precinct 1', '69', '69', '1'],
                ['Ontonagon County Michigan - Total', '69', '69', '1'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '69', '69', '1'],
            ],
            104: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Ontonagon County Michigan', '', ''],
                ['Stannard Township, Precinct 1', '159', '630'],
                ['Ontonagon County Michigan - Total', '159', '630'],
                ['Cumulative', '', ''],
                ['Cumulative', '0', '0'],
                ['Cumulative - Total', '0', '0'],
                ['County - Total', '159', '630'],
                ['Precinct', 'William J. Andrus (REP)', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Ontonagon County Michigan', '', '', ''],
                ['Stannard Township, Precinct 1', '62', '62', '1'],
                ['Ontonagon County Michigan - Total', '62', '62', '1'],
                ['Cumulative', '', '', ''],
                ['Cumulative', '0', '0', '0'],
                ['Cumulative - Total', '0', '0', '0'],
                ['County - Total', '62', '62', '1'],
            ],
            159: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Ontonagon County Michigan', ''],
                ['Bergland Township, Precinct 1', '0'],
                ['Bohemia Township, Precinct 1', '0'],
                ['Bohemia Township, Precinct 2', '0'],
                ['Carp Lake Township, Precinct 1', '0'],
                ['Greenland Township, Precinct 1', '0'],
                ['Haight Township, Precinct 1', '0'],
                ['Interior Township, Precinct 1', '0'],
                ['Matchwood Township, Precinct 1', '0'],
                ['McMillan Township, Precinct 1', '0'],
                ['Ontonagon Township, Precinct 1', '0'],
                ['Rockland Township, Precinct 1', '0'],
                ['Stannard Township, Precinct 1', '0'],
                ['Ontonagon County Michigan - Total', '0'],
                ['Cumulative', ''],
                ['Cumulative', '0'],
                ['Cumulative - Total', '0'],
                ['County - Total', '0'],
            ],
        },
        'precincts': [
            'Bergland Township, Precinct 1', 'Bohemia Township, Precinct 1',
            'Bohemia Township, Precinct 2', 'Carp Lake Township, Precinct 1',
            'Greenland Township, Precinct 1', 'Haight Township, Precinct 1',
            'Interior Township, Precinct 1',
            'Matchwood Township, Precinct 1',
            'McMillan Township, Precinct 1',
            'Ontonagon Township, Precinct 1',
            'Rockland Township, Precinct 1',
            'Stannard Township, Precinct 1',
        ],
        'title_sub': [
            # The senator title doubles ('United States Senator for United
            # States Senator'); p021 appends a timestamp; p142/146/147/149/
            # 150/151 carry ballot-mark junk ('☐', '$ \mathcal{C} $') ahead
            # of the delegate titles.
            (r'^United States Senator for United States Senator',
             'United States Senator'),
            (r' 8/C \d+ [\d:]+ (?:[AP]M)?$', ''),
            (r'^(?:.\s*)?(?:\$\s*\\mathcal\{C\}\s*\$\s*)*'
             r'(?:. )?Delegate to the County Convention for ',
             'Delegate to County Convention for '),
            (r'^County Commissioner for County Commissioner District',
             'County Commissioner District'),
            # LUCE_OFFICE doesn't know the Park Commissioner or Constable
            # titles; Ontonagon 2020 also spells 'the' inside its delegate
            # titles.
            (r'^Township Park Commissioner for (.+)$',
             r'\1 Park Commissioner'),
            (r'^Township Constable for (.+)$', r'\1 Constable'),
            (r'^Township (Supervisor|Clerk|Treasurer|Trustee) for (.+)$',
             r'\2 \1'),
            # p156 source typo.
            (r'Abulance', 'Ambulance'),
        ],
    },
    # Image-only 115-page 2020 primary SOVC; every page is one contest
    # with a single merged 2-up table (left half = Times Cast, right half
    # = results) carrying a phantom blank column between the candidate
    # values and Total Votes (header holds the candidate as colspan=2).
    # No Registered Voters column, so no turnout pseudo rows. Titles
    # survived OCR as <div> lines.
    'Alger 2020': {
        'county_name': 'Alger',
        'cache': 'Alger_MI_August_4_2020_State_Primary_official_',
        'pages': 115,
        'out': '2020/counties/20200804__mi__primary__alger__precinct.csv',
        'two_up': True,
        'title_sub': [
            (r'^United States Senator for State$', 'United States Senator'),
            (r'^(Representative in (?:Congress|State Legislature) '
             r'\d+(?:st|nd|rd|th) District) for State$', r'\1'),
            (r'^County Clerk and Register of Deeds for State$',
             'County Clerk and Register of Deeds'),
            (r'^County Commissioner for County Commissioner (\d+)$',
             r'County Commissioner \1 District'),
        ],
        'precincts': ['City of Munising, Precinct 1', 'AuTrain Township, Precinct 1',
                      'Burt Township, Precinct 1', 'Grand Island Township, Precinct 1',
                      'Limestone Township, Precinct 1', 'Mathias Township, Precinct 1',
                      'Munising Township, Precinct 1', 'Onota Township, Precinct 1',
                      'Rock River Township, Precinct 1'],
    },
    # Image-only 294-page SOVC; each contest is a Luce-style merged table
    # whose data rows repeat the precinct label between the turnout and
    # results halves, with phantom blank columns interleaved (see p003).
    'Tuscola': {
        'cache': 'Tuscola_County_Aug_2024_Primary_Statement_of_Votes_Cast',
        'pages': 294,
        'out': '2024/counties/20240806__mi__primary__tuscola__precinct.csv',
        # PaddleOCR dropped the title line on these pages entirely; the
        # titles were hand-read from page renders.
        'page_titles': {
            11: 'County Clerk (DEM) (Vote for 1)',
            90: 'Treasurer for Wisner Township (DEM) (Vote for 1)',
            113: 'Trustee for Wisner Township (DEM) (Vote for 2)',
            147: 'County Treasurer (REP) (Vote for 1)',
            149: 'Sheriff (REP) (Vote for 1)',
            173: 'Supervisor for Koylton Township (REP) (Vote for 1)',
            182: 'Clerk for Almer Charter Township (REP) (Vote for 1)',
            189: 'Clerk for Elmwood Township (REP) (Vote for 1)',
            191: 'Clerk for Fremont Township (REP) (Vote for 1)',
            209: 'Treasurer for Denmark Township (REP) (Vote for 1)',
            210: 'Treasurer for Elkland Township (REP) (Vote for 1)',
            211: 'Treasurer for Ellington Township (REP) (Vote for 1)',
            215: 'Treasurer for Gilford Township (REP) (Vote for 1)',
            216: 'Treasurer for Indianfields Township (REP) (Vote for 1)',
            217: 'Treasurer for Juniata Township (REP) (Vote for 1)',
            222: 'Treasurer for Tuscola Township (REP) (Vote for 1)',
            227: 'Trustee for Akron Township (REP) (Vote for 2)',
            231: 'Trustee for Dayton Township (REP) (Vote for 2)',
            232: 'Trustee for Denmark Township (REP) (Vote for 2)',
            237: 'Trustee for Fremont Township (REP) (Vote for 2)',
            239: 'Trustee for Indianfields Township (REP) (Vote for 2)',
            247: 'Trustee for Watertown Township (REP) (Vote for 2)',
            257: 'Delegate for Dayton Township, Precinct 1 (REP) (Vote for 6)',
            265: 'Delegate for Indianfields Township, Precinct 1 (REP) '
                 '(Vote for 7)',
            266: 'Delegate for Juniata Township, Precinct 1 (REP) '
                 '(Vote for 5)',
            267: 'Delegate for Kingston Township, Precinct 1 (REP) '
                 '(Vote for 5)',
            276: 'Delegate for Wisner Township, Precinct 1 (REP) '
                 '(Vote for 2)',
        },
            # Pages whose OCR lost values the parser cannot recover: rows
            # rebuilt by hand from page renders (candidate values and
            # Times Cast/Registered Voters cross-checked against the OCR).
            'manual': {
                # The Total Votes column was dropped by OCR on every row;
                # the hand-read totals equal Hill Harper + Elissa Slotkin
                # on all 26 precincts (render-verified).
                3: [
                    ['United States Senator (DEM) (Vote for 1) DEM'],
                    ['Precinct', 'Times Cast', 'Registered Voters',
                     'Precinct', 'Hill Harper (DEM)', 'Elissa Slotkin (DEM)',
                     'Total Votes', 'Unresolved Write-In'],
                    ['County', '', '', 'County', '', '', '', ''],
                    ['Tuscola County', '', '', 'Tuscola County',
                     '', '', '', ''],
                    ['Akron Township, Precinct 1', '307', '1,119',
                     'Akron Township, Precinct 1', '8', '34', '42', '0'],
                    ['Almer Charter Township, Precinct 1', '706', '1,650',
                     'Almer Charter Township, Precinct 1', '10', '69',
                     '79', '0'],
                    ['Arbela Township, Precinct 1', '363', '1,222',
                     'Arbela Township, Precinct 1', '15', '72', '87', '0'],
                    ['Arbela Township, Precinct 2', '251', '1,194',
                     'Arbela Township, Precinct 2', '13', '82', '95', '0'],
                    ['Caro City, Precinct 1', '1,115', '3,386',
                     'Caro City, Precinct 1', '51', '153', '204', '0'],
                    ['Columbia Township, Precinct 1', '355', '974',
                     'Columbia Township, Precinct 1', '4', '36', '40', '0'],
                    ['Dayton Township, Precinct 1', '498', '1,637',
                     'Dayton Township, Precinct 1', '27', '67', '94', '0'],
                    ['Denmark Township, Precinct 1', '766', '2,328',
                     'Denmark Township, Precinct 1', '20', '107', '127',
                     '0'],
                    ['Elkland Township, Precinct 1', '880', '2,744',
                     'Elkland Township, Precinct 1', '33', '143', '176',
                     '0'],
                    ['Ellington Township, Precinct 1', '480', '1,072',
                     'Ellington Township, Precinct 1', '6', '48', '54',
                     '0'],
                    ['Elmwood Township, Precinct 1', '281', '472',
                     'Elmwood Township, Precinct 1', '2', '28', '30', '1'],
                    ['Fairgrove Township, Precinct 1', '427', '1,229',
                     'Fairgrove Township, Precinct 1', '10', '57', '67',
                     '0'],
                    ['Fremont Township, Precinct 1', '775', '2,728',
                     'Fremont Township, Precinct 1', '33', '139', '172',
                     '0'],
                    ['Gilford Township, Precinct 1', '185', '603',
                     'Gilford Township, Precinct 1', '4', '26', '30', '0'],
                    ['Indianfields Township, Precinct 1', '776', '2,135',
                     'Indianfields Township, Precinct 1', '19', '84', '103',
                     '0'],
                    ['Juniata Township, Precinct 1', '523', '1,323',
                     'Juniata Township, Precinct 1', '12', '97', '109',
                     '0'],
                    ['Kingston Township, Precinct 1', '394', '1,146',
                     'Kingston Township, Precinct 1', '3', '44', '47', '0'],
                    ['Koylton Township, Precinct 1', '343', '1,263',
                     'Koylton Township, Precinct 1', '8', '55', '63', '0'],
                    ['Millington Township, Precinct 1', '1,115', '3,593',
                     'Millington Township, Precinct 1', '32', '166', '198',
                     '1'],
                    ['Novesta Township, Precinct 1', '411', '1,109',
                     'Novesta Township, Precinct 1', '8', '49', '57', '0'],
                    ['Tuscola Township, Precinct 1', '655', '1,663',
                     'Tuscola Township, Precinct 1', '18', '80', '98', '0'],
                    ['Vassar City, Precinct 1', '510', '2,075',
                     'Vassar City, Precinct 1', '21', '110', '131', '0'],
                    ['Vassar Township, Precinct 1', '720', '3,145',
                     'Vassar Township, Precinct 1', '35', '135', '170',
                     '0'],
                    ['Watertown Township, Precinct 1', '518', '1,682',
                     'Watertown Township, Precinct 1', '13', '81', '94',
                     '0'],
                    ['Wells Township, Precinct 1', '534', '1,323',
                     'Wells Township, Precinct 1', '23', '70', '93', '1'],
                    ['Wisner Township, Precinct 1', '177', '515',
                     'Wisner Township, Precinct 1', '2', '21', '23', '0'],
                    ['Tuscola County - Total', '14,065', '43,330',
                     'Tuscola County - Total', '430', '2,053', '2,483',
                     '3'],
                    ['Cumulative', '', '', 'Cumulative', '', '', '', ''],
                    ['Cumulative', '0', '0', 'Cumulative', '0', '0', '0',
                     '0'],
                    ['Cumulative - Total', '0', '0', 'Cumulative - Total',
                     '0', '0', '0', '0'],
                    ['County - Total', '14,065', '43,330', 'County - Total',
                     '430', '2,053', '2,483', '3'],
                ],
                # OCR dropped the Total Votes value and one Unresolved
                # Write-In value and inserted phantom empty cells; the row
                # is rebuilt from the render.
                227: [
                    ['Trustee for Akron Township (REP) (Vote for 2) REP'],
                    ['Precinct', 'Times Cast', 'Registered Voters',
                     'Precinct', 'Carrie Anne Hines (REP)',
                     'Kathryn P. Sattelberg (REP)', 'Total Votes',
                     'Unresolved Write-In'],
                    ['County', '', '', 'County', '', '', '', ''],
                    ['Tuscola County', '', '', 'Tuscola County',
                     '', '', '', ''],
                    ['Akron Township, Precinct 1', '307', '1,119',
                     'Akron Township, Precinct 1', '188', '186', '374',
                     '2'],
                    ['Tuscola County - Total', '307', '1,119',
                     'Tuscola County - Total', '188', '186', '374', '2'],
                    ['Cumulative', '', '', 'Cumulative', '', '', '', ''],
                    ['Cumulative', '0', '0', 'Cumulative', '0', '0', '0',
                     '0'],
                    ['Cumulative - Total', '0', '0', 'Cumulative - Total',
                     '0', '0', '0', '0'],
                    ['County - Total', '307', '1,119', 'County - Total',
                     '188', '186', '374', '2'],
                ],
                # OCR dropped the Total Votes value ('912') on every row.
                232: [
                    ['Trustee for Denmark Township (REP) (Vote for 2) REP'],
                    ['Precinct', 'Times Cast', 'Registered Voters',
                     'Precinct', 'Michael Hill (REP)', 'Steve Schwab (REP)',
                     'James Wilkinson (REP)', 'Total Votes',
                     'Unresolved Write-In'],
                    ['County', '', '', 'County', '', '', '', '', ''],
                    ['Tuscola County', '', '', 'Tuscola County',
                     '', '', '', '', ''],
                    ['Denmark Township, Precinct 1', '766', '2,328',
                     'Denmark Township, Precinct 1', '263', '368', '281',
                     '912', '0'],
                    ['Tuscola County - Total', '766', '2,328',
                     'Tuscola County - Total', '263', '368', '281', '912',
                     '0'],
                    ['Cumulative', '', '', 'Cumulative', '', '', '', '',
                     ''],
                    ['Cumulative', '0', '0', 'Cumulative', '0', '0', '0',
                     '0', '0'],
                    ['Cumulative - Total', '0', '0', 'Cumulative - Total',
                     '0', '0', '0', '0', '0'],
                    ['County - Total', '766', '2,328', 'County - Total',
                     '263', '368', '281', '912', '0'],
                ],
                # OCR dropped the Total Votes value ('363').
                235: [
                    ['Trustee for Elmwood Township (REP) (Vote for 2) REP'],
                    ['Precinct', 'Times Cast', 'Registered Voters',
                     'Precinct', 'Rochelle M. Brown (REP)',
                     'Mary Cunningham (REP)', 'Evan Laurie (REP)',
                     'Total Votes', 'Unresolved Write-In'],
                    ['County', '', '', 'County', '', '', '', '', ''],
                    ['Tuscola County', '', '', 'Tuscola County',
                     '', '', '', '', ''],
                    ['Elmwood Township, Precinct 1', '281', '472',
                     'Elmwood Township, Precinct 1', '147', '100', '116',
                     '363', '1'],
                    ['Tuscola County - Total', '281', '472',
                     'Tuscola County - Total', '147', '100', '116', '363',
                     '1'],
                    ['Cumulative', '', '', 'Cumulative', '', '', '', '',
                     ''],
                    ['Cumulative', '0', '0', 'Cumulative', '0', '0', '0',
                     '0', '0'],
                    ['Cumulative - Total', '0', '0', 'Cumulative - Total',
                     '0', '0', '0', '0', '0'],
                    ['County - Total', '281', '472', 'County - Total',
                     '147', '100', '116', '363', '1'],
                ],
                # OCR dropped the Total Votes value ('507').
                247: [
                    ['Trustee for Watertown Township (REP) (Vote for 2) '
                     'REP'],
                    ['Precinct', 'Times Cast', 'Registered Voters',
                     'Precinct', 'Carl Block (REP)', 'Kayla Reed (REP)',
                     'Jamie Thatcher (REP)', 'Total Votes',
                     'Unresolved Write-In'],
                    ['County', '', '', 'County', '', '', '', '', ''],
                    ['Tuscola County', '', '', 'Tuscola County',
                     '', '', '', '', ''],
                    ['Watertown Township, Precinct 1', '518', '1,682',
                     'Watertown Township, Precinct 1', '164', '168', '175',
                     '507', '2'],
                    ['Tuscola County - Total', '518', '1,682',
                     'Tuscola County - Total', '164', '168', '175', '507',
                     '2'],
                    ['Cumulative', '', '', 'Cumulative', '', '', '', '',
                     ''],
                    ['Cumulative', '0', '0', 'Cumulative', '0', '0', '0',
                     '0', '0'],
                    ['Cumulative - Total', '0', '0', 'Cumulative - Total',
                     '0', '0', '0', '0', '0'],
                    ['County - Total', '518', '1,682', 'County - Total',
                     '164', '168', '175', '507', '2'],
                ],
                # The PDF wraps 'Koehler' as 'Koehl'/'er' across header
                # cells and OCR split them; values realigned from the
                # render.
                248: [
                    ['Trustee for Wells Township (REP) (Vote for 2) REP'],
                    ['Precinct', 'Times Cast', 'Registered Voters',
                     'Precinct', 'Terrie Flikkie (REP)',
                     'Jason D. Koehler (REP)', 'Total Votes',
                     'Unresolved Write-In'],
                    ['County', '', '', 'County', '', '', '', ''],
                    ['Tuscola County', '', '', 'Tuscola County',
                     '', '', '', ''],
                    ['Wells Township, Precinct 1', '534', '1,323',
                     'Wells Township, Precinct 1', '239', '266', '505',
                     '5'],
                    ['Tuscola County - Total', '534', '1,323',
                     'Tuscola County - Total', '239', '266', '505', '5'],
                    ['Cumulative', '', '', 'Cumulative', '', '', '', ''],
                    ['Cumulative', '0', '0', 'Cumulative', '0', '0', '0',
                     '0'],
                    ['Cumulative - Total', '0', '0', 'Cumulative - Total',
                     '0', '0', '0', '0'],
                    ['County - Total', '534', '1,323', 'County - Total',
                     '239', '266', '505', '5'],
                ],
            },
        'precincts': [
            'Akron Township, Precinct 1', 'Almer Charter Township, Precinct 1',
            'Arbela Township, Precinct 1', 'Arbela Township, Precinct 2',
            'Caro City, Precinct 1', 'Columbia Township, Precinct 1',
            'Dayton Township, Precinct 1', 'Denmark Township, Precinct 1',
            'Elkland Township, Precinct 1', 'Ellington Township, Precinct 1',
            'Elmwood Township, Precinct 1', 'Fairgrove Township, Precinct 1',
            'Fremont Township, Precinct 1', 'Gilford Township, Precinct 1',
            'Indianfields Township, Precinct 1',
            'Juniata Township, Precinct 1', 'Kingston Township, Precinct 1',
            'Koylton Township, Precinct 1', 'Millington Township, Precinct 1',
            'Novesta Township, Precinct 1', 'Tuscola Township, Precinct 1',
            'Vassar City, Precinct 1', 'Vassar Township, Precinct 1',
            'Watertown Township, Precinct 1', 'Wells Township, Precinct 1',
            'Wisner Township, Precinct 1',
        ],
        'name_fixes': {'Ellington Towship': 'Ellington Township'},
    },
    # Aug 2020 primary. Every contest is one merged 2-up table (left half
    # Undervotes/Overvotes, right half the results) that 'two_up' rewrites
    # to its right half; titles print WITHOUT the '(Vote for N)' group and
    # some pages print the title line AFTER the table. There is no
    # Times Cast column, so no Ballots Cast pseudo rows; write-ins are the
    # Unresolved Write-In column. The left half's Undervotes column is the
    # only ballot accounting, so nothing cross-checks turnout.
    'Tuscola 2020': {
        'county_name': 'Tuscola',
        'cache': 'Tuscola_MI_Statement_of_Votes_Cast_August_4_2020',
        'pages': 309,
        'out': '2020/counties/20200804__mi__primary__tuscola__precinct.csv',
        'two_up': True,
        'titles_without_vote_for': True,
        # Some pages print the contest title after its table (OCR order);
        # hoist single-cell title lines to the front of their page.
        'titles_first': True,
        # 'Delegate for <jur>' titles read jurisdiction-first elsewhere.
        'title_sub': [
            (r'^Delegate for (.+)$', r'\1 Delegate to County Convention'),
            # Trustee pages whose 2-up title row splits as
            # ['Trustee for REP', '<Twp> (REP)']: the two-up split keeps
            # only the right cell, so TITLE_NOVF reads the bare township
            # as the title. The DEM pages split as ['Trustee for DEM',
            # '<Twp> (DEM)'] or ['Trustee for', '| <Twp> (DEM)'] instead.
            (r'^Trustee for (?:DEM|REP) (.+)$', r'Trustee for \1'),
            (r'^Trustee for \| (.+)$', r'Trustee for \1'),
            # A bare '<Twp>'/'<Twp> Charter Township' title (no ' for '
            # inside — Supervisor/Clerk/Treasurer titles keep theirs).
            (r'^(?!.* for )((?:\w+ )*(?:Charter )?Township)$',
             r'Trustee for \1'),
        ],
        # OCR misspelling (results tables only; delegate/proposal titles
        # spell it correctly).
        'precinct_fixes': {'Arbeta': 'Arbela'},
        'precincts': [
            'Akron Township, Precinct 1', 'Almer Charter Township, Precinct 1',
            'Arbela Township, Precinct 1', 'Arbela Township, Precinct 2',
            'City of Caro, Precinct 1', 'City of Caro, Precinct 2',
            'Columbia Township, Precinct 1', 'Dayton Township, Precinct 1',
            'Denmark Township, Precinct 1', 'Elkland Township, Precinct 1',
            'Elkland Township, Precinct 2', 'Ellington Township, Precinct 1',
            'Elmwood Township, Precinct 1', 'Fairgrove Township, Precinct 1',
            'Fremont Township, Precinct 1', 'Gilford Township, Precinct 1',
            'Indianfields Township, Precinct 1', 'Juniata Township, Precinct 1',
            'Kingston Township, Precinct 1', 'Koylton Township, Precinct 1',
            'Millington Township, Precinct 1', 'Millington Township, Precinct 2',
            'Novesta Township, Precinct 1', 'Tuscola Township, Precinct 1',
            'City of Vassar, Precinct 1', 'Vassar Township, Precinct 1',
            'Watertown Township, Precinct 1', 'Wells Township, Precinct 1',
            'Wisner Township, Precinct 1',
        ],
        # p027's OCR dropped City of Caro Precinct 2's row entirely (render-
        # verified 0/11; the p028 spill footer's 343 only sums with it).
        # p073/p110 fused the precinct row into the footer row (render-
        # verified 0/7 and 0/12); the footer survives, the precinct row is
        # re-injected after it.
        'inject_after': {
            '27': [['City of Caro, Precinct 1',
                    ['City of Caro, Precinct 2', '0', '11']]],
            '73': [['Tuscola County - Total Cumulative',
                    ['Elmwood Township, Precinct 1', '0', '7']]],
            '110': [['Tuscola County - Total Cumulative',
                     ['Columbia Township, Precinct 1', '0', '12']]],
            # Single-precinct contests whose only data row fused into a
            # footer row (or was lost entirely); the footer names the
            # precinct's values, so re-inject the row after it.
            '79': [['County - Total',
                    ['Gilford Township, Precinct 1', '0', '6']]],
            '83': [['Tuscola County - Total Cumulative',
                    ['Juniata Township, Precinct 1', '0', '15']]],
            '113': [['County - Total',
                     ['Denmark Township, Precinct 1', '0', '9']]],
            '121': [['Tuscola County - Total Cumulative',
                     ['Fairgrove Township, Precinct 1', '0', '8']]],
            '129': [['County - Total',
                     ['Juniata Township, Precinct 1', '0', '13']]],
            '148': [['Tuscola County - Total Cumulative',
                     ['Wisner Township, Precinct 1', '0', '4']]],
            '149': [['County - Total',
                     ['Akron Township, Precinct 1', '0', '5']]],
            '159': [['County - Total',
                     ['Denmark Township, Precinct 1', '0', '10']]],
            '187': [['County - Total',
                     ['Vassar Township, Precinct 1', '0', '28']]],
            '191': [['County - Total',
                     ['Wells Township, Precinct 1', '0', '9']]],
            '194': [['County - Total',
                     ['Wisner Township, Precinct 1', '0', '2']]],
            '201': [['County - Total',
                     ['Columbia Township, Precinct 1', '0', '4']]],
            '225': [['Tuscola County - Total Cumulative',
                     ['Koylton Township, Precinct 1', '0', '3']]],
            '229': [['County - Total',
                     ['Novesta Township, Precinct 1', '0', '6']]],
            '230': [['County - Total',
                     ['Novesta Township, Precinct 1', '0', '8']]],
            '235': [['Cumulative - Total County - Total',
                     ['Watertown Township, Precinct 1', '0', '32']]],
            '237': [['Cumulative - Total County - Total',
                     ['Wells Township, Precinct 1', '0', '11']]],
            '259': [['County - Total',
                     ['Elkland Township, Precinct 1', '0', '8']]],
            '267': [['Tuscola County - Total Cumulative',
                     ['Fairgrove Township, Precinct 1', '0', '10']]],
            '271': [['County - Total',
                     ['Gilford Township, Precinct 1', '0', '9']]],
            '281': [['County - Total',
                     ['Millington Township, Precinct 1', '0', '12']]],
            '283': [['County - Total',
                     ['Millington Township, Precinct 2', '0', '10']]],
            # p288: the McKay row fused into the footer row
            # (1801 undervotes | McKay 305, total 305, unresolved 3).
            '288': [['County - Total',
                     ['Tuscola Township, Precinct 1', '305', '305', '3']]],
            '291': [['County - Total',
                     ['Vassar Township, Precinct 1', '0', '38']]],
            # p292: the six-candidate row fused into the footer row.
            '292': [['County - Total',
                     ['Vassar Township, Precinct 1', '303', '275', '158',
                      '143', '238', '243', '1360', '10']]],
            '298': [['Tuscola County - Total Cumulative',
                     ['Wisner Township, Precinct 1', '0', '4']]],
            '137': [['County - Total',
                     ['Novesta Township, Precinct 1', '0', '7']]],
            '143': [['County - Total',
                     ['Watertown Township, Precinct 1', '0', '22']]],
            '176': [['County - Total',
                     ['Juniata Township, Precinct 1', '0', '5']]],
            '183': [['County - Total',
                     ['Novesta Township, Precinct 1', '0', '6']]],
            '184': [['County - Total',
                     ['Novesta Township, Precinct 1', '0', '8']]],
            '185': [['County - Total',
                     ['Tuscola Township, Precinct 1', '0', '12']]],
            '188': [['County - Total',
                     ['Vassar Township, Precinct 1', '0', '19']]],
            '268': [['County - Total',
                     ['Fairgrove Township, Precinct 1', '0', '6']]],
        },
        # p237: a garbled 'Cumulative - Total' row IS a footer and wins
        # first-footer with unresolved=0 before the real footer's 11.
        'footer_overrides': {
            237: {'results:unresolved': 11},
            # p009's footer OCR shifted the Noland/Total/Unresolved values
            # one column left (render: Bizon 1195, Noland 971, total 2166,
            # unresolved 5).
            9: {'results:Kelly L. Noland': 971,
                'results:total_votes': 2166,
                'results:unresolved': 5},
        },
        # Pages OCR reduced to a stray '0': hand-transcribed from renders
        # (the Prosecuting Attorney DEM table and its spill page).
        'manual': {
            15: [
                ['Prosecuting Attorney (DEM) (Vote for 1)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '0', '3'],
                ['Almer Charter Township, Precinct 1', '0', '8'],
                ['Arbela Township, Precinct 1', '0', '22'],
                ['Arbela Township, Precinct 2', '0', '23'],
                ['City of Caro, Precinct 1', '0', '19'],
                ['City of Caro, Precinct 2', '0', '10'],
                ['Columbia Township, Precinct 1', '0', '4'],
                ['Dayton Township, Precinct 1', '0', '26'],
                ['Denmark Township, Precinct 1', '0', '10'],
                ['Elkland Township, Precinct 1', '0', '9'],
                ['Elkland Township, Precinct 2', '0', '5'],
                ['Ellington Township, Precinct 1', '0', '8'],
                ['Elmwood Township, Precinct 1', '0', '7'],
                ['Fairgrove Township, Precinct 1', '0', '13'],
                ['Fremont Township, Precinct 1', '0', '20'],
                ['Gilford Township, Precinct 1', '0', '6'],
                ['Indianfields Township, Precinct 1', '0', '18'],
                ['Juniata Township, Precinct 1', '0', '16'],
                ['Kingston Township, Precinct 1', '0', '5'],
                ['Koylton Township, Precinct 1', '0', '4'],
                ['Millington Township, Precinct 1', '0', '12'],
                ['Millington Township, Precinct 2', '0', '9'],
                ['Novesta Township, Precinct 1', '0', '6'],
                ['Tuscola Township, Precinct 1', '0', '12'],
            ],
            16: [
                ['City of Vassar, Precinct 1', '0', '20'],
                ['Vassar Township, Precinct 1', '0', '35'],
                ['Watertown Township, Precinct 1', '0', '22'],
                ['Wells Township, Precinct 1', '0', '9'],
                ['Wisner Township, Precinct 1', '0', '4'],
                ['Tuscola County - Total', '0', '365'],
            ],
            # p004: the U.S. Senator (DEM) spill page's fused lines carry
            # only the left half's junk; hand-transcribed from the render
            # (Peters runs unopposed, so the right half is [votes, total]).
            4: [
                ['City of Vassar, Precinct 1', '181', '181'],
                ['Vassar Township, Precinct 1', '212', '212'],
                ['Watertown Township, Precinct 1', '119', '119'],
                ['Wells Township, Precinct 1', '106', '106'],
                ['Wisner Township, Precinct 1', '60', '60'],
                ['Tuscola County - Total', '3362', '3362'],
            ],
            # p005: the U.S. Senator (REP) main table prints its title at
            # the page end, where it cannot be told apart from the
            # hand-injected title by drop_lines; manualize instead
            # (render-verified: James runs unopposed).
            5: [
                ['United States Senator (REP)'],
                ['Precinct', 'John James (REP)', 'Total Votes',
                 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '221', '221', '2'],
                ['Almer Charter Township, Precinct 1', '351', '351', '1'],
                ['Arbela Township, Precinct 1', '213', '213', '2'],
                ['Arbela Township, Precinct 2', '160', '160', '0'],
                ['City of Caro, Precinct 1', '329', '329', '1'],
                ['City of Caro, Precinct 2', '174', '174', '2'],
                ['Columbia Township, Precinct 1', '187', '187', '3'],
                ['Dayton Township, Precinct 1', '320', '320', '2'],
                ['Denmark Township, Precinct 1', '494', '494', '2'],
                ['Elkland Township, Precinct 1', '192', '192', '0'],
                ['Elkland Township, Precinct 2', '328', '328', '1'],
                ['Ellington Township, Precinct 1', '282', '282', '0'],
                ['Elmwood Township, Precinct 1', '184', '184', '2'],
                ['Fairgrove Township, Precinct 1', '203', '203', '3'],
                ['Fremont Township, Precinct 1', '453', '453', '2'],
                ['Gilford Township, Precinct 1', '116', '116', '0'],
                ['Indianfields Township, Precinct 1', '412', '412', '3'],
                ['Juniata Township, Precinct 1', '249', '249', '0'],
                ['Kingston Township, Precinct 1', '226', '226', '1'],
                ['Koylton Township, Precinct 1', '235', '235', '2'],
                ['Millington Township, Precinct 1', '333', '333', '6'],
                ['Millington Township, Precinct 2', '298', '298', '0'],
                ['Novesta Township, Precinct 1', '254', '254', '2'],
                ['Tuscola Township, Precinct 1', '324', '324', '3'],
            ],
            # p006: the U.S. Senator (REP) spill page's first block holds
            # the left half's undervotes/overvotes and a stray 'Total 919'
            # that would win the footer race; hand-transcribed.
            6: [
                ['City of Vassar, Precinct 1', '251', '251', '1'],
                ['Vassar Township, Precinct 1', '390', '390', '1'],
                ['Watertown Township, Precinct 1', '331', '331', '3'],
                ['Wells Township, Precinct 1', '257', '257', '1'],
                ['Wisner Township, Precinct 1', '93', '93', '1'],
                ['Tuscola County - Total', '7860', '7860', '47'],
            ],
            # p018: the Prosecuting Attorney (REP) spill page's fused lines
            # lose their right half (render-verified values).
            18: [
                ['City of Vassar, Precinct 1', '247', '247', '5'],
                ['Vassar Township, Precinct 1', '365', '365', '8'],
                ['Watertown Township, Precinct 1', '325', '325', '3'],
                ['Wells Township, Precinct 1', '252', '252', '3'],
                ['Wisner Township, Precinct 1', '86', '86', '1'],
                ['Tuscola County - Total', '7683', '7683', '76'],
            ],
            # p021: the Sheriff (REP) main table's label cells interleave
            # with wrapped fragments and its title prints at the page end
            # (where drop_lines cannot distinguish it from the injected
            # one); manualize instead (render-verified values).
            21: [
                ['Sheriff (REP)'],
                ['Precinct', 'Glen G. Skrent (REP)', 'Total Votes',
                 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '215', '215', '0'],
                ['Almer Charter Township, Precinct 1', '376', '376', '0'],
                ['Arbela Township, Precinct 1', '203', '203', '1'],
                ['Arbela Township, Precinct 2', '153', '153', '0'],
                ['City of Caro, Precinct 1', '356', '356', '0'],
                ['City of Caro, Precinct 2', '177', '177', '2'],
                ['Columbia Township, Precinct 1', '207', '207', '1'],
                ['Dayton Township, Precinct 1', '311', '311', '1'],
                ['Denmark Township, Precinct 1', '493', '493', '1'],
                ['Elkland Township, Precinct 1', '196', '196', '0'],
                ['Elkland Township, Precinct 2', '326', '326', '2'],
                ['Ellington Township, Precinct 1', '280', '280', '1'],
                ['Elmwood Township, Precinct 1', '180', '180', '1'],
                ['Fairgrove Township, Precinct 1', '208', '208', '0'],
                ['Fremont Township, Precinct 1', '436', '436', '1'],
                ['Gilford Township, Precinct 1', '115', '115', '0'],
                ['Indianfields Township, Precinct 1', '418', '418', '1'],
                ['Juniata Township, Precinct 1', '242', '242', '0'],
                ['Kingston Township, Precinct 1', '224', '224', '0'],
                ['Koylton Township, Precinct 1', '243', '243', '0'],
                ['Millington Township, Precinct 1', '342', '342', '3'],
                ['Millington Township, Precinct 2', '287', '287', '0'],
                ['Novesta Township, Precinct 1', '247', '247', '1'],
                ['Tuscola Township, Precinct 1', '324', '324', '0'],
            ],
            # p019: the Sheriff (DEM) main table collapsed to a stray '0';
            # hand-transcribed from the render (zero-candidate contest).
            19: [
                ['Sheriff (DEM)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '0', '5'],
                ['Almer Charter Township, Precinct 1', '0', '8'],
                ['Arbela Township, Precinct 1', '0', '21'],
                ['Arbela Township, Precinct 2', '0', '22'],
                ['City of Caro, Precinct 1', '0', '21'],
                ['City of Caro, Precinct 2', '0', '10'],
                ['Columbia Township, Precinct 1', '0', '4'],
                ['Dayton Township, Precinct 1', '0', '26'],
                ['Denmark Township, Precinct 1', '0', '9'],
                ['Elkland Township, Precinct 1', '0', '6'],
                ['Elkland Township, Precinct 2', '0', '4'],
                ['Ellington Township, Precinct 1', '0', '8'],
                ['Elmwood Township, Precinct 1', '0', '9'],
                ['Fairgrove Township, Precinct 1', '0', '12'],
                ['Fremont Township, Precinct 1', '0', '19'],
                ['Gilford Township, Precinct 1', '0', '6'],
                ['Indianfields Township, Precinct 1', '0', '16'],
                ['Juniata Township, Precinct 1', '0', '16'],
                ['Kingston Township, Precinct 1', '0', '5'],
                ['Koylton Township, Precinct 1', '0', '3'],
                ['Millington Township, Precinct 1', '0', '11'],
                ['Millington Township, Precinct 2', '0', '10'],
                ['Novesta Township, Precinct 1', '0', '6'],
                ['Tuscola Township, Precinct 1', '0', '11'],
            ],
            20: [
                ['City of Vassar, Precinct 1', '0', '23'],
                ['Vassar Township, Precinct 1', '0', '34'],
                ['Watertown Township, Precinct 1', '0', '22'],
                ['Wells Township, Precinct 1', '0', '9'],
                ['Wisner Township, Precinct 1', '0', '4'],
                ['Tuscola County - Total', '0', '360'],
            ],
            # p023's Clerk (DEM) main table collapsed to a stray '0' in the
            # OCR cache; hand-transcribed from the page render (zero-
            # candidate contest: Total Votes 0, Unresolved Write-In per
            # precinct; the contest footer [0, 341] prints on p024).
            23: [
                ['Clerk (DEM)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '0', '4'],
                ['Almer Charter Township, Precinct 1', '0', '8'],
                ['Arbela Township, Precinct 1', '0', '20'],
                ['Arbela Township, Precinct 2', '0', '21'],
                ['City of Caro, Precinct 1', '0', '19'],
                ['City of Caro, Precinct 2', '0', '11'],
                ['Columbia Township, Precinct 1', '0', '3'],
                ['Dayton Township, Precinct 1', '0', '23'],
                ['Denmark Township, Precinct 1', '0', '9'],
                ['Elkland Township, Precinct 1', '0', '5'],
                ['Elkland Township, Precinct 2', '0', '4'],
                ['Ellington Township, Precinct 1', '0', '9'],
                ['Elmwood Township, Precinct 1', '0', '7'],
                ['Fairgrove Township, Precinct 1', '0', '9'],
                ['Fremont Township, Precinct 1', '0', '19'],
                ['Gilford Township, Precinct 1', '0', '6'],
                ['Indianfields Township, Precinct 1', '0', '14'],
                ['Juniata Township, Precinct 1', '0', '16'],
                ['Kingston Township, Precinct 1', '0', '5'],
                ['Koylton Township, Precinct 1', '0', '3'],
                ['Millington Township, Precinct 1', '0', '10'],
                ['Millington Township, Precinct 2', '0', '10'],
                ['Novesta Township, Precinct 1', '0', '5'],
                ['Tuscola Township, Precinct 1', '0', '13'],
            ],
            # p024: the Clerk (DEM) spill page's fused lines lose their
            # right half (render-verified values).
            24: [
                ['City of Vassar, Precinct 1', '0', '20'],
                ['Vassar Township, Precinct 1', '0', '33'],
                ['Watertown Township, Precinct 1', '0', '22'],
                ['Wells Township, Precinct 1', '0', '9'],
                ['Wisner Township, Precinct 1', '0', '4'],
                ['Tuscola County - Total', '0', '341'],
            ],
            # p029/p030: the County Treasurer (REP) tables print as a 7-col
            # grid whose label column fuses with the overvote; the OCR's
            # role assignment scrambles (Undervotes became a candidate).
            # Hand-transcribed from the renders.
            29: [
                ['Treasurer (REP)'],
                ['Precinct', 'Ashley Bennett (REP)', 'Rita Papp (REP)',
                 'Total Votes', 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '152', '79', '231', '0'],
                ['Almer Charter Township, Precinct 1', '226', '168',
                 '394', '1'],
                ['Arbela Township, Precinct 1', '170', '39', '209', '0'],
                ['Arbela Township, Precinct 2', '121', '34', '155', '0'],
                ['City of Caro, Precinct 1', '224', '143', '367', '0'],
                ['City of Caro, Precinct 2', '111', '79', '190', '0'],
                ['Columbia Township, Precinct 1', '157', '59', '216', '1'],
                ['Dayton Township, Precinct 1', '228', '101', '329', '0'],
                ['Denmark Township, Precinct 1', '413', '97', '510', '0'],
                ['Elkland Township, Precinct 1', '133', '75', '208', '0'],
                ['Elkland Township, Precinct 2', '211', '126', '337', '0'],
                ['Ellington Township, Precinct 1', '187', '118', '305',
                 '0'],
                ['Elmwood Township, Precinct 1', '140', '55', '195', '0'],
                ['Fairgrove Township, Precinct 1', '150', '72', '222',
                 '0'],
                ['Fremont Township, Precinct 1', '339', '125', '464', '1'],
                ['Gilford Township, Precinct 1', '90', '25', '115', '0'],
                ['Indianfields Township, Precinct 1', '281', '177', '458',
                 '1'],
                ['Juniata Township, Precinct 1', '154', '107', '261', '0'],
                ['Kingston Township, Precinct 1', '163', '66', '229', '0'],
                ['Koylton Township, Precinct 1', '161', '83', '244', '0'],
                ['Millington Township, Precinct 1', '300', '61', '361',
                 '1'],
                ['Millington Township, Precinct 2', '247', '51', '298',
                 '1'],
                ['Novesta Township, Precinct 1', '129', '129', '258', '0'],
                ['Tuscola Township, Precinct 1', '247', '86', '333', '1'],
            ],
            30: [
                ['City of Vassar, Precinct 1', '204', '51', '255', '0'],
                ['Vassar Township, Precinct 1', '317', '97', '414', '0'],
                ['Watertown Township, Precinct 1', '230', '106', '336',
                 '0'],
                ['Wells Township, Precinct 1', '164', '111', '275', '1'],
                ['Wisner Township, Precinct 1', '73', '21', '94', '0'],
                ['Tuscola County - Total', '5722', '2541', '8263', '8'],
            ],
            # p031: the Register of Deeds (DEM) main table collapsed to a
            # stray '0' (render: zero-candidate contest).
            31: [
                ['Register of Deeds (DEM)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '0', '4'],
                ['Almer Charter Township, Precinct 1', '0', '8'],
                ['Arbela Township, Precinct 1', '0', '19'],
                ['Arbela Township, Precinct 2', '0', '21'],
                ['City of Caro, Precinct 1', '0', '19'],
                ['City of Caro, Precinct 2', '0', '10'],
                ['Columbia Township, Precinct 1', '0', '3'],
                ['Dayton Township, Precinct 1', '0', '24'],
                ['Denmark Township, Precinct 1', '0', '9'],
                ['Elkland Township, Precinct 1', '0', '5'],
                ['Elkland Township, Precinct 2', '0', '4'],
                ['Ellington Township, Precinct 1', '0', '8'],
                ['Elmwood Township, Precinct 1', '0', '7'],
                ['Fairgrove Township, Precinct 1', '0', '8'],
                ['Fremont Township, Precinct 1', '0', '19'],
                ['Gilford Township, Precinct 1', '0', '6'],
                ['Indianfields Township, Precinct 1', '0', '13'],
                ['Juniata Township, Precinct 1', '0', '16'],
                ['Kingston Township, Precinct 1', '0', '5'],
                ['Koylton Township, Precinct 1', '0', '3'],
                ['Millington Township, Precinct 1', '0', '11'],
                ['Millington Township, Precinct 2', '0', '10'],
                ['Novesta Township, Precinct 1', '0', '5'],
                ['Tuscola Township, Precinct 1', '0', '12'],
            ],
            32: [
                ['City of Vassar, Precinct 1', '0', '19'],
                ['Vassar Township, Precinct 1', '0', '32'],
                ['Watertown Township, Precinct 1', '0', '22'],
                ['Wells Township, Precinct 1', '0', '9'],
                ['Wisner Township, Precinct 1', '0', '4'],
                ['Tuscola County - Total', '0', '335'],
            ],
            # p034: the Register of Deeds (REP) spill page's fused lines
            # lose their right half (render-verified values).
            34: [
                ['City of Vassar, Precinct 1', '252', '252', '2'],
                ['Vassar Township, Precinct 1', '380', '380', '2'],
                ['Watertown Township, Precinct 1', '323', '323', '1'],
                ['Wells Township, Precinct 1', '259', '259', '0'],
                ['Wisner Township, Precinct 1', '82', '82', '1'],
                ['Tuscola County - Total', '7669', '7669', '21'],
            ],
            # p035: the County Road Commissioner (DEM) main table collapsed
            # to a stray '0' (render: zero-candidate contest).
            35: [
                ['County Road Commissioner (DEM)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '0', '4'],
                ['Almer Charter Township, Precinct 1', '0', '8'],
                ['Arbela Township, Precinct 1', '0', '19'],
                ['Arbela Township, Precinct 2', '0', '22'],
                ['City of Caro, Precinct 1', '0', '19'],
                ['City of Caro, Precinct 2', '0', '11'],
                ['Columbia Township, Precinct 1', '0', '5'],
                ['Dayton Township, Precinct 1', '0', '22'],
                ['Denmark Township, Precinct 1', '0', '9'],
                ['Elkland Township, Precinct 1', '0', '5'],
                ['Elkland Township, Precinct 2', '0', '4'],
                ['Ellington Township, Precinct 1', '0', '9'],
                ['Elmwood Township, Precinct 1', '0', '7'],
                ['Fairgrove Township, Precinct 1', '0', '8'],
                ['Fremont Township, Precinct 1', '0', '19'],
                ['Gilford Township, Precinct 1', '0', '6'],
                ['Indianfields Township, Precinct 1', '0', '13'],
                ['Juniata Township, Precinct 1', '0', '16'],
                ['Kingston Township, Precinct 1', '0', '4'],
                ['Koylton Township, Precinct 1', '0', '3'],
                ['Millington Township, Precinct 1', '0', '11'],
                ['Millington Township, Precinct 2', '0', '10'],
                ['Novesta Township, Precinct 1', '0', '5'],
                ['Tuscola Township, Precinct 1', '0', '13'],
            ],
            36: [
                ['City of Vassar, Precinct 1', '0', '20'],
                ['Vassar Township, Precinct 1', '0', '33'],
                ['Watertown Township, Precinct 1', '0', '22'],
                ['Wells Township, Precinct 1', '0', '9'],
                ['Wisner Township, Precinct 1', '0', '4'],
                ['Tuscola County - Total', '0', '340'],
            ],
            # p038: the County Road Commissioner (REP) spill page's fused
            # lines lose their right half (render-verified values).
            38: [
                ['City of Vassar, Precinct 1', '248', '248', '1'],
                ['Vassar Township, Precinct 1', '372', '372', '3'],
                ['Watertown Township, Precinct 1', '309', '309', '2'],
                ['Wells Township, Precinct 1', '251', '251', '1'],
                ['Wisner Township, Precinct 1', '80', '80', '1'],
                ['Tuscola County - Total', '7516', '7516', '30'],
            ],
            # p039: the County Road Commissioner - Partial Term (DEM) main
            # table collapsed to a stray '0' (render: zero-candidate).
            39: [
                ['County Road Commissioner - Partial Term (DEM)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '0', '4'],
                ['Almer Charter Township, Precinct 1', '0', '7'],
                ['Arbela Township, Precinct 1', '0', '19'],
                ['Arbela Township, Precinct 2', '0', '20'],
                ['City of Caro, Precinct 1', '0', '19'],
                ['City of Caro, Precinct 2', '0', '10'],
                ['Columbia Township, Precinct 1', '0', '3'],
                ['Dayton Township, Precinct 1', '0', '23'],
                ['Denmark Township, Precinct 1', '0', '9'],
                ['Elkland Township, Precinct 1', '0', '5'],
                ['Elkland Township, Precinct 2', '0', '4'],
                ['Ellington Township, Precinct 1', '0', '9'],
                ['Elmwood Township, Precinct 1', '0', '7'],
                ['Fairgrove Township, Precinct 1', '0', '8'],
                ['Fremont Township, Precinct 1', '0', '19'],
                ['Gilford Township, Precinct 1', '0', '5'],
                ['Indianfields Township, Precinct 1', '0', '13'],
                ['Juniata Township, Precinct 1', '0', '15'],
                ['Kingston Township, Precinct 1', '0', '5'],
                ['Koylton Township, Precinct 1', '0', '3'],
                ['Millington Township, Precinct 1', '0', '11'],
                ['Millington Township, Precinct 2', '0', '9'],
                ['Novesta Township, Precinct 1', '0', '6'],
                ['Tuscola Township, Precinct 1', '0', '10'],
            ],
            40: [
                ['City of Vassar, Precinct 1', '0', '21'],
                ['Vassar Township, Precinct 1', '0', '33'],
                ['Watertown Township, Precinct 1', '0', '22'],
                ['Wells Township, Precinct 1', '0', '9'],
                ['Wisner Township, Precinct 1', '0', '4'],
                ['Tuscola County - Total', '0', '332'],
            ],
            # p042: the County Road Commissioner - Partial Term (REP) spill
            # page's fused lines lose their right half (render-verified).
            42: [
                ['City of Vassar, Precinct 1', '250', '250', '0'],
                ['Vassar Township, Precinct 1', '383', '383', '2'],
                ['Watertown Township, Precinct 1', '313', '313', '1'],
                ['Wells Township, Precinct 1', '243', '243', '0'],
                ['Wisner Township, Precinct 1', '77', '77', '1'],
                ['Tuscola County - Total', '7454', '7454', '29'],
            ],
            # p043: the Drain Commissioner (DEM) main table collapsed to a
            # stray '0' (render, rotated: zero-candidate contest).
            43: [
                ['Drain Commissioner (DEM)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '0', '4'],
                ['Almer Charter Township, Precinct 1', '0', '7'],
                ['Arbela Township, Precinct 1', '0', '17'],
                ['Arbela Township, Precinct 2', '0', '21'],
                ['City of Caro, Precinct 1', '0', '19'],
                ['City of Caro, Precinct 2', '0', '11'],
                ['Columbia Township, Precinct 1', '0', '4'],
                ['Dayton Township, Precinct 1', '0', '23'],
                ['Denmark Township, Precinct 1', '0', '9'],
                ['Elkland Township, Precinct 1', '0', '5'],
                ['Elkland Township, Precinct 2', '0', '4'],
                ['Ellington Township, Precinct 1', '0', '8'],
                ['Elmwood Township, Precinct 1', '0', '7'],
                ['Fairgrove Township, Precinct 1', '0', '8'],
                ['Fremont Township, Precinct 1', '0', '17'],
                ['Gilford Township, Precinct 1', '0', '4'],
                ['Indianfields Township, Precinct 1', '0', '12'],
                ['Juniata Township, Precinct 1', '0', '14'],
                ['Kingston Township, Precinct 1', '0', '5'],
                ['Koylton Township, Precinct 1', '0', '3'],
                ['Millington Township, Precinct 1', '0', '10'],
                ['Millington Township, Precinct 2', '0', '8'],
                ['Novesta Township, Precinct 1', '0', '5'],
                ['Tuscola Township, Precinct 1', '0', '12'],
            ],
            44: [
                ['City of Vassar, Precinct 1', '0', '18'],
                ['Vassar Township, Precinct 1', '0', '29'],
                ['Watertown Township, Precinct 1', '0', '18'],
                ['Wells Township, Precinct 1', '0', '8'],
                ['Wisner Township, Precinct 1', '0', '3'],
                ['Tuscola County - Total', '0', '313'],
            ],
            # p203: OCR split the title across three lines (opening a
            # phantom 'Dayton Township (DEM)' contest) and read the single
            # data row three times (render: 0 / 25).
            203: [
                ['Trustee for Dayton Township (DEM)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Dayton Township, Precinct 1', '0', '25'],
                ['Tuscola County - Total', '0', '25'],
            ],
            # p228: OCR interleaved the two precincts' values across rows
            # and lost the title (render-verified values).
            228: [
                ['Trustee for Millington Township (REP)'],
                ['Precinct', 'Allen Green (REP)', 'Luanne Jaruzel (REP)',
                 'Edwyn R. Maschke (REP)', 'Bob Worth (REP)',
                 'Total Votes', 'Unresolved Write-In'],
                ['Millington Township, Precinct 1', '169', '218', '88',
                 '161', '636', '1'],
                ['Millington Township, Precinct 2', '155', '210', '79',
                 '132', '576', '0'],
                ['Tuscola County - Total', '324', '428', '167', '293',
                 '1212', '1'],
            ],
            # p168/p169: single-precinct zero-candidate contests whose data
            # row fused into a 'Precinct County - Total' row (p168) or was
            # lost entirely (p169); hand-transcribed from renders.
            168: [
                ['Treasurer for Fairgrove Township (REP)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Fairgrove Township, Precinct 1', '0', '12'],
                ['Tuscola County - Total', '0', '12'],
            ],
            169: [
                ['Treasurer for Fremont Township (DEM)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Fremont Township, Precinct 1', '0', '20'],
                ['Tuscola County - Total', '0', '20'],
            ],
            # p114: title line lost and data row fused into the footer
            # (Schiefer 459 / total 459 / unresolved 0); p144/p170: title
            # split across lines and the row fused into a 'Precinct
            # County - Total' footer — hand-transcribed from renders.
            114: [
                ['Clerk for Denmark Township (REP)'],
                ['Precinct', 'Renée Louise Schiefer (REP)', 'Total Votes',
                 'Unresolved Write-In'],
                ['Denmark Township, Precinct 1', '459', '459', '0'],
                ['Tuscola County - Total', '459', '459', '0'],
            ],
            144: [
                ['Clerk for Watertown Township (REP)'],
                ['Precinct', 'Malisa Pyles (REP)', 'Total Votes',
                 'Unresolved Write-In'],
                ['Watertown Township, Precinct 1', '334', '334', '3'],
                ['Tuscola County - Total', '334', '334', '3'],
            ],
            170: [
                ['Treasurer for Fremont Township (REP)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Fremont Township, Precinct 1', '0', '36'],
                ['Tuscola County - Total', '0', '36'],
            ],
            # p181: stair-step table — the right half's rows sit one grid
            # row below the left half's, so each precinct's unresolved
            # value OCR'd onto the next row (11 + 9 = 20 = footer).
            181: [
                ['Treasurer for Millington Township (DEM)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Millington Township, Precinct 1', '0', '11'],
                ['Millington Township, Precinct 2', '0', '9'],
                ['Tuscola County - Total', '0', '20'],
            ],
            265: [
                ['Delegate for Elmwood Township, Precinct 1 (DEM)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Elmwood Township, Precinct 1', '0', '9'],
                ['Tuscola County - Total', '0', '9'],
            ],
            272: [
                ['Delegate for Gilford Township, Precinct 1 (REP)'],
                ['Total Votes', 'Unresolved Write-In'],
                ['Gilford Township, Precinct 1', '0', '12'],
                ['Tuscola County - Total', '0', '12'],
            ],
            # p299/p300: the County Proposal's title line OCR dropped, so
            # its main table leaked into the preceding Wisner delegate
            # contest; hand-transcribed with the title restored (the
            # proposal's footer prints on the p300 spill).
            299: [
                ['County Proposal for Tuscola County (Vote for 1)'],
                ['Precinct', 'Yes', 'No', 'Total Votes',
                 'Unresolved Write-In'],
                ['Akron Township, Precinct 1', '293', '36', '329', '0'],
                ['Almer Charter Township, Precinct 1', '487', '96',
                 '583', '0'],
                ['Arbela Township, Precinct 1', '293', '32', '325', '0'],
                ['Arbela Township, Precinct 2', '235', '35', '270', '0'],
                ['City of Caro, Precinct 1', '508', '80', '588', '0'],
                ['City of Caro, Precinct 2', '273', '38', '311', '0'],
                ['Columbia Township, Precinct 1', '292', '33', '325', '0'],
                ['Dayton Township, Precinct 1', '378', '66', '444', '0'],
                ['Denmark Township, Precinct 1', '584', '114', '698', '0'],
                ['Elkland Township, Precinct 1', '264', '42', '306', '0'],
                ['Elkland Township, Precinct 2', '404', '62', '466', '0'],
                ['Ellington Township, Precinct 1', '317', '60', '377', '0'],
                ['Elmwood Township, Precinct 1', '207', '47', '254', '0'],
                ['Fairgrove Township, Precinct 1', '234', '42', '276', '0'],
                ['Fremont Township, Precinct 1', '524', '121', '645', '0'],
                ['Gilford Township, Precinct 1', '136', '24', '160', '0'],
                ['Indianfields Township, Precinct 1', '574', '97', '671',
                 '0'],
                ['Juniata Township, Precinct 1', '305', '93', '398', '0'],
                ['Kingston Township, Precinct 1', '214', '78', '292', '0'],
                ['Koylton Township, Precinct 1', '263', '60', '323', '0'],
                ['Millington Township, Precinct 1', '458', '70', '528',
                 '0'],
                ['Millington Township, Precinct 2', '407', '53', '460',
                 '0'],
                ['Novesta Township, Precinct 1', '265', '50', '315', '0'],
                ['Tuscola Township, Precinct 1', '391', '43', '434', '0'],
            ],
            300: [
                ['City of Vassar, Precinct 1', '354', '38', '392', '0'],
                ['Vassar Township, Precinct 1', '490', '90', '580', '0'],
                ['Watertown Township, Precinct 1', '417', '55', '472',
                 '0'],
                ['Wells Township, Precinct 1', '297', '69', '366', '0'],
                ['Wisner Township, Precinct 1', '121', '17', '138', '0'],
                ['Tuscola County - Total', '9985', '1741', '11726', '0'],
            ],
            # Single-precinct township pages whose rows OCR garbled or
            # dropped; hand-transcribed from the page renders.
            99: [
                ['Supervisor for Wells Township (DEM)'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['Wells Township, Precinct 1', '0', '9'],
                ['Tuscola County - Total', '0', '9'],
            ],
            # p130: the Clerk for Juniata (REP) page prints no Total Votes
            # column value that works — the contest's only candidate is a
            # qualified write-in (Brenda Bigham, 51 votes) and the report
            # prints Total Votes 0 beside 57 unresolved write-ins. The
            # manual drops the Total Votes column so the parser's
            # candidates-vs-total residual logic (which would fold the
            # unresolved 57 into a bogus Write-In delta) stays out of the
            # way; the emitted rows are Bigham 51 + Write-In 57.
            130: [
                ['Clerk for Juniata Township (REP)'],
                ['Precinct', 'Brenda Bigham (W)', 'Unresolved Write-In'],
                ['Juniata Township, Precinct 1', '51', '57'],
                ['Tuscola County - Total', '51', '57'],
            ],
            135: [
                ['Clerk for Millington Township (DEM)'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['Millington Township, Precinct 1', '0', '10'],
                ['Millington Township, Precinct 2', '0', '9'],
                ['Tuscola County - Total', '0', '19'],
            ],
            145: [
                ['Clerk for Wells Township (DEM)'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['Wells Township, Precinct 1', '0', '9'],
                ['Tuscola County - Total', '0', '9'],
            ],
            161: [
                ['Treasurer for Elkland Township (DEM)'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['Elkland Township, Precinct 1', '0', '4'],
                ['Elkland Township, Precinct 2', '0', '4'],
                ['Tuscola County - Total', '0', '8'],
            ],
            200: [
                ['Trustee for Arbela Township (REP)'],
                ['Precinct', 'Timothy M. Anderson (REP)',
                 'Gary Woelzlein (REP)', 'Total Votes',
                 'Unresolved Write-In'],
                ['Arbela Township, Precinct 1', '138', '158', '296', '0'],
                ['Arbela Township, Precinct 2', '99', '104', '203', '0'],
                ['Tuscola County - Total', '237', '262', '499', '0'],
            ],
            204: [
                ['Trustee for Dayton Township (REP)'],
                ['Precinct', 'Greg Lottes (REP)', 'Robert W. Steele (REP)',
                 'Total Votes', 'Unresolved Write-In'],
                ['Dayton Township, Precinct 1', '207', '230', '437', '0'],
                ['Tuscola County - Total', '207', '230', '437', '0'],
            ],
            209: [
                ['Trustee for Ellington Township (DEM)'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['Ellington Township, Precinct 1', '0', '9'],
                ['Tuscola County - Total', '0', '9'],
            ],
            213: [
                ['Trustee for Fairgrove Township (DEM)'],
                ['Precinct', 'Michael W. Day (DEM)', 'Total Votes',
                 'Unresolved Write-In'],
                ['Fairgrove Township, Precinct 1', '88', '88', '1'],
                ['Tuscola County - Total', '88', '88', '1'],
            ],
            214: [
                ['Trustee for Fairgrove Township (REP)'],
                ['Precinct', 'Justin G. Edwards (REP)',
                 'Dennis J. Hadeway (REP)', 'Total Votes',
                 'Unresolved Write-In'],
                ['Fairgrove Township, Precinct 1', '138', '168', '306',
                 '0'],
                ['Tuscola County - Total', '138', '168', '306', '0'],
            ],
            # p276: the delegate page's Robert Harrison write-in column is
            # a real (qualified) write-in candidate, so the manual names it
            # with the (W) tag; the printed Total Votes (372) counts only
            # the two named candidates, so it is dropped here for the same
            # reason as p130's.
            276: [
                ['Delegate for Juniata Township, Precinct 1 (REP)'],
                ['Precinct', 'Melvin Dean Campbell (REP)',
                 'Susan Campbell (REP)', 'Robert Harrison (W)',
                 'Unresolved Write-In'],
                ['Juniata Township, Precinct 1', '189', '183', '3', '5'],
                ['Tuscola County - Total', '189', '183', '3', '5'],
            ],
        },
        # Pages whose title line OCR printed after the table (or garbled); the
        # hand-read title opens the contest at the top of the page and the
        # trailing line is dropped so the contest stays open across the
        # spill page.
        'page_titles': {
            # Pages whose title line the OCR dropped entirely (hand-read
            # from the page renders); the title opens the page's contest.
            58: 'Supervisor for Akron Township (REP)',
            60: 'Supervisor for Almer Charter Township (REP)',
            64: 'Supervisor for Columbia Township (REP)',
            67: 'Supervisor for Denmark Township (DEM)',
            71: 'Supervisor for Ellington Township (DEM)',
            75: 'Supervisor for Fairgrove Township (DEM)',
            76: 'Supervisor for Fairgrove Township (REP)',
            80: 'Supervisor for Gilford Township (REP)',
            81: 'Supervisor for Indianfields Township (DEM)',
            82: 'Supervisor for Indianfields Township (REP)',
            85: 'Supervisor for Kingston Township (DEM)',
            89: 'Supervisor for Millington Township (DEM)',
            90: 'Supervisor for Millington Township (REP)',
            93: 'Supervisor for Tuscola Township (DEM)',
            201: 'Trustee for Columbia Township (DEM)',
            202: 'Trustee for Columbia Township (REP)',
            # p215's OCR title line lost the township ('Trustee for DEM'),
            # so nothing opens the contest and its rows leak into the
            # still-open Fairgrove trustee contest.
            215: 'Trustee for Fremont Township (DEM)',
            239: 'Trustee for Wisner Township (DEM)',
            244: 'Delegate for Almer Charter Township, Precinct 1 (REP)',
            248: 'Delegate for Arbela Township, Precinct 2 (REP)',
            250: 'Delegate for City of Caro, Precinct 1 (REP)',
            251: 'Delegate for City of Caro, Precinct 2 (DEM)',
            255: 'Delegate for Dayton Township, Precinct 1 (DEM)',
            260: 'Delegate for Elkland Township, Precinct 1 (REP)',
            262: 'Delegate for Elkland Township, Precinct 2 (REP)',
            263: 'Delegate for Ellington Township, Precinct 1 (DEM)',
            269: 'Delegate for Fremont Township, Precinct 1 (DEM)',
            271: 'Delegate for Gilford Township, Precinct 1 (DEM)',
            273: 'Delegate for Indianfields Township, Precinct 1 (DEM)',
            274: 'Delegate for Indianfields Township, Precinct 1 (REP)',
            # 276 is manualized (its write-in candidate column needs a
            # hand header); the manual carries its own title row.
            283: 'Delegate for Millington Township, Precinct 2 (DEM)',
            285: 'Delegate for Novesta Township, Precinct 1 (DEM)',
            286: 'Delegate for Novesta Township, Precinct 1 (REP)',
            288: 'Delegate for Tuscola Township, Precinct 1 (REP)',
            289: 'Delegate for City of Vassar, Precinct 1 (DEM)',
            292: 'Delegate for Vassar Township, Precinct 1 (REP)',
            295: 'Delegate for Wells Township, Precinct 1 (DEM)',
            # The closing proposals (OCR drops their '(Vote for 1)' tag, so
            # PROPOSAL never matches and their rows leak into the still-open
            # County Proposal contest).
            301: 'Township Proposal for Akron Township (Vote for 1)',
            302: 'Township Proposal for Almer Charter Township (Vote for 1)',
            303: 'Township Proposal for Columbia Township (Vote for 1)',
            304: 'Township Proposal for Denmark Township (Vote for 1)',
            305: 'Township Proposal for Elkland Township (Vote for 1)',
            306: 'Township Proposal for Indianfields Township (Vote for 1)',
            307: 'Township Proposal for Koylton Township (Vote for 1)',
            308: 'School District Proposal for Unionville-Sebewaing Area '
                 'School District (Vote for 1)',
            309: 'District Library Proposal for Caro Area District Library '
                 '(Vote for 1)',
        },
        'drop_lines': {},
    },
    'Montmorency 2020': {
        'county_name': 'Montmorency',
        'cache': 'Montmorency_MI_Aug_2020_Election_Results',
        'pages': 188,
        'out': '2020/counties/20200804__mi__primary__montmorency__precinct.csv',
        # One contest per page; OCR prints the title AFTER its table
        # (page stamps first), so hoist matched titles to the front.
        'titles_first': True,
        'title_sub': [
            # OCR prints 'Delegates to' / 'Delegate to' / 'Delegated to'.
            (r'^Delega(?:tes?|ted) to (?:the )?County Convention for (.+)$',
             r'\1 Delegate to County Convention'),
            (r'^County rosecuting Attorney', r'County Prosecuting Attorney'),
            (r'^County Oneriff', r'County Sheriff'),
            (r'^County treasurer', r'County Treasurer'),
            ('Townsnip', 'Township'),
            # 'County Commissioner for County Commissioner District 3'
            # must become 'County Commissioner 3 District' for the
            # parser's COMMISSIONER rule to set the district column.
            (r'^County Commissioner for County Commissioner District '
             r'(\d+)$', r'County Commissioner \g<1> District'),
            # Federal/state titles carry a ' for State' suffix that
            # blocks the DISTRICT mapping ('U.S. House'/'State House'),
            # and county proposal titles repeat the county name.
            (r' for State$', ''),
            (r' for Montmorency County, Montmorency County Michigan$', ''),
            ('Porposal', 'Proposal'),
        ],
        # p116 OCR truncated the double-n name: rendered image reads
        # 'Debra Villenneuve'.
        'cand_fixes': {'Debra Villeneuv': 'Debra Villenneuve'},
        'page_titles': {
            9: 'Representative in Congress 1st District for State '
               '(DEM) (Vote for 1) DEM',
            30: 'Township Clerk for Albert Township (DEM) (Vote for 1) DEM',
            32: 'Township Clerk for Briley Township (DEM) (Vote for 1) DEM',
            43: 'Township Treasurer for Montmorency Township (DEM) '
                '(Vote for 1) DEM',
            47: 'Township Trustee for Avery Township (DEM) (Vote for 2) DEM',
            65: 'Representative in State Legislature 105th District for '
                'State (REP) (Vote for 1) REP',
            144: 'Hillman Township Millage For Fire Department Operation '
                 'And Equipment (Vote for 1)',
            154: 'Vienna Township Millage Renewal For Emergency Medical '
                 'Service (Vote for 1)',
            # Manualized pages carry no OCR title; inject the render- or
            # stream-verified title so the contest opens on this page.
            12: 'County Prosecuting Attorney for Montmorency County '
                '(DEM) (Vote for 1) DEM',
            13: 'County Sheriff for Montmorency County (DEM) '
                '(Vote for 1) DEM',
            14: 'County Clerk for Montmorency County (DEM) '
                '(Vote for 1) DEM',
            15: 'County Treasurer for Montmorency County (DEM) '
                '(Vote for 1) DEM',
            16: 'County Register of Deeds for Montmorency County (DEM) '
                '(Vote for 1) DEM',
            18: 'County Commissioner for County Commissioner District 1 '
                '(DEM) (Vote for 1) DEM',
            23: 'Township Supervisor for Albert Township (DEM) '
                '(Vote for 1) DEM',
            27: 'Township Supervisor for Montmorency Township (DEM) '
                '(Vote for 1) DEM',
            36: 'Township Clerk for Rust Township (DEM) (Vote for 1) DEM',
            41: 'Township Treasurer for Hillman Township (DEM) '
                '(Vote for 1) DEM',
            49: 'Township Trustee for Hillman Township (DEM) '
                '(Vote for 2) DEM',
            51: 'Township Trustee for Montmorency Township (DEM) '
                '(Vote for 2) DEM',
            74: 'County Commissioner for County Commissioner District 1 '
                '(REP) (Vote for 1) REP',
            81: 'Township Supervisor for Briley Township (REP) '
                '(Vote for 1) REP',
            110: 'Township Trustee for Briley Township (REP) '
                 '(Vote for 2) REP',
            115: 'Township Trustee for Montmorency Township (REP) '
                 '(Vote for 2) REP',
            119: 'Township Trustee for Vienna Township (REP) '
                 '(Vote for 2) REP',
            # p127's OCR title jams the date stamp between the vote-for
            # group and the party ('... (Vote for 2) 8/6/2020 3:22:29 PM
            # REP'); inject the clean title and drop the garbled one.
            127: 'Montmorency Township, Precinct 2 Delegate to County '
                 'Convention (REP) (Vote for 2) REP',
            128: 'Rust Township, Precinct 1 Delegate to County '
                 'Convention (REP) (Vote for 2) REP',
            # The manualized J-L proposal page carries the countywide
            # totals of all three member counties in its OCR table; the
            # manual recomputes Montmorency-only totals, but the OCR
            # title line is dropped with the rest of the page, so
            # inject it (else the rows continue the Vienna EMS contest).
            156: 'Johannesburg-Lewiston Area Schools Bonding Proposal '
                 '(Vote for 1)',
            # Manualized 2-up pages: inject their clean OCR titles.
            20: 'County Commissioner for County Commissioner District 3 '
                '(DEM) (Vote for 1) DEM',
            25: 'Township Supervisor for Briley Township (DEM) '
                '(Vote for 1) DEM',
            34: 'Township Clerk for Loud Township (DEM) (Vote for 1) DEM',
            39: 'Township Treasurer for Avery Township (DEM) '
                '(Vote for 1) DEM',
            62: 'Vienna Township, Precinct 1 Delegate to County '
                'Convention (DEM) (Vote for 2) DEM',
            77: 'County Commissioner for County Commissioner District 4 '
                '(REP) (Vote for 1) REP',
            126: 'Montmorency Township, Precinct 1 Delegate to County '
                 'Convention (REP) (Vote for 2) REP',
            146: 'Montmorency Township Millage Proposal For Operating '
                 'Refuse Transfer Sites and Equipment (Vote for 1)',
        },
        'drop_lines': {
            127: ['Delegated to the County Convention'],
        },
        'precincts': [
            'Albert Township, Precinct 1', 'Avery Township, Precinct 1',
            'Briley Township, Precinct 1', 'Hillman Township, Precinct 1',
            'Loud Township, Precinct 1', 'Montmorency Township, Precinct 1',
            'Montmorency Township, Precinct 2', 'Rust Township, Precinct 1',
            'Vienna Township, Precinct 1',
        ],
        'manual': {
            # OCR split the results header across two rows and garbled
            # the title ('(Dr.M)'); inject the whole contest.
            9: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Albert Township, Precinct 1', '903', '2,170'],
                ['Avery Township, Precinct 1', '210', '569'],
                ['Briley Township, Precinct 1', '644', '1,715'],
                ['Hillman Township, Precinct 1', '659', '1,637'],
                ['Loud Township, Precinct 1', '107', '243'],
                ['Montmorency Township, Precinct 1', '257', '530'],
                ['Montmorency Township, Precinct 2', '231', '441'],
                ['Rust Township, Precinct 1', '183', '422'],
                ['Vienna Township, Precinct 1', '208', '476'],
                ['Montmorency County Michigan - Total', '3,402', '8,203'],
                ['County - Total', '3,402', '8,203'],
                ['Precinct', 'Dana Ferguson', "Linda O'Dell", 'Total Votes'],
                ['County', '', '', ''],
                ['Albert Township, Precinct 1', '107', '60', '167'],
                ['Avery Township, Precinct 1', '17', '20', '37'],
                ['Briley Township, Precinct 1', '56', '30', '86'],
                ['Hillman Township, Precinct 1', '63', '48', '111'],
                ['Loud Township, Precinct 1', '14', '11', '25'],
                ['Montmorency Township, Precinct 1', '38', '20', '58'],
                ['Montmorency Township, Precinct 2', '23', '18', '41'],
                ['Rust Township, Precinct 1', '10', '8', '18'],
                ['Vienna Township, Precinct 1', '24', '13', '37'],
                ['Montmorency County Michigan - Total', '352', '228', '580'],
                ['County - Total', '352', '228', '580'],
            ],
            # Unresolved Write-In column spilled onto its own page.
            10: [
                ['Precinct', 'Unresolved Write-In'], ['County', ''],
                ['Albert Township, Precinct 1', '0'],
                ['Avery Township, Precinct 1', '0'],
                ['Briley Township, Precinct 1', '0'],
                ['Hillman Township, Precinct 1', '0'],
                ['Loud Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Rust Township, Precinct 1', '1'],
                ['Vienna Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '1'],
                ['County - Total', '1'],
            ],
            # p012-p016: the DEM county-office results tables collapse to
            # stacked single cells (zero candidates, so only Total Votes
            # and Unresolved Write-In). Values from the OCR stream and
            # page-image reads; each column sums to the printed total.
            12: [
                *mont_aux9(),
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Albert Township, Precinct 1', '0', '31'],
                ['Avery Township, Precinct 1', '0', '6'],
                ['Briley Township, Precinct 1', '0', '22'],
                ['Hillman Township, Precinct 1', '0', '27'],
                ['Loud Township, Precinct 1', '0', '4'],
                ['Montmorency Township, Precinct 1', '0', '13'],
                ['Montmorency Township, Precinct 2', '0', '6'],
                ['Rust Township, Precinct 1', '0', '3'],
                ['Vienna Township, Precinct 1', '0', '1'],
                ['Montmorency County Michigan - Total', '0', '113'],
                ['County - Total', '0', '113'],
            ],
            13: [
                *mont_aux9(),
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Albert Township, Precinct 1', '0', '28'],
                ['Avery Township, Precinct 1', '0', '5'],
                ['Briley Township, Precinct 1', '0', '23'],
                ['Hillman Township, Precinct 1', '0', '27'],
                ['Loud Township, Precinct 1', '0', '4'],
                ['Montmorency Township, Precinct 1', '0', '13'],
                ['Montmorency Township, Precinct 2', '0', '6'],
                ['Rust Township, Precinct 1', '0', '3'],
                ['Vienna Township, Precinct 1', '0', '4'],
                ['Montmorency County Michigan - Total', '0', '113'],
                ['County - Total', '0', '113'],
            ],
            14: [
                *mont_aux9(),
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Albert Township, Precinct 1', '0', '29'],
                ['Avery Township, Precinct 1', '0', '5'],
                ['Briley Township, Precinct 1', '0', '20'],
                ['Hillman Township, Precinct 1', '0', '27'],
                ['Loud Township, Precinct 1', '0', '4'],
                ['Montmorency Township, Precinct 1', '0', '13'],
                ['Montmorency Township, Precinct 2', '0', '6'],
                ['Rust Township, Precinct 1', '0', '3'],
                ['Vienna Township, Precinct 1', '0', '3'],
                ['Montmorency County Michigan - Total', '0', '110'],
                ['County - Total', '0', '110'],
            ],
            15: [
                *mont_aux9(),
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Albert Township, Precinct 1', '0', '28'],
                ['Avery Township, Precinct 1', '0', '5'],
                ['Briley Township, Precinct 1', '0', '22'],
                ['Hillman Township, Precinct 1', '0', '26'],
                ['Loud Township, Precinct 1', '0', '4'],
                ['Montmorency Township, Precinct 1', '0', '12'],
                ['Montmorency Township, Precinct 2', '0', '6'],
                ['Rust Township, Precinct 1', '0', '3'],
                ['Vienna Township, Precinct 1', '0', '2'],
                ['Montmorency County Michigan - Total', '0', '108'],
                ['County - Total', '0', '108'],
            ],
            16: [
                *mont_aux9(),
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Albert Township, Precinct 1', '0', '29'],
                ['Avery Township, Precinct 1', '0', '6'],
                ['Briley Township, Precinct 1', '0', '21'],
                ['Hillman Township, Precinct 1', '0', '25'],
                ['Loud Township, Precinct 1', '0', '4'],
                ['Montmorency Township, Precinct 1', '0', '13'],
                ['Montmorency Township, Precinct 2', '0', '6'],
                ['Rust Township, Precinct 1', '0', '3'],
                ['Vienna Township, Precinct 1', '0', '4'],
                ['Montmorency County Michigan - Total', '0', '111'],
                ['County - Total', '0', '111'],
            ],
            # p020/p077 (Commissioner D3 DEM / D4 REP): 2-up pages whose
            # split headers and interleaved label rows leave the aux and
            # County - Total values roleless; inject aux + results.
            20: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Hillman Township, Precinct 1', '551', '1,420'],
                ['Montmorency County Michigan - Total', '551', '1,420'],
                ['County - Total', '551', '1,420'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Hillman Township, Precinct 1', '0', '21'],
                ['Montmorency County Michigan - Total', '0', '21'],
                ['County - Total', '0', '21'],
            ],
            # p018/p074 (Commissioner D1 DEM/REP): the four-district
            # precincts' all-zero results stack into ambiguous single
            # cells; inject the aux table plus 0/0 results.
            18: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Hillman Township, Precinct 1', '108', '217'],
                ['Montmorency Township, Precinct 1', '257', '530'],
                ['Montmorency Township, Precinct 2', '231', '441'],
                ['Vienna Township, Precinct 1', '208', '476'],
                ['Montmorency County Michigan - Total', '804', '1,664'],
                ['County - Total', '804', '1,664'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Hillman Township, Precinct 1', '0', '0'],
                ['Montmorency Township, Precinct 1', '0', '0'],
                ['Montmorency Township, Precinct 2', '0', '0'],
                ['Vienna Township, Precinct 1', '0', '0'],
                ['Montmorency County Michigan - Total', '0', '0'],
                ['County - Total', '0', '0'],
            ],
            23: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Albert Township, Precinct 1', '903', '2,170'],
                ['Montmorency County Michigan - Total', '903', '2,170'],
                ['County - Total', '903', '2,170'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Albert Township, Precinct 1', '0', '0'],
                ['Montmorency County Michigan - Total', '0', '0'],
                ['County - Total', '0', '0'],
            ],
            # p025: Briley Supervisor DEM — county label split across two
            # rows leaves the aux total roleless; inject both tables.
            25: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Briley Township, Precinct 1', '644', '1,715'],
                ['Montmorency County Michigan - Total', '644', '1,715'],
                ['County - Total', '644', '1,715'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Briley Township, Precinct 1', '0', '19'],
                ['Montmorency County Michigan - Total', '0', '19'],
                ['County - Total', '0', '19'],
            ],
            27: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Montmorency Township, Precinct 1', '257', '530'],
                ['Montmorency Township, Precinct 2', '231', '441'],
                ['Montmorency County Michigan - Total', '488', '971'],
                ['County - Total', '488', '971'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Montmorency Township, Precinct 1', '0', '0'],
                ['Montmorency Township, Precinct 2', '0', '0'],
                ['Montmorency County Michigan - Total', '0', '0'],
                ['County - Total', '0', '0'],
            ],
            # p030/p032/p036/p041: the 2-up pages print a doubled
            # 'County - Total' footer and drop the right half's Total
            # Votes value; inject aux + zero-candidate results.
            30: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Albert Township, Precinct 1', '903', '2,170'],
                ['Montmorency County Michigan - Total', '903', '2,170'],
                ['County - Total', '903', '2,170'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Albert Township, Precinct 1', '0', '0'],
                ['Montmorency County Michigan - Total', '0', '0'],
                ['County - Total', '0', '0'],
            ],
            32: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Briley Township, Precinct 1', '644', '1,715'],
                ['Montmorency County Michigan - Total', '644', '1,715'],
                ['County - Total', '644', '1,715'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Briley Township, Precinct 1', '0', '19'],
                ['Montmorency County Michigan - Total', '0', '19'],
                ['County - Total', '0', '19'],
            ],
            # p034/p039: 2-up Clerk Loud DEM and Treasurer Avery DEM
            # pages — the latter's results header garbles 'Unresolved
            # Write-In' into 'Paid-Unresolved' + 'Write-In' columns,
            # emitting the unresolved 3 as a candidate.
            34: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Loud Township, Precinct 1', '107', '243'],
                ['Montmorency County Michigan - Total', '107', '243'],
                ['County - Total', '107', '243'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Loud Township, Precinct 1', '0', '4'],
                ['Montmorency County Michigan - Total', '0', '4'],
                ['County - Total', '0', '4'],
            ],
            36: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Rust Township, Precinct 1', '183', '422'],
                ['Montmorency County Michigan - Total', '183', '422'],
                ['County - Total', '183', '422'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Rust Township, Precinct 1', '0', '3'],
                ['Montmorency County Michigan - Total', '0', '3'],
                ['County - Total', '0', '3'],
            ],
            39: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Avery Township, Precinct 1', '210', '569'],
                ['Montmorency County Michigan - Total', '210', '569'],
                ['County - Total', '210', '569'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Avery Township, Precinct 1', '0', '3'],
                ['Montmorency County Michigan - Total', '0', '3'],
                ['County - Total', '0', '3'],
            ],
            41: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Hillman Township, Precinct 1', '659', '1,637'],
                ['Montmorency County Michigan - Total', '659', '1,637'],
                ['County - Total', '659', '1,637'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Hillman Township, Precinct 1', '0', '21'],
                ['Montmorency County Michigan - Total', '0', '21'],
                ['County - Total', '0', '21'],
            ],
            # Zero-candidate Treasurer DEM table collapsed to one cell;
            # render shows 0 votes / 13 and 6 unresolved write-ins.
            43: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Montmorency Township, Precinct 1', '257', '530'],
                ['Montmorency Township, Precinct 2', '231', '441'],
                ['Montmorency County Michigan - Total', '488', '971'],
                ['County - Total', '488', '971'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Montmorency Township, Precinct 1', '0', '13'],
                ['Montmorency Township, Precinct 2', '0', '6'],
                ['Montmorency County Michigan - Total', '0', '19'],
                ['County - Total', '0', '19'],
            ],
            # Avery Trustee DEM results collapsed; render shows
            # Dobbyn 31, total 31, unresolved 0.
            47: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Avery Township, Precinct 1', '210', '569'],
                ['Montmorency County Michigan - Total', '210', '569'],
                ['County - Total', '210', '569'],
                ['Precinct', 'Dawn A. Dobbyn', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Avery Township, Precinct 1', '31', '31', '0'],
                ['Montmorency County Michigan - Total', '31', '31', '0'],
                ['County - Total', '31', '31', '0'],
            ],
            # p049: Hillman Trustee DEM all-zero results stack into
            # ambiguous single cells; aux is clean in the OCR stream.
            49: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Hillman Township, Precinct 1', '659', '1,637'],
                ['Montmorency County Michigan - Total', '659', '1,637'],
                ['County - Total', '659', '1,637'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Hillman Township, Precinct 1', '0', '0'],
                ['Montmorency County Michigan - Total', '0', '0'],
                ['County - Total', '0', '0'],
            ],
            # p051: Montmorency Trustee DEM results collapse; page-image
            # read gives 0/21 and 0/8.
            51: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Montmorency Township, Precinct 1', '257', '530'],
                ['Montmorency Township, Precinct 2', '231', '441'],
                ['Montmorency County Michigan - Total', '488', '971'],
                ['County - Total', '488', '971'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Montmorency Township, Precinct 1', '0', '21'],
                ['Montmorency Township, Precinct 2', '0', '8'],
                ['Montmorency County Michigan - Total', '0', '29'],
                ['County - Total', '0', '29'],
            ],
            # p062: Vienna Delegate DEM 2-up page with split headers and
            # interleaved label rows; inject aux + results.
            62: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Vienna Township, Precinct 1', '208', '476'],
                ['Montmorency County Michigan - Total', '208', '476'],
                ['County - Total', '208', '476'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Vienna Township, Precinct 1', '0', '2'],
                ['Montmorency County Michigan - Total', '0', '2'],
                ['County - Total', '0', '2'],
            ],
            # 2-up page whose right half (Borton/Cutler) OCR dropped
            # entirely; p066 alone would emit a bogus Write-In residual,
            # so inject the full render-verified contest.
            65: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Albert Township, Precinct 1', '903', '2,170'],
                ['Avery Township, Precinct 1', '210', '569'],
                ['Briley Township, Precinct 1', '644', '1,715'],
                ['Hillman Township, Precinct 1', '659', '1,637'],
                ['Loud Township, Precinct 1', '107', '243'],
                ['Montmorency Township, Precinct 1', '257', '530'],
                ['Montmorency Township, Precinct 2', '231', '441'],
                ['Rust Township, Precinct 1', '183', '422'],
                ['Vienna Township, Precinct 1', '208', '476'],
                ['Montmorency County Michigan - Total', '3,402', '8,203'],
                ['County - Total', '3,402', '8,203'],
                ['Precinct', 'Ken Borton', 'Tony Cutler'],
                ['County', '', ''],
                ['Albert Township, Precinct 1', '435', '90'],
                ['Avery Township, Precinct 1', '88', '39'],
                ['Briley Township, Precinct 1', '296', '61'],
                ['Hillman Township, Precinct 1', '253', '92'],
                ['Loud Township, Precinct 1', '38', '13'],
                ['Montmorency Township, Precinct 1', '82', '35'],
                ['Montmorency Township, Precinct 2', '96', '38'],
                ['Rust Township, Precinct 1', '88', '18'],
                ['Vienna Township, Precinct 1', '87', '36'],
                ['Montmorency County Michigan - Total', '1,463', '422'],
                ['County - Total', '1,463', '422'],
            ],
            # p072: Register of Deeds REP Unresolved Write-In column
            # spills onto its own page as fused '<precinct> <n>' cells
            # (derived values sum to the printed 6).
            72: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Albert Township, Precinct 1', '4'],
                ['Avery Township, Precinct 1', '0'],
                ['Briley Township, Precinct 1', '1'],
                ['Hillman Township, Precinct 1', '1'],
                ['Loud Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Rust Township, Precinct 1', '0'],
                ['Vienna Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '6'],
                ['County - Total', '6'],
            ],
            74: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Hillman Township, Precinct 1', '108', '217'],
                ['Montmorency Township, Precinct 1', '257', '530'],
                ['Montmorency Township, Precinct 2', '231', '441'],
                ['Vienna Township, Precinct 1', '208', '476'],
                ['Montmorency County Michigan - Total', '804', '1,664'],
                ['County - Total', '804', '1,664'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Hillman Township, Precinct 1', '0', '0'],
                ['Montmorency Township, Precinct 1', '0', '0'],
                ['Montmorency Township, Precinct 2', '0', '0'],
                ['Vienna Township, Precinct 1', '0', '0'],
                ['Montmorency County Michigan - Total', '0', '0'],
                ['County - Total', '0', '0'],
            ],
            77: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Albert Township, Precinct 1', '710', '1,749'],
                ['Montmorency County Michigan - Total', '710', '1,749'],
                ['County - Total', '710', '1,749'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Albert Township, Precinct 1', '0', '64'],
                ['Montmorency County Michigan - Total', '0', '64'],
                ['County - Total', '0', '64'],
            ],
            # p081: Briley Supervisor REP — the results header garbles
            # into 'pTotal Votes' fragments; page-image read gives the
            # qualified write-in Edwards 48 of 48 cast, 57 unresolved.
            81: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Briley Township, Precinct 1', '644', '1,715'],
                ['Montmorency County Michigan - Total', '644', '1,715'],
                ['County - Total', '644', '1,715'],
                ['Precinct', 'Marc Harold Edwards (Qualified Write In)',
                 'Total Votes', 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Briley Township, Precinct 1', '48', '48', '57'],
                ['Montmorency County Michigan - Total', '48', '48', '57'],
                ['County - Total', '48', '48', '57'],
            ],
            # p088/p093/p108/p118/p120: single-precinct Unresolved
            # Write-In spill pages fused into '<precinct> <n>' cells.
            88: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Albert Township, Precinct 1', '2'],
                ['Montmorency County Michigan - Total', '2'],
                ['County - Total', '2'],
            ],
            93: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Hillman Township, Precinct 1', '2'],
                ['Montmorency County Michigan - Total', '2'],
                ['County - Total', '2'],
            ],
            108: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Albert Township, Precinct 1', '5'],
                ['Montmorency County Michigan - Total', '5'],
                ['County - Total', '5'],
            ],
            118: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Rust Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            120: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Vienna Township, Precinct 1', '1'],
                ['Montmorency County Michigan - Total', '1'],
                ['County - Total', '1'],
            ],
            # p110: Briley Trustee REP — Brown/White columns confirmed on
            # the page image; Wojcik + Total Votes + Unresolved continue
            # on p111 (native). No Total Votes column here.
            110: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Briley Township, Precinct 1', '644', '1,715'],
                ['Montmorency County Michigan - Total', '644', '1,715'],
                ['County - Total', '644', '1,715'],
                ['Precinct', 'Brittany Brown', 'Evelyn White'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Briley Township, Precinct 1', '270', '234'],
                ['Montmorency County Michigan - Total', '270', '234'],
                ['County - Total', '270', '234'],
            ],
            # p115: Montmorency Trustee REP — Hardies/Steinke columns
            # confirmed on the page image; Villeneuve + Total Votes +
            # Unresolved continue on p116 (native).
            115: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Montmorency Township, Precinct 1', '257', '530'],
                ['Montmorency Township, Precinct 2', '231', '441'],
                ['Montmorency County Michigan - Total', '488', '971'],
                ['County - Total', '488', '971'],
                ['Precinct', 'Kendell Hardies', 'Gerald Steinke'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Montmorency Township, Precinct 1', '123', '97'],
                ['Montmorency Township, Precinct 2', '65', '81'],
                ['Montmorency County Michigan - Total', '188', '178'],
                ['County - Total', '188', '178'],
            ],
            # p119: Vienna Trustee REP prints aux-left/results-right
            # 2-up with a garbled '(Volc for 2)' title; page image gives
            # Erving 113, Payne 97, total 210.
            119: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Vienna Township, Precinct 1', '208', '476'],
                ['Montmorency County Michigan - Total', '208', '476'],
                ['County - Total', '208', '476'],
                ['Precinct', 'Faye E. Erving', 'Mayree Payne',
                 'Total Votes'],
                ['County', '', '', ''],
                ['Montmorency County Michigan', '', '', ''],
                ['Vienna Township, Precinct 1', '113', '97', '210'],
                ['Montmorency County Michigan - Total', '113', '97',
                 '210'],
                ['County - Total', '113', '97', '210'],
            ],
            # p126: Montmorency P1 Delegate REP 2-up page; the aux and
            # results headers split so the left half's values get no
            # roles. Inject aux + Mary Hamilton results.
            126: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Montmorency Township, Precinct 1', '257', '530'],
                ['Montmorency County Michigan - Total', '257', '530'],
                ['County - Total', '257', '530'],
                ['Precinct', 'Mary Hamilton', 'Total Votes',
                 'Unresolved Write-In'],
                ['County', '', '', ''],
                ['Montmorency Township, Precinct 1', '113', '113', '0'],
                ['Montmorency County Michigan - Total', '113', '113', '0'],
                ['County - Total', '113', '113', '0'],
            ],
            # p128: Rust Delegate REP — doubled County - Total footer
            # drops the right half's Unresolved value; aux is clean.
            128: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Rust Township, Precinct 1', '183', '422'],
                ['Montmorency County Michigan - Total', '183', '422'],
                ['County - Total', '183', '422'],
                ['Precinct', 'Total Votes', 'Unresolved Write-In'],
                ['County', '', ''],
                ['Rust Township, Precinct 1', '0', '10'],
                ['Montmorency County Michigan - Total', '0', '10'],
                ['County - Total', '0', '10'],
            ],
            # p131-p157: proposal Unresolved Write-In spill pages, fused
            # into '<precinct> <n>' cells whose 'County - Total 0' rule
            # would otherwise zero the open contest's total. All values
            # are 0 (page-image reads confirm p143/p145/p149/p155).
            131: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Albert Township, Precinct 1', '0'],
                ['Avery Township, Precinct 1', '0'],
                ['Briley Township, Precinct 1', '0'],
                ['Hillman Township, Precinct 1', '0'],
                ['Loud Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Rust Township, Precinct 1', '0'],
                ['Vienna Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            133: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Albert Township, Precinct 1', '0'],
                ['Avery Township, Precinct 1', '0'],
                ['Briley Township, Precinct 1', '0'],
                ['Hillman Township, Precinct 1', '0'],
                ['Loud Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Rust Township, Precinct 1', '0'],
                ['Vienna Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            135: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Albert Township, Precinct 1', '0'],
                ['Avery Township, Precinct 1', '0'],
                ['Briley Township, Precinct 1', '0'],
                ['Hillman Township, Precinct 1', '0'],
                ['Loud Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Rust Township, Precinct 1', '0'],
                ['Vienna Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            137: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Albert Township, Precinct 1', '0'],
                ['Avery Township, Precinct 1', '0'],
                ['Briley Township, Precinct 1', '0'],
                ['Hillman Township, Precinct 1', '0'],
                ['Loud Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Rust Township, Precinct 1', '0'],
                ['Vienna Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            139: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Briley Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            141: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Briley Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            143: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Hillman Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            145: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Hillman Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            # p146: Montmorency Refuse proposal 2-up page with split
            # headers leaving the aux values roleless.
            146: [
                ['Precinct', 'Times Cast', 'Registered Voters'],
                ['County', '', ''],
                ['Montmorency County Michigan', '', ''],
                ['Montmorency Township, Precinct 1', '257', '530'],
                ['Montmorency Township, Precinct 2', '231', '441'],
                ['Montmorency County Michigan - Total', '488', '971'],
                ['County - Total', '488', '971'],
                ['Precinct', 'Yes', 'No', 'Total Votes'],
                ['County', '', '', ''],
                ['Montmorency Township, Precinct 1', '160', '89', '249'],
                ['Montmorency Township, Precinct 2', '174', '46', '220'],
                ['Montmorency County Michigan - Total', '334', '135', '469'],
                ['County - Total', '334', '135', '469'],
            ],
            147: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Montmorency Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            149: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Montmorency Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            151: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Montmorency Township, Precinct 1', '0'],
                ['Montmorency Township, Precinct 2', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            155: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Vienna Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            # The Johannesburg-Lewiston proposal spans three counties;
            # keep only the Montmorency precincts and recompute totals.
            156: [
                ['Precinct', 'Times Cast', 'Registered Voters',
                 'Precinct', 'Yes', 'No', 'Total Votes'],
                ['County', '', '', 'County', '', '', ''],
                ['Albert Township, Precinct 1', '897', '2,162',
                 'Albert Township, Precinct 1', '436', '418', '854'],
                ['Vienna Township, Precinct 1', '115', '288',
                 'Vienna Township, Precinct 1', '52', '58', '110'],
                ['Montmorency County Michigan - Total', '1,012', '2,450',
                 'Montmorency County Michigan - Total', '488', '476', '964'],
                ['County - Total', '1,012', '2,450',
                 'County - Total', '488', '476', '964'],
            ],
            # p157: J-L proposal Unresolved Write-In spill lists the
            # other counties' precincts too; keep only Montmorency's.
            157: [
                ['Precinct', 'Unresolved Write-In'],
                ['County', ''],
                ['Albert Township, Precinct 1', '0'],
                ['Vienna Township, Precinct 1', '0'],
                ['Montmorency County Michigan - Total', '0'],
                ['County - Total', '0'],
            ],
            # p158-p188: canvass certification statements, no data.
            **{no: [] for no in range(158, 189)},
        },
    },
}

TITLE = re.compile(r'^(.*?) \((DEM|REP|LIB|UST|GRN|NLP)\) \((Vote for [\d.]+)\)'
                   r'(?: (DEM|REP|LIB|UST|GRN|NLP))?$')
# Some formats print titles without the '(Vote for N)' group (Tuscola 2020).
TITLE_NOVF = re.compile(r'^(.*?) \((DEM|REP|LIB|UST|GRN|NLP)\)'
                        r'(?: \((Vote for [\d.]+)\))?'
                        r'(?: (DEM|REP|LIB|UST|GRN|NLP))?$')
PROPOSAL = re.compile(r'^(.*?) \((Vote for [\d.]+)\)$')
# Tuscola 2020's spill pages print each row as one fused text line
# ('<label> <nums> <label> <nums>'): left half + right half.
FUSED_LINE = re.compile(r'^(.*[A-Za-z].*?)\s+((?:\d[\d,.]*\s+)+)'
                        r'([A-Za-z].*?)\s+((?:\d[\d,.]*\s*)+)$')
DISTRICT = re.compile(r'^(Representative in Congress|'
                      r'Representative in State Legislature|'
                      r'Rep in Congress|Rep in State Legislature) '
                      r'(\d+)(?:st|nd|rd|th) District$')
COMMISSIONER = re.compile(r'^County Commissioner (?:District )?'
                          r'(\d+)(?:st|nd|rd|th)? District$')
LUCE_OFFICE = re.compile(r'^Township (Supervisor|Clerk|Treasurer|Trustee) '
                         r'for (.+)$')
LUCE_DELEGATE = re.compile(r'^Delegate to County Convention for (.+)$')


def squash(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def intval(cell):
    """int of an OCR numeric cell; stray dots ('0.', '.27'), quotes ("'113"),
    trailing punctuation ('0 :', '1 !'), and stray text cells (label shifts
    on jumbled pages) become None."""
    s = cell.replace(',', '').strip().strip("'`´‘’").strip('.').strip()
    s = re.sub(r'[^\d]+$', '', s)
    return int(s) if s.isdigit() else None


def flatten(path):
    """One page of cached PaddleOCR markdown -> list of cell-lists."""
    rows = []
    for raw in open(path):
        line = raw.strip().lstrip('#').strip()
        if not line or line.startswith('```') or line == '---':
            continue
        if '<table' in line:
            for table in re.findall(r'<table.*?</table>', line, re.S):
                for tr in re.findall(r'<tr.*?</tr>', table, re.S):
                    cells = [html.unescape(re.sub(r'<[^>]+>', '', c)).strip()
                             for c in re.findall(r'<td[^>]*>(.*?)</td>', tr,
                                                 re.S)]
                    # stray LaTeX debris ("County - Total $ ^{{1}} $")
                    cells = [re.sub(r'\$[^$]*\$', ' ', c) for c in cells]
                    rows.append([c.replace('\\n', ' ').strip()
                                 for c in cells])
            continue
        line = re.sub(r'<[^>]+>', '', line)
        # LaTeX-wrapped title text ('$ \underline{\text{Lake Township
        # Clerk (DEM) }} $') must be unwrapped before the debris strip
        # below, whose dollar spans would otherwise delete the title.
        line = re.sub(r'\\text\{([^{}]*)\}', r'\1', line)
        line = re.sub(r'\\underline\{([^{}]*)\}', r'\1', line)
        # A dollar span is stray debris ("County - Total $ ^{{1}} $") only
        # when it carries no text; a wrapped title's span is kept.
        line = re.sub(r'\$([^$]*)\$', lambda m: m.group(1)
                      if re.search(r'[A-Za-z]', m.group(1)) else ' ', line)
        # A centered contest title can lose the space between its party and
        # vote-for groups ("Senator (DEM)_(Vote for 1)").
        line = line.replace(')_(', ') (')
        line = html.unescape(line).replace('\\n', ' ').strip()
        # Unwrapping LaTeX titles can leave doubled spaces, which break
        # TITLE's single-space party/vote-for separators.
        line = re.sub(r'\s+', ' ', line).strip()
        if line:
            rows.append([line])
    return rows


ROLE_KEYS = ('precinct', 'times_cast', 'registered_voters', 'total_votes',
             'unresolved')


def header_roles(cells, problems, where):
    """(kind, roles) for a table header row; kind is 'aux' or 'results'."""
    roles = []
    for cell in cells:
        sq = squash(cell)
        if sq.startswith('precinct') or sq.startswith('countyprecinct') \
                or sq.startswith('precipinct') \
                or sq.startswith('precinctcounty') \
                or sq in ('predict', 'predinct'):
            roles.append('precinct')
            # Kalkaska fuses the first candidate's name into the 'Precinct'
            # header cell ('Precinct Danielle Stein-Seabolt'); the phantom
            # empty column after the candidate then has no None slot. Emit
            # the remainder as a candidate column.
            if sq.startswith('precinct') and sq != 'precinct' and \
                    not sq.startswith(('precinctcounty', 'precipinct')) and \
                    len(sq) > 12:
                rest = re.sub(r'^\s*Precinct\s+', '', cell,
                              flags=re.I).strip()
                if rest:
                    rsq = squash(rest)
                    if 'time' in rsq or 'regist' in rsq:
                        # A fused aux header ('Precinct Times Cast
                        # Registered Voter', Kalkaska p131).
                        roles.append('times_cast')
                        roles.append('registered_voters')
                        continue
                    # Kalkaska fuses candidate names (and sometimes the
                    # Total Votes caption) into the 'Precinct' header cell
                    # ('Precinct John James (REP) Total '); split on the
                    # caption.  The Unresolved Write-In caption may be
                    # fused or dropped ('Total V'), but this format's
                    # results tables always close with that column.
                    tm = re.match(r'^(.*?)\s*\bTotal\b\s*(.*)$', rest)
                    if tm:
                        rest, tail = tm.group(1).strip(), tm.group(2)
                        tail_sq = squash(tail)
                    else:
                        tail_sq = None
                    # Several candidate names can be fused ('Precinct Bob
                    # Baldwin (REP) Rich Gillisse (REP)'); each ends in a
                    # party tag.
                    parts = re.findall(r'.*?\((?:DEM|REP|LIB|UST|GRN|NLP)\)',
                                       rest)
                    if parts and squash(' '.join(parts)) == squash(rest):
                        roles.extend(
                            re.sub(r'\s*\((?:DEM|REP|LIB|UST|GRN|NLP)\)$', '',
                                   p).strip() for p in parts)
                    elif 'unres' in rsq or 'writein' in rsq:
                        # A fused unresolved-write-in continuation header
                        # ('Precinct Unresolved Write-In', Kalkaska p147).
                        roles.append('unresolved')
                    elif rest:
                        roles.append(rest)
                    if tail_sq is not None:
                        roles.append('total_votes')
                        if 'unres' in tail_sq or 'writein' in tail_sq:
                            roles.append('unresolved')
                        elif tail_sq in ('', 'votes', 'v'):
                            roles.append('unresolved')
                        elif tail_sq:
                            roles.append(tail.strip())
        elif sq in ('county', 'country') and not roles:
            # A label column headed only 'County' (or OCR's 'Country').
            roles.append('precinct')
        elif sq in ('county', 'country') and roles and \
                roles[-1] == 'precinct':
            # A second label-ish cell after 'Precinct' ('Precinct' /
            # 'County' split, or the data's labels shifted into it): not a
            # value column.
            roles.append(None)
        elif sq.startswith('time'):
            roles.append('times_cast')
        elif sq.startswith('regist') or sq in ('voters', 'voter'):
            # "Registered" / "Voter" / "s" can split across three cells.
            if roles and roles[-1] == 'registered_voters':
                roles.append(None)
            else:
                roles.append('registered_voters')
        elif sq == 's' and roles and 'registered_voters' in roles[-2:]:
            roles.append(None)
        elif sq.startswith('totalvote') or sq in ('tot', 'tota', 'total',
                                                  'vote'):
            roles.append('total_votes')
        elif sq.startswith('unres'):
            # covers garbles too ('Unreso Write-Up')
            roles.append('unresolved')
        elif sq in ('writein', 'write', 'in') and roles and \
                roles[-1] == 'unresolved':
            # 'Unresolved Write-In' split across two header cells
            roles.append(None)
        elif sq == 'writein':
            # A report can print write-ins as their own candidate column
            # ("Write-in"); normalize the capitalization.
            roles.append('Write-In')
        elif 'writein' in sq and 'qualif' not in sq:
            # A mangled 'Unresolved Write-In' header ('Unites oved
            # Write-In') that no longer starts with 'unres'.  ('<name>
            # Qualified Write In' columns are real write-in candidates.)
            roles.append('unresolved')
        elif sq in ('yes', 'no'):
            roles.append(cell)
        else:
            # ES&S prints the candidate's party in the header cell
            # ("Hill Harper (DEM)"); the contest tag already carries it.
            cell = re.sub(r'\s*\((?:DEM|REP|LIB|UST|GRN|NLP|NPA|LP)\)$', '',
                          cell)
            sq = squash(cell)
            if roles and isinstance(roles[-1], str) and \
                    roles[-1] not in ROLE_KEYS and cell and \
                    re.search(r'\b[A-Z]\.$', roles[-1]):
                # OCR split a candidate name ("Todd J." / "Smalenberg").
                roles[-1] = f'{roles[-1]} {cell}'
            else:
                roles.append(None if not sq else cell)
    if 'times_cast' in roles and any(
            r not in (None, 'precinct', 'times_cast', 'registered_voters')
            for r in roles[2:]):
        # Merged aux+results table: its leading empty cell is the aux label
        # (precinct) column, not a phantom value column to filter out.
        if roles[0] is None:
            roles[0] = 'precinct'
    elif roles and roles[0] is None and 'times_cast' not in roles and \
            ('total_votes' in roles or any(
                isinstance(r, str) and r not in ROLE_KEYS for r in roles)):
        # A results table can lose its 'Precinct' header label; the data
        # rows still lead with it, so claim the leading empty cell.
        roles[0] = 'precinct'
    if 'times_cast' in roles:
        return 'aux', roles
    if 'total_votes' in roles or any(
            r not in (None, 'precinct') for r in roles):
        return 'results', roles
    # Unclassifiable header (garbage cells): the caller decides whether its
    # first-table default applies; footer cross-checks catch real losses.
    return None, None


def map_office(title):
    office, district = title, ''
    m = DISTRICT.match(title)
    if m:
        office = 'U.S. House' if m.group(1).endswith('Congress') \
            else 'State House'
        district = m.group(2)
    elif title == 'United States Senator':
        office = 'U.S. Senate'
    else:
        m = COMMISSIONER.match(title)
        if m:
            office, district = 'County Commissioner', m.group(1)
        else:
            m = LUCE_OFFICE.match(title)
            if m:
                office = f'{m.group(2)} {m.group(1)}'
            else:
                m = LUCE_DELEGATE.match(title)
                if m:
                    office = f'{m.group(1)} Delegate to County Convention'
    # "Prosecuting Attorney for Dickinson County" style titles
    if office == title:
        m = re.match(r'^(County .*?) for \w+ County$', title)
        if m:
            title = m.group(1)
            if title == 'County Clerk & Register of Deeds':
                office = 'County Clerk and Register of Deeds'
            elif title == 'Prosecuting Attorney':
                office = 'County Prosecuting Attorney'
            else:
                office = title
    return office, district


def parse_county(county, cfg, problems):
    # Multi-PDF counties list one cache dir per file ('caches'); their page
    # numbers concatenate, so MANUAL keys and 'where' labels are global.
    caches = cfg.get('caches') or [cfg['cache']]
    names = []
    for cache in caches:
        cache_dir = os.path.join(CACHE, cache)
        got = sorted(f for f in os.listdir(cache_dir) if f.endswith('.md'))
        names += [os.path.join(cache, f) for f in got]
    if len(names) != cfg['pages']:
        sys.exit(f'{caches}: {len(names)} pages, expected {cfg["pages"]}')

    precincts = cfg['precincts']
    sq_precincts = {squash(p): p for p in precincts}
    county_sq = squash(f'{county} county michigan total')
    ct_sq = squash('county total')
    pfixes = [(squash(k), squash(v))
              for k, v in cfg.get('precinct_fixes', {}).items()]

    def fix_lsq(s):
        """Undo OCR garbles of precinct names before label matching."""
        for wrong, right in pfixes:
            if wrong in s:
                s = s.replace(wrong, right)
        return s

    contests = []
    cur = None
    kind, roles = None, None
    saw_table = False   # a classified table has been read since the title
    data_since_footer = False   # a data row has been read since the last footer
    pending = []        # (label squash, cells) rows whose label wrapped
    pending_header = None   # candidate-name half of a split results header
    used = set()        # precincts already assigned in the current table

    # Tuscola 2020 prints every contest as a merged 2-up table: the left
    # half repeats the precinct label with Undervotes/Overvotes columns
    # (not wanted), the right half carries the results ([Precinct]
    # candidates... Total Votes Unresolved Write-In). Rows are rewritten
    # here to their right half before the main loop sees them.
    def two_up_right(cells):
        """The row's right-half cells — everything from the last
        non-numeric cell on, trailing empties stripped."""
        ri = None
        for i in range(len(cells) - 1, -1, -1):
            c = cells[i]
            if not c.strip():
                continue
            if intval(c) is None:
                ri = i
                break
        if ri is None:
            return None
        right = [cells[ri]] + cells[ri + 1:]
        while right and not right[-1].strip():
            right.pop()
        return right or None

    def two_up_prec(right, cells):
        """(precinct, shift) for the right half's label fragment. OCR splits
        wrapped labels across the two halves and adjacent rows, so the
        fragment may be the label's first line, its second line, or carry a
        digit of itself inside the values ('City of Vassar, Precinct' /
        '1 0 21'); the row's left-half label joins the candidates."""
        frag = fix_lsq(squash(right[0]))
        # The fragment can carry a junk leading digit ('3 Wells Township,
        # Precinct 1'); precinct names never start with one.
        frag = re.sub(r'^\d+', '', frag)
        cands = []      # (squash, shifted)
        if frag:
            cands.append((frag, False))
            if len(right) > 2 and intval(right[1]) is not None:
                cands.append((frag + squash(right[1]), True))
        if cells:
            left = fix_lsq(squash(cells[0]))
            if left and left != frag:
                cands.append((left, False))
                if frag:
                    cands.append((left + frag, False))
                    cands.append((frag + left, False))
        for csq, shifted in cands:
            if csq in sq_precincts:
                return sq_precincts[csq], shifted
        for csq, shifted in cands:
            partial = [p for p in precincts if squash(p).startswith(csq)]
            if len(partial) == 1:
                return partial[0], shifted
        # The fragment can arrive fused with its row's neighbours ('County
        # Tuscola County <precinct>'); a unique precinct squash embedded in
        # it still identifies the row.
        if frag:
            inside = [p for p in precincts if squash(p) and
                      squash(p) in frag]
            if len(inside) == 1:
                return inside[0], False
        return None, False

    county_echo = {squash('County'), squash('Country'),
                   squash(f'{county} County')}
    prev_right_vals = None   # values of the last emitted data row
    held_prec = None     # precinct embedded in a rowspan header cell
    held_n = None        # its table's value-column count

    def fix_footer_label(right):
        """Normalize the county footer label OCR fuses with 'Cumulative',
        truncates ('... - Tota'/'... - Tot') or leading/trailing digits
        ('3 Tuscola County - Total Cumulative' / '... - Total 0'); None
        when the label is not the county footer's."""
        lab_sq = re.sub(r'cumulative$', '', squash(right[0]))
        lab_sq = re.sub(r'^\d+', '', lab_sq)
        lab_sq = re.sub(r'\d+$', '', lab_sq)
        if lab_sq.endswith(('countytotal', 'countytota', 'countytot')) or \
                lab_sq in (squash(f'{county} County - Total'),
                           squash(f'{county} County - Tota')):
            return [f'{county} County - Total'] + right[1:]
        return None

    def two_up_rows(rows_in, where):
        nonlocal prev_right_vals, held_prec, held_n
        if os.environ.get('TUSDBG') == where:
            print(where, 'IN', rows_in, file=sys.stderr)
        out = []
        for cells in rows_in:
            if not cells:
                out.append(cells)
                continue
            if len(cells) == 1:
                # A spill page's rows can print as single fused text lines;
                # split them into table cells first.
                m = FUSED_LINE.match(cells[0])
                if not m:
                    out.append(cells)
                    continue
                cells = ([m.group(1)] + m.group(2).split() +
                         [m.group(3)] + m.group(4).split())
            nonempty = [c for c in cells if c.strip()]
            joined_sq = squash(''.join(cells))
            if nonempty and all(squash(c) in county_echo
                                for c in nonempty):
                continue    # the header's 'County' echo rows
            sq0 = squash(nonempty[0]) if nonempty else ''
            if sq0.startswith('precinct') and nonempty and \
                    all(squash(c) in county_echo for c in nonempty[1:]):
                continue    # a label-echo row that kept its 'Precinct' cell
            if sq0.startswith('precinct') or \
                    sq0 in ('county', 'country', 'predict', 'predinct') or \
                    'undervotes' in joined_sq or 'undenvotes' in joined_sq:
                # Header: keep it from the last 'Precinct' cell, dropping
                # the phantom 'County' that follows and trailing empties.
                prec_cells = [i for i, c in enumerate(cells)
                              if squash(c).startswith('precinct')]
                if not prec_cells:
                    out.append(cells)   # 'Precinct' label lost; classify
                    continue            # the row as-is downstream
                ri = max(prec_cells)
                head = cells[ri:]
                if len(head) > 1 and squash(head[1]) in ('county',
                                                         'country'):
                    head = [head[0]] + head[2:]
                while head and not head[-1].strip():
                    head.pop()
                # A rowspan header fuses every label of the page ('Precinct
                # County Tuscola County <precinct> ... - Total Cumulative')
                # into its first cell; hold the one precinct it names so the
                # all-numeric data rows below can claim it.
                held_prec, held_n = None, None
                inside = [p for p in precincts if squash(p) and
                          squash(p) in joined_sq]
                # A merged header whose right 'Precinct' cell OCR dropped
                # keeps the left half's 'Undervotes'/'Overvotes' captions
                # ahead of the results columns; drop them (the left half's
                # values are not wanted and would swallow the results).
                # A rowspan-fused header names a precinct and needs every
                # caption cell it has — leave it alone.
                if not inside and any(
                        squash(c) in ('totalvotes', 'unresolvedwritein',
                                      'unresolvedwrite') for c in head):
                    head = [c for c in head if squash(c) not in
                            ('undervotes', 'overvotes', 'undenvotes')]
                if len(inside) == 1 and head:
                    held_prec = inside[0]
                    held_n = len(head) - 1
                out.append(head)
                continue
            right = two_up_right(cells)
            if right is None:
                # An all-numeric row whose label was lost into the rowspan
                # header cell: attach the header's held precinct and
                # right-align the row's value columns.
                vals = ([intval(c) for c in nonempty]
                        if held_prec and held_n else None)
                if vals and all(v is not None for v in vals) and \
                        len(vals) >= held_n:
                    tail = vals[-held_n:]
                    if tail != prev_right_vals:
                        prev_right_vals = tail
                        out.append([held_prec] +
                                   [str(v) for v in tail])
                continue
            prec, shift = two_up_prec(right, cells)
            if prec is None:
                fixed = fix_footer_label(right)
                if fixed is not None:
                    held_prec = None
                    out.append(fixed)
                    continue
            if prec is not None:
                right = [prec] + right[1 + shift:]
                prev_right_vals = [intval(c) for c in right[1:]]
                out.append(right)
                continue
            # An unresolvable label fragment repeating the previous row's
            # values is the OCR's second reading of a wrapped label
            # ('Almer Charter' / 'Township, Precinct 1', same numbers):
            # drop it.
            if prev_right_vals is not None and \
                    [intval(c) for c in right[1:]] == prev_right_vals:
                continue
            out.append(right)
        if os.environ.get('TUSDBG') == where:
            print(where, out, file=sys.stderr)
        return out

    def new_contest(title, tag, vote_for):
        title = cfg.get('name_fixes', {}).get(title, title)
        return {'title': title, 'tag': tag, 'vote_for': vote_for,
                'aux': {}, 'cand': {}, 'total': {}, 'unres': {},
                'county_totals': {}}

    def dedup_extra(vals, rs):
        """Tuscola 2020's OCR duplicates one value cell beside its twin
        ('221 221 221 2' / '0 0 4'); drop the latest removable middle
        duplicate when the row carries exactly one surplus value."""
        if len(vals) != len(rs) + 1:
            return vals
        for i in range(len(vals) - 2, 0, -1):
            if vals[i] is not None and (vals[i] == vals[i - 1]
                                        or vals[i] == vals[i + 1]):
                return vals[:i] + vals[i + 1:]
        return vals

    def surplus_pairs(rs, vals, prec):
        """Tuscola 2020's 2-up rows fuse the left half's Undervotes/Overvote
        junk in front of the right half's values; align the survivors with
        the results roles. Render-verified: the right half's columns are the
        ones printed last, so the survivors right-align — except a lone
        trailing 0 surplus, which is a phantom Unresolved column whose
        header caption the OCR dropped (dropped left-to-right instead)."""
        rr = [r for r in rs if r is not None]
        vv = [v for v in vals if v is not None]
        if rs == ['total_votes'] and len(vv) == 2:
            # A headerless zero-candidate results table prints Total Votes
            # and Unresolved Write-In.
            rs = rr = ['total_votes', 'unresolved']
            return list(zip(rr, vv))
        if 'total_votes' in rs and 'unresolved' in rs and len(vv) > len(rr):
            if rs == ['total_votes', 'unresolved']:
                return [('total_votes', vv[-2]), ('unresolved', vv[-1])]
            return list(zip(rr, vv[-len(rr):]))
        if len(vv) == len(rr) + 1 and vv[-1] == 0 and \
                'total_votes' in rs and 'unresolved' not in rs:
            return list(zip(rr, vv[:-1]))
        problems.append(f'{where}: {prec} values {vals} vs roles {rs}')
        return []

    def take_values(prec, cells):
        """Apply one resolved data row to the current contest."""
        nonlocal data_since_footer
        data_since_footer = True
        lsq2 = squash(cells[0])
        segs2 = [[]]
        for c in cells[1:]:
            if lsq2 and squash(c) == lsq2:
                segs2.append([])
            else:
                segs2[-1].append(c)
        seg_roles2 = [r for r in roles if r != 'precinct']
        n_aux = sum(1 for r in seg_roles2
                    if r in ('times_cast', 'registered_voters'))
        for seg_i, seg in enumerate(segs2):
            vals = [intval(c) for c in seg]
            if len(segs2) > 1 and seg_i == 1:
                rs = seg_roles2[n_aux:] if n_aux else seg_roles2
            elif len(segs2) > 1:
                rs = seg_roles2[:n_aux]
            else:
                rs = seg_roles2
                if rs == ['total_votes'] and len(vals) == 2:
                    # A headerless zero-candidate results table prints Total
                    # Votes and Unresolved Write-In; its garbled header was
                    # not detected, so the extra column surfaces here.
                    rs = ['total_votes', 'unresolved']
            if cfg.get('two_up'):
                while True:
                    nv = dedup_extra(vals, rs)
                    if nv == vals:
                        break
                    vals = nv
            if len(vals) == len(rs):
                pairs = list(zip(rs, vals))
                # A phantom empty header column can sit between real ones
                # and shift the row's values off its roles; prefer the
                # alignment that assigns the most values.
                rr = [r for r in rs if r is not None]
                vv = [v for v in vals if v is not None]
                if len(vv) > sum(1 for r, v in pairs
                                 if r is not None and v is not None) and \
                        len(vv) <= len(rr):
                    pairs = list(zip(rr, vv))
            else:
                # Phantom empty cells (OCR artifacts) sit on either side of
                # the row: align surviving values with the named columns.
                rr = [r for r in rs if r is not None]
                vv = [v for v in vals if v is not None]
                # Values assign left-to-right; when OCR drops a trailing
                # value (Mecosta's merged tables lose the Total Votes
                # column) the tail roles simply go unfilled — the
                # per-contest footer cross-check is the backstop. Only a
                # surplus of values is ambiguous.
                pairs = list(zip(rr, vv))
                if len(vv) > len(rr) or (rs == ['total_votes'] and
                                         len(vv) == 2):
                    pairs = surplus_pairs(rs, vals, prec)
            for r, v in pairs:
                if r is None or v is None:
                    continue
                if r in ('times_cast', 'registered_voters'):
                    cur['aux'].setdefault(prec, {})[r] = v
                elif r == 'total_votes':
                    cur['total'][prec] = v
                elif r == 'unresolved':
                    cur['unres'][prec] = v
                else:
                    r = cfg.get('cand_fixes', {}).get(r, r)
                    cur['cand'].setdefault(prec, {})[r] = v

    def complete_pending(line_sq, where2):
        """Finish a held row whose label continues in line_sq."""
        if not pending:
            return False
        hlsq, hcells = pending[0]
        merged = [p for p in precincts if squash(p) == fix_lsq(hlsq + line_sq)]
        if len(merged) == 1:
            pending.pop(0)
            used.add(merged[0])
            take_values(merged[0], hcells)
            return True
        return False

    def resolve_pending(where2):
        # Labels still held match several precincts ("...Precinct" fits
        # P1 and P2); the results rows follow the aux table's precinct
        # order, so assign what is left in canonical order.
        for hlsq, hcells in pending:
            unseen = [p for p in precincts
                      if squash(p).startswith(hlsq) and p not in used]
            if unseen:
                used.add(unseen[0])
                take_values(unseen[0], hcells)
            else:
                problems.append(f'{where2}: held label {hlsq!r} matches no '
                                f'unassigned precinct')
        pending.clear()

    # Mecosta's SOVC nests each precinct's values in counting-board
    # sub-rows ('Election Day' / 'AV Counting Boards' / 'Early Voting' /
    # 'Total'), only the 'Total' row carrying per-precinct values. These
    # rows are collapsed ahead of the main loop: a precinct label row is
    # held, and its 'Total' sub-row is re-emitted as a flat data row with
    # the label in front. The leading turnout pages use the same shape
    # plus a '% Turnout' column, which marks their rows for dropping.
    board_labels = ('electionday', 'avcountingboards', 'earlyvoting')
    pending_board = None

    def collapse_board_rows(rows, where):
        nonlocal pending_board
        out = []
        for cells in rows:
            # The 'Cumulative - Total' footer can also print its label in
            # the second cell; shift it left so the main loop sees the
            # label it skips on.
            if len(cells) >= 2 and not cells[0].strip() and \
                    squash(cells[1]).startswith('cumulative'):
                cells = cells[1:]
            lab_i = 0
            if squash(cells[0]) not in board_labels and \
                    squash(cells[0]) != 'total' and len(cells) >= 2 and \
                    not cells[0].strip() and \
                    (squash(cells[1]) in board_labels or
                     squash(cells[1]) == 'total'):
                # Some tables put the sub-row label in the second cell.
                lab_i = 1
            blab = squash(cells[lab_i])
            if blab in board_labels or blab == 'total':
                if blab == 'total':
                    vals = [intval(c) for c in cells[lab_i + 1:]]
                    if '%' in ''.join(cells) or not any(
                            v for v in vals if v is not None):
                        continue   # turnout table row, or an all-zero row
                    if pending_board is None:
                        problems.append(f'{where}: board Total row without '
                                        f'a precinct label: {cells}')
                        continue
                    if pending_board in used:
                        problems.append(f'{where}: board Total row repeats '
                                        f'{pending_board}')
                    out.append([pending_board] + cells[lab_i + 1:])
                    pending_board = None
                continue
            sq0 = fix_lsq(squash(cells[0]))
            if sq0 and not any(c.strip() for c in cells[1:]):
                prec = sq_precincts.get(sq0)
                if prec is None:
                    partial = [p for p in precincts
                               if squash(p).startswith(sq0)]
                    if len(partial) == 1:
                        prec = partial[0]
                if prec is not None:
                    pending_board = prec
                    continue
            pending_board = None
            out.append(cells)
        return out

    page_titles = {int(k): v
                   for k, v in cfg.get('page_titles', {}).items()}
    inject_after = {int(k): v
                    for k, v in cfg.get('inject_after', {}).items()}
    for no, name in enumerate(names, 1):
        where = f'p{no:03d}'
        rows = cfg.get('manual', {}).get(no)
        # PaddleOCR can drop a contest's title line while keeping its
        # tables intact; the hand-read title is injected here (also ahead
        # of a manual page, whose contest may need (re)opening).
        title = page_titles.get(no)
        if rows is None:
            rows = flatten(os.path.join(CACHE, name))
        if title:
            rows = [[title]] + rows
        # A garbled/misplaced title line already injected above must not
        # re-open the contest mid-page (e.g. between a table and its spill
        # page).
        for pat in cfg.get('drop_lines', {}).get(no, []):
            rows = [r for r in rows
                    if not (len(r) == 1 and pat in r[0])]
        for anchor, new_cells in inject_after.get(no, []):
            for i, cells in enumerate(rows):
                if cells and squash(cells[0]) == squash(anchor):
                    rows = rows[:i + 1] + [list(new_cells)] + rows[i + 1:]
                    break
            else:
                problems.append(f'{where}: inject anchor {anchor!r} '
                                f'not found')
        if cfg.get('titles_first'):
            # Some pages print the contest title AFTER its table (OCR
            # reading order); the title belongs to this page's table, so
            # hoist it to the front before any rows are read.
            front, rest = [], []
            for cells in rows:
                if len(cells) == 1:
                    line = re.sub(r'\s+', ' ', cells[0]).strip().rstrip('.')
                    # Montmorency 2020 stamps every title with a page
                    # number; strip it before matching.
                    line = re.sub(r'^Page: \d+ of \d+\s*', '', line).strip()
                    m = (TITLE_NOVF if cfg.get('titles_without_vote_for')
                         else TITLE).match(line) or PROPOSAL.match(line)
                    if m:
                        front.append([line])
                        continue
                rest.append(cells)
            rows = front + rest
        if cfg.get('two_up'):
            rows = two_up_rows(rows, where)
        if cfg.get('board_rows'):
            rows = collapse_board_rows(rows, where)
        for cells in rows:
            if not cells:
                continue    # OCR can emit a <tr> with no cells at all
            if len(cells) == 1:
                line = cells[0]
                # 'Page: 29 of 174 Felch Township Clerk (DEM) ...'
                line = re.sub(r'^Page: \d+ of \d+\s*', '', line).strip()
                line = line.replace('DÉM', 'DEM')
                line = line.replace('Commissionerr', 'Commissioner')
                # A title can pick up a stray trailing period from OCR, or
                # the page stamp ('... (Vote for 1) 8/9/2024 11:16:46 AM',
                # or mid-title: '... (Vote for 1) Page: 138 of 153
                # 3/6/2020 3:22:20', or '... (Vote for 2) 3:22:29 PM REP').
                line = line.rstrip('.')
                line = re.sub(r'\s*,?\s*\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}'
                              r':\d{2}(?::\d{2})?\s*(?:[AP]M)?', '', line)
                line = re.sub(r'\s*\bPage: \d+ of \d+\s*$', '', line)
                # A title can repeat its party between the party and
                # vote-for groups ('... Precinct 1 (DEM) DEM (Vote for 2)'),
                # or garble it ('... (Vote for 4) PEP').
                line = re.sub(r'\((DEM|REP|LIB|UST|GRN|NLP)\) \1 \(Vote for',
                              r'(\1) (Vote for', line)
                line = re.sub(r' PEP$', ' REP', line)
                # Montmorency 2020 OCR lowercased the vote-for group on
                # some titles and printed a comma before it on others.
                line = re.sub(r'\(vote for', '(Vote for', line)
                line = re.sub(r'\), \(', ') (', line)
                # ... and garbles it on many more ('Vole', 'Volc',
                # 'Voue', 'Vice', 'V.e', 'Ste', 'Vote or'), wraps the
                # digit in stray parens ('(Vote for (1) REP)'), swallows
                # the party into the group ('(Vote for 2, DEM'), or
                # garbles the party itself ('(EP)', '(Rep)').
                line = re.sub(r'\((?:Vole|Volc|Voue|Vice|V\.e|Ste|Vote or)'
                              r' for', '(Vote for', line)
                line = re.sub(r'\(Vote for \(([\d.]+)\)',
                              r'(Vote for \1)', line)
                line = re.sub(r'\(Vote for (\d+), (DEM|REP|LIB|UST|GRN|NLP)'
                              r'\s*$', r'(Vote for \1) \2', line)
                line = re.sub(r' \(EP\)', ' (REP)', line)
                line = re.sub(r' \(Rep\)', ' (REP)', line)
                # ... or wraps the whole tail in an extra paren
                # ('(Vote for (1) REP)').
                line = re.sub(r' ((?:DEM|REP|LIB|UST|GRN|NLP))\)$',
                              r' \1', line)
                m = (TITLE_NOVF if cfg.get('titles_without_vote_for')
                     else TITLE).match(line) or PROPOSAL.match(line)
                if m:
                    resolve_pending(where)
                    groups = m.groups()
                    tag = groups[1] if len(groups) >= 2 and groups[1] in (
                        'DEM', 'REP', 'LIB', 'UST', 'GRN', 'NLP') else ''
                    title = groups[0].strip()
                    for pat, rep in cfg.get('title_sub', []):
                        title = re.sub(pat, rep, title)
                    # '(Vote for .1)' — an OCR dot before the digit; a title
                    # printed without the group (Tuscola 2020) yields ''.
                    vote_for = ((groups[2] if tag else groups[1])
                                or '').replace('.', '')
                    title = cfg.get('name_fixes', {}).get(title, title)
                    # A column-spill continuation page repeats the parent
                    # contest's title (the last candidate column(s) plus
                    # Total Votes/Unresolved print on their own page); a
                    # re-open of the still-open contest continues it rather
                    # than duplicating it.
                    if cur is not None and \
                            (cur['title'], cur['tag'], cur['vote_for']) == \
                            (title, tag, vote_for):
                        saw_table = False
                        used.clear()
                        pending_header = None
                        continue
                    if cur is not None:
                        contests.append(cur)
                    cur = new_contest(title, tag, vote_for)
                    saw_table = False
                    used.clear()
                    pending_header = None
                    continue
                # A wrapped label can leave its number on a lone digits
                # line ("<Big Creek Township, Precinct>" / "1").
                if re.match(r'^\d[\d,]*$', line) and \
                        complete_pending(squash(line), where):
                    continue
                # The results table can lose its header; its County - Total
                # footer may then squash into one cell with the value.
                m = re.match(r'^County - Total ([\d,.]+)$', line)
                if m and cur is not None and kind == 'results':
                    cur['county_totals']['results:total_votes'] = \
                        intval(m.group(1))
                    continue
                # A results header fused into one cell ('Precinct John
                # James (REP) Total ', Kalkaska) reads as a single-cell
                # line, not a header row; classify it before it can be
                # mistaken for noise.
                if cur is not None and len(line) > 20 and \
                        squash(line).startswith('precinct') and \
                        re.search(r'\b(Total|DEM|REP|Times|Registered|'
                                  r'Unresolved)\b', line):
                    got_kind, got_roles = header_roles([line], problems,
                                                       where)
                    if got_kind:
                        kind, roles = got_kind, got_roles
                        saw_table = True
                        used.clear()
                        continue
                # A results table can collapse to single-cell lines with
                # the value merged into the label ("<precinct> 12") or the
                # precinct number displaced onto the next line.
                if cur is not None and kind == 'results':
                    m2 = re.match(r'^(.+?\S)((?:\s+\d[\d,.]*)+)$', line)
                    if m2:
                        toks = re.findall(r'\d[\d,.]*', m2.group(2))
                        # The label may itself end in digits ('Precinct 1'),
                        # so prefer the longest label that exactly matches a
                        # precinct; a zero-candidate contest's collapsed row
                        # then carries Total Votes and Unresolved Write-In.
                        for k in range(len(toks), 0, -1):
                            lab = m2.group(1) + ' ' + ' '.join(
                                toks[:len(toks) - k]) if k < len(toks) \
                                else m2.group(1)
                            psq = squash(lab)
                            exact = [p for p in precincts
                                     if squash(p) == psq]
                            if len(exact) == 1:
                                vals = [intval(v) for v in toks[len(toks)-k:]]
                                cur['total'][exact[0]] = vals[0]
                                if len(vals) == 2:
                                    cur['unres'][exact[0]] = vals[1]
                                break
                        else:
                            psq = squash(m2.group(1))
                            if psq and psq not in ('county',) and \
                                    not psq.startswith('cumulative') and \
                                    'total' not in psq and \
                                    [p for p in precincts
                                     if squash(p).startswith(psq)]:
                                # Truncated label ("Greenwood Township, 8" /
                                # "Precinct 1"): hold for the completion.
                                pending.append(
                                    (psq, [m2.group(1)] +
                                     [str(intval(v) or '') for v in toks]))
                                continue
                # other single-cell lines are noise
                continue
            joined = squash(''.join(cells))
            first_sq = squash(cells[0])
            if 'timescast' in joined or 'totalvotes' in joined or \
                    joined.startswith('precinct') or \
                    first_sq in ('predict', 'predinct') or \
                    (first_sq in ('county', 'country') and
                     ('unresolved' in joined or 'writein' in joined or
                      not any(c.strip() for c in cells[1:]))):
                # Header rows: OCR garbles of 'Precinct' ('Predict',
                # 'Predinct') only head header rows, and a 'County'-led row
                # naming Unresolved/Write-In columns is a header whose
                # 'Precinct' label was lost.
                resolve_pending(where)
                hcells = cells
                if pending_header is not None:
                    # The candidate-name cells printed on their own line
                    # above the "Total Votes" line.
                    pad = [''] * (len(cells) - len(pending_header))
                    hcells = [a or b for a, b in
                              zip(pending_header + pad, cells)]
                    pending_header = None
                got_kind, got_roles = header_roles(hcells, problems, where)
                if got_kind is None and cur is not None and not saw_table:
                    # The first table of a contest is always the aux table,
                    # even when OCR dropped its header labels.
                    got_kind, got_roles = 'aux', ['times_cast',
                                                  'registered_voters']
                if got_kind is not None:
                    kind, roles = got_kind, got_roles
                    saw_table = True
                    used.clear()
                continue
            label = cells[0]
            # A row's label can sit in the second cell (an empty first cell
            # from the header's split 'Precinct'/'County' label column).
            nxt_sq = fix_lsq(squash(cells[1])) if len(cells) >= 2 else ''
            if not label.strip() and len(cells) >= 3 and cells[1].strip() and \
                    (sq_precincts.get(nxt_sq) or
                     nxt_sq.endswith(county_sq) or
                     nxt_sq.endswith(ct_sq) or
                     nxt_sq in (squash(f'{county} county'),
                                squash(f'{county} county michigan'))):
                cells = cells[1:]
                label = cells[0]
            lsq = fix_lsq(squash(label))
            if lsq.startswith('cumulative') or lsq in ('county',) or \
                    not any(c.strip() and (c.replace(',', '')
                                           .strip('.').isdigit()
                                           or c == '****')
                            for c in cells[1:]):
                # A valueless fragment may complete the held row's label.
                if pending:
                    complete_pending(lsq, where)
                # An all-text, no-label row can be the candidate-name half
                # of a split results header.
                pending_header = cells if not cells[0].strip() and \
                    any(c.strip() for c in cells) else None
                continue
            if cur is None:
                continue   # page-1 turnout summary
            if kind is None or roles is None:
                problems.append(f'{where}: data row {cells} outside a table')
                continue
            # Footer rows: countywide column totals for the current table.
            # 'Total' alone (Huron), '<County> County' and '<County> County
            # Michigan' (totals whose 'Michigan - Total' tail OCR dropped),
            # and bare value rows with no label at all are footer variants.
            is_footer = lsq.endswith(county_sq) or lsq.endswith(ct_sq) or \
                lsq in (squash(f'{county} county'),
                        squash(f'{county} county michigan')) or \
                (cfg.get('total_footer') and lsq == 'total') or \
                (not lsq and cells[1:] and
                 all(intval(c) is not None or not c.strip()
                     for c in cells[1:]) and
                 any(intval(c) is not None for c in cells[1:]))
            if is_footer:
                resolve_pending(where)
                used.clear()   # a footer ends this table's rows
                data_since_footer = False
                seg_roles = [r for r in roles if r != 'precinct']
                n_aux = sum(1 for r in seg_roles
                            if r in ('times_cast', 'registered_voters'))
                segs = [[]]
                for c in cells[1:]:
                    # An empty label cannot repeat mid-row: its empty cells
                    # are padding, not segment boundaries.
                    if lsq and squash(c) == lsq:
                        segs.append([])
                    else:
                        segs[-1].append(c)
                for seg_i, seg in enumerate(segs):
                    vals = [intval(c) for c in seg]
                    if len(segs) > 1 and seg_i == 1:
                        rs = seg_roles[n_aux:] if n_aux else seg_roles
                    elif len(segs) > 1:
                        rs = seg_roles[:n_aux]
                    else:
                        rs = seg_roles
                    if cfg.get('two_up'):
                        while True:
                            nv = dedup_extra(vals, rs)
                            if nv == vals:
                                break
                            vals = nv
                    if kind == 'results' and roles == ['total_votes'] \
                            and not data_since_footer and \
                            len([x for x in vals if x is not None]) > 1:
                        # The aux table's trailing 'County - Total' row, read
                        # after the switch to the headerless results table:
                        # its values belong to the aux columns. Only trusted
                        # before any results rows have been read, else a
                        # lost-header results footer would clobber them.
                        for r, v in zip(('times_cast', 'registered_voters'),
                                        [x for x in vals if x is not None]):
                            cur['county_totals'].setdefault(f'aux:{r}', v)
                        pairs = []
                    elif len(vals) == len(rs):
                        pairs = list(zip(rs, vals))
                        # A phantom empty header column can shift the
                        # footer's values off its roles; prefer the
                        # alignment that assigns the most values.
                        rr = [r for r in rs if r is not None]
                        vv = [v for v in vals if v is not None]
                        if len(vv) > sum(1 for r, v in pairs
                                         if r is not None and v is not None) \
                                and len(vv) <= len(rr):
                            pairs = list(zip(rr, vv))
                    else:
                        # Phantom empty cells: align surviving values with
                        # the named columns in order.
                        rr = [r for r in rs if r is not None]
                        vv = [v for v in vals if v is not None]
                        # Short footers assign left-to-right with tail roles
                        # unfilled (see take_values); only a surplus of
                        # values is ambiguous.
                        pairs = list(zip(rr, vv))
                        if len(vv) > len(rr) or \
                                (rs == ['total_votes'] and len(vv) == 2):
                            # A headerless zero-candidate results footer
                            # prints Total Votes and Unresolved Write-In;
                            # surplus values right-align (see surplus_pairs).
                            pairs = surplus_pairs(rs, vals, label)
                    for r, v in pairs:
                        if r is None or v is None:
                            continue
                        # In a merged table the footer spans both halves, so
                        # the key kind follows the role, not the row. First
                        # footer wins: a table's own '<County> ... - Total'
                        # precedes the trailing 'County - Total', whose value
                        # OCR can garble to the cumulative total's.
                        kk = 'aux' if r in ('times_cast',
                                            'registered_voters') else 'results'
                        key = f'{kk}:{r}'
                        # A footer value the OCR garbled beyond repair
                        # (hand-verified against the page image) is forced
                        # here instead of sticking via first-footer-wins.
                        override = cfg.get('footer_overrides', {}) \
                            .get(no, {}).get(key)
                        if override is not None:
                            cur['county_totals'][key] = override
                        else:
                            cur['county_totals'].setdefault(key, v)
                if kind == 'aux' and not any(
                        r not in (None, 'precinct', 'times_cast',
                                  'registered_voters') for r in roles):
                    # A split-table contest: the results table follows and
                    # may lose its header (a zero-candidate contest prints
                    # only Total Votes). A merged table keeps its roles.
                    kind, roles = 'results', ['total_votes']
                continue
            if pending:
                # The held row may complete here; otherwise this row is a
                # repeated truncated label or an unrelated precinct, so
                # resolve the held row in canonical order.
                if not complete_pending(lsq, where):
                    resolve_pending(where)
            prec = sq_precincts.get(lsq)
            if prec is None:
                partial = [p for p in precincts
                           if lsq and squash(p).startswith(lsq)]
                if len(partial) == 1:
                    prec = partial[0]
                elif partial and any(c.strip() for c in cells[1:]):
                    # A label truncated by OCR (e.g. "Big Creek Township,
                    # Precinct" for both P1 and P2): hold the row until the
                    # next row or table edge disambiguates it.
                    pending.append((lsq, cells))
                    continue
                elif not any(c.strip() for c in cells[1:]):
                    continue   # wrapped label fragment carrying no values
                else:
                    problems.append(f'{where}: precinct label {label!r} '
                                    f'matches no precinct')
                    continue
            used.add(prec)
            take_values(prec, cells)
    resolve_pending('end')
    if cur is not None:
        contests.append(cur)

    # ---- validation and emission ----
    rows_out = []
    for c in contests:
        office, district = map_office(c['title'])
        prec_set = set(c['aux']) | set(c['cand']) | set(c['total'])
        for prec in prec_set:
            if prec in c['total']:
                # Total Votes includes write-in votes. When the report
                # prints an Unresolved Write-In column (Luce) it sits
                # outside Total Votes; without one (Oscoda) the
                # unattributed residual of Total Votes is the write-in
                # total.
                got = sum(c['cand'].get(prec, {}).values())
                total = c['total'][prec]
                unres = c['unres'].get(prec, 0)
                residual = total - got if not c['unres'] \
                    else unres + (total - got)
                if total < got:
                    problems.append(f'{c["title"]} / {prec}: candidates '
                                    f'{got} > Total Votes {total}')
                elif residual:
                    c['cand'].setdefault(prec, {})['Write-In'] = residual
            elif c['unres'].get(prec):
                # A zero-candidate contest (no candidates filed) prints no
                # Total Votes column; every vote is an unresolved write-in.
                c['cand'].setdefault(prec, {})['Write-In'] = c['unres'][prec]
                tc = c['aux'].get(prec, {}).get('times_cast')
                total = c['total'].get(prec, 0)
                if tc is not None and total > tc \
                        and c['vote_for'] == '(Vote for 1)':
                    problems.append(f'{c["title"]} / {prec}: Total Votes '
                                    f'{total} > Times Cast {tc}')
        # A single-precinct contest's footer IS that precinct's row (the
        # county total equals the precinct's). When OCR dropped a row's
        # value cell or the row entirely, the footer still names every
        # role's county total, so recover the precinct's values from it.
        solo = [p for p in prec_set
                if p in c['total'] or p in c['unres'] or p in c['cand']
                or p in c['aux']]
        if len(solo) == 1:
            prec = solo[0]
            for key, want in sorted(c['county_totals'].items()):
                kk, r = key.split(':', 1)
                if kk == 'aux':
                    if r in ('times_cast', 'registered_voters'):
                        c['aux'].setdefault(prec, {}) \
                            .setdefault(r, want)
                elif r == 'total_votes':
                    c['total'].setdefault(prec, want)
                elif r == 'unresolved':
                    c['unres'].setdefault(prec, want)
                # Any other role should be a candidate, but a garbled
                # footer can fuse caption fragments into the label
                # ('Undervotes 82', '231'); only accept plausible names.
                elif not re.search(
                        r'undervote|overvote|precinct|county|cumulative'
                        r'|writein|timescast|registered|^tota?l?$'
                        r'|^\d+$', squash(r)):
                    c['cand'].setdefault(prec, {}).setdefault(r, want)
        sums = {'aux:times_cast': sum(a.get('times_cast', 0)
                                      for a in c['aux'].values()),
                'aux:registered_voters':
                    sum(a.get('registered_voters', 0)
                        for a in c['aux'].values()),
                'results:total_votes': sum(c['total'].get(p, 0)
                                           for p in prec_set)}
        for prec in prec_set:
            for cand, v in c['cand'].get(prec, {}).items():
                sums[f'results:{cand}'] = sums.get(f'results:{cand}', 0) + v
            if prec in c['unres']:
                # Include zero-valued entries so a printed 0 footer checks.
                sums['results:unresolved'] = \
                    sums.get('results:unresolved', 0) + c['unres'][prec]
        for key, want in sorted(c['county_totals'].items()):
            # Footer candidate keys carry the source's spelling; a
            # cand_fixes rename must apply to both sides.
            fixed = [(cfg.get('cand_fixes', {}).get(k, k), v)
                     for k, v in [(key[len('results:'):], want)]
                     if key.startswith('results:')]
            if fixed:
                key = f'results:{fixed[0][0]}'
            got = sums.get(key)
            if got is None:
                problems.append(f'{c["title"]}: footer {key}={want} has no '
                                f'precinct counterpart')
            elif got != want:
                problems.append(f'{c["title"]}: {key} precinct sum {got} != '
                                f'footer {want}')
        for prec in [p for p in precincts if p in prec_set]:
            for cand, v in sorted(c['cand'].get(prec, {}).items()):
                if v:
                    rows_out.append([county, prec, office, district,
                                     c['tag'], cand, v])
            tc = c['aux'].get(prec, {}).get('times_cast')
            if tc:
                rows_out.append([county, prec, office, district, c['tag'],
                                 'Ballots Cast', tc])
    return rows_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('county', help='COUNTY_CONFIG key')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    cfg = COUNTY_CONFIG[args.county]
    problems = []
    rows = parse_county(cfg.get('county_name', args.county), cfg, problems)
    out = args.out or cfg['out']
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    print(f'Wrote {len(rows)} rows to {out} ({len(problems)} problems)')
    for p in problems:
        print('PROBLEM:', p, file=sys.stderr)


if __name__ == '__main__':
    main()