from unittest.mock import Mock, patch

from app.api.routes.ingestion import (
    classify_sourcemap_error,
    process_sourcemap_safely,
)
from app.models.source_map import SourceMap


def test_classify_sourcemap_error_classes():
    assert classify_sourcemap_error("HTTP error fetching source map: 404") == "fetch_http_404"
    assert classify_sourcemap_error("HTTP error fetching source map: 503") == "fetch_http_5xx"
    assert classify_sourcemap_error("Invalid JSON in source map: bad") == "decode_invalid_json"
    assert classify_sourcemap_error("Request error fetching source map: timeout") == "processing_timeout"


def test_transient_error_retries_and_is_classified():
    mock_record = Mock(spec=SourceMap)
    mock_record.processing_status = "pending"
    mock_db = Mock()
    calls = {"count": 0}

    with patch("app.api.routes.ingestion.NativeSourceMapProcessor") as mock_processor_class:
        mock_processor = mock_processor_class.return_value

        async def async_process(*args, **kwargs):
            calls["count"] += 1
            return {
                "success": False,
                "files": [],
                "error": "HTTP error fetching source map: 503",
            }

        mock_processor.process_sourcemap_from_url = async_process

        with patch("app.api.routes.ingestion.httpx.head") as mock_head, patch("time.sleep") as mock_sleep:
            mock_head.side_effect = Exception("head unavailable")
            process_sourcemap_safely(mock_record, "https://example.com/transient.map", mock_db)

    assert calls["count"] == 3
    assert mock_sleep.call_count == 2
    assert mock_record.processing_status == "failed"
    assert mock_record.processing_error.startswith("[fetch_http_5xx]")


def test_non_retriable_404_fails_once():
    mock_record = Mock(spec=SourceMap)
    mock_record.processing_status = "pending"
    mock_db = Mock()
    calls = {"count": 0}

    with patch("app.api.routes.ingestion.NativeSourceMapProcessor") as mock_processor_class:
        mock_processor = mock_processor_class.return_value

        async def async_process(*args, **kwargs):
            calls["count"] += 1
            return {
                "success": False,
                "files": [],
                "error": "HTTP error fetching source map: 404",
            }

        mock_processor.process_sourcemap_from_url = async_process

        with patch("app.api.routes.ingestion.httpx.head") as mock_head, patch("time.sleep") as mock_sleep:
            mock_head.side_effect = Exception("head unavailable")
            process_sourcemap_safely(mock_record, "https://example.com/not-found.map", mock_db)

    assert calls["count"] == 1
    assert mock_sleep.call_count == 0
    assert mock_record.processing_status == "failed"
    assert mock_record.processing_error.startswith("[fetch_http_404]")
