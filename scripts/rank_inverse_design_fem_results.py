#!/usr/bin/env python3
"""Rank inverse-design candidates using confirmed FEM results."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


TARGETS = [
    "kappa_eff_fem_w_mk",
    "r_e_fem_ohm",
    "alpha_eff_fem_v_k",
    "p_max_coeff_fem_w_k2",
    "p_area_coeff_fem_w_m2_k2",
]

DEVICE_TARGETS = [
    "device_delta_t_k",
    "device_v_oc_v",
    "device_abs_v_oc_v",
    "device_p_max_w",
    "device_p_area_w_m2",
    "device_q_hot_w_m2",
    "device_t_hot_k",
    "device_t_cold_k",
]

SUMMARY_FIELDS = [
    "final_rank",
    "fem_sample_id",
    "case_id",
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
    "score_target",
    "score",
]


def finite_float(value: str, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Cannot parse {field}: {value}")
    if not math.isfinite(parsed):
        raise ValueError(f"Non-finite {field}: {value}")
    return parsed


def infer_carrier(row: dict[str, str]) -> str:
    carrier = row.get("carrier_type", "")
    if carrier:
        return carrier
    material = row.get("material_name", "")
    if material == "Sb2Te3":
        return "p"
    if material == "Bi2Te3":
        return "n"
    return ""


def row_float(row: dict[str, str], field: str, default: float) -> float:
    value = row.get(field, "")
    if value == "":
        return default
    return finite_float(value, field)


def device_metrics(row: dict[str, str], args: argparse.Namespace) -> dict[str, float]:
    area_m2 = args.area_m2 if args.area_m2 is not None else row_float(row, "a_device_m2", math.pi * 0.002 * 0.002)
    length_m = args.length_m if args.length_m is not None else row_float(row, "l_device_m", 0.01)
    if area_m2 <= 0.0:
        raise ValueError("--area-m2 / a_device_m2 must be positive")
    if length_m <= 0.0:
        raise ValueError("--length-m / l_device_m must be positive")

    kappa = finite_float(row["kappa_eff_fem_w_mk"], "kappa_eff_fem_w_mk")
    r_e = finite_float(row["r_e_fem_ohm"], "r_e_fem_ohm")
    alpha = finite_float(row["alpha_eff_fem_v_k"], "alpha_eff_fem_v_k")
    if kappa <= 0.0:
        raise ValueError("kappa_eff_fem_w_mk must be positive for device scoring")

    if args.boundary_type == "fixed_hot_surface_cold_convection":
        delta_t_source = args.t_hot_k - args.t_cold_k
        delta_t_device = delta_t_source * length_m / (length_m + kappa / args.h_c_w_m2k)
        t_hot_device = args.t_hot_k
        t_cold_device = t_hot_device - delta_t_device
        q_hot_w_m2 = kappa * delta_t_device / length_m
        delta_t_retention = delta_t_device / delta_t_source if delta_t_source else 0.0
    elif args.boundary_type == "fixed_q_cold_convection":
        if args.q_hot_w_m2 is None:
            raise ValueError("--q-hot-w-m2 is required for fixed_q_cold_convection")
        q_hot_w_m2 = args.q_hot_w_m2
        delta_t_device = q_hot_w_m2 * length_m / kappa
        t_cold_device = args.t_cold_k + q_hot_w_m2 / args.h_c_w_m2k
        t_hot_device = t_cold_device + delta_t_device
        delta_t_retention = 1.0
    else:
        raise ValueError(f"Unknown boundary_type: {args.boundary_type}")

    v_oc = alpha * delta_t_device
    p_max = (v_oc * v_oc) / (4.0 * r_e) if r_e > 0.0 else 0.0
    return {
        "device_delta_t_k": delta_t_device,
        "device_delta_t_retention": delta_t_retention,
        "device_v_oc_v": v_oc,
        "device_abs_v_oc_v": abs(v_oc),
        "device_p_max_w": p_max,
        "device_p_area_w_m2": p_max / area_m2,
        "device_p_volume_w_m3": p_max / (area_m2 * length_m),
        "device_q_hot_w_m2": q_hot_w_m2,
        "device_q_hot_w": q_hot_w_m2 * area_m2,
        "device_t_hot_k": t_hot_device,
        "device_t_cold_k": t_cold_device,
        "device_t_avg_k": 0.5 * (t_hot_device + t_cold_device),
        "device_area_m2": area_m2,
        "device_length_m": length_m,
    }


def is_done(row: dict[str, str]) -> bool:
    if row.get("fem_status", "") != "done":
        return False
    if row.get("fem_valid", "1") in {"0", "false", "False"}:
        return False
    for target in TARGETS:
        try:
            finite_float(row.get(target, ""), target)
        except ValueError:
            return False
    return True


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Input CSV does not exist: {path}")
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No rows found in {path}")
    return rows


def read_error_rows(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    if not path.exists():
        raise SystemExit(f"Error CSV does not exist: {path}")
    with path.open("r", newline="", encoding="utf-8") as f:
        return {row["case_id"]: row for row in csv.DictReader(f)}


def output_row(
    row: dict[str, str],
    error_row: dict[str, str],
    rank: int,
    score_target: str,
    score: float,
    device: dict[str, float],
) -> dict[str, object]:
    output: dict[str, object] = {}
    for field in SUMMARY_FIELDS:
        if field == "final_rank":
            output[field] = rank
        elif field == "score_target":
            output[field] = score_target
        elif field == "score":
            output[field] = score
        elif field == "carrier_type":
            output[field] = infer_carrier(row)
        else:
            output[field] = row.get(field, "")

    for target in TARGETS:
        output[f"fem_{target}"] = finite_float(row[target], target)
        output[f"surrogate_{target}"] = error_row.get(f"surrogate_{target}", "")
        output[f"rel_error_{target}"] = error_row.get(f"rel_error_{target}", "")
    for target, value in device.items():
        output[f"fem_{target}"] = value
    return output


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = SUMMARY_FIELDS
    for target in TARGETS:
        fieldnames.extend([f"fem_{target}", f"surrogate_{target}", f"rel_error_{target}"])
    for target in [
        "device_area_m2",
        "device_length_m",
        "device_t_hot_k",
        "device_t_cold_k",
        "device_t_avg_k",
        "device_delta_t_k",
        "device_delta_t_retention",
        "device_q_hot_w_m2",
        "device_q_hot_w",
        "device_v_oc_v",
        "device_abs_v_oc_v",
        "device_p_max_w",
        "device_p_area_w_m2",
        "device_p_volume_w_m3",
    ]:
        fieldnames.append(f"fem_{target}")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, args: argparse.Namespace, total_rows: int, usable_rows: int, output_rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"fem_results: {args.fem_results}\n")
        f.write(f"score_target: {args.score_target}\n")
        f.write(f"direction: {args.direction}\n")
        f.write(f"top_k: {args.top_k}\n")
        f.write(f"boundary_type: {args.boundary_type}\n")
        f.write(f"t_hot_k: {args.t_hot_k}\n")
        f.write(f"t_cold_k: {args.t_cold_k}\n")
        f.write(f"h_c_w_m2k: {args.h_c_w_m2k}\n")
        f.write(f"q_hot_w_m2: {args.q_hot_w_m2}\n")
        f.write(f"area_m2: {args.area_m2}\n")
        f.write(f"length_m: {args.length_m}\n")
        f.write(f"total_rows: {total_rows}\n")
        f.write(f"usable_rows: {usable_rows}\n")
        f.write(f"output: {args.output}\n")
        if output_rows:
            best = output_rows[0]
            f.write("\nbest_confirmed_candidate:\n")
            for field in [
                "case_id",
                "material_name",
                "carrier_type",
                "column_type",
                "path_type",
                "score",
                "fem_kappa_eff_fem_w_mk",
                "fem_r_e_fem_ohm",
                "fem_p_max_coeff_fem_w_k2",
                "fem_p_area_coeff_fem_w_m2_k2",
                "fem_device_delta_t_k",
                "fem_device_p_max_w",
                "fem_device_p_area_w_m2",
            ]:
                f.write(f"  {field}: {best.get(field, '')}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank inverse-design candidates using confirmed FEM results.")
    parser.add_argument("--fem-results", default="results/inverse_design/fem_check_top200/fem_results.csv")
    parser.add_argument(
        "--error-csv",
        default="results/inverse_design/fem_check_top200/surrogate_vs_fem_errors.csv",
        help="Optional surrogate-vs-FEM error CSV from run_inverse_design_fem_check.py.",
    )
    parser.add_argument("--output", default="results/inverse_design/final_confirmed_candidates.csv")
    parser.add_argument("--summary", default="results/inverse_design/final_confirmed_summary.txt")
    parser.add_argument("--score-target", choices=TARGETS + DEVICE_TARGETS, default="p_area_coeff_fem_w_m2_k2")
    parser.add_argument("--direction", choices=["max", "min"], default="max")
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument(
        "--boundary-type",
        choices=["fixed_hot_surface_cold_convection", "fixed_q_cold_convection"],
        default="fixed_hot_surface_cold_convection",
    )
    parser.add_argument("--t-hot-k", type=float, default=393.15)
    parser.add_argument("--t-cold-k", type=float, default=293.15)
    parser.add_argument("--h-c-w-m2k", type=float, default=10.0)
    parser.add_argument("--q-hot-w-m2", type=float, default=None)
    parser.add_argument("--area-m2", type=float, default=None)
    parser.add_argument("--length-m", type=float, default=None)
    args = parser.parse_args()

    if args.top_k <= 0:
        raise SystemExit("--top-k must be positive")
    if args.h_c_w_m2k <= 0.0:
        raise SystemExit("--h-c-w-m2k must be positive")
    if args.boundary_type == "fixed_q_cold_convection" and args.q_hot_w_m2 is None:
        raise SystemExit("--q-hot-w-m2 is required when --boundary-type fixed_q_cold_convection")

    fem_rows = read_csv(Path(args.fem_results))
    error_rows = read_error_rows(Path(args.error_csv) if args.error_csv else None)
    usable = [row for row in fem_rows if is_done(row)]
    reverse = args.direction == "max"
    device_by_case = {row.get("case_id", ""): device_metrics(row, args) for row in usable}
    ranked = sorted(
        usable,
        key=lambda row: finite_float(row[args.score_target], args.score_target)
        if args.score_target in TARGETS
        else device_by_case[row.get("case_id", "")][args.score_target],
        reverse=reverse,
    )
    output_rows = [
        output_row(
            row,
            error_rows.get(row.get("case_id", ""), {}),
            index,
            args.score_target,
            finite_float(row[args.score_target], args.score_target)
            if args.score_target in TARGETS
            else device_by_case[row.get("case_id", "")][args.score_target],
            device_by_case[row.get("case_id", "")],
        )
        for index, row in enumerate(ranked[: args.top_k], start=1)
    ]

    write_csv(Path(args.output), output_rows)
    write_summary(Path(args.summary), args, len(fem_rows), len(usable), output_rows)

    print(f"Input rows: {len(fem_rows)}")
    print(f"Usable rows: {len(usable)}")
    print(f"Output rows: {len(output_rows)}")
    print(f"Output: {args.output}")
    print(f"Summary: {args.summary}")
    if output_rows:
        best = output_rows[0]
        print()
        print("best_confirmed_candidate")
        print(f"case_id: {best.get('case_id', '')}")
        print(f"material: {best.get('material_name', '')} {best.get('carrier_type', '')}")
        print(f"column_type: {best.get('column_type', '')}")
        print(f"path_type: {best.get('path_type', '')}")
        print(f"score: {best.get('score', '')}")


if __name__ == "__main__":
    main()
