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
}

TITLE = re.compile(r'^(.*?) \((DEM|REP|LIB|UST|GRN|NLP)\) \((Vote for [\d.]+)\)'
                   r'(?: (DEM|REP|LIB|UST|GRN|NLP))?$')
PROPOSAL = re.compile(r'^(.*?) \((Vote for [\d.]+)\)$')
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

    def new_contest(title, tag, vote_for):
        title = cfg.get('name_fixes', {}).get(title, title)
        return {'title': title, 'tag': tag, 'vote_for': vote_for,
                'aux': {}, 'cand': {}, 'total': {}, 'unres': {},
                'county_totals': {}}

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
                if rs == ['total_votes'] and len(vv) == 2:
                    # A headerless zero-candidate results table prints
                    # Total Votes and Unresolved Write-In.
                    rr = ['total_votes', 'unresolved']
                # Values assign left-to-right; when OCR drops a trailing
                # value (Mecosta's merged tables lose the Total Votes
                # column) the tail roles simply go unfilled — the
                # per-contest footer cross-check is the backstop. Only a
                # surplus of values is ambiguous.
                pairs = list(zip(rr, vv))
                if len(vv) > len(rr):
                    problems.append(f'{where}: {prec} values {vals} vs '
                                    f'roles {rs}')
                    pairs = []
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
    for no, name in enumerate(names, 1):
        where = f'p{no:03d}'
        rows = cfg.get('manual', {}).get(no)
        if rows is None:
            rows = flatten(os.path.join(CACHE, name))
            # PaddleOCR can drop a contest's title line while keeping its
            # tables intact; the hand-read title is injected here.
            title = page_titles.get(no)
            if title:
                rows = [[title]] + rows
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
                # the page stamp ('... (Vote for 1) 8/9/2024 11:16:46 AM').
                line = line.rstrip('.')
                line = re.sub(r'\s+\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}'
                              r'(?::\d{2})?\s*(?:[AP]M)?\s*$', '', line)
                # A title can repeat its party between the party and
                # vote-for groups ('... Precinct 1 (DEM) DEM (Vote for 2)'),
                # or garble it ('... (Vote for 4) PEP').
                line = re.sub(r'\((DEM|REP|LIB|UST|GRN|NLP)\) \1 \(Vote for',
                              r'(\1) (Vote for', line)
                line = re.sub(r' PEP$', ' REP', line)
                m = TITLE.match(line) or PROPOSAL.match(line)
                if m:
                    resolve_pending(where)
                    if cur is not None:
                        contests.append(cur)
                    groups = m.groups()
                    tag = groups[1] if len(groups) >= 2 and groups[1] in (
                        'DEM', 'REP', 'LIB', 'UST', 'GRN', 'NLP') else ''
                    title = groups[0].strip()
                    for pat, rep in cfg.get('title_sub', []):
                        title = re.sub(pat, rep, title)
                    # '(Vote for .1)' — an OCR dot before the digit
                    vote_for = (groups[2] if tag else groups[1]).replace(
                        '.', '')
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
                        if rs == ['total_votes'] and len(vv) == 2:
                            # A headerless zero-candidate results footer
                            # prints Total Votes and Unresolved Write-In.
                            rr = ['total_votes', 'unresolved']
                        # Short footers assign left-to-right with tail roles
                        # unfilled (see take_values); only a surplus of
                        # values is ambiguous.
                        pairs = list(zip(rr, vv))
                        if len(vv) > len(rr):
                            problems.append(
                                f'{where}: footer {label!r} seg {seg_i} '
                                f'values {vals} vs roles {rs}')
                            pairs = []
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
    rows = parse_county(args.county, cfg, problems)
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