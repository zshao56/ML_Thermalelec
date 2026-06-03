# ML_Thermalelec

ML_Thermalelec 是一个用于构建 3D 打印骨架 + 热电薄膜 coating 元胞数据库的研究仓库。当前模型聚焦于“圆环-支柱”元胞：上下圆环之间由多根 coated 支柱连接，通过几何描述符、材料描述符和工况描述符估算元胞的热学与电学性质。

English version: [README.md](README.md)

## 当前设计空间

第一版数据库采用以下离散变量：

| 类别 | 变量 |
| --- | --- |
| 圆环几何 | `R_out = 2 mm`，`t_ring = 0.1/0.2/0.3 mm`，`ratio_hole = 0/0.25/0.5/0.75` |
| 高度 | `H_total = 10 mm`，`H_uc = 1/2.5/5/10 mm`，`N_layer = 10/4/2/1` |
| 支柱截面 | 圆柱、方柱、五角柱、六角柱 |
| 支柱尺寸 | `size1 = 0.2/0.3/0.4/0.5 mm` |
| 支柱数量 | `num = 5/10/15/20` |
| 层间错位 | `0`，`n/5`，`2n/5` |
| 连接路径 | 直线、单折线、圆弧、正弦波、螺旋缠绕、贝塞尔曲线 |
| coating 材料 | Bi2Te3 n 型，Sb2Te3 p 型 |
| coating 厚度 | `500/1000/1500/2000 nm` |

完整笛卡尔组合数据量为：

```text
3 * 4 * 4 * 4 * 4 * 4 * 3 * 2 * 4 * 6 = 442,368 条元胞样本
```

## 仓库结构

```text
assets/
  研究笔记、模型文档、框架图、参考文献和 STL 示例。
assets/stl_connection_modes/
  6 种连接路径的 STL 示例。
data/
  生成数据库的位置。SQLite 文件不进入 Git。
scripts/
  数据库生成脚本和 STL 生成脚本。
```

## 生成数据库

生成后的 SQLite 数据库体积较大，因此不直接提交到 GitHub。可以在本地运行：

```bash
python3 scripts/build_unit_cell_database.py --overwrite
```

默认输出为：

```text
data/unit_cell_design_space.sqlite
```

## 生成 STL 示例

6 个默认元胞 STL 文件可以用下面命令重新生成：

```bash
python3 scripts/generate_connection_stls.py
```

默认输出为：

```text
assets/stl_connection_modes/
```

## 主要解析估算结果

数据库生成脚本会计算第一版筛选用的描述符和估算结果，包括：

```text
V_scaffold
A_surface_uc
kappa_uc_est
N_square_eff_est
R_sheet
R_coat_uc_est
R_uc_est
geometry_valid
invalid_reason
```

这些结果适合用于数据库构建、设计空间整理和初筛。后续高精度结论仍建议对代表性样本进行 FEM 或实验验证。

