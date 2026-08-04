"""
Tests for B-008 - Sensitive File Reference Detection

Tests the detection of sensitive file references with confidence scoring 
and noise suppression.
"""

import pytest
from app.services.sensitive_file_detector import SensitiveFileDetector


class TestSensitiveFileDetector:
    """Test sensitive file reference detection."""

    def setup_method(self):
        """Set up test fixtures."""
        self.detector = SensitiveFileDetector()

    def test_empty_content(self):
        """Test detector with empty content."""
        result = self.detector.detect_sensitive_files("", "test.js")
        assert result == []

    def test_high_confidence_config_files(self):
        """Test detection of high-confidence config files."""
        js_content = '''
        const config = require('./config.json');
        const env = process.env.NODE_ENV || 'production';
        const settings = './settings.yaml';
        const dbConfig = './application.properties';
        fetch('.env.local');
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        # Should find high-confidence config files
        paths = [item["path"] for item in result]
        assert "./config.json" in paths
        assert "./settings.yaml" in paths
        assert "./application.properties" in paths
        assert ".env.local" in paths
        
        # Check confidence levels
        for item in result:
            if item["path"] in ["./config.json", "./settings.yaml", "./application.properties", ".env.local"]:
                assert item["confidence"] == "high"
                assert item["category"] == "config"

    def test_high_confidence_backup_files(self):
        """Test detection of backup and archive files."""
        js_content = '''
        import backup from './data.bak';
        const oldFile = 'config.old';
        const origFile = './script.orig';
        const saveFile = 'backup.save';
        const tempFile = './temp.tmp.js';
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        backup_files = [item for item in result if item["category"] == "backup"]
        assert len(backup_files) >= 4
        
        for item in backup_files:
            assert item["confidence"] == "high"
            assert any(pattern in item["path"] for pattern in [".bak", ".old", ".orig", ".save", ".tmp.js"])

    def test_high_confidence_key_files(self):
        """Test detection of key and certificate files."""
        js_content = '''
        const privateKey = fs.readFileSync('./private.key');
        const cert = './server.crt';
        const pem = 'certificate.pem';
        const p12 = './keystore.p12';
        const rsa = '/home/user/.ssh/id_rsa';
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        key_files = [item for item in result if item["category"] == "keys"]
        assert len(key_files) >= 4
        
        for item in key_files:
            assert item["confidence"] == "high"
            assert any(ext in item["path"] for ext in [".key", ".crt", ".pem", ".p12", "id_rsa"])

    def test_medium_confidence_files(self):
        """Test detection of medium-confidence files."""
        js_content = '''
        const constants = require('./constants.js');
        const secrets = './secrets.json';
        const globals = 'globals.js';
        const creds = './credentials.json';
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        medium_files = [item for item in result if item["confidence"] == "medium"]
        assert len(medium_files) >= 3
        
        for item in medium_files:
            assert item["category"] == "config"

    def test_low_confidence_with_flag(self):
        """Test that low-confidence files are only included when flag is set."""
        js_content = '''
        const test = require('./test.js');
        const spec = './spec.json';
        const mock = 'mock.js';
        '''
        
        # Without flag - should not include low confidence
        result = self.detector.detect_sensitive_files(js_content, "app.js", include_low_confidence=False)
        low_confidence = [item for item in result if item["confidence"] == "low"]
        assert len(low_confidence) == 0
        
        # With flag - should include low confidence
        result = self.detector.detect_sensitive_files(js_content, "app.js", include_low_confidence=True)
        low_confidence = [item for item in result if item["confidence"] == "low"]
        assert len(low_confidence) >= 2

    def test_suppression_patterns(self):
        """Test that bundler artifacts and common paths are suppressed."""
        js_content = '''
        import './node_modules/package/file.js';
        const bundle = './dist/bundle.js';
        const chunk = './build/chunk.12345.js';
        const static = './public/asset.css';
        const vendor = './vendor/library.min.js';
        const webpack = './webpack.runtime.js';
        const mainBundle = './main.a1b2c3d4.js';
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        # All of these should be suppressed
        paths = [item["path"] for item in result]
        assert not any("node_modules" in path for path in paths)
        assert not any("dist/" in path for path in paths)
        assert not any("build/" in path for path in paths)
        assert not any("public/" in path for path in paths)
        assert not any("vendor/" in path for path in paths)
        assert not any("webpack" in path for path in paths)
        assert not any(".min.js" in path for path in paths)

    def test_line_and_context_tracking(self):
        """Test that line numbers and context are tracked correctly."""
        js_content = '''const config = require('./config.json');
const settings = './settings.yaml';
fetch('.env.local');'''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        # Check that line numbers are tracked
        for item in result:
            assert item["line"] is not None
            assert item["line"] > 0
            assert item["context"] is not None
            assert len(item["context"]) > 0

    def test_deduplication(self):
        """Test that duplicate file paths are deduplicated."""
        js_content = '''
        const config1 = require('./config.json');
        const config2 = './config.json';
        import config3 from './config.json';
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        # Should only have one entry for config.json despite multiple references
        config_entries = [item for item in result if item["path"] == "./config.json"]
        assert len(config_entries) == 1

    def test_confidence_sorting(self):
        """Test that results are sorted by confidence (high first)."""
        js_content = '''
        const test = './test.js';
        const constants = './constants.js';
        const config = './config.json';
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js", include_low_confidence=True)
        
        # Results should be sorted by confidence (high first)
        confidences = [item["confidence"] for item in result]
        confidence_order = {"high": 0, "medium": 1, "low": 2}
        confidence_nums = [confidence_order[conf] for conf in confidences]
        assert confidence_nums == sorted(confidence_nums)

    def test_url_extraction_patterns(self):
        """Test various patterns for extracting file references."""
        js_content = '''
        // String literals
        const file1 = './config.json';
        
        // require() calls
        const file2 = require('./secrets.json');
        
        // import statements
        import file3 from './settings.yaml';
        
        // fetch calls
        fetch('./data.bak');
        
        // URL constructor
        new URL('./private.key', base);
        
        // axios calls
        axios.get('./backup.sql');
        
        // Assignment patterns
        const path = './certificate.pem';
        const url = "https://example.com/config.xml";
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        # Should find files from all different extraction patterns
        paths = [item["path"] for item in result]
        assert "./config.json" in paths
        assert "./secrets.json" in paths
        assert "./settings.yaml" in paths
        assert "./data.bak" in paths
        assert "./private.key" in paths
        assert "./backup.sql" in paths
        assert "./certificate.pem" in paths

    def test_database_files_detection(self):
        """Test detection of database-related files."""
        js_content = '''
        const db = './database.db';
        const sqlite = './data.sqlite3';
        const dump = './backup/dump.sql';
        const dbBackup = './database.sql';
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        db_files = [item for item in result if item["category"] == "database"]
        assert len(db_files) >= 3
        
        for item in db_files:
            assert item["confidence"] == "high"

    def test_development_files_detection(self):
        """Test detection of development-related files."""
        js_content = '''
        const gitConfig = './.git/config';
        const htaccess = './.htaccess';
        const htpasswd = './.htpasswd';
        const composer = './composer.json';
        const packageLock = './package-lock.json';
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        dev_files = [item for item in result if item["category"] == "development"]
        assert len(dev_files) >= 4
        
        for item in dev_files:
            assert item["confidence"] in ["high", "low"]  # git config is high, test files are low

    def test_case_insensitive_detection(self):
        """Test that detection works regardless of case."""
        js_content = '''
        const config = './CONFIG.JSON';
        const env = './.ENV';
        const backup = './DATA.BAK';
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        # Should detect uppercase extensions
        assert len(result) >= 3
        for item in result:
            assert item["confidence"] == "high"

    def test_api_endpoint_filtering(self):
        """Test that API endpoints without file extensions are filtered out."""
        js_content = '''
        fetch('/api/users');
        axios.get('https://api.example.com/data');
        const url = '/api/v1/config';
        const fileUrl = '/api/config.json';  // This should be detected
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        paths = [item["path"] for item in result]
        # API endpoints without extensions should be filtered out
        assert "/api/users" not in paths
        assert "https://api.example.com/data" not in paths
        assert "/api/v1/config" not in paths
        
        # But file with extension should be included
        assert "/api/config.json" in paths

    def test_extractor_metadata(self):
        """Test that extractor metadata is correctly set."""
        js_content = '''
        const config = './config.json';
        '''
        
        result = self.detector.detect_sensitive_files(js_content, "app.js")
        
        for item in result:
            assert item["extractor"] == "sensitive_file_detector"
            assert "reason" in item
            assert "category" in item
            assert item["reason"].startswith("Matches")

    def test_rejects_operator_heavy_minified_blob_candidates(self):
        """Ensure minified-expression strings are not misclassified as file paths."""
        js_content = """
        const path = "!==o||0!==h.wi)&&h.data._shouldRender&&0!==this.global";
        const good = "./config.json";
        """

        result = self.detector.detect_sensitive_files(js_content, "app.js")
        paths = [item["path"] for item in result]

        assert "./config.json" in paths
        assert "!==o||0!==h.wi)&&h.data._shouldRender&&0!==this.global" not in paths

    def test_rejects_overly_long_path_candidates(self):
        """Long candidate strings should be ignored to prevent payload blow-ups."""
        long_candidate = "./" + ("a" * 2000) + ".json"
        js_content = f'const maybePath = "{long_candidate}";'

        result = self.detector.detect_sensitive_files(js_content, "app.js")
        assert result == []
