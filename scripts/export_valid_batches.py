#!/usr/bin/env python3
"""Export valid unit-cell designs into worker-friendly CSV batches."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


DEFAULT_COLUMNS = [
    "case_id",
    "r_out_m",
    "ratio_hole",
    "r_in_m",
    "t_ring_m",
    "h_total_m",
    "h_uc_m",
    "n_layer",
    "h_col_m",
    "a_uc_m2",
    "v_uc_m3",
    "column_type",
    "size1_m",
    "num_columns",
    "path_type",
    "connection_offset_units",
    "carrier_type",
    "material_name",
    "seebeck_v_k",
    "t_coating_m",
    "v_scaffold_m3",
    "a_surface_uc_m2",
    "f_scaffold",
    "f_coating",
    "porosity",
    "kappa_uc_est_w_mk",
    "r_uc_est_ohm",
]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def export_batch(
    conn: sqlite3.Connection,
    output_path: Path,
    columns: list[str],
    workers: int,
    worker_id: int,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    select_cols = ", ".join(columns)
    query = f"""
        SELECT {select_cols}
        FROM unit_cell_designs
        WHERE geometry_valid = 1
          AND case_id % ? = ?
        ORDER BY case_id
    """
    count = 0
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in conn.execute(query, (workers, worker_id)):
            writer.writerow(row)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Export valid design rows into CSV batches.")
    parser.add_argument("--db-path", default="data/unit_cell_design_space.sqlite", help="Input SQLite database.")
    parser.add_argument("--out-dir", default="data/batches", help="Output directory for batch CSV files.")
    parser.add_argument("--workers", type=int, default=8, help="Number of batch files to create.")
    parser.add_argument(
        "--all-columns",
        action="store_true",
        help="Export every column instead of the compact ML/simulation starter subset.",
    )
    args = parser.parse_args()

    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")

    db_path = Path(args.db_path)
    if not db_path.exists():
        raise SystemExit(f"Database does not exist: {db_path}")

    with sqlite3.connect(db_path) as conn:
        available_columns = table_columns(conn, "unit_cell_designs")
        columns = available_columns if args.all_columns else DEFAULT_COLUMNS
        missing = sorted(set(columns) - set(available_columns))
        if missing:
            raise RuntimeError(f"Database is missing expected columns: {missing}")

        total = conn.execute(
            "SELECT COUNT(*) FROM unit_cell_designs WHERE geometry_valid = 1"
        ).fetchone()[0]

        exported = 0
        out_dir = Path(args.out_dir)
        for worker_id in range(args.workers):
            output_path = out_dir / f"valid_cases_worker_{worker_id:03d}_of_{args.workers:03d}.csv"
            count = export_batch(conn, output_path, columns, args.workers, worker_id)
            exported += count
            print(f"{output_path}: {count}")

    print(f"Exported {exported} valid rows across {args.workers} batches.")
    if exported != total:
        raise RuntimeError(f"Exported row count mismatch: expected {total}, got {exported}")


if __name__ == "__main__":
    main()
