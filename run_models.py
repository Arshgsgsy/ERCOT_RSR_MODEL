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
- smearing_check.csv       residual spread by factor quintile and block, and the
                           dollar value with local against pooled smearing
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


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------

INF = CONFIG["inference"]
BLOCK_AT = {b: 1 for b in BLOCK}   # surfaces are described for the post-RTC+B regime (6.6)


def load_references() -> dict:
    path = CONFIG["paths"]["processed"] / "features_report.json"
    if not path.exists():
        raise ModelError(f"{path} is missing. Run `python build.py` first.")
    return json.loads(path.read_text())["references"]


def load_reference_mw(zone: str, refs: dict) -> float:
    return refs["sys_load_reference_mw"] if zone == SYSTEM else refs["load_reference_mw"][zone]


def to_dollars(yhat, resid: np.ndarray, info: dict) -> tuple[np.ndarray, np.ndarray]:
    """Back-transform fitted values to $/MWh with Duan smearing (Build Plan 6.6).
    Returns (level, d level / d yhat). Volatility: SD = exp(yhat) * mean(exp(e)).
    Level: S = median + MAD * mean(sinh(yhat + e)). Both assume one residual
    distribution across factor values."""
    yhat = np.asarray(yhat, float)
    if info["transform"] == "none":
        level = np.exp(yhat) * np.mean(np.exp(resid))
        return level, level
    v = yhat[..., None] + resid
    return info["median"] + info["mad"] * np.sinh(v).mean(axis=-1), info["mad"] * np.cosh(v).mean(axis=-1)


def chosen_terms(s: Surface, model: str, zone: str) -> tuple[list[str], str]:
    terms = selected_terms(model, zone)
    if terms is not None:
        return terms, "owner"
    return s.search()["final_terms"], "NCSS search (owner choice pending)"


def effect_days(s: Surface) -> pd.DataFrame:
    """The cold, mild and hot observed days for marginal effects."""
    cfg = INF["effect_days"]
    temp = pd.Series(s.Xreal[:, 0])
    pick = {"cold": (temp - temp.quantile(cfg["cold_quantile"])).abs().idxmin(),
            "mild": (temp - cfg["mild_temp_f"]).abs().idxmin(),
            "hot": (temp - temp.quantile(cfg["hot_quantile"])).abs().idxmin()}
    rows = [{"day": k, "local_date": s.index[i], **dict(zip(s.factors, s.Xreal[i]))} for k, i in pick.items()]
    return pd.DataFrame(rows)


def design_row(s: Surface, fit, real: np.ndarray) -> np.ndarray:
    Bv = np.array([[BLOCK_AT[b] for b in s.block]]) if s.block else None
    X = s.design(fit.terms, Z=s.coding.code(np.atleast_2d(real)), B=Bv)
    return X[fit.params.index].to_numpy()[0]


def marginal_effects_dollars(s: Surface, fit, draws: pd.DataFrame, info: dict, ref_mw: float) -> pd.DataFrame:
    """Slopes at the effect days, from the fit and from every bootstrap draw. A
    central difference is exact for a quadratic, so draws need no gradient code.
    Dollar slopes use the coding chain rule (inside s.coding) and the smeared
    back-transform evaluated at each draw's own fitted value."""
    level = INF["level"]
    beta, D = fit.params.to_numpy(), draws[fit.params.index].to_numpy()
    resid = fit.resid.to_numpy()
    per_unit = {0: ("$/MWh per °F", 1.0), 1: ("$/MWh per GW", 1000.0 / ref_mw)}
    rows = []
    for _, day in effect_days(s).iterrows():
        x = day[s.factors].to_numpy(float)
        x0 = design_row(s, fit, x)
        lvl, dlvl = to_dollars(beta @ x0, resid, info)
        lvl_b, dlvl_b = to_dollars(D @ x0, resid, info)
        for i, f in enumerate(s.factors):
            h = np.zeros_like(x)
            h[i] = 0.5 * (s.coding.hi[i] - s.coding.lo[i]) / 100
            g = (design_row(s, fit, x + h) - design_row(s, fit, x - h)) / (2 * h[i])
            slope, slope_b = float(beta @ g), D @ g
            unit, conv = per_unit[i]
            usd, usd_b = float(dlvl) * slope * conv, dlvl_b * slope_b * conv
            lo, hi = percentile_interval(slope_b[:, None], level)
            ulo, uhi = percentile_interval(usd_b[:, None], level)
            rows.append({"day": day["day"], "local_date": day["local_date"], **{c: day[c] for c in s.factors},
                         "factor": f, "fitted_dollars": float(lvl), "slope_response": slope,
                         "slope_response_low": float(lo[0]), "slope_response_high": float(hi[0]),
                         "slope_dollars": usd, "dollar_unit": unit,
                         "slope_dollars_low": float(ulo[0]), "slope_dollars_high": float(uhi[0])})
    return pd.DataFrame(rows)


def smearing_check(s: Surface, fit, info: dict, n_bins: int = 5) -> pd.DataFrame:
    """Does one pooled smearing factor fit every part of the surface (Build Plan
    6.6)? Residual SD by factor quintile and by block value, and the dollar value
    at each bin's median fitted value with that bin's own residuals against the
    pooled ones. A ratio far from 1 means the dollar contours are approximate there."""
    e, yh = fit.resid.to_numpy(), fit.fitted.to_numpy()
    groups = {f: pd.qcut(s.Xreal[:, i], n_bins, labels=False) + 1 for i, f in enumerate(s.factors)}
    groups.update({b: s.B[:, i].astype(int) for i, b in enumerate(s.block)})
    rows = []
    for by, g in groups.items():
        for v in np.unique(g):
            m = g == v
            y0 = float(np.median(yh[m]))
            local, pooled = float(to_dollars(y0, e[m], info)[0]), float(to_dollars(y0, e, info)[0])
            rows.append({"by": by, "bin": int(v), "n": int(m.sum()), "resid_sd": float(e[m].std(ddof=1)),
                         "resid_sd_pooled": float(e.std(ddof=1)), "dollars_local_smear": local,
                         "dollars_pooled_smear": pooled, "dollars_unsmeared": float(to_dollars(y0, np.zeros(1), info)[0]),
                         "local_minus_pooled": local - pooled})
    return pd.DataFrame(rows)


def fern_diagnostic(daily: pd.DataFrame, model: str, zone: str, s: Surface, full) -> dict:
    """Refit without the Fern window (diagnostic only, Build Plan 7.4). The shape
    check compares the two full-quadratic fits at the ordinary (non-Fern) days."""
    d, _ = prepare_model_data(daily, model, zone)
    keep = ~pd.to_datetime(d["local_date"]).between(*INF["fern_window"])
    spec = MODELS[model]
    nf = Surface(d[keep], response="y", factors=spec["factors"], labels=spec["labels"], block=BLOCK,
                 date="local_date", settings=CONFIG["rsr"])
    nf_full = nf.full()
    ordinary = ~pd.Series(s.index).between(*INF["fern_window"]).to_numpy()
    a = s.predict(full, s.Xreal[ordinary], BLOCK_AT)
    b = nf.predict(nf_full, s.Xreal[ordinary], BLOCK_AT)
    fern_ss = float(((full.fitted - full.fitted.mean()) ** 2)[~ordinary].sum())
    return {"fern_days_in_sample": int((~ordinary).sum()), "r2_no_fern": nf_full.r2,
            "shape_corr_ordinary_days": float(np.corrcoef(a, b)[0, 1]),
            "fern_share_of_explained_ss": fern_ss / (full.tss - full.rss)}


def run_one(daily: pd.DataFrame, model: str, zone: str, refs: dict) -> dict:
    B, L, seed = INF["bootstrap_B"], INF["block_len"], INF["seed"]
    s, info = make_surface(daily, model, zone)
    full = s.full()
    terms, terms_source = chosen_terms(s, model, zone)
    desc = s.fit(terms)
    search = s.search()
    key = {"model": model, "zone": zone}

    lof = {Lb: s.lack_of_fit_boot(B=B, block_len=Lb, seed=seed) for Lb in (L, *INF["block_len_sensitivity"])}
    ct_hac = s.conditional_tests()
    ct = {Lb: s.conditional_tests_boot(B=B, block_len=Lb, seed=seed) for Lb in (L, *INF["block_len_sensitivity"])}
    draws = s.bootstrap_pairs(full, B=B, block_len=L, seed=seed)
    can_full = s.canonical(full, block_values=BLOCK_AT)
    can_desc = s.canonical(desc, block_values=BLOCK_AT)
    rng_desc = s.fitted_range(desc)
    diag = s.diagnostics(full)
    fern = fern_diagnostic(daily, model, zone, s, full)
    smear = smearing_check(s, desc, info).assign(**key)

    coefs = full.table().rename(columns={"se": "se_hac", "p_approx": "p_hac"}).join(
        s.boot_table(full, draws, INF["level"]).drop(columns="coef_coded"))
    blk = coefs.loc[BLOCK[0]] if BLOCK else None

    row = {
        **key, "priority": MODELS[model]["priority"], "response": info["response"], "transform": info["transform"],
        "factors": ", ".join(s.factors), "calendar_days": info["calendar_days"], "n": s.n,
        "date_gaps": s.n_gaps, "hac_lags": s.hac_lags,
        "r2_full": full.r2, "adj_r2_full": full.adj_r2,
        "terms_chosen": ", ".join(desc.names), "terms_source": terms_source, "terms_ncss_search": ", ".join(search["final_terms"]),
        "r2_chosen": desc.r2, "adj_r2_chosen": desc.adj_r2,
        "lof_F": lof[L]["F"], "lof_p_hac": lof[L]["p_asymptotic"], "lof_p_boot": lof[L]["p_boot"],
        **{f"lof_p_boot{Lb}": lof[Lb]["p_boot"] for Lb in INF["block_len_sensitivity"]},
        "shape_full": can_full["shape"], "shape_chosen": can_desc["shape"],
        "stationary_inside_chosen": can_desc.get("inside_data"),
        "fitted_low": rng_desc.loc["lowest", "fitted"], "fitted_low_date": rng_desc.loc["lowest", "date"],
        "fitted_high": rng_desc.loc["highest", "fitted"], "fitted_high_date": rng_desc.loc["highest", "date"],
        "fitted_low_dollars": float(to_dollars(rng_desc.loc["lowest", "fitted"], desc.resid.to_numpy(), info)[0]),
        "fitted_high_dollars": float(to_dollars(rng_desc.loc["highest", "fitted"], desc.resid.to_numpy(), info)[0]),
        "post_rtcb_coef": blk["coef_coded"] if blk is not None else np.nan,
        "post_rtcb_boot_low": blk["boot_low"] if blk is not None else np.nan,
        "post_rtcb_boot_high": blk["boot_high"] if blk is not None else np.nan,
        "n_boot_pairs": int(draws.notna().all(axis=1).sum()),
        **fern,
        "smear_resid_sd_ratio": float(smear["resid_sd"].max() / smear["resid_sd"].min()),
        "smear_local_vs_pooled_min": float((smear["dollars_local_smear"] / smear["dollars_pooled_smear"]).min())
        if info["transform"] == "none" else np.nan,
        "smear_local_vs_pooled_max": float((smear["dollars_local_smear"] / smear["dollars_pooled_smear"]).max())
        if info["transform"] == "none" else np.nan,
    }
    seq = s.sequential_ss().reset_index().assign(**key)
    cond = ct_hac[["terms", "q", "F", "p_approx", "last_ss", "r2_loss"]].rename(columns={"p_approx": "p_hac"}).join(
        ct[L][["p_boot"]]).reset_index().assign(**key)
    sens = pd.concat([ct[Lb][["p_boot"]].rename(columns={"p_boot": f"p_boot{Lb}"}) for Lb in ct], axis=1)
    sens.loc["lack of fit (cubic)"] = [lof[Lb]["p_boot"] for Lb in ct]
    me = marginal_effects_dollars(s, full, draws, info, load_reference_mw(zone, refs)).assign(**key)
    diag_row = {**key, "vif": json.dumps({k: round(v, 3) for k, v in diag["vif"].items()}),
                "condition_number": diag["condition_number"], "rank": diag["rank"], "n_params": diag["n_params"],
                "leverage_mean": diag["leverage_mean"], "resid_acf_1": diag["resid_acf_1_5"][0],
                "ljung_box_p": diag["ljung_box"]["p"], "durbin_watson": diag["durbin_watson"],
                "top_cooks_days": ", ".join(f"{d.date()} ({v:.3f})" for d, v in diag["top_cooks"].items()),
                "top_leverage_days": ", ".join(f"{d.date()} ({v:.3f})" for d, v in diag["top_leverage"].items())}
    return {"row": row, "seq": seq, "cond": cond, "coef": coefs.reset_index(names="term").assign(**key),
            "sens": sens.reset_index(names="test").assign(**key), "me": me, "diag": diag_row,
            "search": search["path"].assign(**key), "smear": smear, "fits": (f"{model} | {zone}", desc)}


def run(out: Path, models: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Fit every model in priority order and write the tables (priority 1 first)."""
    daily, refs = load_daily(), load_references()
    order = sorted(models or MODELS, key=lambda m: (MODELS[m]["priority"], list(MODELS).index(m)))
    out.mkdir(parents=True, exist_ok=True)
    parts: dict[str, list] = {k: [] for k in ("row", "seq", "cond", "coef", "sens", "me", "diag", "search", "smear",
                                              "fits")}
    for model in order:
        for zone in zones_for(model):
            res = run_one(daily, model, zone, refs)
            for k in parts:
                parts[k].append(res[k])
            r = res["row"]
            print(f"{model:16s} {zone:10s} n {r['n']:3d}  R² {r['r2_full']:.3f}  terms {r['terms_chosen']:22s} "
                  f"LoF p boot {r['lof_p_boot']:.3f} (HAC {r['lof_p_hac']:.2g})  R² no Fern {r['r2_no_fern']:.3f}",
                  flush=True)
    keys = ["model", "zone"]

    def front(df):
        return df[keys + [c for c in df.columns if c not in keys]]

    tables = {
        "results_table": pd.DataFrame(parts["row"]),
        "sequential_ss": front(pd.concat(parts["seq"])),
        "conditional_tests": front(pd.concat(parts["cond"])),
        "coefficients": front(pd.concat(parts["coef"])),
        "marginal_effects": front(pd.concat(parts["me"])),
        "bootstrap_sensitivity": front(pd.concat(parts["sens"])),
        "diagnostics": pd.DataFrame(parts["diag"]),
        "selection_history": front(pd.concat(parts["search"])),
        "smearing_check": front(pd.concat(parts["smear"])),
    }
    for name, t in tables.items():
        t.to_csv(out / f"{name}.csv", index=False, float_format="%.6g")
    from figures import diagnostics_figure
    fig_dir = out / "figures"
    fig_dir.mkdir(exist_ok=True)
    p1 = [f for f, r in zip(parts["fits"], parts["row"]) if r["priority"] == 1]
    for resp in ("vol_zone", "lvl_zone"):
        diagnostics_figure([f for f in p1 if f[0].startswith(resp)], fig_dir, name=f"diagnostics_{resp}")
    print(f"Wrote {len(tables)} tables and the diagnostics figures to {out}")
    return tables


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=CONFIG["paths"]["outputs"] / "dev", help="output folder")
    ap.add_argument("--models", nargs="*", choices=sorted(MODELS), help="only these models (default all)")
    args = ap.parse_args(argv)
    try:
        run(args.out, args.models)
    except ModelError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
