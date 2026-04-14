"""
Tests for B-025 - Secret Rollup by Type+Value with Source Provenance

Tests secret deduplication and rollup functionality across multiple files.
"""

import pytest
from app.services.secret_rollup import SecretRollupService, SecretOccurrence, RolledUpSecret


class TestSecretRollupService:
    """Test secret rollup and deduplication service."""

    def test_empty_analysis_list(self):
        """Test rollup with empty analysis list."""
        service = SecretRollupService()
        result = service.rollup_secrets([])
        
        assert result["secrets"] == []
        assert result["summary"]["total_unique_secrets"] == 0
        assert result["summary"]["total_occurrences"] == 0

    def test_no_secrets_in_analyses(self):
        """Test rollup with analyses containing no secrets."""
        service = SecretRollupService()
        analyses = [
            {
                "id": "file1",
                "file": {"url": "https://example.com/app.js"},
                "analysis": {"endpoints": [], "dependencies": []}
            }
        ]
        
        result = service.rollup_secrets(analyses)
        
        assert result["secrets"] == []
        assert result["summary"]["total_unique_secrets"] == 0
        assert result["summary"]["total_occurrences"] == 0

    def test_single_unique_secret(self):
        """Test rollup with a single unique secret."""
        service = SecretRollupService()
        analyses = [
            {
                "id": "file1",
                "file": {"url": "https://example.com/app.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "abc123_secret_key",
                            "type": "api_key",
                            "ruleId": "api-key-rule",
                            "ruleName": "API Key Detection",
                            "confidence": "high",
                            "extractor": "rep_kingfisher",
                            "line": 42,
                            "column": 10,
                            "context": "const key = 'abc123_secret_key';"
                        }
                    ]
                }
            }
        ]
        
        result = service.rollup_secrets(analyses)
        
        assert len(result["secrets"]) == 1
        secret = result["secrets"][0]
        
        assert secret["type"] == "api_key"
        assert secret["value"] == "abc123_secret_key"
        assert secret["rule_id"] == "api-key-rule"
        assert secret["occurrence_count"] == 1
        assert secret["file_count"] == 1
        assert secret["extractors"] == ["rep_kingfisher"]
        assert secret["confidence_levels"] == ["high"]
        assert len(secret["occurrences"]) == 1
        
        occurrence = secret["occurrences"][0]
        assert occurrence["file_id"] == "file1"
        assert occurrence["file_url"] == "https://example.com/app.js"
        assert occurrence["line"] == 42
        assert occurrence["column"] == 10
        assert occurrence["context"] == "const key = 'abc123_secret_key';"
        assert occurrence["extractor"] == "rep_kingfisher"

    def test_duplicate_secret_same_file(self):
        """Test rollup with duplicate secret in same file."""
        service = SecretRollupService()
        analyses = [
            {
                "id": "file1",
                "file": {"url": "https://example.com/app.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "duplicate_key",
                            "type": "api_key",
                            "ruleId": "api-key-rule",
                            "confidence": "high",
                            "extractor": "rep_kingfisher",
                            "line": 10,
                            "context": "key1 = 'duplicate_key'"
                        },
                        {
                            "value": "duplicate_key",
                            "type": "api_key",
                            "ruleId": "api-key-rule",
                            "confidence": "high",
                            "extractor": "rep_kingfisher",
                            "line": 20,
                            "context": "key2 = 'duplicate_key'"
                        }
                    ]
                }
            }
        ]
        
        result = service.rollup_secrets(analyses)
        
        assert len(result["secrets"]) == 1  # Should be deduplicated
        secret = result["secrets"][0]
        
        assert secret["value"] == "duplicate_key"
        assert secret["occurrence_count"] == 2  # Two occurrences
        assert secret["file_count"] == 1  # Same file
        assert len(secret["occurrences"]) == 2
        
        # Verify both occurrences are tracked
        lines = [occ["line"] for occ in secret["occurrences"]]
        assert 10 in lines
        assert 20 in lines

    def test_duplicate_secret_across_files(self):
        """Test rollup with duplicate secret across multiple files."""
        service = SecretRollupService()
        analyses = [
            {
                "id": "file1",
                "file": {"url": "https://example.com/app.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "shared_secret",
                            "type": "token",
                            "ruleId": "token-rule",
                            "confidence": "high",
                            "extractor": "rep_kingfisher",
                            "line": 15
                        }
                    ]
                }
            },
            {
                "id": "file2", 
                "file": {"url": "https://example.com/utils.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "shared_secret",
                            "type": "token",
                            "ruleId": "token-rule",
                            "confidence": "medium",
                            "extractor": "jsluice_secrets",
                            "line": 5
                        }
                    ]
                }
            }
        ]
        
        result = service.rollup_secrets(analyses)
        
        assert len(result["secrets"]) == 1  # Should be deduplicated
        secret = result["secrets"][0]
        
        assert secret["value"] == "shared_secret"
        assert secret["occurrence_count"] == 2
        assert secret["file_count"] == 2  # Different files
        assert set(secret["extractors"]) == {"rep_kingfisher", "jsluice_secrets"}
        assert set(secret["confidence_levels"]) == {"high", "medium"}
        
        # Check occurrences from both files
        file_urls = [occ["file_url"] for occ in secret["occurrences"]]
        assert "https://example.com/app.js" in file_urls
        assert "https://example.com/utils.js" in file_urls

    def test_different_secret_types(self):
        """Test rollup with different secret types."""
        service = SecretRollupService()
        analyses = [
            {
                "id": "file1",
                "file": {"url": "https://example.com/config.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "secret123",
                            "type": "api_key",
                            "confidence": "high",
                            "extractor": "rep_kingfisher"
                        },
                        {
                            "value": "secret123",  # Same value, different type
                            "type": "password",
                            "confidence": "high", 
                            "extractor": "rep_kingfisher"
                        },
                        {
                            "value": "jwt_token_xyz",
                            "type": "jwt",
                            "confidence": "medium",
                            "extractor": "jsluice_secrets"
                        }
                    ]
                }
            }
        ]
        
        result = service.rollup_secrets(analyses)
        
        assert len(result["secrets"]) == 3  # Different types = different secrets
        
        secret_types = [secret["type"] for secret in result["secrets"]]
        assert "api_key" in secret_types
        assert "password" in secret_types
        assert "jwt" in secret_types

    def test_risk_score_calculation(self):
        """Test risk score calculation for different secret types and confidence levels."""
        service = SecretRollupService()
        analyses = [
            {
                "id": "file1",
                "file": {"url": "https://example.com/secrets.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "high_risk_key",
                            "type": "private_key",  # High risk type
                            "confidence": "high",  # High confidence
                            "extractor": "rep_kingfisher"  # Reliable extractor
                        },
                        {
                            "value": "low_risk_item",
                            "type": "webhook",  # Lower risk type
                            "confidence": "low",  # Low confidence
                            "extractor": "custom_patterns"  # Less reliable
                        }
                    ]
                }
            }
        ]
        
        result = service.rollup_secrets(analyses)
        
        assert len(result["secrets"]) == 2
        
        # Secrets should be sorted by risk score (descending)
        high_risk_secret = result["secrets"][0]
        low_risk_secret = result["secrets"][1]
        
        assert high_risk_secret["risk_score"] > low_risk_secret["risk_score"]
        assert high_risk_secret["value"] == "high_risk_key"
        assert low_risk_secret["value"] == "low_risk_item"

    def test_summary_statistics(self):
        """Test generation of summary statistics."""
        service = SecretRollupService()
        analyses = [
            {
                "id": "file1",
                "file": {"url": "https://example.com/app.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "api_secret_1",
                            "type": "api_key",
                            "confidence": "high",
                            "extractor": "rep_kingfisher"
                        },
                        {
                            "value": "api_secret_2", 
                            "type": "api_key",
                            "confidence": "medium",
                            "extractor": "rep_kingfisher"
                        },
                        {
                            "value": "jwt_token",
                            "type": "jwt",
                            "confidence": "high",
                            "extractor": "jsluice_secrets"
                        }
                    ]
                }
            }
        ]
        
        result = service.rollup_secrets(analyses)
        
        summary = result["summary"]
        assert summary["total_unique_secrets"] == 3
        assert summary["total_occurrences"] == 3
        assert summary["total_files_with_secrets"] == 1
        assert summary["by_type"]["api_key"] == 2
        assert summary["by_type"]["jwt"] == 1
        assert summary["by_confidence"]["high"] == 2  # api_secret_1 and jwt_token
        assert summary["by_confidence"]["medium"] == 1  # api_secret_2
        assert summary["by_extractor"]["rep_kingfisher"] == 2
        assert summary["by_extractor"]["jsluice_secrets"] == 1
        assert summary["deduplication_ratio"] == 1.0  # 3 occurrences / 3 unique = 1.0

    def test_deduplication_ratio(self):
        """Test deduplication ratio calculation with actual duplicates."""
        service = SecretRollupService()
        analyses = [
            {
                "id": "file1",
                "file": {"url": "https://example.com/app.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "repeated_secret",
                            "type": "api_key",
                            "confidence": "high",
                            "extractor": "rep_kingfisher",
                            "line": 10
                        },
                        {
                            "value": "repeated_secret",  # Duplicate
                            "type": "api_key", 
                            "confidence": "high",
                            "extractor": "rep_kingfisher",
                            "line": 20
                        }
                    ]
                }
            },
            {
                "id": "file2",
                "file": {"url": "https://example.com/utils.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "repeated_secret",  # Another duplicate
                            "type": "api_key",
                            "confidence": "high", 
                            "extractor": "rep_kingfisher",
                            "line": 5
                        }
                    ]
                }
            }
        ]
        
        result = service.rollup_secrets(analyses)
        
        assert len(result["secrets"]) == 1  # Only 1 unique secret
        
        summary = result["summary"]
        assert summary["total_unique_secrets"] == 1
        assert summary["total_occurrences"] == 3
        assert summary["deduplication_ratio"] == 3.0  # 3 occurrences / 1 unique = 3.0

    def test_empty_secret_values_ignored(self):
        """Test that empty or whitespace-only secret values are ignored."""
        service = SecretRollupService()
        analyses = [
            {
                "id": "file1", 
                "file": {"url": "https://example.com/app.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "",  # Empty value
                            "type": "api_key",
                            "confidence": "high"
                        },
                        {
                            "value": "   ",  # Whitespace only
                            "type": "token", 
                            "confidence": "medium"
                        },
                        {
                            "value": "valid_secret",
                            "type": "api_key",
                            "confidence": "high"
                        }
                    ]
                }
            }
        ]
        
        result = service.rollup_secrets(analyses)
        
        assert len(result["secrets"]) == 1  # Only valid secret
        assert result["secrets"][0]["value"] == "valid_secret"

    def test_case_sensitivity_in_grouping(self):
        """Test that secret grouping is case-sensitive for values but not types."""
        service = SecretRollupService()
        analyses = [
            {
                "id": "file1",
                "file": {"url": "https://example.com/app.js"},
                "analysis": {
                    "secrets": [
                        {
                            "value": "CaseSensitiveSecret",
                            "type": "API_KEY",  # Uppercase type
                            "confidence": "high"
                        },
                        {
                            "value": "casesensitivesecret",  # Different case value
                            "type": "api_key",  # Lowercase type
                            "confidence": "high"
                        },
                        {
                            "value": "CaseSensitiveSecret",  # Same case value
                            "type": "api_key",  # Lowercase type - should group with first
                            "confidence": "medium"
                        }
                    ]
                }
            }
        ]
        
        result = service.rollup_secrets(analyses)
        
        # Should have 2 unique secrets - types are normalized to lowercase but values are case-sensitive
        assert len(result["secrets"]) == 2
        
        secret_values = [secret["value"] for secret in result["secrets"]]
        assert "CaseSensitiveSecret" in secret_values
        assert "casesensitivesecret" in secret_values
        
        # Find the grouped secret
        grouped_secret = next(s for s in result["secrets"] if s["value"] == "CaseSensitiveSecret")
        assert grouped_secret["occurrence_count"] == 2  # Should have 2 occurrences
        assert set(grouped_secret["confidence_levels"]) == {"high", "medium"}


class TestSecretOccurrence:
    """Test SecretOccurrence dataclass."""

    def test_secret_occurrence_creation(self):
        """Test creation of SecretOccurrence."""
        occurrence = SecretOccurrence(
            file_id="test-file-id",
            file_url="https://example.com/test.js",
            line=42,
            column=15,
            context="const secret = 'value';",
            extractor="rep_kingfisher",
            rule_id="api-key-rule",
            confidence="high"
        )
        
        assert occurrence.file_id == "test-file-id"
        assert occurrence.file_url == "https://example.com/test.js"
        assert occurrence.line == 42
        assert occurrence.column == 15
        assert occurrence.context == "const secret = 'value';"
        assert occurrence.extractor == "rep_kingfisher"
        assert occurrence.rule_id == "api-key-rule"
        assert occurrence.confidence == "high"


class TestRolledUpSecret:
    """Test RolledUpSecret dataclass."""

    def test_rolled_up_secret_creation(self):
        """Test creation of RolledUpSecret."""
        occurrence = SecretOccurrence(
            file_id="file1",
            file_url="https://example.com/app.js",
            line=10,
            column=5,
            context="key = 'secret'",
            extractor="rep_kingfisher",
            rule_id="api-key",
            confidence="high"
        )
        
        secret = RolledUpSecret(
            type="api_key",
            value="test_secret",
            rule_id="api-key-rule",
            rule_name="API Key Detection",
            occurrence_count=1,
            file_count=1,
            extractors=["rep_kingfisher"],
            confidence_levels=["high"],
            occurrences=[occurrence],
            first_seen="https://example.com/app.js",
            risk_score=0.85
        )
        
        assert secret.type == "api_key"
        assert secret.value == "test_secret"
        assert secret.occurrence_count == 1
        assert secret.file_count == 1
        assert secret.extractors == ["rep_kingfisher"]
        assert secret.confidence_levels == ["high"]
        assert len(secret.occurrences) == 1
        assert secret.first_seen == "https://example.com/app.js"
        assert secret.risk_score == 0.85