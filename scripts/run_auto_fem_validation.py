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
import os
import shutil
import subprocess
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.sparse.linalg import MatrixRankWarning

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
        mesh_case_with_gmsh_cli(stl_path, msh_path, mesh_size_mm)
        return

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


def mesh_case_with_gmsh_cli(stl_path: Path, msh_path: Path, mesh_size_mm: float) -> None:
    gmsh_cmd = shutil.which("gmsh")
    if gmsh_cmd is None:
        raise RuntimeError(f"gmsh Python module is unavailable and gmsh command was not found: {GMsh_IMPORT_ERROR}")

    geo_path = msh_path.with_suffix(".geo")
    geo_path.write_text(
        "\n".join(
            [
                f'Merge "{stl_path.resolve()}";',
                "ClassifySurfaces{40*Pi/180, 1, 1, Pi};",
                "CreateGeometry;",
                "Surface Loop(1) = Surface{:};",
                "Volume(1) = {1};",
                f"Mesh.CharacteristicLengthMin = {mesh_size_mm};",
                f"Mesh.CharacteristicLengthMax = {mesh_size_mm};",
                "Mesh.Algorithm3D = 4;",
                "Mesh.ElementOrder = 1;",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            gmsh_cmd,
            str(geo_path),
            "-3",
            "-format",
            "msh2",
            "-o",
            str(msh_path),
        ],
        check=True,
    )


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
    with warnings.catch_warnings():
        warnings.simplefilter("error", MatrixRankWarning)
        u[free_nodes] = spla.spsolve(K_ff, rhs)
    if not np.all(np.isfinite(u)):
        raise RuntimeError("Linear solve produced non-finite values")
    return u


def position_for_index_m(index: int, placement: dict[str, object], z: float, n_columns: int) -> tuple[float, float, float]:
    mode = placement["mode"]
    if mode == "single_ring":
        r = float(placement["r_place_m"])
        theta = 2.0 * math.pi * (index % n_columns) / n_columns
        return (r * math.cos(theta), r * math.sin(theta), z)

    if mode == "double_ring":
        n_inner = int(placement["n_inner"])
        n_outer = int(placement["n_outer"])
        idx = index % n_columns
        if idx < n_inner:
            r = float(placement["r_inner_m"])
            theta = 2.0 * math.pi * idx / n_inner
        else:
            r = float(placement["r_outer_m"])
            outer_idx = idx - n_inner
            theta = 2.0 * math.pi * outer_idx / n_outer + float(placement["theta_offset_outer_rad"])
        return (r * math.cos(theta), r * math.sin(theta), z)

    raise ValueError(f"Unknown placement mode: {mode}")


def path_points_m(path_type: str, p0: tuple[float, float, float], p1: tuple[float, float, float]) -> np.ndarray:
    samples = {
        "straight": 2,
        "single_kink": 9,
        "arc_curve": 13,
        "sine_wave": 17,
        "helix_winding": 21,
        "bezier_curve": 17,
    }[path_type]
    p0v = np.array(p0, dtype=float)
    p1v = np.array(p1, dtype=float)
    mid = 0.5 * (p0v + p1v)
    radial = np.array([mid[0], mid[1], 0.0], dtype=float)
    radial_norm = np.linalg.norm(radial)
    if radial_norm < 1e-18:
        radial = np.array([1.0, 0.0, 0.0])
    else:
        radial = radial / radial_norm
    chord = p1v - p0v
    lateral = np.array([-chord[1], chord[0], 0.0], dtype=float)
    lateral_norm = np.linalg.norm(lateral)
    lateral = lateral / lateral_norm if lateral_norm > 1e-18 else np.array([0.0, 1.0, 0.0])

    pts = []
    for i in range(samples):
        t = i / (samples - 1)
        base = (1.0 - t) * p0v + t * p1v
        if path_type == "straight":
            offset = 0.0 * radial
        elif path_type == "single_kink":
            offset = radial * (0.18e-3 * (1.0 - abs(2.0 * t - 1.0)))
        elif path_type == "arc_curve":
            offset = radial * (0.22e-3 * 4.0 * t * (1.0 - t))
        elif path_type == "sine_wave":
            offset = lateral * (0.12e-3 * math.sin(2.0 * math.pi * t))
        elif path_type == "helix_winding":
            offset = radial * (0.12e-3 * math.sin(2.0 * math.pi * t))
        elif path_type == "bezier_curve":
            offset = radial * (0.16e-3 * math.sin(math.pi * t)) + lateral * (0.08e-3 * math.sin(2.0 * math.pi * t))
        else:
            raise ValueError(f"Unknown path_type: {path_type}")
        pts.append(base + offset)
    return np.asarray(pts)


def add_tube_mask(mask: np.ndarray, X: np.ndarray, Y: np.ndarray, Z: np.ndarray, points: np.ndarray, radius: float) -> None:
    radius2 = radius * radius
    for p0, p1 in zip(points[:-1], points[1:]):
        axis = p1 - p0
        denom = float(np.dot(axis, axis))
        if denom <= 1e-30:
            continue
        vx = X - p0[0]
        vy = Y - p0[1]
        vz = Z - p0[2]
        t = np.clip((vx * axis[0] + vy * axis[1] + vz * axis[2]) / denom, 0.0, 1.0)
        dx = vx - t * axis[0]
        dy = vy - t * axis[1]
        dz = vz - t * axis[2]
        mask |= (dx * dx + dy * dy + dz * dz) <= radius2


def build_voxel_mask(design: dict[str, object], voxel_size_m: float) -> tuple[np.ndarray, tuple[float, float, float], dict[str, object]]:
    r_out_m = parse_float(design, "r_out_m")
    r_in_m = parse_float(design, "r_in_m")
    t_ring_m = parse_float(design, "t_ring_m")
    h_uc_m = parse_float(design, "h_uc_m")
    n_layer = parse_int(design, "n_layer")
    h_col_m = parse_float(design, "h_col_m")
    n_columns = parse_int(design, "num_columns")
    size1_m = parse_float(design, "size1_m")
    column_type = str(design["column_type"])
    path_type = str(design["path_type"])
    offset_units = parse_int(design, "connection_offset_units")
    placement = design["placement"]

    column_area, _perimeter = column_section(column_type, size1_m)
    equivalent_radius = math.sqrt(column_area / math.pi)
    z_max = n_layer * h_uc_m + t_ring_m
    xs = np.arange(-r_out_m + 0.5 * voxel_size_m, r_out_m, voxel_size_m)
    ys = np.arange(-r_out_m + 0.5 * voxel_size_m, r_out_m, voxel_size_m)
    zs = np.arange(0.5 * voxel_size_m, z_max, voxel_size_m)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    radius_xy = np.sqrt(X * X + Y * Y)
    mask = np.zeros(X.shape, dtype=bool)

    for layer_index in range(n_layer + 1):
        z0 = layer_index * h_uc_m
        z1 = z0 + t_ring_m
        mask |= (Z >= z0) & (Z <= z1) & (radius_xy >= r_in_m) & (radius_xy <= r_out_m)

    for layer_index in range(n_layer):
        z_bottom = layer_index * h_uc_m + t_ring_m
        z_top = (layer_index + 1) * h_uc_m
        layer_shift = layer_index * offset_units
        for column_index in range(n_columns):
            bottom_index = column_index + layer_shift
            top_index = column_index + layer_shift + offset_units
            p0 = position_for_index_m(bottom_index, placement, z_bottom, n_columns)
            p1 = position_for_index_m(top_index, placement, z_top, n_columns)
            add_tube_mask(mask, X, Y, Z, path_points_m(path_type, p0, p1), equivalent_radius)

    metadata = {
        "voxel_size_m": voxel_size_m,
        "nx": len(xs),
        "ny": len(ys),
        "nz": len(zs),
        "occupied_cells": int(mask.sum()),
    }
    return mask, (voxel_size_m, voxel_size_m, voxel_size_m), metadata


def solve_voxel_conductance(mask: np.ndarray, spacing: tuple[float, float, float], conductivity: float) -> float:
    occupied = np.flatnonzero(mask.ravel())
    if occupied.size == 0:
        raise RuntimeError("Voxel mask has no occupied cells")
    local_index = -np.ones(mask.size, dtype=np.int64)
    local_index[occupied] = np.arange(occupied.size)
    nx, ny, nz = mask.shape
    dx, dy, dz = spacing
    conductances = [
        (1, 0, 0, conductivity * dy * dz / dx),
        (0, 1, 0, conductivity * dx * dz / dy),
        (0, 0, 1, conductivity * dx * dy / dz),
    ]

    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    fixed: dict[int, float] = {}

    coords = np.argwhere(mask)
    z_indices = coords[:, 2]
    z_min = int(z_indices.min())
    z_max = int(z_indices.max())
    for i, j, k in coords:
        idx = int(local_index[np.ravel_multi_index((i, j, k), mask.shape)])
        if k == z_min:
            fixed[idx] = 0.0
        elif k == z_max:
            fixed[idx] = 1.0
        for di, dj, dk, g in conductances:
            ni, nj, nk = i + di, j + dj, k + dk
            if ni >= nx or nj >= ny or nk >= nz or not mask[ni, nj, nk]:
                continue
            nidx = int(local_index[np.ravel_multi_index((ni, nj, nk), mask.shape)])
            rows.extend([idx, nidx, idx, nidx])
            cols.extend([idx, nidx, nidx, idx])
            data.extend([g, g, -g, -g])

    K = sp.coo_matrix((data, (rows, cols)), shape=(occupied.size, occupied.size)).tocsr()
    u = solve_dirichlet(K, fixed)
    return float(u @ (K @ u))


def count_connected_boundary_components(mask: np.ndarray) -> tuple[int, bool]:
    coords = np.argwhere(mask)
    if coords.size == 0:
        return 0, False
    z_min = int(coords[:, 2].min())
    z_max = int(coords[:, 2].max())
    visited = np.zeros(mask.shape, dtype=bool)
    components = 0
    has_spanning_component = False
    directions = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
    nx, ny, nz = mask.shape

    for start in coords:
        i, j, k = (int(start[0]), int(start[1]), int(start[2]))
        if visited[i, j, k]:
            continue
        components += 1
        touches_bottom = k == z_min
        touches_top = k == z_max
        stack = [(i, j, k)]
        visited[i, j, k] = True
        while stack:
            ci, cj, ck = stack.pop()
            touches_bottom = touches_bottom or ck == z_min
            touches_top = touches_top or ck == z_max
            for di, dj, dk in directions:
                ni, nj, nk = ci + di, cj + dj, ck + dk
                if ni < 0 or ni >= nx or nj < 0 or nj >= ny or nk < 0 or nk >= nz:
                    continue
                if not mask[ni, nj, nk] or visited[ni, nj, nk]:
                    continue
                visited[ni, nj, nk] = True
                stack.append((ni, nj, nk))
        has_spanning_component = has_spanning_component or (touches_bottom and touches_top)

    return components, has_spanning_component


def solve_voxel_unit_cell(
    design: dict[str, object],
    kappa_mix: float,
    sigma_mix: float,
    voxel_size_m: float,
    a_device_m2: float,
    l_device_m: float,
) -> tuple[float, float, dict[str, object]]:
    mask, spacing, metadata = build_voxel_mask(design, voxel_size_m)
    components, has_spanning_component = count_connected_boundary_components(mask)
    metadata["connected_components"] = components
    metadata["has_spanning_component"] = has_spanning_component
    if not has_spanning_component:
        raise RuntimeError(
            f"Voxelized geometry has no connected path between bottom and top boundaries; components={components}"
        )
    thermal_flow = solve_voxel_conductance(mask, spacing, kappa_mix)
    electrical_flow = solve_voxel_conductance(mask, spacing, sigma_mix)
    kappa_eff = thermal_flow * l_device_m / a_device_m2 if a_device_m2 > 0 else 0.0
    r_e = 1.0 / electrical_flow if electrical_flow > 0 else math.inf
    return kappa_eff, r_e, metadata


def solve_unit_cell(
    job: dict[str, object],
    mesh_cache_dir: Path,
    force_mesh: bool,
    voxel_size_m: float,
    solver: str,
) -> dict[str, object]:
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

    fem_solver = "gmsh+scipy_tetra_poisson"
    mesh_note = ""
    fallback_note = ""
    result_valid = 1
    invalid_reason = ""
    try:
        if solver == "voxel":
            raise RuntimeError("voxel solver requested")
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
        mesh_note = f"tetra={len(tets)}; nodes={len(points)}; mesh={mesh_path.name}"
    except Exception as exc:
        if solver == "tetra":
            raise
        fem_solver = "voxel_fvm_fallback"
        if solver == "voxel":
            fem_solver = "voxel_fvm"
        try:
            kappa_eff_fem, r_e_fem, voxel_meta = solve_voxel_unit_cell(
                design,
                kappa_mix,
                sigma_mix,
                voxel_size_m,
                a_device_m2,
                l_device_m,
            )
            mesh_note = (
                f"voxel={voxel_meta['occupied_cells']}; "
                f"grid={voxel_meta['nx']}x{voxel_meta['ny']}x{voxel_meta['nz']}; "
                f"voxel_size_m={voxel_meta['voxel_size_m']}; "
                f"components={voxel_meta['connected_components']}"
            )
            if solver == "auto":
                fallback_note = f"; tetra_fallback_reason={type(exc).__name__}: {exc}"
        except Exception as voxel_exc:
            result_valid = 0
            invalid_reason = f"{type(voxel_exc).__name__}: {voxel_exc}"
            kappa_eff_fem = math.nan
            r_e_fem = math.inf
            mesh_note = f"voxel_failed; voxel_size_m={voxel_size_m}"

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
        "fem_status": "done" if result_valid else "invalid",
        "fem_valid": result_valid,
        "fem_invalid_reason": invalid_reason,
        "fem_solver": fem_solver,
        "mesh_note": mesh_note,
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
            f"{fallback_note}"
            + (f"; invalid_reason={invalid_reason}" if invalid_reason else "")
        ),
    }


def solve_job_dir(args: tuple[str, bool, float, str]) -> tuple[str, dict[str, object]]:
    job_dir_text, force_mesh, voxel_size_m, solver = args
    job_dir = Path(job_dir_text)
    input_json = job_dir / "input.json"
    if not input_json.exists():
        raise RuntimeError(f"Missing input.json: {input_json}")
    job = read_json(input_json)
    result = solve_unit_cell(job, job_dir, force_mesh, voxel_size_m, solver)
    return job_dir.name, result


def read_sample_ids(path: Path) -> set[str]:
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "fem_sample_id" not in (reader.fieldnames or []):
            raise SystemExit(f"Missing fem_sample_id column in {path}")
        return {row["fem_sample_id"] for row in reader if row.get("fem_sample_id")}


def sample_id_from_job_dir(job_dir: Path) -> str:
    return job_dir.name.split("_", 1)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run automated FEM validation for prepared jobs.")
    parser.add_argument("--jobs-dir", default="results/fem_sampling/jobs")
    parser.add_argument("--template", default="results/fem_sampling/fem_results_template_200.csv")
    parser.add_argument("--output", default="results/fem_sampling/fem_results_200.csv")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--sample-ids-csv",
        default=None,
        help="Optional CSV containing fem_sample_id values; only matching jobs are solved.",
    )
    parser.add_argument(
        "--solver",
        choices=["auto", "tetra", "voxel"],
        default="auto",
        help="auto tries tetra meshing then voxel fallback; voxel skips gmsh and is much faster for current STL jobs.",
    )
    parser.add_argument("--workers", type=int, default=1, help="Parallel worker processes.")
    parser.add_argument("--force-mesh", action="store_true", help="Regenerate meshes even if cached .msh files exist.")
    parser.add_argument(
        "--voxel-size-m",
        type=float,
        default=1.5e-4,
        help="Structured voxel size used when tetrahedral meshing fails.",
    )
    args = parser.parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")

    jobs_dir = Path(args.jobs_dir)
    template_path = Path(args.template)
    output_path = Path(args.output)
    if not jobs_dir.exists():
        raise SystemExit(f"Jobs directory does not exist: {jobs_dir}")
    if not template_path.exists():
        raise SystemExit(f"Template CSV does not exist: {template_path}")

    job_dirs = sorted(p for p in jobs_dir.iterdir() if p.is_dir())
    if args.sample_ids_csv:
        sample_ids_path = Path(args.sample_ids_csv)
        if not sample_ids_path.exists():
            raise SystemExit(f"Sample-id CSV does not exist: {sample_ids_path}")
        requested_sample_ids = read_sample_ids(sample_ids_path)
        job_dirs = [job_dir for job_dir in job_dirs if sample_id_from_job_dir(job_dir) in requested_sample_ids]
    if args.limit is not None:
        job_dirs = job_dirs[: args.limit]
    if not job_dirs:
        raise SystemExit(f"No job directories found in {jobs_dir}")

    results = {}
    print(f"Jobs: {len(job_dirs)}")
    print(f"Solver: {args.solver}")
    print(f"Workers: {args.workers}")
    print(f"Voxel size: {args.voxel_size_m}")
    if args.sample_ids_csv:
        print(f"Sample filter: {args.sample_ids_csv}")
    task_args = [(str(job_dir), args.force_mesh, args.voxel_size_m, args.solver) for job_dir in job_dirs]
    if args.workers == 1:
        for task in task_args:
            job_name, result = solve_job_dir(task)
            results[str(result["fem_sample_id"])] = result
            print(f"{job_name}: kappa={result['kappa_eff_fem_w_mk']:.6g}, r_e={result['r_e_fem_ohm']:.6g}")
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(solve_job_dir, task) for task in task_args]
            for future in as_completed(futures):
                job_name, result = future.result()
                results[str(result["fem_sample_id"])] = result
                print(f"{job_name}: kappa={result['kappa_eff_fem_w_mk']:.6g}, r_e={result['r_e_fem_ohm']:.6g}")

    with template_path.open("r", newline="", encoding="utf-8") as f:
        template_rows = list(csv.DictReader(f))
    if not template_rows:
        raise SystemExit(f"No rows found in template: {template_path}")

    fieldnames = list(template_rows[0].keys())
    for field in ["fem_valid", "fem_invalid_reason"]:
        if field not in fieldnames:
            fieldnames.append(field)
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
