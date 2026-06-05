#!/usr/bin/env python3
"""Create a validation data-entry template for fabrication-ready cases."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


DESIGN_FIELDS = [
    "case_id",
    "sample_id",
    "t_ring_m",
    "ratio_hole",
    "h_uc_m",
    "n_layer",
    "column_type",
    "size1_m",
    "num_columns",
    "path_type",
    "connection_offset_units",
    "material_name",
    "t_coating_m",
]

NETWORK_FIELDS = [
    "scenario_id",
    "network_result_valid",
    "network_invalid_reason",
    "network_kappa_uc_est_w_mk",
    "network_r_uc_est_ohm",
    "network_delta_t_device_k",
    "network_v_oc_v",
    "network_p_max_w",
    "network_p_area_w_m2",
]

MEASUREMENT_FIELDS = [
    "fabrication_ready",
    "validation_method",
    "validation_status",
    "measured_kappa_eff_w_mk",
    "measured_r_e_ohm",
    "measured_delta_t_device_k",
    "measured_v_oc_v",
    "measured_p_max_w",
    "measured_p_area_w_m2",
    "mechanical_valid",
    "fabrication_note",
    "validation_note",
]

OUTPUT_FIELDS = DESIGN_FIELDS + NETWORK_FIELDS + MEASUREMENT_FIELDS


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def sample_ids(cases: list[dict[str, str]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for index, row in enumerate(cases, start=1):
        case_id = row["case_id"]
        mapping[case_id] = row.get("sample_id") or f"V{index:03d}"
    return mapping


def network_rows_by_case(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(row)
    for case_id in grouped:
        grouped[case_id].sort(key=lambda row: row.get("scenario_id", ""))
    return grouped


def build_output_row(
    case: dict[str, str],
    sample_id: str,
    network: dict[str, str] | None,
) -> dict[str, str]:
    output = {field: "" for field in OUTPUT_FIELDS}
    for field in DESIGN_FIELDS:
        if field == "sample_id":
            output[field] = sample_id
        else:
            output[field] = case.get(field, "")

    if network is not None:
        output["scenario_id"] = network.get("scenario_id", "")
        output["network_result_valid"] = network.get("result_valid", "")
        output["network_invalid_reason"] = network.get("invalid_reason", "")
        output["network_kappa_uc_est_w_mk"] = network.get("kappa_uc_est_w_mk", "")
        output["network_r_uc_est_ohm"] = network.get("r_uc_est_ohm", "")
        output["network_delta_t_device_k"] = network.get("delta_t_device_k", "")
        output["network_v_oc_v"] = network.get("v_oc_v", "")
        output["network_p_max_w"] = network.get("p_max_w", "")
        output["network_p_area_w_m2"] = network.get("p_area_w_m2", "")

    output["fabrication_ready"] = "1"
    output["validation_method"] = "experiment_or_high_fidelity_FEM"
    output["validation_status"] = "pending"
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a validation template for selected cases.")
    parser.add_argument(
        "--cases",
        default="results/network_validation/fabrication_ready_cases.csv",
        help="CSV containing full design rows for fabrication-ready cases.",
    )
    parser.add_argument(
        "--network-results",
        default="results/network_validation/network_results.csv",
        help="Network result CSV used to prefill predictions.",
    )
    parser.add_argument(
        "--output",
        default="results/network_validation/validation_template.csv",
        help="Output validation template CSV.",
    )
    args = parser.parse_args()

    cases_path = Path(args.cases)
    network_path = Path(args.network_results)
    output_path = Path(args.output)
    if not cases_path.exists():
        raise SystemExit(f"Cases CSV does not exist: {cases_path}")
    if not network_path.exists():
        raise SystemExit(f"Network results CSV does not exist: {network_path}")

    cases = read_csv(cases_path)
    network_rows = network_rows_by_case(read_csv(network_path))
    ids = sample_ids(cases)

    rows: list[dict[str, str]] = []
    for case in cases:
        case_id = case["case_id"]
        matched_network_rows = network_rows.get(case_id, [])
        if matched_network_rows:
            for network in matched_network_rows:
                rows.append(build_output_row(case, ids[case_id], network))
        else:
            rows.append(build_output_row(case, ids[case_id], None))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Fabrication-ready cases: {len(cases)}")
    print(f"Validation rows: {len(rows)}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
