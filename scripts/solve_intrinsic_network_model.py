#!/usr/bin/env python3
"""Generate scenario-independent intrinsic network labels for design cases.

This script is for ML training data. It produces one row per design case and
does not apply application scenarios or boundary conditions.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

from solve_network_model import (
    MATERIALS,
    geometry_and_conductance,
    parse_int,
    solve_coating_resistance,
)


OUTPUT_COLUMNS = [
    "case_id",
    "sample_id",
    "material_name",
    "carrier_type",
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
    "a_device_m2",
    "l_device_m",
    "v_domain_m3",
    "h_col_m",
    "l_path_m",
    "column_core_area_m2",
    "coating_shell_area_m2",
    "air_area_m2",
    "g_th_scaffold_segment_w_k",
    "g_th_coating_segment_w_k",
    "g_th_air_segment_w_k",
    "g_th_segment_w_k",
    "k_te_w_k",
    "r_te_k_w",
    "kappa_eff_network_w_mk",
    "r_e_network_ohm",
    "r_coating_network_ohm",
    "r_contact_ohm",
    "alpha_device_v_k",
    "abs_alpha_device_v_k",
    "p_max_coeff_w_k2",
    "p_area_coeff_w_m2_k2",
    "p_volume_coeff_w_m3_k2",
    "baseline_kappa_uc_est_w_mk",
    "baseline_r_uc_est_ohm",
    "network_nodes",
    "network_edges",
    "result_valid",
    "invalid_reason",
]


def carrier_type(material_name: str) -> str:
    if material_name == "Sb2Te3":
        return "p"
    if material_name == "Bi2Te3":
        return "n"
    return ""


def evaluate(row: dict[str, str]) -> dict[str, object]:
    material_name = row["material_name"]
    material = MATERIALS[material_name]
    n_layer = parse_int(row, "n_layer")
    geom = geometry_and_conductance(row, material)
    r_coating, r_e = solve_coating_resistance(
        n_layer,
        geom["g_e_segment_s"],
        material.contact_ohm_nominal,
    )

    alpha = material.seebeck_v_k
    p_max_coeff = (alpha * alpha) / (4.0 * r_e) if r_e > 0.0 and math.isfinite(r_e) else 0.0
    p_area_coeff = p_max_coeff / geom["a_device_m2"]
    p_volume_coeff = p_max_coeff / geom["v_domain_m3"] if geom["v_domain_m3"] > 0.0 else 0.0

    result_valid = True
    invalid_reason = ""
    if geom["kappa_network_w_mk"] <= 0.0:
        result_valid = False
        invalid_reason = "non_positive_kappa"
    if r_e <= 0.0 or not math.isfinite(r_e):
        result_valid = False
        invalid_reason = "invalid_electrical_resistance"

    return {
        "case_id": row["case_id"],
        "sample_id": row.get("sample_id", ""),
        "material_name": material_name,
        "carrier_type": row.get("carrier_type", carrier_type(material_name)),
        "t_ring_m": row["t_ring_m"],
        "ratio_hole": row["ratio_hole"],
        "h_uc_m": row["h_uc_m"],
        "n_layer": row["n_layer"],
        "column_type": row["column_type"],
        "size1_m": row["size1_m"],
        "num_columns": row["num_columns"],
        "path_type": row["path_type"],
        "connection_offset_units": row["connection_offset_units"],
        "t_coating_m": row["t_coating_m"],
        "a_device_m2": geom["a_device_m2"],
        "l_device_m": geom["l_device_m"],
        "v_domain_m3": geom["v_domain_m3"],
        "h_col_m": geom["h_col_m"],
        "l_path_m": geom["l_path_m"],
        "column_core_area_m2": geom["column_core_area_m2"],
        "coating_shell_area_m2": geom["coating_shell_area_m2"],
        "air_area_m2": geom["air_area_m2"],
        "g_th_scaffold_segment_w_k": geom["g_th_scaffold_segment_w_k"],
        "g_th_coating_segment_w_k": geom["g_th_coating_segment_w_k"],
        "g_th_air_segment_w_k": geom["g_th_air_segment_w_k"],
        "g_th_segment_w_k": geom["g_th_segment_w_k"],
        "k_te_w_k": geom["k_te_w_k"],
        "r_te_k_w": geom["r_te_k_w"],
        "kappa_eff_network_w_mk": geom["kappa_network_w_mk"],
        "r_e_network_ohm": r_e,
        "r_coating_network_ohm": r_coating,
        "r_contact_ohm": material.contact_ohm_nominal,
        "alpha_device_v_k": alpha,
        "abs_alpha_device_v_k": abs(alpha),
        "p_max_coeff_w_k2": p_max_coeff,
        "p_area_coeff_w_m2_k2": p_area_coeff,
        "p_volume_coeff_w_m3_k2": p_volume_coeff,
        "baseline_kappa_uc_est_w_mk": row.get("kappa_uc_est_w_mk", ""),
        "baseline_r_uc_est_ohm": row.get("r_uc_est_ohm", ""),
        "network_nodes": n_layer + 1,
        "network_edges": n_layer,
        "result_valid": 1 if result_valid else 0,
        "invalid_reason": invalid_reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate intrinsic network labels for design cases.")
    parser.add_argument("--input", required=True, help="Input design-case CSV.")
    parser.add_argument("--output", required=True, help="Output intrinsic-label CSV.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    input_rows = 0
    output_rows = 0
    with input_path.open("r", newline="", encoding="utf-8") as f_in, output_path.open(
        "w", newline="", encoding="utf-8"
    ) as f_out:
        reader = csv.DictReader(f_in)
        writer = csv.DictWriter(f_out, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in reader:
            input_rows += 1
            writer.writerow(evaluate(row))
            output_rows += 1

    print(f"Read {input_rows} design rows from {input_path}")
    print(f"Wrote {output_rows} intrinsic rows to {output_path}")


if __name__ == "__main__":
    main()
