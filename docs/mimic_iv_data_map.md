# SafeProbeMD — MIMIC-IV Data Map

**Version:** draft 1  
**Depends on:** Official MIT-LCP `mimic-code` repository, MIMIC-IV, MIMIC-IV-Note  
**Feeds:** `safeprobemd_agent_input_schema.md`  
**Status:** design — no implementation yet  

---

## 1. Purpose

This document defines the data contract that converts one MIMIC-IV hospital admission into a SafeProbeMD-ready patient snapshot.

The goal is to transform raw relational clinical data into a structured, auditable JSON object that can be routed into the SafeProbeMD multi-agent pipeline.

The snapshot unit is:

```text
subject_id → hadm_id → stay_id
```

Where:

- `subject_id` = patient/person
- `hadm_id` = hospital admission
- `stay_id` = ICU stay

The core extraction unit is **one `hadm_id`**.  
If an admission has ICU data, the pipeline resolves the relevant `stay_id` through `icustays`.

---

## 2. Scope decisions

These decisions fix the meaning of one SafeProbeMD patient snapshot.

| Decision | Selected rule | Reason |
|---|---|---|
| Multiple ICU stays per admission | Use the **highest-acuity ICU stay** | Best fit for risk and safety review |
| SOFA aggregation | Use **maximum SOFA during ICU stay** | Captures worst organ dysfunction |
| SAPS-II / APS-III | Use the score attached to selected `stay_id` | These are stay-level severity scores |
| Lab window | Use **first 24h labs + critical abnormal labs from full admission** | Keeps context bounded while preserving major safety signals |
| Medication window | Use admission-level prescriptions, with start/stop times preserved | Supports medication safety screening |
| Note handling | Use **section-parsed discharge note** when possible | Reduces token cost and improves routing |
| Snapshot level | One JSON object per `hadm_id` | Matches hospital episode reasoning |

---

## 3. High-level data flow

```text
MIMIC-IV raw tables
      ↓
MIMIC-IV derived concepts
      ↓
MIMIC-IV-Note discharge summary
      ↓
Admission-level patient snapshot
      ↓
Agent-specific routed inputs
      ↓
SafeProbeMD multi-agent reasoning
```

---

## 4. Join spine

The main join structure is:

```text
patients.subject_id
      ↓
admissions.subject_id + admissions.hadm_id
      ↓
hospital tables keyed by subject_id / hadm_id
      ↓
icustays.subject_id + icustays.hadm_id + icustays.stay_id
      ↓
ICU and derived tables keyed by stay_id
```

### Key join rules

| Join | Purpose |
|---|---|
| `patients.subject_id = admissions.subject_id` | Attach demographics to admissions |
| `admissions.hadm_id = diagnoses_icd.hadm_id` | Attach diagnosis codes |
| `admissions.hadm_id = procedures_icd.hadm_id` | Attach procedure codes |
| `admissions.hadm_id = labevents.hadm_id` | Attach lab results |
| `admissions.hadm_id = prescriptions.hadm_id` | Attach medication orders |
| `admissions.hadm_id = icustays.hadm_id` | Resolve ICU stays |
| `icustays.stay_id = mimiciv_derived.*.stay_id` | Attach ICU severity scores |
| `hadm_id` in MIMIC-IV-Note | Attach discharge summary |

---

## 5. Source map

| Source | What it contains | Key IDs | SafeProbeMD use | Routed to |
|---|---|---|---|---|
| `patients` | Gender, anchor age, anchor year group, date of death | `subject_id` | Demographic baseline | Intake |
| `admissions` | Admission time, discharge time, admission type, location, insurance, language, race, marital status, hospital death flag | `subject_id`, `hadm_id` | Episode framing and outcome | Intake |
| `diagnoses_icd` | ICD-9/10 diagnosis codes with sequence priority | `subject_id`, `hadm_id`, `icd_code`, `icd_version` | Problem list and comorbidities | Diagnostic, Verification |
| `d_icd_diagnoses` | Text descriptions for ICD diagnosis codes | `icd_code`, `icd_version` | Converts codes into reasoning-ready labels | Diagnostic, Verification |
| `procedures_icd` | ICD procedure codes | `subject_id`, `hadm_id`, `icd_code`, `icd_version` | Interventions performed during admission | Diagnostic, Verification |
| `d_icd_procedures` | Text descriptions for ICD procedure codes | `icd_code`, `icd_version` | Converts procedure codes into readable labels | Diagnostic, Verification |
| `labevents` | Lab results, values, units, abnormal flags, chart times | `subject_id`, `hadm_id`, `itemid`, `specimen_id` | Objective evidence and safety checks | Lab, Medication, Risk, Verification |
| `d_labitems` | Lab item labels and categories | `itemid` | Converts lab item IDs into readable lab names | Lab, Verification |
| `prescriptions` | Medication orders, drug, dose, route, start/stop times | `subject_id`, `hadm_id`, `pharmacy_id` | Medication safety and treatment context | Medication, Verification |
| `pharmacy` | Pharmacy-level medication information | `subject_id`, `hadm_id`, `pharmacy_id` | Optional medication detail enrichment | Medication |
| `emar` | Medication administration records | `subject_id`, `hadm_id`, `emar_id` | Optional administered-medication check | Medication |
| `icustays` | ICU stay time, care unit, length of stay | `subject_id`, `hadm_id`, `stay_id` | ICU context and `stay_id` bridge | Intake, Risk |
| `mimiciv_derived.age` | Computed age at admission | `subject_id`, `hadm_id` | Admission-specific age | Intake, Medication |
| `mimiciv_derived.weight_durations` | Patient weight over ICU time windows | `stay_id` | Weight-based medication safety | Medication |
| `mimiciv_derived.sofa` | Sequential Organ Failure Assessment score | `stay_id`, time fields | Organ failure severity | Risk |
| `mimiciv_derived.sapsii` | SAPS-II severity and mortality probability | `stay_id` | Severity and mortality risk | Risk |
| `mimiciv_derived.apsiii` | APACHE-III severity and mortality probability | `stay_id` | Severity and mortality risk | Risk |
| MIMIC-IV-Note discharge note | Free-text discharge summary | `note_id`, `subject_id`, `hadm_id` | Main narrative clinical context | Diagnostic |

---

## 6. Patient selection

Each extraction run should start with one target `hadm_id`.

Recommended first prototype selection:

```text
Select admissions that have:
1. one discharge note
2. at least one diagnosis code
3. at least one lab result
4. at least one medication order
5. optional ICU stay
```

For the first local test, use **5 to 10 admissions**.  
For pilot evaluation, use **50 admissions**.  
For full evaluation, scale only after the JSON schema and agent routing are stable.

---

## 7. ICU stay selection rule

A single `hadm_id` can map to multiple `stay_id`s.

SafeProbeMD uses one selected ICU stay for risk scoring.

### Rule

Use the ICU stay with the highest available acuity, selected in this order:

1. Highest max SOFA
2. If SOFA missing, highest SAPS-II
3. If SAPS-II missing, highest APS-III
4. If all scores missing, longest ICU length of stay
5. If no ICU stay exists, set `icu_summary.has_icu = false`

### Output fields

```json
{
  "selected_stay_id": "",
  "selection_rule": "highest_acuity",
  "n_icu_stays": 0,
  "has_icu": false
}
```

---

## 8. Lab selection rule

`labevents` is large and should not be routed raw.

SafeProbeMD uses a curated lab packet.

### Include

1. All selected labs from the first 24 hours after admission
2. Any critical or abnormal labs from the full admission
3. Renal and hepatic labs needed for medication safety
4. Labs needed for severity scoring or organ dysfunction review

### Recommended core lab groups

| Group | Example labs | Use |
|---|---|---|
| CBC | WBC, hemoglobin, hematocrit, platelets | Infection, anemia, bleeding, general acuity |
| BMP/CMP | sodium, potassium, chloride, bicarbonate, BUN, creatinine, glucose | Metabolic and renal status |
| Liver | AST, ALT, bilirubin, alkaline phosphatase, albumin | Hepatic status and medication safety |
| Coagulation | INR, PT, PTT | Bleeding risk and liver dysfunction |
| Blood gas | pH, pCO2, pO2, lactate | Respiratory/metabolic instability |
| Cardiac | troponin, BNP | Cardiac injury or strain |
| Inflammatory/Infection | lactate, cultures if available | Sepsis and shock context |

### Output fields

```json
{
  "lab_window": "first_24h_plus_critical_abnormal",
  "labs": [
    {
      "itemid": "",
      "label": "",
      "category": "",
      "charttime": "",
      "valuenum": null,
      "value": "",
      "valueuom": "",
      "ref_range_lower": null,
      "ref_range_upper": null,
      "flag": "",
      "selection_reason": "first_24h | abnormal | critical | renal_safety | hepatic_safety"
    }
  ]
}
```

---

## 9. Medication selection rule

Medication safety needs enough detail to check dosing and interactions, but should not receive unrelated patient data.

### Include

- Drug name
- Dose
- Dose unit
- Route
- Frequency if available
- Start time
- Stop time
- Linked pharmacy ID if available

### Output fields

```json
{
  "medications": [
    {
      "drug": "",
      "dose_val_rx": "",
      "dose_unit_rx": "",
      "route": "",
      "frequency": "",
      "starttime": "",
      "stoptime": "",
      "pharmacy_id": ""
    }
  ]
}
```

---

## 10. Discharge note handling

MIMIC-IV-Note contains free-text clinical notes. The discharge note is the main narrative input for the Diagnostic Reasoning Agent.

### Preferred approach

Use a section-parsed discharge note.

Recommended sections:

| Section | Use |
|---|---|
| Chief complaint | Initial presentation |
| History of present illness | Pre-admission clinical story |
| Past medical history | Comorbidities |
| Hospital course | Main reasoning narrative |
| Discharge diagnosis | Final diagnosis reference |
| Discharge medications | Medication context |
| Follow-up instructions | Post-discharge care plan |

### Output fields

```json
{
  "clinical_note": {
    "note_id": "",
    "note_type": "discharge",
    "raw_text_available": true,
    "sections": {
      "chief_complaint": "",
      "history_of_present_illness": "",
      "past_medical_history": "",
      "hospital_course": "",
      "discharge_diagnosis": "",
      "discharge_medications": "",
      "follow_up": ""
    },
    "section_parse_quality": "high | medium | low",
    "text_truncated": false
  }
}
```

---

## 11. Derived risk scores

Derived scores should be treated as risk signals, not diagnoses.

### SOFA

Use maximum SOFA for the selected ICU stay.

```json
{
  "sofa_max": 0,
  "sofa_components": {
    "respiration": null,
    "coagulation": null,
    "liver": null,
    "cardiovascular": null,
    "cns": null,
    "renal": null
  }
}
```

### SAPS-II

Use the stay-level SAPS-II score and probability.

```json
{
  "sapsii": 0,
  "sapsii_prob": null
}
```

### APS-III

Use the stay-level APS-III score and probability.

```json
{
  "apsiii": 0,
  "apsiii_prob": null
}
```

### Missing ICU scores

If no ICU stay exists:

```json
{
  "has_icu_scores": false,
  "reason": "no ICU stay for this admission"
}
```

---

## 12. Patient Snapshot JSON schema

This is the neutral data object that feeds the agent routing layer.

```json
{
  "schema_version": "0.1",
  "source": {
    "dataset": "MIMIC-IV + MIMIC-IV-Note",
    "repo": "MIT-LCP/mimic-code",
    "extraction_timestamp": "",
    "extraction_unit": "hadm_id"
  },
  "ids": {
    "patient_id": "",
    "admission_id": "",
    "selected_stay_id": null
  },
  "demographics": {
    "age": null,
    "gender": "",
    "race": "",
    "language": "",
    "insurance": "",
    "marital_status": "",
    "weight_kg": null
  },
  "admission": {
    "admit_time": "",
    "discharge_time": "",
    "admission_type": "",
    "admission_location": "",
    "discharge_location": "",
    "hospital_expire_flag": null,
    "los_days": null
  },
  "diagnoses": [
    {
      "icd_code": "",
      "icd_version": "",
      "seq_num": null,
      "description": ""
    }
  ],
  "procedures": [
    {
      "icd_code": "",
      "icd_version": "",
      "seq_num": null,
      "description": ""
    }
  ],
  "labs": [
    {
      "itemid": "",
      "label": "",
      "category": "",
      "charttime": "",
      "valuenum": null,
      "value": "",
      "valueuom": "",
      "ref_range_lower": null,
      "ref_range_upper": null,
      "flag": "",
      "selection_reason": ""
    }
  ],
  "medications": [
    {
      "drug": "",
      "dose_val_rx": "",
      "dose_unit_rx": "",
      "route": "",
      "frequency": "",
      "starttime": "",
      "stoptime": "",
      "pharmacy_id": ""
    }
  ],
  "icu_summary": {
    "has_icu": false,
    "selected_stay_id": null,
    "selection_rule": "highest_acuity",
    "n_icu_stays": 0,
    "first_careunit": "",
    "last_careunit": "",
    "intime": "",
    "outtime": "",
    "los_days": null
  },
  "clinical_note": {
    "note_id": "",
    "note_type": "discharge",
    "sections": {
      "chief_complaint": "",
      "history_of_present_illness": "",
      "past_medical_history": "",
      "hospital_course": "",
      "discharge_diagnosis": "",
      "discharge_medications": "",
      "follow_up": ""
    },
    "section_parse_quality": "",
    "text_truncated": false
  },
  "derived_risk_scores": {
    "has_icu_scores": false,
    "sofa_max": null,
    "sofa_components": {
      "respiration": null,
      "coagulation": null,
      "liver": null,
      "cardiovascular": null,
      "cns": null,
      "renal": null
    },
    "sapsii": null,
    "sapsii_prob": null,
    "apsiii": null,
    "apsiii_prob": null
  },
  "data_quality": {
    "missing_blocks": [],
    "warnings": [],
    "extraction_notes": []
  }
}
```

---

## 13. Agent routing map

The patient snapshot should be routed into the existing SafeProbeMD agent input schema.

| Snapshot block | Intake | Diagnostic | Lab | Medication | Risk | Verification | Critic | Consensus |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ids | yes | yes | yes | yes | yes | yes | yes | yes |
| demographics.age | yes | context only | yes | yes | context only | grounding only | yes | context only |
| demographics.gender | yes | no | yes | yes | no | grounding only | no | context only |
| demographics.weight_kg | no | no | no | yes | no | grounding only | no | no |
| race / language / insurance / marital | intake only | no | no | no | no | no | no | no |
| admission | yes | no | no | no | no | grounding only | no | context only |
| diagnoses | no | yes | no | contraindication subset | no | grounding only | no | no |
| procedures | no | yes | no | no | no | grounding only | no | no |
| labs full curated | no | no | yes | no | no | grounding only | no | no |
| renal/hepatic labs | no | no | no | yes | no | grounding only | no | no |
| abnormal labs | no | no | no | no | yes | grounding only | no | no |
| medications | no | no | no | yes | no | grounding only | no | no |
| icu_summary | yes | no | no | no | yes | grounding only | no | context only |
| clinical_note sections | no | yes | no | no | no | cited spans only | no | no |
| derived_risk_scores | no | no | no | no | yes | grounding only | no | no |

---

## 14. Data quality rules

The extraction layer should not silently fill missing information.

### Required fields

A snapshot is valid only if it has:

- `subject_id`
- `hadm_id`
- admission time
- discharge time or clear missingness flag
- at least one clinical note or diagnosis source

### Warning fields

Add warnings for:

- no discharge note
- no diagnosis codes
- no labs
- no medications
- no ICU stay
- multiple ICU stays
- missing age
- missing medication dose
- missing lab units
- contradictory death outcome fields
- truncated note

### Example warning object

```json
{
  "data_quality": {
    "missing_blocks": ["clinical_note"],
    "warnings": [
      {
        "type": "missing_discharge_note",
        "severity": "high",
        "message": "No discharge note found for this hadm_id."
      }
    ],
    "extraction_notes": []
  }
}
```

---

## 15. First implementation target

Build the first extractor as:

```text
scripts/extract_patient_snapshot_duckdb.py
```

Input:

```text
--db-path ~/data/mimic-iv.duckdb
--hadm-id <HADM_ID>
--output snapshots/<HADM_ID>.json
```

Output:

```text
snapshots/<HADM_ID>.json
```

Minimum first version:

1. Load patient/admission demographics
2. Load diagnoses with text descriptions
3. Load procedures with text descriptions
4. Load first-24h labs with labels
5. Load medications
6. Load ICU summary
7. Load selected derived scores if ICU exists
8. Load discharge note if available
9. Write one JSON snapshot

---

## 16. Next file after this

After this data map, the next implementation document should be:

```text
docs/extraction_plan_duckdb.md
```

That file should define the actual SQL queries and Python functions needed to produce the patient snapshot.
