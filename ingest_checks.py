"""Ingest safety and one-time checks (Build Plan, Sections 6.1 and 8).

Nothing here changes a value in the raw data. It covers the access check that
wrote access.json, the stage order (access -> test -> full), the row budget,
the trial's all-vintage temperature check and the exit checks that write
_checks.json. ingest.py calls these from its command line.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pandas as pd

from ingest import (
    CONFIG, GS, PATHS, REQUIRED, IngestError,
    cache_status, call, file_paths, local_hours, make_pull, plan_pulls, usage, write_json,
)


# --------------------------------------------------------------------------
# Trial-only pulls
# --------------------------------------------------------------------------

def trial_pulls(only: list[str] | None = None) -> list[dict]:
    """The one-week trial plus every temperature vintage for two days.
    publish_time=None drops the "latest" rule; each hour is covered by about
    12 daily files, so ~12 rows per hour."""
    pulls = plan_pulls("test", only)
    if not only or "temperature" in only:
        vc = CONFIG["temperature_vintage_check"]
        p = make_pull("temperature_all_vintages", "temperature", vc["start"], vc["end"], publish_time=None)
        p["expected_rows"] = 12 * local_hours(vc["start"], vc["end"])
        p["expected_is_rough"] = True
        pulls.append(p)
    return pulls


# --------------------------------------------------------------------------
# Gates and the row budget
# --------------------------------------------------------------------------

def require_gates(stage: str) -> None:
    """Access before trial, trial before full (Build Plan, Section 8)."""
    if not PATHS["access"].exists():
        raise IngestError("Run `python ingest.py access` first.")
    if stage == "full":
        for p in trial_pulls():
            if cache_status(PATHS["raw_test"], p["name"], p["query"]) != "reuse":
                raise IngestError(f"Trial file {p['name']} is missing. Run `python ingest.py test` first.")


def estimate(pulls: list[dict], page_size: int) -> dict:
    rows = sum(p["expected_rows"] for p in pulls)
    requests = sum(max(1, math.ceil(p["expected_rows"] / page_size)) for p in pulls)
    return {"rows": rows, "requests": requests}


def check_budget(u: dict, need: dict) -> None:
    lim = u["limits"]
    rows_left = lim["api_rows_returned_limit"] - u["rows"]
    requests_left = lim["api_requests_limit"] - u["requests"]
    if need["rows"] > GS["max_share_of_remaining_rows"] * rows_left:
        raise IngestError(f"Needs about {need['rows']:,} rows but only {rows_left:,} remain this period.")
    if need["requests"] > requests_left - 5:
        raise IngestError(f"Needs about {need['requests']} requests but only {requests_left} remain.")


def preflight(u: dict, pending: list[dict]) -> None:
    """Passed to ingest.run_pulls: print the estimate and stop if over budget."""
    need = estimate(pending, u["limits"]["api_rows_per_response_limit"])
    print(f"Pulling {len(pending)} files, about {need['rows']:,} rows / {need['requests']} requests. "
          f"Used so far: {u['requests']} requests, {u['rows']:,} rows.")
    check_budget(u, need)


# --------------------------------------------------------------------------
# Access check
# --------------------------------------------------------------------------

def run_access(client) -> dict:
    """Measure what the account can reach before anything is pulled."""
    before = usage(client)
    study_start = pd.Timestamp(CONFIG["study"]["start"], tz=CONFIG["study"]["tz"])
    datasets = {}
    for key, spec in REQUIRED.items():
        try:
            meta = call(client.get_dataset_metadata, spec["id"])
            earliest = next((v for k, v in meta.items() if "earliest" in k and v), None)
            covers = None if earliest is None else pd.Timestamp(earliest) <= study_start
            datasets[key] = {"id": spec["id"], "accessible": True, "earliest": earliest,
                             "covers_study_start": covers, "metadata": meta}
        except Exception as e:  # recorded, not fatal: the report shows every dataset
            datasets[key] = {"id": spec["id"], "accessible": False, "error": repr(e)}
    after = usage(client)

    page = before["limits"]["api_rows_per_response_limit"]
    full = estimate(plan_pulls("full"), page)
    trial = estimate(trial_pulls(), page)
    access = {
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "plan": before["plan"],
        "limits": before["limits"],
        "usage_period_end": before["period_end"],
        "usage_before": {"requests": before["requests"], "rows": before["rows"]},
        "usage_after": {"requests": after["requests"], "rows": after["rows"]},
        "requests_spent_on_metadata": after["requests"] - before["requests"],
        "estimate": {
            "trial": trial,
            "full": full,
            "full_share_of_monthly_rows": round(full["rows"] / before["limits"]["api_rows_returned_limit"], 3),
        },
        "datasets": datasets,
    }
    write_json(PATHS["access"], access)

    print(f"Plan {access['plan']}: {before['limits']['api_requests_limit']} requests, "
          f"{before['limits']['api_rows_returned_limit']:,} rows, {page:,} rows per response")
    print(f"Used so far: {after['requests']} requests, {after['rows']:,} rows (metadata cost "
          f"{access['requests_spent_on_metadata']} requests)")
    for key, d in datasets.items():
        status = "ok" if d["accessible"] else f"NO ACCESS {d['error']}"
        print(f"  {key:12s} {d['id']:48s} {status}  earliest={d.get('earliest')}  covers={d.get('covers_study_start')}")
    print(f"Estimate: trial {trial['rows']:,} rows / {trial['requests']} requests; "
          f"full {full['rows']:,} rows / {full['requests']} requests "
          f"({access['estimate']['full_share_of_monthly_rows']:.0%} of monthly rows)")
    print(f"Wrote {PATHS['access']}")
    if not all(d["accessible"] and d.get("covers_study_start") is not False for d in datasets.values()):
        raise IngestError("Access gate failed: see access.json")
    return access


# --------------------------------------------------------------------------
# Exit checks (Build Plan 6.1): gates fail the stage, findings are reported
# --------------------------------------------------------------------------

def time_column(df: pd.DataFrame, which: str = "start") -> str | None:
    for c in (f"interval_{which}_local", f"interval_{which}_utc"):
        if c in df.columns:
            return c
    return None


def vintage_check(df: pd.DataFrame) -> dict:
    """For hours already past when a file was published, does the value still change?"""
    pub = next((c for c in ("publish_time_utc", "publish_time_local", "publish_time") if c in df.columns), None)
    start, end = time_column(df, "start"), time_column(df, "end")
    if not (pub and start and end):
        return {"result": "manual check needed", "columns": list(df.columns)}
    skip = {pub, start, end, "interval_start_utc", "interval_end_utc", "publish_time_utc"}
    zones = [c for c in df.select_dtypes("number").columns if c not in skip]
    past = df[pd.to_datetime(df[pub], utc=True) >= pd.to_datetime(df[end], utc=True)]
    if past.empty:
        return {"result": "no vintages published after their hour", "zones": zones}
    distinct = past.groupby(start)[zones].nunique()
    changed = int((distinct > 1).sum().sum())
    # Same count, ignoring files published within a day of the hour. The
    # "latest" vintage used in the main pull is 2 to 3 days after the hour.
    late = past[pd.to_datetime(past[pub], utc=True) >= pd.to_datetime(past[end], utc=True) + pd.Timedelta("1D")]
    changed_late = int((late.groupby(start)[zones].nunique() > 1).sum().sum())
    if changed == 0:
        result = "stable after the hour (call it observed)"
    elif changed_late == 0:
        result = "early same-day values revised once, then stable from a day after the hour (call it observed)"
    else:
        result = "changes more than a day after the hour (call it ERCOT's latest after-the-fact estimate)"
    return {
        "zones": zones,
        "hour_zone_cells_with_post_hour_vintages": int(distinct.size),
        "cells_where_value_changes_after_the_hour": changed,
        "cells_where_value_changes_a_day_or_more_after_the_hour": changed_late,
        "result": result,
    }


def check_file(p: dict, df: pd.DataFrame) -> tuple[list[str], dict]:
    """Returns (gate failures, findings) for one file."""
    fails, info = [], {"rows": len(df), "expected_rows": p["expected_rows"]}
    start = time_column(df)
    if start:
        local = pd.to_datetime(df[start], utc=True).dt.tz_convert(CONFIG["study"]["tz"])
        info["first_local"], info["last_local"] = str(local.min()), str(local.max())

    if p["key"] in ("da", "rt"):
        want = set(CONFIG["datasets"][p["key"]]["params"]["filter_value"])
        got = set(df["location"].unique()) if "location" in df.columns else set()
        info["locations"] = sorted(got)
        if got != want:
            fails.append(f"{p['name']}: locations {sorted(got)} != {sorted(want)}")
        if start:
            info["duplicate_keys"] = int(df.duplicated([start, "location"]).sum())
    elif p["key"] == "load":
        info["columns"] = list(df.columns)
        missing = [z["load_column"] for k, z in CONFIG["zones"].items() if k != "decision"
                   and z["load_column"] not in df.columns]
        if missing:
            fails.append(f"{p['name']}: load columns {missing} not found")
    elif p["key"] == "temperature":
        info["columns"] = list(df.columns)
        wanted = {c for k, zs in CONFIG["weather_zone_map"].items() if k != "decision" for c in zs}
        missing = sorted(wanted - set(df.columns))
        if missing:
            fails.append(f"{p['name']}: weather-zone columns {missing} not found")
        if p["name"] == "temperature_all_vintages":
            info["vintage_check"] = vintage_check(df)
    elif p["key"] == "gas":
        info["series"] = sorted(df["series"].unique()) if "series" in df.columns else None
        info["units"] = sorted(df["units"].unique()) if "units" in df.columns else None
        if "period" in df.columns:
            info["last_observation"] = str(df["period"].max())

    if not p.get("expected_is_rough") and len(df) != p["expected_rows"]:
        info["row_count_differs"] = True
    return fails, info


def run_checks(pulls: list[dict], folder: Path) -> None:
    all_fails, findings = [], {}
    for p in pulls:
        pq, _ = file_paths(folder, p["name"])
        if not pq.exists():
            all_fails.append(f"{p['name']}: no file")
            continue
        fails, info = check_file(p, pd.read_parquet(pq))
        all_fails += fails
        findings[p["name"]] = info
    summary = {
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "gate_failures": all_fails,
        "files": findings,
    }
    write_json(folder / "_checks.json", summary)
    print(f"Wrote {folder / '_checks.json'}")
    for f in all_fails:
        print(f"  GATE FAILED: {f}")
    if all_fails:
        raise IngestError("Exit checks failed")
