"""Size check of the engine's tests on the project's own designs.

Keeps each model's real factor values (temperature, peak load, post_rtcb block)
and replaces the response with noise in which every coefficient is zero. Then
the cubic lack-of-fit test and each term's t-test are true nulls, and a correct
5% test rejects about 5% of the time. Each test is run with the engine's HAC
covariance (as in the pipeline) and with plain OLS errors (control).

Noise:
- iid       independent N(0, 1)
- ar1       AR(1) with the lag-1 autocorrelation of the model's own residuals
- block_vol circular block resampling (25-day blocks) of the vol_zone residuals
- block_lvl the same for the lvl_zone residuals

    .venv/bin/python notebooks/hac_size_check.py [--reps 1000]

Writes outputs/hac_size/size.csv and prints a summary.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from config import CONFIG
from rsr import Surface
from run_models import BLOCK, MODELS, load_daily, make_surface, prepare_model_data

OUT = CONFIG["paths"]["outputs"] / "hac_size"
ALPHA = 0.05
BLOCK_LEN = 25
DESIGNS = [("vol_zone", z) for z in ("LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST")] + [("vol_sys", "SYSTEM")]
LEVEL_TWIN = {"vol_zone": "lvl_zone", "vol_sys": "lvl_sys"}


def block_resample(resid: np.ndarray, n: int, rng) -> np.ndarray:
    starts = rng.integers(0, len(resid), size=int(np.ceil(n / BLOCK_LEN)))
    idx = (starts[:, None] + np.arange(BLOCK_LEN)[None, :]).ravel()[:n] % len(resid)
    return resid[idx]


def ar1(n: int, phi: float, rng) -> np.ndarray:
    e = rng.normal(size=n + 200)
    x = np.empty_like(e)
    x[0] = e[0]
    for t in range(1, len(e)):
        x[t] = phi * x[t - 1] + e[t]
    return x[200:]


def one_rep(d: pd.DataFrame, spec: dict, y: np.ndarray) -> dict:
    data = d.assign(y=y)
    out = {}
    for cov in ("hac", "ols"):
        s = Surface(data, response="y", factors=spec["factors"], labels=spec["labels"], block=BLOCK,
                    date="local_date", cov=cov, settings=CONFIG["rsr"])
        full = s.full()
        out[f"lof_{cov}"] = s.lack_of_fit()["cubic"]["p"]
        for name, p in full.table()["p_approx"].items():
            if name not in ("Intercept", *BLOCK):
                out[f"t_{name}_{cov}"] = p
    return out


def run(reps: int, seed: int = 20261009) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    daily = load_daily()
    rows = []
    for model, zone in DESIGNS:
        spec = MODELS[model]
        d, _ = prepare_model_data(daily, model, zone)
        n = len(d)
        vol_resid = make_surface(daily, model, zone)[0].full().resid.to_numpy()
        lvl_resid = make_surface(daily, LEVEL_TWIN[model], zone)[0].full().resid.to_numpy()
        phi = float(pd.Series(vol_resid).autocorr(1))
        noises = {
            "iid": lambda: rng.normal(size=n),
            "ar1": lambda: ar1(n, phi, rng),
            "block_vol": lambda: block_resample(vol_resid, n, rng),
            "block_lvl": lambda: block_resample(lvl_resid, n, rng),
        }
        for noise, draw in noises.items():
            ps = pd.DataFrame([one_rep(d, spec, draw()) for _ in range(reps)])
            rej = (ps < ALPHA).mean()
            rows.append({"model": model, "zone": zone, "n": n, "noise": noise, "phi": phi, "reps": reps,
                         **rej.to_dict()})
            print(f"{model:9s} {zone:10s} {noise:9s} LoF HAC {rej['lof_hac']:.3f}  OLS {rej['lof_ols']:.3f}  "
                  f"| t HAC {rej.filter(regex='^t_.*_hac$').min():.3f}-{rej.filter(regex='^t_.*_hac$').max():.3f}  "
                  f"OLS {rej.filter(regex='^t_.*_ols$').min():.3f}-{rej.filter(regex='^t_.*_ols$').max():.3f}",
                  flush=True)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=1000)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    res = run(args.reps)
    res.to_csv(OUT / "size.csv", index=False, float_format="%.4f")
    print(f"\nWrote {OUT / 'size.csv'}. Monte Carlo SE at 5%: {np.sqrt(0.05 * 0.95 / args.reps):.4f}")


if __name__ == "__main__":
    main()
