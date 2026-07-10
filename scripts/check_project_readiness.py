"""Check whether the SafeProbeMD repo is ready for local MIMIC-IV testing."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "docs/mimic_iv_data_map.md",
    "docs/build_mimic_duckdb_local.md",
    "docs/extraction_plan_duckdb.md",
    "docs/github_release_checklist.md",
    "docs/local_integration_checklist.md",
    "docs/repo_structure.md",
    "scripts/build_mimic_duckdb_from_files.py",
    "scripts/extract_patient_snapshot_duckdb.py",
    "scripts/run_safeprobemd_case.py",
    "scripts/run_batch_safeprobemd.py",
    "safeprobemd/orchestrator.py",
    "tests/test_agent_routing.py",
    "tests/test_safety_verification.py",
    "tests/test_build_mimic_duckdb_from_files.py",
    "tests/test_extract_patient_snapshot_duckdb.py",
    "tests/test_run_batch_safeprobemd.py",
]

REQUIRED_GITIGNORE_PATTERNS = [
    "*.duckdb",
    "*.db",
    "*.sqlite",
    "*.csv",
    "*.csv.gz",
    "*.parquet",
    "snapshots/",
    "outputs/",
    "data/",
    "~/data/",
    "hadm_ids.txt",
    "*_safeprobemd_output.json",
    "*_agent_inputs.json",
    "batch_summary.csv",
    "aggregate_metrics.json",
    ".env",
    ".env.*",
    ".DS_Store",
    "__pycache__/",
    ".pytest_cache/",
    ".venv/",
    "venv/",
]

README_REQUIRED_MARKERS = {
    "project title": "# SafeProbeMD",
    "GitHub-safe repository description": "SafeProbeMD is a research prototype for safe, evidence-grounded multi-agent clinical reasoning over approved MIMIC-IV data.",
    "DuckDB build command": "scripts/build_mimic_duckdb_from_files.py",
    "extraction command": "scripts/extract_patient_snapshot_duckdb.py",
    "run SafeProbeMD command": "scripts/run_safeprobemd_case.py",
    "batch command": "scripts/run_batch_safeprobemd.py",
    "readiness checker command": "scripts/check_project_readiness.py",
    "Data Safety section": "## Data Safety",
    "status section": "## Repository Status",
}

PYTHON_DIRS = ["scripts", "safeprobemd", "tests"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check SafeProbeMD local testing readiness.")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    failures: list[str] = []

    failures.extend(check_required_files(root))
    failures.extend(check_gitignore(root))
    failures.extend(check_readme(root))
    failures.extend(check_python_compile(root))

    ready = not failures
    for failure in failures:
        print(f"FAIL: {failure}")
    if ready:
        print("All readiness checks passed.")
    print(f"READY_FOR_LOCAL_MIMIC_TESTING = {str(ready).lower()}")
    return 0 if ready else 1


def check_required_files(root: Path) -> list[str]:
    failures = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            failures.append(f"missing required file: {relative}")
    return failures


def check_gitignore(root: Path) -> list[str]:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return ["missing .gitignore"]
    patterns = normalized_lines(gitignore.read_text(encoding="utf-8"))
    failures = []
    for pattern in REQUIRED_GITIGNORE_PATTERNS:
        if pattern not in patterns:
            failures.append(f".gitignore missing safety pattern: {pattern}")
    return failures


def check_readme(root: Path) -> list[str]:
    readme = root / "README.md"
    if not readme.is_file():
        return ["missing README.md"]
    text = readme.read_text(encoding="utf-8")
    failures = []
    for label, marker in README_REQUIRED_MARKERS.items():
        if marker not in text:
            failures.append(f"README missing {label}: {marker}")
    return failures


def check_python_compile(root: Path) -> list[str]:
    failures = []
    for path in iter_python_files(root):
        relative = path.relative_to(root)
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
        except Exception as exc:
            failures.append(f"python compile failed for {relative}: {exc}")
    return failures


def iter_python_files(root: Path) -> Iterable[Path]:
    for dirname in PYTHON_DIRS:
        directory = root / dirname
        if not directory.exists():
            continue
        yield from sorted(directory.rglob("*.py"))


def normalized_lines(text: str) -> set[str]:
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


if __name__ == "__main__":
    raise SystemExit(main())
