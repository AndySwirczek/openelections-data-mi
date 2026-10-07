"""Build a Michigan precinct swing/turnout baseline from OpenElections data.

General: 2022 Governor (Whitmer v Dixon) and 2024 President (Harris v Trump)
margins per precinct, with empty 2026 Governor columns to fill in after Nov 3.
Primary: party turnout in the top-of-ticket race, 2024 Aug (U.S. Senate, the
only statewide contest on both ballots) vs 2026 Aug (Governor; Senate also given).

Outputs both a precinct-level table (joined on a normalized precinct name) and a
municipality-level rollup, which survives the 2025 precinct consolidations and
absentee-counting-board reporting.

Usage: .venv/bin/python analysis/build_swing.py
"""
import glob
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "output"

# Rows that are summaries, not votes for a candidate.
NON_CANDIDATE = re.compile(
    r"total|ballots cast|over ?votes?|under ?votes?|blank|registered|rejected|"
    r"unresolved|not assigned|times cast|^\s*$",
    re.I,
)


def load(paths):
    frames = [pd.read_csv(p, dtype=str) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    df["votes"] = pd.to_numeric(df["votes"].str.replace(",", ""), errors="coerce").fillna(0)
    df["candidate"] = df["candidate"].fillna("")
    df["precinct"] = df["precinct"].fillna("").str.strip()
    return df[~df["candidate"].str.contains(NON_CANDIDATE)]


def norm_county(name):
    s = re.sub(r"\d+", "", name).strip()
    s = re.sub(r"'s$", "", s)
    s = re.sub(r"^(saint|st\.?)\s+", "St. ", s, flags=re.I)
    return s.title().replace("St. ", "St. ")


def norm_precinct(name):
    s = name.lower()
    s = re.sub(r"\bfhp\b", "", s)
    # Absentee counting boards stay distinct so they never merge into a real precinct.
    s = re.sub(r"\b(absent voter|absentee|av) counting board\b", "avcb", s)
    s = re.sub(r"[.,#:\-()]", " ", s)
    s = re.sub(r"\bcharter\b", "", s)
    s = re.sub(r"\b(twp|twsp)\b", "township", s)
    s = re.sub(r"\b(pct|prec)\b", "precinct", s)
    s = re.sub(r"\b0+(\d)", r"\1", s)  # Precinct 01 -> 1
    s = re.sub(r"\s+", " ", s).strip()
    # Bare trailing number ("Armada Township 01") is a precinct number.
    if not re.search(r"\b(precinct|ward|avcb)\b", s):
        s = re.sub(r"\s(\d+)( [a-z])?$", r" precinct \1", s)  # "Danby 1 A" too
    # "city of X" / "township of X" -> "X city" / "X township"
    m = re.match(r"(city|township|village) of ([a-z' ]+?)(?=\s+(?:ward|precinct|avcb)\b|$)(.*)", s)
    if m:
        kind, place, rest = m.groups()
        s = f"{place}{'' if place.endswith(' ' + kind) else ' ' + kind}{rest}"
    # Drop split-precinct codes after the number ("1a"/"1b", "2NW", "- 3FE"); the
    # pieces sum into one precinct.
    s = re.sub(r"\b(precinct \d+)[a-z]*\b.*", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if re.search(r"\bprecinct$", s):  # "Assyria Township, Precinct" -> precinct 1
        s += " 1"
    if not re.search(r"\b(precinct|ward|avcb)\b", s):
        s += " precinct 1"
    return s


def municipality(key):
    return re.split(r"\s(?:ward|precinct|avcb)\b", key)[0].strip()


TYPE_WORDS = re.compile(r"\b(township|city|village)\b")


def base_name(muni):
    return re.sub(r"\s+", " ", TYPE_WORDS.sub("", muni)).strip()


def precinct_no(pkey):
    m = re.search(r"\bprecinct (\d+)$", pkey)
    return m.group(1) if m else None


def align_to(src, spine):
    """Rewrite src keys to match the 2024 general (the spine), in order:
    1. muni name ignoring township/city/village, when that's unique in the county;
    2. precinct number ignoring ward, when unique within the muni;
    3. a muni with exactly one precinct on both sides maps precinct-to-precinct."""
    spine_keys = set(spine.index)
    spine_munis = set(spine.index.droplevel("pkey"))
    by_base, by_no, by_muni = {}, {}, {}
    for c, m, k in spine_keys:
        by_base.setdefault((c, base_name(m)), set()).add(m)
        by_no.setdefault((c, m, precinct_no(k)), set()).add(k)
        by_muni.setdefault((c, m), set()).add(k)
    df = src.reset_index()
    for i, (c, m) in enumerate(zip(df["county"], df["muni"])):
        if (c, m) in spine_munis:
            continue
        hits = by_base.get((c, base_name(m)), set())
        if len(hits) == 1:
            new = next(iter(hits))
            df.at[i, "pkey"] = new + df.at[i, "pkey"][len(m):]
            df.at[i, "muni"] = new
    src_muni_size = df.groupby(["county", "muni"])["pkey"].nunique()
    for i, (c, m, k) in enumerate(zip(df["county"], df["muni"], df["pkey"])):
        if (c, m, k) in spine_keys or "avcb" in k:
            continue
        hits = by_no.get((c, m, precinct_no(k)), set())
        if len(hits) == 1:
            df.at[i, "pkey"] = next(iter(hits))
        elif src_muni_size.get((c, m)) == 1 and len(by_muni.get((c, m), ())) == 1:
            df.at[i, "pkey"] = next(iter(by_muni[(c, m)]))
    agg = {c: ("first" if c.endswith("_precinct_name") else "sum") for c in df.columns
           if c not in ("county", "muni", "pkey")}
    return df.groupby(["county", "muni", "pkey"]).agg(agg)


def keyed(df):
    df = df.copy()
    df["county"] = df["county"].fillna("").map(norm_county)
    df["pkey"] = df["precinct"].map(norm_precinct)
    df["muni"] = df["pkey"].map(municipality)
    return df


def general(path, office, dem_pat, rep_pat, tag):
    df = load([path])
    df = keyed(df[df["office"] == office])
    # Party column is unreliable in 2024 (blank, "Democrat", "DEM", ...), so classify
    # the two majors by name and everyone else as "oth".
    df["side"] = "oth"
    df.loc[df["candidate"].str.contains(dem_pat, case=False), "side"] = "dem"
    df.loc[df["candidate"].str.contains(rep_pat, case=False), "side"] = "rep"
    return pivot(df, tag, ["dem", "rep", "oth"])


def primary(paths, offices, tag):
    df = load(paths)
    df = keyed(df[df["office"].isin(offices)])
    p = df["party"].fillna("").str.upper()
    df["side"] = p.map({"DEM": "dem", "D": "dem", "REP": "rep", "R": "rep"})
    df = df.dropna(subset=["side"])
    return pivot(df, tag, ["dem", "rep"])


def pivot(df, tag, sides):
    t = df.pivot_table(index=["county", "muni", "pkey"], columns="side", values="votes",
                       aggfunc="sum", fill_value=0)
    t = t.reindex(columns=sides, fill_value=0)
    t.columns = [f"{tag}_{c}" for c in t.columns]
    # Keep one readable precinct label per key.
    label = df.groupby(["county", "muni", "pkey"])["precinct"].first().rename(f"{tag}_precinct_name")
    return t.join(label)


def add_metrics(t):
    for tag in ("g22", "g24"):
        tot = t[[f"{tag}_dem", f"{tag}_rep", f"{tag}_oth"]].sum(axis=1, min_count=1)
        t[f"{tag}_total"] = tot
        t[f"{tag}_margin_votes"] = t[f"{tag}_dem"] - t[f"{tag}_rep"]
        t[f"{tag}_margin_pct"] = (100 * t[f"{tag}_margin_votes"] / tot.where(tot > 0)).round(2)
    t["swing_22_to_24_pts"] = (t["g24_margin_pct"] - t["g22_margin_pct"]).round(2)
    t["g24_winner"] = t["g24_margin_votes"].map(
        lambda m: "" if pd.isna(m) else ("DEM" if m > 0 else ("REP" if m < 0 else "TIE")))
    for tag in ("p24sen", "p26gov", "p26sen"):
        tot = t[f"{tag}_dem"] + t[f"{tag}_rep"]
        t[f"{tag}_total"] = tot
        t[f"{tag}_dem_share_pct"] = (100 * t[f"{tag}_dem"] / tot.where(tot > 0)).round(2)
    for side in ("dem", "rep", "total"):
        a, b = t[f"p24sen_{side}"], t[f"p26gov_{side}"]
        t[f"primary_{side}_change"] = b - a
        t[f"primary_{side}_change_pct"] = (100 * (b - a) / a.where(a > 0)).round(1)
    t["primary_dem_share_shift_pts"] = (t["p26gov_dem_share_pct"] - t["p24sen_dem_share_pct"]).round(2)
    # Placeholders for the Nov 3, 2026 general (Governor).
    for c in ("g26_dem", "g26_rep", "g26_oth", "g26_total", "g26_margin_votes", "g26_margin_pct",
              "swing_24_to_26_pts"):
        t[c] = pd.NA
    return t


COLS = [
    "county", "muni", "precinct",
    "g22_dem", "g22_rep", "g22_oth", "g22_total", "g22_margin_votes", "g22_margin_pct",
    "g24_dem", "g24_rep", "g24_oth", "g24_total", "g24_margin_votes", "g24_margin_pct", "g24_winner",
    "swing_22_to_24_pts",
    "p24sen_dem", "p24sen_rep", "p24sen_total", "p24sen_dem_share_pct",
    "p26gov_dem", "p26gov_rep", "p26gov_total", "p26gov_dem_share_pct",
    "primary_dem_change", "primary_dem_change_pct", "primary_rep_change", "primary_rep_change_pct",
    "primary_total_change", "primary_total_change_pct", "primary_dem_share_shift_pts",
    "p26sen_dem", "p26sen_rep", "p26sen_total", "p26sen_dem_share_pct",
    "g26_dem", "g26_rep", "g26_oth", "g26_total", "g26_margin_votes", "g26_margin_pct", "swing_24_to_26_pts",
    "in_2024_general", "in_2022_general", "in_2024_primary", "in_2026_primary",
]


def flags(t):
    t["in_2024_general"] = t["g24_dem"].notna()
    t["in_2022_general"] = t["g22_dem"].notna()
    t["in_2024_primary"] = t["p24sen_dem"].notna()
    t["in_2026_primary"] = t["p26gov_dem"].notna()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    g22 = general(ROOT / "2022/20221108__mi__general__precinct.csv", "Governor", r"whitmer", r"dixon", "g22")
    g24 = general(ROOT / "2024/20241105__mi__general__precinct.csv", "President", r"harris", r"trump", "g24")
    sen = ["U.S. Senate", "United States Senator", "United States Senator for State"]
    p24_files = sorted(glob.glob(str(ROOT / "2024/counties/20240806__mi__primary__*precinct.csv")))
    p26_files = sorted(glob.glob(str(ROOT / "2026/counties/20260804__mi__primary__*precinct.csv")))
    p24 = primary(p24_files, sen, "p24sen")
    p26g = primary(p26_files, ["Governor"], "p26gov")
    p26s = primary(p26_files, sen, "p26sen")

    g22, p24, p26g, p26s = (align_to(x, g24) for x in (g22, p24, p26g, p26s))

    p24_counties = set(p24.index.get_level_values("county"))
    p26_counties = set(p26g.index.get_level_values("county"))

    # Outer join so nothing is dropped: rows that don't line up with a 2024 general
    # precinct (old 2022 precincts, consolidated/renamed 2026 precincts, early-voting
    # pools, source rows with garbled names) are kept and flagged.
    parts = [g24, g22, p24, p26g, p26s]
    t = pd.concat(parts, axis=1, join="outer")
    names = [c for c in t.columns if c.endswith("_precinct_name")]
    label = t[names[0]]
    for c in names[1:]:
        label = label.combine_first(t[c])
    t = t.drop(columns=names)
    flags(t)
    t = t.reset_index()
    t["precinct"] = label.values
    precinct = add_metrics(t)

    # Municipality rollup: robust to precinct renumbering/consolidation and to
    # absentee/early-vote pools reported separately from precincts.
    sums = [p.drop(columns=[c for c in p.columns if c.endswith("_precinct_name")])
              .groupby(level=["county", "muni"]).sum() for p in parts]
    m = pd.concat(sums, axis=1, join="outer")
    flags(m)
    m = m.reset_index()
    m["precinct"] = ""
    muni = add_metrics(m)

    precinct = precinct[COLS].sort_values(["county", "muni", "precinct"])
    muni = muni[[c for c in COLS if c != "precinct"]].sort_values(["county", "muni"])
    precinct.to_csv(OUT / "mi_precinct_swing_baseline.csv", index=False)
    muni.to_csv(OUT / "mi_municipality_swing_baseline.csv", index=False)

    # Coverage report: how many 2024 general precincts line up with each other source.
    rows = []
    for c in sorted(precinct["county"].unique()):
        sub = precinct[(precinct["county"] == c) & precinct["in_2024_general"]]
        rows.append({
            "county": c,
            "precincts_2024_general": len(sub),
            "matched_2022_general": int(sub["in_2022_general"].sum()),
            "has_2024_primary_file": c in p24_counties,
            "matched_2024_primary": int(sub["in_2024_primary"].sum()),
            "has_2026_primary_file": c in p26_counties,
            "matched_2026_primary": int(sub["in_2026_primary"].sum()),
        })
    cov = pd.DataFrame(rows)
    cov.to_csv(OUT / "coverage_by_county.csv", index=False)

    with pd.ExcelWriter(OUT / "mi_swing_baseline.xlsx") as xw:
        precinct.to_excel(xw, sheet_name="Precinct", index=False)
        muni.to_excel(xw, sheet_name="Municipality", index=False)
        cov.to_excel(xw, sheet_name="Coverage", index=False)

    keys = ["g22_dem", "g22_rep", "g24_dem", "g24_rep", "p24sen_dem", "p24sen_rep", "p26gov_dem", "p26gov_rep"]
    print("Totals (all rows):", precinct[keys].sum().astype(int).to_dict())
    sp = precinct[precinct["in_2024_general"]]
    print("Precinct rows:", len(precinct), "| on a 2024 general precinct:", len(sp))
    print("Share of each source's votes landing on a 2024 general precinct:")
    for k in keys:
        print(f"  {k}: {100 * sp[k].sum() / precinct[k].sum():.1f}%")


if __name__ == "__main__":
    main()
