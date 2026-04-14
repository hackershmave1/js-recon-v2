#!/usr/bin/env bash

set -u

API_BASE="${API_BASE:-${1:-http://localhost:3000}}"
JS_URL="${JS_URL:-}"
EXPECTED_MAP_URL="${EXPECTED_MAP_URL:-}"
ALLOWED_DOMAIN="wishandwash.co.il"

TMP_DIR="$(mktemp -d)"
PASS=0
FAIL=0

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

line() {
  printf '%s\n' "------------------------------------------------------------"
}

pass() {
  PASS=$((PASS + 1))
  printf '[PASS] %s\n' "$1"
}

fail() {
  FAIL=$((FAIL + 1))
  printf '[FAIL] %s\n' "$1"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf '[FATAL] Missing required command: %s\n' "$1"
    exit 1
  fi
}

require_command curl
require_command python3
require_command sha256sum

if [[ -z "$JS_URL" ]]; then
  printf '[FATAL] JS_URL is required and must point to %s\n' "$ALLOWED_DOMAIN"
  exit 1
fi

if [[ -z "$EXPECTED_MAP_URL" ]]; then
  EXPECTED_MAP_URL="${JS_URL}.map"
fi

assert_allowed_domain() {
  local url="$1"
  local host
  host="$(python3 - "$url" <<'PY'
import sys
from urllib.parse import urlparse
print((urlparse(sys.argv[1]).hostname or "").lower())
PY
)"
  if [[ "$host" == "$ALLOWED_DOMAIN" || "$host" == *".${ALLOWED_DOMAIN}" ]]; then
    return 0
  fi
  printf '[FATAL] URL host must be %s (got: %s)\n' "$ALLOWED_DOMAIN" "$host"
  exit 1
}

assert_allowed_domain "$JS_URL"
assert_allowed_domain "$EXPECTED_MAP_URL"

JS_FILE="$TMP_DIR/app.min.js"
MAP_FILE="$TMP_DIR/app.min.js.map"
INGEST_PAYLOAD="$TMP_DIR/ingest_payload.json"
SAVE_RESP="$TMP_DIR/save_resp.json"
FILE_RESP="$TMP_DIR/file_resp.json"
ANALYSIS_RESP="$TMP_DIR/analysis_resp.json"
SOURCEMAP_RESP="$TMP_DIR/sourcemap_resp.json"
RECON_RESP="$TMP_DIR/reconstructed_resp.json"

line
printf 'Wishandwash sourcemap flow test\n'
printf 'API_BASE=%s\n' "$API_BASE"
printf 'JS_URL=%s\n' "$JS_URL"
printf 'EXPECTED_MAP_URL=%s\n' "$EXPECTED_MAP_URL"
line

if curl -fsSL "$JS_URL" -o "$JS_FILE"; then
  pass "Fetched JavaScript target"
else
  fail "Failed to fetch JavaScript target"
  printf 'Smoke test complete: PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
  exit 1
fi

DETECTED_MAP_URL="$(python3 - "$JS_FILE" "$JS_URL" <<'PY'
import re
import sys
from urllib.parse import urljoin

js_path, js_url = sys.argv[1], sys.argv[2]
content = open(js_path, "r", encoding="utf-8", errors="replace").read()
patterns = [
    r'//# sourceMappingURL=(.+?)(?:\n|$)',
    r'/\*# sourceMappingURL=(.+?)\*/',
    r'//@ sourceMappingURL=(.+?)(?:\n|$)',
]
for pattern in patterns:
    m = re.search(pattern, content)
    if not m:
        continue
    raw = m.group(1).strip()
    if raw.startswith(("http://", "https://", "data:")):
        print(raw)
    else:
        print(urljoin(js_url, raw))
    break
PY
)"

if [[ -n "$DETECTED_MAP_URL" ]]; then
  pass "Detected sourceMappingURL from JavaScript"
  if [[ "$DETECTED_MAP_URL" != data:* ]]; then
    assert_allowed_domain "$DETECTED_MAP_URL"
  fi
else
  fail "Could not detect sourceMappingURL in JavaScript"
fi

if python3 - "$DETECTED_MAP_URL" "$EXPECTED_MAP_URL" <<'PY'
import sys
from urllib.parse import urlparse

detected, expected = sys.argv[1], sys.argv[2]
if detected == expected:
    raise SystemExit(0)
d = urlparse(detected)
e = urlparse(expected)
same_target = d.netloc.lower() == e.netloc.lower() and d.path == e.path and (d.query or "") == (e.query or "")
raise SystemExit(0 if same_target else 1)
PY
then
  pass "Detected source map matches expected target (allowing scheme differences)"
else
  fail "Detected source map differs (detected=$DETECTED_MAP_URL)"
fi

if curl -fsSL "$EXPECTED_MAP_URL" -o "$MAP_FILE"; then
  pass "Fetched source map target"
else
  fail "Failed to fetch source map target"
fi

if python3 - "$MAP_FILE" <<'PY'
import json
import sys
path = sys.argv[1]
text = open(path, "r", encoding="utf-8", errors="replace").read()
json.loads(text)
PY
then
  pass "Source map is valid JSON"
else
  fail "Source map is not valid JSON"
fi

SESSION_ID="$(python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
)"
CONTENT_HASH="$(sha256sum "$JS_FILE" | awk '{print $1}')"
CONTENT_LENGTH="$(wc -c < "$JS_FILE" | tr -d ' ')"

python3 - "$JS_FILE" "$MAP_FILE" "$JS_URL" "$DETECTED_MAP_URL" "$EXPECTED_MAP_URL" "$SESSION_ID" "$CONTENT_HASH" "$CONTENT_LENGTH" "$INGEST_PAYLOAD" <<'PY'
import json
import sys

js_path, map_path, js_url, detected_map_url, expected_map_url, session_id, content_hash, content_length, out_path = sys.argv[1:]
content = open(js_path, "r", encoding="utf-8", errors="replace").read()
map_text = open(map_path, "r", encoding="utf-8", errors="replace").read()
map_json = json.loads(map_text)
map_url = detected_map_url or expected_map_url
payload = {
    "metadata": {
        "sessionId": session_id,
        "performAnalysis": True,
        "analysisOptions": {
            "include_sourcemap": True,
            "resolve_urls": True,
            "use_rep_endpoints": True,
            "use_rep_secrets": True,
            "use_jsluice_endpoints": False,
            "use_jsluice_secrets": False
        }
    },
    "files": [
        {
            "url": js_url,
            "contentHash": content_hash,
            "sessionId": session_id,
            "capturedAt": "2026-02-09T14:10:00Z",
            "contentType": "application/javascript",
            "contentEncoding": "identity",
            "contentLength": int(content_length),
            "content": content,
            "sourceMapUrl": map_url,
            "sourceMapContent": map_json,
            "dependencies": []
        }
    ]
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(payload, f)
PY

SAVE_CODE="$(curl -s -o "$SAVE_RESP" -w '%{http_code}' -X POST "$API_BASE/api/save-files" -H 'Content-Type: application/json' --data-binary "@$INGEST_PAYLOAD")"
if [[ "$SAVE_CODE" == "200" ]]; then
  pass "POST /api/save-files succeeded"
else
  fail "POST /api/save-files failed (status=$SAVE_CODE)"
  if [[ -s "$SAVE_RESP" ]]; then
    printf 'Response: %s\n' "$(head -c 400 "$SAVE_RESP")"
  fi
  printf 'Smoke test complete: PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
  exit 1
fi

FILE_ID="$(python3 - "$SAVE_RESP" <<'PY'
import json
import sys
body = json.load(open(sys.argv[1], "r", encoding="utf-8"))
file_ids = body.get("fileIds") or []
print(file_ids[0] if file_ids else "")
PY
)"

if [[ -n "$FILE_ID" ]]; then
  pass "Received fileId from ingestion response ($FILE_ID)"
else
  fail "Ingestion response missing fileId"
  printf 'Smoke test complete: PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
  exit 1
fi

FILE_CODE="$(curl -s -o "$FILE_RESP" -w '%{http_code}' "$API_BASE/api/files/$FILE_ID")"
if [[ "$FILE_CODE" == "200" ]]; then
  pass "GET /api/files/{fileId} succeeded"
else
  fail "GET /api/files/{fileId} failed (status=$FILE_CODE)"
fi

ANALYSIS_CODE="$(curl -s -o "$ANALYSIS_RESP" -w '%{http_code}' "$API_BASE/api/files/$FILE_ID/analysis")"
if [[ "$ANALYSIS_CODE" == "200" ]]; then
  pass "GET /api/files/{fileId}/analysis succeeded"
else
  fail "GET /api/files/{fileId}/analysis failed (status=$ANALYSIS_CODE)"
fi

SOURCEMAP_CODE="$(curl -s -o "$SOURCEMAP_RESP" -w '%{http_code}' "$API_BASE/api/files/$FILE_ID/sourcemap-content")"
if [[ "$SOURCEMAP_CODE" == "200" ]]; then
  pass "GET /api/files/{fileId}/sourcemap-content succeeded"
else
  fail "GET /api/files/{fileId}/sourcemap-content failed (status=$SOURCEMAP_CODE)"
fi

RECON_CODE="$(curl -s -o "$RECON_RESP" -w '%{http_code}' "$API_BASE/api/files/$FILE_ID/reconstructed-sources")"
if [[ "$RECON_CODE" == "200" ]]; then
  pass "GET /api/files/{fileId}/reconstructed-sources succeeded"
else
  fail "GET /api/files/{fileId}/reconstructed-sources failed (status=$RECON_CODE)"
fi

line
python3 - "$SAVE_RESP" "$FILE_RESP" "$ANALYSIS_RESP" "$SOURCEMAP_RESP" "$RECON_RESP" <<'PY'
import json
import sys

def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

save, file_meta, analysis, sourcemap, recon = [read_json(p) for p in sys.argv[1:]]
file_row = (save.get("files") or [{}])[0]
src = file_row.get("sourceMap") or {}
ana = file_row.get("analysis") or {}

analysis_body = analysis if isinstance(analysis, dict) else {}
analysis_data = analysis_body.get("analysis") if isinstance(analysis_body.get("analysis"), dict) else {}
analysis_stats = analysis_data.get("stats") if isinstance(analysis_data.get("stats"), dict) else {}

summary = {
    "ingestion": {
        "success": save.get("success"),
        "stored": save.get("stored"),
        "fileId": (save.get("fileIds") or [None])[0],
    },
    "sourcemap": {
        "detectedMapUrl": src.get("detectedMapUrl"),
        "mapUrl": src.get("mapUrl"),
        "processingStatus": src.get("processingStatus"),
        "processingError": src.get("processingError"),
        "reconstructedFilesCount": src.get("reconstructedFilesCount"),
    },
    "analysis": {
        "status": analysis_body.get("status"),
        "error": analysis_body.get("error"),
        "extractorsUsed": analysis_body.get("extractors_used"),
        "totalEndpoints": analysis_stats.get("total_endpoints"),
        "totalSecrets": analysis_stats.get("total_secrets"),
        "totalDependencies": analysis_stats.get("total_dependencies"),
    },
    "storedSourcemapContent": {
        "available": bool((sourcemap or {}).get("content")),
        "length": len((sourcemap or {}).get("content") or "")
    },
    "reconstructedSources": {
        "totalFiles": ((recon or {}).get("stats") or {}).get("totalFiles"),
        "totalSize": ((recon or {}).get("stats") or {}).get("totalSize")
    }
}

print(json.dumps(summary, indent=2))
PY
line
printf 'Smoke test complete: PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
