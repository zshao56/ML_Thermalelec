#!/usr/bin/env python3
"""Build the first-pass ring-column unit-cell design database.

The database contains the full Cartesian design space for:

    ring_thickness(3) * hole_ratio(4) * H_uc(4)
    * column_type(4) * size1(4) * num(4)
    * connection_offset(3)
    * carrier_type(2) * coating_thickness(4)
    * path_type(6)

Total rows: 442,368.

Rows are not filtered out when geometry checks fail. They are kept and marked
with geometry_valid=false so downstream workflows can decide how to filter.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MM = 1e-3
UM = 1e-6
NM = 1e-9


@dataclass(frozen=True)
class Material:
    carrier_type: str
    material_name: str
    seebeck_uv_per_k: float
    sigma_s_per_m: float
    kappa_w_per_mk: float
    density_kg_per_m3: float
    contact_ohm_min: float
    contact_ohm_max: float

    @property
    def seebeck_v_per_k(self) -> float:
        return self.seebeck_uv_per_k * 1e-6

    @property
    def contact_ohm_nominal(self) -> float:
        return 0.5 * (self.contact_ohm_min + self.contact_ohm_max)


MATERIALS = [
    Material(
        carrier_type="n",
        material_name="Bi2Te3",
        seebeck_uv_per_k=-155.0,
        sigma_s_per_m=400.0 * 100.0,  # 400 S/cm -> S/m
        kappa_w_per_mk=1.0,
        density_kg_per_m3=7860.0,
        contact_ohm_min=50.0,
        contact_ohm_max=1000.0,
    ),
    Material(
        carrier_type="p",
        material_name="Sb2Te3",
        seebeck_uv_per_k=100.0,
        sigma_s_per_m=1000.0 * 100.0,  # 1000 S/cm -> S/m
        kappa_w_per_mk=1.0,
        density_kg_per_m3=6500.0,
        contact_ohm_min=20.0,
        contact_ohm_max=400.0,
    ),
]


R_OUT_M = 2.0 * MM
H_TOTAL_M = 10.0 * MM
KAPPA_SCAFFOLD = 0.25
KAPPA_AIR = 0.026
COVERAGE_RATIO_DEFAULT = 1.0
C_NET_DEFAULT = 1.0
GAP_MIN_M = 0.05 * MM

RING_THICKNESSES_M = [0.1 * MM, 0.2 * MM, 0.3 * MM]
HOLE_RATIOS = [0.0, 0.25, 0.5, 0.75]
H_UC_VALUES_M = [1.0 * MM, 2.5 * MM, 5.0 * MM, 10.0 * MM]
SIZE1_VALUES_M = [0.1 * MM, 0.15 * MM, 0.2 * MM, 0.25 * MM]
NUM_VALUES = [5, 10, 15, 20]
T_COATING_VALUES_M = [500 * NM, 1000 * NM, 1500 * NM, 2000 * NM]
CONNECTION_OFFSET_FIFTH_STEPS = [0, 1, 2]

COLUMN_TYPES = [
    ("circular_column", "d"),
    ("square_column", "a"),
    ("pentagonal_column", "D_poly"),
    ("hexagonal_column", "D_poly"),
]

# First pass: all six route/path types are included with one default setting.
# path_length_factor is a coarse analytic placeholder; exact centerline length
# can be recomputed from CAD/mesh later.
PATH_TYPES = [
    ("straight", 1.00, {"formula": "line", "default": True}),
    ("single_kink", 1.15, {"formula": "piecewise_line", "r": 0.5, "dx_mm": 0.0, "dy_mm": 0.0}),
    ("arc_curve", 1.10, {"formula": "quadratic_bezier_arc", "curve_c": 0.5, "plane": "XZ", "direction": "convex"}),
    ("sine_wave", 1.20, {"formula": "sine", "amplitude_ratio": 0.10, "periods": 1.0, "phase": 0.0}),
    ("helix_winding", 1.35, {"formula": "helix", "radius_ratio": 0.15, "turns": 1.0}),
    ("bezier_curve", 1.20, {"formula": "cubic_bezier", "r1": 0.33, "r2": 0.66, "dx1_mm": 0.0, "dy1_mm": 0.0, "dx2_mm": 0.0, "dy2_mm": 0.0}),
]


def polygon_area_from_circumradius(n_sides: int, radius_m: float) -> float:
    return 0.5 * n_sides * radius_m * radius_m * math.sin(2.0 * math.pi / n_sides)


def polygon_perimeter_from_circumradius(n_sides: int, radius_m: float) -> float:
    return 2.0 * n_sides * radius_m * math.sin(math.pi / n_sides)


def column_section(column_type: str, size1_m: float) -> tuple[float, float, float]:
    """Return cross-sectional area, perimeter, and collision radius."""
    if column_type == "circular_column":
        radius = 0.5 * size1_m
        return math.pi * radius * radius, 2.0 * math.pi * radius, radius
    if column_type == "square_column":
        return size1_m * size1_m, 4.0 * size1_m, size1_m / math.sqrt(2.0)
    if column_type == "pentagonal_column":
        radius = 0.5 * size1_m
        return (
            polygon_area_from_circumradius(5, radius),
            polygon_perimeter_from_circumradius(5, radius),
            radius,
        )
    if column_type == "hexagonal_column":
        radius = 0.5 * size1_m
        return (
            polygon_area_from_circumradius(6, radius),
            polygon_perimeter_from_circumradius(6, radius),
            radius,
        )
    raise ValueError(f"Unknown column_type: {column_type}")


def ring_surface_area(r_out_m: float, r_in_m: float, t_ring_m: float) -> float:
    annulus_area = math.pi * (r_out_m * r_out_m - r_in_m * r_in_m)
    outer_wall = 2.0 * math.pi * r_out_m * t_ring_m
    inner_wall = 2.0 * math.pi * r_in_m * t_ring_m if r_in_m > 0 else 0.0
    return 2.0 * annulus_area + outer_wall + inner_wall


def placement_check(
    n_columns: int,
    r_in_m: float,
    r_out_m: float,
    feature_radius_m: float,
    gap_min_m: float,
) -> tuple[str, str, float, bool, str]:
    """Choose deterministic loose placement and check simple collisions.

    Returns placement_mode, placement_json, effective connection radius, valid,
    invalid_reason.
    """
    if n_columns < 15:
        lambda_r = 0.75
        r_place = r_in_m + lambda_r * (r_out_m - r_in_m)
        min_neighbor = 2.0 * r_place * math.sin(math.pi / n_columns)
        boundary_ok = (r_in_m + feature_radius_m + gap_min_m <= r_place <= r_out_m - feature_radius_m - gap_min_m)
        collision_ok = min_neighbor >= 2.0 * feature_radius_m + gap_min_m
        payload = {
            "mode": "single_ring",
            "lambda_r": lambda_r,
            "r_place_m": r_place,
            "connection_radius_eff_m": r_place,
        }
        valid = boundary_ok and collision_ok
        reason = "" if valid else "collision_or_boundary_violation"
        return "single_ring", json.dumps(payload, ensure_ascii=True), r_place, valid, reason

    # Split high-count cases into two rings.
    n_inner = n_columns // 2
    n_outer = n_columns - n_inner
    r1 = r_in_m + 0.35 * (r_out_m - r_in_m)
    r2 = r_in_m + 0.75 * (r_out_m - r_in_m)
    min_inner = 2.0 * r1 * math.sin(math.pi / max(n_inner, 1))
    min_outer = 2.0 * r2 * math.sin(math.pi / max(n_outer, 1))
    radial_gap = abs(r2 - r1)
    boundary_ok = (
        r_in_m + feature_radius_m + gap_min_m <= r1
        and r2 <= r_out_m - feature_radius_m - gap_min_m
    )
    collision_ok = (
        min_inner >= 2.0 * feature_radius_m + gap_min_m
        and min_outer >= 2.0 * feature_radius_m + gap_min_m
        and radial_gap >= 2.0 * feature_radius_m + gap_min_m
    )
    payload = {
        "mode": "double_ring",
        "r_inner_m": r1,
        "r_outer_m": r2,
        "n_inner": n_inner,
        "n_outer": n_outer,
        "theta_offset_outer_rad": math.pi / max(n_outer, 1),
    }
    r_eff = (n_inner * r1 + n_outer * r2) / n_columns
    payload["connection_radius_eff_m"] = r_eff
    valid = boundary_ok and collision_ok
    reason = "" if valid else "collision_or_boundary_violation"
    return "double_ring", json.dumps(payload, ensure_ascii=True), r_eff, valid, reason


def connection_offsets_for_num(n_columns: int) -> list[int]:
    """Return layer-to-layer index offsets: 0, n/5, and 2n/5."""
    if n_columns % 5 != 0:
        raise ValueError(f"num_columns must be divisible by 5, got {n_columns}")
    return [step * n_columns // 5 for step in CONNECTION_OFFSET_FIFTH_STEPS]


# =========================================================================
# 3D pillar collision detection for design-space validity filtering
# =========================================================================

_Vec3 = tuple[float, float, float]


def _v3_add(a: _Vec3, b: _Vec3) -> _Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _v3_sub(a: _Vec3, b: _Vec3) -> _Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _v3_scale(a: _Vec3, s: float) -> _Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _v3_dot(a: _Vec3, b: _Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _v3_norm(a: _Vec3) -> float:
    return math.sqrt(_v3_dot(a, a))


def _v3_unit(a: _Vec3) -> _Vec3:
    n = _v3_norm(a)
    if n < 1e-12:
        return (1.0, 0.0, 0.0)
    return _v3_scale(a, 1.0 / n)


def _v3_lerp(a: _Vec3, b: _Vec3, t: float) -> _Vec3:
    return _v3_add(_v3_scale(a, 1.0 - t), _v3_scale(b, t))


def _v3_cross(a: _Vec3, b: _Vec3) -> _Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _v3_dist(a: _Vec3, b: _Vec3) -> float:
    return _v3_norm(_v3_sub(a, b))


def _radial_dir(p0: _Vec3, p1: _Vec3) -> _Vec3:
    mid = (0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1]), 0.0)
    return _v3_unit(mid)


def _lateral_dir(p0: _Vec3, p1: _Vec3) -> _Vec3:
    chord = _v3_sub(p1, p0)
    return _v3_unit((-chord[1], chord[0], 0.0))


def _sample_path_3d(
    path_type: str,
    p0: _Vec3,
    p1: _Vec3,
    n_samples: int,
    path_params: dict[str, object] | None = None,
) -> list[_Vec3]:
    """Sample centreline points along a pillar path (all units in metres)."""
    if path_type == "straight":
        return [_v3_lerp(p0, p1, i / max(n_samples - 1, 1)) for i in range(n_samples)]

    if path_type == "single_kink":
        bump = _v3_scale(_radial_dir(p0, p1), 0.42 * MM)
        mid = _v3_add(_v3_scale(_v3_add(p0, p1), 0.5), bump)
        pts: list[_Vec3] = []
        for i in range(n_samples):
            t = i / (n_samples - 1)
            if t < 0.5:
                pts.append(_v3_lerp(p0, mid, 2.0 * t))
            else:
                pts.append(_v3_lerp(mid, p1, 2.0 * (t - 0.5)))
        return pts

    if path_type == "arc_curve":
        control = _v3_add(
            _v3_scale(_v3_add(p0, p1), 0.5),
            _v3_scale(_radial_dir(p0, p1), 0.55 * MM),
        )
        pts: list[_Vec3] = []
        for i in range(n_samples):
            t = i / (n_samples - 1)
            pts.append(
                _v3_add(
                    _v3_add(
                        _v3_scale(p0, (1.0 - t) ** 2),
                        _v3_scale(control, 2.0 * (1.0 - t) * t),
                    ),
                    _v3_scale(p1, t * t),
                )
            )
        return pts

    if path_type == "sine_wave":
        lateral = _lateral_dir(p0, p1)
        outward = _radial_dir(p0, p1)
        pts: list[_Vec3] = []
        for i in range(n_samples):
            t = i / (n_samples - 1)
            base = _v3_lerp(p0, p1, t)
            offset = _v3_add(
                _v3_scale(lateral, 0.23 * MM * math.sin(2.0 * math.pi * t)),
                _v3_scale(outward, 0.10 * MM * math.sin(4.0 * math.pi * t)),
            )
            pts.append(_v3_add(base, offset))
        return pts

    if path_type == "helix_winding":
        axis = _v3_sub(p1, p0)
        axis_len = _v3_norm(axis)
        e_axis = _v3_unit(axis)
        u = _v3_unit(_v3_cross(e_axis, (0.0, 0.0, 1.0)))
        v = _v3_unit(_v3_cross(e_axis, u))
        params = path_params or {}
        radius_ratio = float(params.get("radius_ratio", 0.15))
        turns = float(params.get("turns", 1.0))
        helix_r = axis_len * radius_ratio
        pts: list[_Vec3] = []
        for i in range(n_samples):
            t = i / (n_samples - 1)
            base = _v3_lerp(p0, p1, t)
            env = math.sin(math.pi * t)
            phase = 2.0 * math.pi * turns * t
            offset = _v3_scale(
                _v3_add(
                    _v3_scale(u, math.cos(phase)),
                    _v3_scale(v, math.sin(phase)),
                ),
                helix_r * env,
            )
            pts.append(_v3_add(base, offset))
        return pts

    if path_type == "bezier_curve":
        d = _v3_sub(p1, p0)
        outward = _radial_dir(p0, p1)
        lateral = _lateral_dir(p0, p1)
        c1 = _v3_add(
            _v3_add(p0, _v3_scale(d, 0.28)),
            _v3_add(
                _v3_scale(outward, 0.50 * MM),
                _v3_scale(lateral, 0.22 * MM),
            ),
        )
        c2 = _v3_add(
            _v3_add(p0, _v3_scale(d, 0.72)),
            _v3_add(
                _v3_scale(outward, -0.35 * MM),
                _v3_scale(lateral, -0.24 * MM),
            ),
        )
        pts: list[_Vec3] = []
        for i in range(n_samples):
            t = i / (n_samples - 1)
            pts.append(
                _v3_add(
                    _v3_add(
                        _v3_scale(p0, (1.0 - t) ** 3),
                        _v3_scale(c1, 3.0 * (1.0 - t) ** 2 * t),
                    ),
                    _v3_add(
                        _v3_scale(c2, 3.0 * (1.0 - t) * t * t),
                        _v3_scale(p1, t**3),
                    ),
                )
            )
        return pts

    raise ValueError(f"Unknown path_type: {path_type}")


def _pillar_endpoints(
    placement: dict[str, object],
    n_columns: int,
    connection_offset_units: int,
    h_col_m: float,
) -> list[tuple[_Vec3, _Vec3]]:
    """Return (bottom, top) centreline endpoints for every pillar in a layer.

    Bottom points lie on the lower ring's top surface (z=0 in local coords).
    Top points lie on the upper ring's bottom surface (z=h_col_m).
    All co-ordinates are in metres.
    """
    mode = placement["mode"]
    endpoints: list[tuple[_Vec3, _Vec3]] = []

    if mode == "single_ring":
        r = float(placement["r_place_m"])
        for i in range(n_columns):
            th0 = 2.0 * math.pi * i / n_columns
            p0: _Vec3 = (r * math.cos(th0), r * math.sin(th0), 0.0)
            top_i = i + connection_offset_units
            th1 = 2.0 * math.pi * top_i / n_columns
            p1: _Vec3 = (r * math.cos(th1), r * math.sin(th1), h_col_m)
            endpoints.append((p0, p1))
        return endpoints

    if mode == "double_ring":
        n_inner = int(placement["n_inner"])
        n_outer = int(placement["n_outer"])
        r1 = float(placement["r_inner_m"])
        r2 = float(placement["r_outer_m"])
        off_outer = float(placement["theta_offset_outer_rad"])
        for i in range(n_columns):
            if i < n_inner:
                th0 = 2.0 * math.pi * i / n_inner
                p0 = (r1 * math.cos(th0), r1 * math.sin(th0), 0.0)
            else:
                th0 = 2.0 * math.pi * (i - n_inner) / n_outer + off_outer
                p0 = (r2 * math.cos(th0), r2 * math.sin(th0), 0.0)
            top_i = i + connection_offset_units
            if top_i < n_inner:
                th1 = 2.0 * math.pi * top_i / n_inner
                p1 = (r1 * math.cos(th1), r1 * math.sin(th1), h_col_m)
            else:
                th1 = 2.0 * math.pi * (top_i - n_inner) / n_outer + off_outer
                p1 = (r2 * math.cos(th1), r2 * math.sin(th1), h_col_m)
            endpoints.append((p0, p1))
        return endpoints

    raise ValueError(f"Unknown placement mode: {mode}")


def _seg_seg_min_dist(
    a0: _Vec3, a1: _Vec3, b0: _Vec3, b1: _Vec3
) -> float:
    """Analytic minimum distance between two 3-D line segments."""
    d1 = _v3_sub(a1, a0)
    d2 = _v3_sub(b1, b0)
    r = _v3_sub(a0, b0)
    a = _v3_dot(d1, d1)
    e = _v3_dot(d2, d2)
    f = _v3_dot(d2, r)

    if a <= 1e-20 and e <= 1e-20:
        return _v3_dist(a0, b0)
    if a <= 1e-20:
        s = 0.0
        t = max(0.0, min(1.0, f / e)) if e > 1e-20 else 0.0
    else:
        c = _v3_dot(d1, r)
        if e <= 1e-20:
            t = 0.0
            s = max(0.0, min(1.0, -c / a))
        else:
            b = _v3_dot(d1, d2)
            denom = a * e - b * b
            if abs(denom) > 1e-20:
                s = max(0.0, min(1.0, (b * f - c * e) / denom))
            else:
                s = 0.0
            tnom = b * s + f
            if tnom < 0.0:
                t = 0.0
                s = max(0.0, min(1.0, -c / a))
            elif tnom > e:
                t = 1.0
                s = max(0.0, min(1.0, (b - c) / a))
            else:
                t = tnom / e

    cp1 = _v3_add(a0, _v3_scale(d1, s))
    cp2 = _v3_add(b0, _v3_scale(d2, t))
    return _v3_dist(cp1, cp2)


def _check_3d_pillar_collision(
    path_type: str,
    endpoints: list[tuple[_Vec3, _Vec3]],
    feature_radius_m: float,
    gap_min_m: float,
    path_params: dict[str, object] | None = None,
    n_samples: int = 24,
) -> tuple[bool, str]:
    """Test whether any two pillar centreline paths infringe the minimum gap.

    Uses analytic segment-segment distance for straight paths and
    sampled centreline points for curved paths.

    Returns (has_collision, reason_string).
    """
    n_cols = len(endpoints)
    if n_cols <= 1:
        return False, ""
    min_allowed = 2.0 * feature_radius_m + gap_min_m

    if path_type == "straight":
        for i in range(n_cols):
            for j in range(i + 1, n_cols):
                d = _seg_seg_min_dist(
                    endpoints[i][0], endpoints[i][1],
                    endpoints[j][0], endpoints[j][1],
                )
                if d < min_allowed:
                    return True, f"3d_pillar_collision_{i}_{j}"
        return False, ""

    paths = [
        _sample_path_3d(path_type, ep[0], ep[1], n_samples, path_params)
        for ep in endpoints
    ]
    for i in range(n_cols):
        for j in range(i + 1, n_cols):
            for pi in paths[i]:
                for pj in paths[j]:
                    if _v3_dist(pi, pj) < min_allowed:
                        return True, f"3d_pillar_collision_{i}_{j}"
    return False, ""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS unit_cell_designs;
        DROP TABLE IF EXISTS scenario_cases;
        DROP TABLE IF EXISTS metadata;

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE scenario_cases (
            scenario_id TEXT PRIMARY KEY,
            scenario_name TEXT NOT NULL,
            boundary_type TEXT NOT NULL,
            t_hot_surface_k REAL,
            t_cold_env_k REAL,
            q_hot_w_m2 REAL,
            h_cold_w_m2k REAL,
            notes TEXT
        );

        CREATE TABLE unit_cell_designs (
            case_id INTEGER PRIMARY KEY,

            -- Ring and cell geometry
            r_out_m REAL NOT NULL,
            ratio_hole REAL NOT NULL,
            r_in_m REAL NOT NULL,
            t_ring_m REAL NOT NULL,
            h_total_m REAL NOT NULL,
            h_uc_m REAL NOT NULL,
            n_layer INTEGER NOT NULL,
            h_col_m REAL NOT NULL,
            a_uc_m2 REAL NOT NULL,
            v_uc_m3 REAL NOT NULL,
            a_ring_m2 REAL NOT NULL,

            -- Column geometry
            column_type TEXT NOT NULL,
            size_param_name TEXT NOT NULL,
            size1_m REAL NOT NULL,
            num_columns INTEGER NOT NULL,
            column_cross_area_m2 REAL NOT NULL,
            column_perimeter_m REAL NOT NULL,
            feature_radius_m REAL NOT NULL,

            -- Path
            path_type TEXT NOT NULL,
            path_length_factor REAL NOT NULL,
            path_params_json TEXT NOT NULL,
            connection_offset_units INTEGER NOT NULL,
            connection_offset_fraction REAL NOT NULL,
            connection_twist_rad REAL NOT NULL,
            connection_chord_m REAL NOT NULL,
            l_path_m REAL NOT NULL,

            -- Deterministic placement
            placement_mode TEXT NOT NULL,
            placement_json TEXT NOT NULL,
            geometry_valid INTEGER NOT NULL,
            invalid_reason TEXT,

            -- Material/coating
            carrier_type TEXT NOT NULL,
            material_name TEXT NOT NULL,
            seebeck_uv_k REAL NOT NULL,
            seebeck_v_k REAL NOT NULL,
            sigma_s_m REAL NOT NULL,
            kappa_coating_w_mk REAL NOT NULL,
            density_kg_m3 REAL NOT NULL,
            t_coating_m REAL NOT NULL,
            coverage_ratio REAL NOT NULL,
            c_net REAL NOT NULL,
            r_contact_ohm_min REAL NOT NULL,
            r_contact_ohm_max REAL NOT NULL,
            r_contact_ohm_nominal REAL NOT NULL,

            -- Analytic geometry estimates
            v_ring_m3 REAL NOT NULL,
            v_columns_m3 REAL NOT NULL,
            v_scaffold_m3 REAL NOT NULL,
            a_ring_surface_m2 REAL NOT NULL,
            a_column_surface_m2 REAL NOT NULL,
            a_surface_uc_m2 REAL NOT NULL,
            v_coating_m3 REAL NOT NULL,
            f_scaffold REAL NOT NULL,
            f_coating REAL NOT NULL,
            f_air REAL NOT NULL,
            porosity REAL NOT NULL,

            -- First-pass analytic property estimates
            kappa_uc_est_w_mk REAL NOT NULL,
            n_square_eff_est REAL NOT NULL,
            r_sheet_ohm_sq REAL NOT NULL,
            r_coat_uc_est_ohm REAL NOT NULL,
            r_uc_est_ohm REAL NOT NULL
        );

        CREATE INDEX idx_unit_cell_geometry
            ON unit_cell_designs (t_ring_m, ratio_hole, h_uc_m, column_type, size1_m, num_columns, connection_offset_units);
        CREATE INDEX idx_unit_cell_material
            ON unit_cell_designs (carrier_type, material_name, t_coating_m);
        CREATE INDEX idx_unit_cell_path
            ON unit_cell_designs (path_type);
        CREATE INDEX idx_unit_cell_valid
            ON unit_cell_designs (geometry_valid);
        """
    )


def insert_metadata(conn: sqlite3.Connection, expected_count: int) -> None:
    rows = {
        "version": "unit_cell_first_pass_v1",
        "expected_unit_cell_design_rows": str(expected_count),
        "description": "Ring-column unit-cell design space with four column primitives, three layer-to-layer connection offsets, and six path types.",
        "count_formula": "3 ring thickness * 4 hole ratio * 4 height * 4 column type * 4 size * 4 num * 3 connection offset * 2 material * 4 coating thickness * 6 path = 442368",
        "h_col_definition": "h_col = H_uc - t_ring, shared-ring stacking assumption",
        "size1_definition": "circular d, square edge length a, pentagonal/hexagonal circumscribed diameter D_poly",
        "connection_offset_definition": "offset_units in {0, n/5, 2n/5}; examples n=5 -> 0/1/2, n=15 -> 0/3/6",
        "coverage_ratio_default": str(COVERAGE_RATIO_DEFAULT),
        "c_net_default": str(C_NET_DEFAULT),
    }
    conn.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", rows.items())


def insert_scenarios(conn: sqlite3.Connection) -> None:
    # Scenarios are lookup rows only; they are not crossed with designs in this
    # first-pass design-space database.
    rows = [
        ("wearable_static", "Wearable TEG static", "fixed_hot_surface_cold_convection", 303.15, 293.15, None, 5.0, "skin-side hot surface, natural convection"),
        ("wearable_active", "Wearable TEG active", "fixed_hot_surface_cold_convection", 308.15, 298.15, None, 15.0, "motion-enhanced natural convection"),
        ("pipe_static", "Pipe car waste heat static", "fixed_hot_surface_cold_convection", 393.15, 293.15, None, 10.0, "static air cooling"),
        ("pipe_active", "Pipe car waste heat active", "fixed_hot_surface_cold_convection", 433.15, 313.15, None, 100.0, "forced convection"),
        ("industrial", "Industrial waste heat", "fixed_hot_surface_cold_convection", 493.15, 323.15, None, 15.0, "natural convection"),
        ("laptop_cpu_gpu", "Laptop CPU/GPU", "fixed_q_cold_convection", None, 313.15, 10000.0, 100.0, "q_hot and hot-surface limit require confirmation"),
    ]
    conn.executemany(
        """
        INSERT INTO scenario_cases(
            scenario_id, scenario_name, boundary_type, t_hot_surface_k,
            t_cold_env_k, q_hot_w_m2, h_cold_w_m2k, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def iter_design_rows() -> Iterable[tuple]:
    case_id = 1
    _endpoint_cache: dict[tuple, list[tuple[_Vec3, _Vec3]]] = {}
    _collision_cache: dict[tuple, tuple[bool, str]] = {}
    for (
        t_ring_m,
        ratio_hole,
        h_uc_m,
        (column_type, size_param_name),
        size1_m,
        num_columns,
    ) in itertools.product(
        RING_THICKNESSES_M,
        HOLE_RATIOS,
        H_UC_VALUES_M,
        COLUMN_TYPES,
        SIZE1_VALUES_M,
        NUM_VALUES,
    ):
        r_in_m = ratio_hole * R_OUT_M
        n_layer = int(round(H_TOTAL_M / h_uc_m))
        h_col_m = h_uc_m - t_ring_m
        if h_col_m <= 0:
            geometry_valid = False
            invalid_reason = "non_positive_column_height"
        else:
            geometry_valid = True
            invalid_reason = ""

        a_uc_m2 = math.pi * R_OUT_M * R_OUT_M
        v_uc_m3 = a_uc_m2 * h_uc_m
        a_ring_m2 = math.pi * (R_OUT_M * R_OUT_M - r_in_m * r_in_m)
        v_ring_m3 = a_ring_m2 * t_ring_m

        column_area_m2, column_perimeter_m, feature_radius_m = column_section(column_type, size1_m)

        placement_mode, placement_json, connection_radius_eff_m, placement_valid, placement_reason = placement_check(
            num_columns, r_in_m, R_OUT_M, feature_radius_m, GAP_MIN_M
        )
        if not placement_valid:
            geometry_valid = False
            invalid_reason = placement_reason

        for (
            connection_offset_units,
            material,
            t_coating_m,
            (path_type, path_length_factor, path_params),
        ) in itertools.product(
            connection_offsets_for_num(num_columns),
            MATERIALS,
            T_COATING_VALUES_M,
            PATH_TYPES,
        ):
            connection_offset_fraction = connection_offset_units / num_columns
            connection_twist_rad = 2.0 * math.pi * connection_offset_fraction
            connection_chord_m = 2.0 * connection_radius_eff_m * math.sin(0.5 * abs(connection_twist_rad))
            direct_connection_length_m = math.sqrt(h_col_m * h_col_m + connection_chord_m * connection_chord_m)
            l_path_m = direct_connection_length_m * path_length_factor

            v_columns_m3 = column_area_m2 * l_path_m * num_columns
            v_scaffold_m3 = v_ring_m3 + v_columns_m3

            a_ring_surface_m2 = ring_surface_area(R_OUT_M, r_in_m, t_ring_m)
            a_column_surface_m2 = column_perimeter_m * l_path_m * num_columns
            a_surface_uc_m2 = a_ring_surface_m2 + a_column_surface_m2
            v_coating_m3 = a_surface_uc_m2 * t_coating_m * COVERAGE_RATIO_DEFAULT

            f_scaffold = v_scaffold_m3 / v_uc_m3
            f_coating = v_coating_m3 / v_uc_m3
            f_air = max(0.0, 1.0 - f_scaffold - f_coating)
            porosity = max(0.0, 1.0 - f_scaffold)
            row_geometry_valid = geometry_valid
            row_invalid_reason = invalid_reason
            if f_scaffold + f_coating > 1.0:
                row_geometry_valid = False
                row_invalid_reason = "solid_and_coating_volume_exceed_domain"

            if row_geometry_valid:
                ep_key = (placement_mode, num_columns, connection_offset_units, h_col_m)
                if ep_key not in _endpoint_cache:
                    placement_obj = json.loads(placement_json)
                    _endpoint_cache[ep_key] = _pillar_endpoints(
                        placement_obj, num_columns, connection_offset_units, h_col_m
                    )
                endpoints = _endpoint_cache[ep_key]
                col_key = (path_type, ep_key, feature_radius_m)
                if col_key not in _collision_cache:
                    has_col, reason = _check_3d_pillar_collision(
                        path_type, endpoints, feature_radius_m, GAP_MIN_M,
                        path_params,
                    )
                    _collision_cache[col_key] = (has_col, reason)
                has_col, col_reason = _collision_cache[col_key]
                if has_col:
                    row_geometry_valid = False
                    row_invalid_reason = (
                        col_reason if not row_invalid_reason else row_invalid_reason
                    )

            kappa_uc_est = (
                f_scaffold * KAPPA_SCAFFOLD
                + f_coating * material.kappa_w_per_mk
                + f_air * KAPPA_AIR
            )

            # Surface-current shape factor. The side coating on all columns is
            # treated as parallel conductive sheets. Ring spreading resistance is
            # ignored in this first-pass estimate and can be added after FEM.
            effective_width_m = max(column_perimeter_m * num_columns, 1e-18)
            n_square_eff_est = l_path_m / effective_width_m
            r_sheet_ohm_sq = 1.0 / (material.sigma_s_per_m * t_coating_m)
            r_coat_uc_est = r_sheet_ohm_sq * n_square_eff_est / (COVERAGE_RATIO_DEFAULT * C_NET_DEFAULT)
            r_uc_est = r_coat_uc_est + material.contact_ohm_nominal

            yield (
                case_id,
                R_OUT_M,
                ratio_hole,
                r_in_m,
                t_ring_m,
                H_TOTAL_M,
                h_uc_m,
                n_layer,
                h_col_m,
                a_uc_m2,
                v_uc_m3,
                a_ring_m2,
                column_type,
                size_param_name,
                size1_m,
                num_columns,
                column_area_m2,
                column_perimeter_m,
                feature_radius_m,
                path_type,
                path_length_factor,
                json.dumps(path_params, ensure_ascii=True),
                connection_offset_units,
                connection_offset_fraction,
                connection_twist_rad,
                connection_chord_m,
                l_path_m,
                placement_mode,
                placement_json,
                1 if row_geometry_valid else 0,
                row_invalid_reason,
                material.carrier_type,
                material.material_name,
                material.seebeck_uv_per_k,
                material.seebeck_v_per_k,
                material.sigma_s_per_m,
                material.kappa_w_per_mk,
                material.density_kg_per_m3,
                t_coating_m,
                COVERAGE_RATIO_DEFAULT,
                C_NET_DEFAULT,
                material.contact_ohm_min,
                material.contact_ohm_max,
                material.contact_ohm_nominal,
                v_ring_m3,
                v_columns_m3,
                v_scaffold_m3,
                a_ring_surface_m2,
                a_column_surface_m2,
                a_surface_uc_m2,
                v_coating_m3,
                f_scaffold,
                f_coating,
                f_air,
                porosity,
                kappa_uc_est,
                n_square_eff_est,
                r_sheet_ohm_sq,
                r_coat_uc_est,
                r_uc_est,
            )
            case_id += 1


def build_database(db_path: Path, overwrite: bool) -> None:
    if db_path.exists() and not overwrite:
        raise SystemExit(f"Database already exists: {db_path}. Pass --overwrite to replace it.")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    expected_count = (
        len(RING_THICKNESSES_M)
        * len(HOLE_RATIOS)
        * len(H_UC_VALUES_M)
        * len(COLUMN_TYPES)
        * len(SIZE1_VALUES_M)
        * len(NUM_VALUES)
        * len(CONNECTION_OFFSET_FIFTH_STEPS)
        * len(MATERIALS)
        * len(T_COATING_VALUES_M)
        * len(PATH_TYPES)
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        create_schema(conn)
        insert_metadata(conn, expected_count)
        insert_scenarios(conn)
        column_count = len(conn.execute("PRAGMA table_info(unit_cell_designs)").fetchall())
        insert_sql = f"INSERT INTO unit_cell_designs VALUES ({','.join(['?'] * column_count)})"
        conn.executemany(insert_sql, iter_design_rows())
        actual_count = conn.execute("SELECT COUNT(*) FROM unit_cell_designs").fetchone()[0]
        if actual_count != expected_count:
            raise RuntimeError(f"Row count mismatch: expected {expected_count}, got {actual_count}")
        conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("actual_unit_cell_design_rows", str(actual_count)))
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the unit-cell design-space SQLite database.")
    parser.add_argument("--db-path", default="data/unit_cell_design_space.sqlite", help="Output SQLite database path.")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing database.")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    build_database(db_path, overwrite=args.overwrite)
    print(f"Created {db_path}")
    print("Expected and actual unit_cell_designs rows: 442368")


if __name__ == "__main__":
    main()
