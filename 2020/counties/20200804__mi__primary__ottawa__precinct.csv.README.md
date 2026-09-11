# Ottawa County 2020 primary precinct file — KNOWN DATA PROBLEM (not committed)

The only precinct-level source for the 8/4/2020 primary in Ottawa County is
`Ottawa MI AUG0420_Election_Results.xlsx` (openelections-sources-mi/2020/primary,
title "August 4th, 2020 Official Election Results", exported 2020-09-08).
Parsed by `src/xlsx_2020_primary.py` (parse_ottawa), it fails verification
against the certified county-level CENR file
(`2020/20200804__mi__primary__county.csv`) and against the state's official
turnout page (mielections.us 2020PRI_CENR_TURNOUT, archived copy consulted).

## The numbers

| source | ballots cast |
|---|---|
| Unofficial precinct PDF (miottawa.org precinctFile-124, run 8/5/2020) | 70,926 |
| **State-certified official turnout** | **72,213** |
| XLSX "Official Election Results" | 75,025 |

The XLSX exceeds the certified total by 2,812 ballots. It also exceeds the
certified candidate votes (CENR): Peters +610, James +878, Berghoef +206,
Huizenga +277.

## Where the excess is

Comparing the XLSX per precinct against the (unofficial) 8/5 PDF:

- Georgetown Charter Township Precinct 8: XLSX 945 (8-A 724 + 8-B 221) vs PDF 321
- Georgetown Charter Township Precinct 12: XLSX 780 vs PDF 262
- City of Holland Ward 4 Precinct 10: XLSX 445 vs PDF 304
- City of Holland W4 P9 / W5 P11 / W5 P12 / W5 P13 appear in the XLSX but not
  at all in the 8/5 PDF (1,703 ballots)

i.e. ~2,800 of the 2,812 excess sits in those precincts and looks like
double-counted absentee batches. Candidate votes are inflated in the same
precincts (e.g. Georgetown P8 Peters: XLSX 216 vs unofficial PDF 29).

The six combined-reporting townships (Fillmore, Laketown, Overisel, Ravenna,
Salem, Sullivan Precinct 1, 1,109 ballots total) carry only turnout/millage
rows in the XLSX; they are absent from the 8/5 PDF, which explains most of the
remaining certified-minus-PDF gap (72,213 - 70,926 = 1,287).

## Status

No post-canvass (certified) precinct-level file for the Aug 2020 primary is
publicly available: miottawa.org's precinctFile-124 was captured by the Wayback
Machine (Oct 2020) but contains the 8/5 *unofficial* run, and the county's
current results site (miottawavotes.gov, AUG0420) is a JS app with no archived
data. The generated
`2020/counties/20200804__mi__primary__ottawa__precinct.csv` is therefore
**withheld from the repo** pending a reliable source; do not commit it as-is.

Note: the same county exports used for Marquette and Macomb inflate their
"Total Ballots Cast" columns too, but there the candidate votes match CENR and
the inflation came from duplicate precinct-name variants (West/W. Branch,
Powell comma variant, Ishpeming Pct 1 aggregate, Richmond City "1A"), which
`src/csv_2020_primary.py` now normalizes so that turnout sums match the
certified totals exactly (17,141 and 212,513).