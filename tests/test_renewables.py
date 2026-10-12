"""Renewable integration: preserve baseline, correct aggregation and fold isolation."""
import numpy as np
import pandas as pd
import pytest

from build import align, make_features, prep_renewable, BuildError
from tests.test_build import synth, FALL, SPRING
from tests.test_models import fake_daily
import run_models as rm
from renewable_validation import heldout


def with_renewables(raw):
    for source, amplitude in [('wind', 1100), ('solar', 2000)]:
        times = raw['load'].interval_start_utc
        raw[f'{source}_actual'] = pd.DataFrame({'interval_start_utc': times,
            'interval_end_utc': times+pd.Timedelta(hours=1), 'publish_time_utc': times+pd.Timedelta(days=2),
            'gen_system_wide': amplitude*(1+np.sin(np.arange(len(times))*.3))})
    return raw


@pytest.mark.parametrize('window', [FALL, SPRING])
def test_net_daily_max_and_missing_hour_do_not_change_baseline(window):
    raw = synth(*window)
    base, _ = align(raw, *window)
    bh, bd, _ = make_features(base)
    raw = with_renewables(raw)
    raw['wind_actual'] = raw['wind_actual'].drop(index=25)
    panel, _ = align(raw, *window)
    h, d, refs = make_features(panel)
    # Every pre-existing scientific value stays unchanged; newly introduced net columns may differ.
    old = [c for c in bd if c not in ['n_net_load_hours', 'net_load_complete', 'sys_net_load_peak', 'sys_net_load_peak_norm']]
    pd.testing.assert_frame_equal(bd[old], d[old])
    sys = d[d.zone == 'SYSTEM'].set_index('local_date')
    hourly = h[h.zone == 'LZ_NORTH']
    for day, g in hourly.groupby('local_date'):
        vals = g.sys_load-g.wind_gen-g.solar_gen
        if vals.notna().all():
            assert sys.loc[day, 'sys_net_load_peak'] == pytest.approx(vals.max())
            assert sys.loc[day, 'sys_net_load_peak_norm'] == pytest.approx(vals.max()/refs['sys_net_load_reference_mw'])
        else:
            assert pd.isna(sys.loc[day, 'sys_net_load_peak_norm'])
            assert sys.loc[day, 'complete_day']


@pytest.mark.parametrize('failure', ['duplicate', 'early', 'infinite'])
def test_renewable_bad_rows_rejected(failure):
    raw = with_renewables(synth(*FALL))['wind_actual']
    if failure == 'duplicate': raw = pd.concat([raw, raw.iloc[[0]]])
    if failure == 'early': raw.loc[0, 'publish_time_utc'] = raw.loc[0, 'interval_start_utc']
    if failure == 'infinite': raw.loc[0, 'gen_system_wide'] = np.inf
    with pytest.raises(BuildError): prep_renewable(raw, 'wind')


def fake_system():
    d = fake_daily(n=120, zone='SYSTEM')
    d['sys_temp'] = d.temp_mean
    d['sys_load_peak_norm'] = d.load_peak_norm
    d['sys_net_load_peak_norm'] = np.random.default_rng(4).uniform(.3, 1, len(d))
    d['spread_sd'] = np.exp(d.log_spread_sd)
    return d


def test_replacement_is_matched_and_not_an_added_factor_test(monkeypatch):
    monkeypatch.setitem(rm.INF, 'bootstrap_B', 9)
    d = fake_system(); d.loc[0, 'sys_net_load_peak_norm'] = np.nan
    refs = {'sys_load_reference_mw': 90000., 'sys_net_load_reference_mw': 60000.}
    result = rm.run_one(d, 'vol_sys_net', 'SYSTEM', refs)
    assert result['row']['n'] == result['row']['n_base_same_days'] == 119
    assert pd.isna(result['row']['added_factor_p_boot'])
    assert result['row']['added_factor_hold'] == '{}'
    assert rm.make_surface(d, 'vol_sys', 'SYSTEM')[0].n == 120
    s, _ = rm.make_surface(d, 'vol_sys_net', 'SYSTEM')
    fit = s.full()
    me = result['me'].query("factor == 'sys_net_load_peak_norm'")
    np.testing.assert_allclose(me.slope_dollars, me.fitted_dollars*me.slope_response*1000/60000)


def test_missing_renewables_skip_only_new_models():
    d = fake_system(); d['sys_net_load_peak_norm'] = np.nan
    assert rm.make_surface(d, 'vol_sys', 'SYSTEM')[0].n == len(d)
    with pytest.raises(rm.ModelUnavailable): rm.make_surface(d, 'vol_sys_net', 'SYSTEM')


def test_holdout_outcomes_do_not_leak_into_predictions():
    d = fake_system()
    # Multiple pre-regime months ensure every omitted month leaves both blocks.
    d['local_date'] = pd.date_range('2025-10-01', periods=len(d))
    d['post_rtcb'] = (d.local_date >= '2025-12-05').astype(int)
    p1 = heldout(d, 'net', 'level', 'month')
    jan = d.local_date.dt.month == 1
    d.loc[jan, 'spread_mean'] += 10000
    p2 = heldout(d, 'net', 'level', 'month')
    np.testing.assert_allclose(p1.loc[jan, 'predicted_dollars'], p2.loc[jan, 'predicted_dollars'])
