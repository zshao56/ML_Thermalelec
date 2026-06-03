# 圆环-支柱元胞数据库方案评估

> 目标：评估当前“上下圆环 + 中间若干支柱”的元胞参数化方案是否适合建立数据库，并估算全组合数据点数量。当前阶段仍以元胞为对象，不直接计算完整器件。

## 1. 方案是否可行

总体可行，而且比之前开放式 lattice 变量更适合建数据库。原因是：

```text
1. 上下圆环定义了统一边界，便于施加热端/冷端边界条件。
2. 支柱形状作为中间连接体，能系统控制热阻和 coating 电阻。
3. 总高度固定为 10 mm，元胞高度只决定 z 方向层数，便于比较不同结构。
4. 结构都来自外接圆和参数方程，适合自动 CAD/mesh 生成。
```

当前方案已经能支持以下结果计算：

```text
V_scaffold
A_surface_uc
kappa_uc
N_square_eff 或 electrical_shape_factor
DeltaT_uc
V_oc_uc
R_coat_uc
R_uc
P_max_uc
```

但还需要明确两个建模约定：

```text
1. 元胞高度 H_uc 与圆环厚度 t_ring 的关系。
2. 支柱在圆环上的位置排布规则。
```

## 2. 元胞几何定义

### 2.1 圆环参数

| 参数 | 符号 | 取值 | 单位 |
| :--- | :--- | :--- | :--- |
| 外圆半径 | `R_out` | 2 | mm |
| 圆环厚度 | `t_ring` | 0.1, 0.2, 0.3 | mm |
| 内圆半径比例 | `ratio_hole` | 0, 0.25, 0.5, 0.75 | - |

派生参数：

```text
R_in = ratio_hole * R_out
A_ring = pi * (R_out^2 - R_in^2)
```

注意：

```text
ratio_hole = 0 时，圆环退化为实心圆薄板。
```

### 2.2 元胞高度与层数

总结构高度：

```text
H_total = 10 mm
```

元胞高度：

```text
H_uc = 1, 2.5, 5.0, 10.0 mm
```

层数：

```text
N_layer = H_total / H_uc
```

对应：

| `H_uc` | `N_layer` |
| :--- | :--- |
| 1 mm | 10 |
| 2.5 mm | 4 |
| 5.0 mm | 2 |
| 10.0 mm | 1 |

固定采用“层间共享圆环”的定义：

```text
h_col = H_uc - t_ring
```

也就是说，相邻元胞之间共用一个圆环；数据库中每个元胞只分摊一层圆环厚度。这个定义会影响支柱高度、体积、表面积和电流路径长度，后续 CAD/mesh 生成也按这个约定执行。

## 3. 支柱类型与参数

当前建议第一轮只保留 4 类规则直柱：

```text
圆柱
方柱
五角柱
六角柱
```

这些结构的优点是：

```text
1. 参数少，只有一个截面特征尺寸和支柱数量。
2. 适合直接比较不同截面形状对热阻、电阻和 coating 表面积的影响。
3. CAD/mesh 生成稳定，不容易出现自交、缠绕、碰撞等几何问题。
4. PVD 视线可达性比螺旋、折线、双锥等复杂路径更容易控制。
```

第一轮暂时不展开三角柱、螺旋柱、锥形柱、斜撑柱、变直径圆柱和双锥形柱。它们可以作为第二轮扩展变量。

第一轮支柱类型：

| ID | 支柱类型 | 参数 |
| :--- | :--- | :--- |
| 0 | 圆柱 | `d`, `n` |
| 1 | 方柱 | `a`, `n` |
| 2 | 五角柱 | `D_poly`, `n` |
| 3 | 六角柱 | `D_poly`, `n` |

离散取值：

| 参数 | 取值 |
| :--- | :--- |
| `size1` | 0.2, 0.3, 0.4, 0.5 mm |
| `num` | 5, 10, 15, 20 |

其中：

```text
圆柱: size1 = d
方柱: size1 = a
五角柱: size1 = D_poly, 外接圆直径
六角柱: size1 = D_poly, 外接圆直径
```

第二轮可选扩展类型：

```text
三角柱
螺旋柱
锥形柱
斜撑柱
变直径圆柱
双锥形柱
```

## 4. 连接路径类型

当前连接路径包括：

```text
直线
单折线
圆弧曲线
正弦波浪
螺旋缠绕
贝塞尔曲线
```

这些路径方程可以作为支柱中心线生成器。第一版数据库把 `path_type` 作为离散变量，但每种路径只取一个默认参数。建议数据库字段为：

```text
path_type
path_param_1
path_param_2
path_param_3
path_param_4
```

如果后续要进一步展开路径内部连续参数，则需要先离散化。例如：

```text
单折线: r, delta_x, delta_y
圆弧: curve_c, plane, direction
正弦: amplitude_A, period_N, phase_phi
贝塞尔: r1, delta_x1, delta_y1, r2, delta_x2, delta_y2
```

目前这些内部参数不展开，所以路径只提供 6 倍组合数。

## 5. 支柱位置如何排布

你的直觉“尽可能松散”是合理的，但需要更精确地表述为：

```text
在圆环允许区域内，最大化支柱中心之间的最小距离，同时避免靠近内外圆边界。
```

### 5.1 推荐默认排布：单环均匀角度排布

对 `n` 根支柱，先放在一个半径 `r_place` 的同心圆上：

```text
theta_i = 2*pi*i/n + theta_offset
i = 0, 1, ..., n-1
```

推荐：

```text
r_place = R_in + lambda_r * (R_out - R_in)
lambda_r = 0.5 到 0.75
```

坐标：

```text
x_i = r_place * cos(theta_i)
y_i = r_place * sin(theta_i)
```

如果追求松散排列，可以取：

```text
lambda_r = 0.65 或 0.75
```

因为越靠外，圆周长度越大，支柱之间角向距离越大。

### 5.2 层间上/下位点的错位连接

在一个元胞内，下层圆环上的支柱起点与上层圆环上的终点可以错位连接。错位不是连续角度变量，而是按支柱编号移动若干个单元。

定义：

```text
connection_offset_units = k
theta_bottom_i = 2*pi*i/n
theta_top_i = 2*pi*(i + k)/n
```

第一版采用 3 档错位：

```text
k = 0, n/5, 2n/5
```

也就是：

| `num` | `connection_offset_units` |
| :--- | :--- |
| 5 | 0, 1, 2 |
| 10 | 0, 2, 4 |
| 15 | 0, 3, 6 |
| 20 | 0, 4, 8 |

这个变量反映“下层第 i 个位点连接到上层第 i+k 个位点”。错位越大，支柱中心线越倾斜，路径长度增加，通常会增加热阻与 coating 电阻。

路径长度的一阶估算为：

```text
connection_twist = 2*pi*k/n
connection_chord = 2*r_connection*sin(abs(connection_twist)/2)
L_direct = sqrt(h_col^2 + connection_chord^2)
L_path = L_direct * path_length_factor
```

其中 `r_connection` 由默认排布半径给出；若采用双环排布，则使用内外圈按支柱数量加权后的等效连接半径。

### 5.3 高支柱数量时的双环排布

当：

```text
n = 15 或 20
```

如果单环排布发生碰撞或过密，可以分成两圈：

```text
r_1 = R_in + 0.35 * (R_out - R_in)
r_2 = R_in + 0.75 * (R_out - R_in)
```

两圈角度错位：

```text
theta_offset_outer = theta_offset_inner + pi / n_outer
```

### 5.4 几何有效性约束

每个设计需要检查：

```text
R_in + r_feature + gap_min <= r_place <= R_out - r_feature - gap_min
distance(center_i, center_j) >= 2*r_feature + gap_min
```

建议：

```text
gap_min = 0.05 mm
```

如果不满足，标记：

```text
geometry_valid = false
invalid_reason = collision_or_boundary_violation
```

### 5.5 排布是否影响热电性能

在最简一维模型里，支柱位置不显著改变热阻；热阻主要由：

```text
支柱数量 n
支柱截面积 A
路径长度 L_path
材料热导率 kappa
```

决定。

但在真实 3D 元胞中，位置会影响：

```text
局部热流拥挤
圆环内的横向热扩散路径
PVD coating 可达性
电流路径均匀性
结构碰撞与制造性
```

所以第一版数据库建议不要把“位置排布”作为自由变量，而是使用确定性的松散排布规则。这样可以避免参数爆炸。

## 6. 数据点数量估算

下面按当前离散取值估算。

### 6.1 圆环组合数

```text
t_ring: 3
ratio_hole: 4
```

因此：

```text
N_ring = 3 * 4 = 12
```

### 6.2 高度组合数

```text
H_uc: 4
```

因此：

```text
N_height = 4
```

### 6.3 支柱参数组合数

第一轮只保留圆柱、方柱、五角柱、六角柱。每类支柱只有：

```text
size1: 4
num: 4
```

因此每类：

```text
4 * 4 = 16
```

| 支柱类型 | 组合数 | 说明 |
| :--- | :--- | :--- |
| 圆柱 | 4 * 4 = 16 | `size1 * num` |
| 方柱 | 4 * 4 = 16 | `size1 * num` |
| 五角柱 | 4 * 4 = 16 | `size1 * num` |
| 六角柱 | 4 * 4 = 16 | `size1 * num` |

总支柱组合数：

```text
N_column = 4 column types * 16 = 64
```

### 6.4 层间错位连接组合数

每个 `num` 对应 3 档错位：

```text
connection_offset_units: 3
```

因此：

```text
N_connection = 3
```

### 6.5 几何组合数

不计 path_type 时：

```text
N_geometry = N_ring * N_height * N_column * N_connection
```

当前：

```text
N_geometry = 12 * 4 * 64 * 3 = 9,216
```

### 6.6 加入 coating/material 后

如果每个几何分别计算：

```text
carrier_type: 2, p/n
t_coating: 4, 0.5/1.0/1.5/2.0 um
```

则：

```text
N_material = 2 * 4 = 8
```

总数据点：

```text
N_total = 9,216 * 8 = 73,728
```

### 6.7 如果再加入路径类型

如果 6 种路径类型都只取一个默认参数：

```text
N_total_with_path = 73,728 * 6 = 442,368
```

这个数量在解析公式层面仍然可接受。若每种路径继续加入多个连续参数离散值，数量会继续指数增长。

## 7. 建议的数据库策略

### 7.1 不建议直接全组合高精度仿真

全组合在解析公式层面可以接受，但在 CAD + mesh + FEM 层面会很重。

建议分三层：

```text
Level 1: 解析几何 + 公式计算，全组合可做。
Level 2: 中等精度数值计算，选 5,000-20,000 个代表点。
Level 3: 高精度 FEM/实验验证，选 100-500 个代表点。
```

### 7.2 第一版推荐精简

第一版已经将支柱类型精简为：

```text
圆柱
方柱
五角柱
六角柱
```

同时建议继续固定以下内容：

```text
1. 6 种路径类型都保留，但每种路径先只取一个默认参数。
2. 支柱平面位置不作为自由变量，用松散均匀排布规则自动生成。
3. 层间上/下位点错位作为 3 档离散变量。
4. 三角柱、螺旋柱、锥形柱、斜撑柱、变直径圆柱、双锥形柱放到第二轮。
```

### 7.3 推荐第一轮核心变量

建议第一轮保留：

```text
t_ring: 3
ratio_hole: 4
H_uc: 4
column_type: 4
size1: 4
num: 4
connection_offset_units: 3
t_coating: 4
carrier_type: 2
path_type: 6
```

粗略数据量：

```text
3 * 4 * 4 * 4 * 4 * 4 * 3 * 4 * 2 * 6 = 442,368
```

这个规模用于公式计算非常可行；用于中等精度仿真建议抽样到 5,000-20,000 个代表点。

## 8. 当前方案结论

精简后当前参数方案更可控：

```text
几何点: 9,216
加入 p/n 与 coating 厚度: 73,728
再加入 6 类路径: 442,368
```

因此建议：

```text
1. 平面位置排布不作为自由变量，采用统一松散排布算法。
2. 层间错位连接作为 3 档离散变量。
3. path_type 第一轮保留 6 类，但每类只取默认参数。
4. 全组合可用于解析公式快速计算。
5. FEM/实验仍建议对代表点采样。
```

这样数据库既有覆盖度，又不会一开始就被组合数量压垮。
