# 2022 November General (2022-11-08) — precinct-file vs county-file discrepancies

Comparison of the per-county precinct files
(`2022/counties/20221108__mi__general__<county>__precinct.csv`) against the
county-level certified file (`2022/20221108__mi__general__county.csv`, converted
from the SOS CENR by-county export by `src/cenr_2022.py`). Written 2026-09-07,
after the five largest gaps (Macomb, Livingston, Emmet, Allegan, Dickinson) were
repaired by reparsing their county source PDFs — see
`src/macomb_2022_general.py`, `src/livingston_2022_proposal.py`,
`src/emmet_2022_general.py`, `src/allegan_2022_general.py`,
`src/dickinson_2022_general.py` and the memory note `gap-repairs-2022-general`.

Reproduce the full per-county detail with:

```
.venv/bin/python src/verify_2022_general_county.py
```

## Method

For each county the precinct file is summed by (office, district, candidate) and
compared against the CENR county rows. Pseudo-candidates (Ballots Cast,
Registered Voters, Over/Under Votes, blank candidate) and the Straight Party
office are not compared. Differences fall into four classes:

- **Missing contests** — (office, district) contests in the CENR with no rows in
  the county file.
- **Missing candidates (partial)** — contests partially present where named CENR
  candidates are absent from the file.
- **Numeric** — candidates in both whose sums differ.
- **File-only** — candidates in the file the CENR does not carry (almost all are
  the lumped `Write-In` rows most files carry; the CENR instead names individual
  write-in candidates, so the same votes appear on the CENR side as missing
  0-vote write-in candidates).

**Result: 83 county files, 0 fully clean. 80 counties are missing at least one
contest; 856 CENR candidates are absent from files (828 of them zero-vote,
mostly named write-in candidates the files lump into a Write-In row); 86 numeric
diffs; 220 file-only candidates.** The three counties repaired by reparse have
no missing contests (Allegan, Dickinson, Macomb); Emmet is missing only
`Court of Appeals Judge[4]`, and Livingston still carries the full set of
missing offices above because its reparse covered only Proposal 22-3.

## Class 1 — missing contests

### Non-whitelisted offices (no impact on the generated statewide file)

`statewide_generator.py`'s `OFFICE_WHITELIST` filters all of the following out
of the statewide file, so these gaps do not create holes there — but the county
files are incomplete relative to the certified results:

| Office | Counties missing it |
|---|---|
| Court of Appeals Judge | 80 |
| Proposal 22-1 / 22-2 | 76 each |
| State Board of Education / Regent / MSU Trustee / WSU Governor / Justice of Supreme Court | 75 each |
| District Court Judge | 51 |
| Circuit Court Judge | 42 |
| Probate Court Judge | 10 |

The five reparsed counties: Macomb, Allegan and Dickinson now carry all of
these; Emmet is missing only `Court of Appeals Judge[4]` (its SOVC source
predates the CoA contest tables); Livingston's 2026 reparse covered only
Proposal 22-3, so it is still missing Circuit Court Judge[44], District Court
Judge[53], `Court of Appeals Judge[4]`, and the statewide-nominated offices and
Proposals 22-1/22-2. Dickinson's appended offices came from the OCR'd source,
Allegan's and Macomb's from their per-precinct PDF / Civera DB.

### Whitelisted legislative contests (real holes in the statewide file)

These are filtered *into* the statewide file, so their absence leaves actual
gaps in `2022/20221108__mi__general__precinct.csv`:

| County | Missing contest | CENR totals |
|---|---|---|
| Lake | State Senate 33 | Bignell 1,167 / Gillotte 65 / Outman 1,942 |
| Lapeer | State Senate 25 | Van Dyke 0 / Lauwers 3 |
| Midland | State Senate 35 | McDonald Rivet 14,257 / Glenn 15,899 |
| Midland | U.S. House 8 | Kildee 16,621 / Canny 588 / Junge 18,305 / Goodwin 881 |
| Sanilac | State House 64 | Howell 578 / Beeler 1,259 |
| St. Clair | State House 63, State House 64, State Senate 12 | — |

Partially present legislative contests (named candidate missing from the file):

| County | Contest | Missing candidate | CENR votes | File has lumped Write-In rows? |
|---|---|---|---|---|
| Clare | State Senate 34 | Christine Gerace | 3,935 | no |
| Clare | Attorney General | Joseph W. McHugh Jr. | 213 | no |
| Clare | Secretary of State | Larry James Hutchinson Jr. | 51 | no |
| Mackinac | State Senate 38 | Wade Paul Roberts | 12 | 1 |
| Macomb | State House 58 | Giovanni Ndrea | 5 | no |
| Lapeer | State House 67 | Gabriel Bernard Lossing | 11 | 77 |
| Tuscola | State House 67 | Gabriel Bernard Lossing | 0 | 5 |

Clare is the worst of these — its file (one of two with a nonstandard
single-underscore filename, see notes below) is missing 3,935 Gerace votes and
the two U.S. Taxpayers statewide candidates entirely.

## Class 2 — numeric diffs (86)

### Larger unexplained diffs (|diff| ≥ 6)

| County | Office [district] | Candidate | File | CENR | Diff |
|---|---|---|---|---|---|
| Monroe | Governor | Gretchen Whitmer | 30,452 | 29,482 | +970 |
| Monroe | U.S. House [5] | Tim Walberg | 41,940 | 41,549 | +391 |
| Clare | U.S. House [2] | John Moolenaar | 8,375 | 8,756 | −381 |
| Monroe | Secretary of State | Jocelyn Benson | 30,154 | 30,284 | −130 |
| Gratiot | Governor | Gretchen Whitmer | 6,385 | 6,285 | +100 |
| Clare | Governor | Tudor M. Dixon | 7,950 | 7,850 | +100 |
| Clare | State House [100] | Nate Bailey | 3,619 | 3,519 | +100 |
| Tuscola | U.S. House [9] | Jake Kelts | 338 | 247 | +91 |
| Osceola | Proposal 22-3 | No | 6,415 | 6,375 | +40 |
| Muskegon | Proposal 22-3 | No | 32,402 | 32,445 | −43 |
| Muskegon | Proposal 22-3 | Yes | 40,472 | 40,447 | +25 |
| Kent | Proposal 22-3 | Yes | 161,271 | 161,304 | −33 |
| Gogebic | U.S. House [1] | Jack Bergman | 3,711 | 3,681 | +30 |
| Oakland | Proposal 22-3 | Yes | 396,786 | 396,814 | −28 |
| Ingham | Proposal 22-3 | Yes | 81,620 | 81,642 | −22 |
| Wayne | Proposal 22-3 | No | 200,068 | 200,048 | +20 |
| Kent | Proposal 22-3 | No | 133,598 | 133,581 | +17 |
| Ingham | Proposal 22-3 | No | 35,473 | 35,458 | +15 |
| Macomb | Proposal 22-2 | No | 153,814 | 153,827 | −13 |
| Antrim | Proposal 22-3 | Yes | 6,501 | 6,489 | +12 |
| Washtenaw | Proposal 22-3 | Yes | 136,355 | 136,366 | −11 |
| Ogemaw | Secretary of State | Kristina Elaine Karamo | 5,793 | 5,803 | −10 |
| Genesee | Secretary of State | Larry James Hutchinson Jr. | 658 | 667 | −9 |
| Iron | Secretary of State | Jocelyn Benson | 2,334 | 2,284 | +50 |
| Antrim | Proposal 22-3 | No | 7,347 | 7,356 | −9 |
| Macomb | Proposal 22-3 | No | 167,350 | 167,368 | −18 |
| Schoolcraft | State House [108] | David Prestin | 2,400 | 2,451 | −51 |
| Otsego | Proposal 22-3 | Yes | 5,252 | 5,258 | −6 |
| Grand Traverse | Proposal 22-3 | No | 22,152 | 22,158 | −6 |
| Presque Isle | Governor | Daryl M. Simpson | 23 | 17 | +6 |

Schoolcraft deserves a special note beyond the Prestin diff above: **every**
statewide contest it carries is short by 1–3 votes (Nessel −1, DePerno −1,
McHugh −2, Whitmer −3, Buzuma −1, Benson −2, Stempfle −2, Bergman −1, Gale −2,
Lorinser −1, McBroom −1, Braamse −1, Roberts −2, Lopez −3, 22-3 Yes −4) — a
systematic undercount pattern, most likely one missing precinct's rows or a
missed absentee breakdown.

### Certified-vs-canvass residuals (|diff| ≤ 5)

The remaining 56 diffs are 1–5 vote differences, overwhelmingly Proposal 22-3
Yes/No (the 22-3 rows were appended from county SOVC sources in the original
commit, and the county canvass totals differ from the certified CENR by a few
votes): Allegan 22-3 No −1 (source-faithful, matches the county's own printed
total), Benzie No +1/Yes −2, Berrien No −1/Yes +1, Calhoun No +1/Yes −4,
Charlevoix No −1/Yes +1, Clare No −3/Yes +2, Clinton No −3, Crawford Brandenburg
−1, Dickinson 22-3 No −1 and Space −1 (both source-faithful), Emmet No −1/Yes
+1, Genesee Van Sickle −1 and 22-3 No −4/Yes −5, Gogebic McBroom +1, Iosco No
−2, Iron Markkanen +3, Isabella Yes −1, Jackson No −1, Kalamazoo No +1/Yes +1,
Kalkaska Yes −2, Lapeer Alexander −3, Livingston No +1/Yes −4, Macomb 22-2 Yes
−3, Marquette Yes −1, Oakland 22-3 No −4, Oscoda Lorinser −1, Otsego 22-3 No −2,
Ottawa Yes −1, Shiawassee No −5/Yes +1, Washtenaw 22-3 No +2, Wayne 22-3 Yes
−2. The Allegan and Dickinson entries were verified to match those counties' own
printed SOVC totals during the 2026 reparse and are kept deliberately.

## Class 3 — write-in representation (not gaps in the data, mostly)

The CENR names individual write-in candidates (Governor: Bob Scott, Evan S.
Space, Michael David Kelley, …); county files instead carry a single lumped
`Write-In` row per contest (St. Joseph additionally uses `Unqualified
Write-Ins`). This shows up as 856 CENR candidates absent from files — 828 of
them 0-vote except:

- **Governor write-ins** (Kelley 1–2, Space 1–4, Gipson 1–3, Hunt 1, Adkisson 1,
  Scott 2) in Allegan, Cheboygan, Chippewa, Grand Traverse, Ingham, Jackson,
  Kent, Macomb, Mason, St. Clair, Wayne — votes are presumably inside each
  file's lumped `Write-In` row.
- **Wayne** State House 3 (Shona Doreen Beasley 4), State Senate 2 (Michael Lynn
  Beasley 4), U.S. House 13 (Sanders 3, Landin 1, Cole 1), U.S. House 6 (Acosta
  1) — same lumped-write-in situation (file lumped sums: 99 / 189 / 727 / 260).
- The genuine gaps already listed in Class 1: Clare AG/SoS/SD-34, Mackinac
  SD-38, Macomb HD-58, Lapeer/Tuscola HD-67.

Presque Isle's Daryl M. Simpson diff (+6) is the same representation issue: the
file counts 23 write-in votes for Simpson where the CENR certified 17.

## Class 4 — file-only candidates (220)

Lumped `Write-In` rows the CENR does not carry (215 keys across most counties,
e.g. Van Buren: AG 43, Governor 25, SoS 30, U.S. House 4 38, …) plus St.
Joseph's `Unqualified Write-Ins` rows (Governor 15, U.S. House 5 46). These are
extra detail beyond the CENR, not missing votes, and were kept as-is.

## Housekeeping notes

- Two county files use single-underscore filenames,
  `20221108__mi__general__clare_precinct.csv` and
  `20221108__mi__general__presque_isle_precinct.csv`, violating the
  `{date}__mi__{election}__{county}__precinct.csv` convention. `statewide_generator.py`
  and the CI format test tolerate them, but renaming to double underscores is
  recommended before any reprocessing.
- The five reparsed counties' files match the CENR except the deliberate
  source-faithful residuals above (Allegan 22-3 No −1; Dickinson 22-3 No −1 and
  Governor Evan S. Space 0-vs-1) and their preserved lumped `Write-In` rows.