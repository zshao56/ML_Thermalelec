# 最小单元胞热-电模型

> 当前阶段只算一种元胞，不预设 p/n 成对。每条数据表示一个 coating 元胞：它可以是 p 型，也可以是 n 型；p 和 n 的骨架、几何、coating 厚度、表面积和电阻都可以不同。

## 1. 元胞定义

一个 `unit_cell` 表示：

```text
一种 3D 骨架元胞 + 一种热电 coating
```

它可以是：

```text
p-cell: scaffold_p + p-type coating
n-cell: scaffold_n + n-type coating
```

但在本模型中不把 p 和 n 强行配对。后续如果需要组成 p-n thermocouple，再在更高层级组合：

```text
p-cell result + n-cell result -> p-n pair result
```

因此，元胞模型只计算：

```text
DeltaT_uc
V_oc_uc
R_uc
P_max_uc
```

## 2. 最少需要提供的参数

### 2.1 热学参数

| 参数 | 符号 | 建议范围 | 单位 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| 元胞类型 | `carrier_type` | p / n | - | 该元胞 coating 的导电类型 |
| 元胞投影面积 | `A_uc` | 2.5e-7 到 2.5e-5 | m^2 | 约等于 `cell_size_x * cell_size_y` |
| 元胞热流方向长度 | `L_uc` | 0.5e-3 到 5e-3 | m | 通常等于单胞高度 |
| 元胞等效热导率 | `kappa_uc` | 0.02 到 1.0 | W/(m K) | 骨架 + coating + 空气的等效热导率 |
| 热端表面温度 | `T_hot_surface` | 303.15 到 493.15 | K | 恒温热端工况 |
| 冷端环境温度 | `T_cold_env` | 293.15 到 323.15 | K | 冷端对流环境 |
| 冷端换热系数 | `h_cold` | 5 到 100 | W/(m^2 K) | 冷端自然/强制对流 |
| 输入热流密度 | `q_hot` | 1000 到 10000 | W/m^2 | 固定热流工况 |
| 最高允许温度 | `T_max_allowed` | 523.15 左右 | K | coating 或基底耐温约束 |

### 2.2 电学基础参数

`R_coat_uc` 不作为输入，而是由更基础的 coating 材料和几何参数计算得到。

| 参数 | 符号 | 建议范围 / 当前值 | 单位 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| 元胞 Seebeck 系数 | `S_uc` | p: 100；n: -155 | uV/K | signed value，p 为正，n 为负 |
| coating 电导率 | `sigma_uc` | p: 1.0e4-2.5e4; n: 3.6e4-4.35e4 | S/m | coating 材料本征电导率 |
| coating 厚度 | `t_coating` | 0.5e-6 到 2.0e-6 | m | PVD coating 厚度 |
| 有效方块数 | `N_square_eff` | 待由几何给出 | - | 表面薄膜电流路径的等效长宽比 |
| coating 覆盖率 | `coverage_ratio` | 0.5 到 1.0 | - | 实际连续导电 coating 覆盖比例 |
| 有效电流路径长度 | `L_elec` | 0.5e-3 到 10e-3 | m | 电流沿 coating 网络从一端到另一端的路径长度 |
| 有效导电宽度 | `w_elec_eff` | 待由几何给出 | m | 截面法计算电阻时使用 |
| 元胞可 coating 表面积 | `A_surface_uc` | 1e-6 到 1e-4 | m^2 | 体积法计算电阻时使用 |
| 电连通因子 | `C_net` | 0.01 到 1.0 | - | coating 网络沿电流方向的连通效率 |
| 元胞接触电阻 | `R_contact_uc` | 0 到 10 | ohm | coating 与电极/连接点的接触电阻 |

实际建库时，优先使用薄膜面电阻法；如果没有 `N_square_eff`，再用截面法或体积法估算：

```text
面电阻法: sigma_uc, t_coating, N_square_eff, coverage_ratio, C_net
截面法: sigma_uc, t_coating, L_elec, w_elec_eff, coverage_ratio
体积法: sigma_uc, t_coating, L_elec, A_surface_uc, coverage_ratio, C_net
```

## 3. 元胞温差计算

### 3.1 元胞热阻

```text
R_th_uc = L_uc / (kappa_uc * A_uc)
```

### 3.2 恒温热端 + 冷端对流

适合 wearable、pipe、industrial 这类热端表面温度已知、冷端靠空气散热的工况。

```text
R_cold_uc = 1 / (h_cold * A_uc)
```

```text
DeltaT_uc =
  (T_hot_surface - T_cold_env)
  * R_th_uc / (R_th_uc + R_cold_uc)
```

代入热阻后：

```text
DeltaT_uc =
  (T_hot_surface - T_cold_env)
  * L_uc / (L_uc + kappa_uc / h_cold)
```

### 3.3 固定热流 + 冷端对流

适合 laptop CPU/GPU 或固定热流密度热源。

```text
DeltaT_uc = q_hot * L_uc / kappa_uc
```

冷端元胞表面温度：

```text
T_cold_uc = T_cold_env + q_hot / h_cold
```

热端元胞表面温度：

```text
T_hot_uc = T_cold_uc + DeltaT_uc
```

温度约束：

```text
T_hot_uc <= T_max_allowed
```

如果超出：

```text
result_valid = false
invalid_reason = temperature_limit_exceeded
```

## 4. 元胞电压计算

Seebeck 系数要先换成 `V/K`：

```text
S_uc_V = S_uc_uV * 1e-6
```

开路电压：

```text
V_oc_uc = S_uc_V * DeltaT_uc
```

因为 n 型 `S_uc` 为负，`V_oc_uc` 也可能为负。用于功率计算时使用平方即可；如果只比较电压幅值，可以记录：

```text
abs_V_oc_uc = abs(V_oc_uc)
```

当前示例：

```text
p-cell: S_uc = 100 uV/K
n-cell: S_uc = -155 uV/K
```

分别计算：

```text
V_oc_p_uc = 100e-6 * DeltaT_p_uc
V_oc_n_uc = -155e-6 * DeltaT_n_uc
```

注意：由于 p 和 n 骨架可以不同，通常：

```text
DeltaT_p_uc != DeltaT_n_uc
R_p_uc != R_n_uc
```

因此不要在元胞模型里强行写成 `S_pair * DeltaT`。

## 5. 元胞电阻计算

由于热电材料是覆盖在骨架表面的薄膜，最自然的写法是先计算 coating 的面电阻：

```text
R_sheet = 1 / (sigma_uc * t_coating)
```

单位是：

```text
ohm per square
```

这一步确实只需要：

```text
sigma_uc
t_coating
```

但 `R_sheet` 还不是整个元胞的总电阻。总电阻还需要知道电流在表面薄膜上等效走过多少个“方块”。

## 5.1 面电阻法：推荐的 coating 表面模型

如果元胞表面的 coating 可以近似为一个连续薄膜网络，则：

```text
R_coat_uc =
  R_sheet * N_square_eff / (coverage_ratio * C_net)
```

也就是：

```text
R_coat_uc =
  N_square_eff
  / (sigma_uc * t_coating * coverage_ratio * C_net)
```

其中：

```text
N_square_eff = 有效方块数，近似等于 L_elec / w_elec_eff
C_net = 表面 coating 网络的连通修正因子
coverage_ratio = 连续 coating 覆盖比例
```

所以，`sigma_uc` 和 `t_coating` 可以确定薄膜本身的导电能力；骨架几何决定 `N_square_eff`。如果我们已经有 CAD/mesh，可以从几何中计算或估计 `N_square_eff`。

最简单的规则结构近似：

```text
N_square_eff = L_elec / w_elec_eff
```

### 5.2 截面法：由有效导电宽度计算

截面法与面电阻法是等价的。基本关系是欧姆定律的几何形式：

```text
R = L / (sigma * A)
```

对 coating 元胞：

```text
R_coat_uc = L_elec / (sigma_uc * A_elec_eff)
```

其中：

```text
L_elec = coating 上的有效电流路径长度
A_elec_eff = coating 的有效导电截面
```

如果可以从 CAD 或几何定义中得到电流方向截面上的 coating 有效宽度 `w_elec_eff`，则：

```text
A_elec_eff =
  w_elec_eff * t_coating * coverage_ratio
```

所以：

```text
R_coat_uc =
  L_elec
  / (sigma_uc * w_elec_eff * t_coating * coverage_ratio)
```

这个方法最直观，适用于直杆、并联杆、通孔阵列、规则框架等结构。

### 5.3 体积法：由 coating 表面积估算

coating 体积：

```text
V_coat_uc = A_surface_uc * t_coating * coverage_ratio
```

有效导电截面近似：

```text
A_elec_eff = C_net * V_coat_uc / L_elec
```

代入得到：

```text
R_coat_uc =
  L_elec^2
  / (C_net * sigma_uc * A_surface_uc * t_coating * coverage_ratio)
```

这个方法适合复杂 3D coating 网络。`C_net` 用来修正并非所有 coating 体积都沿有效电流方向连通的问题。

### 5.4 螺旋/手性杆的更基础估算

如果元胞由螺旋支柱导电，可以把一根螺旋 coating 近似为薄壁圆管导体。

单根螺旋路径长度：

```text
L_helix =
  sqrt(H_uc^2 + (2 * pi * r_helix * n_turn)^2)
```

其中：

```text
H_uc = 元胞高度
r_helix = 螺旋中心线半径
n_turn = 螺旋圈数
```

单根 coated strut 的导电截面：

```text
A_shell_one =
  2 * pi * r_strut * t_coating * coverage_ratio
```

如果有 `n_strut_parallel` 根近似并联的螺旋支柱：

```text
A_elec_eff =
  C_net * n_strut_parallel * A_shell_one
```

则：

```text
R_coat_uc =
  L_helix
  / (sigma_uc * C_net * n_strut_parallel * 2 * pi * r_strut * t_coating * coverage_ratio)
```

总元胞内阻：

```text
R_uc = R_coat_uc + R_contact_uc
```

## 6. 元胞最大功率

当外接负载等于元胞内阻：

```text
R_load_uc = R_uc
```

最大功率：

```text
P_max_uc = V_oc_uc^2 / (4 * R_uc)
```

代入电压：

```text
P_max_uc =
  (S_uc_V * DeltaT_uc)^2
  / (4 * R_uc)
```

这个公式体现了关键折中：

```text
降低 kappa_uc -> DeltaT_uc 增大 -> abs(V_oc_uc) 增大
但如果骨架太细或 coating 太薄 -> R_uc 增大 -> P_max_uc 可能下降
```

## 7. 最小输入与输出

### 7.1 最小输入，推荐版

```text
thermal:
  carrier_type
  L_uc
  A_uc
  kappa_uc
  T_hot_surface or q_hot
  T_cold_env
  h_cold
  T_max_allowed

electrical:
  S_uc
  sigma_uc
  t_coating
  N_square_eff
  coverage_ratio
  C_net
  R_contact_uc
```

### 7.2 最小输入，截面法

如果不能直接给 `N_square_eff`，但能给有效电流路径长度和有效导电宽度：

```text
thermal:
  carrier_type
  L_uc
  A_uc
  kappa_uc
  T_hot_surface or q_hot
  T_cold_env
  h_cold
  T_max_allowed

electrical:
  S_uc
  sigma_uc
  t_coating
  L_elec
  w_elec_eff
  coverage_ratio
  C_net
  R_contact_uc
```

### 7.3 最小输入，体积法

如果很难定义 `w_elec_eff`，则改用 coating 表面积和连通因子：

```text
thermal:
  carrier_type
  L_uc
  A_uc
  kappa_uc
  T_hot_surface or q_hot
  T_cold_env
  h_cold
  T_max_allowed

electrical:
  S_uc
  sigma_uc
  t_coating
  A_surface_uc
  coverage_ratio
  C_net
  L_elec
  R_contact_uc
```

### 7.4 最小输入，螺旋杆法

如果元胞主要由螺旋支柱组成，则可以提供更几何化的参数：

```text
thermal:
  carrier_type
  L_uc
  A_uc
  kappa_uc
  T_hot_surface or q_hot
  T_cold_env
  h_cold
  T_max_allowed

electrical:
  S_uc
  sigma_uc
  t_coating
  coverage_ratio
  H_uc
  r_helix
  n_turn
  r_strut
  n_strut_parallel
  C_net
  R_contact_uc
```

### 7.5 最小输出

```text
DeltaT_uc
T_hot_uc
T_cold_uc
V_oc_uc
abs_V_oc_uc
R_coat_uc
R_uc
P_max_uc
result_valid
invalid_reason
```

## 8. 后续如何组合 p/n

如果后续要计算一个 p-n pair，由于 p 和 n 的骨架可以不同，应分别先算：

```text
p-cell:
  DeltaT_p_uc
  V_oc_p_uc
  R_p_uc

n-cell:
  DeltaT_n_uc
  V_oc_n_uc
  R_n_uc
```

再在 pair 层级组合：

```text
V_pair = V_oc_p_uc - V_oc_n_uc
R_pair = R_p_uc + R_n_uc + R_bridge
P_pair_max = V_pair^2 / (4 * R_pair)
```

这一步不属于当前元胞模型，只是后续扩展。

## 9. 当前阶段一句话模型

```text
一个单元胞的热学结构决定 DeltaT_uc；
该 coating 的 signed Seebeck 决定 V_oc_uc；
coating 网络和接触决定 R_uc；
最终 P_max_uc = V_oc_uc^2 / (4R_uc)。
```
