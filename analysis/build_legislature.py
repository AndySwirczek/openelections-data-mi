"""Democratic and Republican nominees for every Michigan State House and State Senate seat, 2026.

Nominee = top vote-getter in the August 4, 2026 primary, summed across every county in the
district (from the OpenElections 2026 county-level primary file). Third-party nominees are chosen
at conventions and aren't in primary data, so they aren't listed.

Usage: .venv/bin/python analysis/build_legislature.py
"""
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "output"
SRC = ROOT / "2026/20260804__mi__primary__county.csv"
SEATS = {"State House": 110, "State Senate": 38}
NON_CANDIDATE = re.compile(r"total|ballots|over ?vote|under ?vote|blank|registered", re.I)


def tidy(n):
    """Title-case names that only ever appear in all capitals (e.g. "NANCY JENKINS-ARNO")."""
    if n != n.upper():
        return n
    t = n.title()
    t = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), t)
    return re.sub(r"\b(Ii|Iii|Iv|Jr|Sr)\b", lambda m: {"Ii": "II", "Iii": "III", "Iv": "IV"}.get(m.group(1), m.group(1)), t)


def name_key(n):
    return re.sub(r"\s+", " ", re.sub(r"[.,]", " ", n.lower())).strip()


def main():
    c = pd.read_csv(SRC, dtype=str)
    c = c[c["office"].isin(SEATS) & c["party"].isin(["DEM", "REP"])].copy()
    c = c[~c["candidate"].fillna("").str.contains(NON_CANDIDATE)]
    c["votes"] = pd.to_numeric(c["votes"].str.replace(",", ""), errors="coerce").fillna(0).astype(int)
    c["district"] = pd.to_numeric(c["district"]).astype(int)
    c["write_in"] = c["candidate"].str.fullmatch(r"(?i)write[- ]?ins?|wi")
    c["key"] = c["candidate"].map(name_key)
    # Use the most common spelling of each candidate's name across counties.
    # Use the most common spelling of each candidate's name across counties, preferring mixed case.
    spelled = c.groupby(["office", "district", "party", "key"])["candidate"].agg(
        lambda s: tidy(sorted(s.value_counts().index, key=lambda n: n == n.upper())[0]))

    rows, long = [], []
    for (office, dist), d in c.groupby(["office", "district"]):
        row = {"chamber": office.replace("State ", ""), "district": dist,
               "counties": ", ".join(sorted(d["county"].unique()))}
        for party, label in (("DEM", "dem"), ("REP", "rep")):
            p = d[(d["party"] == party)]
            named = p[~p["write_in"]].groupby("key")["votes"].sum().sort_values(ascending=False)
            wi = int(p.loc[p["write_in"], "votes"].sum())
            total = int(named.sum()) + wi
            cands = [(spelled[(office, dist, party, k)], v) for k, v in named.items()]
            for rank, (n, v) in enumerate(cands, 1):
                long.append({"chamber": row["chamber"], "district": dist, "party": party, "candidate": n,
                             "votes": v, "pct": round(100 * v / total, 1) if total else None, "won_primary": rank == 1})
            row[f"{label}_nominee"] = cands[0][0] if cands else ""
            row[f"{label}_nominee_votes"] = cands[0][1] if cands else None
            row[f"{label}_nominee_pct"] = round(100 * cands[0][1] / total, 1) if cands and total else None
            row[f"{label}_candidates_on_ballot"] = len(cands)
            row[f"{label}_contested"] = len(cands) > 1
            row[f"{label}_other_candidates"] = "; ".join(f"{n} ({v:,})" for n, v in cands[1:])
            row[f"{label}_primary_votes_total"] = total
        notes = []
        for label, pname in (("dem", "Democratic"), ("rep", "Republican")):
            if not row[f"{label}_nominee"]:
                wi = row[f"{label}_primary_votes_total"]
                notes.append(f"no {pname} candidate on the primary ballot ({wi:,} {pname} write-in votes; "
                             "check the official canvass for a write-in nominee)")
        for label in ("dem", "rep"):
            if row[f"{label}_contested"]:
                runner = int(row[f"{label}_other_candidates"].split("(")[1].split(")")[0].replace(",", ""))
                margin = row[f"{label}_nominee_votes"] - runner
                if margin < 0.02 * row[f"{label}_primary_votes_total"]:
                    notes.append(f"close {label.upper()} primary: won by {margin:,} votes")
        row["notes"] = "; ".join(notes)
        rows.append(row)

    wide = pd.DataFrame(rows)
    wide["chamber"] = pd.Categorical(wide["chamber"], ["Senate", "House"], ordered=True)
    wide = wide.sort_values(["chamber", "district"])
    cols = ["chamber", "district", "dem_nominee", "rep_nominee", "dem_nominee_pct", "rep_nominee_pct",
            "dem_contested", "rep_contested", "dem_other_candidates", "rep_other_candidates",
            "dem_nominee_votes", "rep_nominee_votes", "dem_primary_votes_total", "rep_primary_votes_total",
            "dem_candidates_on_ballot", "rep_candidates_on_ballot", "counties", "notes"]
    wide[cols].to_csv(OUT / "mi_legislature_2026_nominees.csv", index=False)
    long = pd.DataFrame(long)
    long["chamber"] = pd.Categorical(long["chamber"], ["Senate", "House"], ordered=True)
    long = long.sort_values(["chamber", "district", "party", "votes"], ascending=[True, True, True, False])
    long.to_csv(OUT / "mi_legislature_2026_primary_candidates.csv", index=False)
    return wide[cols], long, c


if __name__ == "__main__":
    w, l, c = main()
    for ch in ("Senate", "House"):
        x = w[w.chamber == ch]
        print(ch, len(x), "seats | no DEM:", (x.dem_nominee == "").sum(), "| no REP:", (x.rep_nominee == "").sum(),
              "| contested DEM:", x.dem_contested.sum(), "| contested REP:", x.rep_contested.sum())
    print(w[w.notes != ""][["chamber", "district", "dem_nominee", "rep_nominee", "notes"]].to_string())
