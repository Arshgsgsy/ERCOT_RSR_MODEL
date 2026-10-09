"""Block-bootstrap inference on the real models, for block lengths 10, 25 and 50.

For every model and zone (full quadratic, the model for inference):
- lack of fit: asymptotic HAC p and bootstrap p, all days and without the
  Winter Storm Fern days (Jan 23 - Feb 1, 2026)
- conditional tests (groups and factors): asymptotic HAC p and bootstrap p
- coefficients: HAC SE and interval against pairs-bootstrap SE and interval

    .venv/bin/python notebooks/bootstrap_real.py [--B 999]

Writes outputs/hac_size/real_lof.csv, real_conditional.csv, real_coef.csv.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from scipy import stats

from config import CONFIG
from rsr import Surface
from run_models import BLOCK, MODELS, load_daily, make_surface, prepare_model_data, zones_for

OUT = CONFIG["paths"]["outputs"] / "hac_size"
BLOCK_LENS = (10, 25, 50)
FERN = ("2026-01-23", "2026-02-01")


def no_fern_surface(daily, model, zone) -> Surface:
    d, _ = prepare_model_data(daily, model, zone)
    d = d[~pd.to_datetime(d["local_date"]).between(*FERN)]
    spec = MODELS[model]
    return Surface(d, response="y", factors=spec["factors"], labels=spec["labels"], block=BLOCK,
                   date="local_date", settings=CONFIG["rsr"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--B", type=int, default=999)
    B = ap.parse_args().B
    OUT.mkdir(parents=True, exist_ok=True)
    daily = load_daily()
    lof_rows, cond_rows, coef_rows = [], [], []
    for model in MODELS:
        for zone in zones_for(model):
            s, _ = make_surface(daily, model, zone)
            nf = no_fern_surface(daily, model, zone)
            f = s.full()
            key = {"model": model, "zone": zone}
            row = {**key, "n": s.n, "lof_F": None}
            for label, surf in (("all", s), ("no_fern", nf)):
                for L in BLOCK_LENS:
                    r = surf.lack_of_fit_boot(B=B, block_len=L)
                    row[f"lof_p_hac_{label}"] = r["p_asymptotic"]
                    row[f"lof_p_boot{L}_{label}"] = r["p_boot"]
                    if label == "all":
                        row["lof_F"] = r["F"]
            lof_rows.append(row)

            ct = {L: s.conditional_tests_boot(B=B, block_len=L) for L in BLOCK_LENS}
            for test in ct[25].index:
                cond_rows.append({**key, "test": test, "terms": ct[25].loc[test, "terms"], "F": ct[25].loc[test, "F"],
                                  "p_hac": ct[25].loc[test, "p_asymptotic"],
                                  **{f"p_boot{L}": ct[L].loc[test, "p_boot"] for L in BLOCK_LENS}})

            tq = stats.t.ppf(0.975, f.df_resid)
            hac = f.table()
            boots = {L: s.boot_table(f, s.bootstrap_pairs(f, B=B, block_len=L)) for L in BLOCK_LENS}
            for name in f.params.index:
                r = {**key, "coef": name, "estimate": f.params[name], "se_hac": hac.loc[name, "se"],
                     "low_hac": f.params[name] - tq * hac.loc[name, "se"],
                     "high_hac": f.params[name] + tq * hac.loc[name, "se"], "p_hac": hac.loc[name, "p_approx"]}
                for L, bt in boots.items():
                    r.update({f"se_boot{L}": bt.loc[name, "boot_se"], f"low_boot{L}": bt.loc[name, "boot_low"],
                              f"high_boot{L}": bt.loc[name, "boot_high"], f"n_boot{L}": bt.loc[name, "n_boot"]})
                coef_rows.append(r)
            print(f"{model:16s} {zone:10s} LoF p hac {row['lof_p_hac_all']:.2g} | boot "
                  + " ".join(f"{row[f'lof_p_boot{L}_all']:.3f}" for L in BLOCK_LENS)
                  + " | no Fern boot " + " ".join(f"{row[f'lof_p_boot{L}_no_fern']:.3f}" for L in BLOCK_LENS),
                  flush=True)
    pd.DataFrame(lof_rows).to_csv(OUT / "real_lof.csv", index=False, float_format="%.6g")
    pd.DataFrame(cond_rows).to_csv(OUT / "real_conditional.csv", index=False, float_format="%.6g")
    pd.DataFrame(coef_rows).to_csv(OUT / "real_coef.csv", index=False, float_format="%.6g")
    print(f"Wrote real_lof.csv, real_conditional.csv, real_coef.csv to {OUT}")


if __name__ == "__main__":
    main()
