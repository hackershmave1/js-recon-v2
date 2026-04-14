#!/usr/bin/env bash

set -u

# Testing-domain policy: use wishandwash.co.il (or subdomain via TEST_DOMAIN override).
# Do not use example.com for smoke validation payloads.

API_BASE="${API_BASE:-${1:-http://localhost:3000}}"
ORIGIN="${ORIGIN:-http://localhost:3000}"
TEST_DOMAIN="${TEST_DOMAIN:-wishandwash.co.il}"
TMP_DIR="$(mktemp -d)"

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

LAST_STATUS=""
LAST_BODY_FILE=""
LAST_HEADER_FILE=""
LAST_ERR_FILE=""

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

print_line() {
  printf '%s\n' "------------------------------------------------------------"
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '[PASS] %s\n' "$1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf '[FAIL] %s\n' "$1"
}

warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  printf '[WARN] %s\n' "$1"
}

body_contains() {
  local file_path="$1"
  local needle="$2"
  [[ -f "$file_path" ]] && grep -Fqi "$needle" "$file_path"
}

body_not_contains() {
  local file_path="$1"
  local needle="$2"
  [[ -f "$file_path" ]] && ! grep -Fqi "$needle" "$file_path"
}

show_failure_context() {
  local body_file="$1"
  local err_file="$2"
  if [[ -s "$err_file" ]]; then
    printf '  stderr: %s\n' "$(tr '\n' ' ' < "$err_file" | cut -c1-250)"
  fi
  if [[ -s "$body_file" ]]; then
    printf '  body: %s\n' "$(tr '\n' ' ' < "$body_file" | cut -c1-350)"
  fi
}

run_curl() {
  local method="$1"
  local url="$2"
  local data="${3:-}"
  local extra_header_key="${4:-}"
  local extra_header_val="${5:-}"
  local extra_header2_key="${6:-}"
  local extra_header2_val="${7:-}"

  LAST_BODY_FILE="$TMP_DIR/body.$RANDOM.$RANDOM"
  LAST_HEADER_FILE="$TMP_DIR/headers.$RANDOM.$RANDOM"
  LAST_ERR_FILE="$TMP_DIR/err.$RANDOM.$RANDOM"
  : > "$LAST_BODY_FILE"
  : > "$LAST_HEADER_FILE"
  : > "$LAST_ERR_FILE"

  local -a cmd
  cmd=(curl -sS -m 20 -D "$LAST_HEADER_FILE" -o "$LAST_BODY_FILE" -w '%{http_code}' -X "$method" "$url")

  if [[ -n "$data" ]]; then
    cmd+=(-H "Content-Type: application/json" --data "$data")
  fi

  if [[ -n "$extra_header_key" ]]; then
    cmd+=(-H "$extra_header_key: $extra_header_val")
  fi

  if [[ -n "$extra_header2_key" ]]; then
    cmd+=(-H "$extra_header2_key: $extra_header2_val")
  fi

  LAST_STATUS="$("${cmd[@]}" 2>"$LAST_ERR_FILE" || true)"
  if [[ -z "$LAST_STATUS" ]]; then
    LAST_STATUS="000"
  fi
}

assert_status() {
  local test_name="$1"
  local expected_regex="$2"

  if [[ "$LAST_STATUS" =~ ^($expected_regex)$ ]]; then
    pass "$test_name (status=$LAST_STATUS)"
  else
    fail "$test_name (expected=$expected_regex actual=$LAST_STATUS)"
    show_failure_context "$LAST_BODY_FILE" "$LAST_ERR_FILE"
  fi
}

parse_json_field() {
  local file_path="$1"
  local field="$2"
  python3 - "$file_path" "$field" <<'PY'
import json
import sys

path, field = sys.argv[1], sys.argv[2]
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    print("")
    sys.exit(0)

value = data
for part in field.split("."):
    if isinstance(value, dict) and part in value:
        value = value[part]
    elif isinstance(value, list):
        if not part.isdigit():
            print("")
            sys.exit(0)
        idx = int(part)
        if idx < 0 or idx >= len(value):
            print("")
            sys.exit(0)
        value = value[idx]
    else:
        print("")
        sys.exit(0)

print("" if value is None else str(value))
PY
}

generate_uuid() {
  python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
}

print_line
printf 'API smoke test started\n'
printf 'API_BASE=%s\n' "$API_BASE"
printf 'ORIGIN=%s\n' "$ORIGIN"
print_line

# Optional fallback if localhost is not reachable in current environment.
run_curl "GET" "$API_BASE/health"
if [[ "$LAST_STATUS" != "200" && "$API_BASE" == *"localhost"* ]]; then
  alt_base="${API_BASE/localhost/127.0.0.1}"
  run_curl "GET" "$alt_base/health"
  if [[ "$LAST_STATUS" == "200" ]]; then
    warn "Primary API_BASE unreachable; falling back to $alt_base"
    API_BASE="$alt_base"
  fi
fi

SESSION_ID="${SESSION_ID:-$(generate_uuid)}"
CONTENT_HASH="smoke-$(date +%s)"
JS_CONTENT="function smoke(){return 42;} smoke();"
CONTENT_LENGTH="$(printf '%s' "$JS_CONTENT" | wc -c | tr -d ' ')"

VALID_PAYLOAD="$(cat <<JSON
{
  "metadata": {
    "sessionId": "$SESSION_ID",
    "performAnalysis": false
  },
  "files": [
    {
      "url": "https://${TEST_DOMAIN}/app.js",
      "contentHash": "$CONTENT_HASH",
      "sessionId": "$SESSION_ID",
      "capturedAt": "2026-02-09T10:00:00Z",
      "contentType": "application/javascript",
      "contentEncoding": "identity",
      "contentLength": $CONTENT_LENGTH,
      "content": "$JS_CONTENT",
      "dependencies": []
    }
  ]
}
JSON
)"

INVALID_PAYLOAD="$(cat <<JSON
{
  "metadata": {
    "sessionId": "$SESSION_ID"
  },
  "files": [
    {
      "url": "ftp://${TEST_DOMAIN}/bad.js",
      "contentHash": "bad-$CONTENT_HASH",
      "sessionId": "$SESSION_ID",
      "contentType": "application/javascript",
      "contentEncoding": "identity",
      "contentLength": 1,
      "content": "x",
      "dependencies": []
    }
  ]
}
JSON
)"

run_curl "GET" "$API_BASE/health"
assert_status "Health check" "200"
if body_contains "$LAST_BODY_FILE" "\"healthy\""; then
  pass "Health payload contains healthy status"
else
  fail "Health payload contains healthy status"
  show_failure_context "$LAST_BODY_FILE" "$LAST_ERR_FILE"
fi

run_curl "GET" "$API_BASE/api"
assert_status "API root" "200"

run_curl "OPTIONS" "$API_BASE/health" "" "Origin" "$ORIGIN" "Access-Control-Request-Method" "GET"
assert_status "CORS preflight" "200"
if body_contains "$LAST_HEADER_FILE" "access-control-allow-origin: $ORIGIN"; then
  pass "CORS allow-origin matches $ORIGIN"
else
  fail "CORS allow-origin matches $ORIGIN"
  show_failure_context "$LAST_HEADER_FILE" "$LAST_ERR_FILE"
fi

run_curl "POST" "$API_BASE/api/save-files" "$VALID_PAYLOAD"
assert_status "Save files (valid payload)" "200"
if body_contains "$LAST_BODY_FILE" "\"success\":true"; then
  pass "Save files response success=true"
else
  fail "Save files response success=true"
  show_failure_context "$LAST_BODY_FILE" "$LAST_ERR_FILE"
fi

FILE_ID="$(parse_json_field "$LAST_BODY_FILE" "fileIds.0")"
if [[ -n "$FILE_ID" ]]; then
  pass "Extract fileId from upload response ($FILE_ID)"
else
  fail "Extract fileId from upload response"
fi

run_curl "GET" "$API_BASE/api/sessions"
assert_status "List sessions" "200"
if body_contains "$LAST_BODY_FILE" "$SESSION_ID"; then
  pass "Sessions list contains test session"
else
  fail "Sessions list contains test session"
  show_failure_context "$LAST_BODY_FILE" "$LAST_ERR_FILE"
fi

run_curl "GET" "$API_BASE/api/sessions/$SESSION_ID/files?dedupe=true"
assert_status "List files for session" "200"

if [[ -n "$FILE_ID" ]]; then
  run_curl "GET" "$API_BASE/api/files/$FILE_ID"
  assert_status "Get file metadata" "200"
  if body_not_contains "$LAST_BODY_FILE" "storedPath" && body_not_contains "$LAST_BODY_FILE" "mapPath"; then
    pass "File metadata does not leak storedPath/mapPath"
  else
    fail "File metadata does not leak storedPath/mapPath"
    show_failure_context "$LAST_BODY_FILE" "$LAST_ERR_FILE"
  fi

  run_curl "GET" "$API_BASE/api/files/$FILE_ID/content"
  assert_status "Get file content" "200"

  run_curl "GET" "$API_BASE/api/files/$FILE_ID/dependencies?recursive=true"
  assert_status "Get file dependencies" "200"

  run_curl "POST" "$API_BASE/api/files/$FILE_ID/analyze" '{"options":{"includeSourceMap":true,"resolveUrls":true}}'
  assert_status "Analyze file" "200"

  run_curl "GET" "$API_BASE/api/files/$FILE_ID/analysis"
  assert_status "Get file analysis" "200|404"
  if [[ "$LAST_STATUS" == "404" ]]; then
    warn "Analysis not found yet (404). Re-run this check after analyze completes."
  fi
fi

run_curl "POST" "$API_BASE/api/save-files" "$INVALID_PAYLOAD"
assert_status "Save files (invalid ftp URL)" "422"

if [[ -n "$FILE_ID" ]]; then
  run_curl "DELETE" "$API_BASE/api/files/$FILE_ID"
  assert_status "Delete file" "200"
  if body_not_contains "$LAST_BODY_FILE" "deletedPaths"; then
    pass "Delete file response does not leak deleted paths"
  else
    fail "Delete file response does not leak deleted paths"
    show_failure_context "$LAST_BODY_FILE" "$LAST_ERR_FILE"
  fi
fi

run_curl "DELETE" "$API_BASE/api/sessions/$SESSION_ID"
assert_status "Delete session" "200|404"
if [[ "$LAST_STATUS" == "404" ]]; then
  warn "Session already deleted or not found during cleanup."
fi

print_line
printf 'Smoke test complete: PASS=%d FAIL=%d WARN=%d\n' "$PASS_COUNT" "$FAIL_COUNT" "$WARN_COUNT"
print_line

if (( FAIL_COUNT > 0 )); then
  exit 1
fi

exit 0
