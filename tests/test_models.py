"""Tests for run_models.py data preparation (model samples and response transforms)."""

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
