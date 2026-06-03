# 简化热阻模型：面向不同工况的最大温差预测

> 第一阶段目标：不追求完整热电输出，也不先做复杂数据库。先用少量参数建立清晰物理图像，预测不同工况下器件两端能够维持的最大温差 `DeltaT_device_max`。

## 1. 核心物理图像

当前问题可以先简化成一维热阻网络：

```text
热源 / 热端环境
    |
R_hot_ext
    |
器件热端表面
    |
R_TE
    |
器件冷端表面
    |
R_cold_ext
    |
冷端环境
```

其中最核心的量只有一个：

```text
R_TE = 器件等效热阻
```

器件实际温差由热流和器件热阻决定：

```text
DeltaT_device = Q * R_TE
```

所以第一阶段的任务可以表述为：

```text
给定工况边界和一个设计的等效热阻 R_TE，预测该设计最多能在器件两端保留多少温差。
```

## 2. 参数精简原则

完整数据库里有很多参数，例如 lattice type、cell size、strut diameter、coating thickness、材料电导率、Seebeck 系数、接触电阻等。

如果当前只预测最大温差，可以暂时只保留热学相关参数。

### 2.1 必须保留的设计参数

| 参数 | 符号 | 单位 | 说明 |
| :--- | :--- | :--- | :--- |
| 器件投影面积 | `A_device` | m^2 | 热流通过面积 |
| 器件厚度 | `L_device` | m | 热流方向长度 |
| 等效热导率 | `kappa_eff` | W/(m K) | 把骨架、coating、空气孔隙统一压缩成一个等效热导率 |
| 器件最高允许温度 | `T_max_allowed` | K | 由 coating 或基底耐温决定 |

由此计算：

```text
R_TE = L_device / (kappa_eff * A_device)
K_TE = 1 / R_TE
```

### 2.2 必须保留的工况参数

| 参数 | 符号 | 单位 | 说明 |
| :--- | :--- | :--- | :--- |
| 工况类型 | `boundary_type` | - | fixed_T, fixed_q, convection, mixed |
| 热端表面温度 | `T_hot_surface` | K | 若热端直接接触恒温热源 |
| 热端环境温度 | `T_hot_env` | K | 若热端通过对流换热 |
| 冷端表面温度 | `T_cold_surface` | K | 若冷端直接恒温 |
| 冷端环境温度 | `T_cold_env` | K | 若冷端对流散热 |
| 输入热流密度 | `q_hot` | W/m^2 | 若热端给定热流 |
| 热端换热系数 | `h_hot` | W/(m^2 K) | 热端对流 |
| 冷端换热系数 | `h_cold` | W/(m^2 K) | 冷端对流 |

由此计算：

```text
R_hot_ext = 1 / (h_hot * A_device)
R_cold_ext = 1 / (h_cold * A_device)
```

如果某一侧是固定表面温度，则该侧外部热阻可以不参与计算。

## 3. 暂时删除或降级的参数

第一阶段可以先不进入主模型：

```text
Seebeck coefficient S
electrical conductivity sigma
electrical resistance R_e
P_max
eta_max
N_pair / N_series / N_parallel
contact electrical resistance
mechanical properties
```

这些参数对发电性能重要，但对“能维持多少温差”的第一阶热学图像不是必须。

以下结构参数也可以先降级为用于生成 `kappa_eff` 的辅助信息：

```text
lattice_type
cell_size
strut_diameter
porosity
A_surface
t_coating
coverage_ratio
```

也就是说，第一阶段不直接用这些参数预测温差，而是先把它们折算成：

```text
kappa_eff
```

## 4. 设计到等效热导率的简化映射

### 4.1 最简单版本：直接给定 kappa_eff

如果有仿真、实验或文献估计，直接为每个设计记录：

```text
kappa_eff
```

这是最稳的第一阶段做法。

### 4.2 代理版本：由体积分数估算 kappa_eff

如果暂时没有 `kappa_eff`，可以使用简化混合模型：

```text
kappa_eff =
  g_scaffold * f_scaffold * kappa_scaffold
  + g_coat * f_coat * kappa_coat
  + g_void * f_void * kappa_void
```

其中：

```text
f_scaffold + f_coat + f_void = 1
```

第一阶段可以先取：

```text
g_scaffold = 1
g_coat = 1
g_void = 1
```

后续如果发现与仿真/实验偏差较大，再用几何因子修正。

薄 coating 体积分数可以近似为：

```text
V_coat = A_surface * t_coating * coverage_ratio
f_coat = V_coat / (A_device * L_device)
```

骨架体积分数：

```text
f_scaffold = 1 - porosity
```

空隙体积分数：

```text
f_void = 1 - f_scaffold - f_coat
```

## 5. 不同工况下的温差计算

### 5.1 两侧固定表面温度 fixed_T

如果热端和冷端表面温度都被直接固定：

```text
T_hot_device = T_hot_surface
T_cold_device = T_cold_surface
DeltaT_device = T_hot_surface - T_cold_surface
```

这个工况下，温差由外部边界直接给定，结构不会改变 `DeltaT_device`，只会改变热流：

```text
Q = DeltaT_device / R_TE
```

所以 fixed_T 更适合作为实验对标，而不是体现结构保温能力的主场景。

### 5.2 热端固定表面温度 + 冷端对流

适合：

```text
wearable TEG
pipe waste heat
industrial fixed hot surface
```

计算：

```text
R_cold_ext = 1 / (h_cold * A_device)
Q = (T_hot_surface - T_cold_env) / (R_TE + R_cold_ext)
DeltaT_device = Q * R_TE
```

等价写法：

```text
DeltaT_device =
  (T_hot_surface - T_cold_env)
  * R_TE / (R_TE + R_cold_ext)
```

结论：

```text
R_TE 越大，DeltaT_device 越接近外部源温差；
但 R_TE 越大，进入器件的热流 Q 越小。
```

如果当前只追求最大温差，最优方向是提高 `R_TE`，也就是：

```text
降低 kappa_eff
增加 L_device
减小有效导热截面
提高孔隙率
```

### 5.3 双侧对流

适合：

```text
热端不是直接接触恒温表面，而是由热空气、热流体或环境换热提供热量。
```

计算：

```text
R_hot_ext = 1 / (h_hot * A_device)
R_cold_ext = 1 / (h_cold * A_device)

Q =
  (T_hot_env - T_cold_env)
  / (R_hot_ext + R_TE + R_cold_ext)

DeltaT_device = Q * R_TE
```

等价写法：

```text
DeltaT_device =
  (T_hot_env - T_cold_env)
  * R_TE / (R_hot_ext + R_TE + R_cold_ext)
```

这个工况下，器件温差由三个热阻分配决定。`R_TE` 越大，器件分到的温差比例越高。

温差保持比例：

```text
DeltaT_retention =
  DeltaT_device / (T_hot_env - T_cold_env)
```

即：

```text
DeltaT_retention =
  R_TE / (R_hot_ext + R_TE + R_cold_ext)
```

### 5.4 固定热流 + 冷端对流

适合：

```text
Laptop CPU/GPU
固定功率芯片
固定热流密度热源
```

计算：

```text
Q = q_hot * A_device
R_cold_ext = 1 / (h_cold * A_device)

DeltaT_device = Q * R_TE
T_cold_device = T_cold_env + Q * R_cold_ext
T_hot_device = T_cold_device + DeltaT_device
```

即：

```text
DeltaT_device = q_hot * A_device * R_TE
```

代入：

```text
R_TE = L_device / (kappa_eff * A_device)
```

可得：

```text
DeltaT_device = q_hot * L_device / kappa_eff
```

这个结果很重要：在固定热流密度下，如果忽略边缘效应，温差与面积无关，主要由：

```text
q_hot
L_device
kappa_eff
```

决定。

但必须检查温度上限：

```text
T_hot_device <= T_max_allowed
```

如果超过，则该设计在该工况下不可用：

```text
result_valid = false
invalid_reason = temperature_limit_exceeded
```

## 6. 最大温差定义

第一阶段建议定义两个温差结果。

### 6.1 实际器件温差

```text
DeltaT_device
```

表示在给定工况下，模型预测器件热端与冷端之间实际维持的温差。

### 6.2 有效最大温差

```text
DeltaT_device_max
```

定义为满足温度上限约束时的最大可用温差：

```text
DeltaT_device_max = DeltaT_device
```

如果计算得到：

```text
T_hot_device > T_max_allowed
```

则需要截断或标记无效。

固定热流场景下，可用温差上限为：

```text
DeltaT_allowed = T_max_allowed - T_cold_device
DeltaT_device_max = min(DeltaT_device, DeltaT_allowed)
```

并记录：

```text
temperature_limited = true / false
```

## 7. 精简数据库字段

### 7.1 design_case 最小字段

| 字段 | 单位 | 说明 |
| :--- | :--- | :--- |
| `design_id` | - | 设计编号 |
| `geometry_family` | - | 例如 classic_truss, tpms_network, hybrid_lattice |
| `geometry_subtype` | - | 例如 FCC, Gyroid_network, cube_node_four_helical_struts |
| `A_device` | m^2 | 投影面积 |
| `L_device` | m | 热流方向厚度 |
| `kappa_eff` | W/(m K) | 等效热导率 |
| `T_max_allowed` | K | 最高允许温度 |
| `R_TE` | K/W | 可由公式计算 |

如果 `kappa_eff` 还没有直接数据，可以额外保留：

```text
porosity
t_coating
A_surface
kappa_scaffold
kappa_coating
kappa_void
```

但这些只作为计算 `kappa_eff` 的辅助字段。

### 7.2 scenario_case 最小字段

| 字段 | 单位 | 说明 |
| :--- | :--- | :--- |
| `scenario_id` | - | 工况编号 |
| `scenario_name` | - | Wearable, Pipe, Industrial, Laptop 等 |
| `boundary_type` | - | fixed_T, fixed_hot_surface_cold_convection, double_convection, fixed_q_cold_convection |
| `T_hot_surface` | K | 热端固定表面温度，可空 |
| `T_hot_env` | K | 热端环境温度，可空 |
| `T_cold_surface` | K | 冷端固定表面温度，可空 |
| `T_cold_env` | K | 冷端环境温度，可空 |
| `q_hot` | W/m^2 | 输入热流密度，可空 |
| `h_hot` | W/(m^2 K) | 热端换热系数，可空 |
| `h_cold` | W/(m^2 K) | 冷端换热系数，可空 |

### 7.3 result 最小字段

| 字段 | 单位 | 说明 |
| :--- | :--- | :--- |
| `R_TE` | K/W | 器件热阻 |
| `R_hot_ext` | K/W | 热端外部热阻 |
| `R_cold_ext` | K/W | 冷端外部热阻 |
| `Q` | W | 通过器件的热流 |
| `T_hot_device` | K | 器件热端温度 |
| `T_cold_device` | K | 器件冷端温度 |
| `DeltaT_device` | K | 实际器件温差 |
| `DeltaT_device_max` | K | 满足温度约束的最大可用温差 |
| `DeltaT_retention` | - | 温差保持比例 |
| `temperature_limited` | boolean | 是否受最高温度约束限制 |
| `result_valid` | boolean | 该工况下结果是否可用 |
| `invalid_reason` | text | 无效原因 |

## 8. 当前四类工况的简化表达

### 8.1 Wearable TEG

```text
boundary_type = fixed_hot_surface_cold_convection
T_hot_surface = 303.15-308.15 K
T_cold_env = 293.15-298.15 K
h_cold = 5 or 15 W/(m^2 K)
```

计算：

```text
DeltaT_device =
  (T_hot_surface - T_cold_env)
  * R_TE / (R_TE + R_cold_ext)
```

### 8.2 Pipe_car_waste_heat

```text
boundary_type = fixed_hot_surface_cold_convection
T_hot_surface = 393.15-433.15 K
T_cold_env = 293.15-313.15 K
h_cold = 10 or 100 W/(m^2 K)
```

计算同 wearable。

### 8.3 Industrial_waste_heat

```text
boundary_type = fixed_hot_surface_cold_convection
T_hot_surface = 453.15-493.15 K
T_cold_env = 303.15-323.15 K
h_cold = 15 W/(m^2 K)
```

计算同 wearable。

### 8.4 Laptop_CPU/GPU

```text
boundary_type = fixed_q_cold_convection
q_hot = 10000 W/m^2
T_cold_env = 303.15-313.15 K
h_cold = 100 W/(m^2 K)
T_hot_surface_limit = 313.15-333.15 K, to be confirmed
```

计算：

```text
DeltaT_device = q_hot * L_device / kappa_eff
T_cold_device = T_cold_env + q_hot / h_cold
T_hot_device = T_cold_device + DeltaT_device
```

并检查：

```text
T_hot_device <= T_max_allowed
```

## 9. 第一阶段建议只收集的数据

为了快速跑通最大温差预测，建议第一版数据库只收集：

```text
design:
  design_id
  geometry_family
  geometry_subtype
  A_device
  L_device
  kappa_eff
  T_max_allowed

scenario:
  scenario_id
  scenario_name
  boundary_type
  T_hot_surface
  T_hot_env
  T_cold_surface
  T_cold_env
  q_hot
  h_hot
  h_cold

result:
  R_TE
  Q
  T_hot_device
  T_cold_device
  DeltaT_device
  DeltaT_device_max
  DeltaT_retention
  temperature_limited
  result_valid
```

其他参数全部放入第二阶段。

## 10. 物理解释

这个简化模型想表达的物理图像是：

```text
3D 骨架 + coating 的复杂结构
    ↓
被压缩成一个等效热导率 kappa_eff
    ↓
由 A_device 和 L_device 得到器件热阻 R_TE
    ↓
在不同外部工况下，热阻网络决定实际器件温差
```

因此第一阶段最重要的数据库质量不在于收集很多电学参数，而在于获得可信的：

```text
kappa_eff
```

以及清晰的工况边界：

```text
T_hot_surface / T_hot_env
T_cold_surface / T_cold_env
q_hot
h_hot / h_cold
```

只要这两部分可靠，就可以先建立一个可解释、可计算的最大温差预测数据库。

