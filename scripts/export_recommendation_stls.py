#!/usr/bin/env python3
"""Export STL files for final recommended design cases."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path

from generate_case_stls import make_case_mesh, write_ascii_stl


def read_recommendations(path: Path, top_k: int | None) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Recommendation CSV does not exist: {path}")
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No rows found in {path}")
    if "case_id" not in rows[0]:
        raise SystemExit(f"Recommendation CSV is missing case_id: {path}")
    return rows[:top_k] if top_k is not None else rows


def fetch_design_rows(db_path: Path, case_ids: list[str]) -> dict[str, dict[str, str]]:
    if not db_path.exists():
        raise SystemExit(f"Design database does not exist: {db_path}")
    unique_case_ids = []
    seen = set()
    for case_id in case_ids:
        if case_id not in seen:
            seen.add(case_id)
            unique_case_ids.append(case_id)
    placeholders = ",".join(["?"] * len(unique_case_ids))
    query = f"SELECT * FROM unit_cell_designs WHERE case_id IN ({placeholders})"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, unique_case_ids).fetchall()
    found = {str(row["case_id"]): {key: str(row[key]) if row[key] is not None else "" for key in row.keys()} for row in rows}
    missing = [case_id for case_id in unique_case_ids if case_id not in found]
    if missing:
        raise SystemExit(f"Missing {len(missing)} case_ids in design database; first missing: {missing[:10]}")
    return found


def write_design_rows(path: Path, recommendations: list[dict[str, str]], design_by_case: dict[str, dict[str, str]]) -> None:
    if not recommendations:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(next(iter(design_by_case.values())).keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in recommendations:
            writer.writerow(design_by_case[rec["case_id"]])


def safe_rank(rec: dict[str, str], index: int) -> int:
    for field in ("final_rank", "rank", "inverse_rank"):
        value = rec.get(field, "")
        if value:
            try:
                return int(float(value))
            except ValueError:
                pass
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description="Export STL files for final recommendation CSV rows.")
    parser.add_argument("--recommendations", default="results/design_advisor/sb2te3_p_pipe_static_device/final_recommendations.csv")
    parser.add_argument("--db-path", default="data/unit_cell_design_space.sqlite")
    parser.add_argument("--out-dir", default="results/design_advisor/sb2te3_p_pipe_static_device/stl")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--ring-segments", type=int, default=128)
    parser.add_argument(
        "--design-rows-output",
        default="",
        help="Optional CSV path for complete unit_cell_designs rows used to generate STL.",
    )
    args = parser.parse_args()

    if args.top_k is not None and args.top_k <= 0:
        raise SystemExit("--top-k must be positive")
    if args.ring_segments < 16:
        raise SystemExit("--ring-segments must be >= 16")

    recommendations = read_recommendations(Path(args.recommendations), args.top_k)
    case_ids = [row["case_id"] for row in recommendations]
    design_by_case = fetch_design_rows(Path(args.db_path), case_ids)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "description": "STL files exported for final recommended design cases.",
        "recommendations": args.recommendations,
        "db_path": args.db_path,
        "units": "mm",
        "ring_segments": args.ring_segments,
        "files": [],
    }

    for index, rec in enumerate(recommendations, start=1):
        case_id = rec["case_id"]
        rank = safe_rank(rec, index)
        design = design_by_case[case_id]
        triangles, metadata = make_case_mesh(design, ring_segments=args.ring_segments)
        file_name = f"rank_{rank:03d}_case_{case_id}.stl"
        stl_path = out_dir / file_name
        triangles_written = write_ascii_stl(stl_path, f"rank_{rank:03d}_case_{case_id}", triangles)
        entry = {
            "rank": rank,
            "case_id": case_id,
            "file": file_name,
            "triangles": triangles_written,
            "score": rec.get("score", ""),
            "score_target": rec.get("score_target", ""),
            "material_name": rec.get("material_name", design.get("material_name", "")),
            "carrier_type": rec.get("carrier_type", design.get("carrier_type", "")),
            "column_type": rec.get("column_type", design.get("column_type", "")),
            "path_type": rec.get("path_type", design.get("path_type", "")),
            "metadata": metadata,
        }
        manifest["files"].append(entry)
        print(f"{stl_path}  triangles={triangles_written}")

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")

    if args.design_rows_output:
        write_design_rows(Path(args.design_rows_output), recommendations, design_by_case)
        print(f"Design rows: {args.design_rows_output}")


if __name__ == "__main__":
    main()
