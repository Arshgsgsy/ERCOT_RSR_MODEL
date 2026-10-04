"""Offline tests for ingest.py: planning, cache identity and atomic writes.

No test calls the Grid Status API. A fake client stands in for it.
"""

import json

import pandas as pd
import pytest

import ingest
from ingest import IngestError, cache_status, file_paths, plan_pulls, write_atomic


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setitem(ingest.GS, "pause_seconds", 0)


def small_df():
    return pd.DataFrame({
        "interval_start_utc": pd.date_range("2025-10-01 05:00", periods=3, freq="h", tz="UTC"),
        "location": ["LZ_NORTH"] * 3,
        "spp": [20.0, 25.5, -3.0],
    })


# --- planning --------------------------------------------------------------

def test_study_window_is_8760_hours():
    s = ingest.CONFIG["study"]
    assert ingest.local_hours(s["start"], s["end"]) == 8760
    assert ingest.local_hours("2025-11-02", "2025-11-03") == 25
    assert ingest.local_hours("2026-03-08", "2026-03-09") == 23


def test_full_plan_expected_rows():
    by_key = {}
    for p in plan_pulls("full"):
        by_key[p["key"]] = by_key.get(p["key"], 0) + p["expected_rows"]
    assert by_key["da"] == 35_040
    assert by_key["rt"] == 140_160
    assert by_key["load"] == 8_760
    assert by_key["temperature"] == 8_760
    assert len([p for p in plan_pulls("full") if p["key"] == "rt"]) == 4


def test_every_query_uses_market_timezone():
    for stage in ("test", "full"):
        assert all(p["query"]["timezone"] == "market" for p in plan_pulls(stage))


def test_trial_includes_all_vintage_temperature_check():
    names = {p["name"]: p for p in plan_pulls("test")}
    assert "publish_time" not in names["temperature_all_vintages"]["query"]
    assert names["temperature"]["query"]["publish_time"] == "latest"


def test_only_filters_and_rejects_unknown():
    assert {p["key"] for p in plan_pulls("full", ["gas"])} == {"gas"}
    with pytest.raises(IngestError):
        plan_pulls("full", ["nope"])


def test_rt_chunks_must_tile_the_window(monkeypatch):
    ingest.check_chunks()
    bad = [("q1", "2025-10-01", "2026-01-01"), ("q2", "2026-01-02", "2026-10-01")]
    monkeypatch.setitem(ingest.REQUIRED["rt"], "chunks", bad)
    with pytest.raises(IngestError):
        ingest.check_chunks()


# --- cache identity --------------------------------------------------------

def test_missing_then_reuse_on_exact_match(tmp_path):
    q = plan_pulls("full", ["da"])[0]["query"]
    assert cache_status(tmp_path, "da", q) == "missing"
    write_atomic(small_df(), tmp_path, "da", q, {})
    assert cache_status(tmp_path, "da", q) == "reuse"


def test_trial_file_cannot_satisfy_full_query(tmp_path):
    trial = plan_pulls("test", ["da"])[0]["query"]
    full = plan_pulls("full", ["da"])[0]["query"]
    write_atomic(small_df(), tmp_path, "da", trial, {})
    with pytest.raises(IngestError, match="different query"):
        cache_status(tmp_path, "da", full)


def test_any_query_change_stops(tmp_path):
    q = plan_pulls("full", ["da"])[0]["query"]
    write_atomic(small_df(), tmp_path, "da", q, {})
    changed = {**q, "filter_value": ["LZ_NORTH"]}
    with pytest.raises(IngestError):
        cache_status(tmp_path, "da", changed)


def test_file_without_manifest_stops(tmp_path):
    q = plan_pulls("full", ["da"])[0]["query"]
    small_df().to_parquet(tmp_path / "da.parquet")
    with pytest.raises(IngestError, match="no manifest"):
        cache_status(tmp_path, "da", q)


def test_incomplete_manifest_stops(tmp_path):
    q = plan_pulls("full", ["da"])[0]["query"]
    write_atomic(small_df(), tmp_path, "da", q, {})
    _, mf = file_paths(tmp_path, "da")
    m = json.loads(mf.read_text())
    m["status"] = "partial"
    mf.write_text(json.dumps(m))
    with pytest.raises(IngestError, match="not marked complete"):
        cache_status(tmp_path, "da", q)


def test_edited_file_stops(tmp_path):
    q = plan_pulls("full", ["da"])[0]["query"]
    write_atomic(small_df(), tmp_path, "da", q, {})
    small_df().head(1).to_parquet(tmp_path / "da.parquet")
    with pytest.raises(IngestError, match="checksum"):
        cache_status(tmp_path, "da", q)


def test_atomic_write_leaves_no_temp_files(tmp_path):
    q = plan_pulls("full", ["da"])[0]["query"]
    m = write_atomic(small_df(), tmp_path, "da", q, {"stage": "full"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["da.json", "da.parquet"]
    assert m["rows"] == 3 and m["status"] == "complete" and m["query"] == q
    back = pd.read_parquet(tmp_path / "da.parquet")
    assert str(back["interval_start_utc"].dt.tz) == "UTC"


# --- pulling with a fake client -------------------------------------------

class FakeClient:
    def __init__(self, rows_used=0):
        self.requests, self.rows, self.calls = 0, rows_used, []

    def get_api_usage(self):
        return {
            "plan_name": "Free",
            "limits": {"api_rows_returned_limit": 500_000, "api_requests_limit": 250,
                       "api_rows_per_response_limit": 50_000},
            "current_usage_period_end": "2026-11-01T00:00:00Z",
            "current_period_usage": {"total_requests": self.requests, "total_api_rows_returned": self.rows},
        }

    def get_dataset(self, **query):
        self.calls.append(query)
        df = small_df()
        self.requests += 1
        self.rows += len(df)
        return df


def test_pull_then_rerun_reuses_without_calls(tmp_path):
    pulls = plan_pulls("full", ["da"])
    client = FakeClient()
    ingest.run_pulls(client, "full", pulls, tmp_path, "snap")
    assert len(client.calls) == 1
    m = json.loads((tmp_path / "da.json").read_text())
    assert m["requests_used"] == 1 and m["rows_billed"] == 3 and m["snapshot"] == "snap"

    ingest.run_pulls(client, "full", pulls, tmp_path, "snap")
    assert len(client.calls) == 1


def test_budget_stops_before_any_pull(tmp_path):
    client = FakeClient(rows_used=490_000)
    with pytest.raises(IngestError, match="rows"):
        ingest.run_pulls(client, "full", plan_pulls("full"), tmp_path, "snap")
    assert client.calls == []


def test_full_requires_trial(tmp_path, monkeypatch):
    monkeypatch.setitem(ingest.PATHS, "access", tmp_path / "access.json")
    monkeypatch.setitem(ingest.PATHS, "raw_test", tmp_path / "test")
    with pytest.raises(IngestError, match="access"):
        ingest.require_gates("full")
    (tmp_path / "access.json").write_text("{}")
    with pytest.raises(IngestError, match="Trial file"):
        ingest.require_gates("full")


# --- checks ---------------------------------------------------------------

def test_vintage_check_detects_change_after_the_hour():
    hour = pd.Timestamp("2025-10-30 10:00", tz="UTC")
    df = pd.DataFrame({
        "interval_start_utc": [hour] * 3,
        "interval_end_utc": [hour + pd.Timedelta("1h")] * 3,
        "publish_time_utc": [hour - pd.Timedelta("1D"), hour + pd.Timedelta("2D"), hour + pd.Timedelta("3D")],
        "coast": [80.0, 81.0, 82.0],
    })
    out = ingest.vintage_check(df)
    assert out["cells_where_value_changes_after_the_hour"] == 1
    assert out["cells_where_value_changes_a_day_or_more_after_the_hour"] == 1
    df.loc[2, "coast"] = 81.0
    assert ingest.vintage_check(df)["cells_where_value_changes_after_the_hour"] == 0


def test_vintage_check_ignores_same_day_revision():
    hour = pd.Timestamp("2025-10-30 09:00", tz="UTC")
    df = pd.DataFrame({
        "interval_start_utc": [hour] * 3,
        "interval_end_utc": [hour + pd.Timedelta("1h")] * 3,
        "publish_time_utc": [hour + pd.Timedelta("1h"), hour + pd.Timedelta("1D 1h"), hour + pd.Timedelta("2D 1h")],
        "far_west": [47.0, 45.5, 45.5],
    })
    out = ingest.vintage_check(df)
    assert out["cells_where_value_changes_after_the_hour"] == 1
    assert out["cells_where_value_changes_a_day_or_more_after_the_hour"] == 0


def test_price_file_with_extra_location_fails_gate():
    p = plan_pulls("test", ["da"])[0]
    df = small_df()
    df.loc[0, "location"] = "HB_NORTH"
    fails, _ = ingest.check_file(p, df)
    assert fails
