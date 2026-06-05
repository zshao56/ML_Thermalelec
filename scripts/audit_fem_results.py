#!/usr/bin/env python3
"""Audit automated FEM result CSV quality before using it for training."""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from pathlib import Path


NUMERIC_FIELDS = [
    "kappa_eff_fem_w_mk",
    "r_e_fem_ohm",
    "alpha_eff_fem_v_k",
    "p_max_coeff_fem_w_k2",
    "p_area_coeff_fem_w_m2_k2",
]


def parse_float(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit automated FEM result quality.")
    parser.add_argument("--input", default="results/fem_sampling/fem_results_200.csv")
    parser.add_argument("--out-dir", default="results/fem_sampling/audit")
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    usable = 0
    status_counts = Counter()
    solver_counts = Counter()
    nonfinite_counts = Counter()
    invalid_rows = []

    with input_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        missing = sorted(set(NUMERIC_FIELDS + ["fem_status", "fem_solver", "fem_sample_id", "case_id"]) - set(fieldnames))
        if missing:
            raise SystemExit(f"Missing expected columns: {missing}")

        for row in reader:
            total += 1
            status = row.get("fem_status", "")
            solver = row.get("fem_solver", "")
            status_counts[status] += 1
            solver_counts[solver] += 1

            row_ok = status == "done"
            for field in NUMERIC_FIELDS:
                if parse_float(row.get(field, "")) is None:
                    nonfinite_counts[field] += 1
                    row_ok = False
            if row_ok:
                usable += 1
            else:
                invalid_rows.append(row)

    summary_path = out_dir / "summary.txt"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write(f"input: {input_path}\n")
        f.write(f"total_rows: {total}\n")
        f.write(f"usable_rows: {usable}\n")
        f.write(f"unusable_rows: {total - usable}\n")
        f.write("\nfem_status:\n")
        for value, count in status_counts.most_common():
            f.write(f"  {value}: {count}\n")
        f.write("\nfem_solver:\n")
        for value, count in solver_counts.most_common():
            f.write(f"  {value}: {count}\n")
        f.write("\nnonfinite_counts:\n")
        for field in NUMERIC_FIELDS:
            f.write(f"  {field}: {nonfinite_counts[field]}\n")

    invalid_path = out_dir / "unusable_rows.csv"
    with invalid_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(invalid_rows)

    print(f"Rows: {total}")
    print(f"Usable rows: {usable}")
    print(f"Unusable rows: {total - usable}")
    print(f"Summary: {summary_path}")
    print(f"Unusable rows: {invalid_path}")


if __name__ == "__main__":
    main()
