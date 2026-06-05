#!/usr/bin/env python3
"""Evaluate a CSV batch with the first-pass thermal-electrical model.

This is a screening/baseline evaluator. It is useful for checking data flow and
ranking designs before FEM or experimental labels are available.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    boundary_type: str
    t_hot_surface_k: float | None
    t_cold_env_k: float
    q_hot_w_m2: float | None
    h_cold_w_m2k: float
    t_hot_max_k: float | None = None


SCENARIOS = [
    Scenario("wearable_static", "fixed_hot_surface_cold_convection", 303.15, 293.15, None, 5.0),
    Scenario("wearable_active", "fixed_hot_surface_cold_convection", 308.15, 298.15, None, 15.0),
    Scenario("pipe_static", "fixed_hot_surface_cold_convection", 393.15, 293.15, None, 10.0),
    Scenario("pipe_active", "fixed_hot_surface_cold_convection", 433.15, 313.15, None, 100.0),
    Scenario("industrial", "fixed_hot_surface_cold_convection", 493.15, 323.15, None, 15.0),
    Scenario("laptop_cpu_gpu", "fixed_q_cold_convection", None, 313.15, 10000.0, 100.0, 358.15),
]

SEEBECK_BY_MATERIAL = {
    "Bi2Te3": -155.0e-6,
    "Sb2Te3": 100.0e-6,
}

OUTPUT_COLUMNS = [
    "case_id",
    "scenario_id",
    "boundary_type",
    "material_name",
    "carrier_type",
    "kappa_uc_est_w_mk",
    "r_uc_est_ohm",
    "a_device_m2",
    "l_device_m",
    "k_te_w_k",
    "r_te_k_w",
    "t_hot_device_k",
    "t_cold_device_k",
    "t_avg_k",
    "delta_t_device_k",
    "delta_t_retention",
    "q_hot_input_w_m2",
    "q_hot_input_w",
    "q_leak_open_w_m2",
    "v_oc_v",
    "abs_v_oc_v",
    "p_max_w",
    "p_area_w_m2",
    "p_volume_w_m3",
    "result_valid",
    "invalid_reason",
]


def parse_float(row: dict[str, str], key: str, default: float | None = None) -> float:
    value = row.get(key, "")
    if value == "":
        if default is None:
            raise ValueError(f"Missing required numeric column: {key}")
        return default
    return float(value)


def evaluate(row: dict[str, str], scenario: Scenario) -> dict[str, object]:
    case_id = row["case_id"]
    material_name = row.get("material_name", "")
    carrier_type = row.get("carrier_type", "")

    r_out_m = parse_float(row, "r_out_m")
    a_device_m2 = parse_float(row, "a_uc_m2", math.pi * r_out_m * r_out_m)
    l_device_m = parse_float(row, "h_total_m")
    v_domain_m3 = parse_float(row, "v_uc_m3", a_device_m2 * parse_float(row, "h_uc_m", l_device_m))
    kappa = parse_float(row, "kappa_uc_est_w_mk")
    r_e = parse_float(row, "r_uc_est_ohm")
    seebeck = parse_float(row, "seebeck_v_k", SEEBECK_BY_MATERIAL.get(material_name, 0.0))

    k_te = kappa * a_device_m2 / l_device_m
    r_te = 1.0 / k_te if k_te > 0.0 else math.inf

    result_valid = True
    invalid_reason = ""

    if scenario.boundary_type == "fixed_hot_surface_cold_convection":
        assert scenario.t_hot_surface_k is not None
        delta_t_source = scenario.t_hot_surface_k - scenario.t_cold_env_k
        delta_t_device = delta_t_source * l_device_m / (
            l_device_m + kappa / scenario.h_cold_w_m2k
        )
        t_hot_device = scenario.t_hot_surface_k
        t_cold_device = t_hot_device - delta_t_device
        q_hot_input_w_m2 = kappa * delta_t_device / l_device_m
        delta_t_retention = delta_t_device / delta_t_source if delta_t_source else 0.0
    elif scenario.boundary_type == "fixed_q_cold_convection":
        assert scenario.q_hot_w_m2 is not None
        q_hot_input_w_m2 = scenario.q_hot_w_m2
        delta_t_device = q_hot_input_w_m2 * l_device_m / kappa
        t_cold_device = scenario.t_cold_env_k + q_hot_input_w_m2 / scenario.h_cold_w_m2k
        t_hot_device = t_cold_device + delta_t_device
        delta_t_source = delta_t_device
        delta_t_retention = 1.0
    else:
        raise ValueError(f"Unknown boundary_type: {scenario.boundary_type}")

    if delta_t_device < 0:
        result_valid = False
        invalid_reason = "negative_delta_t"
    if scenario.t_hot_max_k is not None and t_hot_device > scenario.t_hot_max_k:
        result_valid = False
        invalid_reason = "hot_side_temperature_exceeds_limit"

    q_hot_input_w = q_hot_input_w_m2 * a_device_m2
    q_leak_open_w_m2 = kappa * delta_t_device / l_device_m
    v_oc = seebeck * delta_t_device
    p_max = (v_oc * v_oc) / (4.0 * r_e) if r_e > 0.0 else 0.0

    return {
        "case_id": case_id,
        "scenario_id": scenario.scenario_id,
        "boundary_type": scenario.boundary_type,
        "material_name": material_name,
        "carrier_type": carrier_type,
        "kappa_uc_est_w_mk": kappa,
        "r_uc_est_ohm": r_e,
        "a_device_m2": a_device_m2,
        "l_device_m": l_device_m,
        "k_te_w_k": k_te,
        "r_te_k_w": r_te,
        "t_hot_device_k": t_hot_device,
        "t_cold_device_k": t_cold_device,
        "t_avg_k": 0.5 * (t_hot_device + t_cold_device),
        "delta_t_device_k": delta_t_device,
        "delta_t_retention": delta_t_retention,
        "q_hot_input_w_m2": q_hot_input_w_m2,
        "q_hot_input_w": q_hot_input_w,
        "q_leak_open_w_m2": q_leak_open_w_m2,
        "v_oc_v": v_oc,
        "abs_v_oc_v": abs(v_oc),
        "p_max_w": p_max,
        "p_area_w_m2": p_max / a_device_m2,
        "p_volume_w_m3": p_max / v_domain_m3 if v_domain_m3 > 0.0 else 0.0,
        "result_valid": 1 if result_valid else 0,
        "invalid_reason": invalid_reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run first-pass evaluation for one CSV batch.")
    parser.add_argument("--input", required=True, help="Input batch CSV.")
    parser.add_argument("--output", required=True, help="Output evaluation CSV.")
    parser.add_argument(
        "--scenario",
        choices=[scenario.scenario_id for scenario in SCENARIOS] + ["all"],
        default="all",
        help="Scenario to evaluate. Default: all.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    selected = SCENARIOS if args.scenario == "all" else [
        scenario for scenario in SCENARIOS if scenario.scenario_id == args.scenario
    ]

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
            for scenario in selected:
                writer.writerow(evaluate(row, scenario))
                output_rows += 1

    print(f"Read {input_rows} design rows from {input_path}")
    print(f"Wrote {output_rows} evaluation rows to {output_path}")


if __name__ == "__main__":
    main()
