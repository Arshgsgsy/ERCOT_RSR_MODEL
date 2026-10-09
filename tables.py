"""Descriptive tables (Build Plan 5.5, D28, D36, 6.7 event table).

Reads the processed daily table and hourly features and writes CSV tables into
an output folder. No model is fitted here.

    python tables.py --out outputs/<run_id>

Tables
- representative_spreads.csv  all-hours, on-peak and off-peak daily means by zone
                              and system: mean, median, SD, 5th/95th percentiles,
                              share of days with DA above RT, day counts (D28)
- between_zone.csv            correlation with the system spread, first principal
                              component share, DA and RT price basis (D36)
- spread_correlation.csv      pairwise correlation of daily all-hours mean spreads
- representative_table.csv    the compact report table: the two above merged
- heat_rates_and_load.csv     zonal DA/RT implied heat rates and load measures
- gas_sensitivity.csv         heat rates under the lagged and same-date gas price (D13)
- event_table.csv             top days per zone by daily spread SD (6.7)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from config import CONFIG

PATHS = CONFIG["paths"]
FEAT = CONFIG["features"]
SYSTEM = "SYSTEM"
MEASURES = {"all_hours": "spread_mean", "onpeak": "spread_onpeak_mean", "offpeak": "spread_offpeak_mean"}
BASIS_THRESHOLDS = (5.0, 20.0)
EVENT_TOP_N = 5


class TableError(RuntimeError):
    """A table cannot be built from the processed data."""


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_path = PATHS["processed"] / "daily.parquet"
    hourly_path = PATHS["processed"] / "hourly_features.parquet"
    for p in (daily_path, hourly_path):
        if not p.exists():
            raise TableError(f"{p} is missing. Run `python build.py` first.")
    return pd.read_parquet(daily_path), pd.read_parquet(hourly_path)


def zone_order(zones) -> list[str]:
    return sorted(z for z in set(zones) if z != SYSTEM) + ([SYSTEM] if SYSTEM in set(zones) else [])


def describe(x: pd.Series) -> dict:
    x = x.dropna()
    return {"n_days": len(x), "mean": x.mean(), "median": x.median(), "sd": x.std(),
            "p05": x.quantile(0.05), "p95": x.quantile(0.95), "share_da_above_rt": (x > 0).mean()}


# --------------------------------------------------------------------------
# Representative spreads (D28)
# --------------------------------------------------------------------------

def representative_spreads(daily: pd.DataFrame) -> pd.DataFrame:
    """One row per zone and measure. Complete days only (every expected hour has a
    spread). On-peak exists on weekdays only, so its day count is smaller."""
    d = daily[daily["complete_day"]]
    rows = []
    for zone in zone_order(d["zone"]):
        g = d[d["zone"] == zone]
        for measure, col in MEASURES.items():
            rows.append({"zone": zone, "measure": measure, **describe(g[col])})
    return pd.DataFrame(rows).set_index(["zone", "measure"])


# --------------------------------------------------------------------------
# Between-zone columns (D36)
# --------------------------------------------------------------------------

def spread_wide(daily: pd.DataFrame) -> pd.DataFrame:
    """Daily all-hours mean spread, one column per zone (and SYSTEM), complete days."""
    d = daily[daily["complete_day"]]
    return d.pivot(index="local_date", columns="zone", values="spread_mean")[zone_order(d["zone"])]


def pc1_share(wide: pd.DataFrame) -> float:
    """Share of variance on the first principal component of the zones' standardized
    daily spreads (correlation matrix), days on which every zone is complete."""
    zones = [z for z in wide.columns if z != SYSTEM]
    corr = wide[zones].dropna().corr().to_numpy()
    evals = np.linalg.eigvalsh(corr)
    return float(evals.max() / evals.sum())


def basis_stats(hourly: pd.DataFrame, col: str) -> pd.DataFrame:
    rows = []
    for zone, g in hourly.groupby("zone", sort=True):
        b = g[col].dropna()
        row = {"zone": zone, f"{col}_n_hours": len(b), f"{col}_mean": b.mean(),
               f"{col}_p05": b.quantile(0.05), f"{col}_p95": b.quantile(0.95)}
        for t in BASIS_THRESHOLDS:
            row[f"{col}_share_abs_gt_{t:g}"] = (b.abs() > t).mean()
        rows.append(row)
    return pd.DataFrame(rows).set_index("zone")


def between_zone(daily: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    wide = spread_wide(daily)
    zones = [z for z in wide.columns if z != SYSTEM]
    corr_sys = {}
    for z in zones:
        both = wide[[z, SYSTEM]].dropna()
        corr_sys[z] = both[z].corr(both[SYSTEM])
    out = pd.DataFrame({"corr_with_system": pd.Series(corr_sys)})
    out["pc1_share_four_zones"] = pc1_share(wide)
    out = out.join(basis_stats(hourly, "rt_basis")).join(basis_stats(hourly, "da_basis"))
    return out.rename_axis("zone")


def representative_table(rep: pd.DataFrame, bz: pd.DataFrame) -> pd.DataFrame:
    """The compact report table: one row per zone (and SYSTEM)."""
    out = {}
    for zone in rep.index.get_level_values("zone").unique():
        a, on, off = (rep.loc[(zone, m)] for m in MEASURES)
        out[zone] = {
            "all_mean": a["mean"], "all_median": a["median"], "all_sd": a["sd"], "all_p05": a["p05"],
            "all_p95": a["p95"], "all_share_da_above_rt": a["share_da_above_rt"], "all_n_days": a["n_days"],
            "onpeak_mean": on["mean"], "onpeak_n_days": on["n_days"],
            "offpeak_mean": off["mean"], "offpeak_n_days": off["n_days"],
        }
    table = pd.DataFrame(out).T.rename_axis("zone")
    keep = ["corr_with_system", "rt_basis_mean", "rt_basis_p05", "rt_basis_p95",
            "rt_basis_share_abs_gt_5", "rt_basis_share_abs_gt_20"]
    return table.join(bz[keep])


# --------------------------------------------------------------------------
# Heat rates, load and the gas sensitivity
# --------------------------------------------------------------------------

def heat_rates_and_load(daily: pd.DataFrame) -> pd.DataFrame:
    """Zonal implied heat rates (Henry-Hub-based, descriptive, D29) and load
    measures, over complete days."""
    d = daily[daily["complete_day"] & daily["factors_complete"]]
    lo, hi = FEAT["imhr_typical_range"]
    rows = []
    for zone in zone_order(d["zone"]):
        g = d[d["zone"] == zone]
        row = {"zone": zone, "n_days": len(g)}
        for col in ("imhr_da_mean", "imhr_rt_mean"):
            x = g[col].dropna()
            row.update({f"{col}_mean": x.mean(), f"{col}_median": x.median(), f"{col}_p05": x.quantile(0.05),
                        f"{col}_p95": x.quantile(0.95), f"{col}_n_outside_{lo:g}_{hi:g}": int(((x < lo) | (x > hi)).sum())})
        row.update({"load_mean_mw": g["load_mean"].mean(), "load_peak_mw_max": g["load_peak"].max(),
                    "load_peak_norm_median": g["load_peak_norm"].median(),
                    "load_peak_norm_p95": g["load_peak_norm"].quantile(0.95),
                    "share_days_load_peak_norm_gt_1": (g["load_peak_norm"] > 1).mean(),
                    "load_factor_mean": g["load_factor"].mean()})
        rows.append(row)
    return pd.DataFrame(rows).set_index("zone")


def gas_sensitivity(daily: pd.DataFrame) -> pd.DataFrame:
    """Daily mean heat rates under the lagged gas price (main, D13) and the
    same-date price (sensitivity). Gas enters no model, so this is the whole
    sensitivity. With one gas price per day, mean(price / gas) = mean price / gas."""
    d = daily[daily["complete_day"] & (daily["zone"] != SYSTEM)].copy()
    floor = FEAT["gas_floor"]
    same = d["gas_price_same"].where(d["gas_price_same"] > floor)
    d["imhr_da_same"], d["imhr_rt_same"] = d["da_mean"] / same, d["rt_mean"] / same
    lo, hi = FEAT["imhr_typical_range"]
    rows = []
    for zone, g in d.groupby("zone", sort=True):
        row = {"zone": zone, "n_days": len(g), "days_gas_differs": int((g["gas_price"] != g["gas_price_same"]).sum())}
        for kind, lag_col, same_col in (("da", "imhr_da_mean", "imhr_da_same"), ("rt", "imhr_rt_mean", "imhr_rt_same")):
            row.update({f"imhr_{kind}_mean_lagged": g[lag_col].mean(), f"imhr_{kind}_mean_same": g[same_col].mean(),
                        f"imhr_{kind}_median_lagged": g[lag_col].median(), f"imhr_{kind}_median_same": g[same_col].median(),
                        f"imhr_{kind}_corr": g[lag_col].corr(g[same_col])})
        out_lag = ((g["imhr_da_mean"] < lo) | (g["imhr_da_mean"] > hi) | (g["imhr_rt_mean"] < lo) | (g["imhr_rt_mean"] > hi))
        out_same = ((g["imhr_da_same"] < lo) | (g["imhr_da_same"] > hi) | (g["imhr_rt_same"] < lo) | (g["imhr_rt_same"] > hi))
        row.update({"days_outside_range_lagged": int(out_lag.sum()), "days_outside_range_same": int(out_same.sum())})
        rows.append(row)
    return pd.DataFrame(rows).set_index("zone")


# --------------------------------------------------------------------------
# Event table (6.7)
# --------------------------------------------------------------------------

def event_table(daily: pd.DataFrame, top: int = EVENT_TOP_N) -> pd.DataFrame:
    """Top days per zone (and SYSTEM) by the actual daily spread SD, ties broken by
    date. Complete days only, the same days the models use. Events are annotated in
    the report after ranking, never here."""
    cols = ["spread_sd", "spread_mean", "spread_abs_max", "da_mean", "rt_mean", "temp_mean", "temp_min",
            "temp_max", "load_peak", "load_peak_norm", "gas_price"]
    d = daily[daily["complete_day"]]
    rows = []
    for zone in zone_order(d["zone"]):
        g = d[d["zone"] == zone].sort_values(["spread_sd", "local_date"], ascending=[False, True]).head(top)
        g = g.assign(rank=range(1, len(g) + 1))
        rows.append(g[["zone", "rank", "local_date", "weekday", *cols]])
    return pd.concat(rows).set_index(["zone", "rank"])


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def make_tables(daily: pd.DataFrame, hourly: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Every descriptive table. No I/O."""
    rep = representative_spreads(daily)
    bz = between_zone(daily, hourly)
    return {
        "representative_spreads": rep,
        "between_zone": bz,
        "spread_correlation": spread_wide(daily).corr(),
        "representative_table": representative_table(rep, bz),
        "heat_rates_and_load": heat_rates_and_load(daily),
        "gas_sensitivity": gas_sensitivity(daily),
        "event_table": event_table(daily),
    }


def write_tables(out: Path) -> dict[str, pd.DataFrame]:
    daily, hourly = load_inputs()
    tables = make_tables(daily, hourly)
    out.mkdir(parents=True, exist_ok=True)
    for name, t in tables.items():
        t.to_csv(out / f"{name}.csv", float_format="%.6g")
    print(f"Wrote {len(tables)} tables to {out}")
    return tables


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=PATHS["outputs"] / "dev", help="output folder (default outputs/dev)")
    args = ap.parse_args(argv)
    try:
        tables = write_tables(args.out)
    except TableError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 1
    with pd.option_context("display.width", 250, "display.max_columns", 40):
        print("\nRepresentative spread table:\n" + tables["representative_table"].to_string(float_format=lambda v: f"{v:.3f}"))
        print("\nEvent table:\n" + tables["event_table"].to_string(float_format=lambda v: f"{v:.2f}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
