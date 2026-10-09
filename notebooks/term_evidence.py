"""Term-selection evidence for every model and zone.

Runs the same engine calls as notebooks/explore_terms.ipynb (Steps 1-5) for all
model x zone combinations and writes the evidence to outputs/term_selection/:
one Markdown file and one figure per combination, plus summary.csv.

It also applies the selection rule recorded in docs/term_selection.md to give a
proposal: the NCSS search (R² loss < 0.01), then a tie-break to the reference
set T, L, L^2, T*L when it is within 2 AIC of the search result. On-peak rows
mirror the all-hours level choice for the zone (no selection of their own).
The proposal is reviewed by hand (lack of fit, masked contours) and approved by
the owner before anything goes into config.py. A refit without the Winter Storm
Fern days is reported as a diagnostic.

    .venv/bin/python notebooks/term_evidence.py
"""

from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from config import CONFIG
from rsr import Surface, missing_parents, term_name
from run_models import BLOCK, MODELS, load_daily, make_surface, prepare_model_data, zones_for

OUT = CONFIG["paths"]["outputs"] / "term_selection"
BLOCK_AT = {"post_rtcb": 1}
MAX_D_AIC = 2.0          # tie-break window around the search result
FULL = ["T", "L", "T^2", "L^2", "T*L"]
# Tie-break reference: the most common NCSS search result over the 16 fits (8 of 16).
REFERENCE = ["T", "L", "L^2", "T*L"]
MIRROR = {"lvl_zone_onpeak": "lvl_zone"}
FERN = ("2026-01-23", "2026-02-01")   # Winter Storm Fern and the days after it (diagnostic only)
RESPONSE_LABEL = {"asinh_mad": "asinh((spread - median) / MAD)", "none": "log daily spread SD"}


def hierarchical_subsets(s) -> list[list[str]]:
    """Every non-empty hierarchical subset of the full quadratic."""
    full = s.terms(FULL)
    out = []
    for r in range(1, len(full) + 1):
        for combo in combinations(full, r):
            if not missing_parents(combo):
                out.append([term_name(t, s.labels) for t in combo])
    return out


def rule_path(s) -> tuple[list[str], pd.DataFrame]:
    """The NCSS search (D31): drop the cheapest removable term while its R² loss is
    below the cutoff. An earlier version also required ΔAIC < 2 for each drop; that
    guard was removed on Oct 9, 2026 (it changed only Houston volatility)."""
    search = s.search()
    return search["final_terms"], search["path"]


def propose(s) -> tuple[list[str], list[str], pd.DataFrame, str]:
    """Rule pick, then the tie-break: use the reference set instead when it is
    within MAX_D_AIC of the rule pick (so zones compare like with like)."""
    rule_terms, steps = rule_path(s)
    rule, ref = s.fit(rule_terms), s.fit(REFERENCE)
    if s.names(rule_terms) != s.names(REFERENCE) and ref.aic - rule.aic < MAX_D_AIC:
        return list(REFERENCE), rule_terms, steps, (f"tie-break: reference set is within {MAX_D_AIC} AIC of the "
                                                    f"rule pick (ΔAIC {ref.aic - rule.aic:+.2f})")
    return rule_terms, rule_terms, steps, "rule"


def surface_agreement(s, a, b) -> dict:
    """How closely two fits agree inside the data (post_rtcb = 1, masked grid)."""
    ga = s.predict_grid(a, n=80, block_values=BLOCK_AT)
    gb = s.predict_grid(b, n=80, block_values=BLOCK_AT)
    m = ga["inside"]
    za, zb = ga["z"][m], gb["z"][m]
    rng = zb.max() - zb.min()
    return {"grid_corr": float(np.corrcoef(za, zb)[0, 1]),
            "grid_max_abs_diff_share": float(np.abs(za - zb).max() / rng),
            "fitted_corr": float(np.corrcoef(a.fitted, b.fitted)[0, 1])}


def figure(s, model, zone, fits, titles, label, path) -> None:
    grids = [s.predict_grid(f, n=80, block_values=BLOCK_AT) for f in fits]
    zs = [np.where(g["inside"], g["z"], np.nan) for g in grids]
    lo, hi = np.nanmin([np.nanmin(z) for z in zs]), np.nanmax([np.nanmax(z) for z in zs])
    levels = np.linspace(lo, hi, 16)
    fig = plt.figure(figsize=(5.2 * len(fits), 8.4), constrained_layout=True)
    gs = fig.add_gridspec(2, len(fits))
    top = [fig.add_subplot(gs[0, i]) for i in range(len(fits))]
    for ax, g, z, title in zip(top, grids, zs, titles):
        cf = ax.contourf(g["x"], g["y"], z, levels=levels, cmap="viridis")
        ax.contour(g["x"], g["y"], z, levels=levels, colors="k", linewidths=0.3)
        ax.scatter(s.Xreal[:, 0], s.Xreal[:, 1], s=6, c="white", edgecolors="k", linewidths=0.3)
        ax.set_title(title, fontsize=9)
        ax.set_xlabel(g["x_name"])
    top[0].set_ylabel(grids[0]["y_name"])
    fig.colorbar(cf, ax=top, label=label, format="%.2f")
    fit = fits[0]
    bottom = [fig.add_subplot(gs[1, i]) for i in range(min(3, len(fits)))] if len(fits) >= 3 else \
        [fig.add_subplot(gs[1, :])]
    if len(bottom) == 3:
        bottom[0].scatter(fit.fitted, fit.resid, s=6)
        bottom[0].axhline(0, color="k", lw=0.8)
        bottom[0].set(xlabel="fitted", ylabel="residual", title="Proposal: residuals vs fitted")
        bottom[1].plot(fit.resid.index, fit.resid.to_numpy(), lw=0.7)
        bottom[1].axhline(0, color="k", lw=0.8)
        bottom[1].set(title="Proposal: residuals over time")
        bottom[1].tick_params(axis="x", labelrotation=30)
        stats.probplot(fit.resid.to_numpy(), dist="norm", plot=bottom[2])
        bottom[2].set_title("Proposal: normal probability plot")
    fig.suptitle(f"{model} | {zone} | post_rtcb = 1, masked at radius {s.settings['inside_radius']} (coded)")
    fig.savefig(path, dpi=110)
    plt.close(fig)


def block(title: str, obj) -> str:
    text = obj.to_string(float_format=lambda v: f"{v:.5g}") if hasattr(obj, "to_string") else str(obj)
    return f"### {title}\n\n```text\n{text}\n```\n"


def evidence(daily, model, zone) -> dict:
    s, info = make_surface(daily, model, zone)
    full = s.full()
    search = s.search()
    rule_terms, rule_only, rule_steps, how = propose(s)
    if model in MIRROR:
        # Robustness rows get no selection of their own: they mirror the all-hours
        # choice so the two surfaces compare like with like (Build Plan 6.5).
        rule_terms, _, _, _ = propose(make_surface(daily, MIRROR[model], zone)[0])
        how = f"mirrors {MIRROR[model]} (no selection on the robustness row)"
    rule = s.fit(rule_terms)
    fern = fern_check(daily, model, zone, rule_terms)

    subsets = hierarchical_subsets(s)
    cmp = s.compare(*subsets, labels=[", ".join(t) for t in subsets])
    cmp["d_aic_vs_full"] = cmp["aic"] - full.aic
    cmp["d_bic_vs_full"] = cmp["bic"] - full.bic
    cmp = cmp.drop(columns=["terms"]).sort_values("aic")

    lof_full = s.lack_of_fit()["cubic"]
    lof_rule = s.lack_of_fit(rule_terms)["cubic"]
    can_full, can_rule = s.canonical(full, block_values=BLOCK_AT), s.canonical(rule, block_values=BLOCK_AT)
    agree = surface_agreement(s, rule, full)
    diag_full, diag_rule = s.diagnostics(full), s.diagnostics(rule)

    fits, titles = [rule, full], [f"proposal: {', '.join(rule.names)}", "full quadratic"]
    if s.names(rule_only) != rule.names:
        fits.insert(1, s.fit(rule_only))
        titles.insert(1, f"rule before tie-break: {', '.join(s.names(rule_only))}")
    elif search["final_terms"] != rule.names:
        fits.insert(1, s.fit(search["final_terms"]))
        titles.insert(1, f"NCSS search: {', '.join(search['final_terms'])}")
    elif len(fits) < 3:
        fits.append(s.fit(["T", "L"]))
        titles.append("linear: T, L")
    stem = f"{model}__{zone}"
    figure(s, model, zone, fits, titles, RESPONSE_LABEL[info["transform"]], OUT / f"{stem}.png")

    def lof_text(c):
        return f"p = {c['p']:.4f} (F = {c['F']:.3f})" if "p" in c else f"skipped ({c['skipped']})"

    def can_text(c):
        if "stationary_real" not in c:
            return c["shape"]
        return (f"{c['shape']}, stationary point {c['stationary_real'].round(3).to_dict()}, "
                f"inside data: {c['inside_data']}")

    md = [
        f"# {model} | {zone}\n",
        f"n = {s.n} of {info['calendar_days']} calendar days, HAC lags {s.hac_lags}, date gaps {s.n_gaps}. "
        f"Response: {RESPONSE_LABEL[info['transform']]} of `{info['response']}`.\n",
        f"![surfaces]({stem}.png)\n",
        "## Summary\n",
        f"- Full quadratic: R² {full.r2:.4f}, adj R² {full.adj_r2:.4f}, cubic lack-of-fit {lof_text(lof_full)}, "
        f"shape {can_text(can_full)}",
        f"- NCSS search keeps: {', '.join(search['final_terms'])} (R² {search['final_r2']:.4f})",
        f"- Rule pick: {', '.join(s.names(rule_only))}",
        f"- Proposal ({how}): {', '.join(rule.names)} (R² {rule.r2:.4f}, adj R² {rule.adj_r2:.4f}, "
        f"ΔAIC vs full {rule.aic - full.aic:+.2f}, ΔBIC {rule.bic - full.bic:+.2f})",
        f"- Proposal cubic lack-of-fit {lof_text(lof_rule)}; shape {can_text(can_rule)}",
        f"- Proposal vs full inside the data: grid correlation {agree['grid_corr']:.3f}, max abs difference "
        f"{agree['grid_max_abs_diff_share']:.1%} of the full surface's range, fitted-value correlation "
        f"{agree['fitted_corr']:.3f}",
        f"- VIF (full) {({k: round(v, 1) for k, v in diag_full['vif'].items()})}, condition number "
        f"{diag_full['condition_number']:.1f}; proposal VIF {({k: round(v, 1) for k, v in diag_rule['vif'].items()})}",
        f"- Proposal residual ACF lags 1-5 {[round(a, 3) for a in diag_rule['resid_acf_1_5']]}, Ljung-Box p "
        f"{diag_rule['ljung_box']['p']:.4f}, Durbin-Watson {diag_rule['durbin_watson']:.3f}",
        f"- Diagnostic, {FERN[0]} to {FERN[1]} left out (n {fern['n']}): full R² {fern['full_r2']:.4f}, "
        f"rule pick {fern['rule']}, proposal ΔAIC vs that pick {fern['d_aic']:+.2f}, "
        f"proposal cubic lack-of-fit p {fern['lof_p']:.4f}\n",
        "## Full quadratic (model for inference)\n",
        block("Summary", s.summary(full)),
        block("Sequential sums of squares (descriptive)", s.sequential_ss()),
        block("Conditional HAC tests", s.conditional_tests()),
        block("drop_one on the full quadratic", s.drop_one()),
        block("NCSS search path (cutoff 0.01)", search["path"]),
        block("Every hierarchical subset, sorted by AIC", cmp),
        "## Proposal\n",
        block("Summary", s.summary(rule)),
        block("drop_one on the proposal", s.drop_one(rule_terms)),
        block("Fitted range over observed days", s.fitted_range(rule)),
        block("Highest Cook's distance days", diag_rule["top_cooks"].to_frame("cooks_distance")),
    ]
    (OUT / f"{stem}.md").write_text("\n".join(md))

    return {"model": model, "zone": zone, "n": s.n, "full_r2": full.r2, "full_adj_r2": full.adj_r2,
            "full_lof_p": lof_full.get("p"), "full_shape": can_full["shape"],
            "ncss_search": ", ".join(search["final_terms"]), "ncss_r2": search["final_r2"],
            "rule_pick": ", ".join(s.names(rule_only)), "proposal": ", ".join(rule.names), "how": how,
            "prop_r2": rule.r2, "prop_adj_r2": rule.adj_r2,
            "prop_d_aic": rule.aic - full.aic, "prop_d_bic": rule.bic - full.bic,
            "prop_lof_p": lof_rule.get("p"), "prop_shape": can_rule["shape"],
            "prop_stationary_inside": can_rule.get("inside_data"),
            "best_aic_set": cmp.index[0], "best_bic_set": cmp.sort_values("bic").index[0],
            **{f"agree_{k}": v for k, v in agree.items()},
            **{f"nofern_{k}": v for k, v in fern.items()}}


def fern_check(daily, model, zone, terms) -> dict:
    """Diagnostic only (Build Plan 7.4): refit without the Fern days and see whether
    the rule would pick the same terms."""
    d, _ = prepare_model_data(daily, model, zone)
    d = d[~pd.to_datetime(d["local_date"]).between(*FERN)]
    spec = MODELS[model]
    s = Surface(d, response="y", factors=spec["factors"], labels=spec["labels"], block=BLOCK,
                date="local_date", settings=CONFIG["rsr"])
    rule_terms, _ = rule_path(s)
    pick, rule = s.fit(terms), s.fit(rule_terms)
    return {"n": s.n, "full_r2": s.full().r2, "rule": ", ".join(rule.names), "prop_r2": pick.r2,
            "d_aic": pick.aic - rule.aic, "lof_p": s.lack_of_fit(terms)["cubic"].get("p", np.nan)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    daily = load_daily()
    order = sorted(MODELS, key=lambda m: (MODELS[m]["priority"], list(MODELS).index(m)))
    rows = [evidence(daily, m, z) for m in order for z in zones_for(m)]
    summary = pd.DataFrame(rows).set_index(["model", "zone"])
    summary.to_csv(OUT / "summary.csv", float_format="%.6g")
    with pd.option_context("display.width", 250, "display.max_columns", 40):
        print(summary.to_string(float_format=lambda v: f"{v:.4g}"))


if __name__ == "__main__":
    main()
