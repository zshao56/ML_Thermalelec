#!/usr/bin/env python3
"""Rank first-pass evaluation CSV files by scenario."""

from __future__ import annotations

import argparse
import csv
import glob
from collections import defaultdict
from pathlib import Path


DEFAULT_FIELDS = [
    "case_id",
    "scenario_id",
    "material_name",
    "carrier_type",
    "p_area_w_m2",
    "p_max_w",
    "delta_t_device_k",
    "v_oc_v",
    "kappa_uc_est_w_mk",
    "r_uc_est_ohm",
]


def parse_float(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    return float(value) if value else float("-inf")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find top-ranked evaluation rows per scenario.")
    parser.add_argument(
        "--pattern",
        default="results/batches/*_eval.csv",
        help="Glob pattern for evaluation CSV files.",
    )
    parser.add_argument("--score", default="p_area_w_m2", help="Numeric column to maximize.")
    parser.add_argument("--top-k", type=int, default=10, help="Rows to keep per scenario.")
    parser.add_argument("--output", default="results/top_evaluations.csv", help="Output CSV path.")
    args = parser.parse_args()

    paths = sorted(glob.glob(args.pattern))
    if not paths:
        raise SystemExit(f"No files matched: {args.pattern}")

    best_by_scenario: dict[str, list[tuple[float, dict[str, str]]]] = defaultdict(list)
    rows_read = 0
    rows_valid = 0

    for index, path in enumerate(paths, start=1):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows_read += 1
                if row.get("result_valid") != "1":
                    continue
                rows_valid += 1
                scenario = row["scenario_id"]
                score = parse_float(row, args.score)
                bucket = best_by_scenario[scenario]
                bucket.append((score, row))
                bucket.sort(key=lambda item: item[0], reverse=True)
                del bucket[args.top_k :]
        print(f"Read {index}/{len(paths)}: {path}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["rank", "score_column", "score_value"] + DEFAULT_FIELDS
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for scenario in sorted(best_by_scenario):
            for rank, (score, row) in enumerate(best_by_scenario[scenario], start=1):
                output = {
                    "rank": rank,
                    "score_column": args.score,
                    "score_value": score,
                }
                for field in DEFAULT_FIELDS:
                    output[field] = row.get(field, "")
                writer.writerow(output)

    print(f"Rows read: {rows_read}")
    print(f"Valid rows: {rows_valid}")
    print(f"Wrote: {output_path}")

    for scenario in sorted(best_by_scenario):
        score, row = best_by_scenario[scenario][0]
        print()
        print(f"scenario: {scenario}")
        print(f"case_id: {row['case_id']}")
        print(f"material: {row.get('material_name', '')} {row.get('carrier_type', '')}")
        print(f"{args.score}: {score}")
        print(f"p_max_w: {row.get('p_max_w', '')}")
        print(f"delta_t_device_k: {row.get('delta_t_device_k', '')}")
        print(f"v_oc_v: {row.get('v_oc_v', '')}")
        print(f"kappa: {row.get('kappa_uc_est_w_mk', '')}")
        print(f"r_uc_ohm: {row.get('r_uc_est_ohm', '')}")


if __name__ == "__main__":
    main()
