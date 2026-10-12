"""Tests for run_models.py data preparation (model samples and response transforms)."""

import json

import numpy as np
import pandas as pd
import pytest

import run_models
from rsr import HierarchyError
from run_models import ModelError, make_surface, prepare_model_data, selected_terms


def fake_daily(n=60, zone="LZ_NORTH", seed=0):
    rng = np.random.default_rng(seed)
    days = pd.date_range("2025-12-01", periods=n)
    d = pd.DataFrame({
        "zone": zone, "local_date": days, "weekday": days.weekday,
        "complete_day": True, "factors_complete": True,
        "spread_mean": rng.normal(1, 5, n), "spread_onpeak_mean": rng.normal(1, 5, n),
        "log_spread_sd": rng.normal(2.5, 0.5, n),
        "temp_mean": rng.uniform(30, 100, n), "load_peak_norm": rng.uniform(0.4, 1.05, n),
        "post_rtcb": (days >= "2025-12-05").astype(int),
    })
    d.loc[d["weekday"] >= 5, "spread_onpeak_mean"] = np.nan
    return d


def test_incomplete_days_are_excluded():
    d = fake_daily()
    d.loc[3, "complete_day"] = False
    d.loc[7, "factors_complete"] = False
    out, info = prepare_model_data(d, "vol_zone", "LZ_NORTH")
    assert info["n"] == 58 and info["calendar_days"] == 60
    assert not out["local_date"].isin(d.loc[[3, 7], "local_date"]).any()


def test_level_response_is_asinh_of_median_mad_standardized():
    d = fake_daily()
    out, info = prepare_model_data(d, "lvl_zone", "LZ_NORTH")
    raw = d["spread_mean"]
    med = raw.median()
    mad = (raw - med).abs().median()
    assert info["median"] == pytest.approx(med) and info["mad"] == pytest.approx(mad)
    expected = np.arcsinh((out["spread_mean"] - med) / mad)
    np.testing.assert_allclose(out["y"], expected)


def test_volatility_response_is_log_sd_unchanged():
    out, _ = prepare_model_data(fake_daily(), "vol_zone", "LZ_NORTH")
    np.testing.assert_allclose(out["y"], out["log_spread_sd"])


def test_onpeak_model_uses_weekdays_only():
    out, info = prepare_model_data(fake_daily(), "lvl_zone_onpeak", "LZ_NORTH")
    assert (out["weekday"] < 5).all()
    assert info["n"] == int((fake_daily()["weekday"] < 5).sum())


def test_zero_mad_stops():
    d = fake_daily()
    d["spread_mean"] = 2.0
    d.loc[0, "spread_mean"] = 3.0
    with pytest.raises(ModelError, match="MAD"):
        prepare_model_data(d, "lvl_zone", "LZ_NORTH")


def test_wrong_zone_for_model_stops():
    with pytest.raises(ModelError):
        prepare_model_data(fake_daily(), "lvl_sys", "LZ_NORTH")


def test_make_surface_sorts_by_date_and_uses_block():
    d = fake_daily().sample(frac=1, random_state=1)
    s, info = make_surface(d, "vol_zone", "LZ_NORTH")
    assert s.index.is_monotonic_increasing
    assert s.block == ["post_rtcb"] and info["hac_lags"] == s.hac_lags
    assert "post_rtcb" in s.full().params.index


def test_selected_terms_must_be_hierarchical(monkeypatch):
    monkeypatch.setitem(run_models.CONFIG["selected_terms"], "vol_zone|LZ_NORTH",
                        {"terms": ["T", "L", "T^2", "T*L"], "why": "test"})
    assert selected_terms("vol_zone", "LZ_NORTH") == ["T", "L", "T^2", "T*L"]
    monkeypatch.setitem(run_models.CONFIG["selected_terms"], "vol_zone|LZ_WEST",
                        {"terms": ["T", "T*L"], "why": "test"})
    with pytest.raises(HierarchyError):
        selected_terms("vol_zone", "LZ_WEST")
    monkeypatch.delitem(run_models.CONFIG["selected_terms"], "vol_zone|LZ_SOUTH", raising=False)
    assert selected_terms("vol_zone", "LZ_SOUTH") is None


# --- runner ----------------------------------------------------------------------------

from run_models import effect_days, marginal_effects_dollars, run_one, to_dollars


def test_to_dollars_volatility_is_smeared_exp_and_its_own_derivative():
    e = np.array([-0.5, 0.0, 0.5])
    lvl, d = to_dollars(np.array([1.0, 2.0]), e, {"transform": "none"})
    np.testing.assert_allclose(lvl, np.exp([1.0, 2.0]) * np.exp(e).mean())
    np.testing.assert_allclose(d, lvl)


def test_to_dollars_level_matches_numerical_derivative():
    e = np.array([-1.0, 0.2, 0.9])
    info = {"transform": "asinh_mad", "median": 1.5, "mad": 4.0}
    y = np.array([0.3])
    lvl, d = to_dollars(y, e, info)
    assert lvl[0] == pytest.approx(1.5 + 4.0 * np.sinh(0.3 + e).mean())
    h = 1e-6
    num = (to_dollars(y + h, e, info)[0] - to_dollars(y - h, e, info)[0]) / (2 * h)
    assert d[0] == pytest.approx(num[0], rel=1e-6)


def test_marginal_effects_match_engine_slopes_and_per_gw_conversion():
    s, info = make_surface(fake_daily(n=120), "vol_zone", "LZ_NORTH")
    full = s.full()
    draws = s.bootstrap_pairs(full, B=20, block_len=10)
    me = marginal_effects_dollars(s, full, draws, info, ref_mw=2000.0)
    days = effect_days(s)
    assert list(days["day"]) == ["cold", "mild", "hot"]
    eng = s.marginal_effects(full, days[s.factors], block_values={"post_rtcb": 1})
    np.testing.assert_allclose(me["slope_response"].to_numpy(), eng["slope"].to_numpy(), rtol=1e-6, atol=1e-9)
    load = me[me["factor"] == "load_peak_norm"]
    np.testing.assert_allclose(load["slope_dollars"], load["fitted_dollars"] * load["slope_response"] * 1000 / 2000)


def test_run_one_produces_every_table(monkeypatch):
    monkeypatch.setitem(run_models.INF, "bootstrap_B", 29)
    d = fake_daily(n=120)
    refs = {"load_reference_mw": {"LZ_NORTH": 30000.0}, "sys_load_reference_mw": 90000.0}
    res = run_one(d, "vol_zone", "LZ_NORTH", refs)
    r = res["row"]
    assert r["n"] == 120 and r["terms_source"] in ("owner", "NCSS search (owner choice pending)")
    assert 0 < r["lof_p_boot"] <= 1
    assert r["fern_days_in_sample"] == 10   # fake days run Dec 1 - Mar 30, so Jan 23 - Feb 1 is inside
    assert set(res["cond"]["test"]) >= {"factor: T", "factor: L"}
    assert {"p_hac", "p_boot"} <= set(res["cond"].columns)
    assert len(res["me"]) == 6 and res["me"]["dollar_unit"].nunique() == 2


def test_smearing_check_bins_and_pooled_value():
    from run_models import smearing_check
    s, info = make_surface(fake_daily(n=120), "vol_zone", "LZ_NORTH")
    f = s.full()
    sm = smearing_check(s, f, info)
    assert len(sm) == 5 + 5 + 2   # temperature and load quintiles, post_rtcb 0/1
    assert sm.groupby("by")["n"].sum().eq(s.n).all()
    row = sm.iloc[0]
    y0 = np.median(f.fitted.to_numpy()[pd.qcut(s.Xreal[:, 0], 5, labels=False) == 0])
    assert row["dollars_pooled_smear"] == pytest.approx(float(to_dollars(y0, f.resid.to_numpy(), info)[0]))
    assert row["dollars_unsmeared"] == pytest.approx(np.exp(y0))


# --- optional phase: load forecast error as a third factor -----------------------------

from run_models import ModelUnavailable, base_comparison, dollar_units


def fe_daily(n=120, seed=1):
    d = fake_daily(n=n, seed=seed)
    rng = np.random.default_rng(seed + 10)
    d["fe_pct_rms"] = rng.gamma(4, 0.8, n)
    d["fe_pct_mean"] = rng.normal(0, 2, n)
    return d


def test_fe_model_skipped_when_forecast_not_loaded():
    with pytest.raises(ModelUnavailable):
        prepare_model_data(fake_daily(), "vol_zone_fe", "LZ_NORTH")          # column absent
    d = fe_daily()
    d["fe_pct_rms"] = np.nan
    with pytest.raises(ModelUnavailable):
        prepare_model_data(d, "vol_zone_fe", "LZ_NORTH")                     # column empty


def test_dollar_units_by_factor_label():
    u = dollar_units(["T", "L", "F"], ref_mw=2000.0)
    assert u[0] == ("$/MWh per °F", 1.0) and u[1] == ("$/MWh per GW", 0.5)
    assert u[2] == ("$/MWh per percentage point", 1.0)


def test_base_comparison_uses_the_same_days():
    d = fe_daily()
    s, _ = make_surface(d, "vol_zone_fe", "LZ_NORTH")
    full = s.full()
    ct = pd.DataFrame({"p_boot": [0.25]}, index=["factor: F"])
    c = base_comparison(d, "vol_zone_fe", "LZ_NORTH", s, full, ct)
    base, _ = make_surface(d, "vol_zone", "LZ_NORTH")
    assert c["base_model"] == "vol_zone" and c["n_base_same_days"] == s.n == 120
    assert c["r2_base_same_days"] == pytest.approx(base.full().r2)
    assert c["r2_gain_vs_base"] >= 0                       # the two-factor quadratic is nested
    assert c["added_factor_p_boot"] == 0.25 and -1 <= c["surface_corr_vs_base"] <= 1
    assert json.loads(c["added_factor_hold"]) == {"fe_pct_rms": pytest.approx(np.median(d["fe_pct_rms"]), abs=1e-4)}


def test_run_one_three_factor_model(monkeypatch):
    monkeypatch.setitem(run_models.INF, "bootstrap_B", 29)
    refs = {"load_reference_mw": {"LZ_NORTH": 30000.0}, "sys_load_reference_mw": 90000.0}
    res = run_one(fe_daily(), "vol_zone_fe", "LZ_NORTH", refs)
    assert "factor: F" in set(res["cond"]["test"]) and len(res["me"]) == 9
    assert res["row"]["base_model"] == "vol_zone" and 0 < res["row"]["added_factor_p_boot"] <= 1


@pytest.mark.parametrize('model', ['vol_sys', 'vol_zone', 'lvl_zone'])
def test_run_selected_models_has_only_available_figures(tmp_path, monkeypatch, model):
    zone = 'SYSTEM' if model == 'vol_sys' else 'LZ_NORTH'
    d = fake_daily(n=120, zone=zone)
    d['sys_temp'], d['sys_load_peak_norm'] = d['temp_mean'], d['load_peak_norm']
    monkeypatch.setattr(run_models, 'load_daily', lambda: d)
    monkeypatch.setattr(run_models, 'zones_for', lambda m: [zone])
    monkeypatch.setattr(run_models, 'load_references', lambda: {
        'load_reference_mw': {zone: 30000.0}, 'sys_load_reference_mw': 90000.0})
    monkeypatch.setitem(run_models.INF, 'bootstrap_B', 9)
    tables = run_models.run(tmp_path, [model])
    assert tables['results_table']['model'].tolist() == [model]
    expected = [] if model == 'vol_sys' else [f'diagnostics_{model}.png']
    assert sorted(p.name for p in (tmp_path / 'figures').glob('*.png')) == expected


def test_run_only_unavailable_models_is_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(run_models, 'load_daily', lambda: fake_daily())
    monkeypatch.setattr(run_models, 'load_references', lambda: {})
    with pytest.raises(ModelUnavailable, match='No requested models'):
        run_models.run(tmp_path, ['vol_zone_fe'])
    assert not list(tmp_path.glob('*.csv'))
