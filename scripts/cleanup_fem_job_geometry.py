#!/usr/bin/env python3
"""Remove bulky generated geometry/mesh files from FEM job folders."""

from __future__ import annotations

import argparse
from pathlib import Path


DEFAULT_PATTERNS = ["*.stl", "*.msh", "*.geo"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete generated FEM geometry files while keeping input.json files.")
    parser.add_argument("--jobs-dir", default="results/fem_sampling/jobs")
    parser.add_argument("--pattern", action="append", default=None, help="Glob pattern to delete; can be repeated.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    jobs_dir = Path(args.jobs_dir)
    if not jobs_dir.exists():
        raise SystemExit(f"Jobs directory does not exist: {jobs_dir}")
    patterns = args.pattern or DEFAULT_PATTERNS

    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in jobs_dir.rglob(pattern) if path.is_file())
    files = sorted(set(files))

    total_bytes = sum(path.stat().st_size for path in files)
    for path in files:
        print(path)
        if not args.dry_run:
            path.unlink()

    print(f"Files matched: {len(files)}")
    print(f"Space {'would be freed' if args.dry_run else 'freed'}: {total_bytes / (1024 ** 3):.3f} GiB")


if __name__ == "__main__":
    main()
