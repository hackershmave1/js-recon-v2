from unittest.mock import Mock, patch

from app.api.routes.ingestion import process_sourcemap_safely
from app.models.source_map import SourceMap
from app.services.auth_context import (
    get_auth_replay_headers,
    redact_file_metadata_for_output,
    sanitize_captured_auth_context,
)


def test_sanitize_auth_context_allows_allowlisted_headers_only():
    raw_context = {
        "schemaVersion": "1.0",
        "source": "extension.webRequest",
        "capturedAt": "2026-02-10T22:40:00Z",
        "domain": "wishandwash.co.il",
        "requestUrl": "https://wishandwash.co.il/assets/app.js",
        "headers": {
            "Authorization": "Bearer wish-token",
            "Cookie": "sessionid=abc123; csrftoken=xyz",
            "X-Api-Key": "api-key-1234",
            "X-Forwarded-For": "10.0.0.1",
        },
    }

    sanitized = sanitize_captured_auth_context(
        raw_context,
        "https://wishandwash.co.il/assets/app.js",
    )

    assert sanitized is not None
    assert sanitized["domain"] == "wishandwash.co.il"
    assert set(sanitized["replayHeaders"].keys()) == {"authorization", "cookie", "x-api-key"}
    assert "x-forwarded-for" not in sanitized["replayHeaders"]
    assert sanitized["cookie"]["present"] is True
    assert sanitized["cookie"]["count"] == 2
    assert "sessionid" in [name.lower() for name in sanitized["cookie"]["names"]]


def test_sanitize_auth_context_rejects_cross_domain_context():
    raw_context = {
        "domain": "api.other-domain.tld",
        "requestUrl": "https://api.other-domain.tld/chunks/app.js",
        "headers": {
            "Authorization": "Bearer wish-token",
        },
    }

    sanitized = sanitize_captured_auth_context(
        raw_context,
        "https://wishandwash.co.il/assets/app.js",
    )
    assert sanitized is None


def test_get_auth_replay_headers_respects_domain_scope():
    auth_context = {
        "domain": "wishandwash.co.il",
        "replayHeaders": {
            "authorization": "Bearer wish-token",
            "cookie": "sessionid=abc123",
        },
    }

    allowed_headers = get_auth_replay_headers(
        auth_context,
        "https://static.wishandwash.co.il/assets/app.js.map",
    )
    blocked_headers = get_auth_replay_headers(
        auth_context,
        "https://unauthorized.notwishandwash.local/assets/app.js.map",
    )

    assert allowed_headers is not None
    assert allowed_headers["authorization"] == "Bearer wish-token"
    assert blocked_headers is None


def test_redact_file_metadata_hides_replay_headers():
    metadata = {
        "url": "https://wishandwash.co.il/assets/app.js",
        "authContext": {
            "domain": "wishandwash.co.il",
            "replayHeaders": {
                "authorization": "Bearer wish-token",
                "cookie": "sessionid=abc123; csrftoken=xyz",
            },
        },
    }

    redacted = redact_file_metadata_for_output(metadata)
    assert redacted is not None
    assert "replayHeaders" not in redacted["authContext"]
    assert redacted["authContext"]["headers"]["authorization"] == "Bearer ***"
    assert "cookie(s) redacted" in redacted["authContext"]["headers"]["cookie"]


def test_process_sourcemap_retries_with_auth_context_after_direct_403():
    mock_record = Mock(spec=SourceMap)
    mock_record.processing_status = "pending"
    mock_record.processing_error = None
    mock_record.reconstructed_files_count = 0
    mock_record.processed_at = None

    mock_db = Mock()
    call_headers = []

    async def fake_process(js_url, sourcemap_url, custom_headers=None):
        call_headers.append(custom_headers)
        if custom_headers is None:
            return {
                "success": False,
                "files": [],
                "error": "HTTP error fetching source map: 403",
                "stats": {"total_files": 0, "total_size": 0},
            }
        return {
            "success": True,
            "files": [
                {
                    "path": "src/main.js",
                    "content": "const x = 1;",
                    "size": 12,
                    "type": "javascript",
                }
            ],
            "stats": {"total_files": 1, "total_size": 12},
        }

    with patch("app.api.routes.ingestion.NativeSourceMapProcessor") as mock_processor_class, patch(
        "app.api.routes.ingestion.httpx.head"
    ) as mock_head:
        mock_processor = mock_processor_class.return_value
        mock_processor.process_sourcemap_from_url = fake_process
        mock_head.side_effect = Exception("HEAD skipped in test")

        process_sourcemap_safely(
            sourcemap_record=mock_record,
            sourcemap_url="https://wishandwash.co.il/assets/app.js.map",
            db=mock_db,
            js_url="https://wishandwash.co.il/assets/app.js",
            auth_context={
                "domain": "wishandwash.co.il",
                "replayHeaders": {
                    "authorization": "Bearer wish-token",
                    "cookie": "sessionid=abc123",
                },
            },
        )

    assert len(call_headers) == 2
    assert call_headers[0] is None
    assert isinstance(call_headers[1], dict)
    assert call_headers[1]["authorization"] == "Bearer wish-token"
    assert mock_record.processing_status == "completed"
    assert mock_record.processing_error is None
    assert mock_record.reconstructed_files_count == 1
    assert mock_record.processed_at is not None
