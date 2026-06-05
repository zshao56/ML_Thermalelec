#!/usr/bin/env python3
"""Solve a first-pass thermal-electrical network model for sampled designs.

The model treats each stacked unit cell as a 1D network:

* ring planes are network nodes;
* coated columns between adjacent planes are parallel thermal/electrical edges;
* air in the open domain contributes a parallel thermal edge;
* heat and electric potentials are solved from assembled linear systems.

This is not a full 3D FEM replacement. It is a reproducible intermediate
validation model between the analytic screening formulas and high-fidelity FEM
or experiments.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path


R_OUT_M = 2.0e-3
KAPPA_SCAFFOLD_W_MK = 0.25
KAPPA_AIR_W_MK = 0.026


@dataclass(frozen=True)
class Material:
    seebeck_v_k: float
    sigma_s_m: float
    kappa_w_mk: float
    contact_ohm_nominal: float


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    boundary_type: str
    t_hot_surface_k: float | None
    t_cold_env_k: float
    q_hot_w_m2: float | None
    h_cold_w_m2k: float
    t_hot_max_k: float | None = None


MATERIALS = {
    "Bi2Te3": Material(
        seebeck_v_k=-155.0e-6,
        sigma_s_m=400.0 * 100.0,
        kappa_w_mk=1.0,
        contact_ohm_nominal=0.5 * (50.0 + 1000.0),
    ),
    "Sb2Te3": Material(
        seebeck_v_k=100.0e-6,
        sigma_s_m=1000.0 * 100.0,
        kappa_w_mk=1.0,
        contact_ohm_nominal=0.5 * (20.0 + 400.0),
    ),
}


SCENARIOS = [
    Scenario("wearable_static", "fixed_hot_surface_cold_convection", 303.15, 293.15, None, 5.0),
    Scenario("wearable_active", "fixed_hot_surface_cold_convection", 308.15, 298.15, None, 15.0),
    Scenario("pipe_static", "fixed_hot_surface_cold_convection", 393.15, 293.15, None, 10.0),
    Scenario("pipe_active", "fixed_hot_surface_cold_convection", 433.15, 313.15, None, 100.0),
    Scenario("industrial", "fixed_hot_surface_cold_convection", 493.15, 323.15, None, 15.0),
    Scenario("laptop_cpu_gpu", "fixed_q_cold_convection", None, 313.15, 10000.0, 100.0, 358.15),
]


PATH_LENGTH_FACTORS = {
    "straight": 1.00,
    "single_kink": 1.15,
    "arc_curve": 1.10,
    "sine_wave": 1.20,
    "helix_winding": 1.35,
    "bezier_curve": 1.20,
}


OUTPUT_COLUMNS = [
    "sample_id",
    "case_id",
    "scenario_id",
    "boundary_type",
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
    "kappa_uc_est_w_mk",
    "r_uc_est_ohm",
    "baseline_kappa_uc_est_w_mk",
    "baseline_r_uc_est_ohm",
    "r_coating_network_ohm",
    "r_contact_ohm",
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
    "network_nodes",
    "network_edges",
    "result_valid",
    "invalid_reason",
]


def parse_float(row: dict[str, str], key: str, default: float | None = None) -> float:
    value = row.get(key, "")
    if value == "":
        if default is None:
            raise ValueError(f"Missing numeric column: {key}")
        return default
    return float(value)


def parse_int(row: dict[str, str], key: str) -> int:
    return int(float(row[key]))


def polygon_area_from_circumradius(n_sides: int, radius_m: float) -> float:
    return 0.5 * n_sides * radius_m * radius_m * math.sin(2.0 * math.pi / n_sides)


def polygon_perimeter_from_circumradius(n_sides: int, radius_m: float) -> float:
    return 2.0 * n_sides * radius_m * math.sin(math.pi / n_sides)


def column_section(column_type: str, size1_m: float) -> tuple[float, float]:
    if column_type == "circular_column":
        radius = 0.5 * size1_m
        return math.pi * radius * radius, 2.0 * math.pi * radius
    if column_type == "square_column":
        return size1_m * size1_m, 4.0 * size1_m
    if column_type == "pentagonal_column":
        radius = 0.5 * size1_m
        return (
            polygon_area_from_circumradius(5, radius),
            polygon_perimeter_from_circumradius(5, radius),
        )
    if column_type == "hexagonal_column":
        radius = 0.5 * size1_m
        return (
            polygon_area_from_circumradius(6, radius),
            polygon_perimeter_from_circumradius(6, radius),
        )
    raise ValueError(f"Unknown column_type: {column_type}")


def connection_radius_eff(n_columns: int, r_in_m: float, r_out_m: float) -> float:
    if n_columns < 15:
        return r_in_m + 0.75 * (r_out_m - r_in_m)
    n_inner = n_columns // 2
    n_outer = n_columns - n_inner
    r1 = r_in_m + 0.35 * (r_out_m - r_in_m)
    r2 = r_in_m + 0.75 * (r_out_m - r_in_m)
    return (n_inner * r1 + n_outer * r2) / n_columns


def add_edge(matrix: list[list[float]], i: int, j: int, conductance: float) -> None:
    matrix[i][i] += conductance
    matrix[j][j] += conductance
    matrix[i][j] -= conductance
    matrix[j][i] -= conductance


def apply_dirichlet(matrix: list[list[float]], rhs: list[float], node: int, value: float) -> None:
    n = len(rhs)
    for i in range(n):
        if i == node:
            continue
        rhs[i] -= matrix[i][node] * value
        matrix[i][node] = 0.0
    for j in range(n):
        matrix[node][j] = 0.0
    matrix[node][node] = 1.0
    rhs[node] = value


def solve_linear_system(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    n = len(rhs)
    a = [row[:] for row in matrix]
    b = rhs[:]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-30:
            raise ValueError("Singular linear system.")
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            b[col], b[pivot] = b[pivot], b[col]
        scale = a[col][col]
        for j in range(col, n):
            a[col][j] /= scale
        b[col] /= scale
        for row in range(n):
            if row == col:
                continue
            factor = a[row][col]
            if factor == 0.0:
                continue
            for j in range(col, n):
                a[row][j] -= factor * a[col][j]
            b[row] -= factor * b[col]
    return b


def assemble_chain(n_layer: int, segment_conductance: float) -> tuple[list[list[float]], list[float]]:
    n_nodes = n_layer + 1
    matrix = [[0.0 for _ in range(n_nodes)] for _ in range(n_nodes)]
    rhs = [0.0 for _ in range(n_nodes)]
    for i in range(n_layer):
        add_edge(matrix, i, i + 1, segment_conductance)
    return matrix, rhs


def solve_thermal_network(
    scenario: Scenario,
    n_layer: int,
    g_segment_w_k: float,
    area_m2: float,
) -> tuple[list[float], float, float, float, float, bool, str]:
    cold_node = n_layer
    h_area = scenario.h_cold_w_m2k * area_m2
    matrix, rhs = assemble_chain(n_layer, g_segment_w_k)

    if scenario.boundary_type == "fixed_hot_surface_cold_convection":
        assert scenario.t_hot_surface_k is not None
        matrix[cold_node][cold_node] += h_area
        rhs[cold_node] += h_area * scenario.t_cold_env_k
        apply_dirichlet(matrix, rhs, 0, scenario.t_hot_surface_k)
        temperatures = solve_linear_system(matrix, rhs)
        q_hot_w = g_segment_w_k * (temperatures[0] - temperatures[1])
        delta_source = scenario.t_hot_surface_k - scenario.t_cold_env_k
        delta_retention = (temperatures[0] - temperatures[cold_node]) / delta_source if delta_source else 0.0
    elif scenario.boundary_type == "fixed_q_cold_convection":
        assert scenario.q_hot_w_m2 is not None
        matrix[cold_node][cold_node] += h_area
        rhs[cold_node] += h_area * scenario.t_cold_env_k
        q_hot_w = scenario.q_hot_w_m2 * area_m2
        rhs[0] += q_hot_w
        temperatures = solve_linear_system(matrix, rhs)
        delta_retention = 1.0
    else:
        raise ValueError(f"Unknown boundary_type: {scenario.boundary_type}")

    result_valid = True
    invalid_reason = ""
    if temperatures[0] < temperatures[cold_node]:
        result_valid = False
        invalid_reason = "negative_delta_t"
    if scenario.t_hot_max_k is not None and temperatures[0] > scenario.t_hot_max_k:
        result_valid = False
        invalid_reason = "hot_side_temperature_exceeds_limit"

    q_hot_w_m2 = q_hot_w / area_m2
    q_leak_open_w_m2 = q_hot_w_m2
    return temperatures, q_hot_w, q_hot_w_m2, q_leak_open_w_m2, delta_retention, result_valid, invalid_reason


def solve_coating_resistance(n_layer: int, g_e_segment_s: float, contact_ohm: float) -> tuple[float, float]:
    if g_e_segment_s <= 0.0:
        return math.inf, math.inf
    matrix, rhs = assemble_chain(n_layer, g_e_segment_s)
    apply_dirichlet(matrix, rhs, 0, 1.0)
    apply_dirichlet(matrix, rhs, n_layer, 0.0)
    voltages = solve_linear_system(matrix, rhs)
    current_a = g_e_segment_s * (voltages[0] - voltages[1])
    r_coating = 1.0 / current_a if current_a > 0.0 else math.inf
    return r_coating, r_coating + contact_ohm


def solve_open_circuit_voltage(
    n_layer: int,
    g_e_segment_s: float,
    seebeck_v_k: float,
    temperatures_k: list[float],
) -> float:
    if g_e_segment_s <= 0.0:
        return 0.0
    matrix, rhs = assemble_chain(n_layer, g_e_segment_s)
    for i in range(n_layer):
        j = i + 1
        rhs[i] += g_e_segment_s * seebeck_v_k * (temperatures_k[i] - temperatures_k[j])
        rhs[j] += g_e_segment_s * seebeck_v_k * (temperatures_k[j] - temperatures_k[i])
    apply_dirichlet(matrix, rhs, n_layer, 0.0)
    voltages = solve_linear_system(matrix, rhs)
    return voltages[0] - voltages[n_layer]


def geometry_and_conductance(row: dict[str, str], material: Material) -> dict[str, float]:
    ratio_hole = parse_float(row, "ratio_hole")
    t_ring_m = parse_float(row, "t_ring_m")
    h_uc_m = parse_float(row, "h_uc_m")
    n_layer = parse_int(row, "n_layer")
    size1_m = parse_float(row, "size1_m")
    n_columns = parse_int(row, "num_columns")
    t_coating_m = parse_float(row, "t_coating_m")
    connection_offset_units = parse_int(row, "connection_offset_units")
    path_type = row["path_type"]

    h_col_m = h_uc_m - t_ring_m
    if h_col_m <= 0.0:
        raise ValueError(f"case_id {row.get('case_id')} has non-positive h_col_m")

    r_in_m = ratio_hole * R_OUT_M
    r_eff_m = connection_radius_eff(n_columns, r_in_m, R_OUT_M)
    twist_rad = 2.0 * math.pi * connection_offset_units / n_columns
    chord_m = 2.0 * r_eff_m * math.sin(0.5 * abs(twist_rad))
    path_factor = PATH_LENGTH_FACTORS[path_type]
    l_path_m = math.sqrt(h_col_m * h_col_m + chord_m * chord_m) * path_factor

    area_device_m2 = math.pi * R_OUT_M * R_OUT_M
    l_device_m = n_layer * h_uc_m
    v_domain_m3 = area_device_m2 * l_device_m
    core_area_m2, perimeter_m = column_section(row["column_type"], size1_m)
    shell_area_m2 = perimeter_m * t_coating_m
    air_area_m2 = max(area_device_m2 - n_columns * core_area_m2, 1e-18)

    g_th_scaffold = n_columns * KAPPA_SCAFFOLD_W_MK * core_area_m2 / l_path_m
    g_th_coating = n_columns * material.kappa_w_mk * shell_area_m2 / l_path_m
    g_th_air = KAPPA_AIR_W_MK * air_area_m2 / h_col_m
    g_th_segment = g_th_scaffold + g_th_coating + g_th_air
    g_e_segment = n_columns * material.sigma_s_m * shell_area_m2 / l_path_m

    k_device_w_k = g_th_segment / n_layer
    kappa_network = k_device_w_k * l_device_m / area_device_m2

    return {
        "a_device_m2": area_device_m2,
        "l_device_m": l_device_m,
        "v_domain_m3": v_domain_m3,
        "h_col_m": h_col_m,
        "l_path_m": l_path_m,
        "column_core_area_m2": core_area_m2,
        "coating_shell_area_m2": shell_area_m2,
        "air_area_m2": air_area_m2,
        "g_th_scaffold_segment_w_k": g_th_scaffold,
        "g_th_coating_segment_w_k": g_th_coating,
        "g_th_air_segment_w_k": g_th_air,
        "g_th_segment_w_k": g_th_segment,
        "g_e_segment_s": g_e_segment,
        "k_te_w_k": k_device_w_k,
        "r_te_k_w": 1.0 / k_device_w_k if k_device_w_k > 0.0 else math.inf,
        "kappa_network_w_mk": kappa_network,
    }


def evaluate(row: dict[str, str], scenario: Scenario) -> dict[str, object]:
    material_name = row["material_name"]
    material = MATERIALS[material_name]
    n_layer = parse_int(row, "n_layer")
    geom = geometry_and_conductance(row, material)
    temperatures, q_hot_w, q_hot_w_m2, q_leak_open_w_m2, retention, valid, invalid_reason = solve_thermal_network(
        scenario,
        n_layer,
        geom["g_th_segment_w_k"],
        geom["a_device_m2"],
    )
    r_coating, r_e = solve_coating_resistance(n_layer, geom["g_e_segment_s"], material.contact_ohm_nominal)
    v_oc = solve_open_circuit_voltage(n_layer, geom["g_e_segment_s"], material.seebeck_v_k, temperatures)
    p_max = (v_oc * v_oc) / (4.0 * r_e) if r_e > 0.0 and math.isfinite(r_e) else 0.0

    baseline_kappa = row.get("kappa_uc_est_w_mk", "")
    baseline_r = row.get("r_uc_est_ohm", "")
    delta_t = temperatures[0] - temperatures[-1]

    return {
        "sample_id": row.get("sample_id", ""),
        "case_id": row["case_id"],
        "scenario_id": scenario.scenario_id,
        "boundary_type": scenario.boundary_type,
        "material_name": material_name,
        "carrier_type": "p" if material_name == "Sb2Te3" else "n",
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
        "kappa_uc_est_w_mk": geom["kappa_network_w_mk"],
        "r_uc_est_ohm": r_e,
        "baseline_kappa_uc_est_w_mk": baseline_kappa,
        "baseline_r_uc_est_ohm": baseline_r,
        "r_coating_network_ohm": r_coating,
        "r_contact_ohm": material.contact_ohm_nominal,
        "t_hot_device_k": temperatures[0],
        "t_cold_device_k": temperatures[-1],
        "t_avg_k": 0.5 * (temperatures[0] + temperatures[-1]),
        "delta_t_device_k": delta_t,
        "delta_t_retention": retention,
        "q_hot_input_w_m2": q_hot_w_m2,
        "q_hot_input_w": q_hot_w,
        "q_leak_open_w_m2": q_leak_open_w_m2,
        "v_oc_v": v_oc,
        "abs_v_oc_v": abs(v_oc),
        "p_max_w": p_max,
        "p_area_w_m2": p_max / geom["a_device_m2"],
        "p_volume_w_m3": p_max / geom["v_domain_m3"] if geom["v_domain_m3"] > 0.0 else 0.0,
        "network_nodes": n_layer + 1,
        "network_edges": n_layer,
        "result_valid": 1 if valid else 0,
        "invalid_reason": invalid_reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Solve the network validation model for sampled cases.")
    parser.add_argument("--input", default="results/sampling_plan_top50.csv", help="Input sampling-plan CSV.")
    parser.add_argument("--output", default="results/network_results_top50.csv", help="Output network result CSV.")
    parser.add_argument(
        "--scenario",
        choices=[scenario.scenario_id for scenario in SCENARIOS] + ["all"],
        default="all",
        help="Scenario to evaluate. Default: all.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")
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

    print(f"Read {input_rows} sampled designs from {input_path}")
    print(f"Wrote {output_rows} network evaluations to {output_path}")


if __name__ == "__main__":
    main()
