"""Out-of-sample evaluation of every available explanatory surface.

How well does a surface predict days it was not fitted on? Each fold is left
out in turn, the model is refitted by OLS on the rest and the left-out days are
predicted (with their own block value). Folds:
- day    leave one day out (PRESS); neighbouring days stay in, so this is the
         most optimistic version
- week   leave out 7 consecutive calendar days
- month  leave out one calendar month (also leaves out that month's season,
         and the January fold leaves out all of Winter Storm Fern)

Out-of-sample R² = 1 - sum of squared prediction errors / sum of squares about
the mean of the scored days. Below 0 means the surface predicts the left-out
days worse than their own average. Scored on all days and on the days outside
Jan 23 - Feb 1, 2026 (Fern stays in the training folds either way).

Term sets: the full quadratic (inference model), the chosen descriptive terms
(CONFIG["selected_terms"]), the linear surface T, L, and the block alone
(intercept + post_rtcb) as the floor. Coding does not matter: every set is
hierarchical, so fitted values do not depend on it.

    python evaluation.py --out outputs/evaluation

Writes oos_r2.csv and prints a summary. Also called by run_all.py.
Response transforms and approved terms use the full sample; training uses
days before and after each fold. This is not prospective forecasting or
nested model-selection validation. Renewable evaluation separately uses
training-only response transforms and smearing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from config import CONFIG
from run_models import MODELS, ModelUnavailable, chosen_terms, load_daily, make_surface, zones_for

OUT = CONFIG["paths"]["outputs"] / "out_of_sample"
FERN = CONFIG["inference"]["fern_window"]


def folds(dates: pd.Series) -> dict[str, np.ndarray]:
    return {"day": np.arange(len(dates)),
            "week": ((dates - dates.min()).dt.days // 7).to_numpy(),
            "month": dates.dt.to_period("M").astype(str).to_numpy()}


def oos_r2(X: np.ndarray, y: np.ndarray, groups: np.ndarray, scored: np.ndarray) -> float:
    pred = np.full(len(y), np.nan)
    for g in np.unique(groups):
        te = groups == g
        beta = np.linalg.lstsq(X[~te], y[~te], rcond=None)[0]
        pred[te] = X[te] @ beta
    e, yc = (y - pred)[scored], y[scored] - y[scored].mean()
    return float(1 - (e ** 2).sum() / (yc ** 2).sum())


def check(daily: pd.DataFrame, model: str, zone: str) -> list[dict]:
    s, _ = make_surface(daily, model, zone)
    dates = pd.to_datetime(pd.Series(s.index))
    fern = dates.between(*FERN).to_numpy()
    chosen, _ = chosen_terms(s, model, zone)
    sets = {"full": None, "chosen": chosen, "linear": ["T", "L"], "block_only": []}
    rows = []
    for name, terms in sets.items():
        f = s.fit(terms)
        X = f.X.to_numpy()
        base = {"model": model, "zone": zone, "term_set": name, "terms": ", ".join(f.names) or "(block only)",
                "n": s.n, "r2_in_sample": f.r2, "adj_r2_in_sample": f.adj_r2}
        for fold, groups in folds(dates).items():
            rows.append({**base, "folds": fold,
                         "r2_oos_all_days": oos_r2(X, s.y, groups, np.ones(len(fern), bool)),
                         "r2_oos_non_fern_days": oos_r2(X, s.y, groups, ~fern)})
    return rows


def write_evaluation(out: Path) -> Path:
    daily = load_daily()
    rows = []
    for model in MODELS:
        for zone in zones_for(model):
            try:
                rows += check(daily, model, zone)
            except ModelUnavailable as exc:
                print(f"skipped {model} {zone}: {exc}")
                continue
            print(f"done {model} {zone}", flush=True)
    if not rows:
        raise ModelUnavailable("No models available for evaluation")
    result = pd.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "oos_r2.csv"
    result.to_csv(path, index=False, float_format="%.6g")
    show = result[result["term_set"].isin(["full", "chosen"])].pivot_table(
        index=["model", "zone"], columns=["term_set", "folds"], values="r2_oos_all_days").round(3)
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print("\nOut-of-sample R², all days scored:\n" + show.to_string())
        show = result[result["term_set"].isin(["full", "chosen"])].pivot_table(
            index=["model", "zone"], columns=["term_set", "folds"], values="r2_oos_non_fern_days").round(3)
        print("\nOut-of-sample R², non-Fern days scored:\n" + show.to_string())
    print(f"\nWrote {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    write_evaluation(ap.parse_args(argv).out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
