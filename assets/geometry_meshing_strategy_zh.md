# 几何建库与网格划分策略

## 1. 建库几何原则

新版建库逻辑按“先定义单层，再复制多层”的方式组织结构：

```text
ring k:
  z = k * h_uc 到 k * h_uc + t_ring

pillar layer k:
  bottom_z = k * h_uc + t_ring
  top_z    = (k + 1) * h_uc
```

支柱中心线只在上下圆环之间存在，默认不插入圆环内部。这样 STL/CAD 预览时不会出现支柱穿进上下层圆环的视觉穿模。

## 2. 支柱尺寸范围

支柱尺寸采用更小、更可制造的范围：

```text
0.10 mm
0.15 mm
0.20 mm
0.25 mm
```

数据库中的 `size1_m` 统一按外接圆尺度理解：

```text
circular_column   圆直径
square_column     边长，对应碰撞半径为 a / sqrt(2)
pentagonal_column 外接圆直径
hexagonal_column  外接圆直径
```

## 3. 几何有效性过滤

建库时不直接删除无效结构，而是保留行并标记：

```text
geometry_valid = 0
invalid_reason = ...
```

当前重点过滤：

- 支柱尺寸导致环内/环外边界空间不足；
- 同一层内支柱之间距离小于安全间隙；
- 实体体积分数超过单元域；
- 支柱高度非正。

后续采样和 FEM 只使用：

```sql
WHERE geometry_valid = 1
```

## 4. 为什么训练阶段优先用 voxel-FVM

这些结构包含大量曲线支柱、双环排布和多层复制。直接把完整 STL 丢给四面体网格器，容易出现：

- 表面交叠；
- 非流形边；
- 体网格无法封闭；
- 网格数量暴涨；
- 单个失败结构中断批量任务。

因此大规模数据采集阶段建议用 voxel-FVM：

```text
80k / 150k / 全量结构：voxel-FVM
最终 top 候选：更细 voxel 或高保真 CAD/FEM 复核
```

## 5. voxel 网格建议

当前推荐：

```text
快速筛选: voxel_size = 1.0e-4 m = 100 um
精细复核: voxel_size = 5.0e-5 m = 50 um
局部最终确认: voxel_size = 2.5e-5 m = 25 um
```

经验规则：

```text
voxel_size <= min(size1_m) / 2
```

因为新版最小支柱尺寸是 0.10 mm，所以 100 um 是最低成本筛选网格；50 um 更适合最终确认。

## 6. 高保真体网格建议

如果后续要做真正四面体 FEM，不建议直接使用当前预览 STL。更稳的流程是：

1. 用 CAD 内核生成每根支柱的 sweep solid；
2. 对支柱和圆环做 Boolean union；
3. 删除内部重合面；
4. 对最终封闭实体划分体网格；
5. 局部加密支柱-圆环连接区域。

建议网格尺度：

```text
global mesh size: 0.10-0.15 mm
pillar local size: size1 / 3 到 size1 / 4
ring-pillar junction: 0.025-0.05 mm
```

如果使用 Gmsh，推荐先在少量 top 结构上测试：

```text
Mesh.Algorithm3D = 10
Mesh.Optimize = 1
Mesh.OptimizeNetgen = 1
```

## 7. 重新采样建议

修改建库逻辑后，旧数据库和旧训练集不应继续作为主结果。推荐流程：

```bash
python3 scripts/build_unit_cell_database.py --overwrite
python3 scripts/export_valid_batches.py --workers 25
python3 scripts/run_dataset_pipeline.py --workers 25 --skip-fem-jobs --skip-env-check
python3 scripts/run_rigorous_dataset_pipeline.py --workers 25 --fem-count 80000 --train-torch --force
```

如果 80k 模型稳定，再扩大到：

```bash
python3 scripts/run_rigorous_dataset_pipeline.py --workers 25 --fem-count 150000 --train-torch --force
```
