# 2024 August Primary (2024-08-06) — precinct-file vs county-file discrepancies

Comparison of the per-county precinct files (`2024/counties/20240806__mi__primary__<county>__precinct.csv`)
against the county-level certified file (`2024/20240806__mi__primary__county.csv`,
converted from the SOS CENR by-county export by `src/cenr_2024_primary.py`).

## Method

For each county, the precinct file is summed by (office, district, party, candidate)
for the offices the county file covers (U.S. Senate, U.S. House, State House, and
Circuit/District/Probate Court Judge) and compared for candidates present in **both**
sets. Candidate names are matched exactly, then on a normalized form, then on
space-free form, token containment, and last-name + first-initial fallbacks
(`src/verify_2024_primary_county.py`). Pseudo-candidates (Ballots Cast, Registered
Voters, Over/Under Votes) are not compared; write-in variants (' (W)', 'Write-in',
'Write-ins') are collapsed to 'Write-In'.

**Result: 1,157 candidates compared, 1,082 match exactly, 75 mismatch.**
75 counties have at least one comparable contest; **55 counties match with zero
mismatches**. The 75 mismatches fall in 15 counties and are all accounted for by
the classes below — none is a data error in the precinct files.

| County | matched | mismatched | reason |
|---|---|---|---|
| Alcona | 0 | 14 | certified-vs-canvass (1) |
| Arenac | 9 | 3 | certified-vs-canvass (1) |
| Barry | 13 | 2 | certified-vs-canvass (1) |
| Berrien | 19 | 1 | certified-vs-canvass (1) |
| Chippewa | 14 | 1 | certified-vs-canvass (1) |
| Genesee | 27 | 6 | certified-vs-canvass (1) + CENR HD68 quirk (4) |
| Huron | 6 | 3 | county's own reports disagree (2) |
| Ingham | 11 | 7 | privacy-masked precincts (3) |
| Isabella | 8 | 2 | certified-vs-canvass (1) |
| Kalkaska | 11 | 5 | certified-vs-canvass (1) |
| Kent | 36 | 3 | privacy-masked precincts (3) |
| Macomb | 55 | 1 | write-in class (5) |
| Midland | 6 | 10 | certified-vs-canvass (1) |
| Oceana | 2 | 11 | certified-vs-canvass (1) |
| Wayne | 78 | 6 | GP Shores P2 cross-county precinct (6) + write-in class (5) |

## The mismatch classes

### 1. Certified total vs county canvass (CENR-vs-source), 54 of the 75

For these counties, every contested candidate's precinct sum matches the county's
**own** source exactly — the printed county totals in the county's SOVC / summary
PDF, or the county's own CSV/XLSX export — and the certified CENR figure differs,
in both directions, typically by 1–33 votes (largest: Berrien U.S. House 5 DEM
4742 vs 4683, Chippewa HD108 DEM 618 vs 539, Alcona HD106 REP 1313 vs 1286).
This is the normal gap between the county canvass and the certified statewide
canvass; our files are faithful to the county source.

Spot-verifications against the counties' own sources (2026-09-07):

- **Midland**: source CSV sums identical for all 10 contested candidates.
- **Isabella**: XLSX "Summary Results" identical (Odykirk 4,667 / Neyer 6,345).
- **Arenac**: SOVC prints Slotkin 711 / Lorinser 355 / LeRoux 697 (= ours; CENR
  712/357/698).
- **Barry**: SOVC prints Amash 5,186 / O'Donnell 1,499 (= ours; CENR 5,188/1,497).
- **Alcona**: printed Totals row 1,686 Bergman / 1,286 Cavitt / 799 Smalenberg /
  463 Borenstein / 274 Hamilton (= ours).
- **Chippewa**: SOVC prints Reynolds 539 (= ours; CENR 618).
- **Berrien, Kalkaska, Oceana, Genesee**: verified in earlier sessions.
- **Dickinson**: HD109 now matches CENR exactly; 'George Meister' 12 is a real
  named candidate column in the source that CENR does not record (kept,
  source-faithful; appears as a precinct-only candidate, not a mismatch).

### 2. County's own reports disagree — Huron, 3

Our file matches the per-precinct PDF's own printed Total row exactly
(Hill Harper 198 / Elissa Slotkin 1,316 / write-in 3). Huron's separate countywide
summary PDF prints 200 / 1,315 / 3 — the county's two reports disagree by ±1–2,
and the certified CENR (199 / …) sits between them. Ours is faithful to the
precinct-level report, the better source for precinct data.

### 3. `****` privacy-masked precincts — Ingham (7) and Kent (3)

The source masks low-turnout precinct cells with `**** - Insufficient Turnout to
Protect Voter Privacy`; where every candidate cell in a precinct is masked, the
votes are unknowable and are emitted as blank votes (the Lenawee/Jackson
precedent). CENR carries the unmasked certified totals, so our sums fall short by
exactly the masked votes:

- **Ingham**: masked precincts East Lansing 1/12/14/15, Lansing Charter 1,
  Williamstown 1; residual per candidate ≤ those precincts' Times Cast
  (Brixie 26 of ≤30, Hertel 16, Slotkin 13, Harper 4, Rockey 3, O'Donnell 2,
  Barrett 1).
- **Kent**: masked precincts Wyoming Ward 1 Precinct 5 and Grand Rapids Ward 3
  Precinct 51 (Rigas 2, Skaggs 2, Sage 1).

### 4. CENR quirk — Genesee HD68 DEM

CENR records Tim Sneller 0 and a Write-In 1,216; the source (and our file) record
Sneller 6,656 and 'Matt Schlinker (W)' 1,216 — CENR filed the qualified write-in
under Write-In and zeroed the named candidate. Accepted.

### 5. Cross-county / unmodeled candidates — Wayne HD12 and Dickinson

- **Wayne HD12**: the county extract's HD12 report includes Grosse Pointe Shores
  Precinct 2 — a MACOMB-side precinct (the November general file prints it as
  "Precinct 2 MACOMB" with all-zero HD12 votes) — worth +25 Times Cast, +4
  Kimberly L. Edwards, +11 Randell J. Shafer. The county's official summary
  (15 of 15 precincts) and CENR attribute those ballots to Macomb, so our sums
  exceed CENR by exactly those amounts. Kept: source-faithful to the extract.
- **Dickinson HD109 REP**: 'George Meister' 12 is a real named column in the
  source (an unqualified write-in candidate who qualified nowhere); CENR omits
  the name. Kept.

### 6. Raw vs official write-in rows, 5 mismatches (incl. Macomb's 1)

Precinct files carry the raw (unqualified) write-in totals per contest; CENR
carries only the official qualified write-in figure, usually 0. These rows differ
by construction and are listed separately by the verifier.

## Fixed during this review

Three real data errors were found by this same comparison and corrected
(2026-09-07):

- **Alger**: the committed file was missing City of Munising rows for several REP
  contests (Rogers 699 vs 846, Bohnak 866 vs 1,070). Re-parsed from the SOVC;
  file grew 287 → 546 rows and now matches CENR for all state offices.
- **Dickinson**: the HD109 candidate columns were split across two OCR table
  pages per party and the original parse collapsed the second table's candidates
  into bogus 'Write-In' rows. Rewritten by hand from the OCR cache; DEM Hill 400 /
  Brumm 33 / Girard 50, REP Bohnak 721 / Mason 87 / Wagner 72 / Meister 12,
  Breen unresolved Write-In 1 — all matching the source's printed county totals.
- **Wayne**: wrapped rotated headers were read out of order, mangling 23 candidate
  names ('Kimberly L. Edwards' as 'Edwards Kimberly L.', 'DeArtriss
  Coleman-Richardson' as 'DeArtriss Coleman-', …). Parser fixed and regenerated;
  all 23 now match CENR.