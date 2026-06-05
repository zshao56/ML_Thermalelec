#!/usr/bin/env python3
"""Run intrinsic network labeling across exported valid-case batches."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def output_path_for(input_path: Path, out_dir: Path) -> Path:
    name = input_path.name
    if name.endswith(".csv"):
        name = name[:-4]
    return out_dir / f"{name}_intrinsic.csv"


def run_one(script: Path, input_path: Path, output_path: Path) -> tuple[Path, Path]:
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        check=True,
    )
    return input_path, output_path


def combine_outputs(paths: list[Path], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    wrote_header = False
    with output_path.open("w", newline="", encoding="utf-8") as f_out:
        writer: csv.DictWriter[str] | None = None
        for path in paths:
            with path.open("r", newline="", encoding="utf-8") as f_in:
                reader = csv.DictReader(f_in)
                if writer is None:
                    writer = csv.DictWriter(f_out, fieldnames=reader.fieldnames)
                if not wrote_header:
                    writer.writeheader()
                    wrote_header = True
                for row in reader:
                    writer.writerow(row)
                    total_rows += 1
    return total_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Run intrinsic network labeling for all batch CSVs.")
    parser.add_argument("--input-dir", default="data/batches", help="Directory containing valid-case batch CSVs.")
    parser.add_argument("--out-dir", default="results/intrinsic_network_batches", help="Output batch directory.")
    parser.add_argument("--pattern", default="valid_cases_worker_*_of_032.csv", help="Input glob pattern.")
    parser.add_argument("--workers", type=int, default=32, help="Parallel worker count.")
    parser.add_argument(
        "--combined-output",
        default="results/intrinsic_network_dataset.csv",
        help="Combined output CSV. Pass an empty string to skip combining.",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    out_dir = Path(args.out_dir)
    if not input_dir.exists():
        raise SystemExit(f"Input directory does not exist: {input_dir}")
    paths = sorted(input_dir.glob(args.pattern))
    if not paths:
        raise SystemExit(f"No input files matched: {input_dir / args.pattern}")
    out_dir.mkdir(parents=True, exist_ok=True)

    script = Path(__file__).resolve().parent / "solve_intrinsic_network_model.py"
    print(f"Input batches: {len(paths)}")
    print(f"Workers: {args.workers}")

    output_paths: list[Path] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = []
        for input_path in paths:
            output_path = output_path_for(input_path, out_dir)
            output_paths.append(output_path)
            futures.append(executor.submit(run_one, script, input_path, output_path))
        for future in as_completed(futures):
            input_path, output_path = future.result()
            print(f"Done: {input_path} -> {output_path}")

    if args.combined_output:
        total_rows = combine_outputs(output_paths, Path(args.combined_output))
        print(f"Combined rows: {total_rows}")
        print(f"Combined output: {args.combined_output}")


if __name__ == "__main__":
    main()
