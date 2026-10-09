"""Step 4 — Model runner (Build Plan, Section 6.5).

Fits every model in priority order and writes the result tables into an output
folder. The notebook (notebooks/explore_terms.ipynb) uses the same data
preparation, so every model sample and response transform is defined once.

    python run_models.py --out outputs/<run_id>

    from run_models import load_daily, make_surface
    s, info = make_surface(load_daily(), "vol_zone", "LZ_WEST")

For each model and zone the full quadratic is the model for inference (D31) and
the owner's chosen terms give the descriptive surface. Tests and intervals use
the block bootstrap (CONFIG["inference"]); asymptotic HAC values are kept beside
them for comparison only.

Tables written
- results_table.csv        one row per model: n, R², terms, lack of fit, shape,
                           fitted range, RTC+B block, Fern diagnostic
- sequential_ss.csv        descriptive split of explained variation (no p-values)
- conditional_tests.csv    group and factor tests, HAC and bootstrap p-values
- coefficients.csv         full-quadratic coefficients, HAC and bootstrap intervals
- marginal_effects.csv     slopes at a cold, a mild and a hot observed day, in
                           response units and in $/MWh per °F and per GW
- bootstrap_sensitivity.csv  bootstrap p-values for block lengths 10, 25, 50
- diagnostics.csv          VIF, condition number, autocorrelation, top Cook's days
- selection_history.csv    the NCSS search path for every model
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from config import CONFIG
from rsr import Surface, check_hierarchy, parse_term, percentile_interval

MODELS = {k: v for k, v in CONFIG["models"].items() if isinstance(v, dict)}
BLOCK = CONFIG["models"]["block"]
ZONES = [z["location"] for k, z in CONFIG["zones"].items() if k != "decision"]
SYSTEM = "SYSTEM"


class ModelError(RuntimeError):
    """A model sample or transform cannot be built."""


def load_daily() -> pd.DataFrame:
    path = CONFIG["paths"]["processed"] / "daily.parquet"
    if not path.exists():
        raise ModelError(f"{path} is missing. Run `python build.py` first.")
    return pd.read_parquet(path)


def zones_for(model: str) -> list[str]:
    return ZONES if MODELS[model]["zones"] == "zones" else [SYSTEM]


def prepare_model_data(daily: pd.DataFrame, model: str, zone: str) -> tuple[pd.DataFrame, dict]:
    """The model sample and its response `y`.

    Days enter only if every expected hour has a spread and the factors cover
    every hour (Build Plan 5.4). On-peak models use weekdays only. Level
    responses are asinh((y - median) / MAD) with the median and MAD of this
    sample (raw MAD, not scaled by 1.4826); the run stops if MAD is 0.
    """
    if model not in MODELS:
        raise ModelError(f"unknown model {model}; choose from {sorted(MODELS)}")
    spec = MODELS[model]
    if zone not in zones_for(model):
        raise ModelError(f"{model} runs on {zones_for(model)}, not {zone}")

    d = daily[daily["zone"] == zone]
    n_days = len(d)
    d = d[d["complete_day"] & d["factors_complete"]]
    if spec["sample"] == "weekday":
        d = d[d["weekday"] < 5]
    d = d.dropna(subset=[spec["response"], *spec["factors"], *BLOCK]).copy()

    raw = d[spec["response"]].astype(float)
    info = {"model": model, "zone": zone, "response": spec["response"], "transform": spec["transform"],
            "calendar_days": n_days, "n": len(d)}
    if spec["transform"] == "asinh_mad":
        med = float(raw.median())
        mad = float((raw - med).abs().median())
        if not mad > 0:
            raise ModelError(f"{model} {zone}: MAD of {spec['response']} is 0; asinh standardization undefined")
        d["y"] = np.arcsinh((raw - med) / mad)
        info.update({"median": med, "mad": mad})
    elif spec["transform"] == "none":
        if not np.isfinite(raw).all():
            raise ModelError(f"{model} {zone}: {spec['response']} has non-finite values")
        d["y"] = raw
    else:
        raise ModelError(f"unknown transform {spec['transform']}")
    return d.sort_values("local_date").reset_index(drop=True), info


def make_surface(daily: pd.DataFrame, model: str, zone: str, **kwargs) -> tuple[Surface, dict]:
    """A Surface for one model and zone, with the project's engine settings."""
    d, info = prepare_model_data(daily, model, zone)
    spec = MODELS[model]
    s = Surface(d, response="y", factors=spec["factors"], labels=spec["labels"], block=BLOCK,
                date="local_date", settings=CONFIG["rsr"], **kwargs)
    info.update({"n_used": s.n, "date_gaps": s.n_gaps, "hac_lags": s.hac_lags})
    return s, info


def selected_terms(model: str, zone: str) -> list[str] | None:
    """The owner's chosen terms for this model and zone, checked for hierarchy."""
    entry = CONFIG["selected_terms"].get(f"{model}|{zone}")
    if entry is None:
        return None
    labels = MODELS[model]["labels"]
    check_hierarchy([parse_term(t, labels) for t in entry["terms"]], labels)
    return list(entry["terms"])
