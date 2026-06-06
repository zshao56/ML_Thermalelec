#!/usr/bin/env python3
"""Train a PyTorch MLP FEM surrogate for continuous inverse-design workflows."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import time
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


def import_torch():
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset
    except Exception as exc:
        raise SystemExit(
            "PyTorch is required for neural-network training. Install on the Linux server with a CUDA build if available, e.g.:\n"
            "  conda install -c pytorch -c nvidia pytorch pytorch-cuda=12.1\n"
            "or CPU only:\n"
            "  conda install -c pytorch pytorch\n"
            f"Import error: {exc}"
        )
    return torch, nn, DataLoader, TensorDataset


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


def split_rows(
    rows: list[dict[str, str]],
    test_fraction: float,
    val_fraction: float,
    seed: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    n_test = max(1, round(len(shuffled) * test_fraction))
    remaining = len(shuffled) - n_test
    n_val = max(1, round(remaining * val_fraction))
    n_train = remaining - n_val
    if n_train < 2:
        raise SystemExit("Need enough rows for train/validation/test split")
    return shuffled[:n_train], shuffled[n_train : n_train + n_val], shuffled[n_train + n_val :]


def categorical_vocab(train_rows: list[dict[str, str]]) -> dict[str, list[str]]:
    vocab: dict[str, list[str]] = {}
    for field in CATEGORICAL_FEATURES:
        vocab[field] = sorted({row[field] for row in train_rows})
    return vocab


def build_x(rows: list[dict[str, str]], vocab: dict[str, list[str]]) -> np.ndarray:
    values: list[list[float]] = []
    for row in rows:
        item = [parse_float(row, field) for field in NUMERIC_FEATURES]
        for field in CATEGORICAL_FEATURES:
            value = row[field]
            choices = vocab[field]
            item.extend(1.0 if value == choice else 0.0 for choice in choices)
        values.append(item)
    return np.asarray(values, dtype=np.float32)


def raw_targets(rows: list[dict[str, str]]) -> np.ndarray:
    values = np.asarray([[parse_float(row, target) for target in TARGETS] for row in rows], dtype=np.float64)
    for index, target in enumerate(TARGETS):
        if target in LOG_TARGETS and np.any(values[:, index] <= 0.0):
            raise SystemExit(f"Target {target} contains non-positive values; cannot log-transform")
    return values


def transform_y(raw: np.ndarray) -> np.ndarray:
    values = raw.copy()
    for index, target in enumerate(TARGETS):
        if target in LOG_TARGETS:
            values[:, index] = np.log(values[:, index])
    return values.astype(np.float32)


def inverse_y(values: np.ndarray) -> np.ndarray:
    raw = values.astype(np.float64).copy()
    for index, target in enumerate(TARGETS):
        if target in LOG_TARGETS:
            raw[:, index] = np.exp(raw[:, index])
    return raw


def fit_standardizer(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = values.mean(axis=0)
    scale = values.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.0)
    return mean.astype(np.float32), scale.astype(np.float32)


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, list[float]]:
    output = {"mae": [], "rmse": [], "mape_percent": [], "r2": []}
    for index in range(y_true.shape[1]):
        residual = y_pred[:, index] - y_true[:, index]
        mae = float(np.mean(np.abs(residual)))
        rmse = float(math.sqrt(np.mean(residual * residual)))
        denom = np.where(np.abs(y_true[:, index]) > 1e-30, np.abs(y_true[:, index]), np.nan)
        mape = float(np.nanmean(np.abs(residual) / denom) * 100.0)
        ss_res = float(np.sum(residual * residual))
        ss_tot = float(np.sum((y_true[:, index] - y_true[:, index].mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else float("nan")
        output["mae"].append(mae)
        output["rmse"].append(rmse)
        output["mape_percent"].append(mape)
        output["r2"].append(r2)
    return output


def write_metrics(path: Path, train_m: dict[str, list[float]], test_m: dict[str, list[float]]) -> None:
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
        for index, target in enumerate(TARGETS):
            writer.writerow(
                {
                    "target": target,
                    "train_mae": train_m["mae"][index],
                    "train_rmse": train_m["rmse"][index],
                    "train_mape_percent": train_m["mape_percent"][index],
                    "train_r2": train_m["r2"][index],
                    "test_mae": test_m["mae"][index],
                    "test_rmse": test_m["rmse"][index],
                    "test_mape_percent": test_m["mape_percent"][index],
                    "test_r2": test_m["r2"][index],
                }
            )


def write_predictions(path: Path, rows: list[dict[str, str]], y_true: np.ndarray, y_pred: np.ndarray) -> None:
    fieldnames = ["case_id"]
    for target in TARGETS:
        fieldnames.extend([f"true_{target}", f"pred_{target}", f"abs_error_{target}", f"rel_error_{target}"])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row_index, row in enumerate(rows):
            output: dict[str, object] = {"case_id": row.get("case_id", "")}
            for target_index, target in enumerate(TARGETS):
                true_value = float(y_true[row_index, target_index])
                pred_value = float(y_pred[row_index, target_index])
                output[f"true_{target}"] = true_value
                output[f"pred_{target}"] = pred_value
                output[f"abs_error_{target}"] = abs(pred_value - true_value)
                output[f"rel_error_{target}"] = abs(pred_value - true_value) / abs(true_value) if abs(true_value) > 1e-30 else ""
            writer.writerow(output)


def parse_hidden(text: str) -> list[int]:
    values = [int(item.strip()) for item in text.split(",") if item.strip()]
    if not values or any(value <= 0 for value in values):
        raise argparse.ArgumentTypeError("--hidden must be a comma-separated list of positive integers")
    return values


def make_model(nn, input_dim: int, output_dim: int, hidden: list[int], dropout: float):
    layers = []
    previous = input_dim
    for width in hidden:
        layers.append(nn.Linear(previous, width))
        layers.append(nn.SiLU())
        layers.append(nn.LayerNorm(width))
        if dropout > 0.0:
            layers.append(nn.Dropout(dropout))
        previous = width
    layers.append(nn.Linear(previous, output_dim))
    return nn.Sequential(*layers)


def predict_numpy(torch, model, x: np.ndarray, batch_size: int, device: str) -> np.ndarray:
    model.eval()
    predictions = []
    with torch.no_grad():
        for start in range(0, len(x), batch_size):
            batch = torch.as_tensor(x[start : start + batch_size], dtype=torch.float32, device=device)
            predictions.append(model(batch).cpu().numpy())
    return np.vstack(predictions)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a PyTorch MLP FEM surrogate.")
    parser.add_argument("--input", default="results/fem_sampling/fem_training_dataset_80000_voxel100um.csv")
    parser.add_argument("--out-dir", default="results/fem_surrogate_80000_voxel100um_torch")
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--val-fraction", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--hidden", type=parse_hidden, default=parse_hidden("256,256,128"))
    parser.add_argument("--dropout", type=float, default=0.02)
    parser.add_argument("--epochs", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=2048)
    parser.add_argument("--lr", type=float, default=2.0e-3)
    parser.add_argument("--weight-decay", type=float, default=1.0e-5)
    parser.add_argument("--patience", type=int, default=40)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    if args.epochs <= 0:
        raise SystemExit("--epochs must be positive")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if not 0.0 <= args.dropout < 1.0:
        raise SystemExit("--dropout must be in [0, 1)")

    torch, nn, DataLoader, TensorDataset = import_torch()
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    if args.device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    elif args.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA was requested but torch.cuda.is_available() is false")
    else:
        device = args.device

    rows = read_rows(Path(args.input))
    train_rows, val_rows, test_rows = split_rows(rows, args.test_fraction, args.val_fraction, args.seed)
    vocab = categorical_vocab(train_rows)

    x_train_raw = build_x(train_rows, vocab)
    x_val_raw = build_x(val_rows, vocab)
    x_test_raw = build_x(test_rows, vocab)
    x_mean, x_scale = fit_standardizer(x_train_raw)
    x_train = (x_train_raw - x_mean) / x_scale
    x_val = (x_val_raw - x_mean) / x_scale
    x_test = (x_test_raw - x_mean) / x_scale

    y_train_raw = raw_targets(train_rows)
    y_val_raw = raw_targets(val_rows)
    y_test_raw = raw_targets(test_rows)
    y_train_t = transform_y(y_train_raw)
    y_val_t = transform_y(y_val_raw)
    y_mean, y_scale = fit_standardizer(y_train_t)
    y_train = (y_train_t - y_mean) / y_scale
    y_val = (y_val_t - y_mean) / y_scale

    train_dataset = TensorDataset(
        torch.as_tensor(x_train, dtype=torch.float32),
        torch.as_tensor(y_train, dtype=torch.float32),
    )
    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.num_workers,
        pin_memory=device == "cuda",
    )

    model = make_model(nn, x_train.shape[1], len(TARGETS), args.hidden, args.dropout).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    loss_fn = nn.MSELoss()

    x_val_tensor = torch.as_tensor(x_val, dtype=torch.float32, device=device)
    y_val_tensor = torch.as_tensor(y_val, dtype=torch.float32, device=device)
    best_state = None
    best_val_loss = float("inf")
    best_epoch = 0
    no_improve = 0
    started = time.time()

    print(f"Input rows: {len(rows)}")
    print(f"Train rows: {len(train_rows)}")
    print(f"Validation rows: {len(val_rows)}")
    print(f"Test rows: {len(test_rows)}")
    print(f"Device: {device}")
    print(f"Hidden: {args.hidden}")

    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss_sum = 0.0
        train_count = 0
        for x_batch, y_batch in train_loader:
            x_batch = x_batch.to(device, non_blocking=True)
            y_batch = y_batch.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(x_batch), y_batch)
            loss.backward()
            optimizer.step()
            train_loss_sum += float(loss.detach().cpu()) * len(x_batch)
            train_count += len(x_batch)

        model.eval()
        with torch.no_grad():
            val_loss = float(loss_fn(model(x_val_tensor), y_val_tensor).detach().cpu())
        train_loss = train_loss_sum / max(train_count, 1)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_epoch = epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1

        if args.progress_every and (epoch == 1 or epoch % args.progress_every == 0):
            elapsed = time.time() - started
            print(
                f"epoch={epoch} train_loss={train_loss:.6g} val_loss={val_loss:.6g} "
                f"best_val={best_val_loss:.6g} best_epoch={best_epoch} elapsed={elapsed:.1f}s"
            )

        if no_improve >= args.patience:
            print(f"Early stopping at epoch {epoch}; best epoch {best_epoch}")
            break

    if best_state is None:
        raise SystemExit("Training failed before producing a model state")
    model.load_state_dict(best_state)

    pred_train_t = predict_numpy(torch, model, x_train, args.batch_size, device) * y_scale + y_mean
    pred_test_t = predict_numpy(torch, model, x_test, args.batch_size, device) * y_scale + y_mean
    pred_train = inverse_y(pred_train_t)
    pred_test = inverse_y(pred_test_t)
    train_m = metrics(y_train_raw, pred_train)
    test_m = metrics(y_test_raw, pred_test)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "fem_surrogate_torch.pt"
    metadata_path = out_dir / "preprocess.json"
    metrics_path = out_dir / "metrics.csv"
    test_predictions_path = out_dir / "test_predictions.csv"
    train_predictions_path = out_dir / "train_predictions.csv"

    torch.save(
        {
            "model_type": "mlp_onehot_multioutput",
            "state_dict": best_state,
            "input_dim": int(x_train.shape[1]),
            "output_dim": len(TARGETS),
            "hidden": args.hidden,
            "dropout": args.dropout,
            "targets": TARGETS,
            "log_targets": sorted(LOG_TARGETS),
            "numeric_features": NUMERIC_FEATURES,
            "categorical_features": CATEGORICAL_FEATURES,
            "categorical_vocab": vocab,
            "x_mean": x_mean.tolist(),
            "x_scale": x_scale.tolist(),
            "y_mean": y_mean.tolist(),
            "y_scale": y_scale.tolist(),
            "best_epoch": best_epoch,
            "best_val_loss": best_val_loss,
        },
        model_path,
    )
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "model": str(model_path),
                "input": args.input,
                "train_rows": len(train_rows),
                "validation_rows": len(val_rows),
                "test_rows": len(test_rows),
                "device": device,
                "best_epoch": best_epoch,
                "best_val_loss": best_val_loss,
                "numeric_features": NUMERIC_FEATURES,
                "categorical_features": CATEGORICAL_FEATURES,
                "categorical_vocab": vocab,
                "targets": TARGETS,
                "log_targets": sorted(LOG_TARGETS),
            },
            f,
            indent=2,
            sort_keys=True,
        )
    write_metrics(metrics_path, train_m, test_m)
    write_predictions(test_predictions_path, test_rows, y_test_raw, pred_test)
    write_predictions(train_predictions_path, train_rows, y_train_raw, pred_train)

    print(f"Best epoch: {best_epoch}")
    print(f"Model: {model_path}")
    print(f"Metadata: {metadata_path}")
    print(f"Metrics: {metrics_path}")
    print(f"Test predictions: {test_predictions_path}")
    print()
    for index, target in enumerate(TARGETS):
        print(
            f"{target}: "
            f"test_r2={test_m['r2'][index]:.4g}, "
            f"test_mape={test_m['mape_percent'][index]:.3g}%"
        )


if __name__ == "__main__":
    main()
