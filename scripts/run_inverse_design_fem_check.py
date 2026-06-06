#!/usr/bin/env python3
"""Run voxel-FEM confirmation for inverse-design top candidates."""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import subprocess
import sys
from pathlib import Path


TARGETS = [
    "kappa_eff_fem_w_mk",
    "r_e_fem_ohm",
    "alpha_eff_fem_v_k",
    "p_max_coeff_fem_w_k2",
    "p_area_coeff_fem_w_m2_k2",
]

PREDICTED_COLUMNS = {
    target: f"pred_{target}" for target in TARGETS
}

REQUIRED_FEM_DESIGN_FIELDS = [
    "r_out_m",
    "ratio_hole",
    "r_in_m",
    "t_ring_m",
    "h_total_m",
    "h_uc_m",
    "n_layer",
    "h_col_m",
    "column_type",
    "size1_m",
    "num_columns",
    "path_type",
    "connection_offset_units",
    "connection_offset_fraction",
    "connection_twist_rad",
    "connection_chord_m",
    "l_path_m",
    "placement_mode",
    "placement_json",
    "material_name",
    "carrier_type",
    "t_coating_m",
]


def read_top_candidates(path: Path, top_k: int) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Top-candidate CSV does not exist: {path}")
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No rows found in {path}")
    required = {"case_id", "score"} | set(PREDICTED_COLUMNS.values())
    missing = sorted(required - set(rows[0]))
    if missing:
        raise SystemExit(f"Top-candidate CSV is missing expected columns: {missing}")
    return rows[:top_k]


def read_intrinsic_rows(path: Path, case_ids: set[str]) -> dict[str, dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Intrinsic dataset does not exist: {path}")
    found: dict[str, dict[str, str]] = {}
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            case_id = row.get("case_id", "")
            if case_id in case_ids and case_id not in found:
                found[case_id] = row
                if len(found) == len(case_ids):
                    break
    missing = sorted(case_ids - set(found))
    if missing:
        raise SystemExit(f"Could not find {len(missing)} top case_ids in intrinsic dataset; first missing: {missing[:10]}")
    return found


def read_design_rows(db_path: Path, case_ids: set[str]) -> dict[str, dict[str, str]]:
    if not db_path.exists():
        raise SystemExit(f"Design database does not exist: {db_path}")
    case_id_list = sorted(case_ids, key=lambda value: int(value))
    placeholders = ",".join(["?"] * len(case_id_list))
    query = f"SELECT * FROM unit_cell_designs WHERE case_id IN ({placeholders})"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, case_id_list).fetchall()
    found = {str(row["case_id"]): {key: str(row[key]) if row[key] is not None else "" for key in row.keys()} for row in rows}
    missing = sorted(case_ids - set(found))
    if missing:
        raise SystemExit(f"Could not find {len(missing)} top case_ids in design database; first missing: {missing[:10]}")
    return found


def merge_design_and_intrinsic_rows(
    design_by_case: dict[str, dict[str, str]],
    intrinsic_by_case: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for case_id, design_row in design_by_case.items():
        row = dict(design_row)
        row.update(intrinsic_by_case[case_id])
        missing = [field for field in REQUIRED_FEM_DESIGN_FIELDS if row.get(field, "") == ""]
        if missing:
            raise SystemExit(f"Case {case_id} is missing required FEM design fields after merge: {missing}")
        merged[case_id] = row
    return merged


def write_sampling_csv(
    path: Path,
    top_rows: list[dict[str, str]],
    intrinsic_by_case: dict[str, dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    extra_fields = [
        "fem_sample_id",
        "selection_priority",
        "selection_reason",
        "inverse_rank",
        "inverse_score",
    ] + [f"inverse_{column}" for column in PREDICTED_COLUMNS.values()]

    source_fields = list(next(iter(intrinsic_by_case.values())).keys())
    fieldnames = extra_fields + [field for field in source_fields if field not in extra_fields]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for index, top in enumerate(top_rows, start=1):
            case_id = top["case_id"]
            output = dict(intrinsic_by_case[case_id])
            output["fem_sample_id"] = f"INV{index:04d}"
            output["selection_priority"] = "A" if index <= 20 else "B"
            output["selection_reason"] = f"inverse_design_rank_{index}"
            output["inverse_rank"] = index
            output["inverse_score"] = top.get("score", "")
            for column in PREDICTED_COLUMNS.values():
                output[f"inverse_{column}"] = top.get(column, "")
            writer.writerow(output)


def run_command(command: list[str]) -> None:
    print()
    print("[RUN] " + " ".join(command))
    subprocess.run(command, check=True)


def finite_float(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def write_error_report(
    top_rows: list[dict[str, str]],
    fem_results_path: Path,
    output_path: Path,
    summary_path: Path,
) -> None:
    top_by_case = {row["case_id"]: row for row in top_rows}
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with fem_results_path.open("r", newline="", encoding="utf-8") as f:
        fem_rows = list(csv.DictReader(f))
    if not fem_rows:
        raise SystemExit(f"No FEM rows found in {fem_results_path}")

    fieldnames = [
        "inverse_rank",
        "case_id",
        "material_name",
        "carrier_type",
        "column_type",
        "path_type",
        "surrogate_score",
        "fem_status",
        "fem_valid",
        "fem_invalid_reason",
    ]
    for target in TARGETS:
        fieldnames.extend([f"surrogate_{target}", f"fem_{target}", f"abs_error_{target}", f"rel_error_{target}"])

    rows_out: list[dict[str, object]] = []
    usable_count = 0
    rel_errors: dict[str, list[float]] = {target: [] for target in TARGETS}

    for row in fem_rows:
        case_id = row.get("case_id", "")
        top = top_by_case.get(case_id, {})
        item: dict[str, object] = {
            "inverse_rank": top.get("inverse_rank", ""),
            "case_id": case_id,
            "material_name": row.get("material_name", ""),
            "carrier_type": row.get("carrier_type", ""),
            "column_type": row.get("column_type", ""),
            "path_type": row.get("path_type", ""),
            "surrogate_score": top.get("score", ""),
            "fem_status": row.get("fem_status", ""),
            "fem_valid": row.get("fem_valid", ""),
            "fem_invalid_reason": row.get("fem_invalid_reason", ""),
        }
        row_is_usable = row.get("fem_status") == "done" and row.get("fem_valid", "1") not in {"0", "false", "False"}
        if row_is_usable:
            usable_count += 1
        for target in TARGETS:
            surrogate = finite_float(top.get(PREDICTED_COLUMNS[target], ""))
            fem = finite_float(row.get(target, ""))
            item[f"surrogate_{target}"] = "" if surrogate is None else surrogate
            item[f"fem_{target}"] = "" if fem is None else fem
            if surrogate is not None and fem is not None and abs(fem) > 1e-30:
                abs_error = abs(surrogate - fem)
                rel_error = abs_error / abs(fem)
                item[f"abs_error_{target}"] = abs_error
                item[f"rel_error_{target}"] = rel_error
                rel_errors[target].append(rel_error)
            else:
                item[f"abs_error_{target}"] = ""
                item[f"rel_error_{target}"] = ""
        rows_out.append(item)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    with summary_path.open("w", encoding="utf-8") as f:
        f.write(f"fem_results: {fem_results_path}\n")
        f.write(f"comparison_rows: {len(rows_out)}\n")
        f.write(f"usable_fem_rows: {usable_count}\n")
        f.write(f"output: {output_path}\n")
        f.write("\nmean_relative_error:\n")
        for target in TARGETS:
            values = rel_errors[target]
            mean_rel = sum(values) / len(values) if values else float("nan")
            f.write(f"  {target}: {mean_rel}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Confirm inverse-design top candidates with voxel FEM.")
    parser.add_argument("--top-candidates", default="results/inverse_design/top_candidates.csv")
    parser.add_argument("--intrinsic-dataset", default="results/intrinsic_network_dataset.csv")
    parser.add_argument("--db-path", default="data/unit_cell_design_space.sqlite")
    parser.add_argument("--out-dir", default="results/inverse_design/fem_check_top50")
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--workers", type=int, default=25)
    parser.add_argument("--voxel-size-m", type=float, default=1.0e-4)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--skip-solve", action="store_true", help="Only prepare sampling/template/jobs; do not run FEM.")
    args = parser.parse_args()

    if args.top_k <= 0:
        raise SystemExit("--top-k must be positive")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")

    repo_scripts = Path(__file__).resolve().parent
    out_dir = Path(args.out_dir)
    sampling_path = out_dir / "inverse_fem_sampling.csv"
    jobs_dir = out_dir / "jobs"
    template_path = out_dir / "fem_results_template.csv"
    fem_results_path = out_dir / "fem_results.csv"
    audit_dir = out_dir / "audit"
    errors_path = out_dir / "surrogate_vs_fem_errors.csv"
    comparison_summary_path = out_dir / "surrogate_vs_fem_summary.txt"

    top_rows = read_top_candidates(Path(args.top_candidates), args.top_k)
    for index, row in enumerate(top_rows, start=1):
        row["inverse_rank"] = str(index)
    case_ids = {row["case_id"] for row in top_rows}
    intrinsic_by_case = read_intrinsic_rows(Path(args.intrinsic_dataset), case_ids)
    design_by_case = read_design_rows(Path(args.db_path), case_ids)
    merged_by_case = merge_design_and_intrinsic_rows(design_by_case, intrinsic_by_case)
    write_sampling_csv(sampling_path, top_rows, merged_by_case)
    print(f"Top candidates: {len(top_rows)}")
    print(f"Sampling CSV: {sampling_path}")

    run_command(
        [
            sys.executable,
            str(repo_scripts / "make_fem_results_template.py"),
            "--input",
            str(sampling_path),
            "--output",
            str(template_path),
        ]
    )
    run_command(
        [
            sys.executable,
            str(repo_scripts / "prepare_fem_jobs.py"),
            "--input",
            str(sampling_path),
            "--out-dir",
            str(jobs_dir),
            "--skip-stl",
        ]
    )
    if args.skip_solve:
        print("Skipped FEM solve.")
        return

    run_command(
        [
            sys.executable,
            str(repo_scripts / "run_auto_fem_validation.py"),
            "--jobs-dir",
            str(jobs_dir),
            "--template",
            str(template_path),
            "--output",
            str(fem_results_path),
            "--solver",
            "voxel",
            "--workers",
            str(args.workers),
            "--voxel-size-m",
            str(args.voxel_size_m),
            "--progress-every",
            str(args.progress_every),
        ]
    )
    run_command(
        [
            sys.executable,
            str(repo_scripts / "audit_fem_results.py"),
            "--input",
            str(fem_results_path),
            "--out-dir",
            str(audit_dir),
        ]
    )
    write_error_report(top_rows, fem_results_path, errors_path, comparison_summary_path)
    print()
    print(f"FEM results: {fem_results_path}")
    print(f"Audit summary: {audit_dir / 'summary.txt'}")
    print(f"Surrogate-vs-FEM errors: {errors_path}")
    print(f"Surrogate-vs-FEM summary: {comparison_summary_path}")


if __name__ == "__main__":
    main()
