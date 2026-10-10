"""Facts sheet and report tables for one run (Build Plan 8, number check).

Reads the tables of a finished run and writes, into the same folder:
- facts.csv / facts.md   every number the report is likely to quote, each with its
                         source file, row and column, so the final number check is
                         a lookup
- report_tables.md       the four-zone results table and the representative spread
                         table, rounded and labelled for the page

    python facts.py --out outputs/<run_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from config import CONFIG

ZONE = {"LZ_HOUSTON": "Houston", "LZ_NORTH": "North", "LZ_SOUTH": "South", "LZ_WEST": "West", "SYSTEM": "System"}
HAC_SIZE = CONFIG["paths"]["outputs"] / "hac_size"


class Facts:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, fid: str, text: str, value, file: str, where: str = "", column: str = "", fmt: str = "{:.3g}"):
        shown = fmt.format(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
        self.rows.append({"id": fid, "statement": text, "value": shown, "file": file, "row": where, "column": column})

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def read(out: Path, name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(out / name, **kw)


def add_data_facts(F: Facts) -> None:
    rep = json.loads((CONFIG["paths"]["processed"] / "features_report.json").read_text())
    f = "data/processed/features_report.json"
    for z, c in rep["day_counts"].items():
        F.add(f"days_main_{z}", f"{ZONE[z]}: days in the main models", c["main_model_days"], f, z, "day_counts.main_model_days", "{:d}")
        F.add(f"days_onpeak_{z}", f"{ZONE[z]}: days in the on-peak models", c["onpeak_model_days"], f, z, "day_counts.onpeak_model_days", "{:d}")
    F.add("imhr_outside", "zone-days with a daily mean heat rate outside 3-30 MMBtu/MWh",
          rep["imhr_outside_typical_range"]["zone_days"], f, "", "imhr_outside_typical_range.zone_days", "{:d}")
    for z, v in rep["references"]["load_reference_mw"].items():
        F.add(f"load_ref_{z}", f"{ZONE[z]}: 99.5th percentile hourly load (MW)", v, f, z, "references.load_reference_mw", "{:,.0f}")


def add_descriptive_facts(F: Facts, out: Path) -> None:
    t = read(out, "representative_table.csv", index_col="zone")
    for z, r in t.iterrows():
        for col, label, fmt in [("all_mean", "mean daily all-hours spread ($/MWh)", "{:.2f}"),
                                ("all_median", "median daily all-hours spread ($/MWh)", "{:.2f}"),
                                ("all_share_da_above_rt", "share of days with DA above RT", "{:.1%}"),
                                ("onpeak_mean", "mean weekday on-peak spread ($/MWh)", "{:.2f}"),
                                ("offpeak_mean", "mean off-peak spread ($/MWh)", "{:.2f}"),
                                ("corr_with_system", "correlation of daily spread with the system spread", "{:.3f}"),
                                ("rt_basis_share_abs_gt_20", "share of hours with |RT basis| > $20", "{:.1%}")]:
            if pd.notna(r[col]):
                F.add(f"rep_{col}_{z}", f"{ZONE[z]}: {label}", r[col], "representative_table.csv", z, col, fmt)
    bz = read(out, "between_zone.csv", index_col="zone")
    F.add("pc1_share", "first principal component share of the four zones' daily spreads",
          bz["pc1_share_four_zones"].iloc[0], "between_zone.csv", "any zone", "pc1_share_four_zones", "{:.1%}")
    c = read(out, "spread_correlation.csv", index_col="zone")
    zones = [z for z in c.index if z != "SYSTEM"]
    pair = c.loc[zones, zones].where(lambda x: x < 1).stack()
    F.add("corr_min", "lowest pairwise correlation of zone daily spreads", pair.min(), "spread_correlation.csv",
          " / ".join(pair.idxmin()), "", "{:.3f}")
    F.add("corr_max", "highest pairwise correlation of zone daily spreads", pair.max(), "spread_correlation.csv",
          " / ".join(pair.idxmax()), "", "{:.3f}")
    g = read(out, "gas_sensitivity.csv", index_col="zone")
    F.add("gas_days_differ", "days on which the lagged and same-date gas prices differ",
          g["days_gas_differs"].iloc[0], "gas_sensitivity.csv", "any zone", "days_gas_differs", "{:d}")
    F.add("gas_imhr_max_change", "largest change in a zone's mean DA or RT heat rate, lagged vs same-date gas",
          max((g["imhr_da_mean_lagged"] - g["imhr_da_mean_same"]).abs().max(),
              (g["imhr_rt_mean_lagged"] - g["imhr_rt_mean_same"]).abs().max()),
          "gas_sensitivity.csv", "all zones", "imhr_*_mean_lagged - imhr_*_mean_same", "{:.3f}")
    e = read(out, "event_table.csv")
    for _, r in e[e["rank"] == 1].iterrows():
        F.add(f"event1_{r['zone']}", f"{ZONE[r['zone']]}: most volatile day (daily spread SD, $/MWh)",
              f"{r['local_date'][:10]}, SD {r['spread_sd']:.0f}", "event_table.csv", f"{r['zone']}, rank 1",
              "local_date, spread_sd")
    q = read(out, "by_temperature_quintile.csv")
    q1 = q[q["quintile"] == 1]
    for _, r in q1.iterrows():
        F.add(f"q1_level_{r['zone']}", f"{ZONE[r['zone']]}: mean spread on the coldest fifth of days, all / without Fern ($/MWh)",
              f"{r['spread_mean']:.2f} / {r['spread_mean_no_fern']:.2f}", "by_temperature_quintile.csv",
              f"{r['zone']}, quintile 1", "spread_mean, spread_mean_no_fern")


def add_model_facts(F: Facts, out: Path) -> None:
    r = read(out, "results_table.csv")
    for _, m in r.iterrows():
        key, lab = f"{m['model']}|{m['zone']}", f"{m['model']} {ZONE[m['zone']]}"
        where = f"model={m['model']}, zone={m['zone']}"
        for col, label, fmt in [("n", "days", "{:d}"), ("r2_full", "R² (full quadratic)", "{:.3f}"),
                                ("terms_chosen", "chosen terms", "{}"), ("r2_chosen", "R² (chosen terms)", "{:.3f}"),
                                ("lof_p_boot", "lack-of-fit p (bootstrap)", "{:.3f}"),
                                ("lof_p_hac", "lack-of-fit p (HAC, comparison only)", "{:.2g}"),
                                ("shape_chosen", "shape (chosen surface)", "{}"),
                                ("fitted_low_dollars", "lowest fitted value over observed days ($/MWh)", "{:.1f}"),
                                ("fitted_high_dollars", "highest fitted value over observed days ($/MWh)", "{:.0f}"),
                                ("r2_no_fern", "R² without the Fern days", "{:.3f}"),
                                ("fern_share_of_explained_ss", "Fern days' share of explained SS", "{:.0%}"),
                                ("shape_corr_ordinary_days", "surface correlation over ordinary days, with vs without Fern", "{:.2f}"),
                                ("post_rtcb_coef", "post-RTC+B block coefficient (response units)", "{:.3f}")]:
            F.add(f"{col}|{key}", f"{lab}: {label}", m[col], "results_table.csv", where, col, fmt)
        F.add(f"post_rtcb_ci|{key}", f"{lab}: post-RTC+B block 95% bootstrap interval",
              f"[{m['post_rtcb_boot_low']:.3f}, {m['post_rtcb_boot_high']:.3f}]", "results_table.csv", where,
              "post_rtcb_boot_low, post_rtcb_boot_high")
    c = read(out, "conditional_tests.csv")
    for _, t in c.iterrows():
        F.add(f"cond|{t['model']}|{t['zone']}|{t['test']}", f"{t['model']} {ZONE[t['zone']]}: {t['test']} p (bootstrap / HAC)",
              f"{t['p_boot']:.3f} / {t['p_hac']:.2g}", "conditional_tests.csv",
              f"model={t['model']}, zone={t['zone']}, test={t['test']}", "p_boot, p_hac")
    me = read(out, "marginal_effects.csv")
    for _, e in me[me["model"].isin(["vol_zone", "vol_sys"])].iterrows():
        F.add(f"me|{e['model']}|{e['zone']}|{e['day']}|{e['factor']}",
              f"{e['model']} {ZONE[e['zone']]}, {e['day']} day ({e['local_date'][:10]}): slope in {e['factor']} ({e['dollar_unit']})",
              f"{e['slope_dollars']:.2f} [{e['slope_dollars_low']:.2f}, {e['slope_dollars_high']:.2f}]",
              "marginal_effects.csv", f"model={e['model']}, zone={e['zone']}, day={e['day']}, factor={e['factor']}",
              "slope_dollars, slope_dollars_low, slope_dollars_high")


def add_inference_facts(F: Facts) -> None:
    if not (HAC_SIZE / "size.csv").exists():
        return
    s = pd.read_csv(HAC_SIZE / "size.csv")
    F.add("hac_size_lof", "HAC lack-of-fit false-rejection rate at nominal 5% (range over designs and noise)",
          f"{s['lof_hac'].min():.0%}-{s['lof_hac'].max():.0%}", "outputs/hac_size/size.csv", "all rows", "lof_hac")
    t = s.filter(regex=r"^t_.*_hac$")
    F.add("hac_size_t", "HAC single-term false-rejection rate at nominal 5% (range)",
          f"{t.min().min():.0%}-{t.max().max():.0%}", "outputs/hac_size/size.csv", "all rows", "t_*_hac")
    if (HAC_SIZE / "bootstrap_size.csv").exists():
        b = pd.read_csv(HAC_SIZE / "bootstrap_size.csv")
        cols = [c for c in b.columns if c.startswith("lack_of_fit|boot")]
        F.add("boot_size_lof", "bootstrap lack-of-fit false-rejection rate at nominal 5% (range, block lengths 10/25/50)",
              f"{b[cols].min().min():.1%}-{b[cols].max().max():.1%}", "outputs/hac_size/bootstrap_size.csv",
              f"all rows (reps {b['reps'].iloc[0]})", ", ".join(cols))


def fmt_p(p: float) -> str:
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def report_tables(out: Path) -> str:
    r = read(out, "results_table.csv")
    lines = ["# Report tables", "", "## Four-zone results (full quadratic for tests; chosen terms for the surface)", ""]
    for model, title in [("vol_zone", "Volatility: log daily spread SD"), ("lvl_zone", "Level: asinh-standardized daily mean spread"),
                         ("vol_sys", "System volatility"), ("lvl_sys", "System level"),
                         ("lvl_zone_onpeak", "Robustness: level on the weekday on-peak mean")]:
        g = r[r["model"] == model]
        lines += [f"### {title}", "",
                  "| Zone | n | R² full | R² chosen | Chosen terms | Lack of fit p (boot) | Shape | Fitted range ($/MWh) | R² w/o Fern | Corr. with no-Fern surface (ordinary days) |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for _, m in g.iterrows():
            lines.append(f"| {ZONE[m['zone']]} | {m['n']} | {m['r2_full']:.3f} | {m['r2_chosen']:.3f} | "
                         f"{m['terms_chosen'].replace('^2', '²').replace('*', '·')} | {fmt_p(m['lof_p_boot'])} | "
                         f"{m['shape_chosen']} | {m['fitted_low_dollars']:.1f} to {m['fitted_high_dollars']:.0f} | "
                         f"{m['r2_no_fern']:.3f} | {m['shape_corr_ordinary_days']:.2f} |")
        lines.append("")
    t = read(out, "representative_table.csv", index_col="zone")
    lines += ["## Representative spreads ($/MWh, daily means over complete days; spread = DA − RT)", "",
              "| Zone | All-hours mean | Median | SD | 5th–95th pct | DA > RT days | On-peak mean | Off-peak mean | Corr. with system | \\|RT basis\\| > $20 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for z, m in t.iterrows():
        corr = "—" if pd.isna(m["corr_with_system"]) else f"{m['corr_with_system']:.3f}"
        basis = "—" if pd.isna(m["rt_basis_share_abs_gt_20"]) else f"{m['rt_basis_share_abs_gt_20']:.1%}"
        lines.append(f"| {ZONE[z]} | {m['all_mean']:.2f} | {m['all_median']:.2f} | {m['all_sd']:.1f} | "
                     f"{m['all_p05']:.1f} to {m['all_p95']:.1f} | {m['all_share_da_above_rt']:.0%} | "
                     f"{m['onpeak_mean']:.2f} | {m['offpeak_mean']:.2f} | {corr} | {basis} |")
    lines += ["", f"Days: {int(t['all_n_days'].iloc[0])} all-hours and off-peak, {int(t['onpeak_n_days'].iloc[0])} on-peak per zone.", ""]
    return "\n".join(lines)


def write_facts(out: Path) -> Path:
    F = Facts()
    add_data_facts(F)
    add_descriptive_facts(F, out)
    add_model_facts(F, out)
    add_inference_facts(F)
    df = F.frame()
    df.to_csv(out / "facts.csv", index=False)
    md = ["# Facts sheet", "", "Every number with its source. Files are in this run folder unless a path is given.", "",
          "| id | statement | value | file | row | column |", "|---|---|---|---|---|---|"]
    md += [f"| {r.id} | {r.statement} | {r.value} | {r.file} | {r.row} | {r.column} |" for r in df.itertuples()]
    (out / "facts.md").write_text("\n".join(md) + "\n")
    (out / "report_tables.md").write_text(report_tables(out))
    print(f"Wrote {len(df)} facts and the report tables to {out}")
    return out / "facts.csv"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True, help="a finished run folder")
    write_facts(ap.parse_args(argv).out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
