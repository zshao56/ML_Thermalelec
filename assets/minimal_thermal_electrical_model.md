# 最小热-电模型：由工况预测温差、电压、内阻与最大功率

> 第一阶段建议使用这个模型替代“只最大化温差”的模型。原因是：最大温差主要反映低热导率，但热电器件最终还需要形成有效电回路。结构太细、coating 太薄时，`DeltaT` 可能变大，但电阻 `R_e` 也会变大，导致最大功率下降。

## 1. 核心目标

最终比较设计时，不只看：

```text
DeltaT_device
```

而应至少看：

```text
V_oc
R_e
P_max
```

其中：

```text
P_max = V_oc^2 / (4 * R_e)
```

所以第一阶段最小模型为：

```text
工况 + 热阻  -> DeltaT_device
DeltaT_device + Seebeck -> V_oc
coating 几何 + 电导率 -> R_e
V_oc + R_e -> P_max
```

## 2. 最少需要提供的参数

### 2.1 热学参数

| 参数 | 符号 | 建议范围 | 单位 | 作用 |
| :--- | :--- | :--- | :--- | :--- |
| 器件投影面积 | `A_device` | 4e-6 到 4e-4 | m^2 | 计算热阻和对流热阻 |
| 器件厚度 | `L_device` | 0.5e-3 到 20e-3 | m | 热流方向长度 |
| 等效热导率 | `kappa_eff` | 0.02 到 1.0 | W/(m K) | 描述骨架 + coating + 空气的等效导热能力 |
| 热端表面温度 | `T_hot_surface` | 303.15 到 493.15 | K | 恒温热端工况 |
| 冷端环境温度 | `T_cold_env` | 293.15 到 323.15 | K | 冷端对流工况 |
| 冷端换热系数 | `h_cold` | 5 到 100 | W/(m^2 K) | 冷端散热能力 |
| 输入热流密度 | `q_hot` | 1000 到 10000 | W/m^2 | 固定热流工况 |
| 最高允许温度 | `T_max_allowed` | 523.15 左右 | K | 材料耐温约束 |

### 2.2 电学参数

| 参数 | 符号 | 建议范围 / 当前值 | 单位 | 作用 |
| :--- | :--- | :--- | :--- | :--- |
| p 型 Seebeck 系数 | `S_p` | 当前 100；可扩展 100 到 240 | uV/K | 产生 p-leg 电压 |
| n 型 Seebeck 系数 | `S_n` | 当前 -155；可扩展 -120 到 -200 | uV/K | 产生 n-leg 电压 |
| p 型 coating 电导率 | `sigma_p` | 建议先用 1.0e4 到 2.5e4 | S/m | 计算 p-leg 电阻 |
| n 型 coating 电导率 | `sigma_n` | 建议先用 3.6e4 到 4.35e4 | S/m | 计算 n-leg 电阻 |
| coating 厚度 | `t_coating` | 0.5e-6 到 2.0e-6 | m | 决定导电截面 |
| coating 覆盖率 | `coverage_ratio` | 0.5 到 1.0 | - | 有效导电覆盖面积 |
| 可 coating 表面积 | `A_surface_p/n` | 1e-6 到 1e-4 | m^2 | 决定 coating 总体积和有效导电网络 |
| 电连通因子 | `C_net_p/n` | 0.01 到 1 | - | 描述 3D coating 网络沿电流方向的连通效率 |
| 串联热电对数 | `N_series` | 1 到 100 | - | 电压和电阻都随串联数增加 |
| 并联支路数 | `N_parallel_effective` | 1 到 20 | - | 降低总电阻 |
| 接触/电极电阻 | `R_contact_total + R_electrode_total` | 0 到 10 | ohm | 额外串联电阻 |

注意：

```text
docx 中 Sigma 行疑似误填为 Seebeck，不能直接作为 sigma_p/sigma_n 使用。
```

如果暂时没有 coating 电导率，建议先用文献或实验估计值填入 `sigma_p` 和 `sigma_n`，否则无法可靠计算 `R_e` 和 `P_max`。

## 3. 温差计算

### 3.1 器件热阻

```text
R_TE = L_device / (kappa_eff * A_device)
```

### 3.2 恒温热端 + 冷端对流

适用于：

```text
Wearable TEG
Pipe_car_waste_heat
Industrial_waste_heat
```

冷端对流热阻：

```text
R_cold = 1 / (h_cold * A_device)
```

器件温差：

```text
DeltaT_device =
  (T_hot_surface - T_cold_env)
  * R_TE / (R_TE + R_cold)
```

代入 `R_TE` 后：

```text
DeltaT_device =
  (T_hot_surface - T_cold_env)
  * L_device
  / (L_device + kappa_eff / h_cold)
```

### 3.3 固定热流 + 冷端对流

适用于：

```text
Laptop_CPU/GPU
固定功率芯片
```

器件温差：

```text
DeltaT_device = q_hot * L_device / kappa_eff
```

冷端温度：

```text
T_cold_device = T_cold_env + q_hot / h_cold
```

热端温度：

```text
T_hot_device = T_cold_device + DeltaT_device
```

温度约束：

```text
T_hot_device <= T_max_allowed
```

## 4. 电压计算

### 4.1 单个 p-n 热电对的 Seebeck 系数

Seebeck 系数必须使用 `V/K`。如果输入是 `uV/K`，需要乘以：

```text
1e-6
```

单个 p-n pair 的等效 Seebeck 系数：

```text
S_pair = S_p - S_n
```

因为 `S_n` 通常为负值，所以：

```text
S_pair = S_p + abs(S_n)
```

用当前 docx 数值：

```text
S_p = 100 uV/K
S_n = -155 uV/K
S_pair = 255 uV/K = 255e-6 V/K
```

### 4.2 开路电压

单个 p-n pair：

```text
V_pair = S_pair * DeltaT_device
```

`N_series` 个热电对串联：

```text
V_oc = N_series * S_pair * DeltaT_device
```

并联不会提高开路电压，只会降低总内阻。

## 5. 电阻计算

电阻有两种最小计算方式。

## 5.1 推荐方式：直接提供或测得 R_p、R_n

如果可以通过实验或电学 FEM 得到每个 leg 的电阻，最简单：

```text
R_pair = R_p + R_n + R_contact_pair + R_electrode_pair
```

总内阻：

```text
R_e =
  N_series * R_pair / N_parallel_effective
  + R_electrode_total
```

这是最稳的第一阶段做法，因为复杂 3D coating 网络的真实电流路径不容易用几何公式准确表达。

## 5.2 估算方式：由 coating 网络计算 R_p、R_n

如果没有测得 `R_p` 和 `R_n`，可以先用薄壳网络近似。

对每个 p 或 n leg：

```text
V_coat = A_surface * t_coating * coverage_ratio
f_coat = V_coat / (A_device * L_device)
```

等效电导率：

```text
sigma_eff = C_net * f_coat * sigma_coat
```

leg 电阻：

```text
R_leg = L_device / (sigma_eff * A_device)
```

代入 `sigma_eff` 后：

```text
R_leg =
  L_device^2
  / (C_net * sigma_coat * A_surface * t_coating * coverage_ratio)
```

所以：

```text
R_p =
  L_device^2
  / (C_net_p * sigma_p * A_surface_p * t_coating_p * coverage_ratio_p)

R_n =
  L_device^2
  / (C_net_n * sigma_n * A_surface_n * t_coating_n * coverage_ratio_n)
```

单个热电对：

```text
R_pair = R_p + R_n + R_contact_pair + R_electrode_pair
```

整个器件：

```text
R_e =
  N_series * R_pair / N_parallel_effective
  + R_electrode_total
```

## 6. 最大功率计算

当外接负载等于内阻时：

```text
R_load = R_e
```

最大功率：

```text
P_max = V_oc^2 / (4 * R_e)
```

展开后：

```text
P_max =
  (N_series * S_pair * DeltaT_device)^2
  / (4 * R_e)
```

这说明设计存在热-电折中：

```text
提高 R_TE -> DeltaT_device 增大 -> V_oc 增大
但如果骨架太细或 coating 太薄 -> R_e 增大 -> P_max 下降
```

所以第一阶段优化目标不应该是：

```text
maximize DeltaT_device
```

而应该至少是：

```text
maximize P_max
```

或者同时记录：

```text
DeltaT_device
V_oc
R_e
P_max
```

## 7. 最小输入与输出清单

### 7.1 最小输入

```text
thermal:
  A_device
  L_device
  kappa_eff
  T_hot_surface or q_hot
  T_cold_env
  h_cold
  T_max_allowed

electrical:
  S_p
  S_n
  sigma_p
  sigma_n
  t_coating_p
  t_coating_n
  A_surface_p
  A_surface_n
  coverage_ratio_p
  coverage_ratio_n
  C_net_p
  C_net_n
  N_series
  N_parallel_effective
  R_contact_pair
  R_electrode_total
```

### 7.2 如果要进一步精简

可以把复杂 coating 网络直接压缩成：

```text
R_p
R_n
```

这样最小输入变成：

```text
A_device
L_device
kappa_eff
T_hot_surface or q_hot
T_cold_env
h_cold
S_p
S_n
R_p
R_n
N_series
N_parallel_effective
R_contact_pair
```

这是最推荐的快速建模版本。

### 7.3 最小输出

```text
DeltaT_device
V_oc
R_e
P_max
T_hot_device
T_cold_device
result_valid
```

