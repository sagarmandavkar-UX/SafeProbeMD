"""Build a local DuckDB database from approved MIMIC-IV files."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any


SCHEMAS = ["mimiciv_hosp", "mimiciv_icu", "mimiciv_note", "mimiciv_derived"]

REQUIRED_TABLE_SPECS = [
    ("mimiciv_hosp", "hosp", "admissions"),
    ("mimiciv_hosp", "hosp", "patients"),
    ("mimiciv_hosp", "hosp", "diagnoses_icd"),
    ("mimiciv_hosp", "hosp", "d_icd_diagnoses"),
    ("mimiciv_hosp", "hosp", "procedures_icd"),
    ("mimiciv_hosp", "hosp", "d_icd_procedures"),
    ("mimiciv_hosp", "hosp", "labevents"),
    ("mimiciv_hosp", "hosp", "d_labitems"),
    ("mimiciv_hosp", "hosp", "prescriptions"),
    ("mimiciv_hosp", "hosp", "pharmacy"),
    ("mimiciv_icu", "icu", "icustays"),
    ("mimiciv_note", "note", "discharge"),
]

OPTIONAL_DERIVED_TABLE_SPECS = [
    ("mimiciv_derived", "derived", "age"),
    ("mimiciv_derived", "derived", "weight_durations"),
    ("mimiciv_derived", "derived", "sofa"),
    ("mimiciv_derived", "derived", "sapsii"),
    ("mimiciv_derived", "derived", "apsiii"),
]

SUPPORTED_FORMATS = ("auto", "csv.gz", "csv", "parquet")
AUTO_FORMAT_SEARCH_ORDER = ("csv", "csv.gz", "parquet")


@dataclass(frozen=True)
class BuildArgs:
    mimic_root: Path
    db_path: Path
    file_format: str


@dataclass(frozen=True)
class TableLoadSpec:
    schema: str
    folder: str
    table: str
    path: Path
    required: bool
    searched_paths: tuple[Path, ...]


def parse_args(argv: list[str] | None = None) -> BuildArgs:
    parser = argparse.ArgumentParser(
        description="Build a local DuckDB database from approved MIMIC-IV files."
    )
    parser.add_argument("--mimic-root", required=True, type=Path)
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--format", required=True, choices=SUPPORTED_FORMATS, dest="file_format")
    ns = parser.parse_args(argv)
    return BuildArgs(
        mimic_root=ns.mimic_root.expanduser(),
        db_path=ns.db_path.expanduser(),
        file_format=ns.file_format,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        specs = resolve_table_specs(args)
        validate_required_files(specs)
        build_database(args.db_path, specs, args.file_format)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def resolve_table_specs(args: BuildArgs) -> list[TableLoadSpec]:
    specs: list[TableLoadSpec] = []
    for schema, folder, table in REQUIRED_TABLE_SPECS:
        specs.append(
            TableLoadSpec(
                schema=schema,
                folder=folder,
                table=table,
                path=resolve_table_path(args.mimic_root, folder, table, args.file_format),
                required=True,
                searched_paths=table_search_paths(args.mimic_root, folder, table, args.file_format),
            )
        )
    for schema, folder, table in OPTIONAL_DERIVED_TABLE_SPECS:
        specs.append(
            TableLoadSpec(
                schema=schema,
                folder=folder,
                table=table,
                path=resolve_table_path(args.mimic_root, folder, table, args.file_format),
                required=False,
                searched_paths=table_search_paths(args.mimic_root, folder, table, args.file_format),
            )
        )
    return specs


def validate_required_files(specs: list[TableLoadSpec]) -> None:
    missing_required = [spec for spec in specs if spec.required and not spec.path.is_file()]
    if missing_required:
        lines = ["Missing required MIMIC-IV files:"]
        for spec in missing_required:
            searched = ", ".join(str(path) for path in spec.searched_paths)
            lines.append(f"- {spec.schema}.{spec.table}; searched: {searched}")
        raise RuntimeError("\n".join(lines))


def build_database(db_path: Path, specs: list[TableLoadSpec], file_format: str) -> None:
    con = connect_duckdb(db_path)
    for schema in SCHEMAS:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    loaded = 0
    for spec in specs:
        if not spec.path.is_file():
            searched = ", ".join(str(path) for path in spec.searched_paths)
            print(f"WARNING: optional file missing; skipping {spec.schema}.{spec.table}; searched: {searched}")
            continue
        load_table(con, spec)
        loaded += 1
        print(f"Loaded {spec.schema}.{spec.table} from {spec.path}")

    print(f"Created DuckDB database: {db_path}")
    print(f"Loaded tables: {loaded}")
    print("No source files were copied into the repository.")


def connect_duckdb(db_path: Path):
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The duckdb Python package is required. Install duckdb in the approved local environment."
        ) from exc

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def load_table(con: Any, spec: TableLoadSpec) -> None:
    source = str(spec.path)
    table_name = f"{spec.schema}.{spec.table}"
    detected_format = file_format_from_path(spec.path)
    if detected_format in {"csv", "csv.gz"}:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {table_name} AS
            SELECT *
            FROM read_csv_auto(?, header=true);
            """,
            [source],
        )
        return
    if detected_format == "parquet":
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {table_name} AS
            SELECT *
            FROM read_parquet(?);
            """,
            [source],
        )
        return
    raise ValueError(f"Unsupported format for {spec.path}")


def table_file_path(mimic_root: Path, folder: str, table: str, file_format: str) -> Path:
    return mimic_root / folder / f"{table}.{file_format}"


def table_search_paths(mimic_root: Path, folder: str, table: str, file_format: str) -> tuple[Path, ...]:
    if file_format == "auto":
        return tuple(table_file_path(mimic_root, folder, table, fmt) for fmt in AUTO_FORMAT_SEARCH_ORDER)
    return (table_file_path(mimic_root, folder, table, file_format),)


def resolve_table_path(mimic_root: Path, folder: str, table: str, file_format: str) -> Path:
    search_paths = table_search_paths(mimic_root, folder, table, file_format)
    for path in search_paths:
        if path.is_file():
            return path
    return search_paths[0]


def file_format_from_path(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".csv.gz"):
        return "csv.gz"
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".parquet"):
        return "parquet"
    raise ValueError(f"Cannot infer supported file format from path: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
