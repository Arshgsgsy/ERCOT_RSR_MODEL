"""Offline tests for build.align(): calendar and coverage fixtures (Build Plan 8).

Every test builds small synthetic raw frames shaped like the Grid Status files,
so nothing reads data/ or calls the API.
"""

import numpy as np
import pandas as pd
import pytest

import build
from build import BuildError, align, exit_checks, make_grid

TZ = build.TZ
WZ = sorted({c for cols in build.WEATHER.values() for c in cols})


def synth(start, end, gas_dates=None):
    """Raw frames for [start, end) local days with smooth, distinct values."""
    local = pd.date_range(start, end, freq="h", tz=TZ, inclusive="left")
    u = local.tz_convert("UTC")
    n = len(u)
    da = pd.concat([pd.DataFrame({
        "interval_start_utc": u, "interval_end_utc": u + pd.Timedelta("1h"),
        "location": loc, "spp": 50.0 + np.arange(n) % 7,
    }) for loc in build.LOCATIONS], ignore_index=True)

    q = pd.date_range(u[0], u[-1] + pd.Timedelta("45min"), freq="15min")
    rt = pd.concat([pd.DataFrame({
        "interval_start_utc": q, "interval_end_utc": q + pd.Timedelta("15min"),
        "location": loc, "spp": 40.0 + np.arange(len(q)) % 4,
    }) for loc in build.LOCATIONS], ignore_index=True)

    load = pd.DataFrame({"interval_start_utc": u, "interval_end_utc": u + pd.Timedelta("1h")})
    for i, z in enumerate(build.ZONES.values()):
        load[z["load_column"]] = 1000.0 * (i + 1) + np.arange(n)
    load["total"] = load[[z["load_column"] for z in build.ZONES.values()]].sum(axis=1)

    temp = pd.DataFrame({"interval_start_utc": u, "interval_end_utc": u + pd.Timedelta("1h"),
                         "publish_time_utc": u + pd.Timedelta("2D")})
    for i, c in enumerate(WZ):
        temp[c] = 60.0 + i + 10 * np.sin(2 * np.pi * np.arange(n) / 24)

    if gas_dates is None:
        days = pd.date_range(pd.Timestamp(start) - pd.Timedelta("5D"), end, freq="D")
        gas_dates = [d for d in days if d.weekday() < 5]
    gas = pd.DataFrame({"period": [str(pd.Timestamp(d).date()) for d in gas_dates],
                        "price": 3.0 + np.arange(len(gas_dates)) / 100, "units": "$/MMBTU"})
    return {"da": da, "rt": rt, "load": load, "temperature": temp, "gas": gas}


FALL = ("2025-11-01", "2025-11-04")
SPRING = ("2026-03-07", "2026-03-10")


def row(panel, zone, hour):
    return panel[(panel["zone"] == zone) & (panel["hour_utc"] == pd.Timestamp(hour))].iloc[0]


# --- grid -----------------------------------------------------------------

def test_grid_daylight_saving_days():
    g = make_grid(*FALL)
    one = g[g["zone"] == "LZ_NORTH"]
    assert one.groupby("local_date").size()[pd.Timestamp("2025-11-02")] == 25
    local = one["hour_utc"].dt.tz_convert(TZ)
    one_am = one.loc[(one["local_date"] == "2025-11-02") & (local.dt.hour == 1), "hour_utc"]
    assert list(one_am.dt.strftime("%H:%M")) == ["06:00", "07:00"]

    s = make_grid(*SPRING)
    assert s[s["zone"] == "LZ_NORTH"].groupby("local_date").size()[pd.Timestamp("2026-03-08")] == 23


def test_full_study_grid_boundaries():
    g = make_grid(build.CONFIG["study"]["start"], build.CONFIG["study"]["end"])
    one = g[g["zone"] == "LZ_NORTH"]
    assert len(one) == 8760 and len(g) == 35_040
    assert one["hour_utc"].min() == pd.Timestamp("2025-10-01 05:00", tz="UTC")
    assert one["hour_utc"].max() == pd.Timestamp("2026-10-01 04:00", tz="UTC")


# --- clean data -----------------------------------------------------------

@pytest.mark.parametrize("window", [FALL, SPRING])
def test_clean_data_passes_and_round_trips(window, tmp_path):
    panel, report = align(synth(*window), *window)
    assert exit_checks(panel, *window) == []
    assert panel["rt_complete"].all() and panel["rt_price"].notna().all()
    assert set(report["matched_share"].values()) == {1.0}
    assert not panel["temp_dst_fix"].any()
    panel.to_parquet(tmp_path / "p.parquet", index=False)
    back = pd.read_parquet(tmp_path / "p.parquet")
    assert back.equals(panel)
    assert str(back["hour_utc"].dt.tz) == "UTC"


def test_rt_price_is_mean_of_four():
    panel, _ = align(synth(*FALL), *FALL)
    r = row(panel, "LZ_WEST", "2025-11-02 06:00Z")
    assert r["rt_n"] == 4 and r["rt_price"] == pytest.approx(41.5)
    assert r["rt_intrahour_sd"] == pytest.approx(np.std([40, 41, 42, 43], ddof=1))


# --- RT coverage ----------------------------------------------------------

def test_duplicated_quarter_hour_stops():
    raw = synth(*FALL)
    raw["rt"] = pd.concat([raw["rt"], raw["rt"].iloc[[10]]], ignore_index=True)
    with pytest.raises(BuildError, match="duplicated quarter-hours"):
        align(raw, *FALL)


def test_missing_quarter_hour_gives_no_rt_price():
    raw = synth(*FALL)
    gone = (raw["rt"]["location"] == "LZ_NORTH") & \
           (raw["rt"]["interval_start_utc"] == pd.Timestamp("2025-11-02 07:30Z"))
    raw["rt"] = raw["rt"][~gone]
    panel, _ = align(raw, *FALL)
    r = row(panel, "LZ_NORTH", "2025-11-02 07:00Z")
    assert r["rt_n"] == 3 and not r["rt_complete"]
    assert pd.isna(r["rt_price"]) and pd.isna(r["rt_intrahour_sd"])
    assert pd.notna(r["rt_price_partial"])
    assert row(panel, "LZ_SOUTH", "2025-11-02 07:00Z")["rt_complete"]
    assert len(panel) == 4 * 73


# --- missing source rows stay visible ---------------------------------------

def test_missing_hour_stays_as_empty_row():
    raw = synth(*FALL)
    raw["da"] = raw["da"][raw["da"]["interval_start_utc"] != pd.Timestamp("2025-11-02 06:00Z")]
    panel, report = align(raw, *FALL)
    assert len(panel) == 4 * 73
    assert pd.isna(row(panel, "LZ_NORTH", "2025-11-02 06:00Z")["da_price"])
    assert report["matched_share"]["da"] < 1


def test_source_row_outside_window_stops():
    raw = synth(*FALL)
    extra = raw["da"].iloc[[0]].copy()
    extra["interval_start_utc"] -= pd.Timedelta("1D")
    extra["interval_end_utc"] -= pd.Timedelta("1D")
    raw["da"] = pd.concat([raw["da"], extra], ignore_index=True)
    with pytest.raises(BuildError, match="outside the study grid"):
        align(raw, *FALL)


def test_sys_load_needs_all_four_zones():
    raw = synth(*FALL)
    raw["load"].loc[5, "west"] = np.nan
    panel, _ = align(raw, *FALL)
    h = raw["load"].loc[5, "interval_start_utc"]
    assert pd.isna(row(panel, "LZ_NORTH", h)["sys_load"])
    assert pd.isna(row(panel, "LZ_WEST", h)["load"])
    assert pd.notna(row(panel, "LZ_NORTH", h)["load"])


# --- temperature ----------------------------------------------------------

def set_temp(raw, hour, values):
    t = raw["temperature"]
    t.loc[t["interval_start_utc"] == pd.Timestamp(hour), WZ] = values


def get_temp(raw, hour):
    t = raw["temperature"]
    return t.loc[t["interval_start_utc"] == pd.Timestamp(hour), WZ].iloc[0].values


def test_doubled_fallback_hour_is_repaired():
    raw = synth(*FALL)
    set_temp(raw, "2025-11-02 06:00Z", 2 * get_temp(raw, "2025-11-02 06:00Z"))
    expected = (get_temp(raw, "2025-11-02 05:00Z") + get_temp(raw, "2025-11-02 08:00Z")) / 2
    panel, report = align(raw, *FALL)
    assert report["dst_temperature"][0]["result"] == "repaired"
    for h in ("2025-11-02 06:00Z", "2025-11-02 07:00Z"):
        r = row(panel, "LZ_HOUSTON", h)
        assert r["temp_dst_fix"]
        assert r["temp"] == pytest.approx(expected[WZ.index("coast")])
    assert panel["temp_dst_fix"].sum() == 2 * 4


def test_second_1am_copied_from_2am_is_repaired():
    raw = synth(*FALL)
    set_temp(raw, "2025-11-02 07:00Z", get_temp(raw, "2025-11-02 08:00Z"))
    _, report = align(raw, *FALL)
    assert report["dst_temperature"][0]["second_1am_identical_to_2am"]
    assert report["dst_temperature"][0]["result"] == "repaired"


def test_clean_fallback_hour_is_left_alone():
    _, report = align(synth(*FALL), *FALL)
    assert report["dst_temperature"][0]["result"] == "not broken, left as published"


def test_temperature_outside_texas_range_stops():
    raw = synth(*FALL)
    set_temp(raw, "2025-11-03 15:00Z", 150.0)
    with pytest.raises(BuildError, match="outside"):
        align(raw, *FALL)


def test_houston_has_no_temperature_range():
    panel, _ = align(synth(*FALL), *FALL)
    assert panel.loc[panel["zone"] == "LZ_HOUSTON", "temp_range"].isna().all()
    assert panel.loc[panel["zone"] == "LZ_NORTH", "temp_range"].notna().all()


# --- gas (D13) ---------------------------------------------------------------

GAS_WINDOW = ("2025-10-03", "2025-10-08")  # Fri to Tue


def gas_by_day(panel):
    return panel[panel["zone"] == "LZ_NORTH"].groupby("local_date")[
        ["gas_price", "gas_obs_date", "gas_price_same"]].first()


def test_gas_lag_and_same_date_rules():
    obs = ["2025-10-02", "2025-10-03", "2025-10-06"]  # Thu, Fri, Mon
    panel, _ = align(synth(*GAS_WINDOW, gas_dates=obs), *GAS_WINDOW)
    g = gas_by_day(panel)
    lag = {str(d.date()): str(o.date()) for d, o in g["gas_obs_date"].items()}
    assert lag == {"2025-10-03": "2025-10-02", "2025-10-04": "2025-10-03", "2025-10-05": "2025-10-03",
                   "2025-10-06": "2025-10-03", "2025-10-07": "2025-10-06"}
    assert g.loc["2025-10-06", "gas_price_same"] == pytest.approx(3.02)
    assert g.loc["2025-10-04", "gas_price_same"] == pytest.approx(3.01)
    assert exit_checks(panel, *GAS_WINDOW) == []


def test_gas_never_carried_past_end_of_series():
    window = ("2025-10-03", "2025-10-10")  # Fri to Thu
    obs = ["2025-10-02", "2025-10-03", "2025-10-06"]  # series ends Monday
    panel, report = align(synth(*window, gas_dates=obs), *window)
    g = gas_by_day(panel)
    assert pd.notna(g.loc["2025-10-07", "gas_price"])        # Tue uses Mon
    assert g.loc["2025-10-08":, "gas_price"].isna().all()    # Wed, Thu: no stale price
    assert pd.notna(g.loc["2025-10-06", "gas_price_same"])
    assert g.loc["2025-10-07":, "gas_price_same"].isna().all()
    assert report["gas"]["flow_days_without_lagged_price"] == ["2025-10-08", "2025-10-09"]


def test_first_day_without_earlier_gas_has_no_lagged_price():
    obs = ["2025-10-03", "2025-10-06"]
    panel, report = align(synth(*GAS_WINDOW, gas_dates=obs), *GAS_WINDOW)
    assert pd.isna(gas_by_day(panel).loc["2025-10-03", "gas_price"])
    assert report["gas"]["flow_days_without_lagged_price"] == ["2025-10-03"]


def test_wrong_gas_units_stop():
    raw = synth(*GAS_WINDOW)
    raw["gas"]["units"] = "$/MWh"
    with pytest.raises(BuildError, match="units"):
        align(raw, *GAS_WINDOW)


# ===========================================================================
# make_features() (Build Plan 5.3, 5.4, 6.3)
# ===========================================================================

from build import SYSTEM, feature_checks, make_features


def features(window=FALL, raw=None, mutate=None):
    panel, _ = align(raw or synth(*window), *window)
    if mutate:
        mutate(panel)
    return make_features(panel)


def day(daily, zone, date):
    return daily[(daily["zone"] == zone) & (daily["local_date"] == pd.Timestamp(date))].iloc[0]


def test_daily_shape_and_clean_checks():
    hf, daily, _ = features()
    assert len(hf) == 4 * 73
    assert len(daily) == 5 * 3 and set(daily["zone"]) == set(build.LOCATIONS) | {SYSTEM}
    fails, _, _ = feature_checks(hf, daily)
    assert fails == []
    assert daily["complete_day"].all() and daily["factors_complete"].all()


def test_25_and_23_hour_days_use_every_hour():
    hf, daily, _ = features(FALL)
    d = day(daily, "LZ_NORTH", "2025-11-02")
    assert d["expected_hours"] == 25 and d["n_spread_hours"] == 25
    g = hf[(hf["zone"] == "LZ_NORTH") & (hf["local_date"] == pd.Timestamp("2025-11-02"))]
    assert d["spread_mean"] == pytest.approx(g["spread"].mean())
    _, daily_s, _ = features(SPRING)
    assert day(daily_s, "LZ_NORTH", "2026-03-08")["n_spread_hours"] == 23


def test_missing_interval_makes_day_incomplete():
    raw = synth(*FALL)
    gone = (raw["rt"]["location"] == "LZ_SOUTH") & \
           (raw["rt"]["interval_start_utc"] == pd.Timestamp("2025-11-03 20:15Z"))
    raw["rt"] = raw["rt"][~gone]
    hf, daily, _ = features(raw=raw)
    assert not day(daily, "LZ_SOUTH", "2025-11-03")["complete_day"]
    assert day(daily, "LZ_SOUTH", "2025-11-03")["n_spread_hours"] == 23
    assert not day(daily, SYSTEM, "2025-11-03")["complete_day"]
    assert day(daily, "LZ_NORTH", "2025-11-03")["complete_day"]
    assert feature_checks(hf, daily)[0] == []


def test_weekend_onpeak_is_missing_not_zero():
    _, daily, _ = features(FALL)  # Sat Nov 1, Sun Nov 2, Mon Nov 3
    for date in ("2025-11-01", "2025-11-02"):
        d = day(daily, "LZ_WEST", date)
        assert d["n_onpeak_expected"] == 0 and pd.isna(d["spread_onpeak_mean"])
    mon = day(daily, "LZ_WEST", "2025-11-03")
    assert mon["n_onpeak_expected"] == 16 and pd.notna(mon["spread_onpeak_mean"])


def test_onpeak_hour_ending_7_to_22():
    hf, _, _ = features(FALL)
    mon = hf[(hf["zone"] == "LZ_NORTH") & (hf["local_date"] == pd.Timestamp("2025-11-03"))]
    assert sorted(mon.loc[mon["onpeak"], "hour_ending"]) == list(range(7, 23))
    assert mon["hour_ending"].min() == 1 and mon["hour_ending"].max() == 24


def set_hour(panel, hour, col, values):
    for zone, v in zip(build.LOCATIONS, values):
        panel.loc[(panel["zone"] == zone) & (panel["hour_utc"] == pd.Timestamp(hour)), col] = v


H = "2025-11-03 18:00Z"


def test_system_spread_is_load_weighted():
    # LOCATIONS order: HOUSTON, NORTH, SOUTH, WEST
    def mutate(p):
        set_hour(p, H, "da_price", [60.0, 50.0, 40.0, 30.0])
        set_hour(p, H, "rt_price", [50.0, 50.0, 50.0, 50.0])
        set_hour(p, H, "load", [1000.0, 2000.0, 3000.0, 4000.0])
    hf, _, _ = features(mutate=mutate)
    row = hf[(hf["zone"] == "LZ_WEST") & (hf["hour_utc"] == pd.Timestamp(H))].iloc[0]
    spreads, loads = np.array([10, 0, -10, -20]), np.array([1000, 2000, 3000, 4000])
    assert row["sys_spread"] == pytest.approx((spreads * loads).sum() / loads.sum())  # -10
    # West DA basis: 30 minus load-weighted DA of the other three
    others = (60 * 1000 + 50 * 2000 + 40 * 3000) / 6000
    assert row["da_basis"] == pytest.approx(30 - others)


def test_system_hour_needs_all_four_zones():
    hf, daily, _ = features(mutate=lambda p: set_hour(p, H, "load", [1000.0, 2000.0, 3000.0, np.nan]))
    rows = hf[hf["hour_utc"] == pd.Timestamp(H)]
    assert rows["sys_spread"].isna().all() and rows["da_basis"].isna().all()
    assert not day(daily, SYSTEM, "2025-11-03")["complete_day"]


def test_gas_at_or_below_floor_gives_no_imhr():
    def mutate(p):
        p.loc[p["local_date"] == pd.Timestamp("2025-11-03"), "gas_price"] = 0.40
    hf, daily, _ = features(mutate=mutate)
    assert hf.loc[hf["local_date"] == pd.Timestamp("2025-11-03"), "imhr_da"].isna().all()
    assert pd.isna(day(daily, "LZ_NORTH", "2025-11-03")["imhr_da_mean"])
    assert hf.loc[hf["local_date"] == pd.Timestamp("2025-11-01"), "imhr_da"].notna().all()


def test_zero_sd_day_has_empty_log():
    def mutate(p):
        m = (p["zone"] == "LZ_HOUSTON") & (p["local_date"] == pd.Timestamp("2025-11-03"))
        p.loc[m, "da_price"], p.loc[m, "rt_price"] = 45.0, 40.0
    hf, daily, _ = features(mutate=mutate)
    d = day(daily, "LZ_HOUSTON", "2025-11-03")
    assert d["spread_sd"] == 0 and pd.isna(d["log_spread_sd"])
    _, findings, _ = feature_checks(hf, daily)
    assert findings["sd_zero_days"]["LZ_HOUSTON"] == 1


def test_degree_days_from_hourly_temperatures():
    hf, daily, _ = features()
    assert np.allclose(daily["cdd"] - daily["hdd"], daily["temp_mean"] - 65)
    g = hf[(hf["zone"] == "LZ_NORTH") & (hf["local_date"] == pd.Timestamp("2025-11-03"))]
    assert day(daily, "LZ_NORTH", "2025-11-03")["cdd"] == pytest.approx((g["temp"] - 65).clip(lower=0).mean())


def test_load_norm_and_reference_values():
    hf, daily, refs = features()
    north = hf[hf["zone"] == "LZ_NORTH"]
    assert refs["load_reference_mw"]["LZ_NORTH"] == pytest.approx(north["load"].quantile(0.995), abs=1e-3)
    assert (hf["load_norm"] > 1).any()  # never clipped
    assert sum(refs["sys_temp_weights"].values()) == pytest.approx(1)
    sysd = daily[daily["zone"] == SYSTEM]
    assert sysd["sys_temp"].notna().all() and daily.loc[daily["zone"] != SYSTEM, "sys_temp"].isna().all()


def test_post_rtcb_flag():
    window = ("2025-12-04", "2025-12-06")
    _, daily, _ = features(window)
    flags = daily[daily["zone"] == "LZ_NORTH"].set_index("local_date")["post_rtcb"]
    assert flags[pd.Timestamp("2025-12-04")] == 0 and flags[pd.Timestamp("2025-12-05")] == 1


# ===========================================================================
# Optional phase: load forecast (Build Plan 6.7, D8)
# ===========================================================================

def with_forecast(window=FALL, publish_offset="9h30min", scale=0.98):
    """synth() plus a load forecast published at `publish_offset` after local
    midnight the day before each operating day (9:30 a.m. by default)."""
    raw = synth(*window)
    load = raw["load"]
    start = pd.to_datetime(load["interval_start_utc"], utc=True)
    day = start.dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize()
    pub = (day - pd.Timedelta("1D") + pd.Timedelta(publish_offset)).dt.tz_localize(TZ).dt.tz_convert("UTC")
    fc = load[["interval_start_utc", "interval_end_utc"]].assign(publish_time_utc=pub)
    for z in build.ZONES.values():
        fc[z["load_column"]] = load[z["load_column"]] * scale
    raw["load_fcst"] = fc
    return raw


def test_without_forecast_columns_stay_empty():
    hf, daily, _ = features()
    assert hf["load_fcst"].isna().all() and hf["fe_pct"].isna().all()
    assert not daily["fcst_complete"].any() and daily["fe_pct_rms"].isna().all()
    _, findings, _ = feature_checks(hf, daily)
    assert findings["load_forecast_error"] == {"available": False}


def test_forecast_after_10am_day_before_stops():
    with pytest.raises(BuildError, match="after the D8 deadline"):
        align(with_forecast(publish_offset="10h05min"), *FALL)


def test_forecast_error_hourly_and_daily():
    hf, daily, _ = features(raw=with_forecast(scale=0.98))
    want = 100 * (1 / 0.98 - 1)                                   # load = forecast / 0.98
    assert hf["fe_pct"].to_numpy() == pytest.approx(np.full(len(hf), want))
    assert hf["sys_fe_pct"].to_numpy() == pytest.approx(np.full(len(hf), want))
    d = day(daily, "LZ_NORTH", "2025-11-02")
    assert d["n_fcst_hours"] == 25 and d["fcst_complete"]
    assert d["fe_pct_mean"] == pytest.approx(want) and d["fe_pct_rms"] == pytest.approx(want)
    assert day(daily, SYSTEM, "2025-11-02")["fe_pct_mean"] == pytest.approx(want)


def test_one_missing_forecast_hour_empties_that_days_error():
    raw = with_forecast()
    raw["load_fcst"] = raw["load_fcst"].drop(index=30)
    _, daily, _ = features(raw=raw)
    missing_day = daily[~daily["fcst_complete"]]
    assert len(missing_day) == 5 and missing_day["fe_pct_rms"].isna().all()   # 4 zones + SYSTEM
    assert daily["complete_day"].all()                                        # main models unaffected
