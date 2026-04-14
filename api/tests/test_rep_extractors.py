from app.services.comprehensive_extractor import ComprehensiveExtractor
from app.services.rep_endpoints_extractor import RepEndpointsExtractor
from app.services.rep_secrets_extractor import RepSecretsExtractor


def test_rep_endpoint_extractor_detects_endpoints_and_methods():
    content = """
    fetch('/api/users');
    fetch('/api/users');
    axios.delete('https://api.example.com/v1/users/123');
    const gql = '/graphql';
    """

    extractor = RepEndpointsExtractor()
    results = extractor.extract(content, "https://example.com/app.js")

    assert len(results) >= 3
    values = {item["url"] for item in results}
    assert "https://api.example.com/v1/users/123" in values
    assert "/api/users" in values
    assert "/graphql" in values

    delete_entry = next(item for item in results if item["url"] == "https://api.example.com/v1/users/123")
    assert delete_entry["method"] == "DELETE"
    assert delete_entry["extractor"] == "rep_endpoint_extractor"


def test_rep_secrets_extractor_detects_kingfisher_secret():
    content = "const token = 'aio_giXk31KzM05IVxHRwJwtpNGClUE5';"

    extractor = RepSecretsExtractor()
    results = extractor.extract(content, "https://example.com/app.js")

    assert results
    secret = results[0]
    assert secret["extractor"] == "rep_kingfisher"
    assert secret["ruleId"] == "kingfisher.adafruitio.1"
    assert secret["value"].startswith("aio_")
    assert secret["line"] == 1


def test_comprehensive_extractor_defaults_to_rep_and_jsluice_off():
    content = """
    fetch('/api/orders');
    const token = 'aio_giXk31KzM05IVxHRwJwtpNGClUE5';
    """

    extractor = ComprehensiveExtractor()
    result = extractor.extract_all(
        content,
        {"url": "https://example.com/app.js"},
        options={"include_sourcemap": False},
    )

    extractors_used = set(result["extractors_used"])
    assert "rep_endpoint_extractor" in extractors_used
    assert "rep_kingfisher" in extractors_used
    assert "jsluice_urls" not in extractors_used
    assert "jsluice_secrets" not in extractors_used

    analysis = result["analysis"]
    assert any(item["extractor"] in {"rep_endpoint_extractor", "multiple"} for item in analysis["endpoints"])
    assert any(item["extractor"] in {"rep_kingfisher", "multiple"} for item in analysis["secrets"])


def test_comprehensive_extractor_can_disable_rep_extractors():
    content = """
    fetch('/api/orders');
    const token = 'aio_giXk31KzM05IVxHRwJwtpNGClUE5';
    """

    extractor = ComprehensiveExtractor()
    result = extractor.extract_all(
        content,
        {"url": "https://example.com/app.js"},
        options={
            "include_sourcemap": False,
            "use_rep_endpoints": False,
            "use_rep_secrets": False,
            "use_custom_patterns": False,
            "use_jsluice_endpoints": False,
            "use_jsluice_secrets": False,
        },
    )

    analysis = result["analysis"]
    assert analysis["endpoints"] == []
    assert analysis["secrets"] == []
