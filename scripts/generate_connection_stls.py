#!/usr/bin/env python3
"""Generate six STL unit-cell examples for the connection path types.

The STL coordinates are in millimetres. These files are intended as first-pass
physical-shape previews for the six connection centreline families:

    straight, single_kink, arc_curve, sine_wave, helix_winding, bezier_curve

The preview uses one fixed unit-cell setting and only changes the connection
path. The upper ring is included for visual clarity; in the database convention
it is the shared ring of the neighbouring layer.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable

import numpy as np


OUT_DIR = Path("assets/stl_connection_modes")

# Geometry is in mm for STL output.
R_OUT = 2.0
R_IN = 1.0
T_RING = 0.2
H_UC = 5.0
N_COLUMNS = 5
CONNECTION_OFFSET_UNITS = 1
COLUMN_DIAMETER = 0.3
COLUMN_RADIUS = COLUMN_DIAMETER / 2.0
R_PLACE = R_IN + 0.75 * (R_OUT - R_IN)

RING_SEGMENTS = 128
TUBE_SECTIONS = 20


def unit(v: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(v))
    if norm < 1e-12:
        raise ValueError("Cannot normalise a near-zero vector.")
    return v / norm


def safe_unit(v: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(v))
    if norm < 1e-12:
        return unit(fallback)
    return v / norm


def triangle_normal(triangle: tuple[np.ndarray, np.ndarray, np.ndarray]) -> np.ndarray:
    a, b, c = triangle
    n = np.cross(b - a, c - a)
    norm = float(np.linalg.norm(n))
    if norm < 1e-12:
        return np.array([0.0, 0.0, 0.0])
    return n / norm


def write_ascii_stl(path: Path, name: str, triangles: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> None:
    with path.open("w", encoding="ascii") as f:
        f.write(f"solid {name}\n")
        for tri in triangles:
            n = triangle_normal(tri)
            if float(np.linalg.norm(n)) < 1e-12:
                continue
            f.write(f"  facet normal {n[0]:.8e} {n[1]:.8e} {n[2]:.8e}\n")
            f.write("    outer loop\n")
            for vertex in tri:
                f.write(f"      vertex {vertex[0]:.8e} {vertex[1]:.8e} {vertex[2]:.8e}\n")
            f.write("    endloop\n")
            f.write("  endfacet\n")
        f.write(f"endsolid {name}\n")


def ring_mesh(z0: float, z1: float, r_out: float, r_in: float, n: int) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    tris: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for i in range(n):
        a0 = 2.0 * math.pi * i / n
        a1 = 2.0 * math.pi * (i + 1) / n
        co0, so0 = math.cos(a0), math.sin(a0)
        co1, so1 = math.cos(a1), math.sin(a1)

        ob0 = np.array([r_out * co0, r_out * so0, z0])
        ob1 = np.array([r_out * co1, r_out * so1, z0])
        ot0 = np.array([r_out * co0, r_out * so0, z1])
        ot1 = np.array([r_out * co1, r_out * so1, z1])

        ib0 = np.array([r_in * co0, r_in * so0, z0])
        ib1 = np.array([r_in * co1, r_in * so1, z0])
        it0 = np.array([r_in * co0, r_in * so0, z1])
        it1 = np.array([r_in * co1, r_in * so1, z1])

        # Top and bottom annular faces.
        tris.extend([(ot0, ot1, it1), (ot0, it1, it0)])
        tris.extend([(ob0, ib1, ob1), (ob0, ib0, ib1)])

        # Outer and inner cylindrical walls.
        tris.extend([(ob0, ot0, ot1), (ob0, ot1, ob1)])
        tris.extend([(ib0, it1, it0), (ib0, ib1, it1)])
    return tris


def tube_mesh(
    points: np.ndarray,
    radius: float,
    sections: int,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    if len(points) < 2:
        raise ValueError("Tube requires at least two centreline points.")

    tangents: list[np.ndarray] = []
    for i in range(len(points)):
        if i == 0:
            tangent = points[1] - points[0]
        elif i == len(points) - 1:
            tangent = points[-1] - points[-2]
        else:
            tangent = points[i + 1] - points[i - 1]
        tangents.append(unit(tangent))

    frames: list[tuple[np.ndarray, np.ndarray]] = []
    t0 = tangents[0]
    normal = np.cross(t0, np.array([0.0, 0.0, 1.0]))
    if float(np.linalg.norm(normal)) < 1e-12:
        normal = np.cross(t0, np.array([1.0, 0.0, 0.0]))
    normal = unit(normal)
    binormal = unit(np.cross(t0, normal))
    frames.append((normal, binormal))

    for tangent in tangents[1:]:
        normal = normal - np.dot(normal, tangent) * tangent
        normal = safe_unit(normal, np.cross(tangent, np.array([0.0, 0.0, 1.0])))
        binormal = unit(np.cross(tangent, normal))
        frames.append((normal, binormal))

    rings: list[list[np.ndarray]] = []
    for point, (normal, binormal) in zip(points, frames):
        ring: list[np.ndarray] = []
        for j in range(sections):
            theta = 2.0 * math.pi * j / sections
            vertex = point + radius * (math.cos(theta) * normal + math.sin(theta) * binormal)
            ring.append(vertex)
        rings.append(ring)

    tris: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for i in range(len(rings) - 1):
        for j in range(sections):
            jn = (j + 1) % sections
            a = rings[i][j]
            b = rings[i + 1][j]
            c = rings[i + 1][jn]
            d = rings[i][jn]
            tris.extend([(a, b, c), (a, c, d)])

    # End caps.
    start = points[0]
    end = points[-1]
    for j in range(sections):
        jn = (j + 1) % sections
        tris.append((start, rings[0][jn], rings[0][j]))
        tris.append((end, rings[-1][j], rings[-1][jn]))

    return tris


def lerp(p0: np.ndarray, p1: np.ndarray, t: np.ndarray) -> np.ndarray:
    return (1.0 - t)[:, None] * p0 + t[:, None] * p1


def radial_direction(p0: np.ndarray, p1: np.ndarray) -> np.ndarray:
    mid_xy = 0.5 * (p0[:2] + p1[:2])
    return safe_unit(np.array([mid_xy[0], mid_xy[1], 0.0]), np.array([1.0, 0.0, 0.0]))


def lateral_direction(p0: np.ndarray, p1: np.ndarray) -> np.ndarray:
    chord_xy = p1[:2] - p0[:2]
    return safe_unit(np.array([-chord_xy[1], chord_xy[0], 0.0]), radial_direction(p0, p1))


def sample_straight(p0: np.ndarray, p1: np.ndarray, n: int = 72) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n)
    return lerp(p0, p1, t)


def sample_single_kink(p0: np.ndarray, p1: np.ndarray, n: int = 72) -> np.ndarray:
    bump = 0.42 * radial_direction(p0, p1)
    mid = 0.5 * (p0 + p1) + bump
    n1 = n // 2
    a = lerp(p0, mid, np.linspace(0.0, 1.0, n1, endpoint=False))
    b = lerp(mid, p1, np.linspace(0.0, 1.0, n - n1))
    return np.vstack([a, b])


def sample_arc_curve(p0: np.ndarray, p1: np.ndarray, n: int = 96) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n)
    control = 0.5 * (p0 + p1) + 0.55 * radial_direction(p0, p1)
    return ((1.0 - t) ** 2)[:, None] * p0 + (2.0 * (1.0 - t) * t)[:, None] * control + (t**2)[:, None] * p1


def sample_sine_wave(p0: np.ndarray, p1: np.ndarray, n: int = 120) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n)
    base = lerp(p0, p1, t)
    lateral = lateral_direction(p0, p1)
    outward = radial_direction(p0, p1)
    offset = (
        0.23 * np.sin(2.0 * math.pi * t)[:, None] * lateral
        + 0.10 * np.sin(4.0 * math.pi * t)[:, None] * outward
    )
    return base + offset


def sample_helix_winding(p0: np.ndarray, p1: np.ndarray, n: int = 144) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n)
    axis = p1 - p0
    e_axis = unit(axis)
    u = np.cross(e_axis, np.array([0.0, 0.0, 1.0]))
    if float(np.linalg.norm(u)) < 1e-12:
        u = np.cross(e_axis, np.array([1.0, 0.0, 0.0]))
    u = unit(u)
    v = unit(np.cross(e_axis, u))
    base = lerp(p0, p1, t)
    envelope = np.sin(math.pi * t)
    phase = 2.0 * math.pi * 1.25 * t
    offset = 0.20 * envelope[:, None] * (np.cos(phase)[:, None] * u + np.sin(phase)[:, None] * v)
    return base + offset


def sample_bezier_curve(p0: np.ndarray, p1: np.ndarray, n: int = 120) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n)
    d = p1 - p0
    outward = radial_direction(p0, p1)
    lateral = lateral_direction(p0, p1)
    c1 = p0 + 0.28 * d + 0.50 * outward + 0.22 * lateral
    c2 = p0 + 0.72 * d - 0.35 * outward - 0.24 * lateral
    return (
        ((1.0 - t) ** 3)[:, None] * p0
        + (3.0 * ((1.0 - t) ** 2) * t)[:, None] * c1
        + (3.0 * (1.0 - t) * (t**2))[:, None] * c2
        + (t**3)[:, None] * p1
    )


PATH_SAMPLERS: list[tuple[str, Callable[[np.ndarray, np.ndarray], np.ndarray]]] = [
    ("straight", sample_straight),
    ("single_kink", sample_single_kink),
    ("arc_curve", sample_arc_curve),
    ("sine_wave", sample_sine_wave),
    ("helix_winding", sample_helix_winding),
    ("bezier_curve", sample_bezier_curve),
]


def connection_endpoints() -> list[tuple[np.ndarray, np.ndarray]]:
    endpoints: list[tuple[np.ndarray, np.ndarray]] = []
    offset_angle = 2.0 * math.pi * CONNECTION_OFFSET_UNITS / N_COLUMNS
    for i in range(N_COLUMNS):
        bottom_theta = 2.0 * math.pi * i / N_COLUMNS
        top_theta = bottom_theta + offset_angle
        p0 = np.array([R_PLACE * math.cos(bottom_theta), R_PLACE * math.sin(bottom_theta), T_RING])
        p1 = np.array([R_PLACE * math.cos(top_theta), R_PLACE * math.sin(top_theta), H_UC])
        endpoints.append((p0, p1))
    return endpoints


def make_unit_cell(path_name: str, sampler: Callable[[np.ndarray, np.ndarray], np.ndarray]) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    tris: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []

    # Bottom ring: z = 0..T_RING. Top ring is the shared ring of the next layer:
    # z = H_UC..H_UC+T_RING, so the column height follows H_UC - T_RING.
    tris.extend(ring_mesh(0.0, T_RING, R_OUT, R_IN, RING_SEGMENTS))
    tris.extend(ring_mesh(H_UC, H_UC + T_RING, R_OUT, R_IN, RING_SEGMENTS))

    for p0, p1 in connection_endpoints():
        points = sampler(p0, p1)
        tris.extend(tube_mesh(points, COLUMN_RADIUS, TUBE_SECTIONS))

    return tris


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "units": "mm",
        "description": "Six path-type STL previews for one ring-column thermoelectric-coating unit cell.",
        "shared_ring_note": "The upper ring is shown for visual clarity and is treated as the shared ring of the neighbouring layer.",
        "defaults": {
            "R_out_mm": R_OUT,
            "R_in_mm": R_IN,
            "t_ring_mm": T_RING,
            "H_uc_mm": H_UC,
            "h_col_mm": H_UC - T_RING,
            "num_columns": N_COLUMNS,
            "connection_offset_units": CONNECTION_OFFSET_UNITS,
            "column_diameter_mm": COLUMN_DIAMETER,
            "r_place_mm": R_PLACE,
        },
        "files": [],
    }

    for index, (path_name, sampler) in enumerate(PATH_SAMPLERS, start=1):
        stl_name = f"connection_{index:02d}_{path_name}.stl"
        stl_path = OUT_DIR / stl_name
        triangles = make_unit_cell(path_name, sampler)
        write_ascii_stl(stl_path, path_name, triangles)
        manifest["files"].append({"path_type": path_name, "file": stl_name, "triangles": len(triangles)})
        print(f"{stl_path}  triangles={len(triangles)}")

    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{manifest_path}")


if __name__ == "__main__":
    main()
