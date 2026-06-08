#!/usr/bin/env python3
"""Run design-advisor scenarios from a CSV parameter table."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path


DEFAULT_CONFIG = "configs/design_advisor_scenarios.csv"
DEFAULT_ADVISOR_ROOT = "results/design_advisor"
DEFAULT_DB_PATH = "data/unit_cell_design_space.sqlite"

ADVISOR_FIELDS = [
    "model_type",
    "device",
    "objective",
    "material",
    "carrier",
    "boundary_type",
    "t_hot_k",
    "t_cold_k",
    "h_c_w_m2k",
    "q_hot_w_m2",
    "area_m2",
    "length_m",
    "top_k",
    "fem_check",
    "recommendations",
    "workers",
    "voxel_size_m",
    "progress_every",
]


def enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def kebab(name: str) -> str:
    return "--" + name.replace("_", "-")


def add_optional(command: list[str], option: str, value: str | None) -> None:
    if value is None:
        return
    cleaned = value.strip()
    if cleaned == "":
        return
    command.extend([option, cleaned])


def run_command(command: list[str], dry_run: bool) -> None:
    print()
    print("[RUN] " + " ".join(command))
    if not dry_run:
        subprocess.run(command, check=True)


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Scenario config does not exist: {path}")
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No scenario rows found in {path}")
    if "run_name" not in rows[0]:
        raise SystemExit("Scenario config must contain a run_name column")
    return rows


def selected_rows(rows: list[dict[str, str]], only: list[str]) -> list[dict[str, str]]:
    wanted = set(only)
    output = []
    for row in rows:
        run_name = row.get("run_name", "").strip()
        if only and run_name not in wanted:
            continue
        if not only and not enabled(row.get("enabled", "")):
            continue
        output.append(row)
    return output


def advisor_command(script_dir: Path, row: dict[str, str], advisor_root: str, db_path: str) -> list[str]:
    run_name = row["run_name"].strip()
    command = [
        sys.executable,
        str(script_dir / "design_advisor.py"),
        "--run-name",
        run_name,
        "--out-root",
        advisor_root,
        "--db-path",
        db_path,
    ]
    for field in ADVISOR_FIELDS:
        add_optional(command, kebab(field), row.get(field))
    add_optional(command, "--model", row.get("model"))
    add_optional(command, "--candidates", row.get("candidates"))
    return command


def stl_command(
    script_dir: Path,
    row: dict[str, str],
    advisor_root: str,
    db_path: str,
) -> list[str]:
    run_name = row["run_name"].strip()
    top_k = (row.get("stl_top_k") or "").strip() or "5"
    ring_segments = (row.get("ring_segments") or "").strip() or "128"
    run_dir = Path(advisor_root) / run_name
    return [
        sys.executable,
        str(script_dir / "export_recommendation_stls.py"),
        "--recommendations",
        str(run_dir / "final_recommendations.csv"),
        "--db-path",
        db_path,
        "--out-dir",
        str(run_dir / f"stl_top{top_k}"),
        "--top-k",
        top_k,
        "--ring-segments",
        ring_segments,
        "--design-rows-output",
        str(run_dir / f"final_design_rows_top{top_k}.csv"),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one or more design-advisor scenarios from a CSV table.")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--advisor-root", default=DEFAULT_ADVISOR_ROOT)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--only", nargs="*", default=[], help="Run only these run_name values from the table.")
    parser.add_argument("--list", action="store_true", help="List selected scenarios without running them.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    parser.add_argument("--no-stl", action="store_true", help="Skip STL export after each advisor run.")
    args = parser.parse_args()

    config_path = Path(args.config)
    rows = selected_rows(read_rows(config_path), args.only)
    if not rows:
        raise SystemExit("No scenarios selected")

    print(f"Config: {config_path}")
    print(f"Selected scenarios: {len(rows)}")
    for row in rows:
        print(f"  {row['run_name']}")
    if args.list:
        return

    script_dir = Path(__file__).resolve().parent
    for row in rows:
        run_name = row["run_name"].strip()
        print()
        print(f"=== Scenario: {run_name} ===")
        run_command(advisor_command(script_dir, row, args.advisor_root, args.db_path), args.dry_run)
        if not args.no_stl:
            run_command(stl_command(script_dir, row, args.advisor_root, args.db_path), args.dry_run)

    print()
    print("Finished selected scenarios.")


if __name__ == "__main__":
    main()
