# ERCOT DA–RT spread response surfaces

Reproduce ERCOT day-ahead minus real-time (DART) spread analysis for Houston,
North, South, West, and the load-weighted system, October 1, 2025 through
September 30, 2026. The pipeline prepares data, fits response surfaces, evaluates
them, and generates tables, figures, and a facts sheet with source references.

These are **explanatory surfaces using realized same-day inputs**, not operational
forecasts or causal estimates. Baseline temperature × load models, load-forecast
error extensions, and system net-load alternatives are all retained.

## Setup

Use Python 3.12 and the pinned dependencies:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `GRIDSTATUS_API_KEY` in `.env` for downloads. The key and downloaded data are
excluded from Git. API access must cover the configured datasets and study dates.

For development and offline regression tests:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use synthetic data and temporary files; they do not download data or
change cached study inputs. Jupyter is not required for reproduction.

## Obtain the inputs

Data acquisition is separate from rebuilding. Inspect the access/trial results
before requesting a full year; ingestion validates caches and API budgets.

```sh
python ingest.py access
python ingest.py test
python ingest.py full --snapshot 2026-10-03

# Dataset-specific snapshots used by the current configuration
python ingest.py full --only gas --snapshot 2026-10-08
python ingest.py test --only load_fcst
python ingest.py full --only load_fcst --snapshot 2026-10-11
python ingest.py test --only wind_actual solar_actual
python ingest.py full --only wind_actual solar_actual --snapshot 2026-10-10-renewables
```

`python ingest.py full --dry-run` previews the baseline pull plan without API
calls. Downloads consume API quota; `run_all.py` never downloads anything.

Snapshot names match `CONFIG['raw_snapshot']`. They are directory labels, not
requests for the provider's historical data vintage. For exact historical
reproduction, obtain the original raw Parquet files **and their JSON manifests**,
preserve their relative paths, and use the recorded software versions. A later
API download may contain revisions. Cache validation refuses to silently replace
an existing snapshot with different query settings or altered bytes.

```text
data/raw/full/
  2026-10-03/             # DA, RT quarterly chunks, zonal load, temperature
  2026-10-08/             # gas
  2026-10-11/             # load forecast
  2026-10-10-renewables/  # actual wind and solar
```

Forecast and renewable inputs are optional for baseline runs. To reproduce all
extensions and their comparisons, supply all these snapshots. Missing optional
inputs skip the corresponding models without removing baseline results.

## Rebuild and evaluate

```sh
python run_all.py --run-id my-analysis
```

The runner executes alignment/features → tables → models → figures → renewable
validation → baseline held-out evaluation → facts. Results go to
`outputs/my-analysis/`; existing run folders are never overwritten. Use a new
run ID for each rebuild. `--skip-build` reuses prepared data and records that
choice in the manifest.

Each successful run includes `run.json` with settings, seeds, input/output
checksums, Python/package versions, Git state when available, and a snapshot of
the public source and reference files. Local experiments and report drafts are
not included in that snapshot.

Individual steps:

```sh
python build.py
python tables.py --out outputs/dev
python run_models.py --out outputs/dev
python figures.py --out outputs/dev
python evaluation.py --out outputs/evaluation
python facts.py --out outputs/my-analysis
```

| Output | Purpose |
|---|---|
| `representative_table.csv`, `between_zone.csv`, `heat_rates_and_load.csv` | Descriptive market statistics |
| `results_table.csv`, `coefficients.csv`, `conditional_tests.csv` | Model fits and inference |
| `sequential_ss.csv`, `selection_history.csv`, `marginal_effects.csv` | Explained variation, search paths, slopes |
| `bootstrap_sensitivity.csv`, `diagnostics.csv`, `smearing_check.csv` | Inference and model diagnostics |
| `oos_r2.csv` | Day/week/month held-out diagnostics for available models |
| `renewable_*.csv`, `renewable_coverage.json` | Matched gross/net-load fits, component comparisons, held-out scores and coverage |
| `figures/` | Descriptive plots, masked contours, residual diagnostics, sensitivity outputs |
| `facts.csv`, `facts.md`, `report_tables.md` | Numbers with sources and formatted analysis tables |

## Method and interpretation

- Volatility is log daily within-day spread SD; level is the asinh-standardized
  daily mean spread.
- Full quadratics supply inference. Approved hierarchical reduced terms in
  `config.py` supply descriptive surfaces.
- Tests use a residual block bootstrap; intervals use a pairs block bootstrap.
  The main block length is 25 observations, with 10/50 sensitivity, 999 draws,
  and seed 20261009.
- Forecast-error extensions add realized load forecast error. Net-load models
  replace gross system load with load minus delivered wind and solar.
- `evaluation.py` preserves the original diagnostic's full-sample transforms and
  approved terms. Renewable holdouts refit response transforms, factor coding,
  and smearing on training days. Both use realized held-out inputs and can train
  on dates after the held-out period; neither is a forecasting backtest.

See [methodology and limitations](docs/methodology.md) for sample rules, data
repairs, term selection, smearing, matched comparisons, and evaluation caveats.

The three historical inference-calibration summaries used in the facts sheet
are explicit, versioned inputs in
[`reference/inference_validation.json`](reference/inference_validation.json).
They describe prior simulations on the original gross-load designs, not fresh
calibration of every extension. Rebuilds reproduce those reference facts without
depending on historical folders under `outputs/`.

## Source layout

| File | Responsibility |
|---|---|
| `config.py` | Study settings, model specifications, selected terms, seeds |
| `ingest.py`, `ingest_checks.py` | API acquisition, manifests, validation, budget guards |
| `build.py` | Hourly alignment and daily features |
| `rsr.py` | Response-surface fitting, inference, prediction, diagnostics |
| `run_models.py` | Model orchestration and result tables |
| `tables.py`, `figures.py`, `facts.py` | Analysis presentation |
| `evaluation.py`, `renewable_validation.py` | Held-out and matched-model evaluation |
| `run_all.py` | Offline end-to-end runner and reproducibility manifest |
| `tests/` | Automated regression tests |

Generated data and results remain local. Interactive development work and the
separate report evidence packager are outside the public reproduction workflow.
