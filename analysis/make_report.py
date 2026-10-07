"""Render the primary-participation story (analysis/output/mi_primary_story.csv) as a short PDF.

Needs reportlab + matplotlib + pandas (reportlab isn't a project dependency; run with any env that
has it). Usage: python analysis/make_report.py [figure_dir]
"""
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
FIG = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
FIG.mkdir(parents=True, exist_ok=True)

DEM, REP, INK, INK2, INK3, RULE = "#2a78d6", "#e34948", "#14181f", "#4a5160", "#7a8191", "#dfe2e8"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": RULE, "axes.labelcolor": INK2,
    "xtick.color": INK3, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#eceef2", "grid.linewidth": 0.8, "axes.axisbelow": True,
})

df = pd.read_csv(OUT / "mi_primary_story.csv")
st = df[df.level == "state"].iloc[0]
reg = df[df.level == "region"].set_index("name")
cty = df[df.level == "county"].set_index("name")
cd = df[df.level == "district"].copy()
cd["num"] = cd.name.str.extract(r"(\d+)").astype(int)


def f0(x): return f"{x:,.0f}"
def sg(x, d=1): return ("+" if x > 0 else "−" if x < 0 else "") + f"{abs(x):.{d}f}"


# ---------- Figures ----------
def fig_participation(path):
    rows = [("Michigan", st)] + [(n, reg.loc[n]) for n in reg.index]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.5), sharey=True)
    for ax, side, col, title in ((axes[0], "dem", DEM, "Democratic primary"), (axes[1], "rep", REP, "Republican primary")):
        y = range(len(rows))
        a = [r[f"{side}_primary_per100_2018"] for _, r in rows]
        b = [r[f"{side}_primary_per100_2026"] for _, r in rows]
        ax.barh([i - 0.2 for i in y], a, height=0.36, color=col, alpha=0.4, label="2018")
        ax.barh([i + 0.2 for i in y], b, height=0.36, color=col, label="2026")
        for i, (u, v) in enumerate(zip(a, b)):
            ax.text(u + 0.4, i - 0.2, f"{u:.1f}", va="center", fontsize=7.5, color=INK2)
            ax.text(v + 0.4, i + 0.2, f"{v:.1f}", va="center", fontsize=7.5, color=INK)
        ax.set_yticks(list(y), [n for n, _ in rows])
        ax.set_xlim(0, 36); ax.set_title(title, loc="left", fontsize=9.5, color=INK, fontweight="bold")
        ax.grid(axis="y", visible=False)
        ax.legend(frameon=False, fontsize=8, loc="lower right")
    axes[0].invert_yaxis()
    axes[0].set_xlabel("Primary votes per 100 presidential voters"); axes[1].set_xlabel("Primary votes per 100 presidential voters")
    fig.tight_layout(); fig.savefig(path, dpi=220); plt.close(fig)


def fig_cycles(path):
    v = cty.dropna(subset=["shift_pts_2016_2018", "shift_pts_2024_2026"])
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    size = 8 + 260 * (v.pres_total_2024 / v.pres_total_2024.max())
    ax.scatter(v.shift_pts_2016_2018, v.shift_pts_2024_2026, s=size, color=INK2, alpha=0.35, edgecolor="white", linewidth=0.6)
    lo, hi = -2, 34
    ax.plot([lo, hi], [lo, hi], color=INK3, lw=1, ls="--")
    ax.text(33, 29.5, "same shift\nboth cycles", fontsize=7.5, color=INK3, ha="right", va="top")
    offsets = {"Kent": (-6, -11), "St. Clair": (5, 3), "Huron": (-30, 4), "Macomb": (6, -3)}
    for n in ["Ottawa", "Kent", "Macomb", "Oakland", "Wayne", "Washtenaw", "Barry", "Huron", "St. Clair", "Allegan", "Genesee"]:
        if n in v.index:
            ax.annotate(n, (v.at[n, "shift_pts_2016_2018"], v.at[n, "shift_pts_2024_2026"]), xytext=offsets.get(n, (4, 3)),
                        textcoords="offset points", fontsize=7.5, color=INK)
    ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
    ax.set_xlabel("Shift toward Democrats in primary vote share, 2016 → 2018 (points)")
    ax.set_ylabel("Shift, 2024 → 2026 (points)")
    fig.tight_layout(); fig.savefig(path, dpi=220); plt.close(fig)


def fig_districts(path):
    d = cd.sort_values("harris_margin_pct_2024")
    fig, ax = plt.subplots(figsize=(7.2, 3.9))
    for i, (_, r) in enumerate(d.iterrows()):
        a, b, h = r.dem_share_primary_2024, r.dem_share_primary_2026, r.harris_two_party_pct_2024
        col = DEM if b >= a else REP
        ax.plot([a, b], [i, i], color=col, lw=2.2, alpha=0.6, solid_capstyle="round")
        ax.scatter([a], [i], s=34, facecolor="white", edgecolor=INK3, linewidth=1.5, zorder=3)
        ax.scatter([b], [i], s=40, color=col, edgecolor="white", linewidth=1, zorder=4)
        ax.plot([h, h], [i - 0.32, i + 0.32], color=INK, lw=1.6, zorder=5)
        ax.text(101, i, f"{sg(b - a)} pts", va="center", fontsize=7.5, color=INK2)
    ax.set_yticks(range(len(d)), [f"District {n}  ({'D' if m > 0 else 'R'}+{abs(m):.0f})" for n, m in zip(d.num, d.harris_margin_pct_2024)])
    ax.set_xlim(0, 100); ax.axvline(50, color=RULE, lw=1.2, zorder=0)
    ax.set_xlabel("Democratic share of primary voters (%)   ○ 2024   ● 2026   | Harris two-party share, Nov 2024")
    ax.grid(axis="y", visible=False)
    fig.tight_layout(); fig.savefig(path, dpi=220); plt.close(fig)


def fig_calibration(path):
    v = cty.dropna(subset=["dem_share_primary_2018", "whitmer_two_party_pct_2018"])
    fig, ax = plt.subplots(figsize=(7.2, 3.3))
    size = 8 + 220 * (v.pres_total_2016 / v.pres_total_2016.max())
    ax.scatter(v.dem_share_primary_2018, v.whitmer_two_party_pct_2018, s=size, color=DEM, alpha=0.35, edgecolor="white", linewidth=0.6)
    ax.plot([10, 90], [10, 90], color=INK3, lw=1, ls="--")
    ax.scatter([st.dem_share_primary_2018], [st.whitmer_two_party_pct_2018], s=70, color=INK, zorder=4)
    ax.annotate(f"Statewide 2018: {st.dem_share_primary_2018:.1f}% of primary voters → {st.whitmer_two_party_pct_2018:.1f}% for Whitmer",
                (st.dem_share_primary_2018, st.whitmer_two_party_pct_2018), xytext=(-8, 12), textcoords="offset points",
                fontsize=7.5, color=INK, ha="right")
    ax.axvline(st.dem_share_primary_2026, color=DEM, lw=1.2, ls=":")
    ax.text(st.dem_share_primary_2026 + 0.8, 14, f"2026 statewide primary:\n{st.dem_share_primary_2026:.1f}% Democratic", fontsize=7.5, color=DEM)
    ax.set_xlim(10, 90); ax.set_ylim(10, 90)
    ax.set_xlabel("Democratic share of 2018 primary voters (%), by county")
    ax.set_ylabel("Whitmer two-party share, Nov 2018 (%)")
    fig.tight_layout(); fig.savefig(path, dpi=220); plt.close(fig)


figs = {k: FIG / f"{k}.png" for k in ("participation", "cycles", "districts", "calibration")}
fig_participation(figs["participation"]); fig_cycles(figs["cycles"])
fig_districts(figs["districts"]); fig_calibration(figs["calibration"])

# ---------- Document ----------
W = letter[0] - 1.2 * inch
H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=21, leading=25, textColor=colors.HexColor(INK), spaceAfter=6)
SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=10.5, leading=14, textColor=colors.HexColor(INK2), spaceAfter=10)
H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=colors.HexColor(INK), spaceBefore=4, spaceAfter=5)
EYE = ParagraphStyle("eye", fontName="Helvetica", fontSize=7.5, leading=10, textColor=colors.HexColor(INK3), spaceAfter=2)
BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=colors.HexColor(INK), spaceAfter=6, alignment=TA_LEFT)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=11, textColor=colors.HexColor(INK2))
BUL = ParagraphStyle("bul", parent=BODY, leftIndent=12, bulletIndent=0, spaceAfter=4)
CAP = ParagraphStyle("cap", parent=SMALL, fontSize=7.5, leading=10, spaceBefore=2, spaceAfter=10)
CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10)
CELLB = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")


def eyebrow(t): return Paragraph(t.upper(), EYE)
def img(path, w=W): im = Image(str(path)); im.drawWidth, im.drawHeight = w, w * im.imageHeight / im.imageWidth; return im
def dcol(t): return f'<font color="{DEM}">{t}</font>'
def rcol(t): return f'<font color="{REP}">{t}</font>'


def table(data, widths, bold_last=False, align_from=1):
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(INK2)),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8), ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(INK3)),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor(RULE)), ("ALIGN", (align_from, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if bold_last:
        style += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor(INK3))]
    t.setStyle(TableStyle(style))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7); canvas.setFillColor(colors.HexColor(INK3))
    canvas.drawString(0.6 * inch, 0.45 * inch, "Michigan primary participation, 2016–2026 · Source: OpenElections Michigan data and county clerk reports")
    canvas.drawRightString(letter[0] - 0.6 * inch, 0.45 * inch, f"{doc.page}")
    canvas.restoreState()


per18, per26 = st.all_primary_per100_2018, st.all_primary_per100_2026
V60 = cty.dropna(subset=["shift_pts_2016_2018", "shift_pts_2024_2026"])
def share(d, r): return 100 * d / (d + r)
SH16_18 = share(V60.dem_primary_2018.sum(), V60.rep_primary_2018.sum()) - share(V60.dem_primary_2016.sum(), V60.rep_primary_2016.sum())
SH24_26 = share(V60.dem_primary_2026.sum(), V60.rep_primary_2026.sum()) - share(V60.dem_primary_2024.sum(), V60.rep_primary_2024.sum())
N60 = len(V60)
dper = (st.dem_primary_per100_2026 / st.dem_primary_per100_2018 - 1) * 100
rper = (st.rep_primary_per100_2026 / st.rep_primary_per100_2018 - 1) * 100
tot18, tot26 = st.dem_primary_2018 + st.rep_primary_2018, st.dem_primary_2026 + st.rep_primary_2026
cdv = cd.set_index("num")
s = []

# Page 1: summary
s += [eyebrow("Michigan · August primaries · counties 2016–2026, congressional districts 2024–2026"),
      Paragraph("Michigan's 2026 primary: the same size as 2018, with a different mix", H1),
      Paragraph(f"The August 4, 2026 governor primary drew {f0(tot26)} votes, {(tot26 / tot18 - 1) * 100:.0f}% more than 2018. "
                "The electorate grew by about as much, so overall participation was flat. What changed is who showed up: "
                "Democratic participation is above its 2018 level and Republican participation is well below it.", SUB),
      Spacer(1, 4), Paragraph("Key findings", H2)]
bul = [
    f"<b>Participation is flat to slightly lower overall.</b> {per26:.1f} primary votes per 100 presidential voters in 2026, against {per18:.1f} in 2018.",
    f"<b>The mix moved toward Democrats.</b> Democratic participation is {sg(dper, 0)}% versus 2018 ({st.dem_primary_per100_2018:.1f} to "
    f"{st.dem_primary_per100_2026:.1f} per 100); Republican participation is {sg(rper, 0)}% ({st.rep_primary_per100_2018:.1f} to {st.rep_primary_per100_2026:.1f}). "
    f"In raw votes, Republicans fell from {f0(st.rep_primary_2018)} to {f0(st.rep_primary_2026)}.",
    f"<b>The 2024→2026 shift matches Trump's first midterm.</b> On the same {N60} counties, the Democratic share of primary voters rose "
    f"{SH24_26:.1f} points from 2024 to 2026 and {SH16_18:.1f} points from 2016 to 2018.",
    f"<b>Republican-leaning regions moved most, mainly because Republicans stayed home.</b> In the Thumb and Macomb, Democratic participation is about "
    f"where it was in 2018; Republican participation is down about 4 to 5 votes per 100. West Michigan is the one region where Democratic participation also rose clearly.",
    f"<b>Every congressional district moved toward Democrats,</b> from {cdv.shift_pts_2024_2026.min():.1f} points (District {int(cdv.shift_pts_2024_2026.idxmin())}) "
    f"to {cdv.shift_pts_2024_2026.max():.1f} points (District {int(cdv.shift_pts_2024_2026.idxmax())}). The largest shifts are in Districts "
    f"{', '.join(str(i) for i in cdv.shift_pts_2024_2026.sort_values(ascending=False).index[:4])} in western and southern Michigan; the smallest are in "
    "Detroit-area districts whose primaries were already overwhelmingly Democratic.",
    f"<b>Context for November, not a forecast.</b> In 2018 the Democratic share of primary voters ({st.dem_share_primary_2018:.1f}%) landed close to Whitmer's "
    f"two-party share ({st.whitmer_two_party_pct_2018:.1f}%). In 2026 it is {st.dem_share_primary_2026:.1f}%, but the Republican Senate primary was uncontested, "
    "which likely pulled some independents onto the Democratic ballot.",
]
s += [Paragraph(b, BUL, bulletText="•") for b in bul]
s += [Spacer(1, 8), Paragraph("The numbers at a glance", H2)]
glance = [["", "2018", "2026", "Change"],
          ["Governor primary votes, both parties", f0(tot18), f0(tot26), f"{sg((tot26 / tot18 - 1) * 100)}%"],
          [Paragraph(dcol("Democratic primary votes"), CELL), f0(st.dem_primary_2018), f0(st.dem_primary_2026), f"{sg(st.dem_change_pct_2018_2026)}%"],
          [Paragraph(rcol("Republican primary votes"), CELL), f0(st.rep_primary_2018), f0(st.rep_primary_2026), f"{sg(st.rep_change_pct_2018_2026)}%"],
          ["Presidential voters two years earlier", f0(st.pres_total_2016), f0(st.pres_total_2024), f"{sg((st.pres_total_2024 / st.pres_total_2016 - 1) * 100)}%"],
          ["Primary votes per 100 presidential voters", f"{per18:.1f}", f"{per26:.1f}", sg(per26 - per18)],
          ["Democratic share of primary voters", f"{st.dem_share_primary_2018:.1f}%", f"{st.dem_share_primary_2026:.1f}%", f"{sg(st.dem_share_primary_2026 - st.dem_share_primary_2018)} pts"]]
s += [table(glance, [W * 0.46, W * 0.18, W * 0.18, W * 0.18])]
s += [Spacer(1, 6), Paragraph("Both primaries were open-seat governor's races in a Trump midterm. 2018 statewide totals are official; 2026 totals are "
                               "from the OpenElections county file (September 2026 update) and should be confirmed against the Secretary of State.", SMALL),
      PageBreak()]

# Page 2: county story, participation
s += [eyebrow("The county story · long term"), Paragraph("Participation: 2026 against Trump's first midterm", H2),
      Paragraph("Raw vote counts grow with the state, so each primary is scaled by everyone who voted for president two years earlier "
                "(2016 for the 2018 primary, 2024 for 2026). On that basis the two primaries were the same size statewide. The difference is "
                "the party mix, and it is sharpest in the Republican-leaning regions.", BODY),
      img(figs["participation"]),
      Paragraph("Primary votes in the governor's race per 100 people who voted for president two years earlier. West Michigan: Ottawa, Allegan, "
                "Barry, Ionia, Montcalm, Newaygo, Oceana. The Thumb: Huron, Tuscola, Sanilac, Lapeer, St. Clair.", CAP)]
rt = [["Region", "Harris '24", "Dem per 100", "Rep per 100", "Dem share of primary", "Whitmer '18"]]
for n, r in [("Michigan", st)] + [(n, reg.loc[n]) for n in reg.index]:
    rt.append([n, f"{r.harris_two_party_pct_2024:.1f}%", f"{r.dem_primary_per100_2018:.1f} → {r.dem_primary_per100_2026:.1f}",
               f"{r.rep_primary_per100_2018:.1f} → {r.rep_primary_per100_2026:.1f}",
               f"{r.dem_share_primary_2018:.1f}% → {r.dem_share_primary_2026:.1f}%", f"{r.whitmer_two_party_pct_2018:.1f}%"])
s += [table(rt, [W * 0.2, W * 0.13, W * 0.17, W * 0.17, W * 0.2, W * 0.13]),
      Paragraph("Arrows read 2018 → 2026. Harris and Whitmer columns are two-party shares in each region's November election.", CAP)]
# Compare unrounded rates (the CSV's per-100 columns are rounded to one decimal).
r18 = lambda col: cty[col] / cty.pres_total_2016
r26 = lambda col: cty[col] / cty.pres_total_2024
up_t = ((cty.dem_primary_2026 + cty.rep_primary_2026) / cty.pres_total_2024
        > (cty.dem_primary_2018 + cty.rep_primary_2018) / cty.pres_total_2016).sum()
up_d = (r26("dem_primary_2026") > r18("dem_primary_2018")).sum()
up_r = (r26("rep_primary_2026") > r18("rep_primary_2018")).sum()
n18 = cty.dem_primary_2018.notna().sum()
s += [Paragraph(f"Across the {n18} counties with 2018 county results, overall participation was higher than 2018 in {up_t}, Democratic participation "
                f"in {up_d}, and Republican participation in {up_r}.", BODY), PageBreak()]

# Page 3: county story, shift across cycles
s += [eyebrow("The county story · long term"), Paragraph("The 2024→2026 shift looks like 2016→2018", H2),
      Paragraph(f"Each dot is a county, sized by its electorate. The horizontal axis is how far its primary electorate moved toward Democrats "
                f"from 2016 to 2018; the vertical axis is the same measure from 2024 to 2026. Statewide the two shifts are close "
                f"({SH16_18:.1f} and {SH24_26:.1f} points on the same {N60} counties). Counties above the dashed line "
                "moved more this time than in Trump's first midterm.", BODY),
      img(figs["cycles"]),
      Paragraph("Shift = change in the Democratic share of the two-party primary vote in the top race (2016 U.S. House, 2018 Governor, 2024 U.S. Senate, "
                "2026 Governor). Excludes the 15 counties in the 2016 4th District, where Democrats had no House candidate, and the 10 counties "
                "without 2018 county results.", CAP)]
v = cty.dropna(subset=["shift_pts_2016_2018", "shift_pts_2024_2026"])
more = (v.shift_pts_2024_2026 > v.shift_pts_2016_2018).sum()
ct = [["", "2016 → 2018", "2024 → 2026"],
      [Paragraph(dcol("Democratic primary votes"), CELL), f"{sg((v.dem_primary_2018.sum() / v.dem_primary_2016.sum() - 1) * 100, 0)}%",
       f"{sg((v.dem_primary_2026.sum() / v.dem_primary_2024.sum() - 1) * 100, 0)}%"],
      [Paragraph(rcol("Republican primary votes"), CELL), f"{sg((v.rep_primary_2018.sum() / v.rep_primary_2016.sum() - 1) * 100, 0)}%",
       f"{sg((v.rep_primary_2026.sum() / v.rep_primary_2024.sum() - 1) * 100, 0)}%"],
      ["Shift toward Democrats (points)", sg(SH16_18), sg(SH24_26)]]
s += [table(ct, [W * 0.5, W * 0.25, W * 0.25]),
      Paragraph(f"Same 60 counties. Democrats grew faster in 2018 because 2016's August primary was a quiet one (House races only); the bigger "
                f"difference is Republicans, who grew strongly in 2018 and are nearly flat now. {more} of the 60 counties moved more toward Democrats "
                "this cycle than in 2016→2018.", CAP),
      PageBreak()]

# Page 4: district story
s += [eyebrow("The district story · one cycle (2022 congressional map)"), Paragraph("All 13 congressional districts moved toward Democrats", H2),
      Paragraph("Michigan's congressional map changed in 2022, so districts can only be compared from 2024 to 2026. Each row shows the Democratic "
                "share of primary voters in 2024 (hollow) and 2026 (filled), with Harris's November 2024 two-party share as a tick. Districts are "
                "sorted from most Republican (top) to most Democratic, by the 2024 presidential margin.", BODY),
      img(figs["districts"], W * 0.97)]
dt = [["District", "Main counties", "Pres '24", "Dem votes", "Rep votes", "Dem share", "Shift"]]
for _, r in cd.sort_values("num").iterrows():
    names = r.counties.split(", ")
    main = ", ".join(names[:3]) + (f" + {len(names) - 3} more" if len(names) > 3 else "")
    dt.append([str(r.num), Paragraph(main, CELL), f"{'D' if r.harris_margin_pct_2024 > 0 else 'R'}+{abs(r.harris_margin_pct_2024):.1f}",
               f"{sg(r.dem_change_pct_2024_2026, 0)}%", f"{sg(r.rep_change_pct_2024_2026, 0)}%",
               f"{r.dem_share_primary_2024:.1f} → {r.dem_share_primary_2026:.1f}%", sg(r.shift_pts_2024_2026)])
s += [table(dt, [W * 0.08, W * 0.36, W * 0.1, W * 0.11, W * 0.11, W * 0.15, W * 0.09], align_from=2),
      Paragraph("Vote changes compare the 2024 U.S. Senate primary with the 2026 governor primary. Counties split between districts are divided by "
                "precinct results where available, otherwise by each party's U.S. House primary votes in that county (checked against precinct "
                "results, median error about 2.5%). District 11's 2024 figures are fully estimated this way because Oakland County's 2024 "
                "precinct file is missing. Main counties are the district's largest pieces by 2024 presidential vote.", CAP),
      PageBreak()]

# Page 5: reading November + method
s += [eyebrow("Putting the two together"), Paragraph("How the 2018 primary lined up with November", H2),
      Paragraph(f"In Trump's first midterm, each county's primary electorate was a good guide to its November result: the Democratic share of 2018 "
                f"primary voters and Whitmer's two-party share moved together (correlation 0.96), with Whitmer typically running about 3 points "
                f"ahead of her party's primary share. Statewide, {st.dem_share_primary_2018:.1f}% of primary voters chose the Democratic ballot and "
                f"Whitmer won {st.whitmer_two_party_pct_2018:.1f}% of the two-party vote.", BODY),
      img(figs["calibration"]),
      Paragraph("Each dot is a county, sized by its 2016 presidential vote. The dotted line marks the 2026 statewide Democratic share of primary voters.", CAP),
      Paragraph(f"The 2026 primary electorate is {st.dem_share_primary_2026:.1f}% Democratic, well above 2018. Two cautions before reading that as a "
                "November signal. First, it is one prior cycle, so the 2018 relationship is a reference point, not a rule. Second, 2026's ballots "
                "differ: the Democratic Senate primary was a three-way contest while Mike Rogers ran unopposed for the Republican nomination, which "
                "gives independents more reason to take a Democratic ballot.", BODY),
      Spacer(1, 4), Paragraph("Method and data", H2)]
notes = [
    "Primary turnout is votes cast for candidates in the top race on each party's ballot. Voters who skipped that race aren't counted.",
    "2018 county results come from county clerk reports for 73 counties (OpenElections source files and clerk websites), checked against printed "
    "county totals and the official statewide totals. Antrim, Iron, Kalkaska, Keweenaw, Lake, Mackinac, Mecosta, Monroe, Montmorency and Osceola "
    "have no 2018 county report online and appear only in statewide figures.",
    "2016 has no statewide August race, so the 2016 baseline is the U.S. House primary. It is unusable in the 4th District, where Democrats had "
    "no candidate, and it understates Democratic turnout where House primaries were contested only on the Republican side (such as the open "
    "10th District in the Thumb).",
    "2024 and 2026 county totals come from the OpenElections county files. The 2026 results are from a September 2026 work-in-progress update.",
    "Data file: mi_primary_story.csv, one row per state, region, county and congressional district, with the vote counts and every rate shown here.",
]
s += [Paragraph(n, ParagraphStyle("nb", parent=SMALL, leftIndent=10), bulletText="•") for n in notes]

out = OUT / "mi_primary_story.pdf"
doc = SimpleDocTemplate(str(out), pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.6 * inch,
                        bottomMargin=0.7 * inch, title="Michigan's 2026 primary in context", author="OpenElections Michigan analysis")
doc.build(s, onFirstPage=footer, onLaterPages=footer)
print(out)
