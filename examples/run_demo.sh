#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 scripts/run_safeprobemd_case.py \
  --snapshot examples/mock_patient_snapshot.json \
  --output outputs/mock_case_safeprobemd_output.json

echo "Synthetic demo complete: outputs/mock_case_safeprobemd_output.json"
