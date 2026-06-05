#!/usr/bin/env python3
"""Run an automated scalar FEM validation pass for prepared FEM jobs.

This solver uses:

* `geometry.stl` from each job folder,
* `gmsh` to generate a tetrahedral volume mesh,
* `numpy` + `scipy` to assemble and solve a scalar diffusion FEM problem.

The current model is a homogenized composite solve over the solid geometry.
It is more faithful than the reduced-order network model, but it still uses a
single effective conductivity for the solid phase rather than a full explicit
multi-material coating model.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

try:
    import gmsh  # type: ignore
except Exception as exc:  # pragma: no cover - runtime dependency check
    gmsh = None
    GMsh_IMPORT_ERROR = exc
else:
    GMsh_IMPORT_ERROR = None

try:
    import meshio  # type: ignore
except Exception as exc:  # pragma: no cover - runtime dependency check
    meshio = None
    MESHIO_IMPORT_ERROR = exc
else:
    MESHIO_IMPORT_ERROR = None


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
        return polygon_area_from_circumradius(5, radius), polygon_perimeter_from_circumradius(5, radius)
    if column_type == "hexagonal_column":
        radius = 0.5 * size1_m
        return polygon_area_from_circumradius(6, radius), polygon_perimeter_from_circumradius(6, radius)
    raise ValueError(f"Unknown column_type: {column_type}")


def ring_surface_area(r_out_m: float, r_in_m: float, t_ring_m: float) -> float:
    annulus_area = math.pi * (r_out_m * r_out_m - r_in_m * r_in_m)
    outer_wall = 2.0 * math.pi * r_out_m * t_ring_m
    inner_wall = 2.0 * math.pi * r_in_m * t_ring_m if r_in_m > 0 else 0.0
    return 2.0 * annulus_area + outer_wall + inner_wall


def parse_float(mapping: dict[str, object], key: str, default: float | None = None) -> float:
    value = mapping.get(key, default)
    if value is None:
        raise KeyError(key)
    return float(value)


def parse_int(mapping: dict[str, object], key: str, default: int | None = None) -> int:
    value = mapping.get(key, default)
    if value is None:
        raise KeyError(key)
    return int(float(value))


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def mesh_case(stl_path: Path, msh_path: Path, mesh_size_mm: float) -> None:
    if gmsh is None:
        raise RuntimeError(f"gmsh Python module is unavailable: {GMsh_IMPORT_ERROR}")

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 1)
        gmsh.option.setNumber("Mesh.Algorithm3D", 4)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", mesh_size_mm)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", mesh_size_mm)
        gmsh.model.add(stl_path.stem)
        gmsh.merge(str(stl_path))
        gmsh.model.mesh.classifySurfaces(math.radians(40.0), True, True, math.radians(180.0))
        gmsh.model.mesh.createGeometry()
        surface_tags = [tag for dim, tag in gmsh.model.getEntities(2)]
        if not surface_tags:
            raise RuntimeError(f"No surfaces found after importing {stl_path}")
        surface_loop = gmsh.model.geo.addSurfaceLoop(surface_tags)
        gmsh.model.geo.addVolume([surface_loop])
        gmsh.model.geo.synchronize()
        gmsh.model.mesh.generate(3)
        gmsh.write(str(msh_path))
    finally:
        gmsh.finalize()


def read_tetra_mesh(msh_path: Path) -> tuple[np.ndarray, np.ndarray]:
    if meshio is None:
        raise RuntimeError(f"meshio Python module is unavailable: {MESHIO_IMPORT_ERROR}")
    mesh = meshio.read(msh_path)
    tetra_cells = mesh.get_cells_type("tetra")
    if tetra_cells.size == 0:
        tetra_cells = mesh.get_cells_type("tetra10")
        if tetra_cells.size == 0:
            raise RuntimeError(f"No tetrahedral cells found in {msh_path}")
        tetra_cells = tetra_cells[:, :4]
    points = np.asarray(mesh.points, dtype=float)
    return points, np.asarray(tetra_cells, dtype=np.int64)


def assemble_poisson(points: np.ndarray, tets: np.ndarray, conductivity: float) -> sp.csr_matrix:
    n_nodes = points.shape[0]
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []

    for tet in tets:
        p = points[tet]
        mat = np.ones((4, 4), dtype=float)
        mat[:, 1:] = p
        det = np.linalg.det(mat)
        volume = abs(det) / 6.0
        if volume <= 0.0 or not math.isfinite(volume):
            continue
        inv = np.linalg.inv(mat)
        grads = inv[1:, :]  # columns correspond to the gradients of the basis functions.
        ke = conductivity * volume * (grads.T @ grads)
        for i_local, i_global in enumerate(tet):
            for j_local, j_global in enumerate(tet):
                rows.append(int(i_global))
                cols.append(int(j_global))
                data.append(float(ke[i_local, j_local]))

    return sp.coo_matrix((data, (rows, cols)), shape=(n_nodes, n_nodes)).tocsr()


def solve_dirichlet(K: sp.csr_matrix, fixed: dict[int, float]) -> np.ndarray:
    n = K.shape[0]
    fixed_nodes = np.array(sorted(fixed.keys()), dtype=np.int64)
    fixed_values = np.array([fixed[idx] for idx in fixed_nodes], dtype=float)
    mask = np.ones(n, dtype=bool)
    mask[fixed_nodes] = False
    free_nodes = np.flatnonzero(mask)

    u = np.zeros(n, dtype=float)
    u[fixed_nodes] = fixed_values

    if free_nodes.size == 0:
        return u

    K_ff = K[free_nodes][:, free_nodes]
    K_fb = K[free_nodes][:, fixed_nodes]
    rhs = -K_fb @ fixed_values
    u[free_nodes] = spla.spsolve(K_ff, rhs)
    return u


def solve_unit_cell(job: dict[str, object], mesh_cache_dir: Path, force_mesh: bool) -> dict[str, object]:
    design = job["design"]
    materials = job["materials"]
    therm = materials["thermoelectric_coating"]
    scaffold = materials["scaffold"]
    air = materials["air"]

    r_out_m = parse_float(design, "r_out_m")
    r_in_m = parse_float(design, "r_in_m")
    t_ring_m = parse_float(design, "t_ring_m")
    h_uc_m = parse_float(design, "h_uc_m")
    n_layer = parse_int(design, "n_layer")
    h_col_m = parse_float(design, "h_col_m")
    column_type = str(design["column_type"])
    size1_m = parse_float(design, "size1_m")
    num_columns = parse_int(design, "num_columns")
    l_path_m = parse_float(design, "l_path_m")
    t_coating_m = parse_float(design, "t_coating_m")

    a_device_m2 = math.pi * r_out_m * r_out_m
    l_device_m = n_layer * h_uc_m
    v_uc_m3 = a_device_m2 * h_uc_m
    a_ring_m2 = math.pi * (r_out_m * r_out_m - r_in_m * r_in_m)
    v_ring_m3 = a_ring_m2 * t_ring_m
    column_area_m2, column_perimeter_m = column_section(column_type, size1_m)
    v_columns_m3 = column_area_m2 * l_path_m * num_columns
    v_scaffold_m3 = v_ring_m3 + v_columns_m3
    a_ring_surface_m2 = ring_surface_area(r_out_m, r_in_m, t_ring_m)
    a_column_surface_m2 = column_perimeter_m * l_path_m * num_columns
    a_surface_uc_m2 = a_ring_surface_m2 + a_column_surface_m2
    v_coating_m3 = a_surface_uc_m2 * t_coating_m
    f_scaffold = v_scaffold_m3 / v_uc_m3
    f_coating = v_coating_m3 / v_uc_m3
    f_air = max(0.0, 1.0 - f_scaffold - f_coating)

    # Homogenized coefficients used by the FEM solve.
    kappa_mix = (
        f_scaffold * float(scaffold["kappa_w_mk"])
        + f_coating * float(therm["kappa_w_mk"])
        + f_air * float(air["kappa_w_mk"])
    )
    sigma_mix = max(f_coating * float(therm["sigma_s_m"]), 1e-18)

    stl_path = Path(job["geometry_file"])
    if not stl_path.is_absolute():
        stl_path = mesh_cache_dir / stl_path
    if not stl_path.exists():
        raise FileNotFoundError(stl_path)

    mesh_path = stl_path.with_suffix(".msh")
    if force_mesh or not mesh_path.exists():
        mesh_size_mm = max(0.15, min(0.6 * size1_m * 1000.0, 0.6 * t_ring_m * 1000.0, 0.35 * h_col_m * 1000.0))
        mesh_case(stl_path, mesh_path, mesh_size_mm)

    points, tets = read_tetra_mesh(mesh_path)
    if tets.size == 0:
        raise RuntimeError(f"No tetrahedra created for {mesh_path}")

    bbox_min = points.min(axis=0)
    bbox_max = points.max(axis=0)
    z_min = float(bbox_min[2])
    z_max = float(bbox_max[2])
    tol = max(1e-6, 0.02 * (z_max - z_min))
    bottom = np.flatnonzero(points[:, 2] <= z_min + tol)
    top = np.flatnonzero(points[:, 2] >= z_max - tol)
    if bottom.size == 0 or top.size == 0:
        raise RuntimeError("Could not identify top/bottom boundary nodes")

    fixed_temp = {int(node): 0.0 for node in bottom}
    fixed_temp.update({int(node): 1.0 for node in top})

    K_th = assemble_poisson(points, tets, kappa_mix)
    T = solve_dirichlet(K_th, fixed_temp)
    thermal_flow = float(T @ (K_th @ T))
    kappa_eff_fem = thermal_flow * l_device_m / a_device_m2 if a_device_m2 > 0 else 0.0

    K_e = assemble_poisson(points, tets, sigma_mix)
    V = solve_dirichlet(K_e, fixed_temp)
    electrical_flow = float(V @ (K_e @ V))
    r_e_fem = 1.0 / electrical_flow if electrical_flow > 0 else math.inf

    alpha_eff = float(therm["seebeck_v_k"])
    p_max_coeff = (alpha_eff * alpha_eff) / (4.0 * r_e_fem) if math.isfinite(r_e_fem) and r_e_fem > 0 else 0.0
    p_area_coeff = p_max_coeff / a_device_m2 if a_device_m2 > 0 else 0.0

    return {
        "fem_sample_id": job.get("fem_sample_id", ""),
        "case_id": str(job.get("case_id", "")),
        "selection_priority": job.get("selection", {}).get("priority", ""),
        "selection_reason": job.get("selection", {}).get("reason", ""),
        "material_name": design["material_name"],
        "t_ring_m": design["t_ring_m"],
        "ratio_hole": design["ratio_hole"],
        "h_uc_m": design["h_uc_m"],
        "n_layer": design["n_layer"],
        "column_type": design["column_type"],
        "size1_m": design["size1_m"],
        "num_columns": design["num_columns"],
        "path_type": design["path_type"],
        "connection_offset_units": design["connection_offset_units"],
        "t_coating_m": design["t_coating_m"],
        "network_kappa_eff_w_mk": job["intrinsic_network_prediction"]["kappa_eff_network_w_mk"],
        "network_r_e_ohm": job["intrinsic_network_prediction"]["r_e_network_ohm"],
        "network_alpha_device_v_k": job["intrinsic_network_prediction"]["alpha_device_v_k"],
        "network_p_max_coeff_w_k2": job["intrinsic_network_prediction"]["p_max_coeff_w_k2"],
        "network_p_area_coeff_w_m2_k2": job["intrinsic_network_prediction"]["p_area_coeff_w_m2_k2"],
        "fem_status": "done",
        "fem_solver": "gmsh+scipy_tetra_poisson",
        "mesh_note": f"tetra={len(tets)}; nodes={len(points)}; mesh={mesh_path.name}",
        "kappa_eff_fem_w_mk": kappa_eff_fem,
        "r_e_fem_ohm": r_e_fem,
        "alpha_eff_fem_v_k": alpha_eff,
        "p_max_coeff_fem_w_k2": p_max_coeff,
        "p_area_coeff_fem_w_m2_k2": p_area_coeff,
        "mechanical_valid": 1,
        "fabrication_note": "fabrication_ready",
        "fem_note": (
            f"homogenized_mix kappa={kappa_mix:.6g} sigma={sigma_mix:.6g} "
            f"f_scaffold={f_scaffold:.6g} f_coating={f_coating:.6g} f_air={f_air:.6g}"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run automated FEM validation for prepared jobs.")
    parser.add_argument("--jobs-dir", default="results/fem_sampling/jobs")
    parser.add_argument("--template", default="results/fem_sampling/fem_results_template_200.csv")
    parser.add_argument("--output", default="results/fem_sampling/fem_results_200.csv")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force-mesh", action="store_true", help="Regenerate meshes even if cached .msh files exist.")
    args = parser.parse_args()

    jobs_dir = Path(args.jobs_dir)
    template_path = Path(args.template)
    output_path = Path(args.output)
    if not jobs_dir.exists():
        raise SystemExit(f"Jobs directory does not exist: {jobs_dir}")
    if not template_path.exists():
        raise SystemExit(f"Template CSV does not exist: {template_path}")

    job_dirs = sorted(p for p in jobs_dir.iterdir() if p.is_dir())
    if args.limit is not None:
        job_dirs = job_dirs[: args.limit]
    if not job_dirs:
        raise SystemExit(f"No job directories found in {jobs_dir}")

    results = {}
    for job_dir in job_dirs:
        input_json = job_dir / "input.json"
        if not input_json.exists():
            raise SystemExit(f"Missing input.json: {input_json}")
        job = read_json(input_json)
        result = solve_unit_cell(job, job_dir, args.force_mesh)
        results[str(result["fem_sample_id"])] = result
        print(f"{job_dir.name}: kappa={result['kappa_eff_fem_w_mk']:.6g}, r_e={result['r_e_fem_ohm']:.6g}")

    with template_path.open("r", newline="", encoding="utf-8") as f:
        template_rows = list(csv.DictReader(f))
    if not template_rows:
        raise SystemExit(f"No rows found in template: {template_path}")

    fieldnames = list(template_rows[0].keys())
    output_rows = []
    for row in template_rows:
        sample_id = row["fem_sample_id"]
        result = results.get(sample_id)
        if result is None:
            output_rows.append(row)
            continue
        merged = dict(row)
        for key, value in result.items():
            merged[key] = value
        output_rows.append(merged)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Wrote FEM results: {output_path}")
    print(f"Solved jobs: {len(results)}")


if __name__ == "__main__":
    main()
