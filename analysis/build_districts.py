"""Congressional-district view of the 2024 -> 2026 primary shift (2022 map, used in both years).

Primary totals start from the county-level primary files (all 83 counties), so district totals add
up to the official county totals. Counties wholly inside one district go straight to it. The 15
split counties are divided among their districts by:
  1. that year's precinct results, when the county has a precinct file (exact), else
  2. that party's U.S. House primary votes by district within the county, from the same
     election's county file -- an estimate, validated against (1) (median error ~2.5%).

Usage: .venv/bin/python analysis/build_districts.py
"""
import glob
from pathlib import Path

import pandas as pd

from build_swing import NON_CANDIDATE, load, norm_county

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "output"
SEN = ["U.S. Senate", "United States Senator", "United States Senator for State"]


def precinct_cd(df):
    """Map (county, precinct) -> congressional district using a file's own U.S. House rows."""
    h = df[df["office"] == "U.S. House"].copy()
    h["district"] = pd.to_numeric(h["district"], errors="coerce")
    h = h.dropna(subset=["district"])
    # A few precincts list two districts; keep the one with the most House votes.
    best = h.groupby(["county", "precinct", "district"])["votes"].sum().reset_index()
    best = best.sort_values("votes").drop_duplicates(["county", "precinct"], keep="last")
    return best.set_index(["county", "precinct"])["district"].astype(int)


def general24():
    g = load([ROOT / "2024/20241105__mi__general__precinct.csv"])
    g["county"] = g["county"].map(norm_county)
    cd = precinct_cd(g)
    p = g[g["office"] == "President"].copy()
    p["side"] = "oth"
    p.loc[p["candidate"].str.contains("harris", case=False), "side"] = "dem"
    p.loc[p["candidate"].str.contains("trump", case=False), "side"] = "rep"
    p = p.drop(columns="district").join(cd, on=["county", "precinct"])
    missing = p["district"].isna()
    if missing.any():
        # Pseudo-precincts without House rows (e.g. countywide pools): assign to the county's
        # dominant district.
        dom = p.dropna(subset=["district"]).groupby(["county", "district"])["votes"].sum().reset_index()
        dom = dom.sort_values("votes").drop_duplicates("county", keep="last").set_index("county")["district"]
        p.loc[missing, "district"] = p.loc[missing, "county"].map(dom)
    p["district"] = p["district"].astype(int)
    return p.pivot_table(index=["county", "district"], columns="side", values="votes", aggfunc="sum", fill_value=0)


def county_primary(path, offices):
    c = pd.read_csv(path, dtype=str)
    c["votes"] = pd.to_numeric(c["votes"].str.replace(",", ""), errors="coerce").fillna(0)
    c = c[c["office"].isin(offices) & ~c["candidate"].fillna("").str.contains(NON_CANDIDATE)]
    c["county"] = c["county"].map(norm_county)
    c["side"] = c["party"].str.upper().map({"DEM": "dem", "REP": "rep"})
    return c.dropna(subset=["side"]).pivot_table(index="county", columns="side", values="votes", aggfunc="sum")


def precinct_weights(pattern, offices):
    """Per (county, district) share of each party's top-race votes, from precinct files."""
    files = sorted(glob.glob(str(ROOT / pattern)))
    if not files:
        return None
    df = load(files)
    df["county"] = df["county"].map(norm_county)
    cd = precinct_cd(df)
    t = df[df["office"].isin(offices)].copy()
    t["side"] = t["party"].fillna("").str.upper().map({"DEM": "dem", "D": "dem", "REP": "rep", "R": "rep"})
    t = t.dropna(subset=["side"]).drop(columns="district").join(cd, on=["county", "precinct"]).dropna(subset=["district"])
    t["district"] = t["district"].astype(int)
    v = t.pivot_table(index=["county", "district"], columns="side", values="votes", aggfunc="sum", fill_value=0)
    return v / v.groupby(level="county").transform("sum")


def house_weights(path):
    """Per (county, district) share of each party's U.S. House primary votes, from a county file."""
    c = pd.read_csv(path, dtype=str)
    c["votes"] = pd.to_numeric(c["votes"].str.replace(",", ""), errors="coerce").fillna(0)
    c = c[(c["office"] == "U.S. House") & c["party"].isin(["DEM", "REP"])
          & ~c["candidate"].fillna("").str.contains(NON_CANDIDATE)]
    c["county"] = c["county"].map(norm_county)
    c["district"] = pd.to_numeric(c["district"]).astype(int)
    c["side"] = c["party"].str.lower()
    v = c.pivot_table(index=["county", "district"], columns="side", values="votes", aggfunc="sum", fill_value=0)
    return v / v.groupby(level="county").transform("sum")


def allocate(county_tot, gen_w, prec_w, house_w, tag):
    rows = []
    for county, tot in county_tot.iterrows():
        gw = gen_w.loc[county]
        if len(gw) == 1:
            w, method = gw, "whole_county"
        elif prec_w is not None and county in prec_w.index.get_level_values("county"):
            w, method = prec_w.loc[county], "precinct"
        else:
            w, method = house_w.loc[county], "house_weight"
        for d in w.index:
            rows.append({"county": county, "district": d, f"{tag}_dem": tot["dem"] * w.loc[d, "dem"],
                         f"{tag}_rep": tot["rep"] * w.loc[d, "rep"], f"{tag}_method": method})
    return pd.DataFrame(rows).set_index(["county", "district"])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    gen = general24()
    gen_w = gen[["dem", "rep"]] / gen[["dem", "rep"]].groupby(level="county").transform("sum")

    p24 = county_primary(ROOT / "2024/20240806__mi__primary__county.csv", SEN)
    p26 = county_primary(ROOT / "2026/20260804__mi__primary__county.csv", ["Governor"])
    w24 = precinct_weights("2024/counties/20240806__mi__primary__*precinct.csv", SEN)
    w26 = precinct_weights("2026/counties/20260804__mi__primary__*precinct.csv", ["Governor"])
    h24 = house_weights(ROOT / "2024/20240806__mi__primary__county.csv")
    h26 = house_weights(ROOT / "2026/20260804__mi__primary__county.csv")

    # Validate the House-vote weights against precinct weights in split counties.
    split = [c for c in gen_w.index.get_level_values("county").unique() if len(gen_w.loc[c]) > 1]
    checks = []
    for tag, pw, hw, tot in (("2024", w24, h24, p24), ("2026", w26, h26, p26)):
        for c in split:
            if c in pw.index.get_level_values("county"):
                for d in gen_w.loc[c].index:
                    for side in ("dem", "rep"):
                        est = tot.loc[c, side] * hw.loc[(c, d), side] if (c, d) in hw.index else 0
                        ex = tot.loc[c, side] * pw.loc[(c, d), side] if (c, d) in pw.index else 0
                        checks.append({"year": tag, "county": c, "district": d, "side": side,
                                       "exact": ex, "estimate": est})
    chk = pd.DataFrame(checks)

    a24 = allocate(p24, gen_w, w24, h24, "p24sen")
    a26 = allocate(p26, gen_w, w26, h26, "p26gov")
    cd = gen.join(a24).join(a26)
    by_cd = cd.drop(columns=[c for c in cd.columns if c.endswith("_method")]).groupby(level="district").sum()
    by_cd = by_cd.rename(columns={"dem": "pres24_dem", "rep": "pres24_rep", "oth": "pres24_oth"})
    est_share = cd.assign(est24=cd["p24sen_method"].eq("house_weight") * (cd["p24sen_dem"] + cd["p24sen_rep"]),
                          est26=cd["p26gov_method"].eq("house_weight") * (cd["p26gov_dem"] + cd["p26gov_rep"]))
    by_cd["pct_estimated_2024"] = 100 * est_share.groupby(level="district")["est24"].sum() / (by_cd.p24sen_dem + by_cd.p24sen_rep)
    by_cd["pct_estimated_2026"] = 100 * est_share.groupby(level="district")["est26"].sum() / (by_cd.p26gov_dem + by_cd.p26gov_rep)
    # Counties in each district, largest 2024 presidential vote first.
    sized = cd.assign(size=cd[["dem", "rep", "oth"]].sum(axis=1)).reset_index().sort_values("size", ascending=False)
    by_cd["counties"] = sized.groupby("district")["county"].apply(lambda s: ", ".join(s))
    by_cd.round(1).to_csv(OUT / "mi_cd_primary_2024_2026.csv")
    chk.to_csv(OUT / "mi_cd_allocation_check.csv", index=False)
    return by_cd, chk


if __name__ == "__main__":
    by_cd, chk = main()
    pd.set_option("display.width", 200)
    print(by_cd.drop(columns="counties").round(0).to_string())
    print("totals", by_cd[["p24sen_dem", "p24sen_rep", "p26gov_dem", "p26gov_rep", "pres24_dem", "pres24_rep"]].sum().round(0).to_dict())
    chk["err"] = chk.estimate - chk.exact
    print("allocation check: rows", len(chk), "mean abs error (votes)", round(chk.err.abs().mean()),
          "| max", round(chk.err.abs().max()), "| total exact", round(chk.exact.sum()))
    print(chk.assign(pct=100 * chk.err / chk.exact.where(chk.exact > 0)).sort_values("err", key=abs).tail(6).round(1).to_string())
