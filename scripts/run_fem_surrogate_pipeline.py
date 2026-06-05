#!/usr/bin/env python3
"""One-command FEM label generation and surrogate training pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"


@dataclass
class Stage:
    name: str
    status: str
    detail: str


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def run_stage(name: str, cmd: list[str], dry_run: bool) -> Stage:
    print()
    print(f"[RUN] {name}")
    print("      " + " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, cwd=ROOT, check=True)
    return Stage(name, "dry_run" if dry_run else "completed", " ".join(cmd))


def skip_stage(name: str, detail: str) -> Stage:
    print()
    print(f"[SKIP] {name}")
    print(f"       {detail}")
    return Stage(name, "skipped", detail)


def file_ready(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def manifest_ready(path: Path, expected_count: int) -> bool:
    if not file_ready(path):
        return False
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return int(manifest.get("job_count", -1)) == expected_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FEM sampling, automated FEM labels, filtering, and training.")
    parser.add_argument("--count", type=int, default=1000, help="Number of FEM samples to generate.")
    parser.add_argument("--workers", type=int, default=8, help="Parallel FEM worker processes.")
    parser.add_argument("--voxel-size-m", type=float, default=1.0e-4)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--ring-segments", type=int, default=64)
    parser.add_argument(
        "--keep-stl",
        action="store_true",
        help="Generate geometry.stl files. Default is off because voxel solver does not need STL files.",
    )
    parser.add_argument("--intrinsic-dataset", default="results/intrinsic_network_dataset.csv")
    parser.add_argument("--db-path", default="data/unit_cell_design_space.sqlite")
    parser.add_argument("--out-root", default="results/fem_sampling")
    parser.add_argument("--surrogate-root", default="results")
    parser.add_argument("--force", action="store_true", help="Regenerate outputs even if they already exist.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.count < 1:
        raise SystemExit("--count must be >= 1")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.voxel_size_m <= 0.0:
        raise SystemExit("--voxel-size-m must be > 0")

    voxel_um = round(args.voxel_size_m * 1.0e6)
    out_root = ROOT / args.out_root
    surrogate_root = ROOT / args.surrogate_root
    sampling_csv = out_root / f"fem_sampling_{args.count}.csv"
    jobs_dir = out_root / f"jobs_{args.count}"
    template_csv = out_root / f"fem_results_template_{args.count}.csv"
    results_csv = out_root / f"fem_results_{args.count}_voxel{voxel_um}um.csv"
    audit_dir = out_root / f"audit_{args.count}_voxel{voxel_um}um"
    training_csv = out_root / f"fem_training_dataset_{args.count}_voxel{voxel_um}um.csv"
    unusable_csv = out_root / f"fem_training_unusable_{args.count}_voxel{voxel_um}um.csv"
    training_summary = out_root / f"fem_training_summary_{args.count}_voxel{voxel_um}um.txt"
    surrogate_dir = surrogate_root / f"fem_surrogate_{args.count}_voxel{voxel_um}um"
    pipeline_summary = surrogate_dir / "pipeline_summary.json"

    print("Automatic FEM surrogate pipeline")
    print(f"Repo: {ROOT}")
    print(f"FEM samples: {args.count}")
    print(f"Workers: {args.workers}")
    print(f"Voxel size: {args.voxel_size_m} m")
    print(f"Generate STL files: {bool(args.keep_stl)}")
    print("This generates scenario-independent FEM labels for surrogate training.")

    stages: list[Stage] = []

    if args.force or not file_ready(sampling_csv):
        stages.append(
            run_stage(
                "make_fem_sampling_set",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "make_fem_sampling_set.py"),
                    "--intrinsic-dataset",
                    args.intrinsic_dataset,
                    "--db-path",
                    args.db_path,
                    "--output",
                    rel(sampling_csv),
                    "--target-count",
                    str(args.count),
                ],
                args.dry_run,
            )
        )
    else:
        stages.append(skip_stage("make_fem_sampling_set", f"exists: {rel(sampling_csv)}"))

    if args.force or not file_ready(template_csv):
        stages.append(
            run_stage(
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
        stages.append(skip_stage("make_fem_results_template", f"exists: {rel(template_csv)}"))

    if args.force or not manifest_ready(jobs_dir / "manifest.json", args.count):
        prepare_cmd = [
            sys.executable,
            str(SCRIPT_DIR / "prepare_fem_jobs.py"),
            "--input",
            rel(sampling_csv),
            "--out-dir",
            rel(jobs_dir),
            "--ring-segments",
            str(args.ring_segments),
        ]
        if not args.keep_stl:
            prepare_cmd.append("--skip-stl")
        stages.append(
            run_stage(
                "prepare_fem_jobs",
                prepare_cmd,
                args.dry_run,
            )
        )
    else:
        stages.append(skip_stage("prepare_fem_jobs", f"manifest ready: {rel(jobs_dir / 'manifest.json')}"))

    if args.force or not file_ready(results_csv):
        stages.append(
            run_stage(
                "run_auto_fem_validation",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "run_auto_fem_validation.py"),
                    "--jobs-dir",
                    rel(jobs_dir),
                    "--template",
                    rel(template_csv),
                    "--output",
                    rel(results_csv),
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
        stages.append(skip_stage("run_auto_fem_validation", f"exists: {rel(results_csv)}"))

    stages.append(
        run_stage(
            "audit_fem_results",
            [
                sys.executable,
                str(SCRIPT_DIR / "audit_fem_results.py"),
                "--input",
                rel(results_csv),
                "--out-dir",
                rel(audit_dir),
            ],
            args.dry_run,
        )
    )

    stages.append(
        run_stage(
            "make_fem_training_dataset",
            [
                sys.executable,
                str(SCRIPT_DIR / "make_fem_training_dataset.py"),
                "--input",
                rel(results_csv),
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

    stages.append(
        run_stage(
            "train_fem_surrogate",
            [
                sys.executable,
                str(SCRIPT_DIR / "train_fem_surrogate.py"),
                "--input",
                rel(training_csv),
                "--out-dir",
                rel(surrogate_dir),
            ],
            args.dry_run,
        )
    )

    if not args.dry_run:
        surrogate_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "count": args.count,
            "workers": args.workers,
            "voxel_size_m": args.voxel_size_m,
            "progress_every": args.progress_every,
            "keep_stl": bool(args.keep_stl),
            "sampling_csv": rel(sampling_csv),
            "jobs_dir": rel(jobs_dir),
            "results_csv": rel(results_csv),
            "audit_dir": rel(audit_dir),
            "training_csv": rel(training_csv),
            "surrogate_dir": rel(surrogate_dir),
            "stages": [stage.__dict__ for stage in stages],
        }
        pipeline_summary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("Pipeline finished.")
    print(f"FEM results: {rel(results_csv)}")
    print(f"Training CSV: {rel(training_csv)}")
    print(f"Metrics: {rel(surrogate_dir / 'metrics.csv')}")
    print(f"Audit summary: {rel(audit_dir / 'summary.txt')}")


if __name__ == "__main__":
    main()
