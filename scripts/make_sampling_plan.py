#!/usr/bin/env python3
"""Build a first FEM/experiment sampling plan from ranked candidate designs."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


GROUP_FIELDS = [
    "ratio_hole",
    "h_uc_m",
    "column_type",
    "size1_m",
    "num_columns",
    "path_type",
    "connection_offset_units",
]

DESIGN_FIELDS = [
    "case_id",
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
    "kappa_uc_est_w_mk",
    "r_uc_est_ohm",
]

OUTPUT_FIELDS = [
    "sample_id",
    "priority",
    "selection_reason",
    "source_scenarios",
    "best_rank",
    "best_score_p_area_w_m2",
] + DESIGN_FIELDS + [
    "planned_data_source",
    "target_kappa_eff_fem",
    "target_r_e_fem",
    "target_delta_t_device_fem",
    "target_v_oc_fem",
    "target_p_max_fem",
    "mechanical_valid",
    "fabrication_note",
]


def read_ranked_rows(path: Path, max_rank: int) -> dict[str, dict[str, str]]:
    by_case: dict[str, dict[str, str]] = {}
    scenarios: dict[str, list[str]] = defaultdict(list)
    for row in csv.DictReader(path.open("r", newline="", encoding="utf-8")):
        rank = int(row["rank"])
        if rank > max_rank:
            continue
        case_id = row["case_id"]
        scenarios[case_id].append(f"{row['scenario_id']}:rank{rank}")
        current = by_case.get(case_id)
        score = float(row["score_value"])
        if current is None or rank < int(current["best_rank"]):
            by_case[case_id] = {
                "case_id": case_id,
                "best_rank": str(rank),
                "best_score_p_area_w_m2": str(score),
            }
    for case_id, row in by_case.items():
        row["source_scenarios"] = ";".join(scenarios[case_id])
    return by_case


def read_design_rows(path: Path) -> dict[str, dict[str, str]]:
    return {row["case_id"]: row for row in csv.DictReader(path.open("r", newline="", encoding="utf-8"))}


def group_key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[field] for field in GROUP_FIELDS)


def add_sample(
    selected: dict[str, dict[str, str]],
    case_id: str,
    reason: str,
    priority: str,
    ranked: dict[str, dict[str, str]],
    designs: dict[str, dict[str, str]],
) -> None:
    if case_id in selected:
        selected[case_id]["selection_reason"] += f";{reason}"
        return
    design = designs[case_id]
    rank_info = ranked[case_id]
    row = {
        "priority": priority,
        "selection_reason": reason,
        "source_scenarios": rank_info["source_scenarios"],
        "best_rank": rank_info["best_rank"],
        "best_score_p_area_w_m2": rank_info["best_score_p_area_w_m2"],
        "planned_data_source": "FEM_or_experiment",
        "target_kappa_eff_fem": "",
        "target_r_e_fem": "",
        "target_delta_t_device_fem": "",
        "target_v_oc_fem": "",
        "target_p_max_fem": "",
        "mechanical_valid": "",
        "fabrication_note": "",
    }
    for field in DESIGN_FIELDS:
        row[field] = design.get(field, "")
    selected[case_id] = row


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a compact sampling plan from Top-K candidates.")
    parser.add_argument("--ranked-csv", default="results/top50_evaluations_constrained.csv")
    parser.add_argument("--design-csv", default="results/top50_design_cases_constrained.csv")
    parser.add_argument("--output", default="results/sampling_plan_top50.csv")
    parser.add_argument("--rank-max", type=int, default=50)
    parser.add_argument("--target-count", type=int, default=50)
    args = parser.parse_args()

    ranked_path = Path(args.ranked_csv)
    design_path = Path(args.design_csv)
    if not ranked_path.exists():
        raise SystemExit(f"Ranked CSV does not exist: {ranked_path}")
    if not design_path.exists():
        raise SystemExit(f"Design CSV does not exist: {design_path}")

    ranked = read_ranked_rows(ranked_path, args.rank_max)
    designs = read_design_rows(design_path)
    missing = sorted(set(ranked) - set(designs))
    if missing:
        raise SystemExit(f"Design rows missing for case_ids: {missing[:10]}")

    selected: dict[str, dict[str, str]] = {}

    # Priority A: every design that appears as rank 1 in at least one scenario.
    for case_id, info in sorted(ranked.items(), key=lambda item: int(item[1]["best_rank"])):
        if int(info["best_rank"]) == 1:
            add_sample(selected, case_id, "scenario_rank_1", "A", ranked, designs)

    # Priority B: best design from each distinct structural group.
    best_by_group: dict[tuple[str, ...], str] = {}
    for case_id, info in sorted(ranked.items(), key=lambda item: int(item[1]["best_rank"])):
        key = group_key(designs[case_id])
        best_by_group.setdefault(key, case_id)
    for case_id in best_by_group.values():
        if len(selected) >= args.target_count:
            break
        add_sample(selected, case_id, "group_representative", "B", ranked, designs)

    # Priority C: fill remaining slots by rank order.
    for case_id, _info in sorted(ranked.items(), key=lambda item: int(item[1]["best_rank"])):
        if len(selected) >= args.target_count:
            break
        add_sample(selected, case_id, "rank_fill", "C", ranked, designs)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        selected.values(),
        key=lambda row: (row["priority"], int(row["best_rank"]), int(row["case_id"])),
    )
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for idx, row in enumerate(rows, start=1):
            row = dict(row)
            row["sample_id"] = f"S{idx:03d}"
            writer.writerow(row)

    print(f"Ranked candidates read: {len(ranked)}")
    print(f"Design candidates read: {len(designs)}")
    print(f"Sampling plan rows: {len(rows)}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
