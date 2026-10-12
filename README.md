# ERCOT Day-Ahead–Real-Time Spread Analysis

A reproducible Python pipeline for studying how temperature, electricity demand,
and renewable generation relate to ERCOT day-ahead–real-time price spreads.
The analysis covers the Houston, North, South, and West competitive load zones,
plus a load-weighted system aggregate, from **October 1, 2025 to September 30,
2026**.

The pipeline downloads and validates market data, builds hourly and daily
features, fits response-surface regressions, evaluates their stability, and
produces tables and figures with traceable source references.

> These models describe relationships using realized same-day conditions. They
> are explanatory analyses, not operational price forecasts or causal estimates.

## Analysis overview

The spread is **day-ahead price minus real-time price**. Positive values indicate
that day-ahead power was more expensive than real-time power.

Two daily responses are modeled:

- **Spread level:** the daily mean spread, transformed with an asinh function
  after centering by the sample median and scaling by the raw MAD.
- **Spread volatility:** the log of the daily standard deviation of hourly
  spreads, measuring within-day price dispersion.

| Model family | Factors | Coverage |
|---|---|---|
| Baseline | Temperature and normalized peak load | Four zones and system aggregate |
| On-peak level | Temperature and normalized peak load | Weekday on-peak spreads in each zone |
| Load forecast error | Baseline factors plus realized load forecast error | Four zones and system aggregate |
| Net-load alternative | Temperature and peak load net of wind and solar | System aggregate |

Full quadratic models provide statistical inference. Approved hierarchical term
sets define the descriptive surfaces. The analysis includes block-bootstrap
inference, marginal effects, masked contour plots, residual diagnostics,
Winter Storm Fern sensitivity, and held-out evaluations.

## Installation

**Requirements:** Python 3.12 and Grid Status API access if downloading inputs.
Run the following from the repository root after cloning:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`.

To configure downloads, in your .env set:

```dotenv
GRIDSTATUS_API_KEY=your_api_key
```

The API key and downloaded data are excluded from version control. Rebuilding
from existing raw snapshots does not require an API key or make API requests.

## Data acquisition

All inputs are obtained through the Grid Status API:

| Input | Role |
|---|---|
| ERCOT day-ahead and 15-minute real-time settlement point prices | Hourly price spreads |
| Load by forecast zone | Zonal and system demand |
| ERCOT weather-zone temperatures | Temperature factors |
| EIA Henry Hub daily spot price | Implied heat rates and gas-price sensitivity |
| Day-ahead load forecasts | Optional load-forecast-error factors |
| Actual hourly wind and solar generation | Optional system net-load factors |

Access must cover the configured datasets and study period. Downloads consume
API quota. The ingestion workflow checks access, trial data, cache integrity,
and available request/row budgets before full downloads.

Run each stage in order and inspect its results before proceeding:

```sh
# Check access and validate a trial week
python ingest.py access
python ingest.py test

# Download the baseline inputs into the configured snapshot directories
python ingest.py full --only da rt load temperature --snapshot 2026-10-03
python ingest.py full --only gas --snapshot 2026-10-08

# Add load forecasts
python ingest.py test --only load_fcst
python ingest.py full --only load_fcst --snapshot 2026-10-11

# Add actual wind and solar generation
python ingest.py test --only wind_actual solar_actual
python ingest.py full --only wind_actual solar_actual --snapshot 2026-10-10-renewables
```

Append `--dry-run` to an ingestion command to inspect the plan without making
API requests. Forecast and renewable inputs are optional for baseline analysis;
provide both extensions to reproduce every model family and comparison.

Snapshot directory names match `CONFIG['raw_snapshot']` in [config.py](config.py).
They are storage labels, not requests for a historical provider vintage. Later
API downloads may contain revised values. **Exact historical reproduction
requires the original raw Parquet files and their accompanying JSON manifests**
in the configured locations, together with the recorded dependency versions.
Raw data are not bundled with this repository.

## Run the pipeline

With raw snapshots available:

```sh
python run_all.py --run-id my-analysis
```

The workflow is:

```text
Raw snapshots → Alignment and features → Descriptive tables → Model fitting
              → Figures → Renewable comparisons → Held-out evaluation → Facts
```

Results are written to `outputs/my-analysis/`. Run IDs must be unique; the runner
refuses to overwrite an existing run folder. To reuse prepared data:

```sh
python run_all.py --skip-build --run-id my-analysis-reused
```

Missing optional inputs skip their corresponding models while preserving the
baseline analysis. Model specifications, sample rules, selected terms, snapshot
locations, and bootstrap settings are centralized in [config.py](config.py).

Individual stages can also be run separately:

```sh
python build.py
python tables.py --out outputs/dev
python run_models.py --out outputs/dev
python figures.py --out outputs/dev
python evaluation.py --out outputs/evaluation
python facts.py --out outputs/my-analysis
```

`facts.py` reads the tables of an existing analysis run. Use the full runner to
regenerate the complete set of outputs, including renewable comparisons.

## Outputs and reproducibility

| Output | Contents |
|---|---|
| `representative_table.csv`, `between_zone.csv`, `heat_rates_and_load.csv` | Descriptive market statistics |
| `results_table.csv`, `coefficients.csv`, `conditional_tests.csv` | Model fits, coefficients, and inference |
| `sequential_ss.csv`, `selection_history.csv`, `marginal_effects.csv` | Explained variation, search paths, and slopes |
| `bootstrap_sensitivity.csv`, `diagnostics.csv`, `smearing_check.csv` | Inference sensitivity and model diagnostics |
| `oos_r2.csv` | Day-, week-, and month-held-out diagnostics |
| `renewable_*.csv`, `renewable_coverage.json` | Matched gross/net-load fits, component comparisons, predictions, and coverage |
| `figures/` | Descriptive plots, supported-region contours, and diagnostic figures |
| `facts.csv`, `facts.md`, `report_tables.md` | Analysis summaries with source references |
| `run.json`, `source_snapshot/` | Run provenance and the source/reference files used |

Each successful run records settings, random seeds, input/output checksums,
Python and package versions, and Git state when available. Generated data and
results are excluded from version control.

Three historical inference-calibration summaries are supplied as explicit inputs
in [reference/inference_validation.json](reference/inference_validation.json).
They describe earlier simulations on the original gross-load designs; a rebuild
includes these reference facts but does not rerun the original calibration study.

## Interpretation and limitations

- Reported tests use a residual block bootstrap; coefficient intervals use a
  pairs block bootstrap. Defaults are 999 draws, a 25-observation block, 10/50
  sensitivity blocks, and seed 20261009.
- Full-quadratic inference and reduced descriptive surfaces serve different
  purposes. Selected terms should not be interpreted as confirmatory evidence
  of differences between zones.
- Winter Storm Fern has substantial influence on the fitted relationships;
  exclusion diagnostics are reported alongside baseline results.
- Dollar-valued contours use pooled residual smearing, an approximation when
  residual dispersion changes across factor values. Support masks limit
  extrapolation beyond observed conditions.
- Baseline held-out evaluation uses full-sample response transforms and approved
  terms. Renewable holdouts refit response transforms, coding, and smearing on
  training days. Both use realized inputs and can train on later dates, so neither
  is a prospective forecasting backtest.
- Net load uses delivered renewable generation, which reflects dispatch and
  curtailment. Henry Hub is a common gas proxy, and weather-zone mappings
  approximate load-zone geography.

See [methodology and limitations](docs/methodology.md) for full definitions,
data handling, selection rules, and evaluation caveats.

## Tests

Install development dependencies and run the offline suite:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

Tests cover NCSS reference results, daylight saving transitions, missing-data
rules, ingestion/cache validation, model transformations, renewable evaluation,
and reproducibility helpers. They use synthetic data and temporary files;
no API calls or downloaded study data are required.

## Repository structure

| Path | Responsibility |
|---|---|
| `config.py` | Study settings, model specifications, selected terms, and seeds |
| `ingest.py`, `ingest_checks.py` | API acquisition, manifests, and validation |
| `build.py` | Hourly alignment and daily feature construction |
| `rsr.py` | Response-surface engine, inference, and diagnostics |
| `run_models.py` | Model orchestration and result tables |
| `tables.py`, `figures.py`, `facts.py` | Descriptive statistics and analysis presentation |
| `evaluation.py`, `renewable_validation.py` | Held-out and matched-model evaluation |
| `run_all.py` | End-to-end runner and reproducibility manifest |
| `docs/`, `reference/` | Methodology and versioned reference inputs |
| `tests/` | Automated regression tests |
