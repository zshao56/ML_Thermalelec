#!/usr/bin/env python3
"""Summarize categorical design variables in a design-case CSV."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


DEFAULT_FIELDS = [
    "t_ring_m",
    "ratio_hole",
    "h_uc_m",
    "n_layer",
    "column_type",
    "size1_m",
    "num_columns",
    "path_type",
    "connection_offset_units",
    "material_name",
    "t_coating_m",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize variable distributions in design cases.")
    parser.add_argument("--input", default="results/top50_design_cases_constrained.csv", help="Input CSV.")
    parser.add_argument(
        "--fields",
        default=",".join(DEFAULT_FIELDS),
        help="Comma-separated fields to summarize.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")

    fields = [field.strip() for field in args.fields.split(",") if field.strip()]
    rows = list(csv.DictReader(input_path.open("r", newline="", encoding="utf-8")))
    if not rows:
        raise SystemExit(f"No rows found in {input_path}")

    missing = [field for field in fields if field not in rows[0]]
    if missing:
        raise SystemExit(f"Missing fields in CSV: {missing}")

    print(f"input: {input_path}")
    print(f"n_cases: {len(rows)}")

    for field in fields:
        print()
        print(field)
        for value, count in Counter(row[field] for row in rows).most_common():
            print(f"  {value}: {count}")


if __name__ == "__main__":
    main()
