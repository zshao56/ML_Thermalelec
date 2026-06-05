#!/usr/bin/env python3
"""Create a clean ML training CSV from automated FEM results."""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from pathlib import Path


LABEL_FIELDS = [
    "kappa_eff_fem_w_mk",
    "r_e_fem_ohm",
    "alpha_eff_fem_v_k",
    "p_max_coeff_fem_w_k2",
    "p_area_coeff_fem_w_m2_k2",
]

FEATURE_FIELDS = [
    "case_id",
    "material_name",
    "t_ring_m",
    "ratio_hole",
    "h_uc_m",
    "n_layer",
    "column_type",
    "size1_m",
    "num_columns",
    "path_type",
    "connection_offset_units",
    "t_coating_m",
    "network_kappa_eff_w_mk",
    "network_r_e_ohm",
    "network_alpha_device_v_k",
    "network_p_max_coeff_w_k2",
    "network_p_area_coeff_w_m2_k2",
]


def parse_finite_positive(row: dict[str, str], field: str) -> float | None:
    value = row.get(field, "").strip()
    if not value:
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        return None
    return parsed


def is_usable(row: dict[str, str]) -> tuple[bool, str]:
    if row.get("fem_status", "") != "done":
        return False, row.get("fem_invalid_reason", "") or "fem_status_not_done"
    if row.get("fem_valid", "1") in {"0", "false", "False"}:
        return False, row.get("fem_invalid_reason", "") or "fem_valid_is_false"
    for field in LABEL_FIELDS:
        if parse_finite_positive(row, field) is None:
            return False, f"bad_label:{field}"
    return True, ""


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter automated FEM results into a clean training dataset.")
    parser.add_argument("--input", default="results/fem_sampling/fem_results_200.csv")
    parser.add_argument("--output", default="results/fem_sampling/fem_training_dataset.csv")
    parser.add_argument("--unusable-output", default="results/fem_sampling/fem_training_unusable.csv")
    parser.add_argument("--summary", default="results/fem_sampling/fem_training_summary.txt")
    parser.add_argument(
        "--all-columns",
        action="store_true",
        help="Keep all source columns instead of the compact feature+label column set.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")

    with input_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        source_fields = reader.fieldnames or []
        missing = sorted(set(LABEL_FIELDS + ["fem_status", "case_id"]) - set(source_fields))
        if missing:
            raise SystemExit(f"Missing expected columns: {missing}")
        source_rows = list(reader)

    usable_rows: list[dict[str, str]] = []
    unusable_rows: list[dict[str, str]] = []
    reason_counts: Counter[str] = Counter()
    for row in source_rows:
        ok, reason = is_usable(row)
        if ok:
            usable_rows.append(row)
        else:
            row = dict(row)
            row["training_unusable_reason"] = reason
            unusable_rows.append(row)
            reason_counts[reason] += 1

    output_fields = source_fields if args.all_columns else [field for field in FEATURE_FIELDS + LABEL_FIELDS if field in source_fields]
    write_csv(Path(args.output), output_fields, usable_rows)
    write_csv(Path(args.unusable_output), source_fields + ["training_unusable_reason"], unusable_rows)

    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as f:
        f.write(f"input: {input_path}\n")
        f.write(f"total_rows: {len(source_rows)}\n")
        f.write(f"training_rows: {len(usable_rows)}\n")
        f.write(f"unusable_rows: {len(unusable_rows)}\n")
        f.write("\nunusable_reasons:\n")
        for reason, count in reason_counts.most_common():
            f.write(f"  {reason}: {count}\n")

    print(f"Input rows: {len(source_rows)}")
    print(f"Training rows: {len(usable_rows)}")
    print(f"Unusable rows: {len(unusable_rows)}")
    print(f"Training CSV: {args.output}")
    print(f"Unusable CSV: {args.unusable_output}")
    print(f"Summary: {args.summary}")


if __name__ == "__main__":
    main()
