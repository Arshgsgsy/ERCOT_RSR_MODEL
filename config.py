"""Every setting for the pipeline, in one plain dictionary.

Values live here. Reasons live in the Build Plan, Section 9, and each setting
carries the ID of its decision record. run.json records which values made
which outputs.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent

CONFIG = {
    # D4: October 1, 2025 to September 30, 2026, as local Central days.
    # "end" is exclusive, so the window is exactly 365 local days / 8,760 hours.
    "study": {
        "start": "2025-10-01",
        "end": "2026-10-01",
        "tz": "US/Central",
        "decision": "D4",
    },

    # D5: four competitive load zones. Price location -> forecast-zone load column.
    # Load column names are confirmed in the one-week test pull.
    "zones": {
        "HOUSTON": {"location": "LZ_HOUSTON", "load_column": "houston"},
        "NORTH": {"location": "LZ_NORTH", "load_column": "north"},
        "SOUTH": {"location": "LZ_SOUTH", "load_column": "south"},
        "WEST": {"location": "LZ_WEST", "load_column": "west"},
        "decision": "D5, D6",
    },

    # D9: equal-weight average of ERCOT weather zones per load zone.
    # Column names are confirmed in the one-week test pull.
    "weather_zone_map": {
        "HOUSTON": ["coast"],
        "NORTH": ["north", "north_central", "east"],
        "SOUTH": ["south_central", "southern"],
        "WEST": ["west", "far_west"],
        "decision": "D9",
    },

    "paths": {
        "raw_test": ROOT / "data" / "raw" / "test",
        "raw_full": ROOT / "data" / "raw" / "full",
        "access": ROOT / "data" / "raw" / "access.json",
        "interim": ROOT / "data" / "interim",
        "processed": ROOT / "data" / "processed",
        "outputs": ROOT / "outputs",
    },

    # Which raw snapshot the build reads. A dataset re-pulled later (e.g. gas
    # after EIA's weekly refresh) can be taken from its own snapshot.
    "raw_snapshot": {
        "default": "2026-10-03",
        # Gas re-pulled Oct 8, 2026 from Sep 24, 2025, after EIA's weekly
        # refresh. It fills Sep 30, 2025 (lag for Oct 1) and Sep 30, 2026.
        # Load forecast (optional phase) pulled Oct 10, 2026 with the D8 vintage rule. The folder
        # is named 2026-10-11 (named ahead of the pull); the manifest records the real time.
        "per_dataset": {"gas": "2026-10-08", "load_fcst": "2026-10-11"},
    },

    # Step 2, align() (Build Plan 6.2 and 7.3).
    "align": {
        # D7: an hour has an RT price only with all four 15-minute prices.
        "rt_intervals_per_hour": 4,
        # 7.3: the November fall-back 1 a.m. temperatures are repaired (both set
        # to the mean of 12 a.m. and 2 a.m., and flagged) if either 1 a.m. value
        # is more than this far from that mean in any weather zone, or if the
        # second 1 a.m. row is identical to the 2 a.m. row in every weather zone.
        "dst_repair_max_dev_f": 10.0,
        # Plausible Texas range for any weather-zone temperature after repair.
        "temp_range_f": (-10.0, 120.0),
        # D13: gas gaps longer than this inside the series are reported. Shorter
        # gaps are weekends and holidays and are carried forward.
        "gas_max_gap_days": 4,
        # D8 (optional phase): a forecast for an operating hour must be published at or
        # before this local time, this many days before the operating day. Checked per row.
        "forecast_deadline": {"days_before": 1, "local_time": "10:00"},
        "decision": "D2, D3, D7, D8, D9, D13",
    },

    # Step 3, make_features() (Build Plan 5.3, 5.4 and 6.3).
    "features": {
        # Spread = DA minus RT. Positive means buyers paid a day-ahead premium.
        # On-peak: weekdays, hour ending 7 to 22. Holidays are not excluded
        # (owner decision: keep the plan's definition, 261 weekdays).
        "onpeak_hour_ending": (7, 22),
        # load_norm = load / the zone's 99.5th percentile hourly load (never clipped).
        "load_norm_quantile": 0.995,
        # Degree days from hourly temperatures: daily cdd/hdd = mean of the hourly
        # degrees above/below the base (owner decision, not the NOAA high/low rule).
        "degree_day_base_f": 65.0,
        # IMHR is left empty when gas is at or below this price.
        "gas_floor": 0.50,
        # Daily mean IMHR outside this range is listed for investigation, never
        # removed (owner decision: option A, applied to daily means).
        "imhr_typical_range": (3.0, 30.0),
        # post_rtcb = 1 from this operating day on (RTC+B go-live).
        "rtcb_go_live": "2025-12-05",
        # A partial-day sensitivity is needed if more than this share of days fail.
        "max_incomplete_day_share": 0.05,
        "decision": "D10, D11, D12, D22, D36",
    },

    # The RSR engine (Build Plan 6.4). Passed to rsr.Surface as settings.
    "rsr": {
        "search_cutoff_r2": 0.01,       # NCSS default
        "ridge_tolerance": 0.05,        # smallest/largest |eigenvalue| below this -> ridge
        "inside_radius": 0.15,          # coded units; sensitivity at 0.10 and 0.20
        "inside_radius_sensitivity": (0.10, 0.20),
        "min_pure_error_df": 2,
        # HAC lag = ceil(1.3 * sqrt(n)): 25 for 365 days, 22 for 261 (rsr.hac_lag).
        "decision": "D16, D17, D19, D31",
    },

    # Inference for reported tests and intervals (owner decision Oct 9, 2026; departs
    # from D16/D17). On the project's designs the HAC Wald tests reject 27-39% (lack
    # of fit) and 8-40% (single terms) at nominal 5% (notebooks/hac_size_check.py).
    # Tests: residual block bootstrap under the null; intervals: moving-block pairs
    # bootstrap. Block length 25 days (= the HAC lag), 10 and 50 as sensitivity.
    "inference": {
        "bootstrap_B": 999,
        "block_len": 25,
        "block_len_sensitivity": (10, 50),
        "seed": 20261009,
        "level": 0.95,
        # Diagnostic refit without Winter Storm Fern and the days after it (Build Plan 7.4).
        "fern_window": ("2026-01-23", "2026-02-01"),
        # Marginal effects at three observed days per model sample (Build Plan 6.4):
        # the days nearest the 5th and 95th temperature percentiles, and nearest 65 °F.
        "effect_days": {"cold_quantile": 0.05, "hot_quantile": 0.95, "mild_temp_f": 65.0},
        "decision": "D16, D17 (departure), D31, D41",
    },

    # The model grid (Build Plan 6.5). Every model has the post-RTC+B block.
    # Level responses: asinh((y - median) / MAD) on the model sample.
    # Volatility responses: log of the daily spread SD (already in daily.parquet).
    "models": {
        "lvl_zone": {"priority": 1, "zones": "zones", "response": "spread_mean", "transform": "asinh_mad",
                     "factors": ["temp_mean", "load_peak_norm"], "labels": ["T", "L"], "sample": "all"},
        "vol_zone": {"priority": 1, "zones": "zones", "response": "log_spread_sd", "transform": "none",
                     "factors": ["temp_mean", "load_peak_norm"], "labels": ["T", "L"], "sample": "all"},
        "lvl_sys": {"priority": 2, "zones": "system", "response": "spread_mean", "transform": "asinh_mad",
                    "factors": ["sys_temp", "sys_load_peak_norm"], "labels": ["T", "L"], "sample": "all"},
        "vol_sys": {"priority": 2, "zones": "system", "response": "log_spread_sd", "transform": "none",
                    "factors": ["sys_temp", "sys_load_peak_norm"], "labels": ["T", "L"], "sample": "all"},
        "lvl_zone_onpeak": {"priority": 2, "zones": "zones", "response": "spread_onpeak_mean",
                            "transform": "asinh_mad", "factors": ["temp_mean", "load_peak_norm"],
                            "labels": ["T", "L"], "sample": "weekday"},
        # Optional phase (Build Plan 6.7, owner decision Oct 10, 2026): the main models
        # refitted with the load forecast error as a third factor F, zones and system.
        # Signed daily mean (% of forecast) for level, daily RMS for volatility. "base"
        # names the two-factor model it is compared with on the same days. The main
        # models above are unchanged; these run last and are skipped if no forecast
        # was loaded.
        "lvl_zone_fe": {"priority": 3, "zones": "zones", "response": "spread_mean", "transform": "asinh_mad",
                        "factors": ["temp_mean", "load_peak_norm", "fe_pct_mean"], "labels": ["T", "L", "F"],
                        "sample": "all", "base": "lvl_zone"},
        "vol_zone_fe": {"priority": 3, "zones": "zones", "response": "log_spread_sd", "transform": "none",
                        "factors": ["temp_mean", "load_peak_norm", "fe_pct_rms"], "labels": ["T", "L", "F"],
                        "sample": "all", "base": "vol_zone"},
        "lvl_sys_fe": {"priority": 3, "zones": "system", "response": "spread_mean", "transform": "asinh_mad",
                       "factors": ["sys_temp", "sys_load_peak_norm", "fe_pct_mean"], "labels": ["T", "L", "F"],
                       "sample": "all", "base": "lvl_sys"},
        "vol_sys_fe": {"priority": 3, "zones": "system", "response": "log_spread_sd", "transform": "none",
                       "factors": ["sys_temp", "sys_load_peak_norm", "fe_pct_rms"], "labels": ["T", "L", "F"],
                       "sample": "all", "base": "vol_sys"},
        "block": ["post_rtcb"],
        "decision": "D8, D11, D12, D22, D33",
    },

    # Descriptive model terms chosen by the owner in notebooks/explore_terms.ipynb,
    # one entry per model and zone. Must be hierarchical. The full quadratic is
    # always the model used for inference; these are the surfaces presented.
    # Rule (approved Oct 9, 2026; evidence in docs/term_selection.md and outputs/term_selection/):
    # the NCSS search (R² loss < 0.01), then the reference set T, L, L^2, T*L if it is
    # within 2 AIC of the search result. On-peak rows mirror the all-hours level choice.
    "selected_terms": {
        "vol_zone|LZ_HOUSTON": {"terms": ["T", "L", "L^2", "T*L"],
                                "why": "NCSS search drops T^2 (R² loss 0.0088); surface within 10% of the full quadratic"},
        "vol_zone|LZ_NORTH": {"terms": ["T", "L", "L^2", "T*L"],
                              "why": "NCSS search drops T^2 (R² loss 0.0060, ΔAIC +1.0, ΔBIC −2.9)"},
        "vol_zone|LZ_SOUTH": {"terms": ["T", "L", "L^2", "T*L"],
                              "why": "tie-break: search keeps T, L, T*L; reference set is 1.9 AIC better"},
        "vol_zone|LZ_WEST": {"terms": ["T", "L", "T^2"],
                             "why": "NCSS search drops L^2 and T*L; T^2 needed (dropping it costs ΔAIC +10.9), "
                                    "reference set 10.5 AIC worse"},
        "lvl_zone|LZ_HOUSTON": {"terms": ["T", "L", "T^2", "L^2", "T*L"],
                                "why": "NCSS search keeps every term (each costs R² ≥ 0.013); reference set 4.1 AIC worse"},
        "lvl_zone|LZ_NORTH": {"terms": ["T", "L", "L^2", "T*L"],
                              "why": "NCSS search drops T^2 (R² loss 0.0034, ΔAIC −0.7)"},
        "lvl_zone|LZ_SOUTH": {"terms": ["T", "L", "L^2", "T*L"],
                              "why": "NCSS search drops T^2 (R² loss 0.0007, ΔAIC −1.7)"},
        "lvl_zone|LZ_WEST": {"terms": ["T", "L", "L^2", "T*L"],
                             "why": "tie-break: search keeps T, L, T*L; reference set within 1.0 AIC"},
        "vol_sys|SYSTEM": {"terms": ["T", "L", "L^2", "T*L"],
                           "why": "NCSS search drops T^2 (R² loss 0.0006, ΔAIC −1.7)"},
        "lvl_sys|SYSTEM": {"terms": ["T", "L", "L^2", "T*L"],
                           "why": "NCSS search drops T^2 (R² loss 0.0046, ΔAIC −0.2)"},
        "lvl_zone_onpeak|LZ_HOUSTON": {"terms": ["T", "L", "T^2", "L^2", "T*L"],
                                       "why": "mirrors lvl_zone; own search (T, T^2) driven by Jan 26 (Cook's D 1.9)"},
        "lvl_zone_onpeak|LZ_NORTH": {"terms": ["T", "L", "L^2", "T*L"], "why": "mirrors lvl_zone"},
        "lvl_zone_onpeak|LZ_SOUTH": {"terms": ["T", "L", "L^2", "T*L"], "why": "mirrors lvl_zone"},
        "lvl_zone_onpeak|LZ_WEST": {"terms": ["T", "L", "L^2", "T*L"], "why": "mirrors lvl_zone"},
        # Optional phase, three-factor models (approved Oct 10, 2026; docs/term_selection.md Section 5).
        # Same rule, with the tie-break reference = the zone's approved two-factor terms plus any F
        # terms the search kept. Where F is dropped the surface is the approved two-factor surface.
        "vol_zone_fe|LZ_HOUSTON": {"terms": ["T", "L", "L^2", "T*L"],
                                   "why": "NCSS search drops all F terms (F loss 0.0039); F kept in 44% of block resamples"},
        "vol_zone_fe|LZ_NORTH": {"terms": ["T", "L", "L^2", "T*L"],
                                 "why": "NCSS search drops all F terms (F loss 0.0079; adding F ΔAIC −1.9 but ΔBIC +2.0)"},
        "vol_zone_fe|LZ_SOUTH": {"terms": ["T", "L", "L^2", "T*L"],
                                 "why": "tie-break: search keeps T, L, T*L; reference set 1.9 AIC better"},
        "vol_zone_fe|LZ_WEST": {"terms": ["T", "L", "F", "T^2"],
                                "why": "tie-break: search keeps T, F, T^2; reference (base T, L, T^2 plus F) within 1.1 AIC; "
                                       "F kept in 98% of resamples"},
        "vol_sys_fe|SYSTEM": {"terms": ["T", "L", "L^2", "T*L"],
                              "why": "NCSS search drops all F terms (F loss 0.0089; adding F ΔAIC −2.4 but ΔBIC +1.5)"},
        "lvl_zone_fe|LZ_HOUSTON": {"terms": ["T", "L", "F", "T^2", "L^2", "T*L"],
                                   "why": "NCSS search = base terms plus F; dropping F costs ΔAIC +5.4; F kept in 96% of resamples"},
        "lvl_zone_fe|LZ_NORTH": {"terms": ["T", "L", "F", "L^2", "T*L"],
                                 "why": "NCSS search = base terms plus F; dropping F costs ΔAIC +15.7; F kept in 100% of resamples"},
        "lvl_zone_fe|LZ_SOUTH": {"terms": ["T", "L", "L^2", "T*L"],
                                 "why": "NCSS search drops all F terms (F loss 0.0012)"},
        "lvl_zone_fe|LZ_WEST": {"terms": ["F", "F^2"],
                                "why": "NCSS search; best by BIC, within 1.0 AIC of the best by AIC; reference set 4.0 AIC worse. "
                                       "F^2 rests on the Fern days (without them the search keeps F only)"},
        "lvl_sys_fe|SYSTEM": {"terms": ["T", "L", "L^2", "T*L"],
                              "why": "owner override (Oct 10): search keeps F by a hair (loss 0.0109 vs cutoff 0.01) but the "
                                     "F factor test is p 0.44 and F is kept in 67% of resamples; dropping F is ΔAIC +2.2, ΔBIC −1.7"},
    },

    # Units of the interim hourly panel (Build Plan 5.2 and 7.5).
    "units": {
        "da_price": "$/MWh",
        "rt_price": "$/MWh",
        "rt_price_partial": "$/MWh",
        "rt_intrahour_sd": "$/MWh",
        "load": "MW",
        "sys_load": "MW",
        "load_fcst": "MW",
        "sys_load_fcst": "MW",
        "fe": "MW",
        "fe_pct": "% of forecast",
        "sys_fe_pct": "% of forecast",
        "temp": "°F",
        "temp_range": "°F",
        "gas_price": "$/MMBtu",
        "gas_price_same": "$/MMBtu",
        "raw_gas_units": "$/MMBTU",
        "spread": "$/MWh",
        "abs_spread": "$/MWh",
        "sys_spread": "$/MWh",
        "da_basis": "$/MWh",
        "rt_basis": "$/MWh",
        "imhr_da": "MMBtu/MWh",
        "imhr_rt": "MMBtu/MWh",
        "load_norm": "multiple of 99.5th percentile load",
        "cdd_h": "°F",
        "hdd_h": "°F",
        "cdd": "°F·day",
        "hdd": "°F·day",
    },

    # Grid Status client behaviour. The account allows 1 request per second,
    # so every call is followed by a pause. Retries are kept low so a failure
    # stops the run instead of silently spending requests.
    "gridstatus": {
        "api_key_env": "GRIDSTATUS_API_KEY",
        "timezone": "market",
        "max_retries": 1,
        "pause_seconds": 1.1,
        # Stop a pull if it would use more than this share of the remaining rows.
        "max_share_of_remaining_rows": 0.85,
    },

    # The one-week trial. It covers the November 2, 2025 fall-back day, so the
    # repeated 1 a.m. hour is seen in real files before the full pull.
    "test_week": {"start": "2025-10-29", "end": "2025-11-05"},

    # Two-day all-vintage temperature check (Build Plan 5.1): does a past hour's
    # value stop changing once the hour has passed?
    "temperature_vintage_check": {"start": "2025-10-30", "end": "2025-11-01"},

    # Required datasets, pulled by ingest.py. rows_per_hour sets the expected
    # row count (gas is daily; its estimate uses trading days).
    "datasets": {
        "da": {
            "id": "ercot_spp_day_ahead_hourly",
            "priority": "required",
            "params": {
                "filter_column": "location",
                "filter_value": ["LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST"],
                "filter_operator": "in",
            },
            "rows_per_hour": 4,
            "decision": "D5",
        },
        "rt": {
            "id": "ercot_spp_real_time_15_min",
            "priority": "required",
            "params": {
                "filter_column": "location",
                "filter_value": ["LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST"],
                "filter_operator": "in",
            },
            "rows_per_hour": 16,
            # Pulled in quarterly chunks so a failure loses one quarter, not the year.
            "chunks": [
                ("q1", "2025-10-01", "2026-01-01"),
                ("q2", "2026-01-01", "2026-04-01"),
                ("q3", "2026-04-01", "2026-07-01"),
                ("q4", "2026-07-01", "2026-10-01"),
            ],
            "decision": "D5, D7",
        },
        "load": {
            "id": "ercot_load_by_forecast_zone",
            "priority": "required",
            "params": {},
            "rows_per_hour": 1,
            "decision": "D6",
        },
        "temperature": {
            "id": "ercot_temperature_forecast_by_weather_zone",
            "priority": "required",
            "params": {"publish_time": "latest"},
            "rows_per_hour": 1,
            "decision": "D9",
        },
        "gas": {
            "id": "eia_henry_hub_natural_gas_spot_prices_daily",
            "priority": "required",
            "params": {},
            "rows_per_day": 5 / 7,
            # The full pull starts a week before the study window so the first
            # flow day (Oct 1, 2025) has an earlier observation under the lag rule.
            "full_pull_start": "2025-09-24",
            "decision": "D13",
        },
        # Optional phase (Build Plan 6.7): pulled only by name, `ingest.py ... --only load_fcst`.
        # Day-ahead zone load forecast, the latest vintage at or before 10 a.m. Central
        # the day before each operating hour (D8). Same zone columns as actual load.
        "load_fcst": {
            "id": "ercot_load_forecast_by_forecast_zone",
            "priority": "optional",
            "params": {"publish_time": "latest_before:-1 day@10:00:00"},
            "rows_per_hour": 1,
            "decision": "D8",
        },
    },
}
