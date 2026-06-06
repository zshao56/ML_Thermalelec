#!/usr/bin/env python3
"""One-command condition-based inverse-design advisor."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


OBJECTIVES = {
    "max_device_p_area": ("device_p_area_w_m2", "max"),
    "max_device_p_max": ("device_p_max_w", "max"),
    "max_p_area": ("p_area_coeff_fem_w_m2_k2", "max"),
    "max_p_max": ("p_max_coeff_fem_w_k2", "max"),
    "min_kappa": ("kappa_eff_fem_w_mk", "min"),
    "max_kappa": ("kappa_eff_fem_w_mk", "max"),
    "min_r_e": ("r_e_fem_ohm", "min"),
}

FILTER_ARGS = [
    "material",
    "carrier",
    "column_type",
    "path_type",
    "t_ring_m",
    "ratio_hole",
    "h_uc_m",
    "n_layer",
    "size1_m",
    "num_columns",
    "connection_offset_units",
    "t_coating_m",
    "min_kappa",
    "max_kappa",
    "min_r_e",
    "max_r_e",
    "min_p_max",
    "min_p_area",
    "alpha_sign",
]

APPLICATION_ARGS = [
    "boundary_type",
    "t_hot_k",
    "t_cold_k",
    "h_c_w_m2k",
    "q_hot_w_m2",
    "area_m2",
    "length_m",
]


def kebab(name: str) -> str:
    return "--" + name.replace("_", "-")


def slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", text.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "advisor_run"


def add_optional(command: list[str], option: str, value: object | None) -> None:
    if value is None or value == "":
        return
    if option == "--alpha-sign" and value == "any":
        return
    command.extend([option, str(value)])


def run_command(command: list[str]) -> None:
    print()
    print("[RUN] " + " ".join(command))
    subprocess.run(command, check=True)


def default_run_name(args: argparse.Namespace) -> str:
    parts = [args.objective]
    for name in ["material", "carrier", "column_type", "path_type"]:
        value = getattr(args, name)
        if value:
            parts.append(f"{name}_{value}")
    parts.append(datetime.now().strftime("%Y%m%d_%H%M%S"))
    return slug("__".join(parts))


def write_advisor_summary(
    path: Path,
    args: argparse.Namespace,
    score_target: str,
    direction: str,
    out_dir: Path,
    outputs: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conditions = {name: getattr(args, name) for name in FILTER_ARGS if getattr(args, name) not in (None, "", "any")}
    payload = {
        "objective": args.objective,
        "score_target": score_target,
        "direction": direction,
        "conditions": conditions,
        "application_conditions": {
            name: getattr(args, name)
            for name in APPLICATION_ARGS
            if getattr(args, name) is not None
        },
        "surrogate_top_k": args.top_k,
        "fem_check": args.fem_check,
        "recommendations": args.recommendations,
        "out_dir": str(out_dir),
        "outputs": outputs,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run condition-based inverse design with optional FEM confirmation.")
    parser.add_argument("--objective", choices=sorted(OBJECTIVES), default="max_device_p_area")
    parser.add_argument("--model", default="results/fem_surrogate_80000_voxel100um_sklearn/fem_surrogate_sklearn.joblib")
    parser.add_argument("--candidates", default="results/intrinsic_network_dataset.csv")
    parser.add_argument("--db-path", default="data/unit_cell_design_space.sqlite")
    parser.add_argument("--out-root", default="results/design_advisor")
    parser.add_argument("--run-name", default="")
    parser.add_argument("--top-k", type=int, default=300, help="Surrogate-ranked candidates to keep before FEM confirmation.")
    parser.add_argument("--fem-check", type=int, default=50, help="Number of surrogate candidates to confirm with voxel FEM. Use 0 to skip.")
    parser.add_argument("--recommendations", type=int, default=20, help="Final recommendations to output.")
    parser.add_argument("--workers", type=int, default=25)
    parser.add_argument("--voxel-size-m", type=float, default=1.0e-4)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--chunk-size", type=int, default=20000)
    parser.add_argument(
        "--boundary-type",
        choices=["fixed_hot_surface_cold_convection", "fixed_q_cold_convection"],
        default="fixed_hot_surface_cold_convection",
    )
    parser.add_argument("--t-hot-k", type=float, default=393.15)
    parser.add_argument("--t-cold-k", type=float, default=293.15)
    parser.add_argument("--h-c-w-m2k", type=float, default=10.0)
    parser.add_argument("--q-hot-w-m2", type=float, default=None)
    parser.add_argument("--area-m2", type=float, default=None)
    parser.add_argument("--length-m", type=float, default=None)
    parser.add_argument("--material", default="")
    parser.add_argument("--carrier", choices=["", "p", "n"], default="")
    parser.add_argument("--column-type", default="")
    parser.add_argument("--path-type", default="")
    parser.add_argument("--t-ring-m", type=float, default=None)
    parser.add_argument("--ratio-hole", type=float, default=None)
    parser.add_argument("--h-uc-m", type=float, default=None)
    parser.add_argument("--n-layer", type=float, default=None)
    parser.add_argument("--size1-m", type=float, default=None)
    parser.add_argument("--num-columns", type=float, default=None)
    parser.add_argument("--connection-offset-units", type=float, default=None)
    parser.add_argument("--t-coating-m", type=float, default=None)
    parser.add_argument("--min-kappa", type=float, default=None)
    parser.add_argument("--max-kappa", type=float, default=None)
    parser.add_argument("--min-r-e", type=float, default=None)
    parser.add_argument("--max-r-e", type=float, default=None)
    parser.add_argument("--min-p-max", type=float, default=None)
    parser.add_argument("--min-p-area", type=float, default=None)
    parser.add_argument("--alpha-sign", choices=["any", "positive", "negative"], default="any")
    args = parser.parse_args()

    if args.top_k <= 0:
        raise SystemExit("--top-k must be positive")
    if args.fem_check < 0:
        raise SystemExit("--fem-check must be >= 0")
    if args.fem_check > args.top_k:
        raise SystemExit("--fem-check cannot exceed --top-k")
    if args.recommendations <= 0:
        raise SystemExit("--recommendations must be positive")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.h_c_w_m2k <= 0.0:
        raise SystemExit("--h-c-w-m2k must be positive")
    if args.boundary_type == "fixed_q_cold_convection" and args.q_hot_w_m2 is None:
        raise SystemExit("--q-hot-w-m2 is required when --boundary-type fixed_q_cold_convection")

    score_target, direction = OBJECTIVES[args.objective]
    run_name = slug(args.run_name) if args.run_name else default_run_name(args)
    out_dir = Path(args.out_root) / run_name
    surrogate_csv = out_dir / "surrogate_top_candidates.csv"
    surrogate_summary = out_dir / "surrogate_summary.txt"
    advisor_summary = out_dir / "advisor_summary.json"

    script_dir = Path(__file__).resolve().parent
    search_cmd = [
        sys.executable,
        str(script_dir / "run_inverse_design_search.py"),
        "--model",
        args.model,
        "--candidates",
        args.candidates,
        "--output",
        str(surrogate_csv),
        "--summary",
        str(surrogate_summary),
        "--score-target",
        score_target,
        "--direction",
        direction,
        "--top-k",
        str(args.top_k),
        "--chunk-size",
        str(args.chunk_size),
    ]
    for name in FILTER_ARGS:
        add_optional(search_cmd, kebab(name), getattr(args, name))
    for name in APPLICATION_ARGS:
        add_optional(search_cmd, kebab(name), getattr(args, name))
    run_command(search_cmd)

    outputs = {
        "surrogate_top_candidates": str(surrogate_csv),
        "surrogate_summary": str(surrogate_summary),
    }

    if args.fem_check > 0:
        fem_dir = out_dir / f"fem_check_top{args.fem_check}"
        final_csv = out_dir / "final_recommendations.csv"
        final_summary = out_dir / "final_summary.txt"
        fem_check_cmd = [
            sys.executable,
            str(script_dir / "run_inverse_design_fem_check.py"),
            "--top-candidates",
            str(surrogate_csv),
            "--intrinsic-dataset",
            args.candidates,
            "--db-path",
            args.db_path,
            "--out-dir",
            str(fem_dir),
            "--top-k",
            str(args.fem_check),
            "--workers",
            str(args.workers),
            "--voxel-size-m",
            str(args.voxel_size_m),
            "--progress-every",
            str(args.progress_every),
        ]
        run_command(fem_check_cmd)

        rank_cmd = [
            sys.executable,
            str(script_dir / "rank_inverse_design_fem_results.py"),
            "--fem-results",
            str(fem_dir / "fem_results.csv"),
            "--error-csv",
            str(fem_dir / "surrogate_vs_fem_errors.csv"),
            "--output",
            str(final_csv),
            "--summary",
            str(final_summary),
            "--score-target",
            score_target,
            "--direction",
            direction,
            "--top-k",
            str(args.recommendations),
        ]
        for name in APPLICATION_ARGS:
            add_optional(rank_cmd, kebab(name), getattr(args, name))
        run_command(rank_cmd)
        outputs.update(
            {
                "fem_results": str(fem_dir / "fem_results.csv"),
                "fem_audit_summary": str(fem_dir / "audit" / "summary.txt"),
                "surrogate_vs_fem_summary": str(fem_dir / "surrogate_vs_fem_summary.txt"),
                "surrogate_vs_fem_errors": str(fem_dir / "surrogate_vs_fem_errors.csv"),
                "final_recommendations": str(final_csv),
                "final_summary": str(final_summary),
            }
        )

    write_advisor_summary(advisor_summary, args, score_target, direction, out_dir, outputs)

    print()
    print("Design advisor finished.")
    print(f"Run directory: {out_dir}")
    print(f"Advisor summary: {advisor_summary}")
    if "final_recommendations" in outputs:
        print(f"Final recommendations: {outputs['final_recommendations']}")
    else:
        print(f"Surrogate recommendations: {surrogate_csv}")


if __name__ == "__main__":
    main()
