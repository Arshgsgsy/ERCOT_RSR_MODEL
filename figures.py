"""Step 5 — Figures (Build Plan, Section 6.6).

Every figure is drawn at its final printed size (inches, 300 dpi) and written to
<out>/figures/. Data behind each figure that is not already a table is saved as
a CSV beside it, so every plotted number traces to a file.

    python figures.py --out outputs/<run_id>

Written so far
- exploration.png       2 x 2: hourly spread distribution (asinh scale), daily spread
                        SD against temperature colored by peak load, DA implied heat
                        rate against peak load, zonal spread correlation heatmap
- binned_sd_load.png    daily spread SD by peak-load decile, per zone (+ CSV)
- contours_volatility.png   the four zones' volatility surfaces (owner's terms) in
                        $/MWh with Duan smearing, one shared log color scale,
                        masked to observed days, Fern days marked (+ grid CSV)
- contours_volatility_unsmeared.png  the same without smearing (median SD), shown
                        because residual spread changes across the factors
                        (smearing_check.csv)
- contour_system.png    the system surface on its own factors, zone color limits
- contour_radius_sensitivity.csv  share of each grid shown at radius 0.10/0.15/0.20
- diagnostics_figure()  residuals against fitted and time, normal probability plot,
                        for any list of fits (called by the model runner)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from config import CONFIG
from tables import SYSTEM, TableError, load_inputs, spread_wide

PATHS = CONFIG["paths"]
DPI = 300
TEXT_WIDTH_IN = 6.5          # letter page with 1-inch margins
# One fixed color per zone across every figure (Build Plan 6.6).
ZONE_COLORS = {"LZ_HOUSTON": "#1b7837", "LZ_NORTH": "#2166ac", "LZ_SOUTH": "#d6604d", "LZ_WEST": "#8c510a",
               SYSTEM: "#4d4d4d"}
ZONE_LABELS = {"LZ_HOUSTON": "Houston", "LZ_NORTH": "North", "LZ_SOUTH": "South", "LZ_WEST": "West", SYSTEM: "System"}
SPREAD_SIGN = "spread = DA − RT"

plt.rcParams.update({"font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8, "legend.fontsize": 7,
                     "xtick.labelsize": 7, "ytick.labelsize": 7, "savefig.dpi": DPI})


def zones_in(df: pd.DataFrame) -> list[str]:
    return [z for z in ZONE_COLORS if z != SYSTEM and z in set(df["zone"])]


def model_days(daily: pd.DataFrame) -> pd.DataFrame:
    """The days every main model uses: complete spread and complete factors."""
    return daily[daily["complete_day"] & daily["factors_complete"]]


def set_ticks(axis, values) -> None:
    """Plain-number ticks on an asinh axis (the default labels overlap near zero)."""
    axis.set_major_locator(matplotlib.ticker.FixedLocator(values))
    axis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}".replace("-", "−")))
    axis.set_minor_locator(matplotlib.ticker.NullLocator())


def save(fig, out: Path, name: str) -> Path:
    path = out / f"{name}.png"
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# Exploration figure
# --------------------------------------------------------------------------

def panel_hourly_distribution(ax, hourly: pd.DataFrame) -> None:
    """Hourly spreads by zone on an asinh x-axis: linear near zero, log-like in the tails."""
    s = hourly["spread"].dropna()
    edges = np.sinh(np.linspace(np.arcsinh(s.min()), np.arcsinh(s.max()), 90))
    for z in zones_in(hourly):
        x = hourly.loc[hourly["zone"] == z, "spread"].dropna()
        ax.hist(x, bins=edges, histtype="step", lw=0.9, color=ZONE_COLORS[z], label=ZONE_LABELS[z])
    ax.set_xscale("asinh", linear_width=1.0)
    set_ticks(ax.xaxis, [-1000, -100, -10, 0, 10, 100, 1000])
    ax.set_yscale("log")
    ax.set(xlabel=f"hourly spread ($/MWh, asinh scale; {SPREAD_SIGN})", ylabel="hours",
           title="(a) Hourly spread distribution")
    ax.axvline(0, color="k", lw=0.5)
    ax.legend(frameon=False, loc="upper left")


def panel_sd_vs_temp(ax, fig, daily: pd.DataFrame) -> None:
    d = model_days(daily)
    d = d[d["zone"] != SYSTEM]
    sc = ax.scatter(d["temp_mean"], d["spread_sd"], c=d["load_peak_norm"], s=4, cmap="viridis", lw=0)
    ax.set_yscale("log")
    ax.set(xlabel="daily mean zone temperature (°F)", ylabel="daily spread SD ($/MWh, log scale)",
           title="(b) Spread volatility vs temperature, four zones")
    cb = fig.colorbar(sc, ax=ax, pad=0.01)
    cb.set_label("peak load / 99.5th pct load", fontsize=7)


def panel_imhr_vs_load(ax, daily: pd.DataFrame) -> None:
    d = model_days(daily)
    for z in zones_in(d):
        g = d[d["zone"] == z]
        ax.scatter(g["load_peak_norm"], g["imhr_da_mean"], s=4, lw=0, color=ZONE_COLORS[z], alpha=0.7,
                   label=ZONE_LABELS[z])
    ax.set_yscale("asinh", linear_width=1.0)
    set_ticks(ax.yaxis, [-1, 0, 1, 3, 10, 30])
    ax.set(xlabel="peak load / 99.5th pct load", ylabel="daily mean DA IMHR (MMBtu/MWh, asinh scale)",
           title="(c) DA implied heat rate (Henry Hub) vs peak load")
    lo, hi = CONFIG["features"]["imhr_typical_range"]
    ax.axhspan(lo, hi, color="0.9", zorder=0, lw=0, label=f"{lo:g}–{hi:g} MMBtu/MWh")
    ax.legend(frameon=False, loc="lower left", markerscale=2.5, ncol=2)


def panel_correlation(ax, fig, daily: pd.DataFrame) -> pd.DataFrame:
    corr = spread_wide(daily).corr()
    labels = [ZONE_LABELS[z] for z in corr.columns]
    im = ax.imshow(corr.to_numpy(), vmin=0.85, vmax=1.0, cmap="Blues")
    ax.set_xticks(range(len(labels)), labels, rotation=30)
    ax.set_yticks(range(len(labels)), labels)
    for i in range(len(labels)):
        for j in range(len(labels)):
            v = corr.iat[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7, color="white" if v > 0.95 else "black")
    ax.set_title("(d) Correlation of daily mean spreads")
    fig.colorbar(im, ax=ax, pad=0.01)
    return corr


def exploration_figure(daily: pd.DataFrame, hourly: pd.DataFrame, out: Path) -> Path:
    fig, axes = plt.subplots(2, 2, figsize=(TEXT_WIDTH_IN, 5.6), constrained_layout=True)
    panel_hourly_distribution(axes[0, 0], hourly)
    panel_sd_vs_temp(axes[0, 1], fig, daily)
    panel_imhr_vs_load(axes[1, 0], daily)
    panel_correlation(axes[1, 1], fig, daily)
    return save(fig, out, "exploration")


# --------------------------------------------------------------------------
# Binned spread SD against peak load
# --------------------------------------------------------------------------

def binned_sd_by_load(daily: pd.DataFrame, n_bins: int = 10) -> pd.DataFrame:
    """Daily spread SD summarized by peak-load decile within each zone (model days)."""
    d = model_days(daily)
    rows = []
    for z in [*zones_in(d), SYSTEM]:
        g = d[d["zone"] == z].copy()
        g["bin"] = pd.qcut(g["load_peak_norm"], n_bins, labels=False)
        for b, gb in g.groupby("bin"):
            rows.append({"zone": z, "bin": int(b) + 1, "n_days": len(gb),
                         "load_peak_norm_lo": gb["load_peak_norm"].min(), "load_peak_norm_hi": gb["load_peak_norm"].max(),
                         "load_peak_norm_mid": gb["load_peak_norm"].median(),
                         "sd_median": gb["spread_sd"].median(), "sd_mean": gb["spread_sd"].mean(),
                         "sd_p25": gb["spread_sd"].quantile(0.25), "sd_p75": gb["spread_sd"].quantile(0.75)})
    return pd.DataFrame(rows).set_index(["zone", "bin"])


def binned_figure(daily: pd.DataFrame, out: Path) -> Path:
    b = binned_sd_by_load(daily)
    b.to_csv(out / "binned_sd_load.csv", float_format="%.6g")
    zones = list(b.index.get_level_values("zone").unique())
    fig, axes = plt.subplots(1, len(zones), figsize=(TEXT_WIDTH_IN, 2.1), sharey=True, constrained_layout=True)
    for ax, z in zip(axes, zones):
        g = b.loc[z]
        c = ZONE_COLORS[z]
        ax.fill_between(g["load_peak_norm_mid"], g["sd_p25"], g["sd_p75"], color=c, alpha=0.2, lw=0)
        ax.plot(g["load_peak_norm_mid"], g["sd_median"], "o-", color=c, ms=2.5, lw=1, label="median")
        ax.plot(g["load_peak_norm_mid"], g["sd_mean"], "x--", color=c, ms=2.5, lw=0.7, label="mean")
        ax.set_yscale("log")
        ax.set_title(ZONE_LABELS[z])
        ax.set_xlabel("peak load / p99.5")
    axes[0].set_ylabel("daily spread SD ($/MWh)")
    axes[0].legend(frameon=False)
    fig.suptitle("Daily spread SD by peak-load decile (band: 25th–75th percentile)", fontsize=8.5)
    return save(fig, out, "binned_sd_load")


# --------------------------------------------------------------------------
# Masked contour maps of the volatility surfaces (Build Plan 6.6, D19)
# --------------------------------------------------------------------------

def surface_for(daily: pd.DataFrame, model: str, zone: str):
    """The fitted descriptive surface the report draws (the owner's terms). Refitting
    here is deterministic and gives the same fit as the model runner."""
    import run_models as rm
    s, info = rm.make_surface(daily, model, zone)
    terms, _ = rm.chosen_terms(s, model, zone)
    return s, s.fit(terms), info


def contour_grid(s, fit, info: dict, radius: float, n: int = 120) -> pd.DataFrame:
    """Fitted values on a grid over the observed factor ranges, in response units
    and in $/MWh (smeared and unsmeared), with the inside-data mask."""
    import run_models as rm
    g = s.predict_grid(fit, n=n, block_values=rm.BLOCK_AT, radius=radius)
    resid = fit.resid.to_numpy()
    return pd.DataFrame({
        g["x_name"]: g["x"].ravel(), g["y_name"]: g["y"].ravel(), "fitted": g["z"].ravel(),
        "dollars_smeared": rm.to_dollars(g["z"].ravel(), resid, info)[0],
        "dollars_unsmeared": rm.to_dollars(g["z"].ravel(), np.zeros(1), info)[0],
        "inside": g["inside"].ravel(), "radius": radius,
    })


def contour_panel(ax, s, fit, grid: pd.DataFrame, value: str, norm, levels, title: str):
    import run_models as rm
    xn, yn = s.factors
    n = int(np.sqrt(len(grid)))
    X, Y = (grid[c].to_numpy().reshape(n, n) for c in (xn, yn))
    Z = np.where(grid["inside"].to_numpy(), grid[value].to_numpy(), np.nan).reshape(n, n)
    cf = ax.contourf(X, Y, Z, levels=levels, norm=norm, cmap="viridis", extend="both")
    ax.contour(X, Y, Z, levels=levels, colors="k", linewidths=0.25)
    fern = pd.Series(s.index).between(*rm.INF["fern_window"]).to_numpy()
    ax.scatter(s.Xreal[~fern, 0], s.Xreal[~fern, 1], s=2, c="white", edgecolors="k", linewidths=0.15, zorder=3)
    ax.scatter(s.Xreal[fern, 0], s.Xreal[fern, 1], s=14, marker="x", c="red", linewidths=0.8, zorder=4,
               label="Jan 23 – Feb 1, 2026")
    can = s.canonical(fit, block_values=rm.BLOCK_AT)
    if can.get("inside_data"):
        xs = can["stationary_real"]
        ax.scatter([xs.iloc[0]], [xs.iloc[1]], marker="*", s=60, c="orange", edgecolors="k", linewidths=0.4,
                   zorder=5, label=f"stationary point ({can['shape']})")
    ax.set_title(title)
    return cf


def volatility_contours(daily: pd.DataFrame, out: Path, value: str = "dollars_smeared",
                        radius: float | None = None, name: str = "contours_volatility") -> Path:
    """Four zone volatility surfaces on one shared dollar color scale, masked to
    where an observed day lies within `radius` (coded units, both factors)."""
    import run_models as rm
    radius = CONFIG["rsr"]["inside_radius"] if radius is None else radius
    zones = rm.zones_for("vol_zone")
    fitted = {z: surface_for(daily, "vol_zone", z) for z in zones}
    grids = {z: contour_grid(s, f, info, radius) for z, (s, f, info) in fitted.items()}
    inside = pd.concat([g.loc[g["inside"], value] for g in grids.values()])
    norm = matplotlib.colors.LogNorm(vmin=inside.min(), vmax=inside.max())
    levels = np.geomspace(inside.min(), inside.max(), 15)
    fig, axes = plt.subplots(2, 2, figsize=(TEXT_WIDTH_IN, 5.4), constrained_layout=True)
    for ax, z in zip(axes.ravel(), zones):
        s, f, _ = fitted[z]
        cf = contour_panel(ax, s, f, grids[z], value, norm, levels, f"{ZONE_LABELS[z]}: {', '.join(f.names)}")
        ax.set(xlabel="daily mean temperature (°F)", ylabel="peak load / 99.5th pct load")
    axes[0, 0].legend(frameon=False, loc="upper left", fontsize=6)
    what = "smeared back-transform, approximate" if value == "dollars_smeared" else "unsmeared: median SD"
    cb = fig.colorbar(cf, ax=axes, shrink=0.9)
    cb.set_label(f"daily spread SD ($/MWh, {what})")
    cb.set_ticks([t for t in (5, 10, 20, 50, 100, 200, 500, 1000) if levels[0] <= t <= levels[-1]])
    cb.set_ticklabels([f"{t:g}" for t in cb.get_ticks()])
    fig.suptitle(f"Volatility surfaces, post-RTC+B regime, masked at radius {radius:g} (coded, both factors)",
                 fontsize=8.5)
    pd.concat([g.assign(zone=z) for z, g in grids.items()]).to_csv(out / f"{name}.csv", index=False,
                                                                     float_format="%.6g")
    return save(fig, out, name)


def system_contour(daily: pd.DataFrame, out: Path, zone_limits: tuple[float, float]) -> Path:
    """The system volatility surface on its own factors, with the zones' color limits."""
    s, f, info = surface_for(daily, "vol_sys", SYSTEM)
    grid = contour_grid(s, f, info, CONFIG["rsr"]["inside_radius"])
    lo, hi = zone_limits
    norm, levels = matplotlib.colors.LogNorm(vmin=lo, vmax=hi), np.geomspace(lo, hi, 15)
    fig, ax = plt.subplots(figsize=(TEXT_WIDTH_IN * 0.6, 3.0), constrained_layout=True)
    cf = contour_panel(ax, s, f, grid, "dollars_smeared", norm, levels, f"System: {', '.join(f.names)}")
    ax.set(xlabel="load-weighted temperature (°F)", ylabel="system peak load / 99.5th pct load")
    ax.legend(frameon=False, loc="upper left", fontsize=6)
    cb = fig.colorbar(cf, ax=ax)
    cb.set_label("daily system spread SD ($/MWh, approximate)")
    cb.set_ticks([t for t in (5, 10, 20, 50, 100, 200, 500, 1000) if lo <= t <= hi])
    cb.set_ticklabels([f"{t:g}" for t in cb.get_ticks()])
    grid.to_csv(out / "contour_system.csv", index=False, float_format="%.6g")
    return save(fig, out, "contour_system")


def radius_sensitivity(daily: pd.DataFrame, out: Path) -> Path:
    """Share of each zone's grid shown at each mask radius (D19 sensitivity)."""
    import run_models as rm
    radii = sorted({*CONFIG["rsr"]["inside_radius_sensitivity"], CONFIG["rsr"]["inside_radius"]})
    rows = []
    for z in rm.zones_for("vol_zone"):
        s, f, info = surface_for(daily, "vol_zone", z)
        for r in radii:
            g = contour_grid(s, f, info, r)
            v = g.loc[g["inside"], "dollars_smeared"]
            rows.append({"zone": z, "radius": r, "share_of_grid_shown": g["inside"].mean(),
                         "dollars_min_shown": v.min(), "dollars_max_shown": v.max()})
    path = out / "contour_radius_sensitivity.csv"
    pd.DataFrame(rows).to_csv(path, index=False, float_format="%.6g")
    return path


# --------------------------------------------------------------------------
# Compact diagnostics (called by the model runner)
# --------------------------------------------------------------------------

def diagnostics_figure(fits: list[tuple[str, object]], out: Path, name: str = "diagnostics") -> Path:
    """One row per model: residuals vs fitted, residuals over time, normal
    probability plot. `fits` is a list of (label, rsr.FitResult)."""
    fig, axes = plt.subplots(len(fits), 3, figsize=(TEXT_WIDTH_IN, 1.55 * len(fits) + 0.3),
                             constrained_layout=True, squeeze=False)
    for row, (label, fit) in zip(axes, fits):
        r = fit.resid
        row[0].scatter(fit.fitted, r, s=2.5, lw=0, color="0.25")
        row[0].axhline(0, color="k", lw=0.6)
        row[0].set_ylabel(label, fontsize=7)
        row[1].plot(r.index, r.to_numpy(), lw=0.5, color="0.25")
        row[1].axhline(0, color="k", lw=0.6)
        row[1].tick_params(axis="x", labelrotation=30)
        (osm, osr), (slope, icpt, _) = stats.probplot(r.to_numpy(), dist="norm")
        row[2].scatter(osm, osr, s=2.5, lw=0, color="0.25")
        row[2].plot(osm, slope * osm + icpt, color="r", lw=0.7)
    axes[0, 0].set_title("residual vs fitted")
    axes[0, 1].set_title("residual over time")
    axes[0, 2].set_title("normal probability plot")
    return save(fig, out, name)


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def write_figures(out: Path) -> list[Path]:
    daily, hourly = load_inputs()
    fig_dir = out / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    paths = [exploration_figure(daily, hourly, fig_dir), binned_figure(daily, fig_dir)]
    paths.append(volatility_contours(daily, fig_dir))
    zone_grid = pd.read_csv(fig_dir / "contours_volatility.csv")
    shown = zone_grid.loc[zone_grid["inside"], "dollars_smeared"]
    paths.append(system_contour(daily, fig_dir, (shown.min(), shown.max())))
    paths.append(volatility_contours(daily, fig_dir, value="dollars_unsmeared", name="contours_volatility_unsmeared"))
    paths.append(radius_sensitivity(daily, fig_dir))
    for p in paths:
        print(f"Wrote {p}")
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=PATHS["outputs"] / "dev", help="output folder (default outputs/dev)")
    args = ap.parse_args(argv)
    try:
        write_figures(args.out)
    except TableError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
