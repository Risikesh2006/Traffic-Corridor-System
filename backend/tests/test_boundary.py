"""Architecture tests enforcing the two non-negotiable rules (see CLAUDE.md):

1. Hardware boundary: control-system modules never import `sim`.
2. Determinism: no direct wall-clock time or unseeded global randomness
   outside the files explicitly responsible for them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

CONTROL_MODULES = [
    "state",
    "tracking",
    "routing",
    "corridor",
    "safety",
    "controller",
    "health",
    "events",
]

# Relative to BACKEND_DIR. These are the only files permitted to touch real
# wall-clock time / import the global `random` module.
ALLOWED_TIME_FILES = {"core/clock.py"}
ALLOWED_RANDOM_IMPORT_FILES = {"core/rng.py"}

EXCLUDED_DIR_NAMES = {".venv", "__pycache__", "corridor_backend.egg-info"}


def _all_python_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*.py"):
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        files.append(path)
    return files


def _imported_module_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_control_modules_never_import_sim() -> None:
    violations = []
    for module_name in CONTROL_MODULES:
        module_dir = BACKEND_DIR / module_name
        if not module_dir.exists():
            continue
        for path in _all_python_files(module_dir):
            if "sim" in _imported_module_roots(path):
                violations.append(str(path.relative_to(BACKEND_DIR)))
    assert not violations, f"control modules must not import `sim`: {violations}"


def test_no_direct_wall_clock_time_outside_allowed_files() -> None:
    pattern = re.compile(r"\btime\.time\s*\(|\bdatetime\.now\s*\(|\bdatetime\.utcnow\s*\(")
    violations = []
    for path in _all_python_files(BACKEND_DIR):
        rel = path.relative_to(BACKEND_DIR).as_posix()
        if rel.startswith("tests/") or rel in ALLOWED_TIME_FILES:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            violations.append(rel)
    assert not violations, f"direct wall-clock time used outside allowed files: {violations}"


def test_no_global_random_import_outside_allowed_files() -> None:
    pattern = re.compile(r"^\s*import\s+random\b", re.MULTILINE)
    violations = []
    for path in _all_python_files(BACKEND_DIR):
        rel = path.relative_to(BACKEND_DIR).as_posix()
        if rel.startswith("tests/") or rel in ALLOWED_RANDOM_IMPORT_FILES:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            violations.append(rel)
    assert not violations, f"`import random` used outside allowed files: {violations}"
