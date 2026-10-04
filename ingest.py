"""Step 1 — Ingest (Build Plan, Section 6.1).

Pulls the five required Grid Status datasets onto disk once, unchanged, with a
manifest beside every file. Run on its own, never by run_all.py, so a rebuild
never spends API requests or rows.

    python ingest.py access                  # limits + dataset access -> data/raw/access.json
    python ingest.py test                    # one-week trial -> data/raw/test/
    python ingest.py full                    # full year -> data/raw/full/<snapshot>/
    python ingest.py full --only gas --snapshot 2026-10-08

--dry-run lists the planned pulls, expected rows and cache status without
calling the API. Each stage stops on the first failure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import pandas as pd

from config import CONFIG, ROOT

GS = CONFIG["gridstatus"]
PATHS = CONFIG["paths"]
REQUIRED = {k: v for k, v in CONFIG["datasets"].items() if v["priority"] == "required"}


class IngestError(RuntimeError):
    """Stops the run with a clear message."""


# --------------------------------------------------------------------------
# Planning: which files to pull, with the exact query for each
# --------------------------------------------------------------------------

def local_hours(start: str, end: str) -> int:
    """Hours between two local Central dates, counting DST days correctly."""
    return len(pd.date_range(start, end, freq="h", tz=CONFIG["study"]["tz"], inclusive="left"))


def expected_rows(spec: dict, start: str, end: str) -> int:
    hours = local_hours(start, end)
    if "rows_per_hour" in spec:
        return spec["rows_per_hour"] * hours
    return round(spec["rows_per_day"] * hours / 24)


def make_pull(name: str, key: str, start: str, end: str, **overrides) -> dict:
    """One file to pull. `query` is passed to get_dataset as is and is the
    file's identity in its manifest, so any change in it is a different file."""
    spec = CONFIG["datasets"][key]
    query = {
        "dataset": spec["id"],
        "start": start,
        "end": end,
        "timezone": GS["timezone"],
        **spec["params"],
        **overrides,
    }
    query = {k: v for k, v in query.items() if v is not None}
    return {"name": name, "key": key, "query": query, "expected_rows": expected_rows(spec, start, end),
            # Daily gas skips weekends and holidays, so its count is only approximate.
            "expected_is_rough": "rows_per_day" in spec}


def check_chunks() -> None:
    """RT chunks must tile the study window exactly, with no gap or overlap."""
    study = CONFIG["study"]
    for key, spec in REQUIRED.items():
        chunks = spec.get("chunks")
        if not chunks:
            continue
        if chunks[0][1] != study["start"] or chunks[-1][2] != study["end"]:
            raise IngestError(f"{key} chunks do not start and end on the study window")
        for (_, _, end), (_, start, _) in zip(chunks, chunks[1:]):
            if end != start:
                raise IngestError(f"{key} chunks leave a gap or overlap at {end} / {start}")


def plan_pulls(stage: str, only: list[str] | None = None) -> list[dict]:
    if only:
        unknown = set(only) - set(REQUIRED)
        if unknown:
            raise IngestError(f"--only got unknown datasets {sorted(unknown)}; choose from {sorted(REQUIRED)}")
    window = CONFIG["test_week"] if stage == "test" else CONFIG["study"]
    pulls = []
    for key, spec in REQUIRED.items():
        if only and key not in only:
            continue
        if stage == "full" and spec.get("chunks"):
            pulls += [make_pull(f"{key}_{c}", key, s, e) for c, s, e in spec["chunks"]]
        else:
            pulls.append(make_pull(key, key, window["start"], window["end"]))

    if stage == "test" and (not only or "temperature" in only):
        # Every vintage for two days. publish_time=None drops the "latest" rule.
        # Each hour is covered by about 12 daily files, so ~12 rows per hour.
        vc = CONFIG["temperature_vintage_check"]
        p = make_pull("temperature_all_vintages", "temperature", vc["start"], vc["end"], publish_time=None)
        p["expected_rows"] = 12 * local_hours(vc["start"], vc["end"])
        p["expected_is_rough"] = True
        pulls.append(p)
    return pulls


def stage_folder(stage: str, snapshot: str) -> Path:
    return PATHS["raw_test"] if stage == "test" else PATHS["raw_full"] / snapshot


# --------------------------------------------------------------------------
# Manifests and the cache
# --------------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def file_paths(folder: Path, name: str) -> tuple[Path, Path]:
    return folder / f"{name}.parquet", folder / f"{name}.json"


def cache_status(folder: Path, name: str, query: dict) -> str:
    """'missing' or 'reuse'. Anything else stops the run.

    A file is reused only when its manifest is complete, its query matches the
    requested query exactly and its checksum still matches. A refresh never
    overwrites: it goes into a new snapshot.
    """
    pq, mf = file_paths(folder, name)
    if not pq.exists() and not mf.exists():
        return "missing"
    if not mf.exists():
        raise IngestError(f"{pq} has no manifest, so it may be half-written. Delete it and re-run.")
    manifest = json.loads(mf.read_text())
    if manifest.get("status") != "complete":
        raise IngestError(f"{mf} is not marked complete. Delete both files and re-run.")
    if manifest.get("query") != query:
        raise IngestError(
            f"{pq} was pulled with a different query.\n"
            f"  on disk:   {manifest.get('query')}\n  requested: {query}\n"
            "A refresh goes into a new snapshot: pass --snapshot <new name>."
        )
    if not pq.exists():
        raise IngestError(f"{mf} exists but its data file is missing.")
    if sha256(pq) != manifest.get("sha256"):
        raise IngestError(f"{pq} has changed since it was pulled (checksum mismatch).")
    return "reuse"


def write_atomic(df: pd.DataFrame, folder: Path, name: str, query: dict, extra: dict) -> dict:
    """Write to a temporary file, check it, then rename into place.
    The manifest is written last, so a crash never leaves a file that looks complete."""
    import gridstatusio

    folder.mkdir(parents=True, exist_ok=True)
    pq, mf = file_paths(folder, name)
    tmp = pq.with_name(pq.name + ".tmp")
    df.to_parquet(tmp, index=False)
    back = pd.read_parquet(tmp)
    if len(back) != len(df) or list(back.columns) != list(df.columns):
        tmp.unlink()
        raise IngestError(f"{name}: parquet read-back does not match what was downloaded")

    manifest = {
        "name": name,
        "dataset": query["dataset"],
        "query": query,
        "client": f"gridstatusio {gridstatusio.__version__}",
        "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "rows": len(df),
        "columns": list(df.columns),
        "sha256": sha256(tmp),
        **extra,
        "status": "complete",
    }
    os.replace(tmp, pq)
    mtmp = mf.with_name(mf.name + ".tmp")
    mtmp.write_text(json.dumps(manifest, indent=2, default=str))
    os.replace(mtmp, mf)
    return manifest


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Grid Status client
# --------------------------------------------------------------------------

def make_client():
    from dotenv import load_dotenv
    from gridstatusio import GridStatusClient

    load_dotenv(ROOT / ".env")
    key = os.environ.get(GS["api_key_env"])
    if not key:
        raise IngestError(f"{GS['api_key_env']} is not set. Put it in .env.")
    return GridStatusClient(api_key=key, max_retries=GS["max_retries"])


def call(fn, *args, **kwargs):
    """Every API call is followed by a pause (account limit: 1 request/second)."""
    try:
        return fn(*args, **kwargs)
    finally:
        time.sleep(GS["pause_seconds"])


def usage(client) -> dict:
    u = call(client.get_api_usage)
    return {
        "requests": u["current_period_usage"]["total_requests"],
        "rows": u["current_period_usage"]["total_api_rows_returned"],
        "limits": u["limits"],
        "period_end": u["current_usage_period_end"],
        "plan": u.get("plan_name"),
    }


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


# --------------------------------------------------------------------------
# Stages
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
    trial = estimate(plan_pulls("test"), page)
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


def require_gates(stage: str) -> None:
    """Access before trial, trial before full (Build Plan, Section 8)."""
    if not PATHS["access"].exists():
        raise IngestError("Run `python ingest.py access` first.")
    if stage == "full":
        for p in plan_pulls("test"):
            if cache_status(PATHS["raw_test"], p["name"], p["query"]) != "reuse":
                raise IngestError(f"Trial file {p['name']} is missing. Run `python ingest.py test` first.")


def run_pulls(client, stage: str, pulls: list[dict], folder: Path, snapshot: str | None) -> None:
    pending = [p for p in pulls if cache_status(folder, p["name"], p["query"]) == "missing"]
    for p in pulls:
        if p not in pending:
            print(f"  reuse   {p['name']}")
    if not pending:
        print("Nothing to pull.")
        return

    start = usage(client)
    need = estimate(pending, start["limits"]["api_rows_per_response_limit"])
    print(f"Pulling {len(pending)} files, about {need['rows']:,} rows / {need['requests']} requests. "
          f"Used so far: {start['requests']} requests, {start['rows']:,} rows.")
    check_budget(start, need)

    before = start
    for p in pending:
        df = call(client.get_dataset, **p["query"], verbose=False)
        if df is None or len(df) == 0:
            raise IngestError(f"{p['name']}: the query returned no rows")
        after = usage(client)
        m = write_atomic(df, folder, p["name"], p["query"], {
            "stage": stage,
            "snapshot": snapshot,
            "expected_rows": p["expected_rows"],
            "requests_used": after["requests"] - before["requests"],
            "rows_billed": after["rows"] - before["rows"],
        })
        flag = "" if m["rows"] == p["expected_rows"] or p.get("expected_is_rough") else \
            f"  (expected {p['expected_rows']:,})"
        print(f"  pulled  {p['name']:26s} {m['rows']:>8,} rows  {m['requests_used']} req{flag}")
        before = after

    print(f"Stage used {before['requests'] - start['requests']} requests and "
          f"{before['rows'] - start['rows']:,} rows (estimate {need['requests']} / {need['rows']:,}). "
          f"Period total: {before['requests']} requests, {before['rows']:,} rows.")


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


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["access", "test", "full"])
    ap.add_argument("--only", nargs="+", metavar="DATASET", help=f"pull only these: {', '.join(REQUIRED)}")
    ap.add_argument("--snapshot", default=dt.date.today().isoformat(), help="full-pull folder name (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="show planned pulls without calling the API")
    ap.add_argument("--optional", action="store_true", help="optional datasets (not implemented yet)")
    args = ap.parse_args(argv)

    try:
        if args.optional:
            raise IngestError("--optional is not implemented. It waits until the main deliverable is done (6.7).")
        check_chunks()
        if args.stage == "access":
            if args.dry_run:
                print("access: get_api_usage + get_dataset_metadata for", ", ".join(REQUIRED))
                return 0
            run_access(make_client())
            return 0

        pulls = plan_pulls(args.stage, args.only)
        folder = stage_folder(args.stage, args.snapshot)
        if args.dry_run:
            print(f"{args.stage} -> {folder}")
            for p in pulls:
                status = cache_status(folder, p["name"], p["query"])
                print(f"  {status:8s} {p['name']:26s} ~{p['expected_rows']:>8,} rows  {p['query']}")
            return 0

        require_gates(args.stage)
        run_pulls(make_client(), args.stage, pulls, folder, args.snapshot if args.stage == "full" else None)
        run_checks(pulls, folder)
        return 0
    except IngestError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
