#!/usr/bin/env python3
"""Generate a Chinese Markdown/HTML report for the FEM-surrogate workflow."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path


DEFAULT_OUT_DIR = "results/report"
DEFAULT_SCENARIO_CONFIG = "configs/design_advisor_scenarios.csv"
DEFAULT_DB = "data/unit_cell_design_space.sqlite"

TREE_METRICS = "results/fem_surrogate_80000_voxel100um_sklearn/metrics.csv"
TORCH_METRICS = "results/fem_surrogate_80000_voxel100um_torch/metrics.csv"
FEM_AUDIT = "results/fem_sampling/audit_80000_voxel100um/summary.txt"
FEM_TRAINING_SUMMARY = "results/fem_sampling/fem_training_summary_80000_voxel100um.txt"
INTRINSIC_AUDIT = "results/intrinsic_audit/summary.txt"

DISPLAY_TARGETS = [
    "kappa_eff_fem_w_mk",
    "r_e_fem_ohm",
    "alpha_eff_fem_v_k",
    "p_max_coeff_fem_w_k2",
    "p_area_coeff_fem_w_m2_k2",
]

SCENARIO_LABELS = {
    "pipe_static": "管道静态散热工况",
    "pipe_active": "管道主动散热工况",
    "industrial": "工业余热工况",
}


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_summary(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in read_text(path).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def parse_float(value: object, default: float = float("nan")) -> float:
    try:
        parsed = float(str(value))
    except Exception:
        return default
    return parsed if math.isfinite(parsed) else default


def fmt_float(value: object, digits: int = 4) -> str:
    parsed = parse_float(value)
    if not math.isfinite(parsed):
        return ""
    abs_value = abs(parsed)
    if abs_value != 0.0 and (abs_value < 1e-3 or abs_value >= 1e4):
        return f"{parsed:.{digits}e}"
    return f"{parsed:.{digits}g}"


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(lines)


def db_counts(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM unit_cell_designs").fetchone()[0]
            valid = conn.execute("SELECT COUNT(*) FROM unit_cell_designs WHERE geometry_valid = 1").fetchone()[0]
            invalid = conn.execute("SELECT COUNT(*) FROM unit_cell_designs WHERE geometry_valid = 0").fetchone()[0]
    except Exception:
        return {}
    return {"total": int(total), "valid": int(valid), "invalid": int(invalid)}


def metric_map(path: Path) -> dict[str, dict[str, str]]:
    return {row.get("target", ""): row for row in read_csv(path)}


def model_metric_rows(tree_path: Path, torch_path: Path) -> list[list[str]]:
    tree = metric_map(tree_path)
    torch = metric_map(torch_path)
    rows: list[list[str]] = []
    for target in DISPLAY_TARGETS:
        t_row = tree.get(target, {})
        n_row = torch.get(target, {})
        rows.append(
            [
                target,
                fmt_float(t_row.get("test_mape_percent", ""), 3),
                fmt_float(t_row.get("test_r2", ""), 4),
                fmt_float(n_row.get("test_mape_percent", ""), 3),
                fmt_float(n_row.get("test_r2", ""), 4),
            ]
        )
    return rows


def scenario_key(run_name: str) -> str:
    for key in SCENARIO_LABELS:
        if key in run_name:
            return key
    return run_name


def tree_run_from_torch(run_name: str) -> str:
    return run_name.removeprefix("torch_")


def best_row(run_dir: Path) -> dict[str, str]:
    rows = read_csv(run_dir / "final_recommendations.csv")
    return rows[0] if rows else {}


def scenario_rows(config_path: Path, advisor_root: Path) -> list[list[str]]:
    config_rows = [row for row in read_csv(config_path) if row.get("enabled", "").strip() in {"1", "true", "yes"}]
    output: list[list[str]] = []
    for row in config_rows:
        torch_run = row["run_name"]
        tree_run = tree_run_from_torch(torch_run)
        label = SCENARIO_LABELS.get(scenario_key(torch_run), torch_run)
        tree_best = best_row(advisor_root / tree_run)
        torch_best = best_row(advisor_root / torch_run)
        tree_score = parse_float(tree_best.get("score", ""))
        torch_score = parse_float(torch_best.get("score", ""))
        improvement = ""
        if math.isfinite(tree_score) and tree_score != 0.0 and math.isfinite(torch_score):
            improvement = f"{(torch_score / tree_score - 1.0) * 100.0:.2f}%"
        output.append(
            [
                label,
                f"{row.get('t_hot_k', '')}/{row.get('t_cold_k', '')}",
                row.get("h_c_w_m2k", ""),
                tree_best.get("case_id", "未找到"),
                fmt_float(tree_score),
                torch_best.get("case_id", "未找到"),
                fmt_float(torch_score),
                improvement,
            ]
        )
    return output


def top_candidates_table(run_dir: Path, limit: int) -> str:
    rows = read_csv(run_dir / "final_recommendations.csv")[:limit]
    table_rows = []
    for row in rows:
        table_rows.append(
            [
                row.get("final_rank", ""),
                row.get("case_id", ""),
                row.get("material_name", ""),
                row.get("carrier_type", ""),
                row.get("column_type", ""),
                row.get("path_type", ""),
                fmt_float(row.get("score", "")),
                fmt_float(row.get("fem_kappa_eff_fem_w_mk", "")),
                fmt_float(row.get("fem_r_e_fem_ohm", "")),
            ]
        )
    return markdown_table(
        ["rank", "case_id", "材料", "载流子", "柱类型", "路径", "P_area(W/m2)", "kappa", "R_e(ohm)"],
        table_rows,
    )


def stl_list(run_dir: Path) -> list[str]:
    return [str(path) for path in sorted(run_dir.glob("stl_top*/*.stl"))]


def scenario_detail_sections(config_path: Path, advisor_root: Path, top_n: int) -> str:
    parts: list[str] = []
    config_rows = [row for row in read_csv(config_path) if row.get("enabled", "").strip() in {"1", "true", "yes"}]
    for row in config_rows:
        run_name = row["run_name"]
        run_dir = advisor_root / run_name
        label = SCENARIO_LABELS.get(scenario_key(run_name), run_name)
        parts.append(f"### {label}：神经网络推荐 Top {top_n}")
        parts.append(
            f"- 工况：`T_hot={row.get('t_hot_k')} K`，`T_cold={row.get('t_cold_k')} K`，"
            f"`h_c={row.get('h_c_w_m2k')} W/m2K`"
        )
        table = top_candidates_table(run_dir, top_n)
        parts.append(table if table else "未找到 final_recommendations.csv。")
        stls = stl_list(run_dir)
        if stls:
            parts.append("\nSTL 文件：")
            parts.extend(f"- `{path}`" for path in stls[:top_n])
        else:
            parts.append("\nSTL 文件：未找到，请确认是否已运行 STL 导出。")
        parts.append("")
    return "\n".join(parts)


def markdown_to_html(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    body: list[str] = []
    in_table = False
    in_list = False
    in_code = False

    def close_blocks() -> None:
        nonlocal in_table, in_list
        if in_table:
            body.append("</tbody></table>")
            in_table = False
        if in_list:
            body.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                body.append("</code></pre>")
                in_code = False
            else:
                close_blocks()
                body.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            body.append(html.escape(line))
            continue
        if not stripped:
            close_blocks()
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [html.escape(cell.strip()) for cell in stripped.strip("|").split("|")]
            if set(cells) == {"---"}:
                continue
            if not in_table:
                close_blocks()
                body.append("<table><thead><tr>" + "".join(f"<th>{cell}</th>" for cell in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
            continue
        if stripped.startswith("- "):
            if not in_list:
                close_blocks()
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{html.escape(stripped[2:])}</li>")
            continue
        close_blocks()
        if stripped.startswith("### "):
            body.append(f"<h3>{html.escape(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            body.append(f"<h2>{html.escape(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            body.append(f"<h1>{html.escape(stripped[2:])}</h1>")
        else:
            body.append(f"<p>{html.escape(stripped)}</p>")
    close_blocks()

    css = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; line-height: 1.55; color: #202124; }
main { max-width: 1160px; margin: 0 auto; }
table { border-collapse: collapse; width: 100%; margin: 14px 0 24px; font-size: 14px; }
th, td { border: 1px solid #d0d7de; padding: 7px 9px; text-align: left; }
th { background: #f6f8fa; }
code, pre { background: #f6f8fa; border-radius: 6px; }
pre { padding: 12px; overflow-x: auto; }
h1, h2, h3 { line-height: 1.25; }
"""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title><style>{css}</style></head><body><main>"
        + "\n".join(body)
        + "</main></body></html>\n"
    )


def build_markdown(args: argparse.Namespace) -> str:
    out_root = Path(args.advisor_root)
    config_path = Path(args.scenario_config)
    db = db_counts(Path(args.db_path))
    fem_audit = parse_summary(Path(args.fem_audit))
    fem_training = parse_summary(Path(args.fem_training_summary))
    intrinsic_audit = parse_summary(Path(args.intrinsic_audit))

    lines: list[str] = []
    lines.append("# 微结构热电单元机器学习与逆向设计阶段汇报")
    lines.append("")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## 1. 研究目标")
    lines.append("")
    lines.append(
        "本阶段目标是建立一个从单元结构参数到等效热电性能的代理模型，并进一步在给定应用边界条件"
        "（热端温度、冷端温度、对流换热系数等）下进行逆向设计，输出最优可制造结构。"
    )
    lines.append("")
    lines.append("## 2. 方法流程")
    lines.append("")
    lines.extend(
        [
            "- 构建单元结构设计空间，先进行几何合法性筛选。",
            "- 使用快速网络/解析模型生成全库的初始物理特征，用于预筛选和作为机器学习输入特征。",
            "- 使用体素有限体积/FEM 近似求解器对抽样结构生成高保真标签。",
            "- 分别训练树模型和 PyTorch 神经网络模型，预测等效热导、电阻、Seebeck 系数和功率系数。",
            "- 在给定工况下，先由代理模型全库搜索候选结构，再对前若干名进行 FEM 复核，最后输出确认后的推荐结果和 STL 文件。",
        ]
    )
    lines.append("")
    lines.append("## 3. 数据规模与质量检查")
    lines.append("")
    data_rows = [
        ["设计空间总数", db.get("total", "未找到")],
        ["几何有效结构", db.get("valid", "未找到")],
        ["几何无效结构", db.get("invalid", "未找到")],
        ["内禀网络数据行数", intrinsic_audit.get("total_rows", "未找到")],
        ["FEM 结果总数", fem_audit.get("total_rows", "未找到")],
        ["FEM 可用结果", fem_audit.get("usable_rows", "未找到")],
        ["FEM 不可用结果", fem_audit.get("unusable_rows", "未找到")],
        ["训练数据行数", fem_training.get("training_rows", "未找到")],
    ]
    lines.append(markdown_table(["项目", "数值"], data_rows))
    lines.append("")
    lines.append("## 4. 代理模型训练结果")
    lines.append("")
    lines.append("下表比较树模型和神经网络在同一批 80,000 条 FEM 数据上的测试集表现。")
    lines.append("")
    lines.append(
        markdown_table(
            ["预测目标", "树模型 MAPE(%)", "树模型 R2", "神经网络 MAPE(%)", "神经网络 R2"],
            model_metric_rows(Path(args.tree_metrics), Path(args.torch_metrics)),
        )
        or "未找到模型 metrics.csv。"
    )
    lines.append("")
    lines.append(
        "当前结果说明：树模型可以作为快速稳定的全库筛选器；神经网络在连续变量扩展和后续优化中更有优势，"
        "因此后续逆向设计采用“代理模型筛选 + FEM 复核”的策略。"
    )
    lines.append("")
    lines.append("## 5. 不同工况下的逆向设计结果")
    lines.append("")
    lines.append(
        markdown_table(
            ["工况", "T_hot/T_cold(K)", "h_c(W/m2K)", "树模型最佳 case", "树模型 P_area", "神经网络最佳 case", "神经网络 P_area", "提升"],
            scenario_rows(config_path, out_root),
        )
        or "未找到工况推荐结果。"
    )
    lines.append("")
    lines.append("## 6. 神经网络推荐结构明细")
    lines.append("")
    lines.append(scenario_detail_sections(config_path, out_root, args.top_n))
    lines.append("")
    lines.append("## 7. 结果解析")
    lines.append("")
    lines.extend(
        [
            "- 多个工况的最优结构集中在较薄环壁、较大柱尺寸、较多柱数量和 Sb2Te3 p 型材料组合，说明该组合在当前目标函数下更容易获得较高功率密度。",
            "- 工况条件会改变最终排序，因此训练阶段保持场景无关，逆向设计阶段再输入热端、冷端和对流换热系数是合理的。",
            "- 神经网络推荐候选仍需 FEM 复核，最终汇报应以 FEM confirmed 的 `final_recommendations.csv` 为准。",
            "- STL 文件已经按工况导出，可用于后续建模、展示或制造可行性讨论。",
        ]
    )
    lines.append("")
    lines.append("## 8. 后续工作")
    lines.append("")
    lines.extend(
        [
            "- 对最终 Top 结构进行更高精度网格或商业 FEM 软件复核。",
            "- 加入机械强度、制造约束和材料成本等多目标约束。",
            "- 将连续变量范围扩展到更细的参数空间，用神经网络进行连续优化。",
            "- 对推荐 STL 进行实际打印/加工前的几何检查和尺寸公差评估。",
        ]
    )
    return "\n".join(lines).replace("\n\n\n", "\n\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Markdown and HTML report for teacher presentation.")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--advisor-root", default="results/design_advisor")
    parser.add_argument("--scenario-config", default=DEFAULT_SCENARIO_CONFIG)
    parser.add_argument("--db-path", default=DEFAULT_DB)
    parser.add_argument("--tree-metrics", default=TREE_METRICS)
    parser.add_argument("--torch-metrics", default=TORCH_METRICS)
    parser.add_argument("--fem-audit", default=FEM_AUDIT)
    parser.add_argument("--fem-training-summary", default=FEM_TRAINING_SUMMARY)
    parser.add_argument("--intrinsic-audit", default=INTRINSIC_AUDIT)
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = build_markdown(args)
    md_path = out_dir / "teacher_report.md"
    html_path = out_dir / "teacher_report.html"
    md_path.write_text(markdown + "\n", encoding="utf-8")
    html_path.write_text(markdown_to_html(markdown, "微结构热电单元机器学习与逆向设计阶段汇报"), encoding="utf-8")

    manifest = {
        "markdown": str(md_path),
        "html": str(html_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Markdown report: {md_path}")
    print(f"HTML report: {html_path}")


if __name__ == "__main__":
    main()
