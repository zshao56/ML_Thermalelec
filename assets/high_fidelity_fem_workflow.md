# High-fidelity FEM data workflow

This workflow prepares a practical high-fidelity validation set from the full
intrinsic dataset. It does not assume a specific solver. Each job contains:

```text
input.json
geometry.stl
```

These files can be imported into COMSOL, ANSYS, Abaqus, or another FEM tool.

## 1. Create FEM sampling set

Use the intrinsic dataset to select a 200-case high-fidelity sample:

```bash
python3 scripts/make_fem_sampling_set.py \
  --intrinsic-dataset results/intrinsic_network_dataset.csv \
  --db-path data/unit_cell_design_space.sqlite \
  --output results/fem_sampling/fem_sampling_200.csv \
  --target-count 200
```

The sample combines:

```text
top performance cases
diverse structural representatives
boundary/extreme cases
```

## 2. Prepare FEM jobs

Generate job folders:

```bash
python3 scripts/prepare_fem_jobs.py \
  --input results/fem_sampling/fem_sampling_200.csv \
  --out-dir results/fem_sampling/jobs \
  --ring-segments 64
```

For a small smoke test:

```bash
python3 scripts/prepare_fem_jobs.py \
  --input results/fem_sampling/fem_sampling_200.csv \
  --out-dir results/fem_sampling/jobs_test \
  --limit 5 \
  --ring-segments 64
```

## 3. Expected high-fidelity outputs

For each case, collect:

```text
kappa_eff_fem_w_mk
r_e_fem_ohm
alpha_eff_fem_v_k
p_max_coeff_fem_w_k2
p_area_coeff_fem_w_m2_k2
mechanical_valid
fabrication_note
```

These are intrinsic labels and should not include application scenarios.

Create a result-entry template:

```bash
python3 scripts/make_fem_results_template.py \
  --input results/fem_sampling/fem_sampling_200.csv \
  --output results/fem_sampling/fem_results_template_200.csv
```

## 4. Recommended order

Start with the first 5 jobs, confirm geometry import and boundary settings, then
run all 200 jobs.
