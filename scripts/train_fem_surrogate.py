#!/usr/bin/env python3
"""Train a baseline scenario-independent surrogate from FEM labels.

This is intentionally dependency-light: it uses only the Python standard
library and NumPy, so it works in the same server environment as the current
FEM pipeline. The model is a one-hot + standardized-numeric ridge regressor
with log targets for positive FEM outputs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path

import numpy as np


NUMERIC_FEATURES = [
    "t_ring_m",
    "ratio_hole",
    "h_uc_m",
    "n_layer",
    "size1_m",
    "num_columns",
    "connection_offset_units",
    "t_coating_m",
    "network_kappa_eff_w_mk",
    "network_r_e_ohm",
    "network_alpha_device_v_k",
    "network_p_max_coeff_w_k2",
    "network_p_area_coeff_w_m2_k2",
]

CATEGORICAL_FEATURES = [
    "material_name",
    "column_type",
    "path_type",
]

TARGETS = [
    "kappa_eff_fem_w_mk",
    "r_e_fem_ohm",
    "alpha_eff_fem_v_k",
    "p_max_coeff_fem_w_k2",
    "p_area_coeff_fem_w_m2_k2",
]

LOG_TARGETS = {
    "kappa_eff_fem_w_mk",
    "r_e_fem_ohm",
    "p_max_coeff_fem_w_k2",
    "p_area_coeff_fem_w_m2_k2",
}


def parse_float(row: dict[str, str], field: str) -> float:
    value = float(row[field])
    if not math.isfinite(value):
        raise ValueError(f"Non-finite value in {field}: {row[field]}")
    return value


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No rows found in {path}")
    missing = sorted(set(NUMERIC_FEATURES + CATEGORICAL_FEATURES + TARGETS) - set(rows[0]))
    if missing:
        raise SystemExit(f"Missing expected columns: {missing}")
    return rows


def split_rows(rows: list[dict[str, str]], test_fraction: float, seed: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    n_test = max(1, round(len(shuffled) * test_fraction))
    n_train = len(shuffled) - n_test
    if n_train < 2:
        raise SystemExit("Need at least 3 rows for train/test split")
    return shuffled[:n_train], shuffled[n_train:]


def build_preprocessor(train_rows: list[dict[str, str]]) -> dict[str, object]:
    numeric_matrix = np.asarray(
        [[parse_float(row, field) for field in NUMERIC_FEATURES] for row in train_rows],
        dtype=float,
    )
    means = numeric_matrix.mean(axis=0)
    stds = numeric_matrix.std(axis=0)
    stds[stds == 0.0] = 1.0
    categories = {
        field: sorted({row[field] for row in train_rows})
        for field in CATEGORICAL_FEATURES
    }
    return {
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "numeric_mean": means.tolist(),
        "numeric_std": stds.tolist(),
        "categories": categories,
    }


def feature_names(preprocessor: dict[str, object]) -> list[str]:
    names = ["intercept"]
    names.extend(NUMERIC_FEATURES)
    categories = preprocessor["categories"]
    assert isinstance(categories, dict)
    for field in CATEGORICAL_FEATURES:
        for value in categories[field]:
            names.append(f"{field}={value}")
    return names


def transform_rows(rows: list[dict[str, str]], preprocessor: dict[str, object]) -> np.ndarray:
    means = np.asarray(preprocessor["numeric_mean"], dtype=float)
    stds = np.asarray(preprocessor["numeric_std"], dtype=float)
    categories = preprocessor["categories"]
    assert isinstance(categories, dict)

    matrix = []
    for row in rows:
        values = [1.0]
        numeric = np.asarray([parse_float(row, field) for field in NUMERIC_FEATURES], dtype=float)
        values.extend(((numeric - means) / stds).tolist())
        for field in CATEGORICAL_FEATURES:
            row_value = row[field]
            values.extend(1.0 if row_value == value else 0.0 for value in categories[field])
        matrix.append(values)
    return np.asarray(matrix, dtype=float)


def target_values(rows: list[dict[str, str]], target: str) -> np.ndarray:
    values = np.asarray([parse_float(row, target) for row in rows], dtype=float)
    if target in LOG_TARGETS:
        if np.any(values <= 0.0):
            raise SystemExit(f"Target {target} contains non-positive values; cannot log-transform")
        return np.log(values)
    return values


def inverse_target(values: np.ndarray, target: str) -> np.ndarray:
    if target in LOG_TARGETS:
        return np.exp(values)
    return values


def fit_ridge(x_train: np.ndarray, y_train: np.ndarray, ridge_alpha: float) -> np.ndarray:
    penalty = np.eye(x_train.shape[1], dtype=float) * ridge_alpha
    penalty[0, 0] = 0.0
    return np.linalg.solve(x_train.T @ x_train + penalty, x_train.T @ y_train)


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    residual = y_pred - y_true
    mae = float(np.mean(np.abs(residual)))
    rmse = float(math.sqrt(np.mean(residual * residual)))
    denom = np.where(np.abs(y_true) > 1e-30, np.abs(y_true), np.nan)
    mape = float(np.nanmean(np.abs(residual) / denom) * 100.0)
    ss_res = float(np.sum(residual * residual))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else float("nan")
    return {"mae": mae, "rmse": rmse, "mape_percent": mape, "r2": r2}


def write_metrics(path: Path, metric_rows: list[dict[str, object]]) -> None:
    fieldnames = ["target", "train_mae", "train_rmse", "train_mape_percent", "train_r2", "test_mae", "test_rmse", "test_mape_percent", "test_r2"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(metric_rows)


def write_predictions(path: Path, rows: list[dict[str, str]], predictions: dict[str, np.ndarray]) -> None:
    fieldnames = ["case_id"]
    for target in TARGETS:
        fieldnames.extend([f"true_{target}", f"pred_{target}", f"abs_error_{target}", f"rel_error_{target}"])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for index, row in enumerate(rows):
            output = {"case_id": row.get("case_id", "")}
            for target in TARGETS:
                true_value = parse_float(row, target)
                pred_value = float(predictions[target][index])
                output[f"true_{target}"] = true_value
                output[f"pred_{target}"] = pred_value
                output[f"abs_error_{target}"] = abs(pred_value - true_value)
                output[f"rel_error_{target}"] = abs(pred_value - true_value) / abs(true_value) if abs(true_value) > 1e-30 else ""
            writer.writerow(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a baseline FEM surrogate model.")
    parser.add_argument("--input", default="results/fem_sampling/fem_training_dataset.csv")
    parser.add_argument("--out-dir", default="results/fem_surrogate")
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ridge-alpha", type=float, default=1e-6)
    args = parser.parse_args()

    if not 0.0 < args.test_fraction < 0.8:
        raise SystemExit("--test-fraction must be between 0 and 0.8")

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    rows = read_rows(input_path)
    train_rows, test_rows = split_rows(rows, args.test_fraction, args.seed)
    preprocessor = build_preprocessor(train_rows)
    x_train = transform_rows(train_rows, preprocessor)
    x_test = transform_rows(test_rows, preprocessor)

    model = {
        "model_type": "standardized_onehot_ridge",
        "input": str(input_path),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "ridge_alpha": args.ridge_alpha,
        "targets": TARGETS,
        "log_targets": sorted(LOG_TARGETS),
        "preprocessor": preprocessor,
        "feature_names": feature_names(preprocessor),
        "coefficients": {},
    }
    metric_rows: list[dict[str, object]] = []
    train_predictions: dict[str, np.ndarray] = {}
    test_predictions: dict[str, np.ndarray] = {}

    for target in TARGETS:
        y_train_model = target_values(train_rows, target)
        coef = fit_ridge(x_train, y_train_model, args.ridge_alpha)
        train_pred = inverse_target(x_train @ coef, target)
        test_pred = inverse_target(x_test @ coef, target)
        train_true = np.asarray([parse_float(row, target) for row in train_rows], dtype=float)
        test_true = np.asarray([parse_float(row, target) for row in test_rows], dtype=float)
        train_m = metrics(train_true, train_pred)
        test_m = metrics(test_true, test_pred)
        metric_rows.append(
            {
                "target": target,
                "train_mae": train_m["mae"],
                "train_rmse": train_m["rmse"],
                "train_mape_percent": train_m["mape_percent"],
                "train_r2": train_m["r2"],
                "test_mae": test_m["mae"],
                "test_rmse": test_m["rmse"],
                "test_mape_percent": test_m["mape_percent"],
                "test_r2": test_m["r2"],
            }
        )
        model["coefficients"][target] = coef.tolist()
        train_predictions[target] = train_pred
        test_predictions[target] = test_pred

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "fem_surrogate_model.json"
    metrics_path = out_dir / "metrics.csv"
    test_predictions_path = out_dir / "test_predictions.csv"
    train_predictions_path = out_dir / "train_predictions.csv"
    model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    write_metrics(metrics_path, metric_rows)
    write_predictions(test_predictions_path, test_rows, test_predictions)
    write_predictions(train_predictions_path, train_rows, train_predictions)

    print(f"Input rows: {len(rows)}")
    print(f"Train rows: {len(train_rows)}")
    print(f"Test rows: {len(test_rows)}")
    print(f"Model: {model_path}")
    print(f"Metrics: {metrics_path}")
    print(f"Test predictions: {test_predictions_path}")
    print()
    for row in metric_rows:
        print(
            f"{row['target']}: "
            f"test_r2={float(row['test_r2']):.4g}, "
            f"test_mape={float(row['test_mape_percent']):.3g}%"
        )


if __name__ == "__main__":
    main()
