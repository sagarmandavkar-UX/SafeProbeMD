"""Extract one MIMIC-IV admission into a SafeProbeMD Patient Snapshot JSON."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SNAPSHOT_SCHEMA_VERSION = "0.1"

REQUIRED_TABLES = {
    ("mimiciv_hosp", "patients"),
    ("mimiciv_hosp", "admissions"),
    ("mimiciv_hosp", "diagnoses_icd"),
    ("mimiciv_hosp", "d_icd_diagnoses"),
    ("mimiciv_hosp", "procedures_icd"),
    ("mimiciv_hosp", "d_icd_procedures"),
    ("mimiciv_hosp", "labevents"),
    ("mimiciv_hosp", "d_labitems"),
    ("mimiciv_hosp", "prescriptions"),
    ("mimiciv_hosp", "pharmacy"),
    ("mimiciv_icu", "icustays"),
    ("mimiciv_note", "discharge"),
}

OPTIONAL_DERIVED_TABLES = {
    ("mimiciv_derived", "age"),
    ("mimiciv_derived", "weight_durations"),
    ("mimiciv_derived", "sofa"),
    ("mimiciv_derived", "sapsii"),
    ("mimiciv_derived", "apsiii"),
}

SNAPSHOT_TOP_LEVEL_KEYS = [
    "schema_version",
    "source",
    "ids",
    "demographics",
    "admission",
    "diagnoses",
    "procedures",
    "labs",
    "medications",
    "icu_summary",
    "clinical_note",
    "derived_risk_scores",
    "data_quality",
]

SECTION_KEYS = [
    "chief_complaint",
    "history_of_present_illness",
    "past_medical_history",
    "hospital_course",
    "discharge_diagnosis",
    "discharge_medications",
    "follow_up",
]

SECTION_ALIASES = {
    "chief complaint": "chief_complaint",
    "history of present illness": "history_of_present_illness",
    "hpi": "history_of_present_illness",
    "past medical history": "past_medical_history",
    "pmh": "past_medical_history",
    "hospital course": "hospital_course",
    "brief hospital course": "hospital_course",
    "discharge diagnosis": "discharge_diagnosis",
    "discharge diagnoses": "discharge_diagnosis",
    "discharge medications": "discharge_medications",
    "medications on discharge": "discharge_medications",
    "followup instructions": "follow_up",
    "follow up": "follow_up",
    "follow up instructions": "follow_up",
    "follow-up": "follow_up",
    "follow-up instructions": "follow_up",
}

CORE_LAB_ITEMIDS = {
    50868,
    50882,
    50893,
    50902,
    50912,
    50931,
    50971,
    50983,
    51006,
    51221,
    51222,
    51265,
    51301,
    51237,
    51274,
    51275,
    50861,
    50863,
    50878,
    50885,
    50883,
    50960,
    50813,
    50818,
    50820,
    50821,
    50816,
    50817,
    51002,
    50889,
}

RENAL_SAFETY_ITEMIDS = {50912, 51006, 50971}
HEPATIC_SAFETY_ITEMIDS = {50861, 50863, 50878, 50885, 50883, 50960}
SAFETY_LAB_ITEMIDS = RENAL_SAFETY_ITEMIDS | HEPATIC_SAFETY_ITEMIDS


@dataclass(frozen=True)
class ExtractArgs:
    db_path: Path
    hadm_id: int
    output_path: Path
    max_note_chars: int = 60000
    max_labs: int = 500
    strict: bool = False
    include_raw_note: bool = False
    pipeline_version: str = SNAPSHOT_SCHEMA_VERSION


@dataclass
class ValidationResult:
    warnings: list[dict[str, str]]
    missing_blocks: list[str]


def parse_args(argv: list[str] | None = None) -> ExtractArgs:
    parser = argparse.ArgumentParser(
        description="Extract one MIMIC-IV hadm_id into a SafeProbeMD Patient Snapshot JSON."
    )
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--hadm-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path, dest="output_path")
    parser.add_argument("--max-note-chars", type=int, default=60000)
    parser.add_argument("--max-labs", type=int, default=500)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--include-raw-note", action="store_true")
    parser.add_argument("--pipeline-version", default=SNAPSHOT_SCHEMA_VERSION)
    ns = parser.parse_args(argv)
    return ExtractArgs(
        db_path=ns.db_path.expanduser(),
        hadm_id=ns.hadm_id,
        output_path=ns.output_path,
        max_note_chars=ns.max_note_chars,
        max_labs=ns.max_labs,
        strict=ns.strict,
        include_raw_note=ns.include_raw_note,
        pipeline_version=ns.pipeline_version,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    con = connect_duckdb(args.db_path)
    available_tables = get_available_tables(con)
    validate_required_tables(available_tables)

    table_warnings = optional_table_warnings(available_tables)
    context = extract_admission_context(con, args.hadm_id, available_tables)
    icu = extract_selected_icu_stay(con, args.hadm_id, available_tables)
    weight = extract_weight_kg(con, icu.get("selected_stay_id"), available_tables)
    diagnoses = extract_diagnoses(con, args.hadm_id)
    procedures = extract_procedures(con, args.hadm_id)
    labs, labs_capped = extract_labs(con, args.hadm_id, args.max_labs)
    medications = extract_medications(con, args.hadm_id, available_tables)
    note = extract_discharge_note(con, args.hadm_id, args.max_note_chars)
    note_sections = parse_discharge_note_sections(note)
    risk_scores = extract_derived_risk_scores(con, icu.get("selected_stay_id"), available_tables)

    snapshot = build_snapshot(
        args=args,
        context=context,
        icu=icu,
        weight=weight,
        diagnoses=diagnoses,
        procedures=procedures,
        labs=labs,
        medications=medications,
        note=note,
        note_sections=note_sections,
        risk_scores=risk_scores,
        initial_warnings=table_warnings,
    )
    if labs_capped:
        snapshot["data_quality"]["warnings"].append(
            make_warning(
                "max_labs_cap_reached",
                "moderate",
                f"Curated lab rows exceeded --max-labs={args.max_labs}; output was capped.",
            )
        )

    validation = validate_snapshot(snapshot, strict=args.strict)
    snapshot["data_quality"]["warnings"].extend(validation.warnings)
    snapshot["data_quality"]["warnings"] = dedupe_warnings(snapshot["data_quality"]["warnings"])
    snapshot["data_quality"]["missing_blocks"] = dedupe_strings(
        snapshot["data_quality"]["missing_blocks"] + validation.missing_blocks
    )
    validate_snapshot_shape(snapshot, strict=True)
    write_json(snapshot, args.output_path)
    return 0


def connect_duckdb(db_path: Path):
    """Open DuckDB in read-only mode."""

    if not db_path.exists():
        raise FileNotFoundError(f"DuckDB database not found: {db_path}")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The duckdb Python package is required to run this extractor. "
            "Install duckdb in the approved MIMIC environment and rerun."
        ) from exc
    return duckdb.connect(str(db_path), read_only=True)


def get_available_tables(con) -> set[tuple[str, str]]:
    rows = fetch_all_dicts(
        con,
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        ORDER BY table_schema, table_name;
        """,
    )
    return {(str(row["table_schema"]), str(row["table_name"])) for row in rows}


def validate_required_tables(available_tables: set[tuple[str, str]]) -> None:
    missing = sorted(REQUIRED_TABLES - available_tables)
    if missing:
        pretty = ", ".join(f"{schema}.{table}" for schema, table in missing)
        raise RuntimeError(f"Missing required MIMIC-IV DuckDB tables: {pretty}")


def optional_table_warnings(available_tables: set[tuple[str, str]]) -> list[dict[str, str]]:
    warnings = []
    for schema, table in sorted(OPTIONAL_DERIVED_TABLES - available_tables):
        warnings.append(
            make_warning(
                "missing_optional_derived_table",
                "moderate",
                f"Optional derived table is unavailable: {schema}.{table}. Related fields will be null.",
            )
        )
    return warnings


def extract_admission_context(
    con,
    hadm_id: int,
    available_tables: set[tuple[str, str]],
) -> dict[str, Any]:
    has_age = ("mimiciv_derived", "age") in available_tables
    age_join = (
        """
        LEFT JOIN mimiciv_derived.age age
            ON ad.subject_id = age.subject_id
           AND ad.hadm_id = age.hadm_id
        """
        if has_age
        else ""
    )
    age_expr = (
        """
        COALESCE(
            age.age,
            pa.anchor_age + DATE_DIFF(
                'year',
                CAST(CAST(pa.anchor_year AS VARCHAR) || '-01-01' AS TIMESTAMP),
                ad.admittime
            )
        ) AS age
        """
        if has_age
        else """
        pa.anchor_age + DATE_DIFF(
            'year',
            CAST(CAST(pa.anchor_year AS VARCHAR) || '-01-01' AS TIMESTAMP),
            ad.admittime
        ) AS age
        """
    )
    row = fetch_one_dict(
        con,
        f"""
        SELECT
            ad.subject_id,
            ad.hadm_id,
            pa.gender,
            {age_expr},
            ad.race,
            ad.language,
            ad.insurance,
            ad.marital_status,
            ad.admittime AS admit_time,
            ad.dischtime AS discharge_time,
            ad.admission_type,
            ad.admission_location,
            ad.discharge_location,
            ad.hospital_expire_flag,
            CASE
                WHEN ad.dischtime IS NULL THEN NULL
                ELSE DATE_DIFF('second', ad.admittime, ad.dischtime) / 86400.0
            END AS los_days
        FROM mimiciv_hosp.admissions ad
        INNER JOIN mimiciv_hosp.patients pa
            ON ad.subject_id = pa.subject_id
        {age_join}
        WHERE ad.hadm_id = ?;
        """,
        [hadm_id],
    )
    if row is None:
        raise RuntimeError(f"No admission found for hadm_id={hadm_id}")
    return row


def extract_selected_icu_stay(
    con,
    hadm_id: int,
    available_tables: set[tuple[str, str]],
) -> dict[str, Any]:
    has_sofa = ("mimiciv_derived", "sofa") in available_tables
    has_sapsii = ("mimiciv_derived", "sapsii") in available_tables
    has_apsiii = ("mimiciv_derived", "apsiii") in available_tables

    with_clause = ""
    sofa_join = ""
    if has_sofa:
        with_clause = """
        WITH sofa_ranked AS (
            SELECT
                stay_id,
                sofa_24hours,
                respiration_24hours,
                coagulation_24hours,
                liver_24hours,
                cardiovascular_24hours,
                cns_24hours,
                renal_24hours,
                endtime,
                ROW_NUMBER() OVER (
                    PARTITION BY stay_id
                    ORDER BY sofa_24hours DESC NULLS LAST, endtime DESC NULLS LAST
                ) AS rn
            FROM mimiciv_derived.sofa
        ),
        sofa_best AS (
            SELECT
                stay_id,
                sofa_24hours AS sofa_max,
                respiration_24hours AS respiration,
                coagulation_24hours AS coagulation,
                liver_24hours AS liver,
                cardiovascular_24hours AS cardiovascular,
                cns_24hours AS cns,
                renal_24hours AS renal,
                endtime AS sofa_time
            FROM sofa_ranked
            WHERE rn = 1
        )
        """
        sofa_join = "LEFT JOIN sofa_best ON ie.stay_id = sofa_best.stay_id"

    saps_join = "LEFT JOIN mimiciv_derived.sapsii saps ON ie.stay_id = saps.stay_id" if has_sapsii else ""
    aps_join = "LEFT JOIN mimiciv_derived.apsiii aps ON ie.stay_id = aps.stay_id" if has_apsiii else ""
    sofa_select = (
        """
        sofa_best.sofa_max,
        sofa_best.respiration,
        sofa_best.coagulation,
        sofa_best.liver,
        sofa_best.cardiovascular,
        sofa_best.cns,
        sofa_best.renal,
        sofa_best.sofa_time
        """
        if has_sofa
        else """
        NULL AS sofa_max,
        NULL AS respiration,
        NULL AS coagulation,
        NULL AS liver,
        NULL AS cardiovascular,
        NULL AS cns,
        NULL AS renal,
        NULL AS sofa_time
        """
    )
    saps_select = "saps.sapsii, saps.sapsii_prob" if has_sapsii else "NULL AS sapsii, NULL AS sapsii_prob"
    aps_select = "aps.apsiii, aps.apsiii_prob" if has_apsiii else "NULL AS apsiii, NULL AS apsiii_prob"

    row = fetch_one_dict(
        con,
        f"""
        {with_clause}
        SELECT
            ie.subject_id,
            ie.hadm_id,
            ie.stay_id,
            ie.first_careunit,
            ie.last_careunit,
            ie.intime,
            ie.outtime,
            ie.los AS icu_los_days,
            {sofa_select},
            {saps_select},
            {aps_select},
            COUNT(*) OVER () AS n_icu_stays
        FROM mimiciv_icu.icustays ie
        {sofa_join}
        {saps_join}
        {aps_join}
        WHERE ie.hadm_id = ?
        ORDER BY
            sofa_max DESC NULLS LAST,
            sapsii DESC NULLS LAST,
            apsiii DESC NULLS LAST,
            icu_los_days DESC NULLS LAST,
            intime ASC NULLS LAST
        LIMIT 1;
        """,
        [hadm_id],
    )
    if row is None:
        return empty_icu_summary()
    return {
        "has_icu": True,
        "selected_stay_id": row.get("stay_id"),
        "selection_rule": "highest_acuity",
        "n_icu_stays": row.get("n_icu_stays") or 1,
        "first_careunit": row.get("first_careunit") or "",
        "last_careunit": row.get("last_careunit") or "",
        "intime": row.get("intime"),
        "outtime": row.get("outtime"),
        "los_days": row.get("icu_los_days"),
        "score_preview": {
            "sofa_max": row.get("sofa_max"),
            "sapsii": row.get("sapsii"),
            "apsiii": row.get("apsiii"),
        },
    }


def extract_weight_kg(
    con,
    selected_stay_id: Any,
    available_tables: set[tuple[str, str]],
) -> dict[str, Any] | None:
    if selected_stay_id is None or ("mimiciv_derived", "weight_durations") not in available_tables:
        return None
    return fetch_one_dict(
        con,
        """
        SELECT
            stay_id,
            weight AS weight_kg,
            weight_type,
            starttime,
            endtime
        FROM mimiciv_derived.weight_durations
        WHERE stay_id = ?
        ORDER BY
            CASE WHEN weight_type = 'admit' THEN 0 ELSE 1 END,
            starttime ASC NULLS LAST
        LIMIT 1;
        """,
        [selected_stay_id],
    )


def extract_diagnoses(con, hadm_id: int) -> list[dict[str, Any]]:
    return fetch_all_dicts(
        con,
        """
        SELECT
            dx.icd_code,
            CAST(dx.icd_version AS VARCHAR) AS icd_version,
            dx.seq_num,
            COALESCE(dd.long_title, '') AS description
        FROM mimiciv_hosp.diagnoses_icd dx
        LEFT JOIN mimiciv_hosp.d_icd_diagnoses dd
            ON dx.icd_code = dd.icd_code
           AND dx.icd_version = dd.icd_version
        WHERE dx.hadm_id = ?
        ORDER BY dx.seq_num ASC, dx.icd_version, dx.icd_code;
        """,
        [hadm_id],
    )


def extract_procedures(con, hadm_id: int) -> list[dict[str, Any]]:
    return fetch_all_dicts(
        con,
        """
        SELECT
            pr.icd_code,
            CAST(pr.icd_version AS VARCHAR) AS icd_version,
            pr.seq_num,
            COALESCE(dp.long_title, '') AS description
        FROM mimiciv_hosp.procedures_icd pr
        LEFT JOIN mimiciv_hosp.d_icd_procedures dp
            ON pr.icd_code = dp.icd_code
           AND pr.icd_version = dp.icd_version
        WHERE pr.hadm_id = ?
        ORDER BY pr.seq_num ASC, pr.chartdate ASC NULLS LAST, pr.icd_version, pr.icd_code;
        """,
        [hadm_id],
    )


def extract_labs(con, hadm_id: int, max_labs: int) -> tuple[list[dict[str, Any]], bool]:
    values_sql = ", ".join(f"({itemid})" for itemid in sorted(CORE_LAB_ITEMIDS))
    rows = fetch_all_dicts(
        con,
        f"""
        WITH target_admission AS (
            SELECT
                hadm_id,
                subject_id,
                admittime,
                dischtime
            FROM mimiciv_hosp.admissions
            WHERE hadm_id = ?
        ),
        selected_itemids(itemid) AS (
            VALUES {values_sql}
        ),
        candidate_labs AS (
            SELECT
                le.labevent_id,
                le.subject_id,
                le.hadm_id,
                le.itemid,
                COALESCE(dl.label, CAST(le.itemid AS VARCHAR)) AS label,
                COALESCE(dl.category, '') AS category,
                dl.fluid,
                le.charttime,
                le.valuenum,
                le.value,
                le.valueuom,
                le.ref_range_lower,
                le.ref_range_upper,
                le.flag,
                CASE
                    WHEN le.charttime >= ta.admittime
                     AND le.charttime < ta.admittime + INTERVAL 24 HOUR
                        THEN 'first_24h'
                    WHEN LOWER(COALESCE(le.flag, '')) = 'critical'
                        THEN 'critical'
                    WHEN LOWER(COALESCE(le.flag, '')) = 'abnormal'
                        THEN 'abnormal'
                    WHEN le.itemid IN {sql_int_tuple(RENAL_SAFETY_ITEMIDS)}
                        THEN 'renal_safety'
                    WHEN le.itemid IN {sql_int_tuple(HEPATIC_SAFETY_ITEMIDS)}
                        THEN 'hepatic_safety'
                    ELSE 'core_lab'
                END AS selection_reason
            FROM target_admission ta
            INNER JOIN mimiciv_hosp.labevents le
                ON le.hadm_id = ta.hadm_id
            LEFT JOIN mimiciv_hosp.d_labitems dl
                ON le.itemid = dl.itemid
            INNER JOIN selected_itemids si
                ON le.itemid = si.itemid
            WHERE le.charttime IS NOT NULL
              AND (
                    (le.charttime >= ta.admittime
                     AND le.charttime < ta.admittime + INTERVAL 24 HOUR)
                 OR LOWER(COALESCE(le.flag, '')) IN ('abnormal', 'critical')
                 OR le.itemid IN {sql_int_tuple(SAFETY_LAB_ITEMIDS)}
              )
              AND (
                    ta.dischtime IS NULL
                 OR le.charttime <= ta.dischtime + INTERVAL 6 HOUR
              )
        )
        SELECT
            itemid,
            label,
            category,
            charttime,
            valuenum,
            value,
            valueuom,
            ref_range_lower,
            ref_range_upper,
            flag,
            selection_reason
        FROM candidate_labs
        ORDER BY charttime ASC, label ASC, itemid ASC
        LIMIT ?;
        """,
        [hadm_id, max_labs + 1],
    )
    return apply_max_labs_cap(rows, max_labs)


def extract_medications(
    con,
    hadm_id: int,
    available_tables: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    if ("mimiciv_hosp", "pharmacy") in available_tables:
        sql = """
        SELECT
            COALESCE(pr.drug, '') AS drug,
            COALESCE(CAST(pr.dose_val_rx AS VARCHAR), '') AS dose_val_rx,
            COALESCE(CAST(pr.dose_unit_rx AS VARCHAR), '') AS dose_unit_rx,
            COALESCE(pr.route, '') AS route,
            COALESCE(CAST(ph.frequency AS VARCHAR), '') AS frequency,
            pr.starttime,
            pr.stoptime,
            CAST(pr.pharmacy_id AS VARCHAR) AS pharmacy_id
        FROM mimiciv_hosp.prescriptions pr
        LEFT JOIN mimiciv_hosp.pharmacy ph
            ON pr.pharmacy_id = ph.pharmacy_id
        WHERE pr.hadm_id = ?
        ORDER BY
            pr.starttime ASC NULLS LAST,
            pr.stoptime ASC NULLS LAST,
            pr.drug ASC;
        """
    else:
        sql = """
        SELECT
            COALESCE(pr.drug, '') AS drug,
            COALESCE(CAST(pr.dose_val_rx AS VARCHAR), '') AS dose_val_rx,
            COALESCE(CAST(pr.dose_unit_rx AS VARCHAR), '') AS dose_unit_rx,
            COALESCE(pr.route, '') AS route,
            '' AS frequency,
            pr.starttime,
            pr.stoptime,
            CAST(pr.pharmacy_id AS VARCHAR) AS pharmacy_id
        FROM mimiciv_hosp.prescriptions pr
        WHERE pr.hadm_id = ?
        ORDER BY
            pr.starttime ASC NULLS LAST,
            pr.stoptime ASC NULLS LAST,
            pr.drug ASC;
        """
    return fetch_all_dicts(con, sql, [hadm_id])


def extract_discharge_note(con, hadm_id: int, max_note_chars: int) -> dict[str, Any] | None:
    row = fetch_one_dict(
        con,
        """
        SELECT
            note_id,
            subject_id,
            hadm_id,
            note_type,
            note_seq,
            charttime,
            storetime,
            text
        FROM mimiciv_note.discharge
        WHERE hadm_id = ?
        ORDER BY charttime DESC NULLS LAST, note_seq DESC NULLS LAST, storetime DESC NULLS LAST
        LIMIT 1;
        """,
        [hadm_id],
    )
    if row is None:
        return None
    return prepare_discharge_note(row, max_note_chars)


def prepare_discharge_note(row: dict[str, Any], max_note_chars: int) -> dict[str, Any]:
    raw_text = str(row.get("text") or "")
    text, truncated = truncate_text(raw_text, max_note_chars)
    prepared = dict(row)
    prepared["text"] = text
    prepared["raw_text_available"] = bool(raw_text)
    prepared["text_truncated"] = truncated
    return prepared


def extract_derived_risk_scores(
    con,
    selected_stay_id: Any,
    available_tables: set[tuple[str, str]],
) -> dict[str, Any]:
    if selected_stay_id is None:
        return empty_derived_risk_scores("no ICU stay for this admission")

    has_sofa = ("mimiciv_derived", "sofa") in available_tables
    has_sapsii = ("mimiciv_derived", "sapsii") in available_tables
    has_apsiii = ("mimiciv_derived", "apsiii") in available_tables
    if not (has_sofa or has_sapsii or has_apsiii):
        return empty_derived_risk_scores("derived ICU score tables unavailable")

    with_clause = ""
    sofa_join = ""
    if has_sofa:
        with_clause = """
        WITH sofa_ranked AS (
            SELECT
                stay_id,
                sofa_24hours,
                respiration_24hours,
                coagulation_24hours,
                liver_24hours,
                cardiovascular_24hours,
                cns_24hours,
                renal_24hours,
                endtime,
                ROW_NUMBER() OVER (
                    PARTITION BY stay_id
                    ORDER BY sofa_24hours DESC NULLS LAST, endtime DESC NULLS LAST
                ) AS rn
            FROM mimiciv_derived.sofa
            WHERE stay_id = ?
        ),
        sofa_best AS (
            SELECT *
            FROM sofa_ranked
            WHERE rn = 1
        )
        """
        sofa_join = "LEFT JOIN sofa_best sr ON ie.stay_id = sr.stay_id"

    sofa_select = (
        """
        sr.sofa_24hours AS sofa_max,
        sr.respiration_24hours AS respiration,
        sr.coagulation_24hours AS coagulation,
        sr.liver_24hours AS liver,
        sr.cardiovascular_24hours AS cardiovascular,
        sr.cns_24hours AS cns,
        sr.renal_24hours AS renal
        """
        if has_sofa
        else """
        NULL AS sofa_max,
        NULL AS respiration,
        NULL AS coagulation,
        NULL AS liver,
        NULL AS cardiovascular,
        NULL AS cns,
        NULL AS renal
        """
    )
    saps_join = "LEFT JOIN mimiciv_derived.sapsii saps ON ie.stay_id = saps.stay_id" if has_sapsii else ""
    aps_join = "LEFT JOIN mimiciv_derived.apsiii aps ON ie.stay_id = aps.stay_id" if has_apsiii else ""
    saps_select = "saps.sapsii, saps.sapsii_prob" if has_sapsii else "NULL AS sapsii, NULL AS sapsii_prob"
    aps_select = "aps.apsiii, aps.apsiii_prob" if has_apsiii else "NULL AS apsiii, NULL AS apsiii_prob"
    params = [selected_stay_id, selected_stay_id] if has_sofa else [selected_stay_id]
    row = fetch_one_dict(
        con,
        f"""
        {with_clause}
        SELECT
            {sofa_select},
            {saps_select},
            {aps_select}
        FROM mimiciv_icu.icustays ie
        {sofa_join}
        {saps_join}
        {aps_join}
        WHERE ie.stay_id = ?
        LIMIT 1;
        """,
        params,
    )
    if row is None:
        return empty_derived_risk_scores("no derived ICU scores for selected stay")
    return normalize_risk_scores(row)


def parse_discharge_note_sections(note: dict[str, Any] | None) -> dict[str, Any]:
    if not note:
        return {
            "sections": empty_note_sections(),
            "section_parse_quality": "missing",
            "text_truncated": False,
        }

    raw_text = str(note.get("text") or "")
    sections = empty_note_sections()
    current_key: str | None = None
    buffers = {key: [] for key in SECTION_KEYS}

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        header_key, remainder = split_section_header(line)
        if header_key:
            current_key = header_key
            if remainder:
                buffers[current_key].append(remainder)
            continue
        if current_key and line:
            buffers[current_key].append(line)

    for key, parts in buffers.items():
        sections[key] = clean_section_text("\n".join(parts))

    return {
        "sections": sections,
        "section_parse_quality": estimate_section_parse_quality(sections, raw_text),
        "text_truncated": bool(note.get("text_truncated")),
    }


def split_section_header(line: str) -> tuple[str | None, str]:
    if not line or ":" not in line:
        return None, ""
    header, remainder = line.split(":", 1)
    if len(header) > 80:
        return None, ""
    key = normalize_section_header(header)
    if key is None:
        return None, ""
    return key, remainder.strip()


def normalize_section_header(header: str) -> str | None:
    cleaned = re.sub(r"[^a-z0-9]+", " ", header.lower()).strip()
    return SECTION_ALIASES.get(cleaned)


def estimate_section_parse_quality(sections: dict[str, str], raw_text: str) -> str:
    if not raw_text:
        return "missing"
    present = [key for key, value in sections.items() if value.strip()]
    if len(present) >= 5 and (
        sections.get("hospital_course", "").strip()
        or sections.get("discharge_diagnosis", "").strip()
    ):
        return "high"
    if len(present) >= 2:
        return "medium"
    return "low"


def build_snapshot(
    args: ExtractArgs,
    context: dict[str, Any],
    icu: dict[str, Any],
    weight: dict[str, Any] | None,
    diagnoses: list[dict[str, Any]],
    procedures: list[dict[str, Any]],
    labs: list[dict[str, Any]],
    medications: list[dict[str, Any]],
    note: dict[str, Any] | None,
    note_sections: dict[str, Any],
    risk_scores: dict[str, Any],
    initial_warnings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    selected_stay_id = icu.get("selected_stay_id")
    weight_kg = weight.get("weight_kg") if weight else None
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source": {
            "dataset": "MIMIC-IV + MIMIC-IV-Note",
            "repo": "MIT-LCP/mimic-code",
            "extraction_timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "extraction_unit": "hadm_id",
        },
        "ids": {
            "patient_id": str_or_empty(context.get("subject_id")),
            "admission_id": str_or_empty(context.get("hadm_id")),
            "selected_stay_id": str_or_none(selected_stay_id),
        },
        "demographics": {
            "age": nullable_number(context.get("age")),
            "gender": str_or_empty(context.get("gender")),
            "race": str_or_empty(context.get("race")),
            "language": str_or_empty(context.get("language")),
            "insurance": str_or_empty(context.get("insurance")),
            "marital_status": str_or_empty(context.get("marital_status")),
            "weight_kg": nullable_number(weight_kg),
        },
        "admission": {
            "admit_time": stringify_timestamp(context.get("admit_time")),
            "discharge_time": stringify_timestamp(context.get("discharge_time")),
            "admission_type": str_or_empty(context.get("admission_type")),
            "admission_location": str_or_empty(context.get("admission_location")),
            "discharge_location": str_or_empty(context.get("discharge_location")),
            "hospital_expire_flag": nullable_int(context.get("hospital_expire_flag")),
            "los_days": nullable_number(context.get("los_days")),
        },
        "diagnoses": [normalize_code_row(row) for row in diagnoses],
        "procedures": [normalize_code_row(row) for row in procedures],
        "labs": [normalize_lab_row(row) for row in labs],
        "medications": [normalize_medication_row(row) for row in medications],
        "icu_summary": {
            "has_icu": bool(icu.get("has_icu")),
            "selected_stay_id": str_or_none(selected_stay_id),
            "selection_rule": str_or_empty(icu.get("selection_rule")),
            "n_icu_stays": nullable_int(icu.get("n_icu_stays")) or 0,
            "first_careunit": str_or_empty(icu.get("first_careunit")),
            "last_careunit": str_or_empty(icu.get("last_careunit")),
            "intime": stringify_timestamp(icu.get("intime")),
            "outtime": stringify_timestamp(icu.get("outtime")),
            "los_days": nullable_number(icu.get("los_days")),
        },
        "clinical_note": {
            "note_id": str_or_empty(note.get("note_id") if note else ""),
            "note_type": str_or_empty(note.get("note_type") if note else "discharge") or "discharge",
            "raw_text_available": bool(note.get("raw_text_available")) if note else False,
            "sections": note_sections.get("sections", empty_note_sections()),
            "section_parse_quality": note_sections.get("section_parse_quality", "missing"),
            "text_truncated": bool(note_sections.get("text_truncated")),
        },
        "derived_risk_scores": risk_scores,
        "data_quality": {
            "missing_blocks": [],
            "warnings": list(initial_warnings or []),
            "extraction_notes": [
                "DuckDB extractor prototype generated this admission-level SafeProbeMD Patient Snapshot.",
                f"extractor_pipeline_version={args.pipeline_version}",
            ],
        },
    }
    return snapshot


def validate_snapshot(snapshot: dict[str, Any], strict: bool = False) -> ValidationResult:
    warnings: list[dict[str, str]] = []
    missing_blocks: list[str] = []
    missing_top = [key for key in SNAPSHOT_TOP_LEVEL_KEYS if key not in snapshot]
    if missing_top:
        message = f"Snapshot missing top-level keys: {', '.join(missing_top)}"
        if strict:
            raise RuntimeError(message)
        warnings.append(make_warning("schema_shape_incomplete", "high", message))

    if not snapshot.get("diagnoses"):
        missing_blocks.append("diagnoses")
        warnings.append(make_warning("no_diagnosis_codes", "moderate", "No diagnosis codes found for this hadm_id."))
    if not snapshot.get("labs"):
        missing_blocks.append("labs")
        warnings.append(make_warning("no_labs", "high", "No curated lab rows found for this hadm_id."))
    if not snapshot.get("medications"):
        missing_blocks.append("medications")
        warnings.append(make_warning("no_medications", "moderate", "No prescriptions found for this hadm_id."))
    if not snapshot.get("icu_summary", {}).get("has_icu"):
        missing_blocks.append("icu_summary")
        warnings.append(make_warning("no_icu_stay", "low", "No ICU stay found for this admission."))
    if not snapshot.get("clinical_note", {}).get("raw_text_available"):
        missing_blocks.append("clinical_note")
        warnings.append(make_warning("missing_discharge_note", "high", "No discharge note found for this hadm_id."))
    if snapshot.get("clinical_note", {}).get("text_truncated"):
        warnings.append(make_warning("truncated_note", "moderate", "Discharge note text was truncated before parsing."))
    if snapshot.get("demographics", {}).get("age") is None:
        warnings.append(make_warning("missing_age", "moderate", "Admission age is missing."))
    if snapshot.get("medications") and snapshot.get("demographics", {}).get("weight_kg") is None:
        warnings.append(make_warning("missing_weight_kg", "moderate", "No ICU weight was available for medication safety."))
    for med in snapshot.get("medications", []):
        if not med.get("dose_val_rx") or not med.get("dose_unit_rx"):
            warnings.append(
                make_warning("missing_medication_dose", "moderate", f"Medication order lacks dose fields: {med.get('drug', '')}.")
            )
    for lab in snapshot.get("labs", []):
        if not lab.get("valueuom"):
            warnings.append(make_warning("missing_lab_units", "low", f"Lab row lacks units: {lab.get('label', '')}."))
    if snapshot.get("icu_summary", {}).get("has_icu") and not snapshot.get("derived_risk_scores", {}).get("has_icu_scores"):
        warnings.append(
            make_warning("missing_derived_risk_scores", "moderate", "Selected ICU stay lacks available SOFA/SAPS-II/APS-III scores.")
        )

    if discharge_precedes_admit(snapshot.get("admission", {})):
        warnings.append(
            make_warning("inconsistent_admission_window", "high", "Discharge time precedes admission time.")
        )
    return ValidationResult(warnings=dedupe_warnings(warnings), missing_blocks=dedupe_strings(missing_blocks))


def validate_snapshot_shape(snapshot: dict[str, Any], strict: bool = False) -> bool:
    missing = [key for key in SNAPSHOT_TOP_LEVEL_KEYS if key not in snapshot]
    extra = [key for key in snapshot if key not in SNAPSHOT_TOP_LEVEL_KEYS]
    if missing or extra:
        message = f"Snapshot top-level keys mismatch. Missing={missing}; extra={extra}"
        if strict:
            raise RuntimeError(message)
        return False
    required_nested = {
        "source": {"dataset", "repo", "extraction_timestamp", "extraction_unit"},
        "ids": {"patient_id", "admission_id", "selected_stay_id"},
        "demographics": {"age", "gender", "race", "language", "insurance", "marital_status", "weight_kg"},
        "admission": {
            "admit_time",
            "discharge_time",
            "admission_type",
            "admission_location",
            "discharge_location",
            "hospital_expire_flag",
            "los_days",
        },
        "icu_summary": {
            "has_icu",
            "selected_stay_id",
            "selection_rule",
            "n_icu_stays",
            "first_careunit",
            "last_careunit",
            "intime",
            "outtime",
            "los_days",
        },
        "clinical_note": {"note_id", "note_type", "sections", "section_parse_quality", "text_truncated"},
        "derived_risk_scores": {
            "has_icu_scores",
            "sofa_max",
            "sofa_components",
            "sapsii",
            "sapsii_prob",
            "apsiii",
            "apsiii_prob",
        },
        "data_quality": {"missing_blocks", "warnings", "extraction_notes"},
    }
    for block, keys in required_nested.items():
        value = snapshot.get(block)
        if not isinstance(value, dict) or not keys.issubset(value.keys()):
            if strict:
                raise RuntimeError(f"Snapshot block has invalid shape: {block}")
            return False
    if not set(SECTION_KEYS).issubset(snapshot["clinical_note"]["sections"].keys()):
        if strict:
            raise RuntimeError("clinical_note.sections missing required section keys")
        return False
    return True


def write_json(snapshot: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fp:
        json.dump(snapshot, fp, indent=2, ensure_ascii=False)
        fp.write("\n")


def fetch_one_dict(con, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    rows = fetch_all_dicts(con, sql, params)
    return rows[0] if rows else None


def fetch_all_dicts(con, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = con.execute(sql, params or [])
    columns = [desc[0] for desc in cursor.description]
    return [normalize_row(dict(zip(columns, row))) for row in cursor.fetchall()]


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: serialize_value(value) for key, value in row.items()}


def serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    return value


def normalize_code_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "icd_code": str_or_empty(row.get("icd_code")),
        "icd_version": str_or_empty(row.get("icd_version")),
        "seq_num": nullable_int(row.get("seq_num")),
        "description": str_or_empty(row.get("description")),
    }


def normalize_lab_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "itemid": str_or_empty(row.get("itemid")),
        "label": str_or_empty(row.get("label")),
        "category": str_or_empty(row.get("category")),
        "charttime": stringify_timestamp(row.get("charttime")),
        "valuenum": nullable_number(row.get("valuenum")),
        "value": str_or_empty(row.get("value")),
        "valueuom": str_or_empty(row.get("valueuom")),
        "ref_range_lower": nullable_number(row.get("ref_range_lower")),
        "ref_range_upper": nullable_number(row.get("ref_range_upper")),
        "flag": str_or_empty(row.get("flag")),
        "selection_reason": str_or_empty(row.get("selection_reason")),
    }


def normalize_medication_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "drug": str_or_empty(row.get("drug")),
        "dose_val_rx": str_or_empty(row.get("dose_val_rx")),
        "dose_unit_rx": str_or_empty(row.get("dose_unit_rx")),
        "route": str_or_empty(row.get("route")),
        "frequency": str_or_empty(row.get("frequency")),
        "starttime": stringify_timestamp(row.get("starttime")),
        "stoptime": stringify_timestamp(row.get("stoptime")),
        "pharmacy_id": str_or_empty(row.get("pharmacy_id")),
    }


def normalize_risk_scores(row: dict[str, Any]) -> dict[str, Any]:
    scores = {
        "has_icu_scores": any(row.get(key) is not None for key in ("sofa_max", "sapsii", "apsiii")),
        "sofa_max": nullable_number(row.get("sofa_max")),
        "sofa_components": {
            "respiration": nullable_number(row.get("respiration")),
            "coagulation": nullable_number(row.get("coagulation")),
            "liver": nullable_number(row.get("liver")),
            "cardiovascular": nullable_number(row.get("cardiovascular")),
            "cns": nullable_number(row.get("cns")),
            "renal": nullable_number(row.get("renal")),
        },
        "sapsii": nullable_number(row.get("sapsii")),
        "sapsii_prob": nullable_number(row.get("sapsii_prob")),
        "apsiii": nullable_number(row.get("apsiii")),
        "apsiii_prob": nullable_number(row.get("apsiii_prob")),
    }
    if not scores["has_icu_scores"]:
        scores["reason"] = "no derived ICU scores for selected stay"
    return scores


def empty_icu_summary() -> dict[str, Any]:
    return {
        "has_icu": False,
        "selected_stay_id": None,
        "selection_rule": "no_icu_stay",
        "n_icu_stays": 0,
        "first_careunit": "",
        "last_careunit": "",
        "intime": "",
        "outtime": "",
        "los_days": None,
    }


def empty_derived_risk_scores(reason: str) -> dict[str, Any]:
    return {
        "has_icu_scores": False,
        "reason": reason,
        "sofa_max": None,
        "sofa_components": {
            "respiration": None,
            "coagulation": None,
            "liver": None,
            "cardiovascular": None,
            "cns": None,
            "renal": None,
        },
        "sapsii": None,
        "sapsii_prob": None,
        "apsiii": None,
        "apsiii_prob": None,
    }


def empty_note_sections() -> dict[str, str]:
    return {key: "" for key in SECTION_KEYS}


def apply_max_labs_cap(rows: list[dict[str, Any]], max_labs: int) -> tuple[list[dict[str, Any]], bool]:
    if max_labs < 1:
        raise ValueError("--max-labs must be positive")
    capped = len(rows) > max_labs
    return rows[:max_labs], capped


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 1:
        raise ValueError("--max-note-chars must be positive")
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def sql_int_tuple(values: set[int]) -> str:
    ordered = sorted(values)
    if len(ordered) == 1:
        return f"({ordered[0]})"
    return "(" + ", ".join(str(value) for value in ordered) + ")"


def make_warning(warning_type: str, severity: str, message: str) -> dict[str, str]:
    return {"type": warning_type, "severity": severity, "message": message}


def dedupe_warnings(warnings: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped = []
    seen = set()
    for warning in warnings:
        marker = (warning.get("type"), warning.get("severity"), warning.get("message"))
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(warning)
    return deduped


def dedupe_strings(values: list[str]) -> list[str]:
    deduped = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def clean_section_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.strip())


def stringify_timestamp(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def str_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def str_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def nullable_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def nullable_number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric.is_integer():
        return int(numeric)
    return numeric


def discharge_precedes_admit(admission: dict[str, Any]) -> bool:
    admit = parse_timestamp(admission.get("admit_time"))
    discharge = parse_timestamp(admission.get("discharge_time"))
    if admit is None or discharge is None:
        return False
    return discharge < admit


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


if __name__ == "__main__":
    raise SystemExit(main())
