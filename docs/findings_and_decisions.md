# Findings and decisions log

Companion to the Build Plan v3.2. This file records what was found while building
the pipeline and the decisions made along the way that are **not** in the plan
document. Sessions: **October 3, 2026** (setup, ingest, align, features),
**October 8, 2026** (gas re-pull, RSR engine, term-selection notebook) and
**October 9, 2026** (term selection, HAC size problem and block bootstrap,
descriptive tables, runner, figures) and **October 10, 2026** (optional phase:
load forecast error as a third factor; season add-on reviewed and deferred;
out-of-sample check of every surface).

- Study window: October 1, 2025 to September 30, 2026 (D4, unchanged).
- Raw snapshots in use: `data/raw/full/2026-10-03/` for DA, RT, load and
  temperature; `data/raw/full/2026-10-08/` for gas; `data/raw/full/2026-10-11/`
  for the load forecast (optional phase; pulled on Oct 10, see Section 2.15).
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
| Load forecast, trial week Oct 29 – Nov 4, 2025 (Oct 10) | 1 | 169 |
| Load forecast, full year (Oct 10) | 1 | 8,760 |
| Dataset metadata check, load forecast (Oct 10) | 0 | 0 |
| **Total (period Oct 1 – Nov 1, 2026)** | **18** | **206,450 (41%)** |

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

### 2.9 Term selection (Oct 9, 2026)

Full record: `docs/term_selection.md`; evidence per model in
`outputs/term_selection/` (from `notebooks/term_evidence.py`, which makes the
same engine calls as the notebook; the notebook itself was also run top to
bottom and gives the same numbers).

| Model | Houston | North | South | West | SYSTEM |
|---|---|---|---|---|---|
| vol_zone / vol_sys | T, L, L², T·L | T, L, L², T·L | T, L, L², T·L (tie-break) | **T, L, T²** | T, L, L², T·L |
| lvl_zone / lvl_sys | full quadratic | T, L, L², T·L | T, L, L², T·L | T, L, L², T·L (tie-break) | T, L, L², T·L |
| lvl_zone_onpeak | full (mirrors lvl_zone) | mirrors | mirrors | mirrors | — |

- **West volatility is the one real difference in shape**: it curves in
  temperature, not load (dropping T² costs ΔAIC +10.9; the common set is 10.5
  AIC worse). Its T·L coefficient is positive (+0.56) where the other zones'
  are negative.
- **Selected terms are unstable** under block resampling in most zones
  (Houston volatility 58/38 between two sets, West 43/25/13, North 59/24; South
  and SYSTEM about 80%). Differences in which terms were picked are therefore
  not findings; differences in surface shape are.
- **On-peak Houston's own search result (T, T²) rests on one day**: Jan 26 has
  leverage 0.59 and Cook's D 1.89; without it the temperature and load roles
  reverse. Jan 26 has Cook's D 1.8–2.4 in all four on-peak zones.

### 2.10 Winter Storm Fern carries most of the explained variation (Oct 9)

From the runner's diagnostic refit without **Jan 23 – Feb 1, 2026** (10 days,
2.8% of each sample; columns in `results_table.csv`):

| | Full R², all days | Full R², without Fern | Fern share of explained SS | Shape over ordinary days (corr.) |
|---|---|---|---|---|
| vol_zone (4 zones) | 0.206–0.272 | 0.063–0.090 | 0.74–0.76 | **0.97–0.99** |
| vol_sys | 0.258 | 0.073 | 0.79 | 0.99 |
| lvl_zone (4 zones) | 0.049–0.091 | 0.007–0.031 | 0.73–0.90 | **−0.54 to +0.83** |
| lvl_sys | 0.062 | 0.013 | 0.82 | −0.14 |

- **Volatility**: the storm days are high-leverage but fit the surface. Without
  them the shape over ordinary days is the same; they extend the factor range
  rather than change the relationship.
- **Level**: the storm days set the shape. Without them the ordinary-day level
  surface is nearly flat and in North, South, West and SYSTEM tilts the other
  way. Level contours must not be read as describing ordinary days.
- A Fern dummy and Huber/Tukey fits were considered and rejected (research
  review): the dummy is insignificant for level (p 0.4–0.95) and removes the
  corner the surface describes; M-estimators do not guard against high-leverage
  points, and Huber wrongly reported the level surface as stable.

### 2.11 The HAC tests are badly oversized on these designs (Oct 9)

Found by a research review and **verified independently** with
`notebooks/hac_size_check.py`: real factor values and block, responses
replaced by noise with every coefficient zero, 1,000 repetitions per case,
5 designs (four zones and SYSTEM), 4 noise types (iid, AR(1) with each model's
residual autocorrelation, 25-day blocks of the real volatility and level
residuals). Results in `outputs/hac_size/size.csv`.

| Test (correct rate 5%) | HAC, lag 25 (pipeline) | OLS control, iid noise |
|---|---|---|
| Cubic lack of fit | **27–39%** | 4–5% |
| Single-term t-tests | **8–40%** | 4–6% |

Cause (Houston and West volatility, iid noise, lack of fit): HAC lag 25 33% / 41%;
lag 6 16% / 23%; HC0 13% / 18%; **HC3 4.7% / 5.4%**; lag 25 without the Fern days
30% / 24%. Two effects add up: leverage up to 0.55 (Houston) and 0.77 (West) in
the cubic design, which Newey–West does not correct, and a long lag used with
ordinary t and F references (LLSW pair the 1.3√T rule with fixed-b critical
values). The plan's wording ("may be somewhat too small") understates it.

**Consequence:** every HAC p-value and interval in the plan is too optimistic.
Tests and intervals now use a block bootstrap (Section 3, Inference).

**Real-data effect** (`outputs/hac_size/real_*.csv`, B = 999):
- Lack of fit, bootstrap p: volatility 0.23–0.80 (all pass; Houston 0.23–0.26,
  was 0.021 with HAC); level 0.012–0.078 (South 0.011–0.017 and West
  0.019–0.029 below 5%; Houston, North, SYSTEM 0.05–0.08; HAC had 10⁻⁸ to 10⁻³);
  on-peak 0.019–0.17. Stable across 10-, 25- and 50-day blocks.
- Without the Fern days, level lack of fit is 0.06–0.78 and volatility
  Houston 0.024–0.037 (the others 0.10–0.24).
- **Conditional tests: every volatility conclusion survives** (each factor and
  the linear and interaction groups p ≤ 0.04 in all zones) except West's
  quadratic group (0.054) and South's (0.31, as with HAC): 22 of the 23
  volatility tests with HAC p < 0.05 stay below 0.05. **Most level conclusions
  do not**: of the 21 all-hours level tests (lvl_zone, lvl_sys) with HAC
  p < 0.05, only 4 stay below 0.05 with the bootstrap: the linear group in
  Houston (0.033) and South (0.024), South's interaction (0.022) and Houston's
  T factor (0.048).
- Bootstrap standard errors are a median 1.6 times the HAC ones (25-day blocks;
  1.3–1.8 by model, up to about 10 for single coefficients).

**Validation of the bootstrap** (`notebooks/bootstrap_check.py`; same 5
designs and 4 noise types = 20 cases; 300 repetitions × 199 resamples per case;
block lengths 10, 25, 50; `outputs/hac_size/bootstrap_size.csv`,
`bootstrap_coverage.csv`). **It passes.**

| False-rejection rate at nominal 5% | HAC | Bootstrap, 10 / 25 / 50-day blocks |
|---|---|---|
| Lack of fit (q = 4) | 26–38%, mean 33% | means 4.4 / 4.2 / 4.4% (range 2.7–6.3%) |
| Load factor (q = 3) | 18–35%, mean 24% | means 4.4 / 4.5 / 4.8% (range 2.3–7.3%) |
| T·L term (q = 1) | 10–24%, mean 15% | means 4.2 / 4.3 / 4.6% (range 1.7–7.0%) |

| 95% interval coverage (5 coefficients) | HAC | Bootstrap, 10 / 25 / 50-day blocks |
|---|---|---|
| All cases | mean 86%, worst 58% | means 95.1 / 95.3 / 95.2% (range 90–99%) |

- No bootstrap case exceeds 7.5%, the upper edge of the Monte Carlo range for a
  correct 5% test at 300 repetitions (±1.26 points per case). Averaged over 20
  cases (error ±0.28 points) the tests run slightly cautious (4.2–4.8%).
- **West is the one weak spot:** coverage averages 93.2% (25-day blocks), lowest
  for L² and T·L (91–93%), the terms most affected by its high-leverage cold
  days. Other zones average 95.4–96.5%. West intervals should be read as
  roughly 93% intervals.
- Results do not depend on the block length.

### 2.12 Descriptive tables (Oct 9)

From `tables.py` (complete days; figures written to the run folder):

| | All-hours mean / median ($/MWh) | Share of days DA > RT | On-peak mean | Off-peak mean | Corr. with system | RT basis share \|·\| > $20 |
|---|---|---|---|---|---|---|
| Houston | 2.08 / 1.24 | 58.4% | 0.90 | 2.84 | 0.982 | 6.0% |
| North | 1.61 / 1.55 | 57.6% | 0.09 | 2.96 | 0.988 | 6.7% |
| South | 0.73 / 1.83 | 60.7% | 0.09 | 1.42 | 0.947 | 7.8% |
| West | 2.15 / 1.20 | 55.1% | 1.31 | 2.48 | 0.982 | **21.0%** |
| SYSTEM | 1.55 / 1.22 | 59.0% | 0.43 | 2.44 | — | — |

- Day counts: 361 all-hours and off-peak days, 257 on-peak days per zone.
- First principal component of the four zones' daily spreads: **95.2%** of the
  variance.
- West separates from the rest far more often than the other zones (RT basis
  5th–95th percentile −$26.5 to +$34.6, mean +$1.49; others about ±$12–17).
- **Gas sensitivity (D13)**: the lagged and same-date Henry Hub prices differ on
  223 of 361 days, but zone mean heat rates move by at most 0.02 MMBtu/MWh,
  daily heat rates correlate 0.988–0.993, and the out-of-range count is 81 in
  both versions (West 41 → 44, the others one fewer). The gas timing assumption
  does not matter for any reported number.
- **Event table** (top 5 days by daily spread SD per zone): Jan 26 is first in
  every zone (SD $428–558); Jan 25 and Jan 28 follow in most zones. **Mar 23,
  2026** is in the top 5 in all four zones and SYSTEM (SD about $160–170,
  mean spread about −$52 to −$59, mild 64–73 °F, ordinary load): a non-Fern RT
  scarcity event to annotate. Other non-Fern entries: Sep 16 (North), Sep 22
  (South), Aug 26 (West), Apr 24 (Houston, SYSTEM), May 9 (South).
- Binned daily spread SD by peak-load decile shows **no clear "flat then
  steep" rise** in any zone (medians about $5–14 across deciles), so by the
  plan's rule it is not annotated.
- **The level is flat apart from Fern** (`by_temperature_quintile.csv`): the
  coldest fifth of days has a mean daily spread of $7.2–11.8/MWh, but without the
  Fern days −$2.6 to +$1.4; every other quintile is within about ±$3.2.

### 2.13 Mar 23, 2026 and the peak record (Oct 9)

- **Mar 23, 2026 (Monday)**, from our hourly data: a system-wide evening RT
  spike. RT rose from about $100 at hour ending 19 to **about $900/MWh at hour
  ending 22** in all four zones (DA about $109), stayed above $300 at HE23 and
  fell back by HE24. System load was moderate (peak 62.7 GW at HE18). The zones
  moved together, so it was not zonal congestion. It fits the evening net-load
  ramp pattern the market monitor describes; no web source on the specific day
  was found, so the report describes it without a cause.
- **Peak record re-checked** (as reference [25] asks): ERCOT's July 2026
  operational overview lists 91,134 MW on July 22, 2026 at hour ending 18,
  still marked preliminary; EIA reports 91.1 GW at 6 p.m. CT. Our hourly peak
  (91,089.5 MW, 5 p.m. CDT = hour ending 18) matches.

### 2.14 Smearing and the contour maps (Oct 9)

- **Residual spread is not constant** (`smearing_check.csv`, chosen
  surfaces). In the volatility models the residual SD is 1.3–1.9 times larger in
  the hottest and highest-load quintiles than in the calmest (West is flatter),
  and larger after RTC+B (0.68–0.76) than before (0.48–0.59). Smearing with
  each bin's own residuals would move the dollar SD by about −27% to +28%
  against the pooled factor. Per plan 6.6 the dollar contours are labelled
  approximate and an unsmeared (median) version is drawn beside them.
- **Contour maps** (`figures/contours_volatility.png`): the four zones'
  chosen volatility surfaces on one log dollar scale, masked at radius 0.15.
  Fern days sit in the cold corner of every zone and carry the highest values
  (maxima shown $581–1,198/MWh, at the cold edge of the mask; minima $4–8).
  Saddle points lie inside the data for Houston (70.5 °F, 0.588), North
  (68.3 °F, 0.587) and SYSTEM (68.3 °F, 0.620); South's lies outside the data
  and West's surface is a ridge.
- Level surfaces are not drawn as contour maps: they are weak, shaped by Fern
  (Section 2.10) and not distinguishable from flat on most bootstrap tests. The
  temperature-quintile table stands in for them.
- Mask sensitivity (`contour_radius_sensitivity.csv`): the share of the grid
  shown is 29–33% at radius 0.10, 38–43% at 0.15 and 46–52% at 0.20. The
  largest value shown grows with the radius in Houston, North and South (it
  lies at the extrapolated cold edge); West's does not change.

### 2.15 Load forecast error as a third factor (Oct 10, optional phase)

Build Plan 6.7, "three-factor zone models", extended to the system models at
the owner's request. Run `outputs/2026-10-10T2246Z/` (the two-factor outputs in
it are identical to `2026-10-10T0019Z`, see below).

**Data.** `ercot_load_forecast_by_forecast_zone` (ERCOT NP3-560), pulled with
`publish_time = "latest_before:-1 day@10:00:00"`. The account reaches it on the
free plan; it is wide like actual load (`north`, `south`, `west`, `houston`,
`system_total` = the sum of the four). Result: **one row per operating hour, all
8,760 hours, every one from the 9:30 a.m. Central report the day before** (365
distinct publish times, all 0.5 h before the D8 deadline). Checked at ingest and
again in `align()`, row by row. The trial week covers the 25-hour Nov 2.

**F.** Hourly error = 100 × (actual − forecast) / forecast. Daily: signed mean
(`fe_pct_mean`, level models) and RMS (`fe_pct_rms`, volatility models), only on
days where every hour has a forecast. Every model day has one, so **the
three-factor models use exactly the 361 days of the two-factor models**.
- Daily mean error, 5th–95th percentile: about −6% to +5% by zone (system −3.5%
  to +2.3%). Median RMS 2.9–3.2% (system 2.0%).
- **No step at RTC+B worth a control.** The mean error moves by at most 1.6
  points across Dec 5 (Houston −1.6% → 0.0%); RMS is slightly lower after.
- **F is mostly new information**: the T–L quadratic plus block explains 5–22%
  of it, except **West (54–64%)**, where the misses pile up on the Fern days
  (RMS 13.2% against 3.5% on other days; Jan 26 30.4%).
- Largest RMS days: Fern in North, West and SYSTEM (Jan 26); Jan 24 in South
  (26.4%); May–June days in Houston (about 12%).

**Results** (full quadratics, block bootstrap B = 999, same days as the base):

| Model | R² base → with F | F factor p | T / L factor p with F | Surface corr. / SD ratio vs base (F at median) |
|---|---|---|---|---|
| vol Houston | 0.247 → 0.259 | 0.098 | 0.001 / 0.001 | 1.00 / 0.97 |
| vol North | 0.272 → 0.279 | 0.43 | 0.002 / 0.049 | 1.00 / 0.94 |
| vol South | 0.206 → 0.210 | 0.21 | 0.002 / 0.054 | 1.00 / 0.95 |
| vol West | 0.248 → 0.299 | **0.036** | 0.17 / 0.27 | 0.97 / 0.69 |
| vol SYSTEM | 0.258 → 0.267 | 0.16 | 0.001 / 0.039 | 1.00 / 0.95 |
| lvl Houston | 0.091 → 0.119 | **0.005** | 0.048 / 0.015 | 0.99 / 0.98 |
| lvl North | 0.049 → 0.096 | **0.018** | 0.47 / 0.064 | 0.98 / 0.83 |
| lvl South | 0.069 → 0.079 | 0.81 | 0.066 / 0.11 | 1.00 / 1.00 |
| lvl West | 0.066 → 0.179 | **0.001** | 0.27 / 0.80 | −0.60 / 0.51 |
| lvl SYSTEM | 0.062 → 0.077 | 0.44 | 0.51 / 0.47 | 0.99 / 0.87 |

- **Volatility: the temperature–load result holds.** F adds 0.004–0.012 R²
  outside West and is not significant; T stays significant everywhere but West,
  L everywhere but South (0.054) and West; the T–L surface at a typical F is the
  two-factor surface (corr. 1.00, 3–6% flatter).
- **West is the exception in both responses.** F takes over (volatility ΔR²
  +0.05, level +0.11; the NCSS search keeps T, F, T² and F, F²). In West F
  overlaps with T and L (condition number 51, VIF up to 58), so T and L lose
  significance there, which is lost power, not evidence against them. The level
  slice at median F has a different shape (corr. −0.60).
- **Level: F is the clearest new finding.** Houston and North gain 0.03–0.05 R²
  (p 0.005, 0.018). **This survives without Fern** (ad hoc check, B = 299:
  R² without Fern 0.025 → 0.068 Houston, 0.007 → 0.061 North, F p 0.007 both;
  West level 0.063, West volatility 0.020). The linear F coefficient is negative:
  on days when load comes in above the day-ahead forecast, RT clears above DA.
  This is an association; realized load is not exogenous to RT prices.
- Fern still carries about half or more of the explained variation (Fern share
  0.47–0.80).
- Lack of fit passes everywhere (bootstrap p 0.064–0.75). Shapes: saddles, and
  ridges for Houston, North and SYSTEM level.
- **Figure** `contours_volatility_fe.png`: the T–L slice at each zone's median F,
  on the two-factor figure's color scale, masked in all three factors. The cold
  Fern corner is mostly masked out: those days had large F, so they are far from
  the median-F slice. Stationary points are not marked (a three-factor
  stationary point is not on the slice, plan 6.6).

**The two-factor results are untouched.** `2026-10-10T2246Z` against
`2026-10-10T0019Z`: every two-factor row of every model table is identical
(results, coefficients, conditional tests, sequential SS, marginal effects,
diagnostics, selection history, smearing, bootstrap sensitivity); every figure
and its CSV are byte-identical; all 362 earlier facts keep their values (657
facts now); `report_tables.md` differs only by the inserted optional section.
The rebuilt `hourly_panel`, `hourly_features` and `daily.parquet` have every
earlier column identical and only add columns. The pre-change interim and
processed files are kept in `data/archive/two_factor_2026-10-10/`.

**Is F worth it?** R² rises in all 10 models, but it must: the F model nests
the two-factor model. On adjusted R², the F test, the no-Fern refit, the
out-of-sample fit (Section 2.17) and the size of the effect, F earns its place
in **Houston and North level** and **West volatility**:
- Houston level: adjusted R² 0.076 → 0.094, p 0.005, no-Fern R² 0.025 → 0.068.
  Out of sample (week folds, ordinary days) −0.041 → −0.031. The fitted spread
  goes from −$0.7 to −$4.0/MWh between F at its 10th and 90th percentiles.
- North level: adjusted R² 0.033 → 0.070, p 0.018, no-Fern R² 0.007 → 0.061.
  Out of sample −0.063 → −0.020. Spread +$1.4 → −$6.0/MWh.
- West volatility: adjusted R² 0.235 → 0.279, p 0.036, no-Fern R² 0.066 →
  0.144. Out of sample 0.006 → 0.055. Daily SD $10.2 → $15.5/MWh.
- **West level is the partial case.** It has the largest gain (adjusted R²
  0.051 → 0.155, p 0.001) and is the only level surface above 0 out of sample
  on ordinary days. But its F² curvature rests on Fern: predicting a held-out
  January makes it much worse (month folds, all days, chosen F, F²: −0.17 →
  −0.54). Its sign is also the opposite of Houston and North.
- The other five gain nothing: adjusted R² flat or lower, F not significant,
  and out of sample equal or worse.

**Terms** (owner, Oct 10): see `docs/term_selection.md` Section 5. F stays in
the descriptive surface for Houston and North level, West level (F, F² only)
and West volatility. The other six use the approved two-factor surfaces.
System level drops F by owner override. Run `outputs/2026-10-10T2316Z/` uses
these terms. Its two-factor outputs were checked again against
`2026-10-10T0019Z` and are unchanged.

### 2.16 Season add-on: reviewed, deferred (Oct 10)

A proposal to add winter/spring/summer dummies to the block of all 10 main
models was reviewed by a research agent (indicative fits with the engine,
B = 299; scripts outside the repository). Verdict: workable as a sensitivity,
but the proposed reading was too strong. Season explains 61–63% of temperature
(checked); with seasons in the block the RTC+B coefficient rests almost only on
Sep 2026 against Oct–Nov 2025 (pre days: 61 fall, 4 winter); pairs-bootstrap
rank failures rise from 14 to 50 of 999; an intercept shift cannot show that the
shape is not seasonal, and fading T or L would mostly be lost power. Indicative
result: volatility R² +0.03–0.07 (summer calmer at given T and L), T holds, L
loses significance in North, South and SYSTEM; level unchanged. **Owner
decision: not implemented for now.** If revisited: a ladder (Fourier K = 1–2,
season dummies, month dummies), surface match by correlation and SD ratio, and
block values at the sample's season mix.

### 2.17 Out-of-sample check: the surfaces explain, they do not predict (Oct 10)

`notebooks/out_of_sample_check.py` → `outputs/out_of_sample/oos_r2.csv`. For
every model, each fold is left out in turn, the model is refitted by OLS on the
rest, and the left-out days are predicted. Folds are one day (PRESS), one week
(7 consecutive days) or one calendar month. Out-of-sample R² = 1 − SSE / sum
of squares about the scored days' mean; below 0 means worse than their
average. Each fold is scored on all days and on the days outside the Fern
window (Fern stays in training). Four term sets per model: full quadratic,
chosen terms, linear T, L, and the block alone (intercept + post_rtcb) as a
floor.

**Two-factor main models, week folds** (chosen terms; full quadratic in brackets):

| | In-sample R² | Out of sample, all days | Out of sample, non-Fern days | Block only, all / non-Fern | Linear T, L, all days |
|---|---|---|---|---|---|
| vol Houston | 0.247 | 0.183 (0.194) | −0.009 (−0.007) | −0.020 / −0.021 | 0.042 |
| vol North | 0.272 | 0.202 (0.178) | 0.015 (0.020) | −0.022 / −0.027 | 0.118 |
| vol South | 0.206 | 0.140 (0.115) | 0.006 (−0.003) | −0.002 / 0.007 | 0.103 |
| vol West | 0.248 | 0.198 (0.180) | 0.003 (0.006) | −0.009 / −0.002 | 0.016 |
| vol SYSTEM | 0.258 | 0.203 (0.175) | 0.012 (0.004) | −0.017 / −0.016 | 0.120 |
| lvl Houston | 0.091 | −0.201 (−0.201) | −0.041 (−0.041) | −0.021 / −0.021 | −0.034 |
| lvl North | 0.049 | −0.412 (−0.474) | −0.056 (−0.063) | −0.017 / −0.016 | −0.129 |
| lvl South | 0.069 | −0.238 (−0.474) | −0.057 (−0.067) | −0.023 / −0.024 | −0.077 |
| lvl West | 0.066 | −0.291 (−0.574) | −0.068 (−0.074) | −0.012 / −0.009 | −0.064 |
| lvl SYSTEM | 0.062 | −0.306 (−0.536) | −0.053 (−0.063) | −0.018 / −0.017 | −0.115 |

Day folds are higher, about 0.18–0.24 for volatility and about 0 for level.
Neighbouring days stay in training, so that version is optimistic. Month folds
are lower, about 0.11–0.18 for volatility and −0.17 to −0.68 for level. The
on-peak level models are negative in every version.

- **Volatility generalizes, through the stress days.** Every volatility surface
  predicts left-out weeks better than the block alone (0.14–0.20 against about
  −0.02). The quadratic beats the linear surface by 0.04–0.18, so the curvature
  is real and not overfitted. On ordinary days the out-of-sample R² is about 0
  (−0.01 to 0.02). What the surface gets right out of sample is which days are
  extreme (cold, high load). It adds little about day-to-day variation in
  ordinary weather. This matches the Fern share of the explained SS
  (0.74–0.79).
- **Level does not generalize.** Every level surface is below 0 out of sample
  and worse than the block alone. Each added term makes it worse (block >
  linear > chosen > full). The in-sample R² of 0.05–0.09 is fitting a few
  influential days. Predicting them from the other weeks fails, and on ordinary
  days there is nothing left to fit. This is consistent with the bootstrap tests
  (Section 2.11), with the Fern diagnostic (Section 2.10) and with the spread
  being a market forecast error: a level predictable from same-day weather
  would invite virtual bidding.
- **The checks agree; they test different things.** Lack of fit asks whether
  the quadratic shape is adequate. The bootstrap tests ask whether a factor's
  effect is distinguishable from zero, given the dependence between days. The
  out-of-sample R² asks whether the fitted surface, with its estimation error,
  predicts unseen periods. A small but real effect can pass the first two and
  still score about 0 out of sample, when about 6 estimated coefficients cost
  more than the signal is worth (roughly k/n ≈ 0.02 of R²). Volatility on
  ordinary days is that case: no-Fern in-sample R² 0.06–0.09, out of sample
  about 0.
- **F** (Section 2.15) improves the ordinary-day out-of-sample fit only for
  Houston, North and West level and West volatility, and none of the level
  models gets clearly above 0.

---

## 3. Decisions made in this session (not in the plan)

### Environment and repository
| Decision | Detail |
|---|---|
| Python environment | Python 3.12 in `.venv/` (created with `uv`). Versions pinned in `requirements.txt`: gridstatusio 0.16.0, pandas 3.0.6, pyarrow 25.0.1, numpy 2.5.3, scipy 1.18.1, statsmodels 0.15.0, matplotlib 3.11.2, python-dotenv 1.2.4, pytest 9.1.1 |
| API key | `GRIDSTATUS_API_KEY` in `.env` (git-ignored). `.env.example` is the template |
| Data folders | Contents of `data/` are git-ignored; empty `.gitkeep` files keep the folder structure |
| Test files | `tests/test_ingest.py`, `tests/test_build.py`, `tests/test_rsr.py`, `tests/test_models.py` (names chosen here; the plan describes checks, not files). Added Oct 9: `tests/test_tables.py`, `tests/test_run_all.py`. The empty `tests/test_data_quality.py` was deleted on Oct 9 |
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

### Term selection — Oct 9, 2026
| Decision | Detail |
|---|---|
| **Claude proposed, the owner approved** | Instead of the owner working through the notebook, Claude ran the same calls for all 14 model × zone combinations, proposed terms with a written rule, and the owner approved them. A research agent reviewed the three close calls first (Section 2.9) |
| **Rule** | NCSS search (R² loss < 0.01), then the reference set **T, L, L², T·L** if it is within 2 AIC of the search result. The reference set is the most common search result (8 of 16 fits). Models within 2 AIC have substantial support (Burnham & Anderson); inference never depends on the choice |
| AIC guard removed | A first version also required ΔAIC < 2 for each drop. It changed only Houston volatility (kept T², ΔAIC +2.2 while BIC −1.7 and the surfaces are nearly identical), so it was dropped in favour of the plan's D31 |
| West volatility keeps T, L, T² | Adding T·L (0.6 AIC better) does not make West comparable: its T·L sign is opposite to the other zones and the set still lacks L² |
| **On-peak rows get no selection of their own** | They mirror the all-hours level terms for the zone, so the robustness comparison is like with like (plan 6.5). Houston's own search result rested on Jan 26 alone |
| Stored in config | `CONFIG["selected_terms"]`, 14 entries, each with a `why`. All pass the hierarchy check |

### Winter Storm Fern — Oct 9, 2026
| Decision | Detail |
|---|---|
| **Fern stays in the main fit** | As the plan says (7.4). The refit without Jan 23 – Feb 1, 2026 is a diagnostic, now reported as columns of the results table: R² without Fern, Fern share of the explained sum of squares, and the correlation of the two surfaces over ordinary days |
| No Fern dummy, no robust regression | See Section 2.10 |
| Report wording | Volatility: the storm extends the range, the shape holds. Level: the storm sets the shape; level contours are not a description of ordinary days |

### Inference: block bootstrap — Oct 9, 2026 (departs from D16/D17)
| Decision | Detail |
|---|---|
| **Why** | The HAC Wald tests reject 27–39% (lack of fit) and 8–40% (single terms) at nominal 5% on these designs (Section 2.11). Owner chose option C (bootstrap all reported inference) over wording only, bootstrapping lack of fit only, or fixed-b HAC (which fixes the lag problem but not leverage) |
| **Tests** | Residual block bootstrap under the null: fit the model without the tested terms, resample its residuals in circular blocks of consecutive days, add them to its fitted values, refit with the tested terms and recompute the same Wald F. p = (1 + #{F* ≥ F}) / (B + 1). Used for lack of fit and the conditional (group and factor) tests. The statistic is unchanged; only its reference distribution is |
| **Intervals** | Moving-block pairs bootstrap: resample circular blocks of whole days (response, factors, block column together) and refit by OLS, with the coding frozen from the real fit. Percentile intervals for coefficients and marginal effects. A resample whose design is not full rank (no pre-RTC+B day) is dropped and counted (12 of 999 at 25-day blocks in the runner; up to 6% at 50-day blocks) |
| Block length | **25 days** (the HAC lag) for the main results; **10 and 50** reported as sensitivity (`bootstrap_sensitivity.csv`). Results so far are stable across the three |
| Settings | `CONFIG["inference"]`: B = 999, seed 20261009, level 0.95 |
| HAC values kept | Asymptotic HAC p-values stay in every table beside the bootstrap ones, labelled for comparison only |
| Fast Wald | The bootstrap computes Newey–West with `rsr.hac_cov`, which reproduces statsmodels' `cov_hac_simple` (tested to 1e-9); statsmodels is too slow for thousands of refits |
| Smearing | Dollar values use Duan smearing with the residuals of the real fit, applied to each bootstrap draw's own fitted value |

### Tables, figures and runner — Oct 9, 2026
| Decision | Detail |
|---|---|
| `tables.py` (new) | Owner chose a separate module for the descriptive tables: representative spreads (D28), between-zone columns (D36), the compact report table, heat rates and load, gas sensitivity, event table. All on complete days |
| Event table in the main build | From the optional phase; no new data, and it is the clearest way to show Fern and Mar 23. Events are annotated only in the report, after ranking |
| Representative spreads use complete days | Factor completeness is not required for a descriptive spread statistic. All four zones are 361 days either way |
| Marginal-effect days | The observed days nearest the 5th and 95th temperature percentiles (cold, hot) and nearest 65 °F (mild), per model sample. Slopes from the full quadratic, in response units and in $/MWh per °F and per GW (load reference = the zone's 99.5th percentile MW) |
| Fitted range | Reported for the chosen (descriptive) surface, in response units and dollars, with dates |
| Diagnostics figures | One per response (volatility, level) for the four zones; residuals vs fitted, over time, normal probability plot |
| Fixed zone colours | Houston green, North blue, South red, West brown, SYSTEM grey, in every figure |
| `run_all.py` | build → tables → models → figures into `outputs/<run_id>/` (UTC start time). Never overwrites a run folder. `run.json` is written last and records snapshot, raw and processed checksums, git commit and uncommitted files, package versions, seed, decision IDs and a checksum for every output. `--skip-build` reuses `data/processed` and says so in `run.json` |
| Output folders | `outputs/term_selection/` (term evidence) and `outputs/hac_size/` (size checks) are evidence produced before the run, kept outside the run folders |
| **Smearing check** | `smearing_check.csv`: residual SD by temperature and load quintile and by block value, and the dollar value with each bin's own residuals against the pooled ones. Summary columns in `results_table.csv` |
| **Contour maps draw the chosen terms** | The descriptive surface (owner's terms), not the full quadratic, as D31 intends. Refitted in `figures.py` (deterministic, same fit as the runner). Fern days marked with a red ×; stationary point marked only if inside the data. Grid values saved as CSV beside each figure |
| Dollar contours labelled approximate; unsmeared beside | Because residual spread changes across the factors (plan 6.6) |
| Shared log colour scale | Values span about $4 to $1,200/MWh; a linear scale would show nothing outside the Fern corner. The system map uses the zones' limits |
| **No level contour maps** (owner confirmed Oct 9) | See Section 2.14; `by_temperature_quintile.csv` instead |
| `tests/test_data_quality.py` deleted (owner, Oct 9) | It was empty. The structural data checks are build gates in `build.py` (`exit_checks`, `feature_checks`), which stop the build, so a separate offline test file added nothing |
| Level summary table | `tables.by_temperature_quintile`: spread level and SD by each zone's temperature quintile, with and without the Fern days |
| `facts.py` (new step in `run_all.py`) | `facts.csv`/`facts.md`: about 360 reportable numbers, each with file, row and column, for writing and the final number check. `report_tables.md`: the results and representative spread tables rounded for the page |
| README | Rewritten: summary, setup, how to run, layout, inference, future-work list (from plan 6.7), limitations |
| **First real run** | `outputs/2026-10-10T0019Z/` (Oct 10, 00:19 UTC): full rebuild from the raw snapshot, every gate passed, 32 files, about 50 s. Made before the latest code was committed, so `run.json` lists the uncommitted files; rerun after committing for a clean record |

### Optional phase: load forecast error — Oct 10, 2026
| Decision | Detail |
|---|---|
| **Scope** (owner) | The third factor only; zones **and** SYSTEM (the plan names zones only). On-peak models get no F. Season add-on deferred |
| **Structure** (owner chose the simpler of two proposals) | The four models are ordinary entries in `CONFIG["models"]` (`lvl_zone_fe`, `vol_zone_fe`, `lvl_sys_fe`, `vol_sys_fe`, priority 3, with a `base` key), in the same runner and tables, run last. No separate module, flag or output folder. Rejected: an `optional.py` "variant" framework with its own config section and output folder |
| **Two-factor results kept whole** (owner) | The main model entries, terms, figures and facts are unchanged and still produced by every run, so the report can use either model without reverting the code. Checked file by file (Section 2.15) |
| Optional input is soft | `ingest.py --only load_fcst` pulls it (a plain `test`/`full` run still pulls only the required five; the unused `--optional` flag was removed). `build.py` reads it if the snapshot has it, otherwise leaves its columns empty; the runner then skips the F models by name (`ModelUnavailable`) and the F figure is skipped |
| D8 checked twice | `ingest_checks.forecast_vintage_check` (gate at ingest) and `build.prep_load_fcst` / `exit_checks` (gate at build): one row per hour, publish time ≤ 10:00 Central the day before (`ingest.forecast_deadline`, DST-safe; `CONFIG["align"]["forecast_deadline"]`) |
| System forecast | Sum of the four zone forecasts, matching `sys_load` (equals `system_total`) |
| F definitions | Plan 6.7: signed daily mean % for level, daily RMS % for volatility; a day needs every hour's forecast |
| Base comparison | `run_models.base_comparison`: the two-factor base refitted on the same days; R² and adjusted R² gain, the F factor bootstrap p, and the T–L surface with F at its sample median against the base surface over observed days (correlation **and** SD ratio, as the season review recommended). Columns in `results_table.csv`, empty for the main models |
| F held at its median | For the comparison and the figure. RMS is never 0, so "F = 0" is outside the data |
| Marginal effects of F | $/MWh per percentage point (`run_models.dollar_units`) |
| Figure on the two-factor color scale | So the two maps can be compared directly |
| Terms for the F models (owner, Oct 10) | The Oct 9 rule, with the tie-break reference = the zone's approved two-factor terms plus the F terms the search kept. **System level: F dropped by owner override** (the search kept it by a hair, 0.0109 against 0.01; F test p 0.44). `docs/term_selection.md` Section 5 |
| Out-of-sample check (Oct 10) | A diagnostic in `notebooks/` (like the HAC size checks), not a runner step: `notebooks/out_of_sample_check.py` → `outputs/out_of_sample/oos_r2.csv`. Week folds are the headline. Day folds are optimistic (neighbouring days stay in) and month folds also remove a season. No forecasting claim follows from it (D30) |
| `_checks.json` merged, not overwritten | Checking one dataset into a folder that already has checks (the trial folder) would have replaced the earlier record. `run_checks` now merges files and keeps a `history` |
| Snapshot name | The folder is `2026-10-11` but the pull ran on **Oct 10, 2026, 22:38 UTC** (named ahead of the pull; the convention is the pull date). Kept, because renaming would mean editing a raw manifest; the manifest records the real time |
| Archive | `data/archive/two_factor_2026-10-10/`: the interim and processed files and the trial `_checks.json` as they were before this change (git-ignored, like all data) |

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

### 4.2 Term selection: done on Oct 9, 2026
All 14 entries are in `CONFIG["selected_terms"]` (Section 2.9 and
`docs/term_selection.md`). The notebook stays as the tool for checking any one
model by hand.

### 4.3 Done on Oct 9, 2026
Bootstrap validation, smearing check, contour maps, temperature-quintile
table, facts sheet and report tables, README, notebook re-run (runs cleanly
with the approved terms), Mar 23 annotation, peak record re-check, first real
run.

### 4.4 Open as of Oct 10, 2026
- **Commit, then rerun `python run_all.py`** so the final run's `run.json`
  points at a clean commit. Push to GitHub (the report links the repository).
- The notebook file is saved with outputs; decide whether to clear them.
- The 81 heat-rate days: described in Section 2.7 (renewable surpluses in West,
  RT scarcity days); a paragraph in the Data section closes the exit check.
- Optional phase (forecast error, net load, West basis): needs new pulls; the
  owner's call.
- The report itself.
- **F models**: decide whether the report uses them, and where. The suggestion
  is one paragraph and one row group: F adds to level in Houston and North and
  to both responses in West, and leaves the volatility surface unchanged
  elsewhere (Section 2.15).
- **Out-of-sample result in the report** (Section 2.17): suggested as one
  column (week folds, all days and non-Fern days) or two sentences in
  Limitations.
- Superseded run folders `outputs/2026-10-10T2242Z/` and `T2246Z/` (before the
  F terms were approved) can be deleted. `T2316Z` is current.
- Commit, then rerun `python run_all.py` for a clean `run.json`.

---

## 5. Implementation notes

- **pandas 3**: `DataFrame.stack()` keeps missing values (older versions dropped
  them). Code that filters with a mask and then stacks must call `.dropna()`.
- **python-dotenv**: `load_dotenv()` without a path fails when Python reads
  the script from stdin. Pass the path explicitly (`load_dotenv(ROOT / ".env")`).
- **Running `ingest.py` directly**: the `__main__` block re-imports the module
  as `ingest`, so `ingest_checks` shares one copy of it.
- Tests: 86 offline tests on Oct 8 (20 ingest; 31 build, of which 19 cover
  `align()` and 12 cover `make_features()`; 27 engine; 8 model preparation).
  **105 on Oct 9**: + 7 engine bootstrap tests (fast HAC against statsmodels,
  fast Wald, block indices, test statistic and power, pairs bootstrap on a
  noise-free surface), + 4 runner tests (dollar back-transform, marginal effects
  against the engine, a full `run_one`), 6 table tests, 2 `run_all` tests. Then
  the smearing check and the temperature-quintile table: **107 tests**. None
  touches `data/` or the API except `test_git_state` (reads git). `facts.py`
  has no test (it only reformats run outputs; checked on two runs). Run with
  `python -m pytest -q`.
- `test_selected_terms_must_be_hierarchical` assumed `selected_terms` was empty;
  it now removes the entry it needs with `monkeypatch.delitem`.
- **`run.json` bug caught in the dev run**: `git status --porcelain` output was
  `.strip()`ped, which removed the leading space of the first line and cut the
  first letter of the first file name. Now `.rstrip()` only, with a test.
- The notebook is currently saved **with** outputs (the Oct 8 entry above says
  without); to be settled when it is re-run.
- **Engine bug caught by the tests** (Oct 8): fitted values and residuals were
  built with `pd.Series(statsmodels_series, index=dates)`. pandas aligned the
  statsmodels 0..n−1 index with the date index and returned all NaN. Fixed by
  passing `np.asarray(...)`. Only fits with a date column were affected; the
  Odor check (no dates) had passed.
- **Oct 10: 119 tests** (+12): optional pull planned only by name, the D8
  deadline across both DST days, the vintage check flags late and duplicate
  rows, `_checks.json` merge; build without a forecast (empty columns), a 10:05
  a.m. forecast stops the build, hourly and daily F values, one missing forecast
  hour empties only that day's F; runner skips F models without a forecast,
  units by label, the base comparison uses the same days, a full three-factor
  `run_one`.
- **pandas timezone slip** (Oct 10): building a frame from `Series.values` drops
  the UTC timezone, so a merge on `hour_utc` failed (tz-aware against naive).
  Use `reset_index(drop=True)` instead.
- Six feature values were also checked by hand against the hourly panel: the
  Nov 2 West mean and SD over 25 hours, the system spread at the Jul 22 peak
  hour, the West DA basis, the Jul 22 North on-peak mean, the degree-day
  identity, and an incomplete day (Jul 9). All match exactly.
