#!/usr/bin/env python3
"""Run a stricter FEM-data pipeline for production surrogate training.

The default rigorous profile keeps all currently defined path types. Specific
path types can still be excluded explicitly for ablation or debugging.
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


def file_exists(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def csv_has_rows(path: Path, expected_count: int) -> bool:
    if not file_exists(path):
        return False
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return sum(1 for _row in reader) == expected_count


def manifest_has_jobs(path: Path, expected_count: int) -> bool:
    if not file_exists(path):
        return False
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return int(manifest.get("job_count", -1)) == expected_count


def run_command(name: str, command: list[str], dry_run: bool) -> StageResult:
    command_text = " ".join(command)
    print()
    print(f"[RUN] {name}")
    print(f"      {command_text}")
    if dry_run:
        return StageResult(name, "dry_run", command_text)
    subprocess.run(command, cwd=ROOT, check=True)
    return StageResult(name, "completed", command_text)


def skip_stage(name: str, detail: str) -> StageResult:
    print()
    print(f"[SKIP] {name}")
    print(f"       {detail}")
    return StageResult(name, "skipped", detail)


def add_design_filter_args(command: list[str], args: argparse.Namespace) -> None:
    if args.exclude_path_types:
        command.extend(["--exclude-path-types", args.exclude_path_types])
    for item in args.max_columns_for_path:
        command.extend(["--max-columns-for-path", item])


def write_summary(path: Path, args: argparse.Namespace, outputs: dict[str, str], results: Iterable[StageResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile": args.profile_name,
        "fem_count": args.fem_count,
        "voxel_size_m": args.voxel_size_m,
        "workers": args.workers,
        "exclude_path_types": args.exclude_path_types,
        "max_columns_for_path": args.max_columns_for_path,
        "fill_strategy": args.fill_strategy,
        "performance_fill_fraction": args.performance_fill_fraction,
        "stratified_bins": args.stratified_bins,
        "outputs": outputs,
        "stages": [result.__dict__ for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"Pipeline summary: {rel(path)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a rigorous no-geometry-ambiguity FEM training pipeline.")
    parser.add_argument("--intrinsic-dataset", default="results/intrinsic_network_dataset.csv")
    parser.add_argument("--db-path", default="data/unit_cell_design_space.sqlite")
    parser.add_argument("--profile-name", default="rigorous_precise_helix")
    parser.add_argument("--fem-count", type=int, default=80000)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 8)
    parser.add_argument("--voxel-size-m", type=float, default=1.0e-4)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--exclude-path-types", default="")
    parser.add_argument("--fill-strategy", choices=["performance", "stratified", "hybrid"], default="hybrid")
    parser.add_argument("--performance-fill-fraction", type=float, default=0.5)
    parser.add_argument("--stratified-bins", type=int, default=20)
    parser.add_argument(
        "--max-columns-for-path",
        action="append",
        default=[],
        metavar="PATH:MAX_COLUMNS",
        help="Additional path-type column-count limits, e.g. helix_winding:10. Can be repeated.",
    )
    parser.add_argument("--n-estimators", type=int, default=1200)
    parser.add_argument("--min-samples-leaf", type=int, default=2)
    parser.add_argument("--skip-sklearn", action="store_true")
    parser.add_argument("--train-torch", action="store_true")
    parser.add_argument("--torch-device", choices=["auto", "cpu", "cuda"], default="cuda")
    parser.add_argument("--torch-epochs", type=int, default=500)
    parser.add_argument("--torch-batch-size", type=int, default=2048)
    parser.add_argument("--torch-hidden", default="256,256,128")
    parser.add_argument("--torch-dropout", type=float, default=0.05)
    parser.add_argument("--torch-lr", type=float, default=1.0e-3)
    parser.add_argument("--torch-weight-decay", type=float, default=1.0e-5)
    parser.add_argument("--torch-patience", type=int, default=40)
    parser.add_argument("--torch-num-workers", type=int, default=8)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.fem_count < 1:
        raise SystemExit("--fem-count must be >= 1")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.voxel_size_m <= 0.0:
        raise SystemExit("--voxel-size-m must be positive")
    if args.performance_fill_fraction < 0.0 or args.performance_fill_fraction > 1.0:
        raise SystemExit("--performance-fill-fraction must be between 0 and 1")
    if args.stratified_bins < 1:
        raise SystemExit("--stratified-bins must be >= 1")

    intrinsic_dataset = ROOT / args.intrinsic_dataset
    db_path = ROOT / args.db_path
    if not file_exists(intrinsic_dataset):
        raise SystemExit(
            f"Intrinsic dataset does not exist: {rel(intrinsic_dataset)}\n"
            "Run the intrinsic pipeline first, for example:\n"
            "  python3 scripts/run_dataset_pipeline.py --workers 25 --skip-fem-jobs --skip-env-check"
        )
    if not file_exists(db_path):
        raise SystemExit(f"Design database does not exist: {rel(db_path)}")

    voxel_label = f"{args.voxel_size_m * 1e6:.0f}um"
    base_dir = ROOT / "results" / "fem_sampling" / args.profile_name
    sampling_csv = base_dir / f"fem_sampling_{args.fem_count}.csv"
    template_csv = base_dir / f"fem_results_template_{args.fem_count}.csv"
    jobs_dir = base_dir / "jobs"
    fem_results_csv = base_dir / f"fem_results_{args.fem_count}_voxel{voxel_label}.csv"
    audit_dir = base_dir / "audit"
    training_csv = base_dir / f"fem_training_dataset_{args.fem_count}_voxel{voxel_label}.csv"
    unusable_csv = base_dir / f"fem_training_unusable_{args.fem_count}_voxel{voxel_label}.csv"
    training_summary = base_dir / f"fem_training_summary_{args.fem_count}_voxel{voxel_label}.txt"
    sklearn_dir = ROOT / "results" / f"fem_surrogate_{args.profile_name}_{args.fem_count}_voxel{voxel_label}_sklearn"
    torch_dir = ROOT / "results" / f"fem_surrogate_{args.profile_name}_{args.fem_count}_voxel{voxel_label}_torch"

    print("Rigorous FEM-data pipeline")
    print(f"Repo: {ROOT}")
    print(f"Profile: {args.profile_name}")
    print(f"FEM samples: {args.fem_count}")
    print(f"Workers: {args.workers}")
    print(f"Voxel size: {args.voxel_size_m}")
    print(f"Excluded path types: {args.exclude_path_types or '(none)'}")
    print(
        "Sampling fill: "
        f"{args.fill_strategy} "
        f"(performance_fraction={args.performance_fill_fraction}, bins={args.stratified_bins})"
    )
    if args.max_columns_for_path:
        print(f"Path column limits: {', '.join(args.max_columns_for_path)}")
    print()
    print("This pipeline keeps application conditions out of training labels.")
    print("Use design_advisor.py later for condition-specific inverse design.")

    results: list[StageResult] = []

    if args.force or not csv_has_rows(sampling_csv, args.fem_count):
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "make_fem_sampling_set.py"),
            "--intrinsic-dataset",
            rel(intrinsic_dataset),
            "--db-path",
            rel(db_path),
            "--output",
            rel(sampling_csv),
            "--target-count",
            str(args.fem_count),
            "--fill-strategy",
            args.fill_strategy,
            "--performance-fill-fraction",
            str(args.performance_fill_fraction),
            "--stratified-bins",
            str(args.stratified_bins),
        ]
        add_design_filter_args(cmd, args)
        results.append(run_command("make_fem_sampling_set", cmd, args.dry_run))
    else:
        results.append(skip_stage("make_fem_sampling_set", f"exists: {rel(sampling_csv)}"))

    if args.force or not csv_has_rows(template_csv, args.fem_count):
        results.append(
            run_command(
                "make_fem_results_template",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "make_fem_results_template.py"),
                    "--input",
                    rel(sampling_csv),
                    "--output",
                    rel(template_csv),
                ],
                args.dry_run,
            )
        )
    else:
        results.append(skip_stage("make_fem_results_template", f"exists: {rel(template_csv)}"))

    if args.force or not manifest_has_jobs(jobs_dir / "manifest.json", args.fem_count):
        results.append(
            run_command(
                "prepare_fem_jobs",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "prepare_fem_jobs.py"),
                    "--input",
                    rel(sampling_csv),
                    "--out-dir",
                    rel(jobs_dir),
                    "--skip-stl",
                ],
                args.dry_run,
            )
        )
    else:
        results.append(skip_stage("prepare_fem_jobs", f"exists: {rel(jobs_dir / 'manifest.json')}"))

    if args.force or not csv_has_rows(fem_results_csv, args.fem_count):
        results.append(
            run_command(
                "run_auto_fem_validation",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "run_auto_fem_validation.py"),
                    "--jobs-dir",
                    rel(jobs_dir),
                    "--template",
                    rel(template_csv),
                    "--output",
                    rel(fem_results_csv),
                    "--solver",
                    "voxel",
                    "--workers",
                    str(args.workers),
                    "--voxel-size-m",
                    str(args.voxel_size_m),
                    "--progress-every",
                    str(args.progress_every),
                ],
                args.dry_run,
            )
        )
    else:
        results.append(skip_stage("run_auto_fem_validation", f"exists: {rel(fem_results_csv)}"))

    audit_summary = audit_dir / "summary.txt"
    if args.force or not file_exists(audit_summary):
        results.append(
            run_command(
                "audit_fem_results",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "audit_fem_results.py"),
                    "--input",
                    rel(fem_results_csv),
                    "--out-dir",
                    rel(audit_dir),
                ],
                args.dry_run,
            )
        )
    else:
        results.append(skip_stage("audit_fem_results", f"exists: {rel(audit_summary)}"))

    if args.force or not csv_has_rows(training_csv, args.fem_count):
        results.append(
            run_command(
                "make_fem_training_dataset",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "make_fem_training_dataset.py"),
                    "--input",
                    rel(fem_results_csv),
                    "--output",
                    rel(training_csv),
                    "--unusable-output",
                    rel(unusable_csv),
                    "--summary",
                    rel(training_summary),
                ],
                args.dry_run,
            )
        )
    else:
        results.append(skip_stage("make_fem_training_dataset", f"exists: {rel(training_csv)}"))

    if args.skip_sklearn:
        results.append(skip_stage("train_fem_surrogate_sklearn", "--skip-sklearn was set"))
    elif args.force or not file_exists(sklearn_dir / "metrics.csv"):
        results.append(
            run_command(
                "train_fem_surrogate_sklearn",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "train_fem_surrogate_sklearn.py"),
                    "--input",
                    rel(training_csv),
                    "--out-dir",
                    rel(sklearn_dir),
                    "--n-estimators",
                    str(args.n_estimators),
                    "--min-samples-leaf",
                    str(args.min_samples_leaf),
                    "--n-jobs",
                    str(args.workers),
                ],
                args.dry_run,
            )
        )
    else:
        results.append(skip_stage("train_fem_surrogate_sklearn", f"exists: {rel(sklearn_dir / 'metrics.csv')}"))

    if args.train_torch:
        if args.force or not file_exists(torch_dir / "metrics.csv"):
            results.append(
                run_command(
                    "train_fem_surrogate_torch",
                    [
                        sys.executable,
                        str(SCRIPT_DIR / "train_fem_surrogate_torch.py"),
                        "--input",
                        rel(training_csv),
                        "--out-dir",
                        rel(torch_dir),
                        "--device",
                        args.torch_device,
                        "--epochs",
                        str(args.torch_epochs),
                        "--batch-size",
                        str(args.torch_batch_size),
                        "--hidden",
                        args.torch_hidden,
                        "--dropout",
                        str(args.torch_dropout),
                        "--lr",
                        str(args.torch_lr),
                        "--weight-decay",
                        str(args.torch_weight_decay),
                        "--patience",
                        str(args.torch_patience),
                        "--num-workers",
                        str(args.torch_num_workers),
                    ],
                    args.dry_run,
                )
            )
        else:
            results.append(skip_stage("train_fem_surrogate_torch", f"exists: {rel(torch_dir / 'metrics.csv')}"))
    else:
        results.append(skip_stage("train_fem_surrogate_torch", "use --train-torch to train the neural-network surrogate"))

    outputs = {
        "sampling_csv": rel(sampling_csv),
        "template_csv": rel(template_csv),
        "jobs_dir": rel(jobs_dir),
        "fem_results_csv": rel(fem_results_csv),
        "audit_summary": rel(audit_summary),
        "training_csv": rel(training_csv),
        "training_summary": rel(training_summary),
        "sklearn_dir": rel(sklearn_dir),
        "torch_dir": rel(torch_dir),
    }
    summary_path = ROOT / "results" / f"{args.profile_name}_pipeline_summary.json"
    if not args.dry_run:
        write_summary(summary_path, args, outputs, results)

    print()
    print("Rigorous pipeline finished.")
    print(f"FEM results: {rel(fem_results_csv)}")
    print(f"Training CSV: {rel(training_csv)}")
    print(f"Sklearn metrics: {rel(sklearn_dir / 'metrics.csv')}")
    if args.train_torch:
        print(f"Torch metrics: {rel(torch_dir / 'metrics.csv')}")
    print(f"Audit summary: {rel(audit_summary)}")


if __name__ == "__main__":
    main()
