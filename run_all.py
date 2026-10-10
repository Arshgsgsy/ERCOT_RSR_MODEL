"""The runner (Build Plan 3.1, 7.6, D42).

Rebuilds every table and figure from the raw snapshot named in config.py, in
order, and stops at the first failure:

    build (align + features) -> tables -> models -> figures -> facts

Outputs go to outputs/<run_id>/. run.json is written last, only when every step
succeeded, with the resolved settings, decision IDs, raw snapshot and checksums,
processed-input checksums, package versions, code commit and seeds. ingest.py is
never run here, so a rebuild never spends API requests.

    python run_all.py                 # full rebuild
    python run_all.py --skip-build    # reuse data/processed (recorded in run.json)
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata as md
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

from config import CONFIG, ROOT
from ingest import sha256, write_json

PACKAGES = ["gridstatusio", "pandas", "pyarrow", "numpy", "scipy", "statsmodels", "matplotlib", "python-dotenv"]


class RunError(RuntimeError):
    """A pipeline step failed; run.json is not written."""


def git_state() -> dict:
    def git(*args) -> str:
        # rstrip only: porcelain lines start with a space when the index is clean
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.rstrip()
    try:
        return {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
                "dirty_files": [ln[3:] for ln in git("status", "--porcelain").splitlines()]}
    except (OSError, subprocess.CalledProcessError) as e:
        return {"error": str(e)}


def decision_ids(obj) -> list[str]:
    """Every decision ID named in the configuration ("decision" keys)."""
    found = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "decision" and isinstance(v, str):
                    found.update(p.strip().split(" ")[0] for p in v.split(","))
                else:
                    walk(v)
    walk(obj)
    return sorted(found, key=lambda d: (len(d), d))


def step(name: str, fn, timings: dict):
    print(f"\n=== {name} ===", flush=True)
    t = time.time()
    try:
        result = fn()
    except Exception as e:  # stop at the first failure, whatever it is
        raise RunError(f"step '{name}' failed: {e}") from e
    if isinstance(result, int) and result != 0:
        raise RunError(f"step '{name}' returned exit code {result}")
    timings[name] = round(time.time() - t, 1)
    return result


def run(skip_build: bool = False, run_id: str | None = None) -> Path:
    import build
    import facts
    import figures
    import run_models
    import tables

    started = dt.datetime.now(dt.timezone.utc)
    run_id = run_id or started.strftime("%Y-%m-%dT%H%MZ")
    out = CONFIG["paths"]["outputs"] / run_id
    if out.exists():
        raise RunError(f"{out} already exists; outputs are never overwritten")
    timings: dict = {}

    if not skip_build:
        step("build", lambda: build.main([]), timings)
    step("tables", lambda: tables.write_tables(out), timings)
    step("models", lambda: run_models.run(out), timings)
    step("figures", lambda: figures.write_figures(out), timings)
    step("facts", lambda: facts.write_facts(out), timings)

    interim, processed = CONFIG["paths"]["interim"], CONFIG["paths"]["processed"]
    align_report = json.loads((interim / "align_report.json").read_text())
    outputs = sorted(p for p in out.rglob("*") if p.is_file())
    manifest = {
        "run_id": run_id,
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "build_step": "skipped (reused data/processed)" if skip_build else "run",
        "timings_seconds": timings,
        "raw_snapshot": align_report["snapshot"],
        "raw_inputs": align_report["inputs"],
        "processed_inputs": [{"file": str(p.relative_to(ROOT)), "sha256": sha256(p)}
                             for p in (interim / "hourly_panel.parquet", processed / "hourly_features.parquet",
                                       processed / "daily.parquet", processed / "features_report.json")],
        "code": git_state(),
        "python": platform.python_version(),
        "packages": {p: md.version(p) for p in PACKAGES},
        "seeds": {"bootstrap": CONFIG["inference"]["seed"]},
        "decision_ids": decision_ids(CONFIG),
        "settings": CONFIG,
        "outputs": [{"file": str(p.relative_to(out)), "sha256": sha256(p)} for p in outputs],
    }
    write_json(out / "run.json", manifest)
    print(f"\nRun {run_id} complete: {len(outputs)} files in {out}, run.json written. Timings (s): {timings}")
    if manifest["code"].get("dirty_files"):
        print(f"Note: {len(manifest['code']['dirty_files'])} uncommitted files; run.json lists them.")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-build", action="store_true", help="reuse data/processed instead of rebuilding")
    ap.add_argument("--run-id", help="output folder name (default: UTC start time)")
    args = ap.parse_args(argv)
    try:
        run(args.skip_build, args.run_id)
    except RunError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
