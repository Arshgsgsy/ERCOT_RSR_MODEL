# Findings and decisions log

Companion to the Build Plan v3.2. This file records what was found while building
the pipeline and the decisions made along the way that are **not** in the plan
document. Sessions: **October 3, 2026** (setup, ingest, align, features) and
**October 8, 2026** (gas re-pull, RSR engine, term-selection notebook).

- Study window: October 1, 2025 to September 30, 2026 (D4, unchanged).
- Raw snapshots in use: `data/raw/full/2026-10-03/` for DA, RT, load and
  temperature; `data/raw/full/2026-10-08/` for gas.
- All times are Central (CST = UTC−6, CDT = UTC−5) unless marked UTC.

---

## 1. Data issues by date

| Date (local) | Source | Issue | How it was handled | Status |
|---|---|---|---|---|
| **Oct 1, 2025** | Gas | No lagged gas price in the Oct 3 pull. The rule "flow day F uses the last observation dated before F" needs the Sep 30, 2025 price, but that pull started on Oct 1, 2025. Until the fix, Oct 1 heat rates were empty | **Oct 8, 2026:** gas query now starts on Sep 24, 2025 (`full_pull_start`) and was re-pulled into snapshot `2026-10-08`. Sep 30, 2025 = $3.12, so Oct 1 now has `gas_price` $3.12 and heat rates (North: DA 10.34, RT 8.00 MMBtu/MWh) | **Fixed** |
| **Nov 2, 2025, 1 a.m. (first, CDT)** = 06:00 UTC | Temperature | Values about doubled in all 8 weather zones (Coast 115.9, Southern 131.6, South Central 109.0, North Central 104.75, Far West 98.0, East 94.5, West 93.0, North 91.5 °F). Up to 65.2 °F from the neighbour mean. These are the only values above 115 °F and the only jumps over 20 °F in the year | Both 1 a.m. hours set to the mean of 12 a.m. (05:00 UTC) and 2 a.m. (08:00 UTC) per weather zone, flagged `temp_dst_fix = True`. Raw file untouched | Fixed in `align()` |
| **Nov 2, 2025, 1 a.m. (second, CST)** = 07:00 UTC | Temperature | Identical to the 2 a.m. row (08:00 UTC) in all 8 weather zones: a copy, not a reading | Repaired together with the first 1 a.m. hour, same rule | Fixed in `align()` |
| Nov 27–28, 2025 | Gas | No observations (Thanksgiving and the day after). 5-day gap | Previous price carried forward. Expected holiday gap | OK |
| Dec 25–26, 2025 | Gas | No observations (Christmas and the day after). 5-day gap | Carried forward. Expected holiday gap | OK |
| Jan 1–2, 2026 | Gas | No observations (New Year's Day and the day after). 5-day gap | Carried forward. Expected holiday gap | OK |
| **Jan 8, 2026, 3:30 p.m. CST** | RT prices | 15-minute interval missing in all four zones (missing at the source) | Hour 3 p.m. has `rt_n = 3`, no `rt_price` (D7). `rt_price_partial` kept as a diagnostic only | Handled |
| Jan 23–27, 2026 | Gas, temperature | Winter Storm Fern. Henry Hub $30.72 on Jan 23, $25.01 on Jan 26, $17.19 on Jan 27. Coldest hour of the year 7 °F (Far West, Jan 26 7 a.m.) | Real events, kept (Build Plan 7.4) | OK |
| Jan 25–26, 2026 | Spreads | The two largest system spreads of the year: daily mean **+$335.2/MWh** (Jan 25) and **+$493.9/MWh** (Jan 26). DA averaged $593.8 and $648.1 while RT averaged $258.6 and $154.3, so the day-ahead market priced in scarcity that real time did not deliver. Jan 26 also has the largest daily spread SD ($484.3) | Real events, kept. These days will have high leverage; the plan's leverage and Cook's distance diagnostics cover them | OK |
| Jan 28, 2026 | Spreads | The most negative system spread of the year: **−$107.4/MWh** (DA $76.5, RT $183.9), just after Fern | Real event, kept | OK |
| Mar 8, 2026 | All | Spring-forward day | Checked: 23 hours, 2 a.m. correctly absent, temperatures smooth. No repair needed | OK |
| **Jun 4, 2026, 3:30 p.m. CDT** | RT prices | 15-minute interval missing, all four zones | Hour 3 p.m. has no `rt_price` | Handled |
| **Jul 9, 2026, 3:30 p.m. CDT** | RT prices | 15-minute interval missing, all four zones | Hour 3 p.m. has no `rt_price` | Handled |
| Jul 22, 2026, 5 p.m. CDT | Load | Peak hour of the year: 91,089.5 MW (sum of four forecast zones) | Consistent with ERCOT's listed record of 91,134 MW (an instantaneous peak, not an hourly mean) | OK |
| **Aug 27, 2026, 10:30 a.m. CDT** | RT prices | 15-minute interval missing, all four zones | Hour 10 a.m. has no `rt_price` | Handled |
| Sep 7, 2026 | Gas | No observation (Labor Day) | Carried forward | OK |
| **Sep 30, 2026** | Gas | Not yet published by EIA when pulled on Oct 3, 2026 (latest observation then: Sep 29). The lagged price existed (it uses Sep 29); only the same-date sensitivity value was empty | **Oct 8, 2026:** the re-pull includes Sep 30, 2026 = $3.02, so `gas_price_same` is filled. Every flow day now has both gas prices | **Fixed** |

Three of the four missing RT intervals fall on summer afternoons, when prices
are most volatile. The report should name these hours, not only count them.

**Effect on the daily table:** each of the four missing-interval days (**Jan 8,
Jun 4, Jul 9, Aug 27, 2026**) has 23 of 24 spread hours, so
`complete_day = False` in every zone and in SYSTEM. These days stay in the table
(with their partial-day statistics), but no model uses them. That is 4 of 365
days (1.1%), below the plan's 5% threshold, so no partial-day sensitivity is
needed. All four are weekdays, so the on-peak sample drops from 261 to 257.

---

## 2. Findings

### 2.1 Account limits (measured Oct 3, 2026, in `data/raw/access.json`)

| Limit | Value | Plan assumption |
|---|---|---|
| Plan | Free | — |
| Requests per month | 250 | 250 |
| Rows per month | 500,000 | 500,000 |
| **Rows per response (page size)** | **50,000** | 1,000 (illustration) |
| Rate limit | 1/second, 30/minute, 600/hour | not stated |
| Usage period | Oct 1 – Nov 1, 2026 | — |

- With 50,000-row pages the full required pull costs **8 requests**, not about
  200. **Rows, not requests, are the binding limit.**
- `get_api_usage()` and `get_dataset_metadata()` cost 0 requests.
- Back-to-back calls hit the 1/second limit (HTTP 429). Every call is now
  followed by a 1.1 s pause.

### 2.2 Usage so far

| Step | Requests | Rows |
|---|---|---|
| Gas availability check (Sep 1 – Oct 3, 2026) | 1 | 20 |
| Access check | 0 | 0 |
| One-week test pull | 6 | 4,299 |
| Full-year pull | 8 | 192,950 |
| Gas re-pull, Sep 24, 2025 – Sep 30, 2026 (Oct 8) | 1 | 252 |
| **Total (period Oct 1 – Nov 1, 2026)** | **16** | **197,521 (40%)** |

Every hourly row count matched the estimate exactly. The four RT quarters were
16 rows short in total (4 missing intervals × 4 zones). Gas returned 246 rows
against a rough estimate of 261, because the estimate assumes 5 trading days a
week and ignores holidays.

### 2.3 Dataset structure (confirmed from metadata and the test pull)

- All datasets return `interval_start_utc`/`interval_end_utc` (interval
  start, so no hour-ending shift is needed) plus `_local` versions with Central
  offsets. Timezones survive a parquet round trip.
- Price files: `location`, `spp`. Only the four `LZ_` zones are returned.
- Load: `north`, `south`, `west`, `houston`, `total`. The sum of the four zones
  matches `total` to within 0.02 MW.
- **Temperature is wide**: one row per hour, with the 8 weather zones as
  columns (`coast`, `east`, `far_west`, `north`, `north_central`,
  `south_central`, `southern`, `west`). The plan's worst case (long format, 8
  times the rows) does not apply, so the RT `resample="1 hour"` fallback is not
  needed.
- Temperature has no unit option on the API. The ERCOT product is °F natively.
  °F is checked through a plausible range (−10 to 120 °F) after repair.
- Gas: one series, `RNGWHHD` (Henry Hub spot), units `$/MMBTU`, column
  `period` is the observation date. Oct 3 pull: 246 rows, Oct 1, 2025 – Sep 29,
  2026. Oct 8 re-pull: 252 rows, Sep 24, 2025 – Sep 30, 2026.
- History starts well before the study window for every dataset (prices 2010,
  load 2017, temperature 2017, gas 1993).

### 2.4 Daylight saving in the raw data

- **Prices and load handle Nov 2, 2025 correctly**: two separate 1 a.m. rows
  (06:00 and 07:00 UTC) with distinct values. RT has all 20 quarter-hours
  between 04:00 and 09:00 UTC.
- **Temperature does not**: see Nov 2 in section 1. This is the defect the
  dataset page warns about.
- Mar 8, 2026 is clean in every source.

### 2.5 Temperature vintage check: the series can be called "observed"

Plan 5.1 asked whether a past hour's value stops changing once the hour has
passed. All vintages for Oct 30–31, 2025 were pulled (576 rows):

- 384 hour × weather-zone values had at least one vintage published after the
  hour.
- Only 2 changed after the hour: **Far West, 4 a.m. on Oct 30** (42.0 → 41.0
  °F) and **Far West, 4 a.m. on Oct 31** (47.0 → 45.5 °F). In both cases the
  first value, published one hour after the hour, was revised in the next
  day's file and then never changed.
- **0 values changed once a file was a day or more old.** The `latest`
  vintage used in the main pull is published 2 days 5 hours to 3 days 5 hours
  after each hour, so every value used is final.
- The last forecast made before the hour differed from the final value by 0.7
  to 2.2 °F on average, depending on the weather zone. That fits the final
  value being a measurement rather than a forecast.

**Wording for the report:** ERCOT's observed weather-zone temperatures, final
from one day after the hour.

### 2.6 Hand trace (LZ_NORTH, against the raw files)

These rows were checked independently of `build.py`. The RT mean and SD, the
temperature mean (including the repair) and the gas lag all match exactly:

| Hour (UTC) | Local | RT price | Temp °F | Gas (obs date) |
|---|---|---|---|---|
| 2026-07-22 22:00 | Jul 22, 5 p.m. (peak hour) | 46.1325 | 102.42 | 2.80 (Jul 21) |
| 2025-11-02 06:00 | Nov 2, first 1 a.m. | 30.61 | 48.17 (repaired) | 3.57 (Oct 31) |
| 2025-11-02 07:00 | Nov 2, second 1 a.m. | 24.7875 | 48.17 (repaired) | 3.57 (Oct 31) |
| 2026-03-08 08:00 | Mar 8, 3 a.m. (after the jump) | 52.325 | 49.00 | 3.10 (Mar 6) |

Zone-average temperatures over the year: Houston 24.8–100.0 °F, North
15.5–105.4 °F, South 23.2–102.1 °F, West 8.5–104.4 °F.

### 2.7 Features and the daily table (`data/processed/features_report.json`)

**Day counts that each model will use**

| | Days | Complete days | Factors complete | Main model days | On-peak model days |
|---|---|---|---|---|---|
| Each of the 4 zones | 365 | 361 | 365 | **361** | **257** |
| SYSTEM | 365 | 361 | 365 | **361** | **257** |

**Correlation of daily all-hours mean spreads** (complete days, from our data;
the plan says to report this number rather than the >0.9 from the literature)

| | Houston | North | South | West | SYSTEM |
|---|---|---|---|---|---|
| Houston | 1 | 0.969 | 0.900 | 0.965 | 0.982 |
| North | | 1 | 0.902 | 0.964 | 0.988 |
| South | | | 1 | 0.912 | 0.947 |
| West | | | | 1 | 0.983 |

South is the least correlated with the other zones (about 0.90–0.91).

**Reference values (saved, used for normalization)**

| | Load 99.5th percentile (MW) | System temperature weight (annual load share) |
|---|---|---|
| Houston | 21,832.2 | 0.2438 |
| North | 32,763.6 | 0.3302 |
| South | 23,948.7 | 0.2601 |
| West | 11,377.6 | 0.1659 |
| SYSTEM | 89,334.9 | — |

**Other exit checks**
- **No day has a spread SD of 0**, so `log_spread_sd` is defined on every day.
  Smallest daily SD: Houston $1.61, North $2.08, South $2.11, West $2.11, SYSTEM
  $1.72.
- **Share of days with `load_peak_norm > 1`:** Houston 3.6%, North 3.3%, South
  4.7%, West 5.2%, SYSTEM 4.1%. Kept, never clipped.
- **Typical summary:** the daily mean spread averages $0.7 (South) to $2.1
  (West) per MWh, with medians of $1.15–1.61. The mean daily SD is $14–19.
- `cdd − hdd = temp_mean − 65` holds on every day to within 3 × 10⁻¹⁴.

**Implied heat rates outside 3–30 MMBtu/MWh** (daily means, listed in
`data/processed/imhr_outside_range.csv`, not removed)
- 81 zone-days in total: 58 low and 23 high. No day is both.
- By zone: West 41, South 16, North 15, Houston 9.
- Most listed days fall in **Jan–May 2026** (11, 15, 18, 15 and 7 listed
  zone-days from January to May, high and low together). Low days are mostly
  West: negative or near-zero prices, consistent with wind and solar surpluses. The lowest is West on **Feb 24, 2026**
  (DA 0.4, RT −2.8; RT mean −$8.7/MWh).
- High days are RT scarcity days with ordinary gas prices. The highest is North
  on **Apr 27, 2026** (DA 46.7, RT 63.9; RT mean $162.2/MWh). Others: **Sep 16,
  2026** (North), **Aug 26, 2026** (West), **Apr 24, 2026** and **Sep 22, 2026**
  (South).
- Fern days are **not** on the list: gas spiked along with power, so the ratio
  stayed in range.

**Before and after RTC+B (Dec 5, 2025)**: descriptive only, not a test

| | Mean daily spread pre / post | Mean daily SD pre / post |
|---|---|---|
| Houston | $0.97 / $2.32 | $10.91 / $15.17 |
| North | $0.31 / $1.90 | $11.94 / $18.94 |
| South | −$1.02 / $1.12 | $14.34 / $15.39 |
| West | $1.02 / $2.40 | $16.02 / $19.25 |
| SYSTEM | $0.29 / $1.83 | $11.46 / $15.68 |

The post period contains Winter Storm Fern and the summer, while the pre period
is Oct 1 – Dec 4 only. So the difference is mostly season and events, not
RTC+B. The plan makes no claim about the regime effect.

### 2.8 RSR engine validation against NCSS (Oct 8, 2026)

The expected values were typed from the printed output of NCSS Chapter 330
(Response Surface Regression, Example 1, Odor data; PDF from ncss.com), not
computed by the engine. The engine matches **every printed number** to the
printed precision, with OLS errors (as NCSS uses):

| NCSS report | Values matched |
|---|---|
| Hierarchical model summary | R² 0.881989 |
| Estimation | All 10 coefficients (e.g. Temp −12.125, Ratio² 47.83333), standard errors 7.947353 / 11.69819 / 11.23925, p-values |
| Sequential ANOVA | Regression 18,881.98; Linear 7,143.25; Quadratic 11,445.23; Lin×Lin 293.5; Total error 2,526.417; incremental R² 0.333666 / 0.534614 / 0.013710 |
| Factor ANOVA | Last SS 5,258.016 / 11,044.6 / 3,813.016; F 2.60 / 5.46 / 1.89; p 0.161334 / 0.045377 / 0.251025; term R² 0.245605 / 0.515900 / 0.178108 |
| Last R-Squared (each term) | All 9, e.g. Temp×Height 0.000420, Ratio×Height 0.000572. This is what `drop_one` reports as `r2_loss` |
| Lack of fit (pure error) | Lack of fit SS 2,485.75 (3 df), pure error 40.66667 (2 df), F 40.75, p 0.024047 |
| Optimum | Temp 0.1219125, Ratio 0.1995746, Height 1.770525, value −52.02463; a minimum, outside the design |
| Residuals | Prediction for row 16 (Temp 1, Ratio 1, Height 0): 28.375 |

**Search on** (path from the Build Plan review, not printed by NCSS): removes
Temp×Height (0.00042), Ratio×Height (0.00057), Height² (0.0064), stops at
Temp×Ratio (0.0127), leaving Temp, Ratio, Height, Temp², Ratio², Temp×Ratio.
Matched exactly.

**First look at the real models** (full quadratic, HAC; for orientation only,
before any term choice):

| Model | n | R² (range over zones) | NCSS search keeps |
|---|---|---|---|
| `vol_zone` | 361 | 0.206 – 0.272 | Houston, North: T, L, L², T·L; South: T, L, T·L; West: T, L, T² |
| `lvl_zone` | 361 | 0.049 – 0.091 | Houston: all 5; North, South: T, L, L², T·L; West: T, L, T·L |
| `vol_sys` / `lvl_sys` | 361 | 0.258 / 0.062 | T, L, L², T·L (both) |
| `lvl_zone_onpeak` | 257 | 0.030 – 0.063 | Houston: T, T²; North, South: T, L, L², T·L; West: T, L, T², T·L |

- Volatility surfaces explain about a quarter of the variation; level surfaces
  under a tenth. The plan expects modest R² for spreads.
- West volatility (full quadratic): cubic lack-of-fit p = 0.53; shape a saddle
  inside the data. VIF up to 36 (T·L) and condition number 22.8, the
  temperature–load collinearity the plan anticipates.
- The very cold days (mostly Winter Storm Fern) sit apart from the rest of the
  data and show up as small separate islands on the masked contour plots.

---

## 3. Decisions made in this session (not in the plan)

### Environment and repository
| Decision | Detail |
|---|---|
| Python environment | Python 3.12 in `.venv/` (created with `uv`). Versions pinned in `requirements.txt`: gridstatusio 0.16.0, pandas 3.0.6, pyarrow 25.0.1, numpy 2.5.3, scipy 1.18.1, statsmodels 0.15.0, matplotlib 3.11.2, python-dotenv 1.2.4, pytest 9.1.1 |
| API key | `GRIDSTATUS_API_KEY` in `.env` (git-ignored). `.env.example` is the template |
| Data folders | Contents of `data/` are git-ignored; empty `.gitkeep` files keep the folder structure |
| Test files | `tests/test_ingest.py`, `tests/test_build.py`, `tests/test_rsr.py`, `tests/test_models.py`, `tests/test_data_quality.py` (names chosen here; the plan describes checks, not files) |
| **Jupyter for exploration** (Oct 8) | `jupyterlab` 4.6.4 and `ipykernel` 7.4.0 in a separate `requirements-dev.txt`, because the pipeline never needs them. `.ipynb_checkpoints/` is git-ignored. The notebook is saved without outputs |

### Study window
| Decision | Detail |
|---|---|
| **Keep Oct 1, 2025 – Sep 30, 2026** | Considered shifting two weeks earlier (Sep 17, 2025 – Sep 16, 2026) to avoid late gas data. The shifted window has identical counts (365 days, 8,760 hours, 261 weekdays, both DST days, RTC+B, Fern, the Jul 22 peak). Rejected after checking gas on Oct 3, 2026: data runs through Sep 29, so every day's main lagged gas price exists. Only the Sep 30 same-date sensitivity value is missing |

### Ingest (`ingest.py`, `ingest_checks.py`)
| Decision | Detail |
|---|---|
| Three separate stages, reviewed in between | `python ingest.py access` → `test` → `full`. `full` refuses to run until `access.json` exists and every trial file is complete |
| Snapshot naming | Pull date: `data/raw/full/<YYYY-MM-DD>/` |
| Test week | **Oct 29 – Nov 4, 2025**, chosen to include the Nov 2 fall-back day so DST handling is seen in real files before the full pull |
| Vintage check window | Oct 30 – Oct 31, 2025 (inside the test week) |
| Refresh rule | A changed query never overwrites. It needs a new `--snapshot`. Any mismatch between a manifest and the requested query stops the run |
| `--only <dataset>` | Re-pulls one dataset into a snapshot (e.g. gas after EIA's refresh, or a failed dataset) |
| Client settings | `max_retries = 1`, so failures stop instead of silently spending requests. 1.1 s pause after every call |
| Row budget guard | A pull stops if it would use more than 85% of the rows remaining in the period |
| Per-file usage | Each manifest records `requests_used` and `rows_billed` |
| Vintage check wording | Same-day revisions are reported separately from later changes. The first version labelled the series an "estimate" because of the two Far West revisions; corrected to "observed, final after a day" |
| Code split | Functions the pipeline needs stay in `ingest.py` (planning, manifests and cache, download loop). Safety and one-time checks moved to `ingest_checks.py` (`run_access`, `require_gates`, `estimate`, `check_budget`, `preflight`, `trial_pulls`, `vintage_check`, `check_file`, `run_checks`, `time_column`). `ingest.run_pulls` takes the budget check as a `preflight` hook |
| `--optional` | Not implemented until the main deliverable is done (plan 6.7) |

### Align (`build.py`)
| Decision | Detail |
|---|---|
| Snapshot selection | `config.py` → `raw_snapshot.default = "2026-10-03"`, with `per_dataset` overrides. Since Oct 8: `per_dataset = {"gas": "2026-10-08"}`. `build.py --snapshot` overrides the default. `align_report.json` records the snapshot used for each dataset |
| **Gas pull starts before the window** | `full_pull_start = "2025-09-24"` for gas (Oct 8). The lag rule needs an observation before the first flow day, so the gas query starts a week early. Observations before Oct 1, 2025 are used only as lags; no flow day outside the window is created |
| Raw file verification | `build.py` re-checks every raw file's manifest, query and checksum with `ingest.cache_status` before reading, and records the checksums in `align_report.json` |
| **DST repair trigger** | On a 25-hour day the two 1 a.m. temperature hours are repaired if **either** 1 a.m. value is more than **10 °F** from the mean of 12 a.m. and 2 a.m. in any weather zone, **or** the second 1 a.m. row is identical to the 2 a.m. row in every weather zone. (The plan says "repair only if broken" without defining broken) |
| Temperature range gate | Any weather-zone value outside **−10 to 120 °F** after repair stops the build |
| **Gas stale-price guard** | Inside the series, gaps are weekends and holidays and are carried forward. Gaps longer than **4 days** are reported. Past the last observation a price is never carried: the same-date version stops at the last observation's day, and the lagged version reaches at most the next weekday after it |
| `gas_obs_date` | Stored on every row so "gas observation before flow day" is checked directly |
| RT intra-hour SD | Sample SD (ddof = 1) of the four quarter-hour prices. The plan does not specify |
| System load check | `sys_load` (sum of four zones) compared with Grid Status's published `total`. Difference recorded in the report (max 0.02 MW) |
| Zone labels | `zone` column uses `LZ_HOUSTON`, `LZ_NORTH`, `LZ_SOUTH`, `LZ_WEST` |
| Unit declarations | Added to `config.py` under `units`. Raw gas units must equal `$/MMBTU` or the build stops |
| Outputs | `data/interim/hourly_panel.parquet`, `align_report.json`, `coverage.csv`, `rt_complete_by_month.csv`. The panel is written only if every gate passes and it reads back identically |
| Running the build | `python build.py` runs align then features. `--step align` or `--step features` runs one. `make_features()` reads the saved `hourly_panel.parquet`, keeping the interim file as the boundary between the two steps |

### Features (`build.py`, `make_features()`)
| Decision | Detail |
|---|---|
| **On-peak definition: keep the plan** | Weekdays, hour ending 7–22, **holidays not excluded** (261 weekdays; 257 after the 4 incomplete days). The industry NERC 5×16 definition also excludes six holidays. In this window those are Thanksgiving, Christmas, New Year's Day, Memorial Day, July 4 (a Saturday, so no effect) and Labor Day. On-peak is only a robustness response; the report can mention the difference in one sentence |
| **Degree days from hourly temperatures** | Daily `cdd`/`hdd` = the mean of the hourly degrees above/below 65 °F, not NOAA's (high + low)/2 rule. Reasons: 34% of zone-days have both heating and cooling hours, which the NOAA midpoint hides (e.g. West, **Nov 8, 2025**: 45.9–84.5 °F; hourly CDD 5.5 and HDD 6.5 against NOAA 0.2 and 0.0). It also keeps `cdd − hdd = temp_mean − 65` exactly, consistent with the main temperature factor. Report sentence: "Degree days are computed from hourly temperatures, so they can exceed NOAA-convention values on days that swing across 65 °F" |
| **Keep degree days** | Not used in any main model (the quadratic in `temp_mean` already captures the U shape, and CDD − HDD carries no extra information). Kept for report vocabulary, exploratory plots, a possible robustness swap and the interview answer |
| **SYSTEM price levels** | `da_mean`, `rt_mean`, `rt_sd` and the heat rates for SYSTEM use load-weighted hourly prices (`sys_da_price`, `sys_rt_price`), consistent with the load-weighted system spread. The plan does not define them |
| **Heat-rate list: option A** | Daily mean `imhr_da_mean` or `imhr_rt_mean` outside **3–30 MMBtu/MWh** is listed for investigation, never removed. Rejected: the same range on hourly values (3,391 DA and 4,506 RT hours, about 10–13% of the year, mostly normal renewable-surplus hours), and the hourly 1st–99th percentile (always about 2% by construction). Hourly data for reference: DA median 8.6, RT median 8.3; 1.9% of DA and 4.2% of RT hours are negative |
| Statistics on incomplete days | Computed from the hours that exist and kept in the table, with `complete_day = False`. Models filter on `complete_day` and `factors_complete`. The partial values exist only for a possible partial-day sensitivity |
| `factors_complete` | Added: True when temperature and load cover every expected hour. The plan requires factor coverage but names no column |
| SYSTEM factor columns | On SYSTEM rows `sys_temp` = `temp_mean` (annual load-weighted temperature) and `sys_load_peak_norm` = `load_peak_norm` (system peak ÷ system 99.5th percentile). Both are empty on zone rows |
| Price basis | `da_basis`, `rt_basis` built hourly (each zone minus the load-weighted average of the other three), only when all four zones have a price and a load (D36, descriptive) |
| `n_temp_repaired_hours` | Added to the daily table so the Nov 2 repair is visible at the daily level |
| Weekday numbering | `weekday` 0 = Monday … 6 = Sunday; `hour_ending` = local hour + 1, so both Nov 2 1 a.m. hours are hour ending 2 |
| Outputs | `data/processed/hourly_features.parquet` (35,040 rows), `daily.parquet` (1,825 rows), `features_report.json`, `imhr_outside_range.csv` |

### RSR engine and term selection (`rsr.py`, `run_models.py`, notebook) — Oct 8, 2026
| Decision | Detail |
|---|---|
| **The owner chooses the descriptive terms** | Instead of taking the NCSS search result automatically, the owner picks each model's terms in `notebooks/explore_terms.ipynb`. The NCSS search path is shown as a guide, and the runner will report it beside the owner's choice |
| **Full quadratic stays the model for inference** | Tests and intervals always come from the full quadratic. The chosen terms define the descriptive surface that is drawn and explained. Reason: p-values on a model picked from the same data are optimistic, whether NCSS or a person picks it |
| **Hierarchy enforced** | `Surface.fit` refuses a non-hierarchical term set (e.g. "T^2 needs T") unless `allow_nonhierarchical=True` is passed explicitly. `run_models.selected_terms` refuses non-hierarchical entries with no override. Reason: non-hierarchical models change with the coding, which is hard to defend |
| **Terms chosen per model per zone** | `CONFIG["selected_terms"]` has one entry per `"model|zone"` (e.g. `"vol_zone|LZ_WEST"`) with `terms` and `why`. The notebook prints the line to paste; it never edits `config.py`, so config stays the single record |
| Order of work | Engine and validation first, then the notebook, then the owner explores. The rest of `run_models.py` waits for the choices |
| Term syntax | `"T"`, `"L"`, `"T^2"`, `"L^2"`, `"T*L"` (and cubic terms such as `"T^2*L"` for lack of fit). Internally each term is a tuple of exponents |
| Exploration tools | `drop_one` (per term: removable, blocked by, R² loss, adjusted R², ΔAIC, ΔBIC, HAC p), `compare` (term sets side by side, with cubic lack-of-fit p and shape), `search` (NCSS path), contour plots with the inside-data mask, residual plots |
| Covariance option | `Surface(cov="hac")` by default; `cov="ols"` exists so the engine can be checked against NCSS, which prints OLS errors |
| Lack of fit | The pure-error test is reported when there are at least 2 degrees of freedom of exact repeats; the cubic-extension HAC test is reported whenever the augmented design is full rank. Both can appear together (the plan uses one or the other). Daily data has no exact repeats, so the cubic test is what applies |
| Unique-value check | A factor needs at least m + 1 distinct values for exponent m (NCSS rule); otherwise the fit stops |
| Canonical analysis block value | Evaluated at each block column's mean unless a value is passed (NCSS evaluates covariates at their means). The notebook and runner pass `post_rtcb = 1`, the regime of most of the year (Build Plan 6.6) |
| **asinh uses the raw MAD** | Level responses are asinh((y − median) / MAD) with the median and raw MAD (not scaled by 1.4826) of each model's sample. The plan does not say which. The run stops if MAD = 0 |
| HAC lag for on-peak | 257 on-peak days give ceil(1.3·√257) = **21** lags. The plan's 22 was for 261 days, before the 4 incomplete days were removed |
| Model grid in config | `CONFIG["models"]` holds the five model types (response, transform, factors, labels, sample, priority) and the block. `run_models.prepare_model_data` builds every sample from it |
| Engine settings | `CONFIG["rsr"]`: search cutoff 0.01, ridge tolerance 0.05, inside radius 0.15 (sensitivity 0.10, 0.20), minimum pure-error df 2 |

---

## 4. Open items

### 4.1 Gas re-pull: done on Oct 8, 2026
1. `config.py`: gas `full_pull_start = "2025-09-24"`.
2. `python ingest.py full --only gas --snapshot 2026-10-08`: 1 request, 252
   rows (Sep 24, 2025 – Sep 30, 2026).
3. `raw_snapshot.per_dataset = {"gas": "2026-10-08"}`, then `python build.py`.
4. **Comparison with the Oct 3 pull:** all 246 dates in both pulls have
   identical prices, so EIA made no revisions. The new file adds Sep 24, 25, 26,
   29 and 30, 2025 ($2.88, $2.98, $2.90, $2.93, $3.12) and Sep 30, 2026 ($3.02).
5. **Result:** no flow day is missing `gas_price` or `gas_price_same`. All
   gates pass. The heat-rate review list is still 81 zone-days (Oct 1 falls
   inside the typical range).

As expected, the Oct 3 gas file no longer matches the config's gas query, so
`ingest.py full --snapshot 2026-10-03` now stops on gas. It is kept for the
record; the build reads gas only from `2026-10-08`.

### 4.2 Term selection (owner)
Run `notebooks/explore_terms.ipynb` for each model and zone (8 main models,
then the 6 second-priority ones) and paste each choice into
`CONFIG["selected_terms"]`. Start Jupyter from the project folder:
`.venv/bin/jupyter lab notebooks/explore_terms.ipynb`.

### 4.3 Next build steps
- The rest of `run_models.py` (Build Plan 6.5): fit every model in priority
  order, using the owner's terms for the descriptive model and the full
  quadratic for inference, and write the tables and `run.json`.
- Investigate the 81 heat-rate days in `imhr_outside_range.csv` (plan 6.3 exit
  check).

---

## 5. Implementation notes

- **pandas 3**: `DataFrame.stack()` keeps missing values (older versions dropped
  them). Code that filters with a mask and then stacks must call `.dropna()`.
- **python-dotenv**: `load_dotenv()` without a path fails when Python reads
  the script from stdin. Pass the path explicitly (`load_dotenv(ROOT / ".env")`).
- **Running `ingest.py` directly**: the `__main__` block re-imports the module
  as `ingest`, so `ingest_checks` shares one copy of it.
- Tests: 86 offline tests (20 ingest; 31 build, of which 19 cover `align()`
  and 12 cover `make_features()`; 27 engine; 8 model preparation), none
  touching `data/` or the API. Run with `python -m pytest -q`.
- **Engine bug caught by the tests** (Oct 8): fitted values and residuals were
  built with `pd.Series(statsmodels_series, index=dates)`. pandas aligned the
  statsmodels 0..n−1 index with the date index and returned all NaN. Fixed by
  passing `np.asarray(...)`. Only fits with a date column were affected; the
  Odor check (no dates) had passed.
- Six feature values were also checked by hand against the hourly panel: the
  Nov 2 West mean and SD over 25 hours, the system spread at the Jul 22 peak
  hour, the West DA basis, the Jul 22 North on-peak mean, the degree-day
  identity, and an incomplete day (Jul 9). All match exactly.
