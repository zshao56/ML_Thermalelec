#!/usr/bin/env python3
"""Export top recommendation STL files for one or more advisor run folders."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_RUNS = [
    "sb2te3_p_pipe_static_device",
    "sb2te3_p_pipe_active_device",
    "sb2te3_p_industrial_device",
]


def run_command(command: list[str]) -> None:
    print()
    print("[RUN] " + " ".join(command))
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export top STL files for design-advisor scenario runs.")
    parser.add_argument(
        "--runs",
        nargs="*",
        default=DEFAULT_RUNS,
        help="Advisor run folder names under results/design_advisor. Defaults to the three Sb2Te3 p scenarios.",
    )
    parser.add_argument("--advisor-root", default="results/design_advisor")
    parser.add_argument("--db-path", default="data/unit_cell_design_space.sqlite")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--ring-segments", type=int, default=128)
    args = parser.parse_args()

    if args.top_k <= 0:
        raise SystemExit("--top-k must be positive")

    script_dir = Path(__file__).resolve().parent
    advisor_root = Path(args.advisor_root)
    exported_dirs = []

    for run_name in args.runs:
        run_dir = advisor_root / run_name
        recommendations = run_dir / "final_recommendations.csv"
        if not recommendations.exists():
            raise SystemExit(f"Missing final recommendations for run {run_name}: {recommendations}")

        out_dir = run_dir / f"stl_top{args.top_k}"
        design_rows = run_dir / f"final_design_rows_top{args.top_k}.csv"
        run_command(
            [
                sys.executable,
                str(script_dir / "export_recommendation_stls.py"),
                "--recommendations",
                str(recommendations),
                "--db-path",
                args.db_path,
                "--out-dir",
                str(out_dir),
                "--top-k",
                str(args.top_k),
                "--ring-segments",
                str(args.ring_segments),
                "--design-rows-output",
                str(design_rows),
            ]
        )
        exported_dirs.append(out_dir)

    print()
    print("Exported STL directories:")
    for out_dir in exported_dirs:
        print(out_dir)


if __name__ == "__main__":
    main()
