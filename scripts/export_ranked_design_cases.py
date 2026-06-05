#!/usr/bin/env python3
"""Export full design rows for case_ids appearing in a ranked evaluation CSV."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


def read_case_ids(ranked_csv: Path, max_rank: int | None) -> list[int]:
    case_ids: list[int] = []
    seen: set[int] = set()
    with ranked_csv.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if max_rank is not None and int(row["rank"]) > max_rank:
                continue
            case_id = int(row["case_id"])
            if case_id not in seen:
                seen.add(case_id)
                case_ids.append(case_id)
    return case_ids


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export complete unit_cell_designs rows for ranked case_ids."
    )
    parser.add_argument(
        "--ranked-csv",
        default="results/top_evaluations_constrained.csv",
        help="Input ranking CSV from scripts/rank_evaluations.py.",
    )
    parser.add_argument(
        "--db-path",
        default="data/unit_cell_design_space.sqlite",
        help="Input SQLite database.",
    )
    parser.add_argument(
        "--output",
        default="results/top_design_cases_constrained.csv",
        help="Output CSV with complete design rows.",
    )
    parser.add_argument(
        "--max-rank",
        type=int,
        default=None,
        help="Only export designs with rank <= max-rank within each scenario.",
    )
    args = parser.parse_args()

    ranked_csv = Path(args.ranked_csv)
    db_path = Path(args.db_path)
    output_path = Path(args.output)
    if not ranked_csv.exists():
        raise SystemExit(f"Ranking CSV does not exist: {ranked_csv}")
    if not db_path.exists():
        raise SystemExit(f"Database does not exist: {db_path}")

    case_ids = read_case_ids(ranked_csv, args.max_rank)
    if not case_ids:
        raise SystemExit("No case_ids found in ranking CSV.")

    placeholders = ",".join(["?"] * len(case_ids))
    query = f"""
        SELECT *
        FROM unit_cell_designs
        WHERE case_id IN ({placeholders})
        ORDER BY case_id
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn, output_path.open("w", newline="", encoding="utf-8") as f:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, case_ids).fetchall()
        if not rows:
            raise SystemExit("No matching design rows found.")
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))

    print(f"Read ranked case_ids: {len(case_ids)}")
    print(f"Wrote design rows: {len(rows)}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
