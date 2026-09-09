# 2022 August Primary (2022-08-02) — remaining gaps and discrepancies

Status of the per-county precinct files
(`2022/counties/20220802__mi__primary__<county>__precinct.csv`) against the
certified county-level CENR (`2022/20220802__mi__primary__county.csv`, converted
from the SOS by-county export; shared classify/verify/write in
`src/primary_2022_common.py`). Written 2026-09-09, at the end of the 2022
primary parsing run. **57 of the 83 CENR counties have a precinct file** (57,639
rows generated into `2022/20220802__mi__general__precinct.csv` by
`statewide_generator.py 2022 20220802`); all 57 county files and the statewide
file pass the four openelections-data-tests checks (file_format,
duplicate_entries, missing_values, vote_breakdown_totals).

Every contest in a written file sums exactly to the CENR except where noted
below. Sources live in `openelections-sources-mi/2022/primary/`; parsers are
`src/primary_2022_*.py` plus the format-specific scripts named in `src/`.

## 1. Counties with no precinct file (26)

### 1a. Source exists but is unusable (2)

| County | Source | Problem |
|---|---|---|
| Kent | `Kent County Aug 2022 Primary Precinct Results.pdf` (22 pp, image-only SOVC) | Turnout-only: Registered Voters / Ballots Cast / % Turnout / Election Day / AVCB per precinct — the PDF contains no candidate names at all (verified by `pdftotext` + a PaddleOCR check). |
| Bay | `Bay County Aug 2022 Primary Official Summary Results.pdf` (19 pp) | Summary-only: countywide TOTALs per contest (44/44 precincts reporting), no per-precinct rows. |

### 1b. No source file at all (24)

The other 24 CENR counties have nothing in `openelections-sources-mi/2022/primary/`.
The CENR votes they carry (about 2.5M of the 8.06M certified total; Kent alone is
545,864 and Oakland 1,132,910):

Chippewa 25,236 · Clinton 64,793 · Eaton 84,298 · Grand Traverse 104,669 ·
Gladwin 21,859 · Huron 27,024 · Keweenaw 2,609 · Lake 9,843 · Lapeer 86,260 ·
Luce 4,404 · Mackinac 11,853 · Mason 26,072 · Menominee 21,003 · Monroe 116,377 ·
Oakland 1,132,910 · Osceola 20,651 · Oscoda 7,745 · Ottawa 271,280 ·
Presque Isle 12,842 · Roscommon 27,466 · Shiawassee 58,909 · St. Clair 143,854 ·
St. Joseph 41,879 · Van Buren 49,204

Bay (84,370) and Kent (545,864) bring the total missing to 3,003,274 votes —
the statewide precinct file covers only the 57 sourced counties.

## 2. Contests absent from a written county's source (3 counties)

- **Genesee** — `Circuit Court Judge[7]` (CENR nonpartisan: Dawn M. Weier 21,349,
  Mary Hood 20,102, Rebecca Jurva-Brinn 13,975) is absent from the canvass
  "Canvass Results" PDF, which carries only the partisan contests (no "Circuit"
  anywhere in its text). Parser: `src/primary_2022_genesee.py`.
- **Newaygo** — `State Senate[33]` (CENR DEM Mark Bignell 1,953, REP Rick Outman
  7,713) is omitted from the county's JSON export entirely; the export's ballot
  list just lacks it. Parser: `src/primary_2022_json_counties.py`.
- **Wayne** — `U.S. House[6]`, `[12]`, `[13]` (Dingell/Tlaib/Thanedar contests;
  25 CENR candidates, ~150k votes): the county's "Election Precinct Report" PDF
  set has no U.S. House report. Parser: `src/primary_2022_wayne.py` (documented
  in its header).

## 3. Qualified write-in candidates the files omit (12 counties)

The CENR names individual qualified write-ins (chiefly Governor-REP James Elmer
Craig); in these counties the county's report either drops the write-in column
entirely or the OCR lost it, so the file carries no such candidate and no lumped
`Write-In` row covering it. All other candidates in those counties match the
CENR exactly.

| County | Contest | Candidate | CENR votes |
|---|---|---|---|
| Antrim | Governor REP | James Elmer Craig | 32 |
| Charlevoix | Governor REP | James Elmer Craig | 44 |
| Charlevoix | U.S. House 1 REP | David John McDonell | 1 |
| Cheboygan | Governor REP | James Elmer Craig | 30 (file carries all-zero rows) |
| Dickinson | Governor REP | James Elmer Craig | 21 |
| Iron | Governor REP | James Elmer Craig | 3 |
| Macomb | Governor REP | James Elmer Craig | 5,483 |
| Mecosta | Governor REP | James Elmer Craig | 23 |
| Montcalm | Governor REP | James Elmer Craig | 27 |
| Montcalm | U.S. House 2 REP | Jericho Joel Gonzales | 2 |
| Otsego | Governor REP | James Elmer Craig | 11 |
| Saginaw | Governor REP | James Elmer Craig | 60 |
| Sanilac | Governor REP | James Elmer Craig | 126 |
| Tuscola | Governor REP | James Elmer Craig | 40 |

(Antrim's omission is allowlisted in `src/primary_2022_ocr_sovc.py` `ALLOWED` —
the printed report's Total 5,446 excludes his 32 certified votes. Kalkaska's
Craig column, lost the same way by OCR, *was* recovered via the parser's
`SPILL_TABLES` mechanism and is complete.)

Total write-in votes not attributed to precincts: ~5,906. In 15 other counties
the same write-ins sit inside a lumped `Write-In` row (verify NOTEs) — see §5.

## 4. Numeric residuals kept source-faithful (4 counties)

Small per-candidate differences where the file is faithful to the source's
printed precinct numbers and the CENR disagrees. All are allowlisted in the
respective parser's `ACCEPTED_RESIDUALS`/`ALLOWED_WRITEIN`:

- **Wayne** — 61 residuals (Governor, State Senate 1-13, State House 1-29) from
  the county's 8/3/2022 **unofficial** "Election Precinct Report" PDFs; parsed
  sums match each report's own printed county totals. Largest: Governor-DEM
  Whitmer 189,769 vs 190,142 (−373), Senate 5-DEM Overman 6,694 vs 6,954
  (−260), Senate 5-DEM Polehanki 19,762 vs 19,822 (−60), House 26-DEM
  Steven Chisholm 2,337 vs 2,548 (−211). Parser: `src/primary_2022_wayne.py`.
- **Kalamazoo** — 6 residuals (Circuit Court 9 Barnard −1, District Court 8
  Jones −4, House 42 Hall −1, Senate 19 Mitchell −1, U.S. House 5 Goldberg −14,
  Walberg −1) from the county's own XLSX export. Parser:
  `src/primary_2022_kalamazoo.py`.
- **Lenawee** — 3 residuals in `State House[34]` REP (Dale W. Zorn 4,775 vs
  4,788, Julie Moore 2,413 vs 2,418, Ryan Rank 4,767 vs 4,771); every
  precinct cell matches the source. Parser: `src/lenawee_sovc_parser.py`.
- **Ingham** — 2 *positive* residuals: the source's printed precinct rows sum
  **above** the source's own printed county totals (Governor-REP Craig 209 vs
  printed 208 (+1), U.S. House 7-REP Jake Hagg 681 vs 678 (+3)); the CENR
  agrees with the printed totals. The printed precinct rows are kept as-is
  (Wayne-parser allowlist precedent). Parser: `src/primary_2022_ingham.py`
  `ALLOWED_WRITEIN`.

## 5. Acceptable verify NOTEs — lumped write-in coverage (15 counties)

In these counties the CENR's named qualified write-ins are inside a lumped
`Write-In` row the file does carry (their vote totals are not lost; they are
just not attributed to the named candidate). From a full re-run of
`primary_2022_common.verify` over the written files:

| County | Candidates inside the lumped Write-In row (CENR votes) |
|---|---|
| Allegan | Craig (46), Alfonso (1,082) |
| Alpena | Craig (21) |
| Barry | Craig (27), Rocha (533), Gonzales (5) |
| Berrien | Craig (85), Alfonso (673), Trouten (1), Ferszt (20) |
| Branch | Craig (6) |
| Cass | Craig (26), Trouten (1), Ferszt (1) |
| Delta | Adkisson (1), Craig (25), Blackburn (1), McDonell (3) |
| Iosco | Craig (35) |
| Isabella | Craig (40) |
| Kalamazoo | Craig (124), Alfonso (6,050), Ferszt (3) |
| Lenawee | Craig (129), Ferszt (8) |
| Ogemaw | Craig (2) |
| Ontonagon | Craig (1) |
| Wayne | Adkisson (7), Craig (4,411), Blackburn (7), Johnson (77), Alexander (8) |
| Wexford | Craig (34), Gonzales (4) |

## Reproduce

Sum each county file against the CENR (prints the NOTE/WARNING lines; returns
any residual numeric/missing-candidate diffs):

```
.venv/bin/python - <<'EOF'
import csv, glob, sys, re
from collections import defaultdict
sys.path.insert(0, 'src')
import primary_2022_common as pc
for path in sorted(glob.glob('2022/counties/20220802__mi__primary__*precinct.csv')):
    county = re.match(r'.*primary__(.+?)__precinct', path).group(1).title().replace('Gd.', 'Grand')
    rows, write_ins = [], defaultdict(int)
    for r in csv.DictReader(open(path)):
        rows.append((r['precinct'], r['office'], r['district'], r['party'],
                     r['candidate'], int(r['votes'])))
        if r['candidate'] == 'Write-In':
            write_ins[(r['office'], r['district'], r['party'])] += int(r['votes'])
    probs = pc.verify(county, rows, write_ins)
    print(county, len(probs), 'diffs')
EOF
```

Re-generate the statewide file: `python3 statewide_generator.py 2022 20220802`.