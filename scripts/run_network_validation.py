#!/usr/bin/env python3
"""Run the complete first-pass network validation workflow."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> None:
    print()
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run network solve and ranking for a sampling plan.")
    parser.add_argument("--sampling-plan", default="results/sampling_plan_top50.csv")
    parser.add_argument("--out-dir", default="results/network_validation")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--score", default="p_area_w_m2")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    network_results = out_dir / "network_results.csv"
    top_results = out_dir / "network_top_evaluations.csv"

    run(
        [
            sys.executable,
            str(script_dir / "solve_network_model.py"),
            "--input",
            args.sampling_plan,
            "--output",
            str(network_results),
        ]
    )
    run(
        [
            sys.executable,
            str(script_dir / "rank_evaluations.py"),
            "--pattern",
            str(network_results),
            "--score",
            args.score,
            "--top-k",
            str(args.top_k),
            "--output",
            str(top_results),
        ]
    )

    print()
    print(f"Network results: {network_results}")
    print(f"Top ranked results: {top_results}")


if __name__ == "__main__":
    main()
