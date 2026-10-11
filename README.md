# ERCOT DA–RT spread response surfaces

Response surface regression of ERCOT day-ahead minus real-time (DART) price
spreads on temperature and peak load, for the four competitive load zones
(Houston, North, South, West) and the load-weighted system, October 1, 2025 to
September 30, 2026.

- **Responses**: daily spread volatility (log of the daily SD of hourly spreads)
  and daily spread level (asinh-standardized all-hours mean spread).
- **Factors**: daily mean temperature and daily peak load relative to the zone's
  99.5th percentile, plus a post-RTC+B intercept block (from December 5, 2025).
- **Method**: the NCSS response surface procedure (full quadratic, hierarchical
  backward search), built on statsmodels and validated against the NCSS Odor
  example. Tests and intervals use a block bootstrap (see *Inference* below).
- **Data**: every input comes from Grid Status through its API: ERCOT DA and
  15-minute RT settlement point prices, load by forecast zone, ERCOT
  weather-zone temperatures, and the EIA Henry Hub daily spot price.

All surfaces are **explanatory**, built from same-day realized conditions. None
is a forecast.

## Setup

```bash
uv venv .venv --python 3.12        # or python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt   # Jupyter, for the notebook only
cp .env.example .env               # then add GRIDSTATUS_API_KEY
```

## Running

The data pull is separate and is never run by the rebuild, so a rebuild never
spends API requests.

```bash
# 1. Pull (spends API requests; run once, stage by stage, reviewing in between)
python ingest.py access            # measure account limits -> data/raw/access.json
python ingest.py test              # one-week trial pulls -> data/raw/test/
python ingest.py full --snapshot 2026-10-03   # the full year -> data/raw/full/<snapshot>/
python ingest.py test --only load_fcst        # optional phase: load forecast, trial week
python ingest.py full --only load_fcst --snapshot 2026-10-11

# 2. Rebuild everything from the raw snapshot named in config.py
python run_all.py                  # build -> tables -> models -> figures -> facts -> outputs/<run_id>/
python run_all.py --skip-build     # reuse data/processed (recorded in run.json)

# Single steps
python build.py [--step align|features]
python tables.py --out outputs/dev
python run_models.py --out outputs/dev [--models vol_zone lvl_zone]
python figures.py --out outputs/dev

# Tests (offline; none touches data/ or the API)
python -m pytest -q
```

Every run folder holds the tables, the figures and a `run.json` with the
settings, decision IDs, raw snapshot and checksums, package versions, code
commit and a checksum for every output. `run_all.py` never overwrites a run
folder.

## Layout

| Path | What it is |
|---|---|
| `config.py` | Every setting, in one dictionary, with decision IDs |
| `ingest.py`, `ingest_checks.py` | Pulls with manifests, cache identity, row and request budget guards |
| `build.py` | `align()`: one row per zone × UTC hour (DST-safe grid, coverage flags). `make_features()`: hourly features and the daily modeling table |
| `rsr.py` | The RSR engine: terms and hierarchy, coding, NCSS search, sequential sums of squares, conditional tests, lack of fit, canonical analysis, inside-data masks, marginal effects, block bootstrap. Knows nothing about electricity |
| `tables.py` | Descriptive tables: representative spreads, between-zone columns, heat rates and load, gas sensitivity, event table |
| `run_models.py` | Fits every model in priority order and writes the result tables |
| `figures.py` | Exploration figure, binned volatility, masked contour maps, diagnostics |
| `run_all.py` | The runner and `run.json` |
| `facts.py` | Facts sheet (every reportable number with its source) and report-ready tables for a run |
| `notebooks/explore_terms.ipynb` | Interactive term choice for one model at a time |
| `notebooks/term_evidence.py` | Term-selection evidence for all models → `outputs/term_selection/` |
| `notebooks/hac_size_check.py`, `bootstrap_check.py`, `bootstrap_real.py` | Size and coverage checks of the HAC and bootstrap inference → `outputs/hac_size/` |
| `notebooks/out_of_sample_check.py` | Leave-one-day/week/month-out R² of every surface → `outputs/out_of_sample/` |
| `docs/findings_and_decisions.md` | Everything found while building and every decision not in the plan |
| `docs/term_selection.md` | The chosen descriptive terms and why |
| `data/raw`, `data/interim`, `data/processed` | Raw snapshots (immutable, with manifests), the hourly panel, features and the daily table. Git-ignored |
| `outputs/<run_id>/` | Tables, figures and `run.json` of one run |

## Inference

On these designs the Newey–West HAC tests reject far more often than their
nominal level: about 27–39% for the cubic lack-of-fit test and 8–40% for single
terms at a nominal 5% (`notebooks/hac_size_check.py`). The causes are
high-leverage days (largely Winter Storm Fern) and a long lag used with
ordinary t and F references. Reported tests therefore use a **residual block
bootstrap under the null** and intervals a **moving-block pairs bootstrap**
(25-day blocks; 10 and 50 as sensitivity). Asymptotic HAC values are kept
beside them for comparison. The full quadratic is the model for inference; the
chosen reduced terms only define the surface that is drawn.

## Optional phase: load forecast error as a third factor

`lvl_zone_fe`, `vol_zone_fe`, `lvl_sys_fe` and `vol_sys_fe` refit the main
models with a third factor F, the day-ahead load forecast error (ERCOT's
forecast from the 9:30 a.m. report the day before, checked against the
10 a.m. bid-close rule row by row): the signed daily mean for level and the
daily RMS for volatility, in % of forecast. They run last, in the same tables,
and each results row compares the model with its two-factor base on the same
days. The two-factor models, their terms, figures and facts are unchanged and
are still produced by every run. Without a forecast in the snapshot, the
F models are skipped. Findings: `docs/findings_and_decisions.md`, Section 2.15.

## Not done (future work)

What each would need to be done properly:

| Item | What it would need |
|---|---|
| Net-load system model | System wind and solar actuals and forecasts |
| West basis model | West load-zone wind; the basis is its own descriptive response, not a stacked test |
| Load-weighted zone temperature | Weather-zone load |
| Ex-ante surfaces, rolling folds, a persistence baseline and Diebold–Mariano tests | One declared forecast origin, every input proven available at that time, gas release times rather than observation dates, fold-local selection and scaling |
| RTC+B regime test | The pre-period is October 1 – December 4 only, so season and regime cannot be separated. Would need joint intercept and slope tests on shared support and other years as a placebo |
| Battery-charging load adjustment | Zonal charging data or a tested allocation rule |
| Heat-rate and demand surfaces, gross against net load | Not required by the assignment |
| Deseasonalized SD, log IQR, HSL-based errors | Page budget; HSL field meaning unverified |
| Quantile surfaces, regional gas prices | Own inference questions; no free daily Texas hub history |

## Limitations in brief

- Load zones are load-weighted averages of many nodes, so zone comparisons
  understate local congestion, especially in the Far West.
- Henry Hub is used for every zone, so heat rates are Henry-Hub-based and miss
  Waha.
- Temperature groups ERCOT weather zones by load zone with equal weights;
  Houston rests on Coast alone.
- About ten Winter Storm Fern days carry most of the explained variation; the
  level surfaces in particular are shaped by them.
- The surfaces explain; they do not predict. Out of sample (leave one week out)
  the volatility surfaces keep R² 0.14–0.20 through the stress days, but are
  about 0 on ordinary days. The level surfaces do worse than a constant.
- The main surfaces use two factors and cannot separate expected conditions
  from surprises. The optional F models add the load forecast surprise only; it
  is realized, so its link to spreads is an association.
