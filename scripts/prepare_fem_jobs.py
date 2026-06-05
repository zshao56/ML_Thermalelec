#!/usr/bin/env python3
"""Prepare generic FEM job folders from a FEM sampling CSV."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from generate_case_stls import make_case_mesh, write_ascii_stl


MATERIAL_LIBRARY = {
    "Bi2Te3": {
        "carrier_type": "n",
        "seebeck_v_k": -155.0e-6,
        "sigma_s_m": 400.0 * 100.0,
        "kappa_w_mk": 1.0,
        "density_kg_m3": 7860.0,
    },
    "Sb2Te3": {
        "carrier_type": "p",
        "seebeck_v_k": 100.0e-6,
        "sigma_s_m": 1000.0 * 100.0,
        "kappa_w_mk": 1.0,
        "density_kg_m3": 6500.0,
    },
    "scaffold": {
        "kappa_w_mk": 0.25,
        "electrically_insulating": True,
    },
    "air": {
        "kappa_w_mk": 0.026,
    },
}


def numeric(value: str) -> float | int | str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return value
    if parsed.is_integer():
        return int(parsed)
    return parsed


def design_payload(row: dict[str, str]) -> dict[str, object]:
    keys = [
        "case_id",
        "r_out_m",
        "ratio_hole",
        "r_in_m",
        "t_ring_m",
        "h_total_m",
        "h_uc_m",
        "n_layer",
        "h_col_m",
        "column_type",
        "size1_m",
        "num_columns",
        "path_type",
        "connection_offset_units",
        "connection_offset_fraction",
        "connection_twist_rad",
        "connection_chord_m",
        "l_path_m",
        "placement_mode",
        "placement_json",
        "material_name",
        "carrier_type",
        "t_coating_m",
        "v_scaffold_m3",
        "v_coating_m3",
        "v_air_m3",
        "f_scaffold",
        "f_coating",
        "f_air",
        "porosity",
        "coverage_ratio",
    ]
    payload = {}
    for key in keys:
        if key in row:
            payload[key] = numeric(row[key])
    if "placement_json" in payload and isinstance(payload["placement_json"], str):
        payload["placement"] = json.loads(payload["placement_json"])
    return payload


def intrinsic_payload(row: dict[str, str]) -> dict[str, object]:
    keys = [
        "kappa_eff_network_w_mk",
        "r_e_network_ohm",
        "r_coating_network_ohm",
        "alpha_device_v_k",
        "p_max_coeff_w_k2",
        "p_area_coeff_w_m2_k2",
        "baseline_kappa_uc_est_w_mk",
        "baseline_r_uc_est_ohm",
    ]
    return {key: numeric(row[key]) for key in keys if key in row}


def job_payload(row: dict[str, str], stl_name: str) -> dict[str, object]:
    return {
        "fem_sample_id": row.get("fem_sample_id", ""),
        "case_id": row["case_id"],
        "selection": {
            "priority": row.get("selection_priority", ""),
            "reason": row.get("selection_reason", ""),
        },
        "geometry_file": stl_name,
        "geometry_units": "mm",
        "design": design_payload(row),
        "materials": {
            "thermoelectric_coating": MATERIAL_LIBRARY[row["material_name"]],
            "scaffold": MATERIAL_LIBRARY["scaffold"],
            "air": MATERIAL_LIBRARY["air"],
        },
        "intrinsic_network_prediction": intrinsic_payload(row),
        "requested_high_fidelity_outputs": [
            "kappa_eff_fem_w_mk",
            "r_e_fem_ohm",
            "alpha_eff_fem_v_k",
            "p_max_coeff_fem_w_k2",
            "p_area_coeff_fem_w_m2_k2",
            "mechanical_valid",
            "fabrication_note",
        ],
        "recommended_solve_order": [
            "thermal_conductivity",
            "electrical_resistance",
            "thermoelectric_open_circuit",
            "mechanical_check_optional",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare generic FEM job folders.")
    parser.add_argument("--input", default="results/fem_sampling/fem_sampling_200.csv")
    parser.add_argument("--out-dir", default="results/fem_sampling/jobs")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ring-segments", type=int, default=64, help="Ring tessellation segments for STL output.")
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if args.limit is not None:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit(f"No rows found in {input_path}")

    manifest = {
        "input": str(input_path),
        "job_count": len(rows),
        "jobs": [],
    }

    for row in rows:
        sample_id = row.get("fem_sample_id") or f"case_{row['case_id']}"
        job_dir = out_dir / f"{sample_id}_case_{row['case_id']}"
        job_dir.mkdir(parents=True, exist_ok=True)

        triangles, metadata = make_case_mesh(row, ring_segments=args.ring_segments)
        stl_name = "geometry.stl"
        triangle_count = write_ascii_stl(job_dir / stl_name, f"case_{row['case_id']}", triangles)

        payload = job_payload(row, stl_name)
        payload["stl_metadata"] = metadata
        payload["stl_metadata"]["triangles"] = triangle_count
        input_json = job_dir / "input.json"
        input_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        manifest["jobs"].append(
            {
                "fem_sample_id": sample_id,
                "case_id": row["case_id"],
                "job_dir": str(job_dir),
                "input_json": str(input_json),
                "geometry_stl": str(job_dir / stl_name),
                "triangles": triangle_count,
            }
        )
        print(f"{job_dir}  triangles={triangle_count}")

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared FEM jobs: {len(rows)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
