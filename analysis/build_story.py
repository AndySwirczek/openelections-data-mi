"""One simplified table for the primary-participation story: statewide, regions, counties, districts.

County story (long term): Aug 2016 (U.S. House) -> Aug 2018 (Governor) and
Aug 2024 (U.S. Senate) -> Aug 2026 (Governor), plus 2018 vs 2026 participation scaled to the
presidential electorate two years earlier.
District story (one cycle, 2022 congressional map): 2024 -> 2026 by congressional district.

Inputs: repo county/precinct files, analysis/output/mi_2018_primary_governor_by_county.csv
(county clerk reports), analysis/output/mi_cd_primary_2024_2026.csv (build_districts.py).
Usage: .venv/bin/python analysis/build_story.py
"""
from pathlib import Path

import pandas as pd

from build_swing import NON_CANDIDATE, load, norm_county

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "output"
SEN = ["U.S. Senate", "United States Senator", "United States Senator for State"]
# Official statewide 2018 governor primary totals (10 counties lack online county reports).
STATE_2018 = {"dem": 1131447, "rep": 989525}
REGIONS = {
    "West Michigan": ["Ottawa", "Allegan", "Barry", "Ionia", "Montcalm", "Newaygo", "Oceana"],
    "The Thumb": ["Huron", "Tuscola", "Sanilac", "Lapeer", "St. Clair"],
    "Macomb": ["Macomb"],
}
# 2016 4th District had no Democratic House candidate on the primary ballot.
MI4_2016 = {"Saginaw", "Montcalm", "Clare", "Clinton", "Isabella", "Mecosta", "Gladwin", "Gratiot",
            "Missaukee", "Midland", "Osceola", "Ogemaw", "Roscommon", "Shiawassee", "Wexford"}


def county_file(path, offices):
    c = pd.read_csv(path, dtype=str)
    c["votes"] = pd.to_numeric(c["votes"].str.replace(",", ""), errors="coerce").fillna(0)
    c = c[c["office"].str.contains(offices) & ~c["candidate"].fillna("").str.contains(NON_CANDIDATE)] \
        if "candidate" in c else c[c["office"].str.contains(offices)]
    c["county"] = c["county"].str.title().str.replace("Gd. Traverse", "Grand Traverse").map(norm_county)
    c["side"] = c["party"].str.upper().map({"DEM": "dem", "REP": "rep"})
    return c.dropna(subset=["side"]).pivot_table(index="county", columns="side", values="votes", aggfunc="sum")


def pres(path):
    g = load([path])
    g = g[(g["office"] == "President") & ~g["precinct"].str.contains("total|cumulative", case=False)]
    g["county"] = g["county"].str.replace("Gd. Traverse", "Grand Traverse").map(norm_county)
    g["side"] = "oth"
    g.loc[g["candidate"].str.contains("harris|clinton", case=False), "side"] = "dem"
    g.loc[g["candidate"].str.contains("trump", case=False), "side"] = "rep"
    return g.pivot_table(index="county", columns="side", values="votes", aggfunc="sum", fill_value=0)


def governor18(path):
    g = load([path])
    g = g[g["office"] == "Governor"]
    g["county"] = g["county"].str.replace("Gd. Traverse", "Grand Traverse").map(norm_county)
    g["side"] = None
    g.loc[g["candidate"].str.contains("whitmer", case=False), "side"] = "dem"
    g.loc[g["candidate"].str.contains("schuette", case=False), "side"] = "rep"
    return g.dropna(subset=["side"]).pivot_table(index="county", columns="side", values="votes", aggfunc="sum")


def metrics(t):
    t = t.copy()
    for y in ("2016", "2018", "2024", "2026"):
        d, r = t[f"dem_primary_{y}"], t[f"rep_primary_{y}"]
        t[f"dem_share_primary_{y}"] = (100 * d / (d + r)).round(1)
    t["harris_two_party_pct_2024"] = (100 * t.pres_dem_2024 / (t.pres_dem_2024 + t.pres_rep_2024)).round(1)
    t["harris_margin_pct_2024"] = (100 * (t.pres_dem_2024 - t.pres_rep_2024) / t.pres_total_2024).round(1)
    for y, base in (("2018", "pres_total_2016"), ("2026", "pres_total_2024")):
        for s in ("dem", "rep"):
            t[f"{s}_primary_per100_{y}"] = (100 * t[f"{s}_primary_{y}"] / t[base]).round(1)
        t[f"all_primary_per100_{y}"] = (t[f"dem_primary_per100_{y}"] + t[f"rep_primary_per100_{y}"]).round(1)
    t["shift_pts_2016_2018"] = (t.dem_share_primary_2018 - t.dem_share_primary_2016).round(1)
    t["shift_pts_2024_2026"] = (t.dem_share_primary_2026 - t.dem_share_primary_2024).round(1)
    for s in ("dem", "rep"):
        t[f"{s}_change_pct_2024_2026"] = (100 * (t[f"{s}_primary_2026"] / t[f"{s}_primary_2024"] - 1)).round(1)
        t[f"{s}_change_pct_2018_2026"] = (100 * (t[f"{s}_primary_2026"] / t[f"{s}_primary_2018"] - 1)).round(1)
    t["whitmer_two_party_pct_2018"] = (100 * t.gen18_dem / (t.gen18_dem + t.gen18_rep)).round(1)
    t["primary_lean_vs_harris_2026"] = (t.dem_share_primary_2026 - t.harris_two_party_pct_2024).round(1)
    return t


COLS = [
    "level", "name", "counties", "harris_two_party_pct_2024", "harris_margin_pct_2024",
    "pres_total_2016", "pres_total_2024",
    "dem_primary_2016", "rep_primary_2016", "dem_primary_2018", "rep_primary_2018",
    "dem_primary_2024", "rep_primary_2024", "dem_primary_2026", "rep_primary_2026",
    "dem_share_primary_2016", "dem_share_primary_2018", "dem_share_primary_2024", "dem_share_primary_2026",
    "shift_pts_2016_2018", "shift_pts_2024_2026", "whitmer_two_party_pct_2018", "primary_lean_vs_harris_2026",
    "dem_change_pct_2024_2026", "rep_change_pct_2024_2026", "dem_change_pct_2018_2026", "rep_change_pct_2018_2026",
    "all_primary_per100_2018", "all_primary_per100_2026", "dem_primary_per100_2018", "dem_primary_per100_2026",
    "rep_primary_per100_2018", "rep_primary_per100_2026", "notes",
]


def main():
    p16, p24 = pres(ROOT / "2016/20161108__mi__general__precinct.csv"), pres(ROOT / "2024/20241105__mi__general__precinct.csv")
    h16 = county_file(ROOT / "2016/20160802__mi__primary__county.csv", "Representative in Congress")
    s24 = county_file(ROOT / "2024/20240806__mi__primary__county.csv", "^U.S. Senate$")
    g26 = county_file(ROOT / "2026/20260804__mi__primary__county.csv", "^Governor$")
    g18 = pd.read_csv(OUT / "mi_2018_primary_governor_by_county.csv", index_col=0)[["d18", "r18"]]
    gen18 = governor18(ROOT / "2018/20181106__mi__general__precinct.csv")

    c = pd.DataFrame({
        "pres_total_2016": p16.sum(axis=1), "pres_total_2024": p24.sum(axis=1),
        "pres_dem_2024": p24["dem"], "pres_rep_2024": p24["rep"],
        "dem_primary_2016": h16["dem"], "rep_primary_2016": h16["rep"],
        "dem_primary_2018": g18["d18"], "rep_primary_2018": g18["r18"],
        "dem_primary_2024": s24["dem"], "rep_primary_2024": s24["rep"],
        "dem_primary_2026": g26["dem"], "rep_primary_2026": g26["rep"],
        "gen18_dem": gen18["dem"], "gen18_rep": gen18["rep"],
    })
    assert len(c) == 83, len(c)
    notes = pd.Series("", index=c.index)
    notes[c.dem_primary_2018.isna()] = "2018 county report not found online; included only in statewide total"
    notes[c.index.isin(MI4_2016)] = "2016 House baseline unusable (no Democratic candidate in 2016 4th District)"
    c["notes"], c["level"], c["name"], c["counties"] = notes, "county", c.index, c.index

    def total(names, label, level):
        sub = c.loc[names].drop(columns=["notes", "level", "name", "counties"])
        row = sub.sum(min_count=1)
        return {**row.to_dict(), "level": level, "name": label, "counties": ", ".join(names)}

    rows = []
    st = total(list(c.index), "Michigan", "state")
    st["dem_primary_2018"], st["rep_primary_2018"] = STATE_2018["dem"], STATE_2018["rep"]
    st["notes"] = "2018 = official statewide totals"
    rows.append(st)
    for label, names in REGIONS.items():
        r = total(names, label, "region")
        r["notes"] = "Montcalm's 2016 House baseline is unusable" if "Montcalm" in names else ""
        rows.append(r)
    out = pd.concat([pd.DataFrame(rows), c], ignore_index=True)

    cd = pd.read_csv(OUT / "mi_cd_primary_2024_2026.csv")
    d = pd.DataFrame({
        "level": "district", "name": "District " + cd.district.astype(str), "counties": cd.counties,
        "pres_total_2024": cd.pres24_dem + cd.pres24_rep + cd.pres24_oth,
        "pres_dem_2024": cd.pres24_dem, "pres_rep_2024": cd.pres24_rep,
        "dem_primary_2024": cd.p24sen_dem.round(), "rep_primary_2024": cd.p24sen_rep.round(),
        "dem_primary_2026": cd.p26gov_dem.round(), "rep_primary_2026": cd.p26gov_rep.round(),
        "notes": [f"{e:.0f}% of 2024 primary votes estimated from House-vote shares in split counties" if e > 0 else ""
                  for e in cd.pct_estimated_2024],
    })
    for col in ("pres_total_2016", "dem_primary_2016", "rep_primary_2016", "dem_primary_2018", "rep_primary_2018",
                "gen18_dem", "gen18_rep"):
        d[col] = float("nan")
    out = metrics(pd.concat([out, d], ignore_index=True))
    # 2016 -> 2018 shift only where both years are usable: blank it for 4th-District / missing-2018
    # counties, and recompute state and region figures over the usable counties only.
    valid = c.index[~c.index.isin(MI4_2016) & c.dem_primary_2018.notna()]
    bad = (out.level == "county") & ~out.name.isin(valid)
    out.loc[bad, ["dem_share_primary_2016", "shift_pts_2016_2018"]] = float("nan")
    for i in out.index[out.level.isin(["state", "region"])]:
        names = [n for n in out.at[i, "counties"].split(", ") if n in valid]
        v = c.loc[names]
        sh16 = 100 * v.dem_primary_2016.sum() / (v.dem_primary_2016.sum() + v.rep_primary_2016.sum())
        sh18 = 100 * v.dem_primary_2018.sum() / (v.dem_primary_2018.sum() + v.rep_primary_2018.sum())
        out.at[i, "dem_share_primary_2016"] = round(sh16, 1)
        out.at[i, "shift_pts_2016_2018"] = round(sh18 - sh16, 1)
        if len(names) < len(out.at[i, "counties"].split(", ")):
            note = f"2016->2018 shift uses the {len(names)} counties with usable 2016 and 2018 data"
            out.at[i, "notes"] = "; ".join(x for x in (out.at[i, "notes"], note) if x)
    # Per-100 for 2026 in districts uses the 2024 presidential electorate; 2018 isn't available.
    out = out[COLS]
    out.to_csv(OUT / "mi_primary_story.csv", index=False)
    return out


if __name__ == "__main__":
    o = main()
    pd.set_option("display.width", 250)
    print(o[o.level != "county"][["name", "harris_margin_pct_2024", "dem_share_primary_2024", "dem_share_primary_2026",
                                  "shift_pts_2024_2026", "primary_lean_vs_harris_2026", "dem_change_pct_2024_2026",
                                  "rep_change_pct_2024_2026", "all_primary_per100_2018", "all_primary_per100_2026",
                                  "shift_pts_2016_2018"]].to_string())
