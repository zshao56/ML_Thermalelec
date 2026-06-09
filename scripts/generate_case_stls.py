#!/usr/bin/env python3
"""Generate STL previews for selected design cases.

The STL coordinates are in millimetres. The geometry follows the same shared
ring convention as the database:

* each ring occupies z = k * H_uc .. k * H_uc + t_ring;
* columns connect from the top of one ring to the bottom of the next ring;
* the final top ring is included for visual inspection.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


Vec3 = tuple[float, float, float]
Triangle = tuple[Vec3, Vec3, Vec3]

RING_SEGMENTS = 128
DEFAULT_PATH_SAMPLES = {
    "straight": 2,
    "single_kink": 18,
    "arc_curve": 32,
    "sine_wave": 48,
    "helix_winding": 64,
    "bezier_curve": 48,
}


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a: Vec3, scalar: float) -> Vec3:
    return (a[0] * scalar, a[1] * scalar, a[2] * scalar)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def norm(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec3, fallback: Vec3 = (1.0, 0.0, 0.0)) -> Vec3:
    length = norm(a)
    if length < 1e-12:
        fallback_length = norm(fallback)
        if fallback_length < 1e-12:
            raise ValueError("Cannot normalize a near-zero vector.")
        return mul(fallback, 1.0 / fallback_length)
    return mul(a, 1.0 / length)


def triangle_normal(triangle: Triangle) -> Vec3:
    a, b, c = triangle
    normal = cross(sub(b, a), sub(c, a))
    length = norm(normal)
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return mul(normal, 1.0 / length)


def write_ascii_stl(path: Path, name: str, triangles: list[Triangle]) -> int:
    written = 0
    with path.open("w", encoding="ascii") as f:
        f.write(f"solid {name}\n")
        for tri in triangles:
            n = triangle_normal(tri)
            if norm(n) < 1e-12:
                continue
            f.write(f"  facet normal {n[0]:.8e} {n[1]:.8e} {n[2]:.8e}\n")
            f.write("    outer loop\n")
            for vertex in tri:
                f.write(f"      vertex {vertex[0]:.8e} {vertex[1]:.8e} {vertex[2]:.8e}\n")
            f.write("    endloop\n")
            f.write("  endfacet\n")
            written += 1
        f.write(f"endsolid {name}\n")
    return written


def ring_mesh(z0: float, z1: float, r_out: float, r_in: float, n: int) -> list[Triangle]:
    tris: list[Triangle] = []
    for i in range(n):
        a0 = 2.0 * math.pi * i / n
        a1 = 2.0 * math.pi * (i + 1) / n
        co0, so0 = math.cos(a0), math.sin(a0)
        co1, so1 = math.cos(a1), math.sin(a1)

        ob0 = (r_out * co0, r_out * so0, z0)
        ob1 = (r_out * co1, r_out * so1, z0)
        ot0 = (r_out * co0, r_out * so0, z1)
        ot1 = (r_out * co1, r_out * so1, z1)

        ib0 = (r_in * co0, r_in * so0, z0)
        ib1 = (r_in * co1, r_in * so1, z0)
        it0 = (r_in * co0, r_in * so0, z1)
        it1 = (r_in * co1, r_in * so1, z1)

        tris.extend([(ot0, ot1, it1), (ot0, it1, it0)])
        tris.extend([(ob0, ib1, ob1), (ob0, ib0, ib1)])
        tris.extend([(ob0, ot0, ot1), (ob0, ot1, ob1)])
        if r_in > 1e-12:
            tris.extend([(ib0, it1, it0), (ib0, ib1, it1)])
    return tris


def lerp(p0: Vec3, p1: Vec3, t: float) -> Vec3:
    return add(mul(p0, 1.0 - t), mul(p1, t))


def radial_direction(p0: Vec3, p1: Vec3) -> Vec3:
    mid = (0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1]), 0.0)
    return unit(mid, (1.0, 0.0, 0.0))


def lateral_direction(p0: Vec3, p1: Vec3) -> Vec3:
    chord = sub(p1, p0)
    return unit((-chord[1], chord[0], 0.0), radial_direction(p0, p1))


def sample_path(
    path_type: str,
    p0: Vec3,
    p1: Vec3,
    amplitude_scale: float = 1.0,
    path_params: dict[str, object] | None = None,
) -> list[Vec3]:
    n = DEFAULT_PATH_SAMPLES[path_type]
    if path_type == "straight":
        return [p0, p1]

    points: list[Vec3] = []
    if path_type == "single_kink":
        bump = mul(radial_direction(p0, p1), 0.42 * amplitude_scale)
        mid = add(mul(add(p0, p1), 0.5), bump)
        for i in range(n):
            t = i / (n - 1)
            points.append(lerp(p0, mid, 2.0 * t) if t < 0.5 else lerp(mid, p1, 2.0 * (t - 0.5)))
        return points

    if path_type == "arc_curve":
        control = add(mul(add(p0, p1), 0.5), mul(radial_direction(p0, p1), 0.55 * amplitude_scale))
        for i in range(n):
            t = i / (n - 1)
            points.append(
                add(
                    add(mul(p0, (1.0 - t) ** 2), mul(control, 2.0 * (1.0 - t) * t)),
                    mul(p1, t * t),
                )
            )
        return points

    if path_type == "sine_wave":
        lateral = lateral_direction(p0, p1)
        outward = radial_direction(p0, p1)
        for i in range(n):
            t = i / (n - 1)
            base = lerp(p0, p1, t)
            offset = add(
                mul(lateral, 0.23 * amplitude_scale * math.sin(2.0 * math.pi * t)),
                mul(outward, 0.10 * amplitude_scale * math.sin(4.0 * math.pi * t)),
            )
            points.append(add(base, offset))
        return points

    if path_type == "helix_winding":
        axis = sub(p1, p0)
        axis_length = norm(axis)
        e_axis = unit(axis)
        u = unit(cross(e_axis, (0.0, 0.0, 1.0)), cross(e_axis, (1.0, 0.0, 0.0)))
        v = unit(cross(e_axis, u))
        params = path_params or {}
        radius_ratio = float(params.get("radius_ratio", 0.15))
        turns = float(params.get("turns", 1.0))
        helix_radius = axis_length * radius_ratio * amplitude_scale
        for i in range(n):
            t = i / (n - 1)
            base = lerp(p0, p1, t)
            envelope = math.sin(math.pi * t)
            phase = 2.0 * math.pi * turns * t
            offset = mul(add(mul(u, math.cos(phase)), mul(v, math.sin(phase))), helix_radius * envelope)
            points.append(add(base, offset))
        return points

    if path_type == "bezier_curve":
        d = sub(p1, p0)
        outward = radial_direction(p0, p1)
        lateral = lateral_direction(p0, p1)
        c1 = add(
            add(p0, mul(d, 0.28)),
            add(mul(outward, 0.50 * amplitude_scale), mul(lateral, 0.22 * amplitude_scale)),
        )
        c2 = add(
            add(p0, mul(d, 0.72)),
            add(mul(outward, -0.35 * amplitude_scale), mul(lateral, -0.24 * amplitude_scale)),
        )
        for i in range(n):
            t = i / (n - 1)
            points.append(
                add(
                    add(mul(p0, (1.0 - t) ** 3), mul(c1, 3.0 * (1.0 - t) ** 2 * t)),
                    add(mul(c2, 3.0 * (1.0 - t) * t * t), mul(p1, t**3)),
                )
            )
        return points

    raise ValueError(f"Unknown path_type: {path_type}")


def cross_section(column_type: str, size1: float) -> tuple[int, float]:
    if column_type == "circular_column":
        return 24, 0.5 * size1
    if column_type == "square_column":
        return 4, size1 / math.sqrt(2.0)
    if column_type == "pentagonal_column":
        return 5, 0.5 * size1
    if column_type == "hexagonal_column":
        return 6, 0.5 * size1
    raise ValueError(f"Unknown column_type: {column_type}")


def tube_mesh(points: list[Vec3], column_type: str, size1: float) -> list[Triangle]:
    sections, radius = cross_section(column_type, size1)
    tangents: list[Vec3] = []
    for i, point in enumerate(points):
        if i == 0:
            tangent = sub(points[1], point)
        elif i == len(points) - 1:
            tangent = sub(point, points[-2])
        else:
            tangent = sub(points[i + 1], points[i - 1])
        tangents.append(unit(tangent, (0.0, 0.0, 1.0)))

    normal = unit(cross(tangents[0], (0.0, 0.0, 1.0)), cross(tangents[0], (1.0, 0.0, 0.0)))
    rings: list[list[Vec3]] = []
    for point, tangent in zip(points, tangents):
        normal = sub(normal, mul(tangent, dot(normal, tangent)))
        normal = unit(normal, cross(tangent, (0.0, 0.0, 1.0)))
        binormal = unit(cross(tangent, normal))
        ring: list[Vec3] = []
        angle_shift = math.pi / 4.0 if sections == 4 else math.pi / 2.0
        for j in range(sections):
            theta = angle_shift + 2.0 * math.pi * j / sections
            ring.append(
                add(
                    point,
                    add(mul(normal, radius * math.cos(theta)), mul(binormal, radius * math.sin(theta))),
                )
            )
        rings.append(ring)

    tris: list[Triangle] = []
    for i in range(len(rings) - 1):
        for j in range(sections):
            jn = (j + 1) % sections
            a, b, c, d = rings[i][j], rings[i + 1][j], rings[i + 1][jn], rings[i][jn]
            tris.extend([(a, b, c), (a, c, d)])

    start, end = points[0], points[-1]
    for j in range(sections):
        jn = (j + 1) % sections
        tris.append((start, rings[0][jn], rings[0][j]))
        tris.append((end, rings[-1][j], rings[-1][jn]))
    return tris


def position_for_index(index: int, placement: dict[str, object], z: float, n_columns: int) -> Vec3:
    mode = placement["mode"]
    if mode == "single_ring":
        r = float(placement["r_place_m"]) * 1000.0
        theta = 2.0 * math.pi * (index % n_columns) / n_columns
        return (r * math.cos(theta), r * math.sin(theta), z)

    if mode == "double_ring":
        n_inner = int(placement["n_inner"])
        n_outer = int(placement["n_outer"])
        idx = index % n_columns
        if idx < n_inner:
            r = float(placement["r_inner_m"]) * 1000.0
            theta = 2.0 * math.pi * idx / n_inner
        else:
            r = float(placement["r_outer_m"]) * 1000.0
            outer_idx = idx - n_inner
            theta = 2.0 * math.pi * outer_idx / n_outer + float(placement["theta_offset_outer_rad"])
        return (r * math.cos(theta), r * math.sin(theta), z)

    raise ValueError(f"Unknown placement mode: {mode}")


def make_case_mesh(
    row: dict[str, str],
    ring_segments: int = RING_SEGMENTS,
    column_scale: float = 1.0,
    path_amplitude_scale: float = 1.0,
    layer_start: int | None = None,
    layer_count: int | None = None,
    include_rings: bool = True,
) -> tuple[list[Triangle], dict[str, object]]:
    case_id = row["case_id"]
    r_out = float(row["r_out_m"]) * 1000.0
    r_in = float(row["r_in_m"]) * 1000.0
    t_ring = float(row["t_ring_m"]) * 1000.0
    h_uc = float(row["h_uc_m"]) * 1000.0
    n_layer = int(row["n_layer"])
    n_columns = int(row["num_columns"])
    offset_units = int(row["connection_offset_units"])
    size1 = float(row["size1_m"]) * 1000.0
    visual_size1 = size1 * column_scale
    column_type = row["column_type"]
    path_type = row["path_type"]
    path_params = json.loads(row.get("path_params_json") or "{}")
    placement = json.loads(row["placement_json"])

    first_layer = 0 if layer_start is None else layer_start
    selected_layer_count = n_layer if layer_count is None else layer_count
    last_layer = first_layer + selected_layer_count
    if first_layer < 0 or first_layer >= n_layer:
        raise ValueError(f"layer_start must be in [0, {n_layer - 1}], got {first_layer}")
    if selected_layer_count < 1:
        raise ValueError("layer_count must be >= 1")
    if last_layer > n_layer:
        raise ValueError(f"layer_start + layer_count must be <= {n_layer}, got {last_layer}")

    triangles: list[Triangle] = []
    if include_rings:
        for layer_index in range(first_layer, last_layer + 1):
            z0 = layer_index * h_uc
            triangles.extend(ring_mesh(z0, z0 + t_ring, r_out, r_in, ring_segments))

    for layer_index in range(first_layer, last_layer):
        z_bottom = layer_index * h_uc + t_ring
        z_top = (layer_index + 1) * h_uc
        layer_shift = layer_index * offset_units
        for column_index in range(n_columns):
            bottom_index = column_index + layer_shift
            top_index = column_index + layer_shift + offset_units
            p0 = position_for_index(bottom_index, placement, z_bottom, n_columns)
            p1 = position_for_index(top_index, placement, z_top, n_columns)
            triangles.extend(
                tube_mesh(
                    sample_path(path_type, p0, p1, path_amplitude_scale, path_params),
                    column_type,
                    visual_size1,
                )
            )

    metadata = {
        "case_id": case_id,
        "units": "mm",
        "r_out_mm": r_out,
        "r_in_mm": r_in,
        "t_ring_mm": t_ring,
        "h_uc_mm": h_uc,
        "n_layer": n_layer,
        "h_total_nominal_mm": n_layer * h_uc,
        "top_ring_extends_to_mm": n_layer * h_uc + t_ring,
        "layer_start": first_layer,
        "layer_count": selected_layer_count,
        "exported_layer_range": [first_layer, last_layer - 1],
        "exported_ring_range": [first_layer, last_layer],
        "include_rings": include_rings,
        "column_type": column_type,
        "size1_mm": size1,
        "visual_size1_mm": visual_size1,
        "column_scale": column_scale,
        "path_amplitude_scale": path_amplitude_scale,
        "num_columns": n_columns,
        "path_type": path_type,
        "path_params": path_params,
        "connection_offset_units": offset_units,
        "placement_mode": placement["mode"],
        "ring_segments": ring_segments,
        "triangles_before_degenerate_filter": len(triangles),
    }
    return triangles, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate STL previews for design cases from a CSV file.")
    parser.add_argument("--input", default="results/network_validation/key_design_cases.csv", help="Input design CSV.")
    parser.add_argument("--out-dir", default="results/network_validation/stl_cases", help="Output STL directory.")
    parser.add_argument(
        "--column-scale",
        type=float,
        default=1.0,
        help="Scale polygon/tube diameter for visualization. 1.0 preserves database size.",
    )
    parser.add_argument(
        "--path-amplitude-scale",
        type=float,
        default=1.0,
        help="Scale decorative path curvature/helix amplitude. 1.0 preserves the default preview shape.",
    )
    parser.add_argument(
        "--layer-start",
        type=int,
        default=None,
        help="Export only columns starting from this layer index. Default exports all layers.",
    )
    parser.add_argument(
        "--layer-count",
        type=int,
        default=None,
        help="Number of connection layers to export. Use --layer-start 0 --layer-count 1 for one layer.",
    )
    parser.add_argument(
        "--omit-rings",
        action="store_true",
        help="Export only the connection columns/tubes. Useful for inspecting dense paths.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "description": "STL previews generated from unit_cell_designs rows.",
        "units": "mm",
        "shared_ring_note": "The final top ring is included and extends by t_ring above nominal H_total for visualization.",
        "files": [],
    }

    with input_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No design rows found in {input_path}")
    if args.column_scale <= 0.0:
        raise SystemExit("--column-scale must be positive.")
    if args.path_amplitude_scale < 0.0:
        raise SystemExit("--path-amplitude-scale must be non-negative.")
    if args.layer_count is not None and args.layer_count < 1:
        raise SystemExit("--layer-count must be >= 1.")

    for row in rows:
        case_id = row["case_id"]
        triangles, metadata = make_case_mesh(
            row,
            column_scale=args.column_scale,
            path_amplitude_scale=args.path_amplitude_scale,
            layer_start=args.layer_start,
            layer_count=args.layer_count,
            include_rings=not args.omit_rings,
        )
        stl_path = out_dir / f"case_{case_id}.stl"
        triangles_written = write_ascii_stl(stl_path, f"case_{case_id}", triangles)
        metadata["file"] = stl_path.name
        metadata["triangles"] = triangles_written
        manifest["files"].append(metadata)
        print(f"{stl_path}  triangles={triangles_written}")

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{manifest_path}")


if __name__ == "__main__":
    main()
