"""Does the block bootstrap fix what hac_size_check.py found?

Same designs and noise types as hac_size_check.py (real factor values and
post_rtcb block, synthetic responses), for block lengths 10, 25 and 50 days.

1. Size: the response is pure noise, so every tested coefficient is zero. A
   correct 5% test rejects about 5%. Tests: cubic lack of fit (q = 4), the load
   factor (every term with L, q = 3) and the T*L term (q = 1), each with the
   bootstrap p-value and, for comparison, the asymptotic HAC p-value.
2. Coverage: the response is the real full-quadratic fit plus noise, so the true
   coefficients are known. A correct 95% interval covers them about 95% of the
   time. Pairs-bootstrap percentile intervals against HAC t intervals.

    .venv/bin/python notebooks/bootstrap_check.py [--reps 300] [--B 199]

Writes outputs/hac_size/bootstrap_size.csv and bootstrap_coverage.csv.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from scipy import stats

from config import CONFIG
from rsr import Surface, percentile_interval
from run_models import BLOCK, MODELS, load_daily, make_surface, prepare_model_data

from hac_size_check import DESIGNS, LEVEL_TWIN, ar1, block_resample

OUT = CONFIG["paths"]["outputs"] / "hac_size"
BLOCK_LENS = (10, 25, 50)
ALPHA = 0.05
TESTS = {"lack_of_fit": None, "factor_L": ["L", "L^2", "T*L"], "term_T*L": ["T*L"]}


def surface(d, spec, y):
    return Surface(d.assign(y=y), response="y", factors=spec["factors"], labels=spec["labels"], block=BLOCK,
                   date="local_date", settings=CONFIG["rsr"])


def size_rep(d, spec, y, B, seed) -> dict:
    s = surface(d, spec, y)
    full = s.full()
    out = {}
    for L in BLOCK_LENS:
        for name, test in TESTS.items():
            if test is None:
                r = s.lack_of_fit_boot(full.terms, B=B, block_len=L, seed=seed)
            else:
                rest = [t for t in full.names if t not in test]
                r = s.bootstrap_test(rest, test, B=B, block_len=L, seed=seed)
            out[f"{name}|boot{L}"] = r["p_boot"]
            out[f"{name}|hac"] = r["p_asymptotic"]
    return out


def coverage_rep(d, spec, beta, y, B, seed) -> dict:
    s = surface(d, spec, y)
    f = s.full()
    names = f.names
    tq = stats.t.ppf(0.975, f.df_resid)
    se = np.sqrt(np.diag(f.cov.loc[names, names].to_numpy()))
    b = f.params[names].to_numpy()
    true = beta[names].to_numpy()
    out = {f"{nm}|hac": float(abs(bi - ti) <= tq * si) for nm, bi, ti, si in zip(names, b, true, se)}
    for L in BLOCK_LENS:
        draws = s.bootstrap_pairs(f, B=B, block_len=L, seed=seed)[names].to_numpy()
        lo, hi = percentile_interval(draws)
        out.update({f"{nm}|boot{L}": float(l <= t <= h) for nm, l, h, t in zip(names, lo, hi, true)})
    return out


def run(reps: int, B: int, seed: int = 20261009) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    daily = load_daily()
    size_rows, cov_rows = [], []
    for model, zone in DESIGNS:
        spec = MODELS[model]
        d, _ = prepare_model_data(daily, model, zone)
        n = len(d)
        real = make_surface(daily, model, zone)[0].full()
        beta, fitted = real.params, real.fitted.to_numpy()
        vol_resid = real.resid.to_numpy()
        lvl_resid = make_surface(daily, LEVEL_TWIN[model], zone)[0].full().resid.to_numpy()
        phi, sd = float(pd.Series(vol_resid).autocorr(1)), float(vol_resid.std())
        noises = {
            "iid": lambda: rng.normal(scale=sd, size=n),
            "ar1": lambda: ar1(n, phi, rng) * sd * np.sqrt(1 - phi ** 2),
            "block_vol": lambda: block_resample(vol_resid, n, rng),
            "block_lvl": lambda: block_resample(lvl_resid, n, rng),
        }
        for noise, draw in noises.items():
            sz = pd.DataFrame([size_rep(d, spec, draw(), B, i) for i in range(reps)])
            cv = pd.DataFrame([coverage_rep(d, spec, beta, fitted + draw(), B, i) for i in range(reps)])
            size_rows.append({"model": model, "zone": zone, "noise": noise, "reps": reps, "B": B,
                              **(sz < ALPHA).mean().to_dict()})
            cov_rows.append({"model": model, "zone": zone, "noise": noise, "reps": reps, "B": B,
                             **cv.mean().to_dict()})
            print(f"{model:9s} {zone:10s} {noise:9s} | size LoF hac {size_rows[-1]['lack_of_fit|hac']:.3f} "
                  + " ".join(f"b{L} {size_rows[-1][f'lack_of_fit|boot{L}']:.3f}" for L in BLOCK_LENS)
                  + " | coverage hac "
                  + f"{cv.filter(like='|hac').to_numpy().mean():.3f} "
                  + " ".join(f"b{L} {cv.filter(like=f'|boot{L}').to_numpy().mean():.3f}" for L in BLOCK_LENS),
                  flush=True)
    return pd.DataFrame(size_rows), pd.DataFrame(cov_rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=300)
    ap.add_argument("--B", type=int, default=199)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    size, cov = run(args.reps, args.B)
    size.to_csv(OUT / "bootstrap_size.csv", index=False, float_format="%.4f")
    cov.to_csv(OUT / "bootstrap_coverage.csv", index=False, float_format="%.4f")
    print(f"\nWrote {OUT / 'bootstrap_size.csv'} and {OUT / 'bootstrap_coverage.csv'}")


if __name__ == "__main__":
    main()
