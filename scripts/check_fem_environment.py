#!/usr/bin/env python3
"""Check whether this machine has tools for automated high-fidelity FEM."""

from __future__ import annotations

import importlib.util
import shutil


COMMANDS = [
    ("gmsh", "mesh generation from CAD/STL"),
    ("ElmerSolver", "open-source multiphysics FEM solver"),
    ("ElmerGrid", "mesh conversion for Elmer"),
    ("getdp", "finite-element solver using Gmsh problem files"),
]

PYTHON_PACKAGES = [
    ("numpy", "array operations"),
    ("scipy", "sparse linear solvers"),
    ("meshio", "mesh format conversion"),
    ("sfepy", "Python FEM framework"),
    ("dolfinx", "FEniCSx FEM framework"),
    ("fenics", "legacy FEniCS framework"),
]


def main() -> None:
    print("Command-line FEM tools:")
    command_ok = {}
    for command, purpose in COMMANDS:
        path = shutil.which(command)
        command_ok[command] = path is not None
        status = "OK" if path else "MISSING"
        print(f"  {command}: {status}" + (f" ({path})" if path else f" - {purpose}"))

    print()
    print("Python FEM/scientific packages:")
    package_ok = {}
    for package, purpose in PYTHON_PACKAGES:
        ok = importlib.util.find_spec(package) is not None
        package_ok[package] = ok
        status = "OK" if ok else "MISSING"
        print(f"  {package}: {status} - {purpose}")

    print()
    if command_ok.get("gmsh") and command_ok.get("ElmerSolver") and command_ok.get("ElmerGrid"):
        print("Recommended automated route: Gmsh + Elmer is available.")
    elif command_ok.get("gmsh") and command_ok.get("getdp"):
        print("Recommended automated route: Gmsh + GetDP is available.")
    elif package_ok.get("sfepy") or package_ok.get("dolfinx") or package_ok.get("fenics"):
        print("Recommended automated route: Python FEM package is available.")
    else:
        print("No high-fidelity FEM solver stack was found.")
        print("Current repo scripts can prepare jobs, STL, JSON inputs, and reduced-order labels,")
        print("but they cannot run true high-fidelity FEM without installing a solver.")
        print()
        print("Practical install choices on Linux:")
        print("  Option A: conda install -c conda-forge gmsh meshio sfepy scipy")
        print("  Option B: install Gmsh + ElmerSolver through your system or cluster package manager")
        print("  Option C: use COMSOL/ANSYS/Abaqus and consume the generated STL/input.json jobs")


if __name__ == "__main__":
    main()
