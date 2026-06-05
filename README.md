# ML_Thermalelec

ML_Thermalelec is a research workspace for building a parametric database of 3D printed scaffold unit cells coated with thermoelectric thin films. The current model focuses on a ring-column unit cell: two annular plates connected by multiple coated columns, with thermal and electrical properties estimated from geometry, material descriptors, and operating conditions.

Chinese version: [README.zh-CN.md](README.zh-CN.md)

## Current Design Space

The first-pass database uses the following discrete variables:

| Category | Variables |
| --- | --- |
| Ring geometry | `R_out = 2 mm`, `t_ring = 0.1/0.2/0.3 mm`, `ratio_hole = 0/0.25/0.5/0.75` |
| Height | `H_total = 10 mm`, `H_uc = 1/2.5/5/10 mm`, `N_layer = 10/4/2/1` |
| Column primitive | circular, square, pentagonal, hexagonal |
| Column size | `size1 = 0.2/0.3/0.4/0.5 mm` |
| Column count | `num = 5/10/15/20` |
| Layer connection offset | `0`, `n/5`, `2n/5` |
| Path type | straight, single kink, arc curve, sine wave, helix winding, Bezier curve |
| Coating material | Bi2Te3 n-type, Sb2Te3 p-type |
| Coating thickness | `500/1000/1500/2000 nm` |

The full Cartesian design space contains:

```text
3 * 4 * 4 * 4 * 4 * 4 * 3 * 2 * 4 * 6 = 442,368 unit-cell rows
```

## Repository Layout

```text
assets/
  Research notes, model documents, framework diagram, references, and STL previews.
assets/stl_connection_modes/
  Six STL examples showing the six supported connection path types.
data/
  Generated database location. SQLite files are ignored by Git.
scripts/
  Database and STL generation scripts.
```

## Generate the Database

The generated SQLite database is not committed because it is large. Rebuild it locally with:

```bash
python3 scripts/build_unit_cell_database.py --overwrite
```

Default output:

```text
data/unit_cell_design_space.sqlite
```

## Generate STL Previews

Six default unit-cell STL files can be regenerated with:

```bash
python3 scripts/generate_connection_stls.py
```

Default output:

```text
assets/stl_connection_modes/
```

## Network Validation

After ranking candidate samples, run the reduced thermal-electrical network
validation workflow with:

```bash
python3 scripts/run_network_validation.py \
  --sampling-plan results/sampling_plan_top50.csv \
  --out-dir results/network_validation \
  --top-k 10
```

See [assets/network_validation_workflow.md](assets/network_validation_workflow.md)
for assumptions, outputs, and interpretation.

## Intrinsic ML Dataset

For the main general surrogate model, generate scenario-independent intrinsic
labels. Application scenarios are reserved for final validation and inverse
design checks.

```bash
python3 scripts/run_intrinsic_batches.py \
  --input-dir data/batches \
  --out-dir results/intrinsic_network_batches \
  --workers 32 \
  --combined-output results/intrinsic_network_dataset.csv
```

Default output:

```text
results/intrinsic_network_dataset.csv
```

Audit the dataset before training:

```bash
python3 scripts/audit_intrinsic_dataset.py \
  --input results/intrinsic_network_dataset.csv \
  --out-dir results/intrinsic_audit
```

## High-Fidelity FEM Sampling

Prepare a smaller high-fidelity simulation set from the intrinsic dataset:

```bash
python3 scripts/make_fem_sampling_set.py \
  --intrinsic-dataset results/intrinsic_network_dataset.csv \
  --db-path data/unit_cell_design_space.sqlite \
  --output results/fem_sampling/fem_sampling_200.csv \
  --target-count 200
```

Create generic FEM job folders:

```bash
python3 scripts/prepare_fem_jobs.py \
  --input results/fem_sampling/fem_sampling_200.csv \
  --out-dir results/fem_sampling/jobs_test \
  --limit 5
```

See [assets/high_fidelity_fem_workflow.md](assets/high_fidelity_fem_workflow.md).

## Main Analytical Estimates

The database generator computes first-pass descriptors and estimates including:

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

These estimates are intended for database screening and design-space organization. Higher-fidelity FEM or experimental validation should be applied to selected representative samples.
