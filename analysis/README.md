# Michigan primary participation & swing analysis (2016–2026)

Analysis built on this repo's OpenElections Michigan data, plus 2018 primary county totals collected from county clerk reports. Everything here is derived; the source data files elsewhere in the repo are unchanged.

## Deliverables

| File | What |
|---|---|
| `output/mi_primary_story.pdf` | 5-page report: county story (2016→2018 vs 2024→2026, 2018 vs 2026 participation) and congressional-district story (2024→2026) |
| `output/mi_primary_story.csv` | Simplified table behind the report: one row per state, region, county and congressional district |
| `output/mi_legislature_2026_nominees.csv` | Democratic and Republican nominee for every State House and State Senate seat (2026 primary winners) |
| `output/mi_legislature_2026_primary_candidates.csv` | Every Democratic and Republican legislative primary candidate with votes |
| `michigan_primary_shift.html` | Interactive page: 2024 vs 2026 primary turnout by county, region, municipality and precinct |
| `michigan_midterm_primaries.html` | Interactive page: 2018 vs 2026 governor primary participation by county |
| `output/mi_swing_baseline.xlsx` | Precinct and municipality tables: 2022/2024 general margins, 2024/2026 primary turnout, empty 2026 general columns |

## Scripts (run in this order with `.venv/bin/python`)

| Script | Builds |
|---|---|
| `build_swing.py` | Precinct/municipality swing baseline (`mi_precinct_swing_baseline.csv`, `mi_municipality_swing_baseline.csv`, `mi_swing_baseline.xlsx`, `coverage_by_county.csv`) |
| `build_districts.py` | Congressional-district primary totals, 2024 and 2026 (`mi_cd_primary_2024_2026.csv`, `mi_cd_allocation_check.csv`) |
| `build_story.py` | `mi_primary_story.csv` |
| `make_report.py` | `mi_primary_story.pdf` (needs reportlab and matplotlib, which aren't project dependencies) |
| `build_legislature.py` | The two legislature CSVs |

`output/mi_2018_primary_governor_by_county.csv` and `output/mi_2018_primary_governor_clerk_sources.csv` hold the 2018 governor primary county totals and where each came from. They were collected by hand from clerk reports, so no script regenerates them.

## Swing baseline columns

Margins are **D minus R**: positive = Democratic lead, negative = Republican lead.

- `g22_*`: 2022 general, Governor (Whitmer v Dixon). `_oth` = all other candidates.
- `g24_*`: 2024 general, President (Harris v Trump). `g24_margin_votes`, `g24_margin_pct` (% of all presidential votes), `g24_winner`.
- `swing_22_to_24_pts`: change in margin, in points (negative = moved toward R).
- `p24sen_*`: Aug 2024 primary, U.S. Senate (the only statewide race on both party ballots).
- `p26gov_*`: Aug 2026 primary, Governor (top of ticket). `p26sen_*` also given (R Senate was uncontested).
- `primary_{dem,rep,total}_change[_pct]`: 2026 Governor primary vs 2024 Senate primary votes.
- `primary_dem_share_shift_pts`: change in Democratic share of the two-party primary vote.
- `g26_*`, `swing_24_to_26_pts`: **empty placeholders** for the Nov 3, 2026 general.
- `in_*` flags: whether the row has data from that source. Unmatched rows are kept so totals reconcile.

## Coverage and caveats

- **2026 results** come from the repo's September 2026 work-in-progress update and haven't been checked against the Michigan Secretary of State.
- **2018 primary county totals** cover 73 counties from clerk reports. Antrim, Iron, Kalkaska, Keweenaw, Lake, Mackinac, Mecosta, Monroe, Montmorency and Osceola aren't online; statewide figures use the official totals (1,131,447 D, 989,525 R). Oakland's came from an Internet Archive copy of the county clerk's own results file.
- **2016 primary baseline** is the U.S. House race (no statewide race in Aug 2016). It's unusable in the 2016 4th District, where Democrats had no candidate.
- **Congressional districts** split across counties are divided by precinct results where available, otherwise by each party's U.S. House primary votes in that county (median error about 2.5% when checked against precinct results). District 11's 2024 figures are fully estimated this way.
- **Precinct coverage:** 2024 primary precinct files exist for 74 counties, 2026 for 51. Michigan consolidated many precincts in 2025, and some places report absentee or early votes as separate pools, so use the municipality table where precincts don't line up.
- **Primary turnout** counts votes cast for candidates in the top race, so voters who skipped that race aren't counted.
- **Legislature nominees** are primary winners only. Third-party nominees (chosen at conventions) and changes after the primary aren't included.
- **Source-data defects found:** 2018 primary precinct files include `Totals` rows (dropped here) and Berrien's has no governor rows; the Oakland and Sanilac 2018 "primary" source files are actually the November general; Kent 2024 primary has rows named just "Township, Precinct N"; Barry 2024 primary has rows without municipality names; Otsego 2024 general has garbled names; the 2024 general file uses "St. Joseph's" as a county name and stores some vote counts with thousands separators.
