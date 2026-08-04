import asyncio

from app.services.native_sourcemap_processor import NativeSourceMapProcessor


SOURCEMAP_CONTENT = """
{
  "version": 3,
  "file": "app.min.js",
  "sources": ["src/app.js"],
  "sourcesContent": ["const apiUrl = '/api/users';"],
  "names": [],
  "mappings": "AAAA"
}
""".strip()


def test_process_sourcemap_from_content_accepts_optional_base_url():
    processor = NativeSourceMapProcessor()

    result = asyncio.run(
        processor.process_sourcemap_from_content(
            SOURCEMAP_CONTENT,
            "https://example.com/static/app.min.js",
        )
    )

    assert result["success"] is True
    assert result["stats"]["total_files"] == 1
    assert result["files"][0]["path"] == "src/app.js"


def test_process_sourcemap_from_url_direct_map_url(monkeypatch):
    processor = NativeSourceMapProcessor()

    async def fake_fetch(url, custom_headers=None):
        assert url == "https://example.com/static/app.min.js.map"
        return SOURCEMAP_CONTENT

    async def fail_extract(*args, **kwargs):
        raise AssertionError("Extractor path should not be used when sourcemap_url is provided")

    monkeypatch.setattr(processor, "_fetch_sourcemap", fake_fetch)
    monkeypatch.setattr(processor, "_extract_sourcemap_url_from_js_url", fail_extract)

    result = asyncio.run(
        processor.process_sourcemap_from_url(
            "https://example.com/static/app.min.js",
            "https://example.com/static/app.min.js.map",
        )
    )

    assert result["success"] is True
    assert result["stats"]["total_files"] == 1


def test_process_sourcemap_from_url_extracted_map_url(monkeypatch):
    processor = NativeSourceMapProcessor()

    async def fake_extract(js_url, custom_headers=None):
        assert js_url == "https://example.com/static/app.min.js"
        return "https://example.com/static/app.min.js.map"

    async def fake_fetch(url, custom_headers=None):
        assert url == "https://example.com/static/app.min.js.map"
        return SOURCEMAP_CONTENT

    monkeypatch.setattr(processor, "_extract_sourcemap_url_from_js_url", fake_extract)
    monkeypatch.setattr(processor, "_fetch_sourcemap", fake_fetch)

    result = asyncio.run(
        processor.process_sourcemap_from_url("https://example.com/static/app.min.js")
    )

    assert result["success"] is True
    assert result["stats"]["total_files"] == 1
