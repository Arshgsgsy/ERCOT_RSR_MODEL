# Methodology and limitations

## Study and preparation

The study covers local Central days from October 1, 2025 through September 30,
2026, for Houston, North, South, and West competitive load zones. Values and
approved descriptive term sets live in `config.py`. Decision IDs in source and
configuration are stable provenance labels.

Inputs are Grid Status API data: ERCOT day-ahead and 15-minute real-time
settlement point prices, load by forecast zone, weather-zone temperatures, EIA
Henry Hub spot gas, and optional load forecasts and actual renewable generation.
Parquet snapshots have query/checksum manifests. Rebuilds make no API requests.

Alignment uses one row per zone × UTC hour. Hourly real-time price requires all
four quarter-hours. Duplicates and malformed intervals fail validation. Local
days retain their expected 23, 24, or 25 hours across daylight saving changes.
Daily models require complete spreads and factors; missing hours are not zero.

Weather-zone temperatures are averaged equally within configured mappings:
Coast for Houston; North/North Central/East for North; South Central/Southern for
South; and West/Far West for West. These approximate load-zone geography. A
flagged fall-back-hour repair uses neighboring midnight/2 a.m. temperatures when
the repeated hour fails the configured consistency rule. Actual weather-zone load
weighting was not implemented; no robustness to that alternative is claimed.
System temperature uses the zone weights saved by the feature builder.

System hourly prices/spreads are load-weighted across all four zones and require
all zones. Daily peak load is normalized by saved hourly p99.5 load; values above
one are not clipped. On-peak means weekdays HE7–22, including holidays. Weekends
have no on-peak response.

Henry Hub is a common gas proxy, missing regional differences such as Waha.
The primary lagged mapping and same-date sensitivity are both retained.
Observation dates are not proof of release-time availability. Gas gaps and
outlying heat rates are reported, not automatically discarded as errors.

## Responses, models, and terms

Spread is DA minus RT; positive is a day-ahead premium. Volatility is log(daily
SD of hourly spreads), measuring within-day dispersion rather than deseasonalized
uncertainty. Level is asinh((daily mean − sample median)/raw MAD); zero MAD fails
validation. R² is on the transformed response, not dollar-price risk explained.

Baseline factors are temperature T and normalized peak load L, with a post-RTC+B
intercept block from December 5, 2025. Full quadratics include T, L, T², L², T×L.
Factors are min/max coded within the fit sample. Hierarchy requires lower-order
parents of retained powers/interactions.

Full quadratics supply inference; approved reduced terms only define descriptive
surfaces. The NCSS backward search removes the hierarchy-permitted term with
smallest R² loss while that loss is below 0.01. For baseline descriptive choices,
T, L, L², T×L is the comparability reference when within 2 AIC of the search
result. On-peak choices mirror each zone's all-hours level choice. Selection is
exploratory; differences in selected terms are not confirmatory zonal differences.

All approved choices and reasons are in `CONFIG['selected_terms']`. Search
history is emitted separately. Without an approved choice, the runner uses and
labels the search result. Net-load alternatives retain full quadratics for
inference and description. Forecast-error choices use the approved base terms
plus retained F terms as the comparability reference, with hierarchy preserved.

## Inference and diagnostics

Tests use residual block resampling under the null; intervals use pairs block
resampling. The main circular block is 25 observations, with 10/50 sensitivity,
999 draws, and seed 20261009. HAC asymptotic results remain comparisons.
Historical size studies on the original gross-load designs motivated this choice,
but do not establish nominal size or coverage for every extension.

Three historical calibration ranges are frozen inputs in
`reference/inference_validation.json`, preserving original source selectors and
CSV SHA-256 hashes. The pipeline reads and snapshots this reference explicitly.
It neither reruns the simulations nor reads whatever experiment outputs happen
to be present locally. Reproducing the original simulation study itself is
outside the public analysis pipeline's scope.

Outputs retain sequential sums of squares, conditional group/factor tests,
cubic lack of fit, canonical shape, marginal effects, autocorrelation, VIF,
leverage, and Cook's distance. Nonsignificant lack of fit is not proof of
adequacy, and absence of a significant difference does not establish equivalence.

Winter Storm Fern (January 23–February 1, 2026) remains in baseline fits. No-Fern
refits and ordinary-day shape comparisons remain visible. The legacy level
diagnostic preserves its original response transform after excluding Fern;
matched renewable comparisons instead recompute transforms on the reduced
sample. Fern's share of explained sums of squares is descriptive, not causal.

Dollar surfaces use pooled residual smearing: exp(yhat) × mean(exp(residual))
for volatility, and median + MAD × mean(sinh(yhat + residual)) for level.
Changing residual dispersion limits these approximations. Local-versus-pooled
checks and unsmeared contours remain. Contours mask unsupported combinations
using a coded-space radius; radius sensitivity is retained. Stationary points
outside observed support are not practical optima or recommendations.

## Forecast-error models

The optional F factor is realized load forecast error relative to the prior-day
forecast. Publication must meet the configured 10 a.m. Central day-before
deadline, checked row by row. Level uses daily signed mean error; volatility
uses daily RMS error, both as percentages of forecast.

Extended full quadratics are compared with their bases on identical days. The
added-factor test covers all F terms. Contour comparisons hold extra factors at
sample medians. This realized surprise is not an ex-ante signal. The RTC+B
intercept cannot separate regime from season in this one-year sample.

## Renewable and net-load alternatives

Actual generation is `gen_system_wide` in the configured ERCOT hourly wind/solar
datasets: delivered output affected by dispatch and curtailment, not uncurtailed
potential. No zonal renewable allocation is fabricated.

Hourly net load is gross load − wind − solar. Daily peak is the maximum of that
hourly difference, not the difference of separate maxima, normalized by saved
annual hourly p99.5 net load. Every expected hour requires both generation
series and load; missing renewable values are not filled. Baseline sample flags
remain unchanged when optional inputs are absent.

Gross/net models are non-nested replacements. Use `renewable_comparison.csv`
for identical-day comparisons, not unmatched baseline results. Replacing load
has no nested added-factor p-value. Dollar slopes use the appropriate net-load
reference. Wind-only/solar-only diagnostics remain. Observed improvement is not
a causal renewable effect or evidence about evening-ramp steepness.

`renewable_validation.py` produces matched fits, week/month holdouts, predictions,
paired losses, and coverage. Response transforms, coding, and smearing are fitted
on training days per fold. Fern-excluded comparisons remove Fern from both
training and evaluation. Load-normalization references still come from feature
building. Paired intervals use 1,999 circular 25-observation block resamples of
fixed held-out loss differences; folds are not refitted per draw. These are
descriptive uncertainty intervals, not independently calibrated superiority
tests. Component comparisons were exploratory, not preplanned confirmation.

## Held-out evaluation and interpretation

`evaluation.py` preserves day/week/month exclusion diagnostics for full,
approved, T/L-linear, and block-only term sets. Fold coefficients are fitted on
remaining observations, but response transforms and approved term choices use
the full sample. R² uses scored observations' own mean in its denominator. The
non-Fern score excludes Fern from scoring but leaves it in training folds.

Both this diagnostic and renewable holdouts use realized inputs and may train
on dates after the held-out period. They measure generalization of explanatory
relationships, not deployable forecasts or nested selection validation. An
ex-ante study needs a declared origin, input availability checks, fold-local
preparation/selection, chronological evaluation, and forecasting baselines.

Exact reproduction requires matching raw snapshots/manifests, settings, and
software. Later downloads can differ through revisions. Runs record hashes,
settings, seeds, and public source/reference snapshots. Data, results, interactive
experiments, and report-packaging work are local and are not imported by the
public pipeline.
