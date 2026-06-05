#!/usr/bin/env python3
"""Audit the intrinsic network dataset before ML training."""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from pathlib import Path


NUMERIC_COLUMNS = [
    "kappa_eff_network_w_mk",
    "r_e_network_ohm",
    "r_coating_network_ohm",
    "alpha_device_v_k",
    "p_max_coeff_w_k2",
    "p_area_coeff_w_m2_k2",
    "baseline_kappa_uc_est_w_mk",
    "baseline_r_uc_est_ohm",
]

CATEGORICAL_COLUMNS = [
    "material_name",
    "t_ring_m",
    "ratio_hole",
    "h_uc_m",
    "column_type",
    "size1_m",
    "num_columns",
    "path_type",
    "connection_offset_units",
    "t_coating_m",
    "result_valid",
    "invalid_reason",
]


def parse_float(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    weight = pos - lo
    return sorted_values[lo] * (1.0 - weight) + sorted_values[hi] * weight


def relative_difference(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    denom = abs(b)
    if denom < 1e-30:
        return 0.0 if abs(a) < 1e-30 else math.inf
    return abs(a - b) / denom


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit intrinsic ML dataset quality.")
    parser.add_argument("--input", default="results/intrinsic_network_dataset.csv")
    parser.add_argument("--out-dir", default="results/intrinsic_audit")
    parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    numeric_values = {column: [] for column in NUMERIC_COLUMNS}
    categorical_counts = {column: Counter() for column in CATEGORICAL_COLUMNS}
    invalid_reasons = Counter()
    top_rows = {
        "kappa_eff_network_w_mk_min": [],
        "kappa_eff_network_w_mk_max": [],
        "r_e_network_ohm_min": [],
        "r_e_network_ohm_max": [],
        "p_area_coeff_w_m2_k2_max": [],
    }
    diff_rows: list[dict[str, str]] = []

    total_rows = 0
    invalid_rows = 0
    nonfinite_counts = Counter()

    with input_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        missing = sorted(set(NUMERIC_COLUMNS + CATEGORICAL_COLUMNS) - set(fieldnames))
        if missing:
            raise SystemExit(f"Missing expected columns: {missing}")

        for row in reader:
            total_rows += 1
            if row.get("result_valid") != "1":
                invalid_rows += 1
                invalid_reasons[row.get("invalid_reason", "")] += 1

            parsed = {}
            for column in NUMERIC_COLUMNS:
                value = parse_float(row.get(column, ""))
                parsed[column] = value
                if value is None:
                    nonfinite_counts[column] += 1
                else:
                    numeric_values[column].append(value)

            for column in CATEGORICAL_COLUMNS:
                categorical_counts[column][row.get(column, "")] += 1

            kappa_diff = relative_difference(
                parsed["kappa_eff_network_w_mk"],
                parsed["baseline_kappa_uc_est_w_mk"],
            )
            r_diff = relative_difference(
                parsed["r_e_network_ohm"],
                parsed["baseline_r_uc_est_ohm"],
            )
            diff_rows.append(
                {
                    "case_id": row["case_id"],
                    "kappa_rel_diff_network_vs_baseline": "" if kappa_diff is None else str(kappa_diff),
                    "r_e_rel_diff_network_vs_baseline": "" if r_diff is None else str(r_diff),
                    "kappa_eff_network_w_mk": row["kappa_eff_network_w_mk"],
                    "baseline_kappa_uc_est_w_mk": row["baseline_kappa_uc_est_w_mk"],
                    "r_e_network_ohm": row["r_e_network_ohm"],
                    "baseline_r_uc_est_ohm": row["baseline_r_uc_est_ohm"],
                }
            )

            candidates = [
                ("kappa_eff_network_w_mk_min", parsed["kappa_eff_network_w_mk"], False),
                ("kappa_eff_network_w_mk_max", parsed["kappa_eff_network_w_mk"], True),
                ("r_e_network_ohm_min", parsed["r_e_network_ohm"], False),
                ("r_e_network_ohm_max", parsed["r_e_network_ohm"], True),
                ("p_area_coeff_w_m2_k2_max", parsed["p_area_coeff_w_m2_k2"], True),
            ]
            for key, value, descending in candidates:
                if value is None:
                    continue
                bucket = top_rows[key]
                keep = {
                    "case_id": row["case_id"],
                    "score": value,
                    "material_name": row["material_name"],
                    "ratio_hole": row["ratio_hole"],
                    "h_uc_m": row["h_uc_m"],
                    "column_type": row["column_type"],
                    "size1_m": row["size1_m"],
                    "num_columns": row["num_columns"],
                    "path_type": row["path_type"],
                    "t_coating_m": row["t_coating_m"],
                }
                bucket.append(keep)
                bucket.sort(key=lambda item: item["score"], reverse=descending)
                del bucket[args.top_k :]

    summary_path = out_dir / "summary.txt"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write(f"input: {input_path}\n")
        f.write(f"total_rows: {total_rows}\n")
        f.write(f"invalid_rows: {invalid_rows}\n")
        f.write(f"valid_rows: {total_rows - invalid_rows}\n")
        f.write("\nnonfinite_counts:\n")
        for column, count in nonfinite_counts.items():
            f.write(f"  {column}: {count}\n")
        f.write("\ninvalid_reasons:\n")
        for reason, count in invalid_reasons.most_common():
            f.write(f"  {reason}: {count}\n")

    stats_path = out_dir / "numeric_stats.csv"
    with stats_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["column", "count", "min", "p01", "p05", "median", "p95", "p99", "max", "mean"],
        )
        writer.writeheader()
        for column, values in numeric_values.items():
            values.sort()
            mean = sum(values) / len(values) if values else math.nan
            writer.writerow(
                {
                    "column": column,
                    "count": len(values),
                    "min": quantile(values, 0.0),
                    "p01": quantile(values, 0.01),
                    "p05": quantile(values, 0.05),
                    "median": quantile(values, 0.5),
                    "p95": quantile(values, 0.95),
                    "p99": quantile(values, 0.99),
                    "max": quantile(values, 1.0),
                    "mean": mean,
                }
            )

    category_path = out_dir / "categorical_counts.csv"
    with category_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["column", "value", "count"])
        writer.writeheader()
        for column, counts in categorical_counts.items():
            for value, count in counts.most_common():
                writer.writerow({"column": column, "value": value, "count": count})

    diff_rows.sort(
        key=lambda row: max(
            float(row["kappa_rel_diff_network_vs_baseline"] or 0.0),
            float(row["r_e_rel_diff_network_vs_baseline"] or 0.0),
        ),
        reverse=True,
    )
    diff_path = out_dir / "largest_baseline_network_differences.csv"
    with diff_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(diff_rows[0].keys()))
        writer.writeheader()
        writer.writerows(diff_rows[: args.top_k])

    for key, rows in top_rows.items():
        path = out_dir / f"{key}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["case_id", "score"])
            writer.writeheader()
            writer.writerows(rows)

    print(f"Rows: {total_rows}")
    print(f"Invalid rows: {invalid_rows}")
    print(f"Summary: {summary_path}")
    print(f"Numeric stats: {stats_path}")
    print(f"Categorical counts: {category_path}")
    print(f"Largest baseline/network differences: {diff_path}")


if __name__ == "__main__":
    main()
