"""Step 1 — Ingest (Build Plan, Section 6.1).

Pulls the five required Grid Status datasets onto disk once, unchanged, with a
manifest beside every file. Optional datasets (Build Plan 6.7) are pulled only
when named with --only. Run on its own, never by run_all.py, so a rebuild
never spends API requests or rows.

    python ingest.py access                  # limits + dataset access -> data/raw/access.json
    python ingest.py test                    # one-week trial -> data/raw/test/
    python ingest.py full                    # full year -> data/raw/full/<snapshot>/
    python ingest.py full --only gas --snapshot 2026-10-08
    python ingest.py test --only load_fcst   # optional dataset: trial week first
    python ingest.py full --only load_fcst --snapshot 2026-10-11

--dry-run lists the planned pulls, expected rows and cache status without
calling the API. Each stage stops on the first failure.

This file holds what the pipeline needs: planning the pulls, the manifest
cache (also used by build.py to verify raw files) and the download loop. The
access check, stage gates, row budget and exit checks are in ingest_checks.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

from config import CONFIG, ROOT

GS = CONFIG["gridstatus"]
PATHS = CONFIG["paths"]
REQUIRED = {k: v for k, v in CONFIG["datasets"].items() if v["priority"] == "required"}
OPTIONAL = {k: v for k, v in CONFIG["datasets"].items() if v["priority"] == "optional"}


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
    """The required files for a stage, or only the named ones. Optional datasets
    are planned only when named. The trial's extra all-vintage temperature pull
    is added by ingest_checks.trial_pulls."""
    known = {**REQUIRED, **OPTIONAL}
    if only:
        unknown = set(only) - set(known)
        if unknown:
            raise IngestError(f"--only got unknown datasets {sorted(unknown)}; choose from {sorted(known)}")
    window = CONFIG["test_week"] if stage == "test" else CONFIG["study"]
    pulls = []
    for key, spec in known.items():
        if (only and key not in only) or (not only and key in OPTIONAL):
            continue
        if stage == "full" and spec.get("chunks"):
            pulls += [make_pull(f"{key}_{c}", key, s, e) for c, s, e in spec["chunks"]]
        elif stage == "full":
            pulls.append(make_pull(key, key, spec.get("full_pull_start", window["start"]), window["end"]))
        else:
            pulls.append(make_pull(key, key, window["start"], window["end"]))
    return pulls


def forecast_deadline(interval_start_utc: pd.Series) -> pd.Series:
    """Latest allowed publish time (UTC) for a forecast of each operating hour:
    `local_time` Central on the day `days_before` the hour's local operating day (D8)."""
    rule, tz = CONFIG["align"]["forecast_deadline"], CONFIG["study"]["tz"]
    day = pd.to_datetime(interval_start_utc, utc=True).dt.tz_convert(tz).dt.tz_localize(None).dt.normalize()
    deadline = day - pd.Timedelta(days=rule["days_before"]) + pd.Timedelta(rule["local_time"] + ":00")
    return deadline.dt.tz_localize(tz).dt.tz_convert("UTC")


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


def run_pulls(client, stage: str, pulls: list[dict], folder: Path, snapshot: str | None,
              preflight=None) -> None:
    """Download every file not already cached. `preflight(usage, pending)` runs
    once before the first download and may stop the run (the row budget check)."""
    pending = [p for p in pulls if cache_status(folder, p["name"], p["query"]) == "missing"]
    for p in pulls:
        if p not in pending:
            print(f"  reuse   {p['name']}")
    if not pending:
        print("Nothing to pull.")
        return

    start = usage(client)
    if preflight:
        preflight(start, pending)

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
          f"{before['rows'] - start['rows']:,} rows. "
          f"Period total: {before['requests']} requests, {before['rows']:,} rows.")


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import ingest_checks as checks

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["access", "test", "full"])
    ap.add_argument("--only", nargs="+", metavar="DATASET",
                    help=f"pull only these: {', '.join(REQUIRED)}; optional: {', '.join(OPTIONAL)}")
    ap.add_argument("--snapshot", default=dt.date.today().isoformat(), help="full-pull folder name (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="show planned pulls without calling the API")
    args = ap.parse_args(argv)

    try:
        check_chunks()
        if args.stage == "access":
            if args.dry_run:
                print("access: get_api_usage + get_dataset_metadata for", ", ".join(REQUIRED))
                return 0
            checks.run_access(make_client())
            return 0

        pulls = checks.trial_pulls(args.only) if args.stage == "test" else plan_pulls("full", args.only)
        folder = stage_folder(args.stage, args.snapshot)
        if args.dry_run:
            print(f"{args.stage} -> {folder}")
            for p in pulls:
                status = cache_status(folder, p["name"], p["query"])
                print(f"  {status:8s} {p['name']:26s} ~{p['expected_rows']:>8,} rows  {p['query']}")
            return 0

        checks.require_gates(args.stage, args.only)
        run_pulls(make_client(), args.stage, pulls, folder, args.snapshot if args.stage == "full" else None,
                  preflight=checks.preflight)
        checks.run_checks(pulls, folder)
        return 0
    except IngestError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Run as the `ingest` module, so ingest_checks shares this one copy of it.
    import ingest

    sys.exit(ingest.main())
