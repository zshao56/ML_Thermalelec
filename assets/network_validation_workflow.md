# Network validation workflow

This workflow is the first self-contained validation step after analytic
screening. It does not require COMSOL, ANSYS, Abaqus, or GPU libraries.

## Purpose

The analytic screening model ranks the whole design space quickly. The network
model then checks selected samples with an assembled linear system:

```text
K x = b
```

The model treats ring planes as nodes and coated columns as thermal/electrical
edges. Air in the open domain is included as a parallel thermal path. The output
is still a reduced-order validation result, not a full 3D FEM result.

## Inputs

Default input:

```text
results/sampling_plan_top50.csv
```

This file is generated from ranked candidates:

```bash
python3 scripts/make_sampling_plan.py \
  --ranked-csv results/top50_evaluations_constrained.csv \
  --design-csv results/top50_design_cases_constrained.csv \
  --output results/sampling_plan_top50.csv \
  --target-count 50
```

## Run

Run the complete validation workflow:

```bash
python3 scripts/run_network_validation.py \
  --sampling-plan results/sampling_plan_top50.csv \
  --out-dir results/network_validation \
  --top-k 10
```

Main outputs:

```text
results/network_validation/network_results.csv
results/network_validation/network_top_evaluations.csv
```

## Direct solver command

To run only the network solver:

```bash
python3 scripts/solve_network_model.py \
  --input results/sampling_plan_top50.csv \
  --output results/network_results_top50.csv
```

## Generate STL previews

After selecting key design cases, export their full database rows and generate
STL previews:

```bash
sqlite3 -header -csv data/unit_cell_design_space.sqlite \
  "SELECT * FROM unit_cell_designs WHERE case_id IN (106267,97243,106027);" \
  > results/network_validation/key_design_cases.csv

python3 scripts/generate_case_stls.py \
  --input results/network_validation/key_design_cases.csv \
  --out-dir results/network_validation/stl_cases
```

Main outputs:

```text
results/network_validation/stl_cases/case_106267.stl
results/network_validation/stl_cases/case_97243.stl
results/network_validation/stl_cases/case_106027.stl
results/network_validation/stl_cases/manifest.json
```

## Create validation template

After confirming the STL files are fabricable, copy the key cases and create the
data-entry template for experiment or high-fidelity FEM results:

```bash
cp results/network_validation/key_design_cases.csv \
  results/network_validation/fabrication_ready_cases.csv

python3 scripts/make_validation_template.py \
  --cases results/network_validation/fabrication_ready_cases.csv \
  --network-results results/network_validation/network_results.csv \
  --output results/network_validation/validation_template.csv
```

The generated template keeps the network predictions and leaves measured fields
blank for validation data entry.

## Output labels

The network result file contains:

```text
kappa_uc_est_w_mk
r_uc_est_ohm
delta_t_device_k
v_oc_v
p_max_w
p_area_w_m2
baseline_kappa_uc_est_w_mk
baseline_r_uc_est_ohm
result_valid
invalid_reason
```

In this file, `kappa_uc_est_w_mk` and `r_uc_est_ohm` are the network-model
values. The original analytic screening values are preserved in the
`baseline_*` columns.

## Interpretation

Use this order:

```text
analytic screening -> network validation -> high-fidelity FEM or experiment
```

The network model is useful for selecting which samples deserve expensive FEM or
experimental measurement. It should not be treated as the final physical truth.
