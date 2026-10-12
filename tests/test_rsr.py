"""Engine tests for rsr.py (Build Plan 6.4 exit checks).

Expected NCSS values are typed in from the printed output of NCSS Chapter 330
(Response Surface Regression, Example 1, Odor data) and from the Build Plan's
review of the search path. None of them is computed by the engine.
"""

import math

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from rsr import (HierarchyError, RSRError, Surface, check_hierarchy, cubic_extension, full_quadratic,
                 block_index, hac_cov, hac_lag, parse_term, removable, term_name)

# NCSS Odor dataset (Chapter 330, page 330-4), already coded to -1, 0, 1.
ODOR = pd.DataFrame({
    "Odor":   [66, 58, 65, -31, 39, 17, 7, -35, 43, -5, 43, -26, 49, -40, -22],
    "Temp":   [-1, -1, 0, 0, 1, 1, 0, 0, -1, -1, 0, 0, 1, 1, 0],
    "Ratio":  [-1, 0, -1, 0, -1, 0, 1, 0, 1, 0, -1, 0, 1, 0, 1],
    "Height": [0, -1, -1, 0, 0, -1, -1, 0, 0, 1, 1, 0, 0, 1, 1],
})
FACTORS = ["Temp", "Ratio", "Height"]


@pytest.fixture(scope="module")
def odor():
    return Surface(ODOR, "Odor", FACTORS, cov="ols")


# --- NCSS reference, search off ---------------------------------------------

def test_odor_r2_and_coefficients(odor):
    f = odor.full()
    assert f.r2 == pytest.approx(0.881989, abs=1e-6)
    expected = {  # coefficient, standard error (Estimation report)
        "Intercept": (-30.66667, None), "Temp": (-12.125, 7.947353), "Ratio": (-17, 7.947353),
        "Height": (-21.375, 7.947353), "Temp^2": (32.08333, 11.69819), "Ratio^2": (47.83333, 11.69819),
        "Height^2": (6.083333, 11.69819), "Temp*Ratio": (8.25, 11.23925), "Temp*Height": (1.5, 11.23925),
        "Ratio*Height": (-1.75, 11.23925),
    }
    tab = f.table()
    for name, (coef, se) in expected.items():
        assert tab.loc[name, "coef_coded"] == pytest.approx(coef, abs=1e-4)
        if se:
            assert tab.loc[name, "se"] == pytest.approx(se, abs=1e-5)
    assert tab.loc["Height", "p_approx"] == pytest.approx(0.043321, abs=1e-6)
    assert tab.loc["Ratio^2", "p_approx"] == pytest.approx(0.009457, abs=1e-6)


def test_odor_sequential_anova(odor):
    seq = odor.sequential_ss()
    for source, (df, ss, r2) in {
        "Regression": (9, 18881.98, 0.881989), "Linear": (3, 7143.25, 0.333666),
        "Quadratic": (3, 11445.23, 0.534614), "Lin x Lin": (3, 293.5, 0.013710),
        "Total Error": (5, 2526.417, 0.118011), "Lack of Fit": (3, 2485.75, 0.116111),
        "Pure Error": (2, 40.66667, 0.001900),
    }.items():
        assert seq.loc[source, "df"] == df
        assert seq.loc[source, "seq_ss"] == pytest.approx(ss, abs=0.01)
        assert seq.loc[source, "incremental_r2"] == pytest.approx(r2, abs=1e-6)
    assert seq.loc["Quadratic", "seq_ss"] == pytest.approx(11445.233333, abs=1e-5)


def test_odor_factor_anova(odor):
    ct = odor.conditional_tests()
    for factor, (ss, F, p, r2) in {
        "Temp": (5258.016, 2.60, 0.161334, 0.245605), "Ratio": (11044.6, 5.46, 0.045377, 0.515900),
        "Height": (3813.016, 1.89, 0.251025, 0.178108),
    }.items():
        row = ct.loc[f"factor: {factor}"]
        assert row["q"] == 4
        assert row["last_ss"] == pytest.approx(ss, abs=0.01)
        assert row["F"] == pytest.approx(F, abs=0.005)
        assert row["p_approx"] == pytest.approx(p, abs=1e-6)
        assert row["r2_loss"] == pytest.approx(r2, abs=1e-6)


def test_odor_last_r2_matches_reference(odor):
    last_r2 = {"Temp": 0.054938, "Ratio": 0.107995, "Height": 0.170733, "Temp^2": 0.177530,
               "Ratio^2": 0.394616, "Height^2": 0.006383, "Temp*Ratio": 0.012717,
               "Temp*Height": 0.000420, "Ratio*Height": 0.000572}
    full = odor.full()
    for name, r2 in last_r2.items():
        reduced = odor.fit([t for t in full.names if t != name], allow_nonhierarchical=True)
        assert full.r2 - reduced.r2 == pytest.approx(r2, abs=1e-6)
    candidates = set(removable(full.terms))
    assert parse_term("Temp", FACTORS) not in candidates
    assert parse_term("Temp*Height", FACTORS) in candidates


def test_odor_pure_error_lack_of_fit(odor):
    pe = odor.lack_of_fit()["pure_error"]
    assert pe["ss_lof"] == pytest.approx(2485.75, abs=1e-6) and pe["df_lof"] == 3
    assert pe["ss_pe"] == pytest.approx(40.66667, abs=1e-5) and pe["df_pe"] == 2
    assert pe["F"] == pytest.approx(40.75, abs=1e-6)
    assert pe["p"] == pytest.approx(0.024047, abs=1e-6)


def test_odor_canonical_analysis(odor):
    c = odor.canonical()
    assert c["shape"] == "minimum"
    np.testing.assert_allclose(sorted(c["eigenvalues"]), [6.04, 31.10, 48.86], atol=0.01)
    np.testing.assert_allclose(c["stationary_real"].to_numpy(), [0.1219125, 0.1995746, 1.770525], atol=1e-6)
    assert c["fitted_at_stationary"] == pytest.approx(-52.02463, abs=1e-5)
    assert c["inside_data"] is False  # Height = 1.77 lies outside the design


def test_odor_prediction_for_row_16(odor):
    # NCSS predicts 28.375 for the 16th row (Temp 1, Ratio 1, Height 0).
    assert odor.predict(odor.full(), np.array([[1, 1, 0]]))[0] == pytest.approx(28.375, abs=1e-9)


# --- NCSS search on (path from the Build Plan review) -------------------------

def test_odor_search_path(odor):
    out = odor.search()
    path = out["path"]
    assert list(path["term"]) == ["Temp*Height", "Ratio*Height", "Height^2", "Temp*Ratio"]
    assert list(path["removed"]) == [True, True, True, False]
    np.testing.assert_allclose(path["r2_loss"], [0.00042, 0.00057, 0.0064, 0.0127], atol=5e-5)
    assert out["final_terms"] == ["Temp", "Ratio", "Height", "Temp^2", "Ratio^2", "Temp*Ratio"]


# --- terms and hierarchy ------------------------------------------------------------

def test_term_parsing_round_trip():
    labels = ["T", "L"]
    for name in ("T", "L", "T^2", "T*L", "T^2*L", "L^3"):
        assert term_name(parse_term(name, labels), labels) == name
    assert parse_term("L*T", labels) == (1, 1)
    with pytest.raises(ValueError):
        parse_term("X", labels)


def test_standard_term_sets():
    assert [term_name(t, ["T", "L"]) for t in full_quadratic(2)] == ["T", "L", "T^2", "L^2", "T*L"]
    assert len(full_quadratic(3)) == 9
    assert [term_name(t, ["T", "L"]) for t in cubic_extension(2)] == ["T^3", "L^3", "T^2*L", "T*L^2"]
    assert len(cubic_extension(3)) == 9


def test_hierarchy_enforced():
    labels = ["T", "L"]
    with pytest.raises(HierarchyError, match="T\\^2 needs T"):
        check_hierarchy([parse_term("T^2", labels)], labels)
    with pytest.raises(HierarchyError, match="T\\*L needs L"):
        check_hierarchy([parse_term(t, labels) for t in ("T", "T*L")], labels)
    check_hierarchy([parse_term(t, labels) for t in ("T", "L", "T*L")], labels)
    terms = [parse_term(t, labels) for t in ("T", "L", "T^2", "T*L")]
    assert {term_name(t, labels) for t in removable(terms)} == {"T^2", "T*L"}  # T and L have children


def test_fit_refuses_nonhierarchical_unless_overridden():
    s = known_surface(2)
    with pytest.raises(HierarchyError):
        s.fit(["T^2", "L"])
    assert s.fit(["T^2", "L"], allow_nonhierarchical=True).r2 < 1


def test_too_few_distinct_values():
    df = pd.DataFrame({"y": [1.0, 2, 3, 4, 5, 6], "a": [0, 1, 0, 1, 0, 1], "b": [1.0, 2, 3, 4, 5, 6]})
    s = Surface(df, "y", ["a", "b"], labels=["A", "B"], cov="ols")
    with pytest.raises(RSRError, match="distinct values"):
        s.fit(["A", "B", "A^2"])


# --- noise-free known surfaces --------------------------------------------------------

def known_surface(k, n=40, seed=1, coefs=None, noise=None):
    rng = np.random.default_rng(seed)
    X = rng.uniform([10, 0.3, 5][:k], [100, 1.2, 25][:k], size=(n, k))
    labels = ["T", "L", "W"][:k]
    from rsr import Coding
    Z = Coding.from_data(X).code(X)
    terms = full_quadratic(k)
    coefs = coefs if coefs is not None else rng.uniform(-3, 3, size=len(terms) + 1)
    y = coefs[0] + sum(c * np.prod(Z ** np.array(t), axis=1) for c, t in zip(coefs[1:], terms))
    if noise is not None:
        y = y + noise
    df = pd.DataFrame(X, columns=["temp", "load", "wind"][:k])
    df["y"] = y
    df["date"] = pd.date_range("2025-10-01", periods=n)
    s = Surface(df, "y", list(df.columns[:k]), labels=labels, date="date", cov="ols")
    return s


@pytest.mark.parametrize("k", [2, 3])
def test_noise_free_surface_recovers_coefficients(k):
    rng = np.random.default_rng(7)
    coefs = rng.uniform(-3, 3, size=len(full_quadratic(k)) + 1)
    s = known_surface(k, coefs=coefs)
    f = s.full()
    np.testing.assert_allclose(f.params.to_numpy(), coefs, atol=1e-8)
    assert f.r2 == pytest.approx(1, abs=1e-10)


def test_coding_invariance_of_fit_and_search():
    s = known_surface(2, noise=np.random.default_rng(3).normal(0, 0.5, 40))
    df = s.data.copy()
    df["temp"] = df["temp"] * 1.8 + 32  # change units of one factor
    s2 = Surface(df, "y", ["temp", "load"], labels=["T", "L"], date="date", cov="ols")
    for terms in (None, ["T", "L", "T^2", "T*L"]):
        np.testing.assert_allclose(s.fit(terms).fitted.to_numpy(), s2.fit(terms).fitted.to_numpy(), atol=1e-9)
    p1, p2 = s.search()["path"], s2.search()["path"]
    assert list(p1["term"]) == list(p2["term"])
    np.testing.assert_allclose(p1["r2_loss"], p2["r2_loss"], atol=1e-10)


# --- HAC wiring -------------------------------------------------------------------------

def ar1(n, rho, seed):
    rng = np.random.default_rng(seed)
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = rho * e[i - 1] + rng.normal()
    return e


def test_hac_lag_rule():
    assert hac_lag(365) == 25 and hac_lag(261) == 22


def test_hac_wald_matches_hand_calculation_and_statsmodels():
    n = 120
    s0 = known_surface(2, n=n, noise=ar1(n, 0.6, 11))
    s = Surface(s0.data, "y", ["temp", "load"], labels=["T", "L"], date="date")  # HAC
    f = s.full()
    assert f.cov_type == "hac" and f.hac_lags == hac_lag(n)
    V = f.cov.to_numpy()
    assert V.shape == (6, 6) and list(f.cov.index) == list(f.params.index)
    np.testing.assert_allclose(V, V.T, atol=1e-12)
    assert np.linalg.eigvalsh(V).min() > -1e-10
    names = ["T^2", "L^2", "T*L"]
    b = f.params[names].to_numpy()
    W_hand = float(b @ np.linalg.inv(f.cov.loc[names, names].to_numpy()) @ b)
    w = f.wald(names)
    assert w["wald"] == pytest.approx(W_hand, rel=1e-10)
    R = np.zeros((3, 6))
    for i, nm in enumerate(names):
        R[i, list(f.params.index).index(nm)] = 1
    assert w["F"] == pytest.approx(float(f.sm_result.f_test(R).fvalue), rel=1e-8)
    assert w["q"] < f.df_resid
    ols = sm.OLS(s.y, f.X).fit()
    np.testing.assert_allclose(f.params.to_numpy(), ols.params, atol=1e-10)  # HAC changes errors only


# --- tools for choosing terms ----------------------------------------------------------------


def test_sequential_ss_with_block_adds_up():
    s0 = known_surface(2, n=80, noise=np.random.default_rng(9).normal(0, 1, 80))
    df = s0.data.copy()
    df["blk"] = (np.arange(80) >= 20).astype(float)
    df["y"] = df["y"] + 2 * df["blk"]
    s = Surface(df, "y", ["temp", "load"], labels=["T", "L"], block=["blk"], date="date", cov="ols")
    seq = s.sequential_ss()
    assert list(seq.index[:5]) == ["Regression", "Block", "Linear", "Quadratic", "Lin x Lin"]
    parts = seq.loc[["Block", "Linear", "Quadratic", "Lin x Lin"], "seq_ss"].sum()
    assert parts == pytest.approx(seq.loc["Regression", "seq_ss"], rel=1e-10)
    assert "blk" not in s.full().names and "blk" in s.full().params.index  # block is not a term


def test_cubic_lack_of_fit_detects_a_cubic():
    n = 200
    rng = np.random.default_rng(2)
    X = rng.uniform(-1, 1, size=(n, 2))
    df = pd.DataFrame(X, columns=["a", "b"])
    df["y"] = 1 + df["a"] + 3 * df["a"] ** 3 + rng.normal(0, 0.1, n)
    s = Surface(df, "y", ["a", "b"], labels=["A", "B"], cov="ols")
    lof = s.lack_of_fit()
    assert lof["cubic"]["q"] == 4 and lof["cubic"]["p"] < 1e-6
    df["y"] = 1 + df["a"] + df["a"] ** 2 + rng.normal(0, 0.1, n)
    s2 = Surface(df, "y", ["a", "b"], labels=["A", "B"], cov="ols")
    assert s2.lack_of_fit()["cubic"]["p"] > 0.001


def test_ridge_is_detected():
    coefs = np.array([1.0, 0.5, 0.3, 2.0, 0.0, 0.0])  # no L^2, no T*L: zero eigenvalue along L
    s = known_surface(2, coefs=coefs)
    c = s.canonical()
    assert c["shape"] == "ridge" and "stationary_real" not in c


def test_inside_data_uses_distance_to_observed_days():
    s = known_surface(2)
    obs = s.data[["temp", "load"]].iloc[[0]]
    assert s.inside_data(obs)["inside"].iloc[0]
    far = pd.DataFrame({"temp": [s.coding.lo[0]], "load": [s.coding.hi[1] * 3]})
    assert not s.inside_data(far)["inside"].iloc[0]


def test_marginal_effects_match_finite_differences():
    s = known_surface(2, noise=np.random.default_rng(4).normal(0, 0.3, 40))
    f = s.full()
    at = s.data[["temp", "load"]].iloc[[3, 10]].reset_index(drop=True)
    me = s.marginal_effects(f, at)
    for _, row in me.iterrows():
        p = at.iloc[[int(row["point"])]].copy()
        h = 1e-5 * (s.coding.hi - s.coding.lo)[["temp", "load"].index(row["factor"])]
        up, dn = p.copy(), p.copy()
        up[row["factor"]] += h
        dn[row["factor"]] -= h
        fd = (s.predict(f, up)[0] - s.predict(f, dn)[0]) / (2 * h)
        assert row["slope"] == pytest.approx(fd, rel=1e-6, abs=1e-8)
        assert row["low"] < row["slope"] < row["high"]


def test_fitted_range_reports_observed_extremes():
    s = known_surface(2)
    fr = s.fitted_range()
    f = s.full()
    assert fr.loc["lowest", "fitted"] == pytest.approx(f.fitted.min())
    assert fr.loc["highest", "date"] == f.fitted.idxmax()


def test_diagnostics_run():
    s = known_surface(2, noise=np.random.default_rng(8).normal(0, 1, 40))
    d = s.diagnostics()
    assert d["rank"] == d["n_params"] == 6
    assert set(d["vif"]) == {"T", "L", "T^2", "L^2", "T*L"}
    assert len(d["top_cooks"]) == 5


# --- seeded noisy smoke test (a diagnostic, not a gate: Build Plan 6.4) ------------------------

def test_noisy_smoke_365_days():
    n = 365
    coefs = np.array([1.0, 0.8, 1.2, 0.6, -0.4, 0.5])
    s0 = known_surface(2, n=n, coefs=coefs, noise=ar1(n, 0.5, 21) * 0.5)
    s = Surface(s0.data, "y", ["temp", "load"], labels=["T", "L"], date="date")
    f = s.full()
    se = np.sqrt(np.diag(f.cov.to_numpy()))
    assert (np.abs(f.params.to_numpy() - coefs) < 4 * se).all()
    assert f.hac_lags == 25


# --- block bootstrap (owner decision Oct 9, 2026) ---------------------------------------

def hac_surface(n=150, rho=0.5, seed=11, coefs=None):
    s0 = known_surface(2, n=n, coefs=coefs, noise=ar1(n, rho, seed))
    return Surface(s0.data, "y", ["temp", "load"], labels=["T", "L"], date="date")


def test_fast_hac_cov_matches_statsmodels():
    s = hac_surface()
    f = s.full()
    V = hac_cov(f.X.to_numpy(), f.resid.to_numpy(), s.hac_lags)
    np.testing.assert_allclose(V, f.cov.to_numpy(), rtol=1e-9, atol=1e-12)


def test_fast_wald_matches_fit_wald_for_hac_and_ols():
    for cov in ("hac", "ols"):
        s0 = hac_surface()
        s = Surface(s0.data, "y", ["temp", "load"], labels=["T", "L"], date="date", cov=cov)
        f = s.full()
        names = ["T^2", "T*L"]
        cols = [list(f.X.columns).index(nm) for nm in names]
        assert s._wald_F(f.X.to_numpy(), s.y, cols) == pytest.approx(f.wald(names)["F"], rel=1e-9)


def test_block_index_is_circular_consecutive_blocks():
    idx = block_index(10, 4, np.random.default_rng(0))
    assert len(idx) == 10 and idx.min() >= 0 and idx.max() < 10
    for b in range(0, 10, 4):
        blk = idx[b:b + 4]
        assert all((blk[i + 1] - blk[i]) % 10 == 1 for i in range(len(blk) - 1))


def test_bootstrap_test_statistic_seed_and_power():
    s = hac_surface()
    lof = s.lack_of_fit()["cubic"]
    r = s.lack_of_fit_boot(B=99, block_len=10, seed=3)
    assert r["F"] == pytest.approx(lof["F"], rel=1e-9) and r["q"] == 4
    assert r["p_boot"] == s.lack_of_fit_boot(B=99, block_len=10, seed=3)["p_boot"]
    assert 0 < r["p_boot"] <= 1
    # a real quadratic term (T^2 = 3, noise SD about 1) is detected: minimum attainable p
    s = hac_surface(coefs=np.array([1.0, 0.5, 0.5, 3.0, 0.0, 0.0]))
    strong = s.bootstrap_test(["T", "L", "L^2", "T*L"], ["T^2"], B=99, block_len=10)
    assert strong["p_boot"] == pytest.approx(1 / 100)


def test_bootstrap_lack_of_fit_detects_a_cubic():
    n = 200
    rng = np.random.default_rng(2)
    df = pd.DataFrame(rng.uniform(-1, 1, size=(n, 2)), columns=["a", "b"])
    df["y"] = 1 + df["a"] + 3 * df["a"] ** 3 + rng.normal(0, 0.1, n)
    s = Surface(df, "y", ["a", "b"], labels=["A", "B"])
    assert s.lack_of_fit_boot(B=99, block_len=10)["p_boot"] == pytest.approx(1 / 100)


def test_conditional_tests_boot_matches_asymptotic_statistics():
    s = hac_surface()
    a, b = s.conditional_tests(), s.conditional_tests_boot(B=49, block_len=10)
    assert list(a.index) == list(b.index)
    np.testing.assert_allclose(a["F"], b["F"], rtol=1e-9)
    np.testing.assert_allclose(a["p_approx"], b["p_asymptotic"], rtol=1e-6)


def test_pairs_bootstrap_on_noise_free_surface_returns_the_truth():
    rng = np.random.default_rng(7)
    coefs = rng.uniform(-3, 3, size=6)
    s = known_surface(2, n=60, coefs=coefs)
    f = s.full()
    draws = s.bootstrap_pairs(f, B=30, block_len=8)
    ok = draws.dropna()
    assert len(ok) > 20
    np.testing.assert_allclose(ok.to_numpy(), np.tile(coefs, (len(ok), 1)), atol=1e-8)
    me = s.marginal_effects(f, s.data[s.factors].iloc[[0, 5]], draws=draws)
    np.testing.assert_allclose(me["boot_low"], me["slope"], atol=1e-8)
    np.testing.assert_allclose(me["boot_high"], me["slope"], atol=1e-8)
    t = s.boot_table(f, draws)
    np.testing.assert_allclose(t["boot_se"], 0, atol=1e-8)
