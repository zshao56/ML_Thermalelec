# Generated data

SQLite database files are ignored by Git because they are large.

Regenerate the design-space database from the repository root:

```bash
python3 scripts/build_unit_cell_database.py --overwrite
```

Expected default output:

```text
data/unit_cell_design_space.sqlite
```
