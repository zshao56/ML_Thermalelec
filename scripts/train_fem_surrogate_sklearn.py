#!/usr/bin/env python3
"""Train a stronger nonlinear FEM surrogate with scikit-learn."""

from __future__ import annotations

import argparse
import csv
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


def import_sklearn():
    try:
        import joblib
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import ExtraTreesRegressor
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder
    except Exception as exc:
        raise SystemExit(
            "scikit-learn/joblib is required for this trainer. Install on the server with:\n"
            "  conda install -c conda-forge scikit-learn joblib\n"
            f"Import error: {exc}"
        )
    return joblib, ColumnTransformer, ExtraTreesRegressor, Pipeline, OneHotEncoder


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


def matrix(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    output = []
    for row in rows:
        item: dict[str, object] = {}
        for field in NUMERIC_FEATURES:
            item[field] = parse_float(row, field)
        for field in CATEGORICAL_FEATURES:
            item[field] = row[field]
        output.append(item)
    return output


def target_values(rows: list[dict[str, str]], target: str) -> np.ndarray:
    values = np.asarray([parse_float(row, target) for row in rows], dtype=float)
    if target in LOG_TARGETS:
        if np.any(values <= 0.0):
            raise SystemExit(f"Target {target} contains non-positive values; cannot log-transform")
        return np.log(values)
    return values


def inverse_target(values: np.ndarray, target: str) -> np.ndarray:
    return np.exp(values) if target in LOG_TARGETS else values


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
    fieldnames = [
        "target",
        "train_mae",
        "train_rmse",
        "train_mape_percent",
        "train_r2",
        "test_mae",
        "test_rmse",
        "test_mape_percent",
        "test_r2",
    ]
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
            output: dict[str, object] = {"case_id": row.get("case_id", "")}
            for target in TARGETS:
                true_value = parse_float(row, target)
                pred_value = float(predictions[target][index])
                output[f"true_{target}"] = true_value
                output[f"pred_{target}"] = pred_value
                output[f"abs_error_{target}"] = abs(pred_value - true_value)
                output[f"rel_error_{target}"] = abs(pred_value - true_value) / abs(true_value) if abs(true_value) > 1e-30 else ""
            writer.writerow(output)


def make_preprocessor(ColumnTransformer, OneHotEncoder):
    try:
        encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:
        encoder = OneHotEncoder(handle_unknown="ignore", sparse=False)
    return ColumnTransformer(
        transformers=[
            ("numeric", "passthrough", NUMERIC_FEATURES),
            ("categorical", encoder, CATEGORICAL_FEATURES),
        ],
        remainder="drop",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a nonlinear scikit-learn FEM surrogate.")
    parser.add_argument("--input", default="results/fem_sampling/fem_training_dataset_1000_voxel100um.csv")
    parser.add_argument("--out-dir", default="results/fem_surrogate_1000_voxel100um_sklearn")
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-estimators", type=int, default=600)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--min-samples-leaf", type=int, default=2)
    parser.add_argument("--n-jobs", type=int, default=-1)
    args = parser.parse_args()

    joblib, ColumnTransformer, ExtraTreesRegressor, Pipeline, OneHotEncoder = import_sklearn()

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    rows = read_rows(input_path)
    train_rows, test_rows = split_rows(rows, args.test_fraction, args.seed)
    x_train = matrix(train_rows)
    x_test = matrix(test_rows)

    models = {}
    metric_rows: list[dict[str, object]] = []
    train_predictions: dict[str, np.ndarray] = {}
    test_predictions: dict[str, np.ndarray] = {}

    for target in TARGETS:
        model = Pipeline(
            steps=[
                ("preprocess", make_preprocessor(ColumnTransformer, OneHotEncoder)),
                (
                    "regressor",
                    ExtraTreesRegressor(
                        n_estimators=args.n_estimators,
                        max_depth=args.max_depth,
                        min_samples_leaf=args.min_samples_leaf,
                        random_state=args.seed,
                        n_jobs=args.n_jobs,
                    ),
                ),
            ]
        )
        model.fit(x_train, target_values(train_rows, target))
        train_pred = inverse_target(model.predict(x_train), target)
        test_pred = inverse_target(model.predict(x_test), target)
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
        models[target] = model
        train_predictions[target] = train_pred
        test_predictions[target] = test_pred

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "fem_surrogate_sklearn.joblib"
    metrics_path = out_dir / "metrics.csv"
    test_predictions_path = out_dir / "test_predictions.csv"
    train_predictions_path = out_dir / "train_predictions.csv"
    joblib.dump(
        {
            "model_type": "extra_trees_onehot_per_target",
            "targets": TARGETS,
            "log_targets": sorted(LOG_TARGETS),
            "numeric_features": NUMERIC_FEATURES,
            "categorical_features": CATEGORICAL_FEATURES,
            "train_rows": len(train_rows),
            "test_rows": len(test_rows),
            "models": models,
        },
        model_path,
    )
    write_metrics(metrics_path, metric_rows)
    write_predictions(test_predictions_path, test_rows, test_predictions)
    write_predictions(train_predictions_path, train_rows, train_predictions)

    print(f"Input rows: {len(rows)}")
    print(f"Train rows: {len(train_rows)}")
    print(f"Test rows: {len(test_rows)}")
    print(f"Model: {model_path}")
    print(f"Metrics: {metrics_path}")
    print()
    for row in metric_rows:
        print(
            f"{row['target']}: "
            f"test_r2={float(row['test_r2']):.4g}, "
            f"test_mape={float(row['test_mape_percent']):.3g}%"
        )


if __name__ == "__main__":
    main()
