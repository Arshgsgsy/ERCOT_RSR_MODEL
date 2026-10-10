# Report tables

## Four-zone results (full quadratic for tests; chosen terms for the surface)

### Volatility: log daily spread SD

| Zone | n | R² full | R² chosen | Chosen terms | Lack of fit p (boot) | Shape | Fitted range ($/MWh) | R² w/o Fern | Corr. with no-Fern surface (ordinary days) |
|---|---|---|---|---|---|---|---|---|---|
| Houston | 361 | 0.247 | 0.238 | T, L, L², T·L | 0.262 | saddle | 7.0 to 308 | 0.063 | 0.97 |
| North | 361 | 0.272 | 0.266 | T, L, L², T·L | 0.650 | saddle | 9.5 to 601 | 0.090 | 0.99 |
| South | 361 | 0.206 | 0.206 | T, L, L², T·L | 0.464 | saddle | 9.2 to 473 | 0.067 | 0.97 |
| West | 361 | 0.248 | 0.239 | T, L, T² | 0.790 | ridge | 10.9 to 438 | 0.066 | 0.98 |

### Level: asinh-standardized daily mean spread

| Zone | n | R² full | R² chosen | Chosen terms | Lack of fit p (boot) | Shape | Fitted range ($/MWh) | R² w/o Fern | Corr. with no-Fern surface (ordinary days) |
|---|---|---|---|---|---|---|---|---|---|
| Houston | 361 | 0.091 | 0.091 | T, L, T², L², T·L | 0.082 | minimum | -3.2 to 134 | 0.025 | 0.83 |
| North | 361 | 0.049 | 0.046 | T, L, L², T·L | 0.062 | saddle | -6.0 to 52 | 0.007 | -0.20 |
| South | 361 | 0.069 | 0.068 | T, L, L², T·L | 0.011 | saddle | -4.6 to 160 | 0.017 | -0.01 |
| West | 361 | 0.066 | 0.066 | T, L, L², T·L | 0.028 | saddle | -10.3 to 148 | 0.031 | -0.54 |

### System volatility

| Zone | n | R² full | R² chosen | Chosen terms | Lack of fit p (boot) | Shape | Fitted range ($/MWh) | R² w/o Fern | Corr. with no-Fern surface (ordinary days) |
|---|---|---|---|---|---|---|---|---|---|
| System | 361 | 0.258 | 0.258 | T, L, L², T·L | 0.680 | saddle | 8.1 to 520 | 0.073 | 0.99 |

### System level

| Zone | n | R² full | R² chosen | Chosen terms | Lack of fit p (boot) | Shape | Fitted range ($/MWh) | R² w/o Fern | Corr. with no-Fern surface (ordinary days) |
|---|---|---|---|---|---|---|---|---|---|
| System | 361 | 0.062 | 0.057 | T, L, L², T·L | 0.065 | saddle | -4.9 to 64 | 0.013 | -0.14 |

### Robustness: level on the weekday on-peak mean

| Zone | n | R² full | R² chosen | Chosen terms | Lack of fit p (boot) | Shape | Fitted range ($/MWh) | R² w/o Fern | Corr. with no-Fern surface (ordinary days) |
|---|---|---|---|---|---|---|---|---|---|
| Houston | 257 | 0.063 | 0.063 | T, L, T², L², T·L | 0.033 | minimum | -4.4 to 60 | 0.039 | 0.92 |
| North | 257 | 0.030 | 0.027 | T, L, L², T·L | 0.153 | saddle | -5.0 to 43 | 0.005 | 0.42 |
| South | 257 | 0.043 | 0.042 | T, L, L², T·L | 0.052 | saddle | -5.1 to 72 | 0.034 | 0.31 |
| West | 257 | 0.055 | 0.044 | T, L, L², T·L | 0.156 | saddle | -3.3 to 73 | 0.036 | 0.58 |

## Representative spreads ($/MWh, daily means over complete days; spread = DA − RT)

| Zone | All-hours mean | Median | SD | 5th–95th pct | DA > RT days | On-peak mean | Off-peak mean | Corr. with system | \|RT basis\| > $20 |
|---|---|---|---|---|---|---|---|---|---|
| Houston | 2.08 | 1.24 | 32.4 | -14.2 to 11.9 | 58% | 0.89 | 2.84 | 0.982 | 6.0% |
| North | 1.61 | 1.55 | 37.7 | -20.2 to 13.3 | 58% | 0.09 | 2.96 | 0.988 | 6.7% |
| South | 0.73 | 1.83 | 28.9 | -20.5 to 12.4 | 61% | 0.09 | 1.42 | 0.947 | 7.8% |
| West | 2.15 | 1.20 | 39.6 | -20.3 to 15.9 | 55% | 1.31 | 2.48 | 0.982 | 21.0% |
| System | 1.55 | 1.22 | 33.5 | -18.4 to 11.8 | 59% | 0.43 | 2.44 | — | — |

Days: 361 all-hours and off-peak, 257 on-peak per zone.
