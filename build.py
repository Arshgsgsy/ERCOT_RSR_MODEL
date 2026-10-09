"""Steps 2 and 3 — Align and Features (Build Plan, Sections 6.2 and 6.3).

    python build.py                       # align() then make_features()
    python build.py --step align          # only the hourly panel
    python build.py --step features       # only features, from the saved panel
    python build.py --snapshot 2026-10-03

align() turns one raw snapshot into the hourly panel: one row per zone per UTC
hour on a complete generated grid, with coverage made visible. It computes no
derived variables. make_features() reads that panel and builds every variable
the assignment asks for, hourly and then daily. Model transforms (asinh, the
model-sample MAD) belong to run_models.py.

Outputs in data/interim/:
    hourly_panel.parquet     the panel (4 zones x 8,760 hours)
    align_report.json        inputs and checksums, repairs, gaps, coverage summary
    coverage.csv             non-empty count and share for every column by zone
    rt_complete_by_month.csv complete RT hours by zone and month

Outputs in data/processed/:
    hourly_features.parquet  the panel plus hourly features (35,040 rows)
    daily.parquet            one row per zone and per SYSTEM per local day (1,825 rows)
    features_report.json     reference values, day counts, exit-check findings
    imhr_outside_range.csv   zone-days whose mean IMHR is outside the typical range
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import numpy as np
import pandas as pd

from config import CONFIG, ROOT
from ingest import IngestError, cache_status, file_paths, plan_pulls, sha256, write_json

TZ = CONFIG["study"]["tz"]
ALIGN = CONFIG["align"]
PATHS = CONFIG["paths"]
ZONES = {k: v for k, v in CONFIG["zones"].items() if k != "decision"}
WEATHER = {k: v for k, v in CONFIG["weather_zone_map"].items() if k != "decision"}
LOCATIONS = [z["location"] for z in ZONES.values()]


class BuildError(RuntimeError):
    """Stops the build with a clear message."""


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def snapshot_for(key: str, default: str | None = None) -> str:
    snap = CONFIG["raw_snapshot"]
    return snap["per_dataset"].get(key, default or snap["default"])


def load_snapshot(default: str | None = None) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    """Read every required raw file after verifying its manifest, query and
    checksum against config.py. Returns the frames and the input record."""
    raw, inputs = {}, []
    for key in ("da", "rt", "load", "temperature", "gas"):
        folder = PATHS["raw_full"] / snapshot_for(key, default)
        frames = []
        for p in plan_pulls("full", [key]):
            try:
                status = cache_status(folder, p["name"], p["query"])
            except IngestError as e:
                raise BuildError(str(e)) from e
            pq, mf = file_paths(folder, p["name"])
            if status != "reuse":
                raise BuildError(f"{pq} is missing. Run `python ingest.py full` first.")
            frames.append(pd.read_parquet(pq))
            inputs.append({"file": str(pq.relative_to(ROOT)),
                           "sha256": json.loads(mf.read_text())["sha256"]})
        raw[key] = pd.concat(frames, ignore_index=True)
    return raw, inputs


def find_col(df: pd.DataFrame, candidates: list[str], what: str) -> str:
    """Find a column by name from a list of candidates, ignoring case."""
    lower = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    raise BuildError(f"{what}: none of {candidates} found in {list(df.columns)}")


def utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True)


# --------------------------------------------------------------------------
# The grid
# --------------------------------------------------------------------------

def make_grid(start: str, end: str) -> pd.DataFrame:
    """Every local hour from start up to (not including) end, generated in
    Central time and stored in UTC, repeated for each zone."""
    local = pd.date_range(start, end, freq="h", tz=TZ, inclusive="left")
    hours = pd.DataFrame({
        "hour_utc": local.tz_convert("UTC"),
        "local_date": local.tz_localize(None).normalize(),
    })
    hours["expected_hours"] = hours.groupby("local_date")["hour_utc"].transform("size").astype("int64")
    grid = pd.concat([hours.assign(zone=z) for z in LOCATIONS], ignore_index=True)
    return grid[["zone", "hour_utc", "local_date", "expected_hours"]]


def merge_checked(grid: pd.DataFrame, src: pd.DataFrame, on: list[str], validate: str,
                  name: str, report: dict) -> pd.DataFrame:
    """Left join onto the grid with declared cardinality and a match indicator.
    Source rows that fall outside the grid stop the build."""
    outside = src.merge(grid[on].drop_duplicates(), on=on, how="left", indicator=True)
    n_out = int((outside["_merge"] == "left_only").sum())
    if n_out:
        raise BuildError(f"{name}: {n_out} source rows fall outside the study grid")
    try:
        out = grid.merge(src, on=on, how="left", validate=validate, indicator=True)
    except pd.errors.MergeError as e:
        raise BuildError(f"{name}: merge cardinality check failed ({validate}): {e}") from e
    report.setdefault("matched_share", {})[name] = round(float((out["_merge"] == "both").mean()), 6)
    return out.drop(columns="_merge")


# --------------------------------------------------------------------------
# Sources, each brought to zone x UTC hour (or hour / local day for system data)
# --------------------------------------------------------------------------

def check_interval_length(df: pd.DataFrame, minutes: int, name: str) -> None:
    s = find_col(df, ["interval_start_utc"], f"{name} start")
    e = find_col(df, ["interval_end_utc"], f"{name} end")
    bad = (utc(df[e]) - utc(df[s])) != pd.Timedelta(minutes=minutes)
    if bad.any():
        raise BuildError(f"{name}: {int(bad.sum())} rows are not {minutes}-minute intervals")


def prep_da(da: pd.DataFrame) -> pd.DataFrame:
    check_interval_length(da, 60, "DA")
    out = pd.DataFrame({
        "zone": da[find_col(da, ["location"], "DA location")].astype(str),
        "hour_utc": utc(da[find_col(da, ["interval_start_utc"], "DA start")]),
        "da_price": da[find_col(da, ["spp"], "DA price")].astype(float),
    })
    if out.duplicated(["zone", "hour_utc"]).any():
        raise BuildError("DA: duplicated zone-hour")
    return out


def prep_rt(rt: pd.DataFrame) -> pd.DataFrame:
    """D7: hourly RT price is the mean of four distinct 15-minute prices.
    Fewer than four -> no rt_price. A duplicated quarter-hour stops the build."""
    check_interval_length(rt, 15, "RT")
    q = pd.DataFrame({
        "zone": rt[find_col(rt, ["location"], "RT location")].astype(str),
        "start": utc(rt[find_col(rt, ["interval_start_utc"], "RT start")]),
        "spp": rt[find_col(rt, ["spp"], "RT price")].astype(float),
    })
    if not q["start"].dt.minute.isin([0, 15, 30, 45]).all():
        raise BuildError("RT: intervals not aligned to quarter-hours")
    dup = q.duplicated(["zone", "start"])
    if dup.any():
        raise BuildError(f"RT: {int(dup.sum())} duplicated quarter-hours, e.g. {q.loc[dup].iloc[0].to_dict()}")
    q["hour_utc"] = q["start"].dt.floor("h")
    g = q.groupby(["zone", "hour_utc"])["spp"]
    out = pd.DataFrame({
        "rt_n": g.count(),
        "rt_price_partial": g.mean(),
        "rt_sd_all": g.std(),  # sample SD (ddof=1) of the prices in the hour
    }).reset_index()
    need = ALIGN["rt_intervals_per_hour"]
    out["rt_complete"] = out["rt_n"] == need
    out["rt_price"] = out["rt_price_partial"].where(out["rt_complete"])
    out["rt_intrahour_sd"] = out["rt_sd_all"].where(out["rt_complete"])
    return out.drop(columns="rt_sd_all")


def prep_load(load: pd.DataFrame, report: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Zonal load as published, matched by forecast zone (D6). System load is the
    sum of the four zones, only when all four exist."""
    check_interval_length(load, 60, "load")
    hour = utc(load[find_col(load, ["interval_start_utc"], "load start")])
    if hour.duplicated().any():
        raise BuildError("load: duplicated hour")
    cols = {z["location"]: find_col(load, [z["load_column"]], f"load {z['location']}") for z in ZONES.values()}
    wide = pd.DataFrame({loc: load[c].astype(float).values for loc, c in cols.items()}, index=hour)
    zonal = wide.rename_axis("hour_utc").reset_index().melt("hour_utc", var_name="zone", value_name="load")
    system = pd.DataFrame({"hour_utc": hour.reset_index(drop=True),
                           "sys_load": wide.sum(axis=1, min_count=len(cols)).values})

    total_col = next((c for c in load.columns if c.lower() == "total"), None)
    if total_col:
        diff = (system["sys_load"].values - load[total_col].astype(float).values)
        report["sys_load_minus_published_total_mw"] = {
            "max_abs": round(float(np.nanmax(np.abs(diff))), 3),
            "mean": round(float(np.nanmean(diff)), 3),
        }
    return zonal, system


def repair_fallback(wide: pd.DataFrame, grid_hours: pd.DataFrame, report: dict) -> pd.DataFrame:
    """7.3: on each 25-hour day, if the 1 a.m. temperatures are broken, set both
    to the mean of 12 a.m. and 2 a.m. and flag them. Raw files are untouched."""
    wide = wide.copy()
    wide["temp_dst_fix"] = False
    thr = ALIGN["dst_repair_max_dev_f"]
    for day in grid_hours.loc[grid_hours["expected_hours"] == 25, "local_date"].unique():
        day_hours = grid_hours.loc[grid_hours["local_date"] == day, "hour_utc"].sort_values()
        local = day_hours.dt.tz_convert(TZ)
        h1, h2 = day_hours[local.dt.hour == 1].tolist()
        prev, nxt = h1 - pd.Timedelta("1h"), h2 + pd.Timedelta("1h")
        rec = {"local_date": str(pd.Timestamp(day).date()), "hours_utc": [str(h1), str(h2)]}
        if not all(h in wide.index for h in (prev, h1, h2, nxt)):
            rec["result"] = "not checked: a neighbouring hour is missing"
        else:
            zones = [c for c in wide.columns if c != "temp_dst_fix"]
            mean_nb = (wide.loc[prev, zones] + wide.loc[nxt, zones]) / 2
            dev1 = (wide.loc[h1, zones] - mean_nb).abs()
            dev2 = (wide.loc[h2, zones] - mean_nb).abs()
            copy_of_2am = bool((wide.loc[h2, zones] == wide.loc[nxt, zones]).all())
            broken = bool((dev1 > thr).any() or (dev2 > thr).any() or copy_of_2am)
            rec.update({
                "max_deviation_first_1am_f": round(float(dev1.max()), 2),
                "max_deviation_second_1am_f": round(float(dev2.max()), 2),
                "second_1am_identical_to_2am": copy_of_2am,
                "before": {str(h): wide.loc[h, zones].round(2).to_dict() for h in (h1, h2)},
            })
            if broken:
                wide.loc[[h1, h2], zones] = np.vstack([mean_nb.values, mean_nb.values])
                wide.loc[[h1, h2], "temp_dst_fix"] = True
                rec["result"] = "repaired"
                rec["after"] = mean_nb.round(2).to_dict()
            else:
                rec["result"] = "not broken, left as published"
        report.setdefault("dst_temperature", []).append(rec)
    return wide


def prep_temperature(temp: pd.DataFrame, grid_hours: pd.DataFrame, report: dict) -> pd.DataFrame:
    """D9: equal-weight mean of the weather zones mapped to each load zone,
    with the range across them and the count present."""
    check_interval_length(temp, 60, "temperature")
    hour = utc(temp[find_col(temp, ["interval_start_utc"], "temperature start")])
    if hour.duplicated().any():
        raise BuildError("temperature: duplicated hour (more than one vintage per hour?)")
    wz = sorted({c for cols in WEATHER.values() for c in cols})
    wide = pd.DataFrame({c: temp[find_col(temp, [c], f"weather zone {c}")].astype(float).values for c in wz},
                        index=pd.DatetimeIndex(hour, name="hour_utc"))
    wide = repair_fallback(wide, grid_hours, report)

    lo, hi = ALIGN["temp_range_f"]
    values = wide[wz]
    bad = values.where((values < lo) | (values > hi)).stack().dropna()
    if len(bad):
        raise BuildError(f"temperature: {len(bad)} values outside {lo}..{hi} °F after repair, "
                         f"e.g. {bad.head(5).to_dict()}")
    report["temperature_f"] = {"min": float(values.min().min()), "max": float(values.max().max())}

    parts = []
    for name, cols in WEATHER.items():
        v = wide[cols]
        n = v.notna().sum(axis=1)
        parts.append(pd.DataFrame({
            "hour_utc": wide.index,
            "zone": ZONES[name]["location"],
            "temp": v.mean(axis=1).values,
            "temp_range": (v.max(axis=1) - v.min(axis=1)).where(n >= 2).values,
            "temp_n": n.values.astype("int64"),
            "temp_dst_fix": wide["temp_dst_fix"].values,
        }))
    return pd.concat(parts, ignore_index=True)


def next_weekday(day: pd.Timestamp) -> pd.Timestamp:
    nxt = day + pd.Timedelta("1D")
    while nxt.weekday() >= 5:
        nxt += pd.Timedelta("1D")
    return nxt


def prep_gas(gas: pd.DataFrame, days: pd.Series, report: dict) -> pd.DataFrame:
    """D13, a labeled assumption. Flow day F takes the last Henry Hub observation
    dated strictly before F (gas_price), or on or before F (gas_price_same),
    carried forward over weekends and holidays. Past the end of the series a
    price is never carried: same-date stops at the last observation's day,
    lagged stops one trading day after it."""
    units_col = next((c for c in gas.columns if c.lower() == "units"), None)
    if units_col and set(gas[units_col].unique()) != {CONFIG["units"]["raw_gas_units"]}:
        raise BuildError(f"gas: units {set(gas[units_col].unique())} != {CONFIG['units']['raw_gas_units']}")
    obs = pd.DataFrame({
        "gas_obs_date": pd.to_datetime(gas[find_col(gas, ["period"], "gas date")]),
        "price": gas[find_col(gas, ["price"], "gas price")].astype(float),
    }).sort_values("gas_obs_date")
    if obs["gas_obs_date"].duplicated().any():
        raise BuildError("gas: duplicated observation date")

    gaps = obs["gas_obs_date"].diff().dt.days
    long = obs.loc[gaps > ALIGN["gas_max_gap_days"], "gas_obs_date"]
    last = obs["gas_obs_date"].max()
    report["gas"] = {
        "first_observation": str(obs["gas_obs_date"].min().date()),
        "last_observation": str(last.date()),
        "gaps_longer_than_max": [str(d.date()) for d in long],
    }

    flow = pd.DataFrame({"local_date": pd.Series(days).sort_values().unique()})
    lag = pd.merge_asof(flow, obs, left_on="local_date", right_on="gas_obs_date",
                        allow_exact_matches=False, direction="backward")
    same = pd.merge_asof(flow, obs.rename(columns={"gas_obs_date": "same_obs_date"}),
                         left_on="local_date", right_on="same_obs_date", direction="backward")
    cutoff = next_weekday(last)
    out = pd.DataFrame({
        "local_date": flow["local_date"],
        "gas_price": lag["price"].where(flow["local_date"] <= cutoff),
        "gas_obs_date": lag["gas_obs_date"].where(flow["local_date"] <= cutoff),
        "gas_price_same": same["price"].where(flow["local_date"] < cutoff),
    })
    report["gas"]["flow_days_without_lagged_price"] = [
        str(d.date()) for d in out.loc[out["gas_price"].isna(), "local_date"]]
    report["gas"]["flow_days_without_same_date_price"] = [
        str(d.date()) for d in out.loc[out["gas_price_same"].isna(), "local_date"]]
    return out


# --------------------------------------------------------------------------
# align()
# --------------------------------------------------------------------------

COLUMNS = ["zone", "hour_utc", "local_date", "expected_hours",
           "da_price", "rt_n", "rt_complete", "rt_price", "rt_price_partial", "rt_intrahour_sd",
           "load", "sys_load", "temp", "temp_range", "temp_n", "temp_dst_fix",
           "gas_price", "gas_obs_date", "gas_price_same"]


def align(raw: dict[str, pd.DataFrame], start: str | None = None, end: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Raw frames -> hourly panel and a report. Does no I/O."""
    start = start or CONFIG["study"]["start"]
    end = end or CONFIG["study"]["end"]
    report: dict = {}
    grid = make_grid(start, end)
    hours = grid[grid["zone"] == LOCATIONS[0]][["hour_utc", "local_date", "expected_hours"]]

    panel = merge_checked(grid, prep_da(raw["da"]), ["zone", "hour_utc"], "one_to_one", "da", report)
    panel = merge_checked(panel, prep_rt(raw["rt"]), ["zone", "hour_utc"], "one_to_one", "rt", report)
    zonal_load, sys_load = prep_load(raw["load"], report)
    panel = merge_checked(panel, zonal_load, ["zone", "hour_utc"], "one_to_one", "load", report)
    panel = merge_checked(panel, sys_load, ["hour_utc"], "many_to_one", "sys_load", report)
    temp = prep_temperature(raw["temperature"], hours, report)
    panel = merge_checked(panel, temp, ["zone", "hour_utc"], "one_to_one", "temperature", report)
    gas = prep_gas(raw["gas"], hours["local_date"], report)
    panel = merge_checked(panel, gas, ["local_date"], "many_to_one", "gas", report)

    panel["rt_n"] = panel["rt_n"].fillna(0).astype("int64")
    panel["rt_complete"] = panel["rt_complete"].astype("boolean").fillna(False).astype(bool)
    panel["temp_n"] = panel["temp_n"].fillna(0).astype("int64")
    panel["temp_dst_fix"] = panel["temp_dst_fix"].astype("boolean").fillna(False).astype(bool)
    panel = panel[COLUMNS].sort_values(["zone", "hour_utc"]).reset_index(drop=True)
    return panel, report


# --------------------------------------------------------------------------
# Exit checks (Build Plan 6.2)
# --------------------------------------------------------------------------

def exit_checks(panel: pd.DataFrame, start: str, end: str) -> list[str]:
    """Structural gates. Returns a list of failures (empty when all pass)."""
    fails = []
    n_hours = len(pd.date_range(start, end, freq="h", tz=TZ, inclusive="left"))
    per_zone = panel.groupby("zone").size()
    if set(per_zone.index) != set(LOCATIONS) or (per_zone != n_hours).any():
        fails.append(f"hours per zone {per_zone.to_dict()} != {n_hours}")
    if panel.duplicated(["zone", "hour_utc"]).any():
        fails.append("duplicate (zone, hour_utc) keys")
    steps = panel.groupby("zone")["hour_utc"].diff().dropna()
    if (steps != pd.Timedelta("1h")).any():
        fails.append("hours are not exactly one hour apart")
    day_len = panel.groupby(["zone", "local_date"]).agg(n=("hour_utc", "size"), exp=("expected_hours", "first"))
    if (day_len["n"] != day_len["exp"]).any():
        fails.append("local day lengths do not match expected_hours")
    lengths = day_len.groupby(level="local_date")["exp"].first()
    for day, want in (("2025-11-02", 25), ("2026-03-08", 23)):
        d = pd.Timestamp(day)
        if d in lengths.index and lengths[d] != want:
            fails.append(f"{day} has {lengths[d]} hours, expected {want}")
    g = panel.dropna(subset=["gas_obs_date"])
    if (g["gas_obs_date"] >= g["local_date"]).any():
        fails.append("a gas observation date is not before its flow day")
    return fails


def coverage_tables(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    value_cols = [c for c in COLUMNS if c not in ("zone", "hour_utc", "local_date", "expected_hours")]
    rows = []
    for zone, g in panel.groupby("zone"):
        for c in value_cols:
            flag = g[c].dtype == bool
            n = int(g[c].sum()) if flag else int(g[c].notna().sum())
            rows.append({"zone": zone, "column": c, "unit": CONFIG["units"].get(c, ""),
                         "measure": "true" if flag else "non_empty", "count": n,
                         "share": round(n / len(g), 6)})
    cov = pd.DataFrame(rows)
    month = panel["local_date"].dt.to_period("M").astype(str)
    rt_month = (panel.assign(month=month).groupby(["zone", "month"])
                .agg(hours=("hour_utc", "size"), rt_complete=("rt_complete", "sum")).reset_index())
    rt_month["rt_incomplete"] = rt_month["hours"] - rt_month["rt_complete"]
    return cov, rt_month


def trace_rows(panel: pd.DataFrame) -> pd.DataFrame:
    """Rows to hand-trace against the raw files (Build Plan 6.2 exit checks)."""
    picks = ["2026-07-22 22:00", "2025-11-02 06:00", "2025-11-02 07:00", "2026-03-08 08:00"]
    want = pd.to_datetime(picks).tz_localize("UTC")
    return panel[(panel["zone"] == "LZ_NORTH") & panel["hour_utc"].isin(want)]


def write_parquet_checked(df: pd.DataFrame, path) -> None:
    """Write to a temporary file, confirm it reads back identically, then rename."""
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    if not pd.read_parquet(tmp).equals(df):
        tmp.unlink()
        raise BuildError(f"{path.name} does not read back identically")
    tmp.replace(path)


def run_align(snapshot: str | None = None) -> pd.DataFrame:
    raw, inputs = load_snapshot(snapshot)
    start, end = CONFIG["study"]["start"], CONFIG["study"]["end"]
    panel, report = align(raw, start, end)

    fails = exit_checks(panel, start, end)
    cov, rt_month = coverage_tables(panel)
    out = PATHS["interim"]
    out.mkdir(parents=True, exist_ok=True)

    report = {
        "built_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "snapshot": {k: snapshot_for(k, snapshot) for k in ("da", "rt", "load", "temperature", "gas")},
        "inputs": inputs,
        "settings": {"study": CONFIG["study"], "align": ALIGN, "zones": CONFIG["zones"],
                     "weather_zone_map": CONFIG["weather_zone_map"]},
        "rows": len(panel),
        "rt_incomplete_hours": panel.loc[~panel["rt_complete"], ["zone", "hour_utc"]]
            .assign(hour_utc=lambda d: d["hour_utc"].dt.tz_convert(TZ).astype(str))
            .groupby("zone")["hour_utc"].apply(list).to_dict(),
        "gate_failures": fails,
        **report,
    }
    write_json(out / "align_report.json", report)
    cov.to_csv(out / "coverage.csv", index=False)
    rt_month.to_csv(out / "rt_complete_by_month.csv", index=False)
    if fails:
        for f in fails:
            print(f"  GATE FAILED: {f}")
        raise BuildError("align() exit checks failed; panel not written")

    write_parquet_checked(panel, out / "hourly_panel.parquet")
    print(f"Wrote {out / 'hourly_panel.parquet'}: {len(panel):,} rows, {panel['zone'].nunique()} zones")
    print(f"Matched share by source: {report['matched_share']}")
    print(f"RT incomplete hours: { {z: len(v) for z, v in report['rt_incomplete_hours'].items()} }")
    for d in report.get("dst_temperature", []):
        print(f"DST temperature {d['local_date']}: {d['result']}")
    print(f"Gas: last observation {report['gas']['last_observation']}; "
          f"no lagged price on {report['gas']['flow_days_without_lagged_price']}; "
          f"no same-date price on {report['gas']['flow_days_without_same_date_price']}")
    if "sys_load_minus_published_total_mw" in report:
        print(f"sys_load minus published total (MW): {report['sys_load_minus_published_total_mw']}")
    print("\nRows to hand-trace against the raw files (LZ_NORTH):")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(trace_rows(panel).to_string(index=False))
    return panel


# --------------------------------------------------------------------------
# make_features() (Build Plan 5.3, 5.4 and 6.3)
# --------------------------------------------------------------------------

FEAT = CONFIG["features"]
SYSTEM = "SYSTEM"


def wide(hf: pd.DataFrame, col: str) -> pd.DataFrame:
    """One row per UTC hour, one column per zone."""
    return hf.pivot(index="hour_utc", columns="zone", values=col)[LOCATIONS]


def system_hourly(hf: pd.DataFrame, temp_weights: pd.Series) -> pd.DataFrame:
    """System series built once per UTC hour from the four zones (5.4). An hour
    needs all four zones; a missing zone never shrinks the system quietly."""
    load = wide(hf, "load")
    full_load = load.notna().all(axis=1)

    def load_weighted(col: str) -> pd.Series:
        v = wide(hf, col)
        ok = full_load & v.notna().all(axis=1)
        return ((v * load).sum(axis=1) / load.sum(axis=1)).where(ok)

    temp = wide(hf, "temp")
    return pd.DataFrame({
        "sys_spread": load_weighted("spread"),
        "sys_da_price": load_weighted("da_price"),
        "sys_rt_price": load_weighted("rt_price"),
        # Annual load-share weights, fixed over the year and saved.
        "sys_temp": (temp * temp_weights[LOCATIONS]).sum(axis=1).where(temp.notna().all(axis=1)),
    }).rename_axis("hour_utc").reset_index()


def basis(hf: pd.DataFrame, col: str) -> pd.DataFrame:
    """Each zone's price minus the load-weighted average of the other three (D36)."""
    p, load = wide(hf, col), wide(hf, "load")
    ok = p.notna().all(axis=1) & load.notna().all(axis=1)
    total_pl, total_l = (p * load).sum(axis=1), load.sum(axis=1)
    others = (-(p * load)).add(total_pl, axis=0) / (-load).add(total_l, axis=0)
    out = (p - others).where(ok, axis=0)
    name = col.replace("_price", "_basis")
    return out.rename_axis("hour_utc").reset_index().melt("hour_utc", var_name="zone", value_name=name)


def hourly_features(panel: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Hourly panel -> hourly features and the saved reference values."""
    hf = panel.copy()
    local = hf["hour_utc"].dt.tz_convert(TZ)
    hf["hour_ending"] = (local.dt.hour + 1).astype("int64")
    hf["weekday"] = hf["local_date"].dt.weekday.astype("int64")  # 0 = Monday
    hf["month"] = hf["local_date"].dt.month.astype("int64")
    lo, hi = FEAT["onpeak_hour_ending"]
    hf["onpeak"] = (hf["weekday"] < 5) & hf["hour_ending"].between(lo, hi)

    hf["spread"] = hf["da_price"] - hf["rt_price"]
    hf["abs_spread"] = hf["spread"].abs()

    gas = hf["gas_price"].where(hf["gas_price"] > FEAT["gas_floor"])
    hf["imhr_da"] = hf["da_price"] / gas
    hf["imhr_rt"] = hf["rt_price"] / gas

    q = FEAT["load_norm_quantile"]
    load_ref = hf.groupby("zone")["load"].quantile(q)
    hf["load_norm"] = hf["load"] / hf["zone"].map(load_ref)

    base = FEAT["degree_day_base_f"]
    hf["cdd_h"] = (hf["temp"] - base).clip(lower=0)
    hf["hdd_h"] = (base - hf["temp"]).clip(lower=0)

    annual = hf.groupby("zone")["load"].sum()
    temp_weights = annual / annual.sum()
    sysh = system_hourly(hf, temp_weights)
    hf = hf.merge(sysh, on="hour_utc", how="left", validate="many_to_one")
    for col in ("da_price", "rt_price"):
        hf = hf.merge(basis(hf, col), on=["zone", "hour_utc"], how="left", validate="one_to_one")

    refs = {
        "load_norm_quantile": q,
        "load_reference_mw": {z: round(float(v), 3) for z, v in load_ref.items()},
        "sys_load_reference_mw": round(float(hf.drop_duplicates("hour_utc")["sys_load"].quantile(q)), 3),
        "sys_temp_weights": {z: round(float(v), 6) for z, v in temp_weights.items()},
    }
    return hf.sort_values(["zone", "hour_utc"]).reset_index(drop=True), refs


def summarize_day(g: pd.DataFrame, cols: dict) -> dict:
    """Daily columns for one zone (or the system) on one local day. `cols` names
    the hourly columns to use for spread, prices, load and temperature."""
    spread = g[cols["spread"]]
    expected = int(g["expected_hours"].iloc[0])
    sd = spread.std()
    on, off = spread[g["onpeak"]], spread[~g["onpeak"]]
    load = g[cols["load"]]
    temp = g[cols["temp"]]
    gas = g["gas_price"].iloc[0]
    gas = gas if pd.notna(gas) and gas > FEAT["gas_floor"] else np.nan
    da, rt = g[cols["da"]], g[cols["rt"]]
    n_spread = int(spread.notna().sum())
    n_temp, n_load = int(temp.notna().sum()), int(load.notna().sum())
    return {
        "expected_hours": expected,
        "n_spread_hours": n_spread,
        "complete_day": n_spread == expected,
        "n_temp_hours": n_temp,
        "n_load_hours": n_load,
        "factors_complete": n_temp == expected and n_load == expected,
        "n_onpeak_expected": int(g["onpeak"].sum()),
        "n_onpeak_valid": int(on.notna().sum()),
        "spread_mean": spread.mean(),
        "spread_onpeak_mean": on.mean() if len(on) else np.nan,
        "spread_offpeak_mean": off.mean(),
        "spread_sd": sd,
        "log_spread_sd": np.log(sd) if pd.notna(sd) and sd > 0 else np.nan,
        "spread_abs_max": spread.abs().max(),
        "rt_intrahour_sd_mean": g["rt_intrahour_sd"].mean() if cols["intrahour"] else np.nan,
        "da_mean": da.mean(),
        "rt_mean": rt.mean(),
        "rt_sd": rt.std(),
        "gas_price": g["gas_price"].iloc[0],
        "gas_price_same": g["gas_price_same"].iloc[0],
        "imhr_da_mean": (da / gas).mean(),
        "imhr_rt_mean": (rt / gas).mean(),
        "load_mean": load.mean(),
        "load_peak": load.max(),
        "load_factor": load.mean() / load.max(),
        "temp_mean": temp.mean(),
        "temp_max": temp.max(),
        "temp_min": temp.min(),
        "cdd": (temp - FEAT["degree_day_base_f"]).clip(lower=0).mean(),
        "hdd": (FEAT["degree_day_base_f"] - temp).clip(lower=0).mean(),
        "temp_range_mean": g["temp_range"].mean() if cols["temp_range"] else np.nan,
        "n_temp_repaired_hours": int(g["temp_dst_fix"].sum()),
    }


def daily_table(hf: pd.DataFrame, refs: dict) -> pd.DataFrame:
    """One row per zone per local day, plus one SYSTEM row per day (5.4)."""
    zone_cols = {"spread": "spread", "da": "da_price", "rt": "rt_price", "load": "load",
                 "temp": "temp", "intrahour": True, "temp_range": True}
    sys_cols = {"spread": "sys_spread", "da": "sys_da_price", "rt": "sys_rt_price", "load": "sys_load",
                "temp": "sys_temp", "intrahour": False, "temp_range": False}

    rows = []
    for (zone, day), g in hf.groupby(["zone", "local_date"], sort=True):
        rows.append({"zone": zone, "local_date": day, **summarize_day(g, zone_cols)})
    sys_hours = hf[hf["zone"] == LOCATIONS[0]]
    for day, g in sys_hours.groupby("local_date", sort=True):
        rows.append({"zone": SYSTEM, "local_date": day, **summarize_day(g, sys_cols)})
    daily = pd.DataFrame(rows)

    ref = daily["zone"].map({**refs["load_reference_mw"], SYSTEM: refs["sys_load_reference_mw"]})
    daily["load_peak_norm"] = daily["load_peak"] / ref
    is_sys = daily["zone"] == SYSTEM
    daily["sys_temp"] = daily["temp_mean"].where(is_sys)
    daily["sys_load_peak_norm"] = daily["load_peak_norm"].where(is_sys)
    daily["month"] = daily["local_date"].dt.month.astype("int64")
    daily["weekday"] = daily["local_date"].dt.weekday.astype("int64")
    daily["post_rtcb"] = (daily["local_date"] >= pd.Timestamp(FEAT["rtcb_go_live"])).astype("int64")
    return daily.sort_values(["zone", "local_date"]).reset_index(drop=True)


def feature_checks(hf: pd.DataFrame, daily: pd.DataFrame) -> tuple[list[str], dict, pd.DataFrame]:
    """Gates (structure), findings and the IMHR days to investigate (Build Plan 6.3 exit checks)."""
    fails = []
    n_days = daily["local_date"].nunique()
    if daily.duplicated(["zone", "local_date"]).any():
        fails.append("duplicate (zone, local_date) keys in the daily table")
    if len(daily) != 5 * n_days:
        fails.append(f"daily rows {len(daily)} != 5 x {n_days}")
    if hf.duplicated(["zone", "hour_utc"]).any():
        fails.append("duplicate (zone, hour_utc) keys in hourly features")
    weekend = daily["weekday"] >= 5
    if daily.loc[weekend, "spread_onpeak_mean"].notna().any() or (daily.loc[weekend, "n_onpeak_expected"] > 0).any():
        fails.append("on-peak values on a weekend")
    if (daily.loc[daily["spread_sd"] > 0, "log_spread_sd"].isna()).any():
        fails.append("log_spread_sd missing where spread_sd > 0")
    sys_days = daily[daily["zone"] == SYSTEM].set_index("local_date")
    zone_complete = daily[daily["zone"] != SYSTEM].groupby("local_date")["complete_day"].all()
    if (sys_days["complete_day"] & ~zone_complete.reindex(sys_days.index)).any():
        fails.append("a SYSTEM day is complete while a zone day is not")

    by = daily.groupby("zone")
    usable = daily["complete_day"] & daily["factors_complete"]
    counts = pd.DataFrame({
        "days": by.size(),
        "complete_day": by["complete_day"].sum(),
        "factors_complete": by["factors_complete"].sum(),
        "main_model_days": usable.groupby(daily["zone"]).sum(),
        "onpeak_model_days": (usable & ~weekend).groupby(daily["zone"]).sum(),
    })
    incomplete_share = 1 - counts["main_model_days"] / counts["days"]

    spread_wide = daily[usable].pivot(index="local_date", columns="zone", values="spread_mean")
    corr = spread_wide.corr().round(4)

    lo, hi = FEAT["imhr_typical_range"]
    imhr = daily[daily["zone"] != SYSTEM]
    outside = imhr[((imhr["imhr_da_mean"] < lo) | (imhr["imhr_da_mean"] > hi)) |
                   ((imhr["imhr_rt_mean"] < lo) | (imhr["imhr_rt_mean"] > hi))]

    post = daily["post_rtcb"] == 1
    regime = daily[usable].groupby(["zone", post[usable].map({True: "post_rtcb", False: "pre_rtcb"})])[
        ["spread_mean", "spread_sd"]].mean().round(3)

    findings = {
        "day_counts": counts.astype(int).to_dict("index"),
        "incomplete_days": {z: [str(d.date()) for d in g.loc[~(g["complete_day"] & g["factors_complete"]), "local_date"]]
                            for z, g in by},
        "partial_day_sensitivity_needed": bool((incomplete_share > FEAT["max_incomplete_day_share"]).any()),
        "spread_mean_correlation": corr.to_dict(),
        "min_daily_spread_sd": by["spread_sd"].min().round(4).to_dict(),
        "sd_zero_days": by["spread_sd"].apply(lambda s: int((s == 0).sum())).to_dict(),
        "share_days_load_peak_norm_above_1": by["load_peak_norm"].apply(lambda s: round(float((s > 1).mean()), 4)).to_dict(),
        "imhr_outside_typical_range": {"range": [lo, hi], "zone_days": len(outside)},
        "spread_by_rtcb_regime_descriptive": {f"{z}|{r}": v for (z, r), v in regime.to_dict("index").items()},
    }
    return fails, findings, outside


def make_features(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Hourly panel -> (hourly features, daily table, reference values). No I/O."""
    hf, refs = hourly_features(panel)
    return hf, daily_table(hf, refs), refs


def run_features() -> pd.DataFrame:
    src = PATHS["interim"] / "hourly_panel.parquet"
    if not src.exists():
        raise BuildError(f"{src} is missing. Run align first.")
    panel = pd.read_parquet(src)
    hf, daily, refs = make_features(panel)
    fails, findings, outside = feature_checks(hf, daily)

    out = PATHS["processed"]
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "built_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "input": {"file": str(src.relative_to(ROOT)), "sha256": sha256(src)},
        "settings": FEAT,
        "references": refs,
        "rows": {"hourly": len(hf), "daily": len(daily)},
        "gate_failures": fails,
        **findings,
    }
    write_json(out / "features_report.json", report)
    outside[["zone", "local_date", "imhr_da_mean", "imhr_rt_mean", "da_mean", "rt_mean", "gas_price",
             "temp_mean", "load_peak_norm"]].to_csv(out / "imhr_outside_range.csv", index=False)
    if fails:
        for f in fails:
            print(f"  GATE FAILED: {f}")
        raise BuildError("make_features() exit checks failed; tables not written")
    write_parquet_checked(hf, out / "hourly_features.parquet")
    write_parquet_checked(daily, out / "daily.parquet")

    print(f"Wrote {out / 'hourly_features.parquet'}: {len(hf):,} rows")
    print(f"Wrote {out / 'daily.parquet'}: {len(daily):,} rows ({daily['zone'].nunique()} zones incl. SYSTEM)")
    with pd.option_context("display.width", 200):
        print("\nDay counts:\n" + pd.DataFrame(findings["day_counts"]).T.to_string())
        print("\nCorrelation of daily all-hours mean spread (complete days):\n"
              + pd.DataFrame(findings["spread_mean_correlation"]).to_string())
    print(f"\nIncomplete days: {findings['incomplete_days']}")
    print(f"Partial-day sensitivity needed: {findings['partial_day_sensitivity_needed']}")
    print(f"SD = 0 days: {findings['sd_zero_days']}; min daily SD: {findings['min_daily_spread_sd']}")
    print(f"Share of days with load_peak_norm > 1: {findings['share_days_load_peak_norm_above_1']}")
    print(f"IMHR daily means outside {FEAT['imhr_typical_range']}: {len(outside)} zone-days "
          f"-> {out / 'imhr_outside_range.csv'}")
    print(f"Load references (99.5th pct, MW): {refs['load_reference_mw']}, system {refs['sys_load_reference_mw']}")
    print(f"System temperature weights: {refs['sys_temp_weights']}")
    return daily


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", help="raw snapshot (default: config raw_snapshot)")
    ap.add_argument("--step", choices=["align", "features", "all"], default="all")
    args = ap.parse_args(argv)
    try:
        if args.step in ("align", "all"):
            run_align(args.snapshot)
        if args.step in ("features", "all"):
            print()
            run_features()
        return 0
    except BuildError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
