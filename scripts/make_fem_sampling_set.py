#!/usr/bin/env python3
"""Select a high-fidelity FEM sampling set from intrinsic network labels."""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
from collections import defaultdict
from pathlib import Path


DIVERSITY_FIELDS = [
    "material_name",
    "t_ring_m",
    "ratio_hole",
    "h_uc_m",
    "column_type",
    "size1_m",
    "num_columns",
    "path_type",
    "connection_offset_units",
    "t_coating_m",
]

INTRINSIC_LABEL_FIELDS = [
    "kappa_eff_network_w_mk",
    "r_e_network_ohm",
    "r_coating_network_ohm",
    "alpha_device_v_k",
    "p_max_coeff_w_k2",
    "p_area_coeff_w_m2_k2",
    "baseline_kappa_uc_est_w_mk",
    "baseline_r_uc_est_ohm",
]


def parse_float(row: dict[str, str], key: str) -> float:
    return float(row[key])


def read_intrinsic_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = [row for row in csv.DictReader(f) if row.get("result_valid") == "1"]
    if not rows:
        raise SystemExit(f"No valid intrinsic rows found in {path}")
    return rows


def parse_name_set(text: str) -> set[str]:
    return {item.strip() for item in text.split(",") if item.strip()}


def apply_design_filters(
    rows: list[dict[str, str]],
    exclude_path_types: set[str],
    max_num_columns_for_path: dict[str, int],
) -> list[dict[str, str]]:
    filtered = []
    for row in rows:
        path_type = row.get("path_type", "")
        if path_type in exclude_path_types:
            continue
        max_columns = max_num_columns_for_path.get(path_type)
        if max_columns is not None and int(float(row.get("num_columns", "0"))) > max_columns:
            continue
        filtered.append(row)
    return filtered


def add_selected(
    selected: dict[str, dict[str, str]],
    row: dict[str, str],
    priority: str,
    reason: str,
) -> bool:
    case_id = row["case_id"]
    if case_id in selected:
        existing = selected[case_id]
        reasons = set(existing["selection_reason"].split(";"))
        if reason not in reasons:
            existing["selection_reason"] += f";{reason}"
        return False
    selected[case_id] = {
        "case_id": case_id,
        "selection_priority": priority,
        "selection_reason": reason,
    }
    for field in INTRINSIC_LABEL_FIELDS:
        selected[case_id][field] = row.get(field, "")
    return True


def select_top_performance(rows: list[dict[str, str]], selected: dict[str, dict[str, str]], count: int) -> None:
    ranked = sorted(rows, key=lambda row: parse_float(row, "p_area_coeff_w_m2_k2"), reverse=True)
    added = 0
    for row in ranked:
        if add_selected(selected, row, "A", "top_p_area_coeff"):
            added += 1
        if added >= count:
            return


def select_diverse_representatives(rows: list[dict[str, str]], selected: dict[str, dict[str, str]], count: int) -> None:
    groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = tuple(row[field] for field in DIVERSITY_FIELDS)
        groups[key].append(row)

    representatives: list[dict[str, str]] = []
    for group_rows in groups.values():
        representatives.append(
            max(group_rows, key=lambda row: parse_float(row, "p_area_coeff_w_m2_k2"))
        )
    representatives.sort(
        key=lambda row: (
            row["material_name"],
            float(row["ratio_hole"]),
            float(row["h_uc_m"]),
            row["column_type"],
            float(row["size1_m"]),
            int(float(row["num_columns"])),
            row["path_type"],
            int(float(row["connection_offset_units"])),
            float(row["t_coating_m"]),
        )
    )

    added = 0
    for row in representatives:
        if add_selected(selected, row, "B", "diversity_representative"):
            added += 1
        if added >= count:
            return


def select_boundary_cases(rows: list[dict[str, str]], selected: dict[str, dict[str, str]], count: int) -> None:
    ranked_lists = [
        ("min_kappa", sorted(rows, key=lambda row: parse_float(row, "kappa_eff_network_w_mk"))),
        ("max_kappa", sorted(rows, key=lambda row: parse_float(row, "kappa_eff_network_w_mk"), reverse=True)),
        ("min_r_e", sorted(rows, key=lambda row: parse_float(row, "r_e_network_ohm"))),
        ("max_r_e", sorted(rows, key=lambda row: parse_float(row, "r_e_network_ohm"), reverse=True)),
        ("min_p_area_coeff", sorted(rows, key=lambda row: parse_float(row, "p_area_coeff_w_m2_k2"))),
        ("max_p_area_coeff", sorted(rows, key=lambda row: parse_float(row, "p_area_coeff_w_m2_k2"), reverse=True)),
    ]
    indices = {name: 0 for name, _rows in ranked_lists}
    added = 0
    while added < count:
        progressed = False
        for name, ranked in ranked_lists:
            while indices[name] < len(ranked):
                row = ranked[indices[name]]
                indices[name] += 1
                if add_selected(selected, row, "C", name):
                    added += 1
                    progressed = True
                    break
            if added >= count:
                return
        if not progressed:
            return


def diversity_sort_key(row: dict[str, str]) -> tuple[object, ...]:
    return (
        row["material_name"],
        float(row["ratio_hole"]),
        float(row["h_uc_m"]),
        row["column_type"],
        float(row["size1_m"]),
        int(float(row["num_columns"])),
        row["path_type"],
        int(float(row["connection_offset_units"])),
        float(row["t_coating_m"]),
        int(float(row["case_id"])),
    )


def fill_by_performance(
    rows: list[dict[str, str]],
    selected: dict[str, dict[str, str]],
    target_count: int,
) -> None:
    ranked = sorted(rows, key=lambda row: parse_float(row, "p_area_coeff_w_m2_k2"), reverse=True)
    for row in ranked:
        if len(selected) >= target_count:
            return
        add_selected(selected, row, "D", "target_count_fill")


def fill_by_stratified_performance(
    rows: list[dict[str, str]],
    selected: dict[str, dict[str, str]],
    target_count: int,
    bins: int,
) -> None:
    ranked = sorted(rows, key=lambda row: parse_float(row, "p_area_coeff_w_m2_k2"), reverse=True)
    bins = max(1, min(bins, len(ranked)))
    bucket_size = math.ceil(len(ranked) / bins)
    buckets = [
        sorted(ranked[index : index + bucket_size], key=diversity_sort_key)
        for index in range(0, len(ranked), bucket_size)
    ]
    bucket_indices = [0] * len(buckets)
    while len(selected) < target_count:
        progressed = False
        for bucket_index, bucket in enumerate(buckets):
            while bucket_indices[bucket_index] < len(bucket):
                row = bucket[bucket_indices[bucket_index]]
                bucket_indices[bucket_index] += 1
                if add_selected(selected, row, "D", "stratified_target_count_fill"):
                    progressed = True
                    break
            if len(selected) >= target_count:
                return
        if not progressed:
            return


def fill_to_target(
    rows: list[dict[str, str]],
    selected: dict[str, dict[str, str]],
    target_count: int,
    strategy: str,
    performance_fill_fraction: float,
    stratified_bins: int,
) -> None:
    if strategy == "performance":
        fill_by_performance(rows, selected, target_count)
    elif strategy == "stratified":
        fill_by_stratified_performance(rows, selected, target_count, stratified_bins)
    elif strategy == "hybrid":
        remaining = max(0, target_count - len(selected))
        performance_target = len(selected) + round(remaining * performance_fill_fraction)
        fill_by_performance(rows, selected, performance_target)
        fill_by_stratified_performance(rows, selected, target_count, stratified_bins)
    else:
        raise SystemExit(f"Unknown fill strategy: {strategy}")


def fetch_design_rows(db_path: Path, case_ids: list[str]) -> dict[str, dict[str, str]]:
    placeholders = ",".join(["?"] * len(case_ids))
    query = f"SELECT * FROM unit_cell_designs WHERE case_id IN ({placeholders}) ORDER BY case_id"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, case_ids).fetchall()
    return {str(row["case_id"]): {key: row[key] for key in row.keys()} for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a high-fidelity FEM sampling set.")
    parser.add_argument("--intrinsic-dataset", default="results/intrinsic_network_dataset.csv")
    parser.add_argument("--db-path", default="data/unit_cell_design_space.sqlite")
    parser.add_argument("--output", default="results/fem_sampling/fem_sampling_200.csv")
    parser.add_argument("--target-count", type=int, default=200)
    parser.add_argument("--top-count", type=int, default=50)
    parser.add_argument("--diversity-count", type=int, default=100)
    parser.add_argument("--boundary-count", type=int, default=50)
    parser.add_argument(
        "--fill-strategy",
        choices=["performance", "stratified", "hybrid"],
        default="performance",
        help="How to fill rows after top/diversity/boundary selections. Default preserves legacy behavior.",
    )
    parser.add_argument(
        "--performance-fill-fraction",
        type=float,
        default=0.5,
        help="For --fill-strategy hybrid, fraction of remaining rows filled by high predicted performance first.",
    )
    parser.add_argument("--stratified-bins", type=int, default=20)
    parser.add_argument(
        "--exclude-path-types",
        default="",
        help="Comma-separated path types to exclude from FEM sampling, e.g. helix_winding.",
    )
    parser.add_argument(
        "--max-columns-for-path",
        action="append",
        default=[],
        metavar="PATH:MAX_COLUMNS",
        help="Reject rows whose path type exceeds a column-count limit, e.g. helix_winding:10. Can be repeated.",
    )
    args = parser.parse_args()

    intrinsic_path = Path(args.intrinsic_dataset)
    db_path = Path(args.db_path)
    output_path = Path(args.output)
    if not intrinsic_path.exists():
        raise SystemExit(f"Intrinsic dataset does not exist: {intrinsic_path}")
    if not db_path.exists():
        raise SystemExit(f"Database does not exist: {db_path}")
    if args.performance_fill_fraction < 0.0 or args.performance_fill_fraction > 1.0:
        raise SystemExit("--performance-fill-fraction must be between 0 and 1")
    if args.stratified_bins < 1:
        raise SystemExit("--stratified-bins must be >= 1")

    rows = read_intrinsic_rows(intrinsic_path)
    rows_before_filter = len(rows)
    max_num_columns_for_path: dict[str, int] = {}
    for item in args.max_columns_for_path:
        if ":" not in item:
            raise SystemExit(f"--max-columns-for-path must be PATH:MAX_COLUMNS, got: {item}")
        path_type, max_columns_text = item.split(":", 1)
        max_num_columns_for_path[path_type.strip()] = int(max_columns_text)
    rows = apply_design_filters(rows, parse_name_set(args.exclude_path_types), max_num_columns_for_path)
    if not rows:
        raise SystemExit("No intrinsic rows remain after design filters.")
    selected: dict[str, dict[str, str]] = {}
    select_top_performance(rows, selected, args.top_count)
    select_diverse_representatives(rows, selected, args.diversity_count)
    select_boundary_cases(rows, selected, args.boundary_count)
    fill_to_target(
        rows,
        selected,
        args.target_count,
        args.fill_strategy,
        args.performance_fill_fraction,
        args.stratified_bins,
    )

    selected_items = list(selected.values())[: args.target_count]
    case_ids = [row["case_id"] for row in selected_items]
    design_rows = fetch_design_rows(db_path, case_ids)
    missing = sorted(set(case_ids) - set(design_rows))
    if missing:
        raise SystemExit(f"Missing design rows for case_ids: {missing[:10]}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rows = []
    for index, selected_row in enumerate(selected_items, start=1):
        case_id = selected_row["case_id"]
        combined = {
            "fem_sample_id": f"FEM{index:04d}",
            "selection_priority": selected_row["selection_priority"],
            "selection_reason": selected_row["selection_reason"],
        }
        for field in INTRINSIC_LABEL_FIELDS:
            combined[field] = selected_row.get(field, "")
        combined.update(design_rows[case_id])
        sample_rows.append(combined)

    fieldnames = list(sample_rows[0].keys())
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sample_rows)

    print(f"Intrinsic rows read: {rows_before_filter}")
    print(f"Rows after design filters: {len(rows)}")
    print(f"FEM sampling rows: {len(sample_rows)}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
