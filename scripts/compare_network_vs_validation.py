#!/usr/bin/env python3
"""Compare network predictions against measured or high-fidelity validation data."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path


METRIC_PAIRS = [
    ("kappa_eff_w_mk", "network_kappa_uc_est_w_mk", "measured_kappa_eff_w_mk"),
    ("r_e_ohm", "network_r_uc_est_ohm", "measured_r_e_ohm"),
    ("delta_t_device_k", "network_delta_t_device_k", "measured_delta_t_device_k"),
    ("v_oc_v", "network_v_oc_v", "measured_v_oc_v"),
    ("p_max_w", "network_p_max_w", "measured_p_max_w"),
    ("p_area_w_m2", "network_p_area_w_m2", "measured_p_area_w_m2"),
]

OUTPUT_FIELDS = [
    "case_id",
    "sample_id",
    "scenario_id",
    "metric",
    "network_value",
    "measured_value",
    "abs_error",
    "rel_error",
    "validation_status",
]


def parse_optional_float(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    return parsed


def rel_error(predicted: float, measured: float) -> float:
    denom = abs(measured)
    if denom < 1e-30:
        return math.inf if abs(predicted) > 0.0 else 0.0
    return abs(predicted - measured) / denom


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare network predictions with validation measurements.")
    parser.add_argument(
        "--input",
        default="results/network_validation/validation_template.csv",
        help="Validation CSV with measured fields filled in.",
    )
    parser.add_argument(
        "--output",
        default="results/network_validation/network_validation_errors.csv",
        help="Output per-metric error CSV.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")

    comparison_rows: list[dict[str, object]] = []
    summary: dict[str, list[float]] = defaultdict(list)
    input_rows = 0
    rows_with_any_measurement = 0

    with input_path.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            input_rows += 1
            row_has_measurement = False
            for metric, network_col, measured_col in METRIC_PAIRS:
                network_value = parse_optional_float(row.get(network_col, ""))
                measured_value = parse_optional_float(row.get(measured_col, ""))
                if network_value is None or measured_value is None:
                    continue
                row_has_measurement = True
                abs_err = abs(network_value - measured_value)
                rel_err = rel_error(network_value, measured_value)
                summary[metric].append(rel_err)
                comparison_rows.append(
                    {
                        "case_id": row.get("case_id", ""),
                        "sample_id": row.get("sample_id", ""),
                        "scenario_id": row.get("scenario_id", ""),
                        "metric": metric,
                        "network_value": network_value,
                        "measured_value": measured_value,
                        "abs_error": abs_err,
                        "rel_error": rel_err,
                        "validation_status": row.get("validation_status", ""),
                    }
                )
            if row_has_measurement:
                rows_with_any_measurement += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(comparison_rows)

    print(f"Input rows: {input_rows}")
    print(f"Rows with any measured values: {rows_with_any_measurement}")
    print(f"Comparison rows: {len(comparison_rows)}")
    print(f"Output: {output_path}")

    if not comparison_rows:
        print("No measured values found yet. Fill measured_* columns before comparing.")
        return

    print()
    print("Relative error summary:")
    for metric in sorted(summary):
        values = summary[metric]
        mean_rel = sum(values) / len(values)
        max_rel = max(values)
        print(f"{metric}: n={len(values)} mean_rel_error={mean_rel:.6g} max_rel_error={max_rel:.6g}")


if __name__ == "__main__":
    main()
