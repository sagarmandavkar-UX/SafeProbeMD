from __future__ import annotations

import gzip
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_mimic_duckdb_from_files import (
    BuildArgs,
    REQUIRED_TABLE_SPECS,
    build_database,
    resolve_table_specs,
    validate_required_files,
)


def test_resolve_table_specs_uses_expected_folder_layout() -> None:
    args = BuildArgs(mimic_root=Path("/tmp/mimic"), db_path=Path("/tmp/mimic.duckdb"), file_format="csv.gz")

    specs = resolve_table_specs(args)
    lookup = {(spec.schema, spec.table): spec for spec in specs}

    assert lookup[("mimiciv_hosp", "admissions")].path == Path("/tmp/mimic/hosp/admissions.csv.gz")
    assert lookup[("mimiciv_icu", "icustays")].path == Path("/tmp/mimic/icu/icustays.csv.gz")
    assert lookup[("mimiciv_note", "discharge")].path == Path("/tmp/mimic/note/discharge.csv.gz")
    assert lookup[("mimiciv_derived", "sofa")].path == Path("/tmp/mimic/derived/sofa.csv.gz")


def test_auto_format_search_order_prefers_csv_then_csv_gz_then_parquet() -> None:
    args = BuildArgs(mimic_root=Path("/tmp/mimic"), db_path=Path("/tmp/mimic.duckdb"), file_format="auto")

    specs = resolve_table_specs(args)
    admissions = {(spec.schema, spec.table): spec for spec in specs}[("mimiciv_hosp", "admissions")]

    assert admissions.path == Path("/tmp/mimic/hosp/admissions.csv")
    assert admissions.searched_paths == (
        Path("/tmp/mimic/hosp/admissions.csv"),
        Path("/tmp/mimic/hosp/admissions.csv.gz"),
        Path("/tmp/mimic/hosp/admissions.parquet"),
    )


def test_validate_required_files_reports_missing_required_files() -> None:
    with TemporaryDirectory() as tmp:
        args = BuildArgs(mimic_root=Path(tmp), db_path=Path(tmp) / "mimic.duckdb", file_format="csv")
        specs = resolve_table_specs(args)

        try:
            validate_required_files(specs)
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("validate_required_files should fail for missing required tables")

        assert "Missing required MIMIC-IV files" in message
        assert "mimiciv_hosp.admissions" in message
        assert "mimiciv_note.discharge" in message


def test_build_database_loads_tiny_synthetic_csv_files() -> None:
    try:
        import duckdb
    except ModuleNotFoundError:
        return

    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "mimic"
        db_path = Path(tmp) / "mimic.duckdb"
        write_required_csv_files(root)
        args = BuildArgs(mimic_root=root, db_path=db_path, file_format="csv")
        specs = resolve_table_specs(args)

        validate_required_files(specs)
        build_database(db_path, specs, "csv")

        con = duckdb.connect(str(db_path), read_only=True)
        rows = set(
            con.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema IN ('mimiciv_hosp', 'mimiciv_icu', 'mimiciv_note')
                """
            ).fetchall()
        )
        assert ("mimiciv_hosp", "admissions") in rows
        assert ("mimiciv_icu", "icustays") in rows
        assert ("mimiciv_note", "discharge") in rows


def test_build_database_loads_mixed_synthetic_auto_files() -> None:
    try:
        import duckdb
    except ModuleNotFoundError:
        return

    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "mimic"
        db_path = Path(tmp) / "mimic.duckdb"
        write_required_mixed_files(root)
        args = BuildArgs(mimic_root=root, db_path=db_path, file_format="auto")
        specs = resolve_table_specs(args)

        validate_required_files(specs)
        build_database(db_path, specs, "auto")

        con = duckdb.connect(str(db_path), read_only=True)
        admissions_count = con.execute("SELECT COUNT(*) FROM mimiciv_hosp.admissions").fetchone()[0]
        patients_count = con.execute("SELECT COUNT(*) FROM mimiciv_hosp.patients").fetchone()[0]
        icu_count = con.execute("SELECT COUNT(*) FROM mimiciv_icu.icustays").fetchone()[0]
        assert admissions_count == 1
        assert patients_count == 1
        assert icu_count == 1


def write_required_csv_files(root: Path) -> None:
    for _schema, folder, table in REQUIRED_TABLE_SPECS:
        directory = root / folder
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{table}.csv").write_text(csv_content_for(table), encoding="utf-8")


def write_required_mixed_files(root: Path) -> None:
    try:
        import duckdb
    except ModuleNotFoundError:
        return

    for _schema, folder, table in REQUIRED_TABLE_SPECS:
        directory = root / folder
        directory.mkdir(parents=True, exist_ok=True)
        content = csv_content_for(table)
        if table == "patients":
            with gzip.open(directory / f"{table}.csv.gz", "wt", encoding="utf-8") as fp:
                fp.write(content)
        elif table == "icustays":
            csv_path = directory / f"{table}.csv"
            parquet_path = directory / f"{table}.parquet"
            csv_path.write_text(content, encoding="utf-8")
            con = duckdb.connect()
            con.execute(
                f"COPY (SELECT * FROM read_csv_auto('{sql_quote(csv_path)}')) "
                f"TO '{sql_quote(parquet_path)}' (FORMAT PARQUET)"
            )
            csv_path.unlink()
        else:
            (directory / f"{table}.csv").write_text(content, encoding="utf-8")


def csv_content_for(table: str) -> str:
    if table == "admissions":
        return "subject_id,hadm_id,admittime,dischtime\n1,2,2180-01-01,2180-01-02\n"
    if table == "patients":
        return "subject_id,gender,anchor_age,anchor_year\n1,F,68,2180\n"
    if table == "icustays":
        return "subject_id,hadm_id,stay_id,first_careunit,last_careunit,intime,outtime,los\n1,2,3,MICU,MICU,2180-01-01,2180-01-02,1.0\n"
    if table == "discharge":
        return "note_id,subject_id,hadm_id,note_type,note_seq,charttime,storetime,text\nn1,1,2,discharge,1,2180-01-02,2180-01-02,Chief Complaint: Fever\n"
    return "subject_id,hadm_id,icd_code,icd_version,seq_num,itemid,pharmacy_id,label,long_title\n1,2,X,10,1,1,1,Mock,Mock title\n"


def sql_quote(path: Path) -> str:
    return str(path).replace("'", "''")
