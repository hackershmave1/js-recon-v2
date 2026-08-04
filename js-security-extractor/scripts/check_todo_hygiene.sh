#!/usr/bin/env bash

set -euo pipefail

TODO_FILE="${1:-TODO.md}"

if [[ ! -f "$TODO_FILE" ]]; then
  echo "[FAIL] TODO file not found: $TODO_FILE" >&2
  exit 2
fi

declare -A ALLOWED_STATUS=(
  ["OPEN"]=1
  ["CLAIMED"]=1
  ["IN_PROGRESS"]=1
  ["IN_REVIEW"]=1
  ["BLOCKED_HUMAN"]=1
)

line_no=0
status_count=0
invalid_count=0

while IFS= read -r line; do
  line_no=$((line_no + 1))

  if [[ "$line" =~ ^-[[:space:]]Status:[[:space:]]([A-Z_]+)[[:space:]]*$ ]]; then
    status_count=$((status_count + 1))
    status_value="${BASH_REMATCH[1]}"

    if [[ -z "${ALLOWED_STATUS[$status_value]+x}" ]]; then
      invalid_count=$((invalid_count + 1))
      echo "[FAIL] $TODO_FILE:$line_no contains closed/invalid status '$status_value'. Move this task to COMPLETED_TASKS.md."
    fi
  fi
done < "$TODO_FILE"

if [[ "$status_count" -eq 0 ]]; then
  echo "[FAIL] No task status lines found in $TODO_FILE (expected lines like '- Status: OPEN')." >&2
  exit 1
fi

if [[ "$invalid_count" -gt 0 ]]; then
  echo "[FAIL] TODO hygiene check failed ($invalid_count invalid status line(s))."
  exit 1
fi

echo "[PASS] TODO hygiene check passed ($status_count active status line(s), no closed statuses)."
