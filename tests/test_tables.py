"""Tests for tables.py (descriptive tables). Offline, on small made-up frames."""

import numpy as np
import pandas as pd
import pytest

from tables import (SYSTEM, between_zone, event_table, gas_sensitivity, pc1_share, representative_spreads,
                    representative_table, spread_wide)

ZONES = ["LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST"]


def fake_daily(n=14, seed=0):
    rng = np.random.default_rng(seed)
    days = pd.date_range("2026-03-02", periods=n)  # starts on a Monday
    base = rng.normal(0, 10, n)
    rows = []
    for i, zone in enumerate([*ZONES, SYSTEM]):
        d = pd.DataFrame({
            "zone": zone, "local_date": days, "weekday": days.weekday,
            "complete_day": True, "factors_complete": True,
            "spread_mean": base + rng.normal(0, 1 + i, n),
            "spread_onpeak_mean": rng.normal(1, 5, n), "spread_offpeak_mean": rng.normal(1, 5, n),
            "spread_sd": rng.uniform(5, 50, n), "spread_abs_max": rng.uniform(10, 100, n),
            "da_mean": rng.uniform(20, 60, n), "rt_mean": rng.uniform(20, 60, n),
            "temp_mean": rng.uniform(40, 90, n), "temp_min": 30.0, "temp_max": 95.0,
            "load_peak": 1000.0, "load_peak_norm": rng.uniform(0.5, 1.0, n),
            "gas_price": 3.0, "gas_price_same": 3.0,
        })
        d.loc[d["weekday"] >= 5, "spread_onpeak_mean"] = np.nan
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def fake_hourly(n=100, seed=1):
    rng = np.random.default_rng(seed)
    return pd.concat([pd.DataFrame({"zone": z, "rt_basis": rng.normal(0, 10, n), "da_basis": rng.normal(0, 4, n)})
                      for z in ZONES], ignore_index=True)


def test_representative_spreads_use_complete_days_and_weekdays_for_onpeak():
    d = fake_daily()
    d.loc[(d["zone"] == "LZ_NORTH") & (d.index % 14 == 2), "complete_day"] = False  # one weekday dropped
    rep = representative_spreads(d)
    assert rep.loc[("LZ_NORTH", "all_hours"), "n_days"] == 13
    assert rep.loc[("LZ_NORTH", "onpeak"), "n_days"] == 9   # 10 weekdays, one incomplete
    assert rep.loc[("LZ_HOUSTON", "onpeak"), "n_days"] == 10
    g = d[(d["zone"] == "LZ_HOUSTON")]["spread_mean"]
    assert rep.loc[("LZ_HOUSTON", "all_hours"), "mean"] == pytest.approx(g.mean())
    assert rep.loc[("LZ_HOUSTON", "all_hours"), "share_da_above_rt"] == pytest.approx((g > 0).mean())
    assert list(rep.index.get_level_values("zone").unique()) == [*ZONES, SYSTEM]


def test_pc1_share_is_one_for_identical_zones_and_quarter_for_independent():
    x = np.arange(10.0)
    same = pd.DataFrame({z: x for z in ZONES})
    assert pc1_share(same.assign(**{ZONES[0]: x + 0})) == pytest.approx(1.0)
    rng = np.random.default_rng(3)
    indep = pd.DataFrame({z: rng.normal(size=20000) for z in ZONES})
    assert pc1_share(indep) == pytest.approx(0.25, abs=0.02)


def test_between_zone_correlation_and_basis_shares():
    d, h = fake_daily(), fake_hourly()
    h.loc[h["zone"] == "LZ_WEST", "rt_basis"] = [30.0] * 10 + [1.0] * 90
    bz = between_zone(d, h)
    wide = spread_wide(d)
    assert bz.loc["LZ_SOUTH", "corr_with_system"] == pytest.approx(wide["LZ_SOUTH"].corr(wide[SYSTEM]))
    assert bz.loc["LZ_WEST", "rt_basis_share_abs_gt_20"] == pytest.approx(0.10)
    assert bz.loc["LZ_WEST", "rt_basis_share_abs_gt_5"] == pytest.approx(0.10)
    assert SYSTEM not in bz.index


def test_representative_table_has_one_row_per_zone_and_system():
    d, h = fake_daily(), fake_hourly()
    t = representative_table(representative_spreads(d), between_zone(d, h))
    assert list(t.index) == [*ZONES, SYSTEM]
    assert np.isnan(t.loc[SYSTEM, "corr_with_system"])
    assert t.loc["LZ_NORTH", "onpeak_n_days"] == 10


def test_event_table_ranks_by_sd_with_date_tiebreak_and_skips_incomplete_days():
    d = fake_daily()
    north = d["zone"] == "LZ_NORTH"
    d.loc[north, "spread_sd"] = 1.0
    d.loc[north & (d["local_date"] == "2026-03-05"), "spread_sd"] = 99.0
    d.loc[north & (d["local_date"] == "2026-03-09"), ["spread_sd", "complete_day"]] = [500.0, False]
    ev = event_table(d, top=3).loc["LZ_NORTH"]
    assert ev.loc[1, "local_date"] == pd.Timestamp("2026-03-05")
    # the rest are tied at 1.0, so the earliest dates come first
    assert list(ev.loc[[2, 3], "local_date"]) == [pd.Timestamp("2026-03-02"), pd.Timestamp("2026-03-03")]


def test_gas_sensitivity_identical_when_prices_equal():
    d = fake_daily()
    d["imhr_da_mean"], d["imhr_rt_mean"] = d["da_mean"] / 3.0, d["rt_mean"] / 3.0
    g = gas_sensitivity(d)
    assert (g["days_gas_differs"] == 0).all()
    assert np.allclose(g["imhr_da_mean_lagged"], g["imhr_da_mean_same"])
    assert SYSTEM not in g.index


def test_temperature_quintiles_cover_every_model_day_and_flag_fern():
    from tables import by_temperature_quintile
    d = fake_daily(n=60)
    d["local_date"] = d["local_date"] - pd.Timedelta(days=40)   # Jan 21 - Mar 21, 2026: Fern inside
    q = by_temperature_quintile(d)
    assert set(q.index.get_level_values("quintile")) == {1, 2, 3, 4, 5}
    assert q.groupby("zone")["n_days"].sum().eq(60).all()
    assert q.groupby("zone")["n_fern_days"].sum().eq(10).all()
