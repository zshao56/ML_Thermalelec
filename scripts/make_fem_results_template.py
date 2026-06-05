#!/usr/bin/env python3
"""Create a data-entry template for high-fidelity FEM results."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


OUTPUT_FIELDS = [
    "fem_sample_id",
    "case_id",
    "selection_priority",
    "selection_reason",
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
    "fem_status",
    "fem_solver",
    "mesh_note",
    "kappa_eff_fem_w_mk",
    "r_e_fem_ohm",
    "alpha_eff_fem_v_k",
    "p_max_coeff_fem_w_k2",
    "p_area_coeff_fem_w_m2_k2",
    "mechanical_valid",
    "fabrication_note",
    "fem_note",
]


SOURCE_TO_OUTPUT = {
    "kappa_eff_network_w_mk": "network_kappa_eff_w_mk",
    "r_e_network_ohm": "network_r_e_ohm",
    "alpha_device_v_k": "network_alpha_device_v_k",
    "p_max_coeff_w_k2": "network_p_max_coeff_w_k2",
    "p_area_coeff_w_m2_k2": "network_p_area_coeff_w_m2_k2",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a high-fidelity FEM result template.")
    parser.add_argument("--input", default="results/fem_sampling/fem_sampling_200.csv")
    parser.add_argument("--output", default="results/fem_sampling/fem_results_template_200.csv")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")

    with input_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No rows found in {input_path}")

    output_rows = []
    for row in rows:
        output = {field: "" for field in OUTPUT_FIELDS}
        for field in OUTPUT_FIELDS:
            if field in row:
                output[field] = row[field]
        for src, dst in SOURCE_TO_OUTPUT.items():
            output[dst] = row.get(src, "")
        output["fem_status"] = "pending"
        output["fem_solver"] = ""
        output["mesh_note"] = ""
        output_rows.append(output)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Input rows: {len(rows)}")
    print(f"Template rows: {len(output_rows)}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
