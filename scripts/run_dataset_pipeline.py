#!/usr/bin/env python3
"""Run the full intrinsic-data and FEM-job preparation pipeline.

This is the one-command entry point for the server workflow. It keeps the
training labels scenario-independent, then prepares a smaller high-fidelity
FEM sampling set and job folders for solver-based validation.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"


@dataclass
class StageResult:
    name: str
    status: str
    detail: str


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def run_command(name: str, cmd: list[str], dry_run: bool) -> StageResult:
    command_text = " ".join(cmd)
    print()
    print(f"[RUN] {name}")
    print(f"      {command_text}")
    if dry_run:
        return StageResult(name, "dry_run", command_text)
    subprocess.run(cmd, cwd=ROOT, check=True)
    return StageResult(name, "completed", command_text)


def skip_stage(name: str, detail: str) -> StageResult:
    print()
    print(f"[SKIP] {name}")
    print(f"       {detail}")
    return StageResult(name, "skipped", detail)


def file_exists(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def dir_has_files(path: Path, pattern: str, expected_count: int | None = None) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    matches = list(path.glob(pattern))
    if expected_count is not None:
        return len(matches) == expected_count
    return bool(matches)


def manifest_has_jobs(path: Path, expected_count: int | None) -> bool:
    if not file_exists(path):
        return False
    if expected_count is None:
        return True
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return int(manifest.get("job_count", -1)) == expected_count


def csv_has_rows(path: Path, expected_count: int) -> bool:
    if not file_exists(path):
        return False
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return sum(1 for _row in reader) == expected_count


def write_summary(path: Path, results: Iterable[StageResult], dry_run: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dry_run": dry_run,
        "stages": [result.__dict__ for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"Pipeline summary: {rel(path)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-command pipeline for intrinsic ML data and FEM job preparation."
    )
    parser.add_argument("--db-path", default="data/unit_cell_design_space.sqlite")
    parser.add_argument("--batch-dir", default="data/batches")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 8)
    parser.add_argument("--intrinsic-batch-dir", default="results/intrinsic_network_batches")
    parser.add_argument("--intrinsic-output", default="results/intrinsic_network_dataset.csv")
    parser.add_argument("--audit-dir", default="results/intrinsic_audit")
    parser.add_argument("--fem-count", type=int, default=200)
    parser.add_argument(
        "--fem-sampling-output",
        default=None,
        help="Default: results/fem_sampling/fem_sampling_<fem-count>.csv",
    )
    parser.add_argument("--fem-jobs-dir", default="results/fem_sampling/jobs")
    parser.add_argument(
        "--fem-template-output",
        default=None,
        help="Default: results/fem_sampling/fem_results_template_<fem-count>.csv",
    )
    parser.add_argument("--ring-segments", type=int, default=64)
    parser.add_argument(
        "--limit-fem-jobs",
        type=int,
        default=None,
        help="Prepare only the first N FEM jobs for a small geometry smoke test.",
    )
    parser.add_argument(
        "--skip-fem-jobs",
        action="store_true",
        help="Stop after creating the FEM sampling CSV and template; useful before generating large STL folders.",
    )
    parser.add_argument(
        "--skip-env-check",
        action="store_true",
        help="Do not run the solver environment checker at the end.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate outputs even if matching files already exist.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without running them.")
    args = parser.parse_args()

    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.fem_count < 1:
        raise SystemExit("--fem-count must be >= 1")
    if args.ring_segments < 12:
        raise SystemExit("--ring-segments should be >= 12")
    if args.limit_fem_jobs is not None and args.limit_fem_jobs < 1:
        raise SystemExit("--limit-fem-jobs must be >= 1 when provided")

    db_path = ROOT / args.db_path
    batch_dir = ROOT / args.batch_dir
    intrinsic_output = ROOT / args.intrinsic_output
    audit_dir = ROOT / args.audit_dir
    fem_sampling_output_arg = args.fem_sampling_output or f"results/fem_sampling/fem_sampling_{args.fem_count}.csv"
    fem_template_output_arg = args.fem_template_output or (
        f"results/fem_sampling/fem_results_template_{args.fem_count}.csv"
    )
    fem_sampling_output = ROOT / fem_sampling_output_arg
    fem_jobs_dir = ROOT / args.fem_jobs_dir
    fem_template_output = ROOT / fem_template_output_arg
    batch_pattern = f"valid_cases_worker_*_of_{args.workers:03d}.csv"
    intrinsic_batch_pattern = f"valid_cases_worker_*_of_{args.workers:03d}_intrinsic.csv"
    expected_job_count = args.limit_fem_jobs if args.limit_fem_jobs is not None else args.fem_count

    print("Automatic intrinsic-data pipeline")
    print(f"Repo: {ROOT}")
    print(f"Workers: {args.workers}")
    print(f"FEM sampling target: {args.fem_count}")
    if args.limit_fem_jobs is not None:
        print(f"FEM job limit: {args.limit_fem_jobs}")
    print()
    print("Important: training labels here are intrinsic and scenario-independent.")
    print("Application scenarios belong to later validation/inverse-design checks.")

    results: list[StageResult] = []

    if args.force or not file_exists(db_path):
        cmd = [sys.executable, str(SCRIPT_DIR / "build_unit_cell_database.py"), "--db-path", rel(db_path)]
        if args.force:
            cmd.append("--overwrite")
        results.append(run_command("build_design_database", cmd, args.dry_run))
    else:
        results.append(skip_stage("build_design_database", f"exists: {rel(db_path)}"))

    if args.force or not dir_has_files(batch_dir, batch_pattern, expected_count=args.workers):
        results.append(
            run_command(
                "export_valid_batches",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "export_valid_batches.py"),
                    "--db-path",
                    rel(db_path),
                    "--out-dir",
                    rel(batch_dir),
                    "--workers",
                    str(args.workers),
                ],
                args.dry_run,
            )
        )
    else:
        results.append(
            skip_stage(
                "export_valid_batches",
                f"found {args.workers} batches matching {rel(batch_dir / batch_pattern)}",
            )
        )

    intrinsic_batches_ready = dir_has_files(
        ROOT / args.intrinsic_batch_dir,
        intrinsic_batch_pattern,
        expected_count=args.workers,
    )
    if args.force or not file_exists(intrinsic_output) or not intrinsic_batches_ready:
        results.append(
            run_command(
                "solve_intrinsic_batches",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "run_intrinsic_batches.py"),
                    "--input-dir",
                    rel(batch_dir),
                    "--out-dir",
                    args.intrinsic_batch_dir,
                    "--pattern",
                    batch_pattern,
                    "--workers",
                    str(args.workers),
                    "--combined-output",
                    rel(intrinsic_output),
                ],
                args.dry_run,
            )
        )
    else:
        results.append(skip_stage("solve_intrinsic_batches", f"exists: {rel(intrinsic_output)}"))

    audit_summary = audit_dir / "summary.txt"
    if args.force or not file_exists(audit_summary):
        results.append(
            run_command(
                "audit_intrinsic_dataset",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "audit_intrinsic_dataset.py"),
                    "--input",
                    rel(intrinsic_output),
                    "--out-dir",
                    rel(audit_dir),
                ],
                args.dry_run,
            )
        )
    else:
        results.append(skip_stage("audit_intrinsic_dataset", f"exists: {rel(audit_summary)}"))

    if args.force or not csv_has_rows(fem_sampling_output, args.fem_count):
        results.append(
            run_command(
                "make_fem_sampling_set",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "make_fem_sampling_set.py"),
                    "--intrinsic-dataset",
                    rel(intrinsic_output),
                    "--db-path",
                    rel(db_path),
                    "--output",
                    rel(fem_sampling_output),
                    "--target-count",
                    str(args.fem_count),
                ],
                args.dry_run,
            )
        )
    else:
        results.append(skip_stage("make_fem_sampling_set", f"exists: {rel(fem_sampling_output)}"))

    if args.force or not csv_has_rows(fem_template_output, args.fem_count):
        results.append(
            run_command(
                "make_fem_results_template",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "make_fem_results_template.py"),
                    "--input",
                    rel(fem_sampling_output),
                    "--output",
                    rel(fem_template_output),
                ],
                args.dry_run,
            )
        )
    else:
        results.append(skip_stage("make_fem_results_template", f"exists: {rel(fem_template_output)}"))

    if args.skip_fem_jobs:
        results.append(skip_stage("prepare_fem_jobs", "--skip-fem-jobs was set"))
    elif args.force or not manifest_has_jobs(fem_jobs_dir / "manifest.json", expected_job_count):
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "prepare_fem_jobs.py"),
            "--input",
            rel(fem_sampling_output),
            "--out-dir",
            rel(fem_jobs_dir),
            "--ring-segments",
            str(args.ring_segments),
        ]
        if args.limit_fem_jobs is not None:
            cmd.extend(["--limit", str(args.limit_fem_jobs)])
        results.append(run_command("prepare_fem_jobs", cmd, args.dry_run))
    else:
        results.append(skip_stage("prepare_fem_jobs", f"exists: {rel(fem_jobs_dir / 'manifest.json')}"))

    if args.skip_env_check:
        results.append(skip_stage("check_fem_environment", "--skip-env-check was set"))
    else:
        results.append(
            run_command(
                "check_fem_environment",
                [sys.executable, str(SCRIPT_DIR / "check_fem_environment.py")],
                args.dry_run,
            )
        )

    summary_path = ROOT / "results" / "pipeline_summary.json"
    if not args.dry_run:
        write_summary(summary_path, results, args.dry_run)

    print()
    print("Pipeline finished.")
    print(f"Intrinsic ML dataset: {rel(intrinsic_output)}")
    print(f"Audit directory: {rel(audit_dir)}")
    print(f"FEM sampling CSV: {rel(fem_sampling_output)}")
    print(f"FEM result template: {rel(fem_template_output)}")
    if not args.skip_fem_jobs:
        print(f"FEM jobs directory: {rel(fem_jobs_dir)}")


if __name__ == "__main__":
    main()
