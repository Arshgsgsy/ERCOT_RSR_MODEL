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
            "decision": "D13",
        },
    },
}
