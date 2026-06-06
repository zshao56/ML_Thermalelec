#!/usr/bin/env python3
"""Rank candidate unit-cell designs with a trained FEM surrogate."""

from __future__ import annotations

import argparse
import csv
import heapq
import math
from pathlib import Path
from typing import Iterable

import numpy as np


DEFAULT_MODEL = "results/fem_surrogate_80000_voxel100um_sklearn/fem_surrogate_sklearn.joblib"
DEFAULT_CANDIDATES = "results/intrinsic_network_dataset.csv"
DEFAULT_OUTPUT = "results/inverse_design/top_candidates.csv"
DEFAULT_SUMMARY = "results/inverse_design/summary.txt"

TARGETS = [
    "kappa_eff_fem_w_mk",
    "r_e_fem_ohm",
    "alpha_eff_fem_v_k",
    "p_max_coeff_fem_w_k2",
    "p_area_coeff_fem_w_m2_k2",
]

DEVICE_TARGETS = [
    "device_delta_t_k",
    "device_v_oc_v",
    "device_abs_v_oc_v",
    "device_p_max_w",
    "device_p_area_w_m2",
    "device_q_hot_w_m2",
    "device_t_hot_k",
    "device_t_cold_k",
]

LOG_TARGETS = {
    "kappa_eff_fem_w_mk",
    "r_e_fem_ohm",
    "p_max_coeff_fem_w_k2",
    "p_area_coeff_fem_w_m2_k2",
}

NUMERIC_ALIASES = {
    "network_kappa_eff_w_mk": ["network_kappa_eff_w_mk", "kappa_eff_network_w_mk"],
    "network_r_e_ohm": ["network_r_e_ohm", "r_e_network_ohm"],
    "network_alpha_device_v_k": ["network_alpha_device_v_k", "alpha_device_v_k"],
    "network_p_max_coeff_w_k2": ["network_p_max_coeff_w_k2", "p_max_coeff_w_k2"],
    "network_p_area_coeff_w_m2_k2": ["network_p_area_coeff_w_m2_k2", "p_area_coeff_w_m2_k2"],
}

SUMMARY_FIELDS = [
    "case_id",
    "material_name",
    "carrier_type",
    "t_ring_m",
    "ratio_hole",
    "h_uc_m",
    "n_layer",
    "column_type",
    "size1_m",
    "num_columns",
    "path_type",
    "connection_offset_units",
    "t_coating_m",
]


def import_joblib():
    try:
        import joblib
    except Exception as exc:
        raise SystemExit(
            "joblib/scikit-learn is required to load the trained surrogate. Install with:\n"
            "  conda install -c conda-forge scikit-learn joblib\n"
            f"Import error: {exc}"
        )
    return joblib


def finite_float(value: str, field: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Non-finite value for {field}: {value}")
    return parsed


def first_present(row: dict[str, str], names: Iterable[str], field: str) -> str:
    for name in names:
        value = row.get(name, "")
        if value != "":
            return value
    raise KeyError(f"Missing required candidate field for {field}: tried {list(names)}")


def feature_dict(
    row: dict[str, str],
    numeric_features: list[str],
    categorical_features: list[str],
) -> dict[str, object]:
    item: dict[str, object] = {}
    for field in numeric_features:
        aliases = NUMERIC_ALIASES.get(field, [field])
        item[field] = finite_float(first_present(row, aliases, field), field)
    for field in categorical_features:
        item[field] = first_present(row, [field], field)
    return item


def inverse_target(values: np.ndarray, target: str, log_targets: set[str]) -> np.ndarray:
    return np.exp(values) if target in log_targets else values


def read_chunks(path: Path, chunk_size: int) -> Iterable[tuple[list[str], list[dict[str, str]]]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        chunk: list[dict[str, str]] = []
        for row in reader:
            chunk.append(row)
            if len(chunk) >= chunk_size:
                yield fieldnames, chunk
                chunk = []
        if chunk:
            yield fieldnames, chunk


def pass_row_filters(row: dict[str, str], args: argparse.Namespace) -> bool:
    if args.material and row.get("material_name") != args.material:
        return False
    if args.carrier and row.get("carrier_type") != args.carrier:
        return False
    if args.column_type and row.get("column_type") != args.column_type:
        return False
    if args.path_type and row.get("path_type") != args.path_type:
        return False
    exact_filters = {
        "t_ring_m": args.t_ring_m,
        "ratio_hole": args.ratio_hole,
        "h_uc_m": args.h_uc_m,
        "n_layer": args.n_layer,
        "size1_m": args.size1_m,
        "num_columns": args.num_columns,
        "connection_offset_units": args.connection_offset_units,
        "t_coating_m": args.t_coating_m,
    }
    for field, expected in exact_filters.items():
        if expected is None:
            continue
        try:
            actual = finite_float(row.get(field, ""), field)
        except (TypeError, ValueError):
            return False
        tolerance = max(1e-12, abs(expected) * 1e-9)
        if abs(actual - expected) > tolerance:
            return False
    return True


def pass_prediction_filters(pred: dict[str, float], args: argparse.Namespace) -> bool:
    if args.min_kappa is not None and pred["kappa_eff_fem_w_mk"] < args.min_kappa:
        return False
    if args.max_kappa is not None and pred["kappa_eff_fem_w_mk"] > args.max_kappa:
        return False
    if args.min_r_e is not None and pred["r_e_fem_ohm"] < args.min_r_e:
        return False
    if args.max_r_e is not None and pred["r_e_fem_ohm"] > args.max_r_e:
        return False
    if args.min_p_max is not None and pred["p_max_coeff_fem_w_k2"] < args.min_p_max:
        return False
    if args.min_p_area is not None and pred["p_area_coeff_fem_w_m2_k2"] < args.min_p_area:
        return False
    if args.alpha_sign == "positive" and pred["alpha_eff_fem_v_k"] <= 0.0:
        return False
    if args.alpha_sign == "negative" and pred["alpha_eff_fem_v_k"] >= 0.0:
        return False
    return True


def row_float(row: dict[str, str], field: str, default: float) -> float:
    value = row.get(field, "")
    if value == "":
        return default
    return finite_float(value, field)


def device_metrics(row: dict[str, str], pred: dict[str, float], args: argparse.Namespace) -> dict[str, float]:
    area_m2 = args.area_m2 if args.area_m2 is not None else row_float(row, "a_device_m2", math.pi * 0.002 * 0.002)
    length_m = args.length_m if args.length_m is not None else row_float(row, "l_device_m", 0.01)
    if area_m2 <= 0.0:
        raise ValueError("--area-m2 / a_device_m2 must be positive")
    if length_m <= 0.0:
        raise ValueError("--length-m / l_device_m must be positive")

    kappa = pred["kappa_eff_fem_w_mk"]
    r_e = pred["r_e_fem_ohm"]
    alpha = pred["alpha_eff_fem_v_k"]
    if kappa <= 0.0:
        raise ValueError("Predicted kappa must be positive for device scoring")

    if args.boundary_type == "fixed_hot_surface_cold_convection":
        delta_t_source = args.t_hot_k - args.t_cold_k
        delta_t_device = delta_t_source * length_m / (length_m + kappa / args.h_c_w_m2k)
        t_hot_device = args.t_hot_k
        t_cold_device = t_hot_device - delta_t_device
        q_hot_w_m2 = kappa * delta_t_device / length_m
        delta_t_retention = delta_t_device / delta_t_source if delta_t_source else 0.0
    elif args.boundary_type == "fixed_q_cold_convection":
        if args.q_hot_w_m2 is None:
            raise ValueError("--q-hot-w-m2 is required for fixed_q_cold_convection")
        q_hot_w_m2 = args.q_hot_w_m2
        delta_t_device = q_hot_w_m2 * length_m / kappa
        t_cold_device = args.t_cold_k + q_hot_w_m2 / args.h_c_w_m2k
        t_hot_device = t_cold_device + delta_t_device
        delta_t_retention = 1.0
    else:
        raise ValueError(f"Unknown boundary_type: {args.boundary_type}")

    v_oc = alpha * delta_t_device
    p_max = (v_oc * v_oc) / (4.0 * r_e) if r_e > 0.0 else 0.0
    return {
        "device_delta_t_k": delta_t_device,
        "device_delta_t_retention": delta_t_retention,
        "device_v_oc_v": v_oc,
        "device_abs_v_oc_v": abs(v_oc),
        "device_p_max_w": p_max,
        "device_p_area_w_m2": p_max / area_m2,
        "device_p_volume_w_m3": p_max / (area_m2 * length_m),
        "device_q_hot_w_m2": q_hot_w_m2,
        "device_q_hot_w": q_hot_w_m2 * area_m2,
        "device_t_hot_k": t_hot_device,
        "device_t_cold_k": t_cold_device,
        "device_t_avg_k": 0.5 * (t_hot_device + t_cold_device),
        "device_area_m2": area_m2,
        "device_length_m": length_m,
    }


def format_output_row(
    row: dict[str, str],
    pred: dict[str, float],
    device: dict[str, float],
    score_target: str,
    score: float,
) -> dict[str, object]:
    output: dict[str, object] = {}
    for field in SUMMARY_FIELDS:
        output[field] = row.get(field, "")
    output["score_target"] = score_target
    output["score"] = score
    for target in TARGETS:
        output[f"pred_{target}"] = pred[target]
    for target, value in device.items():
        output[f"pred_{target}"] = value
    return output


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    device_fields = [
        "device_area_m2",
        "device_length_m",
        "device_t_hot_k",
        "device_t_cold_k",
        "device_t_avg_k",
        "device_delta_t_k",
        "device_delta_t_retention",
        "device_q_hot_w_m2",
        "device_q_hot_w",
        "device_v_oc_v",
        "device_abs_v_oc_v",
        "device_p_max_w",
        "device_p_area_w_m2",
        "device_p_volume_w_m3",
    ]
    fieldnames = SUMMARY_FIELDS + ["score_target", "score"] + [f"pred_{target}" for target in TARGETS + device_fields]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, args: argparse.Namespace, counts: dict[str, int], output_rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"model: {args.model}\n")
        f.write(f"candidates: {args.candidates}\n")
        f.write(f"score_target: {args.score_target}\n")
        f.write(f"direction: {args.direction}\n")
        f.write(f"top_k: {args.top_k}\n")
        f.write(f"boundary_type: {args.boundary_type}\n")
        f.write(f"t_hot_k: {args.t_hot_k}\n")
        f.write(f"t_cold_k: {args.t_cold_k}\n")
        f.write(f"h_c_w_m2k: {args.h_c_w_m2k}\n")
        f.write(f"q_hot_w_m2: {args.q_hot_w_m2}\n")
        f.write(f"area_m2: {args.area_m2}\n")
        f.write(f"length_m: {args.length_m}\n")
        f.write(f"rows_read: {counts['read']}\n")
        f.write(f"rows_after_design_filters: {counts['design_filtered']}\n")
        f.write(f"rows_after_prediction_filters: {counts['prediction_filtered']}\n")
        f.write(f"output: {args.output}\n")
        if output_rows:
            best = output_rows[0]
            f.write("\nbest_candidate:\n")
            for field in ["case_id", "material_name", "carrier_type", "column_type", "path_type", "score"]:
                f.write(f"  {field}: {best.get(field, '')}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank inverse-design candidates with a trained FEM surrogate.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", default=DEFAULT_SUMMARY)
    parser.add_argument("--score-target", choices=TARGETS + DEVICE_TARGETS, default="p_area_coeff_fem_w_m2_k2")
    parser.add_argument("--direction", choices=["max", "min"], default="max")
    parser.add_argument("--top-k", type=int, default=200)
    parser.add_argument("--chunk-size", type=int, default=20000)
    parser.add_argument("--progress-every", type=int, default=50000)
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
    args = parser.parse_args()

    if args.top_k <= 0:
        raise SystemExit("--top-k must be positive")
    if args.chunk_size <= 0:
        raise SystemExit("--chunk-size must be positive")
    if args.h_c_w_m2k <= 0.0:
        raise SystemExit("--h-c-w-m2k must be positive")
    if args.boundary_type == "fixed_q_cold_convection" and args.q_hot_w_m2 is None:
        raise SystemExit("--q-hot-w-m2 is required when --boundary-type fixed_q_cold_convection")

    model_path = Path(args.model)
    candidates_path = Path(args.candidates)
    if not model_path.exists():
        raise SystemExit(f"Model file does not exist: {model_path}")
    if not candidates_path.exists():
        raise SystemExit(f"Candidate CSV does not exist: {candidates_path}")

    joblib = import_joblib()
    bundle = joblib.load(model_path)
    numeric_features = list(bundle["numeric_features"])
    categorical_features = list(bundle["categorical_features"])
    models = bundle["models"]
    log_targets = set(bundle.get("log_targets", sorted(LOG_TARGETS)))

    heap: list[tuple[float, int, dict[str, object]]] = []
    seq = 0
    counts = {"read": 0, "design_filtered": 0, "prediction_filtered": 0}

    for _fieldnames, chunk in read_chunks(candidates_path, args.chunk_size):
        counts["read"] += len(chunk)
        filtered_rows = [row for row in chunk if pass_row_filters(row, args)]
        counts["design_filtered"] += len(filtered_rows)
        if not filtered_rows:
            continue

        features = [feature_dict(row, numeric_features, categorical_features) for row in filtered_rows]
        predictions_by_target: dict[str, np.ndarray] = {}
        for target in TARGETS:
            raw = models[target].predict(features)
            predictions_by_target[target] = inverse_target(np.asarray(raw, dtype=float), target, log_targets)

        for index, row in enumerate(filtered_rows):
            pred = {target: float(predictions_by_target[target][index]) for target in TARGETS}
            if not pass_prediction_filters(pred, args):
                continue
            counts["prediction_filtered"] += 1
            device = device_metrics(row, pred, args)
            score = pred[args.score_target] if args.score_target in TARGETS else device[args.score_target]
            rank_key = score if args.direction == "max" else -score
            output_row = format_output_row(row, pred, device, args.score_target, score)
            item = (rank_key, seq, output_row)
            seq += 1
            if len(heap) < args.top_k:
                heapq.heappush(heap, item)
            elif item[0] > heap[0][0]:
                heapq.heapreplace(heap, item)

        if args.progress_every and counts["read"] % args.progress_every < len(chunk):
            print(
                f"Progress: read={counts['read']} "
                f"design_filtered={counts['design_filtered']} "
                f"prediction_filtered={counts['prediction_filtered']}"
            )

    ranked = [item[2] for item in sorted(heap, key=lambda item: item[0], reverse=True)]
    write_csv(Path(args.output), ranked)
    write_summary(Path(args.summary), args, counts, ranked)

    print(f"Rows read: {counts['read']}")
    print(f"Rows after design filters: {counts['design_filtered']}")
    print(f"Rows after prediction filters: {counts['prediction_filtered']}")
    print(f"Wrote top candidates: {args.output}")
    print(f"Summary: {args.summary}")
    if ranked:
        best = ranked[0]
        print()
        print("best_candidate")
        print(f"case_id: {best.get('case_id', '')}")
        print(f"material: {best.get('material_name', '')} {best.get('carrier_type', '')}")
        print(f"column_type: {best.get('column_type', '')}")
        print(f"path_type: {best.get('path_type', '')}")
        print(f"score: {best.get('score', '')}")
        for target in TARGETS:
            print(f"pred_{target}: {best.get(f'pred_{target}', '')}")


if __name__ == "__main__":
    main()
